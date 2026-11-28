"""Validate the offline coding inputs; public ingestion and retrieval are separate checks."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from custom_memory_engine.engine.channels import channel_of
from custom_memory_engine.profile import load_profile
from tests.fixtures.coding_agent import SCOPE, coding_artifacts


def test_six_fixtures_cover_the_coding_column_with_declared_sources() -> None:
    artifacts = coding_artifacts()
    assert [(item.name, item.kind, item.lifecycle, item.source.source_type) for item in artifacts] == [
        ("tool_call", "episode", "observed", "tool"),
        ("diff", "episode", "observed", "file"),
        ("test_run", "episode", "observed", "event"),
        ("repo_claim", "claim", "asserted", "turn"),
        ("ci_procedure", "procedure", "asserted", "turn"),
        ("recurring_failure_mode", "abstraction", "inferred", "tool"),
    ]
    assert len({item.name for item in artifacts}) == 6
    assert len({item.source.ref for item in artifacts}) == 6
    assert SCOPE == "/u/alice/p/chrona/"


def test_all_fixture_sources_are_declared_by_the_packaged_profile() -> None:
    document = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine" / "profiles" / "coding.json"
    profile = load_profile(document)
    supported = {source for extractor in profile.extractors for source in extractor.source_types}
    assert {item.source.source_type for item in coding_artifacts()} <= supported


def test_captured_spans_and_fixed_utc_times_are_consistent() -> None:
    artifacts = coding_artifacts()
    assert [item.event_time for item in artifacts] == [
        datetime(2025, 4, 1, 10, 0, tzinfo=UTC) + timedelta(minutes=i) for i in range(6)
    ]
    for item in artifacts:
        assert item.content, item.name
        assert item.source.ref, item.name
        assert item.source.span and item.source.span in item.source.content, item.name


def test_diff_hash_is_a_pinned_canonical_digest_and_changes_with_the_capture() -> None:
    diff = next(item for item in coding_artifacts() if item.name == "diff")
    assert diff.source.span == "+import json"
    assert diff.source.content_hash == "28e6067832150d2a6ee82bea7f279ce26f2088b5599bad241c4b316c7717a38a"
    changed = replace(diff.source, content=diff.source.content.replace("+import json", "+import time"))
    assert changed.content_hash != diff.source.content_hash
    crlf = replace(diff.source, content=diff.source.content.replace("\n", "\r\n"))
    assert crlf.content_hash == diff.source.content_hash


def test_declared_channels_match_the_pure_membership_contract() -> None:
    """This tests the membership function only, not public API routing or persistence."""
    artifacts = coding_artifacts()
    assert [item.expected_channel for item in artifacts] == [
        "facts",
        "facts",
        "facts",
        "facts",
        "procedures",
        "hypotheses",
    ]
    for item in artifacts:
        actual = channel_of(item.kind, item.lifecycle, item.verification, False, False)
        assert actual == item.expected_channel, item.name


def test_generation_is_deterministic_and_returns_immutable_records() -> None:
    first, second = coding_artifacts(), coding_artifacts()
    assert first == second
    assert [item.source.content_hash for item in first] == [item.source.content_hash for item in second]
    assert replace(first[0], content="another capture") != first[0]
    with pytest.raises(FrozenInstanceError):
        first[0].__setattr__("content", "another capture")
