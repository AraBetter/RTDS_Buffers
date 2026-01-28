import socket
import time

HOST = "127.24.4.1"   # same machine as RSCAD Runtime
PORT = 4575          # must match ListenOnPort(4575, true)

# Create TCP socket and connect to Runtime
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("Connecting to RTDS Runtime...")
s.connect((HOST, PORT))
print("Connected.")

# 1) Ask Runtime to print a message in its Messages window
s.sendall(b'fprintf(stdmsg, "Hello from Python!\\n");')

# 2) Small pause in the script engine (so you have time to see it)
s.sendall(b'SUSPEND 0.5;')

# 3) Close the port -> this lets ListenOnPort() return and finish the script
time.sleep(1)  # short delay before closing
s.sendall(b'ClosePort(4575);')

s.close()
print("Commands sent, socket closed.")
