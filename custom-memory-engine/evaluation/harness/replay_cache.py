"""Deterministic slow-path extraction record-and-replay cache (Proposal §9, RL-032)."""

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from math import isfinite
from pathlib import Path
from threading import Lock
from typing import Any, Literal, Protocol, runtime_checkable

import structlog
from pydantic import AwareDatetime, Field, field_validator, model_validator

from evaluation.harness.config import canonical_json
from evaluation.harness.models import Record, Usage
from evaluation.harness.paths import directory, reject_links

logger = structlog.get_logger(__name__)

MemoryLayer = Literal["episodic", "semantic", "opinion", "persona"]
CandidateAssertionMode = Literal[
    "asserted",
    "hedged",
    "hypothetical",
    "planned",
    "quoted",
    "third_party",
    "joking",
]
ValidFromBasis = Literal["explicit", "inferred"]
EvidenceRole = Literal["user", "assistant"]


class ExtractionSchemaValidationError(ValueError):
    """Raised when an extraction payload or candidate fails schema validation before caching."""


class ExtractionCacheKey(Record):
    """Composite cache key (turn_id, extractor_version, model_id) per Proposal §9 (RL-032)."""

    turn_id: str = Field(min_length=1)
    extractor_version: str = Field(min_length=1)
    model_id: str = Field(min_length=1)

    @field_validator("turn_id", "extractor_version", "model_id")
    @classmethod
    def reject_blank_components(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nonempty_extraction_cache_key_component_required")
        return value

    def as_tuple(self) -> tuple[str, str, str]:
        """Return the canonical 3-tuple (turn_id, extractor_version, model_id)."""
        return (self.turn_id, self.extractor_version, self.model_id)

    def key_sha256(self) -> str:
        """Compute the deterministic SHA-256 digest of this cache key."""
        return hashlib.sha256(canonical_json(self.model_dump(mode="json"))).hexdigest()


def _parse_optional_aware_datetime(value: object) -> AwareDatetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timezone_aware_datetime_required")
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("nonempty_datetime_string_required")
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("timezone_aware_datetime_required")
        return parsed
    raise TypeError("datetime_or_iso_string_required")


class ExtractedCandidate(Record):
    """Schema-validated slow-path extraction candidate (TECHNICAL_SPECIFICATION.md §§3.1, 4, 5)."""

    subject_key: str = Field(min_length=1)
    predicate_key: str = Field(min_length=1)
    content: str = Field(min_length=1)
    layer: MemoryLayer = "episodic"
    single_valued: bool = True
    assertion_mode: CandidateAssertionMode = "asserted"
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None
    valid_from_basis: ValidFromBasis = "explicit"
    evidence_span: str = Field(min_length=1)
    evidence_role: EvidenceRole = "user"
    turn_id: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    importance: float | None = Field(default=None, ge=0.0, le=1.0)
    candidate_fingerprint: str | None = None

    @field_validator("valid_from", "valid_to", mode="before")
    @classmethod
    def parse_datetime_bounds(cls, value: object) -> AwareDatetime | None:
        return _parse_optional_aware_datetime(value)

    @field_validator("confidence", "importance", mode="before")
    @classmethod
    def validate_probability_score(cls, value: object) -> float | None:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise TypeError("numeric_score_required")
        score = float(value)
        if not isfinite(score) or score < 0.0 or score > 1.0:
            raise ValueError("score_must_be_finite_in_unit_interval")
        return score

    @field_validator("subject_key", "predicate_key", "content", "evidence_span")
    @classmethod
    def reject_blank_required_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("nonempty_candidate_text_required")
        return value

    @field_validator("candidate_fingerprint", "turn_id")
    @classmethod
    def reject_blank_optional_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("nonempty_optional_candidate_field_required")
        return value

    @model_validator(mode="after")
    def check_temporal_interval(self) -> "ExtractedCandidate":
        if self.valid_from is not None and self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to_must_exceed_valid_from")
        return self

    def compute_fingerprint(self) -> str:
        """Compute deterministic SHA-256 fingerprint for Invariant 9 idempotency."""
        identity_payload = {
            "assertion_mode": self.assertion_mode,
            "content": self.content,
            "evidence_role": self.evidence_role,
            "evidence_span": self.evidence_span,
            "layer": self.layer,
            "predicate_key": self.predicate_key,
            "single_valued": self.single_valued,
            "subject_key": self.subject_key,
            "valid_from": self.valid_from.isoformat() if self.valid_from is not None else None,
            "valid_from_basis": self.valid_from_basis,
            "valid_to": self.valid_to.isoformat() if self.valid_to is not None else None,
        }
        return hashlib.sha256(canonical_json(identity_payload)).hexdigest()


def validate_and_canonicalize_candidates(
    candidates: Sequence[Mapping[str, Any] | ExtractedCandidate],
) -> tuple[bytes, tuple[dict[str, Any], ...]]:
    """Validate a candidate sequence against ExtractedCandidate and return canonical UTF-8 JSON bytes and dicts."""
    if isinstance(candidates, str | bytes | bytearray) or not isinstance(candidates, Sequence):
        raise ExtractionSchemaValidationError("candidate_sequence_required")

    normalized_list: list[dict[str, Any]] = []
    for item in candidates:
        try:
            if isinstance(item, ExtractedCandidate):
                raw_dict = item.model_dump(mode="json")
            elif isinstance(item, Mapping):
                plain = dict(item)
                ExtractedCandidate.model_validate(plain)
                raw_dict = json.loads(canonical_json(plain).decode("utf-8"))
            else:
                raise ExtractionSchemaValidationError("candidate_mapping_or_model_required")
        except ExtractionSchemaValidationError:
            raise
        except Exception as exc:
            raise ExtractionSchemaValidationError(f"invalid_extraction_candidate_schema: {exc}") from exc
        normalized_list.append(raw_dict)

    canonical_bytes = canonical_json(normalized_list)
    deserialized = tuple(json.loads(canonical_bytes.decode("utf-8")))
    return canonical_bytes, deserialized


class ExtractionProviderOutput(Record):
    """Accounted return value from a live slow-path extractor invocation."""

    candidates: tuple[dict[str, Any] | ExtractedCandidate, ...]
    usage: Usage = Usage.zero()


class ExtractionReplayOutcome(Record):
    """Result of get_or_extract; charges Usage.zero() on cache hit so ablations incur zero LLM cost."""

    key: ExtractionCacheKey
    candidates: tuple[dict[str, Any], ...]
    cache_hit: bool
    usage: Usage
    recorded_usage: Usage
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    def as_dicts(self) -> list[dict[str, Any]]:
        """Return a fresh deep-isolated list of candidate dicts."""
        raw = canonical_json(list(self.candidates))
        return list(json.loads(raw.decode("utf-8")))


class CacheEntry(Record):
    """Single immutable record in the extraction replay cache."""

    key: ExtractionCacheKey
    candidates: tuple[dict[str, Any], ...]
    recorded_usage: Usage = Usage.zero()
    entry_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_entry_integrity(self) -> "CacheEntry":
        canonical_bytes, _ = validate_and_canonicalize_candidates(self.candidates)
        expected = _compute_entry_sha256(self.key, canonical_bytes)
        if self.entry_sha256 != expected:
            raise ValueError("cache_entry_sha256_mismatch")
        return self


def _compute_entry_sha256(key: ExtractionCacheKey, canonical_candidates_bytes: bytes) -> str:
    envelope = {
        "candidates_sha256": hashlib.sha256(canonical_candidates_bytes).hexdigest(),
        "key": key.model_dump(mode="json"),
    }
    return hashlib.sha256(canonical_json(envelope)).hexdigest()


class ReplayCacheManifestSummary(Record):
    """Run-manifest summary for the extraction record-and-replay cache (RL-032)."""

    schema_version: Literal[1] = 1
    task: Literal["RL-032"] = "RL-032"
    entry_count: int = Field(ge=0)
    hits: int = Field(ge=0)
    misses: int = Field(ge=0)
    errors_rejected: int = Field(ge=0)
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")


class ReplayCacheFileEnvelope(Record):
    """Durable on-disk envelope for the extraction replay cache."""

    schema_version: Literal[1] = 1
    task: Literal["RL-032"] = "RL-032"
    entries: tuple[CacheEntry, ...]
    integrity_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def verify_envelope_digest(self) -> "ReplayCacheFileEnvelope":
        seen: set[tuple[str, str, str]] = set()
        for entry in self.entries:
            k_tuple = entry.key.as_tuple()
            if k_tuple in seen:
                raise ValueError("duplicate_cache_entry_key_in_envelope")
            seen.add(k_tuple)
        expected = _compute_collection_integrity_sha256({entry.key.as_tuple(): entry for entry in self.entries})
        if self.integrity_sha256 != expected:
            raise ValueError("replay_cache_file_integrity_sha256_mismatch")
        return self


def _compute_collection_integrity_sha256(entries: Mapping[tuple[str, str, str], CacheEntry]) -> str:
    ordered_payload = [
        {
            "entry_sha256": entries[k].entry_sha256,
            "key": entries[k].key.model_dump(mode="json"),
            "recorded_usage": entries[k].recorded_usage.model_dump(mode="json"),
        }
        for k in sorted(entries.keys())
    ]
    return hashlib.sha256(canonical_json(ordered_payload)).hexdigest()


@runtime_checkable
class ReplayCacheSummaryProvider(Protocol):
    """Protocol for recording replay cache integrity digest and hit/miss counters in run manifests."""

    def manifest_summary(self) -> ReplayCacheManifestSummary: ...


def _unpack_extractor_output(
    raw_output: object,
) -> tuple[Sequence[Mapping[str, Any] | ExtractedCandidate], Usage]:
    if isinstance(raw_output, ExtractionProviderOutput):
        return raw_output.candidates, raw_output.usage
    if (
        isinstance(raw_output, tuple)
        and len(raw_output) == 2
        and isinstance(raw_output[1], Usage)
        and isinstance(raw_output[0], Sequence)
        and not isinstance(raw_output[0], str | bytes | bytearray)
    ):
        return raw_output[0], raw_output[1]
    if isinstance(raw_output, Sequence) and not isinstance(raw_output, str | bytes | bytearray):
        return raw_output, Usage.zero()
    raise ExtractionSchemaValidationError("invalid_extractor_return_type")


def _coerce_key(key: ExtractionCacheKey | tuple[str, str, str]) -> ExtractionCacheKey:
    if isinstance(key, ExtractionCacheKey):
        return key
    if isinstance(key, tuple) and len(key) == 3:
        turn_id, extractor_version, model_id = key
        return ExtractionCacheKey(
            turn_id=turn_id,
            extractor_version=extractor_version,
            model_id=model_id,
        )
    raise ValueError("invalid_extraction_cache_key")


class ExtractionReplayCache:
    """Deterministic record-and-replay cache keyed by (turn_id, extractor_version, model_id) (RL-032)."""

    def __init__(
        self,
        *,
        cache_path: Path | None = None,
        auto_persist: bool = True,
    ) -> None:
        self._lock = Lock()
        self._entries: dict[tuple[str, str, str], CacheEntry] = {}
        self._canonical_bytes: dict[tuple[str, str, str], bytes] = {}
        self._hits = 0
        self._misses = 0
        self._errors_rejected = 0
        self._cache_path = cache_path
        self._auto_persist = auto_persist
        if cache_path is not None:
            reject_links(cache_path)
            if cache_path.exists():
                self._load_unlocked(cache_path)

    @property
    def hits(self) -> int:
        with self._lock:
            return self._hits

    @property
    def misses(self) -> int:
        with self._lock:
            return self._misses

    @property
    def errors_rejected(self) -> int:
        with self._lock:
            return self._errors_rejected

    @property
    def entry_count(self) -> int:
        with self._lock:
            return len(self._entries)

    def __len__(self) -> int:
        return self.entry_count

    def __contains__(self, key: ExtractionCacheKey | tuple[str, str, str]) -> bool:
        resolved = _coerce_key(key)
        with self._lock:
            return resolved.as_tuple() in self._entries

    def integrity_sha256(self) -> str:
        """Compute the deterministic SHA-256 digest over all cached entries in canonical key order."""
        with self._lock:
            return _compute_collection_integrity_sha256(self._entries)

    def manifest_summary(self) -> ReplayCacheManifestSummary:
        """Return immutable counters and integrity digest for recording in run manifests."""
        with self._lock:
            return ReplayCacheManifestSummary(
                entry_count=len(self._entries),
                hits=self._hits,
                misses=self._misses,
                errors_rejected=self._errors_rejected,
                integrity_sha256=_compute_collection_integrity_sha256(self._entries),
            )

    def get(self, key: ExtractionCacheKey | tuple[str, str, str]) -> list[dict[str, Any]] | None:
        """Look up candidate dicts by key; increments hits on hit, misses on miss.

        Returns a fresh deserialized list of byte-identical candidate dicts on hit so
        caller mutation cannot corrupt cached state.
        """
        resolved = _coerce_key(key)
        k_tuple = resolved.as_tuple()
        with self._lock:
            raw = self._canonical_bytes.get(k_tuple)
            if raw is None:
                self._misses += 1
                return None
            self._hits += 1
            return list(json.loads(raw.decode("utf-8")))

    def put(
        self,
        key: ExtractionCacheKey | tuple[str, str, str],
        candidates: Sequence[Mapping[str, Any] | ExtractedCandidate],
        *,
        usage: Usage | None = None,
    ) -> CacheEntry:
        """Validate candidates against the schema gate and store canonical bytes under key.

        Invalid candidates increment errors_rejected and are never cached.
        Conflicting writes to an existing key raise ValueError without mutating the entry.
        """
        resolved = _coerce_key(key)
        if usage is not None and not isinstance(usage, Usage):
            with self._lock:
                self._errors_rejected += 1
            raise ExtractionSchemaValidationError("valid_usage_instance_required")
        resolved_usage = usage if usage is not None else Usage.zero()

        try:
            canonical_bytes, validated_tuple = validate_and_canonicalize_candidates(candidates)
            entry_sha = _compute_entry_sha256(resolved, canonical_bytes)
            entry = CacheEntry(
                key=resolved,
                candidates=validated_tuple,
                recorded_usage=resolved_usage,
                entry_sha256=entry_sha,
            )
        except Exception as exc:
            with self._lock:
                self._errors_rejected += 1
            logger.warning(
                "extraction_replay_cache_rejected_invalid_candidates",
                turn_id=resolved.turn_id,
                extractor_version=resolved.extractor_version,
                model_id=resolved.model_id,
                error=str(exc),
            )
            if isinstance(exc, ExtractionSchemaValidationError):
                raise
            raise ExtractionSchemaValidationError(str(exc)) from exc

        k_tuple = resolved.as_tuple()
        with self._lock:
            existing = self._entries.get(k_tuple)
            if existing is not None:
                if existing.entry_sha256 != entry.entry_sha256:
                    self._errors_rejected += 1
                    raise ValueError("replay_cache_conflicting_overwrite_for_key")
                return existing
            self._entries[k_tuple] = entry
            self._canonical_bytes[k_tuple] = canonical_bytes
            if self._cache_path is not None and self._auto_persist:
                try:
                    self._save_unlocked(self._cache_path)
                except BaseException:
                    self._entries.pop(k_tuple, None)
                    self._canonical_bytes.pop(k_tuple, None)
                    self._errors_rejected += 1
                    raise
            return entry

    def get_or_extract(
        self,
        key: ExtractionCacheKey | tuple[str, str, str],
        extractor: Callable[
            [],
            ExtractionProviderOutput
            | tuple[Sequence[Mapping[str, Any] | ExtractedCandidate], Usage]
            | Sequence[Mapping[str, Any] | ExtractedCandidate],
        ],
    ) -> ExtractionReplayOutcome:
        """Replay cached candidates with zero LLM calls on hit, or invoke extractor once on miss.

        Only successful, schema-validated extractions are cached; exceptions or malformed
        outputs increment errors_rejected and leave the cache untouched.
        """
        resolved = _coerce_key(key)
        k_tuple = resolved.as_tuple()
        with self._lock:
            existing = self._entries.get(k_tuple)
            raw_bytes = self._canonical_bytes.get(k_tuple)
            if existing is not None and raw_bytes is not None:
                self._hits += 1
                fresh_candidates = tuple(json.loads(raw_bytes.decode("utf-8")))
                return ExtractionReplayOutcome(
                    key=resolved,
                    candidates=fresh_candidates,
                    cache_hit=True,
                    usage=Usage.zero(),
                    recorded_usage=existing.recorded_usage,
                    entry_sha256=existing.entry_sha256,
                )
            self._misses += 1

        try:
            raw_output = extractor()
        except BaseException:
            with self._lock:
                self._errors_rejected += 1
            logger.warning(
                "extraction_replay_cache_extractor_error_not_cached",
                turn_id=resolved.turn_id,
                extractor_version=resolved.extractor_version,
                model_id=resolved.model_id,
            )
            raise

        try:
            raw_candidates, actual_usage = _unpack_extractor_output(raw_output)
        except ExtractionSchemaValidationError:
            with self._lock:
                self._errors_rejected += 1
            raise

        entry = self.put(resolved, raw_candidates, usage=actual_usage)
        with self._lock:
            stored_bytes = self._canonical_bytes[k_tuple]
        fresh_candidates = tuple(json.loads(stored_bytes.decode("utf-8")))
        return ExtractionReplayOutcome(
            key=resolved,
            candidates=fresh_candidates,
            cache_hit=False,
            usage=actual_usage,
            recorded_usage=entry.recorded_usage,
            entry_sha256=entry.entry_sha256,
        )

    def save(self, path: Path | None = None) -> Path:
        """Atomically persist the cache envelope to disk with fsync and integrity digest."""
        with self._lock:
            target = path if path is not None else self._cache_path
            if target is None:
                raise ValueError("replay_cache_path_required")
            self._save_unlocked(target)
            return target

    def _save_unlocked(self, target: Path) -> None:
        reject_links(target)
        parent = directory(target.parent)
        ordered_entries = tuple(self._entries[k] for k in sorted(self._entries.keys()))
        envelope = ReplayCacheFileEnvelope(
            entries=ordered_entries,
            integrity_sha256=_compute_collection_integrity_sha256(self._entries),
        )
        payload = canonical_json(envelope.model_dump(mode="json")) + b"\n"
        tmp_path = parent / f".{target.name}.{os.getpid()}.tmp"
        reject_links(tmp_path)
        try:
            with tmp_path.open("wb") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, target)
            if os.name != "nt":
                dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    @classmethod
    def load(cls, path: Path, *, auto_persist: bool = True) -> "ExtractionReplayCache":
        """Load and validate an ExtractionReplayCache from disk."""
        reject_links(path)
        if not path.is_file():
            raise FileNotFoundError(f"replay_cache_file_not_found: {path}")
        return cls(cache_path=path, auto_persist=auto_persist)

    def _load_unlocked(self, path: Path) -> None:
        reject_links(path)
        envelope = ReplayCacheFileEnvelope.model_validate_json(path.read_bytes())
        loaded_entries: dict[tuple[str, str, str], CacheEntry] = {}
        loaded_bytes: dict[tuple[str, str, str], bytes] = {}
        for entry in envelope.entries:
            k_tuple = entry.key.as_tuple()
            canonical_bytes, _ = validate_and_canonicalize_candidates(entry.candidates)
            loaded_entries[k_tuple] = entry
            loaded_bytes[k_tuple] = canonical_bytes
        self._entries = loaded_entries
        self._canonical_bytes = loaded_bytes
