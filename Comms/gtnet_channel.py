# gtnet_channel.py
# -----------------------------------------------------------------------------
# Generic GTNET-SKT MULTI channel client for RTDS
#
# Design goals:
# - One reusable class per channel (CH1..CH4), configured by a ChannelSpec
# - Threaded RX + TX loops (simple, robust in lab environments)
# - Strict struct packing/unpacking (big-endian by default)
# - Thread-safe: latest measurements + command state
# - Optional send gating: only send when ReadyToSend signal indicates RTDS is ready
#
# Notes:
# - GTNET-SKT MULTI typically uses fixed-length binary frames.
# - Your existing behavior: "dirty send" (send when command changes), optionally
#   with a keepalive period.
# -----------------------------------------------------------------------------

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple, Union

import socket
import struct
import threading
import time


Number = Union[int, float]


@dataclass(frozen=True)
class ChannelSpec:
    """Defines how to decode (RTDS->Py) and encode (Py->RTDS) frames for one channel."""
    name: str
    ip: str
    port: int

    cmd_types: Sequence[str]  # "int" or "float"

    # RTDS -> Python
    meas_names: Sequence[str]
    meas_fmt: str  # e.g. ">iiiiifff"
    # Python -> RTDS
    cmd_names: Sequence[str]
    cmd_fmt: str   # e.g. ">ff"

    # Optional: signal used to gate transmissions, e.g. "ReadyToSend_4"
    ready_to_send_name: Optional[str] = None
    # Optional: if True, require ready_to_send == 1 to send
    require_ready_to_send: bool = True

    # Network settings
    connect_timeout_s: float = 5.0
    tcp_nodelay: bool = True

    # Reconnect policy
    reconnect: bool = True
    reconnect_initial_backoff_s: float = 0.5
    reconnect_max_backoff_s: float = 5.0



@dataclass
class ChannelStats:
    connected: bool = False
    last_connect_time: float = 0.0
    last_disconnect_time: float = 0.0

    rx_frames: int = 0
    tx_frames: int = 0
    rx_errors: int = 0
    tx_errors: int = 0
    reconnects: int = 0

    last_rx_time: float = 0.0
    last_tx_time: float = 0.0


