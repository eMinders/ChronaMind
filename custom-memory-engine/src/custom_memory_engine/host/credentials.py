"""Transport authentication: the only place a caller's tier and admin role come from (``V2-D47``, ``E-046``).

The engine's entry point takes tier from a :class:`~custom_memory_engine.api.entry.Credential` and never from an
argument, so the transport is the one component that must be right about *who* is calling. Three properties matter and
all are tested:

* **The tier comes from the token, not the request.** The envelope's only fields are ``operation``, ``arguments`` and
  (for a delegating token) ``principal``; a top-level ``tier`` is never read, and ``arguments["tier"]`` is a
  ``reserved_argument`` refusal from the entry point. A caller cannot promote itself by asking.
* **A wrong token is indistinguishable from a missing one.** Authentication walks the whole registry in constant time
  and answers ``None`` for both, so a timing side channel does not tell an attacker whether a guessed prefix was close.
  The registry is small by design (one entry per service principal, not per user), so this costs nothing.
* **Delegation is a property of the token, never of the request** (``V2-D120``). A service that serves many end users
  (the FastAPI app) holds one token marked ``"delegate": true`` and names the end user per request; the end user is
  then the principal for every engine rule (namespace, RLS, rate limit). A token without the flag cannot name a
  principal, a delegated caller is never ``admin``, its tier is the token's, and it can never act as a principal that
  owns a token of its own — so a delegating service cannot reach an operator's or another service's namespace.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from collections.abc import Collection, Mapping
from dataclasses import dataclass

from custom_memory_engine.api.entry import Credential
from custom_memory_engine.scope.paths import ScopeError, user_scope


class CredentialsError(ValueError):
    """A static code; a token never appears in a message, a log line or a traceback."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Authenticated:
    """What a bearer token established: the token's own credential, and whether it may act for an end user."""

    credential: Credential
    may_delegate: bool = False


class DelegationRefused(Exception):
    """A request's ``principal`` cannot be honoured. ``code`` is an ``API_V2.md`` 1.1 code, ``detail`` a static one."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class HostCredentials:
    """A token registry: opaque bearer token -> (principal, tier, admin, may_delegate).

    Tokens are compared by SHA-256 digest rather than directly, so the comparison is over fixed-length values and the
    digest of a token is never the token itself. The registry's ``repr`` deliberately shows the count and not the keys:
    a dataclass default would print every live credential into the first traceback that touched it.
    """

    __slots__ = ("_entries", "_service_principals")

    def __init__(self, entries: Mapping[str, Credential], *, delegating: Collection[str] = ()) -> None:
        if not entries:
            raise CredentialsError("at_least_one_token_required")
        self._entries: dict[bytes, Authenticated] = {}
        for token, credential in entries.items():
            if not isinstance(token, str) or len(token) < 16:
                # A short token is a misconfiguration that turns authentication into decoration. Refused at start-up.
                raise CredentialsError("token_too_short")
            if not isinstance(credential, Credential):
                raise CredentialsError("credential_required")
            may_delegate = token in delegating
            if may_delegate and credential.admin:
                # Admin is an operator's authority over the deployment; it must not travel with an end user's call.
                raise CredentialsError("delegating_token_cannot_be_admin")
            self._entries[_digest(token)] = Authenticated(credential, may_delegate)
        if any(token not in entries for token in delegating):
            raise CredentialsError("delegating_token_unknown")
        # Every principal that owns a token: none of them may be impersonated through delegation.
        self._service_principals = frozenset(entry.credential.principal for entry in self._entries.values())

    def __repr__(self) -> str:
        return f"HostCredentials(tokens={len(self._entries)})"

    @classmethod
    def from_environment(cls, raw: str | None) -> HostCredentials:
        """Parse ``CME_HOST_TOKENS``: a JSON object of ``token -> {principal, tier, admin?, delegate?}``.

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
        delegating: set[str] = set()
        for token, value in document.items():
            if not isinstance(value, dict):
                raise CredentialsError("host_token_entry_must_be_an_object")
            principal = value.get("principal")
            tier = value.get("tier")
            admin = value.get("admin", False)
            delegate = value.get("delegate", False)
            if (
                not isinstance(principal, str)
                or not isinstance(admin, bool)
                or not isinstance(delegate, bool)
                or tier not in ("B", "C")
            ):
                raise CredentialsError("host_token_entry_invalid")
            try:
                entries[token] = Credential(principal, tier, admin)
            except Exception:  # noqa: BLE001 - Credential raises EntryError, which must not echo the token
                raise CredentialsError("host_token_entry_invalid") from None
            if delegate:
                delegating.add(token)
        return cls(entries, delegating=delegating)

    def authenticate(self, authorization: str | None) -> Authenticated | None:
        """The identity behind an ``Authorization: Bearer <token>`` header, or ``None``.

        Every entry is compared, and the loop does not short-circuit: the number of comparisons and their duration do
        not depend on which token (if any) matched.
        """
        token = _bearer(authorization)
        if token is None:
            return None
        presented = _digest(token)
        found: Authenticated | None = None
        for stored, entry in self._entries.items():
            if hmac.compare_digest(presented, stored):
                found = entry
        return found

    def acting_credential(self, caller: Authenticated, principal: object) -> Credential:
        """The credential a request runs as: the token's own, or the end user a delegating token names.

        ``principal`` is the envelope's ``principal`` field (``None`` when absent). The rules, each a static refusal:

        * a token without the delegate flag that names a principal is ``forbidden`` — it cannot act for anyone else;
        * a delegating token must name one (``validation_error``): it has no namespace of its own to fall back on,
          and an implicit fallback would write an end user's memory into the service's namespace;
        * the name must be a valid user segment (the same grammar ``/u/<id>/`` enforces, so it cannot forge a path);
        * it must not be a principal that owns a token (``forbidden``), so delegation cannot reach an operator's or
          another service's data;
        * the delegated credential keeps the token's tier and is never ``admin``.
        """
        if not caller.may_delegate:
            if principal is not None:
                raise DelegationRefused("forbidden", "delegation_not_permitted")
            return caller.credential
        if principal is None:
            raise DelegationRefused("validation_error", "principal_required")
        if not isinstance(principal, str):
            raise DelegationRefused("validation_error", "principal_invalid")
        try:
            user_scope(principal)
        except ScopeError:
            raise DelegationRefused("validation_error", "principal_invalid") from None
        if principal in self._service_principals:
            raise DelegationRefused("forbidden", "delegation_not_permitted")
        return Credential(principal, caller.credential.tier, admin=False)


def _bearer(authorization: str | None) -> str | None:
    if not isinstance(authorization, str):
        return None
    scheme, _, value = authorization.partition(" ")
    if scheme.lower() != "bearer" or not value or len(value) > 512 or "\x00" in value:
        return None
    return value.strip()


def _digest(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


__all__ = ["Authenticated", "CredentialsError", "DelegationRefused", "HostCredentials"]
