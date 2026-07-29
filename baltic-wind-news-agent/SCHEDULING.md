# Scheduling: create the Routine from the claude.ai UI

**A Routine dispatching this workflow is the reliable trigger. It must be
created in the claude.ai Routines UI, not with the `create_trigger` MCP tool.**
GitHub Actions `schedule:` is only a fallback here — it fires late and
sometimes not at all.

## The mistake that cost two days of reports

The first Routine was created with `mcp__…__create_trigger` from a Claude Code
session. That call returned a warning which was noted and then not acted on:

> this trigger stores no MCP connectors, so the sessions it fires will run
> without connector (`mcp__<server>__*`) tools. Connectors on triggers created
> via this tool are limited to those the calling session itself holds […] If
> the routine needs connectors, create it from a session that holds them, or
> **ask the user to create it from the claude.ai routines UI**.

So its firings had no GitHub connector — hence "no GitHub access available",
and hence the later 403 on `git push` too. The wrong conclusion was drawn from
that (that *no* Routine can trigger this workflow). It is wrong because
`estonian-law-daily.yml` in this same repo is dispatched by a Routine **every
single day at 07:00:4x UTC**, without fail, including on the day the Baltic
wind Routine was failing. Routines in this account can dispatch workflows
perfectly well — they just need the connector grant that only UI creation (or
a session that itself holds the connector) provides.

## How the repo's agents actually get triggered

| Workflow | Primary trigger | Punctuality |
| --- | --- | --- |
| `estonian-law-daily.yml` | Routine → `workflow_dispatch`, 07:00 UTC | Exact, every day |
| `err-news-daily.yml` | `schedule:` cron | Fires, ~45 min–2 h late |
| `baltic-wind-daily.yml` | **needs a UI-created Routine** | cron alone: unreliable |

Evidence for "unreliable": on 2026-07-29 this workflow got **zero** scheduled
runs between 06:30 and 11:00 UTC even though ten cron entries covered that
window, while `err-news-daily.yml` (two entries) fired normally. GitHub
documents that high-frequency schedules are throttled and that scheduled runs
can be dropped under load, so the cron list here is deliberately kept to two.

## The Routine to create

In claude.ai → Routines, create a routine named
**"Baltic wind news report (workdays 09:30 Tallinn)"** with cron
`30 6 * * 1-5` (UTC) and this prompt:

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

- **Actions UI / API:** `workflow_dispatch`. `force: true` bypasses both the
  09:30 guard and the once-per-day guard, so it *will* send a second report
  the same day; `force: false` respects them.
- **With push access:** commit a timestamp to
  `baltic-wind-news-agent/trigger/run-request.txt` on `main`.
