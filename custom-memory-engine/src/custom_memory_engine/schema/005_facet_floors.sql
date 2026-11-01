-- Migration 5: user-model facet tag and retention floors (M8) - E-020, V2-D26, V2-D29, V2-D10.
--
--   um_facet   nullable tag marking an artifact as part of the evolving user model. The user model is a VIEW over
--              artifacts (V2-D26), so it needs no table and no extra deletion channel. Exactly the six canonical
--              facets (USER_MODEL.md section 2.1); `inferred_preference` is not one - inference is a lifecycle.
--   floors     only episodes and procedures may be `working` or `session`; claims and abstractions are `durable`
--              until explicitly retired (V2-D29). Without the floor, expiring working memory would silently delete
--              knowledge - the v1 archival bug in a new place (V2-D10).
--
-- Requires M1 (004): the floor reads `kind` and `retention`. v1 has no `working`/`session` values and every backfilled
-- row is `durable`, so validating the CHECK against existing data cannot fail; um_facet starts NULL for every row.
-- Additive: no existing object is altered, only a column, two CHECKs and an index are added.

ALTER TABLE memory_version ADD COLUMN um_facet text;

ALTER TABLE memory_version
  ADD CONSTRAINT memory_version_um_facet_check CHECK (
    um_facet IS NULL OR um_facet IN (
      'preference', 'goal', 'constraint', 'context', 'decision_pattern', 'meta_preference'
    )
  ),
  ADD CONSTRAINT memory_version_retention_floor CHECK (kind NOT IN ('claim', 'abstraction') OR retention = 'durable');

-- Facet-filtered queries per scope. Before E-011 the scope key is user_id; E-011 will recreate this index on its
-- scope column in the same migration that introduces it. Partial: most rows carry no facet.
CREATE INDEX memory_version_um_facet_idx ON memory_version (user_id, um_facet) WHERE um_facet IS NOT NULL;
