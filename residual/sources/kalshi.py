"""Kalshi: open events with nested markets, and recent trades. Public endpoints, no key needed."""

from __future__ import annotations

import logging
from datetime import datetime

from ..http import get_json

log = logging.getLogger(__name__)

API = "https://api.elections.kalshi.com/trade-api/v2"
PACE = 0.35  # Kalshi rate-limits bursts; stay well under it


def _ts(s: str | None) -> float | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _f(x) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def fetch_markets(cfg) -> list[dict]:
    out, cursor = [], None
    for _ in range(cfg.kalshi_max_pages):
        d = get_json(f"{API}/events", {
            "status": "open", "with_nested_markets": "true", "limit": 200, "cursor": cursor,
        }, min_interval=PACE)
        for ev in d.get("events") or []:
            for m in ev.get("markets") or []:
                row = _normalize(m, ev, cfg)
                if row:
                    out.append(row)
        cursor = d.get("cursor")
        if not cursor or not d.get("events"):
            break
    return out


def _prev(m: dict) -> float | None:
    """The price a day ago: previous_price_dollars, or the older previous_price in cents."""
    p = _f(m.get("previous_price_dollars"))
    if p is None and _f(m.get("previous_price")) is not None:
        p = _f(m.get("previous_price")) / 100
    return p or None


def _normalize(m: dict, ev: dict, cfg) -> dict | None:
    if m.get("status") not in (None, "active", "open"):
        return None
    if (m.get("mve_collection_ticker") or "") or m["ticker"].startswith("KXMVE"):
        return None  # multi-leg combo markets
    bid, ask, last = _f(m.get("yes_bid_dollars")), _f(m.get("yes_ask_dollars")), _f(m.get("last_price_dollars"))
    if bid and ask and 0 < ask - bid <= cfg.max_spread:
        price = (bid + ask) / 2
    else:
        price = last
    if not price:
        return None
    title = m.get("title") or ev.get("title") or ""
    sub = m.get("yes_sub_title") or ""
    question = f"{title} [{sub}]" if sub and sub.lower() not in title.lower() else title
    series = ev.get("series_ticker") or m["event_ticker"].split("-")[0]
    return {
        "platform": "kalshi",
        "market_id": m["ticker"],
        "question": question,
        "url": f"https://kalshi.com/markets/{series.lower()}",
        "event_id": m["event_ticker"],
        "event_title": ev.get("title"),
        "category": ev.get("category") or "",
        "end_ts": _ts(m.get("close_time")),
        "rules": ((m.get("rules_primary") or "") + "\n" + (m.get("rules_secondary") or "")).strip()[:2000],
        "extra": {"series": series, "previous_price": _prev(m)},
        "price": price,
        "volume24h": _f(m.get("volume_24h_fp")),
        "liquidity": _f(m.get("open_interest_fp")),
    }


def window_trades(ticker: str, since_ts: float, max_pages: int = 10) -> list[dict]:
    out, cursor = [], None
    for _ in range(max_pages):
        d = get_json(f"{API}/markets/trades", {
            "ticker": ticker, "min_ts": int(since_ts), "limit": 1000, "cursor": cursor,
        }, min_interval=PACE)
        out.extend(d.get("trades") or [])
        cursor = d.get("cursor")
        if not cursor or not d.get("trades"):
            break
    return out


def trade_series(trades: list[dict]) -> list[tuple[float, float]]:
    pts = [(_ts(t["created_time"]), float(t["yes_price_dollars"])) for t in trades]
    return sorted(p for p in pts if p[0])


def signals(market: dict, move_start_ts: float, now_ts: float, direction: int, cfg) -> dict:
    window_min = max((now_ts - move_start_ts) / 60, 1)
    try:
        trades = window_trades(market["market_id"], move_start_ts)
    except Exception as e:
        log.warning("trades unavailable for %s: %s", market["market_id"], e)
        return {"error": f"trades unavailable: {e}"}
    total_usd, with_move, first_large = 0.0, 0.0, None
    for t in trades:
        count = float(t.get("count_fp") or 0)
        usd = count * float(t["yes_price_dollars"] if t["taker_side"] == "yes" else t["no_price_dollars"])
        total_usd += usd
        if (t["taker_side"] == "yes") == (direction > 0):
            with_move += usd
            ts = _ts(t["created_time"])
            if usd >= 1000 and (first_large is None or ts < first_large):
                first_large = ts
    # 24h volume is in contracts; price it at the current level for a rough dollar baseline
    v24 = (market.get("volume24h") or 0) * (market.get("price") or 0.5)
    expected = v24 / (24 * 60) * window_min
    return {
        "window_minutes": round(window_min),
        "window_volume_usd": round(total_usd),
        "volume_multiple": round(total_usd / expected, 1) if expected else None,
        "trades": len(trades),
        "share_with_move": round(with_move / total_usd, 2) if total_usd else None,
        "first_large_trade_ts": first_large,
        "note": "Kalshi accounts are identity-verified and not public, so there are no wallet signals.",
    }
