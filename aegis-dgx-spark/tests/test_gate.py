#!/usr/bin/env python3
"""Unit tests for the Egress Gate's decision logic.

These run without root, without the network, and without the box. They assert
the properties the architecture rests on, so a refactor that quietly breaks
the boundary fails here rather than in production.

    python3 tests/test_gate.py
"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src" / "egress_gate"))
import gate  # noqa: E402

PASS = FAIL = 0


def check(label: str, cond: bool) -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS {label}")
    else:
        FAIL += 1
        print(f"  FAIL {label}")


def write_cfg(body: str) -> Path:
    path = Path(tempfile.mkdtemp()) / "egress.toml"
    path.write_text(body)
    return path


print("Approval queue")
d = Path(tempfile.mkdtemp())
q = gate.ApprovalQueue(d)

check("empty queue denies", q.consume("api.anthropic.com") is None)

(d / "a.json").write_text(json.dumps(
    {"id": "a", "host": "api.anthropic.com", "reason": "r",
     "expires_at": time.time() + 60}))
check("valid token is consumed", (q.consume("api.anthropic.com") or {}).get("id") == "a")
check("token is single-use", q.consume("api.anthropic.com") is None)

(d / "b.json").write_text(json.dumps(
    {"id": "b", "host": "api.anthropic.com", "expires_at": time.time() - 1}))
check("expired token denied", q.consume("api.anthropic.com") is None)
check("expired token reaped", not (d / "b.json").exists())

(d / "c.json").write_text(json.dumps(
    {"id": "c", "host": "evil.com", "expires_at": time.time() + 60}))
check("token for another host does not apply", q.consume("api.anthropic.com") is None)

(d / "d.json").write_text("{not json")
check("corrupt token ignored, not crashed", q.consume("api.anthropic.com") is None)

print("\nAllowlist matching")
cfg = gate.GateConfig.load(write_cfg("""
[gate]
listen_port = 3128
[[route]]
host = "api.anthropic.com"
class = "gated"
[[route]]
host = "archive.ubuntu.com"
class = "auto"
"""))

check("exact host matches", cfg.lookup("api.anthropic.com") is not None)
check("match is case-insensitive", cfg.lookup("API.ANTHROPIC.COM") is not None)
for bad in ("evil-api.anthropic.com", "api.anthropic.com.evil.net",
            "anthropic.com", "xapi.anthropic.com", ""):
    check(f"no leak via {bad!r}", cfg.lookup(bad) is None)
check("unlisted host denied", cfg.lookup("google.com") is None)
check("class survives load", cfg.lookup("api.anthropic.com").klass == "gated")
check("auto class survives load", cfg.lookup("archive.ubuntu.com").klass == "auto")

print("\nConfig rejection (fail closed on bad input)")
for label, body in [
    ("bad class", '[[route]]\nhost="a.com"\nclass="allow"\n'),
    ("wildcard host", '[[route]]\nhost="*.anthropic.com"\nclass="auto"\n'),
    ("bare ip", '[[route]]\nhost="1.2.3.4"\nclass="auto"\n'),
]:
    try:
        gate.GateConfig.load(write_cfg(body))
        check(f"rejects {label}", False)
    except (ValueError, KeyError):
        check(f"rejects {label}", True)

print("\nBurn failure fails closed, loudly")
import os  # noqa: E402
if os.geteuid() == 0:
    print("  skip (running as root — directory permissions do not bind)")
else:
    d2 = Path(tempfile.mkdtemp())
    q2 = gate.ApprovalQueue(d2)
    (d2 / "t.json").write_text(json.dumps(
        {"id": "t", "host": "api.anthropic.com",
         "expires_at": time.time() + 60}))
    d2.chmod(0o500)  # readable, not writable: unlink -> PermissionError
    try:
        result = q2.consume("api.anthropic.com")
        check("unburnable token is NOT served (old code denied silently, "
              "new code denies loudly)", result is None)
        check("burn_denied flag set for the audit trail", q2.burn_denied)
    finally:
        d2.chmod(0o700)
    check("token still present (nothing consumed it)", (d2 / "t.json").exists())
    check("burn_denied resets on next consume",
          q2.consume("api.anthropic.com") is not None or not q2.burn_denied)

print("\nAudit fails closed")
# /proc rejects mkdir even for root, so this is unwritable regardless of
# who runs the suite.
a = gate.Audit(Path("/proc/aegis-no-such-dir/audit.jsonl"))
import asyncio  # noqa: E402
check("unwritable audit returns False",
      asyncio.run(a.write(event="test")) is False)

good = gate.Audit(Path(tempfile.mkdtemp()) / "audit.jsonl")
check("writable audit returns True", asyncio.run(good.write(event="test")) is True)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
