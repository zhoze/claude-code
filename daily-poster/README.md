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
**persistent** (a VPS, home server, Raspberry Pi, or GitHub Actions). Two options:

**A) System cron (recommended)** — edit `crontab -e`:

```
0 10 * * * cd /path/to/daily-poster && /usr/bin/node src/post.js >> post.log 2>&1
```

**B) Long-running scheduler** (keep a process alive with pm2/systemd):

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
