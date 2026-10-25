-- Bi-temporal semantics check for Graduation_Project_Proposal_Revised_v2.md (Sections 7.4, 7.5, 8).
-- Scope: a reduced MEMORY_VERSION table (no embeddings, no RLS, no queue, no pgvector).
-- It exercises: the CHECK constraints and exclusion constraint of invariants 2 and 4, the update
-- guard of invariant 3 (as a trigger), the close-then-insert supersession rule, and the three
-- query predicates, against the worked example of Section 7.4.
-- Run with: psql -v ON_ERROR_STOP=1 -f bitemporal_checks.sql   (any failed assertion aborts the run)

SET timezone = 'UTC';
DROP SCHEMA IF EXISTS bt CASCADE;
CREATE SCHEMA bt;
SET search_path = bt, public;
CREATE EXTENSION IF NOT EXISTS btree_gist;

CREATE TABLE memory_version (
  id             text PRIMARY KEY,
  memory_key     text NOT NULL,
  user_id        text NOT NULL,
  status         text NOT NULL CHECK (status IN ('candidate','conflict_pending','rejected','active','archived')),
  subject_key    text NOT NULL,
  predicate_key  text NOT NULL,
  single_valued  boolean NOT NULL,
  content        text NOT NULL,
  valid_range    tstzrange NOT NULL,
  recorded_range tstzrange,
  CONSTRAINT valid_wellformed    CHECK (NOT isempty(valid_range) AND NOT lower_inf(valid_range)),
  CONSTRAINT recorded_iff_believed CHECK ((status IN ('active','archived')) = (recorded_range IS NOT NULL)),
  CONSTRAINT recorded_wellformed CHECK (recorded_range IS NULL
                                        OR (NOT isempty(recorded_range) AND NOT lower_inf(recorded_range))),
  CONSTRAINT no_bitemporal_overlap EXCLUDE USING gist
      (user_id WITH =, subject_key WITH =, predicate_key WITH =,
       valid_range WITH &&, recorded_range WITH &&)
      WHERE (single_valued AND status IN ('active','archived'))
);

-- Injectable transaction clock (production: clock_timestamp(); replay: bt.clock is set by the harness).
CREATE FUNCTION clock_now() RETURNS timestamptz LANGUAGE sql STABLE AS
$$ SELECT COALESCE(NULLIF(current_setting('bt.clock', true), '')::timestamptz, clock_timestamp()) $$;

-- Invariant 3 (as a trigger): after insertion, only `status` and the upper bound of recorded_range may change,
-- and the upper bound may only be closed (never reopened, never moved, lower bound never edited).
CREATE FUNCTION guard_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (NEW.id, NEW.memory_key, NEW.user_id, NEW.subject_key, NEW.predicate_key, NEW.single_valued, NEW.content, NEW.valid_range)
     IS DISTINCT FROM
     (OLD.id, OLD.memory_key, OLD.user_id, OLD.subject_key, OLD.predicate_key, OLD.single_valued, OLD.content, OLD.valid_range) THEN
    RAISE EXCEPTION 'only status and the upper bound of recorded_range may change';
  END IF;
  IF NEW.recorded_range IS DISTINCT FROM OLD.recorded_range THEN
    IF OLD.recorded_range IS NULL AND NEW.status = 'active' AND upper_inf(NEW.recorded_range)
       AND lower(NEW.recorded_range) = clock_now() THEN
      NULL;  -- commit into the belief timeline: recorded_from = clock
    ELSIF OLD.recorded_range IS NOT NULL AND upper_inf(OLD.recorded_range)
       AND lower(NEW.recorded_range) = lower(OLD.recorded_range)
       AND NOT upper_inf(NEW.recorded_range)
       AND upper(NEW.recorded_range) = clock_now() THEN
      NULL;  -- closing a belief at the clock
    ELSE
      RAISE EXCEPTION 'illegal recorded_range change';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_guard BEFORE UPDATE ON memory_version FOR EACH ROW EXECUTE FUNCTION guard_update();

