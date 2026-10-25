"""Normalized fixture envelope; real benchmark adaptation is RL-025."""

from pathlib import Path

from pydantic import Field

from evaluation.harness.models import EvaluationCase, Record, Split, SuiteId


class CaseDataset(Record):
    schema_version: int = Field(ge=1, le=1)
    split: Split
    suite_id: SuiteId
    cases: tuple[EvaluationCase, ...] = Field(min_length=1)


def load_cases(path: Path, split: Split, suite_id: SuiteId) -> tuple[EvaluationCase, ...]:
    """Assert the declared split/suite matches the envelope before adapter creation."""
    dataset = CaseDataset.model_validate_json(path.read_bytes())
    if dataset.split != split or dataset.suite_id != suite_id:
        raise ValueError("dataset_split_or_suite_mismatch")
    return dataset.cases
