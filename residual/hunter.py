"""Scheduled mode on the Claude API: the hunt, run by this code instead of a Claude session.

`python -m residual --data DIR cycle` does what AGENT.md asks a scheduled Claude session to do (scan,
name the small stories, hunt the queued ones, record what was found) but calls the Claude API with an
ANTHROPIC_API_KEY. The site build and the git pushes stay in the workflow that runs it
(.github/workflows/hourly.yml).

The rules for sourcing and writing are the same as in AGENT.md. One thing is stricter here: the list
of URLs a story's sources are checked against (`searched`) is collected from the web search and web
fetch results the API actually returned, not from what the model says it saw.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from . import scheduled, stories

log = logging.getLogger(__name__)

# ---------- tools ----------

SOURCE_SCHEMA = {
    "type": "object",
    "properties": {
        "url": {"type": "string"},
        "title": {"type": "string"},
        "outlet": {"type": "string"},
        "published_at": {
            "type": ["string", "null"],
            "description": "ISO 8601 with a time zone if the page shows a time (2026-11-04T13:05:00-05:00), "
                           "a bare date if it shows only a date, or null if you cannot tell. Never guess.",
        },
        "relation": {"type": "string", "enum": ["cause", "consequence", "background", "market_coverage"]},
        "role": {"type": "string", "enum": list(stories.ROLES)},
        "note": {"type": "string", "description": "Optional, a few words such as 'quoting the State Department'."},
        "summary": {"type": "string", "description": "One sentence: what it says."},
    },
    "required": ["url", "relation", "role", "published_at", "summary"],
}

SUBMIT_TOOL = {
    "name": "submit_story",
    "description": "Report what the hunt found. Call this exactly once, when you have finished searching. "
                   "If you found the development, fill headline, dek, dateline, slug, summary, why_it_matters "
                   "and (if you have one) image. If you found nothing, leave those out and fill question instead.",
    "input_schema": {
        "type": "object",
        "properties": {
            "headline": {"type": "string", "description": "Plain, factual, sentence case, under 90 characters."},
            "dek": {"type": "string", "description": "One sentence on what changed."},
            "dateline": {"type": "string", "description": "Where it happened: a city, or a country or region."},
            "slug": {"type": "string", "description": "three-to-six-word-url-slug"},
            "summary": {"type": "string", "description": "Two to four sentences on what happened, with when and who."},
            "why_it_matters": {"type": "string", "description": "One or two sentences on the consequences in the world."},
            "image": {
                "type": "object",
                "description": "Only an image URL returned by lead_image. Credit and caption only if you saw them printed.",
                "properties": {"url": {"type": "string"}, "source_url": {"type": "string"},
                               "credit": {"type": "string"}, "caption": {"type": "string"}, "alt": {"type": "string"}},
                "required": ["url", "source_url"],
            },
            "question": {"type": "string", "description": "For an open lead only: a question about the world, "
                                                          "not the market, that does not speculate about a cause."},
            "trail_note": {"type": "string", "description": "Optional, for an open lead: one short sentence of context."},
            "sources": {"type": "array", "items": SOURCE_SCHEMA},
            "check_next": {"type": "array", "items": {"type": "string"},
                           "description": "Up to three concrete open questions for the next recheck. Not published."},
            "merge_with": {"type": "array", "items": {"type": "integer"},
                           "description": "Ids of other stories today that are the same news."},
        },
        "required": ["sources", "check_next"],
    },
}

IMAGE_TOOL = {
    "name": "lead_image",
    "description": "Read a news article's own lead image (og:image) and any credit the page declares. "
                   "Use only an image URL this tool returned.",
    "input_schema": {"type": "object", "properties": {"url": {"type": "string", "description": "The article URL."}},
                     "required": ["url"]},
}

X_TIME_TOOL = {
    "name": "x_post_time",
    "description": "The exact UTC time a post on X was published, worked out from its numeric post ID "
                   "(the number after /status/ in the URL).",
    "input_schema": {"type": "object", "properties": {"post_id": {"type": "string"}}, "required": ["post_id"]},
}

HUNT_RULES = """You are the reporter for Newshound, an automated news site. Prediction markets give you a scent: \
when expectations about something shift sharply, something probably happened. You are given what the markets \
moved and when. Your job is to find, with web search and web fetch, what actually happened, and report it by \
calling submit_story once. Nobody is watching this run, so do not ask questions.

