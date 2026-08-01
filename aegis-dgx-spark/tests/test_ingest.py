#!/usr/bin/env python3
"""Unit tests for the ingestion pipeline: the chunker's contract, sidecar
filtering, and the embed->upsert path against a local mock of the embedding
service and Qdrant (no services, no network beyond a loopback mock).

    python3 tests/test_ingest.py
"""
import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

PASS = FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")


# ---------------------------------------------------------------------------
# Mock embedding + qdrant endpoints, then import ingest with env pointing at
# them.
# ---------------------------------------------------------------------------

RECORDED = {"upserts": [], "collections_created": []}
DIM = 8


class Mock(BaseHTTPRequestHandler):
    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802  (embeddings)
        payload = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0))))
        if self.path == "/v1/embeddings":
            data = [{"index": i, "embedding": [float(i)] * DIM}
                    for i in range(len(payload["input"]))]
            self._json(200, {"data": data})
        else:
            self._json(404, {})

    def do_GET(self):  # noqa: N802  (collection info)
        if RECORDED["collections_created"]:
            self._json(200, {"result": {"config": {"params": {
                "vectors": {"size": DIM}}}}})
        else:
            self._json(404, {"status": "not found"})

    def do_PUT(self):  # noqa: N802  (create collection / upsert)
        payload = json.loads(self.rfile.read(
            int(self.headers.get("Content-Length", 0))))
        if self.path.endswith("/points?wait=true") or "/points" in self.path:
            RECORDED["upserts"].append(payload)
        else:
            RECORDED["collections_created"].append(self.path)
        self._json(200, {"status": "ok"})

    def log_message(self, *_):
        pass


server = HTTPServer(("127.0.0.1", 0), Mock)
port = server.server_address[1]
threading.Thread(target=server.serve_forever, daemon=True).start()

os.environ["AEGIS_EMBED_URL"] = f"http://127.0.0.1:{port}/v1/embeddings"
os.environ["AEGIS_QDRANT_URL"] = f"http://127.0.0.1:{port}"
os.environ["AEGIS_INGEST_COLLECTION"] = "aegis_test"

from aegis_core import ingest  # noqa: E402


print("Chunker contract")
LIMIT = 800 * 4
chunks = ingest.chunk("para one.\n\npara two.")
check("small text -> one chunk", len(chunks) == 1)
check("no leading separator artefact", not chunks[0].startswith("\n"))

giant = "x" * 20_000  # single paragraph far beyond the window
chunks = ingest.chunk(giant)
check("giant paragraph is split (old code returned it whole)",
      len(chunks) > 1)
check("split chunks respect the window (+overlap tolerance)",
      all(len(c) <= LIMIT + 100 * 4 + 2 for c in chunks))
check("no content lost in the split",
      sum(len(c.replace("\n\n", "")) for c in chunks) >= len(giant))

many = "\n\n".join(f"paragraph {i} " + "y" * 500 for i in range(30))
chunks = ingest.chunk(many)
check("multi-paragraph text produces multiple chunks", len(chunks) > 1)
check("chunks stay within the window", all(len(c) <= LIMIT + 100 * 4 + 2
                                           for c in chunks))

print("\nSidecar filtering")
check(".meta.json is a sidecar", ingest.is_sidecar(Path("a.pdf.meta.json")))
check(".reason is a sidecar (old code re-ingested these)",
      ingest.is_sidecar(Path("a.pdf.reason")))
check("a document is not a sidecar", not ingest.is_sidecar(Path("a.pdf")))

print("\nEmbed -> upsert path (mocked services)")
tmp = Path(tempfile.mkdtemp())
doc = tmp / "note.txt"
doc.write_text("First paragraph of the test document.\n\nSecond paragraph.")
meta = ingest.ingest_one(doc)
check("ingest returns metadata", meta is not None)
check("collection was created", len(RECORDED["collections_created"]) == 1)
check("points were upserted", len(RECORDED["upserts"]) == 1)
points = RECORDED["upserts"][0]["points"]
check("one point per chunk", len(points) == meta.chunks)
check("payload carries classification fail-closed",
      all(p["payload"]["classification"] == "confidential" for p in points))
check("payload carries trust zone", all(p["payload"]["trust_zone"] == "high"
                                        for p in points))
check("point ids deterministic (idempotent re-ingest)",
      points[0]["id"] == ingest.uuid.uuid5(
          ingest.uuid.NAMESPACE_URL,
          f"aegis:{meta.sha256}:0").__str__())
check("sidecar written", doc.with_suffix(".txt.meta.json").exists())

print("\nInfrastructure failure is not a document failure")
saved_url = ingest.EMBED_URL
ingest.EMBED_URL = "http://127.0.0.1:1/nope"  # nothing listens there
doc2 = tmp / "note2.txt"
doc2.write_text("content that should survive an outage")
try:
    ingest.ingest_one(doc2)
    check("raises InfraError when embed service is down", False)
except ingest.InfraError:
    check("raises InfraError when embed service is down", True)
check("document NOT quarantined on infra failure", doc2.exists())
ingest.EMBED_URL = saved_url

print(f"\n{PASS} passed, {FAIL} failed")
server.shutdown()
sys.exit(1 if FAIL else 0)
