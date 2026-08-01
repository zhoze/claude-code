# AEGIS Part 06 — Storage & Backup (revised)

*The original deferred backup implementation three times and never wrote it.
It is here, with an RPO, an RTO, and a restore drill.*

---

## Layout

Created by `bootstrap/01-users-and-dirs.sh`. Ownership is the access-control
mechanism, not a convention.

| Path | Owner | Mode | Contents | Backed up |
|---|---|---|---|---|
| `/aegis/config` | `root:aegis-core` | 0750 | service config, allowlist | yes, hourly |
| `/aegis/models` | `aegis-llm` | 0755 | weights | no — re-downloadable |
| `/aegis/knowledge` | `aegis-ht` | 0750 | **source documents** | yes, hourly |
| `/aegis/vectordb` | `aegis-ht` | 0750 | embeddings, indexes | yes, daily |
| `/aegis/scratch` | `aegis-lt` | 0750 | low-trust workspace | no — disposable |
| `/aegis/logs` | mixed | 0750 | audit, core, agents | yes, daily |
| `/aegis/backups` | `root` | 0700 | local snapshots | → off-site |

Two rules that follow: **models are never backed up** (they are large and
re-downloadable), and **the vector db is never the source of truth** — it can
always be rebuilt from `/aegis/knowledge`, which is why the two have different
backup frequencies.

## Quotas and rotation

```bash
# Stop any one directory taking the box down.
sudo systemd-run --unit=aegis-scratch-reaper --on-calendar=daily \
  find /aegis/scratch -type f -mtime +7 -delete
```

`/etc/logrotate.d/aegis`:

```
/aegis/logs/*/*.log {
    daily
    rotate 30
    compress
    delaycompress
    missingok
    notifempty
    create 0640 root root
}

/aegis/logs/egress/audit.jsonl {
    weekly
    rotate 104          # two years — this is your evidence trail
    compress
    copytruncate        # never break the gate's open file handle
    create 0640 aegis-proxy aegis-proxy
}
```

`copytruncate` matters: rotating out from under the Gate's open handle would
silently stop auditing, and the Gate now denies egress when it cannot write.

## Backup

**RPO 1 hour** for config and knowledge, **24 hours** for the vector db and
logs. **RTO 4 hours** — the time to a working system from bare metal.

Three tiers:

1. **Local snapshot** — hourly rsync to `/aegis/backups`. Protects against
   accidental deletion. Not a backup: same disk, same machine.
2. **Off-box** — nightly to NAS or a second machine over the LAN.
3. **Off-site encrypted** — weekly, `age`-encrypted, to remote storage. The
   key is **not on this machine**.

```bash
sudo scripts/aegis-backup.sh --tier local     # by timer, hourly
sudo scripts/aegis-backup.sh --tier offsite   # by timer, weekly
```

## Restore drill — do this in phase 7, not after an incident

```bash
sudo scripts/aegis-backup.sh --verify              # checksums only
sudo scripts/aegis-backup.sh --restore-test /tmp/rt
diff -r /tmp/rt/knowledge /aegis/knowledge && echo "restore verified"
```

Then rebuild the vector db from the restored documents and confirm retrieval
still works. **A backup you have not restored from is a hypothesis**, and the
vector db is exactly the component that silently fails to restore because it
depends on the embedding model version.

Record each drill in `docs/DECISIONS.md` with the date and the RTO you
actually achieved, not the one you planned.

## Deletion

The hard case, promised by Part 12 and never specified. A deletion must
propagate to: the source document, its extracted text, its chunks, its
embeddings, the metadata row, the decision log references, **and every backup
tier**. `scripts/aegis-forget.py` does the first six; backups are handled by
retention expiry, which means a deletion is not complete until the last backup
containing it ages out. State that retention period explicitly — it is the
honest answer to a GDPR erasure request.
