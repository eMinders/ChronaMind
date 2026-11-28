-- Replay of validation/bitemporal_checks.sql (43 assertions) against the full MEMORY_VERSION table.
-- Requires 001_full_schema.sql already applied on the current search_path.
-- Invariant 1 is disabled here: the reduced harness inserts believed rows without evidence.

SET timezone = 'UTC';

INSERT INTO app_user (id, external_ref, created_at) VALUES
  ('u1', 'ext-u1', '2026-01-01Z'),
  ('u2', 'ext-u2', '2026-01-01Z'),
  ('u3', 'ext-u3', '2026-01-01Z')
ON CONFLICT (id) DO NOTHING;

INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) VALUES
  ('diet', 'single', 'semantic'),
  ('language', 'multi', 'semantic'),
  ('p', 'single', 'semantic')
ON CONFLICT (predicate_key) DO NOTHING;

ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_provenance;
-- The scenarios below time-travel on purpose (app.clock is set backwards between them), which is exactly what the
-- recorded-time watermark of migration 9 (THREAT_MODEL T-28) forbids to a real session. This design-check script is
-- the owner replaying history, so it steps the watermark aside for its own run, as it does for provenance.
ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_recorded_watermark;
-- Likewise the lifecycle transition records of migration 10: the script builds bare versions, not their decisions.
ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_transition_records;
-- And the entry records of migration 14 (the gate decision a version is created under).
ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_record_of_creation;
ALTER TABLE memory_version DISABLE TRIGGER trg_memory_version_record_of_entry;

-- Supersession for a single-valued predicate: close-then-insert, one transaction, one recorded instant.
CREATE FUNCTION supersede_single(p_new_id text, p_reissue_id text, p_user text, p_subject text,
                                 p_pred text, p_content text, p_new_vf timestamptz) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE c timestamptz := clock_now(); old memory_version%ROWTYPE;
BEGIN
  SELECT * INTO old FROM memory_version
   WHERE user_id = p_user AND subject_key = p_subject AND predicate_key = p_pred
     AND status = 'active' AND recorded_range @> c AND event_range @> p_new_vf FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'no current belief to supersede'; END IF;
  UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), c) WHERE id = old.id;   -- close first
  IF lower(old.event_range) < p_new_vf THEN                                                            -- re-issue only if non-empty
    INSERT INTO memory_version VALUES (p_reissue_id, old.memory_key, old.user_id, 'active', old.subject_key,
      old.predicate_key, true, old.content, tstzrange(lower(old.event_range), p_new_vf), tstzrange(c, NULL));
  END IF;
  INSERT INTO memory_version VALUES (p_new_id, old.memory_key, old.user_id, 'active', old.subject_key,
      old.predicate_key, true, p_content, tstzrange(p_new_vf, NULL), tstzrange(c, NULL));
END $$;

-- Query predicates (Section 7.4). All take user scope first.
CREATE FUNCTION q_current(u text, rho timestamptz) RETURNS SETOF memory_version LANGUAGE sql STABLE AS
$$ SELECT * FROM memory_version
    WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND event_range @> rho $$;

CREATE FUNCTION q_historical(u text, t timestamptz, rho timestamptz) RETURNS SETOF memory_version LANGUAGE plpgsql STABLE AS
$$ BEGIN
     IF t > rho THEN RAISE EXCEPTION 'historical-as-of requires t <= reference time'; END IF;
     RETURN QUERY SELECT * FROM memory_version
       WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND event_range @> t;
   END $$;

CREATE FUNCTION q_change(u text, w tstzrange, rho timestamptz) RETURNS SETOF memory_version LANGUAGE sql STABLE AS
$$ SELECT * FROM memory_version
    WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND event_range && w $$;

CREATE FUNCTION q_audit(u text, t timestamptz, beta timestamptz) RETURNS SETOF memory_version LANGUAGE plpgsql STABLE AS
$$ BEGIN
     IF beta > clock_now() THEN RAISE EXCEPTION 'audit-as-of requires belief time <= clock'; END IF;
     RETURN QUERY SELECT * FROM memory_version
       WHERE user_id = u AND status IN ('active','archived') AND recorded_range @> beta AND event_range @> t;
   END $$;

-- Assertion helpers.
CREATE FUNCTION expect(label text, got text[], want text[]) RETURNS text LANGUAGE plpgsql AS $$
BEGIN
  got := COALESCE(got, ARRAY[]::text[]);
  IF got <> want THEN RAISE EXCEPTION 'FAIL %: got %, want %', label, got, want; END IF;
  RETURN format('PASS  %-62s -> %s', label, COALESCE(NULLIF(array_to_string(got, ','), ''), '(none)'));
