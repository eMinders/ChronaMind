-- Rollback of migration 17. Run in ONE transaction by whoever operates the schema; the forward runner never executes
-- it. Restores the version-16 shape rule and source trigger exactly. It REFUSES, with a static error, while any
-- 'tool', 'file' or 'event' evidence row exists: version 16 cannot store one, and rewriting it as 'external' would
-- erase the declared type it was recorded with.

DO $$
BEGIN
  IF EXISTS (SELECT 1 FROM memory_evidence WHERE source_type IN ('tool', 'file', 'event')) THEN
    RAISE EXCEPTION 'm17_rollback_refused_declared_source_rows_exist' USING ERRCODE = 'P0001';
  END IF;
END
$$;

ALTER TABLE memory_evidence
  DROP CONSTRAINT memory_evidence_turn_source_check,
  ADD CONSTRAINT memory_evidence_turn_source_check CHECK (
    (source_type = 'turn' AND turn_id IS NOT NULL AND source_ref = turn_id AND span = evidence_span)
    OR (source_type = 'external' AND turn_id IS NULL)
  );

CREATE OR REPLACE FUNCTION sync_memory_evidence_source() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  turn_content text;
BEGIN
  IF NEW.source_type IS NULL THEN
    -- A v1 writer: the evidence is a turn.
    IF NEW.turn_id IS NULL THEN
      RAISE EXCEPTION 'evidence_source_required' USING ERRCODE = 'P0001';
    END IF;
    -- Only this tenant's own turn counts (V2-D54): an unknown turn and another tenant's turn look the same, and the
    -- composite foreign key rejects both with its usual error. NOT NULL is checked before foreign keys, so such a row
    -- gets an all-zero placeholder hash here and is then refused by the foreign key.
    SELECT content INTO turn_content FROM turn WHERE id = NEW.turn_id AND user_id = NEW.user_id;
    NEW.source_type := 'turn';
    NEW.source_ref := NEW.turn_id;
    NEW.span := NEW.evidence_span;
    NEW.content_hash := CASE WHEN turn_content IS NULL THEN repeat('0', 64) ELSE encode(
      sha256(convert_to(
        replace(replace(normalize(turn_content, NFC), E'\r\n', E'\n'), E'\r', E'\n'), 'UTF8'
      )), 'hex'
    ) END;
    RETURN NEW;
  END IF;

  IF NEW.source_type = 'external' THEN
    IF NEW.turn_id IS NOT NULL THEN
      RAISE EXCEPTION 'external_source_has_no_turn' USING ERRCODE = 'P0001';
    END IF;
    IF NEW.evidence_span IS NULL THEN
      NEW.evidence_span := NEW.span;  -- the legacy column mirrors the locator
    END IF;
  END IF;
  RETURN NEW;
END
$$;

DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
BEGIN
  EXECUTE pg_catalog.format(
    'ALTER FUNCTION %I.sync_memory_evidence_source() SET search_path = %I, pg_catalog, pg_temp',
    engine_schema, engine_schema
  );
END
$pin$;

DELETE FROM _cme_schema_migrations WHERE version = 17;
