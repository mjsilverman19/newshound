"""Polymarket: market listings (Gamma API), price history (CLOB API), trades and wallets (Data API v2)."""

from __future__ import annotations

import json
import logging
import time
from collections import defaultdict
from datetime import datetime

from ..http import get_json

log = logging.getLogger(__name__)

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com/v2"


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
    """Active markets, grouped under their events, highest 24h volume first."""
    out, seen, cursor = [], 0, None
    while seen < cfg.polymarket_max_events:
        d = get_json(f"{GAMMA}/events/keyset", {  # 100 per page; plain offsets stop at 2,000
            "closed": "false", "limit": 100,
            "order": "volume24hr", "ascending": "false", "after_cursor": cursor,
        })
        events = d.get("events") or []
        for ev in events:
            tags = [t.get("label", "") for t in ev.get("tags") or []]
            for m in ev.get("markets") or []:
                row = _normalize(m, ev, tags, cfg)
                if row:
                    out.append(row)
        seen += len(events)
        cursor = d.get("next_cursor")
        if not events or not cursor:
            break
    return out


def _normalize(m: dict, ev: dict, tags: list[str], cfg) -> dict | None:
    if m.get("closed") or not m.get("active") or not m.get("enableOrderBook"):
        return None
    try:
        tokens = json.loads(m.get("clobTokenIds") or "[]")
        outcomes = json.loads(m.get("outcomes") or "[]")
    except json.JSONDecodeError:
        return None
    if len(tokens) != 2:
        return None
    bid, ask, last = _f(m.get("bestBid")), _f(m.get("bestAsk")), _f(m.get("lastTradePrice"))
    if bid is not None and ask is not None and 0 < ask - bid <= cfg.max_spread:
        price = (bid + ask) / 2
    else:
        price = last
    if price is None:
        return None
    question = m.get("question") or ev.get("title") or ""
    if outcomes and outcomes[0] not in ("Yes", "No"):
        question = f"{question} [{outcomes[0]}]"
    ev_slug = ev.get("slug") or ""
    return {
        "platform": "polymarket",
        "market_id": m["conditionId"],
        "question": question,
        "url": f"https://polymarket.com/event/{ev_slug}/{m.get('slug', '')}".rstrip("/"),
        "event_id": str(ev.get("id")),
        "event_title": ev.get("title"),
        "category": "; ".join(tags),
        "end_ts": _ts(m.get("endDate")),
        "rules": (m.get("description") or "")[:2000],
        "extra": {
            "yes_token": tokens[0], "no_token": tokens[1], "outcome": outcomes[0] if outcomes else "Yes",
            "volume1wk": _f(m.get("volume1wk")), "one_hour_change": _f(m.get("oneHourPriceChange")),
            "one_day_change": _f(m.get("oneDayPriceChange")),
        },
        "price": price,
        "volume24h": _f(m.get("volume24hr")),
        "liquidity": _f(m.get("liquidityNum")),
    }


def price_history(token_id: str, start_ts: float, end_ts: float) -> list[tuple[float, float]]:
    d = get_json(f"{CLOB}/prices-history", {
        "market": token_id, "startTs": int(start_ts), "endTs": int(end_ts), "fidelity": 1,
    })
    return [(float(p["t"]), float(p["p"])) for p in (d or {}).get("history", [])]


def window_trades(condition_id: str, since_ts: float, max_pages: int = 20) -> list[dict]:
    """Trades in one market since a time, newest first."""
    out, cursor = [], None
    for _ in range(max_pages):
        d = get_json(f"{DATA}/trades", {"condition": condition_id, "limit": 500, "cursor": cursor})
        rows = d.get("data") or []
        out.extend(r for r in rows if r["timestamp"] >= since_ts)
        cursor = (d.get("pagination") or {}).get("next_cursor")
        if not rows or rows[-1]["timestamp"] < since_ts or not cursor:
            break
    return out


def wallet_trade_count(wallet: str, cap: int) -> int:
    """Number of trades a wallet has ever made, counted up to cap + 1."""
    d = get_json(f"{DATA}/activity", {"user": wallet, "limit": cap + 1, "type": "TRADE"})
    return len(d.get("data") or [])


def signals(market: dict, move_start_ts: float, now_ts: float, direction: int, cfg) -> dict:
    """Trading signals around a move: volume spike, buyer concentration, fresh wallets."""
    extra = market["extra"]
    window_min = max((now_ts - move_start_ts) / 60, 1)
    try:
        trades = window_trades(market["market_id"], move_start_ts)
    except Exception as e:  # keep going without trade data
        log.warning("trades unavailable for %s: %s", market["market_id"], e)
        return {"error": f"trades unavailable: {e}"}

    total_usd = 0.0
    with_move: dict[str, float] = defaultdict(float)
    first_large = None
    for t in trades:
        usd = float(t["size"]) * float(t["price"])
        total_usd += usd
        yes = t["token_id"] == extra["yes_token"]
        pushes_up = (yes and t["side"] == "BUY") or (not yes and t["side"] == "SELL")
        if (pushes_up and direction > 0) or (not pushes_up and direction < 0):
            with_move[t["proxy_wallet"]] += usd
            if usd >= 1000 and (first_large is None or t["timestamp"] < first_large):
                first_large = t["timestamp"]

    weekly = extra.get("volume1wk") or 0
    expected = weekly / (7 * 24 * 60) * window_min if weekly else 0
    ranked = sorted(with_move.items(), key=lambda kv: -kv[1])
    with_total = sum(with_move.values())

    fresh = []
    for wallet, usd in ranked[: cfg.wallets_to_profile]:
        try:
            n = wallet_trade_count(wallet, cfg.fresh_wallet_max_trades)
        except Exception:
            continue
        if n <= cfg.fresh_wallet_max_trades:
            fresh.append({"wallet": wallet, "usd": round(usd), "lifetime_trades": n})

    return {
        "window_minutes": round(window_min),
        "window_volume_usd": round(total_usd),
        "volume_multiple": round(total_usd / expected, 1) if expected else None,
        "trades": len(trades),
        "wallets_with_move": len(with_move),
        "top_wallet_share": round(ranked[0][1] / with_total, 2) if with_total else None,
        "top_wallets": [{"wallet": w, "usd": round(u)} for w, u in ranked[:3]],
        "fresh_wallets": fresh,
        "first_large_trade_ts": first_large,
    }
