"""Cost audit of the hunting stage, from the ledger (see residual/ledger.py).

    python -m residual --data rdata audit report [--since D] [--until D] [--out report.md]
    python -m residual --data rdata audit reconcile --since D --until D [--console-csv F]

`report` prices every request from audit/prices.json and answers: what each part of a hunt costs, what a
published story costs, how many searches produced a cited source, how the context grows turn by turn,
and how much is spent after a hunt has already found what it ends up citing.

`reconcile` compares the ledger with Anthropic's own records for the same UTC days: the Admin API's
usage and cost reports (needs ANTHROPIC_ADMIN_KEY) or a cost CSV exported from the Console. A gap over
5% means the ledger is missing requests (another program using the same key, failed requests, calls made
before the ledger existed) or the prices are out of date.
"""

from __future__ import annotations

import csv
import gzip
import json
import os
import statistics
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

from . import ledger as L

PRICES = Path(__file__).resolve().parent.parent / "audit" / "prices.json"
COMPONENTS = ("uncached_input", "cache_writes", "cache_reads", "output", "searches")


def load_prices(path: str | Path | None = None) -> dict:
    return json.loads(Path(path or PRICES).read_text())


def _rates(prices: dict, model: str | None, prompt_tokens: int) -> dict:
    m = prices["models"].get(model or "") or next(
        (v for k, v in prices["models"].items() if model and model.startswith(k)), None)
    if m is None:
        raise KeyError(f"no price for model {model!r}; add it to audit/prices.json")
    if m.get("long_prompt_over") and prompt_tokens > m["long_prompt_over"]:
        return m["long"]
    return m


def cost(row: dict, prices: dict) -> dict:
    """Dollars for one ledger line, by component."""
    u = row.get("usage") or {}
    if not u:
        return dict.fromkeys(COMPONENTS, 0.0)
    prompt = u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"]
    r = _rates(prices, row.get("model") or row.get("model_requested"), prompt)
    per = 1e-6
    return {
        "uncached_input": u["input_tokens"] * r["input"] * per,
        "cache_writes": (u["cache_write_5m_tokens"] * r["cache_write_5m"] + u["cache_write_1h_tokens"] * r["cache_write_1h"]) * per,
        "cache_reads": u["cache_read_input_tokens"] * r["cache_read"] * per,
        "output": u["output_tokens"] * r["output"] * per,
        "searches": u["web_searches"] * prices["web_search_per_1000"] / 1000,
    }


