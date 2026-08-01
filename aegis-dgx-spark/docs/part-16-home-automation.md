# AEGIS Part 16 — Home Automation (revised)

*Substantially changed. Home automation moves to separate hardware.*

---

## Why it is not on the Spark

The original combined two things: Part 03's "prefer fewer, more powerful
systems" and Part 16's control of locks, gates and alarms. The result is one
machine that holds CF&S client correspondence **and** opens your front door,
reachable from your phone.

The blast radius of a single compromise is your business data and your house.
IoT devices are, as a class, the least trustworthy software you will ever run,
and putting them on the same host as confidential material fails Part 01's
tiebreaker on the first line.

## Architecture

```
   IoT devices ── Zigbee/Z-Wave/Matter/MQTT ──┐
                                              ▼
                              Home Assistant  (separate box: Pi 5 or NUC)
                                              │  narrow authenticated API,
                                              │  LAN or Tailscale only
                                              ▼
                                      AEGIS `home` agent  (low trust)
                                              │
                                          AEGIS Core
```

Hardware: a Pi 5 or small NUC. This is not an expensive addition, and it buys
a real security boundary plus something better — the house keeps working when
you reboot the Spark.

## Permissions

| Class | Examples | AEGIS may |
|---|---|---|
| Read | temperature, occupancy, energy, device health | freely |
| Comfort write | lights, climate, media, irrigation | act on request |
| **Safety-critical** | locks, gates, alarms, high-power equipment | **propose only** |

Safety-critical actions require confirmation through a channel **independent
of AEGIS** — the Home Assistant app, a physical control, or a push
notification you approve. Not a voice command. Not an AEGIS confirmation
prompt, because if AEGIS is compromised it can fake its own prompt.

The `home` agent holds a Home Assistant token scoped to read plus comfort
writes only. It has no credential that can open a lock. That is the actual
control; the policy table above is documentation of it.

## Offline operation

Automations live in Home Assistant, not in AEGIS. When the Spark is off, the
house works. When the internet is down, both work.

AEGIS adds reasoning on top — "why is the workshop cold", "has the gate been
opened while I was away" — and its absence degrades convenience, never safety.
This is Part 02 design rule 6 in practice.

## Network

Home Assistant on its own LAN segment or VLAN. The Spark reaches it on one
port. IoT devices reach neither the Spark nor the internet unless individually
justified — most of them have no legitimate reason to phone home, and the ones
that insist should be replaced.

## Phase

Phase 10. Last, deliberately. It is the most fun and the least important, and
building it early is how the security boundary gets negotiated away.
