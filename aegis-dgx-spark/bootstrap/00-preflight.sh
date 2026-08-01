#!/usr/bin/env bash
# AEGIS phase 00 — preflight. Read-only apart from recording decision D1.
# Run after the scan/compare stages (install.sh enforces that order).
set -uo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"

pass() { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn2() { printf '  \033[33mwarn\033[0m %s\n' "$*"; WARN=$((WARN+1)); }
fail() { printf '  \033[31mFAIL\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
WARN=0; FAIL=0

verify() {
  # Postcondition for the driver: preflight leaves no state beyond the D1
  # acknowledgement marker.
  [[ -f /var/lib/aegis/d1-acknowledged || -n "$(lsblk -o TYPE 2>/dev/null | grep crypt)" ]] \
    || { echo "D1 not acknowledged"; exit 1; }
  exit 0
}
[[ "${1:-}" == "--verify" ]] && verify

echo "AEGIS preflight — $(date -Is)"
echo

echo "Platform"
. /etc/os-release 2>/dev/null || true
[[ "${VERSION_ID:-}" == "24.04" ]] && pass "base ${PRETTY_NAME:-unknown}" \
  || warn2 "expected Ubuntu 24.04 base, found ${PRETTY_NAME:-unknown}"
[[ "$(uname -m)" == "aarch64" ]] && pass "arch aarch64" \
  || fail "expected aarch64, found $(uname -m)"
[[ -f /etc/dgx-release ]] && pass "DGX OS release file present" \
  || warn2 "no /etc/dgx-release — is this really a DGX?"
SYSD=$(systemctl --version 2>/dev/null | awk 'NR==1{print $2}')
[[ "${SYSD:-0}" -ge 254 ]] && pass "systemd $SYSD (ImportCredential needs 254+)" \
  || fail "systemd ${SYSD:-unknown} — 254+ required"

echo
echo "Compute"
if command -v nvidia-smi >/dev/null; then
  if nvidia-smi -L >/dev/null 2>&1; then
    pass "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1)"
    pass "driver: $(nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1)"
  else
    fail "nvidia-smi present but returns no device"
  fi
else
  fail "nvidia-smi not found"
fi
command -v nvcc >/dev/null \
  && pass "nvcc $(nvcc --version | sed -n 's/.*release \([0-9.]*\).*/\1/p') (CUDA build of llama.cpp possible)" \
  || warn2 "nvcc not found — phase 06 falls back to a CPU build unless the CUDA toolkit is installed"

echo
echo "Memory (unified — models, KV cache, vector db and OS all share this)"
TOTAL_KB=$(awk '/MemTotal/{print $2}' /proc/meminfo)
TOTAL_GB=$((TOTAL_KB/1024/1024))
echo "  total ${TOTAL_GB} GiB, available $(($(awk '/MemAvailable/{print $2}' /proc/meminfo)/1024/1024)) GiB"
(( TOTAL_GB >= 100 )) && pass "memory budget viable (see docs/part-03)" \
  || warn2 "less memory than a Spark should report"

echo
echo "Storage"
df -h --output=target,size,avail,pcent / /home 2>/dev/null | sed 's/^/  /'
ROOT_AVAIL=$(df -BG --output=avail / | tail -1 | tr -dc '0-9')
(( ROOT_AVAIL >= 150 )) && pass "root has ${ROOT_AVAIL}G free" \
  || warn2 "only ${ROOT_AVAIL}G free on / — model weights are large"
lsblk -d -o NAME,SIZE,ROTA,MODEL | sed 's/^/  /'

echo
echo "Disk encryption — decision D1 (docs/part-04, docs/DECISIONS.md)"
if lsblk -o TYPE | grep -q crypt; then
  pass "encrypted volume present"
elif [[ -f /var/lib/aegis/d1-acknowledged ]]; then
  warn2 "no LUKS; D1 acknowledged previously ($(cat /var/lib/aegis/d1-acknowledged))"
else
  echo "  No LUKS volume detected. Encrypting after the fact means"
  echo "  REINSTALLING DGX OS. Everything this system will hold — client"
  echo "  documents, mail, the vector index — sits on these disks."
  echo "  Continue only if you accept unencrypted storage, and record the"
  echo "  decision with a date in docs/DECISIONS.md (D1)."
  if [[ $EUID -eq 0 ]] && confirm_typed "  Type ACCEPT-UNENCRYPTED to proceed (anything else aborts): " "ACCEPT-UNENCRYPTED"; then
    install -d -m 0755 /var/lib/aegis
    date -Is > /var/lib/aegis/d1-acknowledged
    warn2 "unencrypted storage explicitly accepted — recorded"
  else
    fail "no encryption and no acknowledgement — resolve D1 first"
  fi
fi

echo
echo "Required tooling (missing entries marked 'later' arrive in phase 02)"
for tool in systemctl python3 curl; do
  command -v "$tool" >/dev/null && pass "$tool" || fail "$tool missing"
done
for tool in nft git ss jq rsync; do
  command -v "$tool" >/dev/null && pass "$tool" || warn2 "$tool missing (installed by phase 02)"
done
PYV=$(python3 -c 'import sys;print("%d.%d"%sys.version_info[:2])' 2>/dev/null)
[[ "${PYV%%.*}" == "3" && "${PYV#*.}" -ge 11 ]] && pass "python $PYV" \
  || fail "python 3.11+ required for tomllib, found ${PYV:-none}"

echo
echo "Current network exposure (this is what the world can reach today)"
ss -tlnp 2>/dev/null | awk 'NR>1{print "  "$4"  "$6}' | sort -u

echo
echo "Firewall state"
if nft list ruleset 2>/dev/null | grep -q 'table inet aegis'; then
  warn2 "an AEGIS table is already loaded — phase 05 will replace it atomically"
elif [[ -z "$(nft list ruleset 2>/dev/null)" ]]; then
  pass "no ruleset loaded (expected on a clean box)"
else
  warn2 "a non-AEGIS nftables ruleset is loaded; phase 05 replaces ONLY table inet aegis, but review coexistence (scan-report/compare-report.txt)"
fi

echo
echo "Time sync"
timedatectl show -p NTPSynchronized --value 2>/dev/null | grep -q yes \
  && pass "clock synchronised" || warn2 "clock not synchronised — fix before TLS work"

echo
printf 'preflight complete: %d failures, %d warnings\n' "$FAIL" "$WARN"
(( FAIL == 0 )) || { echo "resolve failures before continuing"; exit 1; }
