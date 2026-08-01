#!/usr/bin/env bash
# AEGIS phase 02 — packages. Minimal by design; every entry needs a reason.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib.sh
. "$SRC/bootstrap/lib.sh"
require_root

PKGS=(
  nftables            # egress enforcement
  python3-venv        # aegis services run in a venv, not system python
  python3-pip
  acl                 # occasional forensic use; the install itself uses none
  jq                  # audit log inspection
  curl ca-certificates
  git                 # llama.cpp checkout (phase 06)
  rsync               # code install (phase 03) and backup tier 1/2 (phase 08)
  age                 # backup tier 3 encryption (phase 08)
  openssh-client      # backup tier 2 transport
  build-essential     # llama.cpp build (phase 06); grace-blackwell wheels do not exist
  cmake               # llama.cpp build
  libcurl4-openssl-dev # llama.cpp default build links libcurl
  auditd              # who did what, part-05
  logrotate
  smartmontools       # disk health, part-03 reliability
  unattended-upgrades # security patches only; see config below
)

verify() {
  local rc=0
  for p in nftables rsync age build-essential cmake jq; do
    dpkg -s "$p" >/dev/null 2>&1 || { echo "package $p missing"; rc=1; }
  done
  systemctl is-enabled --quiet nftables || { echo "nftables not enabled"; rc=1; }
  exit $rc
}
[[ "${1:-}" == "--verify" ]] && verify

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends "${PKGS[@]}"

# Security updates only. Feature upgrades on a 24/7 inference box should be
# deliberate, not automatic. (The _apt uid rule in the firewall template is
# what keeps this working after phase 05 — apt's downloaders do not run as
# root.)
cat > /etc/apt/apt.conf.d/51aegis-unattended <<'CONF'
Unattended-Upgrade::Allowed-Origins {
    "${distro_id}:${distro_codename}-security";
};
Unattended-Upgrade::Automatic-Reboot "false";
CONF

systemctl enable --now nftables auditd
echo "packages installed. Deliberately absent: docker-compose stacks, a"
echo "desktop environment, and anything that opens a port you did not choose."
