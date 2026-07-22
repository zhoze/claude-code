/**
 * WhatsApp auto-reply bot — Cloudflare Worker (official Meta Cloud API).
 *
 * Answers messages from one specific person as the owner, using the Claude API.
 * Single file, zero dependencies — deployable with `wrangler deploy` or by
 * pasting into the Cloudflare dashboard.
 *
 * Secrets (wrangler secret put ...):
 *   ANTHROPIC_API_KEY, WHATSAPP_TOKEN, WHATSAPP_APP_SECRET, VERIFY_TOKEN, PERSONA
 * Vars (wrangler.toml):
 *   PHONE_NUMBER_ID, ALLOWED_WA_NUMBER, CLAUDE_MODEL
 * KV binding: CHAT_HISTORY
 */

const GRAPH = "https://graph.facebook.com/v21.0";
const MAX_HISTORY_TURNS = 20;      // messages kept per sender (10 exchanges)
const HISTORY_TTL_S = 86400;       // conversation memory lifetime
const DEDUPE_TTL_S = 300;          // Meta retries webhook deliveries
const WA_TEXT_LIMIT = 4096;        // WhatsApp per-message char limit

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    if (request.method === "GET") {
      return handleVerification(url, env);
    }

    if (request.method === "POST") {
      const rawBody = await request.text();
      const signature = request.headers.get("X-Hub-Signature-256") || "";
      if (!(await verifySignature(rawBody, signature, env.WHATSAPP_APP_SECRET))) {
        return new Response("invalid signature", { status: 401 });
      }
      // Ack immediately; Meta retries on slow responses.
      ctx.waitUntil(processWebhook(rawBody, env));
      return new Response("ok", { status: 200 });
    }

    return new Response("method not allowed", { status: 405 });
  },
};

function handleVerification(url, env) {
  const mode = url.searchParams.get("hub.mode");
  const token = url.searchParams.get("hub.verify_token");
  const challenge = url.searchParams.get("hub.challenge");
  if (mode === "subscribe" && token === env.VERIFY_TOKEN && challenge) {
    return new Response(challenge, { status: 200 });
  }
  return new Response("forbidden", { status: 403 });
}

async function verifySignature(body, header, appSecret) {
  if (!header.startsWith("sha256=")) return false;
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(appSecret),
    { name: "HMAC", hash: "SHA-256" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = [...new Uint8Array(mac)].map(b => b.toString(16).padStart(2, "0")).join("");
  const provided = header.slice("sha256=".length);
  if (provided.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ provided.charCodeAt(i);
  }
  return diff === 0;
}

async function processWebhook(rawBody, env) {
  let payload;
  try {
    payload = JSON.parse(rawBody);
  } catch {
    return;
  }

  for (const entry of payload.entry || []) {
    for (const change of entry.changes || []) {
      if (change.field !== "messages") continue;
      for (const msg of change.value?.messages || []) {
        try {
          await handleMessage(msg, env);
        } catch (e) {
          console.error(`error handling ${msg.id}: ${e}`);
        }
      }
    }
  }
}

async function handleMessage(msg, env) {
  if (msg.from !== env.ALLOWED_WA_NUMBER) {
    console.log(`ignoring message from non-allowed number`);
    return;
  }
  if (msg.type !== "text" || !msg.text?.body) {
    console.log(`ignoring unsupported message type: ${msg.type}`);
    return;
  }

  const dedupeKey = `dedupe:${msg.id}`;
  if (await env.CHAT_HISTORY.get(dedupeKey)) {
    console.log(`duplicate delivery of ${msg.id}, skipping`);
    return;
  }
  await env.CHAT_HISTORY.put(dedupeKey, "1", { expirationTtl: DEDUPE_TTL_S });

  await markAsRead(msg.id, env);

  const histKey = `hist:${msg.from}`;
  const history = JSON.parse((await env.CHAT_HISTORY.get(histKey)) || "[]");
  const messages = [...history, { role: "user", content: msg.text.body }];

  const reply = await askClaude(messages, env);
  if (!reply) {
    console.error("empty reply from Claude, nothing sent");
    return;
  }

  await sendText(msg.from, reply, env);

  const newHistory = [...messages, { role: "assistant", content: reply }]
    .slice(-MAX_HISTORY_TURNS);
  await env.CHAT_HISTORY.put(histKey, JSON.stringify(newHistory), {
    expirationTtl: HISTORY_TTL_S,
  });
}

async function askClaude(messages, env) {
  const resp = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": env.ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: env.CLAUDE_MODEL || "claude-opus-4-8",
      max_tokens: 1024,
      system: env.PERSONA,
      messages,
    }),
  });

  if (!resp.ok) {
    throw new Error(`Claude API ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
  }
  const data = await resp.json();
  if (data.stop_reason === "refusal") {
    console.error("Claude refused the request");
    return "";
  }
  return (data.content || [])
    .filter(b => b.type === "text")
    .map(b => b.text)
    .join("")
    .trim();
}

async function sendText(to, text, env) {
  for (let i = 0; i < text.length; i += WA_TEXT_LIMIT) {
    const resp = await fetch(`${GRAPH}/${env.PHONE_NUMBER_ID}/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.WHATSAPP_TOKEN}`,
      },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        to,
        type: "text",
        text: { body: text.slice(i, i + WA_TEXT_LIMIT) },
      }),
    });
    if (!resp.ok) {
      throw new Error(`WhatsApp send ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
    }
  }
}

async function markAsRead(messageId, env) {
  // Best-effort: blue ticks make the auto-reply look natural; failure is non-fatal.
  try {
    await fetch(`${GRAPH}/${env.PHONE_NUMBER_ID}/messages`, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        authorization: `Bearer ${env.WHATSAPP_TOKEN}`,
      },
      body: JSON.stringify({
        messaging_product: "whatsapp",
        status: "read",
        message_id: messageId,
      }),
    });
  } catch (e) {
    console.log(`mark-as-read failed: ${e}`);
  }
}
