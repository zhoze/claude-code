"""
AI & robotics weekly news agent (GitHub Actions edition).

Each run: ChatGPT (Responses API + web search) hunts for the week's most
significant AI and robotics news; Claude evaluates the stories and their
sources (with its own web search for verification); the two models debate;
Claude may request targeted follow-up searches from ChatGPT (bounded rounds);
after a final debate Claude synthesizes the consensus into a data JSON, from
which the markdown report is rendered deterministically. Both files are
written to reports/YYYY-MM-DD.{md,json} and committed by the workflow.

Env vars (GitHub Actions secrets):
  ANTHROPIC_API_KEY, OPENAI_API_KEY

Usage:
  weekly_news.py                normal run (only acts Mon 11:00 Europe/Tallinn)
  weekly_news.py --force        bypass the Monday 11:00 local-time guard
  weekly_news.py --dry-run      print the report, write no files
  weekly_news.py --date DATE    override the run date used in filenames
"""

import argparse
import asyncio
import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI

TALLINN = ZoneInfo("Europe/Tallinn")
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
REPORTS_DIR = BASE_DIR / "reports"

MAX_TOKENS = 8000          # search/eval/debate stages; web search results are large
SYNTH_MAX_TOKENS = 16000   # final synthesis emits the whole data JSON
CALL_TIMEOUT = 900         # seconds per API call (web-search evaluations run long)
DEBATE_LOG_TRUNCATE = 500

