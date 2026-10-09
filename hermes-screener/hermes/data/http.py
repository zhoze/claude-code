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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

log = logging.getLogger(__name__)


class MissingKeyError(RuntimeError):
    pass


def require_key(env_name: str) -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise MissingKeyError(f"environment variable {env_name} is not set "
                              "(API keys are read from the environment only)")
    return key


def endpoint_family(path: str) -> str:
    """Endpoint label without tickers/dates/keys, e.g. 'v2/aggs', 'stocks/v1/dividends'."""
    p = urllib.parse.urlparse(path).path.lstrip("/")
    parts = [x for x in p.split("/") if x]
    if parts[:1] == ["stable"]:
        parts = parts[1:]
    keep = []
    for x in parts:
        if x.isupper() or not any(c.isalpha() for c in x):
            break
        keep.append(x)
        if len(keep) == 3:
            break
    return "/".join(keep) or "?"


class RateLimiter:
    """Thread-safe minimum spacing between requests (max_rpm requests per minute)."""

    def __init__(self, max_rpm: float | None):
        self.interval = 60.0 / max_rpm if max_rpm else 0.0
        self._next = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if not self.interval:
            return
        with self._lock:
            now = time.monotonic()
            t = max(now, self._next)
            self._next = t + self.interval
        if t > now:
            time.sleep(t - now)


class JsonClient:
    def __init__(self, base_url: str, key: str, key_param: str | None = "apikey",
                 bearer: bool = False, retries: int = 4, timeout: float = 30.0,
                 user_agent: str = "hermes-screener/0.1", max_rpm: float | None = None,
                 retries_429: int = 8):
        self.base_url = base_url.rstrip("/")
        self._key = key
        self.key_param = key_param
        self.bearer = bearer
        self.retries = retries
        self.retries_429 = retries_429
        self.timeout = timeout
        self.user_agent = user_agent
        self.limiter = RateLimiter(max_rpm)
        self.calls = 0
        self.failures = 0
        self.status: Counter = Counter()      # (endpoint family, HTTP status) -> count
        self._lock = threading.Lock()

    def _count(self, fam: str, code) -> None:
        with self._lock:
            self.status[f"{fam} {code}"] += 1

    def status_summary(self) -> dict:
        return dict(sorted(self.status.items()))

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
        fam = endpoint_family(safe)
        attempt = n429 = 0
        while attempt < self.retries:
            self.limiter.wait()
            with self._lock:
                self.calls += 1
            try:
                req = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    data = json.loads(r.read().decode("utf-8", "replace"))
                self._count(fam, 200)
                return data
            except urllib.error.HTTPError as e:
                self._count(fam, e.code)
                if e.code == 429 and n429 < self.retries_429:
                    n429 += 1
                    wait = e.headers.get("Retry-After") if e.headers else None
                    try:
                        wait = float(wait)
                    except (TypeError, ValueError):
                        wait = min(5.0 * n429, 60.0)
                    log.debug("GET %s -> HTTP 429, waiting %.0fs", safe, wait)
                    time.sleep(wait)
                    continue                       # 429s do not consume normal retries
                if e.code in (400, 401, 403, 404):
                    log.debug("GET %s -> HTTP %s (not retried)", safe, e.code)
                    break
                log.debug("GET %s -> HTTP %s (attempt %d)", safe, e.code, attempt + 1)
            except Exception as e:  # noqa: BLE001
                self._count(fam, type(e).__name__)
                log.debug("GET %s -> %s (attempt %d)", safe, type(e).__name__, attempt + 1)
            attempt += 1
            if attempt < self.retries:
                time.sleep(min(1.5 * (2 ** attempt), 20.0))
        with self._lock:
            self.failures += 1
        return None
