"""Find a news article's lead image (og:image and friends) and whatever credit the page gives it.

Standard library only. Used by `python -m residual image <url>` so the agent gets the exact image URL
from the page itself instead of from a summary of it.
"""

from __future__ import annotations

import json
import re
import urllib.request
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urljoin

UA = "Mozilla/5.0 (compatible; Newshound/1.0; +https://mjsilverman19.github.io/newshound/)"


class _Meta(HTMLParser):
    def __init__(self):
        super().__init__()
        self.meta: dict[str, str] = {}
        self.ld: list[str] = []
        self._in_ld = False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = unescape(a["content"]).strip()
        elif tag == "script" and a.get("type", "").lower() == "application/ld+json":
            self._in_ld = True
            self.ld.append("")

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_ld = False

    def handle_data(self, data):
        if self._in_ld:
            self.ld[-1] += data


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _name(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return v.get("name") or ""
    if isinstance(v, list):
        return ", ".join(filter(None, (_name(x) for x in v)))
    return ""


def parse(page_url: str, html: str) -> dict:
    p = _Meta()
    p.feed(html)
    m = p.meta
    out = {"source_url": page_url, "url": None, "alt": None, "credit": None, "caption": None}
    for key in ("og:image:secure_url", "og:image", "og:image:url", "twitter:image", "twitter:image:src", "thumbnailurl"):
        if m.get(key):
            out["url"] = urljoin(page_url, m[key])
            break
    out["alt"] = m.get("og:image:alt") or m.get("twitter:image:alt")
    for blob in p.ld:
        try:
            data = json.loads(blob.strip())
        except ValueError:
            continue
        for node in _walk(data):
            t = node.get("@type")
            types = t if isinstance(t, list) else [t]
            if "ImageObject" not in types:
                continue
            img_url = node.get("url") or node.get("contentUrl")
            if out["url"] is None and isinstance(img_url, str):
                out["url"] = urljoin(page_url, img_url)
            same = isinstance(img_url, str) and out["url"] and img_url.split("?")[0] == out["url"].split("?")[0]
            if same or not out["credit"]:
                credit = node.get("creditText") or _name(node.get("copyrightHolder")) or _name(node.get("author"))
                if credit and (same or out["credit"] is None):
                    out["credit"] = credit
                cap = node.get("caption") or node.get("description")
                if cap and (same or out["caption"] is None):
                    out["caption"] = cap if isinstance(cap, str) else None
    if out["url"] and not out["url"].startswith("https://"):
        out["url"] = None
    return out


def lead_image(page_url: str, timeout: float = 20) -> dict:
    req = urllib.request.Request(page_url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
    html = raw.decode(charset, "replace")
    # skip to the head when the page is huge
    end = re.search(r"</head>", html, re.I)
    head = html[: end.end()] if end else html
    out = parse(page_url, head)
    if out["url"] and out["credit"] is None and end:
        out = {**parse(page_url, html), **{k: v for k, v in out.items() if v}}
    return out
