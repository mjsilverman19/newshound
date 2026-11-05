"""Briefs: one Markdown file per move, plus a single-page HTML report of everything flagged."""

from __future__ import annotations

import html
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from . import verdict as V

ET = ZoneInfo("America/New_York")
LABELS = {"explained": "Explained", "unexplained": "Unexplained", "unclear": "Unclear", None: "Not investigated yet"}


def t(ts: float | None, fmt: str = "%b %d, %H:%M %Z") -> str:
    return datetime.fromtimestamp(ts, timezone.utc).astimezone(ET).strftime(fmt) if ts else "—"


def gather(db, move: dict) -> dict:
    """Merge every investigation of a move into one view, re-checking sources against all searches."""
    invs = db.investigations(move["id"])
    searched = [s for i in invs for s in i["searched"] or []]
    seen, sources = set(), []
    for i in invs:
        for s in i["findings"].get("sources", []):
            key = V.norm_url(s.get("url", ""))
            if key and key not in seen:
                seen.add(key)
                sources.append({**s, "stage": i["stage"]})
    assessed = V.assess(sources, searched, move["move_start_ts"], 15)["sources"] if invs else []
    latest = invs[-1]["findings"] if invs else {}
    order = {"before": 0, "unknown": 1, "after": 2}
    assessed.sort(key=lambda s: (order[s["timing"]], s.get("published_ts") or 0))
    return {
        "investigations": invs,
        "sources": assessed,
        "assessment": latest.get("assessment"),
        "check_next": latest.get("check_next") or [],
        "searches": sum((i["usage"] or {}).get("web_searches", 0) for i in invs),
    }


def to_markdown(move: dict, g: dict) -> str:
    s = move.get("signals") or {}
    delta = (move["price_to"] - move["price_from"]) * 100
    out = [
        f"# {move['question']}",
        "",
        f"**{LABELS[move['verdict']]}** · {move['platform'].title()} · [open market]({move['url']})",
        "",
        f"- Move: {move['price_from'] * 100:.0f}¢ → {move['price_to'] * 100:.0f}¢ ({delta:+.0f} points)",
        f"- Started about {t(move['move_start_ts'])}; flagged {t(move['detected_ts'])}",
    ]
    if move.get("confirmed_ts"):
        out.append(f"- First report of the underlying news: {t(move['confirmed_ts'])}, "
                   f"**{move['lead_minutes']:.0f} minutes after the market moved**")
    if s.get("volume_multiple") is not None:
        out.append(f"- Volume: {s['volume_multiple']}× normal (${s.get('window_volume_usd', 0):,} traded)")
    if s.get("top_wallet_share") is not None:
        out.append(f"- Largest buyer: {s['top_wallet_share']:.0%} of buying in the move's direction")
    if s.get("fresh_wallets"):
        out.append(f"- New or nearly unused wallets among top buyers: {len(s['fresh_wallets'])} "
                   f"({', '.join(w['wallet'][:10] + '…' for w in s['fresh_wallets'])})")
    if s.get("related"):
        out.append("- Moved alongside: " + "; ".join(
            f"{r['question']} ({r['from'] * 100:.0f}¢ → {r['to'] * 100:.0f}¢)" for r in s["related"][:4]))
    if g["assessment"]:
        out += ["", "## Assessment", "", g["assessment"]]
    if g["sources"]:
        out += ["", "## Sources", ""]
        for src in g["sources"]:
            flags = [src["relation"].replace("_", " "), f"published {src['timing']} the move"
                     if src["timing"] != "unknown" else "publication time unknown"]
            if not src["verified"]:
                flags.append("NOT FOUND in search results, ignored")
            when = src.get("published_at") or "undated"
            out.append(f"- [{src.get('title') or src['url']}]({src['url']}) · {src.get('outlet') or ''} · "
                       f"{when} · {', '.join(flags)}  \n  {src.get('summary', '')}")
    if g["check_next"]:
        out += ["", "## Check next", ""] + [f"- {c}" for c in g["check_next"]]
    out += ["", f"_{len(g['investigations'])} investigation passes, {g['searches']} web searches._", ""]
    return "\n".join(out)


