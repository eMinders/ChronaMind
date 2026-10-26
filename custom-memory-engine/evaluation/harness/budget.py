"""Durable, shared campaign reservations implement RL-010's combined ceilings."""

import json
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from decimal import MAX_EMAX, MIN_EMIN, Decimal, localcontext
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import Field, model_validator
from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_exponential

from evaluation.harness.config import HarnessConfig, canonical_json
from evaluation.harness.models import Record, Split, Usage

_MAX_STATE_BYTES = 8 * 1024 * 1024
_LOCK_TIMEOUT_SECONDS = 2.0
type _ReservationId = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]


class BudgetExceeded(ValueError):
    """Dispatch would exceed a ceiling or the campaign has already breached one."""


class AccountedOperationError(RuntimeError):
    """An adapter failure with known aggregate usage, including all its attempts."""

    def __init__(self, message: str, usage: Usage) -> None:
        super().__init__(message)
        if not isinstance(usage, Usage):
            raise TypeError("operation_usage_required")
        self.usage = usage


class BudgetSnapshot(Record):
    """A consistent view of spent and still-reserved provider usage."""

    spent: Usage
    reserved: Usage
    dev_spent: Usage
    dev_reserved: Usage
    unknown_operations: int = Field(ge=0)
    completed_operations: int = Field(ge=0)
    failed_operations: int = Field(ge=0)
    breached: bool


class _Limits(Record):
    token_cap: int = Field(gt=0, le=50_000_000)
    cost_cap_usd: Decimal = Field(gt=0, le=100)
    dev_cost_cap_usd: Decimal = Field(gt=0, le=20)


class _Reservation(Record):
    maximum: Usage
    split: Split


class _State(Record):
    version: Literal[1] = 1
    budget_id: str
    limits: _Limits
    spent: Usage
    dev_spent: Usage
    pending: dict[_ReservationId, _Reservation]
    unknown_operations: int = Field(ge=0)
    completed_operations: int = Field(ge=0)
    failed_operations: int = Field(ge=0)
    breached: bool

    @model_validator(mode="after")
    def validate_counters(self) -> "_State":
        if (
            self.failed_operations > self.completed_operations
            or self.unknown_operations > self.completed_operations
            or not _within(self.dev_spent, self.spent)
            or self.limits.dev_cost_cap_usd > self.limits.cost_cap_usd
        ):
            raise ValueError("inconsistent_budget_counters")
        return self


def _within(actual: Usage, maximum: Usage) -> bool:
    """Bounds apply to each declared counter, including separately logged embeddings."""
    return (
        actual.input_tokens <= maximum.input_tokens
        and actual.output_tokens <= maximum.output_tokens
        and actual.embedding_tokens <= maximum.embedding_tokens
        and actual.cost_usd <= maximum.cost_usd
    )


def _sum_usage(values: Iterator[Usage]) -> Usage:
    """Keep exact currency even when default Decimal precision would round a cap."""
    items = list(values)
    costs = [item.cost_usd for item in items]
    nonzero = [cost for cost in costs if cost]
    precision = 28
    if nonzero:
        minimum_exponent = min(int(cost.as_tuple().exponent) for cost in nonzero)
        maximum_adjusted = max(cost.adjusted() for cost in nonzero)
        precision = max(precision, maximum_adjusted - minimum_exponent + len(str(len(nonzero))) + 2)
    with localcontext() as context:
        context.prec = precision
        context.Emax = MAX_EMAX
        context.Emin = MIN_EMIN
        cost = sum(costs, Decimal(0))
    return Usage(
        input_tokens=sum(item.input_tokens for item in items),
        output_tokens=sum(item.output_tokens for item in items),
        embedding_tokens=sum(item.embedding_tokens for item in items),
        cost_usd=cost,
    )