-- Supersession for a single-valued predicate: close-then-insert, one transaction, one recorded instant.
CREATE FUNCTION supersede_single(p_new_id text, p_reissue_id text, p_user text, p_subject text,
                                 p_pred text, p_content text, p_new_vf timestamptz) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE c timestamptz := clock_now(); old memory_version%ROWTYPE;
BEGIN
  SELECT * INTO old FROM memory_version
   WHERE user_id = p_user AND subject_key = p_subject AND predicate_key = p_pred
     AND status = 'active' AND recorded_range @> c AND valid_range @> p_new_vf FOR UPDATE;
  IF NOT FOUND THEN RAISE EXCEPTION 'no current belief to supersede'; END IF;
  UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), c) WHERE id = old.id;   -- close first
  IF lower(old.valid_range) < p_new_vf THEN                                                            -- re-issue only if non-empty
    INSERT INTO memory_version VALUES (p_reissue_id, old.memory_key, old.user_id, 'active', old.subject_key,
      old.predicate_key, true, old.content, tstzrange(lower(old.valid_range), p_new_vf), tstzrange(c, NULL));
  END IF;
  INSERT INTO memory_version VALUES (p_new_id, old.memory_key, old.user_id, 'active', old.subject_key,
      old.predicate_key, true, p_content, tstzrange(p_new_vf, NULL), tstzrange(c, NULL));
END $$;

-- Query predicates (Section 7.4). All take user scope first.
CREATE FUNCTION q_current(u text, rho timestamptz) RETURNS SETOF memory_version LANGUAGE sql STABLE AS
$$ SELECT * FROM memory_version
    WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND valid_range @> rho $$;

CREATE FUNCTION q_historical(u text, t timestamptz, rho timestamptz) RETURNS SETOF memory_version LANGUAGE plpgsql STABLE AS
$$ BEGIN
     IF t > rho THEN RAISE EXCEPTION 'historical-as-of requires t <= reference time'; END IF;
     RETURN QUERY SELECT * FROM memory_version
       WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND valid_range @> t;
   END $$;

CREATE FUNCTION q_change(u text, w tstzrange, rho timestamptz) RETURNS SETOF memory_version LANGUAGE sql STABLE AS
$$ SELECT * FROM memory_version
    WHERE user_id = u AND status = 'active' AND recorded_range @> rho AND valid_range && w $$;

CREATE FUNCTION q_audit(u text, t timestamptz, beta timestamptz) RETURNS SETOF memory_version LANGUAGE plpgsql STABLE AS
$$ BEGIN
     IF beta > clock_now() THEN RAISE EXCEPTION 'audit-as-of requires belief time <= clock'; END IF;
     RETURN QUERY SELECT * FROM memory_version
       WHERE user_id = u AND status IN ('active','archived') AND recorded_range @> beta AND valid_range @> t;
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
SELECT set_config('bt.clock', '2026-03-10', false);
INSERT INTO memory_version VALUES ('v1','diet','u1','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10', NULL), tstzrange(clock_now(), NULL));

-- E2: "I started eating fish again", committed 5 Aug: v1 closed in recorded time, v1' re-issued, v2 inserted.
SELECT set_config('bt.clock', '2026-08-05', false);
BEGIN;
SELECT supersede_single('v2','v1p','u1','user','diet','eats fish','2026-08-05');
COMMIT;

-- E3: "Actually I switched in mid-July": valid boundary moves from 5 Aug to 15 Jul, committed 20 Aug.
SELECT set_config('bt.clock', '2026-08-20', false);
BEGIN;
UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id IN ('v1p','v2');  -- close first
INSERT INTO memory_version VALUES ('v1pp','diet','u1','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10','2026-07-15'), tstzrange(clock_now(), NULL));
INSERT INTO memory_version VALUES ('v2p','diet','u1','active','user','diet',true,'eats fish',
        tstzrange('2026-07-15', NULL), tstzrange(clock_now(), NULL));
