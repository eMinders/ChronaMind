-- Migration 29: provenance for non-memory structural artifacts (`I2`, `artifact_provenance`).
--
-- `memory_version` has enforced provenance at commit since migrations 1/7/10 (`enforce_provenance`), while
-- `association`, `entity` (plus `aliases`), and `entity_link` recorded no attribution to the evidence version or API
-- call that created them.
--
-- Design (`V2-D110`):
--   1. One generic `artifact_provenance` table covering `association`, `entity`, `entity_alias`, and `entity_link`,
--      enforced at commit by deferred constraint triggers (`enforce_artifact_provenance()`).
--   2. When a `memory_version` referenced via `source_version_id` is deleted, `BEFORE DELETE ON memory_version`
--      (`mark_artifact_provenance_source_deleted()`) transitions `source_version_id = NULL, origin_kind = 'source_deleted'`
--      so deletion is never blocked by `ON DELETE RESTRICT` and never leaves dangling IDs or silent blanks.
--   3. When an `association`, `entity`, or `entity_link` row is deleted, `AFTER DELETE`
--      (`cascade_artifact_provenance_delete()`) deletes its attribution rows (including `entity_alias` rows for a
--      deleted `entity` so alias strings never outlive entity erasure).
--   4. Pre-migration rows are backfilled with `origin_kind = 'pre_migration'` (rendered as
--      `"unattributed (pre-migration)"`), never presented as newly attributed.

CREATE TABLE artifact_provenance (
  id                text PRIMARY KEY,
  user_id           text NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
  artifact_kind     text NOT NULL CHECK (artifact_kind IN ('association', 'entity', 'entity_alias', 'entity_link')),
  artifact_id       text NOT NULL CHECK (btrim(artifact_id) <> ''),
  alias_value       text,
  source_version_id text,
  method_id         text REFERENCES method (id),
  origin_kind       text NOT NULL CHECK (
                      origin_kind IN (
                        'evidence_version', 'tier_c_call', 'tier_b_call', 'tier_b_method', 'pre_migration', 'source_deleted'
                      )
                    ),
  created_by        text NOT NULL CHECK (btrim(created_by) <> ''),
  created_at        timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
  CONSTRAINT artifact_provenance_source_same_tenant
    FOREIGN KEY (source_version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE RESTRICT,
  CONSTRAINT artifact_provenance_source_matches_origin CHECK (
    (origin_kind = 'evidence_version' AND source_version_id IS NOT NULL)
    OR (origin_kind = 'tier_b_method' AND method_id IS NOT NULL AND source_version_id IS NULL)
    OR (origin_kind IN ('tier_c_call', 'tier_b_call', 'pre_migration', 'source_deleted') AND source_version_id IS NULL)
  ),
  CONSTRAINT artifact_provenance_alias_matches_kind CHECK (
    (artifact_kind = 'entity_alias' AND alias_value IS NOT NULL AND btrim(alias_value) <> '')
    OR (artifact_kind <> 'entity_alias' AND alias_value IS NULL)
  )
);

CREATE INDEX artifact_provenance_artifact_idx
  ON artifact_provenance (user_id, artifact_kind, artifact_id);
CREATE INDEX artifact_provenance_source_idx
  ON artifact_provenance (user_id, source_version_id) WHERE source_version_id IS NOT NULL;

-- Backfill existing rows before enabling forced RLS so non-bypass owners upgrade cleanly (`test_migration_owner.py`).
INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by, created_at)
SELECT 'ap_m29_assoc_' || a.id, a.user_id, 'association', a.id, 'pre_migration', 'pre_migration', a.created_at
  FROM association AS a;

INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by, created_at)
SELECT 'ap_m29_ent_' || e.id, e.user_id, 'entity', e.id, 'pre_migration', 'pre_migration', e.created_at
  FROM entity AS e;

INSERT INTO artifact_provenance (
  id, user_id, artifact_kind, artifact_id, alias_value, origin_kind, created_by, created_at
)
SELECT 'ap_m29_alias_' || md5(e.id || ':' || al.ord::text || ':' || al.alias_val),
       e.user_id, 'entity_alias', e.id, al.alias_val, 'pre_migration', 'pre_migration', e.created_at
  FROM entity AS e
  CROSS JOIN LATERAL unnest(e.aliases) WITH ORDINALITY AS al(alias_val, ord)
 WHERE btrim(al.alias_val) <> '';

