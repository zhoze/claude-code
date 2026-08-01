#!/usr/bin/env bash
# AEGIS phase 01 — service identities and the /aegis tree.
#
# Six roles, six uids. The separation between them is what the nftables
# policy and the Gate both key off, so do not collapse them "for now".
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root
load_site "$SRC"
require_admin_user

AEGIS_USERS=(aegis-core aegis-proxy aegis-llm aegis-lt aegis-ht aegis-vectordb)

verify() {
  local rc=0
  for u in "${AEGIS_USERS[@]}"; do
    id "$u" >/dev/null 2>&1 || { echo "missing user $u"; rc=1; }
  done
  getent group aegis-operators >/dev/null || { echo "missing group aegis-operators"; rc=1; }
  id -nG "$ADMIN_USER" | tr ' ' '\n' | grep -qx aegis-operators \
    || { echo "$ADMIN_USER not in aegis-operators"; rc=1; }
  id -nG aegis-proxy | tr ' ' '\n' | grep -qx aegis-operators \
    || { echo "aegis-proxy not in aegis-operators (cannot burn tokens)"; rc=1; }
  [[ "$(stat -c '%a %U %G' /aegis/run/approvals)" == "2770 root aegis-operators" ]] \
    || { echo "approvals dir is $(stat -c '%a %U:%G' /aegis/run/approvals), want 2770 root:aegis-operators"; rc=1; }
  [[ -d /aegis/scratch/lt && -d /aegis/scratch/ht ]] || { echo "scratch split missing"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

echo "== service users =="
# --system: no login shell, no home in /home, uid below 1000.
for u in "${AEGIS_USERS[@]}"; do
  if id "$u" >/dev/null 2>&1; then
    echo "  exists: $u"
  else
    useradd --system --no-create-home --shell /usr/sbin/nologin \
            --comment "AEGIS ${u#aegis-}" "$u"
    echo "  created: $u"
  fi
done

# Operators may mint egress approvals. You, and nobody else — plus the Gate
# itself as a SUPPLEMENTARY member so it can read and burn tokens in the
# setgid queue. Membership lets the Gate consume approvals; it still cannot
# mint into the directory in any way that matters, because minting is just
# file creation and anything the Gate wrote would be an approval the Gate
# chose to honour anyway — the property that counts is that CORE and the
# agents are not members and cannot reach the directory at all.
getent group aegis-operators >/dev/null || groupadd --system aegis-operators
usermod -aG aegis-operators "$ADMIN_USER"
usermod -aG aegis-operators aegis-proxy
echo "  $ADMIN_USER and aegis-proxy added to aegis-operators"
echo "  (a FRESH LOGIN is needed before $ADMIN_USER's shells pick this up)"

echo
echo "== directory tree =="
install -d -m 0755 -o root       -g root        /aegis
# Config is world-traversable: it holds POLICY, never secrets (rule 1 in the
# README; keys live in systemd-creds). Per-file groups scope each consumer.
install -d -m 0755 -o root       -g root        /aegis/config
install -d -m 0755 -o aegis-llm  -g aegis-llm   /aegis/models
install -d -m 0750 -o aegis-ht   -g aegis-ht    /aegis/knowledge      # business data
install -d -m 0750 -o aegis-ht   -g aegis-ht    /aegis/knowledge/inbox
install -d -m 0750 -o aegis-ht   -g aegis-ht    /aegis/knowledge/.quarantine
install -d -m 0750 -o aegis-vectordb -g aegis-vectordb /aegis/vectordb
# Scratch is per-zone. One shared directory would be a channel between the
# zones (and the old single-owner layout broke high-trust agents entirely).
install -d -m 0755 -o root       -g root        /aegis/scratch
install -d -m 0750 -o aegis-lt   -g aegis-lt    /aegis/scratch/lt
install -d -m 0750 -o aegis-ht   -g aegis-ht    /aegis/scratch/ht
install -d -m 0755 -o root       -g root        /aegis/logs
# setgid + group aegis-operators: the daily `jq` over the audit log is an
# OPERATOR task; needing root to read your own audit trail meant it did not
# get read.
install -d -m 2750 -o aegis-proxy -g aegis-operators /aegis/logs/egress
install -d -m 0750 -o aegis-core -g aegis-core  /aegis/logs/core
install -d -m 0750 -o aegis-llm  -g aegis-llm   /aegis/logs/llm
install -d -m 0700 -o root       -g root        /aegis/backups
install -d -m 0755 -o root       -g root        /aegis/bin
install -d -m 0755 -o root       -g root        /aegis/run
install -d -m 0750 -o aegis-core -g aegis-core  /aegis/run/core
# Tier-2/3 memory state (part-12) and the erasure surface aegis-forget scans.
install -d -m 0750 -o aegis-core -g aegis-core  /aegis/state

# The approval queue: operators (and the Gate, via supplementary membership)
# write; setgid so tokens inherit the group. NO sticky bit — sticky would
# stop the Gate unlinking operator-owned tokens, which silently turns
# "single-use" into "no gated route ever works". NO ACLs — one mechanism,
# visible in ls -l, hard to misread.
install -d -m 2770 -o root -g aegis-operators /aegis/run/approvals
echo "  /aegis tree created"

echo
echo "== what each role can see =="
printf '  %-14s %s\n' \
  aegis-core     "orchestration; reads config, writes core logs + state" \
  aegis-proxy    "egress gate ONLY; stages cloud credentials" \
  aegis-llm      "model runtime (llm + embeddings); reads /aegis/models" \
  aegis-lt       "low-trust agents: web + research, NO business data" \
  aegis-ht       "high-trust agents: /aegis/knowledge, NO internet" \
  aegis-vectordb "qdrant server; owns /aegis/vectordb, initiates nothing"

echo
echo "Deliberately NOT done here: giving any service user a password, a shell,"
echo "or membership in sudo. If you find yourself wanting to, stop and re-read"
echo "docs/part-05."
