#!/usr/bin/env python3
"""Unit tests for agent manifest validation.

The assertion that matters: a high-trust agent declaring network access is
REJECTED, not warned about. If this test ever starts failing, the
exfiltration path in docs/part-00-threat-model.md T2 is open again.
"""
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from aegis_core import registry  # noqa: E402

PASS = FAIL = 0


def check(label, cond):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS {label}")
    else:
        FAIL += 1; print(f"  FAIL {label}")


def manifest(body: str) -> Path:
    p = Path(tempfile.mkdtemp()) / "a.toml"
    p.write_text(body)
    return p


def rejects(body: str) -> bool:
    try:
        registry.load(manifest(body))
        return False
    except registry.ManifestError:
        return True


GOOD_HIGH = '''
[agent]
name = "documents"
trust_zone = "high"
[permissions]
read = ["/aegis/knowledge"]
network = false
'''

GOOD_LOW = '''
[agent]
name = "research"
trust_zone = "low"
[permissions]
read = ["/aegis/scratch"]
network = true
'''

print("Valid manifests load")
check("high-trust, no network", registry.load(manifest(GOOD_HIGH)).trust_zone == "high")
check("low-trust with network", registry.load(manifest(GOOD_LOW)).network is True)
check("high-trust runs as aegis-ht", registry.load(manifest(GOOD_HIGH)).run_as == "aegis-ht")
check("low-trust runs as aegis-lt", registry.load(manifest(GOOD_LOW)).run_as == "aegis-lt")
check("high-trust may never reach the gate",
      registry.load(manifest(GOOD_HIGH)).may_reach_gate is False)

print("\nContradictory manifests are REJECTED")
check("high trust + network=true", rejects('''
[agent]
name = "bad"
trust_zone = "high"
[permissions]
read = ["/aegis/knowledge"]
network = true
'''))

check("low trust reading /aegis/knowledge", rejects('''
[agent]
name = "bad"
trust_zone = "low"
[permissions]
read = ["/aegis/knowledge"]
network = true
'''))

check("invented middle zone", rejects('''
[agent]
name = "bad"
trust_zone = "medium"
[permissions]
read = ["/aegis/scratch"]
'''))

check("path outside the zone's roots", rejects('''
[agent]
name = "bad"
trust_zone = "low"
[permissions]
read = ["/etc"]
'''))

check("absurd memory limit", rejects('''
[agent]
name = "bad"
trust_zone = "low"
[permissions]
read = ["/aegis/scratch"]
[limits]
memory_gib = 512
'''))

print("\nDefaults fail closed")
m = registry.load(manifest('[agent]\nname="x"\n'))
check("undeclared zone defaults to low", m.trust_zone == "low")
check("undeclared network defaults to false", m.network is False)

print(f"\n{PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL else 0)
