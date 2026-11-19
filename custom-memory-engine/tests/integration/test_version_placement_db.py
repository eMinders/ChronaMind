"""Close-then-insert keeps a belief where it was (found while wiring ``change``, ``V2-D81``; ``V2-D82``).

``R1`` makes every revision of a belief a *new row*: supersession re-issues the old value under a closed validity
range and inserts the new one, boundary revision re-issues both sides, and ending a multi-valued fact re-issues the
row with an upper bound. Each re-issued row is the **same belief**, so it must sit at the same scope, with the same
visibility, the same kind, the same method and the same subject entity.

Before this fix none of those columns was written on any close-then-insert path. ``fill_scope_path()`` (migration 11)
then filled the personal root, so a belief stored at ``/u/u1/p/chrona/`` moved to ``/u/u1/`` the first time it was
superseded: it left every reader who held only the project scope, and a scoped ``change`` query reported it as removed.

Every test asserts the copy against the original read back from the database, so a column the original does not have
is not invented. Control: with the placement carry reverted every test here fails (see the commit message).
"""

from datetime import timedelta

import pytest
from psycopg import AsyncConnection

from custom_memory_engine.engine.transactions import (
    CandidateVersionInput,
    EvidenceInput,
    commit_initial_version,
    end_multivalued,
    resolve_conflict_pending,
    revise_transition_boundary,
    stage_candidate,
    supersede_single,
)
from tests.integration.test_transactions_db import (
    APR1,
    JUL15,
    JUN1,
    MAR10,
    connect,
    database,  # noqa: F401  (fixture)
)

pytestmark = pytest.mark.integration

#: A sub-scope of the user's root: the placement the bug lost.
PROJECT = "/u/u1/p/chrona/"
PLACEMENT = ("scope_path", "visibility", "kind", "subject_entity_id")


async def _placement(conn: AsyncConnection, version_id: str) -> tuple[object, ...]:
    cursor = await conn.execute(
        f"SELECT {', '.join(PLACEMENT)} FROM memory_version WHERE id = %s",  # noqa: S608 (constant columns)
        (version_id,),
    )
    row = await cursor.fetchone()
    assert row is not None, version_id
    return tuple(row)


