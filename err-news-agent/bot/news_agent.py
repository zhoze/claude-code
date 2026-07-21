"""
ERR news trustworthiness agent (GitHub Actions edition).

Each run: fetch the top N stories from each configured source's RSS feed;
for each story, Claude and ChatGPT independently search the web for
corroboration from reputable global outlets, debate their findings in one
critique round, and reach a verdict. A story is "confirmed" only if BOTH
models agree it's trustworthy after the debate. Claude then writes a full
summary for each confirmed story, grounded in the debate's findings. The
digest (confirmed stories with full summaries, plus a "not confirmed"
section with reasons) is emailed via Gmail SMTP. Already-processed stories
(by RSS guid) are skipped for `dedup_days` to avoid re-checking/re-emailing
stories that linger at the top of a feed.

Env vars (GitHub Actions secrets):
  ANTHROPIC_API_KEY, OPENAI_API_KEY, EMAIL_ADDRESS, EMAIL_APP_PASSWORD

Usage:
  news_agent.py                normal run (only acts at run_hour_tallinn)
  news_agent.py --force        bypass the local-time guard
  news_agent.py --dry-run      print the digest, no email, no state write
  news_agent.py --date DATE    override the run date used in state/logs
"""

import argparse
import asyncio
import json
import os
import smtplib
import sys
import traceback
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

TALLINN = ZoneInfo("Europe/Tallinn")
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
STATE_FILE = BASE_DIR / "state" / "sent_guids.json"

MAX_TOKENS = 1500          # trust-check / debate calls
SUMMARY_MAX_TOKENS = 1200  # full-summary call
CALL_TIMEOUT = 300         # seconds per API call
DEBATE_LOG_TRUNCATE = 500

ASSESSMENT_SCHEMA = (
    '{"trustworthy": true/false, "confidence": 1-10, '
    '"corroborating_sources": ["url", ...], "reasoning": "2-3 sentences"}'
)


def extract_json(text: str) -> dict:
    """Parse a JSON object out of model output: direct, fence-stripped, or sliced."""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[-1]
        if stripped.rstrip().endswith("```"):
            stripped = stripped.rstrip()[:-3]
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])  # raises with a useful message
    raise ValueError(f"no JSON object found in model output: {text[:200]!r}")


def fetch_top_stories(source: dict, top_n: int, seen_guids: set) -> list[dict]:
    """Top N unseen <item>s from a source's RSS feed, in feed order."""
    r = requests.get(source["rss_url"], timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.text)
    stories = []
    for item in root.findall("./channel/item"):
        link = (item.findtext("link") or "").strip()
        guid = (item.findtext("guid") or link).strip()
        if not link or guid in seen_guids:
            continue
        stories.append({
            "guid": guid,
            "source": source["name"],
            "title": (item.findtext("title") or "").strip(),
            "link": link,
            "description": (item.findtext("description") or "").strip(),
            "pub_date": (item.findtext("pubDate") or "").strip(),
            "category": (item.findtext("category") or "").strip(),
        })
        if len(stories) >= top_n:
            break
    return stories


