# AEGIS Part 09 — Model Routing (revised)

*The original made this layer the privacy enforcement point. It is not, and
cannot be — it lives above the trust boundary. It now **requests** egress; the
Gate **grants** it.*

---

## What changed

| Original | Revised |
|---|---|
| Router enforces privacy policy | Router *classifies*; the Gate enforces |
| "Cloud used when the user allows" | Cloud requires a token the user typed |
| Privacy is an input to routing | Privacy is a property of the network |

The router is still worth having — it picks the right model and degrades
sensibly. It is simply no longer load-bearing for security.

## Routing inputs, in order

1. **Data classification of the request** — see below
2. Task type
3. Required capability (context length, vision, language)
4. Current memory headroom
5. Latency target
6. Model availability
7. Cost, for cloud

## Data classification

Assigned by Core at intake and carried with the request. This is the first
thing computed, because it constrains everything after.

| Class | Examples | May route to |
|---|---|---|
| `confidential` | CF&S client material, contracts, rates | **local only** — never eligible for cloud, regardless of approval |
| `personal` | your notes, calendar, mail | local; cloud only with per-request approval |
| `public` | general knowledge, published material | local first; cloud on approval |

`confidential` is a hard stop in the router **and** unreachable at the network
layer, because high-trust agents cannot reach the Gate. Two independent
mechanisms for the case that matters most.

## Policy

```toml
[routing]
default = "local"
confidential_may_leave = false     # changing this does not grant access;
                                   # nftables still refuses.

[routing.fallback]
# When the preferred model is unavailable:
#   1. retry once
#   2. any compatible local model
#   3. cloud, if class permits AND an approval token exists
#   4. tell the user plainly that no suitable model is available
on_unavailable = ["retry", "local_alt", "cloud_if_approved", "report"]
```

Step 4 matters. The failure mode to avoid is a system that quietly gives a
worse answer from a smaller model without saying so.

## Cloud request flow

```
Core classifies → router selects cloud → Core asks you
    ↓
you: aegis-approve api.anthropic.com --reason "..."
    ↓
Core issues request via HTTPS_PROXY=127.0.0.1:3128
    ↓
Gate: allowlist ✓  uid ✓  token ✓ (burned) → tunnel opens
    ↓
audit.jsonl records host, uid, reason, bytes out
```

No token, no request. Not "no token, a warning."

## Cost control

Cloud spend is not a routing preference, it is a budget:

```toml
[routing.cost]
monthly_cap_eur   = 50
alert_at_percent  = 70
on_cap_exceeded   = "local_only"    # degrade, do not silently keep spending
```

Set a hard spend cap at the provider too. The first visible symptom of a
leaked or looping key is usually the invoice.

## Monitoring

Per route: success rate, latency p50/p95, tokens in/out, and refusals by
reason. A rising `no_approval` count means either a broken workflow or an
agent trying something it should not — both worth knowing about.
