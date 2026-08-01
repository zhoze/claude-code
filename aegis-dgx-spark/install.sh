#!/usr/bin/env bash
# AEGIS guided installer for the NVIDIA DGX Spark (DGX OS).
#
# The flow is gated. Nothing installs until the machine has been scanned and
# the code reconciled against what the machine actually is:
#
#   1. ./scan/aegis-scan.sh                  read-only system profile
#   2. ./scan/aegis-compare.sh [--write-overrides]
#   3. amend code/machine until zero FAILs   (see scan/REVIEW-WITH-CLAUDE.md)
#   4. sudo ./install.sh                     phases 00-09, resumable
#
# Usage:
#   sudo ./install.sh                run/resume all phases in order
#   sudo ./install.sh --status       show phase state and exit
#   sudo ./install.sh --phase 05     force one phase to re-run
#   sudo ./install.sh --from 06      resume starting at a given phase
#
# State lives in /var/lib/aegis/install-state; a dropped SSH session or a
# reboot costs nothing — run install.sh again and it continues.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=bootstrap/lib.sh
. "$SRC/bootstrap/lib.sh"

PHASES=(
  "00-preflight        read-only checks + decision D1 acknowledgement"
  "01-users-and-dirs   service identities and the /aegis tree"
  "02-packages         OS packages (incl. build toolchain, rsync, age)"
  "03-python-env       venv, gate + core code, configs, nft template staged"
  "04-credentials      cloud API keys into systemd-creds (skippable)"
  "05-egress-gate      firewall (auto-rollback protected) + the Gate + verify"
  "06-models           llama.cpp build, model download THROUGH the gate, llm+embed services"
  "07-knowledge        Qdrant vector db, ingestion deps, agent manifests, D3 gate"
  "08-storage-backup   logrotate, timers, backup config, first snapshot"
  "09-scaffold         core stub + agent units installed (nothing enabled)"
)

phase_id()     { echo "${1%% *}"; }
phase_script() { echo "$SRC/bootstrap/$(phase_id "$1").sh"; }

status() {
  echo "AEGIS install state ($AEGIS_STATE_FILE):"
  for p in "${PHASES[@]}"; do
    local id; id="$(phase_id "$p")"
    if phase_is_done "$id"; then printf '  \033[32mdone\033[0m     %s\n' "$p"
    else printf '  \033[33mpending\033[0m  %s\n' "$p"; fi
  done
}

scan_gate() {
  local facts="$SRC/scan-report/facts.json"
  local status_f="$SRC/scan-report/compare-status.json"
  [[ -f "$facts" && -f "$status_f" ]] || die \
    "no scan/compare on record. Run ./scan/aegis-scan.sh then ./scan/aegis-compare.sh first — install.sh does not run blind. (docs: scan/REVIEW-WITH-CLAUDE.md)"
  python3 - "$facts" "$status_f" <<'PY' || die \
    "the recorded compare does not PASS against the current scan. Re-run ./scan/aegis-scan.sh and ./scan/aegis-compare.sh; resolve every FAIL first."
import hashlib, json, sys
facts, status = sys.argv[1], sys.argv[2]
s = json.load(open(status))
h = hashlib.sha256(open(facts, "rb").read()).hexdigest()
ok = s.get("ok") and s.get("facts_sha256") == h
sys.exit(0 if ok else 1)
PY
  ok "scan gate: compare passed against the current facts.json"
  local age_days
  age_days=$(( ( $(date +%s) - $(stat -c %Y "$facts") ) / 86400 ))
  (( age_days <= 7 )) || warn "scan is ${age_days} days old — consider re-scanning"
}

main() {
  local force_phase="" from_phase=""
  case "${1:-}" in
    --status) status; exit 0 ;;
    --phase)  force_phase="${2:?usage: --phase NN}" ;;
    --from)   from_phase="${2:?usage: --from NN}" ;;
    --help|-h) sed -n '2,21p' "$0"; exit 0 ;;
    "") ;;
    *) die "unknown option ${1}" ;;
  esac

  require_root
  scan_gate

  # Resolve and persist the admin identity exactly once.
  load_site "$SRC"
  if [[ -z "${ADMIN_USER:-}" || "$ADMIN_USER" == "root" ]]; then
    read -rp "Admin (operator) username on this box: " ADMIN_USER
  fi
  require_admin_user
  if ! grep -q '^ADMIN_USER=' "$AEGIS_DEFAULTS" 2>/dev/null; then
    echo
    echo "Installing for admin user: $ADMIN_USER"
    echo "This account will: hold aegis-operators membership (mints egress"
    echo "approvals), keep its own firewall egress rule, and administer the box."
    confirm "Correct?" || die "set the right user and re-run"
  fi
  save_site_admin "$ADMIN_USER"

  for p in "${PHASES[@]}"; do
    local id script
    id="$(phase_id "$p")"
    script="$(phase_script "$p")"

    if [[ -n "$force_phase" && "$id" != "$force_phase"* ]]; then continue; fi
    if [[ -n "$from_phase" && "$id" < "$from_phase" ]]; then continue; fi
    if [[ -z "$force_phase" ]] && phase_is_done "$id"; then
      ok "skip $p (done — use --phase ${id%%-*} to re-run)"
      continue
    fi

    echo
    log "=== phase $p ==="
    [[ -x "$script" || -f "$script" ]] || die "missing $script"
    bash "$script" || die "phase $id failed — fix and re-run sudo ./install.sh (it resumes here)"
    if ! bash "$script" --verify; then
      die "phase $id ran but its verification failed — investigate before continuing"
    fi
    phase_mark_done "$id"
    ok "phase $id complete and verified"
  done

  echo
  log "install complete."
  echo
  echo "  aegis-approve --list                    # approvals outstanding"
  echo "  systemctl status aegis-egress-gate aegis-llm aegis-embed aegis-vectordb"
  echo "  sudo $SRC/tests/verify-egress.sh        # re-prove the boundary any time"
  echo
  echo "  NOTE: your aegis-operators membership needs a fresh login session"
  echo "  before aegis-approve works from your own shell (or use:"
  echo "  sg aegis-operators -c 'aegis-approve --list')."
  echo
  echo "Read docs/part-17-roadmap.md for what phases 5-10 look like from here."
}

main "$@"
