"""The front page: today's stories ranked by how much money repriced on them.

Styled after the Book Critic Data site: black and white, system type, hairline rules, ranked lists.
Each story's image is its own price chart. One self-contained HTML file; story pages open with
#s-<id> and need no script.
"""

from __future__ import annotations

import html
import time
from datetime import datetime, timezone

from . import verdict as V
from .stories import ET, et_day, intensity

e = html.escape


def money(x: float | None) -> str:
    x = x or 0
    if x >= 1e6:
        return f"${x / 1e6:.1f}M"
    if x >= 1e3:
        return f"${x / 1e3:.0f}K"
    return f"${x:.0f}"


def when(published: str | None) -> str:
    """A source's publication time in Eastern time, or its date, or 'undated'."""
    ts, has_time = V.parse_published(published)
    if ts is None:
        return "undated"
    return t(ts, "%b %-d, %-I:%M %p ET") if has_time else datetime.fromtimestamp(ts, timezone.utc).strftime("%b %-d")


def t(ts: float | None, fmt: str = "%-I:%M %p") -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).strftime(fmt) if ts else ""


STATUS = {
    "reported": "Reported",
    "unclear": "Reported, timing unclear",
    "unreported": "Nothing reported yet",
    "pending": "Not looked into yet",
}


def status_label(s: dict, cfg) -> str:
    if s["status"] == "pending" and s["score"] < cfg.hunt_min_score:
        return "Below the hunt threshold"
    if s["status"] in ("unreported", "unclear") and s.get("lead_minutes") is not None:
        return f"Markets moved {s['lead_minutes']:.0f} min before the first report"
    return STATUS.get(s["status"], s["status"])


# ---------- charts ----------

def chart(moves: list[dict], w: int = 400, h: int = 300, cls: str = "") -> str:
    """The main market's price line, with the move's window shaded. Stretches to fit its box."""
    if not moves:
        return ""
    mv = max(moves, key=intensity)
    pts = [(float(a), float(b)) for a, b in (mv.get("series") or [])]
    if len(pts) < 2:
        pts = [(mv["move_start_ts"], mv["price_from"]), (mv["detected_ts"], mv["price_to"])]
    t0, t1 = pts[0][0], pts[-1][0]
    span_t = max(t1 - t0, 1)
    lo, hi = min(p for _, p in pts), max(p for _, p in pts)
    pad = max((hi - lo) * 0.15, 0.02)
    lo, hi = max(lo - pad, 0), min(hi + pad, 1)

    def x(ts): return (ts - t0) / span_t * w
    def y(p): return h - (p - lo) / max(hi - lo, 1e-6) * h

    line = " ".join(f"{x(a):.1f},{y(b):.1f}" for a, b in pts)
    xs = max(x(mv["move_start_ts"]), 0)
    shade = f'<rect x="{xs:.1f}" y="0" width="{max(w - xs, 2):.1f}" height="{h}" class="win"/>'
    ex, ey = x(pts[-1][0]), y(pts[-1][1])
    return (f'<svg class="chart {cls}" viewBox="0 0 {w} {h}" preserveAspectRatio="none" role="img" '
            f'aria-label="Price of {e(mv["question"])}, {mv["price_from"] * 100:.0f}% to {mv["price_to"] * 100:.0f}%">'
            f'{shade}<polyline points="{line}" class="ln" vector-effect="non-scaling-stroke"/>'
            f'<circle cx="{ex:.1f}" cy="{ey:.1f}" r="3" class="dot" vector-effect="non-scaling-stroke"/></svg>')


def bar(score: float) -> str:
    return f'<span class="ibar" aria-hidden="true"><i style="width:{max(score, 2):.0f}%"></i></span>'


# ---------- pieces ----------

def title_of(db, s: dict) -> str:
    if s.get("headline"):
        return s["headline"]
    moves = db.story_moves(s["id"])
    top = max(moves, key=intensity) if moves else None
    if not top:
        return "Untitled"
    return f"{top['question']} ({top['price_from'] * 100:.0f}% → {top['price_to'] * 100:.0f}%)"


