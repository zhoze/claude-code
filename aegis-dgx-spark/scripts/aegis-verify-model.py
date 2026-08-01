#!/usr/bin/env python3
"""aegis-verify-model — refuse to serve a model that is not what the
registry says it is.

Runs as ExecStartPre= of aegis-llm.service and aegis-embed.service:

    aegis-verify-model /aegis/models/<file>.gguf

Looks the path up in /aegis/config/models.toml and compares sha256. Exit 0
only on a match. A model file that changed without a registry entry is
either an operator who skipped the bookkeeping or something worse — both
are worth a refused start.

Hashing a 20-40 GB file costs a few seconds on the Spark's NVMe at service
start. AEGIS_SKIP_MODEL_VERIFY=1 (in llm.env) skips the hash — debugging
only, and it still requires the registry entry to exist.
"""
from __future__ import annotations

import hashlib
import os
import sys
import tomllib
from pathlib import Path

MODELS_TOML = Path(os.environ.get("AEGIS_MODELS_TOML", "/aegis/config/models.toml"))


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: aegis-verify-model <model-path>", file=sys.stderr)
        return 2
    target = Path(sys.argv[1])

    if not target.is_file():
        print(f"aegis-verify-model: {target} does not exist", file=sys.stderr)
        return 1
    if not MODELS_TOML.is_file():
        print(f"aegis-verify-model: no registry at {MODELS_TOML} — record the "
              "model first (bootstrap/06-models.sh does this)", file=sys.stderr)
        return 1

    with MODELS_TOML.open("rb") as fh:
        registry = tomllib.load(fh)

    entry = next((m for m in registry.get("model", [])
                  if m.get("path") == str(target) and not m.get("retired")),
                 None)
    if entry is None:
        print(f"aegis-verify-model: {target} has no active entry in "
              f"{MODELS_TOML} — refusing to serve an unregistered model",
              file=sys.stderr)
        return 1

    if os.environ.get("AEGIS_SKIP_MODEL_VERIFY") == "1":
        print(f"aegis-verify-model: SKIPPING hash of {target.name} "
              "(AEGIS_SKIP_MODEL_VERIFY=1 — debugging only)")
        return 0

    actual = sha256(target)
    expected = entry.get("sha256", "")
    if actual != expected:
        print(f"aegis-verify-model: sha256 MISMATCH for {target}\n"
              f"  registry: {expected}\n"
              f"  on disk : {actual}\n"
              "The file changed since it was recorded. Re-download or "
              "re-record deliberately; do not serve it by accident.",
              file=sys.stderr)
        return 1

    print(f"aegis-verify-model: {target.name} verified ({actual[:16]}…)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
