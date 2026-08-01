# Reviewing the scan with Claude Code (stage 2–3)

The install flow is deliberately gated:

```
1. SCAN      ./scan/aegis-scan.sh          read-only profile of THIS machine
2. COMPARE   ./scan/aegis-compare.sh       machine vs. the install code's assumptions
3. AMEND     fix code (or machine) until the compare has zero FAILs
4. INSTALL   sudo ./install.sh             refuses to run without a passing compare
```

The compare script checks the assumptions we know about. A machine can still
surprise you in ways no fixed checklist anticipates — that is what this review
step is for. Run Claude Code on the DGX Spark (or on any machine with this
repository plus the `scan-report/` directory copied over) and let it read the
actual facts against the actual code.

## Setup

```bash
# on the Spark, in the repository root, after stages 1–2:
claude
```

## Ready prompt

Paste this as your first message:

> Read scan-report/facts.json and scan-report/compare-report.txt in this
> repository. This machine is about to run ./install.sh, which executes the
> phases in bootstrap/ in order (00–09), applies config/nftables/aegis.nft.tmpl
> as a default-deny egress firewall, and installs the systemd units in
> systemd/. Review the scan facts against the installation code and tell me:
>
> 1. Anything on this specific machine that contradicts an assumption in the
>    code and is NOT already flagged in compare-report.txt — look especially
>    at: device nodes vs systemd/aegis-llm.service, listening sockets and
>    running services vs the nftables output/input chains, users/uids
>    referenced in config/nftables/aegis.nft.tmpl vs what exists here, apt
>    sources vs config/egress.toml, Docker/container state vs the forward
>    chain, and anything in the enabled services list that will lose network
>    access under a per-uid default-deny egress policy and matters.
> 2. For each finding: the exact file and line to amend, and the amendment.
> 3. Apply the amendments I approve, then re-run ./scan/aegis-compare.sh and
>    show me the result.
>
> Do not run install.sh or apply any firewall rule yourself.

## What to do with WARN findings

`WARN` means "the install will proceed but you should have consciously
decided". The recurring ones:

- **No LUKS (D1)** — encryption after the fact means reinstalling DGX OS.
  install.sh will require a typed acknowledgement; record the decision in
  `docs/DECISIONS.md`.
- **No Tailscale** — the firewall ships Tailscale-shaped holes and remote
  admin assumes it. Install it before phase 5, or accept LAN-only admin and
  say so in `docs/DECISIONS.md`.
- **Docker with running containers** — the AEGIS policy no longer destroys
  Docker's rules, but per-uid default-deny still applies to everything the
  host originates. Test your containers after phase 5.
- **Existing non-AEGIS ruleset** — the policy only replaces `table inet
  aegis`, but its default-deny chains apply to all traffic regardless of
  which table accepted it first. Review overlap before phase 5.

## After amendments

Always re-run both stages so the recorded state matches reality:

```bash
./scan/aegis-scan.sh
./scan/aegis-compare.sh --write-overrides
sudo ./install.sh
```

`--write-overrides` regenerates `config/site-overrides.env` (admin user,
memory-derived cgroup caps, GPU node style). Review the file — it is sourced
by every bootstrap phase.
