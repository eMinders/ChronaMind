"""Migration 21: procedure storage, ``preconditions`` and ``steps`` (``E-030``, issue #201 criteria 1 and 4).

The properties the migration itself must own, checked against the REAL migrated schema rather than through the handler
(a replay harness, a backfill or an operator writes SQL directly): only a procedure may carry a shape; a shape is never
updated; a malformed one is refused at write time; the parser and the database accept exactly the same shapes; the
shape goes with its procedure; and the rollback restores version 20 exactly.
"""

import json
from typing import LiteralString, cast

import pytest
from psycopg import AsyncConnection
from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from custom_memory_engine.engine.procedure_shape import ProcedureShapeError, parse_shape
from custom_memory_engine.model.axes import FACETS, KINDS, LIFECYCLES
from custom_memory_engine.schema import migrate
from tests.integration.rollbacks import rollback_sql
from tests.integration.test_migration_m1 import connect, database, rows  # noqa: F401  (fixture; seeds u1 and u2)
from tests.integration.test_migration_m20 import version

pytestmark = pytest.mark.integration
M21 = 21
VALID_PRECONDITIONS = [
    {"op": "scope_within", "scope": "/u/u1/p/chrona/"},
    {"op": "exists", "match": {"kind": "episode", "predicate": "episode"}},
    {"op": "absent", "match": {"lifecycle": "contested", "facet": "constraint"}},
]


async def shape(
    conn: AsyncConnection, version_id: str, preconditions: object = (), steps: object = (), *, user: str = "u1"
) -> None:
    await conn.execute(
        "INSERT INTO procedure_shape (version_id, user_id, preconditions, steps) VALUES (%s, %s, %s, %s)",
        (version_id, user, Jsonb(list(preconditions) if isinstance(preconditions, tuple) else preconditions),
         Jsonb(list(steps) if isinstance(steps, tuple) else steps)),
    )  # fmt: skip


