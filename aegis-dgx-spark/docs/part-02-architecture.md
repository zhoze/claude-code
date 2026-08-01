# AEGIS Part 02 — System Architecture (revised)

*Supersedes the original Part 02. Two layers are new — Isolation and the
Egress Gate — and one design rule has been split. Everything else is
preserved.*

---

## Why this revision exists

The original architecture placed the privacy boundary in the Model Router and
AEGIS Core: software components that the agent controls. That makes "cloud
models are used only when the user allows it" a rule the system is *asked* to
follow. A confused, buggy, or prompt-injected Core simply does not follow it.

The revision moves enforcement below the components being constrained. The
principle:

> **A component must not be responsible for enforcing a limit on itself.**

---

## Layers

Numbered canonically. Refer to them by number everywhere else in the
specification; "layer" means only this list. (Part 12's memory tiers are
renamed *tiers* to remove the collision.)

```
                              You
                               │
  L11  Interfaces        voice │ web │ terminal │ mobile
                               │
  L10  Automation        schedules, events, notifications
                               │
  L9   Knowledge         documents, embeddings, retrieval
                               │
  L8   Agents            research │ documents │ logistics │ monitoring
                               │
  L7   AEGIS Core        planning, orchestration, synthesis
                               │
  L6   Model Router      chooses a model; REQUESTS egress
                               │
  ╔════════════════════════════╪════════════════════════════╗
  ║  L5   EGRESS GATE          │   allowlist + approval      ║  ← not
  ║       nftables + proxy     │   holds the credentials     ║    reachable
  ╚════════════════════════════╪════════════════════════════╝    from above
                               │
  L4   AI Runtime        local model serving
                               │
  L3   ISOLATION         per-agent uid, namespace, mounts
                               │
  L2   Operating System  DGX OS 7
                               │
  L1   Hardware          DGX Spark
```

The double border is the trust boundary. Layers above it are *asked* to
behave. Layers at and below it *enforce*. Everything above L5 is assumed
potentially compromised; the design still has to hold.

---

## L3 — Isolation (new)

The original specification asserted least privilege for agents and gave no
mechanism. This layer is the mechanism.

**Every agent gets:**

- its own uid, from a fixed set of trust zones (below)
- its own systemd unit, so it has its own lifecycle, logs, and failure domain
- a read-only root filesystem with explicit bind mounts only
- a memory cap and a task cap, so one agent cannot take the box down
- a syscall filter (`@system-service` minus `@privileged @resources @mount`)
- an empty capability bounding set

**Trust zones.** Two, and the boundary between them is the answer to T1 in
Part 00.

| Zone | uid | Reads | Network |
|---|---|---|---|
| Low trust | `aegis-lt` | `/aegis/scratch` only | Gate, for allowlisted routes |
| High trust | `aegis-ht` | `/aegis/knowledge`, vector db | **none** |

An agent that reads untrusted input goes in low trust. An agent that touches
CF&S data goes in high trust. No agent is in both, and there is no third zone
— a "medium trust" tier is how this collapses back into one zone over time.

**They do not talk to each other.** Content crossing from low to high is data,
never instruction: passed as a delimited payload into a prompt built to treat
it as untrusted, and reviewed by you for anything consequential. The failure
modes, written down so they stay written down:

- high → low is **exfiltration**
- low → high is **prompt injection**

An agent that declares no zone gets low trust. Failing closed is the point.

---

## L5 — Egress Gate (new)

Two mechanisms, one boundary.

**Kernel (`config/nftables/aegis.nft`).** Default-deny output. Outbound
sockets to the public internet are permitted for exactly one uid,
`aegis-proxy`. Loopback is scoped by uid too, so `aegis-ht` cannot reach the
Gate. DNS is denied to agents, which removes the standard covert channel — the
Gate resolves on their behalf.

**Userspace (`src/egress_gate/gate.py`).** A CONNECT proxy that:

- matches hostnames exactly; no wildcards, no suffix matching
- classifies every route `auto`, `gated`, or absent-means-denied
- requires a single-use approval token for every `gated` route
- holds the cloud API credentials, so Core cannot construct a call it is not
  entitled to make
- writes an append-only audit record of every decision, allowed and denied

**Approval is out of band.** `aegis-approve` runs as *you*, in the
`aegis-operators` group, writing to a directory AEGIS cannot write to. This is
what turns "only on my explicit request" from a promise made by a model into a
property of the system.

**What the Gate does not do:** inspect payloads. Doing so would mean
terminating TLS and holding your traffic in plaintext on the box, which is a
worse position than the one it defends. Control is at the domain and the
approval, not the byte.

---

## Design rules (revised)

1. **All user requests flow through Core.** *(Was: "never bypass the Core".
   Narrowed — see rule 6.)*
2. Keep components loosely coupled.
3. Prefer local execution.
4. Minimise trust boundaries, and make the ones that remain explicit and
   testable.
5. Every layer is independently replaceable.
6. **Safety-critical and time-critical automation does not depend on Core.**
   The original rule made Core a single point of failure for the house: Part
   16 promises the home keeps working during an outage while Part 02 routed
   every device event through Core. Local automations execute in L10 and
   *report* to Core rather than depending on it.
7. **A component does not enforce limits on itself.** Permission decisions
   live in L3 and L5, not in Core. Compromising Core must not by itself grant
   access.
8. **Fail closed.** Every default — undeclared trust zone, unlisted host,
   missing approval, unparseable config — resolves to the restrictive option.

---

## What this costs

Honesty about the trade, so it is not discovered later as a surprise:

- Adding a capability is now a **two-place change**: the agent, and the
  allowlist. This is friction by design, and it is the friction that makes the
  boundary real.
- High-trust agents cannot search the web. Ever. If that turns out to be
  intolerable in practice, the correct response is to move the capability to a
  low-trust agent and pass results across as data — not to give the high-trust
  agent a route.
- Cloud inference requires a typed command each time. If that becomes
  unbearable, `--count` mints a small batch with a short TTL. Resist the urge
  to mint a hundred with a day-long TTL; at that point you have rebuilt the
  original design.
