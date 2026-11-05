"""The public site, built as static files for GitHub Pages.

The markets give the hound a scent; the page shows only what the hunt caught. Stories carry a
headline, dateline, image, body, why it matters and sources. Leads the hunt hasn't run down yet sit
"On the trail" as open questions. Market details (odds, dollars, points) appear nowhere except the
Method page, which explains how the scent is scored.

Layout:
  index.html                    front page
  YYYY/MM/DD/<slug>/index.html  one page per story; the slug is set once and never changes
  YYYY/MM/DD/index.html         that day's stories and leads
  archive/index.html            every day
  method/index.html             how it works
  feed.xml                      RSS
"""

from __future__ import annotations

import html
import shutil
import time
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from urllib.parse import urlparse

from . import verdict as V
from .stories import ET, et_day

e = html.escape

WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven",
         "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty"]
PARA = "\n\n"
ROLE_ORDER = ["original report", "announcement", "statement", "filing", "data", "denial", "confirmation",
              "follow-up", "analysis", "context"]


# ---------- small helpers ----------

def num(n: int, cap: bool = False) -> str:
    w = WORDS[n] if 0 <= n < len(WORDS) else str(n)
    return w[:1].upper() + w[1:] if cap else w


def names(items: list[str]) -> str:
    items = [x for x in items if x]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def et(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET)


def clock(ts: float) -> str:
    return et(ts).strftime("%-I:%M %p")


def long_day(day: str, year: bool = False) -> str:
    d = datetime.strptime(day, "%Y-%m-%d")
    return d.strftime("%A, %B %-d, %Y" if year else "%A, %B %-d")


def shift_label(score: float, cfg) -> str:
    for floor, label in cfg.shift_bands:
        if (score or 0) >= floor:
            return label
    return cfg.shift_bands[-1][1]


def base_path(cfg) -> str:
    p = urlparse(cfg.site_url).path or "/"
    return p if p.endswith("/") else p + "/"


def day_path(day: str) -> str:
    return day.replace("-", "/")


def story_path(s: dict) -> str:
    return f"{day_path(s['day'])}/{s['slug']}/"


# ---------- which stories go where ----------

def is_story(s: dict) -> bool:
    """Something the hunt caught: reported, or a lead a later recheck found reporting for."""
    if not s.get("headline") or not s.get("slug"):
        return False
    return s["status"] in ("reported", "unclear") or (s["status"] == "unreported" and bool(s.get("confirmed_ts")))


def is_lead(s: dict) -> bool:
    return s["status"] == "unreported" and not s.get("confirmed_ts") and bool(s.get("question"))


def shown_sources(s: dict) -> list[dict]:
    legacy = {"cause": "confirmation", "consequence": "follow-up", "background": "context"}
    out = [{**x, "role": x.get("role") or legacy.get(x.get("relation"), "context")}
           for x in s.get("sources") or [] if x.get("verified") and x.get("relation") != "market_coverage"]
    out = [x for x in out if not (x.get("background_only") and x["role"] == "context")]
    order = {r: i for i, r in enumerate(ROLE_ORDER)}
    return sorted(out, key=lambda x: (order.get(x.get("role"), 99), x.get("published_ts") or 9e12))


def outlet_of(x: dict) -> str:
    return x.get("outlet") or V.host(x.get("url", ""))


def reported_by(s: dict) -> str:
    seen = []
    for x in shown_sources(s):
        if x.get("role") == "context":
            continue
        o = outlet_of(x).split(" (via")[0]
        if o not in seen:
            seen.append(o)
    return f"Reported by {names(seen[:3])}" if seen else ""


# ---------- pieces ----------

def figure(s: dict, href: str | None, cls: str) -> str:
    img = s.get("image")
    if not img or not img.get("url"):
        return ""
    src = next((x for x in s.get("sources") or [] if V.norm_url(x.get("url", "")) == V.norm_url(img.get("source_url", ""))), None)
    via = img.get("outlet") or (outlet_of(src) if src else V.host(img.get("source_url", "")))
    credit = f"{e(img['credit'])}, via " if img.get("credit") else "Image via "
    cap = f'{e(img["caption"])} ' if img.get("caption") and cls == "big" else ""
    pic = (f'<img src="{e(img["url"])}" alt="{e(img.get("alt") or "")}" loading="lazy" referrerpolicy="no-referrer" '
           f'onerror="this.closest(\'figure\').remove()">')
    if href:
        pic = f'<a href="{e(href)}" tabindex="-1">{pic}</a>'
    return (f'<figure class="{cls}">{pic}<figcaption>{cap}{credit}'
            f'<a href="{e(img["source_url"])}">{e(via)}</a></figcaption></figure>')


def dek(s: dict, cls: str = "") -> str:
    if not s.get("dek"):
        return ""
    return f'<p class="{cls}">{e(s["dek"])}</p>' if cls else f"<p>{e(s['dek'])}</p>"


def dateline(s: dict) -> str:
    return f'<span>{e(s["dateline"])}</span>' if s.get("dateline") else ""


