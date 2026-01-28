import socket
import time
import matplotlib
matplotlib.use("TkAgg")  # Force real GUI window
import matplotlib.pyplot as plt
from collections import deque

HOST = "127.24.4.1"
PORT = 4575

# ---- Data buffers ----
MAX_POINTS = 2000
t_buf = deque(maxlen=MAX_POINTS)
A_buf = deque(maxlen=MAX_POINTS)
B_buf = deque(maxlen=MAX_POINTS)
C_buf = deque(maxlen=MAX_POINTS)

# ---- Connect to RTDS ----
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("Connecting to RTDS Runtime...")
s.connect((HOST, PORT))
print("Connected.\n")

def get_value_for(path: str) -> float:
    # path = full RTDS signal path, e.g.
    # "Subsystem #1|Node Voltages\\S1) N680A"

    cmd1 = f'temp_float = CaptureSignal("{path}");'
    cmd2 = 'sprintf(temp_string, "VAL = %f END", temp_float);'
    cmd3 = 'ListenOnPortHandshake(temp_string);'

    s.sendall(cmd1.encode())
    s.sendall(cmd2.encode())
    s.sendall(cmd3.encode())

    reply = s.recv(1024).decode().strip()

    try:
        val = float(reply.split()[2])
    except Exception:
        print("Bad reply:", reply)
        val = float("nan")

    return val


# ---- Plot setup ----
plt.ion()
fig, ax = plt.subplots()
lineA, = ax.plot([], [], label="N680A")
lineB, = ax.plot([], [], label="N680B")
lineC, = ax.plot([], [], label="N680C")

ax.set_xlabel("Time (s)")
ax.set_ylabel("Voltage")
ax.grid(True)
ax.legend(loc="upper right")

print("Plotting real-time signals. Press CTRL+C in the terminal to stop.")

start = time.time()

try:
    while True:
        # 1) Read meters from RTDS
        vA = get_value_for(r"Subsystem #1|Node Voltages\S1) N680A")
        vB = get_value_for(r"Subsystem #1|Node Voltages\S1) N680B")
        vC = get_value_for(r"Subsystem #1|Node Voltages\S1) N680C")

        # 2) Append to buffers
        t = time.time() - start
        t_buf.append(t)
        A_buf.append(vA)
        B_buf.append(vB)
        C_buf.append(vC)

        # 3) Update plot
        lineA.set_data(t_buf, A_buf)
        lineB.set_data(t_buf, B_buf)
        lineC.set_data(t_buf, C_buf)

        if len(t_buf) > 2:
            t_max = t_buf[-1]
            t_min = max(0.0, t_max - 8.0)  # last 8 seconds
            ax.set_xlim(t_min, t_max)

            all_vals = list(A_buf) + list(B_buf) + list(C_buf)
            v_min = min(all_vals)
            v_max = max(all_vals)
            if v_min == v_max:
                v_min -= 0.1
                v_max += 0.1
            margin = 0.05 * (v_max - v_min)
            ax.set_ylim(v_min - margin, v_max + margin)

        plt.pause(0.05)  # ~20 Hz refresh

except KeyboardInterrupt:
    print("\nCTRL+C detected – stopping acquisition.")

finally:
    print("Closing RTDS port...")
    try:
        s.sendall(b"ClosePort(4575);")
    except Exception:
        pass
    s.close()
    print("Socket closed.")
    plt.ioff()
    plt.show()  # show last frame before exit

