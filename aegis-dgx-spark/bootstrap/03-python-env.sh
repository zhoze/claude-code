#!/usr/bin/env bash
# AEGIS phase 03 — python environment and code installation. Idempotent:
# re-running resyncs code exactly (rsync --delete, no cp -r nesting).
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root

verify() {
  local rc=0
  [[ -x /aegis/venv/bin/python ]] || { echo "venv missing"; rc=1; }
  [[ -f /aegis/src/egress_gate/gate.py ]] || { echo "gate.py not installed"; rc=1; }
  [[ -f /aegis/src/aegis_core/registry.py ]] || { echo "aegis_core not installed"; rc=1; }
  [[ ! -d /aegis/src/egress_gate/egress_gate ]] || { echo "nested egress_gate dir (old cp -r bug)"; rc=1; }
  [[ -x /usr/local/bin/aegis-approve ]] || { echo "aegis-approve missing"; rc=1; }
  [[ -f /aegis/config/nftables/aegis.nft.tmpl ]] || { echo "nft template not staged"; rc=1; }
  sudo -u aegis-proxy head -c1 /aegis/config/egress.toml >/dev/null 2>&1 \
    || { echo "aegis-proxy cannot read egress.toml"; rc=1; }
  /aegis/venv/bin/python -c 'import sys; assert sys.version_info >= (3,11)' \
    || { echo "venv python too old"; rc=1; }
  python3 -m py_compile /aegis/src/egress_gate/*.py /aegis/src/aegis_core/*.py \
    || { echo "installed code does not compile"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

[[ -x /aegis/venv/bin/python ]] || python3 -m venv /aegis/venv
/aegis/venv/bin/pip install --upgrade pip wheel >/dev/null

# The Gate has zero third-party dependencies on purpose: the component that
# enforces your security boundary should not have a supply chain. Core's
# ingest pipeline (also synced here so phases 5+ have one code drop) keeps
# its imports lazy — nothing below needs pip yet.
install_tree "$SRC/src/egress_gate" /aegis/src/egress_gate
install_tree "$SRC/src/aegis_core"  /aegis/src/aegis_core
chown -R root:root /aegis/src
find /aegis/src -type d -exec chmod 0755 {} +
find /aegis/src -type f -exec chmod 0644 {} +
chmod 0755 /aegis/src/egress_gate/gate.py

install -m 0755 "$SRC/src/egress_gate/approve.py" /usr/local/bin/aegis-approve
chown root:aegis-operators /usr/local/bin/aegis-approve
chmod 0750 /usr/local/bin/aegis-approve

# Operator tooling used by later phases.
install -m 0755 "$SRC/scripts/aegis-zone-sync.py"    /aegis/bin/aegis-zone-sync
install -m 0755 "$SRC/scripts/aegis-verify-model.py" /aegis/bin/aegis-verify-model

# Config. The gate's config is group-readable by aegis-proxy — no ACLs, the
# group IS the statement of who reads it. /aegis/config holds policy, never
# secrets (those live in systemd-creds), so 0755 on the directory is fine.
install -m 0640 -o root -g aegis-proxy "$SRC/config/egress.toml" /aegis/config/egress.toml

# The nftables TEMPLATE is staged; phase 05 renders (@@ADMIN_USER@@) and
# applies it. The rendered file is never edited by hand and never shipped.
install -d -m 0755 /aegis/config/nftables
install -m 0644 "$SRC/config/nftables/aegis.nft.tmpl" /aegis/config/nftables/aegis.nft.tmpl

echo "installed. aegis-approve is runnable only by the aegis-operators group."
