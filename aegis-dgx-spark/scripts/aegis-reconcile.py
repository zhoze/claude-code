#!/usr/bin/env python3
"""Reconcile Core's decision log against the Gate's audit log.

The single most valuable routine check in the system — once Core exists.

Core records every request it made, including whether it attempted egress.
The Gate records every connection it saw. If the Gate saw a GATED connection
that Core has no record of, something reached a cloud endpoint outside the
orchestration path — which is either a bug or the thing you built all of
this to detect.

Honesty notes (this replaces an earlier version that cried wolf):
  * `auto`-class traffic (weight downloads, mirrors) is EXPECTED to happen
    outside Core — it is compared against nothing.
  * While Core is not deployed (no decisions.jsonl), the cross-count check
    is SKIPPED and says so, instead of alerting on every allowed connection.
  * The aegis-ht check and the missing-reason check run regardless — those
    need no Core to be meaningful.

Run weekly by aegis-reconcile.timer, and after any incident.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

CORE = Path("/aegis/logs/core/decisions.jsonl")
GATE = Path("/aegis/logs/egress/audit.jsonl")


def read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    core = read(CORE)
    gate = read(GATE)

    core_egress = sum(1 for r in core if r.get("egress", {}).get("attempted"))
    gate_allowed = [r for r in gate if r.get("event") == "allow"]
    gate_gated = [r for r in gate_allowed if r.get("klass") == "gated"]
    gate_denied = [r for r in gate if r.get("event") == "deny"]
    gate_errors = [r for r in gate if r.get("event") == "error"]

    print(f"core requests               : {len(core)}")
    print(f"core egress attempts        : {core_egress}")
    print(f"gate connections allowed    : {len(gate_allowed)} "
          f"({len(gate_gated)} gated)")
    print(f"gate connections denied     : {len(gate_denied)}")
    print(f"gate errors (incl. burn/audit): {len(gate_errors)}")

    problems = 0

    if not CORE.exists():
        print("\ncore decision log absent — Core is not deployed yet "
              "(phase 5); gated-vs-core cross-check skipped.")
    elif len(gate_gated) > core_egress:
        gap = len(gate_gated) - core_egress
        print(f"\n!! {gap} GATED connection(s) with no matching Core request.")
        print("   Something reached a cloud endpoint outside the orchestration path.")
        print("   Check which uid:")
        for user, n in Counter(r.get("user") for r in gate_gated).most_common():
            print(f"     {user}: {n}")
        problems += 1

    ht = [r for r in gate if r.get("user") == "aegis-ht"]
    if ht:
        print(f"\n!! {len(ht)} gate contact(s) from aegis-ht. A high-trust agent")
        print("   should not be able to reach the Gate at all — nftables should")
        print("   have dropped it. Run verify-egress.sh immediately.")
        problems += 1

    no_reason = [r for r in gate_gated if not r.get("approval_reason")]
    if no_reason:
        print(f"\n!! {len(no_reason)} gated connection(s) with no recorded reason.")
        problems += 1

    burn_denied = [r for r in gate_errors
                   if r.get("reason") == "token_unlink_denied"]
    if burn_denied:
        print(f"\n!! {len(burn_denied)} token-burn failure(s) — the approvals "
              "directory permissions are wrong (see bootstrap/01).")
        problems += 1

    if gate_denied:
        print("\ndenials by reason (expected — this is the boundary working):")
        for reason, n in Counter(r.get("reason") for r in gate_denied).most_common():
            print(f"  {reason}: {n}")

    print(f"\n{'RECONCILED' if not problems else f'{problems} PROBLEM(S)'}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
