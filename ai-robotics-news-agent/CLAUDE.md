# ai-robotics-news-agent

Weekly AI & robotics news digest produced by a ChatGPT↔Claude debate pipeline.
Runs Mondays at 11:00 Europe/Tallinn on GitHub Actions; the report and its
data are committed to `reports/` and delivered into the owner's Claude Code
Remote session by a Routine that dispatches the workflow and posts the result.

## Pipeline (`bot/weekly_news.py`)

1. **ChatGPT searches** (Responses API + `web_search`): ~10 candidate stories
   from the past 7 days as structured JSON.
2. **Claude evaluates** stories AND source quality (own `web_search_20260318`,
   capped by `claude_web_max_uses`), flags issues and missing topics.
3. **Debate round** (parallel): GPT defends/corrects its stories; Claude
   revises its assessments.
4. **Claude verdict**: consensus reached, or specific follow-up queries —
   capped at `max_follow_up_rounds` (then consensus is forced).
5. **GPT follow-up searches** → merge → re-evaluate → debate again → verdict.
6. **Claude synthesizes** the consensus into the final data JSON; the markdown
   report is rendered deterministically in Python from that JSON so the two
   can never disagree.

Outputs: `reports/YYYY-MM-DD.json` (full data: per-story GPT + Claude
assessments, consensus verdicts, exclusions with reasons, debate log) and
`reports/YYYY-MM-DD.md` (the readable report). Same-day re-runs overwrite —
idempotent by design.

## Scheduling reality

`.github/workflows/ai-robotics-weekly.yml` has Monday crons at 08:00 and
09:00 UTC (11:00 Tallinn summer/winter; the script's local-time guard lets the
right one through). But GitHub throttles `schedule:` hard in this repo and
cron only fires from `main` — so the **primary trigger is the owner's Claude
Code Remote Routine**, which fires Monday 11:00 Tallinn, dispatches the
workflow via `workflow_dispatch` (not throttled), waits for the run, then
posts the report + data summary into the owner's session. The GH cron is
fallback only.

## Conventions

- Secrets only via GitHub Actions secrets: `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`. Never commit or print values.
- Models are set in `config.yaml` only (`claude-sonnet-5`, `gpt-5.6-terra`).
- All model calls demand JSON-only output; `extract_json` tolerates fences and
  surrounding prose, and each call gets ONE no-search repair retry before the
  run goes red (nonzero exit → no commit).
- Report commits use `[skip ci]` and `git pull --rebase` before push (the
  debate bot commits state every few minutes; races are expected).
- Cost per run: worst case ~12 API calls and ~15–25 web searches (Claude
  search capped via `max_uses`; GPT capped via prompt hint + bounded number of
  search-enabled calls) — roughly under a dollar plus token costs.

## Backlog

- Dedup against previous week: the prior `reports/*.json` is already in the
  checkout and could be fed to the initial search prompt as "already covered".
