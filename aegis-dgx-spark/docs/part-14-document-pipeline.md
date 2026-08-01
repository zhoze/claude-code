# AEGIS Part 14 — Document Processing Pipeline (revised)

*Pipeline stages preserved. Added: where each stage runs, which trust zone
owns it, and what happens when a stage fails.*

---

## Which zone runs this

**Ingestion is a high-trust operation** — it reads confidential documents and
writes the index. It runs as `aegis-ht`, with no network access.

The consequence: **the pipeline cannot fetch anything.** No remote OCR, no
cloud classification, no downloading a linked resource. If a stage needs
something from outside, it fails and asks you. That is the correct behaviour —
a document processor with network access is an exfiltration channel wearing a
uniform.

## Stages

```
import → validate → integrity → extract → OCR? → metadata
       → classify → chunk → embed → index → verify
```

| Stage | Tool | Fails to |
|---|---|---|
| Validate | format, size, readability | quarantine |
| Integrity | sha256, duplicate check | skip, log |
| Extract | pdftotext, python-docx, openpyxl | quarantine |
| OCR | local only, when no text layer | quarantine |
| Metadata | title, author, dates, language | proceed with defaults |
| Classify | classification + trust zone | **`confidential`, high zone** |
| Chunk | ~800 tokens, structure-aware | quarantine |
| Embed | local embedding model | retry once, then quarantine |
| Index | Qdrant upsert | retry, then quarantine |
| Verify | retrieve own content back | flag for review |

**Classification fails closed.** An unrecognised document becomes confidential
and high-zone. Over-restricting costs you an inconvenience; under-restricting
costs you the thing this system exists to protect.

## Quarantine

`/aegis/knowledge/.quarantine/`, with a reason file per item. Reviewed by you,
not auto-retried. Documents that fail repeatedly are usually telling you
something — a corrupt scan, an unsupported format, or a file that is not what
its extension claims.

## Versioning

Never overwrite an original. Each version keeps: original bytes, extracted
text, metadata, embeddings, and processing history. On change: detect by
sha256, reprocess only affected chunks, refresh embeddings, preserve history.

## Injection surface

Every ingested document is untrusted input (Part 00, T1). Two consequences
built into the pipeline:

1. Extracted text is stored and retrieved as **data**, wrapped in the
   `<untrusted_content>` delimiters from Part 11 whenever it reaches a model.
2. The pipeline has no network and no tool access, so a document that
   successfully injects the extraction stage has nothing to injectitself into.

Watch specifically for: white-on-white text in PDFs, text in document
metadata fields, and content in image alt-text — all standard carriers.

## Running it

```bash
# One document
sudo -u aegis-ht /aegis/venv/bin/python -m aegis_core.ingest \
     --path /aegis/knowledge/inbox/contract.pdf

# (A directory-watching path unit is a phase 4+ build; until it ships,
# ingestion is an explicit command — which also matches the "you decide
# what enters the knowledge base" stance.)

# Rebuild everything after an embedding-model change
sudo -u aegis-ht PYTHONPATH=/aegis/src /aegis/venv/bin/python -m aegis_core.ingest --reindex-all
```
