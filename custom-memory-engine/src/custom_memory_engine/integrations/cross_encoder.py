"""A local cross-encoder *relevance judge* over ``onnxruntime`` (``V2-D104``).

**Why this exists, in one paragraph.** A bi-encoder puts a question and a memory in the same vector space and compares
them with one dot product. That is what makes retrieval cheap enough to run on every read, and it is also its limit:
"our neighbours complain about the noise every night" scored 0.605 against "I am on call every second weekend",
because both are short sentences with a recurring-time frame — the *topics* are far apart and the vector does not
notice. Measured on the labelled set (`evaluation/retrieval`, `V2-D104`), **no** threshold, corpus-relative statistic
or arm-agreement rule separates that case from a genuine paraphrase: the weakest true match sat at 0.46 and that false
one at 0.605. A cross-encoder reads the pair together, so it can. On the same set it separates cleanly: every
answerable question scored at least ``-6.68`` and every unanswerable one at most ``-9.41``.

**Why a local file and not a provider.** The same three reasons the embedder is local (`V2-D94`): the deployment's
provider serves no reranking endpoint, a frozen ``model.onnx`` plus a tokenizer JSON is a fixed artifact whose score is
reproducible byte for byte, and a generative "is this relevant?" call per candidate would put an LLM inside the read
path. A cross-encoder is a classifier: one forward pass, one number, no sampling, no temperature.

**Why the score is comparable across queries (and a reranker's is not).** This model is trained with a contrastive
objective over (query, passage) *pairs*, so its logit is a relevance decision on one absolute scale, which is what
makes an absolute floor meaningful. An order-only reranker's scale is arbitrary and only its order can be used, which
is why this is a judge and why the old order-only seam is gone.

**Cost, measured on the reference CPU.** ~28 ms per candidate pair, so judging the default candidate cap of ten costs
~280 ms. That is real money on a read path, which is why the judge is *optional* (a deployment without the asset keeps
the fused order and reports that nothing was judged) and why the candidate count is bounded rather than unbounded.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class CrossEncoderError(ValueError):
    """A static code: no path, no text, no model output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CrossEncoderAssets:
    """One pinned model directory, the file inside it that holds the weights, and the name it reports as."""

    directory: Path
    model: str
    #: Which file in ``directory`` is the graph. A quantised export is a different file with the same tokenizer, and
    #: naming the file rather than hardcoding ``model.onnx`` is what lets an int8 deployment stay a *pinned* asset.
    weights: str = "model.onnx"
    max_tokens: int = 512

    def validate(self) -> None:
        if self.max_tokens <= 0:
            raise CrossEncoderError("cross_encoder_max_tokens_invalid")
        for required in (self.weights, "tokenizer.json"):
            if not (self.directory / required).is_file():
                raise CrossEncoderError(f"missing_{required.replace('.', '_').replace('/', '_')}")
        if not self.model:
            raise CrossEncoderError("cross_encoder_model_name_required")


#: The assets this project measures with, so a deployment names one instead of assembling fields by hand.
KNOWN_RERANKERS: dict[str, dict[str, Any]] = {
    # 568M multilingual parameters, int8: the one `V2-D104` measured. Its scores separate answerable from unanswerable
    # by 2.7 logits on the labelled set, which is the whole reason the judge is worth its latency.
    "bge-reranker-v2-m3": {
        "model": "BAAI/bge-reranker-v2-m3",
        "weights": "model_int8.onnx",
        "max_tokens": 512,
    },
    # 278M multilingual parameters. Measured too, and **not** chosen: its logits overlap (worst answerable -2.91,
    # best unanswerable -2.38), so it reorders but cannot gate. Kept in the table so a re-measurement starts from the
    # comparison rather than from one model.
    "jina-reranker-v2-base-multilingual": {
        "model": "jinaai/jina-reranker-v2-base-multilingual",
        "weights": "model_int8.onnx",
        "max_tokens": 512,
    },
}


