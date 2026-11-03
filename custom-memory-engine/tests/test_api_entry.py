"""The public API entry point (E-039): authorise first, enforce every condition, speak only the static contract."""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from itertools import product

import pytest
from psycopg.errors import DeadlockDetected, InsufficientPrivilege, SerializationFailure

from custom_memory_engine.api import entry
from custom_memory_engine.api.contract import (
    BY_NAME,
    CONDITIONS,
    OPERATIONS,
    UNIVERSAL_ERRORS,
    ContractError,
    authorize,
)
from custom_memory_engine.api.entry import (
    INTERNAL_ERROR,
    Call,
    CallerContext,
    Credential,
    Engine,
    EntryError,
    TargetInfo,
)
from custom_memory_engine.model.axes import AxisError
from custom_memory_engine.scope.access import ReadSet, Selector, resolve_read_set
from custom_memory_engine.scope.paths import ScopeError

LEAK_MARKER = "ID-7781-LEAK"


class Resolver:
    """In-memory targets. `calls` records every lookup so tests can prove none happened."""

    def __init__(self, known: Mapping[str, TargetInfo]) -> None:
        self.known = dict(known)
        self.calls: list[Sequence[str]] = []

    async def resolve(self, ctx: CallerContext, ids: Sequence[str]) -> Mapping[str, TargetInfo]:
        self.calls.append(tuple(ids))
        found = {i: self.known[i] for i in ids if i in self.known}
        # a row whose tier is unspecified was produced by the caller in the role it is calling as
        return {
            i: replace(info, lineage_tier=ctx.tier)
            if info.lineage_tier is None and info.lineage_owner == ctx.principal
            else info
            for i, info in found.items()
        }


class Principals:
    """Known user ids, and whether the entry point consulted the registry at all."""

    def __init__(self, known: Sequence[str] = ("alice", "bob")) -> None:
        self.known = set(known)
        self.calls: list[str] = []

    async def exists(self, principal: str) -> bool:
        self.calls.append(principal)
        return principal in self.known


class Methods:
    def __init__(self, registered: bool = True) -> None:
        self.registered = registered
        self.calls = 0

    async def is_registered(self, ctx: CallerContext, method: Mapping[str, object]) -> bool:
        del ctx, method
        self.calls += 1
        return self.registered


MINE = TargetInfo("/u/alice/", "private", "asserted", "alice")
KNOWN = {
    **{i: MINE for i in ("a1", "a2", "e1", "e2", "r1", "p1", "as1", "g1")},
    "inferred-1": TargetInfo("/u/alice/", "private", "inferred", "alice"),
    "bobs": TargetInfo("/u/bob/", "private", "asserted", "bob"),
    "carols": TargetInfo("/u/alice/", "private", "asserted", "carol"),
    "bobs-grant": TargetInfo("/u/bob/", "private", None, "bob"),
}
READ_SET = resolve_read_set("alice", [Selector.of("/u/alice/")], [])


def ctx(
    tier: str = "C", admin: bool = False, write_scope: str = "/u/alice/", principal: str = "alice"
) -> CallerContext:
    return CallerContext.from_credential(
        Credential(principal, tier, admin),  # type: ignore[arg-type]
        write_scope=write_scope,
        read_set=READ_SET,
    )


VALUES: dict[str, object] = {
    "scope": "/u/alice/",
    "parent": "/u/alice/",
    "kind": "claim",
    "candidate": {"text": "x"},
    "evidence": [{"type": "turn"}],
    "counter_evidence": [{"type": "turn"}],
    "idempotency_key": "k1",
    "source": {"type": "turn"},
    "content": "x",
    "event_time": None,
    "event_range": None,
    "request": {},
    "result_id": "r1",
    "instruction": "x",
    "artifact_id": "a1",
    "depth": 1,
    "reason": "x",
    "method": {"id": "m", "version": "1", "params_hash": "h"},
    "sources": ["a1", "a2"],
    "statement": "x",
    "predicate": None,
    "ledger": {"s": 1, "k": 0, "u": 0},
    "verification": {},
    "from": "a1",
    "to": "a2",
    "type": "related_to",
    "weight": 1,
    "association_id": "as1",
    "entity_type": "person",
    "label": "x",
    "entity_a": "e1",
    "entity_b": "e2",
    "subject_entity": "e1",
    "relation": "x",
    "procedure_id": "p1",
    "outcome": "success",
    "cost": 0,
    "episode_ref": None,
    "decision": "confirm",
    "note": "x",
    "as_of": None,
    "index": "fts",
    "name": "m",
    "version": "1",
    "params_schema_hash": "h",
    "description": "x",
    "target_ref": "k",
    "deletion_epoch": None,
    "selectors": [],
    "window": None,
    "segment": "p1",
    "principal": "bob",
    "grant_id": "g1",
}


