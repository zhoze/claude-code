# AEGIS Part 01 — Vision & Principles (revised)

*Content preserved. What changed: the success criteria were unfalsifiable, so
they have been given thresholds. A criterion that cannot fail is not a
criterion.*

---

## Purpose

A personal, self-hosted AI platform acting as a trusted digital chief of
staff: reasoning, planning, remembering, automating, coordinating specialised
agents, operating locally, protecting private data, and continuing to function
without internet access.

The system should evolve for years without a redesign.

## Mission

A secure, modular, expandable AI ecosystem where local intelligence is
preferred over cloud intelligence, and cloud services are used only when
explicitly permitted — *permitted* meaning a typed command, not a
configuration flag.

## Core principles

1. **Local first** — cloud is an exception requiring an approval token.
2. **Privacy first** — enforced by kernel and OS, never by model behaviour.
3. **Offline first** — the house and the assistant work during an outage.
4. **Security by default** — every ambiguous default resolves restrictive.
5. **Modular architecture** — layers replaceable independently.
6. **Vendor independence** — *restated:* avoid lock-in where switching cost
   is high. The original absolute form was contradicted by Parts 03 and 07,
   which mandate DGX Spark and Tailscale specifically. Both are fine choices;
   the principle just needed to be achievable.
7. **Explainability** — every consequential decision is recorded with its
   reason. Implemented as the decision log (Part 10) and the egress audit log
   (Part 07), not as an aspiration.
8. **Long-term maintainability**.

## Tiebreaker

> **Privacy > Reliability > Simplicity > Performance > Convenience**

This is the most useful sentence in the specification. Every hard decision
later resolves against it, in this order. Notably it says a system that is
*down* is better than one that *leaks*.

## Success criteria — with thresholds

| # | Criterion | Passes when |
|---|---|---|
| 1 | Answers from local knowledge | 20 questions over your own documents answered with correct citations; no cloud call |
| 2 | Automates repetitive work | ≥3 recurring tasks run unattended for 30 days |
| 3 | Controls local infrastructure | Home state queries answered; safety-critical writes still require confirmation |
| 4 | Orchestrates agents | ≥2 agents used in one request, with the decision log showing why |
| 5 | Helps with professional work | One real CF&S task completed end to end that you would otherwise have done by hand |
| 6 | Survives outages | WAN unplugged 60 min: local Q&A and local automations continue; degradation is reported, not silent |
| 7 | Holds the boundary | `verify-egress.sh` reports zero failures continuously for 30 days, including after agents are added |

Criterion 7 is not optional and not last in importance. It is the one that
makes the other six safe to have.

## Non-goals

Stated so they do not creep in: replacing your judgement on client-facing
commercial decisions; acting autonomously on production CF&S systems (read
scope only); or being available to anyone but you.