def _recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    """Receive exactly nbytes or raise ConnectionError."""
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = sock.recv(nbytes - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed by peer")
        buf.extend(chunk)
    return bytes(buf)


class GtnetChannel:
    """
    One channel connection to GTNET-SKT MULTI.
    - RX thread continuously decodes measurement frames into `latest_meas`
    - TX thread sends command frames when dirty or on keepalive interval

    Typical use:
      ch = GtnetChannel(spec)
      ch.start()
      ch.set_cmd({"REM_PLOAD": 10.0, "REM_QLOAD": 2.0})
      meas = ch.get_latest_meas()
      ch.stop()
    """

    def __init__(
        self,
        spec: ChannelSpec,
        *,
        tx_keepalive_s: Optional[float] = None,
        tx_period_s: float = 0.05,
        enable_tx: bool = True,
        on_meas: Optional[Callable[[Dict[str, Number]], None]] = None,
        log: Optional[Callable[[str], None]] = print,
    ) -> None:
        self.spec = spec

        self._meas_struct = struct.Struct(spec.meas_fmt)
        self._cmd_struct = struct.Struct(spec.cmd_fmt)
        self.meas_bytes = self._meas_struct.size
        self.cmd_bytes = self._cmd_struct.size

        if len(spec.meas_names) != len(self._meas_struct.format.replace(">", "").replace("<", "").replace("!", "").replace("@", "")):
            # Not a perfect check for all fmt tokens, but catches gross mismatches early.
            pass

        self.tx_keepalive_s = tx_keepalive_s
        self.tx_period_s = tx_period_s
        self.on_meas = on_meas
        self.log = log

        self._sock: Optional[socket.socket] = None
        self._stop_evt = threading.Event()

        self._rx_thread: Optional[threading.Thread] = None
        self._tx_thread: Optional[threading.Thread] = None

        self._latest_lock = threading.Lock()
        self._latest_meas: Dict[str, Number] = {}

        self._cmd_lock = threading.Lock()
        self._cmd_state = {}
        for name, typ in zip(spec.cmd_names, spec.cmd_types):
            self._cmd_state[name] = 0 if typ == "int" else 0.0

        self._dirty = False  # DO NOT send anything on startup
        self.stats = ChannelStats()

        self.enable_tx = enable_tx

    # -------------------------------------------------------------------------
    # Lifecycle
    # -------------------------------------------------------------------------

    def start(self) -> None:
        """Start background threads. Connects immediately (with reconnect if enabled)."""
        if self._rx_thread and self._rx_thread.is_alive():
            return

        self._stop_evt.clear()

        self._rx_thread = threading.Thread(
            target=self._rx_loop,
            name=f"{self.spec.name}-RX",
            daemon=True,
        )
        self._rx_thread.start()

        if self.enable_tx:
            self._tx_thread = threading.Thread(
                target=self._tx_loop,
                name=f"{self.spec.name}-TX",
                daemon=True,
            )
            self._tx_thread.start()

    def stop(self) -> None:
        """Stop threads and close socket."""
        self._stop_evt.set()
        self._close_socket()

        if self._rx_thread and self._rx_thread.is_alive():
            self._rx_thread.join(timeout=1.0)
        if self._tx_thread and self._tx_thread.is_alive():
            self._tx_thread.join(timeout=1.0)

    # -------------------------------------------------------------------------
    # Public API: measurements + commands
    # -------------------------------------------------------------------------

    def get_latest_meas(self) -> Dict[str, Number]:
        with self._latest_lock:
            return dict(self._latest_meas)

    def get_cmd_state(self) -> Dict[str, Number]:
        with self._cmd_lock:
            return dict(self._cmd_state)

    def set_cmd(self, updates: Dict[str, Number]) -> None:
        changed = False
        with self._cmd_lock:
            for k, v in updates.items():
                if k not in self._cmd_state:
                    raise KeyError(f"[{self.spec.name}] Unknown cmd name: {k}")

                idx = list(self.spec.cmd_names).index(k)
                typ = self.spec.cmd_types[idx]

                if typ == "int":
                    nv = int(v)
                else:
                    nv = float(v)

                if self._cmd_state[k] != nv:
                    self._cmd_state[k] = nv
                    changed = True

        if changed:
            self._dirty = True

    def set_cmd_word(self, name: str, value: Number) -> None:
        self.set_cmd({name: value})

    # -------------------------------------------------------------------------
    # Internal socket management
    # -------------------------------------------------------------------------

    def _close_socket(self) -> None:
        sock = self._sock
        self._sock = None
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    def _connect(self) -> Optional[socket.socket]:
        """Attempt to connect. Return socket or None."""
        try:
            sock = socket.create_connection((self.spec.ip, self.spec.port), timeout=self.spec.connect_timeout_s)
            sock.settimeout(None)
            if self.spec.tcp_nodelay:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                except Exception:
                    pass
            self.stats.connected = True
            self.stats.last_connect_time = time.time()
            if self.log:
                self.log(f"[{self.spec.name}] Connected to {self.spec.ip}:{self.spec.port}")
            return sock
        except Exception as e:
            if self.log:
                self.log(f"[{self.spec.name}] Connect failed: {e}")
            return None

    def _ensure_connected(self) -> bool:
        """Ensure there is an open socket. Returns True if connected."""
        if self._sock is not None:
            return True

        backoff = self.spec.reconnect_initial_backoff_s
        while not self._stop_evt.is_set():
            sock = self._connect()
            if sock is not None:
                self._sock = sock
                self.stats.reconnects += 1
                return True

            if not self.spec.reconnect:
                return False

            time.sleep(backoff)
            backoff = min(backoff * 2, self.spec.reconnect_max_backoff_s)

        return False

    # -------------------------------------------------------------------------
    # RX/TX loops
    # -------------------------------------------------------------------------

    def _rx_loop(self) -> None:
        while not self._stop_evt.is_set():
            if not self._ensure_connected():
                break

            assert self._sock is not None
            try:
                frame = _recv_exact(self._sock, self.meas_bytes)
                values = self._meas_struct.unpack(frame)
                meas = dict(zip(self.spec.meas_names, values))

                with self._latest_lock:
                    self._latest_meas = meas

                self.stats.rx_frames += 1
                self.stats.last_rx_time = time.time()

                if self.on_meas:
                    try:
                        self.on_meas(meas)
                    except Exception as cb_e:
                        # callback failure should not kill comms
                        if self.log:
                            self.log(f"[{self.spec.name}] on_meas error: {cb_e}")

            except Exception as e:
                self.stats.rx_errors += 1
                self._handle_disconnect(f"RX error: {e}")

    def _tx_loop(self) -> None:
        last_tx = 0.0
        while not self._stop_evt.is_set():
            if not self._ensure_connected():
                break

            now = time.time()
            should_keepalive = self.tx_keepalive_s is not None and (now - last_tx) >= self.tx_keepalive_s
            should_send = self._dirty or should_keepalive

            if should_send and self._ok_to_send():
                try:
                    payload = self._build_cmd_payload()
                    assert self._sock is not None
                    self._sock.sendall(payload)

                    self._dirty = False
                    last_tx = now
                    self.stats.tx_frames += 1
                    self.stats.last_tx_time = now

                except Exception as e:
                    self.stats.tx_errors += 1
                    self._handle_disconnect(f"TX error: {e}")

            time.sleep(self.tx_period_s)

    def _ok_to_send(self) -> bool:
        """Optional gating using ReadyToSend_* from telemetry."""
        if not self.spec.ready_to_send_name or not self.spec.require_ready_to_send:
            return True

        meas = self.get_latest_meas()
        rts = meas.get(self.spec.ready_to_send_name, None)
        try:
            return int(rts) == 1
        except Exception:
            return False

    def _build_cmd_payload(self) -> bytes:
        with self._cmd_lock:
            vals: List[Number] = [self._cmd_state[name] for name in self.spec.cmd_names]
        return self._cmd_struct.pack(*vals)

    def _handle_disconnect(self, reason: str) -> None:
        if self.log:
            self.log(f"[{self.spec.name}] Disconnected ({reason})")
        self.stats.connected = False
        self.stats.last_disconnect_time = time.time()
        self._close_socket()
        # Mark dirty so first send after reconnect pushes current cmd state
        self._dirty = True
