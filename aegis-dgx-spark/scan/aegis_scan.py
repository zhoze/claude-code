#!/usr/bin/env python3
"""AEGIS system scan — stage 1 of the install flow. STRICTLY READ-ONLY.

Collects a complete profile of this machine into scan-report/facts.json
(machine-readable, consumed by aegis_compare.py) and scan-report/report.txt
(human-readable). It changes nothing on the system and writes nowhere except
the output directory.

Run it before anything else:

    ./scan/aegis-scan.sh                 # as your admin user
    sudo ./scan/aegis-scan.sh            # richer detail (ruleset, sshd config)

Then run ./scan/aegis-compare.sh and read scan/REVIEW-WITH-CLAUDE.md.
"""
from __future__ import annotations

import json
import os
import pwd
import grp
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

# Ports the AEGIS install will claim. Collisions are a compare-stage FAIL.
AEGIS_PORTS = {3128: "egress gate", 8000: "llm runtime", 8001: "embeddings",
               8080: "aegis core", 6333: "vector db"}

# System uids the nftables template grants specific allowances to.
REQUIRED_SYSTEM_USERS = ["systemd-resolve", "systemd-timesync", "_apt"]

AEGIS_USERS = ["aegis-core", "aegis-proxy", "aegis-llm", "aegis-lt",
               "aegis-ht", "aegis-vectordb"]


def run(cmd: list[str] | str, timeout: int = 20) -> str:
    """Run a read-only probe; empty string on any failure."""
    try:
        shell = isinstance(cmd, str)
        out = subprocess.run(cmd, shell=shell, capture_output=True, text=True,
                             timeout=timeout)
        return out.stdout.strip()
    except Exception:
        return ""


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def os_release() -> dict:
    facts: dict = {}
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                facts[k.lower()] = v.strip('"')
    except OSError:
        pass
    dgx = Path("/etc/dgx-release")
    facts["dgx_release_present"] = dgx.exists()
    if dgx.exists():
        try:
            facts["dgx_release"] = dgx.read_text().strip()[:2000]
        except OSError:
            facts["dgx_release"] = "(unreadable)"
    return facts


def systemd_version() -> int | None:
    out = run(["systemctl", "--version"])
    m = re.search(r"systemd (\d+)", out)
    return int(m.group(1)) if m else None


def gpu_facts() -> dict:
    facts: dict = {"nvidia_smi_present": have("nvidia-smi")}
    if facts["nvidia_smi_present"]:
        facts["gpus"] = [g for g in run(
            ["nvidia-smi", "--query-gpu=name,driver_version,memory.total",
             "--format=csv,noheader"]).splitlines() if g]
        facts["cuda_version"] = ""
        smi = run(["nvidia-smi"])
        m = re.search(r"CUDA Version:\s*([\d.]+)", smi)
        if m:
            facts["cuda_version"] = m.group(1)
    nvcc = run(["nvcc", "--version"])
    m = re.search(r"release ([\d.]+)", nvcc)
    facts["nvcc_version"] = m.group(1) if m else ""
    facts["nvcc_present"] = have("nvcc")

    # The actual device nodes matter more than any assumption. GB10 (Tegra
    # lineage) exposes /dev/nvgpu/*; discrete boards expose /dev/nvidia*.
    nodes = []
    for pattern in ("/dev/nvidia*", "/dev/nvgpu", "/dev/nvhost*", "/dev/nvmap",
                    "/dev/dri"):
        for p in sorted(Path("/dev").glob(pattern.removeprefix("/dev/"))):
            try:
                st = p.stat()
                group = grp.getgrgid(st.st_gid).gr_name
            except (OSError, KeyError):
                group = "?"
            nodes.append({"path": str(p), "group": group,
                          "dir": p.is_dir()})
            if p.is_dir():
                for sub in sorted(p.rglob("*")):
                    try:
                        group = grp.getgrgid(sub.stat().st_gid).gr_name
                    except (OSError, KeyError):
                        group = "?"
                    nodes.append({"path": str(sub), "group": group,
                                  "dir": sub.is_dir()})
    facts["device_nodes"] = nodes
    return facts