INSERT INTO artifact_provenance (id, user_id, artifact_kind, artifact_id, origin_kind, created_by, created_at)
SELECT 'ap_m29_elink_' || l.id, l.user_id, 'entity_link', l.id, 'pre_migration', 'pre_migration', l.created_at
  FROM entity_link AS l;

ALTER TABLE artifact_provenance ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_provenance FORCE ROW LEVEL SECURITY;
CREATE POLICY artifact_provenance_isolation ON artifact_provenance
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));

-- Commit-time provenance enforcement for association, entity, and entity_link.
CREATE FUNCTION enforce_artifact_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  row_exists boolean := false;
BEGIN
  IF TG_TABLE_NAME = 'association' THEN
    SELECT EXISTS (SELECT 1 FROM association WHERE id = NEW.id AND user_id = NEW.user_id) INTO row_exists;
  ELSIF TG_TABLE_NAME = 'entity' THEN
    SELECT EXISTS (SELECT 1 FROM entity WHERE id = NEW.id AND user_id = NEW.user_id) INTO row_exists;
  ELSIF TG_TABLE_NAME = 'entity_link' THEN
    SELECT EXISTS (SELECT 1 FROM entity_link WHERE id = NEW.id AND user_id = NEW.user_id) INTO row_exists;
  END IF;
  IF NOT row_exists THEN
    RETURN NULL;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM artifact_provenance
     WHERE user_id = NEW.user_id AND artifact_kind = TG_TABLE_NAME AND artifact_id = NEW.id
  ) THEN
    RAISE EXCEPTION '%', TG_TABLE_NAME || '_requires_provenance' USING ERRCODE = 'P0001';
  END IF;
  RETURN NULL;
END
$$;

CREATE CONSTRAINT TRIGGER trg_association_provenance
  AFTER INSERT ON association
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_artifact_provenance();

CREATE CONSTRAINT TRIGGER trg_entity_provenance
  AFTER INSERT ON entity
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_artifact_provenance();

CREATE CONSTRAINT TRIGGER trg_entity_link_provenance
  AFTER INSERT ON entity_link
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_artifact_provenance();

-- Explicit source-deleted tombstoning on memory_version delete (no dangling source_version_id).
CREATE FUNCTION mark_artifact_provenance_source_deleted() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  previous_tenant text := pg_catalog.current_setting('app.user_id', true);
BEGIN
  PERFORM pg_catalog.set_config('app.user_id', OLD.user_id, true);
  UPDATE artifact_provenance
     SET source_version_id = NULL,
         origin_kind = 'source_deleted'
   WHERE user_id = OLD.user_id AND source_version_id = OLD.id;
  PERFORM pg_catalog.set_config('app.user_id', COALESCE(previous_tenant, ''), true);
  RETURN OLD;
END
$$;

CREATE TRIGGER trg_memory_version_mark_artifact_provenance_deleted
  BEFORE DELETE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION mark_artifact_provenance_source_deleted();

-- Clean up attribution rows when their owning association, entity, or entity_link is deleted.
CREATE FUNCTION cascade_artifact_provenance_delete() RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
AS $$
DECLARE
  previous_tenant text := pg_catalog.current_setting('app.user_id', true);
BEGIN
  PERFORM pg_catalog.set_config('app.user_id', OLD.user_id, true);
  IF TG_TABLE_NAME = 'entity' THEN
    DELETE FROM artifact_provenance
     WHERE user_id = OLD.user_id AND artifact_kind IN ('entity', 'entity_alias') AND artifact_id = OLD.id;
  ELSE
    DELETE FROM artifact_provenance
     WHERE user_id = OLD.user_id AND artifact_kind = TG_TABLE_NAME AND artifact_id = OLD.id;
  END IF;
  PERFORM pg_catalog.set_config('app.user_id', COALESCE(previous_tenant, ''), true);
  RETURN NULL;
END
$$;

CREATE TRIGGER trg_association_cascade_provenance
  AFTER DELETE ON association
  FOR EACH ROW
  EXECUTE FUNCTION cascade_artifact_provenance_delete();

CREATE TRIGGER trg_entity_cascade_provenance
  AFTER DELETE ON entity
  FOR EACH ROW
  EXECUTE FUNCTION cascade_artifact_provenance_delete();

CREATE TRIGGER trg_entity_link_cascade_provenance
  AFTER DELETE ON entity_link
  FOR EACH ROW
  EXECUTE FUNCTION cascade_artifact_provenance_delete();

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'enforce_artifact_provenance()',
    'mark_artifact_provenance_source_deleted()',
    'cascade_artifact_provenance_delete()'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
