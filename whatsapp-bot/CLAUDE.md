# whatsapp-bot

WhatsApp auto-reply bot: answers messages from ONE specific person as the
owner (first person, owner's style), using the Claude API. Built on the
**Twilio WhatsApp API** — Twilio is an official WhatsApp Business Solution
Provider, so this needs no Meta developer account (which was unavailable to
the owner in Estonia). Replies come from a Twilio WhatsApp number, not the
owner's personal number.

## Architecture

Unlike the repo's other bots, this one CANNOT run on GitHub Actions cron:
inbound WhatsApp messages arrive only as webhook pushes to a public HTTPS
endpoint. It therefore runs as a single dependency-free **Cloudflare
Worker** (free tier), `worker.js`:

1. `POST` from Twilio — validate `X-Twilio-Signature` (HMAC-SHA1 over
   URL + sorted form params with the auth token), ack immediately with
   empty TwiML, process in `ctx.waitUntil()` (a Claude call can exceed
   Twilio's ~15 s webhook timeout)
2. Filter: sender must equal `ALLOWED_WA_NUMBER`; text only; dedupe on
   `MessageSid` in KV
3. Load last 20 turns from KV (`hist:{number}`, 24 h TTL) → call the Claude
   Messages API (`claude-opus-4-8`, `system` = `PERSONA` secret) → send the
   reply via Twilio's REST Messages API, chunked at 1500 chars (Twilio
   WhatsApp body limit is 1600) → save updated history

Because the person always writes first, replies are inside WhatsApp's
24-hour session window — free-form text is allowed, no templates.

## Configuration

Vars in `wrangler.toml`: `TWILIO_WHATSAPP_FROM` (keep the `whatsapp:`
prefix), `ALLOWED_WA_NUMBER` (E.164 with `+`), `CLAUDE_MODEL`.
KV binding: `CHAT_HISTORY`.

Secrets via `npx wrangler secret put` (never commit or print values):

- `ANTHROPIC_API_KEY`
- `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`
- `PERSONA` — the private system prompt defining who "you" are and how you
  write; the person is not told it is a bot (owner's choice)

## One-time setup

1. Cloudflare: `npx wrangler kv namespace create CHAT_HISTORY` → put the id
   in `wrangler.toml`; set the four secrets; `npx wrangler deploy`.
2. Twilio: sign up at twilio.com (works from Estonia). Console → Messaging
   → Try it out → **Send a WhatsApp message**: this is the sandbox. Note
   the sandbox number and join code.
3. The person joins the sandbox once by sending `join <code>` from their
   WhatsApp to the sandbox number (sandbox participation expires after
   72 h of inactivity — they must re-join with the same code).
4. In the sandbox settings, set "WHEN A MESSAGE COMES IN" to the Worker
   URL (method POST).
5. Fill `ALLOWED_WA_NUMBER`, redeploy, and have the person send a message.
6. Production (no sandbox joins/expiry): register a WhatsApp sender in the
   Twilio console (Messaging → Senders → WhatsApp senders). This uses a
   Meta *Business* account (business.facebook.com) via Twilio's embedded
   flow — distinct from the developer portal that was blocked.

## Conventions

- `ALLOWED_WA_NUMBER` filtering must stay enabled — without it anyone who
  messages the number burns API credits.
- Signature validation must stay enabled — the webhook URL is public.
- API errors are logged (`npx wrangler tail`) and never sent to the person.
- Messages are chunked at 1500 chars (Twilio WhatsApp limit is 1600).
- History is capped at 20 messages per sender and expires after 24 h.

## Verification

- Local: `npx wrangler dev`, then POST a simulated Twilio form payload with
  a signature computed from the auth token.
- Live: after the person joins the sandbox, they message the sandbox
  number → reply arrives in seconds; `npx wrangler tail` shows the flow.
  A message from any other number must be ignored; a duplicate delivery
  must produce one reply.

## Backlog

- Support voice notes (transcribe) and images (Claude vision)
- Optional owner-notification channel (e.g. Telegram) summarizing what the
  bot answered on the owner's behalf
- Alternative BSP (360dialog) if Twilio pricing becomes an issue
