"""Stories: moves grouped by what they are about, scored by how much money repriced.

Importance index
----------------
For each market in a story:

    intensity = points moved (0-1) x dollars traded during the move x sqrt(volume vs normal, capped at 100)

The story's raw score is the sum over its markets. The index shown on the page is
10 x log10(1 + raw), capped at 100, so each 10 points is ten times more money-weighted repricing.
A 17-point move on $20,000 of trading at ten times the usual pace scores about 40.
"""

from __future__ import annotations

import math
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from . import verdict as V

ET = ZoneInfo("America/New_York")


def et_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).strftime("%Y-%m-%d")


def fmt_et(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).strftime("%b %d %H:%M ET")


# ---------- scoring ----------

def intensity(mv: dict) -> float:
    s = mv.get("signals") or {}
    dp = abs(mv["price_to"] - mv["price_from"])
    money = s.get("window_volume_usd") or 0
    vm = min(max(s.get("volume_multiple") or 1, 1), 100)
    return dp * money * math.sqrt(vm)


def index(raw: float) -> float:
    return round(min(100.0, 10 * math.log10(1 + max(raw, 0))), 1)


def recompute(db, story_id: int) -> dict:
    moves = db.story_moves(story_id)
    raw = sum(intensity(m) for m in moves)
    db.update_story(
        story_id, raw=raw, score=index(raw), updated_ts=time.time(),
        money_usd=sum((m.get("signals") or {}).get("window_volume_usd") or 0 for m in moves),
        markets=len({(m["platform"], m["market_id"]) for m in moves}),
        max_points=round(max((abs(m["price_to"] - m["price_from"]) * 100 for m in moves), default=0), 1),
    )
    return db.get_story(story_id)


# ---------- grouping ----------

def _story_for(db, day: str, platform: str, market_id: str, event_id: str | None):
    """Today's story already holding this market, or another market from the same event."""
    r = db.conn.execute(
        """SELECT s.id FROM stories s JOIN moves m ON m.story_id = s.id
           LEFT JOIN markets k ON k.platform = m.platform AND k.market_id = m.market_id
           WHERE s.day = ? AND m.platform = ? AND (m.market_id = ? OR (k.event_id IS NOT NULL AND k.event_id = ?))
           ORDER BY s.score DESC LIMIT 1""",
        (day, platform, market_id, event_id or "\x00"),
    ).fetchone()
    return r[0] if r else None


def assign(db, move_ids: list[int], now: float | None = None) -> list[int]:
    """Attach new moves to today's stories (same market or same event), creating stories as needed."""
    now = now or time.time()
    day = et_day(now)
    touched = set()
    for mid in move_ids:
        mv = db.get_move(mid)
        mk = db.market(mv["platform"], mv["market_id"]) or {}
        sid = _story_for(db, day, mv["platform"], mv["market_id"], mk.get("event_id"))
        if sid is None:
            sid = db.add_story({"day": day, "created_ts": now, "updated_ts": now, "status": "pending"})
        db.update_move(mid, story_id=sid, status="in_story")
        touched.add(sid)
    for sid in touched:
        recompute(db, sid)
    return sorted(touched)


def merge(db, into_id: int, other_ids: list[int]) -> int:
    """Fold stories that are the same news into one, and return the id of the one that remains.

    The story that remains is the earliest published one (or the earliest, if none is published), so a
    published story's URL never disappears. Any other published story's URL is kept as an alias, and the
    site redirects it. Sources found for each story are kept.
    """
    sts = [db.get_story(i) for i in dict.fromkeys([into_id, *other_ids])]
    sts = [x for x in sts if x]
    published = [x for x in sts if x.get("slug")]
    keep = min(published or sts, key=lambda x: (x["created_ts"] or 0, x["id"]))
    aliases = list(keep.get("aliases") or [])
    sources = list(keep["sources"] or [])
    seen = {V.norm_url(x["url"]) for x in sources}
    for x in sts:
        if x["id"] == keep["id"]:
            continue
        db.conn.execute("UPDATE moves SET story_id=? WHERE story_id=?", (keep["id"], x["id"]))
        db.conn.commit()
        if x.get("slug"):
            aliases.append(f"{x['day']}/{x['slug']}")
        aliases += x.get("aliases") or []
        for src in x["sources"] or []:
            if V.norm_url(src["url"]) not in seen:
                seen.add(V.norm_url(src["url"]))
                sources.append(src)
        db.delete_story(x["id"])
    db.update_story(keep["id"], aliases=list(dict.fromkeys(aliases)), sources=sources)
    recompute(db, keep["id"])
    return keep["id"]


# ---------- the scent ----------

def _direction(mv: dict) -> str:
    return "more likely" if mv["price_to"] > mv["price_from"] else "less likely"


