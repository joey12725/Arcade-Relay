#!/usr/bin/env bash
# Generate deterministic test media for the sim Plex server (idempotent).
# Layout is Plex-native: Shows/<Series>/Season NN/... and Movies/<Title>/...
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p "sim/media/Shows/Series A/Season 1" "sim/media/Shows/Series B/Season 1" "sim/media/Movies/Redline (2009)"
gen() {
    [ -s "sim/media/$1" ] && return 0
    docker run --rm -v "$PWD/sim/media:/out" --entrypoint ffmpeg linuxserver/ffmpeg:latest \
        -y -f lavfi -i "testsrc2=size=1280x720:rate=24:duration=20" -f lavfi -i "sine=frequency=$2:duration=20" \
        -c:v libx264 -preset ultrafast -pix_fmt yuv420p -c:a aac -shortest "/out/$1" -loglevel error
}
gen "Shows/Series A/Season 1/Series A S01E01.mkv" 330
gen "Shows/Series A/Season 1/Series A S01E02.mkv" 440
gen "Shows/Series B/Season 1/Series B S01E01.mkv" 550
gen "Movies/Redline (2009)/Redline (2009).mkv" 660
