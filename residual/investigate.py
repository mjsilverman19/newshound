"""The investigating agent: Claude with web search, asked why a market moved.

Three stages:
  quick    a cheap model and a few searches, to clear moves with an obvious public cause
  deep     a stronger model and many searches, only when the quick pass found nothing
  recheck  later on, for moves nobody explained: has anything been published since?
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")

SUBMIT_TOOL = {
    "name": "submit_findings",
    "description": "Report what you found. Call this exactly once, after you finish searching.",
    "input_schema": {
        "type": "object",
        "properties": {
            "sources": {
                "type": "array",
                "description": "Every relevant source you actually opened or saw in search results. "
                               "Leave the list empty if nothing relevant turned up.",
                "items": {
                    "type": "object",
                    "properties": {
                        "url": {"type": "string"},
                        "title": {"type": "string"},
                        "outlet": {"type": "string"},
                        "published_at": {
                            "type": ["string", "null"],
                            "description": "When it was first published, ISO 8601 with time zone if the page "
                                           "shows a time (e.g. 2026-11-04T13:05:00-05:00), a bare date if only "
                                           "a date is shown, or null if you cannot tell.",
                        },
                        "relation": {
                            "type": "string",
                            "enum": ["cause", "consequence", "market_coverage", "background"],
                            "description": "cause: reports a development that plausibly drove the move. "
                                           "consequence: reports that same development but was published after "
                                           "the move started. market_coverage: only reports the odds or the "
                                           "trading itself. background: context, not a trigger.",
                        },
                        "summary": {"type": "string", "description": "One sentence: what it says."},
                    },
                    "required": ["url", "relation", "summary", "published_at"],
                },
            },
            "assessment": {
                "type": "string",
                "description": "Two or three sentences. Say plainly if you found no explanation.",
            },
            "check_next": {
                "type": "array", "items": {"type": "string"},
                "description": "Up to four concrete things a reporter should check next.",
            },
        },
        "required": ["sources", "assessment", "check_next"],
    },
}

BASE_RULES = """You investigate sudden moves in prediction-market prices for a newsroom. A sharp move with \
no public explanation can mean someone knows something that has not been reported yet, so "I found no \
explanation" is a correct, useful result. Never stretch weak or general background into a cause.

