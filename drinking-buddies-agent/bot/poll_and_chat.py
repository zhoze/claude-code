"""
Drinking Buddies Telegram bot — GitHub Actions edition (stateless polling).

Each run: fetch new Telegram messages via getUpdates, run one bar round per
message (user -> Claude -> ChatGPT), send both replies, persist the update
offset and the shared transcript memory.

Env vars (GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY
Optional:
  ALLOWED_CHAT_ID  — restrict the bot to the owner's chat (recommended)
"""

import os
import sys
import time
import asyncio
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import buddies  # noqa: E402  (shared personas, models, memory)

TG = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}"
OFFSET_FILE = Path(__file__).resolve().parent.parent / "state" / "offset.txt"
ALLOWED_CHAT_ID = os.environ.get("ALLOWED_CHAT_ID")
TURN_TIMEOUT_S = 120   # per buddy reply
RUN_BUDGET_S = 300     # stop taking new messages after this; the rest stay queued


def send(chat_id: int, text: str) -> None:
    for i in range(0, len(text), 4000):
        requests.post(
            f"{TG}/sendMessage",
            json={"chat_id": chat_id, "text": text[i:i + 4000]},
            timeout=30,
        )


async def bar_round(chat_id: int, user_text: str) -> None:
    transcript = buddies.load_transcript()
    transcript.append(("You", user_text))
    buddies.save_transcript(transcript)
    for name, ask in (("Claude", buddies.ask_claude), ("ChatGPT", buddies.ask_gpt)):
        try:
            reply = await asyncio.wait_for(ask(transcript), TURN_TIMEOUT_S)
        except Exception as e:
            reply = f"(spills drink) Sorry, I glitched: {type(e).__name__}: {e}"
        transcript.append((name, reply))
        buddies.save_transcript(transcript)
        send(chat_id, f"🍺 {name}: {reply}")


def main() -> None:
    run_start = time.monotonic()
    me = requests.get(f"{TG}/getMe", timeout=30).json()
    print(f"Bot: @{me.get('result', {}).get('username')} (ok={me.get('ok')})")

    offset = int(OFFSET_FILE.read_text().strip() or 0) if OFFSET_FILE.exists() else 0
    r = requests.get(
        f"{TG}/getUpdates", params={"offset": offset + 1, "timeout": 0}, timeout=30
    ).json()
    if not r.get("ok"):
        print(f"getUpdates error: {r.get('error_code')} {r.get('description')}", file=sys.stderr)
    updates = r.get("result", [])
    if not updates:
        print("No new messages.")
        return

    for u in updates:
        if time.monotonic() - run_start > RUN_BUDGET_S:
            print("Run budget exhausted; remaining messages stay queued for the next run.")
            break
        offset = max(offset, u["update_id"])
        msg = u.get("message") or {}
        text = msg.get("text")
        chat_id = msg.get("chat", {}).get("id")
        if not text or not chat_id:
            continue
        if ALLOWED_CHAT_ID and str(chat_id) != ALLOWED_CHAT_ID:
            print("Ignoring message from non-allowed chat.")
            continue
        if text.strip() == "/start":
            send(chat_id, "🍻 Tere tulemast baari! Claude ja ChatGPT on juba ühe õlle ees. "
                          "Ütle midagi — 'forget' pühib mälu.")
            continue
        if text.strip().lower() == "forget":
            buddies.save_transcript([])
            send(chat_id, "🫧 Mälu pühitud. Semud ärkavad, teadmata mis eile juhtus.")
            continue

        print(f"Message: {text[:80]}")
        try:
            asyncio.run(bar_round(chat_id, text))
        except Exception as e:
            send(chat_id, f"Error: {type(e).__name__}: {e}")
            print(f"Error: {type(e).__name__}: {e}", file=sys.stderr)

    OFFSET_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFSET_FILE.write_text(str(offset))
    print(f"Done. New offset: {offset}")


if __name__ == "__main__":
    main()
