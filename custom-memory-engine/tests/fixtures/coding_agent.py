"""Deterministic coding-shaped inputs for the generic profile's representation checks.

The six records cover the coding column of ARCHITECTURE_V2.md §13: a tool-call
episode, a diff episode, a test-run episode, a repository claim, a CI procedure,
and an inferred recurring failure mode. These are descriptions of intended
inputs, not evidence that every source kind or public operation is wired.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from custom_memory_engine.engine.channels import Channel, Verification
from custom_memory_engine.grounding.sources import canonical_hash
from custom_memory_engine.model.axes import Kind, Lifecycle

type CodingSourceType = Literal["turn", "tool", "file", "event"]

BASE_TIME = datetime(2025, 4, 1, 10, 0, tzinfo=UTC)
SCOPE = "/u/alice/p/chrona/"


@dataclass(frozen=True, slots=True)
class CodingSource:
    """One captured synthetic source, with a verbatim excerpt and stable digest."""

    source_type: CodingSourceType
    ref: str
    content: str
    span: str

    @property
    def content_hash(self) -> str:
        """Apply the engine's declared source-content canonicalization."""
        return canonical_hash(self.content)


@dataclass(frozen=True, slots=True)
class CodingArtifact:
    """An intended artifact and its expected channel under the frozen channel rules."""

    name: str
    kind: Kind
    lifecycle: Lifecycle
    verification: Verification
    content: str
    source: CodingSource
    event_time: datetime
    expected_channel: Channel


def coding_artifacts() -> tuple[CodingArtifact, ...]:
    """Return the same small, offline scenario on each call; no service is invoked."""
    return (
        CodingArtifact(
            name="tool_call",
            kind="episode",
            lifecycle="observed",
            verification="none",
            content="Running the focused test failed because json was not imported.",
            source=CodingSource(
                source_type="tool",
                ref="tool:pytest/failure",
                content="pytest -q tests/test_check.py\nNameError: name 'json' is not defined\n",
                span="NameError: name 'json' is not defined",
            ),
            event_time=BASE_TIME,
            expected_channel="facts",
        ),
        CodingArtifact(
            name="diff",
            kind="episode",
            lifecycle="observed",
            verification="none",
            content="The change added the missing json import to src/check.py.",
            source=CodingSource(
                source_type="file",
                ref="file:src/check.py.diff",
                content="--- a/src/check.py\n+++ b/src/check.py\n@@ -1,0 +1 @@\n+import json\n",
                span="+import json",
            ),
            event_time=BASE_TIME + timedelta(minutes=1),
            expected_channel="facts",
        ),
        CodingArtifact(
            name="test_run",
            kind="episode",
            lifecycle="observed",
            verification="none",
            content="The focused test passed after the import was added.",
            source=CodingSource(
                source_type="event",
                ref="event:pytest/success",
                content="pytest -q tests/test_check.py\n1 passed\n",
                span="1 passed",
            ),
            event_time=BASE_TIME + timedelta(minutes=2),
            expected_channel="facts",
        ),
        CodingArtifact(
            name="repo_claim",
            kind="claim",
            lifecycle="asserted",
            verification="none",
            content="The CI job runs the focused test with pytest.",
            source=CodingSource(
                source_type="turn",
                ref="turn:ci-instruction",
                content="The CI job runs the focused test with pytest.",
                span="The CI job runs the focused test with pytest.",
            ),
            event_time=BASE_TIME + timedelta(minutes=3),
            expected_channel="facts",
        ),
        CodingArtifact(
            name="ci_procedure",
            kind="procedure",
            lifecycle="asserted",
            verification="none",
            content="For a missing json name, add the import and rerun the focused pytest command.",
            source=CodingSource(
                source_type="turn",
                ref="turn:ci-recipe",
                content="For a missing json name, add the import and rerun the focused pytest command.",
                span="add the import and rerun the focused pytest command",
            ),
            event_time=BASE_TIME + timedelta(minutes=4),
            expected_channel="procedures",
        ),
        CodingArtifact(
            name="recurring_failure_mode",
            kind="abstraction",
            lifecycle="inferred",
            verification="unverified",
            content="A missing import may explain repeated name errors in focused CI tests.",
            source=CodingSource(
                source_type="tool",
                ref="tool:pytest/repeated-failures",
                content="run one: NameError: json\nrun two: NameError: json\n",
                span="NameError: json",
            ),
            event_time=BASE_TIME + timedelta(minutes=5),
            expected_channel="hypotheses",
        ),
    )
