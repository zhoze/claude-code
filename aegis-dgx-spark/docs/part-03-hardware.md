# AEGIS Part 03 — Hardware & Memory Budget (revised)

*Adds the one number that actually constrains this design and was absent from
the original: the memory budget.*

---

## The constraint

The DGX Spark's 128 GiB is **unified**. There is no separate VRAM pool. Model
weights, KV cache, the vector database, the OS page cache, and every service
compete for the same memory. The original Part 08 simultaneously required
"prevent GPU overcommitment" and "support multiple concurrent models" without
stating a budget, which made the requirement unenforceable.

The second thing to internalise: at roughly 273 GB/s the Spark is a
**capacity** machine, not a throughput machine. It will hold a model that a
consumer GPU cannot, and it will generate tokens more slowly than one. That
shapes what belongs here and what does not.

---

## Budget

| Consumer | Allocation | Enforced by |
|---|---:|---|
| General reasoning model + KV cache | 80 GiB | `MemoryMax=80G` in `aegis-llm.service` |
| Embedding model | 6 GiB | service cap |
| Speech (STT + TTS), when phase 4 lands | 8 GiB | service cap |
| Vector database | 12 GiB | container/service cap |
| AEGIS Core | 4 GiB | service cap |
| Agents (max 4 concurrent × 8 GiB, bursty) | 12 GiB effective | `MemoryMax=8G` per unit |
| OS, page cache, headroom | 6 GiB | — |
| **Total** | **128 GiB** | |

**Consequences to accept up front:**

- You cannot hold a large reasoning model, a separate coding model, and a
  vision model resident at once. Pick one resident general model; load
  specialists on demand and accept the load latency, or route those tasks to
  cloud through the gated path.
- Raising `AEGIS_MAX_LEN` raises KV cache use roughly linearly. A long-context
  run is what will push the box into swap, and swap on an inference host means
  the whole system stops responding — including the voice interface.
- The voice path's latency target and a large resident model pull against each
  other. Set a measured target (recommend: wake word to first audio under
  1200 ms) and treat it as a budget, not an aspiration.

**Eviction policy under pressure:** agents first, then specialists, then the
embedding model. The general model and Core are never evicted; if they cannot
be served, the system reports degraded rather than thrashing.

---

## Reliability

- Automatic startup after power loss — with the encryption consequence
  documented in Part 00, T3.
- SMART monitoring via `smartd`, alerting to a channel that does not depend on
  AEGIS being up.
- Thermal monitoring. The GB10 partner systems run warm under sustained
  prefill; log it and know your normal.
- Graceful shutdown ordering: agents, then Core, then models, then the Gate.
  The Gate goes last so that in-flight approved requests complete.

---

## Administration

Administered from macOS over SSH. The workstation is not part of the
production runtime — nothing in AEGIS may depend on it being awake.

## Home automation

Runs on **separate hardware** (see Part 16, revised, and Part 00 T5). One box
should not both hold CF&S client correspondence and open the front door.
