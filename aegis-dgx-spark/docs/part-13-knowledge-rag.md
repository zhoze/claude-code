# AEGIS Part 13 — Knowledge Base & RAG (revised)

*Design principles preserved — they were sound. Added: concrete parameters,
Estonian-language handling, and the permission model.*

---

## Non-negotiable

**The original documents are the source of truth.** The vector database is a
disposable index that can always be rebuilt from `/aegis/knowledge`. This is
why the two have different backup frequencies (Part 06) and why a corrupt
index is an inconvenience rather than a data loss.

## Stack

| Component | Choice | Why |
|---|---|---|
| Vector db | Qdrant, local, bound to `127.0.0.1:6333` | Runs as a service, no cloud tier needed, good filtering |
| Embeddings | multilingual model via the local runtime | Must handle Estonian and Russian |
| Chunking | ~800 tokens, 100 overlap, structure-aware | Tune per corpus; contracts differ from email |
| Reranking | optional, phase 6 | Adds latency; add only if retrieval quality demands it |

**Test the embedding model on Estonian before committing to it.** Many
English-first models degrade badly on Estonian morphology, and you will not
notice from English-language test queries. Build a 20-question Estonian set
from real CF&S material and measure.

## Metadata

Every document records: title, author, source path, created, imported,
version, sha256, tags, language, **classification**, and **trust zone**.

Classification (`confidential` | `personal` | `public`) drives routing in Part
09. Trust zone controls which agents may retrieve it at all. Both are set at
ingestion, defaulting to the *most* restrictive when detection is uncertain.

## Retrieval

```
query → embed → search (filtered by zone + classification)
      → rank → assemble context → answer with citations
```

The permission filter runs **before** ranking, not after. Filtering after
means restricted content briefly enters the candidate set, and "briefly" is
enough for a bug to leak it.

Citations are mandatory. An answer without a source is a claim, and the whole
point of RAG here is traceability.

## Maintenance

- Re-index when the embedding model changes — embeddings from different models
  are not comparable, and mixing them silently degrades retrieval.
- Detect duplicates by sha256 at ingest.
- Check for broken references weekly.
- Record which model version produced each embedding, so a re-index knows what
  is stale.

## Verification

Build a fixed set of twenty questions with known answers over your own
documents (a yaml file of question/expected pairs is enough; an eval script
is a phase 4+ build — until it exists, run the questions by hand against
the documents agent and score them yourself).

Twenty questions with known answers over your own documents. This is Part 01
criterion 1, and it is also your regression test — run it after every model
change, chunking change, or re-index. Retrieval quality degrades quietly, and
without a fixed question set you will not notice until you are relying on a
wrong answer.
