"""Day mode: rank the day's news by how much it changed expectations, topic by topic.

Each run looks at every market's price now against 24 hours earlier.

    day score of a market = |price change over 24 hours| x its normal daily dollars

Normal daily dollars are a week's trading divided by seven (Polymarket) or the last 24 hours' trading
(Kalshi, which reports no weekly figure). Markets about the same thing are grouped into topics: markets
in the same event, then events that share specific tags or names. A topic's score is
10 x log10(1 + the sum of its markets' day scores), capped at 100, the same scale as before.

Each topic becomes a story (or updates the story it already is), represented by one "day move" per
market, so merging, scoring, verification and the site work as they do for hourly moves. The scent the
hound gets describes the whole topic: how strong the signal is, which markets moved and which way, when
during the day the move happened, and what to find.
"""

from __future__ import annotations

import logging
import math
import time
from collections import Counter, defaultdict

from . import detect, stories
from .sources import polymarket

log = logging.getLogger(__name__)

DAY_STATUS = "day"  # moves.status for the markets of a day-mode topic


# ---------- scoring ----------

def _tags(row: dict) -> list[str]:
    return [t.strip().lower() for t in (row.get("category") or "").replace(",", ";").split(";") if t.strip()]


def day_change(row: dict) -> float | None:
    x = row.get("extra") or {}
    if row["platform"] == "polymarket":
        c = x.get("one_day_change")
        return None if c is None else float(c)
    prev = x.get("previous_price")
    return None if not prev else row["price"] - float(prev)


def normal_usd(row: dict) -> float:
    x = row.get("extra") or {}
    week = x.get("volume1wk")
    if week:
        return float(week) / 7
    v24 = row.get("volume24h") or 0
    if row["platform"] == "kalshi":  # contracts, each worth about its price
        return v24 * max(row["price"], 0.05)
    return v24


def day_usd(row: dict) -> float:
    v24 = row.get("volume24h") or 0
    return v24 * max(row["price"], 0.05) if row["platform"] == "kalshi" else v24


def eligible(row: dict, cfg, now: float) -> bool:
    """Markets that can tell us about the news: not resolving within a day, not a live game or price
    feed, not soft news (sports, entertainment), not a vote count being settled."""
    end = row.get("end_ts")
    if end and end - now < cfg.day_min_hours_to_end * 3600:
        return False
    tags = set(_tags(row))
    if tags & set(cfg.day_soft_tags) or tags & set(cfg.day_settling_tags):
        return False
    q = f" {(row.get('question') or '').lower()} ".replace("?", " ")
    if any(k in q for k in cfg.excluded_keywords + cfg.day_excluded_keywords):
        return False
    cat = (row.get("category") or "").lower()
    if any(c in cat for c in cfg.excluded_categories) and not detect.is_macro(cat, q, end, cfg, now):
        return False
    return True


def score_markets(rows: list[dict], cfg, now: float) -> list[dict]:
    out = []
    for r in rows:
        chg = day_change(r)
        if chg is None or abs(chg) < cfg.day_min_change or day_usd(r) < cfg.day_min_usd:
            continue
        if not eligible(r, cfg, now):
            continue
        n = normal_usd(r)
        out.append({**r, "chg": chg, "normal": n, "day_usd": day_usd(r), "s": abs(chg) * n})
    out.sort(key=lambda m: -m["s"])
    return out


# ---------- grouping ----------