def kicker(s: dict, cfg) -> str:
    return f'<p class="kick">{dateline(s)}<b>{e(shift_label(s["score"], cfg))}</b></p>'


def card(s: dict, cfg, base: str) -> str:
    href = base + story_path(s)
    return (f'<article class="card">{figure(s, href, "thumb")}'
            f'<h3><a href="{e(href)}">{e(s["headline"])}</a></h3>'
            f'{dek(s, "dek")}'
            f'{kicker(s, cfg)}</article>')


def trail_item(s: dict, now: float) -> str:
    moves_ts = s.get("_start_ts") or s["created_ts"]
    today = et_day(now) == s["day"]
    when = f"around {clock(moves_ts)}" if today else f"{et(moves_ts).strftime('%A')} around {clock(moves_ts)}"
    first = s.get("trail_note") or f"Something shifted {when}."
    first = first if first.endswith((".", "?", "!")) else first + "."
    n = len(s.get("checked_domains") or [])
    found = f"{num(n, cap=True) if n <= 20 else n} sources checked with nothing yet" if n else "Nothing found yet"
    if s.get("next_recheck_ts") and s["next_recheck_ts"] <= now:
        rest = f"{found}, rechecking within the hour."
    elif s.get("next_recheck_ts"):
        nxt = s["next_recheck_ts"]
        at = clock(nxt) if et_day(nxt) == et_day(now) else et(nxt).strftime("%A at %-I:%M %p")
        rest = f"{found}, rechecking at {at}."
    else:
        rest = f"{found}, and no more rechecks are scheduled."
    return f'<li><p class="q">{e(s["question"])}</p><p class="d">{e(first)} {e(rest)}</p></li>'


def watching_line(topics: list[str]) -> str:
    uniq = []
    for t in topics:
        if t and t.lower() not in {u.lower() for u in uniq}:
            uniq.append(t)
    return f'<p class="watch">Also watching {e(names(uniq[:6]))}</p>' if uniq else ""


# ---------- pages ----------

