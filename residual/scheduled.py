"""Scheduled mode: one scan per run instead of a loop that polls every two minutes.

Each run compares prices with the previous run's (kept in a small compressed file) and, for Polymarket,
with the platform's own one-hour change. Flagged moves are queued for Claude to investigate in the same
session; Claude's findings come back through `record` and get the same verdict rules as the loop.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from collections import defaultdict
from pathlib import Path

from . import detect, investigate, pipeline

log = logging.getLogger(__name__)
MAX_STATE_AGE_S = 3 * 3600  # older than this, the last run's prices are too stale to compare with


def load_state(path: Path) -> dict:
    if path.exists():
        with gzip.open(path, "rt") as f:
            return json.load(f)
    return {"saved_ts": {}, "prices": {}}


def save_state(path: Path, state: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(path, "wt") as f:
        json.dump(state, f, separators=(",", ":"))


def scan(db, cfg, state_path: Path) -> list[int]:
    state = load_state(state_path)
    new_ids = []
    for platform in cfg.platforms:
        now = time.time()
        try:
            rows = detect.SOURCES[platform].fetch_markets(cfg)
        except Exception as e:
            log.error("%s fetch failed: %s", platform, e)
            continue
        prev_ts = state["saved_ts"].get(platform)
        prev = state["prices"].get(platform, {}) if prev_ts and now - prev_ts <= MAX_STATE_AGE_S else {}
        live = [r for r in rows if detect.eligible(r, cfg, now)]

        by_event = defaultdict(list)
        for r in live:
            by_event[r["event_id"]].append(r)

        cands = []
        for r in live:
            base, base_ts = None, None
            if r["market_id"] in prev:
                base, base_ts = prev[r["market_id"]], prev_ts
            hour = r["extra"].get("one_hour_change") if platform == "polymarket" else None
            if hour is not None and (base is None or abs(hour) > abs(r["price"] - base)):
                base, base_ts = r["price"] - hour, now - 3600
            if base is None or abs(r["price"] - base) < cfg.move_threshold:
                continue
            last = db.last_move(platform, r["market_id"])
            if last and now - last["detected_ts"] < cfg.cooldown_hours * 3600 \
                    and abs(r["price"] - last["price_to"]) < cfg.move_threshold:
                continue
            related = []
            for sib in by_event.get(r["event_id"], []):
                if sib is r or sib["market_id"] not in prev:
                    continue
                if abs(sib["price"] - prev[sib["market_id"]]) >= cfg.move_threshold / 2:
                    related.append({"question": sib["question"], "from": round(prev[sib["market_id"]], 3),
                                    "to": round(sib["price"], 3)})
            related.sort(key=lambda x: -abs(x["to"] - x["from"]))
            cands.append({"row": r, "base": base, "delta": r["price"] - base,
                          "series": [(base_ts, base), (now, r["price"])], "related": related[:8]})

        db.upsert_markets([c["row"] for c in cands])  # keep metadata only for flagged markets
        log.info("%s: %d markets (%d watched), %d sharp moves%s", platform, len(rows), len(live), len(cands),
                 "" if prev else " (no previous prices yet)")
        for c in cands:
            try:
                mv = detect.build_move(db, platform, c, now, cfg)
            except Exception as e:
                log.warning("could not build move for %s: %s", c["row"]["market_id"], e)
                continue
            vol = mv["signals"].get("window_volume_usd")
            if vol is not None and vol < cfg.min_window_volume_usd:
                db.update_move(db.add_move(mv), status="skipped")
                continue
            new_ids.append(db.add_move(mv))
            log.info("flagged: %s %+.0f pts  %s", platform, c["delta"] * 100, c["row"]["question"][:80])

        state["saved_ts"][platform] = now
        state["prices"][platform] = {r["market_id"]: round(r["price"], 4) for r in live}
    save_state(state_path, state)
    return new_ids


def queue(db, cfg) -> list[dict]:
    """Moves for Claude to look into this run: the most suspicious new ones, then re-checks that are due."""
    new = sorted(db.moves("status='new'"), key=pipeline.priority, reverse=True)[: cfg.max_new_per_run]
    due = db.moves("next_recheck_ts IS NOT NULL AND next_recheck_ts <= ?", (time.time(),))[: cfg.max_rechecks_per_run]
    out = []
    for kind, moves in (("investigate", new), ("recheck", due)):
        for mv in moves:
            market = db.market(mv["platform"], mv["market_id"])
            out.append({
                "move_id": mv["id"],
                "kind": kind,
                "question": mv["question"],
                "move_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mv["move_start_ts"])),
                "brief_for_agent": investigate.describe_move(mv, market, time.time()),
            })
    return out


def record(db, cfg, move_id: int, kind: str, findings: dict) -> dict:
    mv = db.get_move(move_id)
    if not mv:
        raise SystemExit(f"no move {move_id}")
    searched = [{"url": u} if isinstance(u, str) else u for u in findings.pop("searched", [])]
    findings.setdefault("sources", [])
    findings.setdefault("check_next", [])
    result = {"stage": kind, "model": "claude (scheduled session)", "findings": findings,
              "searched": searched, "usage": {"web_searches": len(findings.get("queries") or [])}}
    return pipeline.apply(db, cfg, mv, result, kind)
