-- Migration 17: tool / file / event evidence is stored under its declared type (V2-D66, V2-D41 extension; E-021 #136).
--
-- Migration 16 widened `memory_evidence_source_type_check` to admit 'tool', 'file' and 'event', and its comment says
-- such rows "satisfy the OR branch for 'external'" of `memory_evidence_turn_source_check`. They do not: that branch
-- is `source_type = 'external' AND turn_id IS NULL`, so a row that says 'tool' is refused, and the source trigger of
-- migration 8 mirrors the legacy span only for 'external'. The API therefore wrote every non-turn source as
-- 'external', and the declared type a coding agent sent was not recorded. Migration 16 has shipped and is
-- checksum-bound (MA-016 X1), so the correction is this additive migration:
--
-- 1. the shape rule gives 'tool', 'file' and 'event' exactly the branch 'external' has: no turn, a reference, a hash;
-- 2. the source trigger treats them as it treats 'external' (no turn; the legacy span mirrors the locator).
--
-- Existing rows satisfy the wider rule; nothing is backfilled. 'external' rows written for a tool/file/event source
-- before this migration keep the type they were recorded with: the declared type was never stored, so it cannot be
-- recovered, and inventing it would be a fabricated provenance.

ALTER TABLE memory_evidence
  DROP CONSTRAINT memory_evidence_turn_source_check,
  ADD CONSTRAINT memory_evidence_turn_source_check CHECK (
    (source_type = 'turn' AND turn_id IS NOT NULL AND source_ref = turn_id AND span = evidence_span)
    OR (source_type IN ('external', 'tool', 'file', 'event') AND turn_id IS NULL)
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

  IF NEW.source_type IN ('external', 'tool', 'file', 'event') THEN
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

-- V2-D54 (S-02): CREATE OR REPLACE resets the function's settings, so it is pinned again.
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
