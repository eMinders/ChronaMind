-- Migration 27: a deletion reaches the entities it orphans (V2-D108).
--
-- An `entity` row holds a person's, place's or project's *name* (`canonical_name`, `aliases`) and, since migration 26,
-- a vector derived from that name. Both are content in the sense `I8` means: they are what the user said. Until this
-- migration no deletion path touched the table -- `delete(scope='all')` removed every memory and left the name of every
-- person those memories mentioned, and the runtime role could not have removed them, because it holds no DELETE grant
-- on `entity` (by design: a caller must not be able to drop an identity other rows are attached to).
--
-- The fix follows migration 22's pattern exactly (`delete_exclusive_turns`): one SECURITY DEFINER function with a pinned
-- search_path that widens DELETE on `entity` **only inside a deletion transaction** -- `deletion_in_this_transaction`,
-- i.e. the current transaction has itself appended a `deletion_request` row for this user -- and only for rows no
-- surviving memory still cites. The foreign key from `memory_version.subject_entity_id` is `ON DELETE RESTRICT` and stays
-- so: the NOT EXISTS below is a courtesy that keeps the function from raising, the RESTRICT is the guarantee.
--
-- What it deletes is decided by the caller (`engine.deletion`): every orphaned entity of the user for `scope='all'`, and
-- the named entity for a `topic` deletion whose target is that entity. A narrower deletion leaves entities alone: an
-- entity created by `create_entity` is the user's own structure, not a by-product of the deleted memory.
--
-- `entity_link` rows cascade (migration 18). No policy is added: the table's own-user `entity_isolation` policy (all
-- commands) already admits the owner-run function's DELETE for `app.user_id`, and the runtime role still holds no
-- DELETE grant, so nothing is widened for it.

CREATE FUNCTION delete_orphaned_entities(p_user_id text, p_entity_ids text[]) RETURNS integer
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
