"""Local asset integrity, exact token accounting and real ONNX CLS integration."""

import base64
import hashlib
import importlib.metadata
import json
import math
import zlib
from pathlib import Path

import pytest

from evaluation.baselines import assets
from evaluation.baselines.contracts import QUERY_PREFIX
from evaluation.baselines.embedding import OnnxBgeEmbedder
from evaluation.baselines.tokenization import O200K_SHA256, TiktokenTokenizer


def test_constructors_are_local_lazy_and_bad_assets_fail_only_on_use(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_import(name: str) -> None:
        raise AssertionError(name)

    monkeypatch.setattr("importlib.import_module", no_import)
    embedder = OnnxBgeEmbedder(tmp_path / "missing")
    tokenizer = TiktokenTokenizer(tmp_path / "missing-bpe")
    assert tokenizer.identity == f"tiktoken-0.14.0:o200k_base:{O200K_SHA256}"
    with pytest.raises(ValueError, match="asset"):
        embedder.chunks("fictional text")
    with pytest.raises(ValueError, match="asset"):
        tokenizer.encode("fictional text")


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    tokenizers = pytest.importorskip("tokenizers")
    # Generated locally from a tiny ONNX graph, no external model data.
    model_bytes = zlib.decompress(base64.b64decode(TINY_ONNX))
    directory = tmp_path / "assets"
    (directory / "onnx").mkdir(parents=True)
    tokenizer = tokenizers.Tokenizer(
        tokenizers.models.WordLevel(
            {"[PAD]": 0, "[UNK]": 1, "[CLS]": 2, "[SEP]": 3, "alpha": 4, "beta": 5}, unk_token="[UNK]"
        )
    )
    tokenizer.pre_tokenizer = tokenizers.pre_tokenizers.Whitespace()
    tokenizer.post_processor = tokenizers.processors.TemplateProcessing(
        single="[CLS] $A [SEP]", special_tokens=[("[CLS]", 2), ("[SEP]", 3)]
    )
    tokenizer.save(str(directory / "tokenizer.json"))
    (directory / "onnx/model.onnx").write_bytes(model_bytes)
    (directory / "config.json").write_text(
        json.dumps({"model_type": "bert", "hidden_size": 384, "max_position_embeddings": 512})
    )
    (directory / "tokenizer_config.json").write_text(json.dumps({"model_max_length": 512}))
    (directory / "special_tokens_map.json").write_text(
        json.dumps({"pad_token": "[PAD]", "cls_token": "[CLS]", "sep_token": "[SEP]"})
    )
    (directory / "1_Pooling").mkdir()
    (directory / "1_Pooling/config.json").write_text(
        json.dumps({"pooling_mode_cls_token": True, "pooling_mode_mean_tokens": False})
    )
    pins = {
        name: assets.AssetPin(
            hashlib.sha256((directory / name).read_bytes()).hexdigest(), (directory / name).stat().st_size, "0" * 40
        )
        for name in assets.BGE_FILES
    }
    monkeypatch.setattr(assets, "BGE_FILES", pins)
    assets.write_manifest(directory)
    return directory


def test_real_onnx_cls_normalization_prefix_and_exact_input_tokens(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("onnxruntime")
    directory = _fixture(tmp_path, monkeypatch)
    embedder = OnnxBgeEmbedder(directory)
    memory_tokens = embedder.token_count(("alpha beta",))
    query_tokens = embedder.token_count(("alpha beta",), query=True)
    assert memory_tokens == 4  # Two words plus the actual CLS/SEP IDs.
    assert query_tokens > memory_tokens
    memory = embedder.embed(("alpha beta",))
    query = embedder.embed(("alpha beta",), query=True)
    assert memory.usage.embedding_tokens == memory_tokens
    assert query.usage.embedding_tokens == query_tokens
    assert memory.usage.input_tokens == memory.usage.output_tokens == 0
    assert memory.usage.cost_usd == 0
    vector = memory.vectors[0]
    assert len(vector) == 384
    # Fixture token t is [t + sum(input_ids), 1, 0,...]. CLS=2 and sum=14;
    # averaging token positions would produce a different first component.
    assert vector[:2] == pytest.approx((16 / math.sqrt(257), 1 / math.sqrt(257)))
    assert sum(v * v for v in vector) == pytest.approx(1)
    assert embedder.inference_policy == "cls-l2;mean-chunk-vectors-l2;same-length-batches-16-v1"


def test_overflow_retains_unicode_whitespace_and_accounts_every_chunk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pytest.importorskip("onnxruntime")
    embedder = OnnxBgeEmbedder(_fixture(tmp_path, monkeypatch))
    text = " \n" + ("alpha βeta\t" * 620) + "  نهاية \n"
    chunks = embedder.chunks(text)
    assert len(chunks) > 1
    assert "".join(chunks) == text
    for chunk in chunks:
        assert embedder.token_count((chunk,)) <= 512
    expected = sum(embedder.token_count((chunk,)) for chunk in chunks)
    assert embedder.token_count((text,)) == expected
    result = embedder.embed((text, "alpha"), query=False)
    assert result.usage.embedding_tokens == expected + 3
    assert len(result.vectors) == 2
    assert result.usage.embedding_tokens > 512
    assert embedder.embed((text,), query=True).usage.embedding_tokens == embedder.token_count((text,), query=True)
    parts = embedder.embed(chunks).vectors
    expected_vector = tuple(sum(row[index] for row in parts) / len(parts) for index in range(384))
    norm = math.sqrt(sum(value * value for value in expected_vector))
    assert result.vectors[0] == pytest.approx(tuple(value / norm for value in expected_vector))


def test_manifest_tamper_revision_and_link_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _fixture(tmp_path, monkeypatch)
    (directory / "config.json").write_text("{}")
    with pytest.raises(ValueError, match="asset"):
        OnnxBgeEmbedder(directory).chunks("alpha")
    directory = _fixture(tmp_path / "second", monkeypatch)
    manifest = json.loads((directory / "manifest.json").read_text())
    manifest["revision"] = "0" * 40
    (directory / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="asset"):
        OnnxBgeEmbedder(directory).chunks("alpha")


def test_tokenizer_bpe_hash_is_checked_before_parsing(tmp_path: Path) -> None:
    bad = tmp_path / "bpe"
    bad.write_bytes(b"QQ== 0\n")
    with pytest.raises(ValueError, match="asset"):
        TiktokenTokenizer(bad).encode("alpha <|endoftext|>")


def test_package_version_rejected_before_inference(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    directory = _fixture(tmp_path, monkeypatch)
    original = importlib.metadata.version
    monkeypatch.setattr(
        importlib.metadata, "version", lambda package: "0.0.0" if package == "tokenizers" else original(package)
    )
    with pytest.raises(ValueError, match="version"):
        OnnxBgeEmbedder(directory).chunks("alpha")


def test_query_prefix_exact_ids_and_batch_bound_use_real_onnx(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("onnxruntime")
    tokenizers = pytest.importorskip("tokenizers")
    directory = _fixture(tmp_path, monkeypatch)
    embedder = OnnxBgeEmbedder(directory)
    native = tokenizers.Tokenizer.from_file(str(directory / "tokenizer.json"))
    expected = native.encode(QUERY_PREFIX + "alpha beta").ids
    session = embedder._load_session()
    batches = []

    class RecordingSession:
        def run(self, names, feed):
            batches.append(feed["input_ids"].shape[0])
            assert feed["input_ids"].tolist() == [expected] * batches[-1]
            return session.run(names, feed)

    embedder._session = RecordingSession()
    result = embedder.embed(("alpha beta",) * 33, query=True)
    assert batches == [16, 16, 1]
    assert result.usage.embedding_tokens == 33 * len(expected)
    assert len(result.vectors) == 33


def test_links_and_escaping_paths_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ValueError, match="asset_path"):
        assets.local_path(tmp_path, "../escape")
    target = tmp_path / "linked"
    target.write_text("fiction")
    original = Path.is_symlink
    monkeypatch.setattr(Path, "is_symlink", lambda self: self == target or original(self))
    with pytest.raises(ValueError, match="asset_link"):
        assets.verified_bytes(target, hashlib.sha256(b"fiction").hexdigest())


TINY_ONNX = (
    "eJztl71OwzAQx5s2EOdUILUE4ksIdeAjiJGBLogwdwGxdInc+EpN0yTEjlqY+gQsvABPwDOSprTlS+oD1L/Jd77z3zed/gQa"
    "H/twDLaIkkz5gktqd8KYFce6ecOkcm0oq3jbeDfKcAnzW7Bk1mdDIamVxgM/D+r2LfIswLus724A6SEmXPTlpPX0R+tXB10P"
    "4kjhUGUsLBQr15zDGfxKg1noEBwmLOLI6/Z9JJ8yxBeEI5ilwRqgeOiqfIYkjR8xUHnpapOpZhaO9WdJMNuCSVoL8/H8ruAc"
    "I18qprDQp1tj9TQOQ+R+EEq/I/KfpOi+VYlBRhXHqI+qpdLoqqTRaDSapcOb7hr3tTrfCWP0XtBoNJplwytshbtGDMdqGGWv"
    "cC2uMwkNb+qXWiff7dYe7BCLboJFV9pMBV0ASiTm5iYKsHUO60wpjJSII7/PZG9huYp7uZlRzwkufL19AX8dED2EA2LQ3X87"
    "oJKvOs+EklP7BHsfpRg="
)
