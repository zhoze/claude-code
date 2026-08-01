# AEGIS Part 05 — Identities & Security Model (revised)

*The original reduced this to one hedged line: "use dedicated service accounts
where practical." It is the load-bearing decision of the whole design and gets
its own part.*

---

## Rule

**AEGIS never uses your credentials. For anything.**

Two consequences, and they are why the rule exists:

1. **Revocability.** You can cut the agent's access to a system without
   cutting your own. If AEGIS misbehaves at 02:00 you disable one key, not
   your working life.
2. **Attribution.** The audit trail distinguishes what you did from what the
   agent did. Without separate identities, every log line is ambiguous exactly
   when it matters.

---

## System users

| User | Role | Shell | Network | Data |
|---|---|---|---|---|
| *you* | operator, admin, Claude Code | yes | full (own rule) | all |
| `aegis-core` | orchestration | none | Gate only | config, logs |
| `aegis-proxy` | egress gate | none | **the only public egress** | approvals, audit |
| `aegis-llm` | model runtime | none | weight mirrors only | `/aegis/models` |
| `aegis-lt` | low-trust agents | none | Gate, allowlisted routes | `/aegis/scratch` |
| `aegis-ht` | high-trust agents | none | **none** | `/aegis/knowledge` |

All service users are `--system`, no home directory, `/usr/sbin/nologin`, and
none is in `sudo`. If you find yourself wanting to add one, stop.

`aegis-operators` is the group permitted to mint egress approvals. You are in
it. Nothing else is.

---

## Service identities (external)

Separate from Unix users: these are AEGIS's accounts on other people's
systems.

| Identity | Notes |
|---|---|
| Anthropic API key | From Console, billed per token. **Not** your claude.ai subscription — that is for you at a terminal. |
| NVIDIA API key | AEGIS's own. |
| Mailbox | Its own address, if it handles mail. Never your personal inbox. |
| Bot tokens | Per interface, per channel. |
| CF&S system access | **Read-scoped.** An agent that can write into a production logistics system is a category of risk this design does not cover. |

Each must be independently revocable, and **revocation must be tested** — not
assumed. Put a calendar reminder to actually revoke and reissue one key per
quarter; the first time you try it should not be during an incident.

---

## Secrets handling

Keys live in `systemd-creds`, encrypted at rest, injected into one unit's
environment:

```bash
sudo systemd-creds encrypt --name=anthropic_api_key - \
     /etc/credstore.encrypted/anthropic_api_key
# type the key, then Ctrl-D. It does not touch shell history or a file.
```

Rules that follow from this:

- Secrets never appear in `/aegis/config`, in the repository, in a shell
  command, or in a chat window.
- Only `aegis-egress-gate.service` loads the cloud credentials. Core cannot
  read them. That is what stops Core making an unrouted call.
- Config files that reference a secret reference its *name*, never its value.
- Backups of `/etc/credstore.encrypted` are encrypted separately with a key
  that is not on this machine.

---

## Logging and audit

- `auditd` for privileged operations.
- `/aegis/logs/egress/audit.jsonl` — every Gate decision, allowed and denied.
  Append-only, fsynced.
- **Ship logs off-box on a schedule.** A log that exists only on the
  compromised machine is not evidence.
- The decision log satisfies Part 01's "explainability" principle, which the
  original specification declared and then never mentioned again.

Review weekly, and specifically look at denials. A rising denial count for one
host is either a broken service or an agent trying something it should not —
both are worth knowing.
