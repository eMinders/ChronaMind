-- Migration 10: structural tables (M6) and the lifecycle records they enable - E-014 (#65), E-016 (#67),
-- V2-D04, V2-D13, V2-D25, V2-D33, V2-D46, V2-D48, V2-D49 D7, V2-D54.
--
--   ASSOCIATION          relatedness that is NOT provenance: deleting an endpoint deletes the row, never the other endpoint.
--   ENTITY               principal-scoped canonical referent; opaque ids; invisible outside the caller's own namespace.
--   METHOD               append-only registry of inference operators (I3); `register_method` is its only writer (V2-D46).
--   ABSTRACTION_STATS    the stored ledger: o = s + k + u, `stale` is NOT stored (V2-D24).
--   VERIFICATION         held-out verification record with the data to check disjointness from the support set (I11).
--   NEGATIVE_CONSTRAINT  a user's sticky rejection of an inferred element, with a lift record (V2-D33, I16).
--
-- RLS. E-011 (the scope hierarchy) is not built, so every tenant table here carries the same forced per-user isolation
-- policy as the v1 tables (`user_id = app.user_id`) and E-011 migrates them with the rest. METHOD is a shared catalog
-- (like predicate_schema): readable and appendable by everyone, never updatable or deletable. Every foreign key between
-- tenant tables is composite on (id, user_id) and every function is pinned to the engine schema (V2-D54).
--
-- This migration also closes the record half of E-016. `memory_version.method_id` is the method reference of I1':
--   * an active `inferred` row needs a derivation row AND a method at commit (v1 derived claims and v1 persona rows get
--     the seeded v1 methods below, so the dual-write window keeps working, and the persona derivation exemption stays
--     only because a v1 persona carries evidence, not a derivation row);
--   * an abstraction needs its ledger row (I15). v1 persona rows get the zero ledger of V2-D49 D7 (s = k = u = o = 0,
--     unverified), so they can never be verified or rendered in `facts`;
--   * every recorded lifecycle transition of ARCHITECTURE_V2 2.3 needs its record at commit (a deferred constraint
--     trigger, so deletion judges the final state): contested -> asserted needs an accept decision, contested ->
--     retired a reject decision (both are `memory_conflict` rows), asserted/observed -> retired a closed recorded range
--     or an archival branch, inferred -> retired one of those or a refuting VERIFICATION or a dissent,
--     inferred -> contested a contradicting Tier C association. The v1 staging promotion (`candidate` -> active) and
--     the dual-write `-> inferred` corrections need none (migration 7).

-- 1. METHOD ---------------------------------------------------------------------------------------------------------
CREATE TABLE method (
  id                 text PRIMARY KEY,
  name               text NOT NULL CHECK (btrim(name) <> ''),
  version            text NOT NULL CHECK (btrim(version) <> ''),
  params_schema_hash text NOT NULL CHECK (params_schema_hash ~ '^[a-f0-9]{64}$'),
  description        text NOT NULL DEFAULT '',
  registered_at      timestamptz NOT NULL DEFAULT clock_now(),
  CONSTRAINT method_name_version_key UNIQUE (name, version)
);

CREATE FUNCTION guard_method_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'method_registry_is_append_only' USING ERRCODE = 'P0001';
END
$$;

CREATE TRIGGER trg_method_append_only BEFORE UPDATE OR DELETE ON method
  FOR EACH ROW EXECUTE FUNCTION guard_method_append_only();
CREATE TRIGGER trg_method_no_truncate BEFORE TRUNCATE ON method
  FOR EACH STATEMENT EXECUTE FUNCTION guard_method_append_only();

ALTER TABLE method ENABLE ROW LEVEL SECURITY;
ALTER TABLE method FORCE ROW LEVEL SECURITY;
CREATE POLICY method_read ON method FOR SELECT USING (true);
CREATE POLICY method_append ON method FOR INSERT WITH CHECK (true);

-- The v1 operators, so artifacts the v1 code derives carry a registered method.
INSERT INTO method (id, name, version, params_schema_hash, description) VALUES
  ('m-v1-consolidation', 'v1-consolidation', '1', encode(sha256('v1-consolidation:1'::bytea), 'hex'), 'v1 consolidation'),
  ('m-v1-summary', 'v1-summary', '1', encode(sha256('v1-summary:1'::bytea), 'hex'), 'v1 summary'),
  ('m-v1-corroboration', 'v1-corroboration', '1', encode(sha256('v1-corroboration:1'::bytea), 'hex'), 'v1 corroboration'),
  ('m-v1-re-derivation', 'v1-re-derivation', '1', encode(sha256('v1-re-derivation:1'::bytea), 'hex'), 'v1 re-derivation'),
  ('m-v1-persona', 'v1-persona', '1', encode(sha256('v1-persona:1'::bytea), 'hex'), 'v1 persona layer');

-- 2. The method reference on memory_version (I3) ------------------------------------------------------------------
ALTER TABLE memory_version ADD COLUMN method_id text REFERENCES method (id);

-- Set once, never changed (I3: the method an artifact was produced with is a fact about it).
CREATE FUNCTION assign_memory_version_method() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.method_id IS NOT NULL AND NEW.method_id IS DISTINCT FROM OLD.method_id THEN
    RAISE EXCEPTION 'method_id_is_fixed_once_set' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.method_id IS NULL AND NEW.lifecycle = 'inferred' AND NEW.layer = 'persona' THEN
    NEW.method_id := 'm-v1-persona';
  END IF;
  RETURN NEW;
END
$$;

-- Fires after sync_memory_version_axes and the lifecycle guard (trigger-name order) so it sees the final lifecycle.
CREATE TRIGGER trg_memory_version_method_reference
  BEFORE INSERT OR UPDATE ON memory_version
  FOR EACH ROW EXECUTE FUNCTION assign_memory_version_method();

-- A version is inserted before its derivation rows, so its method follows the first derivation (migration 5).
CREATE OR REPLACE FUNCTION mark_derived_version_inferred() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE memory_version
     SET lifecycle = 'inferred',
         method_id = COALESCE(method_id, CASE NEW.derivation_type
           WHEN 'consolidation' THEN 'm-v1-consolidation'
           WHEN 'summary' THEN 'm-v1-summary'
           WHEN 'corroboration' THEN 'm-v1-corroboration'
           ELSE 'm-v1-re-derivation' END)
   WHERE id = NEW.derived_version_id
     AND status = 'active'
     AND kind = 'claim'
     AND lifecycle = 'asserted';
  RETURN NULL;
END
$$;

-- Backfill: existing inferred rows get the method of their first derivation, persona rows the persona method.
-- The owner is subject to FORCE ROW LEVEL SECURITY, so a role that does not bypass RLS would match zero rows and
-- leave every existing artifact without its method - which then fails as `inferred_requires_method_record` on the
-- next write to that row. The backfill therefore lifts FORCE for its own statements and restores it. Evidence and
-- derivation are lifted too: firing the deferred provenance check below re-reads both under the reader's policy, and
-- with no `app.user_id` set it would otherwise see nothing and refuse every row.
ALTER TABLE memory_version NO FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_evidence NO FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_derivation NO FORCE ROW LEVEL SECURITY;
UPDATE memory_version v
   SET method_id = CASE
         WHEN v.layer = 'persona' THEN 'm-v1-persona'
         ELSE COALESCE(
           (SELECT CASE d.derivation_type
                     WHEN 'consolidation' THEN 'm-v1-consolidation'
                     WHEN 'summary' THEN 'm-v1-summary'
                     WHEN 'corroboration' THEN 'm-v1-corroboration'
                     ELSE 'm-v1-re-derivation' END
              FROM memory_derivation d WHERE d.derived_version_id = v.id ORDER BY d.source_version_id LIMIT 1),
           'm-v1-re-derivation')
       END
 WHERE v.lifecycle = 'inferred' AND v.method_id IS NULL;
-- Dispatch the deferred events this UPDATE queued before restoring FORCE: PostgreSQL refuses `ALTER TABLE` while a
-- relation has pending trigger events, and the check is the point of the backfill.
SET CONSTRAINTS trg_memory_version_provenance IMMEDIATE;
SET CONSTRAINTS trg_memory_version_provenance DEFERRED;
ALTER TABLE memory_derivation FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_version FORCE ROW LEVEL SECURITY;

-- 3. ABSTRACTION_STATS ----------------------------------------------------------------------------------------------
CREATE TABLE abstraction_stats (
  version_id            text PRIMARY KEY,
  user_id               text NOT NULL,
  support_count         integer NOT NULL DEFAULT 0 CHECK (support_count >= 0),
  counter_count         integer NOT NULL DEFAULT 0 CHECK (counter_count >= 0),
  undetermined_count    integer NOT NULL DEFAULT 0 CHECK (undetermined_count >= 0),
  opportunity_count     integer NOT NULL DEFAULT 0 CHECK (opportunity_count >= 0),
  coverage              double precision GENERATED ALWAYS AS (
                          CASE WHEN opportunity_count = 0 THEN 0
                               ELSE (support_count + counter_count)::double precision / opportunity_count END
                        ) STORED,
  evidence_generation   integer NOT NULL DEFAULT 0 CHECK (evidence_generation >= 0),
  counter_absence_basis text CHECK (counter_absence_basis IN ('high_opportunity', 'low_opportunity')),
  refutation_attempts   integer NOT NULL DEFAULT 0 CHECK (refutation_attempts >= 0),
  confidence            double precision CHECK (confidence >= 0 AND confidence <= 1),
  confidence_method_id  text REFERENCES method (id),
  closed_over_hash      text CHECK (closed_over_hash ~ '^[a-f0-9]{64}$'),
  verification_status   text NOT NULL DEFAULT 'unverified' CHECK (verification_status IN ('unverified', 'verified', 'refuted')),
  expires_at            timestamptz,
  CONSTRAINT abstraction_stats_ledger_balances CHECK (opportunity_count = support_count + counter_count + undetermined_count),
  CONSTRAINT abstraction_stats_confidence_has_method CHECK (confidence IS NULL OR confidence_method_id IS NOT NULL),
  CONSTRAINT abstraction_stats_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE
);
-- `stale` is deliberately not a column (V2-D24): it is computed at read time from the five causes.

CREATE FUNCTION guard_abstraction_stats() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  version_kind text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT kind INTO version_kind FROM memory_version WHERE id = NEW.version_id AND user_id = NEW.user_id;
    IF version_kind IS DISTINCT FROM 'abstraction' THEN
      RAISE EXCEPTION 'abstraction_stats_require_an_abstraction' USING ERRCODE = 'P0001';
    END IF;
    IF NEW.verification_status <> 'unverified' THEN
      RAISE EXCEPTION 'abstraction_stats_start_unverified' USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.version_id <> OLD.version_id OR NEW.user_id <> OLD.user_id THEN
    RAISE EXCEPTION 'abstraction_stats_identity_is_fixed' USING ERRCODE = 'P0001';
  END IF;
  -- Verification is a record, not a flag (E-016): the held-out check must exist before the status can say so.
  IF NEW.verification_status <> OLD.verification_status
     AND NEW.verification_status <> 'unverified'
     AND NOT EXISTS (
       SELECT 1 FROM verification
        WHERE version_id = NEW.version_id AND user_id = NEW.user_id
          AND outcome = NEW.verification_status
     ) THEN
    RAISE EXCEPTION 'verification_status_requires_a_verification_record' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.verification_status = 'unverified' AND OLD.verification_status <> 'unverified' THEN
    RAISE EXCEPTION 'verification_cannot_be_withdrawn' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;

-- 4. VERIFICATION ---------------------------------------------------------------------------------------------------
CREATE TABLE verification (
  id                        text PRIMARY KEY,
  version_id                text NOT NULL,
  user_id                   text NOT NULL,
  held_out_split_id         text NOT NULL CHECK (btrim(held_out_split_id) <> ''),
  verified_on               timestamptz NOT NULL,
  negative_control_slice_id text NOT NULL CHECK (btrim(negative_control_slice_id) <> ''),
  -- the data needed to check disjointness from the support set (I11): a temporal window and a context key per side
  support_window            tstzrange NOT NULL CHECK (NOT isempty(support_window)),
  held_out_window           tstzrange NOT NULL CHECK (NOT isempty(held_out_window)),
  support_context_key       text NOT NULL,
  held_out_context_key      text NOT NULL,
  windows_disjoint          boolean GENERATED ALWAYS AS (NOT (support_window && held_out_window)) STORED,
  outcome                   text NOT NULL CHECK (outcome IN ('verified', 'refuted')),
  CONSTRAINT verification_version_same_tenant
    FOREIGN KEY (version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE
);
CREATE INDEX verification_version_idx ON verification (user_id, version_id);

CREATE FUNCTION guard_verification_append_only() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  RAISE EXCEPTION 'verification_is_append_only' USING ERRCODE = 'P0001';
END
$$;

-- Append-only is worth nothing if the record can be removed: a refuted verification that disappears would let an
-- abstraction keep a `verified` status with nothing behind it, and I11 is exactly that record. So a direct DELETE is
-- refused too. A row may still go when its version does (`ON DELETE CASCADE`), which is the one legal removal: by the
-- time the cascade reaches this row its parent is already gone. TRUNCATE needs no trigger of its own - the runtime role
-- has no privilege for it and the cascade cannot issue one.
CREATE FUNCTION guard_verification_no_delete() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM memory_version WHERE id = OLD.version_id) THEN
    RAISE EXCEPTION 'verification_is_append_only' USING ERRCODE = 'P0001';
  END IF;
  RETURN OLD;
END
$$;

CREATE TRIGGER trg_verification_append_only BEFORE UPDATE ON verification
  FOR EACH ROW EXECUTE FUNCTION guard_verification_append_only();
CREATE TRIGGER trg_verification_no_delete BEFORE DELETE ON verification
  FOR EACH ROW EXECUTE FUNCTION guard_verification_no_delete();
CREATE TRIGGER trg_verification_no_truncate BEFORE TRUNCATE ON verification
  FOR EACH STATEMENT EXECUTE FUNCTION guard_verification_append_only();

CREATE TRIGGER trg_abstraction_stats_guard BEFORE INSERT OR UPDATE ON abstraction_stats
  FOR EACH ROW EXECUTE FUNCTION guard_abstraction_stats();

-- v1 persona rows get the zero ledger of V2-D49 D7: o = 0 is "insufficient evidence", so they can never be verified.
CREATE FUNCTION create_zero_ledger() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  INSERT INTO abstraction_stats (version_id, user_id) VALUES (NEW.id, NEW.user_id) ON CONFLICT (version_id) DO NOTHING;
  RETURN NULL;
END
$$;
CREATE TRIGGER trg_memory_version_zero_ledger AFTER INSERT ON memory_version
  FOR EACH ROW WHEN (NEW.kind = 'abstraction' AND NEW.layer = 'persona') EXECUTE FUNCTION create_zero_ledger();

-- Every existing abstraction needs a ledger too (I15), not only the persona rows. Both sides of this statement are
-- behind a forced policy: the SELECT on memory_version and the WITH CHECK on abstraction_stats. Under a non-bypass
-- owner it would insert nothing and leave every existing abstraction un-updatable, so FORCE is lifted for it.
ALTER TABLE memory_version NO FORCE ROW LEVEL SECURITY;
ALTER TABLE abstraction_stats NO FORCE ROW LEVEL SECURITY;
INSERT INTO abstraction_stats (version_id, user_id)
SELECT id, user_id FROM memory_version WHERE kind = 'abstraction' ON CONFLICT (version_id) DO NOTHING;
ALTER TABLE abstraction_stats FORCE ROW LEVEL SECURITY;
ALTER TABLE memory_version FORCE ROW LEVEL SECURITY;

-- 5. ASSOCIATION ----------------------------------------------------------------------------------------------------
CREATE TABLE association (
  id         text PRIMARY KEY,
  user_id    text NOT NULL,
  from_id    text NOT NULL,
  to_id      text NOT NULL,
  type       text NOT NULL CHECK (type IN (
               'related_to', 'contradicts', 'refines', 'specialises', 'instance_of',
               'caused_by', 'precedes', 'part_of', 'corroborated_by', 'supersedes'
             )),
  weight     double precision NOT NULL DEFAULT 1 CHECK (weight >= 0 AND weight <= 1),
  created_by text NOT NULL CHECK (btrim(created_by) <> ''),
  -- V2-D48 C-P4: a Tier B `contradicts` edge is recorded with no channel effect.
  origin     text NOT NULL DEFAULT 'tier_c' CHECK (origin IN ('tier_b', 'tier_c')),
  created_at timestamptz NOT NULL DEFAULT clock_now(),
  CONSTRAINT association_no_self_loop CHECK (from_id <> to_id),
  CONSTRAINT association_edge_unique UNIQUE (user_id, from_id, to_id, type),
  -- Deleting an endpoint deletes the association; nothing cascades onward to the other endpoint (this is not provenance).
  CONSTRAINT association_from_same_tenant
    FOREIGN KEY (from_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE,
  CONSTRAINT association_to_same_tenant
    FOREIGN KEY (to_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE CASCADE
);
CREATE INDEX association_from_idx ON association (user_id, from_id);
CREATE INDEX association_to_idx ON association (user_id, to_id);
-- V2-D13: the per-artifact degree cap (default 32), enforced by migration 3's function.
CREATE TRIGGER trg_association_cap BEFORE INSERT OR UPDATE OF from_id, to_id ON association
  FOR EACH ROW EXECUTE FUNCTION enforce_association_cap('32');

-- 6. ENTITY ---------------------------------------------------------------------------------------------------------
CREATE TABLE entity (
  -- Opaque: random, never derived from the name or any content (ARCHITECTURE_V2 5.2).
  id             text PRIMARY KEY DEFAULT 'ent_' || replace(gen_random_uuid()::text, '-', ''),
  user_id        text NOT NULL REFERENCES app_user (id),
  scope_path     text NOT NULL,
  canonical_name text NOT NULL CHECK (btrim(canonical_name) <> ''),
  entity_type    text NOT NULL CHECK (btrim(entity_type) <> ''),
  aliases        text[] NOT NULL DEFAULT '{}',
  created_at     timestamptz NOT NULL DEFAULT clock_now(),
  CONSTRAINT entity_id_user_id_key UNIQUE (id, user_id),
  -- principal-scoped: the entity lives inside its creator's own namespace (an exact literal prefix, never LIKE)
  CONSTRAINT entity_within_own_namespace CHECK (pg_catalog.starts_with(scope_path, '/u/' || user_id || '/'))
);

-- 7. NEGATIVE_CONSTRAINT --------------------------------------------------------------------------------------------
CREATE TABLE negative_constraint (
  id                    text PRIMARY KEY,
  user_id               text NOT NULL REFERENCES app_user (id),
  predicate_key         text NOT NULL CHECK (btrim(predicate_key) <> ''),
  scope_path            text NOT NULL,
  method_family         text NOT NULL CHECK (btrim(method_family) <> ''),
  dissented_at          timestamptz NOT NULL,
  reason                text NOT NULL CHECK (btrim(reason) <> ''),
  -- the inferred artifact the user dissented from, so a retirement by dissent has its record (E-016)
  dissented_version_id  text,
  -- a lift record: who, when, and which new grounded evidence (I16); all three or none
  lifted_at             timestamptz,
  lifted_by             text,
  lift_evidence_version_id text,
  CONSTRAINT negative_constraint_id_user_id_key UNIQUE (id, user_id),
  CONSTRAINT negative_constraint_within_own_namespace CHECK (
    pg_catalog.starts_with(scope_path, '/u/' || user_id || '/')
  ),
  CONSTRAINT negative_constraint_lift_is_complete CHECK (
    (lifted_at IS NULL AND lifted_by IS NULL AND lift_evidence_version_id IS NULL)
    OR (lifted_at IS NOT NULL AND lifted_by IS NOT NULL AND lift_evidence_version_id IS NOT NULL)
  ),
  CONSTRAINT negative_constraint_lift_after_dissent CHECK (lifted_at IS NULL OR lifted_at >= dissented_at),
  CONSTRAINT negative_constraint_dissented_version_same_tenant
    FOREIGN KEY (dissented_version_id, user_id) REFERENCES memory_version (id, user_id) ON DELETE SET NULL (dissented_version_id),
  -- A lift is grounded in the evidence that lifted it: if that version is deleted the reference is cleared, and the
  -- guard below then withdraws the whole lift record, so the constraint becomes active again instead of dangling.
  CONSTRAINT negative_constraint_lift_evidence_same_tenant
    FOREIGN KEY (lift_evidence_version_id, user_id) REFERENCES memory_version (id, user_id)
    ON DELETE SET NULL (lift_evidence_version_id)
);
CREATE UNIQUE INDEX negative_constraint_active_unique
  ON negative_constraint (user_id, scope_path, predicate_key, method_family) WHERE lifted_at IS NULL;

-- Sticky: only the lift may be recorded, once; nothing else changes (I16). Two exceptions exist because the row
-- references memory_version: when the dissented version is deleted its reference is cleared (ON DELETE SET NULL), and
-- when the lift evidence is deleted the lift is withdrawn, because a lift with no grounded evidence behind it is not a
-- lift. Both are the database following a reference, not a caller editing a dissent.
CREATE FUNCTION guard_negative_constraint() RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
  IF (NEW.id, NEW.user_id, NEW.predicate_key, NEW.scope_path, NEW.method_family, NEW.dissented_at, NEW.reason)
     IS DISTINCT FROM
     (OLD.id, OLD.user_id, OLD.predicate_key, OLD.scope_path, OLD.method_family, OLD.dissented_at, OLD.reason) THEN
    RAISE EXCEPTION 'negative_constraint_only_the_lift_may_change' USING ERRCODE = 'P0001';
  END IF;
  -- The lift evidence version was deleted: the lift loses its ground, so the lift record goes and the constraint is
  -- active again (its unique index is partial on `lifted_at IS NULL`, so it competes with any new dissent again).
  IF OLD.lift_evidence_version_id IS NOT NULL AND NEW.lift_evidence_version_id IS NULL
     AND NOT EXISTS (SELECT 1 FROM memory_version WHERE id = OLD.lift_evidence_version_id) THEN
    IF NEW.dissented_version_id IS DISTINCT FROM OLD.dissented_version_id THEN
      RAISE EXCEPTION 'negative_constraint_only_the_lift_may_change' USING ERRCODE = 'P0001';
    END IF;
    NEW.lifted_at := NULL;
    NEW.lifted_by := NULL;
    RETURN NEW;
  END IF;
  -- The dissented version itself was deleted: the dissent stays sticky on its predicate; only the reference goes.
  IF OLD.dissented_version_id IS NOT NULL AND NEW.dissented_version_id IS NULL
     AND (NEW.lifted_at, NEW.lifted_by, NEW.lift_evidence_version_id)
         IS NOT DISTINCT FROM (OLD.lifted_at, OLD.lifted_by, OLD.lift_evidence_version_id)
     AND NOT EXISTS (SELECT 1 FROM memory_version WHERE id = OLD.dissented_version_id) THEN
    RETURN NEW;
  END IF;
  IF OLD.lifted_at IS NOT NULL THEN
    RAISE EXCEPTION 'negative_constraint_already_lifted' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.lifted_at IS NULL THEN
    RAISE EXCEPTION 'negative_constraint_update_must_lift' USING ERRCODE = 'P0001';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER trg_negative_constraint_guard BEFORE UPDATE ON negative_constraint
  FOR EACH ROW EXECUTE FUNCTION guard_negative_constraint();

-- 8. Row-level security on every tenant table (forced; the owner is not exempt) ---------------------------------------
DO $rls$
DECLARE
  tbl text;
BEGIN
  FOREACH tbl IN ARRAY ARRAY['association', 'entity', 'abstraction_stats', 'verification', 'negative_constraint'] LOOP
    EXECUTE pg_catalog.format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    EXECUTE pg_catalog.format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', tbl);
    EXECUTE pg_catalog.format(
      'CREATE POLICY %I ON %I USING (user_id = NULLIF(current_setting(''app.user_id'', true), '''')) '
      'WITH CHECK (user_id = NULLIF(current_setting(''app.user_id'', true), ''''))',
      tbl || '_isolation', tbl
    );
  END LOOP;
END
$rls$;

-- 9. The I1' / I15 guard, extended in place (the deferred provenance trigger of migration 1) ----------------------------
CREATE OR REPLACE FUNCTION enforce_provenance() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_status text;
  current_lifecycle text;
  current_layer text;
  current_kind text;
  current_method text;
BEGIN
  IF NEW.status IN ('active', 'archived') THEN
    IF NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id)
       AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
      RAISE EXCEPTION 'active_or_archived_version_requires_evidence_or_derivation'
        USING ERRCODE = 'P0001';
    END IF;
  END IF;
  -- A deferred trigger sees the row as it was when the event was queued, but the row can change before commit.
  -- Judge what is committed: re-read the current row, and stop if it is gone.
  SELECT status, lifecycle, layer, kind, method_id
    INTO current_status, current_lifecycle, current_layer, current_kind, current_method
    FROM memory_version WHERE id = NEW.id;
  IF FOUND AND current_status = 'active' THEN
    IF current_lifecycle IN ('observed', 'asserted')
       AND NOT EXISTS (SELECT 1 FROM memory_evidence WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'lifecycle_requires_evidence_record' USING ERRCODE = 'P0001';
    END IF;
    -- I1': inferred => a derivation row AND a registered method (I3). v1 persona rows carry evidence, not a derivation
    -- row, so they are exempt from the derivation half for the dual-write window; they do carry the persona method.
    IF current_lifecycle = 'inferred' THEN
      IF current_layer <> 'persona'
         AND NOT EXISTS (SELECT 1 FROM memory_derivation WHERE derived_version_id = NEW.id) THEN
        RAISE EXCEPTION 'inferred_requires_derivation_record' USING ERRCODE = 'P0001';
      END IF;
      IF current_method IS NULL THEN
        RAISE EXCEPTION 'inferred_requires_method_record' USING ERRCODE = 'P0001';
      END IF;
    END IF;
    -- I15: an abstraction exists only with its ledger.
    IF current_kind = 'abstraction'
       AND NOT EXISTS (SELECT 1 FROM abstraction_stats WHERE version_id = NEW.id) THEN
      RAISE EXCEPTION 'abstraction_requires_a_ledger' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NEW;
END
$$;

-- 10. Records for the recorded lifecycle transitions (ARCHITECTURE_V2 2.3), judged at commit -------------------------
CREATE FUNCTION enforce_transition_records() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  current_lifecycle text;
  current_status text;
  current_range tstzrange;
  current_branch text;
BEGIN
  -- Judge what is committed; a row deleted in the same transaction (the deletion path) has nothing to record.
  SELECT lifecycle, status, recorded_range, archival_branch
    INTO current_lifecycle, current_status, current_range, current_branch
    FROM memory_version WHERE id = NEW.id;
  IF NOT FOUND OR current_lifecycle IS DISTINCT FROM NEW.lifecycle THEN
    RETURN NULL;
  END IF;
  -- A v1 staged candidate entering the lifecycle, and the dual-write corrections to `inferred`, record nothing.
  IF (OLD.status = 'candidate' AND OLD.lifecycle = 'retired') OR NEW.lifecycle = 'inferred' THEN
    RETURN NULL;
  END IF;

  IF OLD.lifecycle = 'contested' AND NEW.lifecycle = 'asserted' THEN
    IF NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'accept') THEN
      RAISE EXCEPTION 'lifecycle_requires_adjudication_record' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'contested' AND NEW.lifecycle = 'retired' THEN
    IF NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'reject') THEN
      RAISE EXCEPTION 'lifecycle_requires_reason_code' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle IN ('asserted', 'observed') AND NEW.lifecycle = 'retired' THEN
    -- withdrawal or supersession closes recorded time; the v1 archival path records its branch
    IF upper_inf(current_range) AND current_branch IS NULL THEN
      RAISE EXCEPTION 'lifecycle_requires_recorded_time_close' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'retired' THEN
    IF (current_range IS NULL OR upper_inf(current_range)) AND current_branch IS NULL
       AND NOT EXISTS (
         SELECT 1 FROM verification WHERE version_id = NEW.id AND user_id = NEW.user_id AND outcome = 'refuted'
       )
       AND NOT EXISTS (
         SELECT 1 FROM negative_constraint WHERE dissented_version_id = NEW.id AND user_id = NEW.user_id
       )
       AND NOT EXISTS (SELECT 1 FROM memory_conflict WHERE candidate_version_id = NEW.id AND decision = 'reject') THEN
      RAISE EXCEPTION 'lifecycle_requires_reason_code' USING ERRCODE = 'P0001';
    END IF;
  ELSIF OLD.lifecycle = 'inferred' AND NEW.lifecycle = 'contested' THEN
    -- grounded evidence contradicts it (2.3 rule 1): a Tier C contradicts edge
    IF NOT EXISTS (
      SELECT 1 FROM association
       WHERE user_id = NEW.user_id AND type = 'contradicts' AND origin = 'tier_c'
         AND (from_id = NEW.id OR to_id = NEW.id)
    ) THEN
      RAISE EXCEPTION 'lifecycle_requires_contradiction_record' USING ERRCODE = 'P0001';
    END IF;
  END IF;
  RETURN NULL;
END
$$;

-- Plain AFTER UPDATE, not `UPDATE OF lifecycle`: the lifecycle is usually changed by another trigger (the dual-write
-- derivation from `status`), and a column-list trigger fires only for columns named in the statement's SET list.
CREATE CONSTRAINT TRIGGER trg_memory_version_transition_records
  AFTER UPDATE ON memory_version
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW
  WHEN (OLD.lifecycle IS DISTINCT FROM NEW.lifecycle)
  EXECUTE FUNCTION enforce_transition_records();

-- 11. V2-D54 (S-02): every engine function is pinned to `<this schema>, pg_catalog, pg_temp`. CREATE OR REPLACE resets a
-- function's settings, so the replaced ones are pinned again with the new ones.
DO $pin$
DECLARE
  engine_schema text := pg_catalog.current_schema();
  fn text;
BEGIN
  FOREACH fn IN ARRAY ARRAY[
    'guard_method_append_only()', 'assign_memory_version_method()', 'mark_derived_version_inferred()',
    'guard_abstraction_stats()', 'guard_verification_append_only()', 'guard_verification_no_delete()',
    'create_zero_ledger()', 'guard_negative_constraint()', 'enforce_provenance()', 'enforce_transition_records()'
  ] LOOP
    EXECUTE pg_catalog.format(
      'ALTER FUNCTION %I.%s SET search_path = %I, pg_catalog, pg_temp', engine_schema, fn, engine_schema
    );
  END LOOP;
END
$pin$;