def numbers(s: dict) -> str:
    return (f"Importance <b>{s['score']:.0f}</b> · {money(s['money_usd'])} traded · "
            f"{s['markets']} market{'s' if s['markets'] != 1 else ''} · {s['max_points']:.0f} pts")


def cover(db, s: dict, cfg) -> str:
    moves = db.story_moves(s["id"])
    return (f'<a class="cv" href="#s-{s["id"]}"><span class="img">{chart(moves, 400, 300)}</span>'
            f'<span class="t">{e(title_of(db, s))}</span>'
            f'<span class="a">{e(s.get("dek") or "")}</span>{bar(s["score"])}'
            f'<span class="v"><b>{s["score"]:.0f}</b> · {money(s["money_usd"])} · {e(status_label(s, cfg))}</span></a>')


def rank_row(db, s: dict, i: int, cfg) -> str:
    return (f'<a class="rank" href="#s-{s["id"]}"><span class="i">{i}</span>'
            f'<span><span class="t">{e(title_of(db, s))}</span>'
            f'<span class="a">{e(s.get("dek") or status_label(s, cfg))}</span>{bar(s["score"])}</span>'
            f'<span class="st">{s["score"]:.0f}<span>{money(s["money_usd"])}</span></span></a>')


def story_page(db, s: dict, cfg) -> str:
    moves = sorted(db.story_moves(s["id"]), key=intensity, reverse=True)
    start = min((m["move_start_ts"] for m in moves), default=s["created_ts"])
    mk = "".join(
        f'<tr><td><a href="{e(m["url"])}">{e(m["question"])}</a><span class="sub">{m["platform"].title()}</span></td>'
        f'<td class="r">{m["price_from"] * 100:.0f}% → {m["price_to"] * 100:.0f}%</td>'
        f'<td class="r">{money((m.get("signals") or {}).get("window_volume_usd"))}</td>'
        f'<td class="r desk">{t(m["move_start_ts"])}</td></tr>' for m in moves)
    srcs = "".join(
        f'<div class="rev"><div class="h"><b>{e(x.get("outlet") or "")}</b> · {e(when(x.get("published_at")))} · '
        f'{e(x["relation"].replace("_", " "))}{", before the move" if x.get("timing") == "before" else ", after the move" if x.get("timing") == "after" else ""}</div>'
        f'<p><a href="{e(x["url"])}">{e(x.get("title") or x["url"])}</a></p>'
        f'<p class="d">{e(x.get("summary") or "")}</p></div>'
        for x in s.get("sources") or [] if x.get("verified"))
    nxt = "".join(f"<li>{e(c)}</li>" for c in s.get("check_next") or [])
    body = []
    if s.get("summary"):
        body.append(f'<p class="big">{e(s["summary"])}</p>')
    if s.get("why_moved"):
        body.append(f'<h2 class="sec">Why the money moved</h2><p>{e(s["why_moved"])}</p>')
    return f"""<section class="story-page" id="s-{s['id']}">
<p class="meta"><a href="#top">← Front page</a> · {e(status_label(s, cfg))}</p>
<h1 class="hl">{e(title_of(db, s))}</h1>
{f'<p class="lede">{e(s["dek"])}</p>' if s.get("dek") else ""}
<p class="meta">{numbers(s)} · moving since {t(start, "%b %-d, %-I:%M %p ET")}</p>
<div class="hero-chart">{chart(moves, 760, 240)}</div>
{"".join(body)}
<h2 class="sec">Markets</h2>
<div class="scroll"><table><thead><tr><th>Market</th><th class="r">Odds</th><th class="r">Traded</th><th class="r desk">Started</th></tr></thead><tbody>{mk}</tbody></table></div>
{f'<h2 class="sec">Sources</h2>{srcs}' if srcs else ""}
{f'<h2 class="sec">What to check next</h2><ul>{nxt}</ul>' if nxt else ""}
</section>"""


# ---------- the page ----------

