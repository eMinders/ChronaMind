-- Migration 13: the two generic predicates the public API writes under - E-039 (#97).
--
-- v1 extractors name a predicate for every statement (`diet`, `exercise`, ...), and `memory_version.predicate_key` is a
-- foreign key to this catalog. The V2 write operations (`ingest_episode`, `assert`) take free content and no predicate, so
-- they need a catalog entry to write under. `episode` and `statement` are both MULTI-valued, so two of them never
-- supersede or conflict with each other: a caller that wants single-valued supersession names a registered predicate.
-- (The optional predicate, M5, is deferred past W1: V2-D43.) Catalog data, additive, idempotent.

INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) VALUES
  ('episode', 'multi', 'episodic'),
  ('statement', 'multi', 'semantic')
ON CONFLICT (predicate_key) DO NOTHING;
