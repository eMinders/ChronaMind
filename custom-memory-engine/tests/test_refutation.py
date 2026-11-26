"""Unit tests for ``engine/refutation.py`` that need no database (``I15``, #245)."""

from typing import cast

from psycopg import AsyncConnection

from custom_memory_engine.engine.refutation import run_refutation_probes
from custom_memory_engine.soundness.admission import Ledger, assess


class _UnusableConnection:
    """Fails on any use, so a test proves that no probe ran rather than that one ran and found nothing."""

    def cursor(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("no_probe_may_run_without_a_target")


async def test_refutation_probes_with_no_target_report_zero_attempts_and_an_unclean_control() -> None:
    report = await run_refutation_probes(
        cast(AsyncConnection, _UnusableConnection()),
        "alice",
        None,
        (),
        scope_path="/u/alice/",
    )

    assert report.attempts == 0
    assert report.counter_ids == ()
    assert report.negative_control_clean is False
    # The measured pair can only make an admission fail: the same ledger that admits with three executed probes and a
    # clean control is refused with this report.
    ledger = Ledger(support=60, counter=0, undetermined=0)
    assert assess(ledger, refutation_attempts=3, negative_control_clean=True).admissible
    refused = assess(ledger, refutation_attempts=report.attempts, negative_control_clean=report.negative_control_clean)
    assert not refused.admissible
    assert {"too_few_refutation_attempts", "negative_control_slice_not_clean"} <= set(refused.reasons)