CSS = """
:root { --fg:#111; --dim:#6b6b6b; --soft:#444; --line:#e6e6e6; --bg:#fff; --off:#b0b0b0; --fill:#f2f2f2; --bar:#111; --win:#ececec; }
@media (prefers-color-scheme: dark) { :root { --fg:#e8e8e8; --dim:#8f8f8f; --soft:#bdbdbd; --line:#262626; --bg:#111; --off:#555; --fill:#1d1d1d; --bar:#ececec; --win:#232323; } }
* { box-sizing:border-box; }
html { background:var(--bg); color:var(--fg); -webkit-text-size-adjust:100%; text-size-adjust:100%; }
body { margin:0 auto; max-width:1200px; padding:32px 24px 96px; font:14px/1.5 -apple-system, BlinkMacSystemFont, "Helvetica Neue", Helvetica, Arial, sans-serif; }
a { color:inherit; text-decoration:none; } a:hover { text-decoration:underline; }
nav { display:flex; gap:20px; align-items:baseline; flex-wrap:wrap; margin-bottom:24px; }
nav a { color:var(--dim); } nav a.brand { color:var(--fg); font-weight:600; margin-right:12px; }
nav .n { margin-left:auto; color:var(--dim); font-variant-numeric:tabular-nums; }
.d, .sub { color:var(--dim); } .sub { display:block; font-size:12px; }
.chart { display:block; width:100%; height:100%; }
.chart .ln { fill:none; stroke:var(--fg); stroke-width:1.5; }
.chart .win { fill:var(--win); } .chart .dot { fill:var(--fg); }
.ibar { display:block; height:5px; max-width:180px; background:var(--line); margin:6px 0; }
.ibar i { display:block; height:100%; background:var(--bar); }
#home { padding-top:24px; }
.hero { display:grid; grid-template-columns:minmax(0,1fr) minmax(0,1.1fr); gap:40px; align-items:end; margin-bottom:64px; }
.kick { color:var(--dim); margin:0 0 12px; font-variant-numeric:tabular-nums; } .kick b, .meta b { font-weight:normal; color:var(--fg); }
#home h1, .hl { font-size:44px; line-height:1.08; letter-spacing:-0.02em; font-weight:normal; margin:0 0 18px; }
.lede { font-size:18px; color:var(--soft); margin:0 0 20px; max-width:640px; }
.hero .img { display:block; aspect-ratio:16/10; background:var(--fill); border-radius:2px; overflow:hidden; }
.hero .src { color:var(--dim); } .hero .src a { color:var(--fg); }
section.blk { margin-bottom:72px; }
.sh { display:flex; align-items:baseline; justify-content:space-between; gap:6px 16px; flex-wrap:wrap; border-bottom:1px solid var(--fg); padding-bottom:8px; margin-bottom:20px; }
h2 { font-size:14px; font-weight:600; margin:0; } .sh .d { font-weight:normal; }
.covers { display:grid; grid-template-columns:repeat(auto-fill, minmax(220px, 1fr)); gap:32px 24px; }
.cv { display:block; } .cv:hover { text-decoration:none; } .cv:hover .t { text-decoration:underline; }
.cv .img { display:block; width:100%; aspect-ratio:4/3; background:var(--fill); border-radius:2px; margin-bottom:10px; overflow:hidden; }
.cv .t { display:block; font-weight:500; line-height:1.3; }
.cv .a { display:block; color:var(--dim); line-height:1.35; margin-top:2px; }
.cv .v { display:block; font-size:13px; color:var(--dim); font-variant-numeric:tabular-nums; } .cv .v b { font-weight:normal; color:var(--fg); }
.lists { display:grid; grid-template-columns:repeat(auto-fit, minmax(320px, 1fr)); gap:48px; }
.rank { display:grid; grid-template-columns:24px minmax(0,1fr) auto; gap:12px; align-items:start; padding:12px 0; border-bottom:1px solid var(--line); }
.rank:hover { text-decoration:none; } .rank:hover .t { text-decoration:underline; }
.rank .i { color:var(--dim); font-variant-numeric:tabular-nums; }
.rank .t { display:block; font-weight:500; line-height:1.3; } .rank .a { display:block; color:var(--dim); line-height:1.3; }
.rank .st { text-align:right; white-space:nowrap; font-variant-numeric:tabular-nums; } .rank .st span { display:block; color:var(--dim); font-size:12px; }
.day { color:var(--dim); margin:28px 0 4px; }
.empty { color:var(--dim); padding:24px 0; }
.cta { border-top:1px solid var(--fg); padding-top:32px; display:grid; grid-template-columns:repeat(auto-fit, minmax(280px, 1fr)); gap:24px 64px; }
.cta h2.big { font-size:24px; font-weight:normal; letter-spacing:-0.01em; margin:0 0 10px; }
.cta p { font-size:15px; color:var(--soft); margin:0 0 12px; max-width:620px; }
.stats { display:flex; gap:32px; flex-wrap:wrap; font-variant-numeric:tabular-nums; }
.stats div b { display:block; font-size:24px; font-weight:normal; } .stats div span { color:var(--dim); }
.story-page { display:none; max-width:760px; padding-top:8px; }
.story-page:target { display:block; }
body:has(.story-page:target) #home { display:none; }
.meta { color:var(--dim); margin:0 0 16px; } .meta a { color:var(--fg); }
.hero-chart { aspect-ratio:760/240; background:var(--fill); border-radius:2px; overflow:hidden; margin:8px 0 28px; }
p.big { font-size:17px; line-height:1.55; margin:0 0 20px; }
h2.sec { font-size:14px; font-weight:600; margin:40px 0 8px; padding-bottom:8px; border-bottom:1px solid var(--fg); }
table { width:100%; border-collapse:collapse; font-variant-numeric:tabular-nums; }
th { font-weight:normal; color:var(--dim); text-align:left; padding:6px 12px 6px 0; white-space:nowrap; }
td { padding:8px 12px 8px 0; border-top:1px solid var(--line); vertical-align:top; }
.r { text-align:right; white-space:nowrap; } th:last-child, td:last-child { padding-right:0; }
.scroll { overflow-x:auto; }
.rev { padding:12px 0; border-top:1px solid var(--line); } .rev .h { color:var(--dim); } .rev .h b { font-weight:normal; color:var(--fg); }
.rev p { margin:4px 0 0; }
ul { padding-left:18px; } li { margin:4px 0; }
@media (max-width:760px) {
  body { padding:20px 16px 80px; font-size:14px; }
  nav a.brand { flex-basis:100%; font-size:15px; margin:0; } nav { gap:8px 16px; }
  nav .n { flex-basis:100%; margin-left:0; font-size:13px; }
  .hero { grid-template-columns:1fr; gap:20px; margin-bottom:48px; }
  .hero .img { order:-1; }
  #home h1, .hl { font-size:30px; line-height:1.12; }
  .lede { font-size:16px; }
  section.blk { margin-bottom:48px; }
  .covers { display:flex; overflow-x:auto; gap:16px; margin-right:-16px; padding-right:16px; padding-bottom:4px; }
  .cv { flex:0 0 220px; }
  .desk { display:none; }
}
"""