def _safe_path(path: Path, kind: Literal["file", "directory"], *, missing: bool = False) -> None:
    """Reject symlinks, Windows junctions/reparse points, and unexpected file types."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        if missing:
            return
        raise ValueError("unsafe_budget_path") from None
    if (
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        or (kind == "file" and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))
        or (kind == "directory" and not stat.S_ISDIR(info.st_mode))
    ):
        raise ValueError("unsafe_budget_path")


def _ensure_directory(path: Path) -> bool:
    """Create exactly one expected directory; never follow an existing link."""
    try:
        path.mkdir(mode=0o700)
        return True
    except FileExistsError:
        _safe_path(path, "directory")
        return False


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_budget_state_key")
        result[key] = value
    return result


class BudgetLedger:
    """Serialize reservations across threads/processes sharing a campaign root.

    The caller must first guard/create the runs root. A directory lock is bounded
    and never reclaimed automatically; an interrupted owner requires investigation.
    Persisted reservations remain charged against availability until reconciled.
    """

    def __init__(self, root: Path, config: HarnessConfig) -> None:
        self._root = root.absolute()
        _safe_path(self._root, "directory")
        self._budgets = self._root / ".budgets"
        _ensure_directory(self._budgets)
        self._campaign = self._budgets / config.budget_id
        self._lock_path = self._budgets / f"{config.budget_id}.lock"
        self._state_path = self._campaign / "ledger.json"
        self._budget_id = config.budget_id
        self._limits = _Limits(
            token_cap=config.token_cap,
            cost_cap_usd=config.cost_cap_usd,
            dev_cost_cap_usd=config.dev_cost_cap_usd,
        )
        with self._locked():
            created = _ensure_directory(self._campaign)
            if created:
                self._write(
                    _State(
                        budget_id=self._budget_id,
                        limits=self._limits,
                        spent=Usage.zero(),
                        dev_spent=Usage.zero(),
                        pending={},
                        unknown_operations=0,
                        completed_operations=0,
                        failed_operations=0,
                        breached=False,
                    )
                )
            self._read()

    def reserve(self, maximum: Usage, split: Split) -> str:
        """Atomically reserve the operation's aggregate worst case before dispatch."""
        if not isinstance(maximum, Usage):
            raise TypeError("operation_usage_required")
        if split not in ("dev", "test"):
            raise ValueError("invalid_budget_split")
        with self._locked():
            state = self._read()
            snapshot = self._snapshot(state)
            combined = _sum_usage(iter((snapshot.spent, snapshot.reserved, maximum)))
            dev = _sum_usage(iter((snapshot.dev_spent, snapshot.dev_reserved, maximum)))
            if (
                state.breached
                or self._exceeds(combined)
                or (split == "dev" and dev.cost_usd > self._limits.dev_cost_cap_usd)
            ):
                raise BudgetExceeded("campaign_budget_exceeded")
            reservation = uuid4().hex
            pending = {**state.pending, reservation: _Reservation(maximum=maximum, split=split)}
            self._write(state.model_copy(update={"pending": pending}))
            return reservation

    def reconcile(self, reservation_id: str, actual: Usage | None, *, failed: bool = False) -> None:
        """Persist actual/unknown usage once; failure does not make provider work free."""
        if actual is not None and not isinstance(actual, Usage):
            raise TypeError("operation_usage_required")
        if not isinstance(failed, bool):
            raise ValueError("boolean_failure_status_required")
        with self._locked():
            state = self._read()
            reservation = state.pending.get(reservation_id)
            if reservation is None:
                raise ValueError("unknown_or_completed_reservation")
            charged = reservation.maximum if actual is None else actual
            spent = _sum_usage(iter((state.spent, charged)))
            dev_spent = _sum_usage(iter((state.dev_spent, charged))) if reservation.split == "dev" else state.dev_spent
            pending = {key: value for key, value in state.pending.items() if key != reservation_id}
            breached = (
                state.breached
                or not _within(charged, reservation.maximum)
                or self._exceeds(spent)
                or dev_spent.cost_usd > self._limits.dev_cost_cap_usd
            )
            self._write(
                state.model_copy(
                    update={
                        "spent": spent,
                        "dev_spent": dev_spent,
                        "pending": pending,
                        "unknown_operations": state.unknown_operations + (actual is None),
                        "completed_operations": state.completed_operations + 1,
                        "failed_operations": state.failed_operations + failed,
                        "breached": breached,
                    }
                )
            )

    def snapshot(self) -> BudgetSnapshot:
        """Read one consistent persisted snapshot rather than an instance cache."""
        with self._locked():
            return self._snapshot(self._read())

    def _exceeds(self, usage: Usage) -> bool:
        return usage.tokens > self._limits.token_cap or usage.cost_usd > self._limits.cost_cap_usd

    @staticmethod
    def _snapshot(state: _State) -> BudgetSnapshot:
        return BudgetSnapshot(
            spent=state.spent,
            reserved=_sum_usage(item.maximum for item in state.pending.values()),
            dev_spent=state.dev_spent,
            dev_reserved=_sum_usage(item.maximum for item in state.pending.values() if item.split == "dev"),
            unknown_operations=state.unknown_operations,
            completed_operations=state.completed_operations,
            failed_operations=state.failed_operations,
            breached=state.breached,
        )

    @contextmanager
    def _locked(self) -> Iterator[None]:
        acquired = False
        try:
            for attempt in Retrying(
                stop=stop_after_delay(_LOCK_TIMEOUT_SECONDS),
                wait=wait_exponential(multiplier=0.01, min=0.01, max=0.1),
                retry=retry_if_exception_type(FileExistsError),
                reraise=True,
            ):
                with attempt:
                    _safe_path(self._root, "directory")
                    _safe_path(self._budgets, "directory")
                    try:
                        self._lock_path.mkdir(mode=0o700)
                    except FileExistsError:
                        _safe_path(self._lock_path, "directory")
                        raise
                    acquired = True
            yield
        except FileExistsError:
            raise ValueError("budget_lock_timeout") from None
        finally:
            if acquired:
                _safe_path(self._lock_path, "directory")
                self._lock_path.rmdir()

    def _read(self) -> _State:
        _safe_path(self._campaign, "directory")
        try:
            _safe_path(self._state_path, "file")
        except ValueError:
            if not self._state_path.exists() and not self._state_path.is_symlink():
                raise ValueError("invalid_budget_state") from None
            raise
        try:
            with self._state_path.open("rb") as file:
                raw = file.read(_MAX_STATE_BYTES + 1)
            if len(raw) > _MAX_STATE_BYTES:
                raise ValueError("oversized_budget_state")
            parsed = json.loads(raw, object_pairs_hook=_unique_object)
            if not isinstance(parsed, dict) or type(parsed.get("version")) is not int:
                raise ValueError("invalid_budget_version")
            state = _State.model_validate_json(canonical_json(parsed))
        except (ValueError, TypeError, OverflowError):
            raise ValueError("invalid_budget_state") from None
        if state.budget_id != self._budget_id:
            raise ValueError("invalid_budget_state")
        if state.limits != self._limits:
            raise ValueError("budget_limits_changed")
        snapshot = self._snapshot(state)
        combined = _sum_usage(iter((snapshot.spent, snapshot.reserved)))
        dev = _sum_usage(iter((snapshot.dev_spent, snapshot.dev_reserved)))
        if not state.breached and (self._exceeds(combined) or dev.cost_usd > self._limits.dev_cost_cap_usd):
            raise ValueError("invalid_budget_state")
        return state

    def _write(self, state: _State) -> None:
        _safe_path(self._campaign, "directory")
        _safe_path(self._state_path, "file", missing=True)
        payload = canonical_json(state.model_dump(mode="json"))
        if len(payload) > _MAX_STATE_BYTES:
            raise ValueError("oversized_budget_state")
        temporary = self._campaign / f".ledger-{uuid4().hex}.tmp"
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            created = True
            with os.fdopen(descriptor, "wb") as file:
                file.write(payload)
                file.flush()
                os.fsync(file.fileno())
            _safe_path(self._state_path, "file", missing=True)
            os.replace(temporary, self._state_path)
            if os.name != "nt":
                directory = os.open(self._campaign, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        finally:
            if created:
                temporary.unlink(missing_ok=True)