def valid_arguments(op_name: str) -> dict[str, object]:
    return {name: VALUES[name] for name in BY_NAME[op_name].inputs}


def engine(
    *,
    handler_calls: list[Call] | None = None,
    methods: Methods | None = None,
    principals: Principals | None = None,
    known: Mapping[str, TargetInfo] = KNOWN,
) -> Engine:
    seen = handler_calls if handler_calls is not None else []

    async def handler(call: Call) -> object:
        seen.append(call)
        return {"ok": call.operation.name}

    return Engine(
        resolver=Resolver(known),
        methods=methods or Methods(),
        principals=principals if principals is not None else Principals(),
        handlers={op.name: handler for op in OPERATIONS},
    )


def permitted(op_name: str, tier: str, admin: bool) -> bool:
    try:
        authorize(op_name, tier, admin=admin)
    except ContractError:
        return False
    return True


# --- authorisation comes first (acceptance 1 and 2) ---------------------------------------------------


@pytest.mark.parametrize(
    ("op", "tier", "admin"), list(product([o.name for o in OPERATIONS], ("B", "C"), (False, True)))
)
async def test_every_operation_and_tier_is_either_callable_or_forbidden_before_anything_runs(
    op: str, tier: str, admin: bool
) -> None:
    seen: list[Call] = []
    sut = engine(handler_calls=seen)
    resolver = sut.resolver
    methods = sut.methods
    caller = ctx(tier, admin)
    if permitted(op, tier, admin):
        assert await sut.call(caller, op, valid_arguments(op)) == {"ok": op}
        return
    with pytest.raises(EntryError) as raised:
        await sut.call(caller, op, valid_arguments(op))
    assert (raised.value.code, raised.value.detail) == ("forbidden", None)
    assert seen == [], "the handler must not run"
    assert resolver.calls == [], "no lookup (no statement) may happen"  # type: ignore[attr-defined]
    assert methods.calls == 0  # type: ignore[attr-defined]


@pytest.mark.parametrize("name", ["retract", "delete_all", "", "ASSERT", None, 7, ("assert",)])
async def test_no_operation_outside_the_contract_is_callable(name: object) -> None:
    sut = engine()
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx(), name, {})  # type: ignore[arg-type]
    assert raised.value.code == "forbidden"


async def test_the_same_principal_may_not_touch_in_one_tier_what_it_produced_in_the_other() -> None:
    """Own lineage is (principal, tier): a Tier B call cannot retract what the same user asserted as Tier C."""
    known = {**KNOWN, "c-made": TargetInfo("/u/alice/", "private", "asserted", "alice", "C")}
    sut = engine(known=known)
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx("B"), "retract_own", {"artifact_id": "c-made", "reason": "x"})
    assert raised.value.code == "forbidden"
    assert await sut.call(ctx("C"), "retract_own", {"artifact_id": "c-made", "reason": "x"}) == {"ok": "retract_own"}


async def test_the_tier_b_rules_hold_at_runtime() -> None:
    sut = engine()
    for denied in ("delete", "adjudicate", "dissent", "export", "grant_access", "purge_working"):
        with pytest.raises(EntryError) as raised:
            await sut.call(ctx("B", admin=True), denied, valid_arguments(denied))
        assert raised.value.code == "forbidden", denied


async def test_admin_is_a_tier_c_role_and_is_ignored_for_tier_b() -> None:
    assert ctx("B", admin=True).admin is False
    assert ctx("C", admin=True).admin is True
    sut = engine()
    with pytest.raises(EntryError):
        await sut.call(ctx("C", admin=False), "register_method", valid_arguments("register_method"))
    assert await sut.call(ctx("C", admin=True), "register_method", valid_arguments("register_method"))


# --- the caller comes from the credential only --------------------------------------------------------


@pytest.mark.parametrize("name", ["tier", "admin", "user_id", "caller", "ctx", "credential", "read_set"])
async def test_a_request_cannot_set_who_the_caller_is(name: str) -> None:
    sut = engine()
    arguments = {**valid_arguments("assert"), name: "C"}
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx("B"), "assert", arguments)
    assert (raised.value.code, raised.value.detail) == ("validation_error", "reserved_argument")


async def test_unknown_arguments_are_rejected_even_when_everything_else_is_valid() -> None:
    with pytest.raises(EntryError) as raised:
        await engine().call(ctx(), "get", {**valid_arguments("get"), "include_everything": True})
    assert (raised.value.code, raised.value.detail) == ("validation_error", "unknown_argument")


async def test_a_credential_is_validated() -> None:
    for bad in (("", "B", False), ("alice", "A", False), ("alice", "B", "yes")):
        with pytest.raises(EntryError):
            Credential(*bad)  # type: ignore[arg-type]
    with pytest.raises(EntryError):
        CallerContext.from_credential(Credential("alice", "B"), write_scope="/u/alice", read_set=ReadSet((), ()))


# --- every condition code, with a mutation proving the test discriminates (acceptance 3) --------------

#: condition -> (operation, argument overrides, caller overrides, expected code, expected detail)
VIOLATIONS: dict[str, tuple[str, dict[str, object], dict[str, object], str, str | None]] = {
    "evidence_required": ("assert", {"evidence": []}, {}, "validation_error", "evidence_required"),
    "scope_writable": ("assert", {"scope": "/u/bob/"}, {}, "forbidden", None),
    "namespace_owner_only": ("create_scope", {"parent": "/u/bob/"}, {}, "forbidden", None),
    "registered_method": ("infer", {}, {"unregistered": True}, "rejected", "method_not_registered"),
    "targets_readable": ("get", {"artifact_id": "bobs"}, {}, "forbidden", None),
    "own_lineage_only": ("retract_own", {"artifact_id": "carols"}, {}, "forbidden", None),
    "grounded_endpoint_protected": (
        "link",
        {"type": "not-a-type"},
        {},
        "validation_error",
        "association_type_invalid",
    ),
}


def violating_engine(
    case: tuple[str, dict[str, object], dict[str, object], str, str | None],
) -> tuple[Engine, str, dict]:
    op, overrides, extras, _, _ = case
    sut = engine(methods=Methods(registered=not extras.get("unregistered")), known=KNOWN)
    return sut, op, {**valid_arguments(op), **overrides}


def test_every_condition_code_in_the_contract_has_a_check_and_a_violation_case() -> None:
    assert set(entry.CONDITION_CHECKS) == set(CONDITIONS) == set(VIOLATIONS) == set(entry.CONDITION_ORDER)
    declared = {c for op in OPERATIONS for c in op.conditions}
    assert declared == set(CONDITIONS), "the violation cases must cover conditions that operations actually declare"


@pytest.mark.parametrize("condition", sorted(VIOLATIONS))
async def test_each_condition_rejects_its_violation_with_the_documented_static_error(condition: str) -> None:
    case = VIOLATIONS[condition]
    sut, op, arguments = violating_engine(case)
    assert condition in BY_NAME[op].conditions
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx("B" if "B" in BY_NAME[op].tiers else "C"), op, arguments)
    assert (raised.value.code, raised.value.detail) == case[3:]