async def test_a_procedure_stores_its_preconditions_and_steps(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await shape(conn, "p1", VALID_PRECONDITIONS, ["Rerun the focused suite.", "Check the missing import."])
        assert await rows(conn, "SELECT preconditions, steps FROM procedure_shape WHERE version_id = 'p1'") == [
            (VALID_PRECONDITIONS, ["Rerun the focused suite.", "Check the missing import."])
        ]


async def test_an_empty_shape_is_valid_and_is_the_column_default(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await conn.execute("INSERT INTO procedure_shape (version_id, user_id) VALUES ('p1', 'u1')")
        assert await rows(conn, "SELECT preconditions, steps FROM procedure_shape") == [([], [])]


async def test_only_a_procedure_may_carry_a_shape(database) -> None:  # noqa: F811
    """The guard is ``kind IS DISTINCT FROM 'procedure'``: a claim is refused, and so is an id with no version at all.

    (Migration 20's seed builds semantic-layer rows, i.e. claims and procedures; the guard does not depend on which
    other kind it meets, and the API-level test refuses a shape on a claim through ``Engine.call``.)
    """
    async with connect(database) as conn, conn.transaction():
        await version(conn, "c1", kind="claim")
        with pytest.raises(RaiseException, match="procedure_shape_requires_a_procedure"):
            await shape(conn, "c1", VALID_PRECONDITIONS)
    async with connect(database) as conn, conn.transaction():
        with pytest.raises(RaiseException, match="procedure_shape_requires_a_procedure"):
            await shape(conn, "no-such-version")


async def test_a_shape_cannot_cite_another_principals_procedure(database) -> None:  # noqa: F811
    """The guard looks the procedure up in the writer's tenant; the composite foreign key would refuse it too."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1", user="u1")
        with pytest.raises(RaiseException, match="procedure_shape_requires_a_procedure"):
            await shape(conn, "p1", user="u2")


@pytest.mark.parametrize(
    "assignment",
    [
        "preconditions = '[]'::jsonb",
        "steps = '[\"another step\"]'::jsonb",
        "version_id = 'p2'",
        "user_id = 'u2'",
    ],
)
async def test_a_shape_is_never_updated(database, assignment: str) -> None:  # noqa: F811
    """Preconditions and steps are part of what was asserted: a later change would rewrite the procedure."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        await version(conn, "p2")
        await shape(conn, "p1", VALID_PRECONDITIONS, ["Rerun the focused suite."])
        with pytest.raises(RaiseException, match="procedure_shape_is_immutable"):
            await conn.execute(cast(LiteralString, f"UPDATE procedure_shape SET {assignment} WHERE version_id = 'p1'"))


async def test_the_shape_goes_with_its_procedure(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1", active=False)  # a candidate, the status a test may delete (see migration 20's test)
        await shape(conn, "p1", VALID_PRECONDITIONS)
        await conn.execute("DELETE FROM memory_version WHERE id = 'p1'")
        assert await rows(conn, "SELECT count(*) FROM procedure_shape") == [(0,)]


# ---------------------------------------------------------------------------------------------- the grammar, in SQL

#: Every entry is run through the Python parser and the database guard; the two must agree (issue #201 §7 rec. 1).
CORPUS: list[tuple[object, object]] = [
    (VALID_PRECONDITIONS, ["step"]),
    ([], []),
    ([{"op": "scope_within", "scope": "/u/u1/"}], []),
    ([{"op": "scope_within", "scope": "/u/user.name@example/p/a-b/s/s1/"}], []),
    ([{"op": "exists", "match": {"predicate": "a" * 64}}], []),
    ([{"op": "absent", "match": {"kind": "claim"}}] * 32, ["x" * 2000] * 64),
    # malformed: every one must be refused by both
    ({"op": "exists"}, []),
    (["x"], []),
    ([{"op": "run", "match": {"kind": "claim"}}], []),
    ([{"match": {"kind": "claim"}}], []),
    ([{"op": "scope_within"}], []),
    ([{"op": "scope_within", "scope": 7}], []),
    ([{"op": "scope_within", "scope": "/u/u1/", "x": 1}], []),
    ([{"op": "scope_within", "scope": "/u/u1"}], []),
    ([{"op": "scope_within", "scope": "u/u1/"}], []),
    ([{"op": "scope_within", "scope": "/v/u1/"}], []),
    ([{"op": "scope_within", "scope": "/u/"}], []),
    ([{"op": "scope_within", "scope": "/u//p/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1//"}], []),
    ([{"op": "scope_within", "scope": "/u/../"}], []),
    ([{"op": "scope_within", "scope": "/u/ u1/"}], []),
    ([{"op": "scope_within", "scope": "/u/　u1/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1 /"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/a\x07b/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/Repo/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/a_b/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/-a/"}], []),
    ([{"op": "scope_within", "scope": "/u/" + "x" * 257 + "/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/" + "a" * 129 + "/"}], []),
    ([{"op": "scope_within", "scope": "/u/u1/" + "a/" * 15}], []),
    ([{"op": "exists", "match": {}}], []),
    ([{"op": "exists", "match": "kind"}], []),
    ([{"op": "exists", "match": {"kind": "claim"}, "scope": "/u/u1/"}], []),
    ([{"op": "exists", "match": {"kind": "fact"}}], []),
    ([{"op": "exists", "match": {"lifecycle": "verified"}}], []),
    ([{"op": "exists", "match": {"facet": "inferred_preference"}}], []),
    ([{"op": "exists", "match": {"predicate": "Diet"}}], []),
    ([{"op": "exists", "match": {"predicate": "a" * 65}}], []),
    ([{"op": "exists", "match": {"content": "x"}}], []),
    ([{"op": "exists", "match": {"kind": 3}}], []),
    ([{"op": "absent", "match": {"kind": "claim"}}] * 33, []),
    ([], "step"),
    ([], [""]),
    ([], ["　\t"]),
    ([], [" "]),
    ([], [3]),
    ([], ["x" * 2001]),
    ([], ["x"] * 65),
]


def python_accepts(preconditions: object, steps: object) -> bool:
    try:
        parse_shape(preconditions, steps)
    except ProcedureShapeError:
        return False
    return True


async def database_accepts(conn: AsyncConnection, preconditions: object, steps: object) -> bool:
    try:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO procedure_shape (version_id, user_id, preconditions, steps) VALUES ('p1', 'u1', %s, %s)",
                (Jsonb(preconditions), Jsonb(steps)),
            )
            raise _Accepted
    except _Accepted:
        return True
    except RaiseException as error:
        assert str(error).split("\n")[0] in {"procedure_preconditions_malformed", "procedure_steps_malformed"}
        return False


class _Accepted(Exception):
    """Rolls the savepoint back after an accepted insert, so every corpus entry tests the same empty table."""


async def test_the_parser_and_the_database_accept_exactly_the_same_shapes(database) -> None:  # noqa: F811
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        disagreements = []
        for preconditions, steps in CORPUS:
            in_python = python_accepts(preconditions, steps)
            in_sql = await database_accepts(conn, preconditions, steps)
            if in_python != in_sql:
                disagreements.append((json.dumps(preconditions)[:80], json.dumps(steps)[:40], in_python, in_sql))
        assert disagreements == []
        # and the corpus really exercises both answers
        assert {python_accepts(p, s) for p, s in CORPUS} == {True, False}


async def test_the_database_vocabularies_are_the_models(database) -> None:  # noqa: F811
    """A value added to ``model.axes`` but not to the guard (or the reverse) would make the two disagree."""
    async with connect(database) as conn, conn.transaction():
        await version(conn, "p1")
        for field, values in (("kind", KINDS), ("lifecycle", LIFECYCLES), ("facet", FACETS)):
            for value in values:
                assert await database_accepts(conn, [{"op": "exists", "match": {field: value}}], []), (field, value)


# ---------------------------------------------------------------------------------------------- rollback (criterion 1)


async def test_rollback_restores_version_20_exactly_and_reapply_works(database) -> None:  # noqa: F811
    config, schema = database
    runner = migrate.MigrationRunner(migrate.MigrationConfig(database_url=config.database_url, schema_name=schema))
    async with connect(database) as conn:
        before = await runner._catalog(conn)
        await conn.execute(
            cast(LiteralString, rollback_sql(from_version=migrate.VERSION, to_version=M21 - 1)), prepare=False
        )
        recorded = await rows(conn, "SELECT catalog_sha256 FROM _cme_schema_migrations WHERE version = %s", (M21 - 1,))
        assert await runner._catalog(conn) == recorded[0][0]  # the version-20 fingerprint, exactly
        assert before != recorded[0][0]  # the migration did change the catalog
        assert not await rows(
            conn,
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() "
            "AND table_name = 'procedure_shape'",
        )
        assert not await rows(
            conn,
            "SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
            "WHERE n.nspname = current_schema() AND p.proname = 'guard_procedure_shape'",
        )
        # migration 20 is untouched by rolling 21 back
        assert await rows(
            conn,
            "SELECT 1 FROM information_schema.tables WHERE table_schema = current_schema() "
            "AND table_name = 'outcome_stats'",
        )
    assert (await runner.status()).version == M21 - 1
    assert (await runner.apply()).action == "applied"
    assert (await runner.status()).version == M21
