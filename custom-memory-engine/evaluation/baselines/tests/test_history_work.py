"""History selection preserves exact suffix semantics with bounded tokenization work."""

import os
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import pytest

from evaluation.baselines.context import answer_messages, history_items, join_items, turn_item
from evaluation.baselines.contracts import BaselineSettings, Message, Tokenizer
from evaluation.baselines.tests.helpers import CharacterTokenizer, settings, turn
from evaluation.baselines.tokenization import TiktokenTokenizer
from evaluation.harness.models import QuestionRecord, TurnRecord

CLOCK = datetime(2020, 1, 2, tzinfo=UTC)
QUESTION = QuestionRecord(question_id="q", user_id="user", question="What is known?")


class CountingTokenizer(CharacterTokenizer):
    def __init__(self) -> None:
        self.characters = 0
        self.message_calls = 0

    def encode(self, text: str) -> list[int]:
        self.characters += len(text)
        return super().encode(text)

    def count_messages(self, messages: Sequence[Message]) -> int:
        self.characters += sum(len(message.content) for message in messages)
        self.message_calls += 1
        return super().count_messages(messages)


def _first_fit(
    documents: Sequence[TurnRecord], question: QuestionRecord, selected: BaselineSettings, tokenizer: Tokenizer
):
    """Small independent exhaustive oracle; no assumptions about suffix monotonicity."""
    items = tuple(turn_item(document) for document in documents)
    reserve = selected.request_overhead_tokens + selected.max_output_tokens
    for start in range(len(items) + 1):
        suffix = items[start:]
        count = tokenizer.count_messages(answer_messages(selected, question, join_items(suffix), CLOCK))
        if count + reserve <= selected.context_window_tokens:
            return suffix
    raise AssertionError("fixture question does not fit")


@pytest.mark.parametrize("count", [500, 1000, 2000])
def test_long_history_tokenization_work_is_linear_in_rendered_input(count: int) -> None:
    tokenizer = CountingTokenizer()
    selected = settings()
    documents = tuple(turn(str(index), "X" * 100) for index in range(count))
    result, dropped = history_items(documents, QUESTION, selected, tokenizer, CLOCK)
    total_characters = len(join_items(tuple(turn_item(document) for document in documents)))
    assert tokenizer.message_calls <= 4, "token-counted a separate complete request for each discarded turn"
    assert tokenizer.characters < 8 * total_characters + 10000
    assert result and len(result) < 50 and dropped > 0
    assert result == _first_fit(documents, QUESTION, selected, CharacterTokenizer())


class NonmonotonicTokenizer:
    identity = "fixture-nonmonotonic-request-counts"

    def encode(self, text: str) -> list[int]:
        return [ord(character) for character in text]

    def decode(self, tokens: Sequence[int]) -> str:
        return "".join(chr(token) for token in tokens)

    def count_messages(self, messages: Sequence[Message]) -> int:
        content = messages[-1].content
        for name, count in (("a", 400), ("b", 150), ("c", 300), ("d", 100)):
            if f'"turn_id":"{name}"' in content:
                return count
        return 50


def test_opaque_nonmonotonic_tokenizer_keeps_the_first_fitting_suffix() -> None:
    tokenizer = NonmonotonicTokenizer()
    selected = settings(tokenizer_id=tokenizer.identity, context_window_tokens=2312)
    documents = tuple(turn(name, "Unicode 🌍 العربية") for name in "abcd")
    result, dropped = history_items(documents, QUESTION, selected, tokenizer, CLOCK)
    assert [item.reference_id for item in result] == ["b", "c", "d"]
    assert result == _first_fit(documents, QUESTION, selected, tokenizer)
    assert dropped == len(tokenizer.encode(join_items((turn_item(documents[0]),)))) + 2


def test_question_only_failure_precedes_history_tokenization() -> None:
    tokenizer = CountingTokenizer()
    selected = settings(context_window_tokens=2100)
    with pytest.raises(ValueError, match="question_exceeds_answer_context_window"):
        history_items((turn("unused", "X" * 10000),), QUESTION, selected, tokenizer, CLOCK)
    assert tokenizer.message_calls == 1
    assert tokenizer.characters < 1000


def test_opaque_codec_empty_suffix_diagnostics_subtract_its_empty_encoding() -> None:
    class FramingTokenizer(NonmonotonicTokenizer):
        def encode(self, text: str) -> list[int]:
            return [0, *super().encode(text)]

        def count_messages(self, messages: Sequence[Message]) -> int:
            return 1000 if '"turn_id"' in messages[-1].content else 10

    tokenizer = FramingTokenizer()
    selected = settings(tokenizer_id=tokenizer.identity, context_window_tokens=2200)
    documents = (turn("one", "data 🌍"),)
    result, dropped = history_items(documents, QUESTION, selected, tokenizer, CLOCK)
    assert result == ()
    original = join_items(tuple(turn_item(document) for document in documents))
    assert dropped == len(tokenizer.encode(original)) - len(tokenizer.encode(""))


