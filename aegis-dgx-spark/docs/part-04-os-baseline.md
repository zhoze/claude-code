# AEGIS Part 04 — DGX OS Baseline (revised)

*Concrete. Every step is a command or a decision with a stated consequence.*

Target: NVIDIA DGX Spark, DGX OS 7 (Ubuntu 24.04 base, aarch64), preinstalled.

---

## Decision D1 — disk encryption *(make this before anything else)*

Part 00 T3 sets out the trade in full. Restated because you cannot defer it:

| Option | Protects against | Costs |
|---|---|---|
| No FDE | nothing | — |
| LUKS + passphrase at boot | drive theft **and** whole-machine theft | no unattended reboot; 24/7 requirement broken |
| LUKS + TPM unlock | drive theft only | machine unlocks itself for a thief |

**Recommended:** TPM unlock, plus a second LUKS volume for the most sensitive
material that is unlocked manually and only when needed.

Write your choice and reasoning into `docs/DECISIONS.md` with today's date.
The point is not which you pick; it is that in eight months you can see that
you picked.

```bash
# If DGX OS was installed without encryption, this requires a reinstall.
lsblk -o NAME,TYPE,MOUNTPOINT | grep crypt || echo "no LUKS volume present"

# Optional second volume for high-sensitivity data:
sudo cryptsetup luksFormat /dev/nvme0n1p5
sudo cryptsetup open /dev/nvme0n1p5 aegis-vault
sudo mkfs.ext4 /dev/mapper/aegis-vault
# Deliberately NOT in /etc/crypttab — manual unlock is the feature.
```

---

## First boot sequence

```bash
# 1. Identify the machine
sudo hostnamectl set-hostname aegis
sudo timedatectl set-timezone Europe/Tallinn
timedatectl set-ntp true

# 2. Update, in this order: firmware, OS, drivers
sudo apt update && sudo apt full-upgrade -y
sudo reboot

# 3. Confirm the GPU survived the upgrade
nvidia-smi
```

## Accounts

Root is for emergency recovery only — no login, no SSH, no daily use.

```bash
sudo passwd -l root                     # lock root password login
id "$USER" && groups "$USER"            # you should be in sudo
```

Service accounts are created in `bootstrap/01-users-and-dirs.sh`, not here.

## SSH

```bash
ssh-copy-id -i ~/.ssh/aegis_ed25519.pub you@aegis   # from the Mac, first
```

Then `/etc/ssh/sshd_config.d/10-aegis.conf`:

```
PasswordAuthentication no
PermitRootLogin no
KbdInteractiveAuthentication no
PubkeyAuthentication yes
AllowUsers <your-username>
X11Forwarding no
MaxAuthTries 3
ClientAliveInterval 300
```

```bash
sudo sshd -t && sudo systemctl reload ssh
```

**Do not close your current session until a second one connects.** Record the
host fingerprint (`ssh-keyscan aegis`) somewhere off-box.

## Tailscale

```bash
curl -fsSL https://tailscale.com/install.sh | sh    # read it first
sudo tailscale up --ssh=false --accept-routes=false
```

`--ssh=false` deliberately: Tailscale SSH is convenient and bypasses your
`sshd_config`. Two auth paths is one more than needed.

Then set up a local-LAN fallback and **test it with Tailscale stopped** —
Part 07 explains why that matters for an offline-first system.

## Validation before Part 05

```bash
nvidia-smi                                       # GPU present
timedatectl show -p NTPSynchronized --value      # yes
free -g                                          # ~128 GiB
ss -tlnp                                         # only sshd
journalctl -p err -b --no-pager | tail -20       # no critical errors
sudo smartctl -H /dev/nvme0n1                    # PASSED
```

Then run `bootstrap/00-preflight.sh`, which checks all of the above and a few
things you will forget.
