#!/usr/bin/env python3
"""Check an iha.ee user's "last online" status and notify via Telegram if recently online."""
import os
import re
import sys
import time
import requests

SEARCH_URL = (
    "https://www.iha.ee/search/1/?age1=0&age2=0&K_riik=0&K_elukoht=&username={username}"
    "&name=&kasv1=&kasv2=&V_keha=0&V_juuk=0&S_orjent=0&S_bdsm=0&T_eesm=0&T_soovt=0"
    "&E_haridus=0&T_olen=0&E_elukoht=0&T_kink=0&E_sisset=0&search=1&_cb={cb}"
)


def fetch_status(username: str) -> str | None:
    url = SEARCH_URL.format(username=requests.utils.quote(username), cb=int(time.time()))
    resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
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


def main() -> int:
    username = os.environ.get("IHA_USERNAME", "Suk87")
    status = fetch_status(username)

    if status is None:
        print(f"Could not find a 'Viimati online' value for {username}; skipping.")
        return 0

    print(f"{username} - Viimati online: {status}")

    if re.fullmatch(r"\d+m", status):
        token = os.environ["TELEGRAM_BOT_TOKEN"]
        chat_id = os.environ["TELEGRAM_CHAT_ID"]
        send_telegram(token, chat_id, f"{username} was online {status} ago on iha.ee")
        print("Telegram notification sent.")
    else:
        print("Not recently online (not in minutes); no notification sent.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
