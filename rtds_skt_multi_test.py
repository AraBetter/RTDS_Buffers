import socket
import struct
import time

GTNET_IP = "172.24.4.3"
PORT_CH1 = 7000
PORT_CH2 = 7001

# RTDS Float = IEEE 32-bit
FMT = ">f"   # little-endian float32
# If values look wrong, switch to: FMT = ">f"

def recv_exact(sock, nbytes):
    data = b""
    while len(data) < nbytes:
        chunk = sock.recv(nbytes - len(data))
        if not chunk:
            raise ConnectionError("Socket closed")
        data += chunk
    return data

def connect(ip, port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(5.0)
    s.connect((ip, port))
    s.settimeout(1.0)
    return s

print("Connecting to RTDS...")
s1 = connect(GTNET_IP, PORT_CH1)
print("Connected Channel 1")
s2 = connect(GTNET_IP, PORT_CH2)
print("Connected Channel 2")

echo1 = 10.0
echo2 = 20.0

try:
    while True:
        # --- Channel 1 ---
        raw1 = recv_exact(s1, 4)
        (rx1,) = struct.unpack(FMT, raw1)
        s1.sendall(struct.pack(FMT, echo1))

        # --- Channel 2 ---
        raw2 = recv_exact(s2, 4)
        (rx2,) = struct.unpack(FMT, raw2)
        s2.sendall(struct.pack(FMT, echo2))

        print(
            f"CH1 RTDS->PY: {rx1:.6f} | CH1 PY->RTDS: {echo1:.6f} || "
            f"CH2 RTDS->PY: {rx2:.6f} | CH2 PY->RTDS: {echo2:.6f}"
        )

        echo1 += 0.1
        echo2 += 0.2
        time.sleep(0.1)

except KeyboardInterrupt:
    print("Stopping.")

finally:
    s1.close()
    s2.close()