def scent(db, story: dict) -> str:
    """Everything the markets say about this story, written for the agent that goes to find it."""
    moves = sorted(db.story_moves(story["id"]), key=intensity, reverse=True)
    start = min(m["move_start_ts"] for m in moves)
    lines = [
        f"Importance {story['score']:.0f}/100: about ${story['money_usd']:,.0f} traded during the moves, "
        f"across {story['markets']} market(s). Biggest move {story['max_points']:.0f} points.",
        f"The repricing began around {fmt_et(start)} ({datetime.fromtimestamp(start, timezone.utc):%Y-%m-%d %H:%M} UTC).",
        "",
        "What the markets are saying:",
    ]
    for m in moves:
        s = m.get("signals") or {}
        lines.append(
            f"- {m['platform'].title()}: \"{m['question']}\" went from {m['price_from'] * 100:.0f}% to "
            f"{m['price_to'] * 100:.0f}% between {fmt_et(m['move_start_ts'])} and {fmt_et(m['detected_ts'])}. "
            f"Traders now see it as {_direction(m)}. {m['url']}")
        facts = []
        if s.get("window_volume_usd"):
            facts.append(f"${s['window_volume_usd']:,} traded")
        if s.get("volume_multiple"):
            facts.append(f"{s['volume_multiple']}x the usual pace")
        if s.get("top_wallet_share") is not None:
            facts.append(f"largest buyer {s['top_wallet_share']:.0%} of the buying")
        if s.get("fresh_wallets"):
            facts.append(f"{len(s['fresh_wallets'])} new or nearly unused wallets among the top buyers")
        if facts:
            lines.append("  " + "; ".join(facts) + ".")
        for r in (s.get("related") or [])[:4]:
            lines.append(f"  Also moved in the same event: \"{r['question']}\" {r['from'] * 100:.0f}% to {r['to'] * 100:.0f}%.")
    top = db.market(moves[0]["platform"], moves[0]["market_id"]) or {}
    if top.get("rules"):
        lines += ["", f"How the biggest market resolves: {top['rules'][:1200]}"]
    if story.get("question") and not story.get("headline"):
        lines += ["", f"Open question from the first hunt: {story['question']}"]
        if story.get("checked_domains"):
            lines.append(f"Outlets already searched: {', '.join(story['checked_domains'][:40])}")
    if story.get("headline"):
        lines += ["", f"Already reported on this story: {story['headline']}. {story.get('summary') or ''}"]
        for src in story.get("sources") or []:
            if src.get("verified"):
                lines.append(f"- {src.get('outlet') or ''} {src.get('published_at') or 'undated'}: {src['url']}")
    return "\n".join(lines)


# ---------- the hunt queue ----------

def queue(db, cfg) -> list[dict]:
    now = time.time()
    today = et_day(now)
    todays = db.stories("day=?", (today,))
    hunts = [s for s in todays if s["score"] >= cfg.hunt_min_score
             and (s["hunted_ts"] is None or s["score"] >= (s["hunted_score"] or 0) + cfg.rehunt_gain)]
    hunts = hunts[: cfg.max_new_per_run]
    due = db.stories("status IN ('unreported','unclear') AND next_recheck_ts IS NOT NULL AND next_recheck_ts <= ?",
                     (now,), order="next_recheck_ts")[: cfg.max_rechecks_per_run]
    others = others_today(db, todays)
    out = []
    for kind, group in (("hunt", hunts), ("recheck", due)):
        for s in group:
            moves = db.story_moves(s["id"])
            out.append({
                "story_id": s["id"], "kind": kind, "score": s["score"],
                "move_start_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(min(m["move_start_ts"] for m in moves))),
                "scent": scent(db, s),
                "other_stories_today": [o for o in others if o["story_id"] != s["id"]],
            })
    return out


def others_today(db, todays: list[dict] | None = None) -> list[dict]:
    """Today's stories as the hunter sees them, so it can spot the same news told twice."""
    if todays is None:
        todays = db.stories("day=?", (et_day(time.time()),))
    return [{"story_id": s["id"], "score": s["score"],
             "about": s["headline"] or s.get("question") or (db.story_moves(s["id"]) or [{}])[0].get("question")}
            for s in todays]


# ---------- recording what the agent found ----------

ROLES = ("original report", "denial", "confirmation", "statement", "announcement", "filing", "data",
         "follow-up", "analysis", "context")
DEFAULT_ROLE = {"cause": "original report", "consequence": "follow-up", "background": "context"}


def slugify(text: str, words: int = 6) -> str:
    w = re.findall(r"[a-z0-9]+", (text or "").lower().replace("'", ""))
    stop = {"a", "an", "the", "of", "to", "and", "for", "in", "on", "that", "is", "as", "at", "by", "with"}
    keep = [x for x in w if x not in stop][:words] or w[:words] or ["story"]
    return "-".join(keep)[:60].strip("-")


def unique_slug(db, day: str, want: str, story_id: int) -> str:
    taken = {r["slug"] for r in db.stories("day=? AND id<>? AND slug IS NOT NULL", (day, story_id))}
    slug, n = want, 2
    while slug in taken:
        slug, n = f"{want}-{n}", n + 1
    return slug


