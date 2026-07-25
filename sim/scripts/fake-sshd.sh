#!/bin/sh
# Sim stand-in for `systemctl start ssh` (no systemd in the container).
# Opens a REAL listener on :22 so lifeline's ssh_listening() /proc/net/tcp
# probe observes the genuine effect — the recovery path is proven by its
# observable result, not by a mocked return code.
#
# The listener must fully detach: it outlives this script, so it must NOT
# hold the caller's stdout/stderr pipes open (subprocess.run(capture_output)
# reads until EOF, and an inherited pipe would hang the caller).
setsid python3 -c '
import socket, sys, time
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    s.bind(("0.0.0.0", 22))
except OSError:
    sys.exit(0)          # already listening: idempotent, nothing to do
s.listen(5)
while True:               # keep the socket open, drop connections
    try:
        conn, _ = s.accept()
        conn.close()
    except OSError:
        break
' </dev/null >/dev/null 2>&1 &
# give the bind a moment so the caller's immediate probe sees the listener
sleep 0.5