def memory_and_cpu() -> dict:
    facts: dict = {}
    try:
        meminfo = Path("/proc/meminfo").read_text()
        total_kb = int(re.search(r"MemTotal:\s+(\d+)", meminfo).group(1))
        avail_kb = int(re.search(r"MemAvailable:\s+(\d+)", meminfo).group(1))
        facts["mem_total_gib"] = round(total_kb / 1024 / 1024, 1)
        facts["mem_available_gib"] = round(avail_kb / 1024 / 1024, 1)
        swap_kb = int(re.search(r"SwapTotal:\s+(\d+)", meminfo).group(1))
        facts["swap_total_gib"] = round(swap_kb / 1024 / 1024, 1)
    except (OSError, AttributeError):
        pass
    facts["cpu_count"] = os.cpu_count()
    model = run("awk -F: '/model name/{print $2; exit}' /proc/cpuinfo")
    facts["cpu_model"] = model.strip()
    return facts


def storage() -> dict:
    facts: dict = {}
    try:
        st = os.statvfs("/")
        facts["root_free_gb"] = round(st.f_bavail * st.f_frsize / 1e9)
        facts["root_total_gb"] = round(st.f_blocks * st.f_frsize / 1e9)
    except OSError:
        pass
    facts["lsblk"] = run(["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,MOUNTPOINT"])
    facts["luks_present"] = "crypt" in run(["lsblk", "-o", "TYPE"])
    facts["smart_root"] = run("smartctl -H /dev/nvme0n1 2>/dev/null | tail -3")
    return facts


def users_and_groups() -> dict:
    facts: dict = {"system_users": {}, "aegis_users": {}, "groups": {}}
    for name in REQUIRED_SYSTEM_USERS + AEGIS_USERS:
        try:
            facts_key = ("aegis_users" if name.startswith("aegis-")
                         else "system_users")
            facts[facts_key][name] = pwd.getpwnam(name).pw_uid
        except KeyError:
            pass
    for g in ("aegis-operators", "video", "render", "docker", "sudo"):
        try:
            gr = grp.getgrnam(g)
            facts["groups"][g] = {"gid": gr.gr_gid, "members": gr.gr_mem}
        except KeyError:
            pass
    admin = os.environ.get("SUDO_USER") or os.environ.get("USER") or ""
    try:
        admin = admin or pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        pass
    facts["invoking_user"] = admin
    return facts