class Pipeline:
    def __init__(self, ac: AsyncAnthropic, oc: AsyncOpenAI, cfg: dict):
        self.ac = ac
        self.oc = oc
        self.cfg = cfg
        self.debate_log: list[dict] = []

    def log(self, stage: str, model: str, text: str):
        summary = " ".join(text.split())[:DEBATE_LOG_TRUNCATE]
        self.debate_log.append({"stage": stage, "model": model, "summary": summary})
        print(f"[{stage}] {model}: {summary[:120]}")

    async def ask_claude(self, prompt: str, system: str, *,
                         max_tokens: int = MAX_TOKENS, web_max_uses: int = 0) -> str:
        kwargs = {}
        if web_max_uses > 0:
            kwargs["tools"] = [{"type": "web_search_20260318", "name": "web_search",
                                "max_uses": web_max_uses}]
        resp = await asyncio.wait_for(
            self.ac.messages.create(
                model=self.cfg["claude_model"], max_tokens=max_tokens,
                system=system, messages=[{"role": "user", "content": prompt}],
                **kwargs),
            CALL_TIMEOUT)
        return "".join(b.text for b in resp.content if b.type == "text")

    async def ask_gpt(self, prompt: str, system: str, *, use_search: bool = True) -> str:
        resp = await asyncio.wait_for(
            self.oc.responses.create(
                model=self.cfg["gpt_model"], max_output_tokens=MAX_TOKENS,
                instructions=system, input=prompt,
                tools=[{"type": "web_search"}] if use_search else []),
            CALL_TIMEOUT)
        return resp.output_text

    async def _json_call(self, ask, prompt: str, system: str, **kwargs) -> dict:
        text = await ask(prompt, system, **kwargs)
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            repair = (f"Your previous output was not valid JSON ({e}).\n\n"
                      f"Previous output:\n{text}\n\n"
                      "Output ONLY the corrected JSON object — no prose, no code fences.")
            plain = {k: v for k, v in kwargs.items()
                     if k not in ("web_max_uses", "use_search")}
            if ask is self.ask_gpt:
                plain["use_search"] = False
            fixed = await ask(repair, "You output only valid JSON.", **plain)
            return extract_json(fixed)

    async def ask_claude_json(self, prompt, system, **kwargs) -> dict:
        return await self._json_call(self.ask_claude, prompt, system, **kwargs)

    async def ask_gpt_json(self, prompt, system, **kwargs) -> dict:
        return await self._json_call(self.ask_gpt, prompt, system, **kwargs)

    # ---- stages ----------------------------------------------------------

    def _story_block(self, story: dict) -> str:
        return (f"Title: {story['title']}\n"
                f"Source: {story['source']} — {story['link']}\n"
                f"Published: {story['pub_date']}\n"
                f"Article summary: {story['description']}")

    async def claude_trust_check(self, story: dict) -> dict:
        result = await self.ask_claude_json(
            f"A news outlet published this story:\n{self._story_block(story)}\n\n"
            "Use web search to check whether this story is corroborated by "
            "reputable global news sources (e.g. Reuters, AP, BBC, AFP, or other "
            "established outlets — not blogs, forums, or unverified social media). "
            f"Return ONLY JSON:\n{ASSESSMENT_SCHEMA}",
            "You are a rigorous fact-checker verifying news against reputable "
            "global sources. Output only JSON.",
            web_max_uses=self.cfg["claude_web_max_uses"])
        self.log("trust_check", "claude", json.dumps(result, ensure_ascii=False))
        return result

    async def gpt_trust_check(self, story: dict) -> dict:
        result = await self.ask_gpt_json(
            f"A news outlet published this story:\n{self._story_block(story)}\n\n"
            "Use web search to check whether this story is corroborated by "
            "reputable global news sources (e.g. Reuters, AP, BBC, AFP, or other "
            "established outlets — not blogs, forums, or unverified social media). "
            f"Return ONLY JSON:\n{ASSESSMENT_SCHEMA}",
            "You are a rigorous fact-checker verifying news against reputable "
            "global sources. Output only JSON.")
        self.log("trust_check", "gpt", json.dumps(result, ensure_ascii=False))
        return result

    async def debate_round(self, story: dict, claude_assess: dict,
                           gpt_assess: dict) -> tuple[dict, dict]:
        story_block = self._story_block(story)
        gpt_task = self.ask_gpt_json(
            f"Story:\n{story_block}\n\n"
            f"Your assessment:\n{json.dumps(gpt_assess, ensure_ascii=False)}\n\n"
            f"Claude's assessment:\n{json.dumps(claude_assess, ensure_ascii=False)}\n\n"
            "Consider Claude's assessment. Revise your own verdict if warranted "
            "(re-search if needed); defend it if you still disagree.\n"
            f"Return ONLY your revised assessment JSON:\n{ASSESSMENT_SCHEMA}",
            "You are debating a rigorous fact-checker in good faith. Output only JSON.")
        claude_task = self.ask_claude_json(
            f"Story:\n{story_block}\n\n"
            f"Your assessment:\n{json.dumps(claude_assess, ensure_ascii=False)}\n\n"
            f"ChatGPT's assessment:\n{json.dumps(gpt_assess, ensure_ascii=False)}\n\n"
            "Consider ChatGPT's assessment. Revise your own verdict if warranted "
            "(re-search if needed); defend it if you still disagree.\n"
            f"Return ONLY your revised assessment JSON:\n{ASSESSMENT_SCHEMA}",
            "You are debating in good faith to reach the truth. Output only JSON.",
            web_max_uses=2)
        revised_gpt, revised_claude = await asyncio.gather(gpt_task, claude_task)
        self.log("debate", "gpt", json.dumps(revised_gpt, ensure_ascii=False))
        self.log("debate", "claude", json.dumps(revised_claude, ensure_ascii=False))
        return revised_claude, revised_gpt

    async def claude_full_summary(self, story: dict, claude_assess: dict,
                                  gpt_assess: dict) -> str:
        sources = list(dict.fromkeys(
            claude_assess.get("corroborating_sources", [])
            + gpt_assess.get("corroborating_sources", [])))
        summary = await self.ask_claude(
            f"Story:\n{self._story_block(story)}\n\n"
            f"Fact-check findings (Claude):\n{json.dumps(claude_assess, ensure_ascii=False)}\n\n"
            f"Fact-check findings (ChatGPT):\n{json.dumps(gpt_assess, ensure_ascii=False)}\n\n"
            f"Corroborating sources found: {sources}\n\n"
            "Write a full, detailed summary of this story (4-6 sentences, several "
            "paragraphs if warranted) for a news digest email. Cover the key facts, "
            "context, and why it matters. Base it on the article summary and the "
            "corroborating findings above — do not invent details. Plain text, no "
            "markdown, no preamble.",
            "You are a careful news editor writing a digest summary.",
            max_tokens=SUMMARY_MAX_TOKENS)
        self.log("summary", "claude", summary)
        return summary.strip()

    async def process_story(self, story: dict) -> dict:
        claude_assess, gpt_assess = await asyncio.gather(
            self.claude_trust_check(story), self.gpt_trust_check(story))
        claude_assess, gpt_assess = await self.debate_round(story, claude_assess, gpt_assess)

        confirmed = bool(claude_assess.get("trustworthy")) and bool(gpt_assess.get("trustworthy"))
        result = {**story, "claude_assessment": claude_assess, "gpt_assessment": gpt_assess}
        if confirmed:
            result["summary"] = await self.claude_full_summary(story, claude_assess, gpt_assess)
        else:
            reasons = [a.get("reasoning", "") for a in (claude_assess, gpt_assess) if not a.get("trustworthy")]
            result["reason"] = " / ".join(r for r in reasons if r) or "not corroborated by both models"
        result["confirmed"] = confirmed
        return result

    async def run(self, stories: list[dict]) -> list[dict]:
        results = []
        for story in stories:
            try:
                results.append(await self.process_story(story))
            except Exception as e:
                print(f"Warning: processing failed for {story['guid']}: {e}", file=sys.stderr)
                traceback.print_exc()
        return results


