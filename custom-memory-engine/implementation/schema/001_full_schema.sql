-- Full 12-table schema for TECHNICAL_SPECIFICATION.md §3 and Proposal §8 (RL-040).
-- Apply into the current search_path. Requires extensions vector and btree_gist.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE app_user (
  id           text PRIMARY KEY,
  external_ref text NOT NULL UNIQUE,
  created_at   timestamptz NOT NULL
);

CREATE TABLE session (
  id         text PRIMARY KEY,
  user_id    text NOT NULL REFERENCES app_user (id),
  started_at timestamptz NOT NULL,
  ended_at   timestamptz,
  CONSTRAINT session_bounds CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE turn (
  id          text PRIMARY KEY,
  session_id  text NOT NULL REFERENCES session (id),
  user_id     text NOT NULL REFERENCES app_user (id),
  sequence_no int NOT NULL,
  role        text NOT NULL CHECK (role IN ('user', 'assistant')),
  content     text NOT NULL,
  occurred_at timestamptz NOT NULL,
  UNIQUE (session_id, sequence_no)
);

CREATE TABLE predicate_schema (
  predicate_key text PRIMARY KEY,
  cardinality   text NOT NULL CHECK (cardinality IN ('single', 'multi', 'event')),
  default_layer text NOT NULL CHECK (default_layer IN ('episodic', 'semantic', 'opinion', 'persona'))
);

-- Column order 1–10 matches validation/bitemporal_checks.sql so positional INSERTs remain valid.
CREATE TABLE memory_version (
  id                   text PRIMARY KEY,
  memory_key           text NOT NULL,
  user_id              text NOT NULL REFERENCES app_user (id),
  status               text NOT NULL CHECK (status IN ('candidate', 'conflict_pending', 'rejected', 'active', 'archived')),
  subject_key          text NOT NULL,
  predicate_key        text NOT NULL REFERENCES predicate_schema (predicate_key),
  single_valued        boolean NOT NULL,
  content              text NOT NULL,
  valid_range          tstzrange NOT NULL,
  recorded_range       tstzrange,
  layer                text NOT NULL DEFAULT 'semantic'
                       CHECK (layer IN ('episodic', 'semantic', 'opinion', 'persona')),
  valid_from_basis     text NOT NULL DEFAULT 'inferred'
                       CHECK (valid_from_basis IN ('explicit', 'inferred')),
  assertion_mode       text NOT NULL DEFAULT 'asserted'
                       CHECK (assertion_mode IN (
                         'asserted', 'hedged', 'hypothetical', 'planned', 'quoted', 'third_party', 'joking'
                       )),
  confidence           real,
  importance           real,
  reinforcement_count  int NOT NULL DEFAULT 0,
  extractor_version    text NOT NULL DEFAULT 'schema-check',
  archival_branch      text CHECK (archival_branch IN ('S', 'C')),
  CONSTRAINT valid_wellformed CHECK (NOT isempty(valid_range) AND NOT lower_inf(valid_range)),
  CONSTRAINT recorded_iff_believed CHECK ((status IN ('active', 'archived')) = (recorded_range IS NOT NULL)),
  CONSTRAINT recorded_wellformed CHECK (
    recorded_range IS NULL OR (NOT isempty(recorded_range) AND NOT lower_inf(recorded_range))
  ),
  CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist (
    user_id WITH =,
    subject_key WITH =,
    predicate_key WITH =,
    valid_range WITH &&,
    recorded_range WITH &&
  ) WHERE (single_valued AND status IN ('active', 'archived'))
);

CREATE TABLE memory_evidence (
  version_id    text NOT NULL REFERENCES memory_version (id) ON DELETE CASCADE,
  turn_id       text NOT NULL REFERENCES turn (id),
  user_id       text NOT NULL REFERENCES app_user (id),
  evidence_span text NOT NULL,
  evidence_role text NOT NULL,
  PRIMARY KEY (version_id, turn_id, evidence_span)
);

CREATE TABLE memory_derivation (
  derived_version_id text NOT NULL REFERENCES memory_version (id) ON DELETE CASCADE,
  source_version_id  text NOT NULL REFERENCES memory_version (id),
  user_id            text NOT NULL REFERENCES app_user (id),
  derivation_type    text NOT NULL CHECK (
    derivation_type IN ('consolidation', 'summary', 'corroboration', 're_derivation')
  ),
  PRIMARY KEY (derived_version_id, source_version_id),
  CONSTRAINT derivation_not_self CHECK (derived_version_id <> source_version_id)
);

CREATE TABLE memory_embedding (
  version_id      text NOT NULL REFERENCES memory_version (id) ON DELETE CASCADE,
  embedding_model text NOT NULL,
  user_id         text NOT NULL REFERENCES app_user (id),
  embedding       vector(384) NOT NULL,
  PRIMARY KEY (version_id, embedding_model)
);

CREATE TABLE memory_conflict (
  id                    text PRIMARY KEY,
  candidate_version_id  text NOT NULL REFERENCES memory_version (id) ON DELETE CASCADE,
  existing_version_id   text REFERENCES memory_version (id),
  user_id               text NOT NULL REFERENCES app_user (id),
  relation              text NOT NULL,
  decision              text NOT NULL CHECK (decision IN ('accept', 'hold', 'reject')),
  reason_code           text NOT NULL,
  decided_by            text NOT NULL,
  decided_at            timestamptz NOT NULL
);

CREATE TABLE audit_event (
  id         text PRIMARY KEY,
  user_id    text NOT NULL REFERENCES app_user (id),
  memory_ref text NOT NULL,
  event_type text NOT NULL,
  event_at   timestamptz NOT NULL,
  details    jsonb NOT NULL,
  CONSTRAINT audit_details_content_free CHECK (
    NOT (details ? 'content' OR details ? 'evidence_span' OR details ? 'embedding')
  )
);

CREATE TABLE deletion_request (
  id             text PRIMARY KEY,
  user_id        text NOT NULL REFERENCES app_user (id),
  scope          text NOT NULL CHECK (scope IN ('memory', 'topic', 'all')),
  target_ref     text NOT NULL,
  status         text NOT NULL CHECK (status IN ('pending', 'running', 'completed', 'failed')),
  deletion_epoch bigint NOT NULL,
  requested_at   timestamptz NOT NULL,
  completed_at   timestamptz
);

CREATE TABLE job (
  id                     text PRIMARY KEY,
  user_id                text NOT NULL REFERENCES app_user (id),
  kind                   text NOT NULL,
  turn_id                text REFERENCES turn (id),
  idempotency_key        text NOT NULL UNIQUE,
  status                 text NOT NULL CHECK (status IN ('queued', 'running', 'done', 'dead_letter')),
  attempts               int NOT NULL DEFAULT 0,
  next_run_at            timestamptz NOT NULL,
  payload                jsonb,
  extractor_version      text,
  candidate_fingerprint  text,
  CONSTRAINT job_extraction_idempotent UNIQUE (user_id, turn_id, extractor_version, candidate_fingerprint)
);

-- Transaction clock: one instant per transaction, so `recorded_range` bounds written by several
-- statements in the same transaction share `c` (R1). A replay harness overrides it with `app.clock`.
CREATE FUNCTION clock_now() RETURNS timestamptz
LANGUAGE sql
STABLE
AS $$
  SELECT COALESCE(NULLIF(current_setting('app.clock', true), '')::timestamptz, transaction_timestamp())
$$;

CREATE FUNCTION guard_update() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (
       NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.predicate_key, NEW.single_valued,
       NEW.content, NEW.valid_range, NEW.layer, NEW.valid_from_basis, NEW.assertion_mode,
       NEW.confidence, NEW.importance, NEW.reinforcement_count, NEW.extractor_version
     ) IS DISTINCT FROM (
       OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.predicate_key, OLD.single_valued,
       OLD.content, OLD.valid_range, OLD.layer, OLD.valid_from_basis, OLD.assertion_mode,
       OLD.confidence, OLD.importance, OLD.reinforcement_count, OLD.extractor_version
     )
  THEN
    RAISE EXCEPTION 'only status and the upper bound of recorded_range may change';
  END IF;
  IF NEW.archival_branch IS DISTINCT FROM OLD.archival_branch THEN
    IF NOT (OLD.archival_branch IS NULL AND NEW.archival_branch IN ('S', 'C') AND NEW.status = 'archived') THEN
      RAISE EXCEPTION 'archival_branch may only be set by an archive transition (status = archived)';
    END IF;
  END IF;
  IF NEW.recorded_range IS DISTINCT FROM OLD.recorded_range THEN
    IF OLD.recorded_range IS NULL AND NEW.status = 'active' AND upper_inf(NEW.recorded_range)
       AND lower(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSIF OLD.recorded_range IS NOT NULL AND upper_inf(OLD.recorded_range)
       AND lower(NEW.recorded_range) = lower(OLD.recorded_range)
       AND NOT upper_inf(NEW.recorded_range)
       AND upper(NEW.recorded_range) = clock_now() THEN
      NULL;
    ELSE
      RAISE EXCEPTION 'illegal recorded_range change';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE TRIGGER trg_memory_version_guard_update
  BEFORE UPDATE ON memory_version
  FOR EACH ROW
  EXECUTE FUNCTION guard_update();

CREATE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

CREATE CONSTRAINT TRIGGER trg_memory_version_provenance
  AFTER INSERT OR UPDATE ON memory_version
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  EXECUTE FUNCTION enforce_provenance();

ALTER TABLE app_user ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_user FORCE ROW LEVEL SECURITY;
CREATE POLICY app_user_isolation ON app_user
  USING (id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE session ENABLE ROW LEVEL SECURITY;
ALTER TABLE session FORCE ROW LEVEL SECURITY;
CREATE POLICY session_isolation ON session
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE turn ENABLE ROW LEVEL SECURITY;
ALTER TABLE turn FORCE ROW LEVEL SECURITY;
CREATE POLICY turn_isolation ON turn
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE memory_version ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_version FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_version_isolation ON memory_version
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE memory_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_evidence FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_evidence_isolation ON memory_evidence
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE memory_derivation ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_derivation FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_derivation_isolation ON memory_derivation
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE memory_embedding ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_embedding FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_embedding_isolation ON memory_embedding
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE memory_conflict ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_conflict FORCE ROW LEVEL SECURITY;
CREATE POLICY memory_conflict_isolation ON memory_conflict
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE audit_event ENABLE ROW LEVEL SECURITY;
ALTER TABLE audit_event FORCE ROW LEVEL SECURITY;
CREATE POLICY audit_event_isolation ON audit_event
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE deletion_request ENABLE ROW LEVEL SECURITY;
ALTER TABLE deletion_request FORCE ROW LEVEL SECURITY;
CREATE POLICY deletion_request_isolation ON deletion_request
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));

ALTER TABLE job ENABLE ROW LEVEL SECURITY;
ALTER TABLE job FORCE ROW LEVEL SECURITY;
CREATE POLICY job_isolation ON job
  USING (user_id = NULLIF(current_setting('app.user_id', true), ''))
  WITH CHECK (user_id = NULLIF(current_setting('app.user_id', true), ''));
