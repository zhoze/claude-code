#!/usr/bin/env bash
# AEGIS bootstrap library — shared by install.sh and every phase script.
# Sourced, never executed.

AEGIS_DEFAULTS=/etc/default/aegis
AEGIS_STATE_DIR=/var/lib/aegis
AEGIS_STATE_FILE="$AEGIS_STATE_DIR/install-state"

log()  { printf '\033[36m[aegis]\033[0m %s\n' "$*"; }
ok()   { printf '  \033[32mok\033[0m   %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m %s\n' "$*"; }
die()  { printf '\033[31m[aegis] ERROR:\033[0m %s\n' "$*" >&2; exit 1; }

require_root() { [[ $EUID -eq 0 ]] || die "run with sudo"; }

# Repo root, from any phase script.
repo_root() { cd "$(dirname "${BASH_SOURCE[1]}")/.." && pwd; }

# ---------------------------------------------------------------------------
# Site configuration.
#
# ADMIN_USER is resolved ONCE (install.sh confirms it) and persisted to
# /etc/default/aegis. Phase scripts call load_site and read $ADMIN_USER from
# there — no more passing env vars to two different scripts and no more
# sed of a literal username into an installed file.
# config/site-overrides.env (written by scan/aegis-compare.sh
# --write-overrides) is layered on top when present.
# ---------------------------------------------------------------------------

load_site() {
  local repo="${1:-}"
  # shellcheck disable=SC1090
  [[ -f "$AEGIS_DEFAULTS" ]] && . "$AEGIS_DEFAULTS"
  if [[ -n "$repo" && -f "$repo/config/site-overrides.env" ]]; then
    # shellcheck disable=SC1090
    . "$repo/config/site-overrides.env"
  fi
  ADMIN_USER="${ADMIN_USER:-${AEGIS_SITE_ADMIN_USER:-${SUDO_USER:-}}}"
}

save_site_admin() {
  local user="$1"
  install -d -m 0755 "$(dirname "$AEGIS_DEFAULTS")"
  if [[ -f "$AEGIS_DEFAULTS" ]] && grep -q '^ADMIN_USER=' "$AEGIS_DEFAULTS"; then
    sed -i "s/^ADMIN_USER=.*/ADMIN_USER=${user}/" "$AEGIS_DEFAULTS"
  else
    printf 'ADMIN_USER=%s\n' "$user" >> "$AEGIS_DEFAULTS"
  fi
}

require_admin_user() {
  [[ -n "${ADMIN_USER:-}" ]] || die "ADMIN_USER unset — run ./install.sh (it confirms and persists it), or set ADMIN_USER=name"
  id "$ADMIN_USER" >/dev/null 2>&1 || die "no such user: $ADMIN_USER"
  [[ "$ADMIN_USER" =~ ^[a-z_][a-z0-9_-]*$ ]] || die "ADMIN_USER '$ADMIN_USER' is not a sane unix username"
}

# ---------------------------------------------------------------------------
# Install state — one line per completed phase; survives reboots and dropped
# SSH sessions so ./install.sh resumes where it left off.
# ---------------------------------------------------------------------------

phase_mark_done() {
  install -d -m 0755 "$AEGIS_STATE_DIR"
  local phase="$1"
  grep -q "^${phase}=" "$AEGIS_STATE_FILE" 2>/dev/null && \
    sed -i "/^${phase}=/d" "$AEGIS_STATE_FILE"
  printf '%s=done@%s\n' "$phase" "$(date -Is)" >> "$AEGIS_STATE_FILE"
}

phase_is_done() {
  grep -q "^$1=done" "$AEGIS_STATE_FILE" 2>/dev/null
}

# ---------------------------------------------------------------------------
# Prompts
# ---------------------------------------------------------------------------

confirm() {  # confirm "question" -> 0 on yes
  local ans
  read -rp "$1 [y/N] " ans
  [[ "$ans" == "y" || "$ans" == "Y" ]]
}

confirm_typed() {  # confirm_typed "prompt" "REQUIRED-WORD" [timeout]
  local ans prompt="$1" word="$2" timeout="${3:-0}"
  if (( timeout > 0 )); then
    read -t "$timeout" -rp "$prompt" ans || return 1
  else
    read -rp "$prompt" ans
  fi
  [[ "$ans" == "$word" ]]
}

# Sync a source tree into place: idempotent, removes strays, never nests.
install_tree() {  # install_tree SRC DEST
  install -d -m 0755 "$2"
  rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' "$1"/ "$2"/
}
