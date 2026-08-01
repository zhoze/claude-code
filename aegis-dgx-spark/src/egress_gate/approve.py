#!/usr/bin/env python3
"""
aegis-approve — mint a single-use egress approval token.

This runs as *you*. Not as AEGIS, not as any agent, not from a model tool
call. That separation is the entire mechanism: the thing that decides whether
your data leaves the building is a command you type, and AEGIS has no ability
to mint into the directory it lands in.

    aegis-approve api.anthropic.com --reason "summarise Q3 blade-yard report"
    aegis-approve api.anthropic.com --ttl 300 --reason "..." --count 3
    aegis-approve --list
    aegis-approve --revoke-all

Tokens expire (default 120s), are consumed one per outbound connection, and
are burned by the Gate before the upstream socket opens.

Permissions model (created by bootstrap/01-users-and-dirs.sh):
    /aegis/run/approvals   2770 root:aegis-operators
Operators (group members) mint; the Gate (aegis-proxy, supplementary member
of aegis-operators) reads and unlinks. The setgid bit makes every token land
group-owned by aegis-operators with no chown needed — which also means this
tool works for any operator, not only root.

Install this so that only your account can run it:
    /usr/local/bin/aegis-approve  root:aegis-operators  0750
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import secrets
import sys
import time
from pathlib import Path

APPROVAL_DIR = Path(os.environ.get("AEGIS_APPROVAL_DIR", "/aegis/run/approvals"))


def operator_name() -> str:
    """The uid actually running this process — not $USER, which survives
    sudo and su and would misattribute the audit trail."""
    try:
        return pwd.getpwuid(os.geteuid()).pw_name
    except KeyError:
        return str(os.geteuid())


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="aegis-approve",
        description="Release approval for one outbound connection through the AEGIS Egress Gate.",
    )
    ap.add_argument("host", nargs="?",
                    help="exact hostname, e.g. api.anthropic.com")
    ap.add_argument("--reason",
                    help="why. This goes in the audit log; future-you will want it.")
    ap.add_argument("--ttl", type=int, default=120,
                    help="seconds before the token expires (default 120)")
    ap.add_argument("--count", type=int, default=1,
                    help="how many single-use tokens to mint (default 1)")
    ap.add_argument("--list", action="store_true",
                    help="show outstanding tokens and exit")
    ap.add_argument("--revoke-all", action="store_true",
                    help="delete every outstanding token and exit")
    args = ap.parse_args()

    if not APPROVAL_DIR.is_dir():
        print(f"approvals directory {APPROVAL_DIR} does not exist — run "
              "bootstrap/01-users-and-dirs.sh", file=sys.stderr)
        return 1

    if args.list:
        return _list()
    if args.revoke_all:
        return _revoke_all()

    if not args.host or not args.reason:
        ap.error("minting needs a host and --reason "
                 "(or use --list / --revoke-all)")

    if args.count < 1 or args.count > 20:
        print("refusing: --count must be between 1 and 20", file=sys.stderr)
        return 2
    if args.ttl < 5 or args.ttl > 3600:
        print("refusing: --ttl must be between 5 and 3600 seconds", file=sys.stderr)
        return 2

    expires = time.time() + args.ttl
    minted = []
    for _ in range(args.count):
        token_id = secrets.token_hex(12)
        payload = {
            "id": token_id,
            "host": args.host.lower(),
            "reason": args.reason,
            "approved_by": operator_name(),
            "created_at": time.time(),
            "expires_at": expires,
        }
        path = APPROVAL_DIR / f"{token_id}.json"
        # 0600 while writing, then 0640: the setgid directory has already
        # given the file the aegis-operators group, which is exactly what the
        # Gate reads (and unlinks) through. No chown — this must work for a
        # non-root operator.
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(payload, fh)
        os.chmod(path, 0o640)
        minted.append(token_id)

    plural = "token" if args.count == 1 else "tokens"
    print(f"{args.count} {plural} for {args.host}, valid {args.ttl}s")
    print(f"reason: {args.reason}")
    for tid in minted:
        print(f"  {tid}")
    return 0


def _list() -> int:
    now = time.time()
    rows = []
    for path in sorted(APPROVAL_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        remaining = data.get("expires_at", 0) - now
        rows.append((data.get("host", "?"), int(remaining),
                     data.get("approved_by", "?"), data.get("reason", "")))
    if not rows:
        print("no outstanding approvals")
        return 0
    print(f"{'HOST':<32} {'TTL':>7}  {'BY':<12} REASON")
    for host, ttl, by, reason in rows:
        state = f"{ttl}s" if ttl > 0 else "expired"
        print(f"{host:<32} {state:>7}  {by:<12} {reason}")
    return 0


def _revoke_all() -> int:
    n = 0
    errors = 0
    for path in APPROVAL_DIR.glob("*.json"):
        try:
            path.unlink()
            n += 1
        except OSError as exc:
            errors += 1
            print(f"could not revoke {path.name}: {exc}", file=sys.stderr)
    print(f"revoked {n}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
