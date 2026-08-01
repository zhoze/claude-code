#!/usr/bin/env bash
# AEGIS phase 08 — log rotation, scratch reaping, backup timers, and the
# first snapshot (a backup you have not run is a hypothesis).
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root

verify() {
  local rc=0
  for t in aegis-backup aegis-verify aegis-reconcile aegis-scratch-reaper; do
    systemctl is-enabled --quiet "$t.timer" || { echo "$t.timer not enabled"; rc=1; }
  done
  [[ -L /aegis/backups/current ]] || { echo "no current-snapshot symlink (first backup failed?)"; rc=1; }
  /aegis/bin/aegis-backup --verify >/dev/null || { echo "backup --verify failed"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

install -m0755 "$SRC/scripts/aegis-backup.sh"    /aegis/bin/aegis-backup
install -m0755 "$SRC/scripts/aegis-reconcile.py" /aegis/bin/aegis-reconcile
install -m0755 "$SRC/scripts/aegis-forget.py"    /aegis/bin/aegis-forget
install -m0755 "$SRC/tests/verify-egress.sh"     /aegis/bin/verify-egress.sh
install -d -m0755 /aegis/docs && cp "$SRC"/docs/*.md /aegis/docs/

# Rotation: `create` owners must match the WRITING uid, or rotation quietly
# breaks the writer (the old blanket `create 0640 root root` did exactly
# that to Core's log). `su` keeps logrotate happy about non-root parent dirs.
cat > /etc/logrotate.d/aegis <<'CONF'
/aegis/logs/core/*.log /aegis/logs/core/*.jsonl {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 aegis-core aegis-core
    su aegis-core aegis-core
}

/aegis/logs/llm/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 aegis-llm aegis-llm
    su aegis-llm aegis-llm
}

/aegis/logs/egress/audit.jsonl {
    weekly
    rotate 104
    compress
    copytruncate
    missingok
    create 0640 aegis-proxy aegis-operators
    su aegis-proxy aegis-operators
}
CONF
# copytruncate above is deliberate: rotating out from under the Gate's open
# handle would stop auditing, and the Gate denies egress when it cannot
# write. That would take the system offline for a log rotation.

# Backup destinations: tier 1 works with no config; tiers 2 and 3 wait until
# you fill these in. The skeleton exists so the settings have one obvious
# home instead of being an env var you once exported.
if [[ ! -f /etc/default/aegis-backup ]]; then
  cat > /etc/default/aegis-backup <<'CONF'
# AEGIS backup destinations (see docs/part-06-storage.md).
# Tier 2 — another machine on your LAN (nightly):
#AEGIS_OFFBOX=nas.local:/volume1/aegis
# Tier 3 — off-site, age-encrypted (weekly). The decryption key must NOT
# live on this machine:
#AEGIS_OFFSITE=user@remote:/backups/aegis
#AEGIS_AGE_RECIPIENT=age1...
CONF
  chmod 0600 /etc/default/aegis-backup
fi

for unit in aegis-backup aegis-verify aegis-reconcile aegis-scratch-reaper; do
  install -m0644 "$SRC/systemd/$unit.service" /etc/systemd/system/
  install -m0644 "$SRC/systemd/$unit.timer"   /etc/systemd/system/
done
systemctl daemon-reload
systemctl enable --now aegis-backup.timer aegis-verify.timer \
                       aegis-reconcile.timer aegis-scratch-reaper.timer
systemctl list-timers 'aegis-*' --no-pager

echo
echo "== first snapshot, verified now rather than discovered broken later =="
/aegis/bin/aegis-backup --tier local
/aegis/bin/aegis-backup --verify

echo
echo "Set AEGIS_OFFBOX / AEGIS_OFFSITE / AEGIS_AGE_RECIPIENT in"
echo "/etc/default/aegis-backup, then run the restore drill:"
echo "  sudo /aegis/bin/aegis-backup --restore-test /tmp/rt"
echo "Record the RTO you actually achieved, and D4 (retention = your honest"
echo "erasure horizon) in docs/DECISIONS.md."
