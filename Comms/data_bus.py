# Comms/data_bus.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Union, Callable, List
import threading
import time

Number = Union[int, float]


def ts_now() -> str:
    t = time.time()
    lt = time.localtime(t)
    ms = int((t - int(t)) * 1000)
    return time.strftime("%Y-%m-%d %H:%M:%S", lt) + f".{ms:03d}"


@dataclass(frozen=True)
class TimedFrame:
    """A measurement snapshot with timing metadata."""
    t_unix: float
    t_str: str
    data: Dict[str, Number]


@dataclass(frozen=True)
class CommandEvent:
    """A command emission record (for debugging / traceability)."""
    t_unix: float
    t_str: str
    channel: str
    updates: Dict[str, Number]
    note: str = ""


class DataBus:
    """
    Thread-safe shared state for:
      - latest measurements per channel (+ timestamp)
      - command events log (+ timestamp)
    Buffers should read measurements from here and send commands via here.
    """

    def __init__(
        self,
        *,
        log = print,
        cmd_log_max: int = 500,
        rx_log_channels: Optional[set[str]] = None,
        rx_log_fields: Optional[dict[str, set[str]]] = None,
    ) -> None:
        self._lock = threading.Lock()
        self._meas: Dict[str, TimedFrame] = {}
        self._cmd_log: List[CommandEvent] = []
        self._cmd_log_max = cmd_log_max
        self.log = log

        self.rx_log_channels = rx_log_channels
        self.rx_log_fields = rx_log_fields

    # ------------------------- Measurements -------------------------

    def update_meas(self, channel_name: str, meas: Dict[str, Number]) -> None:
        """Called by comms RX callbacks (on_meas)."""
        t = time.time()
        tf = TimedFrame(t_unix=t, t_str=ts_now(), data=dict(meas))
        with self._lock:
            self._meas[channel_name] = tf

        # #RX logging (filtered)
        # if self.log and self.rx_log_channels and channel_name in self.rx_log_channels:
        #     self.log(f"[{tf.t_str}][RX-ARMED][{channel_name}] {tf.data}")

        if self.log and self.rx_log_channels and channel_name in self.rx_log_channels:
            fields = None
            if self.rx_log_fields and channel_name in self.rx_log_fields:
                fields = self.rx_log_fields[channel_name]
                data = {k: meas.get(k) for k in fields}
            else:
                data = meas

            self.log(f"[{tf.t_str}][RX-ARMED][{channel_name}] {data}")

    def get_meas(self, channel_name: str) -> Optional[TimedFrame]:
        with self._lock:
            return self._meas.get(channel_name)

    def snapshot_all(self) -> Dict[str, TimedFrame]:
        with self._lock:
            return dict(self._meas)

    # ------------------------- Commands ----------------------------

    def emit_cmd(self, channel, channel_name: str, updates: Dict[str, Number], *, note: str = "") -> None:
        """
        Central place to send commands. This is what your buffers should call.
        `channel` is a GtnetChannel instance (or anything with set_cmd()).
        """
        channel.set_cmd(updates)

        evt = CommandEvent(
            t_unix=time.time(),
            t_str=ts_now(),
            channel=channel_name,
            updates=dict(updates),
            note=note,
        )

        with self._lock:
            self._cmd_log.append(evt)
            if len(self._cmd_log) > self._cmd_log_max:
                self._cmd_log = self._cmd_log[-self._cmd_log_max :]

        if self.log:
            self.log(f"[{evt.t_str}][TX-ARMED][{channel_name}] {updates}" + (f"  ({note})" if note else ""))

    def get_cmd_log(self) -> List[CommandEvent]:
        with self._lock:
            return list(self._cmd_log)
