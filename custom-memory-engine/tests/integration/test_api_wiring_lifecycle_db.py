"""The operations that had no handler: ``stats``, ``verify``, ``dissent``, ``expire``, ``rebuild_index``, ``export``.

Before this module the contract declared all of them and ``api.wiring.UNWIRED_OPERATIONS`` named each with a reason
that was real: ``verify`` needs a disjointness algorithm, ``dissent`` needs a reader in inference or it writes a
write-only constraint, ``expire`` needs a policy that does not invent ``V2-D14``'s numbers, and ``rebuild_index`` needs
the queue. Each is now implemented and each test below drives it through the **real** ``Engine.call`` as a restricted
role against the migrated schema, so what is asserted is the contract rather than the handler's private opinion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from psycopg import AsyncConnection, sql

from custom_memory_engine.api.entry import Engine
from custom_memory_engine.api.lifecycle_handlers import ConstraintList
from custom_memory_engine.api.maintain_handlers import ExpiryReceipt
from custom_memory_engine.api.runtime import Receipt
from custom_memory_engine.api.wiring import UNWIRED_OPERATIONS, wire_engine
from custom_memory_engine.engine.export import ExportManifest
from custom_memory_engine.engine.statistics import ArtifactStats
from custom_memory_engine.profile import parse_profile
from custom_memory_engine.scope.access import Selector, resolve_read_set
from tests.integration.test_api_wiring_structure import (  # noqa: F401  (fixtures)
    caller,
    connect,
    database,
    forbidden,
    rows,
)
from tests.integration.test_api_wiring_structure import world as structure_world
from tests.integration.test_hybrid_recall_db import T0

pytestmark = pytest.mark.integration
#: The method family the insert trigger assigns to an inferred persona row. `negative_constraint.method_family` stores
#: the method's *name*, which is how versions of one method share one family (`V2-D97`).
INHERITED_ID = "m-v1-persona"
INHERITED = "v1-persona"
#: ``sha256('v1-persona:1')``, exactly as migration 10 seeds it (`encode(sha256('v1-persona:1'::bytea), 'hex')`).
PERSONA_HASH = "4e7417b078cfd2f705629996b0b10309445b96422b266fb31030395bcb6c7c8a"  # pragma: allowlist secret


@asynccontextmanager
async def world(database) -> AsyncIterator[tuple[AsyncConnection, Engine, str]]:  # noqa: F811
    """The structure fixture's world with the **full** deployment wiring: what a host actually builds
    (``wire_engine``).

    The structure fixture wires only the structure handlers, which is why an unwired operation answered
    ``operation_not_wired`` before this module existed. Every test here goes through one ``wire_engine`` call, so the
    registration of the new handlers is itself under test.
    """
    async with structure_world(database) as (conn, engine, schema):
        yield conn, wire_engine(engine.resolver.runtime), schema  # type: ignore[attr-defined]


async def grounded(
    conn: AsyncConnection,
    version_id: str,
    *,
    user: str = "alice",
    content: str = "I moved to Berlin",
    created: bool = True,
) -> None:
    """One asserted claim owned by ``user``, with its evidence — and its creator when ``created``.

    ``created_by``/``created_tier`` matter for two conditions and not the others: ``own_lineage_only`` (``verify``,
    ``refute``) is decided by the lineage owner, so an unowned row is ``forbidden`` there by design, while ``stats``
    and ``dissent`` never ask. Writing the owner here keeps each test about the condition it names.
    """
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "retention, created_by, created_tier) "
            "VALUES (%s, %s, %s, 'active', 'asserted', 'claim', 'self', 'statement', false, %s, "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', %s, 'private', 'durable', %s, %s)",
            (
                version_id,
                version_id,
                user,
                content,
                T0,
                f"/u/{user}/",
                user if created else None,
                "B" if created else None,
            ),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (version_id, "t-" + user, user),
        )


async def inferred(
    conn: AsyncConnection,
    version_id: str,
    *,
    user: str = "alice",
    content: str = "alice prefers morning meetings",
    facet: str | None = None,
    method_id: str | None = None,
) -> None:
    """One ``inferred`` abstraction with its derivation, ledger and method: the shape ``I1'`` and ``I15`` require.

    Written directly rather than through ``infer`` because these tests are about the operations that *answer* a belief,
    not about how it was derived; ``tests/integration/test_api_wiring_infer.py`` covers the derivation itself.
    """
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', %s, true)", (user,))
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "retention) "
            "VALUES (%s, %s, %s, 'active', 'asserted', 'claim', 'self', 'statement', false, 'support for ' || %s, "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', %s, 'private', 'durable')",
            ("src-" + version_id, "src-" + version_id, user, version_id, T0, f"/u/{user}/"),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            ("src-" + version_id, "t-" + user, user),
        )
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "created_by, created_tier, um_facet) "
            "VALUES (%s, %s, %s, 'active', 'inferred', 'abstraction', 'self', 'statement', false, %s, "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'persona', %s, 'private', %s, 'B', %s)",
            (version_id, version_id, user, content, T0, f"/u/{user}/", user, facet),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            (version_id, "t-" + user, user),
        )
        await conn.execute(
            "INSERT INTO memory_derivation VALUES (%s, %s, %s, 'consolidation')",
            (version_id, "src-" + version_id, user),
        )
        await conn.execute(
            "INSERT INTO memory_version (id, memory_key, user_id, status, lifecycle, kind, subject_key, "
            "predicate_key, single_valued, content, event_range, recorded_range, layer, scope_path, visibility, "
            "retention) "
            "VALUES (%s, %s, %s, 'active', 'asserted', 'claim', 'self', 'statement', false, 'held out for ' || %s, "
            "tstzrange(%s, NULL), tstzrange(clock_now(), NULL), 'semantic', %s, 'private', 'durable')",
            ("held-" + version_id, "held-" + version_id, user, version_id, T0, f"/u/{user}/"),
        )
        await conn.execute(
            "INSERT INTO memory_evidence (version_id, turn_id, user_id, evidence_span, evidence_role) "
            "VALUES (%s, %s, %s, 'x', 'support')",
            ("held-" + version_id, "t-" + user, user),
        )
        # The insert trigger assigns the persona method (`assign_memory_version_method`); an UPDATE of `method_id` is
        # refused once set (`method_id_is_fixed_once_set`), so this only fills a row the trigger left without one.
        await conn.execute(
            "UPDATE memory_version SET method_id = %s WHERE id = %s AND method_id IS NULL",
            (method_id or INHERITED_ID, version_id),
        )
        # Seed an admissible ledger by default so `verify(outcome='verified')` checks pinning and bounds cleanly.
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 36, counter_count = 0, undetermined_count = 0, "
            "opportunity_count = 36, refutation_attempts = 3 WHERE version_id = %s AND user_id = %s",
            (version_id, user),
        )


def record(artifact_id: str = "v-default", **overrides: object) -> dict[str, object]:
    """A valid ``verification`` argument; ``overrides`` change one fact each, so a test refuses for one reason."""
    body: dict[str, object] = {
        "held_out_split_id": "split-2026-03",
        "negative_control_slice_id": "control-2026-03",
        "support_window": ["2026-01-01T00:00:00+00:00", "2026-02-01T00:00:00+00:00"],
        "held_out_window": ["2026-03-01T00:00:00+00:00", "2026-04-01T00:00:00+00:00"],
        "support_context_key": "weekday-mornings",
        "held_out_context_key": "weekend-evenings",
        "support_artifact_ids": [f"src-{artifact_id}"],
        "held_out_artifact_ids": [f"held-{artifact_id}"],
        "outcome": "verified",
    }
    body.update(overrides)
    return body


# ---------------------------------------------------------------------------------------------------- stats


async def test_stats_reports_the_ledger_and_the_lineage_ids_without_any_content(database) -> None:  # noqa: F811
    """``API_V2.md`` §4.11: counts, coverage, refutation attempts, verification status and the *ids* of support items.

    The assertion on content is the point: the report must be a report. It names ids the caller could already ``get``
    and never the text of one, so an observation cannot become a read side channel.
    """
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-abstraction")
        await conn.execute(
            "UPDATE abstraction_stats SET support_count = 3, counter_count = 1, undetermined_count = 2, "
            "opportunity_count = 6, refutation_attempts = 2 WHERE version_id = 'v-abstraction'"
        )
        result = await engine.call(caller("alice"), "stats", {"artifact_id": "v-abstraction"})
        assert isinstance(result, ArtifactStats)
        assert (result.support_count, result.counter_count, result.undetermined_count) == (3, 1, 2)
        assert result.coverage == pytest.approx(4 / 6)
        assert result.refutation_attempts == 2
        assert result.verification == "unverified"
        assert result.support_ids == ("src-v-abstraction",)  # the derivation, by id
        assert "alice prefers" not in repr(result)


async def test_stats_on_someone_elses_artifact_is_forbidden_and_indistinguishable_from_absent(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-bob", user="bob")
        assert (await forbidden(engine, caller("alice"), "stats", {"artifact_id": "v-bob"})).code == "forbidden"
        absent = await forbidden(engine, caller("alice"), "stats", {"artifact_id": "no-such-id"})
        assert absent.code == "forbidden"


async def test_stats_reports_a_grounded_artifacts_empty_ledger_rather_than_failing(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-claim", content="I moved to Berlin")
        result = await engine.call(caller("alice"), "stats", {"artifact_id": "v-claim"})
        assert isinstance(result, ArtifactStats)
        assert (result.kind, result.lifecycle, result.verification) == ("claim", "asserted", "none")
        assert result.opportunity_count == 0 and result.coverage == 0.0


async def test_stats_surfaces_the_verification_record_behind_a_verified_status(database) -> None:  # noqa: F811
    """The status is only honest if the record behind it is readable: this is the read half of ``I11``."""
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-verifiable")
        receipt = await engine.call(
            caller("alice", "B"),
            "verify",
            {"artifact_id": "v-verifiable", "verification": record("v-verifiable")},
        )
        assert isinstance(receipt, Receipt) and receipt.effect == "verified"
        result = await engine.call(caller("alice"), "stats", {"artifact_id": "v-verifiable"})
        assert isinstance(result, ArtifactStats)
        assert (result.verification, result.held_out_split_id) == ("verified", "split-2026-03")
        assert result.negative_control_slice_id == "control-2026-03"
        assert result.windows_disjoint is True
        assert result.verified_on is not None
        assert result.artifact_pinning_status == "pinned"
        assert result.pinning_label == "pinned"
        assert result.verified_support_ids == ("src-v-verifiable",)
        assert result.verified_held_out_ids == ("held-v-verifiable",)
        assert result.closed_over_hash is not None


# --------------------------------------------------------------------------------------------------- verify


async def test_verify_records_a_disjoint_split_and_moves_the_status(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-v2")
        await engine.call(caller("alice", "B"), "verify", {"artifact_id": "v-v2", "verification": record("v-v2")})
        assert await rows(conn, "SELECT count(*) FROM verification WHERE version_id = 'v-v2'") == [(1,)]


async def test_a_leaky_split_is_refused_before_anything_is_written(database) -> None:  # noqa: F811
    """``I11``: the held-out set must be disjoint. A shared context key is leakage, and a shared window is a tautology.

    Each refusal is asserted with its own code so the two checks stay distinguishable, and the ledger is asserted
    unchanged afterwards: a refused verification must not leave a status it cannot justify.
    """
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-v3")
        shared_context = record("v-v3", held_out_context_key="weekday-mornings")
        refused = await forbidden(
            engine, caller("alice", "B"), "verify", {"artifact_id": "v-v3", "verification": shared_context}
        )
        assert refused.detail == "split_shares_a_context_key"
        overlapping = record("v-v3", held_out_window=["2026-01-15T00:00:00+00:00", "2026-02-15T00:00:00+00:00"])
        refused = await forbidden(
            engine, caller("alice", "B"), "verify", {"artifact_id": "v-v3", "verification": overlapping}
        )
        assert refused.detail == "split_windows_overlap"
        assert await rows(conn, "SELECT verification_status FROM abstraction_stats WHERE version_id = 'v-v3'") == [
            ("unverified",)
        ]


async def test_the_same_split_cannot_be_verified_twice(database) -> None:  # noqa: F811
    """Re-running one split until it agrees is not verification, so the second posting is refused."""
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-v4")
        await engine.call(caller("alice", "B"), "verify", {"artifact_id": "v-v4", "verification": record("v-v4")})
        refused = await forbidden(
            engine, caller("alice", "B"), "verify", {"artifact_id": "v-v4", "verification": record("v-v4")}
        )
        assert refused.detail == "split_already_verified"


async def test_only_an_inferred_artifact_can_be_verified(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-grounded", content="I moved to Berlin")
        refused = await forbidden(
            engine, caller("alice", "B"), "verify", {"artifact_id": "v-grounded", "verification": record()}
        )
        assert refused.detail == "artifact_not_inferable"


async def test_a_malformed_verification_is_a_validation_error_with_a_static_code(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-v5")
        for body, code in (
            # the windows are validated before the ids, so an empty body reports the first structural fault
            ({}, "support_window_invalid"),
            (record(outcome="maybe"), "outcome_invalid"),
            (record(support_window="not-a-window"), "support_window_invalid"),
            (record(negative_control_slice_id="split-2026-03"), "control_slice_must_differ_from_the_split"),
        ):
            refused = await forbidden(
                engine, caller("alice", "B"), "verify", {"artifact_id": "v-v5", "verification": body}
            )
            assert (refused.code, refused.detail) == ("validation_error", code)


# -------------------------------------------------------------------------------------------------- dissent


async def test_dissent_retires_the_inference_and_records_the_constraint(database) -> None:  # noqa: F811
    """``I16``: the rejection is more than a retirement — it is a record that blocks the same element being re-
    learned."""
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-dissent")
        receipt = await engine.call(caller("alice"), "dissent", {"artifact_id": "v-dissent", "note": "user_dissent"})
        assert isinstance(receipt, Receipt)
        assert (receipt.id, receipt.effect) == ("v-dissent", "retired")
        assert await rows(
            conn,
            "SELECT scope_path, predicate_key, method_family, reason FROM negative_constraint WHERE user_id = 'alice'",
        ) == [("/u/alice/", "statement", INHERITED, "user_dissent")]


async def test_dissent_refuses_a_belief_that_is_not_inferred(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-asserted", content="I prefer tea")
        refused = await forbidden(engine, caller("alice"), "dissent", {"artifact_id": "v-asserted"})
        assert (refused.code, refused.detail) == ("precondition_failed", "artifact_not_inferable")


async def test_dissent_is_tier_c_only(database) -> None:  # noqa: F811
    """TB2: a cognition service must not dissent on the user's behalf."""
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-d2")
        assert (await forbidden(engine, caller("alice", "B"), "dissent", {"artifact_id": "v-d2"})).code == "forbidden"


