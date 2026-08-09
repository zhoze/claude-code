#!/usr/bin/env bash
# Installs a systemd user timer that starts the pipeline T-120 minutes before the
# regular US open (spec §49). 09:30 ET = 07:30 ET start; the unit runs at a fixed
# UTC time and run_daily.py itself checks the exchange calendar and exits on
# holidays. DST: 07:30 ET is 11:30 UTC (EDT) / 12:30 UTC (EST) — we trigger at
# both and the pipeline's snapshot-freeze guard makes the second run a no-op.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-$(command -v python)}"
UNIT_DIR="${HOME}/.config/systemd/user"
mkdir -p "${UNIT_DIR}"

cat > "${UNIT_DIR}/quant-screener.service" <<EOF
[Unit]
Description=DGX Quant Screener daily pre-market run
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${PROJECT_DIR}
ExecStart=${PYTHON_BIN} ${PROJECT_DIR}/run_daily.py
TimeoutStartSec=7200
Environment=PYTHONUNBUFFERED=1
EOF

cat > "${UNIT_DIR}/quant-screener.timer" <<EOF
[Unit]
Description=Run DGX Quant Screener before each US market open

[Timer]
# 07:30 America/New_York in both DST states; duplicate runs are no-ops because
# the day's snapshot freeze refuses to overwrite (spec §31).
OnCalendar=Mon..Fri 11:30 UTC
OnCalendar=Mon..Fri 12:30 UTC
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now quant-screener.timer
echo "Installed. Check with: systemctl --user list-timers quant-screener.timer"
echo "Logs: journalctl --user -u quant-screener.service -f"
