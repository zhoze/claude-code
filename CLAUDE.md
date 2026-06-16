# Project notes for Claude

## Camera capture workflows

- **scan-viimsi** (`.github/workflows/scan-viimsi.yml` + `scripts/fetch_viimsi.py`):
  VPN-gated capture of `kaamerad.viimsivald.ee` cameras (Estonian exit IP required).
- **cam-watch** (`.github/workflows/cam-watch.yml` + `scripts/watch_cam_spot.py`):
  direct (no VPN) watcher for Tallinn `ristmikud.tallinn.ee` cameras.

### Delivery preference (IMPORTANT)
When I trigger any camera-capture run (scan-viimsi, cam-watch, etc.), I must
**deliver the captured frame(s) to the user automatically** via `SendUserFile`
as soon as the run finishes — download the artifact and send it immediately.
Do NOT wait for the user to ask for the frame.
