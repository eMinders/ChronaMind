"""The integrations layer's contract, without a network: what may name a model, and what a client must do.

Three properties are pinned here, each of which has already been wrong once or would be expensive to get wrong:

* **No model name outside `integrations/`.** The engine takes injected protocols (``V2-D88``); a model id in a
  decision, a migration or the read path would couple a rebuild to a vendor's naming.
* **The protocol follows the family, and the override wins.** The gateway refuses the wrong shape with
  ``400 protocol_mismatch``; guessing badly costs one failed call, not a silent retry against a billed endpoint.
* **A parameter the provider refuses is dropped and remembered**, not retried forever: ``temperature`` and
  ``response_format`` are refused by whole model families.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from custom_memory_engine.integrations.anthropic_messages import MessagesClient
from custom_memory_engine.integrations.onnx_embedder import (
    KNOWN_ASSETS,
    EmbeddingAssets,
    EmbeddingError,
    assets_for,
)
from custom_memory_engine.integrations.openai_compatible import ChatClient
from custom_memory_engine.integrations.provider import client_for, protocol_for
from custom_memory_engine.integrations.wire import ModelError, parse_json_text

#: The only model names allowed outside `integrations/`, each with the reason it must exist. Extending this list is a
#: decision: `DEFAULT_EMBEDDING_MODEL` and `retrieval`'s default are the *legacy* `vector(384)` baseline's label,
#: written into `memory_embedding.embedding_model` so an old row can be read back; they are not a selection.
ALLOWED_MODEL_NAMES = {
    "ingest.py": ("BAAI/bge-small-en-v1.5",),
    "retrieval.py": ("BAAI/bge-small-en-v1.5",),
}


def test_no_model_name_leaks_outside_the_integrations_package() -> None:
    """A model id is configuration. `src/` may name one only in the adapters that exist to name one, or in the
    allowlist above with a reason."""
    root = Path(__file__).resolve().parents[1] / "src" / "custom_memory_engine"
    suspects = ("gpt-", "claude-", "qwen", "mistral", "deepseek", "bge-", "e5-small", "text-embedding")
    offenders: list[str] = []
    for path in root.rglob("*.py"):
        if "integrations" in path.parts:
            continue
        text = path.read_text(encoding="utf-8")
        for needle in suspects:
            if needle in text.lower():
                allowed = ALLOWED_MODEL_NAMES.get(path.name, ())
                # the allowlist is per-file and exact: a *new* name in an allowed file is still a finding
                if not all(item.lower() in text.lower() for item in allowed):
                    offenders.append(f"{path.name}:{needle}")
                elif needle not in " ".join(allowed) and not any(item.lower().startswith(needle) for item in allowed):
                    offenders.append(f"{path.name}:{needle}")
    assert offenders == [], offenders


def test_the_protocol_follows_the_model_family_and_an_override_wins() -> None:
    assert protocol_for("claude-haiku-5-5") == "anthropic"
    assert protocol_for("gpt-6.2-sol") == "openai"
    assert protocol_for("anthropic/claude-sonnet-5-5") == "anthropic"
    assert protocol_for("gpt-6.2-sol", explicit="anthropic") == "anthropic"
    assert protocol_for("anything", explicit="openai") == "openai"


def test_client_for_builds_the_client_the_protocol_implies() -> None:
    openai = client_for(base_url="http://x/v1", api_key="k", model="gpt-6.2-sol")
    anthropic = client_for(base_url="http://x/v1", api_key="k", model="claude-haiku-5-5")
    assert isinstance(openai, ChatClient)
    assert isinstance(anthropic, MessagesClient)


def test_a_key_never_appears_in_a_repr() -> None:
    """A dataclass repr lands in tracebacks and logs; the key must not be in it."""
    secret = "sk-very-secret-value-0123456789"  # pragma: allowlist secret (a fixture string, not a credential)
    assert secret not in repr(ChatClient(base_url="http://x", api_key=secret, model="m"))
    assert secret not in repr(MessagesClient(base_url="http://x", api_key=secret, model="m"))


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1}', {"a": 1}),
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('Here is the answer: {"a": 1} — hope it helps.', {"a": 1}),
        ("", None),
    ],
)
def test_the_json_parse_tolerates_what_models_do_and_nothing_more(text: str, expected: object) -> None:
    assert parse_json_text(text) == expected


def test_unparseable_output_is_a_typed_refusal() -> None:
    with pytest.raises(ModelError) as refused:
        parse_json_text("I cannot help with that.")
    assert refused.value.code == "model_output_not_json"


async def test_a_refused_parameter_is_dropped_and_remembered() -> None:
    """Some models refuse `temperature`; the client learns it once instead of each deployment hard-coding a matrix."""
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        if "temperature" in body:
            return httpx.Response(
                400, json={"error": {"message": "Omit temperature for this model.", "code": "invalid_request_error"}}
            )
        return httpx.Response(
            200,
            json={
                "model": "m",
                "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 4},
            },
        )

    client = ChatClient(
        base_url="http://x", api_key="k", model="m", transport=httpx.MockTransport(handler), json_mode=False
    )
    document = await client.complete_json([{"role": "user", "content": "hi"}])
    await client.aclose()

    assert document == {"ok": True}
    assert "temperature" in seen[0]  # the first attempt carried it
    assert all("temperature" not in body for body in seen[1:])  # and it was never sent again
    assert client.capabilities == {"response_format"}  # remembered on the client, not hard-coded


async def test_the_messages_client_sends_the_anthropic_shape() -> None:
    """`system` is a top-level field and the key travels in `x-api-key`: this is not a chat completion."""
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["path"] = request.url.path
        captured["key"] = request.headers.get("x-api-key")
        captured["version"] = request.headers.get("anthropic-version")
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "model": "claude-x",
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 7, "output_tokens": 2},
                "stop_reason": "end_turn",
            },
        )

    client = MessagesClient(
        base_url="http://x/v1", api_key="secret", model="claude-x", transport=httpx.MockTransport(handler)
    )
    completion = await client.complete(
        [{"role": "system", "content": "SYS"}, {"role": "user", "content": "hi"}], max_tokens=64
    )
    await client.aclose()

    assert captured["path"] == "/v1/messages"
    assert captured["key"] == "secret"
    assert captured["version"]
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["system"].startswith("SYS") and "JSON" in body["system"]  # JSON is asked for in the prompt
    assert body["messages"] == [{"role": "user", "content": "hi"}]  # and never as a system message
    assert (completion.text, completion.prompt_tokens, completion.completion_tokens) == ('{"ok": true}', 7, 2)


# ------------------------------------------------------------------------------------------------ the embedder


def test_the_known_assets_match_the_slot_map() -> None:
    """A dimension the schema cannot index would be stored in no slot and searched by nothing."""
    from custom_memory_engine.engine.hybrid import DENSE_SIMILARITY_FLOOR, EMBEDDING_SLOTS

    assert KNOWN_ASSETS["bge-m3"]["dimensions"] in EMBEDDING_SLOTS
    # 384 fits the legacy `vector(384)` column, which is why the small model is usable at all
    assert KNOWN_ASSETS["multilingual-e5-small"]["dimensions"] == 384
    assert 0 < DENSE_SIMILARITY_FLOOR < 1


def test_an_asset_that_disagrees_with_its_files_is_refused(tmp_path: Path) -> None:
    assets = EmbeddingAssets(directory=tmp_path, dimensions=1024, model="x", pooling="mean")
    with pytest.raises(EmbeddingError) as refused:
        assets.validate()
    assert refused.value.code == "missing_model_onnx"


def test_an_unknown_asset_name_is_refused_rather_than_guessed(tmp_path: Path) -> None:
    with pytest.raises(EmbeddingError) as refused:
        assets_for("some-model", tmp_path)
    assert refused.value.code == "unknown_embedding_asset"
