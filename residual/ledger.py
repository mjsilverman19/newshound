"""The cost ledger: one JSON line per Claude API request, plus the transcript of each hunt.

Every request records the full `usage` the API returned (uncached input, cache writes split by
lifetime, cache reads, output, server-side searches and fetches), the model that actually served it
(which can differ from the one asked for when a fallback kicks in), and what the context held when the
request was sent, by kind of block:

    rules            the system prompt
    tool_defs        the tool definitions
    scent            the scent text in the opening message
    other_stories    the list of the day's other stories in the opening message
    item_other       the rest of the opening message
    search_results   web_search_tool_result blocks returned so far
    fetch_results    web_fetch_tool_result blocks returned so far
    client_results   results of our own tools (lead image, X post time)
    model_text       the model's text and thinking
    tool_calls       the model's tool calls, server and client

The composition is measured in characters and, when the API's token counter is reachable, scaled to
the counter's exact total for the request. `context_tokens_exact` says which one you are looking at.

Files, under <data>/ledger/:
    calls-YYYY-MM-DD.jsonl        one line per request (UTC date)
    transcripts/YYYY-MM-DD/<run>-<story>-<kind>.json.gz   the full message list of a hunt
Transcripts older than `ledger_keep_transcript_days` are deleted; the call lines are kept.
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger(__name__)

USAGE_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
PARTS = ("rules", "tool_defs", "scent", "other_stories", "item_other", "search_results", "fetch_results",
         "client_results", "model_text", "tool_calls")


def _get(o, k, default=None):
    if o is None:
        return default
    return o.get(k, default) if isinstance(o, dict) else getattr(o, k, default)


def _plain(o):
    """SDK objects to plain JSON-able values."""
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json", exclude_none=True)
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_plain(v) for v in o]
    return o


def run_id() -> str:
    return os.environ.get("GITHUB_RUN_ID") or datetime.now(timezone.utc).strftime("local-%Y%m%dT%H%M%S")


def usage_dict(u) -> dict:
    """Every usage field the API reports, flattened."""
    out = {k: _get(u, k) or 0 for k in USAGE_FIELDS}
    cc = _get(u, "cache_creation")
    out["cache_write_5m_tokens"] = _get(cc, "ephemeral_5m_input_tokens") or 0
    out["cache_write_1h_tokens"] = _get(cc, "ephemeral_1h_input_tokens") or 0
    if not (out["cache_write_5m_tokens"] or out["cache_write_1h_tokens"]):
        out["cache_write_5m_tokens"] = out["cache_creation_input_tokens"]  # older responses: all 5-minute
    stu = _get(u, "server_tool_use")
    out["web_searches"] = _get(stu, "web_search_requests") or 0
    out["web_fetches"] = _get(stu, "web_fetch_requests") or 0
    out["service_tier"] = _get(u, "service_tier")
    return out


def _default(o):
    if hasattr(o, "model_dump"):
        return o.model_dump(mode="json", exclude_none=True)
    return vars(o) if hasattr(o, "__dict__") else str(o)


def _chars(o) -> int:
    return len(json.dumps(_plain(o), ensure_ascii=False, default=_default))


def composition(system: str, tools: list, messages: list) -> dict:
    """Characters in the context by kind of block (see the module docstring)."""
    c = dict.fromkeys(PARTS, 0)
    c["rules"] = len(system or "")
    c["tool_defs"] = _chars(tools)
    for i, m in enumerate(messages):
        content = m["content"] if isinstance(m, dict) else _get(m, "content")
        if i == 0 and isinstance(content, str):
            body = content
            try:
                item = json.loads(body[body.index("{"):])
                c["scent"] += len(item.get("scent") or "")
                c["other_stories"] += _chars(item.get("other_stories_today") or [])
                c["item_other"] += len(body) - len(item.get("scent") or "") - _chars(item.get("other_stories_today") or [])
            except ValueError:
                c["item_other"] += len(body)
            continue
        if isinstance(content, str):
            c["model_text" if _get(m, "role") == "assistant" else "item_other"] += len(content)
            continue
        for b in content or []:
            t = _get(b, "type")
            n = _chars(b)
            if t == "web_search_tool_result":
                c["search_results"] += n
            elif t == "web_fetch_tool_result":
                c["fetch_results"] += n
            elif t == "tool_result":
                c["client_results"] += n
            elif t in ("tool_use", "server_tool_use"):
                c["tool_calls"] += n
            elif t in ("text", "thinking", "redacted_thinking"):
                c["model_text"] += n
            else:
                c["item_other"] += n
    return c


def _safe(fn):
    """The ledger must never break a hunt: a failure to record is logged and skipped."""
    import functools

    @functools.wraps(fn)
    def wrap(self, *a, **kw):
        try:
            return fn(self, *a, **kw)
        except Exception as e:
            log.warning("ledger: %s failed: %s", fn.__name__, e)
            return None
    return wrap


class Ledger:
    """Appends one line per request to the day's calls file, and saves hunt transcripts."""

    def __init__(self, folder: str | Path | None, keep_days: int = 14, count_tokens: bool = True):
        self.folder = Path(folder) if folder else None
        self.run = run_id()
        self.keep_days = keep_days
        self.count_tokens = count_tokens
        self._counter_ok = True
        if self.folder:
            (self.folder / "transcripts").mkdir(parents=True, exist_ok=True)

    def _exact_tokens(self, client, model, system, tools, messages) -> int | None:
        """The API's own count of the request's input, free of charge. None if it can't be had."""
        if not (self.count_tokens and self._counter_ok and client is not None):
            return None
        try:
            r = client.messages.count_tokens(model=model, system=system, tools=tools, messages=messages)
            return int(_get(r, "input_tokens"))
        except Exception as e:  # the counter may not know a server tool, or may be rate limited
            log.info("token counter unavailable, using characters only: %s", str(e)[:120])
            self._counter_ok = False
            return None

    @_safe
    def call(self, *, client=None, purpose: str, story_id=None, kind=None, turn=None, model_requested: str,
             system="", tools=(), messages=(), resp=None, elapsed_s: float | None = None, error: str | None = None,
             variant: str | None = None) -> dict:
        comp = composition(system, list(tools), list(messages))
        total_chars = sum(comp.values()) or 1
        exact = self._exact_tokens(client, model_requested, system, list(tools), list(messages)) if purpose == "hunt" else None
        row = {
            "ts": round(time.time(), 3), "run": self.run, "purpose": purpose, "variant": variant,
            "story_id": story_id, "kind": kind, "turn": turn,
            "model_requested": model_requested, "model": _get(resp, "model") if resp is not None else None,
            "request_id": _get(resp, "_request_id") or _get(resp, "id"),
            "stop_reason": _get(resp, "stop_reason"), "elapsed_s": elapsed_s, "error": error,
            "usage": usage_dict(_get(resp, "usage")) if resp is not None else None,
            "context_chars": comp,
            "context_tokens_exact": exact,
            "context_tokens_by_part": ({k: round(exact * v / total_chars) for k, v in comp.items()} if exact else
                                       {k: round(v / 3.6) for k, v in comp.items()}),
        }
        if self.folder:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with open(self.folder / f"calls-{day}.jsonl", "a") as fh:
                fh.write(json.dumps(row) + "\n")
        return row

    @_safe
    def outcome(self, story_id, kind, story: dict, variant: str | None = None, extra: dict | None = None):
        """What a hunt led to, so its cost can be set against it."""
        from .verdict import counts_as
        cited = [{"url": s.get("url"), "verified": bool(s.get("verified")), "counts_as": counts_as(s),
                  "stale": bool(s.get("stale"))} for s in story.get("sources") or []]
        row = {"ts": round(time.time(), 3), "run": self.run, "purpose": "outcome", "variant": variant,
               "story_id": story_id, "kind": kind, "final_story_id": story.get("id"), "status": story.get("status"),
               "headline": story.get("headline"), "question": story.get("question"), "cited": cited, **(extra or {})}
        if self.folder:
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            with open(self.folder / f"calls-{day}.jsonl", "a") as fh:
                fh.write(json.dumps(row) + "\n")
        return row

    @_safe
    def transcript(self, story_id, kind, system, tools, messages, variant: str | None = None):
        if not self.folder:
            return None
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = self.folder / "transcripts" / day
        d.mkdir(parents=True, exist_ok=True)
        name = f"{self.run}-{variant + '-' if variant else ''}{story_id}-{kind}.json.gz"
        with gzip.open(d / name, "wt") as fh:
            json.dump({"run": self.run, "story_id": story_id, "kind": kind, "variant": variant,
                       "system": system, "tools": _plain(list(tools)), "messages": _plain(list(messages))}, fh,
                      default=_default)
        return d / name

    @_safe
    def prune(self):
        if not self.folder:
            return
        cut = (datetime.now(timezone.utc) - timedelta(days=self.keep_days)).strftime("%Y-%m-%d")
        for d in (self.folder / "transcripts").glob("*"):
            if d.is_dir() and d.name < cut:
                shutil.rmtree(d, ignore_errors=True)


def read(folder: str | Path, since: str | None = None, until: str | None = None) -> list[dict]:
    """Every ledger line between two UTC dates (inclusive, YYYY-MM-DD)."""
    rows = []
    for f in sorted(Path(folder).glob("calls-*.jsonl")):
        day = f.stem[len("calls-"):]
        if (since and day < since) or (until and day > until):
            continue
        rows += [{**json.loads(line), "_day": day} for line in f.read_text().splitlines() if line.strip()]
    return rows