def reranker_assets_for(name: str, directory: Path) -> CrossEncoderAssets:
    """The named reranker asset set, validated against the files actually present."""
    if name not in KNOWN_RERANKERS:
        raise CrossEncoderError("unknown_reranker_asset")
    assets = CrossEncoderAssets(directory=directory, **KNOWN_RERANKERS[name])
    assets.validate()
    return assets


@dataclass
class OnnxCrossEncoder:
    """A :class:`~custom_memory_engine.engine.hybrid.RelevanceJudge` backed by one frozen ONNX classifier.

    Loaded lazily, like the embedder: constructing it reads nothing, so a process that never judges pays nothing and a
    misconfigured path is reported at the first judge call rather than at start-up.
    """

    assets: CrossEncoderAssets
    threads: int = 0  # 0 = onnxruntime's default, which respects cgroup limits
    _session: Any = field(default=None, repr=False, compare=False)
    _tokenizer: Any = field(default=None, repr=False, compare=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def model(self) -> str:
        return self.assets.model

    def _load(self) -> None:
        if self._session is not None and self._tokenizer is not None:
            return
        import onnxruntime
        from tokenizers import Tokenizer

        self.assets.validate()
        options = onnxruntime.SessionOptions()
        options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
        if self.threads:
            options.intra_op_num_threads = self.threads
        self._session = onnxruntime.InferenceSession(
            str(self.assets.directory / self.assets.weights), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(self.assets.directory / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=self.assets.max_tokens)
        logger.info("cross_encoder_loaded", model=self.assets.model, weights=self.assets.weights)

    async def relevance(self, query: str, documents: Sequence[str]) -> Sequence[float]:
        """One relevance logit per ``(query, document)`` pair; higher means more relevant.

        Pairs are encoded and run **one at a time**. Batching them would need padding, and this graph is
        padding-sensitive: the same pair scored ``-1.55`` alone and ``-2.68`` inside a padded batch of five. A gate
        whose answer depends on which other candidates happened to be in the page is not a gate, so the cost is paid in
        throughput instead — and the candidate count is bounded by the caller for the same reason.
        """
        if not documents:
            return ()
        async with self._lock:  # one session, one thread pool: concurrent pairs contend anyway
            return await asyncio.to_thread(self._score, query, list(documents))

    def _score(self, query: str, documents: list[str]) -> list[float]:
        self._load()
        scores: list[float] = []
        for document in documents:
            encoded = self._tokenizer.encode(query, document)
            ids = np.asarray([encoded.ids], dtype=np.int64)
            mask = np.asarray([encoded.attention_mask], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            names = {item.name for item in self._session.get_inputs()}
            if "token_type_ids" in names:  # XLM-R style graphs declare it; BGE's does not
                feeds["token_type_ids"] = np.zeros_like(ids)
            logits = self._session.run(None, feeds)[0]
            value = float(np.asarray(logits).reshape(-1)[0])
            if not np.isfinite(value):
                raise CrossEncoderError("non_finite_relevance_score")
            scores.append(value)
        return scores


def assets_from_environment(environment: dict[str, str]) -> CrossEncoderAssets | None:
    """``CME_RERANKER_ASSET`` + ``CME_RERANKER_DIR``: the only place the deployment names its judge."""
    name = environment.get("CME_RERANKER_ASSET", "").strip()
    if not name:
        return None
    directory = Path(environment.get("CME_RERANKER_DIR", "") or f".model-cache/{name}")
    return reranker_assets_for(name, directory)


def model_fingerprint(assets: CrossEncoderAssets) -> str:
    """A short digest of the model bytes, so a re-measurement can prove it used the same graph."""
    digest = hashlib.sha256()
    for name in (assets.weights, "tokenizer.json"):
        with (assets.directory / name).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()[:32]


__all__ = [
    "CrossEncoderAssets",
    "CrossEncoderError",
    "KNOWN_RERANKERS",
    "OnnxCrossEncoder",
    "assets_from_environment",
    "model_fingerprint",
    "reranker_assets_for",
]
