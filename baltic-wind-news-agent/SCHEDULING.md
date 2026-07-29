# Scheduling: how the daily report actually gets triggered

**Primary trigger is the `workflow_run` chain** — this workflow runs right
after the Estonian law digest or the ERR News digest finishes, because both of
those get triggered every day while this workflow's own cron keeps being
skipped. It needs no connector, no push access and no Claude session, so it
works today. `schedule:` and `workflow_dispatch` remain as extra paths, and
`state/last_run.json` means only the first one to land each day sends anything.

## Why not a Routine (yet)

A Routine dispatching this workflow would give exact 09:30 delivery, and that
is what `estonian-law-daily.yml` uses. It is **not currently possible here**,
for a reason that took three wrong turns to pin down:

1. The first Routine was created with the `create_trigger` MCP tool, whose
   response warned that such triggers store no connectors and said to create
   it from the claude.ai UI instead. That warning was not acted on, so its
   firings had no GitHub tools.
2. The conclusion drawn from that failure — "no Routine can ever trigger this"
   — was wrong, and the git-push fallback built on it also 403'd.
3. The actual blocker, found 2026-07-29 by inspecting `ListConnectors` from a
   Routine firing: **there is no GitHub connector on the account at all**
   (only Bigdata.com, Descript, FMP, Gmail, Google Calendar, Google Drive,
   Notion, Slack, Wolfram). A UI-created Routine cannot attach a connector
   that does not exist, so recreating the Routine alone fixes nothing.

To get exact 09:30 delivery, the order must be:
**claude.ai → Settings → Connectors → add GitHub**, *then* create the Routine
from the UI with the schedule and prompt below. Until the connector exists,
the `workflow_run` chain is what delivers the report.

## How this repo's agents get triggered

| Workflow | Trigger | Punctuality |
| --- | --- | --- |
| `estonian-law-daily.yml` | Routine → `workflow_dispatch` ~07:00 UTC | Exact, every day |
| `err-news-daily.yml` | `schedule:` cron | Fires daily, ~45 min–2 h late |
| `baltic-wind-daily.yml` | `workflow_run` off the two above | ~10:00–11:00 Tallinn |

This workflow's own `schedule:` produced **zero** runs on both 2026-07-28 and
2026-07-29 — including a day when ten cron entries covered 06:30–11:00 UTC and
`err-news-daily.yml` (two entries) fired normally. GitHub documents that
scheduled runs are throttled and may be dropped under load. The cron list is
kept to two entries and treated as a bonus, never as the mechanism.

## The Routine to create

In claude.ai → Routines, create a routine named
**"Baltic wind news report (workdays 09:30 Tallinn)"**.

**Schedule.** If the UI accepts a plain-language schedule, prefer
*"every weekday at 9:30 AM"* in **Europe/Tallinn** — it tracks DST for you.
If it wants a cron expression, use `30 6 * * 1-5`, which is UTC and means
minute 30, hour 06, any day of month, any month, days Mon–Fri, i.e. **06:30
UTC on weekdays** = 09:30 Tallinn in summer (EEST). In winter (EET) that same
instant is 08:30 local, which is why step 1 of the prompt waits an hour rather
than reporting early; with a local-time schedule that step simply never fires.

**Prompt:**

---

Baltic wind energy news report run (workdays 09:30 Europe/Tallinn).

1. Run `TZ=Europe/Tallinn date`. If the local hour is 8 (winter time, EET), call `send_later` with delay_minutes 60 and message "Baltic wind news report run — continue from step 2." and stop this turn. If the local hour is 9 or later, continue.
2. Dispatch the workflow: `mcp__github__actions_run_trigger` with method `run_workflow`, owner `zhoze`, repo `claude-code`, workflow_id `baltic-wind-daily.yml`, ref `main`, inputs `{"force": false}`. Leaving force false keeps the once-per-day guard active, so this can never send a duplicate report.
3. Poll the new run with `mcp__github__actions_get` (method `get_workflow_run`) about every 2 minutes, in a background loop — never a foreground sleep. The job timeout is 40 minutes.
4. If it succeeds, end quietly with a one-line summary: the workflow emailed the report itself. If it fails, fetch the failing job logs with `mcp__github__get_job_logs`, diagnose, retry the dispatch at most once, and end with a summary of the failure so it surfaces as a notification.

---

If a firing ever reports that `mcp__github__*` tools are unavailable, the
Routine was created without the GitHub connector — recreate it from the UI
rather than trying to work around it in the prompt.

## Triggering a run by hand

- **Actions UI / API:** `workflow_dispatch`. `force` now defaults to **false**,
  so a plain "Run workflow" respects the 09:30 and once-per-day guards. It used
  to default to true, which is why 2026-07-28 received three identical reports
  (09:13, 09:31, 09:55 UTC). Tick `force` only when a duplicate or off-hours
  report is genuinely wanted.
- **With push access:** commit a timestamp to
  `baltic-wind-news-agent/trigger/run-request.txt` on `main`.
