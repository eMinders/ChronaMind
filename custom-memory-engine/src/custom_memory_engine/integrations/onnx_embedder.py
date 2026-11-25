"""A local sentence-embedding model over ``onnxruntime`` (``V2-D94``).

The dense retrieval arm needs vectors, and the model provider this deployment uses does not serve an ``/embeddings``
endpoint (it answers ``404 Endpoint not supported``). Rather than leave the arm unmeasurable — or hand the engine's
memory to a vendor that happens to offer one — the deployment embeds locally from a pinned file set. The engine already
depends on ``onnxruntime`` and ``tokenizers`` for its evaluation baselines, so this adds no new dependency.

**Why an ONNX file and not a library.** ``sentence-transformers`` would pull PyTorch (~2.5 GB) and a runtime that
loads remote code by default. A frozen ``model.onnx`` plus a tokenizer JSON is a fixed artifact: the same bytes
produce the same vector on every machine, which is what makes an embedding reproducible across a re-embedding
rollout — the property ``hybrid.EMBEDDING_SLOTS`` exists to make possible.

**Pooling is explicit because pooling is a correctness decision.** E5 models are trained with *mean* pooling and
require the ``query: ``/``passage: `` prefixes; BGE-M3 uses *CLS* pooling. Getting either wrong silently degrades
recall by a large margin while every test still passes, so the prefix and the pooling are parameters of the asset, not
defaults buried in code, and :data:`EmbeddingAssets` refuses a mismatched configuration.

**Inference is off the event loop.** ``predict`` is synchronous and CPU-bound, so :meth:`embed` runs it in a worker
thread. A 20 ms encode on the event loop is a 20 ms stall for every other request in the process.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import structlog

logger = structlog.get_logger(__name__)


class EmbeddingError(ValueError):
    """A static code: no path, no text, no model output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class EmbeddingAssets:
    """One pinned model directory and the three facts that must agree with it."""

    directory: Path
    dimensions: int
    pooling: str = "mean"  # "mean" (E5 family) or "cls" (BGE family)
    document_prefix: str = ""
    query_prefix: str = ""
    max_tokens: int = 512
    #: The value stored in `memory_embedding.embedding_model`. It names the artifact, not the vendor: a re-embedding
    #: rollout writes rows under a new name and the slots let both coexist.
    model: str = ""

    def validate(self) -> None:
        if self.pooling not in ("mean", "cls"):
            raise EmbeddingError("pooling_not_supported")
        if self.dimensions <= 0 or self.max_tokens <= 0:
            raise EmbeddingError("embedding_dimensions_invalid")
        for required in ("model.onnx", "tokenizer.json"):
            if not (self.directory / required).is_file():
                raise EmbeddingError(f"missing_{required.replace('.', '_')}")
        if not self.model:
            raise EmbeddingError("embedding_model_name_required")


#: The two shapes this project actually evaluates with, so a deployment names one instead of assembling five fields.
#: Dimensions match ``hybrid.EMBEDDING_SLOTS``: 1024 (and 384, which the legacy `vector(384)` column already holds).
KNOWN_ASSETS: dict[str, dict[str, Any]] = {
    "bge-m3": {
        "dimensions": 1024,
        "pooling": "cls",
        "document_prefix": "",
        "query_prefix": "",
        "max_tokens": 512,
        "model": "BAAI/bge-m3",
    },
    "multilingual-e5-small": {
        "dimensions": 384,
        "pooling": "mean",
        "document_prefix": "passage: ",
        "query_prefix": "query: ",
        "max_tokens": 512,
        "model": "intfloat/multilingual-e5-small",
    },
}


def assets_for(name: str, directory: Path) -> EmbeddingAssets:
    """The named asset set, validated against the files actually present."""
    if name not in KNOWN_ASSETS:
        raise EmbeddingError("unknown_embedding_asset")
    assets = EmbeddingAssets(directory=directory, **KNOWN_ASSETS[name])
    assets.validate()
    return assets