The markets are the scent, not the story. Traders, odds and prices never appear in a headline, dek, summary, \
why-it-matters or question, and you never mention prices, points or dollars traded.

## Reading the scent
Before you search, work out what kind of development would produce this shift. For example, "the odds Rubio \
leaves fell 26 points" means something made him look more likely to stay: a denial, a statement of support, \
or a report being knocked down. Search for that. If several markets moved together, find the single \
development behind all of them. If this story and one in other_stories_today are really the same news, list \
the other's story_id in merge_with.

## Sourcing
- Report only sources that really appeared in your search results or that you fetched. Never invent or guess \
a URL. The code checks every source against the URLs your searches and fetches returned and drops the rest.
- Prefer primary sources and established outlets: the official statement, filing, docket, agency page or post \
by the person involved, and wire services and major news organizations. Find the original report, not a \
write-up of it.
- Aggregators, re-posters and partisan blogs (for example Political Wire, ZeroHedge, Townhall, Raw Story, \
NewsBeep, BizToc) are background at most. The code will not let them explain a story, so look for the \
established or primary source behind them.
- Record each source's publication time as precisely as the page shows it. Fetch the page when a search \
result doesn't show the time. Never guess a time or a time zone. For posts on X, use x_post_time.
- Look beyond news articles: posts on X, official statements and press releases, government and agency sites, \
court dockets, filings, schedules, sports injury reports, and the market's resolution source.
- Never stretch weak or general background into a cause. If you find nothing specific, say so. An open lead \
is a real and useful result.

relation (the code uses it to decide the status):
- cause: reports the development that drove the shift.
- consequence: reports that same development, but was published after the move started.
- background: context, not a trigger.
- market_coverage: only reports the odds or the trading.
role is what readers see next to each source: pick the one that describes what the source is.