Rules:
- Report only sources that appeared in your search results. Never invent or guess a URL.
- Give each source's publication time as precisely as the page or result shows it. Do not guess times.
- A cause must be specific and must plausibly move this exact market, given its resolution rules.
- Articles that only report the market odds or the trading are market_coverage, never a cause.
- Look beyond news articles when it helps: posts on X, official statements and press releases, \
government and agency sites, court dockets, regulatory filings, schedules and calendars, and the \
market's own resolution source.
- Finish by calling submit_findings once."""

STAGE_PROMPTS = {
    "quick": "This is a fast first check. Use at most {n} searches on the most obvious angle: recent "
             "news about the subject of this market. Stop as soon as you find a clear cause or run out.",
    "deep": "A quick check found no public explanation. Investigate thoroughly with up to {n} searches. "
            "Try different angles: the people and organizations named, official sources, social media, "
            "local and non-English outlets where relevant, and anything the resolution rules depend on. "
            "Also note sources published after the move started that report the same development.",
    "recheck": "Earlier searches found no public explanation for this move. Using up to {n} searches, look "
               "for anything published since the move started that reports a development which would "
               "explain it. Report each such source with its publication time. If there is still "
               "nothing, say so.",
}


def _fmt(ts: float) -> str:
    utc = datetime.fromtimestamp(ts, timezone.utc)
    return f"{utc:%Y-%m-%d %H:%M} UTC ({utc.astimezone(ET):%H:%M %Z})"


def describe_move(move: dict, market: dict | None, now_ts: float) -> str:
    sig = move.get("signals") or {}
    lines = [
        f"Market: {move['question']}",
        f"Platform: {move['platform']}   Link: {move['url']}",
    ]
    if market and market.get("rules"):
        lines.append(f"Resolution rules (excerpt): {market['rules'][:1200]}")
    lines += [
        f"Move: price went from {move['price_from'] * 100:.1f}¢ to {move['price_to'] * 100:.1f}¢ "
        f"({(move['price_to'] - move['price_from']) * 100:+.0f} points).",
        f"The move started at about {_fmt(move['move_start_ts'])} and was flagged at {_fmt(move['detected_ts'])}.",
        f"Current time: {_fmt(now_ts)}.",
    ]
    facts = []
    if sig.get("volume_multiple") is not None:
        facts.append(f"volume {sig['volume_multiple']}× normal")
    if sig.get("top_wallet_share") is not None:
        facts.append(f"largest single buyer was {sig['top_wallet_share']:.0%} of buying in the move's direction")
    if sig.get("fresh_wallets"):
        facts.append(f"{len(sig['fresh_wallets'])} new or nearly unused wallets among the biggest buyers")
    if facts:
        lines.append("Trading: " + "; ".join(facts) + ".")
    if sig.get("related"):
        rel = "; ".join(f"{r['question']} ({r['from'] * 100:.0f}¢ → {r['to'] * 100:.0f}¢)" for r in sig["related"][:5])
        lines.append(f"Other markets in the same event that moved at the same time: {rel}")
    return "\n".join(lines)


def _dump(block) -> dict:
    if isinstance(block, dict):
        return block
    return block.model_dump(exclude_none=True)


def run(client, stage: str, move: dict, market: dict | None, cfg, now_ts: float) -> dict:
    model = cfg.anthropic_model_quick if stage == "quick" else cfg.anthropic_model_deep
    n = {"quick": cfg.quick_max_searches, "deep": cfg.deep_max_searches,
         "recheck": cfg.quick_max_searches + 2}[stage]
    system = BASE_RULES + "\n\n" + STAGE_PROMPTS[stage].format(n=n)
    tools = [{"type": cfg.web_search_tool, "name": "web_search", "max_uses": n}, SUBMIT_TOOL]
    messages = [{"role": "user", "content": describe_move(move, market, now_ts)}]

    searched: list[dict] = []
    usage = {"input_tokens": 0, "output_tokens": 0, "web_searches": 0}
    findings = None
    for _ in range(8):
        resp = client.messages.create(model=model, max_tokens=4000, system=system,
                                      tools=tools, messages=messages)
        content = [_dump(b) for b in resp.content]
        u = _dump(resp.usage) if getattr(resp, "usage", None) is not None else {}
        usage["input_tokens"] += u.get("input_tokens", 0)
        usage["output_tokens"] += u.get("output_tokens", 0)
        usage["web_searches"] += (u.get("server_tool_use") or {}).get("web_search_requests", 0)

        for b in content:
            if b.get("type") == "web_search_tool_result" and isinstance(b.get("content"), list):
                for r in b["content"]:
                    if r.get("type") == "web_search_result":
                        searched.append({"url": r.get("url"), "title": r.get("title"), "page_age": r.get("page_age")})
            if b.get("type") == "tool_use" and b.get("name") == "submit_findings":
                findings = b.get("input") or {}
        if findings is not None:
            break

        messages.append({"role": "assistant", "content": content})
        if resp.stop_reason == "pause_turn":
            continue  # long search turn: send it back unchanged so it carries on
        pending = [b for b in content if b.get("type") == "tool_use"]
        if pending:  # some other client tool call: answer it so the turn can continue
            messages.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": b["id"], "content": "Unknown tool."} for b in pending]})
            continue
        messages.append({"role": "user", "content": "Now call submit_findings with what you found."})

    if findings is None:
        findings = {"sources": [], "assessment": "The agent did not return findings.", "check_next": [],
                    "error": "no submit_findings call"}
    findings.setdefault("sources", [])
    findings.setdefault("check_next", [])
    return {"stage": stage, "model": model, "findings": findings, "searched": searched, "usage": usage}


def make_client():
    import anthropic  # imported lazily so polling works without the SDK installed
    return anthropic.Anthropic()


def dumps(x) -> str:
    return json.dumps(x, indent=2, default=str)
