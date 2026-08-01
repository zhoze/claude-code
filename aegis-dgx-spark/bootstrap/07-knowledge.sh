#!/usr/bin/env bash
# AEGIS phase 07 — vector database (real Qdrant) and ingestion.
#
# The old unit invoked `python -m qdrant_client.local_server`, which does not
# exist — qdrant-client is a client library. This phase installs the actual
# Qdrant server: the official aarch64 release binary, pinned, hash-recorded,
# running under its OWN uid (aegis-vectordb) so a compromised high-trust
# agent cannot rewrite the index files behind the server's back.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root
load_site "$SRC"

QDRANT_FALLBACK_VERSION=v1.12.4

verify() {
  local rc=0
  systemctl is-active --quiet aegis-vectordb || { echo "aegis-vectordb not active"; rc=1; }
  curl -s --max-time 10 http://127.0.0.1:6333/readyz | grep -qi 'pass\|ready\|ok' \
    || { echo "qdrant /readyz not answering"; rc=1; }
  /aegis/venv/bin/python -c 'import pypdf, docx, openpyxl' 2>/dev/null \
    || { echo "ingestion libraries missing from venv"; rc=1; }
  PYTHONPATH=/aegis/src /aegis/venv/bin/python /aegis/src/aegis_core/registry.py >/dev/null \
    || { echo "registry check failed"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

# ---------------------------------------------------------------------------
# 1. Qdrant server binary (root downloads directly: root egress is the
#    deliberate maintenance hole, see part-07).
# ---------------------------------------------------------------------------
echo "== qdrant server =="
if [[ -x /aegis/bin/qdrant ]]; then
  echo "  qdrant already installed: $(/aegis/bin/qdrant --version 2>/dev/null || echo '?')"
else
  VER="${AEGIS_QDRANT_VERSION:-}"
  if [[ -z "$VER" ]]; then
    VER=$(curl -fsSL --max-time 20 https://api.github.com/repos/qdrant/qdrant/releases/latest \
          | jq -r .tag_name 2>/dev/null || true)
    [[ -n "$VER" && "$VER" != "null" ]] || VER="$QDRANT_FALLBACK_VERSION"
  fi
  echo "  version: $VER (override with AEGIS_QDRANT_VERSION=vX.Y.Z)"
  TARBALL=/aegis/build/qdrant-${VER}.tar.gz
  install -d -m 0755 /aegis/build
  ok_dl=""
  for triple in aarch64-unknown-linux-gnu aarch64-unknown-linux-musl; do
    URL="https://github.com/qdrant/qdrant/releases/download/${VER}/qdrant-${triple}.tar.gz"
    echo "  trying $URL"
    if curl -fL --retry 3 --progress-bar "$URL" -o "$TARBALL"; then ok_dl=1; break; fi
  done
  [[ -n "$ok_dl" ]] || die "could not download a qdrant aarch64 release for $VER — check the release page assets and set AEGIS_QDRANT_VERSION"
  SHA=$(sha256sum "$TARBALL" | awk '{print $1}')
  echo "  sha256 $SHA — cross-check against the release page checksums"
  tar -xzf "$TARBALL" -C /aegis/build qdrant
  install -m 0755 /aegis/build/qdrant /aegis/bin/qdrant
  rm -f /aegis/build/qdrant
  sed -i '/^AEGIS_QDRANT_INSTALLED=/d' "$AEGIS_DEFAULTS" 2>/dev/null || true
  echo "AEGIS_QDRANT_INSTALLED=${VER}+sha256=${SHA}" >> "$AEGIS_DEFAULTS"
fi

install -m 0640 -o root -g aegis-vectordb "$SRC/config/qdrant.yaml" /aegis/config/qdrant.yaml
install -d -m 0750 -o aegis-vectordb -g aegis-vectordb /aegis/vectordb
install -m 0644 "$SRC/systemd/aegis-vectordb.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now aegis-vectordb
for i in $(seq 1 12); do
  curl -s --max-time 3 http://127.0.0.1:6333/readyz >/dev/null 2>&1 && break
  sleep 5
done
curl -s --max-time 5 http://127.0.0.1:6333/readyz >/dev/null \
  || { journalctl -u aegis-vectordb -n 30 --no-pager; die "qdrant did not come up"; }
echo "  qdrant answering on 127.0.0.1:6333"

# ---------------------------------------------------------------------------
# 2. Ingestion: pure-python extraction libraries (no grpc, no compiled
#    surprises on aarch64 — the Qdrant upsert in ingest.py speaks plain REST).
# ---------------------------------------------------------------------------
echo
echo "== ingestion =="
/aegis/venv/bin/pip install --upgrade pypdf python-docx openpyxl >/dev/null
echo "  pypdf / python-docx / openpyxl installed"

install_tree "$SRC/src/aegis_core" /aegis/src/aegis_core
chown -R root:root /aegis/src/aegis_core
# Manifests are policy, not secrets — world-readable so agent uids can load
# their own manifest (the old 0750 root:aegis-core layout meant they never
# could have).
install -d -m 0755 /aegis/config/agents
install -m 0644 "$SRC"/config/agents/*.toml /aegis/config/agents/

# Zone drop-ins are GENERATED from the manifests, so the manifest and the
# unit cannot drift apart (that drift is what registry.check_unit_matches
# exists to catch — and what the old static drop-in guaranteed).
/aegis/venv/bin/python /aegis/bin/aegis-zone-sync
systemctl daemon-reload

# Validate every manifest AND its installed drop-in. A high-trust agent
# declaring network access is refused here, not warned about.
PYTHONPATH=/aegis/src /aegis/venv/bin/python /aegis/src/aegis_core/registry.py || {
  echo "manifest/unit validation failed — fix before continuing"; exit 1; }

# ---------------------------------------------------------------------------
# 3. Smoke test: one throwaway document through extract -> embed -> upsert,
#    into a scratch collection that is deleted afterwards.
# ---------------------------------------------------------------------------
echo
echo "== ingest smoke test =="
if curl -s --max-time 5 http://127.0.0.1:8001/health >/dev/null 2>&1; then
  T=$(mktemp -d /aegis/scratch/ht/smoke.XXXXXX)
  chown aegis-ht:aegis-ht "$T"
  printf 'AEGIS ingest smoke test.\n\nThis paragraph verifies the embed and upsert path.\n' > "$T/smoke.txt"
  chown aegis-ht:aegis-ht "$T/smoke.txt"
  if sudo -u aegis-ht PYTHONPATH=/aegis/src AEGIS_INGEST_COLLECTION=aegis_smoke \
       /aegis/venv/bin/python -m aegis_core.ingest --path "$T/smoke.txt"; then
    echo "  smoke ingest ok"
  else
    warn "smoke ingest failed — investigate before ingesting real documents"
  fi
  curl -s -X DELETE --max-time 5 http://127.0.0.1:6333/collections/aegis_smoke >/dev/null || true
  rm -rf "$T"
else
  warn "embedding service (127.0.0.1:8001) not up — run phase 06 first; skipping smoke test"
fi

cat <<'NOTE'

GDPR gate — decision D3 (docs/part-12-memory.md), before ANY client material:
  - lawful basis for holding it in an indexed store
  - retention period (this must equal your backup expiry — decision D4)
  - an erasure procedure you have actually run (aegis-forget + collection
    delete + backup age-out)
"I built a searchable index of client correspondence" is a sentence that
should not first be said during an audit.
NOTE
read -rp "Type UNDERSTOOD to acknowledge the D3 gate: " ack
[[ "$ack" == "UNDERSTOOD" ]] || die "acknowledge the GDPR gate to finish this phase"
install -d -m 0755 /var/lib/aegis
date -Is > /var/lib/aegis/d3-acknowledged

echo
echo "Then, to ingest for real:"
echo "  sudo -u aegis-ht PYTHONPATH=/aegis/src /aegis/venv/bin/python \\"
echo "       -m aegis_core.ingest --path /aegis/knowledge/inbox/<file>"
