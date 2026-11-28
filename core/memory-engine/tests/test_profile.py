"""Profile schema tests (E-019): strict validation, the conversational profile, and no branching on identity."""

import copy
import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from custom_memory_engine.engine.transactions import CandidateVersionInput
from custom_memory_engine.model.axes import FACETS, V1_LAYERS, map_v1_version
from custom_memory_engine.profile import (
    PLACEHOLDER_V2_D14,
    Profile,
    ProfileError,
    conversational,
    dump_profile,
    load_profile,
    parse_profile,
)
from custom_memory_engine.scope.paths import user_scope
from tests.profile_branching import find_profile_branching, scan_tree

SRC = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
PACKAGED = SRC / "profiles" / "conversational.json"


def base() -> dict[str, Any]:
    """A valid minimal document to mutate."""
    return copy.deepcopy(json.loads(PACKAGED.read_text(encoding="utf-8")))


def fields_of(error: ValidationError) -> set[str]:
    return {".".join(str(part) for part in e["loc"]) for e in error.errors()}


# --- strict validation (E-019 requirement 1) -------------------------------------------------------


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda d: d.update(surprise=1), "surprise"),
        (lambda d: d["render"].update(typo=True), "render.typo"),
        (lambda d: d["retention"].update(extra=1), "retention.extra"),
        (lambda d: d.update(kinds=["episode", "feeling"]), "kinds.1"),
        (lambda d: d.update(kinds=[]), "kinds"),
        (lambda d: d.update(kinds=["episode", "episode"]), "kinds"),
        (lambda d: d.update(name="Not A Slug"), "name"),
        (lambda d: d.update(name=7), "name"),
        (lambda d: d.update(scope_root="u/{user}/"), "scope_root"),
        (lambda d: d.update(scope_root="/u/{user}"), "scope_root"),
        (lambda d: d.update(scope_root="/u/{nobody}/"), "scope_root"),
        (lambda d: d.update(scope_root="/u/{user}//p/"), "scope_root"),
        (lambda d: d.update(scope_root="/u/{user}/P_1/"), "scope_root"),
        (lambda d: d.update(facets=["vibe"]), "facets"),
        (lambda d: d.update(facets=["goal", "goal"]), "facets"),
        (lambda d: d["extractors"][0].update(source_types=["carrier_pigeon"]), "extractors.0.source_types"),
        (lambda d: d["extractors"][0].update(source_types=[]), "extractors.0.source_types"),
        (lambda d: d["extractors"][0].update(name="Bad Name"), "extractors.0.name"),
        (lambda d: d.update(operators=["consolidation", "consolidation"]), "operators"),
        (lambda d: d["retention"]["defaults"].update(claim="working"), "retention"),
        (lambda d: d["retention"]["defaults"].pop("claim"), "kinds"),
        (lambda d: d["retention"].update(promote_to_durable=True), "retention"),
        (lambda d: d["retention"].update(status="whenever"), "retention.status"),
        (lambda d: d["render"]["channel_budget_share"].update(hypotheses=0.5), "render.channel_budget_share"),
        (lambda d: d["render"].update(channel_budget_share={"facts": 0.0}), "render.channel_budget_share"),
        (
            lambda d: d["render"].update(channel_budget_share={"facts": 1.0, "gossip": 0.1}),
            "render.channel_budget_share",
        ),
        (lambda d: d["association"].update(degree_cap=0), "association.degree_cap"),
        (lambda d: d["association"].update(degree_cap=10**6), "association.degree_cap"),
        (lambda d: d["association"].update(extensions=[{"name": "contradicts"}]), "association.extensions.0.name"),
        (
            lambda d: d["association"].update(extensions=[{"name": "blocks"}, {"name": "blocks"}]),
            "association",
        ),
        (lambda d: d["abstraction_expiry"].update(days_by_facet={"mood": 30}), "abstraction_expiry.days_by_facet"),
        (lambda d: d["abstraction_expiry"].update(days_by_facet={"goal": 0}), "abstraction_expiry.days_by_facet.goal"),
        (lambda d: d["abstraction_expiry"].update(status="decided-ish"), "abstraction_expiry.status"),
        (lambda d: d.update(schema_version=2), "schema_version"),
    ],
)
def test_invalid_documents_are_rejected_with_a_field_specific_error(mutate: Any, field: str) -> None:
    document = base()
    mutate(document)
    with pytest.raises(ValidationError) as raised:
        parse_profile(document)
    assert any(f == field or f.startswith(field + ".") or field.startswith(f) for f in fields_of(raised.value)), (
        field,
        fields_of(raised.value),
    )


def test_types_are_not_coerced() -> None:
    document = base()
    document["association"]["degree_cap"] = "32"
    with pytest.raises(ValidationError):
        parse_profile(document)
    document = base()
    document["render"]["show_provenance"] = "yes"
    with pytest.raises(ValidationError):
        parse_profile(document)


