#!/usr/bin/env python3
"""Delete a subject everywhere it is reachable. See docs/part-12-memory.md.

Reaches: source documents and their sidecars (by filename match), state
files that mention the subject, and the vector db points belonging to the
deleted documents (by sha256 payload filter, over loopback REST).

What it CANNOT reach: backups. A deletion is not complete until the last
backup containing it ages out. That retention period (decision D4) is your
honest answer to an erasure request — do not tell anyone deletion is
instant.

    sudo aegis-forget --subject "blade-yard" --dry-run
    sudo aegis-forget --subject "blade-yard" --confirm
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

KNOWLEDGE = Path("/aegis/knowledge")
STATE = Path("/aegis/state")
LOGS = Path("/aegis/logs")
QDRANT_URL = os.environ.get("AEGIS_QDRANT_URL", "http://127.0.0.1:6333")
COLLECTION = os.environ.get("AEGIS_INGEST_COLLECTION", "aegis_knowledge")

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def sane_subject(value: str) -> str:
    """The subject names files and a manifest — it must not be a path."""
    if len(value) < 3:
        raise argparse.ArgumentTypeError("subject too short (min 3 chars)")
    if "/" in value or "\\" in value or ".." in value or value.startswith("."):
        raise argparse.ArgumentTypeError(
            "subject must be a plain term, not a path")
    return value


def find_targets(subject: str) -> dict[str, list[Path]]:
    hits: dict[str, list[Path]] = {"documents": [], "state": []}
    needle = subject.lower()
    if KNOWLEDGE.is_dir():
        for p in KNOWLEDGE.rglob("*"):
            if not p.is_file() or needle not in p.name.lower():
                continue
            # Refuse anything that resolves outside the tree (symlink games).
            if not p.resolve().is_relative_to(KNOWLEDGE.resolve()):
                print(f"skipping {p}: resolves outside /aegis/knowledge",
                      file=sys.stderr)
                continue
            hits["documents"].append(p)
    if STATE.is_dir():
        hits["state"] = [p for p in STATE.rglob("*.json")
                         if needle in p.read_text(errors="ignore").lower()]
    return hits


def qdrant_delete_by_sha(shas: list[str]) -> str:
    """Delete every point whose payload sha256 matches a removed document."""
    if not shas:
        return "no document hashes to delete"
    url = f"{QDRANT_URL}/collections/{COLLECTION}/points/delete?wait=true"
    body = json.dumps({"filter": {"must": [
        {"key": "sha256", "match": {"any": shas}}
    ]}}).encode()
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"})
    try:
        with _OPENER.open(req, timeout=30) as resp:
            resp.read()
        return f"deleted points for {len(shas)} document hash(es)"
    except (urllib.error.URLError, TimeoutError) as exc:
        return (f"vector db unreachable ({exc}) — run again when "
                "aegis-vectordb is up, or the embeddings LINGER")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def main() -> int:
    if os.geteuid() != 0:
        print("run with sudo: forgetting spans files owned by several uids",
              file=sys.stderr)
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True, type=sane_subject)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--confirm", action="store_true")
    args = ap.parse_args()

    hits = find_targets(args.subject)
    total = sum(len(v) for v in hits.values())

    for kind, paths in hits.items():
        print(f"{kind}: {len(paths)}")
        for p in paths:
            print(f"  {p}")

    print("\nNOTE: matching is by fileNAME (and state-file content). A "
          "subject that only appears INSIDE a document is not found this "
          "way — search /aegis/knowledge yourself before calling an erasure "
          "complete.")

    if args.dry_run:
        print(f"\ndry run — nothing removed. {total} file(s) would be "
              "deleted, plus their vector db points.")
        return 0

    # Hash the real documents BEFORE unlinking so the vector db delete can
    # target exactly their points.
    doc_shas = [sha256(p) for p in hits["documents"]
                if p.suffix.lower() not in (".json", ".reason")]

    manifest = {"subject": args.subject, "removed": [], "vectordb": ""}
    for paths in hits.values():
        for p in paths:
            try:
                p.unlink()
                manifest["removed"].append(str(p))
            except OSError as exc:
                print(f"could not remove {p}: {exc}", file=sys.stderr)

    manifest["vectordb"] = qdrant_delete_by_sha(doc_shas)
    print(f"\nvector db: {manifest['vectordb']}")

    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", args.subject)
    out = LOGS / "deletions" / f"{safe_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2))

    print(f"\nremoved {len(manifest['removed'])} file(s); manifest at {out}")
    print("\nSTILL PRESENT IN BACKUPS. Deletion completes when the last backup")
    print("containing this subject ages out. See docs/part-06-storage.md for")
    print("the retention period you committed to (D4).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
