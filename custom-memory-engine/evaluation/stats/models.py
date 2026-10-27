"""Paired metric observations: runs within items, items within independent clusters."""

from collections import defaultdict
from collections.abc import Sequence
from math import fsum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

type Dataset = Literal["T", "G", "LongMemEval_S", "LoCoMo"]
type Endpoint = Literal["history_taa", "present_taa", "recall", "asr", "luar", "qa"]
type Score = Annotated[float, Field(ge=0, le=1)]


class StatsRecord(BaseModel):
    """Finite, immutable, JSON-serializable analysis inputs and outputs."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)

    @field_validator("*")
    @classmethod
    def reject_blank_identifiers(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            raise ValueError("nonblank_identifier_required")
        return value


class Observation(StatsRecord):
    """One configuration's score; run identity must match before pairing."""

    cluster_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    value: Score


class PairedObservation(StatsRecord):
    """Same question/event and run under P and its comparator; values are fractions."""

    cluster_id: str = Field(min_length=1)
    item_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    proposed: Score
    comparator: Score


class ClusterEffect(StatsRecord):
    cluster_id: str
    difference: float = Field(ge=-1, le=1)
    item_count: int = Field(ge=1)
    run_count: int = Field(ge=1)


class PairedSample(StatsRecord):
    """One endpoint/stratum on one dataset; datasets are never pooled.

    Each item's repeated runs are averaged first, then the items of each cluster.
    A missing item/run is an error, never an imputed zero or an inner-join drop.
    """

    dataset: Dataset
    endpoint: Endpoint
    comparator: str = Field(min_length=1)
    observations: tuple[PairedObservation, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_pairing(self) -> "PairedSample":
        endpoints = {
            "T": {"history_taa", "present_taa"},
            "G": {"asr", "luar"},
            "LongMemEval_S": {"recall"},
            "LoCoMo": {"qa", "recall"},
        }
        if self.endpoint not in endpoints[self.dataset]:
            raise ValueError("endpoint_dataset_mismatch")
        seen: set[tuple[str, str, str]] = set()
        item_clusters: dict[str, str] = {}
        item_runs: dict[str, set[str]] = defaultdict(set)
        for row in self.observations:
            key = (row.cluster_id, row.item_id, row.run_id)
            if key in seen:
                raise ValueError("duplicate_paired_observation")
            seen.add(key)
            if row.item_id in item_clusters and item_clusters[row.item_id] != row.cluster_id:
                raise ValueError("item_belongs_to_multiple_clusters")
            item_clusters[row.item_id] = row.cluster_id
            item_runs[row.item_id].add(row.run_id)
            if self.dataset == "LongMemEval_S" and row.cluster_id != row.item_id:
                raise ValueError("longmemeval_question_must_be_its_cluster")
        if len({frozenset(runs) for runs in item_runs.values()}) != 1:
            raise ValueError("incomplete_repeated_run_grid")
        return self

    def cluster_effects(self) -> tuple[ClusterEffect, ...]:
        """Canonical cluster order and equal item weights, independent of input order."""
        grouped: dict[str, dict[str, list[PairedObservation]]] = defaultdict(lambda: defaultdict(list))
        for row in self.observations:
            grouped[row.cluster_id][row.item_id].append(row)
        effects: list[ClusterEffect] = []
        for cluster_id, items in sorted(grouped.items()):
            item_means: list[float] = []
            for _, rows in sorted(items.items()):
                ordered = sorted(rows, key=lambda row: row.run_id)
                item_means.append(fsum(row.proposed - row.comparator for row in ordered) / len(ordered))
            effects.append(
                ClusterEffect(
                    cluster_id=cluster_id,
                    difference=fsum(item_means) / len(item_means),
                    item_count=len(items),
                    run_count=len(next(iter(items.values()))),
                )
            )
        return tuple(effects)

    @property
    def analysis_unit(self) -> Literal["scenario", "question", "conversation"]:
        if self.dataset == "LongMemEval_S":
            return "question"
        if self.dataset == "LoCoMo":
            return "conversation"
        return "scenario"


def pair_observations(
    proposed: Sequence[Observation],
    comparator: Sequence[Observation],
    *,
    dataset: Dataset,
    endpoint: Endpoint,
    comparator_id: str,
) -> PairedSample:
    """Exact identity join across configurations; reject missing/extra/duplicate rows."""
    indexed: list[dict[tuple[str, str, str], Observation]] = []
    for rows in (proposed, comparator):
        index: dict[tuple[str, str, str], Observation] = {}
        for row in rows:
            key = (row.cluster_id, row.item_id, row.run_id)
            if key in index:
                raise ValueError("duplicate_observation")
            index[key] = row
        indexed.append(index)
    left, right = indexed
    if left.keys() != right.keys():
        raise ValueError("unpaired_observation_identities")
    return PairedSample(
        dataset=dataset,
        endpoint=endpoint,
        comparator=comparator_id,
        observations=tuple(
            PairedObservation(
                cluster_id=cluster,
                item_id=item,
                run_id=run,
                proposed=left[cluster, item, run].value,
                comparator=right[cluster, item, run].value,
            )
            for cluster, item, run in sorted(left)
        ),
    )


class ConfidenceInterval(StatsRecord):
    level: float = Field(gt=0, lt=1)
    low: float = Field(ge=-1, le=1)
    high: float = Field(ge=-1, le=1)

    @model_validator(mode="after")
    def ordered_bounds(self) -> "ConfidenceInterval":
        if self.low > self.high:
            raise ValueError("reversed_confidence_interval")
        return self
