# err-news-agent

Daily news trustworthiness digest, emailed to priitmr@gmail.com. Runs at
08:00 Europe/Tallinn on GitHub Actions.

## Pipeline (`bot/news_agent.py`)

1. **Fetch** the top `top_n` (default 5) stories from each source's RSS feed
   (`config.yaml` → `sources`; currently just ERR News,
   `https://news.err.ee/rss`), skipping any story `guid` already processed
   within `dedup_days`.
2. **Independent trust checks**: Claude (own `web_search_20260318`, capped by
   `claude_web_max_uses`) and ChatGPT (Responses API `web_search`) each
   search the web to see whether the story is corroborated by reputable
   global outlets (Reuters, AP, BBC, AFP, etc. — not blogs/forums/social
   media), independently and in parallel.
3. **Debate round**: each model sees the other's verdict and reasoning and
   may revise its own (re-searching if needed).
4. **Verdict**: a story is *confirmed* only if **both** revised assessments
   say `trustworthy: true`. Otherwise it's *excluded*, with a reason drawn
   from whichever model(s) said no.
5. **Full summary**: for confirmed stories only, one more Claude call writes
   a multi-sentence digest summary grounded in the RSS description plus the
   corroborating sources surfaced during the trust-check/debate stages — no
   scraping of the article's raw HTML page.
6. **Email**: a plain-text digest (confirmed stories with summaries + links
   + corroborating sources, then a "Not confirmed" section with reasons) is
   sent via Gmail SMTP to `recipient_email`.
7. Processed guids (confirmed and excluded) are recorded in
   `state/sent_guids.json` so the same story isn't re-checked/re-emailed
   while it's still near the top of the feed.

## Scheduling reality

Same pattern as `estonian-law-agent`: GitHub throttles `schedule:` crons in
this repo, so `.github/workflows/err-news-daily.yml` has a double UTC cron
(covers EEST/EET) as a fallback, but a Claude Code Remote Routine that
dispatches the workflow via `workflow_dispatch` at 08:00 Tallinn is the
reliable primary trigger.

## Conventions

- Secrets only via GitHub Actions secrets: `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD` (a Gmail App
  Password, not the account password). Never commit or print values.
- Models are set in `config.yaml` only (`claude-sonnet-5`, `gpt-5.6-terra`).
- All model calls demand JSON-only output; `extract_json` tolerates fences
  and surrounding prose, with one no-search repair retry.
- A story needs agreement from BOTH models to be emailed as confirmed —
  deliberately stricter than a single-judge synthesis, since the point is
  trustworthiness, not just significance.
- State commits use `[skip ci]` and `git pull --rebase` before push.
- Local testing (no email, no state write):
  `python bot/news_agent.py --dry-run --force`

## Adding another site

`config.yaml` → `sources` is a list of `{name, rss_url}`. A new site with an
RSS feed just needs an entry added; a site without one needs a small
dedicated fetch function returning the same story shape as
`fetch_top_stories` (`guid`, `source`, `title`, `link`, `description`,
`pub_date`, `category`), called from `main()` alongside the RSS loop.
