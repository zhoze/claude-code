# AEGIS Part 07 — Networking & Egress Gate (revised)

*The original devoted one unelaborated line to the firewall and left privacy
enforcement to a software policy in Part 09. This part makes the boundary
real.*

---

## Inbound

Default drop. Administration arrives over the LAN or Tailscale, never from the
public internet. No port forwarding, ever.

Exposure ladder, in order of preference: local network → Tailscale → reverse
proxy (only with a written reason) → public internet (do not).

**Tailscale dependency, stated plainly.** Part 01 declares Offline First, and
Tailscale's data plane is peer-to-peer WireGuard that keeps working during an
outage — but key exchange and new-device enrolment depend on Tailscale's
coordination servers. Under the exact scenario Part 01 is built around, you
may be unable to bring up a *new* admin session. Mitigation: a documented
local-LAN SSH path with its own key, tested. Self-hosting a coordination
server is not worth the operational cost initially; record that as a decision
rather than an oversight.

---

## Outbound — the Gate

Two mechanisms enforcing one boundary. Both must hold.

### Kernel: `config/nftables/aegis.nft`

- `output` policy `drop`.
- Public egress permitted for exactly one uid: `aegis-proxy`.
- Loopback scoped by uid — `aegis-ht` is denied port 3128, so a high-trust
  agent has no path to the Gate at all.
- DNS permitted only to `aegis-proxy` and root. Agents cannot resolve names,
  which removes DNS as a covert exfiltration channel.
- Your admin account has its own explicit rule, so Claude Code's access to
  `api.anthropic.com` is visible in the policy and revocable without touching
  the Gate.
- Root retains outbound access so the OS can be patched. **This is a real
  hole and it is deliberate** — keep the root-owned service inventory short.

Why not IP allowlisting instead of a proxy? Because every one of these
endpoints is behind a CDN whose addresses rotate. IP rules would break weekly
and you would widen them until they meant nothing.

### Userspace: `src/egress_gate/gate.py`

- Exact hostname match. No wildcards, no suffix matching. Three subdomains
  means three lines.
- `auto` — allowed; for traffic carrying no private data outbound.
- `gated` — requires a single-use token from `aegis-approve`.
- Absent — denied. There is no implicit allow.
- Per-route uid restriction, per-connection byte cap, append-only audit log.

### Approval

```bash
aegis-approve api.anthropic.com --reason "summarise blade-yard stacking note"
```

Runs as you, in `aegis-operators`, writing to a directory AEGIS cannot write
to. Tokens default to a 120-second TTL and are burned by the Gate *before* the
upstream socket opens, so a crash mid-request does not leave a reusable token.

`aegis-approve --list` shows what is outstanding. `--revoke-all` clears it.
Run `--revoke-all` when you finish a session; an approval you forgot about is
an approval an injected agent can use.

---

## Order of operations

Apply the policy **after** the install completes. Locking down before model
weights are downloaded breaks the download, and the temptation at that point
is to open a hole and forget it.

---

## Verification

```bash
sudo tests/verify-egress.sh
```

Eight assertions, each mapping to a property the architecture claims. Run
after every change to `aegis.nft` or `egress.toml`, and on a weekly timer. An
untested policy is not a policy — it is a document.
