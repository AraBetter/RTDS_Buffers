# CmdPushButton.py  (Microgrid1 CH1 console: telemetry(12) + commands(4))
#
# Reads RTDS->Python telemetry (12 words) and sends Python->RTDS commands (4 words)
# for GTNET-SKT MULTI Channel 1 on port 7000.
#
# IMPORTANT:
#  - Packing/unpacking order MUST match GTNET-SKT tabs exactly. (RTDS example stresses this.) :contentReference[oaicite:2]{index=2}
#  - Big-endian network order is typical: use ">" in struct formats. :contentReference[oaicite:3]{index=3}
#
# Commands you can type:
#   status
#   grid
#   island
#   fault
#   dur <cycles>
#   type <0..7>
#   quit

import socket
import struct
import threading
import time

GTNET_IP = "172.24.4.3"
PORT_CH1 = 7000

# --- Fault pulse behavior (pushbutton) ---
PULSE_S = 0.1# hold REM_LGFLTx=1 for this many seconds, then clear to 0

# --- How often to transmit the current command frame (keeps things “fresh”) ---
TX_KEEPALIVE_S = 999999

# --- If your RTDS expects duration in seconds, convert cycles->seconds using this frequency ---
SYSTEM_FREQ_HZ = 60.0

# =============================================================================
# RTDS -> Python telemetry frame (12 words)  [must match "To GTNET-SKT - 1"]
# Order per your screenshot:
#   0 NewDataFlag_1    int
#   1 NewDataSeq_1     int
#   2 ReadyToSend_1    int
#   3 SocketOverflow_1 int
#   4 InvalidMsg_1     int
#   5 MODE             int
#   6 PGRID            float
#   7 QGRID            float
#   8 N650RMSPU        float
#   9 IGRIDA           float
#  10 IGRIDB           float
#  11 IGRIDC           float
# =============================================================================
MEAS_NAMES = [
    "NewDataFlag_1",
    "NewDataSeq_1",
    "ReadyToSend_1",
    "SocketOverflow_1",
    "InvalidMsg_1",
    "MODE",
    "PGRID",
    "QGRID",
    "N650RMSPU",
    "IGRIDA",
    "IGRIDB",
    "IGRIDC",
]
MEAS_FMT = ">iiiiiiffffff"  # 6x int32 + 6x float32 (big-endian)
MEAS_BYTES = struct.calcsize(MEAS_FMT)

# =============================================================================
# Python -> RTDS command frame (4 words)  [must match "From GTNET-SKT - 1"]
# Your screenshot shows numVarsFromGTNETSKT_1 = 4:
#   0 REM_GRID        int   (grid=1, island=0)
#   1 REM_LGFLTx      int   (fault pushbutton pulse 1 then 0)
#   2 REM_LGFTIMEx    float (fault duration)
#   3 REM_LGFLTxType  int   (0..7)
# =============================================================================
CMD_NAMES = ["REM_GRID", "REM_LGFLTx", "REM_LGFTIMEx", "REM_LGFLTxType"]
CMD_FMT = ">iifi"  # int32, int32, float32, int32  (big-endian)
CMD_BYTES = struct.calcsize(CMD_FMT)


def _recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    """Receive exactly nbytes or raise ConnectionError."""
    buf = bytearray()
    while len(buf) < nbytes:
        chunk = sock.recv(nbytes - len(buf))
        if not chunk:
            raise ConnectionError("Socket closed by peer")
        buf.extend(chunk)
    return bytes(buf)


