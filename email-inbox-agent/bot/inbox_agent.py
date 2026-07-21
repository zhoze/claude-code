"""
Email inbox digest agent (GitHub Actions edition).

Each run: connect to Gmail over IMAP, fetch messages received since the last
successful run (or `lookback_hours_default` on a first run), split them into
"API usage" emails (From domain matches `api_usage_domains` — Anthropic,
OpenAI) and everything else. Claude reads the usage emails and writes a short
usage summary for each provider, then scans the rest and picks out a handful
of "interesting" emails worth flagging. The digest is emailed via Gmail SMTP
to `recipient_email`. The mailbox is opened read-only (no flags are changed),
so nothing is ever marked read or archived by this agent.

Env vars (GitHub Actions secrets):
  ANTHROPIC_API_KEY, EMAIL_ADDRESS, EMAIL_APP_PASSWORD

Usage:
  inbox_agent.py                normal run (only acts at run_hour_utc)
  inbox_agent.py --force        bypass the local-time guard
  inbox_agent.py --dry-run      print the digest, no email, no state write
  inbox_agent.py --since DATETIME   override lookback start (ISO 8601, UTC)
"""

import argparse
import email
import imaplib
import json
import os
import re
import smtplib
import sys
import traceback
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.mime.text import MIMEText
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

import yaml
from anthropic import Anthropic

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_FILE = BASE_DIR / "config.yaml"
STATE_FILE = BASE_DIR / "state" / "last_check.json"

TAG_RE = re.compile(r"<[^>]+>")
WS_RE = re.compile(r"\s+")


def decode_mime_header(raw: str) -> str:
    if not raw:
        return ""
    parts = decode_header(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            out.append(text.decode(enc or "utf-8", errors="replace"))
        else:
            out.append(text)
    return "".join(out)


def strip_html(html: str) -> str:
    return WS_RE.sub(" ", TAG_RE.sub(" ", html)).strip()


def extract_body(msg: email.message.Message, max_chars: int) -> str:
    """First text/plain part, falling back to a stripped text/html part."""
    plain, html = None, None
    if msg.is_multipart():
        for part in msg.walk():
            ctype = part.get_content_type()
            if part.get_content_disposition() == "attachment":
                continue
            try:
                payload = part.get_payload(decode=True)
            except Exception:
                continue
            if payload is None:
                continue
            charset = part.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace")
            if ctype == "text/plain" and plain is None:
                plain = text
            elif ctype == "text/html" and html is None:
                html = text
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="replace") if payload else ""
        except Exception:
            text = ""
        if msg.get_content_type() == "text/html":
            html = text
        else:
            plain = text

    body = plain if plain is not None else strip_html(html or "")
    return WS_RE.sub(" ", body).strip()[:max_chars]


def fetch_new_messages(cfg: dict, since_dt: datetime) -> list[dict]:
    host = cfg["imap_host"]
    user = os.environ["EMAIL_ADDRESS"].strip()
    password = os.environ["EMAIL_APP_PASSWORD"]

    imap = imaplib.IMAP4_SSL(host)
    try:
        imap.login(user, password)
        imap.select("INBOX", readonly=True)
        # IMAP SINCE is date-granularity only; we re-filter by exact
        # timestamp below once each message's Date header is parsed.
        since_date = since_dt.strftime("%d-%b-%Y")
        status, data = imap.search(None, f"(SINCE {since_date})")
        if status != "OK":
            raise RuntimeError(f"IMAP search failed: {status}")
        ids = data[0].split()
        ids = ids[-cfg["max_emails_scanned"]:]  # newest N if the window is huge

        messages = []
        for msg_id in ids:
            status, msg_data = imap.fetch(msg_id, "(RFC822)")
            if status != "OK" or not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            date_hdr = msg.get("Date")
            try:
                msg_dt = parsedate_to_datetime(date_hdr) if date_hdr else None
                if msg_dt and msg_dt.tzinfo is None:
                    msg_dt = msg_dt.replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                msg_dt = None
            if msg_dt is not None and msg_dt <= since_dt:
                continue

            name, addr = parseaddr(msg.get("From", ""))
            messages.append({
                "from_name": decode_mime_header(name),
                "from_addr": addr.lower(),
                "subject": decode_mime_header(msg.get("Subject", "")),
                "date": msg_dt.isoformat() if msg_dt else (date_hdr or ""),
                "snippet": extract_body(msg, cfg["snippet_chars"]),
            })
        return messages
    finally:
        try:
            imap.logout()
        except Exception:
            pass


def classify(messages: list[dict], usage_domains: list[str]) -> tuple[list[dict], list[dict]]:
    usage, other = [], []
    for m in messages:
        domain = m["from_addr"].rsplit("@", 1)[-1] if "@" in m["from_addr"] else ""
        if any(domain == d or domain.endswith("." + d) for d in usage_domains):
            usage.append(m)
        else:
            other.append(m)
    return usage, other


