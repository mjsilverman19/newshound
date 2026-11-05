"""Flag sharp price moves from stored snapshots, then gather trading signals for each one."""

from __future__ import annotations

import logging
import time

from .sources import kalshi, polymarket

log = logging.getLogger(__name__)
SOURCES = {"polymarket": polymarket, "kalshi": kalshi}


def eligible(row: dict, cfg, now: float) -> bool:
    cat = (row.get("category") or "").lower()
    if any(c in cat for c in cfg.excluded_categories):
        return False
    q = f" {(row.get('question') or '').lower()} "
    if any(k in q for k in cfg.excluded_keywords):
        return False
    end = row.get("end_ts")
    if end and end - now < cfg.skip_if_ends_within_hours * 3600:
        return False  # about to resolve, or already past its end and awaiting results
    liq = row.get("liquidity") or 0
    floor = cfg.min_liquidity_usd if row["platform"] == "polymarket" else cfg.min_open_interest
    return liq >= floor


def move_start(series: list[tuple[float, float]], base: float, delta: float) -> float:
    """Last time the price still sat at its starting level, before it moved a quarter of the way."""
    start = series[0][0]
    for ts, p in series:
        if abs(p - base) >= 0.25 * abs(delta):
            return start
        start = ts
    return start


def find_moves(db, platform: str, rows: list[dict], now: float, cfg) -> list[dict]:
    """Compare each market's price now with its price at the start of the window."""
    since = now - cfg.window_minutes * 60 - cfg.poll_seconds * 1.5
    history = db.window_series_all(platform, since)
    found = []
    for row in rows:
        if not eligible(row, cfg, now):
            continue
        s = history.get(row["market_id"], []) + [(now, row["price"])]
        if len(s) >= 2:
            base = s[0][1]  # oldest price inside the window (or since watching began)
        elif platform == "polymarket" and row["extra"].get("one_hour_change") is not None:
            # cold start: no stored history yet, fall back to Polymarket's own 1-hour change
            base = row["price"] - row["extra"]["one_hour_change"]
            s = [(now - 3600, base), (now, row["price"])]
        else:
            continue
        delta = row["price"] - base
        if abs(delta) < cfg.move_threshold:
            continue
        last = db.last_move(platform, row["market_id"])
        if last and now - last["detected_ts"] < cfg.cooldown_hours * 3600 \
                and abs(row["price"] - last["price_to"]) < cfg.move_threshold:
            continue
        found.append({"row": row, "base": base, "delta": delta, "series": s})
    return found


def build_move(db, platform: str, cand: dict, now: float, cfg) -> dict:
    row, base, delta = cand["row"], cand["base"], cand["delta"]
    direction = 1 if delta > 0 else -1
    chart = cand["series"]
    start = move_start(chart, base, delta)

    # higher-resolution history for the chart and a sharper start time
    try:
        if platform == "polymarket":
            hist = polymarket.price_history(row["extra"]["yes_token"], now - 6 * 3600, now)
        else:
            hist = db.series(platform, row["market_id"], now - 6 * 3600) + \
                kalshi.trade_series(kalshi.window_trades(row["market_id"], now - 2 * 3600, max_pages=2))
            hist.sort()
        if len(hist) >= 5:
            chart = hist + [(now, row["price"])]
            window = [p for p in chart if p[0] >= start - cfg.window_minutes * 60]
            base_hi = window[0][1] if window else base
            start = move_start(window or chart, base_hi, row["price"] - base_hi)
    except Exception as e:
        log.warning("history unavailable for %s: %s", row["market_id"], e)

    market = {**row}
    sig = SOURCES[platform].signals(market, start, now, direction, cfg)
    sig["related"] = cand["related"] if "related" in cand else related_moves(db, platform, row, now, cfg)
    return {
        "platform": platform,
        "market_id": row["market_id"],
        "question": row["question"],
        "url": row["url"],
        "detected_ts": now,
        "move_start_ts": start,
        "price_from": round(base, 4),
        "price_to": round(row["price"], 4),
        "signals": sig,
        "series": downsample(chart, 72),
    }


def related_moves(db, platform: str, row: dict, now: float, cfg) -> list[dict]:
    """Other markets in the same event that moved at least half as much in the window."""
    if not row.get("event_id"):
        return []
    sibs = db.event_siblings(platform, row["event_id"], row["market_id"])
    out = []
    for sib in sibs[:200]:
        s = db.series(platform, sib["market_id"], now - cfg.window_minutes * 60)
        if len(s) >= 2 and abs(s[-1][1] - s[0][1]) >= cfg.move_threshold / 2:
            out.append({"question": sib["question"], "from": round(s[0][1], 3), "to": round(s[-1][1], 3)})
    return sorted(out, key=lambda r: -abs(r["to"] - r["from"]))[:8]


def downsample(series: list[tuple[float, float]], n: int) -> list[list[float]]:
    if len(series) <= n:
        return [[round(t), round(p, 4)] for t, p in series]
    step = (len(series) - 1) / (n - 1)
    return [[round(series[round(i * step)][0]), round(series[round(i * step)][1], 4)] for i in range(n)]


def poll(db, cfg, platforms: list[str] | None = None) -> list[int]:
    """One polling cycle: fetch prices, store them, flag and record new moves. Returns new move ids."""
    new_ids = []
    for platform in platforms or cfg.platforms:
        now = time.time()
        try:
            rows = SOURCES[platform].fetch_markets(cfg)
        except Exception as e:
            log.error("%s fetch failed: %s", platform, e)
            continue
        cands = find_moves(db, platform, rows, now, cfg)
        db.upsert_markets(rows)
        db.add_snapshots(rows, now)
        log.info("%s: %d markets, %d sharp moves", platform, len(rows), len(cands))
        for c in cands:
            try:
                mv = build_move(db, platform, c, now, cfg)
            except Exception as e:
                log.warning("could not build move for %s: %s", c["row"]["market_id"], e)
                continue
            vol = mv["signals"].get("window_volume_usd")
            if vol is not None and vol < cfg.min_window_volume_usd:
                # remember it, so the cooldown stops us re-checking the same thin move every cycle
                db.update_move(db.add_move(mv), status="skipped")
                log.info("skipped (only $%s traded): %s", vol, c["row"]["question"][:80])
                continue
            new_ids.append(db.add_move(mv))
            log.info("flagged: %s %+.0f pts  %s", platform, c["delta"] * 100, c["row"]["question"][:80])
    db.prune_snapshots()
    return new_ids