class RTDSClient:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port

        self.sock: socket.socket | None = None
        self.stop_evt = threading.Event()

        self.rx_thread: threading.Thread | None = None
        self.tx_thread: threading.Thread | None = None

        self.latest_lock = threading.Lock()
        self.latest: dict[str, int | float] = {}

        self.cmd_lock = threading.Lock()
        # command state (latched values)
        self.cmd_state = {
            "REM_GRID": 0,
            "REM_LGFLTx": 0,
            "REM_LGFTIMEx": 0.083333,  # default ~5 cycles @60Hz (if seconds)
            "REM_LGFLTxType": 1,
        }

        self._dirty = True
        self._last_tx = 0.0

    def connect(self) -> None:
        self.sock = socket.create_connection((self.ip, self.port), timeout=5.0)
        self.sock.settimeout(None)

        self.stop_evt.clear()
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.tx_thread = threading.Thread(target=self._tx_loop, daemon=True)
        self.rx_thread.start()
        self.tx_thread.start()

        print(f"[OK] Connected to {self.ip}:{self.port}")
        print(f"[INFO] MEAS_BYTES={MEAS_BYTES} (12 words): {MEAS_FMT}")
        print(f"[INFO] CMD_BYTES={CMD_BYTES} (4 words):  {CMD_FMT}")

    def stop(self) -> None:
        self.stop_evt.set()
        try:
            if self.sock:
                self.sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            if self.sock:
                self.sock.close()
        except Exception:
            pass
        self.sock = None

        if self.rx_thread and self.rx_thread.is_alive():
            self.rx_thread.join(timeout=1.0)
        if self.tx_thread and self.tx_thread.is_alive():
            self.tx_thread.join(timeout=1.0)

    def _rx_loop(self) -> None:
        assert self.sock is not None
        try:
            while not self.stop_evt.is_set():
                frame = _recv_exact(self.sock, MEAS_BYTES)
                values = struct.unpack(MEAS_FMT, frame)
                m = dict(zip(MEAS_NAMES, values))
                with self.latest_lock:
                    self.latest = m
        except Exception as e:
            if not self.stop_evt.is_set():
                print(f"[ERROR] RX loop stopped: {e}")
            self.stop_evt.set()

    def _tx_loop(self) -> None:
        assert self.sock is not None
        try:
            while not self.stop_evt.is_set():
                now = time.time()
                do_send = self._dirty or (now - self._last_tx) >= TX_KEEPALIVE_S

                if do_send:

                    self._dirty = False
                    payload = self._build_cmd_payload()
                    self.sock.sendall(payload)
                    self._last_tx = now

                time.sleep(0.1)
        except Exception as e:
            if not self.stop_evt.is_set():
                print(f"[ERROR] TX loop stopped: {e}")
            self.stop_evt.set()

    def _build_cmd_payload(self) -> bytes:
        """Build command frame. If MODE=0, force safe actuation (grid/island/fault)."""
        m = self.get_latest()
        mode_val = int(m.get("MODE", 0)) if m else 0

        with self.cmd_lock:
            rem_grid = int(self.cmd_state["REM_GRID"])
            rem_fault = int(self.cmd_state["REM_LGFLTx"])
            rem_dur = float(self.cmd_state["REM_LGFTIMEx"])
            rem_type = int(self.cmd_state["REM_LGFLTxType"])

        # Block actuation in MANUAL (MODE=0). Keep configured duration/type (harmless until you actuate).
        if mode_val == 0:
            rem_grid = 0
            rem_fault = 0

        return struct.pack(CMD_FMT, rem_grid, rem_fault, rem_dur, rem_type)

    def get_latest(self) -> dict[str, int | float]:
        with self.latest_lock:
            return dict(self.latest)

    def _mode_gate(self) -> bool:
        """MODE=0 => block actions; MODE=1 => allow."""
        m = self.get_latest()
        if not m:
            print("[WARN] No telemetry yet; cannot validate MODE.")
            return False

        try:
            mode_val = int(m["MODE"])
        except Exception:
            print("[WARN] MODE not parseable yet.")
            return False

        if mode_val == 0:
            print("[BLOCKED] MODE=0 (MANUAL). Switch MODE to REMOTE (1) first.")
            return False

        return True

    def _mark_dirty(self) -> None:
        self._dirty = True

    # ----------------- user actions -----------------

    def status(self) -> None:
        m = self.get_latest()
        if not m:
            print("[INFO] No telemetry yet.")
            return

        print(
            f"MODE={int(m['MODE'])}  ReadyToSend_1={int(m['ReadyToSend_1'])}  "
            f"NewDataSeq_1={int(m['NewDataSeq_1'])}  NewDataFlag_1={int(m['NewDataFlag_1'])}  "
            f"InvalidMsg_1={int(m['InvalidMsg_1'])}  SocketOverflow_1={int(m['SocketOverflow_1'])}"
        )
        print(
            f"PGRID={float(m['PGRID']):.3f}  QGRID={float(m['QGRID']):.3f}  "
            f"N650RMSPU={float(m['N650RMSPU']):.3f}"
        )
        print(
            f"IGRID(A,B,C)=({float(m['IGRIDA']):.3f}, {float(m['IGRIDB']):.3f}, {float(m['IGRIDC']):.3f})"
        )

        with self.cmd_lock:
            print(
                f"CMD: REM_GRID={self.cmd_state['REM_GRID']}  REM_LGFLTx={self.cmd_state['REM_LGFLTx']}  "
                f"REM_LGFTIMEx={self.cmd_state['REM_LGFTIMEx']:.6f}  REM_LGFLTxType={self.cmd_state['REM_LGFLTxType']}"
            )

    def set_grid(self, grid_on: bool) -> None:
        if not self._mode_gate():
            return
        with self.cmd_lock:
            self.cmd_state["REM_GRID"] = 1 if grid_on else 0
        self._mark_dirty()
        print(f"[TX-ARMED] REM_GRID={'1 (GRID)' if grid_on else '0 (ISLAND)'}")

    def fault_pulse(self) -> None:
        if not self._mode_gate():
            return

        # clean edge: 1 then 0
        with self.cmd_lock:
            self.cmd_state["REM_LGFLTx"] = 1
        self._mark_dirty()
        time.sleep(PULSE_S)

        with self.cmd_lock:
            self.cmd_state["REM_LGFLTx"] = 0
        self._mark_dirty()
        print("[TX-ARMED] REM_LGFLTx pulse (1 then 0)")

    def set_fault_type(self, fault_type: int) -> None:
        if not (0 <= fault_type <= 7):
            print("[ERROR] fault type must be 0..7")
            return
        with self.cmd_lock:
            self.cmd_state["REM_LGFLTxType"] = int(fault_type)
        self._mark_dirty()
        print(f"[SET] REM_LGFLTxType={fault_type}")

    def set_fault_duration_cycles(self, cycles: float) -> None:
        # You said duration is entered as cycles; RTDS variable name suggests time.
        # Default behavior here: convert cycles -> seconds using SYSTEM_FREQ_HZ.
        seconds = float(cycles) / SYSTEM_FREQ_HZ

        with self.cmd_lock:
            self.cmd_state["REM_LGFTIMEx"] = seconds
        self._mark_dirty()
        print(f"[SET] REM_LGFTIMEx={seconds:.6f} s  (from {cycles} cycles @ {SYSTEM_FREQ_HZ} Hz)")