## Day mode (when the item's mode is "day")
The scent is a whole topic: every market about it that moved over the last 24 hours, how strong the \
signal is, and when during the day the lead market moved. Your question is what happened today on this \
topic and how much it matters, not what happened in one particular minute.
- Find the development behind the shift as a whole. If several markets point to one development, that is \
the story. If this topic and one in other_stories_today have the same cause (for example a market rout \
that moved both an IPO's timing and rate expectations), list the other's story_id in merge_with and write \
the story about the shared cause.
- A cause must be from the day of the shift. Reports from earlier days are background: the code treats \
anything published more than about a day and a half before the window as background, whatever you call it.
- If the shift has no single trigger (a slow drift, or the knock-on of bigger news), say so in the summary \
and write about the bigger news if you can source it. Otherwise submit an open lead.
- Follow the strength note. For a weak signal, run two or three searches and stop if nothing specific \
turns up: an open lead is the right result and saves the budget for real news.
- why_it_matters should say how much this changes things, in plain terms.

## Hunts and rechecks
- kind "hunt": start with 2 or 3 searches on the most likely development. If you find it, confirm it, get the \
publication time of the earliest report, and stop. Otherwise dig deeper with up to about 10 more searches. \
Note any report published after the move started too.
- kind "recheck": nothing explained this story when it was first hunted. Run up to 5 searches for anything \
published since move_start_utc that reports the development behind it. If you find reporting, write the story \
up in full so the lead is promoted to a story. If not, keep the question.

## The lead image
When you found the story, call lead_image on one of its sources, preferring the original report. If it returns \
an error or no image, try another source. Use the credit and caption only if the tool returned them or you saw \
them printed with the image. If no source gives a usable image, leave image out.

## Writing
- headline: plain and factual, sentence case, under 90 characters.
- The question for an open lead is about the world, not the market, and must not repeat the market's wording.
- Style: plain language, no hype, no em-dashes.

When you are done, call submit_story exactly once."""


def _x_post_time(post_id: str) -> str:
    ms = (int(str(post_id).strip()) >> 22) + 1288834974657
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def _run_client_tool(block) -> tuple[str, bool]:
    from .images import lead_image
    try:
        if block.name == "lead_image":
            return json.dumps(lead_image(block.input["url"])), False
        if block.name == "x_post_time":
            return _x_post_time(block.input["post_id"]), False
    except Exception as ex:  # blocked, timed out, not HTML: the model moves on to another source
        return f"error: {str(ex)[:200]}", True
    return "Unknown tool.", True


# ---------- the API loop ----------

def _get(b, key, default=None):
    return b.get(key, default) if isinstance(b, dict) else getattr(b, key, default)


def _for_echo(content: list) -> list:
    """After a fallback mid-turn, drop the declined model's internal blocks before the last fallback marker."""
    idx = [i for i, b in enumerate(content) if _get(b, "type") == "fallback"]
    if not idx:
        return content
    cut = idx[-1]
    results = {_get(b, "tool_use_id") for b in content if str(_get(b, "type", "")).endswith("_tool_result")}
    keep = []
    for i, b in enumerate(content):
        t = _get(b, "type")
        if i < cut and (t in ("thinking", "redacted_thinking", "tool_use")
                        or (t == "server_tool_use" and _get(b, "id") not in results)):
            continue
        keep.append(b)
    return keep


def _collect(content: list, seen: list[dict]):
    """Every URL the API's own search and fetch tools returned this turn."""
    for b in content:
        t, c = _get(b, "type"), _get(b, "content")
        if t == "web_search_tool_result" and isinstance(c, list):
            for r in c:
                if _get(r, "type") == "web_search_result" and _get(r, "url"):
                    seen.append({"url": _get(r, "url"), "title": _get(r, "title"), "page_age": _get(r, "page_age")})
        elif t == "web_fetch_tool_result" and c is not None and _get(c, "type") == "web_fetch_result" and _get(c, "url"):
            seen.append({"url": _get(c, "url")})


class BillingError(RuntimeError):
    """The account can't pay for requests (no credit, bad key). Every later request would fail too."""


def _billing(e: Exception) -> bool:
    s = str(e).lower()
    return type(e).__name__ in ("AuthenticationError", "PermissionDeniedError") or \
        "credit balance" in s or "billing" in s


def _call(client, cfg, system, tools, messages):
    kw = dict(model=cfg.hunt_model, max_tokens=32000, system=system, tools=tools, messages=messages,
              output_config={"effort": cfg.hunt_effort}, cache_control={"type": "ephemeral"})
    if cfg.hunt_fallbacks:
        kw.update(betas=["server-side-fallback-2026-07-01"], fallbacks="default")
    with client.beta.messages.stream(**kw) as stream:
        return stream.get_final_message()


def searches_for(cfg, item: dict) -> int:
    n = cfg.hunt_max_searches if item["kind"] == "hunt" else cfg.recheck_max_searches
    if item.get("strength") == "weak":
        n = min(n, cfg.weak_max_searches)
    elif item.get("strength") == "moderate" and cfg.moderate_max_searches:
        n = min(n, cfg.moderate_max_searches)
    return n


def hunt(client, cfg, item: dict, ledger=None, now_ts: float | None = None, variant: str | None = None) -> dict | None:
    """One story. Returns findings in the shape `record` takes, or None if the model refused.
    With a ledger, every request is recorded and the transcript is saved. now_ts replays a hunt as if it
    were run at that time (the model is told that time)."""
    fetch = {"type": cfg.web_fetch_tool_api, "name": "web_fetch", "max_uses": cfg.hunt_max_fetches}
    if cfg.hunt_fetch_max_tokens:
        fetch["max_content_tokens"] = cfg.hunt_fetch_max_tokens
    tools = [
        {"type": cfg.web_search_tool_api, "name": "web_search", "max_uses": searches_for(cfg, item)},
        fetch, IMAGE_TOOL, X_TIME_TOOL, SUBMIT_TOOL,
    ]
    now = datetime.fromtimestamp(now_ts or time.time(), timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    messages = [{"role": "user", "content": f"Current time: {now}.\n\n" + json.dumps(item, indent=2)}]
    seen: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
             "web_searches": 0, "web_fetches": 0, "turns": 0}
    findings, nudged = None, False
    sid, kind = item["story_id"], item["kind"]

    for turn in range(cfg.hunt_max_turns):
        t0 = time.time()
        try:
            resp = _call(client, cfg, HUNT_RULES, tools, messages)
        except Exception as e:
            if ledger:
                ledger.call(client=client, purpose="hunt", story_id=sid, kind=kind, turn=turn, model_requested=cfg.hunt_model,
                            system=HUNT_RULES, tools=tools, messages=messages, elapsed_s=round(time.time() - t0, 2),
                            error=f"{type(e).__name__}: {str(e)[:300]}", variant=variant)
            if _billing(e):
                raise BillingError(str(e)[:300]) from e
            raise
        if ledger:
            ledger.call(client=client, purpose="hunt", story_id=sid, kind=kind, turn=turn, model_requested=cfg.hunt_model,
                        system=HUNT_RULES, tools=tools, messages=messages, resp=resp,
                        elapsed_s=round(time.time() - t0, 2), variant=variant)
        u = resp.usage
        usage["turns"] += 1
        for k in ("input_tokens", "output_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"):
            usage[k] += _get(u, k) or 0
        stu = _get(u, "server_tool_use")
        if stu is not None:
            usage["web_searches"] += _get(stu, "web_search_requests") or 0
            usage["web_fetches"] += _get(stu, "web_fetch_requests") or 0
        if resp.stop_reason == "refusal":
            log.warning("story %s: the model declined (%s)", item["story_id"], _get(resp.stop_details, "category"))
            return None
        content = list(resp.content)
        _collect(content, seen)
        messages.append({"role": "assistant", "content": _for_echo(content)})

        calls = [b for b in content if _get(b, "type") == "tool_use"]
        sub = next((b for b in calls if b.name == "submit_story"), None)
        if sub is not None:
            findings = dict(sub.input or {})
            break
        if resp.stop_reason == "pause_turn":
            continue  # a long server-side search turn: send it back unchanged so it carries on
        if calls:
            results = []
            for b in calls:
                text, err = _run_client_tool(b)
                if b.name == "lead_image" and not err:
                    seen.append({"url": b.input["url"]})  # we fetched this page ourselves
                results.append({"type": "tool_result", "tool_use_id": b.id, "content": text, "is_error": err})
            messages.append({"role": "user", "content": results})
            continue
        if nudged:
            break
        nudged = True
        messages.append({"role": "user", "content": "Now call submit_story with what you found."})

    if ledger:
        ledger.transcript(sid, kind, HUNT_RULES, tools, messages, variant=variant)
    log.info("story %s: %d in / %d out tokens (%d cached, %d cache writes), %d searches, %d fetches, %d turns",
             item["story_id"], usage["input_tokens"], usage["output_tokens"], usage["cache_read_input_tokens"],
             usage["cache_creation_input_tokens"], usage["web_searches"], usage["web_fetches"], usage["turns"])
    if findings is None:
        log.warning("story %s: no submit_story call", item["story_id"])
        findings = {"sources": [], "check_next": []}
    findings.setdefault("sources", [])
    findings.setdefault("check_next", [])
    findings["searched"] = seen
    findings["usage"] = usage
    if not (findings.get("headline") or findings.get("question")):
        findings.pop("headline", None)
    return findings


# ---------- naming the small stories ----------

LABEL_SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {
        "type": "object",
        "properties": {"story_id": {"type": "integer"}, "topic": {"type": "string"}},
        "required": ["story_id", "topic"], "additionalProperties": False}}},
    "required": ["labels"], "additionalProperties": False,
}

