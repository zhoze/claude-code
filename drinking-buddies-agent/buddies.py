"""
Drinking Buddies — a three-way bar chat between you, API Claude, and API ChatGPT.

You say something, Claude answers first, then ChatGPT reacts with his own take
(seeing Claude's answer), then it's your turn again. Round and round.

Env vars: ANTHROPIC_API_KEY, OPENAI_API_KEY

Usage:
  python buddies.py                          # interactive session
  echo "some topic" | python buddies.py      # scripted / smoke test
"""

import sys
import asyncio
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

CLAUDE_MODEL = "claude-opus-5"
OPENAI_MODEL = "gpt-5.6-terra"
MAX_TOKENS = 1000

BASE_PERSONA = (
    "You are {name}, one of three friends hanging out at a bar: the user, Claude "
    "(an Anthropic AI), and ChatGPT (an OpenAI AI). This is a relaxed, fun "
    "conversation between buddies over drinks — NOT an assistant session.\n"
    "Rules of the table:\n"
    "- Talk like a real friend: casual, opinionated, humorous. Have actual takes.\n"
    "- Keep it short and punchy — a few sentences, not an essay. No bullet lists, "
    "no headers, no assistant-speak like 'Great question!' or 'I'd be happy to'.\n"
    "- Disagree freely and tease the other AI when you think they're wrong. "
    "Friendly ribbing is encouraged; being boring is the only sin.\n"
    "- Stay on whatever the user brought up, but riff naturally.\n"
    "- Always reply in the same language the user last spoke (Estonian in, "
    "Estonian out; English in, English out).\n"
    "- Reply as yourself only. Do not write lines for the others, and do not "
    "prefix your reply with your own name."
)

CLAUDE_SYSTEM = BASE_PERSONA.format(name="Claude") + (
    "\nYou always get first crack at whatever the user says — give your honest "
    "take, knowing ChatGPT will pile on right after you."
)

GPT_SYSTEM = BASE_PERSONA.format(name="ChatGPT") + (
    "\nClaude has just given his take on what the user said. React with your own "
    "opinion — agree, push back, or roast him a little, but bring something new "
    "to the table."
)

_anthropic_client = None
_openai_client = None


def anthropic_client() -> AsyncAnthropic:
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = AsyncAnthropic()
    return _anthropic_client


def openai_client() -> AsyncOpenAI:
    global _openai_client
    if _openai_client is None:
        _openai_client = AsyncOpenAI()
    return _openai_client


def render_transcript(transcript: list[tuple[str, str]]) -> str:
    return "\n\n".join(f"{speaker}: {text}" for speaker, text in transcript)


async def ask_claude(transcript: list[tuple[str, str]]) -> str:
    resp = await anthropic_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=MAX_TOKENS,
        system=CLAUDE_SYSTEM,
        messages=[{
            "role": "user",
            "content": "Conversation so far:\n\n" + render_transcript(transcript)
                       + "\n\nYour turn, Claude.",
        }],
    )
    return "".join(b.text for b in resp.content if b.type == "text").strip()


async def ask_gpt(transcript: list[tuple[str, str]]) -> str:
    resp = await openai_client().responses.create(
        model=OPENAI_MODEL,
        max_output_tokens=MAX_TOKENS * 2,  # headroom: reasoning counts against the cap
        instructions=GPT_SYSTEM,
        input="Conversation so far:\n\n" + render_transcript(transcript)
              + "\n\nYour turn, ChatGPT.",
        reasoning={"effort": "low"},
    )
    return resp.output_text.strip()


async def buddy_turn(name, ask, transcript) -> None:
    try:
        reply = await ask(transcript)
    except Exception as e:
        reply = f"(spills drink) Sorry, I glitched: {type(e).__name__}: {e}"
    transcript.append((name, reply))
    print(f"\n🍺 {name}: {reply}", flush=True)


async def main() -> None:
    transcript: list[tuple[str, str]] = []
    interactive = sys.stdin.isatty()
    if interactive:
        print("🍻 Welcome to the bar. Claude and ChatGPT are already a beer in.")
        print("   Say something (exit/quit to leave).\n")

    while True:
        try:
            if interactive:
                user_text = input("You: ").strip()
            else:
                line = sys.stdin.readline()
                if not line:
                    break
                user_text = line.strip()
                if user_text:
                    print(f"You: {user_text}", flush=True)
        except EOFError:
            break
        if not user_text:
            continue
        if user_text.lower() in ("exit", "quit"):
            break

        transcript.append(("You", user_text))
        await buddy_turn("Claude", ask_claude, transcript)
        await buddy_turn("ChatGPT", ask_gpt, transcript)
        if interactive:
            print()

    if interactive:
        print("\n🍻 Closing the tab. See you next round.")


if __name__ == "__main__":
    asyncio.run(main())