STORY_SCHEMA = (
    '{"stories": [{"id": "s1", "title": "...", "url": "https://...", '
    '"source": "publisher name", "published_date": "YYYY-MM-DD", '
    '"category": "ai" or "robotics", "summary": "2-3 sentences", '
    '"why_it_matters": "1-2 sentences"}]}'
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


class Pipeline:
    def __init__(self, ac: AsyncAnthropic, oc: AsyncOpenAI, cfg: dict):
        self.ac = ac
        self.oc = oc
        self.cfg = cfg
        self.debate_log: list[dict] = []
        self.search_rounds = 0

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

    async def ask_gpt(self, prompt: str, system: str, *,
                      use_search: bool = True) -> str:
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
            # repair calls never need search
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

    async def gpt_initial_search(self, week_of: str) -> list[dict]:
        topics = "\n".join(f"- {t}" for t in self.cfg["topics"])
        result = await self.ask_gpt_json(
            f"Search the web for the most significant AI and robotics news of the "
            f"past 7 days (week ending {week_of}). Topics of interest:\n{topics}\n\n"
            f"Use at most {self.cfg['gpt_max_searches_hint']} web searches. Prefer "
            "primary sources and reputable outlets; skip rumor aggregators when a "
            "primary source exists; every URL must be real and reachable.\n\n"
            f"Return ONLY a JSON object with this schema:\n{STORY_SCHEMA}\n"
            f"Include about {self.cfg['target_story_count']} stories with ids s1, s2, ...",
            "You are a diligent tech news researcher. Output only JSON.")
        self.search_rounds += 1
        stories = result.get("stories", [])
        if not stories:
            raise RuntimeError("initial search returned no stories")
        for i, s in enumerate(stories, 1):
            s.setdefault("id", f"s{i}")
        self.log("initial_search", "gpt", json.dumps(stories, ensure_ascii=False))
        return stories

    async def claude_evaluate(self, stories: list[dict]) -> dict:
        result = await self.ask_claude_json(
            "ChatGPT compiled these candidate AI/robotics news stories for the past "
            f"week:\n{json.dumps(stories, ensure_ascii=False, indent=2)}\n\n"
            "Evaluate each story AND the quality of its source. Use web search to "
            "verify major claims, dates and URLs where warranted. Return ONLY JSON:\n"
            '{"assessments": [{"id": "s1", "significance": 1-10, '
            '"source_quality": 1-10, "verified": true/false, '
            '"issues": "problems found or empty string", "notes": "brief"}], '
            '"missing_topics": ["important stories/topics absent from the list"], '
            '"overall_notes": "brief"}',
            "You are a rigorous news evaluator and fact checker. Output only JSON.",
            web_max_uses=self.cfg["claude_web_max_uses"])
        self.log("evaluate", "claude", json.dumps(result, ensure_ascii=False))
        return result

    async def debate_round(self, round_no: int, stories: list[dict],
                           evaluation: dict) -> tuple[list[dict], dict]:
        stories_json = json.dumps(stories, ensure_ascii=False, indent=2)
        eval_json = json.dumps(evaluation, ensure_ascii=False, indent=2)
        gpt_task = self.ask_gpt_json(
            f"Your compiled news stories:\n{stories_json}\n\n"
            f"Claude's evaluation of them:\n{eval_json}\n\n"
            "Defend or concede each criticism. Correct any wrong dates, URLs or "
            "facts (re-search where needed). Drop nothing — revise in place.\n"
            f"Return ONLY the revised stories JSON (same schema):\n{STORY_SCHEMA}",
            "You are debating a rigorous evaluator. Output only JSON.")
        claude_task = self.ask_claude_json(
            f"The candidate stories:\n{stories_json}\n\n"
            f"Your evaluation:\n{eval_json}\n\n"
            "ChatGPT will defend its reporting against your criticism. Anticipate "
            "reasonable defenses and revise your assessments where your criticism "
            "was too harsh or too lenient; spot-check anything still in doubt.\n"
            "Return ONLY the revised evaluation JSON (same schema as your evaluation).",
            "You are debating in good faith to reach the truth. Output only JSON.",
            web_max_uses=2)
        revised_stories_result, revised_eval = await asyncio.gather(gpt_task, claude_task)
        revised = revised_stories_result.get("stories") or stories
        for i, s in enumerate(revised, 1):
            s.setdefault("id", f"s{i}")
        self.log(f"debate_{round_no}", "gpt", json.dumps(revised, ensure_ascii=False))
        self.log(f"debate_{round_no}", "claude", json.dumps(revised_eval, ensure_ascii=False))
        return revised, revised_eval

    async def claude_verdict(self, round_no: int, stories: list[dict],
                             evaluation: dict, remaining: int) -> dict:
        result = await self.ask_claude_json(
            f"Current stories:\n{json.dumps(stories, ensure_ascii=False, indent=2)}\n\n"
            f"Current evaluation:\n{json.dumps(evaluation, ensure_ascii=False, indent=2)}\n\n"
            f"You have {remaining} follow-up search round(s) left. Request follow-up "
            "searches ONLY for genuine gaps: unverified major claims, important "
            "missing topics, or thin sourcing. If coverage is adequate, declare "
            "consensus. Return ONLY JSON:\n"
            '{"consensus_reached": true/false, '
            '"follow_up_queries": ["specific search query", ...], "reason": "brief"}',
            "You decide whether the news coverage is complete enough. Output only JSON.")
        result.setdefault("consensus_reached", True)
        result.setdefault("follow_up_queries", [])
        self.log(f"verdict_{round_no}", "claude", json.dumps(result, ensure_ascii=False))
        return result

    async def gpt_follow_up(self, round_no: int, queries: list[str],
                            stories: list[dict]) -> tuple[list[dict], list[dict]]:
        result = await self.ask_gpt_json(
            f"Existing stories:\n{json.dumps(stories, ensure_ascii=False, indent=2)}\n\n"
            "Claude requested these follow-up searches:\n"
            + "\n".join(f"- {q}" for q in queries) +
            "\n\nRun them. Return ONLY JSON:\n"
            '{"new_stories": [<same story schema as before, ids continuing s'
            f'{len(stories) + 1}...>], '
            '"updates": [{"id": "sN", "correction_or_confirmation": "brief"}]}',
            "You are a diligent tech news researcher. Output only JSON.")
        self.search_rounds += 1
        new = result.get("new_stories", [])
        for i, s in enumerate(new, len(stories) + 1):
            s.setdefault("id", f"s{i}")
        by_id = {s["id"]: s for s in stories}
        for u in result.get("updates", []):
            note = u.get("correction_or_confirmation", "")
            if u.get("id") in by_id and note:
                by_id[u["id"]]["follow_up_note"] = note
        self.log(f"follow_up_{round_no}", "gpt", json.dumps(result, ensure_ascii=False))
        return stories + new, new

    async def claude_synthesize(self, week_of: str, stories: list[dict],
                                evaluation: dict) -> dict:
        result = await self.ask_claude_json(
            f"Week ending {week_of}. Final debated stories:\n"
            f"{json.dumps(stories, ensure_ascii=False, indent=2)}\n\n"
            f"Final evaluation:\n{json.dumps(evaluation, ensure_ascii=False, indent=2)}\n\n"
            "Debate log (stage summaries):\n"
            f"{json.dumps(self.debate_log, ensure_ascii=False, indent=2)}\n\n"
            "As the synthesis judge, merge everything into the final consensus. "
            "Every input story must appear exactly once, either in stories[] or in "
            "excluded[]. Return ONLY JSON:\n"
            '{"executive_summary": "3-5 sentence overview of the week", '
            '"stories": [{"id", "title", "url", "source", "published_date", '
            '"category", "summary" (consensus 2-3 sentences), "why_it_matters", '
            '"gpt_assessment": {"significance": 1-10, "notes": ""}, '
            '"claude_assessment": {"significance": 1-10, "source_quality": 1-10, '
            '"verified": true/false, "notes": ""}, '
            '"consensus": {"significance": 1-10, "verdict": "include", '
            '"disagreements": "empty string if none"}, '
            '"additional_sources": ["url", ...]}], '
            '"excluded": [{"id", "title", "url", "reason"}]}',
            "You are the synthesis judge combining two AI experts' debated findings "
            "into one consensus report. Output only JSON.",
            max_tokens=SYNTH_MAX_TOKENS)
        if not isinstance(result.get("stories"), list) or not result["stories"]:
            raise RuntimeError("synthesis produced no stories")
        self.log("synthesis", "claude", result.get("executive_summary", ""))
        return result

    async def run(self, week_of: str) -> dict:
        stories = await self.gpt_initial_search(week_of)
        evaluation = await self.claude_evaluate(stories)
        stories, evaluation = await self.debate_round(1, stories, evaluation)

        max_rounds = self.cfg["max_follow_up_rounds"]
        for n in range(1, max_rounds + 1):
            verdict = await self.claude_verdict(n, stories, evaluation,
                                               remaining=max_rounds - n + 1)
            if verdict["consensus_reached"] or not verdict["follow_up_queries"]:
                break
            stories, new_stories = await self.gpt_follow_up(
                n, verdict["follow_up_queries"], stories)
            # re-evaluate only the new stories; a full re-evaluation with web
            # search blows past the per-call timeout at 14+ stories
            if new_stories:
                new_eval = await self.claude_evaluate(new_stories)
                evaluation["assessments"] = (evaluation.get("assessments", [])
                                             + new_eval.get("assessments", []))
                evaluation["missing_topics"] = new_eval.get("missing_topics", [])
            stories, evaluation = await self.debate_round(n + 1, stories, evaluation)

        final = await self.claude_synthesize(week_of, stories, evaluation)
        final = {
            "week_of": week_of,
            "generated_at": datetime.now(TALLINN).isoformat(timespec="seconds"),
            "models": {"claude": self.cfg["claude_model"], "gpt": self.cfg["gpt_model"]},
            "search_rounds": self.search_rounds,
            **final,
            "debate_log": self.debate_log,
        }
        return final


def render_markdown(final: dict) -> str:
    def sig(story):
        return story.get("consensus", {}).get("significance") or 0

    included = sorted((s for s in final["stories"]
                       if s.get("consensus", {}).get("verdict") == "include"),
                      key=sig, reverse=True)
    uncertain = [s for s in final["stories"] if s not in included]

    lines = [f"# AI & Robotics Weekly — {final['week_of']}", "",
             final.get("executive_summary", ""), "", "## Top stories", ""]
    for i, s in enumerate(included, 1):
        ca = s.get("claude_assessment", {})
        verified = "verified" if ca.get("verified") else "not independently verified"
        lines += [f"### {i}. {s.get('title', '?')} — significance "
                  f"{sig(s)}/10 ({s.get('category', '?')})",
                  f"**Source:** [{s.get('source', '?')}]({s.get('url', '')}) "
                  f"({s.get('published_date', '?')}) — quality "
                  f"{ca.get('source_quality', '?')}/10, {verified}", "",
                  s.get("summary", ""), "",
                  f"**Why it matters:** {s.get('why_it_matters', '')}"]
        if s.get("consensus", {}).get("disagreements"):
            lines.append(f"\n**Disagreement:** {s['consensus']['disagreements']}")
        if s.get("additional_sources"):
            lines.append("\n**Also:** " + ", ".join(s["additional_sources"]))
        lines.append("")
    if uncertain or final.get("excluded"):
        lines += ["## Excluded or low-confidence items", ""]
        for s in uncertain:
            reason = s.get("consensus", {}).get("disagreements") or "low confidence"
            lines.append(f"- {s.get('title', '?')} ({s.get('url', '')}) — {reason}")
        for e in final.get("excluded", []):
            lines.append(f"- {e.get('title', '?')} ({e.get('url', '')}) — "
                         f"{e.get('reason', '')}")
        lines.append("")
    lines += ["## Method",
              f"Claude `{final['models']['claude']}` + ChatGPT `{final['models']['gpt']}`, "
              f"{final['search_rounds']} search round(s), debate-consensus pipeline. "
              f"Generated {final['generated_at']}."]
    return "\n".join(lines) + "\n"


async def run_pipeline(cfg: dict, week_of: str) -> dict:
    async with AsyncAnthropic() as ac, AsyncOpenAI() as oc:
        return await Pipeline(ac, oc, cfg).run(week_of)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--date", help="override run date (YYYY-MM-DD)")
    args = p.parse_args()

    now = datetime.now(TALLINN)
    force = args.force or os.environ.get("FORCE_RUN") == "true"
    if not (force or args.dry_run) and not (now.weekday() == 0 and now.hour == 11):
        print(f"Not Monday 11:00 Europe/Tallinn (now {now:%a %H:%M}); skipping. "
              "Use --force to run anyway.")
        return

    week_of = args.date or f"{now:%Y-%m-%d}"
    cfg = yaml.safe_load(CONFIG_FILE.read_text())

    try:
        final = asyncio.run(run_pipeline(cfg, week_of))
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    markdown = render_markdown(final)
    if args.dry_run:
        print(markdown)
        return
    REPORTS_DIR.mkdir(exist_ok=True)
    (REPORTS_DIR / f"{week_of}.json").write_text(
        json.dumps(final, ensure_ascii=False, indent=2) + "\n")
    (REPORTS_DIR / f"{week_of}.md").write_text(markdown)
    print(f"Wrote reports/{week_of}.md and .json "
          f"({len(final['stories'])} stories, {final['search_rounds']} search rounds).")


if __name__ == "__main__":
    main()