def test_expiry_for_a_facet_the_profile_did_not_select_is_rejected() -> None:
    document = base()
    document["abstraction_expiry"]["days_by_facet"] = {"preference": 180}
    with pytest.raises(ValidationError) as raised:
        parse_profile(document)
    assert "expiry_for_unselected_facet" in str(raised.value)


def test_non_serialisable_input_is_a_static_error() -> None:
    with pytest.raises(ProfileError) as raised:
        parse_profile({"name": object()})
    assert raised.value.code == "profile_not_serialisable"


# --- facets (requirement 2) ------------------------------------------------------------------------


def test_the_canonical_facet_vocabulary_has_exactly_six_values() -> None:
    assert FACETS == ("preference", "goal", "constraint", "context", "decision_pattern", "meta_preference")
    assert "inferred_preference" not in FACETS


def test_a_profile_may_select_a_subset_of_facets_but_not_add_one() -> None:
    document = base()
    document["facets"] = ["preference", "constraint"]
    assert parse_profile(document).facets == ("preference", "constraint")
    document["facets"] = ["preference", "inferred_preference"]
    with pytest.raises(ValidationError):
        parse_profile(document)


# --- association types (requirement 3) -------------------------------------------------------------


def test_a_profile_may_add_association_types_that_do_not_collide() -> None:
    document = base()
    document["association"]["extensions"] = [
        {"name": "blocks", "directed": True},
        {"name": "pairs_with", "directed": False},
    ]
    profile = parse_profile(document)
    assert [e.name for e in profile.association.extensions] == ["blocks", "pairs_with"]


def test_the_association_extension_limit_is_enforced() -> None:
    document = base()
    document["association"]["extensions"] = [{"name": f"kind_{i}"} for i in range(9)]
    with pytest.raises(ValidationError):
        parse_profile(document)


# --- the conversational profile (requirement 5) ----------------------------------------------------


def test_the_conversational_profile_loads_from_its_packaged_document() -> None:
    profile = conversational()
    assert isinstance(profile, Profile)
    assert profile.name == "conversational"
    assert load_profile(PACKAGED) == profile


def test_the_packaged_document_is_in_canonical_form() -> None:
    assert PACKAGED.read_text(encoding="utf-8") == dump_profile(conversational())


def test_the_conversational_scope_root_is_the_v1_user_root() -> None:
    root = conversational().scope_root
    assert root == "/u/{user}/"
    assert root.replace("{user}", "u1") == user_scope("u1").text


def test_the_conversational_retention_is_durable_for_every_kind() -> None:
    retention = conversational().retention
    assert set(retention.defaults.values()) == {"durable"}
    assert retention.status == "v1_semantics"
    assert not retention.promote_to_durable


def test_the_conversational_kinds_are_exactly_the_kinds_v1_data_maps_to() -> None:
    v1_kinds = {map_v1_version("active", layer, False).kind for layer in V1_LAYERS}
    assert set(conversational().kinds) == v1_kinds
    assert "procedure" not in conversational().kinds, "v1 stored no procedures"


def test_the_conversational_extractor_matches_the_v1_default_and_reads_only_turns() -> None:
    (extractor,) = conversational().extractors
    assert extractor.version == CandidateVersionInput.model_fields["extractor_version"].default
    assert extractor.source_types == ("turn",)


def test_the_conversational_profile_uses_no_facets_because_v1_had_none() -> None:
    assert conversational().facets == ()


# --- placeholders for the open decision V2-D14 -----------------------------------------------------


def test_abstraction_expiry_is_explicitly_marked_as_a_placeholder() -> None:
    assert conversational().abstraction_expiry.status == PLACEHOLDER_V2_D14
    assert PLACEHOLDER_V2_D14 in dump_profile(conversational())


def test_a_profile_cannot_pass_off_expiry_values_as_decided_by_default() -> None:
    document = base()
    document["facets"] = ["preference"]
    document["abstraction_expiry"] = {"days_by_facet": {"preference": 180}}
    assert parse_profile(document).abstraction_expiry.status == PLACEHOLDER_V2_D14


# --- expressiveness: the other rows of the profile table need no schema change ----------------------


def other_profile(name: str, root: str, retention: dict[str, str], promote: bool = False) -> dict[str, Any]:
    document = base()
    document.update(
        name=name,
        scope_root=root,
        kinds=["episode", "claim", "procedure", "abstraction"],
        operators=["consolidation", "cooccurrence"],
        extractors=[{"name": "tool_trace", "version": "1", "source_types": ["tool", "file", "event"]}],
        facets=["preference", "constraint"],
    )
    document["retention"] = {"defaults": retention, "promote_to_durable": promote, "status": PLACEHOLDER_V2_D14}
    document["render"]["channel_budget_share"] = {"facts": 0.5, "hypotheses": 0.2, "procedures": 0.2, "conflicts": 0.1}
    return document


