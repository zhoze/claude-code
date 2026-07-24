# drinking-buddies-agent

Three-way "bar chat" between the user, API Claude, and API ChatGPT. The user
says something, Claude answers first, then ChatGPT reacts to Claude's take with
his own opinion, then the turn returns to the user. One reply each per user
message — no hidden debate, no synthesis (that's `tg-debate-bot`'s job; this is
the open, visible counterpart).

## Architecture

Single terminal script: `buddies.py`.

- Models: `claude-opus-5` (Anthropic) and `gpt-5.6-terra` (OpenAI Responses
  API, low reasoning effort). No web search tools — cheap and chatty.
- A shared transcript of `(speaker, text)` tuples (`You` / `Claude` /
  `ChatGPT`) is rendered as labeled text and sent in full to each model, so
  both buddies see everything everyone said.
- Personas: casual bar buddies — short punchy replies, real opinions, free to
  disagree and tease each other; no assistant-speak.
- A buddy erroring (rate limit, network) prints the error as that buddy's
  reply and the conversation continues — a failure must not kill the loop.
- **Persistent memory:** the transcript is saved to `state/transcript.json`
  after every turn and loaded on startup, so past topics carry over between
  sessions. The models see the last ~40 entries (`CONTEXT_TURNS`). Typing
  `forget` wipes the memory. The `buddies-smoke.yml` workflow commits the
  updated memory back to the branch with `[skip ci]` (same pattern as
  tg-debate-bot's offset state).

## Running

```
pip install -r requirements.txt
python buddies.py                       # interactive
echo "some topic" | python buddies.py   # scripted / smoke test
```

## Conventions

- Secrets only via env vars: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`. Never
  commit or print values.

## Backlog

- Move to Telegram once validated here (copy the `tg-debate-bot` GitHub
  Actions polling pattern: workflow, secrets, offset state).
- Multi-round AI banter between user turns / configurable rounds.
