#!/usr/bin/env python3
"""
shodan_exposure_audit.py
========================

Audit *your own* (or explicitly authorized) network ranges for internet-exposed
devices that are unauthenticated or use default / initial credentials, using the
Shodan API that powers https://monitor.shodan.io.

WHAT THIS DOES
--------------
It *reads Shodan's existing index* of internet-facing services and reports hosts
that Shodan has already flagged as having no authentication or known default
passwords. This is the same data surfaced by Shodan Monitor, exposed as a
scriptable report so you can track exposure across the netblocks you operate.

WHAT THIS DOES NOT DO
---------------------
- It does NOT connect to, probe, log into, or send any traffic to the devices it
  lists. It only queries Shodan's API. The devices themselves are never touched.
- It does NOT attempt to use, validate, or "try" any credential.

AUTHORIZED USE ONLY
-------------------
Shodan Monitor is designed for monitoring assets you own or are contractually
authorized to assess. By default this tool REQUIRES a scope filter (--net,
--org, --hostname, or --asn) so it stays pointed at your own estate. Running
broad, unscoped reconnaissance against third-party devices you do not own or
have written permission to assess may violate Shodan's Terms of Service, the US
CFAA, the UK Computer Misuse Act, and equivalent laws elsewhere. You are
responsible for staying within scope and the law.

SETUP
-----
    pip install requests
    export SHODAN_API_KEY="<your key from https://account.shodan.io>"

EXAMPLES
--------
    # Audit a netblock you own
    ./shodan_exposure_audit.py --net 198.51.100.0/24

    # Audit everything registered to your organization, write a CSV report
    ./shodan_exposure_audit.py --org "Example Corp" --csv exposure.csv

    # Audit by ASN, JSON output, only the "default password" checks
    ./shodan_exposure_audit.py --asn AS64500 --checks default-password --json out.json
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
from dataclasses import dataclass, field, asdict
from typing import Iterable

import requests

SHODAN_API = "https://api.shodan.io"
USER_AGENT = "shodan-exposure-audit/1.0 (+authorized asset monitoring)"


# ---------------------------------------------------------------------------
# Curated exposure "checks".
#
# Each check is a Shodan search query that surfaces a class of hosts that are
# either unauthenticated or rely on default / initial credentials. These are
# intentionally read-only Shodan filters; we never act on the results.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Check:
    key: str
    description: str
    # Shodan query fragment (scope filters are appended at runtime).
    query: str


CHECKS: list[Check] = [
    Check(
        "default-password",
        "Hosts Shodan tagged as using default / known credentials",
        "tag:default-password",
    ),
    Check(
        "open-database",
        "Databases exposed with no authentication (Mongo/Elastic/etc.)",
        '(product:MongoDB "Set-Cookie" -authentication) OR '
        '(product:Elastic "cluster_name") OR '
        'product:"CouchDB" OR product:"Redis key-value store" -"NOAUTH"',
    ),
    Check(
        "vnc-no-auth",
        "VNC services that accept connections without a password",
        'port:5900,5901 "RFB 003" authentication disabled',
    ),
    Check(
        "rdp-no-nla",
        "RDP exposed without Network Level Authentication",
        'port:3389 "Security Layer: RDP" -nla',
    ),
    Check(
        "telnet-open",
        "Telnet services exposing a login/shell without auth",
        'port:23 "Login authentication" OR (port:23 "BusyBox")',
    ),
    Check(
        "ftp-anon",
        "FTP servers that permit anonymous login",
        '"220" "Anonymous access granted" port:21',
    ),
    Check(
        "printer-open",
        "Network printers with no admin password set",
        'tag:default-password "printer" OR product:"HP Printer"',
    ),
    Check(
        "webcam-open",
        "IP cameras / webcams reachable without authentication",
        'tag:default-password (product:"GeoVision" OR "webcamXP" OR "Network Camera") '
        '-"401 Unauthorized"',
    ),
    Check(
        "industrial",
        "Industrial / SCADA endpoints exposed without authentication",
        'tag:ics -"401 Unauthorized" -authentication',
    ),
]

CHECKS_BY_KEY = {c.key: c for c in CHECKS}


@dataclass
class HostResult:
    ip: str
    port: int
    transport: str
    hostnames: list[str]
    org: str
    isp: str
    country: str
    product: str
    tags: list[str]
    check: str
    data_excerpt: str = ""


@dataclass
class Report:
    scope: str
    checks_run: list[str]
    hosts: list[HostResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class ShodanError(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("SHODAN_API_KEY", "").strip()
    if not key:
        raise ShodanError(
            "SHODAN_API_KEY is not set. Get a key at https://account.shodan.io "
            "and run:  export SHODAN_API_KEY='...'"
        )
    return key


def build_scope_filter(args: argparse.Namespace) -> str:
    """Translate the CLI scope flags into Shodan search filters."""
    parts: list[str] = []
    for net in args.net or []:
        parts.append(f"net:{net}")
    for org in args.org or []:
        # quote multi-word org names
        parts.append(f'org:"{org}"')
    for host in args.hostname or []:
        parts.append(f"hostname:{host}")
    for asn in args.asn or []:
        asn = asn if asn.upper().startswith("AS") else f"AS{asn}"
        parts.append(f"asn:{asn}")
    return " ".join(parts)


def shodan_search(
    key: str, query: str, max_results: int, pause: float
) -> Iterable[dict]:
    """Page through Shodan host/search results. Read-only."""
    page = 1
    fetched = 0
    session = requests.Session()
    session.headers["User-Agent"] = USER_AGENT
    while fetched < max_results:
        resp = session.get(
            f"{SHODAN_API}/shodan/host/search",
            params={"key": key, "query": query, "page": page},
            timeout=30,
        )
        if resp.status_code == 401:
            raise ShodanError("Shodan rejected the API key (401 Unauthorized).")
        if resp.status_code == 403:
            raise ShodanError(
                "Shodan returned 403 — your plan may not allow search filters. "
                "Search filters require a paid Shodan membership."
            )
        if resp.status_code == 429:
            time.sleep(2.0)
            continue
        if resp.status_code != 200:
            raise ShodanError(f"Shodan error {resp.status_code}: {resp.text[:200]}")

        payload = resp.json()
        matches = payload.get("matches", [])
        if not matches:
            break
        for match in matches:
            yield match
            fetched += 1
            if fetched >= max_results:
                return
        total = payload.get("total", 0)
        if page * 100 >= total:
            break
        page += 1
        time.sleep(pause)  # be polite to the API / respect rate limits


def to_host_result(match: dict, check_key: str) -> HostResult:
    excerpt = (match.get("data") or "").strip().replace("\n", " ")
    return HostResult(
        ip=match.get("ip_str", ""),
        port=match.get("port", 0),
        transport=match.get("transport", ""),
        hostnames=match.get("hostnames", []) or [],
        org=match.get("org", "") or "",
        isp=match.get("isp", "") or "",
        country=match.get("location", {}).get("country_name", "") or "",
        product=match.get("product", "") or "",
        tags=match.get("tags", []) or [],
        check=check_key,
        data_excerpt=excerpt[:160],
    )


def run_audit(args: argparse.Namespace) -> Report:
    key = api_key()
    scope = build_scope_filter(args)

    if not scope and not args.unscoped:
        raise ShodanError(
            "No scope provided. Specify the assets you own/are authorized to "
            "audit with --net / --org / --hostname / --asn.\n"
            "If you genuinely intend an unscoped query and have authorization, "
            "pass --unscoped to acknowledge that responsibility."
        )

    selected = (
        [CHECKS_BY_KEY[k] for k in args.checks]
        if args.checks
        else CHECKS
    )

    report = Report(scope=scope or "(UNSCOPED)", checks_run=[c.key for c in selected])

    for check in selected:
        query = f"{check.query} {scope}".strip()
        sys.stderr.write(f"[*] {check.key}: {query}\n")
        try:
            for match in shodan_search(key, query, args.limit, args.pause):
                report.hosts.append(to_host_result(match, check.key))
        except ShodanError as exc:
            report.errors.append(f"{check.key}: {exc}")
            sys.stderr.write(f"[!] {check.key}: {exc}\n")
    return report


def write_csv(report: Report, path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["check", "ip", "port", "transport", "hostnames", "org",
             "isp", "country", "product", "tags", "data_excerpt"]
        )
        for h in report.hosts:
            writer.writerow(
                [h.check, h.ip, h.port, h.transport, ";".join(h.hostnames),
                 h.org, h.isp, h.country, h.product, ";".join(h.tags),
                 h.data_excerpt]
            )


def write_json(report: Report, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(
            {
                "scope": report.scope,
                "checks_run": report.checks_run,
                "host_count": len(report.hosts),
                "hosts": [asdict(h) for h in report.hosts],
                "errors": report.errors,
            },
            fh,
            indent=2,
        )


def print_summary(report: Report) -> None:
    print("\n=== Shodan Exposure Audit ===")
    print(f"Scope        : {report.scope}")
    print(f"Checks run   : {', '.join(report.checks_run)}")
    print(f"Hosts found  : {len(report.hosts)}")
    if report.errors:
        print(f"Errors       : {len(report.errors)}")

    by_check: dict[str, int] = {}
    for h in report.hosts:
        by_check[h.check] = by_check.get(h.check, 0) + 1
    if by_check:
        print("\nFindings by check:")
        for k, v in sorted(by_check.items(), key=lambda kv: -kv[1]):
            print(f"  {v:>5}  {k}  — {CHECKS_BY_KEY[k].description}")

    if report.hosts:
        print("\nSample findings (first 20):")
        for h in report.hosts[:20]:
            host = h.hostnames[0] if h.hostnames else "-"
            print(f"  [{h.check}] {h.ip}:{h.port}  {host}  ({h.product or 'unknown'})")
    if not report.hosts:
        print("\nNo exposed/unauthenticated devices found for this scope. ✅")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Audit your authorized network ranges for unauthenticated / "
                    "default-credential devices via the Shodan API (read-only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    scope = p.add_argument_group("scope (specify the assets you are authorized to audit)")
    scope.add_argument("--net", action="append", metavar="CIDR",
                       help="Network range you own, e.g. 198.51.100.0/24 (repeatable)")
    scope.add_argument("--org", action="append", metavar="NAME",
                       help="Organization name registered to your IP space (repeatable)")
    scope.add_argument("--hostname", action="append", metavar="DOMAIN",
                       help="Hostname/domain suffix you control (repeatable)")
    scope.add_argument("--asn", action="append", metavar="ASN",
                       help="Autonomous System Number you operate, e.g. AS64500 (repeatable)")
    scope.add_argument("--unscoped", action="store_true",
                       help="Acknowledge running without a scope filter (authorized use only)")

    p.add_argument("--checks", nargs="+", choices=list(CHECKS_BY_KEY),
                   help="Subset of checks to run (default: all)")
    p.add_argument("--limit", type=int, default=100,
                   help="Max results per check (default: 100)")
    p.add_argument("--pause", type=float, default=1.0,
                   help="Seconds to pause between API pages (default: 1.0)")
    p.add_argument("--csv", metavar="PATH", help="Write findings to a CSV file")
    p.add_argument("--json", metavar="PATH", help="Write findings to a JSON file")
    p.add_argument("--list-checks", action="store_true",
                   help="List available checks and exit")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_checks:
        print("Available checks:\n")
        for c in CHECKS:
            print(f"  {c.key:<18} {c.description}")
        return 0

    try:
        report = run_audit(args)
    except ShodanError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 2
    except KeyboardInterrupt:
        sys.stderr.write("\nInterrupted.\n")
        return 130

    if args.csv:
        write_csv(report, args.csv)
        sys.stderr.write(f"[+] CSV written to {args.csv}\n")
    if args.json:
        write_json(report, args.json)
        sys.stderr.write(f"[+] JSON written to {args.json}\n")

    print_summary(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
