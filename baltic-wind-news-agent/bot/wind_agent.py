"""
Baltic wind energy news agent (GitHub Actions edition).

Each workday run: harvest candidate articles about wind-farm development in
Estonia, Latvia and Lithuania from RSS feeds and listing pages (config.yaml),
plus a per-country Claude web-search discovery sweep for anything the fixed
sources miss. Claude then extracts planned wind-farm PROJECTS from the
candidates and marks each as new or already known against the permanent
project registry (state/reported_projects.json). A plain-text report of new
projects — grouped by country, each with actual news links — is emailed via
Gmail SMTP. The email is sent even when nothing new was found. New projects
are appended to the registry (never pruned); processed article URLs go to
state/seen_urls.json for `seen_url_days` so they aren't re-fetched.

Env vars (GitHub Actions secrets):
  ANTHROPIC_API_KEY, EMAIL_ADDRESS, EMAIL_APP_PASSWORD

Usage:
  wind_agent.py                normal run (only acts 09:30 Europe/Tallinn, Mon-Fri)
  wind_agent.py --force        bypass the local-time guard
  wind_agent.py --dry-run      print the report, no email, no state write
  wind_agent.py --date DATE    override the run date used in state/logs
"""

import argparse
import html
import json
import os
import re
import smtplib
import sys
import time
import traceback
import unicodedata
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import requests
import yaml
from anthropic import Anthropic

TALLINN = ZoneInfo("Europe/Tallinn")
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
REGISTRY_FILE = BASE_DIR / "state" / "reported_projects.json"
SEEN_URLS_FILE = BASE_DIR / "state" / "seen_urls.json"
LAST_RUN_FILE = BASE_DIR / "state" / "last_run.json"

CALL_TIMEOUT = 300           # seconds per API call (extraction)
DISCOVERY_CALL_TIMEOUT = 180  # seconds per discovery web-search call
DISCOVERY_DEADLINE = 840     # stop starting discovery calls this far into the run
DISCOVERY_MAX_TOKENS = 2500
EXTRACTION_MAX_TOKENS = 8000
SNIPPET_LEN = 300
TITLE_LEN = 200

COUNTRY_NAMES = {"EE": "Estonia", "LV": "Latvia", "LT": "Lithuania"}

HEADERS = {"User-Agent": ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}


def extract_json(text: str):
    """Parse a JSON value out of model output: direct, fence-stripped, or sliced."""
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
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                continue
    raise ValueError(f"no JSON found in model output: {text[:200]!r}")


def normalize_key(name: str, country: str) -> str:
    """Stable registry key: ascii-folded, lowercase, hyphenated + country code."""
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")
    return f"{slug}-{country.lower()}"


def matches_keywords(text: str, keywords: list[str]) -> bool:
    lowered = text.lower()
    return any(k.lower() in lowered for k in keywords)


# ---- candidate harvesting --------------------------------------------------