COMMIT;

\echo '-- stored rows'
SELECT id, content, valid_range, recorded_range FROM memory_version ORDER BY lower(recorded_range), id;

SELECT set_config('bt.clock', '2026-10-01', false);   -- reference time "today"
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
    WHERE upper_inf(recorded_range) AND NOT upper_inf(valid_range)), ARRAY['v1pp']);

\echo '== Invariants 2, 3, 4 and the staging rule =='
SELECT expect_error('inv 4: overlapping believed single-valued row rejected',
  $$INSERT INTO bt.memory_version VALUES ('x1','diet','u1','active','user','diet',true,'vegan',
      tstzrange('2026-09-01',NULL), tstzrange('2026-08-20',NULL))$$, '23P01');
SELECT set_config('bt.clock', '2026-08-20', false);   -- clock equals the row's recorded_from
SELECT expect_error('inv 2: inserting an empty recorded range rejected by CHECK',
  $$INSERT INTO bt.memory_version VALUES ('x0','k0','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange('2026-01-01','2026-01-01'))$$, '23514');
SELECT expect_error('inv 3: closing a belief at its own recorded_from (empty range) rejected by update guard',
  $$UPDATE bt.memory_version SET recorded_range = tstzrange(lower(recorded_range), lower(recorded_range)) WHERE id='v2p'$$, 'P0001');
SELECT set_config('bt.clock', '2026-10-01', false);
SELECT expect_error('inv 2: recorded range without lower bound rejected',
  $$INSERT INTO bt.memory_version VALUES ('x2','k2','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange(NULL,'2026-01-02'))$$, '23514');
SELECT expect_error('staging rule: active row without recorded range rejected',
  $$INSERT INTO bt.memory_version VALUES ('x3','k3','u3','active','s','p',true,'c', tstzrange('2026-01-01',NULL), NULL)$$, '23514');
SELECT expect_error('staging rule: candidate row with a recorded range rejected',
  $$INSERT INTO bt.memory_version VALUES ('x4','k4','u3','candidate','s','p',true,'c', tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL))$$, '23514');
SELECT set_config('bt.clock', '2026-10-01', false);
SELECT expect_error('inv 3: editing content after insertion rejected',
  $$UPDATE bt.memory_version SET content='x' WHERE id='v2p'$$, 'P0001');
SELECT expect_error('inv 3: editing the lower bound of recorded_range rejected',
  $$UPDATE bt.memory_version SET recorded_range = tstzrange('2026-01-01', upper(recorded_range)) WHERE id='v1'$$, 'P0001');
SELECT expect_error('inv 3: reopening a closed belief rejected',
  $$UPDATE bt.memory_version SET recorded_range = tstzrange(lower(recorded_range), NULL) WHERE id='v1'$$, 'P0001');
SELECT expect_error('inv 3: closing at a value other than the clock rejected',
  $$UPDATE bt.memory_version SET recorded_range = tstzrange(lower(recorded_range), '2026-09-15') WHERE id='v2p'$$, 'P0001');
SELECT expect_error('read path: historical query with t after reference time rejected',
  $$SELECT * FROM bt.q_historical('u1','2026-11-01','2026-10-01')$$, 'P0001');

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
SELECT set_config('bt.clock', '2026-10-01', false);
UPDATE memory_version SET status = 'archived' WHERE id = 'v2p';
SELECT expect('archived belief excluded from current',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01')), ARRAY[]::text[]);
SELECT expect('archived belief excluded from historical',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-07-20','2026-10-01')), ARRAY[]::text[]);
SELECT expect('archived belief still visible to audit',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-10-01','2026-10-01')), ARRAY['v2p']);
UPDATE memory_version SET status = 'active' WHERE id = 'v2p';

\echo '== Multi-valued predicates may overlap; staging commit; same-day correction (no empty re-issue) =='
INSERT INTO memory_version VALUES ('m1','lang:en','u1','active','user','language',false,'English',
        tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL));
INSERT INTO memory_version VALUES ('m2','lang:fr','u1','active','user','language',false,'French',
        tstzrange('2026-01-01',NULL), tstzrange('2026-01-01',NULL));
SELECT expect('multi-valued rows coexist and are both current',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01') WHERE predicate_key='language'), ARRAY['m1','m2']);
SELECT set_config('bt.clock', '2026-09-02', false);
SELECT expect_error('staging: promoting a conflicting candidate without closing the old belief is rejected',
  $$UPDATE bt.memory_version SET status='active', recorded_range = tstzrange(bt.clock_now(), NULL) WHERE id = 'c2'$$, '23P01');
-- Correct promotion (user review resolves the hold on 2 Sep): close, re-issue truncated copy, then promote the candidate.
BEGIN;
UPDATE memory_version SET recorded_range = tstzrange(lower(recorded_range), clock_now()) WHERE id = 'v2p';
INSERT INTO memory_version VALUES ('v2pp','diet','u1','active','user','diet',true,'eats fish',
        tstzrange('2026-07-15','2026-09-01'), tstzrange(clock_now(), NULL));
UPDATE memory_version SET status='active', recorded_range = tstzrange(clock_now(), NULL) WHERE id = 'c2';
COMMIT;
SELECT set_config('bt.clock', '2026-10-01', false);
SELECT expect('after promotion: current = pescatarian (c2)',
  (SELECT array_agg(id ORDER BY id) FROM q_current('u1','2026-10-01') WHERE predicate_key='diet'), ARRAY['c2']);
SELECT expect('after promotion: historical 20 Jul = fish (v2pp)',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u1','2026-07-20','2026-10-01') WHERE predicate_key='diet'), ARRAY['v2pp']);
SELECT expect('promotion: recorded_from of c2 is the review time, not the turn time',
  (SELECT array_agg(id) FROM memory_version WHERE id='c2' AND lower(recorded_range)='2026-09-02' AND lower(valid_range)='2026-09-01'), ARRAY['c2']);
SELECT expect('audit t=10 Sep, beta=1 Sep (hold was not yet a belief)',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u1','2026-09-10','2026-09-01') WHERE predicate_key='diet'), ARRAY['v2p']);

\echo '== Clock rules: same-day correction (no empty re-issue) and strictly increasing clock =='
SELECT set_config('bt.clock', '2026-03-10', false);
INSERT INTO memory_version VALUES ('w1','diet','u2','active','user','diet',true,'vegetarian',
        tstzrange('2026-03-10',NULL), tstzrange(clock_now(), NULL));
SELECT set_config('bt.clock', '2026-03-11', false);
SELECT supersede_single('w2','w1p','u2','user','diet','eats fish','2026-03-10');
SELECT expect('no empty re-issue when the new valid_from equals the old one',
  (SELECT array_agg(id ORDER BY id) FROM memory_version WHERE user_id='u2'), ARRAY['w1','w2']);
SELECT set_config('bt.clock', '2026-10-01', false);
SELECT expect('historical t=10 Mar after same-day correction -> new belief',
  (SELECT array_agg(id ORDER BY id) FROM q_historical('u2','2026-03-10','2026-10-01')), ARRAY['w2']);
SELECT expect('audit t=10 Mar, beta=10 Mar -> original belief',
  (SELECT array_agg(id ORDER BY id) FROM q_audit('u2','2026-03-10','2026-03-10')), ARRAY['w1']);
SELECT set_config('bt.clock', '2026-03-11', false);
SELECT expect_error('clock tie: replacing a belief at the same instant it was recorded is rejected',
  $$SELECT bt.supersede_single('w3','w2p','u2','user','diet','vegan','2026-03-11')$$, 'P0001');
SELECT set_config('bt.clock', '2026-03-11 00:00:00.000001', false);
SELECT supersede_single('w3','w2p','u2','user','diet','vegan','2026-03-11');
SELECT set_config('bt.clock', '2026-10-01', false);
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

\echo 'ALL ASSERTIONS PASSED'
