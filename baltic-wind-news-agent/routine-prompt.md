# Routine prompt — "Baltic wind news report (workdays 09:30 Tallinn)"

Claude Code Remote Routine `trig_01XXVbs3RzvGMxKxVJLpaZna`, cron `30 6 * * 1-5`
(UTC), fresh session per firing.

**Why git and not GitHub MCP:** verified 2026-07-27/28 — sessions spawned by a
Routine firing get only claude.ai connectors (Gmail, Slack, Drive, …). There is
no GitHub connector and no `gh` CLI, so `mcp__github__*` and `gh` are both
unavailable. What those sessions *do* have is the repo clone at
`/home/user/claude-code` with authenticated git push. Hence: trigger by pushing
the trigger file, verify by watching for the run's state commit.

Paste the text below verbatim as the Routine's prompt (claude.ai → Routines).

---

Baltic wind energy news report run (workdays 09:30 Europe/Tallinn). IMPORTANT: this session has NO GitHub MCP tools (mcp__github__*) and no gh CLI — do not look for them. Use plain git only; the repo clone with authenticated push access is at /home/user/claude-code. Steps:

1. Run `TZ=Europe/Tallinn date`. If the local hour is 8 (winter time, EET), wait an hour before continuing: call the claude-code-remote send_later tool with delay_minutes 60 and message "Baltic wind news report run — it is now 09:30 Tallinn; continue from step 2." and stop this turn; if send_later is unavailable, run `sleep 3600` as a BACKGROUND command instead and continue from step 2 when it finishes. If the local hour is 9 (or this message tells you to continue from step 2), continue immediately.

2. Trigger the workflow with git. In /home/user/claude-code run:
   `git fetch origin main && git checkout main && git pull origin main`
   then write the current UTC timestamp into the trigger file:
   `date -u +"%Y-%m-%dT%H:%M:%SZ" > baltic-wind-news-agent/trigger/run-request.txt`
   then commit and push:
   `git add baltic-wind-news-agent/trigger/run-request.txt && git commit -m "chore: request baltic wind run [trigger]" && git push origin main`
   On push failure retry up to 4 times with 2s/4s/8s/16s backoff (`git pull --rebase origin main` first if rejected). This push fires the "Baltic Wind Energy News Report" workflow on main via its trigger/** path filter. Record your trigger commit SHA.

3. Verify with git polling — never use a foreground sleep; use a background until-loop. Every ~120 seconds run `git fetch origin main && git log origin/main --oneline -5` and look for a NEW commit "chore: update baltic wind state [skip ci]" landing after your trigger commit. Poll for up to 40 minutes (the job timeout is 40 minutes).

4. If that state commit appears: the run succeeded and the workflow already emailed the report to priitmr@gmail.com. End quietly with a one-line summary, e.g. "Baltic wind report sent (state commit <sha>)." Nothing else to do.

5. If no state commit appears within 40 minutes: end with a noteworthy failure summary so the user is notified — say the run failed or never started, give your trigger commit SHA, and point to https://github.com/zhoze/claude-code/actions/workflows/baltic-wind-daily.yml. Retry step 2 at most once before giving up.

---

## Fallbacks if the Routine fails anyway

The workflow's `schedule:` crons (`30 6` + `30 7` UTC, Mon–Fri) also fire on
`main`. GitHub throttles them in this repo — on 2026-07-27 they arrived ~4 h
late — but the bot's guard accepts any workday run from 09:30 Tallinn onward and
`state/last_run.json` allows at most one report per day, so a late cron still
delivers the report and never duplicates one the Routine already sent.
