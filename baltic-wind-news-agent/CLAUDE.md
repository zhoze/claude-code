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

Same pattern as `err-news-agent`/`estonian-law-agent`: GitHub throttles
`schedule:` crons in this repo (today's fired ~4 h late), so the double
UTC cron is a fallback while a Claude Code Remote Routine ("Baltic wind
news report (workdays 09:30 Tallinn)", cron `30 6 * * 1-5` UTC with a
winter-time send_later hop) is the primary trigger.

**Routine-fired sessions have NO GitHub MCP tools** (verified 2026-07-27:
only claude.ai connectors are attached, no `gh` CLI) — they do have the
repo clone at `/home/user/claude-code` with authenticated git push. The
Routine therefore triggers a run with plain git: commit a timestamp to
`baltic-wind-news-agent/trigger/run-request.txt` on `main` and push
(the workflow's `push` trigger is scoped to that path), then verifies by
polling `git log origin/main` for the `chore: update baltic wind state
[skip ci]` commit the run pushes at the end. Only `workflow_dispatch`
carries force; push/cron runs obey the guard, so a stray trigger push is
a cheap no-op. `state/last_run.json` guarantees at most one report per
day across all trigger paths.

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
