#!/usr/bin/env bash
# AEGIS phase 06 — model runtime: llama.cpp on GB10.
#
# Why llama.cpp and not vLLM: there are no vLLM/PyTorch PyPI wheels for a
# Grace-Blackwell (sm_121) aarch64 box — `pip install vllm` either fails or
# quietly gives you CPU inference. llama.cpp builds from source in minutes,
# serves an OpenAI-compatible API on 127.0.0.1:8000, and the SAME binary
# serves the embedding model on :8001 that phase 07's ingestion needs.
#
# Run AFTER phase 05: weight downloads go through the Gate on `auto` routes,
# which is the first real test that the gate passes traffic you want.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root
load_site "$SRC"

GATE_PROXY=http://127.0.0.1:3128
BUILD_DIR=/aegis/build/llama.cpp
MODELS_TOML=/aegis/config/models.toml
LLM_ENV=/aegis/config/llm.env

verify() {
  local rc=0
  systemctl is-active --quiet aegis-llm || { echo "aegis-llm not active"; rc=1; }
  systemctl is-active --quiet aegis-embed || { echo "aegis-embed not active"; rc=1; }
  curl -s --max-time 20 http://127.0.0.1:8000/v1/models | grep -q aegis-general \
    || { echo "llm endpoint not serving aegis-general"; rc=1; }
  curl -s --max-time 30 http://127.0.0.1:8001/v1/embeddings \
       -H 'Content-Type: application/json' \
       -d '{"model":"aegis-embed","input":"verification probe"}' \
    | grep -q '"embedding"' || { echo "embedding endpoint not answering"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

systemctl is-active --quiet aegis-egress-gate || \
  die "the egress gate is not running. Run phase 05 first."

# ---------------------------------------------------------------------------
# 1. Build llama.cpp. Pinned: whatever release tag we build is recorded in
#    /etc/default/aegis so the build is reproducible and answerable.
# ---------------------------------------------------------------------------
echo "== llama.cpp build =="
install -d -m 0755 /aegis/build
if [[ -d "$BUILD_DIR/.git" ]]; then
  git -C "$BUILD_DIR" fetch --tags --quiet
else
  git clone --quiet https://github.com/ggml-org/llama.cpp "$BUILD_DIR"
fi
REF="${AEGIS_LLAMACPP_REF:-$(git -C "$BUILD_DIR" tag --sort=-creatordate | head -1)}"
[[ -n "$REF" ]] || die "could not determine a llama.cpp release tag"
git -C "$BUILD_DIR" checkout --quiet "$REF"
echo "  building $REF ($(git -C "$BUILD_DIR" rev-parse --short HEAD))"

build_llama() {  # build_llama <cuda:ON|OFF>
  cmake -S "$BUILD_DIR" -B "$BUILD_DIR/build" \
        -DCMAKE_BUILD_TYPE=Release -DBUILD_SHARED_LIBS=OFF \
        -DGGML_CUDA="$1" -DGGML_NATIVE=ON >/dev/null \
  && cmake --build "$BUILD_DIR/build" --target llama-server -j"$(nproc)"
}

CUDA=OFF
if command -v nvcc >/dev/null; then
  echo "  attempting CUDA build (this takes a while on first run)"
  if build_llama ON; then CUDA=ON; else
    warn "CUDA build failed — falling back to CPU build. Inference will be slow; investigate (nvcc vs driver vs sm_121 support) and re-run this phase."
    rm -rf "$BUILD_DIR/build"
    build_llama OFF
  fi
else
  warn "no nvcc — CPU build only. Install the CUDA toolkit and re-run for GPU inference."
  build_llama OFF
fi
install -m 0755 "$BUILD_DIR/build/bin/llama-server" /aegis/bin/llama-server
sed -i '/^AEGIS_LLAMACPP_BUILT=/d' "$AEGIS_DEFAULTS" 2>/dev/null || true
echo "AEGIS_LLAMACPP_BUILT=${REF}+cuda=${CUDA}" >> "$AEGIS_DEFAULTS"
echo "  installed /aegis/bin/llama-server (${REF}, CUDA=${CUDA})"

# ---------------------------------------------------------------------------
# 2. Choose and download models — THROUGH the gate, as aegis-llm.
#    Defaults sized for 128 GiB unified memory at ~273 GB/s: a 32B-class
#    model answers interactively; the 70B option fits but generates at
#    low single-digit tok/s. Verify current best options — model tables age
#    fast (docs/part-08).
# ---------------------------------------------------------------------------
echo
echo "== models =="
install -d -m 0755 -o aegis-llm -g aegis-llm /aegis/models
[[ -f "$MODELS_TOML" ]] || install -m 0644 "$SRC/config/models.toml" "$MODELS_TOML"

download_model() {  # download_model <role> <repo> <file>  -> sets DL_PATH DL_SHA
  local role="$1" repo="$2" file="$3"
  local url="https://huggingface.co/${repo}/resolve/main/${file}"
  DL_PATH="/aegis/models/${file}"
  if [[ -f "$DL_PATH" ]]; then
    echo "  $file already present — keeping it (delete to re-download)"
  else
    echo "  downloading $url"
    echo "  (through the gate; if this 403s, read the audit log:"
    echo "   jq -c 'select(.event==\"deny\")' /aegis/logs/egress/audit.jsonl | tail -3"
    echo "   and add the denied CDN host to config/egress.toml with a note)"
    sudo -u aegis-llm HTTPS_PROXY="$GATE_PROXY" HTTP_PROXY="$GATE_PROXY" \
      curl -fL --retry 3 -C - --progress-bar "$url" -o "$DL_PATH" \
      || die "download failed for $url"
  fi
  echo "  hashing (large file — a minute or two)"
  DL_SHA=$(sha256sum "$DL_PATH" | awk '{print $1}')
  echo "  sha256 $DL_SHA"
  echo "  cross-check this against the value shown on the repo page:"
  echo "    https://huggingface.co/${repo}/blob/main/${file}"
  confirm "  Does the sha256 match what the publisher lists?" \
    || die "checksum not confirmed — refusing to serve an unverified model"
  cat >> "$MODELS_TOML" <<EOF

[[model]]
name       = "$([[ "$role" == general ]] && echo aegis-general || echo aegis-embed)"
role       = "${role}"
repo       = "${repo}"
file       = "${file}"
path       = "${DL_PATH}"
sha256     = "${DL_SHA}"
source_url = "${url}"
downloaded = "$(date -Is)"
license    = "see repo page — record it here"
EOF
}

echo "General reasoning model:"
echo "  1) Qwen3-32B Q5_K_M          (~23 GB, interactive speed)   [default]"
echo "  2) Llama-3.3-70B Q4_K_M      (~42 GB, slow but stronger)"
echo "  3) custom Hugging Face repo/file"
read -rp "choice [1]: " choice
case "${choice:-1}" in
  2) G_REPO="bartowski/Llama-3.3-70B-Instruct-GGUF"; G_FILE="Llama-3.3-70B-Instruct-Q4_K_M.gguf" ;;
  3) read -rp "  repo (owner/name): " G_REPO; read -rp "  filename (.gguf): " G_FILE ;;
  *) G_REPO="Qwen/Qwen3-32B-GGUF"; G_FILE="Qwen3-32B-Q5_K_M.gguf" ;;
