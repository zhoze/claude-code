#!/usr/bin/env python3
"""AEGIS compare — stage 2 of the install flow. READ-ONLY except for its own
reports (and config/site-overrides.env when --write-overrides is given).

Loads scan-report/facts.json and checks every assumption the installation
code makes against what the machine actually is. Each finding names the file
in this repository where the assumption lives, so you (or Claude Code — see
scan/REVIEW-WITH-CLAUDE.md) can amend the code before anything runs.

    ./scan/aegis-compare.sh                     # report only
    ./scan/aegis-compare.sh --write-overrides   # also derive site-overrides.env

install.sh refuses to run unless this script's last run PASSED against the
current facts.json (recorded in scan-report/compare-status.json).
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def add(self, status: str, check: str, detail: str, where: str) -> None:
        self.rows.append((status, check, detail, where))

    @property
    def failed(self) -> int:
        return sum(1 for r in self.rows if r[0] == FAIL)

    @property
    def warned(self) -> int:
        return sum(1 for r in self.rows if r[0] == WARN)

    def render(self) -> str:
        out = [f"AEGIS compare — {time.strftime('%Y-%m-%dT%H:%M:%S%z')}", ""]
        for status, check, detail, where in self.rows:
            out.append(f"[{status}] {check}")
            if detail:
                out.append(f"       {detail}")
            if where:
                out.append(f"       assumption: {where}")
        out.append("")
        out.append(f"{len(self.rows)} checks: "
                   f"{len(self.rows) - self.failed - self.warned} pass, "
                   f"{self.warned} warn, {self.failed} fail")
        if self.failed:
            out.append("")
            out.append("FAILs must be resolved before install.sh will run.")
            out.append("Amend the code or the machine, re-run the scan, then "
                       "re-run this compare. See scan/REVIEW-WITH-CLAUDE.md.")
        return "\n".join(out) + "\n"


def load_facts(scan_dir: Path) -> dict:
    path = scan_dir / "facts.json"
    if not path.exists():
        print(f"no {path} — run ./scan/aegis-scan.sh first", file=sys.stderr)
        sys.exit(2)
    return json.loads(path.read_text())


def check_platform(f: dict, r: Report) -> None:
    arch = f.get("arch")
    r.add(PASS if arch == "aarch64" else FAIL, "architecture is aarch64",
          f"found {arch!r}; every install path (llama.cpp build, Qdrant "
          "binary) targets aarch64", "bootstrap/00-preflight.sh")

    ver = f.get("os", {}).get("version_id")
    r.add(PASS if ver == "24.04" else WARN, "Ubuntu 24.04 base",
          f"found {f.get('os', {}).get('pretty_name')}",
          "bootstrap/00-preflight.sh, docs/part-04")

    r.add(PASS if f.get("os", {}).get("dgx_release_present") else WARN,
          "DGX OS release file present",
          "absent — this may not be a DGX Spark; the GPU and memory "
          "assumptions in docs/part-03 will not hold elsewhere",
          "docs/part-03-hardware.md")

    sysd = f.get("systemd_version") or 0
    r.add(PASS if sysd >= 254 else FAIL, "systemd >= 254",
          f"found {sysd}; ImportCredential= in "
          "systemd/aegis-egress-gate.service needs 254+",
          "systemd/aegis-egress-gate.service")

    py = tuple(int(x) for x in f.get("python_version", "0.0.0").split("."))
    r.add(PASS if py >= (3, 11) else FAIL, "python >= 3.11 (tomllib)",
          f"found {f.get('python_version')}",
          "src/egress_gate/gate.py, bootstrap/00-preflight.sh")


def check_gpu(f: dict, r: Report) -> None:
    g = f.get("gpu", {})
    if not g.get("nvidia_smi_present"):
        r.add(FAIL, "nvidia-smi present",
              "not found — no NVIDIA driver stack; phase 6 (llama.cpp CUDA "
              "build) cannot proceed. Override only for a deliberate "
              "CPU-only install.", "bootstrap/06-models.sh")
    elif not g.get("gpus"):
        r.add(FAIL, "GPU visible to nvidia-smi",
              "nvidia-smi returns no device — driver/firmware problem; "
              "fix before installing", "bootstrap/00-preflight.sh")
    else:
        r.add(PASS, "GPU visible", "; ".join(g["gpus"]), "")

    nodes = [n["path"] for n in g.get("device_nodes", [])]
    tegra = [n for n in nodes if "/dev/nvgpu" in n or "nvhost" in n
             or n == "/dev/nvmap"]
    discrete = [n for n in nodes if n.startswith("/dev/nvidia")]
    style = ("tegra (/dev/nvgpu)" if tegra else
             "discrete (/dev/nvidia*)" if discrete else "none detected")
    r.add(PASS if (tegra or discrete) else WARN,
          "GPU device node style identified", f"{style}. The aegis-llm unit "
          "deliberately does not use DeviceAllow= so either style works; "
          "recorded for the record.",
          "systemd/aegis-llm.service")

    render_groups = sorted({n["group"] for n in g.get("device_nodes", [])
                            if not n.get("dir")} - {"?", "root"})
    if render_groups:
        r.add(PASS, "GPU node groups for SupplementaryGroups",
              f"device nodes owned by groups: {render_groups}; aegis-llm "
              "gets SupplementaryGroups=video render (amend if your nodes "
              "use a different group)", "systemd/aegis-llm.service")


def check_memory(f: dict, r: Report) -> None:
    mem = f.get("hardware", {}).get("mem_total_gib") or 0
    r.add(PASS if mem >= 100 else WARN, "unified memory >= 100 GiB",
          f"found {mem} GiB; the part-03 budget (80 GiB LLM ceiling) assumes "
          "a 128 GiB Spark. --write-overrides derives a proportional cap.",
          "config/llm.env, systemd/aegis-llm.service, docs/part-03")

    free = f.get("storage", {}).get("root_free_gb") or 0
    r.add(PASS if free >= 150 else WARN, "root filesystem >= 150 GB free",
          f"found {free} GB; a 32B-class GGUF is ~20-25 GB, a 70B-class "
          "~40-45 GB, plus backups and logs", "bootstrap/00-preflight.sh")


def check_users(f: dict, r: Report) -> None:
    present = f.get("users", {}).get("system_users", {})
    for name, why in [
        ("systemd-resolve", "upstream DNS; without this rule the whole box "
                            "loses name resolution when the policy applies"),
        ("systemd-timesync", "NTP; TLS to every allowlisted host depends on "
                             "a sane clock"),
        ("_apt", "apt drops privileges to _apt for downloads; without this "
                 "rule unattended-upgrades dies silently"),
    ]:
        r.add(PASS if name in present else FAIL,
              f"system user {name} exists",
              f"{why}. The nftables template grants this uid a specific "
              "allowance and `nft -f` refuses to load if the user is "
              "missing.", "config/nftables/aegis.nft.tmpl")

    admin = f.get("users", {}).get("invoking_user", "")
    r.add(PASS if admin and admin != "root" else WARN,
          "admin user identified",
          f"scan invoked by {admin!r}; the template's @@ADMIN_USER@@ rules "
          "will be rendered for this account (confirmed again at install)",
          "config/nftables/aegis.nft.tmpl, bootstrap/05-egress-gate.sh")

    stale = f.get("users", {}).get("aegis_users", {})
    if stale:
        r.add(WARN, "prior AEGIS service users present",
              f"{sorted(stale)} already exist — a previous install; "
              "bootstrap/01 is idempotent but review scan-report for "
              "leftover state", "bootstrap/01-users-and-dirs.sh")


def check_network(f: dict, r: Report) -> None:
    n = f.get("network", {})
    r.add(PASS if n.get("resolved_active") else WARN,
          "systemd-resolved active",
          "the nftables template assumes resolved handles DNS; if another "
          "resolver runs under a different uid, amend the DNS rules",
          "config/nftables/aegis.nft.tmpl")
    r.add(PASS if n.get("ntp_synchronized") else WARN, "clock synchronised",
          "fix before TLS work", "bootstrap/00-preflight.sh")
    r.add(PASS if n.get("tailscale_installed") else WARN,
          "tailscale installed",
          "the firewall carries Tailscale rules and docs/part-04 assumes "
          "remote admin over it; install and test a LAN fallback before "
          "the egress phase, or accept LAN-only admin",
          "config/nftables/aegis.nft.tmpl, docs/part-04")
    if n.get("can_resolve"):
        r.add(PASS, "DNS resolution works pre-install", "", "")
    else:
        r.add(FAIL, "DNS resolution works pre-install",
              "huggingface.co did not resolve; fix networking before "
              "installing", "")


def check_firewall(f: dict, r: Report) -> None:
    fw = f.get("firewall", {})
    if fw.get("has_aegis_table"):
        r.add(WARN, "an AEGIS nftables table is already loaded",
              "re-install will atomically replace `table inet aegis` and "
              "leave other tables alone", "bootstrap/05-egress-gate.sh")
    elif fw.get("ruleset_nonempty"):
        r.add(WARN, "a non-AEGIS nftables ruleset is loaded",
              f"tables: {fw.get('tables')}; the AEGIS policy only replaces "
              "its own table (it does NOT flush ruleset), but its default-"
              "deny output chain still applies to all traffic — review "
              "coexistence before phase 5", "config/nftables/aegis.nft.tmpl")
    else:
        r.add(PASS, "firewall state clean", "no ruleset loaded", "")
    if fw.get("ufw_active"):
        r.add(FAIL, "ufw is active",
              "ufw and the AEGIS policy will fight over verdicts; disable "
              "ufw (or consciously merge) before install",
              "config/nftables/aegis.nft.tmpl")


def check_docker(f: dict, r: Report) -> None:
    d = f.get("docker", {})
    if d.get("present"):
        r.add(WARN if d.get("containers_running") else PASS,
              "Docker present",
              f"active={d.get('active')}, running={d.get('containers_running')}. "
              "The AEGIS policy no longer flushes Docker's tables, and its "
              "forward chain carves out docker0/br-* — but container egress "
              "from non-root uids inside containers still crosses the "
              "default-deny output chain. Review if you rely on containers.",
              "config/nftables/aegis.nft.tmpl (forward chain)")


def check_ports(f: dict, r: Report) -> None:
    from aegis_scan import AEGIS_PORTS  # same directory
    used = {}
    for l in f.get("listeners", []):
        addr, port = l.get("addr", ""), l.get("port")
        if port in AEGIS_PORTS and not l.get("process", "").startswith(
                ("users:((\"gate", "users:((\"llama", "users:((\"qdrant")):
            used.setdefault(port, []).append(f"{addr} {l.get('process', '')}")
    for port, whos in sorted(used.items()):
        r.add(FAIL, f"port {port} is free for AEGIS ({AEGIS_PORTS[port]})",
              f"in use by: {'; '.join(whos)[:120]} — stop it or change the "
              "AEGIS port in config/", "config/egress.toml, config/llm.env, "
              "config/qdrant.yaml")
    if not used:
        r.add(PASS, "AEGIS ports 3128/8000/8001/6333/8080 are free", "", "")


def check_storage_security(f: dict, r: Report) -> None:
    r.add(PASS if f.get("storage", {}).get("luks_present") else WARN,
          "disk encryption present (decision D1)",
          "no LUKS volume detected. docs/part-04 D1: if DGX OS was installed "
          "unencrypted, encrypting requires a reinstall. install.sh will ask "
          "you to acknowledge this in writing before proceeding.",
          "docs/part-04-os-baseline.md, docs/DECISIONS.md")


def check_remnants(f: dict, r: Report) -> None:
    ae = f.get("aegis", {})
    if ae.get("aegis_dir_exists") or ae.get("installed_units"):
        r.add(WARN, "prior AEGIS installation state present",
              f"/aegis exists: {ae.get('aegis_dir_exists')}; units: "
              f"{ae.get('installed_units')}; install-state: "
              f"{ae.get('install_state') or 'none'}. install.sh resumes "
              "idempotently, but review before re-running phase 5.",
              "install.sh")
    else:
        r.add(PASS, "clean box — no prior AEGIS state", "", "")


def derive_overrides(f: dict) -> str:
    mem = f.get("hardware", {}).get("mem_total_gib") or 128
    # LLM cgroup backstop: leave >= 24 GiB for OS + vectordb + everything
    # else, and never exceed the part-03 spirit (~80% of the pool).
    llm_max = max(16, min(int(mem) - 24, int(mem * 0.8)))
    admin = f.get("users", {}).get("invoking_user", "")
    nodes = [n["path"] for n in f.get("gpu", {}).get("device_nodes", [])]
    style = ("tegra" if any("/dev/nvgpu" in n for n in nodes)
             else "discrete" if any(n.startswith("/dev/nvidia")
                                    for n in nodes) else "unknown")
    apt_hosts = " ".join(f.get("apt", {}).get("source_hosts", []))
    return (
        "# Generated by scan/aegis-compare.sh --write-overrides.\n"
        "# Reviewed values derived from THIS machine's scan. Sourced by the\n"
        "# bootstrap scripts; edit deliberately, then re-run the compare.\n"
        f"AEGIS_SITE_ADMIN_USER={admin}\n"
        f"AEGIS_SITE_MEM_TOTAL_GIB={int(mem)}\n"
        f"AEGIS_SITE_LLM_MEMORYMAX={llm_max}G\n"
        f"AEGIS_SITE_GPU_NODE_STYLE={style}\n"
        f"# apt hosts observed on this box (for allowlist review only —\n"
        "# apt egress rides the _apt uid rule, not the Gate):\n"
        f"AEGIS_SITE_APT_HOSTS=\"{apt_hosts}\"\n"
    )


def main() -> int:
    args = sys.argv[1:]
    scan_dir = REPO / "scan-report"
    for i, a in enumerate(args):
        if a == "--scan-dir" and i + 1 < len(args):
            scan_dir = Path(args[i + 1])
    facts = load_facts(scan_dir)

    r = Report()
    check_platform(facts, r)
    check_gpu(facts, r)
    check_memory(facts, r)
    check_users(facts, r)
    check_network(facts, r)
    check_firewall(facts, r)
    check_docker(facts, r)
    check_ports(facts, r)
    check_storage_security(facts, r)
    check_remnants(facts, r)

    text = r.render()
    print(text)
    (scan_dir / "compare-report.txt").write_text(text)

    facts_hash = hashlib.sha256(
        (scan_dir / "facts.json").read_bytes()).hexdigest()
    status = {
        "facts_sha256": facts_hash,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "checks": len(r.rows),
        "warned": r.warned,
        "failed": r.failed,
        "ok": r.failed == 0,
    }
    (scan_dir / "compare-status.json").write_text(json.dumps(status, indent=2))

    if "--write-overrides" in args:
        out = REPO / "config" / "site-overrides.env"
        out.write_text(derive_overrides(facts))
        print(f"site overrides written to {out} — review before install")

    return 1 if r.failed else 0


if __name__ == "__main__":
    sys.exit(main())
