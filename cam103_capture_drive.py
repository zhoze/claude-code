#!/usr/bin/env python3
"""
Capture frames from Tallinn traffic camera cam103 and upload them straight to
Google Drive using the native Google Drive API (no rclone).

Destination: Drive folder "TallinnCam/cam103"
  folder id = 1QSyJyGIlfHu2IkSrltsgyUxyR-Lc3gN8   (already created in your Drive)

────────────────────────────────────────────────────────────────────────────
ONE-TIME SETUP
  1. pip install google-api-python-client google-auth-httplib2 \
                 google-auth-oauthlib requests
  2. Create an OAuth client (Desktop app) at
        https://console.cloud.google.com/apis/credentials
     enable the "Google Drive API", download the JSON as  credentials.json
     next to this script.
  3. Find cam103's image URL: open
        https://ristmikud.tallinn.ee/index.php/cams
     F12 -> Network -> filter "Img" -> click cam103 -> Copy as cURL ->
     paste the .jpg URL into CAM_URL below (plus any cookie/header it needs).
  4. python3 cam103_capture_drive.py
     (first run opens a browser once to authorize; token is cached in token.json)
────────────────────────────────────────────────────────────────────────────
"""

import io
import time
from datetime import datetime

import requests
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload

# >>> EDIT THIS <<<
CAM_URL = "https://ristmikud.tallinn.ee/PASTE_CAM103_IMAGE_URL_HERE.jpg"

DRIVE_FOLDER_ID = "1QSyJyGIlfHu2IkSrltsgyUxyR-Lc3gN8"  # TallinnCam/cam103
INTERVAL = 10           # seconds between frames (keep >= 10, be polite)
MIN_BYTES = 1000        # discard frames smaller than this (error/empty)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Referer": "https://ristmikud.tallinn.ee/index.php/cams",
}


def get_drive():
    """Authorize once (browser), then reuse cached token.json."""
    creds = None
    try:
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    except FileNotFoundError:
        pass
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
            creds = flow.run_local_server(port=0)
        with open("token.json", "w") as f:
            f.write(creds.to_json())
    return build("drive", "v3", credentials=creds)


def upload(drive, name, data):
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype="image/jpeg", resumable=False)
    drive.files().create(
        body={"name": name, "parents": [DRIVE_FOLDER_ID]},
        media_body=media,
        fields="id",
    ).execute()


def main():
    drive = get_drive()
    print(f"Capturing cam103 every {INTERVAL}s -> Drive folder {DRIVE_FOLDER_ID}")
    print("Ctrl-C to stop.")
    session = requests.Session()
    while True:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            r = session.get(CAM_URL, params={"t": ts}, headers=HEADERS, timeout=20)
            r.raise_for_status()
            if len(r.content) < MIN_BYTES:
                print(f"  ! {ts}: frame too small ({len(r.content)}B), skipped")
            else:
                upload(drive, f"cam103_{ts}.jpg", r.content)
                print(f"  + uploaded cam103_{ts}.jpg ({len(r.content)//1024} KB)")
        except Exception as e:  # noqa: BLE001 - keep the loop alive
            print(f"  ! {ts}: {e}")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
