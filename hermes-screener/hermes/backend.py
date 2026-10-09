"""CPU / GPU / AUTO backend policy (the PERF4 design rules applied here).

- CPU is always available and always correct; GPU libraries are optional imports.
- GPU: explicit, for tests and benchmarks; it fails loudly if the GPU is unusable.
- AUTO: GPU only when the workload is above a configured crossover threshold AND a
  CUDA smoke test passes. CUDA merely being present is never enough.
- A GPU failure after partial work restarts the WHOLE calculation on CPU; partial
  GPU and CPU results are never mixed.
- No device state outlives one call, so one snapshot cannot leak into another.
"""
from __future__ import annotations

import functools
import logging

log = logging.getLogger(__name__)

MODES = ("cpu", "gpu", "auto")


@functools.lru_cache(maxsize=1)
def cupy_healthy() -> bool:
    try:
        import cupy as cp  # noqa: PLC0415
        if cp.cuda.runtime.getDeviceCount() < 1:
            return False
        a = cp.arange(1024, dtype=cp.float64)
        ok = float((a * 2.0 + 1.0).sum().get()) == float(1024 * 1023 + 1024)
        cp.cuda.Stream.null.synchronize()
        return ok
    except Exception:  # noqa: BLE001 — any import/driver error => unhealthy
        return False


@functools.lru_cache(maxsize=1)
def cuopt_available() -> bool:
    try:
        from cuopt.linear_programming import data_model, solver  # noqa: F401, PLC0415
        return cupy_healthy()
    except Exception:  # noqa: BLE001
        return False


def choose(mode: str, size: float, threshold: float, gpu_ok: bool) -> str:
    """Resolve a backend mode to 'cpu' or 'gpu' for a workload of `size`."""
    if mode not in MODES:
        raise ValueError(f"backend mode must be one of {MODES}, got {mode!r}")
    if mode == "cpu":
        return "cpu"
    if mode == "gpu":
        if not gpu_ok:
            raise RuntimeError("backend=gpu requested but no healthy GPU stack is available")
        return "gpu"
    return "gpu" if (gpu_ok and size >= threshold) else "cpu"


def array_module(backend: str):
    if backend == "gpu":
        import cupy as cp  # noqa: PLC0415
        return cp
    import numpy as np  # noqa: PLC0415
    return np


def to_host(x):
    """Device -> host copy at a real CPU-consumer boundary (no-op for NumPy)."""
    get = getattr(x, "get", None)
    return get() if callable(get) else x
