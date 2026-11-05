"""Investigation flow: quick check, deep dig if needed, then scheduled re-checks for unexplained moves."""

from __future__ import annotations

import logging
import math
import time

from . import brief, investigate, verdict

log = logging.getLogger(__name__)


def priority(move: dict) -> float:
    """Investigate the most suspicious moves first when the hourly budget is tight."""
    s = move.get("signals") or {}
    score = abs(move["price_to"] - move["price_from"]) * 100
    score += 8 * len(s.get("fresh_wallets") or [])
    score += 4 * math.log1p(min(s.get("volume_multiple") or 0, 200))
    if (s.get("top_wallet_share") or 0) > 0.5:
        score += 6
    return score


def budget_left(db, cfg) -> int:
    return cfg.max_investigations_per_hour - db.investigations_since(time.time() - 3600)


def _record(db, move, result, slack):
    db.add_investigation(move["id"], result["stage"], result["model"], result["findings"],
                         result["searched"], result["usage"])
    return verdict.assess(result["findings"]["sources"], result["searched"], move["move_start_ts"], slack)


def investigate_move(db, client, cfg, move: dict) -> dict:
    market = db.market(move["platform"], move["market_id"])
    now = time.time()
    quick = investigate.run(client, "quick", move, market, cfg, now)
    a = _record(db, move, quick, cfg.reaction_slack_minutes)
    if a["verdict"] != "explained" and budget_left(db, cfg) > 0:
        deep = investigate.run(client, "deep", move, market, cfg, time.time())
        a = _record(db, move, deep, cfg.reaction_slack_minutes)
    return _finish(db, cfg, move, a)


def _finish(db, cfg, move, a) -> dict:
    fields = {"status": "investigated", "verdict": a["verdict"]}
    if a["verdict"] == "explained":
        fields["next_recheck_ts"] = None
    else:
        if a["first_later_report_ts"] and not move.get("confirmed_ts"):
            fields["confirmed_ts"] = a["first_later_report_ts"]
            fields["lead_minutes"] = round((a["first_later_report_ts"] - move["move_start_ts"]) / 60)
        done = move.get("rechecks_done") or 0
        delays = cfg.recheck_after_hours
        fields["next_recheck_ts"] = move["detected_ts"] + delays[done] * 3600 if done < len(delays) else None
    db.update_move(move["id"], **fields)
    brief.write_brief(db, cfg, db.get_move(move["id"]))
    return db.get_move(move["id"])


def recheck(db, client, cfg, move: dict) -> dict:
    market = db.market(move["platform"], move["market_id"])
    result = investigate.run(client, "recheck", move, market, cfg, time.time())
    return apply(db, cfg, move, result, "recheck")


def apply(db, cfg, move: dict, result: dict, kind: str) -> dict:
    """Record one investigation pass (from the API loop or a scheduled session) and update the move."""
    a = _record(db, move, result, cfg.reaction_slack_minutes)
    if kind == "recheck":
        db.update_move(move["id"], rechecks_done=(move.get("rechecks_done") or 0) + 1)
        move = db.get_move(move["id"])
        if a["verdict"] == "explained":
            # a cause from before the move that the first searches missed
            log.info("move %s: earlier searches missed a cause", move["id"])
        elif move["verdict"] in ("unexplained", "unclear"):
            a["verdict"] = move["verdict"]  # keep the original call; a later report confirms, it doesn't explain
    return _finish(db, cfg, move, a)


def work(db, client, cfg) -> None:
    """Spend this cycle's budget: new moves first (most suspicious first), then due re-checks."""
    new = sorted(db.moves("status='new'"), key=priority, reverse=True)
    for mv in new:
        if budget_left(db, cfg) <= 0:
            log.info("hourly investigation budget used up; %d moves waiting", len(new))
            return
        try:
            done = investigate_move(db, client, cfg, mv)
            log.info("move %s: %s  %s", mv["id"], done["verdict"], mv["question"][:70])
        except Exception as e:
            log.exception("investigation failed for move %s", mv["id"])
            db.update_move(mv["id"], status="error", brief={"error": str(e)})
    due = db.moves("next_recheck_ts IS NOT NULL AND next_recheck_ts <= ?", (time.time(),))
    for mv in due:
        if budget_left(db, cfg) <= 0:
            return
        try:
            done = recheck(db, client, cfg, mv)
            if done.get("confirmed_ts"):
                log.info("move %s confirmed by later reporting, %s min after the move", mv["id"], done["lead_minutes"])
        except Exception:
            log.exception("re-check failed for move %s", mv["id"])
