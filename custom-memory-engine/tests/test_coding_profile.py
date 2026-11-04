"""Verify the coding-agent profile document against its declared configuration contract.

These checks cover profile data and scope grammar. They do not establish that the
engine currently ingests every declared source type or persists repository-scoped
artifacts through the public API.
"""

import json
from importlib import resources
from pathlib import Path

import pytest
from pydantic import ValidationError

from custom_memory_engine.model.axes import FACETS
from custom_memory_engine.profile import PLACEHOLDER_V2_D14, Profile, dump_profile, load_profile, parse_profile
from custom_memory_engine.scope.paths import parse_scope

PACKAGED = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine" / "profiles" / "coding.json"


@pytest.fixture
def profile() -> Profile:
    """Load the actual profile document, rather than an example assembled by the test."""
    return load_profile(PACKAGED)


def test_coding_profile_document_loads_strictly_and_is_canonical(profile: Profile) -> None:
    assert profile.name == "coding"
    assert profile.schema_version == 1
    assert PACKAGED.read_text(encoding="utf-8") == dump_profile(profile)


def test_coding_profile_declares_all_required_kinds_and_extractor_sources(profile: Profile) -> None:
    assert set(profile.kinds) == {"episode", "claim", "procedure", "abstraction"}
    sources = {extractor.name: set(extractor.source_types) for extractor in profile.extractors}
    assert sources == {
        "tool_call_log": {"tool"},
        "diff_reader": {"file"},
        "ci_test_run_reader": {"event"},
        "user_instructions": {"turn"},
    }
    assert profile.operators == ("consolidation",)


def test_coding_profile_declares_repository_scope_accepted_by_real_grammar(profile: Profile) -> None:
    assert profile.scope_root == "/u/{user}/p/{repo}/"
    root = profile.scope_root.replace("{user}", "alice").replace("{repo}", "chronamind")
    assert parse_scope(root).text == "/u/alice/p/chronamind/"


@pytest.mark.parametrize("invalid_scope", ["/u/{user}/p/{repo}", "/u/{user}/p//", "/u/{user}//p/{repo}/"])
def test_profile_rejects_malformed_repository_scope_with_field_error(tmp_path: Path, invalid_scope: str) -> None:
    document = json.loads(PACKAGED.read_text(encoding="utf-8"))
    document["scope_root"] = invalid_scope
    mutated = tmp_path / "profile.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationError) as raised:
        load_profile(mutated)
    assert any(error["loc"] == ("scope_root",) for error in raised.value.errors())


def test_coding_profile_retention_floors_and_provisional_policy(profile: Profile) -> None:
    assert profile.retention.defaults == {
        "episode": "session",
        "claim": "durable",
        "procedure": "session",
        "abstraction": "durable",
    }
    assert profile.retention.promote_to_durable is True
    assert profile.retention.status == PLACEHOLDER_V2_D14
    assert profile.abstraction_expiry.status == PLACEHOLDER_V2_D14
    assert profile.abstraction_expiry.days_by_facet == {}


@pytest.mark.parametrize("kind", ["claim", "abstraction"])
def test_coding_profile_rejects_short_retention_for_durable_kinds(tmp_path: Path, kind: str) -> None:
    document = json.loads(PACKAGED.read_text(encoding="utf-8"))
    document["retention"]["defaults"][kind] = "session"
    mutated = tmp_path / "profile.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationError, match="retention_floor_requires_durable"):
        load_profile(mutated)


def test_coding_profile_render_facets_and_association_policy(profile: Profile) -> None:
    shares = profile.render.channel_budget_share
    assert shares["facts"] > 0
    assert shares["procedures"] > 0
    assert shares["hypotheses"] > 0
    assert sum(shares.values()) <= 1.0
    assert set(profile.facets) == {"constraint", "context", "decision_pattern"}
    assert set(profile.facets) <= set(FACETS)
    assert profile.association.degree_cap == 32
    assert profile.association.extensions == ()


def test_coding_profile_rejects_unknown_keys(tmp_path: Path) -> None:
    document = json.loads(PACKAGED.read_text(encoding="utf-8"))
    document["retention"]["default_typo"] = "session"
    mutated = tmp_path / "profile.json"
    mutated.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValidationError) as raised:
        load_profile(mutated)
    assert any(error["loc"] == ("retention", "default_typo") for error in raised.value.errors())


def test_the_coding_profile_loads_from_its_packaged_resource() -> None:
    """E-019 loads ``conversational`` through ``importlib.resources``; the coding document must ship too.

    A filesystem-path read passes in the source tree even if the file is excluded from the built wheel,
    so this mirrors the conversational check and reads the document as a package resource.
    """
    text = resources.files("custom_memory_engine").joinpath("profiles/coding.json").read_text("utf-8")
    profile = parse_profile(json.loads(text))
    assert profile.name == "coding"
    assert profile == load_profile(PACKAGED)
