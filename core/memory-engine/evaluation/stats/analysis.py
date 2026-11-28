"""Connect paired source identities to the two predeclared inferential summaries."""

from hashlib import sha256

from pydantic import model_validator

from evaluation.stats.bootstrap import BootstrapResult, cluster_bootstrap
from evaluation.stats.models import PairedSample, StatsRecord
from evaluation.stats.permutation import SignFlipResult, differences_digest, paired_permutation


class ContrastAnalysis(StatsRecord):
    """Reproducible analysis evidence, not a research result or approval."""

    sample: PairedSample
    input_sha256: str
    bootstrap: BootstrapResult
    permutation: SignFlipResult | None

    @model_validator(mode="after")
    def consistent_evidence(self) -> "ContrastAnalysis":
        canonical = _canonical_sample(self.sample)
        if self.input_sha256 != sha256(canonical.model_dump_json().encode("utf-8")).hexdigest():
            raise ValueError("analysis_input_hash_mismatch")
        clusters = canonical.cluster_effects()
        if self.bootstrap.clusters != clusters:
            raise ValueError("bootstrap_sample_cluster_mismatch")
        if not _requires_superiority_test(self.sample):
            if self.permutation is not None:
                raise ValueError("no_superiority_test_for_descriptive_or_ni_endpoint")
        else:
            if self.permutation is None:
                raise ValueError("permutation_result_required")
            result = self.permutation
            if (
                result.seed != self.bootstrap.seed
                or result.estimate != self.bootstrap.estimate
                or result.n_clusters != len(clusters)
                or result.n_positive != sum(c.difference > 0 for c in clusters)
                or result.n_negative != sum(c.difference < 0 for c in clusters)
                or result.n_zero != sum(c.difference == 0 for c in clusters)
                or result.differences_sha256 != differences_digest([c.difference for c in clusters])
            ):
                raise ValueError("permutation_sample_summary_mismatch")
        return self

    @property
    def inferentially_feasible(self) -> bool:
        """Proposal §10.6's arithmetic floor; this is not a power calculation."""
        return self.permutation is not None and self.permutation.n_nonzero >= 7


def _canonical_sample(sample: PairedSample) -> PairedSample:
    canonical = sample.model_dump()
    canonical["observations"] = tuple(
        row.model_dump()
        for row in sorted(sample.observations, key=lambda row: (row.cluster_id, row.item_id, row.run_id))
    )
    return PairedSample.model_validate(canonical)


def _requires_superiority_test(sample: PairedSample) -> bool:
    return sample.dataset != "LoCoMo" and sample.endpoint in {"history_taa", "recall", "asr"}


def analyze_contrast(
    sample: PairedSample,
    *,
    seed: int,
    bootstrap_resamples: int = 10_000,
    permutation_resamples: int = 100_000,
    max_exact_clusters: int = 16,
) -> ContrastAnalysis:
    """One dataset/endpoint/contrast, with a recorded seed and canonical input hash."""
    ordered = _canonical_sample(sample)
    bootstrap = cluster_bootstrap(ordered, seed=seed, resamples=bootstrap_resamples)
    permutation = (
        None
        if not _requires_superiority_test(sample)
        else paired_permutation(
            [cluster.difference for cluster in bootstrap.clusters],
            seed=seed,
            resamples=permutation_resamples,
            max_exact_clusters=max_exact_clusters,
        )
    )
    return ContrastAnalysis(
        sample=ordered,
        input_sha256=sha256(ordered.model_dump_json().encode("utf-8")).hexdigest(),
        bootstrap=bootstrap,
        permutation=permutation,
    )
