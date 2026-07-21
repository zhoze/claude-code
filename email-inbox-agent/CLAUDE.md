# email-inbox-agent

Daily inbox digest, emailed to st.hoze@mac.com. Runs at 09:00 UTC on GitHub
Actions.

## Pipeline (`bot/inbox_agent.py`)

1. **Fetch**: connect to Gmail over IMAP (`imap.gmail.com`, read-only —
   nothing is ever marked read/archived) and pull every message received
   since the last successful run (`state/last_check.json`), or the last
   `lookback_hours_default` hours on a first run / `--since` override.
2. **Classify**: split messages by the sender's domain into "API usage"
   emails (`config.yaml` → `api_usage_domains`: `anthropic.com`,
   `openai.com`) and everything else.
3. **Summarize**: one Claude call reads the usage emails and writes a short
   summary of Claude/Anthropic and ChatGPT/OpenAI API usage, billing, spend,
   or rate-limit info found in them (empty if there's nothing from a given
   provider), and separately scans the non-usage emails to flag up to
   `max_interesting` that are genuinely worth a human's attention (skipping
   routine notifications, newsletters, marketing).
4. **Email**: a plain-text digest (Claude usage, ChatGPT usage, interesting
   emails with one-line reasons) is sent via Gmail SMTP to `recipient_email`.
5. `state/last_check.json` records the run timestamp so the next run only
   scans mail received since then.

## Scheduling reality

Same pattern as `err-news-agent` / `estonian-law-agent`: GitHub throttles
`schedule:` crons in this repo, so `.github/workflows/email-inbox-daily.yml`
has a `0 9 * * *` UTC cron as a fallback, but a Claude Code Remote Routine
that dispatches the workflow via `workflow_dispatch` at 09:00 UTC is the
reliable primary trigger. No DST handling is needed here since the target
time is already UTC.

## Conventions

- Secrets only via GitHub Actions secrets: `ANTHROPIC_API_KEY`,
  `EMAIL_ADDRESS`, `EMAIL_APP_PASSWORD` (a Gmail App Password — the same one
  `err-news-agent` uses for SMTP also works for IMAP). Never commit or print
  values.
- The mailbox read (`EMAIL_ADDRESS`) and the digest recipient
  (`recipient_email` in `config.yaml`) are different addresses by design.
- Models are set in `config.yaml` only (`claude-sonnet-5`).
- The summarization call demands JSON-only output; `extract_json` tolerates
  fences and surrounding prose.
- IMAP `SINCE` is date-granularity only; messages are re-filtered against
  the exact `last_check` timestamp using each message's parsed `Date`
  header.
- `max_emails_scanned` caps how many messages are pulled per run (safety
  cap for an unexpectedly large inbox window).
- Local testing (no email, no state write):
  `python bot/inbox_agent.py --dry-run --force`
- Local testing with an explicit lookback window:
  `python bot/inbox_agent.py --dry-run --force --since 2026-07-20T00:00:00+00:00`

## Adding another usage-email provider

`config.yaml` → `api_usage_domains` is a flat list of sender domains. Add an
entry there; to also get a dedicated summary section for it (rather than it
being folded into an existing provider's summary), extend the `summarize`
prompt and `render_email` in `bot/inbox_agent.py` with a new key.
