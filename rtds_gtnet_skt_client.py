import socket
import struct
import time

GTNET_IP = "172.24.4.3"
PORT_CH1 = 7000  # Channel 1 port (your measurements + commands)

# -----------------------------
# Channel 1: RTDS -> Python (23 words)
# Types from your screenshots:
#  - Floats: most signals
#  - Ints  : LGFLT_x, GRID, BRK1, BRK1island, BRKGEN, MODE
# -----------------------------
MEAS_NAMES = [
    "Ch1_Test_TX",  # 0 float
    "PGRID",        # 1 float
    "QGRID",        # 2 float
    "N650RMSPU",    # 3 float
    "LGFLT_x",      # 4 int
    "GRID",         # 5 int
    "N680RMSPU",    # 6 float
    "PLOAD680",     # 7 float
    "QLOAD680",     # 8 float
    "BRK1",         # 9 int
    "SOC1",         # 10 float
    "Pmeas",        # 11 float
    "Qmeas",        # 12 float
    "VLOADRMS",     # 13 float
    "BRK1island",   # 14 int
    "PGEN",         # 15 float
    "QGEN",         # 16 float
    "BRKGEN",       # 17 int
    "PMACH",        # 18 float
    "QMACH",        # 19 float
    "VSARMSPU",     # 20 float
    "GENRMSPU",     # 21 float
    "MODE",         # 22 int  (0=MANUAL, 1=REMOTE)
]

# Build measurement unpack format string (23 items)
# > = big-endian
# f = float32
# i = int32
MEAS_FMT = (
    ">"     # big-endian
    "f"     # 0  Ch1_Test_TX
    "f"     # 1  PGRID
    "f"     # 2  QGRID
    "f"     # 3  N650RMSPU
    "i"     # 4  LGFLT_x
    "i"     # 5  GRID
    "f"     # 6  N680RMSPU
    "f"     # 7  PLOAD680
    "f"     # 8  QLOAD680
    "i"     # 9  BRK1
    "f"     # 10 SOC1
    "f"     # 11 Pmeas
    "f"     # 12 Qmeas
    "f"     # 13 VLOADRMS
    "i"     # 14 BRK1island
    "f"     # 15 PGEN
    "f"     # 16 QGEN
    "i"     # 17 BRKGEN
    "f"     # 18 PMACH
    "f"     # 19 QMACH
    "f"     # 20 VSARMSPU
    "f"     # 21 GENRMSPU
    "i"     # 22 MODE
)
MEAS_BYTES = struct.calcsize(MEAS_FMT)

# -----------------------------
# Channel 1: Python -> RTDS (2 words)
# From your screenshot:
#  in_1_0 = Ch1_Test_RX (Float)
#  in_1_1 = REM_GRID    (Int)
# -----------------------------
CMD_FMT = ">fi"  # float32 + int32 (big-endian)
CMD_BYTES = struct.calcsize(CMD_FMT)

# Command behavior
REM_GRID_GRID_VALUE = 1     # set to 1 if "GRID" means breaker closed
REM_GRID_ISLAND_VALUE = 0   # set to 0 if "ISLAND" means breaker open

SEND_PERIOD_S = 0.1         # you can lower later (e.g., 0.02–0.05)

def recv_exact(sock: socket.socket, nbytes: int) -> bytes:
    data = b""
    while len(data) < nbytes:
        chunk = sock.recv(nbytes - len(data))
        if not chunk:
            raise ConnectionError("Socket closed")
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
    return struct.pack(CMD_FMT, float(ch1_test_rx), int(rem_grid))

def main():
    print("Connecting to RTDS...")
    s1 = connect(GTNET_IP, PORT_CH1)
    print(f"Connected Channel 1 on {GTNET_IP}:{PORT_CH1}")
    print(f"Expecting MEAS_BYTES={MEAS_BYTES} (23 words) and sending CMD_BYTES={CMD_BYTES} (2 words)\n")

    # simple test pattern: toggle REM_GRID every 5 seconds when MODE=REMOTE
    desired_rem_grid = REM_GRID_GRID_VALUE
    last_toggle_t = time.time()
    toggle_period_s = 5.0

    # heartbeat value you can watch in RTDS
    ch1_test_rx = 10.0

    try:
        while True:
            # 1) Receive full measurement frame (23 values)
            raw = recv_exact(s1, MEAS_BYTES)
            meas = unpack_measurements(raw)

            mode = int(meas["MODE"])  # 0=MANUAL, 1=REMOTE

            # Print a compact status line every cycle
            print(
                f"MODE={mode} | "
                f"N650={meas['N650RMSPU']:.3f} pu  N680={meas['N680RMSPU']:.3f} pu | "
                f"SOC={meas['SOC1']:.3f} | GRID_state={int(meas['GRID'])} | "
                f"LGFLT_x={int(meas['LGFLT_x'])}"
            )

            # 2) Decide what to send
            if mode == 0:
                # MANUAL: Python should NOT control the breaker
                # Still send a valid command frame so RTDS doesn't stall.
                print("[WARN] MODE is MANUAL -> Python will not command REM_GRID.")
                rem_grid_to_send = REM_GRID_ISLAND_VALUE  # safe default (doesn't matter if MODE blocks it)
            else:
                # REMOTE: allow toggling test
                now = time.time()
                if now - last_toggle_t > toggle_period_s:
                    desired_rem_grid = (
                        REM_GRID_ISLAND_VALUE
                        if desired_rem_grid == REM_GRID_GRID_VALUE
                        else REM_GRID_GRID_VALUE
                    )
                    last_toggle_t = now
                    print(f"[CMD] MODE=REMOTE -> sending REM_GRID={desired_rem_grid}")
                rem_grid_to_send = desired_rem_grid

            # 3) Send command frame (2 values) back to RTDS
            s1.sendall(pack_command(ch1_test_rx, rem_grid_to_send))

            # 4) Update heartbeat and sleep
            ch1_test_rx += 0.1
            time.sleep(SEND_PERIOD_S)

    except KeyboardInterrupt:
        print("Stopping.")

    finally:
        s1.close()

if __name__ == "__main__":
    main()
