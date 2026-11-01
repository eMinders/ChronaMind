-- Migration 8: generalised evidence source (M4) - E-013, V2-D06, V2-D41, D-ENG-04.
--
-- In v1 evidence can only be a chat turn: memory_evidence.turn_id was NOT NULL REFERENCES turn. A general engine needs
-- other evidence (tool output, diffs, file spans, papers, external artifacts), so evidence becomes a SOURCE reference
-- (source_type, source_ref, span, content_hash).
--
-- Decisions followed (not re-opened):
--   shape      columns on memory_evidence, not a separate table (MIGRATION_RISK.md M4): one column cannot be a foreign key
--              for only some rows, so `turn_id` is KEPT, nullable, with its foreign key, and a CHECK ties it to the type.
--   vocabulary V2-D41 declares exactly two source types: `turn` and `external` (an `artifact` is never evidence). The
--              vocabulary is closed in the schema; adding a type is a migration, not a profile setting.
--   hash       V2-D41: SHA-256 over the NFC-normalised, LF-newline UTF-8 text (grounding.sources.canonical_hash). For a
--              turn it is the hash of the turn's content, for an external source of the captured text.
--
-- Existing turn evidence is byte-identical: the old columns are never written by this migration. The backfill fills only
-- the new ones, and a CHECK keeps them equal to the old ones for every turn source.
--
-- Dual-write window (as migration 4). v1 writers still insert (version_id, turn_id, user_id, evidence_span,
-- evidence_role); `sync_memory_evidence_source` derives the source columns for them, so none had to change for the turn
-- case. A writer that supplies a source is validated, not overwritten.
--
-- Deletion contract (ARCHITECTURE_V2 2.1, D-ENG-04): an `external` source lies OUTSIDE the engine's deletion boundary.
-- Deleting an episode deletes the row and its derivations; the external artifact itself is not and cannot be erased by
-- the engine, so the deletion receipt LISTS it as an exclusion (engine/deletion.py), exactly as WAL and backups are.

ALTER TABLE memory_evidence
  ADD COLUMN source_type text,
  ADD COLUMN source_ref text,
  ADD COLUMN span text,
  ADD COLUMN content_hash text;

CREATE FUNCTION sync_memory_evidence_source() RETURNS trigger
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

CREATE TRIGGER trg_memory_evidence_source_dual_write
  BEFORE INSERT ON memory_evidence
  FOR EACH ROW
  EXECUTE FUNCTION sync_memory_evidence_source();

-- Backfill: ('turn', turn_id, evidence_span, sha256(canonical turn content)); the old columns are not touched.
UPDATE memory_evidence e
   SET source_type = 'turn',
       source_ref = e.turn_id,
       span = e.evidence_span,
       content_hash = encode(
         sha256(convert_to(
           replace(replace(normalize(t.content, NFC), E'\r\n', E'\n'), E'\r', E'\n'), 'UTF8'
         )), 'hex'
       )
  FROM turn t
 WHERE t.id = e.turn_id AND e.source_type IS NULL;

-- A primary-key column cannot lose NOT NULL, so the key is replaced first (separate statements).
ALTER TABLE memory_evidence DROP CONSTRAINT memory_evidence_pkey;
ALTER TABLE memory_evidence
  ALTER COLUMN turn_id DROP NOT NULL,
  ALTER COLUMN source_type SET NOT NULL,
  ALTER COLUMN source_ref SET NOT NULL,
  ALTER COLUMN span SET NOT NULL,
  ALTER COLUMN content_hash SET NOT NULL,
  ADD CONSTRAINT memory_evidence_pkey PRIMARY KEY (version_id, source_type, source_ref, span),
  ADD CONSTRAINT memory_evidence_source_type_check CHECK (source_type IN ('turn', 'external')),
  ADD CONSTRAINT memory_evidence_content_hash_check CHECK (content_hash ~ '^[a-f0-9]{64}$'),
  ADD CONSTRAINT memory_evidence_source_nonblank_check CHECK (btrim(source_ref) <> '' AND btrim(span) <> ''),
  -- `turn_id` and its foreign key stay for turn sources and are forbidden for the others; for a turn the new columns
  -- must equal the old ones, which is what makes the backfill byte-identical by construction.
  ADD CONSTRAINT memory_evidence_turn_source_check CHECK (
    (source_type = 'turn' AND turn_id IS NOT NULL AND source_ref = turn_id AND span = evidence_span)
    OR (source_type = 'external' AND turn_id IS NULL)
  );

-- V2-D54 (S-02): every engine function is pinned to `<this schema>, pg_catalog, pg_temp`.
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