def fetch_rss_candidates(source: dict, keywords: list[str], seen_urls: set) -> list[dict]:
    r = requests.get(source["rss_url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    root = ET.fromstring(r.content.strip())  # some feeds lead with whitespace
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").strip()
        title = (item.findtext("title") or "").strip()
        desc = re.sub(r"<[^>]+>", " ", item.findtext("description") or "").strip()
        if not link or link in seen_urls:
            continue
        if not matches_keywords(f"{title} {desc} {link}", keywords):
            continue
        out.append({
            "url": link,
            "title": title[:TITLE_LEN],
            "snippet": desc[:SNIPPET_LEN],
            "source": source["name"],
            "published": (item.findtext("pubDate") or "").strip(),
            "origin": "rss",
        })
    return out


ANCHOR_RE = re.compile(r'<a\s[^>]*href="([^"#]+)"[^>]*>(.*?)</a>', re.I | re.S)


def fetch_listing_candidates(page: dict, keywords: list[str], seen_urls: set,
                             max_links: int) -> list[dict]:
    r = requests.get(page["url"], headers=HEADERS, timeout=30)
    r.raise_for_status()
    out, taken = [], set()
    for href, inner in ANCHOR_RE.findall(r.text):
        text = html.unescape(re.sub(r"<[^>]+>", " ", inner))
        text = " ".join(text.split())
        url = urljoin(page["url"], html.unescape(href.strip()))
        if not url.startswith("http") or url in seen_urls or url in taken:
            continue
        # Skip nav/boilerplate anchors: require a real title and a wind word.
        if len(text) < 20 or not matches_keywords(f"{text} {url}", keywords):
            continue
        taken.add(url)
        out.append({
            "url": url,
            "title": text[:TITLE_LEN],
            "snippet": "",
            "source": page["name"],
            "published": "",
            "origin": "listing",
        })
        if len(out) >= max_links:
            break
    return out


# ---- Claude calls ----------------------------------------------------------

class Agent:
    def __init__(self, client: Anthropic, cfg: dict):
        self.client = client
        self.cfg = cfg

    def ask(self, prompt: str, system: str, *, max_tokens: int,
            web_max_uses: int = 0, timeout: int = CALL_TIMEOUT) -> str:
        kwargs = {}
        if web_max_uses > 0:
            kwargs["tools"] = [{"type": "web_search_20260318", "name": "web_search",
                                "max_uses": web_max_uses}]
        resp = self.client.messages.create(
            model=self.cfg["claude_model"], max_tokens=max_tokens,
            system=system, messages=[{"role": "user", "content": prompt}],
            timeout=timeout, **kwargs)
        return "".join(b.text for b in resp.content if b.type == "text")

    def ask_json(self, prompt: str, system: str, **kwargs):
        text = self.ask(prompt, system, **kwargs)
        try:
            return extract_json(text)
        except (ValueError, json.JSONDecodeError) as e:
            repair = (f"Your previous output was not valid JSON ({e}).\n\n"
                      f"Previous output:\n{text}\n\n"
                      "Output ONLY the corrected JSON — no prose, no code fences.")
            fixed = self.ask(repair, "You output only valid JSON.",
                             max_tokens=kwargs.get("max_tokens", EXTRACTION_MAX_TOKENS),
                             timeout=kwargs.get("timeout", CALL_TIMEOUT))
            return extract_json(fixed)

    def discover(self, country_code: str, run_date: str, seen_urls: set) -> list[dict]:
        """Web-search sweep for recent planned-project news in one country."""
        country = COUNTRY_NAMES[country_code]
        local_hint = {
            "EE": 'Search in English and Estonian (e.g. "tuulepark", "meretuulepark").',
            "LV": 'Search in English and Latvian (e.g. "vēja parks", "vēja elektrostaciju parks").',
            "LT": 'Search in English and Lithuanian (e.g. "vėjo parkas", "vėjo elektrinių parkas").',
        }[country_code]
        result = self.ask_json(
            f"Today is {run_date}. Use web search to find news published within "
            f"the last 7 days about NEWLY ANNOUNCED or PLANNED wind farm projects "
            f"(onshore or offshore) in {country}: new project announcements, "
            f"development permits, special/local plans initiated, environmental "
            f"impact assessments started, seabed/land auctions won, turbine "
            f"orders for new parks. {local_hint}\n"
            "Ignore: opinion/policy pieces without a specific project, "
            "construction progress or financing of long-known projects, and "
            "projects outside Estonia/Latvia/Lithuania.\n"
            "Return ONLY a JSON array (possibly empty), each element:\n"
            '{"title": "...", "url": "https://...", "source": "outlet name", '
            '"snippet": "1-2 sentence gist"}\n'
            "Only include real URLs you actually found via search.",
            "You are a wind-energy market monitor for the Baltic states. "
            "Output only JSON.",
            max_tokens=DISCOVERY_MAX_TOKENS,
            web_max_uses=self.cfg["discovery_web_max_uses"],
            timeout=DISCOVERY_CALL_TIMEOUT)
        out = []
        if isinstance(result, list):
            for item in result:
                url = str(item.get("url", "")).strip()
                if not url.startswith("http") or url in seen_urls:
                    continue
                out.append({
                    "url": url,
                    "title": str(item.get("title", ""))[:TITLE_LEN],
                    "snippet": str(item.get("snippet", ""))[:SNIPPET_LEN],
                    "source": str(item.get("source", "web search"))[:80],
                    "published": "",
                    "origin": f"discovery-{country_code}",
                })
        return out

    def extract_new_projects(self, candidates: list[dict],
                             registry: list[dict], run_date: str) -> list[dict]:
        """Classify candidates into projects; keep only ones not in the registry."""
        known = [{"name": p["name"], "country": p["country"],
                  "aliases": p.get("aliases", [])} for p in registry]
        cand_lines = [
            {"id": i, "title": c["title"], "url": c["url"],
             "source": c["source"], "snippet": c["snippet"]}
            for i, c in enumerate(candidates)
        ]
        result = self.ask_json(
            f"Today is {run_date}. You monitor PLANNED wind farm projects in "
            "Estonia (EE), Latvia (LV) and Lithuania (LT).\n\n"
            f"CANDIDATE ARTICLES (JSON):\n{json.dumps(cand_lines, ensure_ascii=False)}\n\n"
            f"ALREADY-REPORTED PROJECT REGISTRY (JSON):\n{json.dumps(known, ensure_ascii=False)}\n\n"
            "Task: identify wind farm projects that the candidate articles are "
            "about, and return ONLY the projects that are NOT in the registry "
            "(match generously by name/alias/location — 'Pienava Wind' matches "
            "'Pienava'; the same project reported by several outlets is ONE "
            "project). A project qualifies only if it is a specific planned, "
            "newly announced, newly permitted or newly contracted wind farm "
            "(onshore or offshore) in EE/LV/LT. Exclude: policy/market stories "
            "without a specific project, construction/financing progress on "
            "registry projects, solar-only projects, and anything outside the "
            "three countries.\n"
            "Return ONLY JSON:\n"
            '{"new_projects": [{"name": "...", "country": "EE|LV|LT", '
            '"developer": "...", "capacity_mw": number or null, '
            '"status": "short status, e.g. EIA started / permit granted", '
            '"summary": "2-3 sentences", "aliases": ["..."], '
            '"candidate_ids": [0, 3]}]}\n'
            "candidate_ids must list every candidate article about that "
            "project. If nothing qualifies, return {\"new_projects\": []}.",
            "You are a meticulous wind-energy analyst. Output only JSON.",
            max_tokens=EXTRACTION_MAX_TOKENS)
        projects = []
        if isinstance(result, dict):
            for p in result.get("new_projects", []):
                country = str(p.get("country", "")).upper()
                name = str(p.get("name", "")).strip()
                if country not in COUNTRY_NAMES or not name:
                    continue
                ids = [i for i in p.get("candidate_ids", [])
                       if isinstance(i, int) and 0 <= i < len(candidates)]
                links = list(dict.fromkeys(candidates[i]["url"] for i in ids))
                if not links:
                    continue  # every reported project needs an actual link
                projects.append({
                    "name": name,
                    "country": country,
                    "developer": str(p.get("developer", "") or ""),
                    "capacity_mw": p.get("capacity_mw"),
                    "status": str(p.get("status", "") or ""),
                    "summary": str(p.get("summary", "") or ""),
                    "aliases": [str(a) for a in p.get("aliases", [])],
                    "links": links,
                })
        return projects


# ---- email -----------------------------------------------------------------

def render_email(projects: list[dict], run_date: str,
                 candidates_n: int) -> tuple[str, str]:
    n = len(projects)
    word = "project" if n == 1 else "projects"
    subject = f"Baltic Wind — {n} new planned {word} — {run_date}"

    lines = [f"Baltic wind energy report — {run_date}",
             "New planned wind farm projects in Estonia, Latvia and Lithuania "
             "(not previously reported).", ""]
    if not projects:
        lines += ["No new planned wind farm projects found today.",
                  f"({candidates_n} candidate articles were checked against the "
                  "registry of previously reported projects.)", ""]
    else:
        for code in ("EE", "LV", "LT"):
            group = [p for p in projects if p["country"] == code]
            if not group:
                continue
            lines += [f"=== {COUNTRY_NAMES[code]} ===", ""]
            for i, p in enumerate(group, 1):
                head = p["name"]
                extras = []
                if p["developer"]:
                    extras.append(p["developer"])
                if p["capacity_mw"]:
                    extras.append(f"{p['capacity_mw']} MW")
                if p["status"]:
                    extras.append(p["status"])
                if extras:
                    head += " — " + ", ".join(extras)
                lines.append(f"{i}. {head}")
                if p["summary"]:
                    lines.append(f"   {p['summary']}")
                for link in p["links"]:
                    lines.append(f"   Link: {link}")
                lines.append("")
    lines.append("— baltic-wind-news-agent")
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


# ---- state -----------------------------------------------------------------

def load_registry() -> list[dict]:
    return json.loads(REGISTRY_FILE.read_text())["projects"]


def save_registry(projects: list[dict]):
    REGISTRY_FILE.write_text(
        json.dumps({"projects": projects}, ensure_ascii=False, indent=2) + "\n")


def load_last_sent() -> str:
    if LAST_RUN_FILE.exists():
        return json.loads(LAST_RUN_FILE.read_text()).get("last_sent", "")
    return ""


def save_last_sent(run_date: str):
    LAST_RUN_FILE.write_text(json.dumps({"last_sent": run_date}) + "\n")


def load_seen_urls() -> dict:
    if SEEN_URLS_FILE.exists():
        return json.loads(SEEN_URLS_FILE.read_text())
    return {}


def save_seen_urls(seen: dict, today: date, keep_days: int):
    cutoff = (today - timedelta(days=keep_days)).isoformat()
    pruned = {url: d for url, d in seen.items() if d >= cutoff}
    SEEN_URLS_FILE.write_text(json.dumps(pruned, ensure_ascii=False, indent=2) + "\n")


# ---- main ------------------------------------------------------------------

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--date", help="override run date (YYYY-MM-DD)")
    args = p.parse_args()

    now = datetime.now(TALLINN)
    cfg = yaml.safe_load(CONFIG_FILE.read_text())
    force = args.force or os.environ.get("FORCE_RUN") == "true"
    # GitHub throttles schedule crons in this repo, so delayed runs are the
    # norm: accept any workday run from run_hour_tallinn onward, and rely on
    # last_run.json to guarantee at most one report per day.
    if not (force or args.dry_run):
        if now.weekday() >= 5 or now.hour < cfg["run_hour_tallinn"]:
            print(f"Not a workday at/after {cfg['run_hour_tallinn']}:30 "
                  f"Europe/Tallinn (now {now:%a %H:%M}); skipping. "
                  "Use --force to run anyway.")
            return
        if load_last_sent() == f"{now:%Y-%m-%d}":
            print(f"Report for {now:%Y-%m-%d} already sent; skipping.")
            return

    run_date = args.date or f"{now:%Y-%m-%d}"
    today = date.fromisoformat(run_date)
    registry = load_registry()
    seen = load_seen_urls()
    seen_urls = set(seen.keys())
    keywords = cfg["wind_keywords"]

    candidates: list[dict] = []
    for source in cfg["rss_sources"]:
        try:
            got = fetch_rss_candidates(source, keywords, seen_urls)
            candidates += got
            print(f"[rss] {source['name']}: {len(got)} candidates")
        except Exception as e:
            print(f"Warning: RSS fetch failed for {source['name']}: {e}", file=sys.stderr)
    for page in cfg["listing_pages"]:
        try:
            got = fetch_listing_candidates(page, keywords, seen_urls,
                                           cfg["max_links_per_listing"])
            candidates += got
            print(f"[listing] {page['name']}: {len(got)} candidates")
        except Exception as e:
            print(f"Warning: listing fetch failed for {page['name']}: {e}", file=sys.stderr)

    agent = Agent(Anthropic(max_retries=1), cfg)

    run_start = time.monotonic()
    harvested_urls = {c["url"] for c in candidates}
    for code in COUNTRY_NAMES:
        if time.monotonic() - run_start > DISCOVERY_DEADLINE:
            print(f"Warning: discovery deadline reached, skipping {code} sweep",
                  file=sys.stderr)
            continue
        try:
            got = [c for c in agent.discover(code, run_date, seen_urls)
                   if c["url"] not in harvested_urls]
            candidates += got
            harvested_urls |= {c["url"] for c in got}
            print(f"[discovery] {COUNTRY_NAMES[code]}: {len(got)} candidates")
        except Exception as e:
            print(f"Warning: discovery failed for {code}: {e}", file=sys.stderr)
            traceback.print_exc()

    candidates = candidates[:cfg["max_candidates"]]
    print(f"Total candidates for extraction: {len(candidates)}")

    new_projects: list[dict] = []
    if candidates:
        try:
            new_projects = agent.extract_new_projects(candidates, registry, run_date)
        except Exception:
            traceback.print_exc()
            sys.exit(1)

    # Drop anything whose normalized key already exists (belt and braces).
    known_keys = {normalize_key(p["name"], p["country"]) for p in registry}
    for p in registry:
        known_keys |= {normalize_key(a, p["country"]) for a in p.get("aliases", [])}
    deduped = []
    for proj in new_projects:
        keys = {normalize_key(proj["name"], proj["country"])}
        keys |= {normalize_key(a, proj["country"]) for a in proj["aliases"]}
        if keys & known_keys:
            print(f"Registry key collision, dropping: {proj['name']} ({proj['country']})")
            continue
        known_keys |= keys
        deduped.append(proj)
    new_projects = deduped

    subject, body = render_email(new_projects, run_date, len(candidates))

    if args.dry_run:
        print(f"\nSubject: {subject}\n\n{body}")
        return

    send_email(subject, body, cfg["recipient_email"])

    for proj in new_projects:
        registry.append({
            "key": normalize_key(proj["name"], proj["country"]),
            "name": proj["name"],
            "country": proj["country"],
            "aliases": proj["aliases"],
            "developer": proj["developer"],
            "first_reported": run_date,
            "links": proj["links"],
        })
    save_registry(registry)
    for c in candidates:
        seen[c["url"]] = run_date
    save_seen_urls(seen, today, cfg["seen_url_days"])
    save_last_sent(run_date)
    print(f"Sent report: {len(new_projects)} new project(s) from "
          f"{len(candidates)} candidates.")


if __name__ == "__main__":
    main()
