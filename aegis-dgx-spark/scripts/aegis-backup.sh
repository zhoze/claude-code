#!/usr/bin/env bash
# AEGIS backup — three tiers. See docs/part-06-storage.md.
#
#   aegis-backup --tier local|offbox|offsite
#   aegis-backup --verify
#   aegis-backup --restore-test [dest]
#
#   local    hourly rsync to /aegis/backups      (accidental deletion)
#   offbox   nightly to NAS over the LAN         (machine failure)
#   offsite  weekly, age-encrypted, remote       (fire, theft)
#
# Models are deliberately NOT backed up: large, and re-downloadable (their
# identities and checksums live in config/models.toml, which IS backed up).
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "run with sudo"; exit 1; }

STAMP=$(date +%Y%m%d-%H%M%S)
LOCAL=/aegis/backups
OFFBOX="${AEGIS_OFFBOX:-}"            # e.g. nas.local:/volume1/aegis
OFFSITE="${AEGIS_OFFSITE:-}"
AGE_RECIPIENT="${AEGIS_AGE_RECIPIENT:-}"

SOURCES=(/aegis/config /aegis/knowledge /aegis/vectordb /aegis/logs /aegis/state)

usage() { sed -n '2,14p' "$0"; exit 1; }

command -v rsync >/dev/null || { echo "rsync missing — run bootstrap/02-packages.sh"; exit 1; }

current_snapshot() {
  # `current` must be a SYMLINK to the latest snapshot. The old version
  # pre-created it as a real directory, which broke deduplication, --verify
  # and --restore-test all at once. Migrate that state if we find it.
  if [[ -d "$LOCAL/current" && ! -L "$LOCAL/current" ]]; then
    echo "migrating: $LOCAL/current is a real directory (old bug) — removing" >&2
    rm -rf "$LOCAL/current"
  fi
  [[ -L "$LOCAL/current" ]] && readlink -f "$LOCAL/current" || true
}

do_local() {
  install -d -m0700 "$LOCAL"
  local prev; prev="$(current_snapshot)"
  for src in "${SOURCES[@]}"; do
    [[ -d "$src" ]] || continue
    local name; name="$(basename "$src")"
    if [[ -n "$prev" && -d "$prev/$name" ]]; then
      rsync -a --delete --numeric-ids --link-dest="$prev/$name" \
            "$src/" "$LOCAL/$STAMP/$name/"
    else
      rsync -a --delete --numeric-ids "$src/" "$LOCAL/$STAMP/$name/"
    fi
  done
  ln -sfn "$LOCAL/$STAMP" "$LOCAL/current"
  # Prune snapshots older than 7 days — but NEVER the one `current` points
  # to, whatever its age (D4 retention beyond 7 days is the off-box tiers'
  # job).
  local keep; keep="$(readlink -f "$LOCAL/current")"
  find "$LOCAL" -maxdepth 1 -type d -name '20*' -mtime +7 2>/dev/null | while read -r d; do
    [[ "$(readlink -f "$d")" == "$keep" ]] || rm -rf "$d"
  done
  # Manifest covers EVERYTHING in the snapshot, recursively. A verify that
  # only checked config/* was reporting "verified" over unhashed data.
  (cd "$LOCAL/$STAMP" && find . -type f ! -name MANIFEST.sha256 -print0 \
     | xargs -0 -r sha256sum > MANIFEST.sha256)
  echo "local snapshot: $LOCAL/$STAMP ($(wc -l < "$LOCAL/$STAMP/MANIFEST.sha256") files)"
  echo "NOTE: same disk, same machine. This is not a backup — it is undo."
}

do_offbox() {
  [[ -n "$OFFBOX" ]] || { echo "set AEGIS_OFFBOX=host:/path (in /etc/default/aegis-backup)"; exit 1; }
  command -v ssh >/dev/null || { echo "ssh missing"; exit 1; }
  rsync -a --delete -e ssh "${SOURCES[@]}" "$OFFBOX/$STAMP/"
  echo "off-box: $OFFBOX/$STAMP"
}

do_offsite() {
  [[ -n "$OFFSITE" && -n "$AGE_RECIPIENT" ]] || {
    echo "set AEGIS_OFFSITE and AEGIS_AGE_RECIPIENT (in /etc/default/aegis-backup)"; exit 1; }
  command -v age >/dev/null || { echo "age missing — run bootstrap/02-packages.sh"; exit 1; }
  # Staged under /aegis/backups (0700 root), not world-readable /tmp. No
  # shred theatrics: on flash + ext4 journaling, overwrite-based erasure is
  # fiction — the honest control is that the staging dir is root-only and
  # the archive is already encrypted for the offsite recipient.
  local staging="$LOCAL/tmp"
  install -d -m0700 "$staging"
  local out="$staging/aegis-$STAMP.tar.gz.age"
  trap 'rm -f "$out"' EXIT
  tar -C /aegis -cz config knowledge vectordb state \
    | age -r "$AGE_RECIPIENT" > "$out"
  rsync -a "$out" "$OFFSITE/"
  rm -f "$out"
  trap - EXIT
  echo "off-site: $OFFSITE/aegis-$STAMP.tar.gz.age"
  echo "The decryption key must NOT be on this machine."
}

do_verify() {
  local latest; latest="$(current_snapshot)"
  [[ -n "$latest" ]] || { echo "no current snapshot — run --tier local first"; exit 1; }
  [[ -f "$latest/MANIFEST.sha256" ]] || { echo "no manifest in $latest"; exit 1; }
  (cd "$latest" && sha256sum -c MANIFEST.sha256 --quiet) \
    && echo "checksums verified: $latest ($(wc -l < "$latest/MANIFEST.sha256") files)"
}

do_restore_test() {
  local dest="${1:-/tmp/aegis-restore-test}"
  local latest; latest="$(current_snapshot)"
  [[ -n "$latest" && -d "$latest/knowledge" ]] || { echo "no knowledge/ in current snapshot"; exit 1; }
  install -d -m0700 "$dest"
  rsync -a "$latest/knowledge/" "$dest/knowledge/"
  echo "restored to $dest"
  echo
  echo "Now do the part people skip:"
  echo "  diff -r $dest/knowledge /aegis/knowledge"
  echo "  then rebuild the vector db from the restored documents"
  echo "  (sudo -u aegis-ht ... -m aegis_core.ingest --reindex-all) and"
  echo "  confirm retrieval still works. The vector db is what silently"
  echo "  fails to restore, because it depends on the embedding model."
  echo
  echo "Record the RTO you actually achieved in docs/DECISIONS.md."
}

case "${1:-}" in
  --tier)         case "${2:-local}" in
                    local) do_local ;; offbox) do_offbox ;; offsite) do_offsite ;;
                    *) usage ;;
                  esac ;;
  --verify)       do_verify ;;
  --restore-test) do_restore_test "${2:-}" ;;
  *)              usage ;;
esac
