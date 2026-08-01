#!/usr/bin/env bash
# AEGIS phase 04 — cloud credentials into systemd-creds (NEW PHASE).
#
# The old sequence loaded two mandatory credentials the docs never told you
# to create, so the Gate refused to start — one step AFTER the default-deny
# firewall was live. This phase provisions them BEFORE the boundary goes up,
# and the unit now uses ImportCredential= so an absent key degrades a future
# cloud route, never the Gate itself.
#
# Keys are optional at install time. Skipping one is fine: local-only
# operation needs neither. Add them later by re-running:
#   sudo ./install.sh --phase 04
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root

CREDSTORE=/etc/credstore.encrypted
CREDS=(anthropic_api_key nvidia_api_key)

verify() {
  # Provisioned or explicitly skipped — both acceptable, silence is not.
  for name in "${CREDS[@]}"; do
    [[ -f "$CREDSTORE/$name" || -f /var/lib/aegis/cred-skipped-$name ]] \
      || { echo "credential $name neither provisioned nor skipped"; exit 1; }
  done
  exit 0
}
[[ "${1:-}" == "--verify" ]] && verify

command -v systemd-creds >/dev/null || die "systemd-creds not found (systemd 250+?)"
install -d -m 0755 "$CREDSTORE"
install -d -m 0755 /var/lib/aegis

describe() {
  case "$1" in
    anthropic_api_key)
      echo "Anthropic API key (console.anthropic.com — AEGIS's OWN key, never"
      echo "your claude.ai login). Used only by the future gated cloud route." ;;
    nvidia_api_key)
      echo "NVIDIA API key (build.nvidia.com NIM endpoints). Same rules." ;;
  esac
}

for name in "${CREDS[@]}"; do
  echo
  if [[ -f "$CREDSTORE/$name" ]]; then
    echo "$name: already provisioned ($CREDSTORE/$name) — leaving it alone."
    echo "  (to rotate: delete the file and re-run this phase)"
    rm -f "/var/lib/aegis/cred-skipped-$name"
    continue
  fi
  describe "$name"
  read -rsp "  paste key for $name (empty to SKIP): " key; echo
  if [[ -z "$key" ]]; then
    date -Is > "/var/lib/aegis/cred-skipped-$name"
    echo "  skipped — recorded. Cloud routes needing it will fail cleanly."
    continue
  fi
  # stdin -> encrypted blob; the plaintext never touches disk or argv.
  printf '%s' "$key" | systemd-creds encrypt --name="$name" - "$CREDSTORE/$name"
  chmod 0600 "$CREDSTORE/$name"
  rm -f "/var/lib/aegis/cred-skipped-$name"
  unset key
  echo "  encrypted into $CREDSTORE/$name"
done

echo
echo "Rules that still hold: no key in the repo, in /aegis/config, in a shell"
echo "history, or in a chat window. Rotation: revoke at the provider FIRST,"
echo "then re-run this phase. Test one revocation per quarter (part-05)."
