"""Retrying JSON-over-HTTPS client shared by the FMP and Massive clients.

Rules (spec §2.1, R6): API keys come from environment variables only. They are
added to the request at send time and never logged, written to disk, or included
in an exception message. Every fetch is retried at most `retries` times with
backoff. A failure returns None so the caller can mark the dependent stage stale.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)


class MissingKeyError(RuntimeError):
    pass


def require_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise MissingKeyError(f"environment variable {env_name} is not set "
                              "(API keys are read from the environment only)")
    return key


class JsonClient:
    def __init__(self, base_url: str, key: str, key_param: str | None = "apikey",
                 bearer: bool = False, retries: int = 4, timeout: float = 30.0,
                 user_agent: str = "hermes-screener/0.1"):
        self.base_url = base_url.rstrip("/")
        self._key = key
        self.key_param = key_param
        self.bearer = bearer
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self.calls = 0
        self.failures = 0

    def _url(self, path_or_url: str, params: dict | None) -> str:
        url = path_or_url if path_or_url.startswith("http") else \
            f"{self.base_url}/{path_or_url.lstrip('/')}"
        q = dict(params or {})
        if self.key_param and not self.bearer:
            q[self.key_param] = self._key
        if q:
            url += ("&" if "?" in url else "?") + urllib.parse.urlencode(q)
        return url

    def get(self, path_or_url: str, params: dict | None = None):
        url = self._url(path_or_url, params)
        headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if self.bearer:
            headers["Authorization"] = f"Bearer {self._key}"
        safe = path_or_url.split("?")[0]
        for attempt in range(self.retries):
            self.calls += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    return json.loads(r.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as e:
                if e.code in (401, 403, 404):
                    log.debug("GET %s -> HTTP %s (not retried)", safe, e.code)
                    break
                log.debug("GET %s -> HTTP %s (attempt %d)", safe, e.code, attempt + 1)
            except Exception as e:  # noqa: BLE001
                log.debug("GET %s -> %s (attempt %d)", safe, type(e).__name__, attempt + 1)
            time.sleep(min(1.5 * (2 ** attempt), 20.0))
        self.failures += 1
        return None
