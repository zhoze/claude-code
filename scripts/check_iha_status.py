#!/usr/bin/env python3
"""Check iha.ee users' "last online" status and notify via Telegram if recently online."""
import os
import re
import sys
import time
import warnings
import requests
from urllib3.exceptions import InsecureRequestWarning
warnings.filterwarnings("ignore", category=InsecureRequestWarning)

SEARCH_URL = (
    "https://www.iha.ee/search/1/?age1=0&age2=0&K_riik=0&K_elukoht=&username={username}"
    "&name=&kasv1=&kasv2=&V_keha=0&V_juuk=0&S_orjent=0&S_bdsm=0&T_eesm=0&T_soovt=0"
    "&E_haridus=0&T_olen=0&E_elukoht=0&T_kink=0&E_sisset=0&search=1&_cb={cb}"
)


def fetch_status(username: str) -> str | None:
    url = SEARCH_URL.format(username=requests.utils.quote(username), cb=int(time.time()))
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30, verify=False)
    resp.raise_for_status()
    pattern = re.compile(
        r'>' + re.escape(username) + r'</a>.*?Viimati online:\s*([^<]+)<',
        re.DOTALL,
    )
    match = pattern.search(resp.text)
    return match.group(1).strip() if match else None


def send_telegram(token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    resp.raise_for_status()


def check_user(username: str, token: str, chat_id: str) -> None:
    status = fetch_status(username)

    if status is None:
        print(f"Could not find a 'Viimati online' value for {username}; skipping.")
        return

    print(f"{username} - Viimati online: {status}")

    if re.fullmatch(r"\d+m", status):
        send_telegram(token, chat_id, f"{username} was online {status} ago on iha.ee")
        print("Telegram notification sent.")
    else:
        print("Not recently online (not in minutes); no notification sent.")


def main() -> int:
    usernames_env = os.environ.get("IHA_USERNAMES", os.environ.get("IHA_USERNAME", "Suk87"))
    usernames = [u.strip() for u in usernames_env.split(",") if u.strip()]

    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]

    for username in usernames:
        check_user(username, token, chat_id)

    return 0


if __name__ == "__main__":
    sys.exit(main())