async def test_a_dissented_element_is_not_silently_relearned(database) -> None:  # noqa: F811
    """The other half of ``I16``: ``infer`` consults the constraint, so the rejection has an effect.

    Without this the negative constraint would be the write-only table this project keeps finding: retired once, and
    re-derived by the next reflection cycle as if the user had said nothing.
    """
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-relearn")
        await engine.call(caller("alice"), "dissent", {"artifact_id": "v-relearn"})
        again = await engine.call(
            caller("alice", "B"),
            "infer",
            {
                "scope": "/u/alice/",
                # the registry row for `m-v1-persona`, verbatim: `(id, version, params_hash)` is the identity `infer`
                # is checked against, so a made-up hash is `rejected`/`method_not_registered` rather than accepted
                "method": {"id": INHERITED_ID, "version": "1", "params_hash": PERSONA_HASH},
                "sources": ["src-v-relearn"],  # the support row `inferred` wrote
                "statement": "alice prefers morning meetings",
                "ledger": {"support": 1, "counter": 0, "undetermined": 0, "opportunity": 1},
                "idempotency_key": "re-learn-1",
            },
        )
        assert getattr(again, "reason_code", None) == "negative_constraint_active"


# --------------------------------------------------------------------------------------------------- expire


PROFILE = {
    "schema_version": 1,
    "name": "expiring",
    "scope_root": "/u/{user}/",
    "kinds": ["claim", "abstraction"],
    "facets": ["preference"],
    "render": {
        "channel_budget_share": {"facts": 1.0},
        "show_provenance": True,
        "show_validity_intervals": True,
    },
    "retention": {
        "defaults": {"claim": "durable", "abstraction": "durable"},
        "promote_to_durable": False,
        "status": "decided",
    },
    # `V2-D14`'s numbers, supplied by the deployment rather than compiled into the handler: 30 days for a preference.
    "abstraction_expiry": {"days_by_facet": {"preference": 30}, "status": "decided"},
}


