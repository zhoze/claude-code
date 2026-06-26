# iha.ee daily poster

An agent that logs into your **iha.ee** account and posts a predefined message
once a day to the **Mees → Paari (men → couples)** category.

> ⚠️ **Use your own account, post your own ad.** Automated posting may be against
> iha.ee's terms of service — confirm it's allowed for your account before running
> this so you don't get banned. This tool is for legitimately re-posting your own
> ad, not for spamming.

## How it works

- **Browser automation** with Playwright (Chromium). It opens the login page,
  signs in with credentials from your `.env`, opens the post page, fills the
  message, and submits.
- **Message + schedule** live in `config.json`. Credentials live in `.env`
  (git-ignored, never committed).

## Setup

```bash
cd daily-poster
npm install
npx playwright install chromium   # not needed in environments where Chromium is preinstalled
cp .env.example .env              # then edit .env with your iha.ee login
```

Put your message in `config.json` → `message.text` (≤ 160 chars).

## First run: confirm the post form (one-time)

The login fields are already known. The **post form** selectors in `config.json`
are placeholders — capture the real ones once:

```bash
npm run inspect
```

This logs in, opens the post page, and prints every form field (name/id/type) plus
a screenshot in `screenshots/`. Paste the correct selectors into
`config.json` → `selectors.post`. Set `HEADFUL=true` in `.env` to watch it happen.

## Test without posting

```bash
npm run post:dry   # logs in + fills the form but does NOT submit
```

Check `screenshots/dry-run-before-submit-*.png` to confirm it looks right.

## Post once (for real)

```bash
npm run post
```

## Run it daily

This repo's environment is ephemeral, so the schedule must run somewhere
**persistent**. GitHub Actions is set up for this; system cron and a long-running
scheduler are also supported.

### A) GitHub Actions (configured)

Workflow: `.github/workflows/iha-daily-poster.yml`.

1. Add your credentials as repository secrets:
   **Settings → Secrets and variables → Actions → New repository secret**
   - `IHA_USERNAME`
   - `IHA_PASSWORD`
2. Make sure `daily-poster/config.json` has your real `message.text` and the
   confirmed `selectors.post` (see "First run" above), committed to the repo.
3. **Merge this workflow to the default branch** — scheduled (cron) runs only
   fire from the default branch. Until then, use **Actions → iha.ee daily poster
   → Run workflow** to trigger it manually (there's a "dry run" checkbox).

Timing caveats:
- GitHub cron is **UTC and ignores DST**. The workflow uses `0 7 * * *`
  (= 10:00 Tallinn in summer / 09:00 in winter). Change to `0 8 * * *` for exact
  10:00 in winter.
- Scheduled runs on shared runners can be delayed (minutes to occasionally hours).

Debugging: every run uploads the `screenshots/` folder as a build artifact.

### B) System cron — edit `crontab -e`:

```
0 10 * * * cd /path/to/daily-poster && /usr/bin/node src/post.js >> post.log 2>&1
```

### C) Long-running scheduler (pm2/systemd keeps a process alive):

```bash
npm run schedule
```

It reads `schedule.cron` / `schedule.timezone` from `config.json`
(default: `0 10 * * *`, `Europe/Tallinn`).

## Files

| File | Purpose |
|------|---------|
| `config.json` | Message, schedule, URLs, selectors (edit this) |
| `.env` | Your credentials (create from `.env.example`, never commit) |
| `src/post.js` | Login + post logic (`--dry-run`, `--inspect` flags) |
| `src/scheduler.js` | Optional in-process cron scheduler |
| `screenshots/` | Debug screenshots (git-ignored) |
