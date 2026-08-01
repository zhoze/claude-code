#!/usr/bin/env bash
# AEGIS phase 09 — scaffold for phases 5+ (Core and agents).
#
# The Core task loop is NOT built yet (docs/part-17, phases 5-6). What this
# phase does is make the scaffolding honest: units that point at code that
# exists, drop-ins generated from the manifests they must match, and nothing
# enabled. `systemctl start aegis-core` or `aegis-agent@documents` will work
# when YOU decide to start them — and will refuse to run in the wrong zone.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root

verify() {
  local rc=0
  [[ -f /etc/systemd/system/aegis-core.service ]] || { echo "aegis-core.service not installed"; rc=1; }
  [[ -f /etc/systemd/system/aegis-agent@.service ]] || { echo "aegis-agent@.service not installed"; rc=1; }
  [[ -f "/etc/systemd/system/aegis-agent@documents.service.d/zone.conf" ]] \
    || { echo "documents zone drop-in missing"; rc=1; }
  systemctl is-enabled --quiet aegis-core 2>/dev/null && { echo "aegis-core should NOT be enabled yet"; rc=1; }
  PYTHONPATH=/aegis/src /aegis/venv/bin/python /aegis/src/aegis_core/registry.py >/dev/null \
    || { echo "registry/unit cross-check failed"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

install -m 0644 "$SRC/systemd/aegis-core.service"   /etc/systemd/system/
install -m 0644 "$SRC/systemd/aegis-agent@.service" /etc/systemd/system/

# Regenerate zone drop-ins from the manifests (idempotent; also propagates
# each manifest's memory limit into MemoryMax, so limits.memory_gib stops
# being decorative).
/aegis/venv/bin/python /aegis/bin/aegis-zone-sync
systemctl daemon-reload

# Cross-check: every manifest against its installed drop-in, both directions.
PYTHONPATH=/aegis/src /aegis/venv/bin/python /aegis/src/aegis_core/registry.py

echo
echo "Installed, deliberately NOT enabled:"
echo "  aegis-core.service        minimal heartbeat/health stub (:8080) —"
echo "                            the real task loop is the phase 5 build"
echo "  aegis-agent@<name>        preflight-only runner; refuses wrong zone"
echo
echo "Try the preflights without starting anything permanent:"
echo "  sudo systemctl start aegis-agent@documents; journalctl -u aegis-agent@documents -n 5"
