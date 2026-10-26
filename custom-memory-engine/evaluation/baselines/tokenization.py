"""Local, immutable o200k_base tokenization without tiktoken's download registry."""

import base64
import importlib
import importlib.metadata
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from evaluation.baselines.assets import O200K_SHA256, verified_bytes
from evaluation.baselines.contracts import Message

O200K_PATTERN = "|".join(
    [
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
        r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?",
        r"\p{N}{1,3}",
        r" ?[^\s\p{L}\p{N}]+[\r\n/]*",
        r"\s*[\r\n]+",
        r"\s+(?!\S)",
        r"\s+",
    ]
)


class TiktokenTokenizer:
    def __init__(self, encoding_file: Path) -> None:
        self._path = encoding_file
        self._encoding: Any = None

    @property
    def identity(self) -> str:
        return f"tiktoken-0.14.0:o200k_base:{O200K_SHA256}"

    def _load(self) -> Any:
        if self._encoding is None:
            content = verified_bytes(self._path, O200K_SHA256)
            if importlib.metadata.version("tiktoken") != "0.14.0":
                raise ValueError("tokenizer_package_version_mismatch")
            ranks: dict[bytes, int] = {}
            try:
                for line in content.splitlines():
                    token, rank = line.split()
                    decoded = base64.b64decode(token, validate=True)
                    if decoded in ranks:
                        raise ValueError("duplicate_bpe_token")
                    ranks[decoded] = int(rank)
                if len(ranks) != 199_998 or set(ranks.values()) != set(range(199_998)):
                    raise ValueError("invalid_bpe_ranks")
            except (ValueError, TypeError):
                raise ValueError("asset_bpe_invalid") from None
            tiktoken = importlib.import_module("tiktoken")
            self._encoding = tiktoken.Encoding(
                name="o200k_base",
                pat_str=O200K_PATTERN,
                mergeable_ranks=ranks,
                special_tokens={"<|endoftext|>": 199_999, "<|endofprompt|>": 200_018},
            )
        return self._encoding

    def encode(self, text: str) -> list[int]:
        # Corpus text may contain marker-shaped strings. They are ordinary text.
        return self._load().encode_ordinary(text)

    def decode(self, tokens: Sequence[int]) -> str:
        return self._load().decode(list(tokens))

    def count_messages(self, messages: Sequence[Message]) -> int:
        """Conservative framing estimate, not exact provider billing tokenization.

        Runtime adds another64-token allowance and schema framing separately.
        """
        return 12 + sum(
            12 + len(self.encode(message.role)) + len(self.encode(message.content)) for message in messages
        )
