from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Callable, Union, Set
import random
import threading
import time

Number = Union[int, float]


def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x


@dataclass
class DataDegradationConfig:
    """
    Data degraded: telemetry/measurement path disruption (per-channel).
    """
    enabled: bool = False

    # Drop / freeze
    drop_prob: float = 0.0
    freeze: bool = False

    # Delay
    fixed_delay_s: float = 0.0
    jitter_s: float = 0.0

    # Optional perturbation
    add_noise: bool = False
    noise_std: float = 0.0

    # Optional: only apply degradation to these keys; if None -> all keys
    keys: Optional[Set[str]] = None


@dataclass
class AutonomyDegradationConfig:
    """
    Autonomy degraded: command/control path disruption (per-channel).
    """
    enabled: bool = False

    block_all: bool = False
    drop_prob: float = 0.0

    fixed_delay_s: float = 0.0
    jitter_s: float = 0.0


class ChannelDisruptor:
    """
    Per-channel disruption interposer.

    Wire like:
      chX = GtnetChannel(..., on_meas=lambda m: disruptor.on_meas("CHX", m))
    And send commands with:
      disruptor.emit_cmd(chX, "CHX", {...}, note="...")
    """

    def __init__(
        self,
        *,
        meas_sink: Callable[[str, Dict[str, Number]], None],
        cmd_sink: Callable[[object, str, Dict[str, Number], str], None],
        logger: Optional[Callable[[str], None]] = print,
        rng_seed: Optional[int] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._meas_sink = meas_sink
        self._cmd_sink = cmd_sink
        self._log = logger
        self._rng = random.Random(rng_seed)

        # per-channel configs
        self._data_cfg: Dict[str, DataDegradationConfig] = {}
        self._auto_cfg: Dict[str, AutonomyDegradationConfig] = {}

        # last delivered/seen per channel (for freeze behavior)
        self._last_meas: Dict[str, Dict[str, Number]] = {}

    # -------------------------
    # Configuration API
    # -------------------------
    def set_data_degraded(self, channel_name: str, cfg: DataDegradationConfig) -> None:
        cfg.drop_prob = _clamp(cfg.drop_prob, 0.0, 1.0)
        cfg.fixed_delay_s = max(0.0, cfg.fixed_delay_s)
        cfg.jitter_s = max(0.0, cfg.jitter_s)
        cfg.noise_std = max(0.0, cfg.noise_std)

        with self._lock:
            self._data_cfg[channel_name] = cfg

        if self._log:
            self._log(f"[DISRUPT][DATA][{channel_name}] {cfg}")

    def set_autonomy_degraded(self, channel_name: str, cfg: AutonomyDegradationConfig) -> None:
        cfg.drop_prob = _clamp(cfg.drop_prob, 0.0, 1.0)
        cfg.fixed_delay_s = max(0.0, cfg.fixed_delay_s)
        cfg.jitter_s = max(0.0, cfg.jitter_s)

        with self._lock:
            self._auto_cfg[channel_name] = cfg

        if self._log:
            self._log(f"[DISRUPT][AUTO][{channel_name}] {cfg}")

    def clear_channel(self, channel_name: str) -> None:
        with self._lock:
            self._data_cfg.pop(channel_name, None)
            self._auto_cfg.pop(channel_name, None)
        if self._log:
            self._log(f"[DISRUPT] cleared channel {channel_name}")

    def clear_all(self) -> None:
        with self._lock:
            self._data_cfg.clear()
            self._auto_cfg.clear()
        if self._log:
            self._log("[DISRUPT] cleared all channels")

    # -------------------------
    # RX path: telemetry
    # -------------------------
    def on_meas(self, channel_name: str, meas: Dict[str, Number]) -> None:
        with self._lock:
            cfg = self._data_cfg.get(channel_name, DataDegradationConfig(enabled=False))

        # keep last seen
        self._last_meas[channel_name] = dict(meas)

        if not cfg.enabled:
            self._meas_sink(channel_name, meas)
            return

        # Freeze: deliver last stored frame, ignore new updates
        if cfg.freeze:
            frozen = self._last_meas.get(channel_name)
            if frozen is not None:
                self._meas_sink(channel_name, frozen)
            return

        # Drop entire frame probabilistically
        if cfg.drop_prob > 0.0 and self._rng.random() < cfg.drop_prob:
            if self._log:
                self._log(f"[DISRUPT][DATA][{channel_name}] dropped telemetry frame")
            return

        # Apply per-key degradation if cfg.keys is set
        out = dict(meas)
        if cfg.keys is not None:
            # Only degrade selected keys; others pass through untouched.
            # For drop/freeze already handled above. Here we do delay/noise per-key.
            pass  # handled below in noise and delay with keys

        # Optional noise on numeric values (only on selected keys if provided)
        if cfg.add_noise and cfg.noise_std > 0.0:
            keys = cfg.keys
            for k, v in list(out.items()):
                if keys is not None and k not in keys:
                    continue
                if isinstance(v, (int, float)):
                    out[k] = float(v) + self._rng.gauss(0.0, cfg.noise_std)

        # Delay (fixed + jitter)
        delay = cfg.fixed_delay_s
        if cfg.jitter_s > 0.0:
            delay += self._rng.uniform(-cfg.jitter_s, cfg.jitter_s)
            delay = max(0.0, delay)

        if delay <= 0.0:
            self._meas_sink(channel_name, out)
            return

        threading.Thread(
            target=self._delayed_meas_delivery,
            args=(channel_name, out, delay),
            daemon=True,
        ).start()

    def _delayed_meas_delivery(self, channel_name: str, meas: Dict[str, Number], delay_s: float) -> None:
        time.sleep(delay_s)
        self._meas_sink(channel_name, meas)

    # -------------------------
    # TX path: commands
    # -------------------------
    def emit_cmd(self, channel_obj: object, channel_name: str, updates: Dict[str, Number], note: str = "") -> None:
        with self._lock:
            cfg = self._auto_cfg.get(channel_name, AutonomyDegradationConfig(enabled=False))

        if not cfg.enabled:
            self._cmd_sink(channel_obj, channel_name, updates, note)
            return

        if cfg.block_all:
            if self._log:
                self._log(f"[DISRUPT][AUTO][{channel_name}] BLOCKED cmd {updates} ({note})")
            return

        if cfg.drop_prob > 0.0 and self._rng.random() < cfg.drop_prob:
            if self._log:
                self._log(f"[DISRUPT][AUTO][{channel_name}] DROPPED cmd {updates} ({note})")
            return

        delay = cfg.fixed_delay_s
        if cfg.jitter_s > 0.0:
            delay += self._rng.uniform(-cfg.jitter_s, cfg.jitter_s)
            delay = max(0.0, delay)

        if delay <= 0.0:
            self._cmd_sink(channel_obj, channel_name, updates, note)
            return

        threading.Thread(
            target=self._delayed_cmd_delivery,
            args=(channel_obj, channel_name, dict(updates), note, delay),
            daemon=True,
        ).start()

    def _delayed_cmd_delivery(self, channel_obj: object, channel_name: str, updates: Dict[str, Number], note: str, delay_s: float) -> None:
        time.sleep(delay_s)
        self._cmd_sink(channel_obj, channel_name, updates, note)

