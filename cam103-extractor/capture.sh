#!/usr/bin/env bash
#
# capture.sh — grab the current full-resolution frame from a Tallinn traffic
# camera (default: cam103) and upload it to a Google Drive folder via rclone.
#
# The site at https://ristmikud.tallinn.ee only serves the *latest* frame per
# camera at /last/<cam>.jpg; it keeps no archive. To build up a collection of
# "all photos" you run this one-shot script repeatedly on a schedule (cron),
# and each run drops one timestamped, full-quality JPEG into your Drive folder.
#
# Usage:
#   ./capture.sh                     # uses defaults / env vars below
#   CAM=cam104 ./capture.sh          # capture a different camera
#   REMOTE="gdrive:cam103" ./capture.sh
#
# Required:
#   rclone configured with a Google Drive remote (see README.md).
#
set -euo pipefail

# --- configuration (override via environment) --------------------------------
CAM="${CAM:-cam103}"                                   # camera id, e.g. cam103
BASE_URL="${BASE_URL:-https://ristmikud.tallinn.ee/last}"
REMOTE="${REMOTE:-gdrive:${CAM}}"                       # rclone remote:folder
WORKDIR="${WORKDIR:-$(mktemp -d)}"                      # local scratch dir
KEEP_LOCAL="${KEEP_LOCAL:-0}"                           # 1 = keep local copy
# -----------------------------------------------------------------------------

mkdir -p "$WORKDIR"
url="${BASE_URL}/${CAM}.jpg"
ts="$(date -u +%Y%m%d_%H%M%S)"
fname="${CAM}_${ts}.jpg"
local_path="${WORKDIR}/${fname}"

cleanup() { [ "$KEEP_LOCAL" = "1" ] || rm -rf "$WORKDIR"; }
trap cleanup EXIT

echo "[*] Downloading ${url}"
# --fail: error on HTTP >=400; --location: follow redirects; retry transient errors.
if ! curl --fail --location --silent --show-error \
        --retry 4 --retry-delay 2 --retry-connrefused \
        -o "$local_path" "$url"; then
    echo "[!] Download failed for ${url}" >&2
    exit 1
fi

# Sanity check: must be a non-empty JPEG.
if [ ! -s "$local_path" ] || ! file -b "$local_path" | grep -qi jpeg; then
    echo "[!] Downloaded file is not a valid JPEG: $(file -b "$local_path" 2>/dev/null || echo empty)" >&2
    exit 1
fi
size="$(wc -c < "$local_path")"
echo "[*] Saved ${fname} (${size} bytes)"

echo "[*] Uploading to rclone remote: ${REMOTE}/"
rclone copyto "$local_path" "${REMOTE}/${fname}" \
    --retries 4 --low-level-retries 10

echo "[OK] Uploaded ${fname} to ${REMOTE}"
[ "$KEEP_LOCAL" = "1" ] && echo "[*] Local copy kept at ${local_path}"
exit 0
