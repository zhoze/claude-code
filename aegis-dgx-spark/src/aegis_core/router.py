"""
AEGIS model router.

Classifies a request, picks a model, and — when the answer is "cloud" —
*requests* egress rather than granting it.

This module deliberately holds no security-critical authority. It runs above
the trust boundary (L6 in docs/part-02-architecture.md), so anything it
decides can be wrong without the system leaking:

  * It refuses to route `confidential` data to cloud. Good hygiene, clear
    errors, not a security control.
  * The actual control is that high-trust agents have no route to the Gate,
    and that the Gate demands a token this process cannot mint.

If you find yourself adding a security check here, ask whether it belongs in
nftables or the Gate instead. A component must not enforce a limit on itself.
"""

from __future__ import annotations

import logging
import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

log = logging.getLogger("aegis.router")

CONFIG_PATH = Path("/aegis/config/routing.toml")
MODELS_PATH = Path("/aegis/config/models.toml")


class Classification(str, Enum):
    CONFIDENTIAL = "confidential"   # CF&S client material — local only, always
    PERSONAL = "personal"           # your notes, calendar, mail
    PUBLIC = "public"               # general knowledge

    @property
    def may_leave(self) -> bool:
        return self is not Classification.CONFIDENTIAL


# Signals that force CONFIDENTIAL. Deliberately broad: over-classifying costs
# you a local-only answer, under-classifying costs you the thing this system
# protects. Tune by adding, not removing.
_CONFIDENTIAL_HINTS = [
    re.compile(p, re.I) for p in (
        r"\bCF&?S\b", r"\bclient\b", r"\bcontract\b", r"\btender\b",
        r"\brate card\b", r"\bquotation\b", r"\bconsignee\b", r"\bshipper\b",
        r"\bBOL\b", r"\bbill of lading\b", r"\bCMR\b", r"\bpermit (fee|cost)\b",
        r"\bmargin\b", r"\binvoice\b", r"\bNDA\b",
    )
]


@dataclass(frozen=True)
class Decision:
    classification: Classification
    model_name: str
    local: bool
    reason: str
    needs_approval: bool = False
    approval_host: str | None = None

    def as_log(self) -> dict:
        return {
            "classification": self.classification.value,
            "model": self.model_name,
            "local": self.local,
            "reason": self.reason,
            "needs_approval": self.needs_approval,
        }


@dataclass
class RoutingConfig:
    default: str = "local"
    confidential_may_leave: bool = False
    monthly_cap_eur: float = 50.0
    on_cap_exceeded: str = "local_only"
    cloud_host: str = "api.anthropic.com"

    @classmethod
    def load(cls, path: Path = CONFIG_PATH) -> "RoutingConfig":
        if not path.exists():
            log.warning("no routing config at %s — defaulting to local-only", path)
            return cls()
        with path.open("rb") as fh:
            raw = tomllib.load(fh).get("routing", {})
        cfg = cls(
            default=raw.get("default", "local"),
            confidential_may_leave=bool(raw.get("confidential_may_leave", False)),
            monthly_cap_eur=float(raw.get("cost", {}).get("monthly_cap_eur", 50.0)),
            on_cap_exceeded=raw.get("cost", {}).get("on_cap_exceeded", "local_only"),
            cloud_host=raw.get("cloud_host", "api.anthropic.com"),
        )
        if cfg.confidential_may_leave:
            # Loud, because someone edited a file to turn this on and it does
            # not do what they think it does.
            log.error(
                "confidential_may_leave=true in %s. This does NOT grant access: "
                "high-trust agents have no route to the Gate and nftables will "
                "still refuse. Set it back to false and stop being confusing.",
                path,
            )
        return cfg


def classify(text: str, source_hint: str | None = None) -> Classification:
    """Classify a request. Fails toward CONFIDENTIAL when uncertain."""
    if source_hint == "knowledge":
        # Anything retrieved from the document store is confidential by
        # construction — that store is what high trust exists to protect.
        return Classification.CONFIDENTIAL

    for pattern in _CONFIDENTIAL_HINTS:
        if pattern.search(text):
            return Classification.CONFIDENTIAL

    if source_hint in ("mail", "calendar", "notes"):
        return Classification.PERSONAL

    return Classification.PUBLIC


def route(
    text: str,
    *,
    cfg: RoutingConfig | None = None,
    source_hint: str | None = None,
    local_available: bool = True,
    needs_capability_local_lacks: bool = False,
    spend_this_month_eur: float = 0.0,
) -> Decision:
    cfg = cfg or RoutingConfig.load()
    klass = classify(text, source_hint)

    if klass is Classification.CONFIDENTIAL:
        if not local_available:
            return Decision(klass, "none", True,
                            "confidential and no local model available — refusing "
                            "rather than degrading to cloud")
        return Decision(klass, "aegis-general", True,
                        "confidential: local only, never eligible for cloud")

    if local_available and not needs_capability_local_lacks:
        return Decision(klass, "aegis-general", True, "local model is sufficient")

    if spend_this_month_eur >= cfg.monthly_cap_eur:
        log.warning("cloud spend cap reached (%.2f EUR) — degrading to local",
                    spend_this_month_eur)
        return Decision(klass, "aegis-general", True,
                        f"cloud spend cap reached; {cfg.on_cap_exceeded}")

    if not klass.may_leave:
        return Decision(klass, "aegis-general", True, "classification forbids egress")

    return Decision(
        klass, "cloud", False,
        "task exceeds local capability" if needs_capability_local_lacks
        else "no local model available",
        needs_approval=True,
        approval_host=cfg.cloud_host,
    )


def approval_instruction(decision: Decision) -> str:
    """What to tell the operator. Never attempt to mint the token."""
    if not decision.needs_approval:
        return ""
    return (
        f"This needs {decision.approval_host}, and I cannot approve it myself.\n"
        f"Run:  aegis-approve {decision.approval_host} --reason \"<why>\"\n"
        f"Then ask again. The token is single-use and expires in 120 seconds."
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(message)s")
    samples = [
        ("what is the capital of Estonia", None),
        ("summarise the CF&S rate card for Riga", None),
        ("what did the blade yard contract say about stacking", "knowledge"),
        ("draft a reply to the mail from yesterday", "mail"),
    ]
    for text, hint in samples:
        d = route(text, source_hint=hint)
        print(f"{text[:44]:<46} -> {d.classification.value:<12} "
              f"{'local' if d.local else 'CLOUD':<6} {d.reason}")
