#!/usr/bin/env bash
# AEGIS phase 05 — render + apply the egress policy, start the Gate, verify.
#
# This is the step that makes the box private, and the step that can strand
# a headless machine if it goes wrong. So it cannot go wrong silently:
#
#   1. render the template (@@ADMIN_USER@@ -> your user), nft -c it
#   2. snapshot whatever ruleset is live now
#   3. arm a DEAD-MAN TIMER: if you do not type CONFIRM within 3 minutes,
#      the previous ruleset is restored automatically
#   4. apply, probe (DNS, apt, gate), ask for CONFIRM
#   5. only then persist across boots and run the full verification
#
# The policy replaces ONLY `table inet aegis`. Docker's tables survive.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root
load_site "$SRC"
require_admin_user

TMPL=/aegis/config/nftables/aegis.nft.tmpl
RULES=/aegis/config/nftables/aegis.nft
ROLLBACK_DELAY="${AEGIS_NFT_ROLLBACK_DELAY:-180}"
PREV=/run/aegis-nft-prev.nft
RB=/run/aegis-nft-rollback.sh

verify() {
  local rc=0
  nft list table inet aegis >/dev/null 2>&1 || { echo "aegis table not loaded"; rc=1; }
  systemctl is-active --quiet aegis-egress-gate || { echo "gate not active"; rc=1; }
  grep -q "$RULES" /etc/nftables.conf 2>/dev/null || { echo "policy not persisted in /etc/nftables.conf"; rc=1; }
  getent hosts huggingface.co >/dev/null 2>&1 || { echo "DNS broken post-apply"; rc=1; }
  ss -tlnH | grep -q '127.0.0.1:3128' || { echo "gate not listening on 3128"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

[[ -f "$TMPL" ]] || die "no template at $TMPL — run phase 03 first"

echo "== render =="
# Template stays pristine; the rendered file is regenerated every run, so
# re-running with a different ADMIN_USER just works (no one-shot sed).
sed "s/@@ADMIN_USER@@/${ADMIN_USER}/g" "$TMPL" > "$RULES.next"
grep -q '@@' "$RULES.next" && die "unrendered @@tokens@@ remain in $RULES.next"
nft -c -f "$RULES.next" || die "rendered ruleset does not parse — nothing applied"
mv "$RULES.next" "$RULES"
echo "  rendered for admin user '$ADMIN_USER'; syntax ok"

echo
echo "You are about to apply default-deny egress. If you are connected over"
echo "SSH from the LAN or Tailscale, that session is preserved by conntrack —"
echo "but read the rendered file yourself before answering:"
echo "    less $RULES"
confirm "Apply now (auto-rollback armed for ${ROLLBACK_DELAY}s)?" || { echo "aborted"; exit 1; }

echo
echo "== snapshot + dead-man timer =="
nft list ruleset > "$PREV" 2>/dev/null || : > "$PREV"
cat > "$RB" <<EOF
#!/bin/sh
# AEGIS auto-rollback: restores the pre-apply ruleset because no operator
# confirmed the new one in time.
nft flush ruleset
[ -s "$PREV" ] && nft -f "$PREV"
logger -p auth.crit "AEGIS nftables AUTO-ROLLBACK fired — policy reverted, no operator confirmation"
echo "AEGIS: firewall rolled back" > /run/aegis-nft-rolled-back
EOF
chmod 0700 "$RB"
systemctl stop aegis-nft-rollback.timer aegis-nft-rollback.service >/dev/null 2>&1 || true
systemctl reset-failed aegis-nft-rollback.service >/dev/null 2>&1 || true
rm -f /run/aegis-nft-rolled-back
systemd-run --on-active="$ROLLBACK_DELAY" --unit=aegis-nft-rollback "$RB" >/dev/null
echo "  rollback armed: fires in ${ROLLBACK_DELAY}s unless you confirm"

echo
echo "== apply =="
nft -f "$RULES"
echo "  applied (table inet aegis replaced atomically; other tables untouched)"

echo
echo "== probes (these three used to break silently on DGX OS) =="
PROBE_FAIL=0
if getent hosts ports.ubuntu.com >/dev/null 2>&1; then
  echo "  ok   DNS resolves (systemd-resolve rule works)"
else
  echo "  FAIL DNS is broken"; PROBE_FAIL=1
fi
if timeout 45 apt-get update -o APT::Update::Error-Mode=any -qq >/dev/null 2>&1; then
  echo "  ok   apt-get update works (_apt rule works)"
else
  echo "  warn apt-get update failed — investigate before confirming"
fi
if timeout 10 curl -s --max-time 8 https://ports.ubuntu.com >/dev/null 2>&1; then
  echo "  ok   root https egress works"
else
  echo "  warn root https probe failed"
fi
(( PROBE_FAIL == 0 )) || echo "  A FAILed probe means you probably want to let the rollback fire."

echo
if ! confirm_typed "Type CONFIRM to keep this policy (anything else / ${ROLLBACK_DELAY}s timeout rolls back): " "CONFIRM" $((ROLLBACK_DELAY - 20)); then
  echo "not confirmed — leaving the rollback timer armed. The previous"
  echo "ruleset returns in <${ROLLBACK_DELAY}s. Nothing was persisted."
  exit 1
fi
systemctl stop aegis-nft-rollback.timer >/dev/null 2>&1 || true
systemctl stop aegis-nft-rollback.service >/dev/null 2>&1 || true
echo "  confirmed; rollback disarmed"

echo
echo "== persist across boots =="
if [[ -f /etc/nftables.conf && ! -f /etc/nftables.conf.pre-aegis ]] \
   && ! grep -q "$RULES" /etc/nftables.conf; then
  cp /etc/nftables.conf /etc/nftables.conf.pre-aegis
  echo "  existing /etc/nftables.conf saved to /etc/nftables.conf.pre-aegis"
fi
echo "include \"$RULES\"" > /etc/nftables.conf
systemctl enable nftables >/dev/null
echo "  /etc/nftables.conf includes the rendered policy"

echo
echo "== the Gate =="
install -m 0644 "$SRC/systemd/aegis-egress-gate.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aegis-egress-gate
sleep 1
systemctl is-active --quiet aegis-egress-gate \
  && echo "  gate is up on 127.0.0.1:3128" \
  || { echo "gate failed to start:"; journalctl -u aegis-egress-gate -n 30 --no-pager; exit 1; }

echo
echo "== full boundary verification =="
bash "$SRC/tests/verify-egress.sh" || die "verify-egress failed — the boundary is not what the docs claim; fix before phase 06"

echo
echo "The boundary holds. Model downloads in phase 06 go THROUGH this gate —"
echo "that is the first test that it passes traffic you want."
