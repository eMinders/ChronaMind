"""Transport authentication: the only place a caller's tier and admin role come from (``V2-D47``, ``E-046``).

The engine's entry point takes tier from a :class:`~custom_memory_engine.api.entry.Credential` and never from an
argument, so the transport is the one component that must be right about *who* is calling. Two properties matter and
both are tested:

* **The tier comes from the token, not the request.** A body carrying ``tier`` is a ``reserved_argument`` refusal,
  because ``RESERVED_ARGUMENTS`` in the entry point refuses it before dispatch — a caller cannot promote itself by
  asking.
* **A wrong token is indistinguishable from a missing one.** Authentication walks the whole registry in constant time
  and answers ``None`` for both, so a timing side channel does not tell an attacker whether a guessed prefix was close.
  The registry is small by design (one entry per service principal, not per user), so this costs nothing.
"""

from __future__ import annotations

import hmac
import json
from collections.abc import Mapping

from custom_memory_engine.api.entry import Credential


class CredentialsError(ValueError):
    """A static code; a token never appears in a message, a log line or a traceback."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class HostCredentials:
    """A token registry: opaque bearer token -> (principal, tier, admin).

    Tokens are compared by SHA-256 digest rather than directly, so the comparison is over fixed-length values and the
    digest of a token is never the token itself. The registry's ``repr`` deliberately shows the count and not the keys:
    a dataclass default would print every live credential into the first traceback that touched it.
    """

    __slots__ = ("_entries",)

    def __init__(self, entries: Mapping[str, Credential]) -> None:
        if not entries:
            raise CredentialsError("at_least_one_token_required")
        self._entries: dict[bytes, Credential] = {}
        for token, credential in entries.items():
            if not isinstance(token, str) or len(token) < 16:
                # A short token is a misconfiguration that turns authentication into decoration. Refused at start-up.
                raise CredentialsError("token_too_short")
            if not isinstance(credential, Credential):
                raise CredentialsError("credential_required")
            self._entries[_digest(token)] = credential

    def __repr__(self) -> str:
        return f"HostCredentials(tokens={len(self._entries)})"

    @classmethod
    def from_environment(cls, raw: str | None) -> HostCredentials:
        """Parse ``CME_HOST_TOKENS``: a JSON object of ``token -> {principal, tier, admin?}``.

        The token is the key so a deployment can rotate one principal without touching the others, and so the document
        contains **no secret in a value position** — a mistake with a value is easy to make when the shape is
        ``[{token: ...}]`` and it lands in a log line.
        """
        if not raw or not raw.strip():
            raise CredentialsError("host_tokens_required")
        try:
            document = json.loads(raw)
        except json.JSONDecodeError:
            raise CredentialsError("host_tokens_not_json") from None
        if not isinstance(document, dict):
            raise CredentialsError("host_tokens_must_be_an_object")
        entries: dict[str, Credential] = {}
        for token, value in document.items():
            if not isinstance(value, dict):
                raise CredentialsError("host_token_entry_must_be_an_object")
            principal = value.get("principal")
            tier = value.get("tier")
            admin = value.get("admin", False)
            if not isinstance(principal, str) or not isinstance(admin, bool) or tier not in ("B", "C"):
                raise CredentialsError("host_token_entry_invalid")
            try:
                entries[token] = Credential(principal, tier, admin)
            except Exception:  # noqa: BLE001 - Credential raises EntryError, which must not echo the token
                raise CredentialsError("host_token_entry_invalid") from None
        return cls(entries)

    def authenticate(self, authorization: str | None) -> Credential | None:
        """The credential behind an ``Authorization: Bearer <token>`` header, or ``None``.

        Every entry is compared, and the loop does not short-circuit: the number of comparisons and their duration do
        not depend on which token (if any) matched.
        """
        token = _bearer(authorization)
        if token is None:
            return None
        presented = _digest(token)
        found: Credential | None = None
        for stored, credential in self._entries.items():
            if hmac.compare_digest(presented, stored):
                found = credential
        return found


def _bearer(authorization: str | None) -> str | None:
    if not isinstance(authorization, str):
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value or len(value) > 512 or "\x00" in value:
        return None
    return value.strip()


def _digest(token: str) -> bytes:
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).digest()


__all__ = ["CredentialsError", "HostCredentials"]
