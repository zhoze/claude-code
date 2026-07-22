/**
 * WhatsApp auto-reply bot — Cloudflare Worker (Twilio WhatsApp API).
 *
 * Answers messages from one specific person as the owner, using the Claude API.
 * Uses Twilio as the WhatsApp provider (no Meta developer account required).
 * Single file, zero dependencies — deployable with `wrangler deploy` or by
 * pasting into the Cloudflare dashboard.
 *
 * Secrets (wrangler secret put ...):
 *   ANTHROPIC_API_KEY, TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, PERSONA
 * Vars (wrangler.toml):
 *   TWILIO_WHATSAPP_FROM, ALLOWED_WA_NUMBER, CLAUDE_MODEL
 * KV binding: CHAT_HISTORY
 */

const MAX_HISTORY_TURNS = 20;      // messages kept per sender (10 exchanges)
const HISTORY_TTL_S = 86400;       // conversation memory lifetime
const DEDUPE_TTL_S = 300;          // provider may retry webhook deliveries
const WA_TEXT_LIMIT = 1500;        // Twilio WhatsApp body limit is 1600 chars

export default {
  async fetch(request, env, ctx) {
    if (request.method !== "POST") {
      return new Response("ok", { status: 200 });
    }

    const rawBody = await request.text();
    const params = Object.fromEntries(new URLSearchParams(rawBody));
    const signature = request.headers.get("X-Twilio-Signature") || "";

    if (!(await verifyTwilioSignature(request.url, params, signature, env.TWILIO_AUTH_TOKEN))) {
      return new Response("invalid signature", { status: 401 });
    }

    // Ack immediately with empty TwiML; reply is sent async via the REST API
    // because a Claude call can exceed Twilio's webhook timeout.
    ctx.waitUntil(handleMessage(params, env));
    return new Response("<Response></Response>", {
      status: 200,
      headers: { "content-type": "text/xml" },
    });
  },
};

// X-Twilio-Signature = Base64(HMAC-SHA1(authToken, url + sorted(name+value)))
async function verifyTwilioSignature(url, params, signature, authToken) {
  const data = url + Object.keys(params).sort().map(k => k + params[k]).join("");
  const key = await crypto.subtle.importKey(
    "raw", new TextEncoder().encode(authToken),
    { name: "HMAC", hash: "SHA-1" }, false, ["sign"],
  );
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  if (signature.length !== expected.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) {
    diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  }
  return diff === 0;
}

async function handleMessage(params, env) {
  try {
    const from = (params.From || "").replace("whatsapp:", "");
    const body = (params.Body || "").trim();
    const sid = params.MessageSid || params.SmsMessageSid;

    if (from !== env.ALLOWED_WA_NUMBER) {
      console.log("ignoring message from non-allowed number");
      return;
    }
    if (!body || !sid) {
      console.log("ignoring message without text body");
      return;
    }

    const dedupeKey = `dedupe:${sid}`;
    if (await env.CHAT_HISTORY.get(dedupeKey)) {
      console.log(`duplicate delivery of ${sid}, skipping`);
      return;
    }
    await env.CHAT_HISTORY.put(dedupeKey, "1", { expirationTtl: DEDUPE_TTL_S });

    const histKey = `hist:${from}`;
    const history = JSON.parse((await env.CHAT_HISTORY.get(histKey)) || "[]");
    const messages = [...history, { role: "user", content: body }];

    const reply = await askClaude(messages, env);
    if (!reply) {
      console.error("empty reply from Claude, nothing sent");
      return;
    }

    await sendText(from, reply, env);

    const newHistory = [...messages, { role: "assistant", content: reply }]
      .slice(-MAX_HISTORY_TURNS);
    await env.CHAT_HISTORY.put(histKey, JSON.stringify(newHistory), {
      expirationTtl: HISTORY_TTL_S,
    });
  } catch (e) {
    console.error(`error handling message: ${e}`);
  }
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
  const endpoint =
    `https://api.twilio.com/2010-04-01/Accounts/${env.TWILIO_ACCOUNT_SID}/Messages.json`;
  const auth = "Basic " + btoa(`${env.TWILIO_ACCOUNT_SID}:${env.TWILIO_AUTH_TOKEN}`);

  for (let i = 0; i < text.length; i += WA_TEXT_LIMIT) {
    const form = new URLSearchParams({
      From: env.TWILIO_WHATSAPP_FROM,
      To: `whatsapp:${to}`,
      Body: text.slice(i, i + WA_TEXT_LIMIT),
    });
    const resp = await fetch(endpoint, {
      method: "POST",
      headers: { authorization: auth, "content-type": "application/x-www-form-urlencoded" },
      body: form.toString(),
    });
    if (!resp.ok) {
      throw new Error(`Twilio send ${resp.status}: ${(await resp.text()).slice(0, 300)}`);
    }
  }
}
