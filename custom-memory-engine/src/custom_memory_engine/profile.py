"""Profile schema: configuration, never forks (E-019, V2-D08).

A *profile* is data that says how one application (conversational, coding, research, enterprise)
uses the single engine: which kinds it stores, which extractors feed it, which inference operators
it may run, its retention defaults, its scope root, how results are rendered, which of the six
canonical facets it uses, and its association policy. **No code path may branch on a profile's name**
(`tests/test_profile.py` scans ``src/`` for it); everything that differs between applications is a
field below.

Strict by construction: every model forbids unknown keys, so a typo in a profile document is an error
naming the field, not a silently ignored setting.

Serialisation: **JSON** or **TOML** (``load_profile`` chooses by file suffix); ``dump_profile`` writes
canonical JSON. The v1-equivalent profile is the document ``profiles/conversational.json``.

``V2-D14`` (retention defaults per profile and abstraction expiry) is still an open decision. The schema
carries the fields; the values for abstraction expiry are marked ``placeholder:V2-D14`` until it closes.
"""

from __future__ import annotations

import json
import tomllib
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from custom_memory_engine.model.association import ASSOCIATION_TYPES, validate_cap
from custom_memory_engine.model.axes import FACETS, KINDS, RETENTIONS, SHORT_RETENTION_KINDS, Kind, Retention
from custom_memory_engine.scope.paths import ScopeError, parse_scope

PLACEHOLDER_V2_D14 = "placeholder:V2-D14"
SCHEMA_VERSION = 1

# `SourceRef.type` of API_V2.md section 2: what an extractor may read from.
SOURCE_TYPES: tuple[str, ...] = ("turn", "tool", "file", "paper", "event", "external")

# The four answer channels of CHANNELS.md (the audit-only set is not a rendering target).
CHANNELS: tuple[str, ...] = ("facts", "hypotheses", "procedures", "conflicts")

MAX_ASSOCIATION_EXTENSIONS = 8  # a bound chosen here; the extension mechanism itself is still unspecified

_PLACEHOLDERS = {"{user}": "user1", "{repo}": "repo1", "{topic}": "topic1", "{org}": "org1"}

type Identifier = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
type Slug = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,63}$")]


class ProfileError(ValueError):
    """Static code for problems outside the schema (unreadable file, unsupported format)."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class ExtractorSpec(_Strict):
    """A named extractor and the source types it reads."""

    name: Identifier
    version: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    source_types: tuple[str, ...] = Field(min_length=1)

    @field_validator("source_types")
    @classmethod
    def _known_sources(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(SOURCE_TYPES))
        if unknown:
            raise ValueError(f"unknown_source_type:{unknown[0]}")
        if len(set(value)) != len(value):
            raise ValueError("duplicate_source_type")
        return value


class RetentionPolicy(_Strict):
    """Per-kind retention defaults, subject to the retention floors of ARCHITECTURE_V2.md section 1."""

    defaults: dict[Kind, Retention]
    promote_to_durable: bool = False
    status: Literal["v1_semantics", "placeholder:V2-D14", "decided"] = PLACEHOLDER_V2_D14

    @model_validator(mode="after")
    def _floors(self) -> Self:
        for kind, retention in self.defaults.items():
            if retention not in RETENTIONS:
                raise ValueError(f"unknown_retention:{kind}")
            if retention != "durable" and kind not in SHORT_RETENTION_KINDS:
                raise ValueError(f"retention_floor_requires_durable:{kind}")
        if self.promote_to_durable and all(r == "durable" for r in self.defaults.values()):
            raise ValueError("promote_to_durable_requires_a_non_durable_default")
        return self


class ExpiryPolicy(_Strict):
    """Abstraction expiry in days, per facet. Values are placeholders until V2-D14 is decided."""

    days_by_facet: dict[str, Annotated[int, Field(ge=1, le=3650)]] = Field(default_factory=dict)
    status: Literal["placeholder:V2-D14", "decided"] = PLACEHOLDER_V2_D14

    @field_validator("days_by_facet")
    @classmethod
    def _canonical_facets(cls, value: dict[str, int]) -> dict[str, int]:
        unknown = sorted(set(value) - set(FACETS))
        if unknown:
            raise ValueError(f"unknown_facet:{unknown[0]}")
        return value


class RenderConventions(_Strict):
    """How a result is rendered: channel budget shares and what is shown with each item."""

    channel_budget_share: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]]
    show_validity_intervals: bool = True
    show_provenance: bool = True

    @field_validator("channel_budget_share")
    @classmethod
    def _shares(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(CHANNELS))
        if unknown:
            raise ValueError(f"unknown_channel:{unknown[0]}")
        if sum(value.values()) > 1.0 + 1e-9:
            raise ValueError("channel_budget_shares_exceed_one")
        if value.get("facts", 0.0) <= 0.0:
            raise ValueError("facts_budget_floor_required")  # CHANNELS.md R7: facts are never starved
        return value


class AssociationTypeExtension(_Strict):
    """A profile-specific association type. It may not reuse a built-in name."""

    name: Identifier
    directed: bool = True

    @field_validator("name")
    @classmethod
    def _not_builtin(cls, value: str) -> str:
        if value in ASSOCIATION_TYPES:
            raise ValueError("association_type_already_defined")
        return value


class AssociationPolicy(_Strict):
    """The per-artifact association degree cap (V2-D13) and any extra association types."""

    degree_cap: int = 32
    extensions: tuple[AssociationTypeExtension, ...] = ()

    @field_validator("degree_cap")
    @classmethod
    def _cap(cls, value: int) -> int:
        return validate_cap(value)

    @model_validator(mode="after")
    def _extensions(self) -> Self:
        names = [e.name for e in self.extensions]
        if len(set(names)) != len(names):
            raise ValueError("duplicate_association_type")
        if len(names) > MAX_ASSOCIATION_EXTENSIONS:
            raise ValueError("too_many_association_types")
        return self


class Profile(_Strict):
    """One application's configuration of the engine. Data only."""

    schema_version: Literal[1] = SCHEMA_VERSION
    name: Slug
    description: Annotated[str, StringConstraints(max_length=500)] = ""
    kinds: tuple[Kind, ...] = Field(min_length=1)
    extractors: tuple[ExtractorSpec, ...] = ()
    operators: tuple[Identifier, ...] = ()
    retention: RetentionPolicy
    scope_root: str
    render: RenderConventions
    facets: tuple[str, ...] = ()
    association: AssociationPolicy = Field(default_factory=AssociationPolicy)
    abstraction_expiry: ExpiryPolicy = Field(default_factory=ExpiryPolicy)

    @field_validator("kinds")
    @classmethod
    def _kinds(cls, value: tuple[Kind, ...]) -> tuple[Kind, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_kind")
        for kind in value:
            if kind not in KINDS:
                raise ValueError(f"unknown_kind:{kind}")
        return value

    @field_validator("operators")
    @classmethod
    def _unique_operators(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(set(value)) != len(value):
            raise ValueError("duplicate_operator")
        return value

    @field_validator("facets")
    @classmethod
    def _facet_subset(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """A profile may select among the six canonical facets; it may not introduce one."""
        unknown = sorted(set(value) - set(FACETS))
        if unknown:
            raise ValueError(f"unknown_facet:{unknown[0]}")
        if len(set(value)) != len(value):
            raise ValueError("duplicate_facet")
        return value

    @field_validator("scope_root")
    @classmethod
    def _scope_root(cls, value: str) -> str:
        """Validate against the real scope grammar after substituting each placeholder segment."""
        if not value.startswith("/") or not value.endswith("/"):
            raise ValueError("scope_root_must_start_and_end_with_separator")
        probe = value
        for token, sample in _PLACEHOLDERS.items():
            probe = probe.replace(token, sample)
        if "{" in probe or "}" in probe:
            raise ValueError("unknown_scope_placeholder")
        try:
            parse_scope(probe)
        except ScopeError as error:
            raise ValueError(f"scope_root_invalid:{error}") from None
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if set(self.retention.defaults) != set(self.kinds):
            raise ValueError("retention_defaults_must_cover_exactly_the_declared_kinds")
        unused = sorted(set(self.abstraction_expiry.days_by_facet) - set(self.facets))
        if unused:
            raise ValueError(f"expiry_for_unselected_facet:{unused[0]}")
        if self.abstraction_expiry.days_by_facet and "abstraction" not in self.kinds:
            raise ValueError("expiry_requires_the_abstraction_kind")
        return self


def parse_profile(document: object) -> Profile:
    """Validate a decoded document strictly; unknown keys and wrong types raise ``pydantic.ValidationError``.

    Validation runs in JSON mode so arrays become tuples while strings are never coerced into numbers
    or booleans.
    """
    try:
        text = json.dumps(document)
    except (TypeError, ValueError):
        raise ProfileError("profile_not_serialisable") from None
    return Profile.model_validate_json(text, strict=True)


def load_profile(path: str | Path) -> Profile:
    """Load a profile from a ``.json`` or ``.toml`` file."""
    target = Path(path)
    try:
        raw = target.read_bytes()
    except OSError:
        raise ProfileError("profile_unreadable") from None
    try:
        if target.suffix == ".json":
            document = json.loads(raw.decode("utf-8"))
        elif target.suffix == ".toml":
            document = tomllib.loads(raw.decode("utf-8"))
        else:
            raise ProfileError("profile_format_unsupported")
    except (ValueError, UnicodeDecodeError) as error:
        if isinstance(error, ProfileError):
            raise
        raise ProfileError("profile_not_parseable") from None
    return parse_profile(document)


def dump_profile(profile: Profile) -> str:
    """Canonical JSON for a profile (stable key order, trailing newline)."""
    return json.dumps(profile.model_dump(mode="json"), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


@lru_cache(maxsize=1)
def conversational() -> Profile:
    """The v1-equivalent profile, loaded from the packaged document ``profiles/conversational.json``."""
    text = resources.files("custom_memory_engine").joinpath("profiles/conversational.json").read_text("utf-8")
    return parse_profile(json.loads(text))


__all__ = [
    "CHANNELS",
    "MAX_ASSOCIATION_EXTENSIONS",
    "PLACEHOLDER_V2_D14",
    "SCHEMA_VERSION",
    "SOURCE_TYPES",
    "AssociationPolicy",
    "AssociationTypeExtension",
    "ExpiryPolicy",
    "ExtractorSpec",
    "Profile",
    "ProfileError",
    "RenderConventions",
    "RetentionPolicy",
    "conversational",
    "dump_profile",
    "load_profile",
    "parse_profile",
]
