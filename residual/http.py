"""Tiny JSON-over-HTTPS helper with retries and polite pacing. Standard library only."""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

log = logging.getLogger(__name__)

USER_AGENT = "residual/0.1 (prediction-market move monitor)"
_last_call: dict[str, float] = {}


def get_json(url: str, params: dict | None = None, *, min_interval: float = 0.15,
             retries: int = 4, timeout: float = 30):
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url = f"{url}?{urllib.parse.urlencode(clean)}"
    host = urllib.parse.urlparse(url).netloc
    for attempt in range(retries + 1):
        wait = min_interval - (time.monotonic() - _last_call.get(host, 0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host] = time.monotonic()
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < retries:
                delay = 2 ** attempt
                log.debug("HTTP %s from %s, retrying in %ss", e.code, host, delay)
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, TimeoutError):
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise
