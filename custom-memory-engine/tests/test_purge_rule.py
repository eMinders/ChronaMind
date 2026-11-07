"""Rule PW-1 as a pure function (E-026): which working episodes a purge deletes and which it keeps as evidence."""

from custom_memory_engine.engine.purge import EVIDENCE_REFERENCES, PurgePlan, plan_purge


def test_an_episode_nothing_cites_is_purged() -> None:
    assert plan_purge(["e1", "e2"], []) == PurgePlan(purged=("e1", "e2"), retained_as_evidence=())


def test_an_episode_a_surviving_row_cites_is_retained_as_evidence() -> None:
    # e1 is the source of an inference: purging it would delete knowledge (closure) or orphan the inference (I1', I8)
    plan = plan_purge(["e1", "e2"], [("e1", "abstraction")])
    assert plan == PurgePlan(purged=("e2",), retained_as_evidence=("e1",))


def test_a_citation_counts_only_while_its_citing_row_survives() -> None:
    # e1 is cited only by e2, which is itself purged: both go (the plan is a fixpoint, not a single pass)
    assert plan_purge(["e1", "e2"], [("e1", "e2")]).purged == ("e1", "e2")
    # ...but once e2 is itself cited by a survivor, e2 stays, and so does what e2 cites
    plan = plan_purge(["e1", "e2", "e3"], [("e1", "e2"), ("e2", "claim"), ("e3", "e1")])
    assert plan == PurgePlan(purged=(), retained_as_evidence=("e1", "e2", "e3"))


def test_a_self_citation_and_a_duplicate_candidate_change_nothing() -> None:
    assert plan_purge(["e1", "e1"], [("e1", "e1")]) == PurgePlan(purged=("e1",), retained_as_evidence=())


def test_an_empty_scope_plans_nothing() -> None:
    assert plan_purge([], [("e1", "abstraction")]) == PurgePlan(purged=(), retained_as_evidence=())


def test_citations_of_rows_that_are_not_candidates_are_ignored() -> None:
    assert plan_purge(["e1"], [("durable-episode", "abstraction")]).purged == ("e1",)


def test_every_column_that_cites_a_version_as_evidence_is_consulted() -> None:
    """Derivation sources, a lift's evidence and a gate comparison: the three references without a cascade."""
    assert {(table, cited) for table, cited, _ in EVIDENCE_REFERENCES} == {
        ("memory_derivation", "source_version_id"),
        ("negative_constraint", "lift_evidence_version_id"),
        ("memory_conflict", "existing_version_id"),
    }


def test_a_retention_propagates_down_a_long_chain_of_candidates() -> None:
    """The worklist reaches every candidate a retained one cites, however deep, and nothing else."""
    chain = [f"e{i}" for i in range(500)]
    citations = [(chain[i + 1], chain[i]) for i in range(499)]  # e0 cites e1, e1 cites e2, ...
    assert plan_purge(chain, citations).retained_as_evidence == ()
    plan = plan_purge([*chain, "loose"], [*citations, ("e0", "abstraction")])
    assert plan == PurgePlan(purged=("loose",), retained_as_evidence=tuple(chain))
