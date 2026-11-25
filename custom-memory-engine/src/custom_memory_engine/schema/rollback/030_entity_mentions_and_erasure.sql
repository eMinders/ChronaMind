-- Rollback of migration 30: entity mentions and memory-scope entity erasure.
-- Run in ONE transaction; restores the version-29 schema. Roll back later migrations first.
--
-- Refuses while any entity_mention rows or origin='mention' entities exist unless:
--   SET LOCAL cme.rollback_discard = 'entity_mentions';

DO $$
BEGIN
  IF (
       EXISTS (SELECT 1 FROM entity_mention)
       OR EXISTS (SELECT 1 FROM entity WHERE origin = 'mention')
     )
     AND COALESCE(pg_catalog.current_setting('cme.rollback_discard', true), '') <> 'entity_mentions' THEN
    RAISE EXCEPTION 'm30_rollback_refused_entity_mentions_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

DROP FUNCTION IF EXISTS delete_orphaned_mention_entities(text, text[]);

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
  -- `p_entity_ids IS NULL` means "every entity of this user" (scope='all'); an array names the candidates.
  DELETE FROM entity AS e
   WHERE e.user_id = p_user_id
     AND (p_entity_ids IS NULL OR e.id = ANY(p_entity_ids))
     AND NOT EXISTS (
       SELECT 1 FROM memory_version AS v WHERE v.user_id = p_user_id AND v.subject_entity_id = e.id
     );
  GET DIAGNOSTICS deleted_count = ROW_COUNT;
  RETURN deleted_count;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.delete_orphaned_entities(text, text[]) SET search_path = %I, pg_catalog, pg_temp',
    engine_schema, engine_schema
  );
END
$pin$;

DROP POLICY IF EXISTS entity_mention_isolation ON entity_mention;
DROP TABLE IF EXISTS entity_mention;

ALTER TABLE entity
  DROP CONSTRAINT IF EXISTS entity_origin_check,
  DROP COLUMN IF EXISTS origin;

DELETE FROM _cme_schema_migrations WHERE version = 30;
