#!/usr/bin/env python3
"""Agent process entry point. One process per agent, started by
aegis-agent@<name>.service.

Its first job is to verify that the zone it is *running in* matches the zone
its manifest *declares*. A manifest saying "high" while systemd runs it as
aegis-lt is the failure that survives code review, because both files look
correct on their own.
"""
from __future__ import annotations

import argparse
import logging
import os
import pwd
import sys
from pathlib import Path

sys.path.insert(0, "/aegis/src")
from aegis_core import registry  # noqa: E402

log = logging.getLogger("aegis.agent")


def preflight(manifest) -> None:
    # The KERNEL's answer, not the environment's: getpass.getuser() consults
    # $USER/$LOGNAME first, which survive sudo and su — an env-spoofable
    # value has no business in the check this file exists for.
    actual = pwd.getpwuid(os.geteuid()).pw_name
    expected = manifest.run_as
    if actual != expected:
        log.error(
            "ZONE MISMATCH: manifest declares trust_zone=%r (uid %s) but this "
            "process is running as %s. Refusing to start. Regenerate the "
            "zone drop-in with /aegis/bin/aegis-zone-sync and "
            "`systemctl daemon-reload` (drop-in lives at "
            "/etc/systemd/system/aegis-agent@%s.service.d/zone.conf)",
            manifest.trust_zone, expected, actual, manifest.name,
        )
        raise SystemExit(3)

    proxy = os.environ.get("HTTPS_PROXY", "")
    if manifest.trust_zone == "high" and proxy:
        log.error(
            "HTTPS_PROXY is set (%s) for a high-trust agent. nftables will "
            "refuse the connection anyway, but the environment is wrong and "
            "the drop-in should clear it. Refusing to start.", proxy,
        )
        raise SystemExit(3)

    for path in manifest.read:
        if not Path(path).exists():
            log.warning("declared read path %s does not exist", path)

    log.info("agent=%s zone=%s uid=%s network=%s tools=%s",
             manifest.name, manifest.trust_zone, actual,
             manifest.network, ",".join(manifest.tools) or "-")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", required=True)
    ap.add_argument("--once", action="store_true",
                    help="run preflight and exit; used by verification")
    args = ap.parse_args()

    logging.basicConfig(
        level=os.environ.get("AEGIS_LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
    )

    path = registry.AGENT_DIR / f"{args.agent}.toml"
    if not path.exists():
        log.error("no manifest at %s", path)
        return 2
    try:
        manifest = registry.load(path)
    except registry.ManifestError as exc:
        log.error("manifest rejected: %s", exc)
        return 2

    preflight(manifest)

    if args.once:
        log.info("preflight passed")
        return 0

    # ---------------------------------------------------------------
    # Task loop goes here (phase 5). It should:
    #   - read tasks from Core over a unix socket in /aegis/run
    #   - wrap any externally-fetched content with untrusted.wrap()
    #   - enforce manifest.max_tool_calls and manifest.timeout_seconds
    #   - return structured results, never fabricate success
    # ---------------------------------------------------------------
    log.info("no task loop implemented yet — this is the phase 5 build")
    return 0


if __name__ == "__main__":
    sys.exit(main())