async def _seed(
    conn: AsyncConnection, version_id: str, *, predicate: str, valid_from, content: str, single: bool = True
) -> None:
    """One belief at the project scope, shared with the project: the placement every copy must keep."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
        await commit_initial_version(
            conn,
            CandidateVersionInput(
                version_id=version_id,
                memory_key=f"u1:{predicate}:{version_id}",
                user_id="u1",
                scope_path=PROJECT,
                subject_key="user",
                predicate_key=predicate,
                single_valued=single,
                content=content,
                valid_from=valid_from,
                layer="semantic" if single else "persona",
                evidence=(EvidenceInput(turn_id="t1", evidence_span="I live in Cairo."),),
            ),
        )
    # No public write sets visibility yet; the column exists and a copy must keep whatever it holds.
    await conn.execute("UPDATE memory_version SET visibility = 'project' WHERE id = %s", (version_id,))


def _candidate(version_id: str, *, valid_from, content: str, scope_path: str | None) -> CandidateVersionInput:
    return CandidateVersionInput(
        version_id=version_id,
        memory_key="u1:residence:new",
        user_id="u1",
        scope_path=scope_path,
        subject_key="user",
        predicate_key="residence",
        single_valued=True,
        content=content,
        valid_from=valid_from,
        evidence=(EvidenceInput(turn_id="t2", evidence_span="I moved to Berlin on June 1."),),
    )


async def test_supersession_reissues_the_old_value_where_it_was(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=MAR10, content="Cairo")
        original = await _placement(conn, "old")
        assert original == (PROJECT, "project", "claim", None), original
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            outcome = await supersede_single(
                conn,
                _candidate("new", valid_from=JUN1, content="Berlin", scope_path=PROJECT),
                reissued_version_id="old_reissued",
            )
        assert outcome.reissued_version_id == "old_reissued"
        assert await _placement(conn, "old_reissued") == original, "the re-issued old value moved"
        assert (await _placement(conn, "new"))[:1] == (PROJECT,), "the new value left the scope it was asserted in"


async def test_a_candidate_that_names_no_scope_supersedes_in_place(database) -> None:  # noqa: F811
    """The extraction pipeline names no scope (v1): the new value takes the place of the belief it supersedes."""
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=MAR10, content="Cairo")
        original = await _placement(conn, "old")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await supersede_single(conn, _candidate("new", valid_from=JUN1, content="Berlin", scope_path=None))
        assert await _placement(conn, "new") == original, "a scope-less supersession moved the belief to the root"


async def test_a_candidate_in_another_scope_is_private_there(database) -> None:  # noqa: F811
    """Visibility is inherited only in place: a value moved to another scope is not shared there by inheritance."""
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=MAR10, content="Cairo")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await supersede_single(conn, _candidate("new", valid_from=JUN1, content="Berlin", scope_path="/u/u1/"))
        assert (await _placement(conn, "new"))[:2] == ("/u/u1/", "private")


async def test_a_held_candidate_is_held_where_it_was_asserted(database) -> None:  # noqa: F811
    """``R2`` rule 3 (``v_new < v_old``) holds the candidate; it is held in place, not at the root."""
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=JUN1, content="Berlin")
        original = await _placement(conn, "old")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            outcome = await supersede_single(
                conn, _candidate("held", valid_from=MAR10, content="Cairo", scope_path=None)
            )
        assert outcome.held_in_conflict_pending
        assert await _placement(conn, "held") == original


async def test_accepting_a_staged_candidate_reissues_the_covered_value_where_it_was(database) -> None:  # noqa: F811
    """``R4`` accept re-issues the belief the accepted candidate now covers -- in place, not at the root."""
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=MAR10, content="Cairo")
        original = await _placement(conn, "old")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await stage_candidate(conn, _candidate("staged", valid_from=JUN1, content="Berlin", scope_path=None))
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            outcome = await resolve_conflict_pending(
                conn,
                user_id="u1",
                candidate_version_id="staged",
                decision="accept",
                reason_code="adjudicated_confirm",
                reissued_version_id="old_reissued",
            )
        assert outcome.resolution_status == "active"
        assert outcome.reissued_version_id == "old_reissued", outcome
        assert await _placement(conn, "old_reissued") == original


async def test_boundary_revision_reissues_both_sides_where_they_were(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await _seed(conn, "old", predicate="residence", valid_from=MAR10, content="Cairo")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await supersede_single(
                conn,
                _candidate("new", valid_from=JUN1, content="Berlin", scope_path=PROJECT),
                reissued_version_id="prior",
            )
        await conn.execute("UPDATE memory_version SET visibility = 'project' WHERE id IN ('prior', 'new')")
        prior, later = await _placement(conn, "prior"), await _placement(conn, "new")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            await revise_transition_boundary(
                conn,
                user_id="u1",
                prior_version_id="prior",
                later_version_id="new",
                revised_boundary=JUN1 + timedelta(days=14),
                reissued_prior_version_id="prior_rev",
                reissued_later_version_id="later_rev",
            )
        assert await _placement(conn, "prior_rev") == prior
        assert await _placement(conn, "later_rev") == later


async def test_ending_a_multivalued_fact_keeps_it_where_it_was(database) -> None:  # noqa: F811
    async with connect(database) as conn:
        await _seed(conn, "hobby", predicate="hobby", valid_from=APR1, content="chess", single=False)
        original = await _placement(conn, "hobby")
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'u1', true)")
            outcome = await end_multivalued(
                conn, user_id="u1", version_id="hobby", valid_end=JUL15, truncated_version_id="hobby_ended"
            )
        assert outcome.truncated_version_id == "hobby_ended"
        assert await _placement(conn, "hobby_ended") == original
