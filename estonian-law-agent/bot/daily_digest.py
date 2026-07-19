"""
Eesti õiguse agent — igapäevane Riigi Teataja seire (GitHub Actions edition).

Each run: query the Riigi Teataja search API for laws published since the last
run, detect newly effective consolidated versions of the watched laws
(config.yaml), summarize each new act in Estonian with Claude, and send the
digest to Telegram. Every fact in the digest carries a riigiteataja.ee link.

Env vars (GitHub Actions secrets):
  TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY
Optional:
  ALLOWED_CHAT_ID — the owner's chat, used as the digest destination

Usage:
  daily_digest.py                 normal run (only acts at 10:00 Europe/Tallinn)
  daily_digest.py --force         bypass the 10:00 local-time guard
  daily_digest.py --dry-run       print the digest, no Telegram, no state write
  daily_digest.py --since DATE    override the "new since" date (YYYY-MM-DD)
  daily_digest.py --seed          write initial state/last_check.json and exit
"""

import argparse
import json
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests
import yaml

API = "https://www.riigiteataja.ee/api/oigusakt_otsing/1/otsi"
AKT_URL = "https://www.riigiteataja.ee/akt/{}"
AKT_XML_URL = "https://www.riigiteataja.ee/public-api/api/v1/akt/{}/blob-xml"
PAGE_SIZE = 500
SCAN_PAGES = 3          # newest acts live on the last pages of the result set
XML_TRUNCATE = 30_000   # chars of act text handed to the summary model
MAX_SUMMARY_TOKENS = 400
TALLINN = ZoneInfo("Europe/Tallinn")

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
STATE_FILE = BASE_DIR / "state" / "last_check.json"


def parse_globaal_id(gid) -> date | None:
    """Modern globaalID encodes the publication notation: <RT part><DDMM><YYYY><seq>.

    E.g. 116072026004 = RT I, 16.07.2026, 4. Legacy short IDs carry no date.
    """
    s = str(gid)
    if len(s) != 12 or s[0] not in "1234":
        return None
    try:
        return date(int(s[5:9]), int(s[3:5]), int(s[1:3]))
    except ValueError:
        return None


def api_search(**params) -> dict:
    r = requests.get(f"{API}?{urlencode(params)}", timeout=60)
    r.raise_for_status()
    return r.json()