END $$;

CREATE FUNCTION expect_error(label text, stmt text, want_state text) RETURNS text LANGUAGE plpgsql AS $$
DECLARE st text;
BEGIN
  BEGIN
    EXECUTE stmt;
    RAISE EXCEPTION 'FAIL %: statement succeeded but SQLSTATE % was expected', label, want_state;
  EXCEPTION WHEN OTHERS THEN
    GET STACKED DIAGNOSTICS st = RETURNED_SQLSTATE;
    IF SQLERRM LIKE 'FAIL %' THEN RAISE; END IF;
    IF st <> want_state THEN RAISE EXCEPTION 'FAIL %: SQLSTATE % (%), expected %', label, st, SQLERRM, want_state; END IF;
  END;
  RETURN format('PASS  %-62s -> rejected with SQLSTATE %s', label, want_state);
END $$;

\echo '== Worked example (Section 7.4): E1 10 Mar, E2 5 Aug, E3 20 Aug =='
-- E1: "I'm vegetarian" (valid_from = turn time, basis inferred), committed 10 Mar.
SELECT set_config('app.clock', '2026-03-10', false);
INSERT INTO memory_version VALUES ('v1','diet','u1','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10', NULL), tstzrange(clock_now(), NULL));

-- E2: "I started eating fish again", committed 5 Aug: v1 closed in recorded time, v1' re-issued, v2 inserted.
SELECT set_config('app.clock', '2026-08-05', false);
BEGIN;
SELECT supersede_single('v2','v1p','u1','user','diet','eats fish','2026-08-05');
COMMIT;

-- E3: "Actually I switched in mid-July": valid boundary moves from 5 Aug to 15 Jul, committed 20 Aug.
SELECT set_config('app.clock', '2026-08-20', false);
BEGIN;
UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id IN ('v1p','v2');  -- close first
INSERT INTO memory_version VALUES ('v1pp','diet','u1','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10','2026-07-15'), tstzrange(clock_now(), NULL));
INSERT INTO memory_version VALUES ('v2p','diet','u1','active','user','diet',true,'eats fish',
        tstzrange('2026-07-15', NULL), tstzrange(clock_now(), NULL));
COMMIT;

\echo '-- stored rows'
SELECT id, content, event_range, recorded_range FROM memory_version ORDER BY lower(recorded_range), id;

