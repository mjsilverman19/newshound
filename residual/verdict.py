"""Turn an agent's reported sources into a verdict, using rules enforced in code.

The agent is never trusted on its own conclusion. A move counts as explained only if:
  * the agent marked a source as the cause,
  * that source's URL actually came back from a web search in this run (no invented links), and
  * the source was published before the move started, plus a short reaction allowance, and
  * the source is not an aggregator, re-poster or partisan blog (those count as background at most).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from urllib.parse import urlparse


def norm_url(u: str) -> str:
    p = urlparse(u.strip())
    host = p.netloc.lower().removeprefix("www.")
    return f"{host}{p.path.rstrip('/')}".lower()


def is_verified(url: str, searched_urls: set[str]) -> bool:
    n = norm_url(url)
    return bool(n) and any(n == s or n.startswith(s + "/") or s.startswith(n + "/") for s in searched_urls)


def host(url: str) -> str:
    return urlparse(url.strip()).netloc.lower().removeprefix("www.")


def background_only(url: str, domains) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in domains or ())


def parse_published(s: str | None) -> tuple[float | None, bool]:
    """Returns (timestamp, has_time). Date-only strings come back with has_time False."""
    if not s:
        return None, False
    s = s.strip()
    try:
        if len(s) == 10:
            d = date.fromisoformat(s)
            return datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp(), False
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp(), True
    except ValueError:
        return None, False


def timing(published: str | None, move_start_ts: float, slack_s: float) -> tuple[str, float | None]:
    """'before' (could have caused it), 'after' (reported later), or 'unknown'."""
    ts, has_time = parse_published(published)
    if ts is None:
        return "unknown", None
    if not has_time:  # only a date: decide only when the date is clearly a different day
        day = 86400
        if ts + day <= move_start_ts:
            return "before", ts
        if ts > move_start_ts:
            return "after", ts
        return "unknown", ts
    return ("before" if ts <= move_start_ts + slack_s else "after"), ts


def counts_as(s: dict) -> str:
    """The relation the verdict uses: what the agent said, unless the source may only be background."""
    if s.get("background_only") and s.get("relation") in ("cause", "consequence"):
        return "background"
    return s.get("relation") or "background"


def assess(sources: list[dict], searched: list[dict], move_start_ts: float, slack_minutes: float,
           background_domains=()) -> dict:
    searched_urls = {norm_url(s["url"]) for s in searched if s.get("url")}
    checked = []
    for s in sources:
        when, ts = timing(s.get("published_at"), move_start_ts, slack_minutes * 60)
        checked.append({**s, "verified": is_verified(s.get("url", ""), searched_urls),
                        "background_only": background_only(s.get("url", ""), background_domains),
                        "timing": when, "published_ts": ts})

    causes = [s for s in checked if counts_as(s) == "cause" and s["verified"]]
    if any(s["timing"] == "before" for s in causes):
        verdict = "explained"
    elif any(s["timing"] == "unknown" for s in causes):
        verdict = "unclear"
    else:
        verdict = "unexplained"

    later = [s for s in checked if s["verified"] and s["timing"] == "after"
             and counts_as(s) in ("cause", "consequence") and s["published_ts"]]
    first_later = min((s["published_ts"] for s in later), default=None)
    return {"verdict": verdict, "sources": checked, "first_later_report_ts": first_later}