def main() -> None:
    client = RTDSClient(GTNET_IP, PORT_CH1)
    client.connect()

    print("\nCommands:")
    print("  status                 -> print MODE + key telemetry + cmd state")
    print("  grid                   -> REM_GRID=1")
    print("  island                 -> REM_GRID=0")
    print("  fault                  -> pulse REM_LGFLTx (1 then 0)")
    print("  dur <cycles>           -> set REM_LGFTIMEx from fault cycles (converted to seconds)")
    print("  type <0..7>            -> set REM_LGFLTxType")
    print("  quit                   -> exit\n")

    try:
        while True:
            line = input(">> ").strip().lower()
            if not line:
                continue

            if line == "quit":
                break

            if line == "status":
                client.status()

            elif line == "grid":
                client.set_grid(True)

            elif line == "island":
                client.set_grid(False)

            elif line == "fault":
                client.fault_pulse()

            elif line.startswith("dur "):
                try:
                    cycles = float(line.split()[1])
                    client.set_fault_duration_cycles(cycles)
                except Exception:
                    print("[ERROR] usage: dur <cycles> (e.g., dur 5)")

            elif line.startswith("type "):
                try:
                    t = int(line.split()[1])
                    client.set_fault_type(t)
                except Exception:
                    print("[ERROR] usage: type <0..7> (e.g., type 3)")

            else:
                print("Unknown command. Use: status / grid / island / fault / dur <cycles> / type <0..7> / quit")

    finally:
        # RTDS example suggests a short sleep before close can help the close sequence. :contentReference[oaicite:4]{index=4}
        time.sleep(0.2)
        client.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()