CSS = """
:root { --fg:#141414; --dim:#6f6f6f; --soft:#3d3d3d; --line:#e4e2dd; --bg:#fbfaf8; --fill:#f1efea; --chip:#efede8; }
@media (prefers-color-scheme: dark) { :root { --fg:#ecebe8; --dim:#9a9893; --soft:#c9c7c2; --line:#2c2b29; --bg:#141413; --fill:#1e1d1b; --chip:#262523; } }
* { box-sizing:border-box; }
html { background:var(--bg); color:var(--fg); -webkit-text-size-adjust:100%; text-size-adjust:100%; }
body { margin:0 auto; max-width:1040px; padding:24px 24px 96px; font:15px/1.55 -apple-system, BlinkMacSystemFont, "Inter", "Helvetica Neue", Helvetica, Arial, sans-serif; background:var(--bg); }
a { color:inherit; text-decoration:none; } a:hover { text-decoration:underline; text-underline-offset:3px; }
nav { display:flex; gap:24px; align-items:center; flex-wrap:wrap; padding:6px 0 26px; }
nav .end { margin-left:auto; display:flex; gap:14px; align-items:center; }
nav a { color:var(--soft); } nav a.brand { color:var(--fg); font-weight:600; font-size:16px; margin-right:6px; }
nav .n { color:var(--soft); font-variant-numeric:tabular-nums; }
nav .rss { display:inline-flex; color:var(--dim); } nav .rss svg { width:15px; height:15px; }
.kick { margin:0 0 10px; color:var(--dim); display:flex; gap:14px; flex-wrap:wrap; font-size:14px; }
.kick b { font-weight:500; color:var(--fg); }
.hero { display:grid; grid-template-columns:minmax(0,1fr); gap:32px; padding:8px 0 36px; }
.hero.has-img { grid-template-columns:minmax(0,1fr) minmax(0,0.95fr); }
.hero h1 { font-size:34px; line-height:1.15; letter-spacing:-0.015em; font-weight:500; margin:0 0 16px; }
.hero .lede { font-size:17px; line-height:1.55; color:var(--soft); margin:0 0 14px; max-width:620px; }
.by, .note, .empty, .watch { color:var(--dim); font-size:14px; margin:0; }
figure { margin:0; }
figure img { display:block; width:100%; object-fit:cover; background:var(--fill); border-radius:3px; }
figure.lead img { aspect-ratio:16/10; } figure.thumb img { aspect-ratio:16/10; margin-bottom:14px; }
figure.big img { aspect-ratio:16/9; }
figcaption { color:var(--dim); font-size:13px; margin-top:8px; } figure.thumb figcaption { display:none; }
figcaption a { text-decoration:underline; text-decoration-color:var(--line); text-underline-offset:3px; }
.sh { display:flex; align-items:baseline; justify-content:space-between; gap:6px 16px; flex-wrap:wrap; border-bottom:1.5px solid var(--fg); padding-bottom:8px; margin:0 0 4px; }
.sh h2 { font-size:15px; font-weight:500; margin:0; } .sh span { color:var(--dim); font-size:14px; }
section.blk { margin-bottom:44px; }
.cards { display:grid; grid-template-columns:repeat(auto-fill, minmax(280px, 1fr)); }
.card { padding:16px 24px 18px 0; border-right:1px solid var(--line); }
.cards > :last-child { border-right:0; }
.cards > * + * { padding-left:16px; }
.card h3 { font-size:16px; line-height:1.35; font-weight:500; margin:0 0 6px; }
.card .dek { color:var(--soft); margin:0 0 10px; }
.cards .note { align-self:center; padding:16px 0 16px 16px; }
ul.trail { list-style:none; margin:0; padding:0; }
ul.trail li { padding:14px 0; border-bottom:1px solid var(--line); }
ul.trail .q { margin:0 0 2px; font-size:16px; } ul.trail .d { margin:0; color:var(--dim); font-size:14px; }
.watch { padding:14px 0; }
ul.list { list-style:none; margin:0; padding:0; }
ul.list li { padding:14px 0; border-bottom:1px solid var(--line); }
ul.list h3 { font-size:16px; font-weight:500; margin:0 0 4px; } ul.list p { margin:0; color:var(--soft); }
ul.list .kick { margin:6px 0 0; }
article.story { max-width:760px; }
.back { color:var(--dim); display:flex; gap:20px; margin:0 0 10px; font-size:14px; }
article.story h1 { font-size:32px; line-height:1.18; letter-spacing:-0.015em; font-weight:500; margin:0 0 8px; }
.date { color:var(--dim); margin:0 0 22px; font-size:14px; }
figure.big { margin:0 0 22px; }
.body p { font-size:17px; line-height:1.6; margin:0 0 16px; }
article.story h2 { font-size:15px; font-weight:600; margin:26px 0 6px; }
ul.srcs { list-style:none; margin:8px 0 0; padding:0; }
ul.srcs li { display:flex; gap:14px; align-items:baseline; padding:10px 0; border-bottom:1px solid var(--line); }
ul.srcs li:first-child { border-top:0; }
.role { flex:none; background:var(--chip); color:var(--soft); font-size:13px; padding:2px 9px; border-radius:999px; }
.page h1 { font-size:30px; font-weight:500; letter-spacing:-0.015em; margin:0 0 20px; }
.page p { max-width:680px; font-size:16px; }
.method ul { max-width:680px; } .method li { margin:4px 0; }
.abstract { max-width:680px; border-left:2px solid var(--line, #ddd); padding:2px 0 2px 18px; margin:0 0 8px; }
.abstract h2 { margin-top:0; } .eq { text-align:center; font-family:Georgia, 'Times New Roman', serif; font-size:17px; } .page h2 { font-size:15px; font-weight:600; margin:28px 0 6px; }
.days { list-style:none; padding:0; margin:0; } .days li { padding:12px 0; border-bottom:1px solid var(--line); display:flex; justify-content:space-between; gap:16px; flex-wrap:wrap; }
.days span { color:var(--dim); }
@media (max-width:760px) {
  body { padding:16px 16px 80px; }
  nav { gap:8px 18px; } nav a.brand { flex-basis:100%; } nav .end { margin-left:0; flex-basis:100%; justify-content:space-between; }
  nav .n { font-size:13px; color:var(--dim); }
  .hero.has-img { grid-template-columns:1fr; gap:18px; } .hero figure { order:-1; }
  .hero h1, article.story h1 { font-size:26px; }
  .card { border-right:0; padding:16px 0; border-bottom:1px solid var(--line); } .cards > * + * { padding-left:0; }
  .cards .note { padding:16px 0; }
  .body p { font-size:16px; }
}
"""

RSS_ICON = ('<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="3" cy="13" r="1.6" fill="currentColor"/>'
            '<path d="M2 7.5a6.5 6.5 0 0 1 6.5 6.5M2 2.5A11.5 11.5 0 0 1 13.5 14" fill="none" stroke="currentColor" '
            'stroke-width="1.7" stroke-linecap="round"/></svg>')


def shell(cfg, now: float, title: str, body: str, desc: str = "") -> str:
    b = base_path(cfg)
    full = cfg.site_title if title == cfg.site_title else f"{title}, {cfg.site_title}"
    stamp = et(now).strftime("%A, %B %-d, %-I:%M %p ET")
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(full)}</title>
<meta name="description" content="{e(desc or 'The day’s news, found by following where expectations shifted.')}">
<link rel="alternate" type="application/rss+xml" title="{e(cfg.site_title)}" href="{b}feed.xml">
<style>{CSS}</style></head>
<body>
<nav><a class="brand" href="{b}">{e(cfg.site_title)}</a><a href="{b}">Front page</a><a href="{b}#trail">On the trail</a>
<a href="{b}archive/">Archive</a><a href="{b}method/">Method</a>
<span class="end"><span class="n">{stamp}</span><a class="rss" href="{b}feed.xml" title="RSS feed" aria-label="RSS feed">{RSS_ICON}</a></span></nav>
{body}
</body></html>
"""


def ranked(stories: list[dict], cfg) -> list[dict]:
    """Stories with enough money behind them first, each group by score, so a thin market can't lead."""
    floor = cfg.lead_min_money_usd
    day = cfg.mode == "day"  # scores from the hourly detector are on another scale: rank them after
    return sorted(stories, key=lambda s: ((s.get("money_usd") or 0) >= floor, not day or bool(s.get("scent_data")),
                                          s["score"] or 0), reverse=True)


