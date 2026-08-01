# AEGIS Part 10 — AEGIS Core (revised)

*Preserved in substance. Two changes: Core no longer enforces permissions, and
the decision log is now a real artifact rather than a principle.*

---

## What Core is

The orchestration layer. Not a language model — it *uses* models.

Intake → classify → plan → dispatch to agents → synthesise → respond, with
every consequential step recorded.

## What Core is no longer responsible for

The original made Core the enforcement point for permissions and privacy. That
concentrated every security decision in one component: compromise Core and you
had the system.

Enforcement now lives below it:

| Concern | Enforced by |
|---|---|
| Which uid may reach the network | nftables (L5) |
| Which hosts, with what approval | Egress Gate (L5) |
| What an agent can read | filesystem ownership + systemd (L3) |
| What syscalls an agent can make | seccomp filter (L3) |

Core still *decides* — it just cannot *exceed*. Design rule 7: a component
does not enforce limits on itself.

## Decision log

`/aegis/logs/core/decisions.jsonl`. This is Part 01's explainability principle
made concrete, and it doubles as incident evidence — which is how it earns its
cost twice.

```json
{
  "request_id": "01J...",
  "ts": 1785500000.0,
  "intake": {"interface": "terminal", "classification": "confidential"},
  "plan": [{"step": 1, "agent": "documents", "why": "query names a stored contract"}],
  "models": [{"role": "general", "name": "aegis-general", "local": true}],
  "retrieval": {"hits": 4, "sources": ["blade-yard-note.pdf#p3"]},
  "egress": {"attempted": false},
  "outcome": "ok",
  "duration_ms": 4210
}
```

`egress.attempted` is the field you will actually grep. Cross-check it against
`/aegis/logs/egress/audit.jsonl`: **a request in one log with no counterpart in
the other means something bypassed a layer.** That reconciliation is the single
most valuable routine check in the system, and `scripts/aegis-reconcile.py`
does it on the weekly timer.

## Intake

All interfaces normalise to one internal envelope: text, interface, timestamp,
classification, and the trust zone the request may reach. Voice, terminal, web
and scheduled automations differ only in that envelope.

## Planning

For each request determine intent, required knowledge, required tools,
required agents, required models, and the data classification. Simple requests
are one step; do not manufacture plans for them.

**Zone rule:** a plan that needs both untrusted web content and confidential
documents is split into two agent calls in different zones, with the web
result passed to the second as delimited data — never merged into its
instructions. If it cannot be split, Core refuses and says why.

## Synthesis

Validate outputs, resolve conflicts, merge, return one response. When agents
disagree, say so rather than silently picking one — a surfaced disagreement is
information.

## Error handling

Retry transient failures once. Attempt an alternative path. Record. **Never
fabricate successful execution** — an agent that reports success it did not
achieve is worse than one that fails loudly, because you will build on it.
