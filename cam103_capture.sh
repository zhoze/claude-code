#!/usr/bin/env bash
#
# Capture frames from Tallinn traffic camera "cam103" and sync them to Google Drive.
#
# ── ONE-TIME SETUP ──────────────────────────────────────────────────────────
#   1. Find cam103's real image URL:
#        - Open https://ristmikud.tallinn.ee/index.php/cams in Chrome/Firefox
#        - F12 → Network tab → filter "Img" → click the cam103 stream
#        - Right-click the repeating .jpg request → Copy → Copy as cURL
#        - Paste the URL into CAM_URL below. If it needs a cookie/header to load,
#          add it to the curl line further down.
#   2. Install + configure rclone for Google Drive (one time):
#        curl https://rclone.org/install.sh | sudo bash
#        rclone config         # make a remote named "gdrive" of type "drive"
#   3. chmod +x cam103_capture.sh   then   ./cam103_capture.sh
#
#   Stop with Ctrl-C. To run unattended, see the cron/systemd notes at the bottom.
# ────────────────────────────────────────────────────────────────────────────

set -euo pipefail

# >>> EDIT THESE <<<
CAM_URL="https://ristmikud.tallinn.ee/PASTE_CAM103_IMAGE_URL_HERE.jpg"
INTERVAL=10                       # seconds between frames (keep >= 10, be polite)
LOCAL_DIR="$HOME/tallinn_cam103"  # where frames are stored locally
GDRIVE_REMOTE="gdrive:TallinnCam/cam103"   # rclone remote:folder in Drive
UPLOAD_EVERY=6                    # upload to Drive every N frames (6 * 10s = 1 min)

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
REFERER="https://ristmikud.tallinn.ee/index.php/cams"

mkdir -p "$LOCAL_DIR"
echo "Capturing cam103 every ${INTERVAL}s into $LOCAL_DIR (Ctrl-C to stop)"

count=0
while true; do
  ts=$(date +%Y%m%d_%H%M%S)
  out="$LOCAL_DIR/cam103_${ts}.jpg"

  if curl -fsS \
       -A "$UA" \
       -H "Referer: $REFERER" \
       --max-time 20 \
       "${CAM_URL}?t=${ts}" -o "$out"; then
    # Skip tiny/empty/error responses so we don't archive junk frames
    if [ "$(stat -c%s "$out" 2>/dev/null || echo 0)" -lt 1000 ]; then
      echo "  ! frame $ts looked empty (<1KB), discarding"
      rm -f "$out"
    else
      echo "  + saved $out"
      count=$((count + 1))
    fi
  else
    echo "  ! fetch failed at $ts (check CAM_URL / headers)"
  fi

  # Periodically push new frames to Google Drive
  if [ $((count % UPLOAD_EVERY)) -eq 0 ] && [ "$count" -gt 0 ]; then
    echo "  ↑ syncing to $GDRIVE_REMOTE"
    rclone copy "$LOCAL_DIR" "$GDRIVE_REMOTE" --include "*.jpg" --no-traverse || \
      echo "  ! rclone upload failed (is the 'gdrive' remote configured?)"
  fi

  sleep "$INTERVAL"
done

# ── RUN UNATTENDED (optional) ───────────────────────────────────────────────
# cron (capture a single frame each minute instead of a loop): replace the loop
# with one curl call and add to `crontab -e`:
#   * * * * * /path/to/cam103_capture_once.sh
#
# systemd service (Linux): create /etc/systemd/system/cam103.service:
#   [Unit]
#   Description=Tallinn cam103 capture
#   [Service]
#   ExecStart=/path/to/cam103_capture.sh
#   Restart=always
#   [Install]
#   WantedBy=multi-user.target
# then: sudo systemctl enable --now cam103