async def test_expire_stamps_the_policy_window_and_destroys_nothing(database) -> None:  # noqa: F811
    """``V2-D97``: expiry is a derived-cause stamp (``expires_at``), not a deletion, and it reads ``V2-D14``'s numbers
    from the profile instead of inventing them."""
    profile = parse_profile(PROFILE)
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-old", facet="preference", content="alice prefers mornings")
        await conn.execute("UPDATE memory_version SET event_range = tstzrange(%s, NULL) WHERE id = 'v-old'", (T0,))
        with_policy = wire_engine(engine.resolver.runtime, profile=profile)  # type: ignore[attr-defined]
        # no `as_of`: the engine's own clock is the observation (V2-D73), and the belief is older than its window
        receipt = await with_policy.call(caller("alice", "C", admin=True), "expire", {"scope": "/u/alice/"})
        assert isinstance(receipt, ExpiryReceipt)
        assert (receipt.expired, receipt.considered) == (1, 1)
        assert await rows(conn, "SELECT expires_at IS NOT NULL FROM abstraction_stats WHERE version_id = 'v-old'") == [
            (True,)
        ]
        # nothing was removed: the belief is still there, and now it is stale rather than fresh
        assert await rows(conn, "SELECT count(*) FROM memory_version WHERE id = 'v-old'") == [(1,)]


