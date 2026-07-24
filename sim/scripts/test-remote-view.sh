#!/usr/bin/env bash
# Remote view (spec §5.10): arcadectl cab remote-view starts x11vnc on the
# cabinet and tunnels it through the server connection. Proof is a real RFB
# handshake against the SERVER's port — bytes must traverse
# client -> server TCP -> tunnel WS (via toxiproxy) -> cabinet x11vnc.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

echo "== 0. stack up =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. remote-view on: x11vnc up on cabinet, port advertised =="
out=$(srv arcadectl --json cab remote-view)
echo "$out" | grep -q '"remote_view": true' || { echo "FAIL: $out"; exit 1; }
echo "$out" | grep -q '"port": 5901' || { echo "FAIL: no port in $out"; exit 1; }
cab pgrep -f "[x]11vnc" >/dev/null || { echo "FAIL: x11vnc not running on cabinet"; exit 1; }

echo "== 2. full RFB handshake through the tunnel (bidirectional) =="
srv python3 - <<'EOF'
import socket
s = socket.create_connection(("127.0.0.1", 5901), timeout=15)
ver = s.recv(12)
assert ver.startswith(b"RFB "), f"not an RFB server: {ver!r}"
s.sendall(ver)                       # client echoes the protocol version
n = s.recv(1)                        # 3.7+: security-type count
assert n and n[0] > 0, f"no security types: {n!r}"
types = s.recv(n[0])
assert 1 in types, f"None-auth missing (x11vnc -nopw): {list(types)}"
print(f"RFB OK: {ver.decode().strip()}, security types {list(types)}")
s.close()
EOF

echo "== 3. second client connects too (-shared/-forever, fresh token) =="
srv python3 - <<'EOF'
import socket
s = socket.create_connection(("127.0.0.1", 5901), timeout=15)
assert s.recv(12).startswith(b"RFB "), "second connection got no RFB banner"
s.close()
print("second RFB banner OK")
EOF

echo "== 4. cabinet x11vnc is localhost-only (no inbound path needed) =="
cab python3 - <<'EOF'
listeners = []
for line in open("/proc/net/tcp").read().splitlines()[1:]:
    addr, port = line.split()[1].split(":")
    if int(port, 16) == 5900 and line.split()[3] == "0A":   # 0A = LISTEN
        listeners.append(addr)
assert listeners, "no listener on 5900 at all"
assert all(a == "0100007F" for a in listeners), \
    f"x11vnc not localhost-only: {listeners}"
print("x11vnc bound to 127.0.0.1 only")
EOF

echo "== 5. remote-view --off: x11vnc gone, server port closed =="
srv arcadectl --json cab remote-view --off | grep -q '"remote_view": false' \
    || { echo "FAIL: off did not report false"; exit 1; }
t=0; until ! cab pgrep -f "[x]11vnc" >/dev/null; do
    t=$((t+1)); [ $t -ge 10 ] && { echo "FAIL: x11vnc survived off"; exit 1; }; sleep 0.5
done
srv python3 - <<'EOF'
import socket
try:
    socket.create_connection(("127.0.0.1", 5901), timeout=3)
    raise SystemExit("FAIL: server VNC port still open after off")
except (ConnectionRefusedError, socket.timeout, OSError):
    print("server VNC port closed")
EOF

echo "TEST-REMOTE-VIEW PASS"
