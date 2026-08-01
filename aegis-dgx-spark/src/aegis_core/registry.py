"""
AEGIS agent registry.

Loads agent manifests and refuses the ones that contradict themselves.

The important function here is `_validate`. A manifest that declares
`trust_zone = "high"` alongside `network = true` is not a warning — it is a
refusal. That combination is precisely the exfiltration path the whole
architecture exists to close, and the moment it becomes a warning is the
moment someone ships it at 23:00 to make a demo work.

The registry is a second line of defence. nftables already denies `aegis-ht`
any route to the Gate, so a contradictory manifest would fail at runtime
anyway. Catching it at load time means it fails with a sentence you can read
instead of a timeout you have to debug.
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("aegis.registry")

AGENT_DIR = Path("/aegis/config/agents")

# The two zones. There is deliberately no third; see docs/part-11-agents.md.
ZONES = {"low", "high"}

# Which uid systemd must run each zone as. Kept here so a mismatch between
# the manifest and the unit file is detectable.
ZONE_UID = {"low": "aegis-lt", "high": "aegis-ht"}

# Paths a zone may ever be granted. A manifest asking for anything outside
# its zone's set is rejected, so a typo cannot quietly widen access.
# Scratch is split per zone (/aegis/scratch/{lt,ht}) — a shared scratch
# would be a data channel between the zones.
ZONE_READABLE = {
    "low": {"/aegis/scratch", "/aegis/scratch/lt", "/aegis/config/agents"},
    "high": {"/aegis/knowledge", "/aegis/vectordb", "/aegis/scratch/ht",
             "/aegis/config/agents"},
}


class ManifestError(ValueError):
    """A manifest that must not be loaded. Never downgraded to a warning."""


@dataclass(frozen=True)
class AgentManifest:
    name: str
    version: str
    description: str
    trust_zone: str
    model_role: str = "general"
    tools: tuple[str, ...] = ()
    read: tuple[str, ...] = ()
    write: tuple[str, ...] = ()
    network: bool = False
    memory_gib: int = 8
    timeout_seconds: int = 120
    max_tool_calls: int = 12
    source: Path | None = field(default=None, compare=False)

    @property
    def run_as(self) -> str:
        return ZONE_UID[self.trust_zone]

    @property
    def may_reach_gate(self) -> bool:
        """High trust never reaches the Gate, regardless of what it asks for."""
        return self.trust_zone == "low" and self.network


def load(path: Path) -> AgentManifest:
    with path.open("rb") as fh:
        raw = tomllib.load(fh)

    try:
        agent = raw["agent"]
    except KeyError:
        raise ManifestError(f"{path.name}: no [agent] section") from None

    caps = raw.get("capabilities", {})
    perms = raw.get("permissions", {})
    limits = raw.get("limits", {})

    manifest = AgentManifest(
        name=agent["name"],
        version=agent.get("version", "0.0.0"),
        description=agent.get("description", ""),
        trust_zone=agent.get("trust_zone", "low"),   # default restrictive
        model_role=agent.get("model_role", "general"),
        tools=tuple(caps.get("tools", ())),
        read=tuple(perms.get("read", ())),
        write=tuple(perms.get("write", ())),
        network=bool(perms.get("network", False)),
        memory_gib=int(limits.get("memory_gib", 8)),
        timeout_seconds=int(limits.get("timeout_seconds", 120)),
        max_tool_calls=int(limits.get("max_tool_calls", 12)),
        source=path,
    )
    _validate(manifest)
    return manifest


def _validate(m: AgentManifest) -> None:
    if m.trust_zone not in ZONES:
        raise ManifestError(
            f"{m.name}: trust_zone {m.trust_zone!r} is not one of {sorted(ZONES)}. "
            "There is no medium zone — pick the restrictive one."
        )

    # THE check. Everything else in this function is hygiene; this is the
    # security property.
    if m.trust_zone == "high" and m.network:
        raise ManifestError(
            f"{m.name}: declares trust_zone='high' with network=true. "
            "A high-trust agent holds business data and must have no route out. "
            "If it needs external data, have Core fetch it through a low-trust "
            "agent and pass the result across as quoted data. "
            "See docs/part-02-architecture.md, L3."
        )

    allowed = ZONE_READABLE[m.trust_zone]
    for path in m.read + m.write:
        if not any(path == a or path.startswith(a + "/") for a in allowed):
            raise ManifestError(
                f"{m.name}: zone '{m.trust_zone}' may not access {path!r}. "
                f"Permitted roots: {sorted(allowed)}"
            )

    if m.trust_zone == "low":
        for path in m.read + m.write:
            if path.startswith("/aegis/knowledge"):
                raise ManifestError(
                    f"{m.name}: a low-trust agent cannot read /aegis/knowledge. "
                    "Low trust reads untrusted input; giving it your documents "
                    "recreates the injection-to-exfiltration path."
                )

    if m.memory_gib < 1 or m.memory_gib > 32:
        raise ManifestError(f"{m.name}: memory_gib {m.memory_gib} outside 1–32")
    if m.timeout_seconds < 1 or m.timeout_seconds > 3600:
        raise ManifestError(f"{m.name}: timeout_seconds outside 1–3600")


def load_all(directory: Path = AGENT_DIR) -> dict[str, AgentManifest]:
    """Load every manifest. One bad manifest fails the load — it does not get
    skipped. A silently absent agent is harder to notice than a startup error.
    """
    agents: dict[str, AgentManifest] = {}
    if not directory.is_dir():
        log.warning("no agent directory at %s", directory)
        return agents

    for path in sorted(directory.glob("*.toml")):
        manifest = load(path)
        if manifest.name in agents:
            raise ManifestError(f"duplicate agent name {manifest.name!r} in {path}")
        agents[manifest.name] = manifest
        log.info("agent %-14s zone=%-4s uid=%-9s network=%s",
                 manifest.name, manifest.trust_zone, manifest.run_as, manifest.network)
    return agents


def check_unit_matches(m: AgentManifest, unit_dir: Path = Path("/etc/systemd/system")) -> list[str]:
    """Cross-check the manifest against the installed systemd drop-in.

    Catches the case where a manifest says 'high' but nobody installed
    zone.conf, so systemd is running it as aegis-lt with full Gate access.
    The manifest would look correct in review while the process is in the
    wrong zone — exactly the kind of gap that survives an audit.
    """
    problems = []
    dropin = unit_dir / f"aegis-agent@{m.name}.service.d" / "zone.conf"
    if m.trust_zone == "high":
        if not dropin.exists():
            problems.append(
                f"{m.name}: manifest says high trust but {dropin} is missing — "
                f"systemd will run it as aegis-lt (with Gate access). Run "
                f"/aegis/bin/aegis-zone-sync and `systemctl daemon-reload`."
            )
        elif "User=aegis-ht" not in dropin.read_text():
            problems.append(f"{m.name}: {dropin} does not set User=aegis-ht")
    else:
        # The reverse drift matters too: a low-trust agent that somehow got
        # a high-trust drop-in would run with business-data access AND a
        # proxy-configured environment history.
        if dropin.exists() and "User=aegis-ht" in dropin.read_text():
            problems.append(
                f"{m.name}: manifest says LOW trust but {dropin} sets "
                f"User=aegis-ht. Run /aegis/bin/aegis-zone-sync."
            )
    return problems


if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    try:
        agents = load_all()
    except ManifestError as exc:
        print(f"REJECTED: {exc}", file=sys.stderr)
        sys.exit(1)

    issues = [p for m in agents.values() for p in check_unit_matches(m)]
    for issue in issues:
        print(f"MISMATCH: {issue}", file=sys.stderr)
    print(f"\n{len(agents)} agent(s) loaded, {len(issues)} unit mismatch(es)")
    sys.exit(1 if issues else 0)
