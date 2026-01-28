import socket
import time

HOST = "127.24.4.1"
PORT = 4575

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("Connecting to RTDS Runtime...")
s.connect((HOST, PORT))
print("Connected.")

# --- Request meter value from RTDS ---
# 1) Capture meter into temp_float
s.sendall(b'temp_float = MeterCapture("S1) N680A");')

# 2) Format it as a token string
s.sendall(b'sprintf(temp_string, "VAL = %f END", temp_float);')

# 3) Send token back to Python
s.sendall(b'ListenOnPortHandshake(temp_string);')

# 4) Receive the returned string
reply = s.recv(1024).decode()
print("RTDS reply:", reply)

# Small delay and close
time.sleep(0.5)
s.sendall(b'ClosePort(4575);')
s.close()
