# AEGIS Part 17 — Implementation Roadmap

*The original Parts 01–16 are topics, not phases. Nothing stated what depends
on what, or what "finished" means. This part does.*

---

## Definition of minimum viable AEGIS

**One local model, Core, one high-trust agent, the knowledge base, and the
Egress Gate.** No voice. No home automation. No cloud routing.

If that runs for two weeks without intervention and answers questions about
your own documents, the architecture is proven. Everything after is addition,
not validation. The temptation will be to build the voice interface early
because it is the most satisfying — resist it; it is the largest surface area
and the least essential.

---

## Phases

### Phase 0 — Foundation *(you, no agents)*
`00-preflight.sh` · disk encryption decision (Part 00 T3) · admin user · SSH
keys, password auth off · Tailscale · `02-packages.sh`

**Done when:** you can reach the box over Tailscale from the road, preflight
passes clean, and you have written down the encryption trade-off you chose.

### Phase 1 — Identity and structure
`01-users-and-dirs.sh` · `03-python-env.sh` · service identities created at
their providers, keys into `systemd-creds`

**Done when:** five service users exist, none has a shell, `/aegis` is laid
out, and no secret exists in a file under `/aegis/config`.

### Phase 2 — The boundary *(before any model)*
`bootstrap/05-egress-gate.sh` (credentials first: `04-credentials.sh`) ·
`tests/verify-egress.sh` reporting **zero failures**

**Done when:** every assertion passes. Do not proceed with any FAIL. This phase is
the one that makes the rest of the system trustworthy, and it is much easier
to get right before there is anything running that you are reluctant to
break.

### Phase 3 — Local inference
Choose and download a general model · `aegis-llm.service` · budget enforced ·
sustained-load test

**Done when:** the model answers over `127.0.0.1:8000`, memory stays inside
the Part 03 budget under a long-context run, and the service survives a
reboot.

### Phase 4 — Knowledge base
Vector db · ingestion pipeline (Part 14) · one high-trust Document Agent ·
retrieval with citations

**Done when:** you can ask about a real CF&S document and get an answer with a
source. **Do not ingest client material until Part 00's GDPR note is
resolved** — lawful basis, retention period, and a working deletion procedure
across documents, embeddings, metadata and backups.

### Phase 5 — Core and orchestration
`aegis-core` · agent registry · decision log · one low-trust Research Agent,
with the first web domain added to the allowlist deliberately

**Done when:** Core routes to the right agent, the decision log explains why,
and `verify-egress.sh` still passes with the new agent running.

### Phase 6 — Cloud routing
Gated route exercised end to end · spend caps and billing alerts

**Done when:** a cloud call succeeds with approval, fails without it, and both
appear correctly in the audit log.

### Phase 7 — Backup and recovery
*Referenced three times in the original specification and never written.*
Restore target · RPO/RTO stated · off-site encrypted copy · **a restore
actually performed**

**Done when:** you have rebuilt `/aegis/knowledge` from backup onto scratch
storage and diffed it. A backup you have not restored from is a hypothesis.

### Phase 8 — Monitoring
Health checks · alerting on a channel that does not depend on AEGIS ·
`verify-egress.sh` on a timer · weekly audit review

### Phase 9 — Voice *(Part 15)*
Only once phases 0–8 are stable. Set a measured latency target.

### Phase 10 — Home automation *(Part 16, separate hardware)*
Home Assistant on its own box. AEGIS reads state; safety-critical writes
require confirmation through a channel independent of AEGIS.

---

## Dependencies

```
0 → 1 → 2 → 3 → 4 → 5 → 6
             ↘   ↘   ↘
              7   8   9 → 10
```

Phase 2 gates everything. Phase 7 should not wait for phase 8 — the first time
you want a backup is the first time something breaks.

---

## Working with Claude Code

Plan mode, one phase at a time, re-entering between phases. Keep
`SETUP-PLAN.md` as the continuity record; sessions do not survive reboots and
this build has several. Claude Code runs as *you*, not as root and not as any
`aegis-*` user. Every privileged command gets its own approval — a blanket
grant makes the rights model decorative.

---

## Things deliberately deferred

Written down so they are decisions, not omissions: multi-node inference,
distributed agents, knowledge graphs, speaker identification, predictive
automation, robotics, multi-property. None of these should influence a
decision made today.