def test_uncertified_message_counts_fall_back_to_exact_selection() -> None:
    class IncorrectCounter(CharacterTokenizer):
        def history_request_counts(self, messages, user_parts):
            return (0,) * (len(user_parts) - 2)

    documents = tuple(turn(str(index), "X" * 500) for index in range(10))
    selected = settings(context_window_tokens=3200)
    result, _ = history_items(documents, QUESTION, selected, IncorrectCounter(), CLOCK)
    assert result == _first_fit(documents, QUESTION, selected, CharacterTokenizer())


@pytest.fixture
def frozen_tokenizer() -> TiktokenTokenizer:
    directory = os.environ.get("CME_BASELINE_TEST_ASSETS")
    if directory is None:
        pytest.skip("CME_BASELINE_TEST_ASSETS must explicitly name the immutable tokenizer assets")
    pytest.importorskip("tiktoken")
    return TiktokenTokenizer(Path(directory) / "o200k_base.tiktoken")


def test_real_tokenizer_unicode_boundaries_and_exact_windows_match_exhaustive_suffixes(
    frozen_tokenizer: TiktokenTokenizer,
) -> None:
    documents = tuple(
        turn(str(index), content, role="assistant" if index % 2 else "user", speaker="Éva\n🌍")
        for index, content in enumerate(
            ("plain", "\n\n", " \t\n  ", "}///\r\n", "العربية café 🌍", "e\u0301\n", "<|endoftext|>", "\r\nTAIL")
        )
    )
    question = QUESTION.model_copy(
        update={"question": "When? 🌍\n\nQuestion:\n", "mode": "as_of", "as_of_time": CLOCK}
    )
    initial = settings(tokenizer_id=frozen_tokenizer.identity)
    items = tuple(turn_item(document) for document in documents)
    reserve = initial.max_output_tokens + initial.request_overhead_tokens
    empty = frozen_tokenizer.count_messages(answer_messages(initial, question, "", CLOCK)) + reserve
    windows = {empty}
    for start in range(len(items)):
        exact = frozen_tokenizer.count_messages(answer_messages(initial, question, join_items(items[start:]), CLOCK))
        windows.update(exact + reserve + offset for offset in (-1, 0, 1))
    for window in sorted(windows):
        selected = settings(tokenizer_id=frozen_tokenizer.identity, context_window_tokens=max(empty, window))
        result, dropped = history_items(documents, question, selected, frozen_tokenizer, CLOCK)
        assert result == _first_fit(documents, question, selected, frozen_tokenizer)
        assert dropped == max(
            0,
            len(frozen_tokenizer.encode(join_items(items))) - len(frozen_tokenizer.encode(join_items(result))),
        )
        assert (
            frozen_tokenizer.count_messages(answer_messages(selected, question, join_items(result), CLOCK)) + reserve
            <= selected.context_window_tokens
        )


class CountingTiktoken(TiktokenTokenizer):
    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self.characters = 0
        self.message_calls = 0

    def encode(self, text: str) -> list[int]:
        self.characters += len(text)
        return super().encode(text)

    def count_messages(self, messages: Sequence[Message]) -> int:
        self.characters += sum(len(message.content) for message in messages)
        self.message_calls += 1
        return super().count_messages(messages)


@pytest.mark.parametrize("count", [128, 256, 512])
def test_real_frozen_tokenizer_long_history_uses_the_certified_linear_path(
    count: int, frozen_tokenizer: TiktokenTokenizer
) -> None:
    tokenizer = CountingTiktoken(frozen_tokenizer._path)
    selected = settings(tokenizer_id=tokenizer.identity, context_window_tokens=5000)
    documents = tuple(turn(str(index), "Unicode café العربية 🌍!\n" + "X" * 100) for index in range(count))
    result, _ = history_items(documents, QUESTION, selected, tokenizer, CLOCK)
    total_characters = len(join_items(tuple(turn_item(document) for document in documents)))
    assert tokenizer.message_calls <= 4, "production codec fell back to counting every complete suffix"
    assert tokenizer.characters < 8 * total_characters + 10000
    assert 0 < len(result) < count
    assert (
        frozen_tokenizer.count_messages(answer_messages(selected, QUESTION, join_items(result), CLOCK))
        + selected.max_output_tokens
        + selected.request_overhead_tokens
        <= selected.context_window_tokens
    )