def front(db, cfg, now: float, data: dict) -> str:
    b = base_path(cfg)
    today = et_day(now)
    todays = ranked([s for s in data["stories"] if s["day"] == today], cfg)
    parts = []
    if todays:
        lead, also = todays[0], todays[1:]
        fig = figure(lead, b + story_path(lead), "lead")
        by = reported_by(lead)
        parts.append(f"""<section class="hero{' has-img' if fig else ''}"><div>{kicker(lead, cfg)}
<h1><a href="{b}{story_path(lead)}">{e(lead['headline'])}</a></h1>
{f'<p class="lede">{e(lead["dek"])}</p>' if lead.get('dek') else ''}
{f'<p class="by">{e(by)}</p>' if by else ''}</div>{fig}</section>""")
        cards = "".join(card(s, cfg, b) for s in also[:9])
        if len(also) < 2:
            n = len(todays)
            note = ("Only one story cleared the hunt today. Quiet days stay short." if n == 1 else
                    f"Only {num(n)} stories cleared the hunt today. Quiet days stay short.")
            cards += f'<p class="note">{note}</p>'
        parts.append(f'<section class="blk" id="today"><div class="sh"><h2>Also today</h2>'
                     f'<span>by how much it shifted expectations</span></div><div class="cards">{cards}</div></section>')
    else:
        parts.append('<section class="hero"><div><h1>Nothing has cleared the hunt yet today</h1>'
                     '<p class="lede">The hound picks up a new scent every hour. When it catches something, '
                     'it will lead this page.</p></div></section>')

    leads = data["leads"]
    trail = "".join(trail_item(s, now) for s in leads[:12])
    watch = watching_line([s["topic"] for s in data["watching"] if s["day"] == today])
    parts.append(f'<section class="blk" id="trail"><div class="sh"><h2>On the trail</h2>'
                 f'<span>leads the hound is still chasing</span></div>'
                 f'{f"<ul class=trail>{trail}</ul>" if trail else "<p class=watch>No open leads right now.</p>"}'
                 f'{watch}</section>')

    earlier = [s for s in data["stories"] if s["day"] != today][:10]
    if earlier:
        rows = "".join(
            f'<li><h3><a href="{b}{story_path(s)}">{e(s["headline"])}</a></h3>'
            f'{dek(s)}<p class="kick"><span>{e(et(s["created_ts"]).strftime("%A"))}</span>{dateline(s)}'
            f'<b>{e(shift_label(s["score"], cfg))}</b></p></li>' for s in earlier)
        parts.append(f'<section class="blk"><div class="sh"><h2>Earlier this week</h2>'
                     f'<span><a href="{b}archive/">Archive</a></span></div><ul class="list">{rows}</ul></section>')
    return shell(cfg, now, cfg.site_title, f'<main>{"".join(parts)}</main>')


def story_page(cfg, now: float, s: dict) -> str:
    b = base_path(cfg)
    first = s.get("hunted_ts") or s["created_ts"]
    upd = s.get("updated_ts") or first
    date = f'{et(first).strftime("%B %-d, %Y")}, updated {clock(upd)} ET'
    notes = []
    if s["status"] == "unreported" and s.get("lead_minutes") is not None:
        m = int(s["lead_minutes"])
        gap = f"{m} minutes" if m < 120 else f"{round(m / 60)} hours"
        notes.append(f"The hound picked up this scent before anything was published. The first report came {gap} later.")
    elif s["status"] == "unclear":
        notes.append("When this was first reported is unclear.")
    srcs = []
    for x in shown_sources(s):
        bits = [outlet_of(x)]
        ts, has_time = V.parse_published(x.get("published_at"))
        if ts and has_time:
            bits.append(clock(ts) if et_day(ts) == s["day"] else et(ts).strftime("%b %-d, %-I:%M %p"))
        elif ts and (x.get("published_at") or "")[:10] != s["day"]:
            bits.append(datetime.fromtimestamp(ts, timezone.utc).strftime("%b %-d"))
        if x.get("note"):
            bits.append(x["note"])
        srcs.append(f'<li><span class="role">{e((x.get("role") or "context").capitalize())}</span>'
                    f'<a href="{e(x["url"])}" title="{e(x.get("title") or "")}">{e(", ".join(bits))}</a></li>')
    body = f"""<main><article class="story">
<p class="back"><a href="{b}">&larr; Front page</a>{f'<span>{e(s["dateline"])}</span>' if s.get('dateline') else ''}</p>
<h1>{e(s['headline'])}</h1>
<p class="date">{e(date)}</p>
{figure(s, None, "big")}
<div class="body">{''.join(f'<p>{e(p)}</p>' for p in (s.get('summary') or '').split(PARA) if p.strip())}
{''.join(f'<p class="note">{e(n)}</p>' for n in notes)}</div>
{f'<h2>Why it matters</h2><div class="body"><p>{e(s["why_it_matters"])}</p></div>' if s.get('why_it_matters') else ''}
{f'<h2>Sources</h2><ul class="srcs">{"".join(srcs)}</ul>' if srcs else ''}
</article></main>"""
    return shell(cfg, now, s["headline"], body, s.get("dek") or "")


