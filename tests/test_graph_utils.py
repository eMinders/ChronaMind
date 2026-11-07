"""Token counting never needs the network: the encoding loads lazily and a failed load degrades to an estimate."""

from collections.abc import Iterator

import pytest
import tiktoken

from app.utils import graph


class _FakeEncoding:
    """Encodes one token per whitespace-separated word, so counts are exact and obvious."""

    def encode(self, text: str) -> list[int]:
        return [0] * len(text.split())


@pytest.fixture(autouse=True)
def _reset_encoding_cache() -> Iterator[None]:
    graph._encoding = None
    graph._encoding_failed_at = None
    yield
    graph._encoding = None
    graph._encoding_failed_at = None


def test_importing_the_module_loads_no_encoding() -> None:
    # The autouse fixture cleared the cache; nothing at import or collection time repopulated it.
    assert graph._encoding is None


def test_a_loaded_encoding_is_cached_and_used(monkeypatch: pytest.MonkeyPatch) -> None:
    loads: list[str] = []

    def fake_load(model: str) -> _FakeEncoding:
        loads.append(model)
        return _FakeEncoding()

    monkeypatch.setattr(graph, "_load_encoding", fake_load)

    messages = [{"role": "user", "content": "three word message"}]
    # role "user" (1) + content (3) + per-message overhead (4) + reply priming (2)
    assert graph._count_tokens_tiktoken(messages) == 10
    assert graph._count_tokens_tiktoken(messages) == 10
    assert len(loads) == 1


def test_a_failed_load_falls_back_to_an_estimate_and_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = 0

    def failing_load(model: str) -> tiktoken.Encoding:
        nonlocal attempts
        attempts += 1
        raise ConnectionError("no route to the BPE file")

    monkeypatch.setattr(graph, "_load_encoding", failing_load)

    messages = [{"role": "user", "content": "x" * 40}]
    # ceil(4 / 4) for the role + ceil(40 / 4) for the content + 4 overhead + 2 priming
    assert graph._count_tokens_tiktoken(messages) == 17
    assert graph._encoding is None

    # Within the cooldown the load is not retried on every request.
    graph._count_tokens_tiktoken(messages)
    assert attempts == 1

    # After the cooldown it is retried, and a success is cached.
    graph._encoding_failed_at = -graph._ENCODING_RETRY_COOLDOWN_SECONDS
    monkeypatch.setattr(graph, "_load_encoding", lambda model: _FakeEncoding())
    assert graph._count_tokens_tiktoken(messages) == 8
    assert graph._encoding is not None
    assert graph._encoding_failed_at is None


def test_prepare_messages_prepends_the_system_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(graph, "_load_encoding", lambda model: _FakeEncoding())

    prepared = graph.prepare_messages([graph.Message(role="user", content="hello there")], "be helpful")

    assert prepared[0].content == "be helpful"
    assert [message.content for message in prepared[1:]] == ["hello there"]
