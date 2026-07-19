# estonian-law-agent

Eesti õiguse agent: igapäevane Riigi Teataja seire kell 10:00 (Europe/Tallinn)
Telegrami + interaktiivne Claude Code agent (`.claude/agents/eesti-oiguse-agent.md`),
mis vastab ainult eesti keeles ja lisab iga fakti juurde riigiteataja.ee lingi.

## Architecture

Daily run via `.github/workflows/estonian-law-daily.yml` (cron only fires from
the repo's default branch):

1. Local-time guard: proceed only at 10:00 Europe/Tallinn (double UTC cron
   `0 7` + `0 8` covers EEST/EET; the wrong one exits early)
2. Query the Riigi Teataja search API for laws published since
   `state/last_check.json` → `last_date`
3. Check each watched law (`config.yaml` → `lyhendid`) for a newly effective
   consolidated version (`lyhend=X&kehtiv=<today>`)
4. Summarize each new act in Estonian with Claude (`claude-sonnet-5`); a failed
   summary falls back to a title-only entry — the digest must still send
5. Send the digest to Telegram (watched-law hits first, links on every entry)
6. Commit the new state (`[skip ci]`)

Entry point: `bot/daily_digest.py`. Watched laws: VÕS, TSÜS, KAS, KAVS, FIS,
KMS, TLS, TMS (edit `config.yaml` to change).

## Riigi Teataja API cheat-sheet

`GET https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi` (JSON; POST → 500).

Supported params (probed live 2026-07-19; anything else is silently ignored —
notably there is **no publication-date filter**):
`leht`, `limiit` (≤~500), `pealkiri`, `lyhend`, `dokument` (seadus/määrus/…),
`valjaandja`, `kehtiv=YYYY-MM-DD`, `kov=true|false`, `tekst=algtekst|terviktekst`,
`kehtivKehtetus`, `mitteJoustunud`.

New-act detection: results are deterministically ordered with the newest acts on
the **last** pages; modern `globaalID` encodes the publication notation
`<RT part><DDMM><YYYY><seq>` (116072026004 = RT I, 16.07.2026, 4). The bot scans
backwards from the last page and filters by that embedded date + a seen-ID set
in state. NB: the ordering is only *loosely* chronological (interleaved by
indexing order), so partial scans can miss acts — the bot therefore scans ALL
pages of the laws-only scope (~10 pages, ~10s), capped by `max_scan_pages` in
config.yaml (default 40 ≈ 20k acts) as protection if the scope is ever widened.
This makes arbitrary-depth `--since` backfills complete and correct.
Act links: `https://www.riigiteataja.ee/akt/{globaalID}` (user-facing page); act
text for summarization:
`https://www.riigiteataja.ee/public-api/api/v1/akt/{globaalID}/blob-xml`
(the legacy `/akt/{id}.xml` now returns the SPA shell, not the act).

## Scheduling reality

Same as tg-debate-bot: GitHub throttles `schedule` crons in this repo, but a
daily digest tolerates an hour of lag, so no external pinger is required. For
punctual delivery, add a cron-job.org POST to
`https://api.github.com/repos/zhoze/claude-code/actions/workflows/estonian-law-daily.yml/dispatches`
with body `{"ref":"main"}` at 07:00/08:00 UTC. Cron activates only after this
branch merges to `main`; until then use `workflow_dispatch`.

## Conventions

- **Single source of truth: Riigi Teataja only.** All facts in digests and in
  the interactive agent's answers must come from the riigiteataja.ee API /
  act texts — no other websites, search engines, or model background knowledge.
  The summarizer prompt pins Claude to the supplied act text; the interactive
  agent (`.claude/agents/eesti-oiguse-agent.md`) may only curl
  `www.riigiteataja.ee` and has no general web tools.
- Secrets only via GitHub Actions secrets: `DEBATE_TELEGRAM_BOT_TOKEN` (mapped
  to `TELEGRAM_BOT_TOKEN`), `ANTHROPIC_API_KEY`, `ALLOWED_CHAT_ID` (digest
  destination chat). Never commit or print values.
- Digest language is Estonian; every entry carries its riigiteataja.ee link.
- Telegram messages are chunked at 4000 chars; link previews disabled.
- State commits use `[skip ci]` and `git pull --rebase` before push.
- `quiet_days: true` in config: no Telegram message on days without news.
- Local testing: `python bot/daily_digest.py --dry-run --force --since 2026-07-10`
  (no Telegram, no state write). `--seed` re-initializes `state/last_check.json`.
