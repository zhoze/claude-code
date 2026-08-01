# AEGIS Part 12 — Memory (revised)

*Content preserved. "Layers" renamed to **tiers** to remove the collision with
Part 02's architectural layers, and the deletion promise is given a mechanism.*

---

## Tiers

| Tier | Lifetime | Store | Backed up |
|---|---|---|---|
| 1 Working | one request | in-process | no |
| 2 Conversation | configurable, default 30 days | SQLite, `/aegis/config/state/` | yes |
| 3 Long-term | indefinite, versioned | SQLite + git-tracked markdown | yes |
| 4 Knowledge | indefinite | vector db + `/aegis/knowledge` | yes |

Tier 3 in markdown under git is a deliberate choice: durable facts about you
should be readable and editable without the system running, and every change
should be diffable. When AEGIS is down, `git log` still tells you what it
believed.

## What is stored

Store what improves future assistance: preferences, stable project facts,
recurring workflows, corrections.

**Not stored, ever** — even if you say it in passing: health details,
financial account numbers, credentials, government IDs, or anything about
third parties that they did not consent to. The same discipline applies to
what is *retrieved*: sensitive material is not surfaced in a context where you
did not raise it.

## Retention

```toml
[memory.retention]
conversation_days = 30
working           = "discard_on_completion"
long_term         = "explicit_only"      # nothing enters tier 3 automatically
```

`explicit_only` matters. Automatic promotion of conversational detail into
permanent memory is how a system accumulates a dossier you never agreed to.

## Deletion — the mechanism the original promised

```bash
scripts/aegis-forget.py --subject "acme-tender-2025" --dry-run
scripts/aegis-forget.py --subject "acme-tender-2025" --confirm
```

Propagates to: source document → extracted text → chunks → embeddings →
metadata row → conversation references → decision-log references → tier 3
entries. Emits a manifest of what it removed.

**What it cannot reach: backups.** A deletion is not complete until the last
backup containing it ages out. That means your stated retention period *is*
your honest answer to an erasure request. Write it down (Part 06) rather than
implying deletion is instant.

## Quality maintenance

Monthly: duplicates, contradictions, orphaned references, stale facts. Present
findings for your decision — do not let the system silently rewrite what it
believes about you.

## GDPR note

You are in Estonia, ingesting CF&S correspondence that contains personal data
about client contacts. Local processing does not remove the obligation. Before
phase 4 ingests client material, record: the lawful basis, the retention
period, and the erasure procedure above. Worth a short conversation with
whoever handles data protection at CF&S — not because AEGIS is risky, but
because "I built a searchable index of client correspondence" is a sentence
that should not first be said during an audit.