def fetch_new_acts(since: date, seen_ids: set, scope: dict) -> list[dict]:
    """Return acts published on/after `since`, newest publication date first.

    The API has no publication-date filter, but results are deterministically
    ordered and new acts appear on the last pages, so scan those and filter by
    the date embedded in globaalID.
    """
    base = {"limiit": PAGE_SIZE, "tekst": "algtekst", **scope}
    total = api_search(**base, leht=1)["metaandmed"]["kokku"]
    last_page = max(1, -(-total // PAGE_SIZE))

    acts = []
    for page in range(last_page, max(0, last_page - SCAN_PAGES), -1):
        for act in api_search(**base, leht=page).get("aktid", []):
            pub = parse_globaal_id(act["globaalID"])
            if pub and pub >= since and act["globaalID"] not in seen_ids:
                act["avaldamise_kp"] = pub.isoformat()
                acts.append(act)
    acts.sort(key=lambda a: (a["avaldamise_kp"], a["globaalID"]), reverse=True)
    return acts


def fetch_current_versions(lyhendid: list[str], today: date) -> dict:
    """For each watched abbreviation return the consolidated text in force today."""
    versions = {}
    for lyhend in lyhendid:
        try:
            aktid = api_search(limiit=5, lyhend=lyhend, kehtiv=today.isoformat()).get("aktid", [])
        except Exception as e:
            print(f"Hoiatus: {lyhend} päring ebaõnnestus: {e}", file=sys.stderr)
            continue
        if aktid:
            act = aktid[0]
            versions[lyhend] = {
                "globaalID": act["globaalID"],
                "pealkiri": act["pealkiri"],
                "kehtivuse_algus": (act.get("kehtivus") or {}).get("algus"),
            }
    return versions


def fetch_act_text(gid) -> str:
    """Plain text of an act, roughly extracted from its XML, for summarization."""
    r = requests.get(AKT_XML_URL.format(gid), timeout=60)
    r.raise_for_status()
    text = re.sub(r"<[^>]+>", " ", r.text)
    return re.sub(r"\s+", " ", text).strip()[:XML_TRUNCATE]


def summarize(act: dict, model: str) -> str | None:
    """2–3 lauseline eestikeelne kokkuvõte; None kui kokkuvõtet ei õnnestu teha."""
    try:
        from anthropic import Anthropic

        text = fetch_act_text(act["globaalID"])
        resp = Anthropic().messages.create(
            model=model,
            max_tokens=MAX_SUMMARY_TOKENS,
            system=(
                "Oled Eesti õiguse ekspert. Koosta avaldatud õigusaktist 2–3 lauseline "
                "eestikeelne kokkuvõte: mis muutub või kehtestatakse ja kellele see on "
                "oluline. Kasuta AINULT kasutaja sõnumis antud akti teksti (Riigi "
                "Teataja) — ära lisa fakte muudest allikatest ega oma taustateadmistest. "
                "Vasta ainult eesti keeles, ilma sissejuhatuseta."
            ),
            messages=[{
                "role": "user",
                "content": f"Akt: {act['pealkiri']} ({act['valjaandja']})\n\nTekst:\n{text}",
            }],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        print(f"Hoiatus: kokkuvõte ebaõnnestus ({act['globaalID']}): {e}", file=sys.stderr)
        return None


def watched_title_stems(versions: dict) -> dict:
    """lyhend -> lowercase full title, matched as substring against new act titles.

    Estonian genitive extends the nominative ("võlaõigusseaduse muutmise seadus"
    contains "võlaõigusseadus"), so plain containment catches amendment acts.
    """
    return {ly: v["pealkiri"].lower() for ly, v in versions.items() if v.get("pealkiri")}


def format_act(act: dict, model: str) -> str:
    lines = [f"📜 {act['pealkiri']}"]
    detail = f"{act['valjaandja']}, avaldatud {act['avaldamise_kp']}"
    algus = (act.get("kehtivus") or {}).get("algus")
    if algus:
        detail += f", jõustub {algus}"
    lines.append(detail)
    summary = summarize(act, model)
    if summary:
        lines.append(summary)
    lines.append(AKT_URL.format(act["globaalID"]))
    return "\n".join(lines)


def compose_digest(new_acts, watched_hits, new_versions, model, today) -> str:
    parts = [f"⚖️ Riigi Teataja seire — {today.strftime('%d.%m.%Y')}"]

    if new_versions:
        block = ["⭐ Sinu jälgitavate seaduste uued redaktsioonid:"]
        for ly, v in new_versions.items():
            block.append(
                f"• {v['pealkiri']} ({ly}) — uus terviktekst kehtib alates "
                f"{v['kehtivuse_algus']}\n{AKT_URL.format(v['globaalID'])}"
            )
        parts.append("\n".join(block))

    if watched_hits:
        block = ["⭐ Sinu jälgitavaid seadusi puudutavad uued aktid:"]
        block += [format_act(a, model) for a in watched_hits]
        parts.append("\n\n".join(block))

    if new_acts:
        block = ["🆕 Uued avaldatud seadused:"]
        block += [format_act(a, model) for a in new_acts]
        parts.append("\n\n".join(block))

    if not (new_acts or watched_hits or new_versions):
        parts.append("Uusi seadusi ei avaldatud ja jälgitavates seadustes muudatusi ei ole.")

    parts.append("Allikas: Riigi Teataja — https://www.riigiteataja.ee")
    return "\n\n".join(parts)


def send_telegram(text: str):
    tg = f"https://api.telegram.org/bot{os.environ['TELEGRAM_BOT_TOKEN'].strip()}"
    chat_id = os.environ["ALLOWED_CHAT_ID"]
    for i in range(0, len(text), 4000):
        requests.post(
            f"{tg}/sendMessage",
            json={"chat_id": chat_id, "text": text[i:i + 4000],
                  "disable_web_page_preview": True},
            timeout=30,
        ).raise_for_status()


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text())
    return {"last_date": None, "seen_ids": [], "redaktsioonid": {}}


def save_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="skip the 10:00 local-time guard")
    ap.add_argument("--dry-run", action="store_true", help="print digest, no Telegram/state")
    ap.add_argument("--since", help="override new-since date (YYYY-MM-DD)")
    ap.add_argument("--seed", action="store_true", help="write initial state and exit")
    args = ap.parse_args()

    now = datetime.now(TALLINN)
    today = now.date()
    force = args.force or os.environ.get("FORCE_RUN") == "true"
    if not (force or args.dry_run or args.seed) and now.hour != 10:
        print(f"Kell on {now:%H:%M} (Europe/Tallinn), mitte 10 — jätan vahele.")
        return

    config = yaml.safe_load(CONFIG_FILE.read_text())
    scope = {k: str(v) for k, v in (config.get("scope") or {}).items()}
    state = load_state()

    versions = fetch_current_versions(config.get("lyhendid", []), today)

    if args.seed:
        state = {
            "last_date": today.isoformat(),
            "seen_ids": [],
            "redaktsioonid": {ly: v["kehtivuse_algus"] for ly, v in versions.items()},
        }
        save_state(state)
        print(f"Algseis salvestatud: {STATE_FILE}")
        return

    since = date.fromisoformat(args.since or state["last_date"] or
                               (today - timedelta(days=3)).isoformat())
    seen_ids = set(state.get("seen_ids", []))

    new_acts = fetch_new_acts(since, seen_ids, scope)

    # Newly effective consolidated versions of watched laws
    old_versions = state.get("redaktsioonid", {})
    new_versions = {
        ly: v for ly, v in versions.items()
        if v["kehtivuse_algus"] and old_versions.get(ly)
        and v["kehtivuse_algus"] > old_versions[ly]
    }

    # New acts that touch a watched law go to the top of the digest
    stems = watched_title_stems(versions)
    watched_hits = [a for a in new_acts
                    if any(s in a["pealkiri"].lower() for s in stems.values())]
    other_acts = [a for a in new_acts if a not in watched_hits]

    print(f"Uusi akte alates {since}: {len(new_acts)} "
          f"(jälgitavaid: {len(watched_hits)}, uusi redaktsioone: {len(new_versions)})")

    digest = compose_digest(other_acts, watched_hits, new_versions,
                            config.get("summary_model", "claude-sonnet-5"), today)

    if args.dry_run:
        print("\n" + digest)
        return

    if new_acts or new_versions or not config.get("quiet_days", True):
        send_telegram(digest)
        print("Kokkuvõte saadetud Telegrami.")
    else:
        print("Uusi akte pole — sõnumit ei saadeta (quiet_days).")

    cutoff = (today - timedelta(days=14)).isoformat()
    state["last_date"] = today.isoformat()
    state["seen_ids"] = sorted(
        {i for i in seen_ids if (parse_globaal_id(i) or date.min).isoformat() >= cutoff}
        | {a["globaalID"] for a in new_acts}
    )
    state["redaktsioonid"] = {
        ly: v["kehtivuse_algus"] for ly, v in versions.items() if v["kehtivuse_algus"]
    }
    save_state(state)
    print(f"Seis uuendatud: {STATE_FILE}")


if __name__ == "__main__":
    main()
