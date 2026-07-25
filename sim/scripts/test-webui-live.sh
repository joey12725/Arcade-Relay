#!/usr/bin/env bash
# Real-browser test of the web UI (§6.3 + CTL-3). Runs Chromium on the host
# against the sim server's container IP; the cabinet and state repo are
# poked through docker compose exec so the assertions are end-to-end.
set -euo pipefail
cd "$(dirname "$0")/../.."

docker compose -f sim/docker-compose.yml up -d --build --wait --wait-timeout 300 \
    server cabinet toxiproxy
tp() { docker compose -f sim/docker-compose.yml exec -T toxiproxy /toxiproxy-cli "$@"; }
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until docker compose -f sim/docker-compose.yml exec -T server \
        arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

IP=$(docker inspect arcade-sim-server-1 \
     --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' | awk '{print $1}')
[ -n "$IP" ] || { echo "FAIL: no server container IP"; exit 1; }
PLAYWRIGHT_BROWSERS_PATH=/opt/pw-browsers \
    python3 sim/scripts/webui-live-test.py "http://$IP:8000"
