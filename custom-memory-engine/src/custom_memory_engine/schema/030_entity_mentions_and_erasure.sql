-- Migration 30: entity mentions and memory-scope entity erasure (`I8`, `V2-D111`).
--
-- `V2-D108` left `delete(scope='memory')` unable to erase orphaned entities because no table recorded which memory
-- mentioned which entity and `entity` did not distinguish declared user structure from mention by-products.
--
-- Adds:
--   1. `entity.origin IN ('declared', 'mention')` (existing rows default to `'declared'`).
--   2. `entity_mention(id, user_id, version_id, entity_id, resolution_stage, created_at)` inside the deletion closure
--      (`version_id` -> `ON DELETE CASCADE`, `entity_id` -> `ON DELETE RESTRICT`), forced RLS.
--   3. Updated `delete_orphaned_entities` checking BOTH `subject_entity_id` AND `entity_mention` (`scope='all'|'topic'`).
--   4. `delete_orphaned_mention_entities` (`SECURITY DEFINER`, gated by `deletion_in_this_transaction`) erasing
--      `origin = 'mention'` entities left with no surviving mention and no surviving `subject_entity_id` on
--      `scope = 'memory'` deletion.

ALTER TABLE entity
  ADD COLUMN origin text NOT NULL DEFAULT 'declared'
  CONSTRAINT entity_origin_check CHECK (origin IN ('declared', 'mention'));

CREATE TABLE entity_mention (
  id               text PRIMARY KEY,
  user_id          text NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
  version_id       text NOT NULL,
  entity_id        text NOT NULL,
  resolution_stage text NOT NULL CHECK (
                     resolution_stage IN ('exact', 'alias', 'trigram', 'vector', 'created', 'subject')
                   ),
  created_at       timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
  CONSTRAINT entity_mention_unique UNIQUE (user_id, version_id, entity_id),
  CONSTRAINT entity_mention_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE,
  CONSTRAINT entity_mention_entity_same_tenant
    FOREIGN KEY (entity_id, user_id) REFERENCES entity (id, user_id) ON DELETE RESTRICT
);

CREATE INDEX entity_mention_entity_idx ON entity_mention (user_id, entity_id);
CREATE INDEX entity_mention_version_idx ON entity_mention (user_id, version_id);

ALTER TABLE entity_mention ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_mention FORCE ROW LEVEL SECURITY;
CREATE POLICY entity_mention_isolation ON entity_mention
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

CREATE OR REPLACE FUNCTION delete_orphaned_entities(p_user_id text, p_entity_ids text[]) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  deleted_count integer := 0;
BEGIN
  IF NOT deletion_in_this_transaction(p_user_id) THEN
    RAISE EXCEPTION 'entity_erasure_forbidden' USING ERRCODE = '42501';
  END IF;
  DELETE FROM entity AS e
   WHERE e.user_id = p_user_id
     AND (p_entity_ids IS NULL OR e.id = ANY(p_entity_ids))
     AND NOT EXISTS (
       SELECT 1 FROM memory_version AS v WHERE v.user_id = p_user_id AND v.subject_entity_id = e.id
     )
     AND NOT EXISTS (
       SELECT 1 FROM entity_mention AS m WHERE m.user_id = p_user_id AND m.entity_id = e.id
     );
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END
$$;

CREATE FUNCTION delete_orphaned_mention_entities(p_user_id text, p_entity_ids text[]) RETURNS integer
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  deleted_count integer := 0;
BEGIN
  IF NOT deletion_in_this_transaction(p_user_id) THEN
    RAISE EXCEPTION 'entity_erasure_forbidden' USING ERRCODE = '42501';
  END IF;
  DELETE FROM entity AS e
   WHERE e.user_id = p_user_id
     AND e.origin = 'mention'
     AND (p_entity_ids IS NULL OR e.id = ANY(p_entity_ids))
     AND NOT EXISTS (
       SELECT 1 FROM memory_version AS v WHERE v.user_id = p_user_id AND v.subject_entity_id = e.id
     )
     AND NOT EXISTS (
       SELECT 1 FROM entity_mention AS m WHERE m.user_id = p_user_id AND m.entity_id = e.id
     );
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'delete_orphaned_entities(text, text[])',
    'delete_orphaned_mention_entities(text, text[])'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
