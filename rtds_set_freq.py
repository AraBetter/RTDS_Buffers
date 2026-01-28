import socket
import time

HOST = "127.24.4.1"   # same PC as Runtime
PORT = 4575          # same as ListenOnPort(4575, true)

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
print("Connecting to RTDS Runtime...")
s.connect((HOST, PORT))
print("Connected.")

# --- HERE is the important line ---
# This should set the 'Freq' slider on your panel to 30 Hz
s.sendall(b'SetSlider "Freq" = 30;')

# Let Runtime process it
time.sleep(1.0)

# Close port and end script on RTDS side
s.sendall(b'ClosePort(4575);')
s.close()
print("Done.")