def test_a_coding_agent_profile_is_expressible_as_configuration() -> None:
    coding = parse_profile(
        other_profile(
            "coding",
            "/u/{user}/p/{repo}/",
            {"episode": "session", "claim": "durable", "procedure": "session", "abstraction": "durable"},
            promote=True,
        )
    )
    assert coding.scope_root == "/u/{user}/p/{repo}/"
    assert coding.retention.promote_to_durable


def test_a_research_agent_profile_is_expressible_as_configuration() -> None:
    research = parse_profile(
        other_profile(
            "research",
            "/u/{user}/p/{topic}/",
            {"episode": "durable", "claim": "durable", "procedure": "durable", "abstraction": "durable"},
        )
    )
    assert research.name == "research"


def test_the_enterprise_scope_root_is_currently_rejected_by_the_scope_grammar() -> None:
    """Finding: ARCHITECTURE_V2.md section 13 lists `/o/<org>` but `scope.paths.parse_scope` only accepts `/u/...`.

    The profile validates against the real grammar, so this root cannot be expressed until the scope
    grammar (`E-011`) admits the organisation namespace. Recorded here so the gap is visible, not hidden.
    """
    enterprise = other_profile(
        "enterprise", "/o/{org}/", {k: "durable" for k in ("episode", "claim", "procedure", "abstraction")}
    )
    with pytest.raises(ValidationError) as raised:
        parse_profile(enterprise)
    assert "scope_must_begin_with_user_namespace" in str(raised.value)


# --- loading and serialisation ---------------------------------------------------------------------


def test_json_and_toml_documents_load_to_the_same_profile(tmp_path: Path) -> None:
    profile = conversational()
    as_json = tmp_path / "p.json"
    as_json.write_text(dump_profile(profile), encoding="utf-8")
    toml = tmp_path / "p.toml"
    toml.write_text(
        'schema_version = 1\nname = "conversational"\nkinds = ["episode", "claim", "abstraction"]\n'
        'operators = ["consolidation"]\nscope_root = "/u/{user}/"\nfacets = []\n'
        f'description = "{profile.description}"\n'
        '[[extractors]]\nname = "engine"\nversion = "engine-v1"\nsource_types = ["turn"]\n'
        '[retention]\npromote_to_durable = false\nstatus = "v1_semantics"\n'
        '[retention.defaults]\nepisode = "durable"\nclaim = "durable"\nabstraction = "durable"\n'
        "[render]\nshow_provenance = true\nshow_validity_intervals = true\n"
        "[render.channel_budget_share]\nfacts = 1.0\n"
        "[association]\ndegree_cap = 32\nextensions = []\n"
        '[abstraction_expiry]\nstatus = "placeholder:V2-D14"\n[abstraction_expiry.days_by_facet]\n',
        encoding="utf-8",
    )
    assert load_profile(as_json) == profile
    assert load_profile(toml) == profile


def test_unreadable_or_unsupported_files_raise_static_errors(tmp_path: Path) -> None:
    with pytest.raises(ProfileError) as missing:
        load_profile(tmp_path / "absent.json")
    assert missing.value.code == "profile_unreadable"
    odd = tmp_path / "p.yaml"
    odd.write_text("name: x", encoding="utf-8")
    with pytest.raises(ProfileError) as unsupported:
        load_profile(odd)
    assert unsupported.value.code == "profile_format_unsupported"
    broken = tmp_path / "p.json"
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ProfileError) as unparseable:
        load_profile(broken)
    assert unparseable.value.code == "profile_not_parseable"


# --- no branching on profile identity (requirement 4) ----------------------------------------------


@pytest.mark.parametrize(
    "source",
    [
        'if profile.name == "coding":\n    pass\n',
        'if profile_name != "research": pass\n',
        'if self.profile.id in ("coding", "enterprise"): pass\n',
        'match profile.name:\n    case "coding":\n        pass\n',
        'handler = {"a": 1}[profile.name]\n',
        "x = HANDLERS[active_profile.key]\n",
    ],
)
def test_the_scanner_flags_every_shape_of_branching_on_identity(source: str) -> None:
    assert find_profile_branching(source), source


@pytest.mark.parametrize(
    "source",
    [
        'if profile.scope_root == "/u/{user}/": pass\n',
        'if "claim" in profile.kinds: pass\n',
        'if name == "coding": pass\n',
        "label = profile.name\nprint(label)\n",
        "if profile.retention.promote_to_durable: pass\n",
    ],
)
def test_the_scanner_allows_reading_configuration(source: str) -> None:
    assert find_profile_branching(source) == []


def test_no_module_under_src_branches_on_a_profile_identity() -> None:
    assert scan_tree(SRC) == {}


def test_the_scanner_fails_on_a_fixture_module_that_branches(tmp_path: Path) -> None:
    fixture = tmp_path / "forked.py"
    fixture.write_text('def run(profile):\n    if profile.name == "coding":\n        return 1\n    return 2\n')
    found = scan_tree(tmp_path)
    assert list(found) == [str(fixture)]
    assert found[str(fixture)][0][0] == 2
