#!/usr/bin/env bash
# AEGIS stage 1 — full system scan. STRICTLY READ-ONLY.
# Writes scan-report/{facts.json,report.txt} and nothing else.
#
#   ./scan/aegis-scan.sh              # as your admin user
#   sudo ./scan/aegis-scan.sh         # richer detail (ruleset, sshd -T)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"

command -v python3 >/dev/null || { echo "python3 is required"; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "python3 >= 3.9 required to run the scanner"; exit 1; }

exec python3 "$HERE/aegis_scan.py" "${1:-$REPO/scan-report}"
