"""Small helpers for building safe RTDS commands.

Place this file at: Comms/command_helpers.py

These helpers keep the CLI code readable and ensure values are within
expected ranges/types before they are transmitted.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Range:
    lo: float
    hi: float

    def clamp(self, x: float) -> float:
        return max(self.lo, min(self.hi, x))


def _require(condition: bool, msg: str) -> None:
    if not condition:
        raise ValueError(msg)


def set_selector(channel, name: str, value: int) -> None:
    """0/1 selector (int)."""
    _require(value in (0, 1), f"{name} must be 0 or 1 (got {value})")
    channel.set_cmd({name: int(value)})


def pb_pulse(channel, name: str, pulse_s: float = 0.1) -> None:
    """Pushbutton pulse: send 1 then 0 after pulse_s seconds."""
    _require(pulse_s > 0, "pulse_s must be > 0")
    channel.set_cmd({name: 1})
    # keep this sleep short; RTDS side should edge-detect / monostable.
    import time

    time.sleep(pulse_s)
    channel.set_cmd({name: 0})


def set_dial(channel, name: str, value: int, *, lo: int, hi: int) -> None:
    """Dial/enum integer within [lo, hi]."""
    _require(isinstance(value, int), f"{name} must be int")
    _require(lo <= value <= hi, f"{name} must be within [{lo}, {hi}] (got {value})")
    channel.set_cmd({name: int(value)})


def set_slider(
    channel,
    name: str,
    value: float,
    *,
    lo: float,
    hi: float,
    clamp: bool = True,
) -> float:
    """Slider float within [lo, hi].

    If clamp=True (default), the value is clamped to bounds.
    Returns the transmitted value (possibly clamped).
    """
    _require(lo < hi, f"Invalid range for {name}: lo must be < hi")
    v = float(value)
    if clamp:
        v = max(lo, min(hi, v))
    else:
        _require(lo <= v <= hi, f"{name} must be within [{lo}, {hi}] (got {v})")
    channel.set_cmd({name: v})
    return v