def write_brief(db, cfg, move: dict) -> Path:
    g = gather(db, move)
    d = Path(cfg.briefs_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"move-{move['id']:05d}.md"
    path.write_text(to_markdown(move, g))
    db.update_move(move["id"], brief={"assessment": g["assessment"], "check_next": g["check_next"],
                                      "path": str(path)})
    return path


# ---------- HTML report ----------

def _spark(series, w=120, h=32, color="#111"):
    if not series or len(series) < 2:
        return ""
    ps = [p for _, p in series]
    lo, hi = min(ps), max(ps)
    span = max(hi - lo, 0.01)
    pts = " ".join(f"{i / (len(ps) - 1) * w:.1f},{3 + (1 - (p - lo) / span) * (h - 6):.1f}" for i, p in enumerate(ps))
    return (f'<svg width="{w}" height="{h}" viewBox="0 0 {w} {h}" aria-hidden="true">'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.5"/></svg>')


CSS = """
body{margin:0;background:#fff;color:#111;font:15px/1.5 -apple-system,system-ui,'Segoe UI',sans-serif}
main{max-width:1000px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:18px;font-weight:600;margin:0}
.sub{color:#5f5f5f;font-size:13px}
.stats{display:flex;flex-wrap:wrap;gap:24px;margin:24px 0;padding:16px 0;border-top:1px solid #e8e8e8;border-bottom:1px solid #e8e8e8}
.stat b{display:block;font:500 22px ui-monospace,Menlo,monospace}
.stat span{color:#5f5f5f;font-size:13px}
details{border-bottom:1px solid #e8e8e8}
summary{display:flex;flex-wrap:wrap;gap:8px 20px;align-items:center;padding:16px 4px;cursor:pointer;list-style:none}
summary::-webkit-details-marker{display:none}
.when{font:13px ui-monospace,Menlo,monospace;width:110px;color:#5f5f5f}
.q{flex:1 1 300px;font-weight:500}
.mv{font:15px ui-monospace,Menlo,monospace;width:110px;text-align:right}
.v{font-size:12px;width:110px;text-align:right}
.v.unexplained{color:#c2410c;font-weight:600}.v.unclear{color:#8a5a00}.v.explained{color:#5f5f5f}
.body{padding:0 4px 20px 130px;font-size:14px}
.body li{margin:4px 0}
.muted{color:#5f5f5f}
a{color:#111}
@media(max-width:640px){.body{padding-left:4px}.when,.mv,.v{width:auto;text-align:left}}
"""


def report_html(db, limit: int = 300) -> str:
    moves = db.moves("status <> 'skipped'", limit=limit)
    inv = [m for m in moves if m["verdict"]]
    unexpl = [m for m in inv if m["verdict"] in ("unexplained", "unclear")]
    confirmed = [m for m in unexpl if m.get("confirmed_ts")]
    leads = sorted(m["lead_minutes"] for m in confirmed)
    median = leads[len(leads) // 2] if leads else None

    def stat(n, label):
        return f'<div class="stat"><b>{n}</b><span>{label}</span></div>'

    rows = []
    for m in moves:
        g = gather(db, m)
        e = html.escape
        delta = (m["price_to"] - m["price_from"]) * 100
        color = "#c2410c" if m["verdict"] in ("unexplained", "unclear") else "#999"
        s = m.get("signals") or {}
        facts = []
        if s.get("volume_multiple") is not None:
            facts.append(f"{s['volume_multiple']}× volume")
        if s.get("top_wallet_share") is not None:
            facts.append(f"top buyer {s['top_wallet_share']:.0%}")
        if s.get("fresh_wallets"):
            facts.append(f"{len(s['fresh_wallets'])} fresh wallets")
        body = [f'<p class="muted">{m["platform"].title()} · started {t(m["move_start_ts"])} · '
                f'{" · ".join(facts)} · <a href="{e(m["url"])}">open market</a></p>']
        if m.get("confirmed_ts"):
            body.append(f"<p><b>Confirmed later:</b> first report {t(m['confirmed_ts'])}, "
                        f"{m['lead_minutes']:.0f} minutes after the move.</p>")
        if g["assessment"]:
            body.append(f"<p>{e(g['assessment'])}</p>")
        if g["sources"]:
            items = []
            for src in g["sources"]:
                if not src["verified"]:
                    continue
                items.append(f'<li><a href="{e(src["url"])}">{e(src.get("title") or src["url"])}</a> '
                             f'<span class="muted">· {e(src.get("published_at") or "undated")} · '
                             f'{e(src["relation"].replace("_", " "))}, {src["timing"]} the move</span></li>')
            if items:
                body.append("<ul>" + "".join(items) + "</ul>")
        if g["check_next"]:
            body.append("<p class=\"muted\">Check next: " + e("; ".join(g["check_next"])) + "</p>")
        rows.append(
            f'<details><summary><span class="when">{t(m["detected_ts"], "%b %d %H:%M")}</span>'
            f'<span class="q">{e(m["question"])}</span>{_spark(m.get("series"), color=color)}'
            f'<span class="mv">{m["price_from"] * 100:.0f}¢ → {m["price_to"] * 100:.0f}¢</span>'
            f'<span class="v {m["verdict"] or ""}">{LABELS[m["verdict"]]}</span></summary>'
            f'<div class="body">{"".join(body)}</div></details>'
        )

    return f"""<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Newshound</title>
<style>{CSS}</style></head><body><main>
<h1>Newshound</h1><div class="sub">Prediction-market moves, and whether anything public explains them · updated {t(time.time())} · refreshes hourly</div>
<div class="stats">{stat(len(moves), "moves flagged")}{stat(len(inv), "investigated")}{stat(len(unexpl), "no public explanation")}{stat(len(confirmed), "later confirmed by reporting")}{stat(f"{median:.0f} min" if median is not None else "—", "median lead time")}</div>
{"".join(rows) or '<p class="muted">Nothing flagged yet.</p>'}
</main></body></html>"""
