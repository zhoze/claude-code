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

### How to wait for a run WITHOUT stalling (root cause + fix)
The sandbox CANNOT reach `api.github.com` — it returns HTTP 403. So a
background `curl` poll loop against the GitHub API never matches "completed",
spins forever, and never wakes me. That is why earlier runs looked like they
were "waiting" for the user.

Only **I** can reach GitHub, via the `mcp__github__*` tools (separate channel
from the sandbox network). So the wake mechanism must be a background timer
that wakes ME to check via MCP:

1. Trigger the run (`mcp__github__actions_run_trigger`) and note the run id.
2. Poll TIGHTLY so delivery is within seconds of completion — do NOT use one
   long fixed sleep (a flat `sleep 180` makes me wait the full 3 min even when
   the frame is ready in ~60-90s). scan-viimsi is usually done < 2 min.
   - First wake: `Bash(run_in_background=true, command="sleep 75")` (the run
     can't realistically finish sooner, so this first wait is not wasted).
   - Every wake after that: re-check via MCP, then `sleep 20` and repeat until
     the run is completed. Foreground sleep is blocked; background sleep is
     allowed and its completion notifies me.
3. On each wake, check the run via
   `mcp__github__actions_list (list_workflow_jobs)`.
   - If still running: start another short `sleep 20` timer and repeat.
   - If completed: get the artifact, download via the temporary signed blob
     URL (that URL is NOT api.github.com, so `curl` works), unzip, and
     `SendUserFile` the frame immediately. Stop any pending timer (TaskStop).

NEVER `curl https://api.github.com/...` from the sandbox to poll — it is 403.
NEVER make the user ask for the frame — a fixed long sleep that delays delivery
counts as failing this; keep the poll interval short (~20s) after the first check.