LABEL_PROMPT = """Each item below is a small news story, given only as the prediction-market questions that moved. \
For each one, write a topic of two to five words naming the real-world subject, not the bet ("Nigeria's 2027 \
race", "Vance's 2028 standing", "the Fed's December decision"). Give stories about the same subject the same \
topic, and reuse a topic from existing_topics when it fits. No em-dashes."""


def label_small(client, cfg, db, ledger=None) -> int:
    todo = stories.unlabeled(db, cfg)
    if not todo:
        return 0
    day = stories.et_day(time.time())
    existing = sorted({s["topic"] for s in db.stories("day=? AND topic IS NOT NULL", (day,))})
    messages = [{"role": "user", "content": json.dumps({"existing_topics": existing, "stories": todo})}]
    t0 = time.time()
    try:
        resp = client.messages.create(
            model=cfg.label_model, max_tokens=4000, system=LABEL_PROMPT,
            output_config={"format": {"type": "json_schema", "schema": LABEL_SCHEMA}}, messages=messages)
    except Exception as e:
        if ledger:
            ledger.call(purpose="label", model_requested=cfg.label_model, system=LABEL_PROMPT, messages=messages,
                        elapsed_s=round(time.time() - t0, 2), error=f"{type(e).__name__}: {str(e)[:300]}")
        if _billing(e):
            raise BillingError(str(e)[:300]) from e
        raise
    if ledger:
        ledger.call(purpose="label", model_requested=cfg.label_model, system=LABEL_PROMPT, messages=messages,
                    resp=resp, elapsed_s=round(time.time() - t0, 2))
    text = next((b.text for b in resp.content if b.type == "text"), "{}")
    ids = {s["story_id"] for s in todo}
    n = 0
    for lab in json.loads(text).get("labels", []):
        if lab.get("story_id") in ids and (lab.get("topic") or "").strip():
            stories.label(db, lab["story_id"], lab["topic"])
            n += 1
    return n


