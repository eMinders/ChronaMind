-- Migration 9: the three clocks (M3) - E-012, V2-D06, V2-D49 (D8, D9, D10), THREAT_MODEL T-28.
--
--   event time        event_range    renamed IN PLACE from valid_range      producer, or inferred (event_basis)
--   observation time  observed_at    new                                    the engine clock at ingest
--   belief time       recorded_range unchanged                              transaction clock only (I4)
--
-- Decisions followed (not re-opened):
--   D9   RENAME IN PLACE, not drop-and-recreate. The exclusion constraint no_bitemporal_overlap, the CHECK
--        valid_wellformed and the index memory_version_temporal_idx follow the column (a metadata change); the
--        constraint is never dropped, so Invariant 4 has no unguarded window. This amends the literal steps 1-3 of
--        issue #63, as D9 records. The measured cost was 3.2 ms against 48 s under ACCESS EXCLUSIVE at 400k rows.
--   D10  the exclusion key stays `user_id` (v1-equivalent); it moves to the scope key only with the per-scope I9 decision.
--   D8   observed_at = COALESCE(lower(recorded_range), earliest evidencing turn, lower(event_range)): believed rows use
--        their belief start, unbelieved evidenced rows the earliest evidencing turn, the rest their event start.
--
-- Every other object that names the column is rewritten from its own catalog definition, so the rewrite cannot drift
-- from the source it replaces: guard_update, enforce_archival_invariant and guard_derivation_coverage (PL/pgSQL bodies
-- are late-bound text, MIGRATION_RISK.md X3). pg_get_functiondef keeps each function's pinned search_path (V2-D54).
--
-- T-28 (new here): `app.clock` is a session setting, so a compromised runtime could backdate recorded time. A
-- per-namespace WATERMARK makes belief time monotonic in the database whatever the session sets. The namespace is the
-- user today (E-011 moves it to the scope). It is kept in `clock_watermark`, which the runtime role has no privilege on;
-- only the SECURITY DEFINER trigger below writes it.

-- 1. The rename (the exclusion constraint, CHECK and index follow the column).
ALTER TABLE memory_version RENAME COLUMN valid_range TO event_range;

-- 2. Rewrite the three functions that name the column.
DO $m3$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
  definition text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['guard_update()', 'enforce_archival_invariant()', 'guard_derivation_coverage()'] LOOP
    definition := pg_catalog.pg_get_functiondef(
      pg_catalog.to_regprocedure(pg_catalog.format('%I.%s', engine_schema, fn))
    );
    EXECUTE pg_catalog.replace(definition, 'valid_range', 'event_range');
  END LOOP;
END
$m3$;

-- 3. observed_at and event_basis.
ALTER TABLE memory_version ADD COLUMN observed_at timestamptz, ADD COLUMN event_basis text;

UPDATE memory_version v
   SET observed_at = COALESCE(
         lower(v.recorded_range),
         (SELECT min(t.occurred_at) FROM memory_evidence e JOIN turn t ON t.id = e.turn_id WHERE e.version_id = v.id),
         lower(v.event_range)
       ),
       event_basis = v.valid_from_basis;
-- The UPDATE queued the deferred provenance trigger once per row; ALTER TABLE refuses while events are pending.
SET CONSTRAINTS trg_memory_version_provenance IMMEDIATE;
SET CONSTRAINTS trg_memory_version_provenance DEFERRED;

ALTER TABLE memory_version
  ALTER COLUMN observed_at SET NOT NULL,
  ALTER COLUMN event_basis SET NOT NULL,
  ADD CONSTRAINT memory_version_event_basis_check CHECK (event_basis IN ('explicit', 'inferred'));

-- Dual-write window: v1 writers still set valid_from_basis (default 'inferred'). event_basis is derived from it unless
-- the caller supplies it, in which case the caller is authoritative and valid_from_basis is brought into line.
-- observed_at is the engine clock at ingest and is never caller-controlled belief time (I4); both columns are fixed at
-- creation.
CREATE FUNCTION sync_memory_version_clocks() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.event_basis IS NULL THEN
      NEW.event_basis := NEW.valid_from_basis;
    ELSE
      NEW.valid_from_basis := NEW.event_basis;
    END IF;
    IF NEW.observed_at IS NULL THEN
      NEW.observed_at := clock_now();
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.observed_at IS DISTINCT FROM OLD.observed_at OR NEW.event_basis IS DISTINCT FROM OLD.event_basis THEN
    RAISE EXCEPTION 'observed_at and event_basis are fixed at creation' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_version_clocks_dual_write
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION sync_memory_version_clocks();

-- 4. T-28: belief time can never move backwards.
CREATE TABLE clock_watermark (
  user_id text PRIMARY KEY,
  high    timestamptz NOT NULL
);
ALTER TABLE clock_watermark ENABLE ROW LEVEL SECURITY;
-- No policy and no grant: the runtime role can neither read nor write it. Only the definer trigger below (running as
-- the owner, who is not subject to a non-forced policy) does.

INSERT INTO clock_watermark (user_id, high)
SELECT user_id,
       max(GREATEST(lower(recorded_range), CASE WHEN upper_inf(recorded_range) THEN NULL ELSE upper(recorded_range) END))
  FROM memory_version
 WHERE recorded_range IS NOT NULL
 GROUP BY user_id;

CREATE FUNCTION enforce_recorded_watermark() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  instant timestamptz;
BEGIN
  -- NULL (staged), empty and unbounded-below ranges are rejected by the CHECK constraints; they put nothing on the timeline.
  IF NEW.recorded_range IS NULL OR isempty(NEW.recorded_range) OR lower_inf(NEW.recorded_range) THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.recorded_range IS NOT DISTINCT FROM OLD.recorded_range THEN
    RETURN NEW;
  END IF;
  -- The instant this write puts on the belief timeline: a belief start, or the moment a belief is closed.
  instant := GREATEST(lower(NEW.recorded_range), CASE WHEN upper_inf(NEW.recorded_range) THEN NULL ELSE upper(NEW.recorded_range) END);
  INSERT INTO clock_watermark AS w (user_id, high) VALUES (NEW.user_id, instant)
  ON CONFLICT (user_id) DO UPDATE SET high = GREATEST(w.high, EXCLUDED.high)
  WHERE EXCLUDED.high >= w.high
  RETURNING w.high INTO instant;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'recorded_time_moved_backwards' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_version_recorded_watermark
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION enforce_recorded_watermark();

-- V2-D54 (S-02): every engine function is pinned to `<this schema>, pg_catalog, pg_temp`.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY['sync_memory_version_clocks()', 'enforce_recorded_watermark()'] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
