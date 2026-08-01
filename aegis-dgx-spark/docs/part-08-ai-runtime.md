# AEGIS Part 08 — AI Runtime & Local Models (revised)

*Concrete model choices, a memory budget that is enforced rather than
requested, and a registry format.*

---

## Model selection for a 128 GiB unified-memory Spark

The Spark holds models a consumer GPU cannot and generates tokens more slowly
than one. Choose for capacity and quality, not throughput.

| Slot | Recommendation | Memory | Notes |
|---|---|---:|---|
| General reasoning | 70B-class, 4-bit quantised | ~45 GiB weights + KV | The resident model. Quantisation is not optional here. |
| Embeddings | multilingual, 1–2B | ~4 GiB | Must handle Estonian and Russian for CF&S material. Test this before committing — many English-first models are poor on Estonian. |
| Coding | on demand | ~20 GiB | Not resident. Load, use, unload. |
| Vision / OCR | on demand | ~8 GiB | Phase 4+. |
| STT / TTS | on demand | ~8 GiB | Phase 9. |

**Verify the exact model names and current best options at build time** —
this table will be stale within months. What will not go stale is the budget.

## Enforcement

The budget in Part 03 is enforced by systemd, not by intention:

```ini
MemoryHigh=72G      # reclaim pressure starts here
MemoryMax=80G       # hard ceiling; OOM-kill rather than swap
```

Swap on an inference host means the entire system stops responding, including
voice. Killing one service is strictly better. Confirm the cap works:

```bash
systemctl show aegis-llm -p MemoryMax -p MemoryCurrent
```

## Serving

```bash
sudo systemctl start aegis-llm
curl -s localhost:8000/v1/models | jq .

# Sustained-load check — run ~30 minutes of concurrent requests before
# declaring phase 3 done, e.g. four loops of:
while true; do
  curl -s localhost:8000/v1/chat/completions -H 'Content-Type: application/json' \
    -d '{"model":"aegis-general","messages":[{"role":"user","content":"Summarise the tradeoffs of unified memory in three paragraphs."}]}' >/dev/null
done
```

Watch during the run: `systemctl status aegis-llm` MemoryCurrent stays well
under MemoryMax, no swap in `vmstat 5`, GPU temperature stable, and no
restarts in `journalctl -u aegis-llm`.

## Model registry

`/aegis/config/models.toml`. Every model carries provenance, because "which
model produced this answer" is a question you will need to answer.

```toml
[[model]]
name         = "aegis-general"
path         = "/aegis/models/<model-dir>"
role         = "general"           # general|embedding|coding|vision|speech
context_len  = 32768
memory_gib   = 80
sha256       = "<checksum of the manifest>"
license      = "<license>"
source       = "<url>"
status       = "active"            # active|testing|retired
resident     = true
notes        = "Resident reasoning model. Evicting this degrades everything."
```

Rules: checksums recorded and verified on load; a model moves `testing` →
`active` only after the Part 01 criterion-1 question set is re-run against it;
retired models keep their entries so old decision-log lines stay
interpretable.

## Security

- Runs as `aegis-llm`, no shell, `ProtectSystem=strict`.
- Reaches only `huggingface.co` and its CDN through the Gate, `auto` class.
  Note in the allowlist that this reveals *which* models you run.
- Weights verified against recorded checksums before first load.
- Binds `127.0.0.1` only. Never `0.0.0.0` — that is one typo away from
  publishing an unauthenticated inference endpoint to the LAN.

## Eviction under pressure

Agents first, then on-demand specialists, then embeddings. The general model
and Core are never evicted; if they cannot be served the system reports
degraded rather than thrashing. This is Part 01's tiebreaker applied:
reliability of the honest failure over performance of the quiet one.
