import socket
import struct
import threading
import time

GTNET_IP = "172.24.4.3"
PORT_CH1 = 7000

# -----------------------------
# RTDS -> Python (23 words)
# -----------------------------
MEAS_NAMES = [
    "Ch1_Test_TX","MODE"]

MEAS_FMT = (
    ">"
    "f"  # 0
    "i"  # 1

)
MEAS_BYTES = struct.calcsize(MEAS_FMT)

# -----------------------------
# Python -> RTDS (2 words)
# word0: Ch1_Test_RX (float32)
# word1: REM_GRID    (int32)  <-- required by your controlled switch
# -----------------------------
CMD_FMT = ">fi"
CMD_BYTES = struct.calcsize(CMD_FMT)


def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    data = b""
    while len(data) < nbytes:
        chunk = sock.recv(nbytes - len(data))
        if not chunk:
            raise ConnectionError("Socket closed by peer")
        data += chunk
    return data


def connect(ip: str, port: int) -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect((ip, port))
    s.settimeout(2.0)
    return s


def unpack_measurements(payload: bytes) -> dict:
    vals = struct.unpack(MEAS_FMT, payload)
    return {MEAS_NAMES[i]: vals[i] for i in range(len(MEAS_NAMES))}


def pack_command(ch1_test_rx: float, rem_grid: int) -> bytes:
    # rem_grid MUST be int 0/1
    return struct.pack(CMD_FMT, float(ch1_test_rx), int(rem_grid))


class RTDSClient:
    def __init__(self, ip: str, port: int):
        self.ip = ip
        self.port = port
        self.sock: socket.socket | None = None

        self.latest = {}
        self.latest_lock = threading.Lock()

        self.running = False
        self.rx_thread: threading.Thread | None = None

        # pause RX prints while you type
        self.allow_rx_print = True

        # for "is it updating?" checks
        self._last_tx_val = None
        self._last_print_t = 0.0
        self._frames = 0

        # heartbeat sent to RTDS (float)
        self.ch1_test_rx = 10.0

    def start(self):
        self.sock = connect(self.ip, self.port)
        print(f"[OK] Connected to {self.ip}:{self.port}")
        print(
            f"[INFO] MEAS_BYTES={MEAS_BYTES} (23 words), "
            f"CMD_BYTES={CMD_BYTES} (Ch1_Test_RX=float32, REM_GRID=int32)\n"
        )

        self.running = True
        self.rx_thread = threading.Thread(target=self._rx_loop, daemon=True)
        self.rx_thread.start()

    def stop(self):
        # stop RX first, then close socket
        self.running = False
        time.sleep(0.05)
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
        self.sock = None

    def _rx_loop(self):
        while self.running:
            try:
                if not self.sock:
                    break

                raw = recv_exact(self.sock, MEAS_BYTES)
                meas = unpack_measurements(raw)
                self._frames += 1

                with self.latest_lock:
                    self.latest = meas

                # Update check using Ch1_Test_TX (only meaningful if that signal changes)
                tx_val = meas.get("Ch1_Test_TX", None)
                now = time.time()

                if self.allow_rx_print and (now - self._last_print_t > 2.0):
                    mode = int(meas["MODE"])
                    changed = (self._last_tx_val is None) or (tx_val != self._last_tx_val)
                    self._last_tx_val = tx_val
                    self._last_print_t = now

                    print(
                        f"[RX] frames={self._frames} MODE={mode} "
                        f"TXchg={'YES' if changed else 'NO'}"
                    )

            except socket.timeout:
                print("[WARN] RX timeout: no measurement frame received (RTDS not streaming or wrong port).")
            except Exception as e:
                print(f"[ERROR] RX loop stopped: {e}")
                self.running = False
                break

    def get_latest(self) -> dict:
        with self.latest_lock:
            return dict(self.latest)

    def send_rem_grid(self, rem_grid_value: int):
        """
        Sends a command frame ON DEMAND.
        Blocks if MODE=MANUAL (0).
        """
        meas = self.get_latest()
        if not meas:
            print("[WARN] No measurements received yet; cannot validate MODE or send safely.")
            return

        mode = int(meas["MODE"])
        if mode == 0:
            print("[BLOCKED] MODE=MANUAL -> Python command not allowed. Flip MODE to REMOTE first.")
            return

        if not self.sock:
            print("[TX-ERROR] Socket is not available (disconnected).")
            return

        try:
            self.ch1_test_rx += 0.1
            payload = pack_command(self.ch1_test_rx, rem_grid_value)

            # Optional debug: show exactly what we send (uncomment if needed)
            # print(f"[DEBUG] TX bytes: {payload.hex()}")

            self.sock.sendall(payload)
            print(f"[TX] Sent REM_GRID={int(rem_grid_value)} (Ch1_Test_RX={self.ch1_test_rx:.2f})")

        except Exception as e:
            print(f"[TX-ERROR] Failed to send command: {e}")


def main():
    client = RTDSClient(GTNET_IP, PORT_CH1)
    client.start()

    print("\nCommands:")
    print("  one   -> send REM_GRID=1")
    print("  zero -> send REM_GRID=0")
    print("  status -> print latest MODE and a few signals")
    print("  quit   -> exit\n")

    try:
        while True:
            client.allow_rx_print = False
            cmd = input(">> ").strip().lower()
            client.allow_rx_print = True

            if cmd == "quit":
                break
            elif cmd == "status":
                m = client.get_latest()
                if not m:
                    print("[INFO] No measurements yet.")
                else:
                    print(f"MODE={int(m['MODE'])}")

            elif cmd == "one":
                client.send_rem_grid(1)
            elif cmd == "zero":
                client.send_rem_grid(0)
            else:
                print("Unknown command. Use: one / zero / status / quit")

    finally:
        client.stop()
        print("Stopped.")


if __name__ == "__main__":
    main()

