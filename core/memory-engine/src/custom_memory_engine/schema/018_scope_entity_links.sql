-- Migration 18: scope-local entity links (E-024 substrate).
--
-- E-024 needs one thing the schema does not have: a persisted, explicitly authorized identity link between two
-- entities that may live in different scopes of one principal. It adds no scope table: a scope *is* its canonical
-- path (V2-D65), and migration 15 already admits a scoped row anywhere inside its owner's namespace, so a sub-scope
-- needs no row to be usable.

CREATE TABLE entity_link (
  id text PRIMARY KEY,
  user_id text NOT NULL REFERENCES app_user(id),
  entity_a text NOT NULL,
  entity_b text NOT NULL,
  relation text NOT NULL CHECK (relation ~ '^[a-z][a-z0-9_]{0,63}$'),
  created_at timestamptz NOT NULL DEFAULT clock_now(),
  -- unordered pair: one stored orientation per relation, so a replay is the same row whichever way it is asked
  CONSTRAINT entity_link_ordered_entities CHECK (entity_a < entity_b),
  CONSTRAINT entity_link_unique_relation UNIQUE (user_id, entity_a, entity_b, relation),
  -- both endpoints are this principal's entities (composite, so a foreign entity id cannot be cited)
  CONSTRAINT entity_link_a_owner_fk FOREIGN KEY (entity_a, user_id)
    REFERENCES entity (id, user_id) ON DELETE CASCADE,
  CONSTRAINT entity_link_b_owner_fk FOREIGN KEY (entity_b, user_id)
    REFERENCES entity (id, user_id) ON DELETE CASCADE
);

ALTER TABLE entity_link ENABLE ROW LEVEL SECURITY;
ALTER TABLE entity_link FORCE ROW LEVEL SECURITY;
CREATE POLICY entity_link_read ON entity_link FOR SELECT
  USING (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
CREATE POLICY entity_link_insert ON entity_link FOR INSERT
  WITH CHECK (user_id = NULLIF(pg_catalog.current_setting('app.user_id', true), ''));