def render_email(results: list[dict], run_date: str) -> tuple[str, str]:
    confirmed = [r for r in results if r["confirmed"]]
    excluded = [r for r in results if not r["confirmed"]]

    story_word = "story" if len(confirmed) == 1 else "stories"
    subject = f"News Trustworthiness Digest — {len(confirmed)} trustworthy {story_word} — {run_date}"

    lines = [f"News Trustworthiness Digest — {run_date}", ""]
    if confirmed:
        lines.append("=== Confirmed trustworthy stories ===")
        lines.append("")
        for i, r in enumerate(confirmed, 1):
            ca, ga = r["claude_assessment"], r["gpt_assessment"]
            sources = list(dict.fromkeys(
                ca.get("corroborating_sources", []) + ga.get("corroborating_sources", [])))
            lines += [f"{i}. {r['title']}", f"   Source: {r['source']} — {r['link']}", "",
                      r["summary"], ""]
            if sources:
                lines.append("   Corroborating sources: " + ", ".join(sources))
            lines.append("")
    else:
        lines += ["No stories were confirmed trustworthy by both models today.", ""]

    if excluded:
        lines += ["=== Not confirmed ===", ""]
        for r in excluded:
            lines.append(f"- {r['title']} ({r['link']}) — {r['reason']}")
        lines.append("")

    return subject, "\n".join(lines)


def send_email(subject: str, body: str, recipient: str):
    sender = os.environ["EMAIL_ADDRESS"].strip()
    password = os.environ["EMAIL_APP_PASSWORD"]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=60) as smtp:
        smtp.login(sender, password)
        smtp.sendmail(sender, [recipient], msg.as_string())


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {}


def save_state(state: dict, today: date, dedup_days: int):
    cutoff = (today - timedelta(days=dedup_days)).isoformat()
    pruned = {guid: sent for guid, sent in state.items() if sent >= cutoff}
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n")


async def run_pipeline(cfg: dict, stories: list[dict]) -> list[dict]:
    async with AsyncAnthropic() as ac, AsyncOpenAI() as oc:
        return await Pipeline(ac, oc, cfg).run(stories)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--date", help="override run date (YYYY-MM-DD)")
    args = p.parse_args()

    now = datetime.now(TALLINN)
    cfg = yaml.safe_load(CONFIG_FILE.read_text())
    force = args.force or os.environ.get("FORCE_RUN") == "true"
    if not (force or args.dry_run) and now.hour != cfg["run_hour_tallinn"]:
        print(f"Not {cfg['run_hour_tallinn']}:00 Europe/Tallinn (now {now:%a %H:%M}); "
              "skipping. Use --force to run anyway.")
        return

    run_date = args.date or f"{now:%Y-%m-%d}"
    today = date.fromisoformat(run_date)
    state = load_state()
    seen_guids = set(state.keys())

    stories = []
    for source in cfg["sources"]:
        stories += fetch_top_stories(source, cfg["top_n"], seen_guids)

    if not stories:
        print("No new stories since last run.")
        return

    try:
        results = asyncio.run(run_pipeline(cfg, stories))
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    if not results:
        print("All stories failed processing; nothing to send.")
        sys.exit(1)

    subject, body = render_email(results, run_date)

    if args.dry_run:
        print(f"\nSubject: {subject}\n\n{body}")
        return

    send_email(subject, body, cfg["recipient_email"])
    for r in results:
        state[r["guid"]] = run_date
    save_state(state, today, cfg["dedup_days"])
    confirmed_n = sum(1 for r in results if r["confirmed"])
    print(f"Sent digest: {confirmed_n} confirmed, {len(results) - confirmed_n} excluded "
          f"({len(results)} processed).")


if __name__ == "__main__":
    main()
