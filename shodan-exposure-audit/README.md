# Shodan Exposure Audit

A small, read-only command-line tool that uses the **Shodan API** (the same data
behind [monitor.shodan.io](https://monitor.shodan.io)) to find internet-exposed
devices on **your own** network ranges that are **unauthenticated** or use
**default / initial passwords** — so you can fix them before someone else finds
them.

## ⚠️ Authorized use only

This is a **defensive asset-monitoring** tool. Shodan Monitor exists to watch the
networks *you operate*. Accordingly, the tool **requires a scope filter**
(`--net`, `--org`, `--hostname`, or `--asn`) so it stays pointed at your estate.

- It **only reads Shodan's existing index**. It never connects to, scans, probes,
  or logs into any device, and it never tries any credential.
- Running broad, unscoped reconnaissance against systems you do not own or have
  **written authorization** to assess can violate Shodan's Terms of Service and
  computer-misuse laws (US CFAA, UK CMA, etc.). Staying in scope and within the
  law is your responsibility.

## How it relates to `monitor.shodan.io`

Shodan Monitor is the web UI for tracking your registered networks and alerting on
new exposures. This tool is the scriptable equivalent for one specific question:
*"which of my hosts are exposed with no auth or default credentials?"* It calls
`api.shodan.io/shodan/host/search` with a curated set of filters and a scope
restricted to your assets.

## Setup

```bash
pip install requests
export SHODAN_API_KEY="your_key_from_https://account.shodan.io"
```

> Note: Shodan **search filters require a paid membership**. The free tier will
> return `403` for these queries.

## Usage

```bash
# Audit a netblock you own
./shodan_exposure_audit.py --net 198.51.100.0/24

# Audit everything registered to your organization, write a CSV report
./shodan_exposure_audit.py --org "Example Corp" --csv exposure.csv

# Audit by ASN, only the default-password and open-database checks
./shodan_exposure_audit.py --asn AS64500 --checks default-password open-database

# See what checks are available
./shodan_exposure_audit.py --list-checks
```

## Checks

| Key                | What it surfaces                                                |
|--------------------|----------------------------------------------------------------|
| `default-password` | Hosts Shodan tagged as using default / known credentials        |
| `open-database`    | Databases exposed with no authentication (Mongo/Elastic/etc.)   |
| `vnc-no-auth`      | VNC services accepting connections without a password           |
| `rdp-no-nla`       | RDP exposed without Network Level Authentication                |
| `telnet-open`      | Telnet login/shell exposed without auth                         |
| `ftp-anon`         | FTP servers permitting anonymous login                          |
| `printer-open`     | Network printers with no admin password set                     |
| `webcam-open`      | IP cameras / webcams reachable without authentication           |
| `industrial`       | Industrial / SCADA endpoints exposed without authentication     |

## Output

- A human-readable summary to stdout (counts per check + sample findings).
- Optional `--csv` and `--json` reports for ticketing / tracking remediation.

## Remediating what you find

For each finding: put the service behind a VPN or firewall, require
authentication, and rotate any default/initial credentials. Then re-run the audit
to confirm the exposure is gone.

## Why it's built this way (and not as a mass scanner)

The point of monitoring is to reduce *your* attack surface. The tool deliberately
won't help indiscriminately enumerate strangers' devices or touch any host —
it reads Shodan metadata for the scope you assert you own, and stops there.
```