@dataclass
class OnnxEmbedder:
    """An :class:`~custom_memory_engine.semantic.contracts.Embedder` backed by one frozen ONNX file.

    Loaded lazily: constructing it reads nothing, so a process that never embeds (a unit test, the host's health check)
    pays nothing, and a deployment that misconfigured the path finds out at the first embed rather than at start-up
    with no way to serve reads either.
    """

    assets: EmbeddingAssets
    threads: int = 0  # 0 = onnxruntime's default, which respects cgroup limits
    _session: Any = field(default=None, repr=False, compare=False)
    _tokenizer: Any = field(default=None, repr=False, compare=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False, compare=False)

    @property
    def model(self) -> str:
        return self.assets.model

    @property
    def dimensions(self) -> int:
        return self.assets.dimensions

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
            str(self.assets.directory / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._tokenizer = Tokenizer.from_file(str(self.assets.directory / "tokenizer.json"))
        self._tokenizer.enable_truncation(max_length=self.assets.max_tokens)
        logger.info("embedding_model_loaded", model=self.assets.model, dimensions=self.dimensions)

    async def embed(self, texts: Sequence[str], *, query: bool = False) -> Sequence[Sequence[float]]:
        """Vectors for ``texts``, L2-normalised. ``query`` selects the query prefix the model family expects."""
        if not texts:
            return ()
        async with self._lock:  # one session, one thread pool: concurrent encodes contend anyway
            return await asyncio.to_thread(self._encode, list(texts), query)

    def _encode(self, texts: list[str], query: bool) -> list[list[float]]:
        self._load()
        prefix = self.assets.query_prefix if query else self.assets.document_prefix
        vectors: list[list[float]] = []
        # One text at a time: the batch would need padding, and padding changes a pooled vector at the 3rd decimal.
        # The cost is throughput, and the slow lane is not throughput-bound.
        for text in texts:
            encoded = self._tokenizer.encode(prefix + text)
            ids = np.asarray([encoded.ids], dtype=np.int64)
            mask = np.asarray([encoded.attention_mask], dtype=np.int64)
            feeds = {"input_ids": ids, "attention_mask": mask}
            names = {item.name for item in self._session.get_inputs()}
            if "token_type_ids" in names:  # BERT-family assets want it; BGE-M3's graph does not declare it
                feeds["token_type_ids"] = np.zeros_like(ids)
            hidden = self._session.run(None, feeds)[0]
            vector = self._pool(hidden, mask)
            vectors.append(vector)
        return vectors

    def _pool(self, hidden: np.ndarray, mask: np.ndarray) -> list[float]:
        if self.assets.pooling == "cls":
            pooled = hidden[:, 0, :]
        else:
            weights = mask[:, :, None].astype(np.float32)
            pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        norm = float(np.linalg.norm(pooled, axis=1)[0])
        vector = (pooled[0] / norm) if norm > 0 else pooled[0]
        if vector.shape[0] != self.assets.dimensions:
            # A model whose hidden size disagrees with the declared asset would be stored in the wrong slot and then
            # indexed by an HNSW graph of a different width. Refused here, before any row is written.
            raise EmbeddingError("embedding_dimensions_mismatch")
        return [round(float(item), 6) for item in vector]


def assets_from_environment(environment: dict[str, str]) -> EmbeddingAssets | None:
    """``CME_EMBEDDING_ASSET`` + ``CME_EMBEDDING_DIR``: the only place the deployment names its vectors."""
    name = environment.get("CME_EMBEDDING_ASSET", "").strip()
    if not name:
        return None
    directory = Path(environment.get("CME_EMBEDDING_DIR", "") or f".model-cache/{name}")
    return assets_for(name, directory)


__all__ = [
    "EmbeddingAssets",
    "EmbeddingError",
    "KNOWN_ASSETS",
    "OnnxEmbedder",
    "assets_for",
    "assets_from_environment",
    "describe",
    "model_fingerprint",
]


def model_fingerprint(assets: EmbeddingAssets) -> str:
    """A short digest of the model bytes: what a re-embedding rollout compares to decide a slot is stale.

    Content-addressed on the two files that determine the vector (`model.onnx` and `tokenizer.json`), not on the
    directory name: renaming a cached directory must not look like a different model.
    """
    import hashlib

    digest = hashlib.sha256()
    for name in ("model.onnx", "tokenizer.json"):
        with (assets.directory / name).open("rb") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
    return digest.hexdigest()[:32]


def describe(assets: EmbeddingAssets) -> str:
    """A one-line, content-free description for a log or a report."""
    return json.dumps(
        {
            "model": assets.model,
            "dimensions": assets.dimensions,
            "pooling": assets.pooling,
            "query_prefix": bool(assets.query_prefix),
        },
        sort_keys=True,
    )