async def test_a_retried_expire_changes_nothing_the_first_one_did(database) -> None:  # noqa: F811
    """``I9`` for the maintenance path: a client that retries ``expire`` after a timeout must not move the stamp."""
    profile = parse_profile(PROFILE)
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-retry", facet="preference", content="alice prefers mornings")
        await conn.execute("UPDATE memory_version SET event_range = tstzrange(%s, NULL) WHERE id = 'v-retry'", (T0,))
        with_policy = wire_engine(engine.resolver.runtime, profile=profile)  # type: ignore[attr-defined]
        admin = caller("alice", "C", admin=True)
        first = await with_policy.call(admin, "expire", {"scope": "/u/alice/"})
        stamped = await rows(conn, "SELECT expires_at FROM abstraction_stats WHERE version_id = 'v-retry'")
        again = await with_policy.call(admin, "expire", {"scope": "/u/alice/"})
        assert isinstance(first, ExpiryReceipt) and isinstance(again, ExpiryReceipt)
        assert first.expired == 1 and again.expired == 0, "the retry finds the stamp already there"
        assert await rows(conn, "SELECT expires_at FROM abstraction_stats WHERE version_id = 'v-retry'") == stamped


async def test_expire_without_a_policy_expires_nothing_and_says_so(database) -> None:  # noqa: F811
    """``V2-D14``'s numbers are not this handler's to invent: no policy means zero, with the count that shows why."""
    async with world(database) as (conn, engine, _):
        await inferred(conn, "v-nopolicy", facet="preference")
        receipt = await engine.call(caller("alice", "C", admin=True), "expire", {"scope": "/u/alice/"})
        assert isinstance(receipt, ExpiryReceipt)
        # no profile means no window, so nothing is due *and* nothing is even considered: the handler owns no defaults
        assert (receipt.expired, receipt.considered) == (0, 0)


async def test_expire_refuses_an_as_of_in_the_future(database) -> None:  # noqa: F811
    """A caller may not date the clock forward: one request would expire a whole workspace."""
    async with world(database) as (conn, engine, _):
        future = (datetime.now(UTC) + timedelta(days=1)).isoformat()
        refused = await forbidden(
            engine, caller("alice", "C", admin=True), "expire", {"scope": "/u/alice/", "as_of": future}
        )
        assert (refused.code, refused.detail) == ("precondition_failed", "as_of_in_the_future")


