#!/usr/bin/env bash
# Save backup (spec §5): cabd opportunistically pushes a tar.gz of /saves to
# the server when contents change. Backup, never a dependency: partitions
# must not break gameplay-side cabd, and sync resumes after healing.
set -euo pipefail
cd "$(dirname "$0")/.."

compose() { docker compose "$@"; }
srv() { compose exec -T server "$@"; }
cab() { compose exec -T cabinet "$@"; }
tp() { compose exec -T toxiproxy /toxiproxy-cli "$@"; }

status_field() { srv python3 -c "import json,urllib.request; print(json.load(
    urllib.request.urlopen('http://localhost:8000/saves/status')).get('$1',''))"; }
sha_now() { status_field sha; }
recv_at() { status_field received_at; }

echo "== 0. stack up =="
compose up -d --build --force-recreate --wait --wait-timeout 300 server cabinet toxiproxy
if ! tp inspect cab_link >/dev/null 2>&1; then
    tp create --listen 0.0.0.0:8666 --upstream server:8000 cab_link
fi
tp inspect cab_link | grep -q "enabled = false" && tp toggle cab_link || true
t=0; until srv arcadectl --json status | grep -q '"online": true'; do
    t=$((t+1)); [ $t -ge 60 ] && { echo "FAIL: never online"; exit 1; }; sleep 0.5
done

echo "== 1. new save appears -> uploaded, tar contents match =="
cab sh -c 'mkdir -p /saves/snes && printf DATA1 > /saves/snes/smw.srm'
t=0; until [ -n "$(sha_now)" ]; do
    t=$((t+1)); [ $t -ge 30 ] && { echo "FAIL: backup never arrived"; exit 1; }; sleep 0.5
done
sha1=$(sha_now)
srv python3 - <<'EOF'
import tarfile
tar = tarfile.open("/saves-backup/latest.tar.gz")
data = tar.extractfile("saves/snes/smw.srm").read()
assert data == b"DATA1", f"content mismatch: {data!r}"
print("tar contains saves/snes/smw.srm == DATA1")
EOF

echo "== 2. unchanged saves are NOT re-uploaded (content-sha dedupe) =="
at1=$(recv_at)
sleep 7   # >3 sync ticks at 2s
at2=$(recv_at)
[ "$at1" = "$at2" ] || { echo "FAIL: re-upload of unchanged saves ($at1 -> $at2)"; exit 1; }

echo "== 3. modified save -> new sha uploaded =="
cab sh -c 'printf DATA2 > /saves/snes/smw.srm'
t=0; until [ "$(sha_now)" != "$sha1" ]; do
    t=$((t+1)); [ $t -ge 30 ] && { echo "FAIL: modified save never re-uploaded"; exit 1; }; sleep 0.5
done
srv python3 - <<'EOF'
import tarfile
data = tarfile.open("/saves-backup/latest.tar.gz").extractfile("saves/snes/smw.srm").read()
assert data == b"DATA2", f"content mismatch after modify: {data!r}"
print("tar updated to DATA2")
EOF

echo "== 4. partition: backup pauses, cabd survives; heals after re-enable =="
sha3=$(sha_now)
tp toggle cab_link >/dev/null
cab sh -c 'printf DATA3 > /saves/snes/smw.srm'
sleep 6
[ "$(sha_now)" = "$sha3" ] || { echo "FAIL: upload crossed a partition?!"; exit 1; }
cab pgrep -f "[c]abd.py" >/dev/null || { echo "FAIL: cabd died during partition"; exit 1; }
tp toggle cab_link >/dev/null
t=0; until [ "$(sha_now)" != "$sha3" ]; do
    t=$((t+1)); [ $t -ge 40 ] && { echo "FAIL: backup never resumed after heal"; exit 1; }; sleep 0.5
done
srv python3 - <<'EOF'
import tarfile
data = tarfile.open("/saves-backup/latest.tar.gz").extractfile("saves/snes/smw.srm").read()
assert data == b"DATA3", f"post-heal content mismatch: {data!r}"
print("post-heal tar has DATA3")
EOF

echo "TEST-SAVES-BACKUP PASS"