def day_page(cfg, now: float, day: str, stories: list[dict], leads: list[dict], watching: list[dict]) -> str:
    b = base_path(cfg)
    rows = "".join(
        f'<li><h3><a href="{b}{story_path(s)}">{e(s["headline"])}</a></h3>'
        f'{dek(s)}{kicker(s, cfg)}</li>' for s in stories)
    trail = "".join(f'<li><p class="q">{e(s["question"])}</p></li>' for s in leads)
    parts = [f'<h1>{e(long_day(day, True))}</h1>']
    parts.append(f'<section class="blk"><div class="sh"><h2>Stories</h2></div>'
                 f'{f"<ul class=list>{rows}</ul>" if rows else "<p class=watch>Nothing cleared the hunt this day.</p>"}</section>')
    if trail or watching:
        parts.append(f'<section class="blk"><div class="sh"><h2>Leads</h2><span>open questions from this day</span></div>'
                     f'{f"<ul class=trail>{trail}</ul>" if trail else ""}{watching_line([s["topic"] for s in watching])}</section>')
    return shell(cfg, now, long_day(day, True), f'<main class="page">{"".join(parts)}</main>')


def archive_page(cfg, now: float, days: dict) -> str:
    b = base_path(cfg)
    rows = []
    for day in sorted(days, reverse=True):
        n, m = len(days[day]["stories"]), len(days[day]["leads"])
        tally = ", ".join(x for x in (f"{n} {'story' if n == 1 else 'stories'}" if n else "",
                                      f"{m} {'lead' if m == 1 else 'leads'}" if m else "") if x) or "quiet"
        rows.append(f'<li><a href="{b}{day_path(day)}/">{e(long_day(day, True))}</a><span>{tally}</span></li>')
    body = f'<main class="page"><h1>Archive</h1><ul class="days">{"".join(rows) or "<li>Nothing yet.</li>"}</ul></main>'
    return shell(cfg, now, "Archive", body)


REPO_URL = "https://github.com/mjsilverman19/newshound"


