# tg-debate-bot

Telegram bot where Claude and ChatGPT debate the owner's questions and reply
with a synthesized consensus answer. Runs entirely on GitHub Actions cron
polling — no server.

## Architecture

Stateless polling run, every ~5 minutes via `.github/workflows/debate-bot.yml`
(cron only fires from the repo's default branch):

1. `getUpdates` with `offset + 1` from `state/offset.txt`
2. For each new text message in the allowed chat: run the debate
3. `sendMessage` the consensus answer back
4. Commit the new offset to `state/offset.txt` (`[skip ci]`)

Entry point: `bot/poll_and_answer.py`. Models: `claude-sonnet-5` (Anthropic)
and `gpt-5.6-terra` (OpenAI).

## Debate flow

1. Both models answer the question independently (parallel)
2. One critique round: each model sees the other's answer and improves its own
3. Claude acts as synthesis judge and merges the two into the final answer

## Conventions

- Secrets only via GitHub Actions secrets: `DEBATE_TELEGRAM_BOT_TOKEN`
  (mapped to the `TELEGRAM_BOT_TOKEN` env var), `ANTHROPIC_API_KEY`,
  `OPENAI_API_KEY`, `ALLOWED_CHAT_ID`. Never commit or print values.
- `ALLOWED_CHAT_ID` must stay enabled — it restricts the bot to the owner's
  chat; without it anyone who finds the bot can burn API credits.
- Telegram messages are chunked at 4000 chars (API limit is 4096).
- Offset commits use `[skip ci]` and `git pull --rebase` before push to
  tolerate races with the repo's other cron workflows.
- A failed debate must still send the error message to Telegram and continue.

## Backlog

- Web search tool for both models (grounded answers)
- VPS long-polling deployment (instant replies instead of 5–15 min latency)
- Route OOG/logistics questions through the oog-transport-cost-calculator
  scripts
