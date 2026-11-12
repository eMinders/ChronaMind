-- Migration 20: procedure outcome statistics (E-027, the minimal substrate E-030 owns).
--
-- `ARCHITECTURE_V2.md` §6 gives a procedure four fields: `preconditions`, `steps`, `outcome_stats`, `derived_from`.
-- Only `outcome_stats` is needed for the feedback loop this migration unblocks, and only it has a reader: the ranking
-- term in `engine/procedure_ranking.py`. `preconditions` and `steps` are **E-030's** (W3) and are deliberately absent
-- rather than sketched — a half-built procedure shape would be the thing E-030 has to rewrite.
--
-- A side table, not columns on `memory_version`. `abstraction_stats` (migration 10) set this precedent: statistics
-- that are updated by a different operation, at a different rate, under different mutability rules (these counters
-- move; belief content does not) belong beside the artifact. It also keeps the `EXCLUDE`-constrained bitemporal core
-- untouched, so nothing about supersession or the recorded-time clock changes here.
--
-- Ownership: E-030 extends or replaces this table when it lands procedure storage. It will need `preconditions`
-- (matched by the planner) and `steps`; both are additive columns on this table, and neither invalidates
-- `record_outcome` or the ranking term.

CREATE TABLE outcome_stats (
  version_id   text PRIMARY KEY,
  user_id      text NOT NULL,
  -- Counters, not a stored rate: a stored `successes/(successes+failures)` would be a second source of truth that a
  -- later correction to either counter could leave inconsistent. The rate is derived at read time (V2-D75).
  successes    integer NOT NULL DEFAULT 0 CHECK (successes >= 0),
  failures     integer NOT NULL DEFAULT 0 CHECK (failures >= 0),
  last_used_at timestamptz,
  -- `mean_cost` is sum/count rather than a running mean: a running mean makes the stored value depend on the arrival
  -- order of the outcomes, so two identical histories could persist different numbers (E-027 §5 Q4).
  cost_count   integer NOT NULL DEFAULT 0 CHECK (cost_count >= 0),
  cost_sum     double precision NOT NULL DEFAULT 0 CHECK (cost_sum >= 0),
  mean_cost    double precision GENERATED ALWAYS AS (
                 CASE WHEN cost_count = 0 THEN NULL ELSE cost_sum / cost_count END
               ) STORED,
  updated_at   timestamptz NOT NULL DEFAULT clock_now(),
  -- No `(cost_count = 0) = (cost_sum = 0)` constraint: a recorded cost of 0.0 legitimately has count 1 and sum 0, so
  -- that pairing would forbid a real value. The `>= 0` checks above are the whole invariant; "no cost recorded" is
  -- `cost_count = 0`, and `mean_cost` is NULL exactly then.
  CONSTRAINT outcome_stats_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE
);

-- Only a procedure has outcomes. Enforced here rather than only in the handler for the reason migration 10 gives for
-- `abstraction_stats`: a rule that lives only in Python is one a replay harness, a backfill or an operator can break.
CREATE FUNCTION guard_outcome_stats() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  version_kind text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT kind INTO version_kind FROM memory_version WHERE id = NEW.version_id AND user_id = NEW.user_id;
    IF version_kind IS DISTINCT FROM 'procedure' THEN
      RAISE EXCEPTION 'outcome_stats_require_a_procedure' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.version_id <> OLD.version_id OR NEW.user_id <> OLD.user_id THEN
    RAISE EXCEPTION 'outcome_stats_identity_is_fixed' USING ERRCODE = 'P0001';
  END IF;
  -- Counters only ever grow: an outcome is a recorded event, and rewriting one would rewrite history (I3).
  IF NEW.successes < OLD.successes OR NEW.failures < OLD.failures
     OR NEW.cost_count < OLD.cost_count OR NEW.cost_sum < OLD.cost_sum THEN
    RAISE EXCEPTION 'outcome_stats_counters_are_monotone' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_outcome_stats_guard BEFORE INSERT OR UPDATE ON outcome_stats
  FOR EACH ROW EXECUTE FUNCTION guard_outcome_stats();

ALTER TABLE outcome_stats ENABLE ROW LEVEL SECURITY;
ALTER TABLE outcome_stats FORCE ROW LEVEL SECURITY;
CREATE POLICY outcome_stats_read ON outcome_stats FOR SELECT
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY outcome_stats_insert ON outcome_stats FOR INSERT
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY outcome_stats_update ON outcome_stats FOR UPDATE
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

-- Pin the function's search_path: `CREATE` above resolved against the migration's search_path, and a later caller
-- must not be able to redirect `memory_version` through their own. Every engine function is pinned this way (RL-072).
DO $pin$
DECLARE
  engine_schema text := current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_outcome_stats() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
