"""
Telegram Debate Bot — GitHub Actions edition (stateless polling).

Each run: fetch new Telegram messages via getUpdates, have Claude and ChatGPT
debate each question, send the consensus answer, persist the update offset.

Env vars (GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY
Optional:
  ALLOWED_CHAT_ID  — restrict the bot to the owner's chat (recommended)
"""

import os
import sys
import asyncio
import requests
from pathlib import Path
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

CLAUDE_MODEL = "claude-sonnet-5"
OPENAI_MODEL = "gpt-5.6-terra"   # balanced tier of the GPT-5.6 family (Jul 2026)
DEBATE_ROUNDS = 1
MAX_TOKENS = 3000        # nominal answer size
API_TOKEN_CAP = MAX_TOKENS * 2   # headroom: search/reasoning overhead counts against the cap

TG = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}"
OFFSET_FILE = Path(__file__).resolve().parent.parent / "state" / "offset.txt"
ALLOWED_CHAT_ID = os.environ.get("ALLOWED_CHAT_ID")

anthropic_client = AsyncAnthropic()
openai_client = AsyncOpenAI()


async def ask_claude(prompt: str, system: str = "") -> str:
    resp = await anthropic_client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=API_TOKEN_CAP,
        system=system or "You are a careful expert assistant. Be concise and correct.",
        messages=[{"role": "user", "content": prompt}],
        tools=[{"type": "web_search_20260318", "name": "web_search", "max_uses": 3}],
    )
    return "".join(b.text for b in resp.content if b.type == "text")


async def ask_gpt(prompt: str, system: str = "") -> str:
    # Responses API: web search is not available via Chat Completions
    resp = await openai_client.responses.create(
        model=OPENAI_MODEL,
        max_output_tokens=API_TOKEN_CAP,
        instructions=system or "You are a careful expert assistant. Be concise and correct.",
        input=prompt,
        tools=[{"type": "web_search"}],
    )
    return resp.output_text


async def debate(question: str) -> str:
    claude_ans, gpt_ans = await asyncio.gather(ask_claude(question), ask_gpt(question))

    template = (
        "Original question:\n{q}\n\nYour previous answer:\n{own}\n\n"
        "Another AI's answer:\n{other}\n\n"
        "Compare the two, note anything wrong or missing, then output ONLY your improved answer."
    )
    for _ in range(DEBATE_ROUNDS):
        claude_ans, gpt_ans = await asyncio.gather(
            ask_claude(template.format(q=question, own=claude_ans, other=gpt_ans)),
            ask_gpt(template.format(q=question, own=gpt_ans, other=claude_ans)),
        )

    return await ask_claude(
        f"Question:\n{question}\n\nAnswer A:\n{claude_ans}\n\nAnswer B:\n{gpt_ans}\n\n"
        "Merge these into the single best answer. Where they disagree, say which "
        "position is better supported and why. Be direct and practical.",
        system="You are a synthesis judge combining two expert answers.",
    )


def send(chat_id: int, text: str):
    for i in range(0, len(text), 4000):
        requests.post(f"{TG}/sendMessage", json={"chat_id": chat_id, "text": text[i:i + 4000]}, timeout=30)


def main():
    me = requests.get(f"{TG}/getMe", timeout=30).json()
    print(f"Bot: @{me.get('result', {}).get('username')} (ok={me.get('ok')})")

    offset = int(OFFSET_FILE.read_text().strip() or 0)
    r = requests.get(f"{TG}/getUpdates", params={"offset": offset + 1, "timeout": 0}, timeout=30).json()
    if not r.get("ok"):
        print(f"getUpdates error: {r.get('error_code')} {r.get('description')}", file=sys.stderr)
    updates = r.get("result", [])
    if not updates:
        print("No new messages.")
        return

    for u in updates:
        offset = max(offset, u["update_id"])
        msg = u.get("message") or {}
        text = msg.get("text")
        chat_id = msg.get("chat", {}).get("id")
        if not text or not chat_id:
            continue
        if ALLOWED_CHAT_ID and str(chat_id) != ALLOWED_CHAT_ID:
            print("Ignoring message from non-allowed chat.")
            continue

        print(f"Question: {text[:80]}")
        try:
            answer = asyncio.run(debate(text))
            send(chat_id, "✅ Consensus answer:\n" + answer)
        except Exception as e:
            send(chat_id, f"Error: {e}")
            print(f"Error: {e}", file=sys.stderr)

    OFFSET_FILE.write_text(str(offset))
    print(f"Done. New offset: {offset}")


if __name__ == "__main__":
    main()
