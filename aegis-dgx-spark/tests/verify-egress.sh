#!/usr/bin/env bash
# AEGIS — egress policy verification.
#
# An untested policy is not a policy. Run this after phase 05, after every
# change to aegis.nft.tmpl or egress.toml, and on a schedule.
#
# Each test asserts a property the architecture depends on. If any FAIL, the
# privacy boundary is not what the documents claim it is.
#
# AEGIS_VERIFY_NONINTERACTIVE=1 (set by aegis-verify.service) skips the one
# test that mints a real approval token and touches a live cloud endpoint —
# a scheduled root-minted token would itself be a hole.
set -uo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo (needs setpriv)"; exit 1; }

PASS=0; FAIL=0; SKIP=0
GATE="http://127.0.0.1:3128"
# An anycast IP that answers 443 regardless of DNS: lets us distinguish
# "kernel blocked the packet" from "DNS was broken anyway".
PROBE_IP="1.1.1.1"

t_pass() { printf '  \033[32mPASS\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
t_fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
t_skip() { printf '  \033[33mskip\033[0m %s\n' "$*"; SKIP=$((SKIP+1)); }

# Run a command as a service user, with a hard timeout.
as() { local u=$1; shift; timeout 12 setpriv --reuid="$u" --regid="$u" --clear-groups "$@"; }

echo "AEGIS egress verification — $(date -Is)"
echo

echo "1. Direct egress is blocked for every service user (IP-literal probe:"
echo "   cannot false-pass on broken DNS)"
for u in aegis-core aegis-lt aegis-ht aegis-llm aegis-vectordb; do
  if as "$u" curl -s --max-time 8 --noproxy '*' "https://${PROBE_IP}" -o /dev/null -k 2>/dev/null; then
    t_fail "$u reached the internet WITHOUT the gate — nftables is not enforcing"
  else
    t_pass "$u cannot bypass the gate"
  fi
done

echo
echo "2. High-trust agents cannot reach the gate at all"
if as aegis-ht curl -s --max-time 8 --proxy "$GATE" https://api.anthropic.com >/dev/null 2>&1; then
  t_fail "aegis-ht reached the gate — the trust split is not enforced"
else
  t_pass "aegis-ht is denied the gate (no internet path, approved or not)"
fi

echo
echo "3. Non-allowlisted hosts are refused by the gate"
out=$(as aegis-core curl -s --max-time 8 --proxy "$GATE" -o /dev/null -w '%{http_code}' \
      https://example.com 2>/dev/null)
[[ "$out" != "200" ]] && t_pass "example.com refused (got ${out:-no response})" \
                      || t_fail "example.com was allowed — check the allowlist"

echo
echo "4. Gated routes refuse without an approval token"
out=$(as aegis-core curl -s --max-time 8 --proxy "$GATE" -o /dev/null -w '%{http_code}' \
      https://api.anthropic.com 2>/dev/null)
[[ "$out" != "200" ]] && t_pass "api.anthropic.com refused without approval (got ${out:-no response})" \
                      || t_fail "gated route allowed with no token — the gate is not gating"

echo
echo "5. Gated routes work WITH an approval token, and burn it"
if [[ "${AEGIS_VERIFY_NONINTERACTIVE:-0}" == "1" ]]; then
  t_skip "non-interactive run: not minting a live token as root (by design)"
elif [[ -z "${SUDO_USER:-}" || "${SUDO_USER:-root}" == "root" ]]; then
  t_skip "no operator context (SUDO_USER unset) — run from your own sudo session to test the full path"
elif ! command -v aegis-approve >/dev/null; then
  t_skip "aegis-approve not installed yet"
else
  if ! sudo -u "$SUDO_USER" aegis-approve --revoke-all >/dev/null 2>&1; then
    t_fail "aegis-approve --revoke-all failed for $SUDO_USER (group membership? fresh login needed?)"
  fi
  if ! sudo -u "$SUDO_USER" aegis-approve api.anthropic.com \
        --reason "verify-egress test 5" --ttl 60 >/dev/null 2>&1; then
    t_fail "aegis-approve could not mint (this used to crash on os.chown — check bootstrap/01 perms)"
  else
    # curl through the gate: '000' means no tunnel at all.
    first=$(as aegis-core curl -s --max-time 10 --proxy "$GATE" -o /dev/null -w '%{http_code}' \
            https://api.anthropic.com/v1/messages 2>/dev/null)
    if [[ -n "$first" && "$first" != "000" ]]; then
      t_pass "approved request established a tunnel (upstream said ${first})"
    else
      t_fail "approved request did not connect (got ${first:-nothing}) — token present but unusable (burn permissions?)"
    fi
    second=$(as aegis-core curl -s --max-time 8 --proxy "$GATE" -o /dev/null -w '%{http_code}' \
             https://api.anthropic.com/v1/messages 2>/dev/null)
    if [[ "$second" == "000" || -z "$second" || "$second" == "403" ]]; then
      t_pass "token was single-use (second attempt got ${second:-no response})"
    else
      t_fail "token appears reusable (second attempt got ${second}) — check ApprovalQueue.consume"
    fi
    sudo -u "$SUDO_USER" aegis-approve --revoke-all >/dev/null 2>&1 || true
  fi
fi

echo
echo "6. DNS is not available to agents (removes a covert channel)"
if as aegis-lt timeout 6 python3 -c 'import socket; socket.getaddrinfo("api.anthropic.com", 443)' 2>/dev/null; then
  t_fail "aegis-lt can resolve names directly"
else
  t_pass "agents cannot resolve; the gate resolves on their behalf"
fi

echo
echo "7. Audit log is being written"
if [[ -s /aegis/logs/egress/audit.jsonl ]]; then
  t_pass "audit log has $(wc -l < /aegis/logs/egress/audit.jsonl) entries"
  echo "     last denial:"
  jq -c 'select(.event=="deny")' /aegis/logs/egress/audit.jsonl 2>/dev/null | tail -1 | sed 's/^/     /'
else
  t_fail "no audit log — decisions are not being recorded"
fi

echo
echo "8. No unexpected listeners"
# Expected: AEGIS loopback services, SSH, systemd-resolved's stubs
# (127.0.0.53/54:53) and Tailscale's local API (100.100.100.100) — the old
# pattern flagged the resolver stubs every Monday at 07:00.
unexpected=$(ss -tlnH | awk '{print $4}' \
  | grep -vE '^127\.0\.0\.1:(3128|8000|8001|8080|6333)$|^127\.0\.0\.5[34]:53$|^100\.100\.100\.100:|^\[::1\]|^0\.0\.0\.0:22$|^\*:22$|^\[::\]:22$' || true)
[[ -z "$unexpected" ]] && t_pass "only expected ports are listening" \
                       || { t_fail "unexpected listeners:"; echo "$unexpected" | sed 's/^/       /'; }

echo
printf 'verification: %d passed, %d failed, %d skipped\n' "$PASS" "$FAIL" "$SKIP"
(( FAIL == 0 )) || { echo "the boundary is NOT what the documents claim — do not build on it"; exit 1; }
