"""AEGIS Core service — minimal, truthful stub.

The real Core (task loop, decision log, agent orchestration — docs/part-10)
is the PHASE 5 build. This module exists so that aegis-core.service points
at code that runs, answers /healthz, heartbeats to the core log, and exits
cleanly — instead of crash-looping on a module that never existed.

What it deliberately does NOT do: route requests, talk to agents, or write
`decisions.jsonl` entries claiming work happened. aegis-reconcile treats an
absent decision log as "core not deployed", which is the honest state.

Run:  systemctl start aegis-core        (installed by phase 09, not enabled)
"""
from __future__ import annotations

import json
import logging
import signal
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler
from pathlib import Path

LOG_DIR = Path("/aegis/logs/core")
HEARTBEAT_SECONDS = 60

log = logging.getLogger("aegis.core")


class Health(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path != "/healthz":
            self.send_error(404)
            return
        body = json.dumps({
            "service": "aegis-core",
            "status": "stub",
            "note": "task loop is the phase 5 build (docs/part-10, part-17)",
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):  # health checks do not belong in the journal
        pass


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())

    server = socketserver.TCPServer(("127.0.0.1", 8080), Health,
                                    bind_and_activate=False)
    server.allow_reuse_address = True
    server.server_bind()
    server.server_activate()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info("aegis-core STUB up on 127.0.0.1:8080/healthz — the task loop "
             "is not built yet (phase 5)")

    hb = LOG_DIR / "heartbeat.log"
    while not stop.is_set():
        try:
            with hb.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"ts": time.time(), "status": "stub-alive"})
                         + "\n")
        except OSError as exc:
            log.error("cannot write heartbeat to %s: %s", hb, exc)
        stop.wait(HEARTBEAT_SECONDS)

    log.info("aegis-core stub stopping")
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