esac
download_model general "$G_REPO" "$G_FILE"
G_PATH="$DL_PATH"

echo
echo "Embedding model (phase 07 ingestion + retrieval; bge-m3 is multilingual"
echo "and handles Estonian — decision D2 wants this measured on your own docs):"
echo "  1) bge-m3 Q8_0               (~700 MB, multilingual)       [default]"
echo "  2) nomic-embed-text-v1.5 Q8  (~150 MB, English-leaning)"
echo "  3) custom Hugging Face repo/file"
read -rp "choice [1]: " choice
case "${choice:-1}" in
  2) E_REPO="nomic-ai/nomic-embed-text-v1.5-GGUF"; E_FILE="nomic-embed-text-v1.5.Q8_0.gguf" ;;
  3) read -rp "  repo (owner/name): " E_REPO; read -rp "  filename (.gguf): " E_FILE ;;
  *) E_REPO="gpustack/bge-m3-GGUF"; E_FILE="bge-m3-Q8_0.gguf" ;;
esac
download_model embedding "$E_REPO" "$E_FILE"
E_PATH="$DL_PATH"

# ---------------------------------------------------------------------------
# 3. Runtime configuration + units.
# ---------------------------------------------------------------------------
echo
echo "== configuration =="
MEMMAX="${AEGIS_SITE_LLM_MEMORYMAX:-100G}"
cat > "$LLM_ENV" <<EOF
# Model runtime configuration. Sourced by aegis-llm.service and
# aegis-embed.service. Regenerated by bootstrap/06-models.sh.
#
# Sizing note: the Spark's memory is UNIFIED — model weights are mmap'd
# file-backed pages (reclaimable page cache), while the KV cache is anonymous
# memory that grows with context. MemoryMax in the unit ($MEMMAX here) is a
# backstop against runaway KV growth, not a working-set target.

AEGIS_MODEL_NAME=aegis-general
AEGIS_MODEL_PATH=$G_PATH
AEGIS_CTX_SIZE=32768
AEGIS_GPU_LAYERS=999
AEGIS_PARALLEL=2

AEGIS_EMBED_MODEL_NAME=aegis-embed
AEGIS_EMBED_MODEL_PATH=$E_PATH
AEGIS_EMBED_CTX=8192
EOF
chown root:aegis-llm "$LLM_ENV"; chmod 0640 "$LLM_ENV"
install -m 0640 -o root -g aegis-core "$SRC/config/routing.toml" /aegis/config/routing.toml

for unit in aegis-llm aegis-embed; do
  sed "s/@@MEMORYMAX@@/${MEMMAX}/" "$SRC/systemd/$unit.service" \
    > "/etc/systemd/system/$unit.service"
done
systemctl daemon-reload
systemctl enable --now aegis-llm aegis-embed

echo "  waiting for the model to load (large model: up to a few minutes)"
for i in $(seq 1 60); do
  curl -s --max-time 3 http://127.0.0.1:8000/health >/dev/null 2>&1 && break
  sleep 5
done
curl -s --max-time 20 http://127.0.0.1:8000/v1/models | grep -q aegis-general \
  || { journalctl -u aegis-llm -n 30 --no-pager; die "aegis-llm did not come up — see log above"; }
echo "  llm answering on 127.0.0.1:8000"

cat <<'NOTE'

Done. Still on you, deliberately:

  1. Record each model's LICENSE in /aegis/config/models.toml (the field is
     there with a placeholder). "Which model produced this answer" is a
     question you will need to answer; retired models keep their entries.
  2. Load-test before you rely on it: run 30 minutes of concurrent requests
     and watch `systemctl status aegis-llm` MemoryCurrent, `vmstat 5` swap,
     and GPU temperature. The done-criterion in part-17 is surviving a
     reboot AND sustained load inside the memory budget.
  3. Decision D2 (embedding quality on Estonian CF&S text) wants a measured
     answer before phase 07 ingests anything that matters.
NOTE
