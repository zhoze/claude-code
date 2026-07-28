# Scheduling: why this agent runs on cron alone

**Short version: no Claude Code Remote Routine can trigger this workflow.**
The Routine "Baltic wind news report (workdays 09:30 Tallinn)" was deleted
on 2026-07-28 because it could only produce error summaries. GitHub Actions
`schedule:` carries the daily report by itself. Do not add a Routine back
unless the push-access gap below has been closed first.

## What was tried, and why each failed

| Approach | Result |
| --- | --- |
| Routine session calls `mcp__github__*` to dispatch the workflow | **Fails.** Routine-fired sessions get only claude.ai connectors (Gmail, Drive, Slack, …). No GitHub connector is attached and `gh` is not installed. Observed 2026-07-28. |
| Routine session pushes a commit to `trigger/run-request.txt` | **Fails.** The session clones fine (read access) but `git push` returns **403 on every branch**, including a throwaway branch. Read access ≠ write access here. Observed 2026-07-28. |
| GitHub Actions `schedule:` cron | **Works**, with no credentials needed from any session. Fires late (2–4 h behind the cron time in this repo) but fires. |

Both session-based routes are dead ends unless the environment's git proxy is
granted push scope for `zhoze/claude-code` in Routine-spawned sessions. That is
an account/environment setting, not something the agent can arrange.

## How the daily report actually gets sent

`.github/workflows/baltic-wind-daily.yml` schedules an attempt every 30 minutes
from 06:30 to 11:00 UTC on weekdays. Because GitHub delays this repo's crons
unpredictably, the fan of entries maximises the chance that one lands near the
09:30 Europe/Tallinn target. Exactly one of them does real work:

- the local-time guard skips anything before 09:30 Tallinn (so in winter, when
  06:30 UTC is only 08:30 local, the early entries no-op), and
- `state/last_run.json` records the date of the last report sent, so every
  attempt after the day's first successful one exits in ~20 seconds.

The result is at most one report per weekday, arriving somewhere between 09:30
and mid-morning Tallinn time, with no Claude session in the loop.

## Triggering a run by hand

- **Actions UI / API:** run the workflow via `workflow_dispatch` (`force: true`
  bypasses both the time guard and the once-per-day guard — it *will* send a
  second report the same day).
- **From a session with push access:** write a timestamp to
  `baltic-wind-news-agent/trigger/run-request.txt` on `main` and push; the
  `push` trigger picks it up. Without `force` this still honours the guards, so
  it is a no-op once the day's report has gone out.