def _pct(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0
    k = (len(xs) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


# ---------- per hunt ----------

def hunts(rows: list[dict], prices: dict) -> list[dict]:
    """One record per hunt (run, variant, story, kind): its requests, cost and outcome."""
    by = defaultdict(lambda: {"calls": [], "outcome": None})
    for r in rows:
        if r["purpose"] not in ("hunt", "outcome"):
            continue
        key = (r["run"], r.get("variant"), r["story_id"], r["kind"])
        if r["purpose"] == "outcome":
            by[key]["outcome"] = r
        else:
            by[key]["calls"].append(r)
    out = []
    for (run, variant, sid, kind), h in by.items():
        calls = sorted(h["calls"], key=lambda r: r["turn"] if r["turn"] is not None else -1)
        if not calls:
            continue
        comp = defaultdict(float)
        for c in calls:
            for k, v in cost(c, prices).items():
                comp[k] += v
        o = h["outcome"] or {}
        out.append({
            "run": run, "variant": variant, "story_id": sid, "kind": kind, "calls": calls,
            "turns": len(calls), "cost": dict(comp), "total": sum(comp.values()),
            "searches": sum((c["usage"] or {}).get("web_searches", 0) for c in calls),
            "fetches": sum((c["usage"] or {}).get("web_fetches", 0) for c in calls),
            "errors": [c["error"] for c in calls if c.get("error")],
            "models": sorted({c.get("model") or "?" for c in calls}),
            "status": o.get("status"), "published": o.get("status") in ("reported", "unclear"),
            "cited": o.get("cited") or [],
        })
    return out


def _transcript(folder: Path, h: dict) -> dict | None:
    name = f"{h['run']}-{h['variant'] + '-' if h['variant'] else ''}{h['story_id']}-{h['kind']}.json.gz"
    hit = next(iter((folder / "transcripts").glob(f"*/{name}")), None)
    return json.loads(gzip.open(hit, "rt").read()) if hit else None


def search_yield(folder: Path, h: dict) -> dict:
    """From the transcript: how many searches returned a URL that the story went on to cite as a verified
    source, and the turn at which the last such URL first appeared."""
    t = _transcript(folder, h)
    if not t:
        return {"searches": None, "useful": None, "last_useful_turn": None}
    cited = {L_norm(c["url"]) for c in h["cited"] if c.get("verified")}
    searches = useful = 0
    last = None
    turn = -1
    for m in t["messages"]:
        if m.get("role") != "assistant":
            continue
        turn += 1
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "web_search_tool_result":
                searches += 1
                urls = {L_norm(r.get("url", "")) for r in b.get("content") or [] if isinstance(r, dict)}
                if urls & cited:
                    useful += 1
                    last = turn
            elif b.get("type") == "web_fetch_tool_result":
                c = b.get("content") or {}
                if isinstance(c, dict) and L_norm(c.get("url", "")) in cited:
                    last = turn
    return {"searches": searches, "useful": useful, "last_useful_turn": last}


def L_norm(u: str) -> str:
    from .verdict import norm_url
    return norm_url(u or "")


# ---------- the report ----------

def report(folder: str | Path, since=None, until=None, prices_path=None) -> str:
    folder = Path(folder)
    prices = load_prices(prices_path)
    rows = L.read(folder, since, until)
    if not rows:
        return "No ledger lines in that window."
    hs = hunts(rows, prices)
    all_cost = defaultdict(float)
    by_purpose = defaultdict(float)
    for r in rows:
        c = cost(r, prices)
        for k, v in c.items():
            all_cost[k] += v
        by_purpose[r["purpose"]] += sum(c.values())
    total = sum(all_cost.values()) or 1e-9
    lines = [f"# Hunting cost audit, {since or rows[0]['ts']} to {until or 'now'}",
             f"Prices: {prices['source']} (checked {prices['checked']}).", "",
             f"{len(rows)} ledger lines, {len(hs)} hunts, {len({r['run'] for r in rows})} runs. Total ${total:,.2f}.", "",
             "## Where the dollars go", "", "| Component | $ | Share |", "|---|---|---|"]
    for k in COMPONENTS:
        lines.append(f"| {k.replace('_', ' ')} | {all_cost[k]:,.2f} | {all_cost[k] / total:.0%} |")
    lines += ["", "By stage: " + ", ".join(f"{k} ${v:,.2f}" for k, v in sorted(by_purpose.items(), key=lambda x: -x[1]))]

    # what the context is made of, weighted by what each request paid for its prompt
    part_cost = defaultdict(float)
    for r in rows:
        if r["purpose"] != "hunt" or not r.get("usage"):
            continue
        c = cost(r, prices)
        prompt_cost = c["uncached_input"] + c["cache_writes"] + c["cache_reads"]
        parts = r.get("context_tokens_by_part") or {}
        n = sum(parts.values()) or 1
        for k, v in parts.items():
            part_cost[k] += prompt_cost * v / n
    pc = sum(part_cost.values()) or 1e-9
    lines += ["", "## What the prompt dollars paid for", "",
              "Each request's input cost split by what its context held (token counter where available, else characters).", "",
              "| Part | $ | Share of input cost |", "|---|---|---|"]
    for k, v in sorted(part_cost.items(), key=lambda x: -x[1]):
        lines.append(f"| {k.replace('_', ' ')} | {v:,.2f} | {v / pc:.0%} |")

    totals = [h["total"] for h in hs]
    pub = [h for h in hs if h["published"]]
    lines += ["", "## Per hunt", "",
              f"Cost per hunt: p50 ${_pct(totals, .5):.3f}, p90 ${_pct(totals, .9):.3f}, max ${max(totals, default=0):.3f}.",
              f"Turns per hunt: p50 {_pct([h['turns'] for h in hs], .5):.0f}, p90 {_pct([h['turns'] for h in hs], .9):.0f}.",
              f"Published {len(pub)} of {len(hs)} hunts. Cost per published story (all hunt spend / published): "
              f"${sum(totals) / max(len(pub), 1):.3f}.",
              f"Spend on hunts that published nothing: ${sum(h['total'] for h in hs if not h['published']):,.2f} "
              f"({sum(h['total'] for h in hs if not h['published']) / max(sum(totals), 1e-9):.0%})."]
    top = sorted(hs, key=lambda h: -h["total"])
    k = max(1, len(top) // 10)
    lines.append(f"The costliest 10% of hunts ({k}) took {sum(h['total'] for h in top[:k]) / max(sum(totals), 1e-9):.0%} of hunt spend.")

    # searches that produced a cited source; spend after the last useful find
    ys = [(h, search_yield(folder, h)) for h in hs]
    known = [(h, y) for h, y in ys if y["searches"] is not None]
    if known:
        s_all = sum(y["searches"] for _, y in known)
        s_use = sum(y["useful"] for _, y in known)
        after = 0.0
        for h, y in known:
            if h["published"] and y["last_useful_turn"] is not None:
                after += sum(sum(cost(c, prices).values()) for c in h["calls"] if (c["turn"] or 0) > y["last_useful_turn"])
        lines += [f"Searches that returned a URL the story cited as verified: {s_use} of {s_all} "
                  f"({s_use / max(s_all, 1):.0%}); ${all_cost['searches'] / max(s_use, 1):.3f} of search fees per useful search.",
                  f"Spent after a published hunt had already seen everything it cited: ${after:,.2f}."]

    # context growth
    by_turn = defaultdict(list)
    for r in rows:
        if r["purpose"] == "hunt" and r.get("usage"):
            u = r["usage"]
            by_turn[r["turn"]].append(u["input_tokens"] + u["cache_creation_input_tokens"] + u["cache_read_input_tokens"])
    lines += ["", "## Context growth", "", "| Turn | Requests | Median prompt tokens | p90 |", "|---|---|---|---|"]
    for t in sorted(k for k in by_turn if k is not None):
        xs = by_turn[t]
        lines.append(f"| {t} | {len(xs)} | {statistics.median(xs):,.0f} | {_pct(xs, .9):,.0f} |")

    fb = [h for h in hs if any(m != c["model_requested"] for c in h["calls"] for m in [c.get("model")] if m)]
    err = [e for h in hs for e in h["errors"]]
    lines += ["", "## Checks", "",
              f"Hunts served at least partly by a fallback model: {len(fb)}.",
              f"Requests that failed: {len(err)}." + (f" First: {err[0][:160]}" if err else ""),
              f"Requests with an exact token count: {sum(1 for r in rows if r.get('context_tokens_exact'))} of "
              f"{sum(1 for r in rows if r['purpose'] == 'hunt')}."]
    if any(h["variant"] for h in hs):
        lines += ["", "## By variant", "", "| Variant | Hunts | Published | $ total | $ per published |", "|---|---|---|---|---|"]
        for v in sorted({h["variant"] for h in hs if h["variant"]}):
            g = [h for h in hs if h["variant"] == v]
            p = sum(h["published"] for h in g)
            lines.append(f"| {v} | {len(g)} | {p} | {sum(h['total'] for h in g):.2f} | {sum(h['total'] for h in g) / max(p, 1):.3f} |")
    return "\n".join(lines)


# ---------- reconciliation ----------

ADMIN = "https://api.anthropic.com/v1/organizations"


def _admin(path: str, params: list[tuple]) -> list[dict]:
    key = os.environ.get("ANTHROPIC_ADMIN_KEY")
    if not key:
        raise SystemExit("Set ANTHROPIC_ADMIN_KEY (an Admin API key from the Console) or pass --console-csv.")
    out, page = [], None
    while True:
        q = list(params) + ([("page", page)] if page else [])
        req = urllib.request.Request(f"{ADMIN}/{path}?{urllib.parse.urlencode(q)}",
                                     headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
        d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        out += d.get("data") or []
        if not d.get("has_more"):
            return out
        page = d.get("next_page")


def anthropic_records(since: str, until: str) -> dict:
    """Tokens and dollars per UTC day from the Admin API's usage and cost reports."""
    window = [("starting_at", f"{since}T00:00:00Z"), ("ending_at", f"{until}T23:59:59Z"), ("bucket_width", "1d")]
    days = defaultdict(lambda: defaultdict(float))
    for b in _admin("usage_report/messages", window + [("group_by[]", "model")]):
        day = (b.get("starting_at") or "")[:10]
        for r in b.get("results") or []:
            cc = r.get("cache_creation") or {}
            days[day]["input_tokens"] += r.get("uncached_input_tokens") or 0
            days[day]["cache_writes"] += (cc.get("ephemeral_5m_input_tokens") or 0) + (cc.get("ephemeral_1h_input_tokens") or 0)
            days[day]["cache_reads"] += r.get("cache_read_input_tokens") or 0
            days[day]["output_tokens"] += r.get("output_tokens") or 0
            days[day]["web_searches"] += (r.get("server_tool_use") or {}).get("web_search_requests") or 0
    for b in _admin("cost_report", window):
        day = (b.get("starting_at") or "")[:10]
        for r in b.get("results") or []:
            days[day]["usd"] += float(r.get("amount") or 0) / 100  # the report gives cents
    return days


def console_csv(path: str) -> dict:
    """Dollars per day from a cost CSV exported from the Console. Looks for a date column and a cost or
    amount column; tokens are not compared."""
    days = defaultdict(lambda: defaultdict(float))
    with open(path, newline="") as fh:
        rd = csv.DictReader(fh)
        cols = {c.lower(): c for c in rd.fieldnames or []}
        dcol = next((cols[c] for c in cols if "date" in c or c in ("day", "starting_at")), None)
        ccol = next((cols[c] for c in cols if "cost" in c or "amount" in c or "usd" in c), None)
        if not (dcol and ccol):
            raise SystemExit(f"Couldn't find date and cost columns in {path}: {rd.fieldnames}")
        for r in rd:
            v = (r[ccol] or "0").replace("$", "").replace(",", "")
            days[r[dcol][:10]]["usd"] += float(v or 0)
    return days


def reconcile(folder: str | Path, since: str, until: str, csv_path: str | None = None, prices_path=None) -> str:
    prices = load_prices(prices_path)
    rows = L.read(folder, since, until)
    mine = defaultdict(lambda: defaultdict(float))
    for r in rows:  # replays use the same key, so they count too
        u = r.get("usage") or {}
        d = mine[r.get("_day") or _day(r["ts"])]
        d["input_tokens"] += u.get("input_tokens", 0)
        d["cache_writes"] += u.get("cache_creation_input_tokens", 0)
        d["cache_reads"] += u.get("cache_read_input_tokens", 0)
        d["output_tokens"] += u.get("output_tokens", 0)
        d["web_searches"] += u.get("web_searches", 0)
        d["usd"] += sum(cost(r, prices).values())
    theirs = console_csv(csv_path) if csv_path else anthropic_records(since, until)
    keys = ["usd"] if csv_path else ["usd", "input_tokens", "cache_writes", "cache_reads", "output_tokens", "web_searches"]
    lines = [f"# Ledger against Anthropic's records, {since} to {until}", "",
             "| Day | Measure | Ledger | Anthropic | Gap |", "|---|---|---|---|---|"]
    worst = 0.0
    for day in sorted(set(mine) | set(theirs)):
        for k in keys:
            a, b = mine[day][k], theirs[day][k]
            gap = (a - b) / b if b else (0.0 if not a else 1.0)
            worst = max(worst, abs(gap))
            lines.append(f"| {day} | {k} | {a:,.2f} | {b:,.2f} | {gap:+.1%} |")
    lines += ["", f"Largest gap: {worst:.1%}. " + ("Within 5%." if worst <= 0.05 else
              "Over 5%: check for other programs using the same key, requests from before the ledger existed, "
              "and out-of-date prices in audit/prices.json.")]
    return "\n".join(lines)


def _day(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
