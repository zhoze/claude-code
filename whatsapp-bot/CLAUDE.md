# whatsapp-bot

WhatsApp auto-reply bot: answers messages from ONE specific person as the
owner (first person, owner's style), using the Claude API. Built on the
official Meta WhatsApp Business Cloud API — no ToS risk, but replies come
from a separate WhatsApp Business number, not the owner's personal number.

## Architecture

Unlike the repo's other bots, this one CANNOT run on GitHub Actions cron:
the Cloud API has no polling endpoint — inbound messages arrive only as
webhook pushes to a public HTTPS endpoint. It therefore runs as a single
dependency-free **Cloudflare Worker** (free tier), `worker.js`:

1. `GET` — Meta webhook verification handshake (`hub.challenge` echo)
2. `POST` — validate `X-Hub-Signature-256` (HMAC-SHA256 with the app secret),
   ack 200 immediately, process in `ctx.waitUntil()`
3. Filter: sender must equal `ALLOWED_WA_NUMBER`; only `text` messages;
   dedupe on message id in KV (Meta retries deliveries)
4. Load last 20 turns from KV (`hist:{number}`, 24 h TTL) → call the Claude
   Messages API (`claude-opus-4-8`, `system` = `PERSONA` secret) → send the
   reply via the Graph API, chunked at 4096 chars → save updated history
5. Marks incoming messages as read (blue ticks) so the auto-reply looks natural

Because the person always writes first, every reply is inside Meta's
24-hour customer-service window — free-form text is allowed, no templates.

## Configuration

Vars in `wrangler.toml`: `PHONE_NUMBER_ID`, `ALLOWED_WA_NUMBER` (E.164
digits only, no `+`), `CLAUDE_MODEL`. KV binding: `CHAT_HISTORY`.

Secrets via `npx wrangler secret put` (never commit or print values):

- `ANTHROPIC_API_KEY`
- `WHATSAPP_TOKEN` — permanent system-user token, not the 24 h test token
- `WHATSAPP_APP_SECRET` — signs webhook payloads; validation must stay on
- `VERIFY_TOKEN` — random string, must match the Meta webhook config
- `PERSONA` — the private system prompt defining who "you" are and how you
  write; the person is not told it is a bot (owner's choice)

## One-time setup

1. Cloudflare: `npx wrangler kv namespace create CHAT_HISTORY` → put the id
   in `wrangler.toml`; set the five secrets; `npx wrangler deploy`.
2. Meta: create an app at developers.facebook.com → add the WhatsApp
   product. The free **test number** works immediately, but the person must
   be added as a test recipient; register a real business number for
   production. Create a system user in Business Settings and generate a
   permanent token with `whatsapp_business_messaging` permission.
3. App Dashboard → WhatsApp → Configuration: set the callback URL to the
   Worker URL, enter `VERIFY_TOKEN`, subscribe to the `messages` field.
4. Fill `PHONE_NUMBER_ID` and `ALLOWED_WA_NUMBER`, redeploy.

## Conventions

- `ALLOWED_WA_NUMBER` filtering must stay enabled — without it anyone who
  messages the business number burns API credits.
- Signature validation must stay enabled — the webhook URL is public.
- API errors are logged (`npx wrangler tail`) and never sent to the person.
- WhatsApp messages are chunked at 4096 chars (API limit).
- History is capped at 20 messages per sender and expires after 24 h.

## Verification

- Local: `npx wrangler dev`, then curl the GET handshake and a simulated
  message POST with a signature computed from the app secret.
- Live: message the business number from the allowed phone → reply arrives
  in seconds; `npx wrangler tail` shows the flow. A message from any other
  number must be ignored; a duplicate delivery must produce one reply.

## Backlog

- Support voice notes (transcribe) and images (Claude vision)
- Optional owner-notification channel (e.g. Telegram) summarizing what the
  bot answered on the owner's behalf