def clean_image(img, verified_urls: set[str], blocked_hosts=()):
    """Keep the lead image only if it is an https URL, can be shown on another site, and comes from a
    page this run actually found."""
    if not isinstance(img, dict) or not str(img.get("url", "")).startswith("https://"):
        return None
    if V.background_only(img["url"], blocked_hosts):
        return None
    src = img.get("source_url") or ""
    if not src or V.norm_url(src) not in verified_urls:
        return None
    return {k: img.get(k) for k in ("url", "source_url", "outlet", "credit", "caption", "alt") if img.get(k)}


def record(db, cfg, story_id: int, kind: str, f: dict) -> dict:
    story = db.get_story(story_id)
    if not story:
        raise SystemExit(f"no story {story_id}")
    if f.get("merge_with"):
        story_id = merge(db, story_id, [int(x) for x in f["merge_with"]])
        story = db.get_story(story_id)
    moves = db.story_moves(story_id)
    start = min(m["move_start_ts"] for m in moves)
    searched = [{"url": u} if isinstance(u, str) else u for u in f.get("searched", [])]
    for src in f.get("sources") or []:
        role = (src.get("role") or "").lower().strip()
        src["role"] = role if role in ROLES else DEFAULT_ROLE.get(src.get("relation"), "context")
    a = V.assess(f.get("sources") or [], searched, start, cfg.reaction_slack_minutes, cfg.background_only_domains)

    # keep earlier verified sources, add the new ones
    seen = {V.norm_url(s["url"]) for s in a["sources"]}
    sources = a["sources"] + [s for s in story["sources"] if V.norm_url(s["url"]) not in seen]
    earlier = {"reported": "explained", "unclear": "unclear", "unreported": "unexplained"}.get(story["status"])
    new = a["verdict"]
    if kind == "recheck" and new != "explained" and earlier:
        new = earlier  # a later report confirms an unreported story; it doesn't make it reported
    # sources verified in an earlier hunt still count: a new hunt never downgrades a story
    rank = {"unexplained": 0, "unclear": 1, "explained": 2}
    for src in story["sources"] or []:
        if src.get("verified") and V.counts_as(src) == "cause":
            when, _ = V.timing(src.get("published_at"), start, cfg.reaction_slack_minutes * 60)
            prior = {"before": "explained", "unknown": "unclear"}.get(when, "unexplained")
            if rank[prior] > rank[new]:
                new = prior
    status = {"explained": "reported", "unclear": "unclear", "unexplained": "unreported"}[new]

    domains = sorted(set(story["checked_domains"] or []) | {V.host(s["url"]) for s in searched if s.get("url")} - {""})
    fields = {
        "status": status, "sources": sources, "updated_ts": time.time(), "checked_domains": domains,
        "check_next": f.get("check_next") or story["check_next"],
    }
    for k in ("headline", "dek", "summary", "why_it_matters", "dateline", "question", "trail_note"):
        if f.get(k):
            fields[k] = f[k]
    verified = {V.norm_url(s["url"]) for s in sources if s.get("verified")}
    img = clean_image(f.get("image"), verified | {V.norm_url(s["url"]) for s in searched if s.get("url")},
                      cfg.unembeddable_image_hosts)
    if img:
        fields["image"] = img
    if fields.get("headline") and not story.get("slug"):  # set once, so the story's URL never changes
        fields["slug"] = unique_slug(db, story["day"], slugify(f.get("slug") or fields["headline"], 8), story_id)
    if kind == "hunt":
        fields.update(hunted_ts=time.time(), hunted_score=story["score"])
    else:
        fields["rechecks_done"] = (story["rechecks_done"] or 0) + 1
    if status in ("unreported", "unclear"):
        later = a["first_later_report_ts"]
        if later and not story["confirmed_ts"]:
            fields.update(confirmed_ts=later, lead_minutes=round((later - start) / 60))
        done = fields.get("rechecks_done", story["rechecks_done"] or 0)
        delays = cfg.recheck_after_hours
        fields["next_recheck_ts"] = story["created_ts"] + delays[done] * 3600 if done < len(delays) else None
        if fields.get("confirmed_ts") or story["confirmed_ts"]:
            fields["next_recheck_ts"] = None  # reporting found: the lead becomes a story, no more rechecks
    else:
        fields["next_recheck_ts"] = None
    db.update_story(story_id, **fields)
    return db.get_story(story_id)


# ---------- naming the stories nobody hunts ----------

def unlabeled(db, cfg, day: str | None = None) -> list[dict]:
    """Today's stories below the hunt threshold that have no short topic label yet."""
    day = day or et_day(time.time())
    out = []
    for s in db.stories("day=? AND score<? AND topic IS NULL", (day, cfg.hunt_min_score)):
        out.append({"story_id": s["id"], "score": s["score"],
                    "markets": [m["question"] for m in db.story_moves(s["id"])]})
    return out


def label(db, story_id: int, topic: str) -> dict:
    if not db.get_story(story_id):
        raise SystemExit(f"no story {story_id}")
    db.update_story(story_id, topic=topic.strip())
    return db.get_story(story_id)
