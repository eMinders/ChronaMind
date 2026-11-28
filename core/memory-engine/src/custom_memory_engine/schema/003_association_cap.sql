-- Migration 3: database-enforced association degree cap — V2-D13 (resolves B-08).
--
-- V2-D04 (adopted): "Association growth is bounded by a profile-declared cap per
-- artifact to prevent quadratic blow-up." V2-D13 fixes the semantics and default:
--
--   * the cap bounds an artifact's association DEGREE: rows in which it is either
--     endpoint (`from_id` or `to_id`). A cap on association *types* would be vacuous,
--     because the type set is closed at ten values;
--   * default 32 (the plan's recorded suggestion), declared per profile by passing
--     the cap as the trigger argument when ASSOCIATION is created (M6, E-014);
--   * enforced here, in the database, so no API or ingestion path can bypass it;
--   * concurrent inserts touching the same endpoint are serialized with a
--     transaction-scoped advisory lock, so two writers cannot each observe cap-1 and
--     both commit.
--
-- Additive only: this migration installs a function; it alters no existing object.

CREATE FUNCTION enforce_association_cap() RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
  cap integer;
  endpoint text;
  degree integer;
BEGIN
  IF TG_NARGS <> 1 OR TG_ARGV[0] !~ '^[0-9]+$' THEN
    RAISE EXCEPTION 'association_cap_trigger_requires_one_integer_argument' USING ERRCODE = 'P0001';
  END IF;
  cap := TG_ARGV[0]::integer;
  IF cap < 1 OR cap > 1024 THEN
    RAISE EXCEPTION 'association_cap_out_of_range' USING ERRCODE = 'P0001';
  END IF;
  IF NEW.from_id = NEW.to_id THEN
    RAISE EXCEPTION 'association_self_loop_rejected' USING ERRCODE = 'P0001';
  END IF;
  -- Fixed lock order (sorted endpoints) prevents deadlock between opposite-direction edges.
  FOREACH endpoint IN ARRAY ARRAY(SELECT unnest(ARRAY[NEW.from_id, NEW.to_id]) ORDER BY 1) LOOP
    PERFORM pg_catalog.pg_advisory_xact_lock(
      pg_catalog.hashtext(TG_TABLE_SCHEMA || '.' || TG_TABLE_NAME), pg_catalog.hashtext(endpoint)
    );
  END LOOP;
  FOREACH endpoint IN ARRAY ARRAY[NEW.from_id, NEW.to_id] LOOP
    EXECUTE pg_catalog.format(
      'SELECT count(*) FROM %I.%I WHERE (from_id = $1 OR to_id = $1) AND ($2::text IS NULL OR id <> $2)',
      TG_TABLE_SCHEMA, TG_TABLE_NAME
    ) INTO degree USING endpoint, CASE WHEN TG_OP = 'UPDATE' THEN OLD.id ELSE NULL END;
    IF degree >= cap THEN
      RAISE EXCEPTION 'association_cap_exceeded' USING ERRCODE = '54000';  -- program_limit_exceeded
    END IF;
  END LOOP;
  RETURN NEW;
END
$$;
