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
    out = [x for x in s.get("sources") or [] if x.get("verified") and x.get("relation") != "market_coverage"
           and not (x.get("background_only") and x.get("role", "context") == "context")]
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
.page p { max-width:680px; font-size:16px; } .page h2 { font-size:15px; font-weight:600; margin:28px 0 6px; }
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


def front(db, cfg, now: float, data: dict) -> str:
    b = base_path(cfg)
    today = et_day(now)
    todays = [s for s in data["stories"] if s["day"] == today]
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


def method_page(cfg, now: float) -> str:
    lo = cfg.hunt_min_score
    bands = "".join(f"<li>{e(label)}: {floor:.0f} and up</li>" for floor, label in cfg.shift_bands if floor > 0)
    body = f"""<main class="page"><h1>Method</h1>
<p>Newshound follows a scent. Prediction markets on Polymarket and Kalshi are where people put money on what happens next, and when expectations about something shift sharply, it usually means something happened. The shift is the scent. It is not the story.</p>
<p>Every hour the hound checks about 55,000 markets against an hour earlier. Sharp moves with real money behind them are grouped by what they are about, and each group is scored by how much it shifted expectations. Anything that scores high enough gets chased.</p>
<h2>The chase</h2>
<p>Claude works out what kind of development would produce the shift, then goes looking: news reports, official statements and press releases, court dockets and filings, government sites, posts from the people involved. It reads what it finds, checks when each piece was published, and writes up what happened.</p>
<p>Only what it catches is published. A story needs a source that came back from a real search, reported the development, and was published before expectations moved (or within a few minutes of it). Code checks this, not the writer. Wire services, established outlets and primary documents can explain a story. Aggregators, re-posters and partisan blogs can appear as context but never count as the report that explains it.</p>
<h2>On the trail</h2>
<p>When the hunt finds nothing, the lead goes on the trail as an open question, and the hound checks again after 1, 6 and 24 hours. If reporting turns up later, the lead becomes a story, and the page notes how long after the shift the first report came.</p>
<h2>How shifts are scored</h2>
<p>For each market, the points it moved are multiplied by the dollars traded during the move, and weighted up when trading ran far above its usual pace. A story's score adds this up across its markets on a log scale from 0 to 100, so every 10 points means ten times as much. Stories scoring {lo:.0f} or more are chased; smaller ones are noted as things the hound is watching.</p>
<ul>{bands}</ul>
<h2>What it misses</h2>
<p>The page reflects what people bet on. Politics, sports and economics carry the most money, and news with no market behind it never gives off a scent. Live games, weather, crypto prices and other markets that just track a public feed are ignored.</p>
<p>Images are each source article's own lead image, shown with its credit and linked to the article.</p>
</main>"""
    return shell(cfg, now, "Method", body)


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
    watching = [s for s in every if s["status"] == "pending" and s.get("topic") and (s["score"] or 0) < cfg.hunt_min_score]
    return {"every": every, "stories": stories, "leads": leads, "watching": watching}


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
        put(f"{day_path(day)}/index.html", day_page(cfg, now, day, d["stories"], d["leads"], d["watching"]))
    put("archive/index.html", archive_page(cfg, now, days))
    put("feed.xml", feed(cfg, now, data["stories"]))
    put("404.html", shell(cfg, now, "Not found", '<main class="page"><h1>Not found</h1>'
                          f'<p>That page isn’t here. Try the <a href="{base_path(cfg)}">front page</a>.</p></main>'))
    put(".nojekyll", "")
    return written
