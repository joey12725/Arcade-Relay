#!/usr/bin/env bash
# P5 provisioning validation. This environment can't reboot into a kiosk, so
# validation is static-but-strict (documented in ASSUMPTIONS A-19):
#   shellcheck + bash -n on the install scripts,
#   systemd-analyze verify on every unit (in a debian container with the
#   ExecStart binaries stubbed — validates unit syntax/lint, not binaries),
#   udevadm verify on the udev rules,
#   @SERVER_HOST@ substitution correctness.
set -euo pipefail
cd "$(dirname "$0")/../.."   # repo root

echo "== 1. shellcheck + bash -n on provisioning scripts =="
command -v shellcheck >/dev/null || apt-get install -y -qq shellcheck >/dev/null
shellcheck cabinet/provision/install.sh server/provision/install.sh
shellcheck -s sh cabinet/provision/openbox-autostart
bash -n cabinet/provision/install.sh
bash -n server/provision/install.sh

echo "== 2. systemd-analyze verify + udevadm verify (debian container) =="
docker run --rm -v "$PWD:/repo:ro" debian:trixie-slim bash -ec '
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq >/dev/null && apt-get install -y -qq systemd udev >/dev/null
    # stub every ExecStart binary so verify checks unit syntax, not installs
    touch /usr/local/bin/pegasus-fe /usr/bin/steam /usr/bin/retroarch \
          /usr/bin/python3
    chmod +x /usr/local/bin/pegasus-fe /usr/bin/steam /usr/bin/retroarch \
             /usr/bin/python3
    mkdir -p /opt/arcade/cabinet /opt/arcade/server
    touch /opt/arcade/cabinet/cabd.py /opt/arcade/server/arcaded.py
    mkdir -p /run/units
    for u in cabd pegasus steam; do
        sed "s|@SERVER_HOST@|arcade.example.com|g" \
            /repo/cabinet/provision/systemd/$u.service > /run/units/$u.service
    done
    cp /repo/server/provision/arcaded.service /run/units/
    systemd-analyze verify --man=no --recursive-errors=no \
        /run/units/cabd.service /run/units/pegasus.service \
        /run/units/steam.service /run/units/arcaded.service
    echo "systemd units verified"
    udevadm verify --no-style /repo/cabinet/provision/99-arcade-controllers.rules \
        || udevadm verify /repo/cabinet/provision/99-arcade-controllers.rules
    echo "udev rules verified"
'

echo "== 3. @SERVER_HOST@ substitution leaves no placeholders =="
for u in cabd pegasus steam; do
    out=$(sed "s|@SERVER_HOST@|arcade.example.com|g" \
          "cabinet/provision/systemd/$u.service")
    echo "$out" | grep -q "@SERVER_HOST@" \
        && { echo "FAIL: placeholder survived in $u.service"; exit 1; }
done
sed "s|@SERVER_HOST@|arcade.example.com|g" \
    cabinet/provision/systemd/cabd.service \
    | grep -q "wss://arcade.example.com/cab" \
    || { echo "FAIL: cabd WS URL not templated"; exit 1; }

echo "== 4. python daemons referenced by units actually compile =="
python3 -m py_compile cabinet/cabd.py server/arcaded.py

echo "TEST-PROVISION PASS"
