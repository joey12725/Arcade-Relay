#!/bin/sh
# One EMU-1 acceptance cycle, run INSIDE the cabinet container.
# Args: SYSTEM EXPECT_LINE SCRAMBLE(0|1)
# launch (local path) -> configs clean -> window up -> play -> kill ->
# window gone -> frontend focused. Any deviation exits non-zero.
set -eu
SYS="$1"; EXPECT="$2"; SCRAMBLE="$3"

if [ "$SCRAMBLE" = "1" ]; then
    mkdir -p /tmp/rendered
    echo "GARBAGE = scrambled" > "/tmp/rendered/$SYS.rmp"
    echo "drift" >> /tmp/rendered/retroarch.cfg 2>/dev/null || true
fi

python3 /cabinet/cabd-launch "$SYS" "/roms/$SYS/game.rom" >/dev/null

grep -qF "$EXPECT" "/tmp/rendered/$SYS.rmp" || { echo "drift: $SYS.rmp wrong"; exit 1; }
grep -q "GARBAGE" "/tmp/rendered/$SYS.rmp" && { echo "drift: scramble survived"; exit 1; } || true
xdotool search --name retroarch-fake >/dev/null || { echo "no game window"; exit 1; }

sleep 0.3

python3 /cabinet/cabd-launch kill >/dev/null
i=0
while xdotool search --name retroarch-fake >/dev/null 2>&1; do
    i=$((i + 1)); [ "$i" -ge 20 ] && { echo "window survived kill"; exit 1; }
    sleep 0.1
done
f=$(xdotool getwindowfocus 2>/dev/null) || { echo "no focused window"; exit 1; }
p=$(xdotool search --name pegasus-fake 2>/dev/null | head -1)
[ "$f" = "$p" ] || { echo "frontend not focused after cycle"; exit 1; }