def summarize(ac: Anthropic, cfg: dict, usage_emails: list[dict], other_emails: list[dict]) -> dict:
    if not usage_emails and not other_emails:
        return {"claude_usage_summary": "", "chatgpt_usage_summary": "", "interesting": []}

    def block(m: dict) -> str:
        return (f"From: {m['from_name']} <{m['from_addr']}>\n"
                f"Date: {m['date']}\n"
                f"Subject: {m['subject']}\n"
                f"Body: {m['snippet']}")

    usage_block = "\n\n".join(block(m) for m in usage_emails) or "(none)"
    other_block = "\n\n".join(f"[{i}] {block(m)}" for i, m in enumerate(other_emails)) or "(none)"

    prompt = (
        "You are triaging one day's new inbox mail for a daily digest email.\n\n"
        "=== Emails from Anthropic/OpenAI (API usage, billing, limits) ===\n"
        f"{usage_block}\n\n"
        "=== All other new emails ===\n"
        f"{other_block}\n\n"
        "Return ONLY a JSON object with these keys:\n"
        '  "claude_usage_summary": a short plain-text summary (2-4 sentences) of any '
        "Claude/Anthropic API usage, billing, spend, or rate-limit information found in "
        'the first list. Empty string if there is nothing from Anthropic.\n'
        '  "chatgpt_usage_summary": same, but for OpenAI/ChatGPT API usage. Empty string '
        "if there is nothing from OpenAI.\n"
        f'  "interesting": an array of up to {cfg["max_interesting"]} objects '
        '{"index": <i from the other-emails list>, "reason": "one sentence on why it '
        'stands out"} for emails from the second list that seem genuinely worth a '
        "human's attention (e.g. personal correspondence, time-sensitive requests, "
        "security alerts, financial matters, important news) — skip routine "
        "notifications, newsletters, marketing, and receipts unless something about "
        "them is unusual. Return an empty array if nothing stands out.\n"
        "Output only the JSON object, no prose, no code fences."
    )
    resp = ac.messages.create(
        model=cfg["claude_model"],
        max_tokens=cfg["summary_max_tokens"],
        system="You triage email for a digest. Output only valid JSON.",
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in resp.content if b.type == "text")
    return extract_json(text)


def extract_json(text: str) -> dict:
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


def render_email(result: dict, other_emails: list[dict], usage_count: int,
                  other_count: int, run_date: str) -> tuple[str, str]:
    interesting = result.get("interesting", [])
    subject = (f"Inbox Digest — {run_date} "
               f"({usage_count} usage email{'s' if usage_count != 1 else ''}, "
               f"{len(interesting)} interesting)")

    lines = [f"Inbox Digest — {run_date}", ""]

    lines.append("=== Claude (Anthropic) API usage ===")
    lines.append(result.get("claude_usage_summary") or "Nothing new today.")
    lines.append("")

    lines.append("=== ChatGPT (OpenAI) API usage ===")
    lines.append(result.get("chatgpt_usage_summary") or "Nothing new today.")
    lines.append("")

    lines.append("=== Interesting emails ===")
    if interesting:
        for item in interesting:
            idx = item.get("index")
            if not isinstance(idx, int) or not (0 <= idx < len(other_emails)):
                continue
            m = other_emails[idx]
            lines.append(f"- {m['subject']!r} from {m['from_name'] or m['from_addr']}")
            lines.append(f"  {item.get('reason', '').strip()}")
    else:
        lines.append("Nothing stood out today.")
    lines.append("")

    lines.append(f"({other_count} other new email{'s' if other_count != 1 else ''} scanned, "
                 f"{usage_count} usage email{'s' if usage_count != 1 else ''} scanned)")

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


def save_state(now: datetime):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps({"last_check": now.isoformat()}, indent=2) + "\n")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--force", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--since", help="override lookback start (ISO 8601, UTC)")
    args = p.parse_args()

    now = datetime.now(timezone.utc)
    cfg = yaml.safe_load(CONFIG_FILE.read_text())
    force = args.force or os.environ.get("FORCE_RUN") == "true"
    if not (force or args.dry_run) and now.hour != cfg["run_hour_utc"]:
        print(f"Not {cfg['run_hour_utc']}:00 UTC (now {now:%H:%M} UTC); skipping. "
              "Use --force to run anyway.")
        return

    state = load_state()
    if args.since:
        since_dt = datetime.fromisoformat(args.since)
    elif "last_check" in state:
        since_dt = datetime.fromisoformat(state["last_check"])
    else:
        since_dt = now - timedelta(hours=cfg["lookback_hours_default"])
    if since_dt.tzinfo is None:
        since_dt = since_dt.replace(tzinfo=timezone.utc)

    try:
        messages = fetch_new_messages(cfg, since_dt)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    usage_emails, other_emails = classify(messages, cfg["api_usage_domains"])
    print(f"Fetched {len(messages)} new message(s) since {since_dt.isoformat()}: "
          f"{len(usage_emails)} usage, {len(other_emails)} other.")

    try:
        ac = Anthropic()
        result = summarize(ac, cfg, usage_emails, other_emails)
    except Exception:
        traceback.print_exc()
        sys.exit(1)

    run_date = f"{now:%Y-%m-%d}"
    subject, body = render_email(result, other_emails, len(usage_emails), len(other_emails), run_date)

    if args.dry_run:
        print(f"\nSubject: {subject}\n\n{body}")
        return

    send_email(subject, body, cfg["recipient_email"])
    save_state(now)
    print(f"Sent digest to {cfg['recipient_email']}.")


if __name__ == "__main__":
    main()
