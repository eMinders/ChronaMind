-- Migration 21: procedure storage - `preconditions` and `steps` (E-030, W3; ARCHITECTURE_V2.md §6).
--
-- `ARCHITECTURE_V2.md` §6 gives a procedure four fields. Migration 20 (E-027) delivered `outcome_stats`, and
-- `derived_from` is the existing lineage edge. This migration adds the other two, and nothing else: there is no
-- execution engine, no planning and no ranking here. The engine stores and retrieves procedures; the agent executes.
--
-- WHERE THEY LIVE: a third side table, `procedure_shape`, not columns on `outcome_stats` (finding E-030-F1).
-- Migration 20's header expected additive columns on `outcome_stats`; once both fields are needed that is the wrong
-- home, for migration 20's own reason. `outcome_stats` holds counters that `record_outcome` moves and creates lazily at
-- the first outcome; `preconditions` and `steps` are part of what was asserted, written once with the procedure and
-- never changed. Putting them on `outcome_stats` would make `assert` create the counters row (changing
-- `record_outcome`'s first-insert path) and would mix two mutability rules in one row. Columns on `memory_version`
-- would touch the EXCLUDE-constrained bitemporal core and `guard_update` for an optional, kind-specific shape.
--
-- THE SHAPE (enforced here by guard_procedure_shape, so Python and the database cannot disagree; `engine/procedure_shape.py` is the twin):
--   preconditions  a JSON array of at most 32 conditions, read as a conjunction. Each condition is exactly one of
--                    {"op": "scope_within", "scope": <canonical scope path>}
--                    {"op": "exists", "match": M}     {"op": "absent", "match": M}
--                  where M is an object with 1-4 keys from kind | lifecycle | facet | predicate, each a string from
--                  that axis's closed vocabulary (predicate: a predicate-key token). `[]` means "always applicable".
--   steps          a JSON array of 0-64 ordered, non-blank free-text strings of at most 2000 characters each.
-- Neither is ever executed or interpreted by the engine beyond the applicability match (`engine/procedure_matching`).
--
-- GUARDS: the shape grammar above; only a procedure may have a shape (looked up in the writer's own tenant); and a
-- shape is never updated, because its identity and content are fixed exactly as the asserted procedure's content is.
-- The row goes with its procedure by the composite tenant foreign key (`V2-D54`). RLS is forced; the runtime role may
-- only read and append.

CREATE TABLE procedure_shape (
  version_id    text PRIMARY KEY,
  user_id       text NOT NULL,
  preconditions jsonb NOT NULL DEFAULT '[]'::jsonb,
  steps         jsonb NOT NULL DEFAULT '[]'::jsonb,
  CONSTRAINT procedure_shape_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE
);

-- The whole contract of a shape row, judged before it is stored. In the schema rather than only in the handler, for
-- the reason migrations 10 and 20 give: a replay harness, a backfill or an operator writes SQL directly. One
-- zero-argument trigger function rather than CHECK helpers, so the schema keeps the convention every engine function
-- follows (migrate.FUNCTIONS, the reset path and the pinned search_path all assume `name()`).
CREATE FUNCTION guard_procedure_shape() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  version_kind text;
  condition jsonb;
  selector jsonb;
  key text;
  scope text;
  parts text[];
  i integer;
  -- exactly the characters Python's str.isspace() accepts, so `strip()` in the parser and `btrim` here agree
  whitespace constant text := E'\t\n\x0b\x0c\r\x1c\x1d\x1e\x1f \u0085\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000';
BEGIN
  -- a shape is asserted content: identity and content are fixed once written
  IF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'procedure_shape_is_immutable' USING ERRCODE = 'P0001';
  END IF;
  -- only a procedure has one, looked up in the writer's own tenant
  SELECT kind INTO version_kind FROM memory_version WHERE id = NEW.version_id AND user_id = NEW.user_id;
  IF version_kind IS DISTINCT FROM 'procedure' THEN
    RAISE EXCEPTION 'procedure_shape_requires_a_procedure' USING ERRCODE = 'P0001';
  END IF;

  -- preconditions: an array of at most 32 conditions, each exactly one of the three forms
  IF pg_catalog.jsonb_typeof(NEW.preconditions) <> 'array' OR pg_catalog.jsonb_array_length(NEW.preconditions) > 32 THEN
    RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
  END IF;
  FOR condition IN SELECT value FROM pg_catalog.jsonb_array_elements(NEW.preconditions) LOOP
    IF pg_catalog.jsonb_typeof(condition) <> 'object' THEN
      RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
    END IF;
    IF condition->>'op' = 'scope_within' THEN
      IF (SELECT pg_catalog.array_agg(k ORDER BY k) FROM pg_catalog.jsonb_object_keys(condition) AS k)
           <> ARRAY['op', 'scope']
         OR pg_catalog.jsonb_typeof(condition->'scope') <> 'string' THEN
        RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
      END IF;
      -- scope.paths.parse_scope, rule for rule: canonical form, the /u/<user>/ namespace, at most 16 segments; the
      -- user segment non-blank, unpadded, not `.`/`..`, <= 256 characters; every further segment [a-z0-9][a-z0-9-]*
      -- of <= 128 characters
      scope := condition->>'scope';
      IF scope ~ '[\x01-\x1f\x7f-\x9f]' OR scope !~ '^/.+/$' THEN
        RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
      END IF;
      parts := pg_catalog.string_to_array(pg_catalog.substr(scope, 2, pg_catalog.length(scope) - 2), '/');
      IF pg_catalog.cardinality(parts) < 2 OR pg_catalog.cardinality(parts) > 16 OR parts[1] <> 'u'
         OR pg_catalog.btrim(parts[2], whitespace) = '' OR pg_catalog.btrim(parts[2], whitespace) <> parts[2]
         OR parts[2] IN ('.', '..')
         OR pg_catalog.length(parts[2]) > 256 THEN
        RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
      END IF;
      FOR i IN 3 .. pg_catalog.cardinality(parts) LOOP
        IF parts[i] !~ '^[a-z0-9][a-z0-9-]*$' OR pg_catalog.length(parts[i]) > 128 THEN
          RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
        END IF;
      END LOOP;
    ELSIF condition->>'op' IN ('exists', 'absent') THEN
      selector := condition->'match';
      IF (SELECT pg_catalog.array_agg(k ORDER BY k) FROM pg_catalog.jsonb_object_keys(condition) AS k)
           <> ARRAY['match', 'op']
         OR pg_catalog.jsonb_typeof(selector) <> 'object'
         OR (SELECT count(*) FROM pg_catalog.jsonb_object_keys(selector)) NOT BETWEEN 1 AND 4 THEN
        RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
      END IF;
      FOR key IN SELECT k FROM pg_catalog.jsonb_object_keys(selector) AS k LOOP
        -- the closed vocabularies of model.axes (KINDS, LIFECYCLES, FACETS) and a predicate-key token
        IF pg_catalog.jsonb_typeof(selector->key) <> 'string' OR NOT (
          (key = 'kind' AND selector->>key IN ('episode', 'claim', 'procedure', 'abstraction'))
          OR (key = 'lifecycle' AND selector->>key IN ('observed', 'asserted', 'inferred', 'contested', 'retired'))
          OR (key = 'facet' AND selector->>key IN (
                'preference', 'goal', 'constraint', 'context', 'decision_pattern', 'meta_preference'))
          OR (key = 'predicate' AND selector->>key ~ '^[a-z][a-z0-9_]{0,63}$')
        ) THEN
          RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';
        END IF;
      END LOOP;
    ELSE
      RAISE EXCEPTION 'procedure_preconditions_malformed' USING ERRCODE = 'P0001';  -- unknown or missing operator
    END IF;
  END LOOP;

  -- steps: an ordered array of at most 64 non-blank strings of at most 2000 characters; free text, never executed
  IF pg_catalog.jsonb_typeof(NEW.steps) <> 'array' OR pg_catalog.jsonb_array_length(NEW.steps) > 64
     OR EXISTS (
       SELECT 1 FROM pg_catalog.jsonb_array_elements(NEW.steps) AS s(step)
        WHERE pg_catalog.jsonb_typeof(s.step) <> 'string'
           OR pg_catalog.btrim(s.step #>> '{}', whitespace) = ''
           OR pg_catalog.length(s.step #>> '{}') > 2000
     ) THEN
    RAISE EXCEPTION 'procedure_steps_malformed' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_procedure_shape_guard BEFORE INSERT OR UPDATE ON procedure_shape
  FOR EACH ROW EXECUTE FUNCTION guard_procedure_shape();

ALTER TABLE procedure_shape ENABLE ROW LEVEL SECURITY;
ALTER TABLE procedure_shape FORCE ROW LEVEL SECURITY;
CREATE POLICY procedure_shape_read ON procedure_shape FOR SELECT
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY procedure_shape_insert ON procedure_shape FOR INSERT
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

-- V2-D54 (S-02): every engine function is pinned to `<this schema>, pg_catalog, pg_temp`.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.guard_procedure_shape() SET search_path = %I, pg_catalog, pg_temp', engine_schema, engine_schema
  );
END
$pin$;