def method_page(cfg, now: float) -> str:
    lo = cfg.hunt_min_score
    pts = f"{cfg.move_threshold * 100:.0f}"
    floor_usd = f"${cfg.min_window_volume_usd:,.0f}"
    hours = [f"{h:g}" for h in cfg.recheck_after_hours]
    recheck = ", ".join(hours[:-1]) + f" and {hours[-1]}" if len(hours) > 1 else hours[0]
    bands = "".join(f"<li>{e(label)}: S &ge; {floor:.0f}</li>" for floor, label in cfg.shift_bands if floor > 0)
    body = f"""<main class="page method"><h1>Method</h1>
<section class="abstract"><h2>Abstract</h2>
<p>Newshound is an automated news service that uses prediction markets as a detector for news. Each hour it compares the prices of roughly 55,000 Polymarket and Kalshi markets with their prices an hour earlier, flags markets that moved at least {pts} percentage points with at least {floor_usd} traded during the move, groups flagged markets into stories, and scores each story by the money-weighted size of the shift on a logarithmic scale from 0 to 100. Stories scoring {lo:.0f} or more are investigated by a language model (Claude, made by Anthropic) using web search and page retrieval. The model proposes an account of what happened and the sources for it; deterministic code, not the model, decides whether the account is published. Publication requires a source that the model's own searches returned, that is not on a list of aggregators and partisan sites, and that was published no later than {cfg.reaction_slack_minutes} minutes after the shift began. Stories without such a source are kept as open questions and searched again after {recheck} hours. Stories are written and published without human review. Market prices are used only to find stories and never appear in them.</p>
</section>

<h2>1. Signal</h2>
<p>Markets are eligible if they have at least ${cfg.min_liquidity_usd:,.0f} of order-book liquidity (Polymarket) or {cfg.min_open_interest:,.0f} contracts of open interest (Kalshi), do not resolve within {cfg.skip_if_ends_within_hours:g} hours, and are not in categories that track a public feed or a live event, such as weather, crypto and asset prices, and games in progress. Markets about the economy (interest rates, recessions, inflation, and stock-index or oil levels) are the exception: they are kept when they resolve at least {cfg.macro_min_days_to_end:g} days out, because their prices move on news rather than tracking a live feed. A market is flagged when its price, read as a probability, differs by at least {pts} points from the previous hourly reading (for Polymarket, also from the platform's own one-hour change). The start of the shift is estimated as the last time the price sat at its earlier level before covering a quarter of the move, using the market's recent price and trade history where available. Flagged moves with less than {floor_usd} traded since that start are discarded. A market is not flagged again for {cfg.cooldown_hours:g} hours unless it moves a further {pts} points.</p>

<h2>2. Scoring</h2>
<p>For each flagged market <i>m</i>, let &Delta;<i>p</i><sub>m</sub> be the absolute price change (0 to 1), <i>V</i><sub>m</sub> the dollars traded since the shift began, and <i>r</i><sub>m</sub> the ratio of that trading to the market's usual pace over the same length of time (from its one-week volume on Polymarket and its 24-hour volume on Kalshi). The intensity of the move and the score of a story <i>s</i> are</p>
<p class="eq"><i>I</i><sub>m</sub> = &Delta;<i>p</i><sub>m</sub> &middot; <i>V</i><sub>m</sub> &middot; &radic;min(max(<i>r</i><sub>m</sub>, 1), 100)</p>
<p class="eq"><i>S</i> = min(100, 10 &middot; log<sub>10</sub>(1 + &Sigma;<sub>m&isin;s</sub> <i>I</i><sub>m</sub>))</p>
<p>Each 10 points of <i>S</i> is a tenfold increase in money-weighted repricing. For example, a 17-point move on $20,000 of trading at ten times the usual pace scores about 40. Stories scoring {lo:.0f} or more are investigated; smaller ones are listed under a short topic name. Published stories carry a label for the size of the shift:</p>
<ul>{bands}</ul>

<h2>3. Stories</h2>
<p>A flagged market joins a story already holding that market, or another market from the same event on the same platform, on the same day (US Eastern time). Otherwise it starts a new story. Stories on the same day that share a distinctive name (a person, organisation or place, ignoring words common to many stories) are then merged, so the same news told through different markets appears once. During investigation the model may also merge stories it finds to be the same news; merged stories are rescored.</p>
<p>On the front page, stories with at least ${cfg.lead_min_money_usd:,.0f} traded across their markets come first, each group ordered by score. A thinly traded market can score highly on a small, sudden move, so it can be published but cannot lead the page.</p>

<h2>4. Investigation</h2>
<p>The model (<code>{e(cfg.hunt_model)}</code>) receives the shift: which markets moved, in which direction, when, how much was traded, and how each market resolves. It is instructed to infer what kind of development would produce the shift and to search for it, preferring primary sources (statements, filings, dockets, official pages, posts by the people involved) and wire services and established outlets. It may run up to {cfg.hunt_max_searches} web searches and {cfg.hunt_max_fetches} page retrievals per story ({cfg.recheck_max_searches} searches on a recheck). It records each source's publication time as shown on the page, or none if no time is shown, and returns a headline, summary, dateline, sources with their roles, and a lead image taken from a source's own page metadata. Topic names for smaller stories are written by <code>{e(cfg.label_model)}</code>.</p>

<h2>5. Verification and status</h2>
<p>For each source the model marks as the cause of a shift, code checks that (a) its URL was returned by the model's own search or retrieval tools in that run, (b) its site is not one of {len(cfg.background_only_domains)} listed aggregators, re-posters and partisan blogs, which may appear only as context, and (c) its publication time is no later than {cfg.reaction_slack_minutes} minutes after the shift began. A date with no time counts as before the shift only if it is a full day earlier. A story is <b>reported</b> if some source meets all three conditions, and <b>timing unclear</b> if the only qualifying sources lack a usable publication time. Both are published. Otherwise the story is <b>on the trail</b>: it is shown as an open question, written so as not to speculate about a cause, and searched again after {recheck} hours. If a later search finds a qualifying report published after the shift, the story is published with the delay between the shift and the first report.</p>

<h2>6. Limitations</h2>
<ul>
<li><b>Coverage.</b> Only news that people bet on can be detected. Politics, sports and economics are overrepresented; news with no market behind it is invisible.</li>
<li><b>Surprise, not importance.</b> The score measures how far expectations moved in the markets that exist, weighted by the money behind the move. Important news that was widely expected moves prices little and scores low; a surprise in a heavily traded market scores high whatever its wider significance.</li>
<li><b>Timing.</b> Prices are compared an hour apart. Where a market's trade history is sparse, the start of a shift, and therefore the delay before the first report, is accurate only to about an hour.</li>
<li><b>Publication times</b> are read from the pages by the model and can be wrong. Each story lists its sources with their times so readers can check.</li>
<li><b>Model error.</b> The model can misread a source, overstate what it says, or attach a real source to the wrong development. The checks in section 5 limit invented sources and late reports but do not verify that a source says what the summary says.</li>
<li><b>No human review.</b> No person reads stories before they are published.</li>
<li><b>Source lists</b> of aggregators and partisan sites are maintained by hand and are incomplete.</li>
</ul>

<h2>7. Reproducibility and corrections</h2>
<p>The code, including every threshold above, is public at <a href="{REPO_URL}">{e(REPO_URL.removeprefix("https://"))}</a>; the numbers on this page are read from its configuration when the site is built. To report an error in a story, open an issue there with the story's link.</p>
</main>"""
    if cfg.mode == "day":
        body = _day_method(cfg, body, recheck, bands)
    return shell(cfg, now, "Method", body)


