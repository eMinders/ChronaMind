-- Migration 15: sub-scopes, the per-scope exclusion key, and scope-keyed indexes - E-011 (#62) r13, V2-D64, V2-D49
-- D10, I5, I4.
--
-- Migration 11 admitted only the personal root on thirteen tables: `scope_path = '/u/' || user_id || '/'`
-- (`<table>_scope_matches_user`). It deferred everything that needs a scope *below* the root to "the per-scope I9
-- decision": the enterprise root `/o/<org>/`, the coding profile's `/u/<id>/p/<repo>/`, the per-scope exclusion key,
-- and the two indexes whose ownership notes pointed here. This migration takes that decision.
--
-- 1. A scoped row may lie anywhere INSIDE its owner's namespace. `<table>_scope_matches_user` becomes
--    `<table>_scope_within_user`: `pg_catalog.starts_with(scope_path, '/u/' || user_id || '/')`, exact by
--    construction because neither a user id nor a scope segment may contain the separator (D13, migration 11).
--    `/u/4/` still does not contain `/u/42/`, so the property migration 11 established (one tenant cannot forge a
--    path inside another's subtree) is preserved, not weakened. The sub-scope grammar is already the shape CHECK's:
--    lower-case segments matching `[a-z0-9][a-z0-9-]*`.
--
-- 2. D10 revisited (`V2-D64`): the exclusion key moves from `user_id` to `scope_path`. While every scope was the
--    personal root the two were equivalent, so D10's "stays `user_id` in W1" cost nothing. With sub-scopes they are
--    not: two distinct scopes of ONE user (`/u/4/p/a/`, `/u/4/p/b/`) must each be able to hold the same single-valued
--    predicate, and a `user_id`-keyed constraint would refuse the second. This is what `I5` declares ("single-valued
--    exclusivity *within a scope*"), so the constraint now states the invariant rather than an approximation of it.
--    Recreated in this transaction, so Invariant 4 has no unguarded window.
--
-- 3. `I4` per-scope belief monotonicity: `clock_watermark` is keyed by `scope_path` instead of `user_id`, so a
--    sub-scope's belief timeline cannot be walked backwards by writing a sibling scope. The trigger reads the new
--    key from the row it is guarding. Migration 9 keyed it by user because that was the only namespace; the
--    watermark is unchanged in every other respect.
--
-- 4. The two indexes whose ownership notes still pointed at `E-011`:
--    * `memory_version_um_facet_idx` becomes `(scope_path, um_facet)` - facet-filtered reads are scope-filtered
--      reads, and the old leading column is no longer the read key;
--    * `<table>_scope_path_idx` (migration 11) already keys on `scope_path` and is unchanged.
--
-- No existing row changes: every row is in its personal root, `starts_with(scope_path, '/u/' || user_id || '/')`
-- holds for all of them, and re-keying the exclusion constraint and the watermark preserves every key that existed.
-- The enterprise root `/o/<org>/` is NOT admitted as a top-level namespace (V2-D64): grants and RLS both key on a
-- user namespace (`scope_grant.owner_user_id` references `app_user`, and the read policy matches `/u/<caller>/`), so
-- an `/o/` row would have no owner able to grant it and no policy able to return it. The enterprise profile uses
-- `/u/<owner>/o/<org>/`, shared to colleagues through `scope_grant` - the same mechanism as any project scope.

-- 1. Sub-scopes inside the owner's namespace (replaces the personal-root equality).
DO $m15$
DECLARE
  tbl text;
  personal text[] := ARRAY[
    'memory_version', 'memory_evidence', 'memory_derivation', 'memory_embedding', 'memory_conflict',
    'turn', 'session', 'job', 'audit_event', 'deletion_request',
    'association', 'abstraction_stats', 'verification'
  ];
BEGIN
  FOREACH tbl IN ARRAY personal LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I DROP CONSTRAINT %I', tbl, tbl || '_scope_matches_user');
    EXECUTE pg_catalog.format(
      'ALTER TABLE %I ADD CONSTRAINT %I CHECK '
      '(pg_catalog.starts_with(scope_path, ''/u/'' || user_id || ''/''))',
      tbl, tbl || '_scope_within_user'
    );
  END LOOP;
END
$m15$;

-- 2. I5: single-valued exclusivity within a scope, not within a user.
ALTER TABLE memory_version DROP CONSTRAINT no_bitemporal_overlap;
ALTER TABLE memory_version ADD CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (
  scope_path WITH =,
  subject_key WITH =,
  predicate_key WITH =,
  event_range WITH &&,
  recorded_range WITH &&
) WHERE (single_valued AND status IN ('active', 'archived'));

-- 3. I4: belief time is monotonic per scope. Rekey the watermark and the trigger that maintains it.
ALTER TABLE clock_watermark RENAME COLUMN user_id TO scope_path;
DROP TRIGGER trg_memory_version_recorded_watermark ON memory_version;
CREATE OR REPLACE FUNCTION enforce_recorded_watermark() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  instant timestamptz;
  namespace text;
BEGIN
  -- NULL (staged), empty and unbounded-below ranges are rejected by the CHECK constraints; they put nothing on the timeline.
  IF NEW.recorded_range IS NULL OR isempty(NEW.recorded_range) OR lower_inf(NEW.recorded_range) THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'UPDATE' AND NEW.recorded_range IS NOT DISTINCT FROM OLD.recorded_range THEN
    RETURN NEW;
  END IF;
  -- `trg_memory_version_recorded_watermark` sorts before `trg_memory_version_scope_default`, so on INSERT the
  -- scope may not be filled yet. Derive the same personal root `fill_scope_path` would, rather than depend on
  -- trigger order (the row's own scope is what the watermark must key on).
  namespace := COALESCE(NEW.scope_path, '/u/' || NEW.user_id || '/');
  -- The instant this write puts on the belief timeline: a belief start, or the moment a belief is closed.
  instant := GREATEST(lower(NEW.recorded_range), CASE WHEN upper_inf(NEW.recorded_range) THEN NULL ELSE upper(NEW.recorded_range) END);
  INSERT INTO clock_watermark AS w (scope_path, high) VALUES (namespace, instant)
  ON CONFLICT (scope_path) DO UPDATE SET high = GREATEST(w.high, EXCLUDED.high)
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

-- Rebuild the watermark from the stored rows under the new key. A row's own scope is its home, so the per-user
-- maximums of migration 9 become per-scope maximums without losing a value.
DELETE FROM clock_watermark;
INSERT INTO clock_watermark (scope_path, high)
SELECT scope_path,
       max(GREATEST(lower(recorded_range), CASE WHEN upper_inf(recorded_range) THEN NULL ELSE upper(recorded_range) END))
  FROM memory_version
 WHERE recorded_range IS NOT NULL
 GROUP BY scope_path;

-- 4. The facet index, now led by the scope key E-011 introduced.
DROP INDEX memory_version_um_facet_idx;
CREATE INDEX memory_version_um_facet_idx ON memory_version (scope_path text_pattern_ops, um_facet)
  WHERE um_facet IS NOT NULL;

-- 5. V2-D54 (S-02): the rewritten function is pinned to the engine schema.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.enforce_recorded_watermark() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
