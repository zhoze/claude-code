#!/usr/bin/env bash
#
# test_capture.sh — timed capture test.
#
# Captures frames from a camera for DURATION seconds, one every INTERVAL
# seconds, upscales each frame, and saves them locally. Byte-identical
# consecutive frames are detected (the source only refreshes ~once a minute)
# and skipped so you don't store duplicates.
#
# Defaults reproduce the requested test: 1 minute, every 10 seconds (6 grabs),
# each frame upscaled 2x.
#
# Usage:
#   ./test_capture.sh
#   DURATION=120 INTERVAL=15 FACTOR=3 ./test_capture.sh
#
set -euo pipefail

CAM="${CAM:-cam103}"
BASE_URL="${BASE_URL:-https://ristmikud.tallinn.ee/last}"
INTERVAL="${INTERVAL:-10}"          # seconds between grabs
DURATION="${DURATION:-60}"          # total seconds
OUTDIR="${OUTDIR:-./captures}"
FACTOR="${FACTOR:-2}"               # upscale factor
QUALITY="${QUALITY:-95}"            # JPEG quality of upscaled output
KEEP_RAW="${KEEP_RAW:-0}"           # 1 = keep original (pre-upscale) frames

here="$(cd "$(dirname "$0")" && pwd)"
url="${BASE_URL}/${CAM}.jpg"
count=$(( DURATION / INTERVAL ))
mkdir -p "$OUTDIR"

echo "[*] Capturing ${count} frame(s) from ${url}"
echo "    every ${INTERVAL}s over ~${DURATION}s, upscaling x${FACTOR} (q${QUALITY})"

prev_md5=""
unique=0
for ((i=1; i<=count; i++)); do
    ts="$(date -u +%Y%m%d_%H%M%S)"
    raw="${OUTDIR}/${CAM}_${ts}_raw.jpg"

    if curl --fail --location --silent --show-error \
            --retry 3 --retry-delay 2 -o "$raw" "$url"; then
        md5="$(md5sum "$raw" | cut -d' ' -f1)"
        if [ "$md5" = "$prev_md5" ]; then
            echo "  [$i/$count] ${ts}  duplicate frame — skipped"
            rm -f "$raw"
        else
            up="${OUTDIR}/${CAM}_${ts}_x${FACTOR}.jpg"
            dims="$(python3 "${here}/upscale.py" "$raw" "$up" \
                        --factor "$FACTOR" --quality "$QUALITY")"
            sz="$(wc -c < "$up")"
            echo "  [$i/$count] ${ts}  NEW  ${dims}  ${sz} bytes  -> $(basename "$up")"
            prev_md5="$md5"
            unique=$((unique + 1))
            [ "$KEEP_RAW" = "1" ] || rm -f "$raw"
        fi
    else
        echo "  [$i/$count] ${ts}  download failed"
    fi

    if [ "$i" -lt "$count" ]; then
        sleep "$INTERVAL"
    fi
done

echo "[OK] ${unique} unique upscaled frame(s) saved in ${OUTDIR}"
ls -la "$OUTDIR"
