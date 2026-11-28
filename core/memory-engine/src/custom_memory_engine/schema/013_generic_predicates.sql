-- Migration 13: the two generic predicates the public API writes under - E-039 (#97).
--
-- v1 extractors name a predicate for every statement (`diet`, `exercise`, ...), and `memory_version.predicate_key` is a
-- foreign key to this catalog. The V2 write operations (`ingest_episode`, `assert`) take free content and no predicate, so
-- they need a catalog entry to write under. `episode` and `statement` are both MULTI-valued, so two of them never
-- supersede or conflict with each other: a caller that wants single-valued supersession names a registered predicate.
-- (The optional predicate, M5, is deferred past W1: V2-D43.) Catalog data, additive; an existing row under either key
-- is kept only if it already means the same thing, otherwise the migration refuses and changes nothing.

INSERT INTO predicate_schema (predicate_key, cardinality, default_layer) VALUES
  ('episode', 'multi', 'episodic'),
  ('statement', 'multi', 'semantic')
ON CONFLICT (predicate_key) DO NOTHING;

-- An existing row under either key must already mean the same thing: the API writes them multi-valued, so a
-- single-valued definition kept by ON CONFLICT would silently contradict every write. Refuse instead (fail closed).
DO $guard$
BEGIN
  IF EXISTS (
    SELECT 1 FROM predicate_schema
    WHERE (predicate_key = 'episode' AND (cardinality, default_layer) IS DISTINCT FROM ('multi', 'episodic'))
       OR (predicate_key = 'statement' AND (cardinality, default_layer) IS DISTINCT FROM ('multi', 'semantic'))
  ) THEN
    RAISE EXCEPTION 'm13_generic_predicate_already_defined_differently' USING ERRCODE = 'P0001';
  END IF;
END
$guard$;
