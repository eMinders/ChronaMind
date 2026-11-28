"""Transaction-consistent live/replay clocks for the isolated engine."""

from .allocation import ClockError, ClockTick, advance_instant, allocate_clock

__all__ = ["ClockError", "ClockTick", "advance_instant", "allocate_clock"]
