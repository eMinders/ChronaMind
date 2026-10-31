"""#46 / RL-070: the seven-channel leak scanner must measure every channel, and A8 must trip."""

import pytest

from custom_memory_engine.deletion.scanner import CHANNELS, ScanContext, scan_deleted_key
from evaluation.harness.leak_scanner import (
    CANARY_MIN_LENGTH,
    LeakScannerError,
    answer_probe,
    assembled_context_probe,
    external_cache_probe,
    harness_probes,
    log_sink_probe,
    vector_probe,
    verdict,
    verify_detection,
)

CANARY = "CANARY-7f3a91xz"
CONTEXT = ScanContext(user_id="alice", memory_key="k1", canary=CANARY, deletion_epoch=3)


class FakeVector:
    def __init__(self, hits: list[str]) -> None:
        self.hits = hits
        self.calls: list[tuple[str, str, int]] = []

    def vector_hits(self, user_id: str, query: str, limit: int) -> list[str]:
        self.calls.append((user_id, query, limit))
        return self.hits


class FakeContext:
    def __init__(self, text: str) -> None:
        self.text = text

    def assembled_context(self, user_id: str, query: str) -> str:
        return self.text


class FakeAnswer:
    def __init__(self, text: str) -> None:
        self.text = text

    def answer(self, user_id: str, question: str) -> str:
        return self.text


# --- each channel measures correctly ---------------------------------------


@pytest.mark.asyncio
async def test_vector_channel_reports_a_residual_hit() -> None:
    probe = vector_probe(FakeVector([f"doc {CANARY}"]), query="q")
    measurement = await probe(CONTEXT)
    assert measurement.residual_count == 1
    assert measurement.complete is True


@pytest.mark.asyncio
async def test_vector_channel_reports_zero_when_purged() -> None:
    probe = vector_probe(FakeVector(["clean doc", "another"]), query="q")
    measurement = await probe(CONTEXT)
    assert measurement.residual_count == 0


@pytest.mark.asyncio
async def test_assembled_context_channel_detects_leak() -> None:
    probe = assembled_context_probe(FakeContext(f"system: you know {CANARY}"), query="q")
    assert (await probe(CONTEXT)).residual_count == 1


@pytest.mark.asyncio
async def test_final_answer_channel_detects_leak() -> None:
    probe = answer_probe(FakeAnswer(f"The answer is {CANARY}"), question="q")
    assert (await probe(CONTEXT)).residual_count == 1


@pytest.mark.asyncio
async def test_log_channel_detects_leak_in_any_line() -> None:
    lines = ["fine", f"ingest payload={CANARY}", "fine"]
    probe = log_sink_probe(lambda: lines)
    assert (await probe(CONTEXT)).residual_count == 1


@pytest.mark.asyncio
async def test_external_cache_channel_completes_channel_1() -> None:
    probe = external_cache_probe(lambda user_id: [f"cached::{CANARY}"])
    measurement = await probe(CONTEXT)
    assert measurement.residual_count == 1
    assert measurement.complete is True


# --- canary safety ----------------------------------------------------------


@pytest.mark.asyncio
async def test_short_canary_is_rejected_not_silently_accepted() -> None:
    probe = vector_probe(FakeVector([]), query="q")
    short = ScanContext(user_id="alice", memory_key="k1", canary="abc", deletion_epoch=1)
    with pytest.raises(LeakScannerError) as error:
        await probe(short)
    assert error.value.code == "canary_too_short_to_be_distinctive"


def test_canary_minimum_is_enforced_at_the_documented_boundary() -> None:
    assert CANARY_MIN_LENGTH >= 8


@pytest.mark.asyncio
async def test_canary_is_matched_literally_not_as_a_pattern() -> None:
    """A canary containing regex metacharacters must not widen the match."""
    probe = assembled_context_probe(FakeContext("text without the marker"), query="q")
    patternish = ScanContext(user_id="alice", memory_key="k1", canary="CANARY.7f3a91.*", deletion_epoch=1)
    assert (await probe(patternish)).residual_count == 0


# --- composition and fail-closed verdict -----------------------------------


def test_harness_probes_builds_only_supplied_channels() -> None:
    probes = harness_probes(
        vector=(FakeVector([]), "q"),
        logs=lambda: [],
    )
    assert set(probes) == {"channel_3_vector_search", "channel_7_application_logs"}


def test_harness_probes_empty_when_no_surfaces_supplied() -> None:
    assert harness_probes() == {}


@pytest.mark.asyncio
async def test_verdict_fails_closed_when_channels_are_unmeasured() -> None:
    report = await scan_deleted_key(CONTEXT, harness_probes(vector=(FakeVector([]), "q")))
    result = verdict(report)
    assert result.clean is False, "unmeasured channels must never read as clean"
    assert result.complete is False
    assert "channel_5_assembled_context" in result.unmeasured_channels


@pytest.mark.asyncio
async def test_verdict_is_clean_only_when_all_seven_channels_measure_zero() -> None:
    probes = harness_probes(
        vector=(FakeVector([]), "q"),
        assembled=(FakeContext("clean"), "q"),
        answer=(FakeAnswer("clean"), "q"),
        logs=lambda: ["clean"],
        cache=lambda user_id: [],
    )
    report = await scan_deleted_key(CONTEXT, probes)
    partial = verdict(report)
    # Channels 2 and 4 need a database connection; without one they are unmeasured.
    assert "channel_2_lexical_search" in partial.unmeasured_channels
    assert partial.clean is False
    assert partial.leaking_channels == ()


@pytest.mark.asyncio
async def test_verdict_reports_leaking_channel() -> None:
    probes = harness_probes(logs=lambda: [f"kept {CANARY}"])
    result = verdict(await scan_deleted_key(CONTEXT, probes))
    assert "channel_7_application_logs" in result.leaking_channels
    assert result.clean is False


def test_all_seven_channel_names_are_covered_by_the_engine_scanner() -> None:
    """The harness must not invent or omit a channel relative to the engine contract."""
    assert len(CHANNELS) == 7
    assert len(set(CHANNELS)) == 7


# --- A8: detection must be proven ------------------------------------------


@pytest.mark.asyncio
async def test_a8_detection_trips_on_an_injected_leak() -> None:
    probes = harness_probes(logs=lambda: ["clean"])
    result = await verify_detection(context=CONTEXT, probes=probes)
    assert result.detected is True
    assert result.injected_channel == "channel_7_application_logs"


@pytest.mark.asyncio
async def test_a8_detection_uses_the_named_channel() -> None:
    probes = harness_probes(vector=(FakeVector([]), "q"))
    result = await verify_detection(context=CONTEXT, probes=probes, injected_channel="channel_3_vector_search")
    assert result.detected is True


@pytest.mark.asyncio
async def test_a8_rejects_unknown_channel() -> None:
    with pytest.raises(LeakScannerError):
        await verify_detection(context=CONTEXT, probes={}, injected_channel="channel_9_made_up")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_engine_scan_still_reports_unmeasured_without_connection() -> None:
    """The engine scanner's own contract is unchanged by the harness addition."""
    report = await scan_deleted_key(CONTEXT)
    assert len(report.findings) == 7
    assert report.inconclusive