def _day_method(cfg, body: str, recheck: str, bands: str) -> str:
    """The Method page for day mode: sections 1 to 3 and the timing rule in section 5 differ."""
    lo, mv, usd = cfg.day_hunt_min_score, f"{cfg.day_min_change * 100:.0f}", f"${cfg.day_min_usd:,.0f}"
    look = f"{cfg.day_source_lookback_hours:g}"
    top = f"""<section class="abstract"><h2>Abstract</h2>
<p>Newshound is an automated news service that ranks the day's news by how much it changed expectations. Each hour it compares the prices of tens of thousands of Polymarket and Kalshi markets with their prices 24 hours earlier, weights each market's change by how much money normally trades in it, groups markets about the same subject into topics, and scores each topic on a logarithmic scale from 0 to 100. Topics scoring {lo:.0f} or more are investigated by a language model (Claude, made by Anthropic) using web search and page retrieval, starting from a description of the whole topic: which markets moved and which way, how strong the signal is, and when during the day it moved. The model proposes an account of what happened and the sources for it; deterministic code, not the model, decides whether the account is published. Publication requires a source that the model's own searches returned, that is not on a list of aggregators and partisan sites, and that is from the day of the shift. Topics without such a source are kept as open questions and searched again after {recheck} hours. Stories are written and published without human review. Market prices are used only to find and rank stories and never appear in them.</p>
</section>

<h2>1. Signal</h2>
<p>A market counts toward the day's news if its price, read as a probability, has moved at least {mv} points over the past 24 hours with at least {usd} traded in that time, and it does not resolve within {cfg.day_min_hours_to_end:g} hours. Markets that track a public feed or a live event (weather, crypto and asset prices, games in progress) are left out, except markets about the economy (interest rates, recessions, inflation, and stock-index or oil levels) that resolve at least {cfg.macro_min_days_to_end:g} days out. Sports and entertainment markets are set aside as soft news, and markets settling a finished vote count are left out.</p>

<h2>2. Scoring</h2>
<p>For each market <i>m</i>, let &Delta;<i>p</i><sub>m</sub> be the absolute change in its price over 24 hours (0 to 1) and <i>N</i><sub>m</sub> its normal daily trading in dollars: a week's trading divided by seven on Polymarket, and the last 24 hours' trading on Kalshi, which reports no weekly figure. The day score of a market and the score of a topic <i>t</i> are</p>
<p class="eq"><i>D</i><sub>m</sub> = &Delta;<i>p</i><sub>m</sub> &middot; <i>N</i><sub>m</sub></p>
<p class="eq"><i>S</i> = min(100, 10 &middot; log<sub>10</sub>(1 + &Sigma;<sub>m&isin;t</sub> <i>D</i><sub>m</sub>))</p>
<p>The net change counts, not how much a price swung in between, so a rumour that rose and fell back adds little. Weighting by normal trading means a move in a market where a lot of money usually trades counts for more than the same move in a thin one. Each 10 points of <i>S</i> is a tenfold increase. Topics scoring {lo:.0f} or more are investigated; topics between {cfg.day_story_min_score:.0f} and {lo:.0f} are listed under a short topic name. Each topic also gets a signal strength: <b>strong</b> with a score of at least {cfg.day_strong_score:.0f} and at least ${cfg.day_strong_usd:,.0f} of normal daily trading, <b>weak</b> with under ${cfg.lead_min_money_usd:,.0f}, or a single small market, and <b>moderate</b> otherwise. A weak topic gets at most {cfg.weak_max_searches} searches. Published stories carry a label for the size of the shift:</p>
<ul>{bands}</ul>

<h2>3. Stories</h2>
<p>Markets in the same event form one group. Groups join a topic when they share two specific tags (tags on at most {cfg.day_tag_rarity:.0%} of the day's moving groups), or one specific tag and a name, or a name in both group titles; a name counts only if at most {cfg.day_name_max_events} groups share it, so "Hegseth" ties groups together and "Israel" does not. Each topic becomes a story, or updates the story it already is when they share a group; a topic touching several of the day's stories merges them. During investigation the model may also merge stories it finds to have the same cause, such as a market rout that moved both a company's listing plans and interest-rate expectations; merged stories are rescored. A published story is investigated again only if its score rises by {cfg.rehunt_gain:.0f} or more.</p>
<p>On the front page, stories with at least ${cfg.lead_min_money_usd:,.0f} of normal daily trading across their markets come first, each group ordered by score, so a thin market cannot lead the page.</p>
"""
    i, j = body.index('<section class="abstract">'), body.index("<h2>4. Investigation</h2>")
    body = body[:i] + top + "\n" + body[j:]
    old_timing = (f"and (c) its publication time is no later than {cfg.reaction_slack_minutes} minutes after the shift began. "
                  "A date with no time counts as before the shift only if it is a full day earlier.")
    new_timing = (f"and (c) it was published no more than {look} hours before the 24-hour window began; an older report "
                  "is treated as background, because it cannot be what changed expectations that day.")
    body = body.replace(old_timing, new_timing)
    body = body.replace("If a later search finds a qualifying report published after the shift, the story is published "
                        "with the delay between the shift and the first report.",
                        "If a later search finds a qualifying report, the story is published.")
    body = body.replace("receives the shift: which markets moved, in which direction, when, how much was traded, and how each market resolves.",
                        "receives the topic: which markets moved over the day and in which direction, how much money normally trades in them, how strong the signal is, when the lead market moved, and how the biggest market resolves.")
    return body