def render(db, cfg, now: float | None = None) -> str:
    now = now or time.time()
    today = et_day(now)
    week_ago = et_day(now - 6 * 86400)
    todays = db.stories("day=?", (today,))
    lead_pool = [s for s in todays if s.get("headline")] or todays
    lead = lead_pool[0] if lead_pool else None
    rest = [s for s in todays if lead is None or s["id"] != lead["id"]]
    hunted = [s for s in rest if s.get("headline")]
    quiet = [s for s in rest if not s.get("headline")]
    unreported = db.stories("status IN ('unreported','unclear') AND day>=?", (week_ago,))
    earlier = db.stories("day<? AND day>=?", (today, week_ago), order="day DESC, score DESC")
    everything = todays + [s for s in unreported + earlier if s["day"] != today]

    parts = []
    if lead:
        lmoves = db.story_moves(lead["id"])
        srcs = [x for x in lead.get("sources") or [] if x.get("verified")][:3]
        src = " · ".join(f'<a href="{e(x["url"])}">{e(x.get("outlet") or "source")}</a>' for x in srcs)
        parts.append(f"""<div class="hero">
<div><p class="kick">{numbers(lead)}</p>
<h1><a href="#s-{lead['id']}">{e(title_of(db, lead))}</a></h1>
<p class="lede">{e(lead.get('dek') or lead.get('summary') or status_label(lead, cfg))}</p>
<p class="src">{e(status_label(lead, cfg))}{' · ' + src if src else ''}</p></div>
<a class="img" href="#s-{lead['id']}">{chart(lmoves, 640, 400)}</a></div>""")
    else:
        parts.append('<div class="hero"><div><h1>No stories yet today</h1><p class="lede">Nothing has moved enough '
                     'money to make the front page. The next hourly run will check again.</p></div></div>')

    if hunted:
        parts.append(f'<section class="blk" id="front"><div class="sh"><h2>Front page</h2><span class="d">ranked by importance</span></div>'
                     f'<div class="covers">{"".join(cover(db, s, cfg) for s in hunted[:12])}</div></section>')

    lists = []
    lists.append('<section><div class="sh"><h2>All stories today</h2></div>' +
                 ("".join(rank_row(db, s, i + 1, cfg) for i, s in enumerate(todays[:40])) or '<p class="empty">None yet.</p>') +
                 '</section>')
    lists.append('<section id="unreported"><div class="sh"><h2>Nothing reported yet</h2><span class="d">this week</span></div>' +
                 ("".join(rank_row(db, s, i + 1, cfg) for i, s in enumerate(unreported[:20])) or
                  '<p class="empty">Every story the hunt looked into this week had a public explanation.</p>') + '</section>')
    parts.append(f'<section class="blk"><div class="lists">{"".join(lists)}</div></section>')

    if earlier:
        rows, last = [], None
        for s in earlier[:60]:
            if s["day"] != last:
                last = s["day"]
                rows.append(f'<p class="day">{datetime.strptime(last, "%Y-%m-%d").strftime("%A, %B %-d")}</p>')
                n = 0
            n += 1
            rows.append(rank_row(db, s, n, cfg))
        parts.append(f'<section class="blk" id="week"><div class="sh"><h2>Earlier this week</h2></div>{"".join(rows)}</section>')

    tm = sum(s["money_usd"] or 0 for s in todays)
    parts.append(f"""<section class="cta" id="method"><div>
<h2 class="big">How stories are ranked</h2>
<p>Every hour Newshound compares the prices of about 55,000 Polymarket and Kalshi markets with an hour earlier. Markets that move 5 points or more with real money behind them become stories, grouped by the event they are about.</p>
<p>A story's importance is the money that repriced on it: for each market, the points it moved times the dollars traded during the move, weighted up when trading ran far above its usual pace. The index is on a log scale, so every 10 points means ten times as much. Stories scoring {cfg.hunt_min_score:.0f} or more are investigated; the rest are listed with their numbers.</p>
<p>The ranking reflects what traders bet on. Politics, sports and economics carry the most money, and news without a market behind it doesn't appear.</p></div>
<div class="stats"><div><b>{len(todays)}</b><span>stories today</span></div><div><b>{money(tm)}</b><span>traded on them</span></div>
<div><b>{len(hunted) + (1 if lead and lead.get('headline') else 0)}</b><span>investigated</span></div><div><b>{len(unreported)}</b><span>not yet reported, this week</span></div></div>
</section>""")

    pages = "".join(story_page(db, s, cfg) for s in everything)
    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Newshound</title>
<style>{CSS}</style></head><body id="top">
<nav><a href="#top" class="brand">Newshound</a><a href="#front">Front page</a><a href="#unreported">Not yet reported</a><a href="#week">This week</a><a href="#method">Method</a>
<span class="n">{datetime.fromtimestamp(now, timezone.utc).astimezone(ET).strftime("%A, %B %-d · updated %-I:%M %p ET")}</span></nav>
<main id="home">{"".join(parts)}</main>
{pages}
</body></html>"""