def group(markets: list[dict], cfg, max_events: int | None = None) -> list[dict]:
    """Markets -> events -> topics. Events join a topic when they share two specific tags, or one
    specific tag and a name, or a name in both event titles. Returns topics, biggest first."""
    evs: dict[tuple, dict] = {}
    for m in markets:
        e = evs.setdefault((m["platform"], m["event_id"]), {
            "platform": m["platform"], "event_id": m["event_id"], "title": m.get("event_title") or m["question"],
            "tags": set(_tags(m)), "markets": [], "s": 0.0})
        e["markets"].append(m)
        e["s"] += m["s"]
    events = sorted(evs.values(), key=lambda e: -e["s"])[: max_events or cfg.day_max_events]
    if not events:
        return []
    generic = {t.lower() for t in cfg.day_generic_tags}
    tag_n = Counter(t for e in events for t in e["tags"])
    rare = max(2, cfg.day_tag_rarity * len(events))  # a tag two events share can always tie them
    stop = cfg.group_stopwords
    for e in events:
        e["spec"] = {t for t in e["tags"] if t not in generic and tag_n[t] <= rare}
        e["title_names"] = stories.names(e["title"], stop)
        e["names"] = e["title_names"] | set().union(*(stories.names(m["question"], stop) for m in e["markets"][:3]))
    # a name links events only when it is rare among the day's events ("Hegseth" yes, "Israel" no)
    name_n = Counter(n for e in events for n in e["names"])
    for e in events:
        e["title_names"] = {n for n in e["title_names"] if name_n[n] <= cfg.day_name_max_events}
        e["names"] = {n for n in e["names"] if name_n[n] <= cfg.day_name_max_events}

    parent = list(range(len(events)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(events):
        for j in range(i + 1, len(events)):
            b = events[j]
            tags = a["spec"] & b["spec"]
            if len(tags) >= 2 or (tags and a["names"] & b["names"]) or (a["title_names"] & b["title_names"]):
                parent[find(i)] = find(j)
    groups = defaultdict(list)
    for i, e in enumerate(events):
        groups[find(i)].append(e)
    topics = []
    for g in groups.values():
        g.sort(key=lambda e: -e["s"])
        ms = sorted((m for e in g for m in e["markets"]), key=lambda m: -m["s"])
        raw = sum(m["s"] for m in ms)
        topics.append({"raw": raw, "score": stories.index(raw), "events": g, "markets": ms,
                       "normal_usd": sum(m["normal"] for m in ms), "day_usd": sum(m["day_usd"] for m in ms)})
    topics.sort(key=lambda t: -t["raw"])
    for rank, t in enumerate(topics, 1):
        t["rank"] = rank
    return topics


def strength(t: dict, cfg) -> str:
    """How much to trust the signal: strong, moderate or weak."""
    top = t["markets"][0]
    if t["score"] >= cfg.day_strong_score and t["normal_usd"] >= cfg.day_strong_usd:
        return "strong"
    if t["normal_usd"] < cfg.lead_min_money_usd or (len(t["markets"]) == 1 and top["normal"] < cfg.day_strong_usd):
        return "weak"
    return "moderate"


# ---------- timing ----------

def timing(t: dict, now: float) -> dict | None:
    """When the lead Polymarket market moved: the time half of its 24-hour move had happened, and its
    biggest 30-minute step. None when there is no price history to read."""
    m = next((m for m in t["markets"] if m["platform"] == "polymarket" and (m.get("extra") or {}).get("yes_token")), None)
    if not m:
        return None
    try:
        h = polymarket.price_history(m["extra"]["yes_token"], now - 26 * 3600, now)
    except Exception as e:
        log.warning("no price history for %s: %s", m["question"][:60], e)
        return None
    h = [(ts, p) for ts, p in h if ts >= now - 24 * 3600]
    if len(h) < 2:
        return None
    p0, total = h[0][1], h[-1][1] - h[0][1]
    half = next((ts for ts, p in h if abs(p - p0) >= abs(total) / 2), None) if total else None
    step, step_ts, j = 0.0, None, 0
    for i in range(len(h)):  # biggest change over any 30 minutes
        while h[i][0] - h[j][0] > 1800:
            j += 1
        if abs(h[i][1] - h[j][1]) > abs(step):
            step, step_ts = h[i][1] - h[j][1], h[i][0]
    return {"market": m["question"], "half_ts": half, "step_points": round(step * 100, 1), "step_ts": step_ts}


# ---------- topics -> stories ----------

def _story_events(db, day: str) -> dict[tuple, int]:
    """(platform, event_id) -> today's story holding a market from that event."""
    cur = db.conn.execute(
        """SELECT m.platform, k.event_id, s.id FROM stories s JOIN moves m ON m.story_id = s.id
           JOIN markets k ON k.platform = m.platform AND k.market_id = m.market_id
           WHERE s.day = ? AND k.event_id IS NOT NULL ORDER BY s.created_ts""", (day,))
    out = {}
    for platform, ev, sid in cur:
        out.setdefault((platform, ev), sid)
    return out


def attach(db, cfg, topics: list[dict], now: float, timings: dict[int, dict] | None = None) -> list[int]:
    """Make each topic a story, or update the one it already is. A topic touching several of today's
    stories merges them. The story's markets are replaced by the topic's current ones."""
    day = stories.et_day(now)
    owner = _story_events(db, day)
    touched = []
    for t in topics:
        if t["score"] < cfg.day_story_min_score or len(touched) >= cfg.day_max_topics:
            break
        sids = list(dict.fromkeys(owner[(e["platform"], e["event_id"])] for e in t["events"]
                                  if (e["platform"], e["event_id"]) in owner))
        sids = [s for s in sids if db.get_story(s)]
        if not sids:
            sid = db.add_story({"day": day, "created_ts": now, "updated_ts": now, "status": "pending"})
        elif len(sids) == 1:
            sid = sids[0]
        else:
            sid = stories.merge(db, sids[0], sids[1:])
        first = sid not in touched
        if first:  # the story's markets are replaced by today's view of the topic
            db.conn.execute("UPDATE moves SET story_id=NULL WHERE story_id=?", (sid,))
            db.conn.commit()
            touched.append(sid)
        was_day = bool((db.get_story(sid) or {}).get("scent_data"))
        _add_markets(db, sid, t, now)
        st = stories.recompute(db, sid)
        if first and not was_day and st.get("hunted_ts"):
            if st["status"] in ("reported", "unclear"):
                # published from an hourly hunt: the new score is a change of scale, not new news
                db.update_story(sid, hunted_score=st["score"])
            else:
                # an open lead from the hourly detector: hunt it once with the day-level scent
                db.update_story(sid, hunted_ts=None, hunted_score=None, next_recheck_ts=None)
        if first:
            sd = {"rank": t["rank"], "strength": strength(t, cfg), "normal_usd": round(t["normal_usd"]),
                  "events": [e["title"] for e in t["events"][:6]], "updated_ts": now, "window_start_ts": now - 86400}
            if timings and t["rank"] in timings:
                sd["timing"] = timings[t["rank"]]
        else:  # a second topic that is already this story: keep the first one's rank and timing
            sd = dict(st.get("scent_data") or {})
            sd["normal_usd"] = round(st.get("money_usd") or 0)
            sd["events"] = (sd.get("events") or []) + [e["title"] for e in t["events"][:3]]
        db.update_story(sid, scent_data=sd)
        for e in t["events"]:
            owner[(e["platform"], e["event_id"])] = sid
    return touched


def _add_markets(db, sid: int, t: dict, now: float):
    db.upsert_markets([{k: m.get(k) for k in ("platform", "market_id", "question", "url", "event_id",
                                                 "event_title", "category", "end_ts", "rules", "extra")}
                       for m in t["markets"][:40]])
    for m in t["markets"][:40]:
        mid = db.add_move({
            "platform": m["platform"], "market_id": m["market_id"], "question": m["question"], "url": m["url"],
            "detected_ts": now, "move_start_ts": now - 86400, "price_from": m["price"] - m["chg"],
            "price_to": m["price"], "series": [],
            # window_volume_usd x volume_multiple 1 makes stories.intensity the market's day score
            "signals": {"window_volume_usd": round(m["normal"]), "volume_multiple": 1,
                        "day_usd": round(m["day_usd"]), "day_score": round(m["s"])},
        })
        db.update_move(mid, story_id=sid, status=DAY_STATUS)


def scan(db, cfg) -> list[int]:
    """One day-mode run: fetch, score, group, read the timing of the leading topics, attach to stories."""
    now = time.time()
    rows = []
    for platform in cfg.platforms:
        try:
            got = detect.SOURCES[platform].fetch_markets(cfg)
            rows += got
            log.info("%s: %d markets", platform, len(got))
        except Exception as e:
            log.error("%s fetch failed: %s", platform, e)
    markets = score_markets(rows, cfg, now)
    topics = group(markets, cfg)
    timings = {}
    for t in topics[: cfg.day_timing_topics]:
        if t["score"] >= cfg.day_hunt_min_score:
            tm = timing(t, now)
            if tm:
                timings[t["rank"]] = tm
    ids = attach(db, cfg, topics, now, timings)
    for t in topics[:10]:
        log.info("topic %2d (%.1f, %s): %s", t["rank"], t["score"], strength(t, cfg), t["events"][0]["title"][:70])
    log.info("%d markets moved, %d topics, %d stories updated", len(markets), len(topics), len(ids))
    return ids


# ---------- the scent ----------

def _pct(p: float) -> str:
    return f"{p * 100:.0f}%"


def _when(sd: dict) -> str:
    tm = sd.get("timing")
    if not tm:
        return "No intraday price history; the move happened at some point in the last 24 hours."
    parts = []
    if tm.get("half_ts"):
        parts.append(f"half the move had happened by {stories.fmt_et(tm['half_ts'])}")
    if tm.get("step_ts") and abs(tm.get("step_points") or 0) >= 3:
        parts.append(f"the sharpest 30-minute step ({tm['step_points']:+.0f} points) ended around {stories.fmt_et(tm['step_ts'])}")
    return ("In the lead market, " + " and ".join(parts) + ".") if parts else "The move was gradual through the day."


STRENGTH_NOTE = {
    "strong": "Strong: many dollars and markets moved together. Something real very likely happened.",
    "moderate": "Moderate: a real shift, but in fewer or smaller markets.",
    "weak": "Weak: thin markets or a single small one. One trader can move these. Run two or three searches; "
            "if nothing specific turns up, submit an open lead and stop.",
}


def scent(db, story: dict) -> str:
    sd = story.get("scent_data") or {}
    moves = sorted(db.story_moves(story["id"]), key=stories.intensity, reverse=True)
    lines = [
        f"Rank {sd.get('rank', '?')} of today's topics by how much they changed expectations "
        f"(importance {story['score']:.0f}/100). {len(moves)} market(s) moved, in markets that normally trade "
        f"about ${sd.get('normal_usd', story.get('money_usd') or 0):,.0f} a day.",
        STRENGTH_NOTE.get(sd.get("strength"), ""),
        "",
        "What changed in the last 24 hours (now less or more likely, from a day ago):",
    ]
    for m in moves[:12]:
        s = m.get("signals") or {}
        way = "more likely" if m["price_to"] > m["price_from"] else "less likely"
        lines.append(f"- \"{m['question']}\": {_pct(m['price_from'])} to {_pct(m['price_to'])}, now {way} "
                     f"(normally ${s.get('window_volume_usd', 0):,.0f} a day).")
    if len(moves) > 12:
        lines.append(f"- and {len(moves) - 12} smaller moves in the same topic.")
    if sd.get("events"):
        lines += ["", "Market groups in this topic: " + "; ".join(sd["events"])]
    lines += ["", "When: " + _when(sd), "",
              "Find: the development behind this shift and how much it matters. If one development explains "
              "several of these markets, report that one development. If the shift has no single trigger "
              "(a slow drift, or the knock-on of bigger news), say so in the summary and name the bigger news."]
    top = db.market(moves[0]["platform"], moves[0]["market_id"]) if moves else None
    if top and top.get("rules"):
        lines += ["", f"How the biggest market resolves (short): {top['rules'][:400]}"]
    if story.get("question") and not story.get("headline"):
        lines += ["", f"Open question from the first hunt: {story['question']}"]
        if story.get("checked_domains"):
            lines.append(f"Outlets already searched: {', '.join(story['checked_domains'][:40])}")
    if story.get("headline"):
        lines += ["", f"Already reported on this story: {story['headline']}. {story.get('summary') or ''}"]
    return "\n".join(x for x in lines if x is not None)