def feed(cfg, now: float, stories: list[dict]) -> str:
    site = cfg.site_url if cfg.site_url.endswith("/") else cfg.site_url + "/"
    items = []
    for s in sorted(stories, key=lambda x: x.get("hunted_ts") or x["created_ts"], reverse=True)[:40]:
        link = site + story_path(s)
        ts = s.get("hunted_ts") or s["created_ts"]
        items.append(f"<item><title>{e(s['headline'])}</title><link>{e(link)}</link><guid isPermaLink=\"true\">{e(link)}</guid>"
                     f"<pubDate>{format_datetime(datetime.fromtimestamp(ts, timezone.utc))}</pubDate>"
                     f"<description>{e(s.get('dek') or s.get('summary') or '')}</description></item>")
    return (f'<?xml version="1.0" encoding="utf-8"?>\n<rss version="2.0"><channel><title>{e(cfg.site_title)}</title>'
            f"<link>{e(site)}</link><description>The day’s news, found by following where expectations shifted.</description>"
            f"<lastBuildDate>{format_datetime(datetime.fromtimestamp(now, timezone.utc))}</lastBuildDate>{''.join(items)}</channel></rss>\n")


# ---------- build ----------

def collect(db, cfg, now: float) -> dict:
    week_ago = et_day(now - 6 * 86400)
    every = db.stories("1=1", order="day DESC, score DESC", limit=100000)
    for s in every:
        moves = db.story_moves(s["id"])
        s["_start_ts"] = min((m["move_start_ts"] for m in moves), default=s["created_ts"])
    stories = [s for s in every if is_story(s)]
    leads = sorted([s for s in every if is_lead(s) and s["day"] >= week_ago],
                   key=lambda s: (s["day"], s["score"]), reverse=True)
    watching = [s for s in every if s["status"] == "pending" and s.get("topic")
                and (s["score"] or 0) < (cfg.day_hunt_min_score if s.get("scent_data") else cfg.hunt_min_score)]
    return {"every": every, "stories": stories, "leads": leads, "watching": watching}


def redirect_page(target: str) -> str:
    t = e(target)
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Moved</title>'
            f'<link rel="canonical" href="{t}"><meta http-equiv="refresh" content="0; url={t}">'
            f'<meta name="robots" content="noindex"></head><body><p>This story was combined with another. '
            f'<a href="{t}">Read it here</a>.</p></body></html>')


def build(db, cfg, out: str | Path, now: float | None = None, clean: bool = False) -> list[str]:
    now = now or time.time()
    out = Path(out)
    if clean and out.exists():
        for p in out.iterdir():
            if p.name in (".git", "CNAME"):
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    out.mkdir(parents=True, exist_ok=True)
    data = collect(db, cfg, now)
    written = []

    def put(rel: str, text: str):
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        written.append(rel)

    put("index.html", front(db, cfg, now, data))
    put("method/index.html", method_page(cfg, now))
    days: dict[str, dict] = {}
    for s in data["every"]:
        d = days.setdefault(s["day"], {"stories": [], "leads": [], "watching": []})
        if is_story(s):
            d["stories"].append(s)
        elif s["status"] == "unreported" and s.get("question") and not s.get("confirmed_ts"):
            d["leads"].append(s)
        elif s in data["watching"]:
            d["watching"].append(s)
    for s in data["stories"]:
        put(f"{story_path(s)}index.html", story_page(cfg, now, s))
    for day, d in days.items():
        put(f"{day_path(day)}/index.html", day_page(cfg, now, day, ranked(d["stories"], cfg), d["leads"], d["watching"]))
    for s in data["every"]:  # URLs of published stories that were merged into another one
        target = base_path(cfg) + (story_path(s) if s in data["stories"] else f"{day_path(s['day'])}/")
        for alias in s.get("aliases") or []:
            day, _, slug = alias.partition("/")
            put(f"{day_path(day)}/{slug}/index.html", redirect_page(target))
    put("archive/index.html", archive_page(cfg, now, days))
    put("feed.xml", feed(cfg, now, data["stories"]))
    put("404.html", shell(cfg, now, "Not found", '<main class="page"><h1>Not found</h1>'
                          f'<p>That page isn’t here. Try the <a href="{base_path(cfg)}">front page</a>.</p></main>'))
    put(".nojekyll", "")
    return written
