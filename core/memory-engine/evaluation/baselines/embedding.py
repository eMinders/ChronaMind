"""Frozen BGE ONNX CLS embeddings, with complete offset chunks and exact input IDs."""

import importlib
import importlib.metadata
import json
from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from evaluation.baselines import assets
from evaluation.baselines.contracts import QUERY_PREFIX, Embeddings
from evaluation.harness.models import Usage


class OnnxBgeEmbedder:
    """No import/download/inference at construction; all files are local and pinned.

    Each chunk takes CLS then L2. Long single inputs average all normalized chunk
    vectors and L2 again; B2 stores separate chunks by calling chunks first.
    Same-length batches avoid padding, so accounting includes every dispatched ID,
    including both special tokens and every repeated query-prefix token.
    """

    inference_policy = "cls-l2;mean-chunk-vectors-l2;same-length-batches-16-v1"

    def __init__(self, assets_directory: Path) -> None:
        self._directory = assets_directory
        self._assets: dict[str, bytes] | None = None
        self._tokenizer: Any = None
        self._session: Any = None
        self._cls_id = 0
        self._sep_id = 0

    def _load_tokenizer(self) -> Any:
        if self._tokenizer is None:
            self._assets = assets.verify_assets(self._directory)
            if importlib.metadata.version("tokenizers") != "0.23.3":
                raise ValueError("embedding_tokenizer_package_version_mismatch")
            config = json.loads(self._assets["config.json"])
            tokenizer_config = json.loads(self._assets["tokenizer_config.json"])
            pooling = json.loads(self._assets["1_Pooling/config.json"])
            specials = json.loads(self._assets["special_tokens_map.json"])
            if (
                config.get("hidden_size") != 384
                or config.get("max_position_embeddings") != 512
                or config.get("model_type") != "bert"
                or tokenizer_config.get("model_max_length") != 512
                or pooling.get("pooling_mode_cls_token") is not True
                or pooling.get("pooling_mode_mean_tokens") is not False
            ):
                raise ValueError("asset_embedding_configuration_mismatch")
            module = importlib.import_module("tokenizers")
            tokenizer = module.Tokenizer.from_str(self._assets["tokenizer.json"].decode("utf-8"))
            tokenizer.no_truncation()
            tokenizer.no_padding()
            self._cls_id = tokenizer.token_to_id(specials["cls_token"])
            self._sep_id = tokenizer.token_to_id(specials["sep_token"])
            if tokenizer.encode("", add_special_tokens=True).ids != [self._cls_id, self._sep_id]:
                raise ValueError("asset_special_token_policy_mismatch")
            self._tokenizer = tokenizer
        return self._tokenizer

    def _split(self, text: str, *, query: bool) -> tuple[str, ...]:
        tokenizer = self._load_tokenizer()
        prefix = QUERY_PREFIX if query else ""
        capacity = 510 - len(tokenizer.encode(prefix, add_special_tokens=False).ids)
        if capacity < 1:
            raise ValueError("query_prefix_exceeds_embedding_capacity")
        remaining = text
        chunks: list[str] = []
        while True:
            if len(tokenizer.encode(prefix + remaining, add_special_tokens=True).ids) <= 512:
                chunks.append(remaining)
                break
            raw = tokenizer.encode(remaining, add_special_tokens=False)
            offsets = raw.offsets
            if len(offsets) <= capacity:
                raise ValueError("embedding_chunk_boundary_unavailable")
            boundary = offsets[capacity][0]
            if not 0 < boundary <= len(remaining):
                raise ValueError("embedding_chunk_boundary_invalid")
            # Offsets refer to the original Unicode text. Retokenize the slice:
            # cutting a wordpiece can change its segmentation. Shrink only the
            # boundary; never drop whitespace, normalization or original bytes.
            while len(tokenizer.encode(prefix + remaining[:boundary], add_special_tokens=True).ids) > 512:
                candidates = [start for start, _ in offsets[:capacity] if 0 < start < boundary]
                if not candidates:
                    raise ValueError("embedding_chunk_does_not_fit")
                boundary = candidates[-1]
            chunks.append(remaining[:boundary])
            remaining = remaining[boundary:]
        return tuple(chunks)

    def chunks(self, text: str) -> tuple[str, ...]:
        return self._split(text, query=False)

    def _plan(self, texts: Sequence[str], *, query: bool) -> list[tuple[int, Any]]:
        tokenizer = self._load_tokenizer()
        prefix = QUERY_PREFIX if query else ""
        result: list[tuple[int, Any]] = []
        for owner, text in enumerate(texts):
            if not isinstance(text, str):
                raise ValueError("embedding_text_required")
            for chunk in self._split(text, query=query):
                encoding = tokenizer.encode(prefix + chunk, add_special_tokens=True)
                if (
                    not 2 <= len(encoding.ids) <= 512
                    or encoding.ids[0] != self._cls_id
                    or encoding.ids[-1] != self._sep_id
                ):
                    raise ValueError("embedding_input_token_policy_failed")
                result.append((owner, encoding))
        return result

    def token_count(self, texts: Sequence[str], *, query: bool = False) -> int:
        return sum(len(encoding.ids) for _, encoding in self._plan(texts, query=query))

    def _load_session(self) -> Any:
        if self._session is None:
            self._load_tokenizer()
            if importlib.metadata.version("onnxruntime") != "1.31.0":
                raise ValueError("embedding_runtime_package_version_mismatch")
            runtime = importlib.import_module("onnxruntime")
            runtime.disable_telemetry_events()
            options = runtime.SessionOptions()
            options.intra_op_num_threads = 1
            options.inter_op_num_threads = 1
            options.execution_mode = runtime.ExecutionMode.ORT_SEQUENTIAL
            options.enable_mem_pattern = False
            assert self._assets is not None
            # Bytes prevent external-data paths from loading arbitrary adjacent files.
            session = runtime.InferenceSession(
                self._assets["onnx/model.onnx"], sess_options=options, providers=["CPUExecutionProvider"]
            )
            if session.get_providers() != ["CPUExecutionProvider"]:
                raise ValueError("embedding_cpu_provider_required")
            inputs = session.get_inputs()
            if {item.name for item in inputs} != {"input_ids", "attention_mask", "token_type_ids"}:
                raise ValueError("embedding_onnx_inputs_mismatch")
            if any(item.type != "tensor(int64)" or len(item.shape) != 2 for item in inputs):
                raise ValueError("embedding_onnx_input_type_mismatch")
            outputs = session.get_outputs()
            if (
                len(outputs) != 1
                or outputs[0].name != "last_hidden_state"
                or outputs[0].type != "tensor(float)"
                or len(outputs[0].shape) != 3
                or outputs[0].shape[2] != 384
            ):
                raise ValueError("embedding_onnx_output_mismatch")
            self._session = session
            self._assets.pop("onnx/model.onnx")
        return self._session

    def embed(self, texts: Sequence[str], *, query: bool = False) -> Embeddings:
        plan = self._plan(texts, query=query)
        if not plan:
            return Embeddings(vectors=(), usage=Usage.zero())
        session = self._load_session()
        np = importlib.import_module("numpy")
        buckets: dict[int, list[tuple[int, Any]]] = defaultdict(list)
        for owner, encoding in plan:
            buckets[len(encoding.ids)].append((owner, encoding))
        vectors: dict[int, list[Any]] = defaultdict(list)
        for rows in buckets.values():
            for start in range(0, len(rows), 16):
                batch = rows[start : start + 16]
                feed = {
                    "input_ids": np.asarray([encoding.ids for _, encoding in batch], dtype=np.int64),
                    "attention_mask": np.asarray([encoding.attention_mask for _, encoding in batch], dtype=np.int64),
                    "token_type_ids": np.asarray([encoding.type_ids for _, encoding in batch], dtype=np.int64),
                }
                output = session.run(["last_hidden_state"], feed)[0]
                if output.shape != (len(batch), len(batch[0][1].ids), 384) or not np.isfinite(output).all():
                    raise ValueError("embedding_onnx_output_invalid")
                cls = output[:, 0, :].astype(np.float64)
                norms = np.linalg.norm(cls, axis=1, keepdims=True)
                if np.any(norms <= 0):
                    raise ValueError("embedding_zero_vector")
                for (owner, _), vector in zip(batch, cls / norms, strict=True):
                    vectors[owner].append(vector)
        result: list[tuple[float, ...]] = []
        for owner in range(len(texts)):
            aggregate = np.mean(np.stack(vectors[owner]), axis=0)
            norm = np.linalg.norm(aggregate)
            if not np.isfinite(norm) or norm <= 0:
                raise ValueError("embedding_aggregate_invalid")
            result.append(tuple(float(value) for value in aggregate / norm))
        return Embeddings(
            vectors=tuple(result),
            usage=Usage(
                input_tokens=0,
                output_tokens=0,
                embedding_tokens=sum(len(row.ids) for _, row in plan),
                cost_usd=Decimal(0),
            ),
        )