@pytest.mark.parametrize("condition", sorted(VIOLATIONS))
async def test_the_violation_case_is_only_rejected_because_of_that_check(
    condition: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Mutation: with the check removed the very same call is accepted, so the test above depends on it."""

    async def removed(*_: object) -> None:
        return None

    monkeypatch.setitem(entry.CONDITION_CHECKS, condition, removed)
    case = VIOLATIONS[condition]
    sut, op, arguments = violating_engine(case)
    assert await sut.call(ctx("B" if "B" in BY_NAME[op].tiers else "C"), op, arguments) == {"ok": op}


async def test_a_delete_acts_in_the_callers_write_scope_so_a_foreign_write_scope_is_forbidden() -> None:
    caller = ctx("C", write_scope="/u/bob/")
    with pytest.raises(EntryError) as raised:
        await engine().call(caller, "delete", valid_arguments("delete"))
    assert raised.value.code == "forbidden"


async def test_revoking_a_grant_in_another_namespace_is_forbidden() -> None:
    with pytest.raises(EntryError) as raised:
        await engine().call(ctx(), "revoke_access", {"grant_id": "bobs-grant"})
    assert raised.value.code == "forbidden"
    assert await engine().call(ctx(), "revoke_access", {"grant_id": "g1"}) == {"ok": "revoke_access"}


# --- forbidden is indistinguishable for absent and unreadable (acceptance 4; T-11, T-18) ---------------


async def test_an_absent_target_and_an_unreadable_target_give_identical_errors() -> None:
    sut = engine()
    errors = []
    for target in ("does-not-exist", "bobs"):
        with pytest.raises(EntryError) as raised:
            await sut.call(ctx(), "get", {"artifact_id": target})
        errors.append((raised.value.code, raised.value.detail, str(raised.value), repr(raised.value)))
    assert errors[0] == errors[1]
    assert LEAK_MARKER not in "".join(str(part) for part in errors[0])


async def test_an_opaque_provenance_handle_is_answered_like_an_unknown_id_without_a_lookup() -> None:
    sut = engine()
    handle = "opq_" + "0" * 32
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx(), "get", {"artifact_id": handle})
    assert raised.value.code == "forbidden"
    assert sut.resolver.calls == []  # type: ignore[attr-defined]


async def test_a_write_into_an_unwritable_scope_is_indistinguishable_from_a_missing_one() -> None:
    sut = engine()
    outcomes = []
    for scope in ("/u/bob/", "/u/nobody-at-all/p/x/"):
        with pytest.raises(EntryError) as raised:
            await sut.call(ctx(), "assert", {**valid_arguments("assert"), "scope": scope})
        outcomes.append((raised.value.code, raised.value.detail, str(raised.value)))
    assert outcomes[0] == outcomes[1]


# --- the Tier B contradiction rule (V2-D48 C-P4) ------------------------------------------------------


async def test_a_tier_b_contradicts_edge_is_recorded_without_a_channel_effect() -> None:
    seen: list[Call] = []
    sut = engine(handler_calls=seen)
    await sut.call(ctx("B"), "link", {**valid_arguments("link"), "type": "contradicts"})
    assert (seen[0].contradiction_origin, seen[0].opens_contradiction) == ("tier_b", False)


async def test_a_tier_c_contradicts_edge_between_grounded_artifacts_opens_a_contradiction() -> None:
    seen: list[Call] = []
    await engine(handler_calls=seen).call(ctx("C"), "link", {**valid_arguments("link"), "type": "contradicts"})
    assert (seen[0].contradiction_origin, seen[0].opens_contradiction) == ("tier_c", True)


async def test_a_contradiction_against_an_inference_never_opens() -> None:
    seen: list[Call] = []
    arguments = {**valid_arguments("link"), "type": "contradicts", "to": "inferred-1"}
    await engine(handler_calls=seen).call(ctx("C"), "link", arguments)
    assert seen[0].opens_contradiction is False


# --- wiring and dispatch -----------------------------------------------------------------------------


async def test_an_unwired_operation_is_forbidden_and_listed() -> None:
    sut = Engine(resolver=Resolver(KNOWN), methods=Methods())
    assert set(sut.unwired_operations()) == {op.name for op in OPERATIONS}
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx(), "stats", valid_arguments("stats"))
    assert (raised.value.code, raised.value.detail) == ("forbidden", "operation_not_wired")

    async def stats(_: Call) -> object:
        return 1

    sut.register("stats", stats)
    assert "stats" not in sut.unwired_operations()
    assert await sut.call(ctx(), "stats", valid_arguments("stats")) == 1


def test_only_operations_of_the_contract_can_be_wired() -> None:
    sut = Engine(resolver=Resolver(KNOWN), methods=Methods())

    async def handler(_: Call) -> object:
        return None

    with pytest.raises(EntryError) as raised:
        sut.register("retract", handler)
    assert raised.value.code == "forbidden"


async def test_the_handler_receives_the_authorised_call_and_the_resolved_targets() -> None:
    seen: list[Call] = []
    await engine(handler_calls=seen).call(ctx("B"), "verify", valid_arguments("verify"))
    call = seen[0]
    assert call.ctx.tier == "B" and call.operation.name == "verify"
    assert set(call.targets) == {"a1"}


# --- the static error contract (acceptance 4) ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raised", "code", "detail"),
    [
        (InsufficientPrivilege(f"row {LEAK_MARKER} denied"), "forbidden", None),
        (ScopeError("forbidden"), "forbidden", None),
        (ScopeError("scope_must_end_with_separator"), "validation_error", "scope_must_end_with_separator"),
        (AxisError("lifecycle_transition_not_permitted"), "validation_error", "lifecycle_transition_not_permitted"),
        (ValueError(LEAK_MARKER), "internal_error", None),
        (RuntimeError(LEAK_MARKER), "internal_error", None),
        (KeyError(LEAK_MARKER), "internal_error", None),
        (EntryError("precondition_failed", "already_retired"), "precondition_failed", "already_retired"),
        (EntryError("resource_limit"), "validation_error", None),
    ],
)
async def test_every_failure_is_mapped_to_a_static_code_that_never_carries_the_message(
    raised: Exception, code: str, detail: str | None
) -> None:
    async def boom(_: Call) -> object:
        raise raised

    # `retract_own` lists precondition_failed but not resource_limit, so both the accepted and the degraded paths run.
    sut = Engine(resolver=Resolver(KNOWN), methods=Methods(), handlers={"retract_own": boom})
    with pytest.raises(EntryError) as caught:
        await sut.call(ctx(), "retract_own", valid_arguments("retract_own"))
    assert (caught.value.code, caught.value.detail) == (code, detail)
    assert LEAK_MARKER not in str(caught.value) + repr(caught.value) + str(caught.value.detail)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


async def test_serialisation_failures_become_retryable_conflict_only_where_the_contract_allows_it() -> None:
    async def failing(_: Call) -> object:
        raise SerializationFailure("x")

    async def deadlock(_: Call) -> object:
        raise DeadlockDetected("x")

    sut = Engine(
        resolver=Resolver(KNOWN),
        methods=Methods(),
        handlers={"delete": failing, "get": deadlock},
    )
    with pytest.raises(EntryError) as allowed:
        await sut.call(ctx(), "delete", valid_arguments("delete"))
    assert allowed.value.code == "retryable_conflict"
    with pytest.raises(EntryError) as not_allowed:
        await sut.call(ctx(), "get", valid_arguments("get"))
    assert not_allowed.value.code == INTERNAL_ERROR  # `get` does not list retryable_conflict


async def test_no_error_code_outside_the_operations_contract_is_ever_raised() -> None:
    async def handler(_: Call) -> object:
        raise EntryError("retryable_conflict")

    sut = Engine(resolver=Resolver(KNOWN), methods=Methods(), handlers={op.name: handler for op in OPERATIONS})
    for op in OPERATIONS:
        tier = "B" if "B" in op.tiers else "C"
        with pytest.raises(EntryError) as raised:
            await sut.call(ctx(tier, admin=op.admin_only), op.name, valid_arguments(op.name))
        assert raised.value.code in op.errors | {INTERNAL_ERROR}, op.name
        assert UNIVERSAL_ERRORS <= op.errors


# --- T-30: naming a grantee never reveals whether that user exists ------------------------------------


async def test_an_unknown_grant_principal_is_forbidden_exactly_like_an_unwritable_namespace() -> None:
    """T-30: a namespace owner must not be able to test whether a user id exists by granting to it."""
    sut = engine(principals=Principals(known=("bob",)))
    outcomes = []
    for principal in ("someone-who-does-not-exist", "bob"):
        with pytest.raises(EntryError) as raised:
            await sut.call(
                ctx(), "grant_access", {**valid_arguments("grant_access"), "scope": "/u/bob/", "principal": principal}
            )
        outcomes.append((raised.value.code, raised.value.detail, str(raised.value), repr(raised.value)))
    assert outcomes[0] == outcomes[1]
    assert outcomes[0][0] == "forbidden"
    assert LEAK_MARKER not in "".join(str(part) for part in outcomes[0])


async def test_a_known_principal_in_the_callers_own_namespace_reaches_the_handler() -> None:
    seen: list[Call] = []
    sut = engine(handler_calls=seen, principals=Principals(known=("bob",)))
    assert await sut.call(ctx(), "grant_access", valid_arguments("grant_access")) == {"ok": "grant_access"}
    assert [call.operation.name for call in seen] == ["grant_access"]


async def test_with_no_principal_registry_wired_nothing_can_be_granted() -> None:
    """Fail closed: an unconfigured collaborator may not silently turn the existence check off."""
    sut = Engine(resolver=Resolver(KNOWN), methods=Methods(), handlers={"grant_access": _echo})
    with pytest.raises(EntryError) as raised:
        await sut.call(ctx(), "grant_access", valid_arguments("grant_access"))
    assert raised.value.code == "forbidden"


async def test_the_principal_check_runs_before_the_handler_and_only_when_a_grantee_is_named() -> None:
    seen: list[Call] = []
    principals = Principals(known=("bob",))
    sut = engine(handler_calls=seen, principals=principals)
    await sut.call(ctx(), "get", {"artifact_id": "a1"})
    assert principals.calls == [], "an operation with no grantee must not consult the registry"
    await sut.call(ctx(), "grant_access", valid_arguments("grant_access"))
    assert principals.calls == ["bob"]


async def test_the_owner_granting_a_known_principal_succeeds_and_an_unknown_one_is_forbidden() -> None:
    """The owner of the namespace is the one caller who can act, so this is where existence must stay invisible."""
    sut = engine(principals=Principals(known=("carol",)))
    owner = ctx(principal="bob", write_scope="/u/bob/")
    arguments = {**valid_arguments("grant_access"), "scope": "/u/bob/"}
    assert await sut.call(owner, "grant_access", {**arguments, "principal": "carol"}) == {"ok": "grant_access"}
    outcomes = []
    for principal in ("nobody-at-all", "bob"):
        with pytest.raises(EntryError) as raised:
            await sut.call(owner, "grant_access", {**arguments, "principal": principal})
        outcomes.append((raised.value.code, raised.value.detail, str(raised.value), repr(raised.value)))
    assert outcomes[0] == outcomes[1] == ("forbidden", None, "forbidden", "EntryError('forbidden')")


# --- T-27: the caller never chooses a primary key -----------------------------------------------


def test_no_creation_operation_accepts_a_caller_chosen_identifier() -> None:
    """T-27 (S-04): creating takes an idempotency key, never an id, so no request can probe the key space.

    A caller-chosen global primary key would make a clash observable, and v1's predictable ids would let one tenant
    pre-claim another's. If a future operation adds an id input to a creating operation, this fails.
    """
    identifier_names = {
        "id",
        "version_id",
        "artifact_id",
        "memory_id",
        "new_id",
        "entity_id",
        "association_id",
        "grant_id",
        "job_id",
        "method_id",
        "turn_id",
        "session_id",
        "result_id",
    }
    creating = ("assert", "infer", "create_entity", "register_method", "create_scope")
    offenders = [
        (op.name, name) for op in OPERATIONS if op.name in creating for name in set(op.inputs) & identifier_names
    ]
    assert offenders == []
    for name in creating:
        assert "idempotency_key" in BY_NAME[name].inputs


async def _echo(call: Call) -> object:
    return {"ok": call.operation.name}