async def test_expire_is_admin_only(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        assert (await forbidden(engine, caller("alice", "C"), "expire", {})).code == "forbidden"


# ------------------------------------------------------------------------------------------ rebuild_index


async def test_rebuild_index_queues_one_job_and_returns_its_id(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        job_id = await engine.call(caller("alice", "C", admin=True), "rebuild_index", {"index": "fts"})
        assert isinstance(job_id, str) and job_id.startswith("job_")
        assert await rows(
            conn,
            "SELECT kind, status, payload->>'index', payload->>'deletion_epoch' FROM job WHERE id = %s",
            (job_id,),
        ) == [("index_rebuild", "queued", "fts", "0")]


async def test_rebuild_index_refuses_an_unknown_index_and_a_non_admin(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        refused = await forbidden(engine, caller("alice", "C", admin=True), "rebuild_index", {"index": "everything"})
        assert (refused.code, refused.detail) == ("validation_error", "index_unknown")
        assert (await forbidden(engine, caller("alice", "C"), "rebuild_index", {"index": "fts"})).code == "forbidden"


async def test_nothing_is_left_unwired(database) -> None:  # noqa: F811
    """The list that declared the six gaps is now empty, and reality has to agree."""
    assert UNWIRED_OPERATIONS == {}
    async with world(database) as (conn, engine, _):
        assert engine.unwired_operations() == ()


# ---------------------------------------------------------------------------------------------------- export


async def test_export_manifests_what_the_caller_can_read_and_replays_the_same_manifest(database) -> None:  # noqa: F811
    """``API_V2.md`` §4.9: a manifest of the read set, and the same manifest again for an unchanged one.

    The digest is asserted to be the digest *of the ids the caller can actually read*, which is what makes the manifest
    checkable: it is a fact about the corpus at one epoch, not a copy of it.
    """
    from custom_memory_engine.engine.export import manifest_digest

    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-a", content="I live in Berlin")
        await grounded(conn, "v-b", content="I prefer tea")
        await grounded(conn, "v-bob", user="bob", content="not alice's")
        alice = caller("alice")
        first = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(first, ExportManifest)
        assert first.captured_epoch == 0
        assert first.artifact_count == 2
        assert first.manifest_digest == manifest_digest(["v-a", "v-b"])
        assert first.scope_paths == ("/u/alice/",)
        second = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(second, ExportManifest)
        assert (second.snapshot_id, second.created) == (first.snapshot_id, False)


async def test_a_selector_narrower_than_the_read_set_does_not_widen_the_export(database) -> None:  # noqa: F811
    """The manifest cannot state a scope its ids exceed.

    Reachable through sharing: bob holds a grant on ``/u/alice/``, so his read set covers the whole namespace, and he
    exports the narrower ``/u/alice/work/``. Keeping the grant's entry would read every row alice owns at
    ``/u/alice/`` while the manifest recorded ``/u/alice/work/``; anchoring at the request reads exactly that
    sub-scope, which holds nothing (migration 11 pins an artifact to its owner's root).
    """
    from custom_memory_engine.api.export_handlers import ExportHandlers

    async with world(database) as (conn, _engine, _):
        await grounded(conn, "v-root", content="I live in Berlin")
        # the host resolves bob's read set from his grants, so it covers all of alice's namespace ...
        read_set = resolve_read_set("bob", [Selector.of("/u/alice/")], ["/u/alice/"])
        assert [entry.path for entry in read_set.entries] == ["/u/alice/"]
        # ... while the export he asks for names only a sub-scope

        narrowed = ExportHandlers._narrow(read_set, ("/u/alice/work/",))
        assert [entry.path for entry in narrowed.entries] == ["/u/alice/work/"]
        assert narrowed.entries[0].visibilities == read_set.entries[0].visibilities
        # and the request the caller actually sent is still refused when nothing in the read set covers it
        assert ExportHandlers._narrow(read_set, ("/u/carol/",)).entries == ()


async def test_export_is_tier_c_only_and_refuses_a_malformed_selector(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-a", content="I live in Berlin")
        assert (await forbidden(engine, caller("alice", "B"), "export", {"selectors": ["/u/alice/"]})).code == (
            "forbidden"
        )
        refused = await forbidden(engine, caller("alice"), "export", {"selectors": ["not-a-scope"]})
        assert (refused.code, refused.detail) == ("validation_error", "selector_invalid_scope")
        refused = await forbidden(engine, caller("alice"), "export", {"selectors": []})
        assert refused.detail == "selectors_invalid"


async def test_a_snapshot_made_before_a_deletion_is_not_served_after_it(database) -> None:  # noqa: F811
    """Channel 11, the guarantee this whole table exists for.

    A deletion advances the caller's deletion epoch, and a manifest captured under the old one stops being servable in
    the same instant — no trigger, no cache, nothing to invalidate. The test drives the real ``delete`` operation, so
    the epoch it compares against is the one the engine itself wrote.
    """
    from custom_memory_engine.engine.export import ExportError, serve_snapshot

    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-a", content="I live in Berlin")
        alice = caller("alice", "C")
        snapshot = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(snapshot, ExportManifest)
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            manifest, ids = await serve_snapshot(conn, user_id="alice", snapshot_id=snapshot.snapshot_id)
            assert ids == ("v-a",) and manifest.captured_epoch == 0
        # the deletion the engine performs; it appends a `deletion_request` and therefore advances the epoch
        await engine.call(alice, "delete", {"scope": "memory", "target_ref": "v-a"})
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            with pytest.raises(ExportError) as refused:
                await serve_snapshot(conn, user_id="alice", snapshot_id=snapshot.snapshot_id)
            assert refused.value.code == "snapshot_invalidated_by_a_deletion"


# ------------------------------------------------------------------------------- lifting a dissent (I16, V2-D105)


async def listed(engine: Engine, ctx: object, **arguments: object) -> ConstraintList:
    result = await engine.call(ctx, "constraints", arguments)  # type: ignore[arg-type]
    assert isinstance(result, ConstraintList)
    return result


async def _dissented(conn, engine, artifact_id: str = "v-dissented", evidence: str | None = "v-ground") -> str:
    """A grounded artifact, an inferred one dissented, and the id of the constraint the dissent recorded.

    ``evidence`` is written *before* the dissent on purpose: the belief-time watermark only moves forward
    (``enforce_recorded_watermark``), so a grounded row cannot be back-dated after a clock allocation. That is also the
    honest order — evidence for reopening a denial exists before the reopening is considered.
    """
    if evidence is not None:
        await grounded(conn, evidence, content="the user said so on Tuesday")
    await inferred(conn, artifact_id)
    await engine.call(caller("alice"), "dissent", {"artifact_id": artifact_id, "note": "wrong_again"})
    listing = await engine.call(caller("alice"), "constraints", {})
    assert isinstance(listing, ConstraintList)
    return listing.constraints[0].constraint_id


async def test_the_lift_loop_is_closed_and_discoverable(database) -> None:  # noqa: F811
    """``I16`` end to end: dissent blocks re-learning, ``constraints`` names the block, ``lift`` reopens it.

    This is the loop the earlier state could not close. Without ``constraints`` the constraint's id was unknowable
    (nothing returned it), and without ``lift`` the block was permanent — so ``I16``'s "cannot be re-inferred *without
    new grounded evidence*" described a door with no handle on either side.
    """
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine)
        listing = await engine.call(caller("alice"), "constraints", {})
        assert isinstance(listing, ConstraintList) and len(listing) == 1
        element = listing.constraints[0]
        assert element.constraint_id == constraint_id
        assert (element.scope_path, element.predicate_key) == ("/u/alice/", "statement")
        assert element.method_family == INHERITED
        # content-free: a scope path the caller owns, two registered names, two instants, and two ids
        assert element.lift_evidence_version_id is None and element.lifted_at is None
        assert "alice" not in element.predicate_key and "prefers" not in element.method_family

        # a grounded artifact is the ground: new evidence, not a second opinion
        receipt = await engine.call(
            caller("alice"),
            "lift",
            {"constraint_id": constraint_id, "evidence_artifact_id": "v-ground", "note": "user_confirmed"},
        )
        assert isinstance(receipt, Receipt)
        assert (receipt.id, receipt.effect) == (constraint_id, "lifted")
        assert await rows(
            conn,
            "SELECT lifted_by, lift_evidence_version_id, reason FROM negative_constraint WHERE id = %s",
            (constraint_id,),
        ) == [("alice", "v-ground", "wrong_again")]
        # and the element may be learned again
        again = await engine.call(
            caller("alice", "B"),
            "infer",
            {
                "scope": "/u/alice/",
                "method": {"id": INHERITED_ID, "version": "1", "params_hash": PERSONA_HASH},
                "sources": ["v-ground"],
                "statement": "alice prefers morning meetings",
                "ledger": {"support": 1, "counter": 0, "undetermined": 0, "opportunity": 1},
                "idempotency_key": "re-learn-2",
            },
        )
        assert getattr(again, "reason_code", None) is None, "a lifted element must be inferable again"
        # the lifted constraint is still listed when asked for, and carries its ground
        lifted = await engine.call(caller("alice"), "constraints", {"include_lifted": True})
        assert isinstance(lifted, ConstraintList) and len(lifted) == 1
        assert lifted.constraints[0].lift_evidence_version_id == "v-ground"
        # …and hidden by default, because the default question is "what still blocks me"
        assert len(await listed(engine, caller("alice"))) == 0


async def test_a_lift_needs_new_grounded_evidence_and_not_an_inference(database) -> None:  # noqa: F811
    """``I16``'s own words: *without new grounded evidence*. An inference cannot lift a constraint on inference, or
    the engine could reverse a user's denial by guessing the same thing twice."""
    async with world(database) as (conn, engine, _):
        # the inference that will be offered as "new evidence" is written first: a clock allocation cannot be
        # back-dated (enforce_recorded_watermark), and the point of the test is the lifecycle rule, not the watermark
        await inferred(conn, "v-guess", content="alice probably wants this")
        constraint_id = await _dissented(conn, engine)
        refused = await forbidden(
            engine, caller("alice"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-guess"}
        )
        assert (refused.code, refused.detail) == ("precondition_failed", "evidence_not_grounded")
        # and nothing moved
        assert await rows(conn, "SELECT lifted_at FROM negative_constraint WHERE id = %s", (constraint_id,)) == [
            (None,)
        ]


async def test_a_lift_cannot_name_evidence_it_cannot_read(database) -> None:  # noqa: F811
    """The ground must be the caller's: transparency of the *outcome* is not permission to cite someone else's row."""
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine, evidence="v-alices")
        # bob cannot cite alice's artifact, and cannot even tell it from one that does not exist: the constraint is
        # checked first, and bob does not own it, so both answers are the same word
        for evidence in ("v-alices", "v-absent"):
            refused = await forbidden(
                engine, caller("bob"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": evidence}
            )
            # `targets_readable` answers first for bob: alice's artifact and one that does not exist are the same word
            assert (refused.code, refused.detail) == ("forbidden", None)
        # alice can name her own constraint; naming evidence that does not exist is still just `forbidden`, so the
        # answer carries no information about which check refused it
        refused = await forbidden(
            engine, caller("alice"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-nothing-here"}
        )
        assert (refused.code, refused.detail) == ("forbidden", None)


async def test_another_principals_constraint_is_indistinguishable_from_a_missing_one(database) -> None:  # noqa: F811
    """No existence oracle: the refusal is the same word whether the constraint exists or not."""
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine, evidence="v-bobs-gone")
        await grounded(conn, "v-bobs", content="bob wrote this down", user="bob")
        refused = await forbidden(
            engine, caller("bob"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-bobs"}
        )
        assert (refused.code, refused.detail) == ("precondition_failed", "constraint_not_active")
        absent = await forbidden(
            engine, caller("bob"), "lift", {"constraint_id": "nc-nope", "evidence_artifact_id": "v-bobs"}
        )
        assert (absent.code, absent.detail) == (refused.code, refused.detail)
        # bob's own listing shows nothing of alice's
        assert len(await listed(engine, caller("bob"))) == 0


async def test_a_constraint_cannot_be_lifted_twice(database) -> None:  # noqa: F811
    """The lift is a record of when the reconsideration happened; a second one would rewrite it."""
    async with world(database) as (conn, engine, _):
        # both grounds exist before anything allocates a clock: belief time only moves forward
        await grounded(conn, "v-g2", content="evidence two")
        constraint_id = await _dissented(conn, engine, evidence="v-g1")
        await engine.call(caller("alice"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-g1"})
        refused = await forbidden(
            engine, caller("alice"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-g2"}
        )
        assert (refused.code, refused.detail) == ("precondition_failed", "constraint_not_active")
        assert await rows(
            conn, "SELECT lift_evidence_version_id FROM negative_constraint WHERE id = %s", (constraint_id,)
        ) == [("v-g1",)]


async def test_deleting_the_lift_evidence_withdraws_the_lift_and_the_element_blocks_again(database) -> None:  # noqa: F811
    """The schema's own guarantee, reached through the API: the lift loses its ground, so the dissent stands again.

    This is the property that makes a lift safe to offer — it is not a permission granted once, it is a claim that
    *this evidence* outweighs the denial, and the claim dies with its evidence.
    """
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine, evidence="v-ground-2")
        await engine.call(
            caller("alice"), "lift", {"constraint_id": constraint_id, "evidence_artifact_id": "v-ground-2"}
        )
        assert len(await listed(engine, caller("alice"))) == 0
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            await conn.execute("DELETE FROM memory_evidence WHERE version_id = 'v-ground-2'")
            await conn.execute("DELETE FROM memory_version WHERE id = 'v-ground-2'")
        listing = await engine.call(caller("alice"), "constraints", {})
        assert isinstance(listing, ConstraintList) and len(listing) == 1, "the dissent is active again"
        assert listing.constraints[0].lift_evidence_version_id is None
        # and `infer` refuses the element again
        again = await engine.call(
            caller("alice", "B"),
            "infer",
            {
                "scope": "/u/alice/",
                "method": {"id": INHERITED_ID, "version": "1", "params_hash": PERSONA_HASH},
                "sources": ["src-v-dissented"],
                "statement": "alice prefers morning meetings",
                "ledger": {"support": 1, "counter": 0, "undetermined": 0, "opportunity": 1},
                "idempotency_key": "re-learn-3",
            },
        )
        assert getattr(again, "reason_code", None) == "negative_constraint_active"


async def test_lift_and_constraints_are_tier_c_reads_and_writes(database) -> None:  # noqa: F811
    """``constraints`` is a read a Tier B caller may make (its own denial is its business); ``lift`` is not."""
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine, evidence="v-ground-3")
        listing = await engine.call(caller("alice", "B"), "constraints", {})
        assert isinstance(listing, ConstraintList) and len(listing) == 1
        refused = await forbidden(
            engine,
            caller("alice", "B"),
            "lift",
            {"constraint_id": constraint_id, "evidence_artifact_id": "v-ground-3"},
        )
        assert refused.code == "forbidden"


async def test_malformed_lift_and_constraint_arguments_are_static_refusals(database) -> None:  # noqa: F811
    async with world(database) as (conn, engine, _):
        constraint_id = await _dissented(conn, engine, evidence="v-ground-4")
        for arguments, code in (
            ({"evidence_artifact_id": "v-ground-4"}, "validation_error"),
            ({"constraint_id": "  ", "evidence_artifact_id": "v-ground-4"}, "validation_error"),
            ({"constraint_id": constraint_id}, "validation_error"),
            ({"constraint_id": constraint_id, "evidence_artifact_id": 7}, "validation_error"),
        ):
            refused = await forbidden(engine, caller("alice"), "lift", arguments)
            assert refused.code == code, (arguments, refused.code)
        refused = await forbidden(engine, caller("alice"), "constraints", {"include_lifted": "yes"})
        assert (refused.code, refused.detail) == ("validation_error", "include_lifted_must_be_a_boolean")


# --------------------------------------------------------------------------- deletion reaches entities (V2-D108)


async def _entity(engine, label: str, key: str) -> str:
    created = await engine.call(
        caller("alice"),
        "create_entity",
        {"scope": "/u/alice/", "entity_type": "person", "label": label, "idempotency_key": key},
    )
    entity_id = getattr(created, "entity_id", None) or getattr(created, "id", None) or created
    assert isinstance(entity_id, str), created
    return entity_id


async def _names(conn: AsyncConnection) -> list[str]:
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
        cursor = await conn.execute("SELECT canonical_name FROM entity WHERE user_id = 'alice' ORDER BY 1")
        return [str(row[0]) for row in await cursor.fetchall()]


async def test_delete_all_erases_the_names_of_the_people_the_memories_mentioned(database) -> None:  # noqa: F811
    """``I8`` for structure: an entity's name is what the user said. ``delete all`` used to leave every one.

    The regression this pins: the runtime role has no DELETE grant on ``entity``, and no deletion path called one, so
    after "delete everything" the user's memories were gone and the names of the people in them were not.
    """
    async with world(database) as (conn, engine, _):
        await _entity(engine, "Layla Hassan", "e-1")
        await _entity(engine, "Dr. Yusuf", "e-2")
        await grounded(conn, "v-about", content="I moved to Berlin")
        assert await _names(conn) == ["Dr. Yusuf", "Layla Hassan"]
        receipt = await engine.call(caller("alice"), "delete", {"scope": "all"})
        assert getattr(receipt, "deleted_entities", None) == 2
        assert await _names(conn) == []
        # the audit row counts them and names none of them
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            cursor = await conn.execute(
                "SELECT details FROM audit_event WHERE user_id = 'alice' AND event_type = 'deletion_completed'"
            )
            details = [row[0] for row in await cursor.fetchall()]
        assert details and details[-1]["deleted_entities"] == 2
        assert "layla" not in str(details).lower()


async def test_deleting_a_topic_by_entity_erases_that_entity_and_nothing_else(database) -> None:  # noqa: F811
    """Per-person erasure: the entity's id is the subject key of the claims about it, and a ``topic`` deletion of it
    removes the entity too — while another person and a plain ``memory`` deletion leave entities alone."""
    async with world(database) as (conn, engine, _):
        layla = await _entity(engine, "Layla Hassan", "e-3")
        await _entity(engine, "Sara Okafor", "e-4")
        await grounded(conn, "v-plain", content="I like tea")
        await engine.call(caller("alice"), "delete", {"scope": "memory", "target_ref": "v-plain"})
        assert await _names(conn) == ["Layla Hassan", "Sara Okafor"], "a memory deletion leaves declared entities"
        receipt = await engine.call(caller("alice"), "delete", {"scope": "topic", "target_ref": layla})
        assert getattr(receipt, "deleted_entities", None) == 1
        assert await _names(conn) == ["Sara Okafor"]


async def test_the_erasure_function_cannot_be_called_outside_a_deletion(database) -> None:  # noqa: F811
    """The SECURITY DEFINER path is gated exactly like ``delete_exclusive_turns``: setting the marker GUC is not
    enough, the transaction must itself have appended a ``deletion_request``."""
    from psycopg.errors import InsufficientPrivilege

    async with world(database) as (conn, engine, schema):
        await _entity(engine, "Layla Hassan", "e-5")
        role = engine.resolver.runtime.role  # type: ignore[attr-defined]
        with pytest.raises(InsufficientPrivilege, match="entity_erasure_forbidden"):
            async with conn.transaction():
                await conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
                await conn.execute("SELECT set_config('app.deletion_active', '1', true)")
                await conn.execute(
                    sql.SQL("SELECT {}.delete_orphaned_entities('alice', NULL)").format(sql.Identifier(schema))
                )
        assert await _names(conn) == ["Layla Hassan"]


# --------------------------------------------------- export identity is (user, epoch, digest) (PR #241 review)


async def test_an_export_after_an_unrelated_deletion_is_servable(database) -> None:  # noqa: F811
    """The epoch moved, the readable set did not. The second export must name a row that records the *new* epoch.

    The id was derived from the digest alone, so the second insert collided with the first row, `DO NOTHING` returned
    the old row's time, and the manifest claimed epoch 1 over a stored epoch 0: an id that could never be served.
    """
    from custom_memory_engine.engine.export import serve_snapshot

    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-kept", content="I live in Berlin")
        await grounded(conn, "v-gone", content="something else", user="alice")
        alice = caller("alice", "C")
        first = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(first, ExportManifest)
        # move `v-gone` out of reach of nothing: delete it, so the epoch advances and the digest changes too, then
        # export a selector that never covered it — the digest is stable across the deletion only for this selector
        await engine.call(alice, "delete", {"scope": "memory", "target_ref": "v-gone"})
        second = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(second, ExportManifest)
        assert second.captured_epoch == 1
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            manifest, ids = await serve_snapshot(conn, user_id="alice", snapshot_id=second.snapshot_id)
        assert manifest.captured_epoch == 1 and ids == ("v-kept",)


async def test_an_epoch_change_with_an_identical_read_set_mints_a_new_servable_snapshot(database) -> None:  # noqa: F811
    """The exact trigger the review named: same ids, new epoch, same digest — only (user, epoch) tells them apart."""
    from custom_memory_engine.engine.export import serve_snapshot

    async with world(database) as (conn, engine, _):
        await grounded(conn, "v-same", content="I live in Berlin")
        alice = caller("alice", "C")
        first = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        # a deletion that matches nothing still appends a request and advances the epoch
        await engine.call(alice, "delete", {"scope": "memory", "target_ref": "no-such-key"})
        second = await engine.call(alice, "export", {"selectors": ["/u/alice/"]})
        assert isinstance(first, ExportManifest) and isinstance(second, ExportManifest)
        assert first.manifest_digest == second.manifest_digest
        assert first.snapshot_id != second.snapshot_id
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.user_id', 'alice', true)")
            manifest, ids = await serve_snapshot(conn, user_id="alice", snapshot_id=second.snapshot_id)
        assert (manifest.captured_epoch, ids) == (1, ("v-same",))


async def test_two_users_exporting_the_same_empty_set_each_get_their_own_snapshot(database) -> None:  # noqa: F811
    """Two empty read sets hash identically; a global primary key must not let one user's row block the other's."""
    async with world(database) as (_conn, engine, _):
        alice = await engine.call(caller("alice", "C"), "export", {"selectors": ["/u/alice/"]})
        bob = await engine.call(caller("bob", "C"), "export", {"selectors": ["/u/bob/"]})
        assert isinstance(alice, ExportManifest) and isinstance(bob, ExportManifest)
        assert alice.manifest_digest == bob.manifest_digest
        assert alice.snapshot_id != bob.snapshot_id