SELECT set_config('app.clock', '2026-10-01', false);   -- reference time "today"
\echo '-- query predicates on the worked example (reference time 1 Oct)'
SELECT expect('current (t=1 Oct, beta=now)',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01')), ARRAY['v2p']);
SELECT expect('historical-as-of t=1 Apr, beta=now',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-04-01','2026-10-01')), ARRAY['v1pp']);
SELECT expect('historical-as-of t=20 Jul, beta=now (after the correction)',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-07-20','2026-10-01')), ARRAY['v2p']);
SELECT expect('change window [1 Mar, 1 Oct), beta=now',
  (SELECT array_agg(id ORDER BY id) FROM q_change('u1', tstzrange('2026-03-01','2026-10-01'),'2026-10-01')), ARRAY['v1pp','v2p']);
SELECT expect('audit t=1 Apr, beta=1 Jun',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-04-01','2026-06-01')), ARRAY['v1']);
SELECT expect('audit t=1 Jun, beta=1 Jun',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-06-01','2026-06-01')), ARRAY['v1']);
SELECT expect('audit t=10 Aug, beta=10 Aug',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-08-10','2026-08-10')), ARRAY['v2']);
SELECT expect('audit t=20 Jul, beta=10 Aug (system then believed vegetarian)',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-07-20','2026-08-10')), ARRAY['v1p']);
SELECT expect('audit t=20 Jul, beta=25 Aug (after correction)',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-07-20','2026-08-25')), ARRAY['v2p']);
SELECT expect('current at 1 Jun in replay (beta=t=1 Jun) equals audit(1 Jun,1 Jun)',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-06-01')), ARRAY['v1']);
SELECT expect('other user sees nothing (user scope)',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u2','2026-10-01')), ARRAY[]::text[]);

\echo '-- exactly one current belief that is currently valid; one that is historical; three replaced beliefs'
SELECT expect('current beliefs (recorded open)',
  (SELECT array_agg(id ORDER BY id) FROM memory_version WHERE upper_inf(recorded_range)), ARRAY['v1pp','v2p']);
SELECT expect('replaced beliefs (recorded closed) = superseded',
  (SELECT array_agg(id ORDER BY id) FROM memory_version WHERE NOT upper_inf(recorded_range)), ARRAY['v1','v1p','v2']);
SELECT expect('current belief about a past state (historical, valid-ended, not superseded)',
  (SELECT array_agg(id ORDER BY id) FROM memory_version
    WHERE upper_inf(recorded_range) AND NOT upper_inf(event_range)), ARRAY['v1pp']);

\echo '== Invariants 2, 3, 4 and the staging rule =='
SELECT expect_error('inv 4: overlapping believed single-valued row rejected',
  $$INSERT INTO memory_version VALUES ('x1','diet','u1','active','user','diet',true,'vegan',
      tstzrange('2026-09-01',NULL), tstzrange('2026-08-20',NULL))$$, '23P01');
SELECT set_config('app.clock', '2026-08-20', false);   -- clock equals the row's recorded_from
SELECT expect_error('inv 2: inserting an empty recorded range rejected by CHECK',
  $$INSERT INTO memory_version VALUES ('x0','k0','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange('2026-01-01','2026-01-01'))$$, '23514');
SELECT expect_error('inv 3: closing a belief at its own recorded_from (empty range) rejected by update guard',
  $$UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), lower(recorded_range)) WHERE id='v2p'$$, 'P0001');
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect_error('inv 2: recorded range without lower bound rejected',
  $$INSERT INTO memory_version VALUES ('x2','k2','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange(NULL,'2026-01-02'))$$, '23514');
SELECT expect_error('staging rule: active row without recorded range rejected',
  $$INSERT INTO memory_version VALUES ('x3','k3','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), NULL)$$, '23514');
SELECT expect_error('staging rule: candidate row with a recorded range rejected',
  $$INSERT INTO memory_version VALUES ('x4','k4','u3','candidate','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL))$$, '23514');
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect_error('inv 3: editing content after insertion rejected',
  $$UPDATE memory_version SET content='x' WHERE id='v2p'$$, 'P0001');
SELECT expect_error('inv 3: editing the lower bound of recorded_range rejected',
  $$UPDATE memory_version SET recorded_range = tstzrange('2026-01-01', upper(recorded_range)) WHERE id='v1'$$, 'P0001');
SELECT expect_error('inv 3: reopening a closed belief rejected',
  $$UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), NULL) WHERE id='v1'$$, 'P0001');
SELECT expect_error('inv 3: closing at a value other than the clock rejected',
  $$UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), '2026-09-15') WHERE id='v2p'$$, 'P0001');
SELECT expect_error('read path: historical query with t after reference time rejected',
  $$SELECT * FROM q_historical('u1','2026-11-01','2026-10-01')$$, 'P0001');

\echo '== Staging rows never satisfy any predicate; archived rows are audit-visible only =='
INSERT INTO memory_version VALUES ('c1','diet','u1','candidate','user','diet',true,'vegan',
        tstzrange('2026-09-01',NULL), NULL);
INSERT INTO memory_version VALUES ('c2','diet','u1','conflict_pending','user','diet',true,'pescatarian',
        tstzrange('2026-09-01',NULL), NULL);
INSERT INTO memory_version VALUES ('c3','diet','u1','rejected','user','diet',true,'carnivore',
        tstzrange('2026-09-01',NULL), NULL);
SELECT expect('staging rows absent from current',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01')), ARRAY['v2p']);
SELECT expect('staging rows absent from audit (beta=now, t=now)',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-10-01','2026-10-01')), ARRAY['v2p']);
-- Invariant 10 permits archiving only a superseded version (branch S) or one with
-- a covering active replacement (branch C). v1p is superseded at 20 Aug, so it is
-- the valid subject; a currently-valid belief such as v2p must be rejected.
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect_error('inv 10: archiving a currently-valid belief without a replacement is rejected',
  $$UPDATE memory_version SET status='archived', archival_branch='S' WHERE id='v2p'$$, '22023');
UPDATE memory_version SET status = 'archived', archival_branch = 'S' WHERE id = 'v1p';
SELECT expect('archived superseded belief excluded from current and historical',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-07-20','2026-10-01')), ARRAY['v2p']);
SELECT expect('archived superseded belief still visible to audit',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-07-20','2026-08-10')), ARRAY['v1p']);
UPDATE memory_version SET status = 'active', archival_branch = NULL WHERE id = 'v2p';
DELETE FROM memory_version WHERE id = 'v2pp';

\echo '== Multi-valued predicates may overlap; staging commit; same-day correction (no empty re-issue) =='
INSERT INTO memory_version VALUES ('m1','lang:en','u1','active','user','language',false,'English',
        tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL));
INSERT INTO memory_version VALUES ('m2','lang:fr','u1','active','user','language',false,'French',
        tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL));
SELECT expect('multi-valued rows coexist and are both current',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01') WHERE predicate_key='language'), ARRAY['m1','m2']);
SELECT set_config('app.clock', '2026-09-02', false);
SELECT expect_error('staging: promoting a conflicting candidate without closing the old belief is rejected',
  $$UPDATE memory_version SET status='active', recorded_range = tstzrange(clock_now(), NULL) WHERE id = 'c2'$$, '23P01');
-- Correct promotion (user review resolves the hold on 2 Sep): close, re-issue truncated copy, then promote the candidate.
BEGIN;
UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = 'v2p';
INSERT INTO memory_version VALUES ('v2pp','diet','u1','active','user','diet',true,'eats fish',
        tstzrange('2026-07-15','2026-09-01'), tstzrange(clock_now(), NULL));
UPDATE memory_version SET status='active', recorded_range = tstzrange(clock_now(), NULL) WHERE id = 'c2';
COMMIT;
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect('after promotion: current = pescatarian (c2)',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01') WHERE predicate_key='diet'), ARRAY['c2']);
SELECT expect('after promotion: historical 20 Jul = fish (v2pp)',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-07-20','2026-10-01') WHERE predicate_key='diet'), ARRAY['v2pp']);
SELECT expect('promotion: recorded_from of c2 is the review time, not the turn time',
  (SELECT array_agg(id) FROM memory_version WHERE id='c2' AND lower(recorded_range)='2026-09-02' AND lower(event_range)='2026-09-01'), ARRAY['c2']);
SELECT expect('audit t=10 Sep, beta=1 Sep (hold was not yet a belief)',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-09-10','2026-09-01') WHERE predicate_key='diet'), ARRAY['v2p']);

\echo '== Clock rules: same-day correction (no empty re-issue) and strictly increasing clock =='
SELECT set_config('app.clock', '2026-03-10', false);
INSERT INTO memory_version VALUES ('w1','diet','u2','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10',NULL), tstzrange(clock_now(), NULL));
SELECT set_config('app.clock', '2026-03-11', false);
SELECT supersede_single('w2','w1p','u2','user','diet','eats fish','2026-03-10');
SELECT expect('no empty re-issue when the new valid_from equals the old one',
  (SELECT array_agg(id ORDER BY id) FROM memory_version WHERE user_id='u2'), ARRAY['w1','w2']);
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect('historical t=10 Mar after same-day correction -> new belief',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u2','2026-03-10','2026-10-01')), ARRAY['w2']);
SELECT expect('audit t=10 Mar, beta=10 Mar -> original belief',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u2','2026-03-10','2026-03-10')), ARRAY['w1']);
SELECT set_config('app.clock', '2026-03-11', false);
SELECT expect_error('clock tie: replacing a belief at the same instant it was recorded is rejected',
  $$SELECT supersede_single('w3','w2p','u2','user','diet','vegan','2026-03-11')$$, 'P0001');
SELECT set_config('app.clock', '2026-03-11 00:00:00.000001', false);
SELECT supersede_single('w3','w2p','u2','user','diet','vegan','2026-03-11');
SELECT set_config('app.clock', '2026-10-01', false);
SELECT expect('strictly increasing clock allows the replacement',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u2','2026-10-01')), ARRAY['w3']);

\echo '== Deletion removes every version of the memory item, including closed beliefs =='
DELETE FROM memory_version WHERE user_id = 'u1' AND memory_key = 'diet';
SELECT expect('audit queries find no diet row after deletion (any t, any beta)',
  (SELECT array_agg(id ORDER BY id) FROM (
      SELECT * FROM q_audit('u1','2026-04-01','2026-06-01')
      UNION ALL SELECT * FROM q_audit('u1','2026-07-20','2026-08-10')
      UNION ALL SELECT * FROM q_audit('u1','2026-10-01','2026-10-01')) d WHERE predicate_key='diet'), ARRAY[]::text[]);
SELECT expect('other memory items of the user survive',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01')), ARRAY['m1','m2']);

-- Restore invariant 1 enforcement. Keep this last so the harness never leaves a persistent database
-- with the provenance trigger switched off.
ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_provenance;
ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_recorded_watermark;
ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_transition_records;
ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_record_of_creation;
ALTER TABLE memory_version ENABLE TRIGGER trg_memory_version_record_of_entry;

\echo 'ALL ASSERTIONS PASSED'
