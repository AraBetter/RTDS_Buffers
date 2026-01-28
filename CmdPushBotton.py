# CmdPushButton.py  (latched + pulse pushbutton tester for RTDS GTNET-SKT)
#
# What this file does:
# - Continuously reads MODE from RTDS (so we can block commands when MODE=MANUAL)
# - Lets you type:
#     status  -> prints MODE
#     press   -> sends a momentary pulse (1 then 0)
#     on      -> latches command ON (sets 1 and holds)
#     off     -> clears command (sets 0)
#     quit    -> exit
#
# IMPORTANT:
# - Update ONLY the "USER EDIT" section per command you are testing.
# - Make sure RTDS "From GTNET-SKT" input type matches CMD_TYPE (int32 vs float32)
# - This assumes big-endian network order (GTNET-SKT typical): ">" in struct formats.

import socket
import struct
import threading
import time

GTNET_IP = "172.24.4.3"
PORT_CH1 = 7000

# ===== USER EDIT (ONLY THESE PER COMMAND) =====================================
CMD_NAME = "REM_LGFLTx"   # <-- RTDS variable name
CMD_TYPE = "int"          # "int" for pushbuttons/dials/selectors, "float" for sliders
PULSE_S  = 1         # seconds to hold ON during "press" before clearing to 0
# =============================================================================

# ===== MEASUREMENTS COMING FROM RTDS (must match GTNET-SKT "To GTNET-SKT") =====

MEAS_NAMES = ["Ch1_Test_TX", "MODE"]  # RTDS -> Python
MEAS_FMT   = ">fi"                   # float32, int32 (big-endian)
MEAS_BYTES = struct.calcsize(MEAS_FMT)
# =============================================================================

# ===== COMMANDS GOING TO RTDS (must match GTNET-SKT "From GTNET-SKT") ==========
# We always send a heartbeat float + one command value.
# Heartbeat is useful to verify comms on the RTDS side (and helps you debug).
HB_NAME = "Ch1_Test_RX"   # Python -> RTDS heartbeat signal name (for your own tracking)

CMD_FMT = ">fi" if CMD_TYPE.lower() == "int" else ">ff"   # heartbeat + (int32 or float32)
CMD_BYTES = struct.calcsize(CMD_FMT)
# =============================================================================


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
        self.rx_thread: threading.Thread | None = None
        self.stop_evt = threading.Event()

        self.latest_lock = threading.Lock()
        self.latest: dict[str, float | int] = {}

        self.send_lock = threading.Lock()
        self.hb_value = 10.0  # arbitrary starting heartbeat

    def connect(self) -> None:
        self.sock = socket.create_connection((self.ip, self.port), timeout=5.0)
        # After connect, prefer no timeout in steady-state recv:
        self.sock.settimeout(None)

        self.stop_evt.clear()
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()

        print(f"[OK] Connected to {self.ip}:{self.port}")
        print(f"[INFO] MEAS_BYTES={MEAS_BYTES} ({len(MEAS_NAMES)} words), CMD_BYTES={CMD_BYTES} ({HB_NAME}=float32, {CMD_NAME}={'int32' if CMD_TYPE.lower()=='int' else 'float32'})")

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

    def _rx_loop(self) -> None:
        """Continuously read measurement frames from RTDS and store latest."""
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

    def get_latest(self) -> dict[str, float | int]:
        with self.latest_lock:
            return dict(self.latest)

    def _mode_gate(self) -> bool:
        """
        MODE gating convention (you can adjust if your RTDS uses different mapping):
          MODE=0 -> MANUAL (block Python commands)
          MODE=1 -> REMOTE (allow)
        """
        m = self.get_latest()
        if not m:
            print("[WARN] No measurements yet; cannot validate MODE.")
            return False

        try:
            mode_val = int(m["MODE"])
        except Exception:
            print("[WARN] MODE not parseable yet; cannot validate MODE.")
            return False

        if mode_val == 0:
            print("[BLOCKED] MODE=MANUAL -> Python command not allowed. Flip MODE to REMOTE first.")
            return False

        return True

    def send_command(self, value: int | float) -> None:
        """Send heartbeat + command value to RTDS."""
        if self.sock is None:
            print("[ERROR] Not connected.")
            return

        with self.send_lock:
            # bump heartbeat so RTDS can see "fresh" packets
            self.hb_value += 0.1

            if CMD_TYPE.lower() == "int":
                payload = struct.pack(CMD_FMT, float(self.hb_value), int(value))
            else:
                payload = struct.pack(CMD_FMT, float(self.hb_value), float(value))

            self.sock.sendall(payload)

        print(f"[TX] {CMD_NAME}={value}  ({HB_NAME}={self.hb_value:.2f})")

    # ---------- User-level actions ----------
    def press(self) -> None:
        """Momentary pushbutton behavior: 0 -> 1 -> 0 (robust)."""
        if CMD_TYPE.lower() != "int":
            print("[ERROR] 'press' is meant for int pushbuttons (0/1). Set CMD_TYPE='int'.")
            return
        if not self._mode_gate():
            return

        # Force a clean rising edge that RTDS cannot miss

        self.send_command(1)
        time.sleep(PULSE_S)  # your hold time (0.7 s is fine)
        self.send_command(0)


    def on(self) -> None:
        """Latched ON: set command to 1 (holds until you send 'off')."""
        if CMD_TYPE.lower() != "int":
            print("[ERROR] 'on/off' are meant for int commands. Set CMD_TYPE='int'.")
            return
        if not self._mode_gate():
            return
        self.send_command(1)

    def off(self) -> None:
        """Latched OFF: set command to 0."""
        if CMD_TYPE.lower() != "int":
            print("[ERROR] 'on/off' are meant for int commands. Set CMD_TYPE='int'.")
            return
        if not self._mode_gate():
            return
        self.send_command(0)


def main() -> None:
    client = RTDSClient(GTNET_IP, PORT_CH1)
    client.connect()

    print("\nCommands:")
    print("  status -> print MODE")
    print("  press  -> pulse command (1 then 0)")
    print("  on     -> set command = 1 (latched)")
    print("  off    -> set command = 0 (clear)")
    print("  quit   -> exit\n")

    try:
        while True:
            cmd = input(">> ").strip().lower()

            if cmd == "quit":
                break

            elif cmd == "status":
                m = client.get_latest()
                if not m:
                    print("[INFO] No measurements yet.")
                else:
                    print(f"MODE={int(m['MODE'])}  Ch1_Test_TX={float(m['Ch1_Test_TX']):.3f}")

            elif cmd == "press":
                client.press()

            elif cmd == "on":
                client.on()

            elif cmd == "off":
                client.off()

            else:
                print("Unknown command. Use: status / press / on / off / quit")

    finally:
        client.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()

