#!/usr/bin/env bash
# One-shot environment setup for NVIDIA DGX Spark (GB10 Grace-Blackwell, aarch64).
# Creates a conda env "quant" with RAPIDS (cudf/cuml) + cuOpt for CUDA 13, then
# installs the CPU-side Python dependencies.
#
# The DGX Spark ships with the NVIDIA driver + CUDA toolkit preinstalled via
# DGX OS. Verify with `nvidia-smi` before running this script.
set -euo pipefail

ENV_NAME="${ENV_NAME:-quant}"
PYTHON_VERSION="${PYTHON_VERSION:-3.12}"
RAPIDS_VERSION="${RAPIDS_VERSION:-25.06}"
CUDA_VERSION="${CUDA_VERSION:-13.0}"

echo "== DGX Quant Screener setup (aarch64 / CUDA ${CUDA_VERSION}) =="

if ! command -v nvidia-smi >/dev/null; then
  echo "WARNING: nvidia-smi not found — continuing with CPU-only fallback install." >&2
fi

# ---- miniforge (conda-forge native, aarch64) if no conda present
if ! command -v conda >/dev/null; then
  echo "Installing Miniforge..."
  curl -fsSL -o /tmp/miniforge.sh \
    "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-Linux-aarch64.sh"
  bash /tmp/miniforge.sh -b -p "${HOME}/miniforge3"
  source "${HOME}/miniforge3/etc/profile.d/conda.sh"
  conda init bash
else
  source "$(conda info --base)/etc/profile.d/conda.sh"
fi

if conda env list | grep -qE "^${ENV_NAME}[[:space:]]"; then
  echo "conda env '${ENV_NAME}' already exists — updating packages in place."
else
  echo "Creating conda env '${ENV_NAME}' with RAPIDS ${RAPIDS_VERSION}..."
  conda create -y -n "${ENV_NAME}" \
    -c rapidsai -c conda-forge -c nvidia \
    "python=${PYTHON_VERSION}" \
    "rapids=${RAPIDS_VERSION}" \
    "cuda-version=${CUDA_VERSION}" || {
      echo "RAPIDS install failed (no GPU / unsupported CUDA?). Creating CPU-only env." >&2
      conda create -y -n "${ENV_NAME}" -c conda-forge "python=${PYTHON_VERSION}"
    }
fi

conda activate "${ENV_NAME}"

# ---- cuOpt (pip wheels; falls back silently, SciPy HiGHS is used instead)
pip install --extra-index-url=https://pypi.nvidia.com \
  "cuopt-cu13" 2>/dev/null \
  || pip install --extra-index-url=https://pypi.nvidia.com "cuopt-cu12" 2>/dev/null \
  || echo "cuOpt wheels unavailable for this platform — SciPy HiGHS fallback will be used."

# ---- project dependencies
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
pip install -r "${SCRIPT_DIR}/../requirements.txt"

echo
echo "== Verification =="
python - <<'PY'
from quant_screener import gpu  # noqa: E402  (works if run from project root)
print("Backend:", gpu.backend_summary())
PY
echo
echo "Setup complete. Next steps:"
echo "  conda activate ${ENV_NAME}"
echo "  cd $(dirname "${SCRIPT_DIR}")"
echo "  python run_daily.py --dry-run --max-universe 50"
echo "  bash scripts/install_systemd.sh   # schedule the daily pre-market run"