# ---------- one full run ----------

def cycle(db, cfg, client) -> dict:
    """Scan, label and hunt. Stops calling the API at the first billing error (report["billing_error"])."""
    from .ledger import Ledger
    ledger = Ledger(Path(cfg.db_path).parent / "ledger", keep_days=cfg.ledger_keep_transcript_days)
    report = {"new_moves": 0, "labeled": 0, "recorded": [], "failed": [], "billing_error": None}
    report["new_moves"] = len(scheduled.scan(db, cfg, Path(cfg.state_path)))
    try:
        report["labeled"] = label_small(client, cfg, db, ledger)
    except BillingError as e:
        log.error("the API account can't pay for requests: %s", e)
        report["billing_error"] = str(e)
        report["failed"].append(f"billing: {e}")
        ledger.prune()
        return report
    except Exception as e:
        log.exception("labelling failed")
        report["failed"].append(f"labels: {e}")

    q = sorted(scheduled.queue(db, cfg), key=lambda i: (i["kind"] != "hunt", -i["score"]))
    merged: set[int] = set()
    for item in q:
        sid = item["story_id"]
        if sid in merged or not db.get_story(sid):
            continue  # folded into another story earlier this run
        item = {**item, "other_stories_today": [o for o in stories.others_today(db) if o["story_id"] != sid]}
        try:
            f = hunt(client, cfg, item, ledger)
            if f is None:
                report["failed"].append(f"story {sid}: declined")
                continue
            f["merge_with"] = [int(x) for x in f.get("merge_with") or [] if int(x) != sid and db.get_story(int(x))]
            st = scheduled.record(db, cfg, sid, item["kind"], f)
            ledger.outcome(sid, item["kind"], st)
            merged.update({sid, *f["merge_with"]} - {st["id"]})
            report["recorded"].append({"story_id": st["id"], "kind": item["kind"], "status": st["status"],
                                       "score": st["score"], "headline": st.get("headline"),
                                       "question": st.get("question"), "usage": f.get("usage")})
            log.info("story %s (%.0f): %s | %s", st["id"], st["score"], st["status"], st.get("headline") or st.get("question"))
        except BillingError as e:
            log.error("the API account can't pay for requests: %s", e)
            report["billing_error"] = str(e)
            report["failed"].append(f"billing: {e}")
            break
        except Exception as e:
            log.exception("hunt failed for story %s", sid)
            report["failed"].append(f"story {sid}: {e}")
    ledger.prune()
    return report


def summary(db, cfg, report: dict) -> str:
    """Markdown for the run log: the day's top stories, open leads from this run, anything that failed."""
    day = stories.et_day(time.time())
    top = db.stories("day=? AND status IN ('reported','unclear') AND headline IS NOT NULL", (day,))[:3]
    lines = [f"**Newshound run** {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC: "
             f"{report['new_moves']} new moves, {len(report['recorded'])} stories hunted, {report['labeled']} labeled."]
    if top:
        lines += ["", "Top stories today:"]
        for s in top:
            band = next(lbl for floor, lbl in cfg.shift_bands if s["score"] >= floor)
            lines.append(f"- {s['headline']} ({band}, {s['score']:.0f})")
    leads = [r for r in report["recorded"] if r["status"] == "unreported" and r.get("question")]
    if leads:
        lines += ["", "On the trail:"] + [f"- {r['question']}" for r in leads]
    if report["failed"]:
        lines += ["", "Failed:"] + [f"- {x}" for x in report["failed"]]
    if not top and not report["recorded"]:
        lines += ["", "No stories this run."]
    u = [r["usage"] for r in report["recorded"] if r.get("usage")]
    if u:
        lines += ["", f"API use: {sum(x['input_tokens'] for x in u):,} input tokens "
                      f"({sum(x['cache_read_input_tokens'] for x in u):,} cache reads, "
                      f"{sum(x.get('cache_creation_input_tokens', 0) for x in u):,} cache writes), "
                      f"{sum(x['output_tokens'] for x in u):,} output, "
                      f"{sum(x['web_searches'] for x in u)} searches, {sum(x['web_fetches'] for x in u)} fetches."]
    lines += ["", cfg.site_url]
    return "\n".join(lines)
