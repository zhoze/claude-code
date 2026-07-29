# baltic-wind-news-agent

Workday report on newly announced planned wind-farm projects in Estonia,
Latvia and Lithuania, emailed to priitmr@gmail.com at 09:30 Europe/Tallinn
(Mon–Fri) via GitHub Actions. Only projects never reported before are
included; the email is sent even when nothing new was found.

## Pipeline (`bot/wind_agent.py`)

1. **Local-time guard**: proceed on Mon–Fri at/after 09:30 Europe/Tallinn
   (double UTC cron `30 6` + `30 7` Mon–Fri covers EEST/EET), but at most
   once per day — `state/last_run.json` records the last sent date, so a
   throttled/delayed cron still delivers the report while repeat runs the
   same day exit early. `--force`/`FORCE_RUN` bypasses both checks.
2. **RSS harvest**: `config.yaml` → `rss_sources` (ERR, LSM, LRT English,
   BalticWind.EU, OffshoreWIND.biz, BNN, Baltic Times, 15min, Dienas
   Bizness, Äripäev). Multilingual wind-keyword prefilter
   (`wind_keywords`), URLs already in `state/seen_urls.json` skipped.
3. **Listing-page scan**: `config.yaml` → `listing_pages` (developer/OEM
   newsrooms, wind associations, municipality news pages). Anchor
   extraction + same prefilter. Every fetch failure is logged, never fatal.
4. **Claude web-search discovery**: one call per country (EN + local
   language queries, last 7 days) to catch sources outside the fixed list.
   These calls **stream** — a non-streaming request idles through the
   model's web-search turns and trips the SDK read timeout, which killed
   every sweep until 2026-07-28. A sweep costs roughly 2 min per search
   (`discovery_web_max_uses`), so the three countries run in **parallel**
   via a thread pool; results arriving after `DISCOVERY_DEADLINE` are
   ignored so a slow sweep can't sink the report.
5. **Extraction & novelty**: one Claude call gets all candidates plus the
   project registry (`state/reported_projects.json`, names + aliases) and
   returns only projects NOT in the registry. Non-project stories (policy,
   construction progress on known farms, solar-only) are excluded. A
   normalized-key check (`normalize_key`) drops any residual collisions.
   Every reported project must carry at least one actual news link.
6. **Email**: plain-text report grouped by country (name, developer,
   capacity, status, 2–3 sentence summary, links) via Gmail SMTP. Subject:
   `Baltic Wind — N new planned project(s) — YYYY-MM-DD`. Zero-result days
   still send a "No new planned wind farm projects found today" email.
7. **State**: new projects appended to `reported_projects.json`
   (**permanent** — project dedup never expires); processed URLs recorded
   in `seen_urls.json` and pruned after `seen_url_days` (60). The workflow
   commits state with `[skip ci]`.

The registry was seeded (`first_reported: "seed"`) with all ~68 projects
named in `sources/*.md` (the user's curated link collections, retained as
reference), so day-1 reports contain only genuinely new announcements.

## Scheduling reality

Same pattern as `estonian-law-agent`: a **Routine dispatching this
workflow is the reliable trigger**, and `schedule:` cron is the fallback
(it fires late here, and on 2026-07-29 not at all). The Routine **must be
created in the claude.ai Routines UI** — one created with the
`create_trigger` MCP tool fires without connectors, so it has no
`mcp__github__*` and no push, which is exactly why the first attempt at
this failed for two days. Full evidence and the prompt to paste:
`SCHEDULING.md`.

The double UTC cron (`30 6` + `30 7`, Mon–Fri) covers EEST/EET; the
local-time guard skips anything before 09:30 Tallinn, and
`state/last_run.json` reduces any later run that day to a ~20 s no-op, so
Routine and cron can both fire without ever sending twice. Only
`workflow_dispatch` with `force: true` bypasses those guards. Keep the
cron list short — a 10-entry fan got the workflow zero scheduled runs.

Nothing outside this repo is needed to send the daily report — no session,
no connector, no stored prompt. Manual runs: `workflow_dispatch` (with
`force: true` to bypass both guards), or, with push access, commit a
timestamp to `trigger/run-request.txt` on `main`.

## Conventions

- Secrets only via GitHub Actions secrets: `ANTHROPIC_API_KEY`,
  `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD` (Gmail App Password). Never commit
  or print values.
- Model set in `config.yaml` only (`claude-sonnet-5`).
- All model calls demand JSON-only output; `extract_json` tolerates fences
  and surrounding prose, with one repair retry.
- State commits use `[skip ci]` and `git pull --rebase` before push.
- Local testing (no email, no state write):
  `python bot/wind_agent.py --dry-run --force`

## Adding sources

`config.yaml`: an RSS feed goes in `rss_sources` (`{name, rss_url}`); a
plain news/listing page goes in `listing_pages` (`{name, url}`). The full
curated source collections (including every municipality link) live in
`sources/*.md` — promote more of them into `listing_pages` as needed.
