# AEGIS Decision Log

Decisions that shaped the build, with dates and reasoning. Not a changelog —
a record of *why*, so that in eight months you can see what you chose rather
than guessing what past-you was thinking.

Add an entry whenever you resolve a trade-off. The template is at the bottom.

---

## Open — decide before the phase that needs it

### D1 · Disk encryption *(before phase 0 completes)*
See docs/part-00-threat-model.md T3 and part-04.

TPM-bound unlock protects against drive theft but not whole-machine theft.
Passphrase-at-boot protects against both but breaks unattended restart, which
contradicts the 24/7 requirement.

**Chosen:** _______________  **Date:** __________
**Reasoning:** _______________________________________________

### D2 · Embedding model for Estonian *(before phase 4)*
Many English-first models degrade badly on Estonian morphology, and you will
not notice from English test queries. Build a 20-question Estonian set from
real CF&S material and measure before committing.

**Chosen:** _______________  **Date:** __________
**Measured accuracy:** __________

### D3 · GDPR position *(before any client material is ingested)*
Lawful basis, retention period, erasure procedure.

**Lawful basis:** _______________________________________________
**Retention:** __________  **Date:** __________
**Discussed with:** _______________

### D4 · Backup retention period
Determines when a deletion is actually complete (part-12). This number *is*
your honest answer to an erasure request.

**Chosen:** __________ days  **Date:** __________

---

## Settled

### 2026-07 · Privacy enforcement moved below the components it constrains
The original specification placed the boundary in the Model Router and Core —
software the agent controls. Moved to nftables plus the Egress Gate.
**Cost accepted:** adding a capability is now a two-place change. That
friction is the point.

### 2026-07 · Two trust zones, not three
A "medium trust" tier is how the split collapses back into one zone within a
year. High-trust agents have no network path at all; low-trust agents hold no
business data.
**Cost accepted:** high-trust agents cannot search the web. Ever.

### 2026-07 · Home automation on separate hardware
One box should not hold CF&S client correspondence and open the front door.
**Cost accepted:** a Pi or NUC, and one more thing to maintain.
**Bought:** the house keeps working when the Spark reboots.

### 2026-07 · The Gate denies egress when it cannot audit
Found in testing: an unwritable audit log crashed the request handler.
A decision that cannot be recorded has not been made, and a full disk is
exactly the state an attacker would engineer.
**Cost accepted:** a full `/aegis/logs` takes cloud routing offline.

### 2026-08 · Full-tree amendment after the pre-install review
A three-way review against a real DGX Spark profile found the shipped tree
could not install (approval flow broken, firewall killing DNS/apt/NTP,
mandatory credentials nothing created, fictional service entrypoints).
Everything is documented per-defect in AMENDMENTS.md. Structural choices:
**Chosen:** llama.cpp as the runtime (vLLM has no GB10/aarch64 wheels);
real Qdrant binary under its own uid; scan → compare → amend → install
gating with a dead-man auto-rollback on the firewall apply; zone drop-ins
generated from manifests; `ImportCredential=` with a provisioning phase.
**Cost accepted:** llama.cpp trades vLLM's batching throughput for
installability; the gate's staged API keys are not injectable into CONNECT
tunnels (documented honestly instead of MITM).
**Revisit if:** NVIDIA ships supported vLLM wheels/containers for DGX OS
that fit the no-Docker-dependency stance, or the phase-5 Core build needs
the release-step redesigned.

---

## Template

```
### YYYY-MM · <what was decided>
<the trade-off in one or two sentences>
**Chosen:** <option>  **Rejected:** <option>
**Cost accepted:** <what this makes worse>
**Revisit if:** <what would change the answer>
```
