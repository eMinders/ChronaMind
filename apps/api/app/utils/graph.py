"""This file contains the graph utilities for the application."""

import threading
import time

import tiktoken
from langchain_core.messages import BaseMessage
from langchain_core.messages import trim_messages as _trim_messages

from app.core.config import settings
from app.core.logging import logger
from app.schemas import Message

# The tiktoken encoding is loaded on first use, not at import: tiktoken downloads its BPE file on a cold cache, so an
# import-time load made importing the app (server start-up, the test suite) depend on outbound network access, and a
# failed download crashed start-up. Only a successfully loaded encoding is cached; after a failure, token counts fall
# back to an estimate and the load is retried once the cooldown has passed.
_ENCODING_RETRY_COOLDOWN_SECONDS = 300.0
_CHARS_PER_TOKEN_ESTIMATE = 4
_encoding: tiktoken.Encoding | None = None
_encoding_failed_at: float | None = None
_encoding_lock = threading.Lock()


def _load_encoding(model: str) -> tiktoken.Encoding:
    """Return the tiktoken encoding for ``model``, or ``cl100k_base`` when tiktoken does not know the model."""
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding("cl100k_base")


def _get_encoding() -> tiktoken.Encoding | None:
    """Return the cached encoding, loading it if needed; ``None`` while it cannot be loaded (estimate instead)."""
    global _encoding, _encoding_failed_at
    if _encoding is not None:
        return _encoding
    with _encoding_lock:
        if _encoding is not None:
            return _encoding
        if (
            _encoding_failed_at is not None
            and time.monotonic() - _encoding_failed_at < _ENCODING_RETRY_COOLDOWN_SECONDS
        ):
            return None
        try:
            _encoding = _load_encoding(settings.DEFAULT_LLM_MODEL)
        except Exception:
            _encoding_failed_at = time.monotonic()
            logger.exception(
                "tiktoken_encoding_load_failed_using_estimate",
                model=settings.DEFAULT_LLM_MODEL,
                retry_after_seconds=_ENCODING_RETRY_COOLDOWN_SECONDS,
            )
            return None
        _encoding_failed_at = None
        return _encoding


def _count_text_tokens(text: str, encoding: tiktoken.Encoding | None) -> int:
    """Count the tokens of ``text`` exactly, or estimate them (about 4 characters a token) without an encoding."""
    if encoding is None:
        return -(-len(text) // _CHARS_PER_TOKEN_ESTIMATE)
    return len(encoding.encode(text))


def _count_tokens_tiktoken(messages: list) -> int:
    """Count tokens locally using tiktoken — no API call needed."""
    encoding = _get_encoding()
    num_tokens = 0
    for message in messages:
        # Every message has overhead tokens for role/name
        num_tokens += 4
        if isinstance(message, dict):
            for _, value in message.items():
                if isinstance(value, str):
                    num_tokens += _count_text_tokens(value, encoding)
        elif isinstance(message, BaseMessage):
            content = message.content
            if isinstance(content, str):
                num_tokens += _count_text_tokens(content, encoding)
            elif isinstance(content, list):
                for block in content:
                    if isinstance(block, str):
                        num_tokens += _count_text_tokens(block, encoding)
                    elif isinstance(block, dict) and "text" in block:
                        num_tokens += _count_text_tokens(block["text"], encoding)
    num_tokens += 2  # every reply is primed with assistant
    return num_tokens


def dump_messages(messages: list[Message] | list[Message | BaseMessage]) -> list[dict]:
    """Dump the messages to a list of dictionaries.

    Args:
        messages: The messages to dump (app messages, or LangChain messages after trimming).

    Returns:
        list[dict]: The dumped messages.
    """
    return [message.model_dump() for message in messages]


def extract_text_content(content: str | list) -> str:
    """Extract plain text from an LLM content value.

    Handles both the simple string format and the structured block list returned
    by GPT-5 / Responses API models:
        [{'type': 'reasoning', ...}, {'type': 'text', 'text': '...'}]

    Args:
        content: Raw content from a LangChain BaseMessage.

    Returns:
        Plain text string (empty string when nothing extractable is present).
    """
    if isinstance(content, str):
        return content

    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            if block.get("type") == "text":
                parts.append(block.get("text", ""))
            elif block.get("type") == "reasoning":
                logger.debug(
                    "reasoning_block_received",
                    reasoning_id=block.get("id"),
                    has_summary=bool(block.get("summary")),
                )
    return "".join(parts)


def process_llm_response(response: BaseMessage) -> BaseMessage:
    """Normalise a raw LLM response so that ``response.content`` is always a plain string, regardless of the provider's content format.

    Args:
        response: The raw response from the LLM.

    Returns:
        The same BaseMessage instance with ``content`` set to a plain string.
    """
    if isinstance(response.content, list):
        response.content = extract_text_content(response.content)
        logger.debug(
            "processed_structured_content",
            content_block_count=len(response.content),
            extracted_length=len(response.content),
        )
    return response


def prepare_messages(messages: list[Message], system_prompt: str) -> list[Message | BaseMessage]:
    """Prepare the messages for the LLM.

    Args:
        messages (list[Message]): The messages to prepare.
        system_prompt (str): The system prompt to use.

    Returns:
        The system prompt followed by the trimmed history (LangChain messages once trimming has run).
    """
    try:
        trimmed_messages = _trim_messages(
            dump_messages(messages),
            strategy="last",
            token_counter=_count_tokens_tiktoken,
            max_tokens=settings.MAX_TOKENS,
            start_on="human",
            include_system=False,
            allow_partial=False,
        )
    except ValueError as e:
        # Handle unrecognized content blocks (e.g., reasoning blocks from GPT-5)
        if "Unrecognized content block type" in str(e):
            logger.warning(
                "token_counting_failed_skipping_trim",
                error=str(e),
                message_count=len(messages),
            )
            # Skip trimming and return all messages
            trimmed_messages = messages
        else:
            raise

    prepared: list[Message | BaseMessage] = [Message(role="system", content=system_prompt)]
    return prepared + list(trimmed_messages)