def network() -> dict:
    facts: dict = {}
    facts["hostname"] = socket.gethostname()
    facts["interfaces"] = run(["ip", "-brief", "addr"])
    facts["default_route"] = run("ip route show default")
    facts["resolved_active"] = run(
        ["systemctl", "is-active", "systemd-resolved"]) == "active"
    facts["resolv_conf"] = run("head -20 /etc/resolv.conf")
    facts["dns_stub_listeners"] = [
        l for l in run("ss -ulnH 'sport = 53' 2>/dev/null").splitlines() if l]
    facts["timesyncd_active"] = run(
        ["systemctl", "is-active", "systemd-timesyncd"]) == "active"
    facts["ntp_synchronized"] = "yes" in run(
        ["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    facts["tailscale_installed"] = have("tailscale")
    facts["tailscale_active"] = run(
        ["systemctl", "is-active", "tailscaled"]) == "active"
    facts["can_resolve"] = bool(run(
        "getent hosts huggingface.co 2>/dev/null | head -1"))
    return facts


def firewall() -> dict:
    facts: dict = {}
    ruleset = run("nft list ruleset 2>/dev/null", timeout=15)
    facts["nft_present"] = have("nft")
    facts["ruleset_nonempty"] = bool(ruleset.strip())
    facts["has_aegis_table"] = "table inet aegis" in ruleset
    facts["tables"] = [l.strip() for l in ruleset.splitlines()
                       if l.startswith("table ")]
    facts["ruleset_lines"] = len(ruleset.splitlines())
    facts["etc_nftables_conf"] = run("head -30 /etc/nftables.conf 2>/dev/null")
    facts["ufw_active"] = "active" in run("ufw status 2>/dev/null").lower()
    return facts


def docker_facts() -> dict:
    facts: dict = {"present": have("docker")}
    if facts["present"]:
        facts["active"] = run(["systemctl", "is-active", "docker"]) == "active"
        facts["containers_running"] = [
            c for c in run("docker ps --format '{{.Names}}' 2>/dev/null",
                           timeout=10).splitlines() if c]
        facts["nvidia_container_toolkit"] = have("nvidia-ctk") or have(
            "nvidia-container-runtime")
    return facts


def apt_sources() -> dict:
    hosts: set[str] = set()
    for path in [Path("/etc/apt/sources.list"),
                 *Path("/etc/apt/sources.list.d").glob("*")]:
        try:
            text = path.read_text()
        except OSError:
            continue
        for m in re.finditer(r"https?://([^/\s]+)", text):
            hosts.add(m.group(1))
    return {"source_hosts": sorted(hosts)}


def listeners() -> list[dict]:
    out = []
    for line in run("ss -tlnpH 2>/dev/null || ss -tlnH").splitlines():
        parts = line.split()
        if len(parts) >= 4:
            addr = parts[3]
            port = addr.rsplit(":", 1)[-1]
            out.append({"addr": addr,
                        "port": int(port) if port.isdigit() else None,
                        "process": parts[5] if len(parts) > 5 else ""})
    return out


def services() -> dict:
    enabled = run("systemctl list-unit-files --state=enabled --no-legend "
                  "--no-pager 2>/dev/null | awk '{print $1}'")
    return {"enabled": [s for s in enabled.splitlines() if s]}


def aegis_remnants() -> dict:
    facts: dict = {}
    facts["aegis_dir_exists"] = Path("/aegis").exists()
    if facts["aegis_dir_exists"]:
        facts["aegis_dir_entries"] = sorted(
            p.name for p in Path("/aegis").iterdir())[:40]
    facts["installed_units"] = sorted(
        p.name for p in Path("/etc/systemd/system").glob("aegis-*"))
    facts["install_state"] = run("cat /var/lib/aegis/install-state 2>/dev/null")
    facts["credstore_entries"] = sorted(
        p.name for p in Path("/etc/credstore.encrypted").glob("*")
    ) if Path("/etc/credstore.encrypted").is_dir() else []
    return facts


def ssh_facts() -> dict:
    cfg = run("sshd -T 2>/dev/null")   # root only; empty otherwise
    facts: dict = {"effective_config_available": bool(cfg)}
    for key in ("port", "passwordauthentication", "permitrootlogin"):
        m = re.search(rf"^{key} (.+)$", cfg, re.M)
        if m:
            facts[key] = m.group(1)
    return facts


def build_facts() -> dict:
    return {
        "scan": {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "ran_as_root": os.geteuid() == 0,
            "scanner_version": "1.0.0",
        },
        "os": os_release(),
        "kernel": run(["uname", "-r"]),
        "arch": run(["uname", "-m"]),
        "systemd_version": systemd_version(),
        "python_version": "%d.%d.%d" % sys.version_info[:3],
        "hardware": memory_and_cpu(),
        "gpu": gpu_facts(),
        "storage": storage(),
        "users": users_and_groups(),
        "network": network(),
        "firewall": firewall(),
        "docker": docker_facts(),
        "apt": apt_sources(),
        "listeners": listeners(),
        "services": services(),
        "aegis": aegis_remnants(),
        "ssh": ssh_facts(),
        "tools": {t: have(t) for t in
                  ("nft", "systemctl", "python3", "curl", "git", "ss", "jq",
                   "rsync", "age", "cmake", "gcc", "setfacl", "smartctl",
                   "systemd-creds", "docker", "tailscale")},
    }


def write_report(facts: dict, path: Path) -> None:
    L: list[str] = []
    a = L.append
    a(f"AEGIS system scan — {facts['scan']['timestamp']}")
    a(f"ran as root: {facts['scan']['ran_as_root']}"
      " (re-run with sudo for firewall/ssh detail)" if not
      facts['scan']['ran_as_root'] else "ran as root: True")
    a("")
    a("== Platform ==")
    a(f"  os        : {facts['os'].get('pretty_name', '?')}")
    a(f"  dgx       : {facts['os'].get('dgx_release_present')}")
    a(f"  kernel    : {facts['kernel']}   arch: {facts['arch']}")
    a(f"  systemd   : {facts['systemd_version']}   "
      f"python: {facts['python_version']}")
    hw = facts["hardware"]
    a(f"  memory    : {hw.get('mem_total_gib')} GiB total, "
      f"{hw.get('mem_available_gib')} GiB available, "
      f"swap {hw.get('swap_total_gib')} GiB")
    a(f"  cpu       : {hw.get('cpu_count')}x {hw.get('cpu_model', '?')}")
    a("")
    a("== GPU ==")
    g = facts["gpu"]
    a(f"  nvidia-smi: {g['nvidia_smi_present']}   "
      f"cuda: {g.get('cuda_version') or '?'}   nvcc: {g.get('nvcc_version') or 'absent'}")
    for gpu in g.get("gpus", []):
        a(f"  gpu       : {gpu}")
    a(f"  device nodes ({len(g['device_nodes'])}):")
    for n in g["device_nodes"][:30]:
        a(f"    {n['path']:<40} group={n['group']}")
    if len(g["device_nodes"]) > 30:
        a(f"    ... and {len(g['device_nodes']) - 30} more (see facts.json)")
    a("")
    a("== Storage ==")
    s = facts["storage"]
    a(f"  root free : {s.get('root_free_gb')} GB of {s.get('root_total_gb')} GB")
    a(f"  LUKS      : {s.get('luks_present')}")
    for line in s.get("lsblk", "").splitlines():
        a(f"    {line}")
    a("")
    a("== Users & groups relevant to the firewall ==")
    u = facts["users"]
    for name in REQUIRED_SYSTEM_USERS:
        present = name in u["system_users"]
        a(f"  {name:<18}: {'uid ' + str(u['system_users'][name]) if present else 'MISSING'}")
    a(f"  aegis users present: {sorted(u['aegis_users']) or 'none (clean box)'}")
    a(f"  invoking user      : {u['invoking_user']}")
    a("")
    a("== Network ==")
    n = facts["network"]
    a(f"  resolved active : {n['resolved_active']}   "
      f"ntp synced: {n['ntp_synchronized']}   can resolve: {n['can_resolve']}")
    a(f"  tailscale       : installed={n['tailscale_installed']} "
      f"active={n['tailscale_active']}")
    for line in n.get("interfaces", "").splitlines():
        a(f"    {line}")
    a("")
    a("== Firewall ==")
    f = facts["firewall"]
    a(f"  nft present     : {f['nft_present']}   "
      f"ruleset loaded: {f['ruleset_nonempty']} ({f['ruleset_lines']} lines)")
    a(f"  aegis table     : {f['has_aegis_table']}   ufw: {f['ufw_active']}")
    for t in f.get("tables", []):
        a(f"    {t}")
    a("")
    a("== Docker ==")
    d = facts["docker"]
    a(f"  present: {d.get('present')}   active: {d.get('active', False)}   "
      f"containers: {d.get('containers_running', [])}")
    a("")
    a("== Listening sockets ==")
    for l in facts["listeners"]:
        clash = AEGIS_PORTS.get(l["port"] or -1)
        a(f"  {l['addr']:<28} {l['process'][:50]}"
          + (f"   << COLLIDES with AEGIS {clash}" if clash else ""))
    a("")
    a("== Prior AEGIS state ==")
    ae = facts["aegis"]
    a(f"  /aegis exists   : {ae['aegis_dir_exists']} "
      f"{ae.get('aegis_dir_entries', '')}")
    a(f"  installed units : {ae['installed_units'] or 'none'}")
    a(f"  credstore       : {ae['credstore_entries'] or 'none'}")
    a("")
    a("== apt source hosts ==")
    for h in facts["apt"]["source_hosts"]:
        a(f"  {h}")
    a("")
    a("Next: ./scan/aegis-compare.sh   (checks these facts against the install code)")
    path.write_text("\n".join(L) + "\n")


def main() -> int:
    out_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("scan-report")
    out_dir.mkdir(parents=True, exist_ok=True)
    facts = build_facts()
    (out_dir / "facts.json").write_text(json.dumps(facts, indent=2,
                                                   default=str))
    write_report(facts, out_dir / "report.txt")
    print((out_dir / "report.txt").read_text())
    print(f"facts written to {out_dir}/facts.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
