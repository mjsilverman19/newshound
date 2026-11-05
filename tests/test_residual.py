"""Offline tests: synthetic prices for the detector, and a scripted stand-in for the Claude API."""

import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from residual import brief, detect, pipeline, verdict
from residual.config import Config
from residual.db import DB

NOW = time.time()
START = NOW - 20 * 60


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


@pytest.fixture
def env(tmp_path):
    cfg = Config(db_path=str(tmp_path / "t.db"), briefs_dir=str(tmp_path / "briefs"))
    return cfg, DB(cfg.db_path)


def market(**kw):
    row = {"platform": "kalshi", "market_id": "KXTEST-1", "question": "Will the agency approve the drug?",
           "url": "https://kalshi.com/markets/kxtest", "event_id": "KXTEST", "event_title": "Approval",
           "category": "Health", "end_ts": NOW + 30 * 86400, "rules": "Resolves Yes if approved.",
           "extra": {}, "price": 0.70, "volume24h": 5000, "liquidity": 20000}
    row.update(kw)
    return row


# ---------- detector ----------

def test_flags_sharp_move_and_respects_cooldown(env):
    cfg, db = env
    row = market()
    db.upsert_markets([row])
    for i, p in enumerate([0.40, 0.41, 0.40, 0.55, 0.66]):
        db.add_snapshots([{**row, "price": p}], NOW - (30 - i * 7) * 60)
    found = detect.find_moves(db, "kalshi", [row], NOW, cfg)
    assert len(found) == 1 and round(found[0]["delta"], 2) == 0.30

    db.add_move({"platform": "kalshi", "market_id": row["market_id"], "question": row["question"], "url": "",
                 "detected_ts": NOW - 600, "move_start_ts": START, "price_from": 0.4, "price_to": 0.68,
                 "signals": {}, "series": []})
    assert detect.find_moves(db, "kalshi", [row], NOW, cfg) == []  # same move, inside cooldown


def test_skips_small_moves_live_events_and_thin_markets(env):
    cfg, db = env
    base = market()
    for i, p in enumerate([0.40, 0.42, 0.45]):
        db.add_snapshots([{**base, "price": p}], NOW - (30 - i * 10) * 60)
    assert detect.find_moves(db, "kalshi", [{**base, "price": 0.45}], NOW, cfg) == []
    assert not detect.eligible(market(category="Sports"), cfg, NOW)
    assert not detect.eligible(market(end_ts=NOW + 3600), cfg, NOW)
    assert not detect.eligible(market(liquidity=10), cfg, NOW)
    assert not detect.eligible(market(question="Lakers vs. Celtics"), cfg, NOW)
    assert detect.eligible(market(), cfg, NOW)


def test_move_start_is_last_point_before_departure():
    s = [(0, 0.40), (60, 0.41), (120, 0.40), (180, 0.55), (240, 0.66)]
    assert detect.move_start(s, 0.40, 0.26) == 120


# ---------- verdict rules ----------

def test_verdict_rules():
    searched = [{"url": "https://www.reuters.com/world/approval-123/"}]
    before = {"url": "https://reuters.com/world/approval-123", "relation": "cause",
              "published_at": iso(START - 600), "summary": "x"}
    assert verdict.assess([before], searched, START, 15)["verdict"] == "explained"

    invented = {**before, "url": "https://example.com/made-up"}
    assert verdict.assess([invented], searched, START, 15)["verdict"] == "unexplained"

    late = {**before, "published_at": iso(START + 3600)}
    a = verdict.assess([late], searched, START, 15)
    assert a["verdict"] == "unexplained" and a["first_later_report_ts"] == pytest.approx(START + 3600, abs=1)

    within_slack = {**before, "published_at": iso(START + 5 * 60)}
    assert verdict.assess([within_slack], searched, START, 15)["verdict"] == "explained"

    undated = {**before, "published_at": None}
    assert verdict.assess([undated], searched, START, 15)["verdict"] == "unclear"

    coverage = {**late, "relation": "market_coverage"}
    assert verdict.assess([coverage], searched, START, 15)["first_later_report_ts"] is None


# ---------- agent pipeline with a scripted client ----------

class ScriptedClient:
    """Plays back canned API responses in order and records each request."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.messages = self

    def create(self, **kw):
        self.calls.append(kw)
        return self.responses.pop(0)


def resp(*blocks, stop="end_turn", searches=0):
    return SimpleNamespace(content=list(blocks), stop_reason=stop,
                           usage={"input_tokens": 100, "output_tokens": 50,
                                  "server_tool_use": {"web_search_requests": searches}})


def search_result(*urls):
    return {"type": "web_search_tool_result", "tool_use_id": "s1",
            "content": [{"type": "web_search_result", "url": u, "title": u, "page_age": None,
                         "encrypted_content": "x"} for u in urls]}


def submit(sources, assessment="...", check_next=()):
    return {"type": "tool_use", "id": "t1", "name": "submit_findings",
            "input": {"sources": sources, "assessment": assessment, "check_next": list(check_next)}}


def add_move(db):
    db.upsert_markets([market()])
    mid = db.add_move({"platform": "kalshi", "market_id": "KXTEST-1", "question": "Will the agency approve the drug?",
                       "url": "https://kalshi.com/markets/kxtest", "detected_ts": NOW, "move_start_ts": START,
                       "price_from": 0.40, "price_to": 0.70,
                       "signals": {"volume_multiple": 8.0, "related": []},
                       "series": [[START, 0.4], [NOW, 0.7]]})
    return db.get_move(mid)


def test_obvious_cause_stops_after_quick_pass(env):
    cfg, db = env
    url = "https://www.statnews.com/2026/11/04/approval"
    client = ScriptedClient([resp(
        {"type": "server_tool_use", "id": "s1", "name": "web_search", "input": {"query": "agency approval"}},
        search_result(url),
        submit([{"url": url, "relation": "cause", "published_at": iso(START - 300), "summary": "Approved."}]),
        stop="tool_use", searches=1)])
    mv = pipeline.investigate_move(db, client, cfg, add_move(db))
    assert mv["verdict"] == "explained"
    assert len(client.calls) == 1 and client.calls[0]["model"] == cfg.anthropic_model_quick
    assert mv["next_recheck_ts"] is None
    assert "Explained" in open(mv["brief"]["path"]).read()


def test_invented_cause_is_rejected_and_later_report_counts_as_confirmation(env):
    cfg, db = env
    real = "https://apnews.com/article/agency-approves-drug"
    client = ScriptedClient([
        # quick pass: nothing
        resp(search_result("https://unrelated.com/a"), submit([], "No explanation found."), stop="tool_use"),
        # deep pass: a long search turn pauses once, then the agent claims a cause at a URL it never saw,
        # plus a real report published 40 minutes after the move started
        resp(search_result(real), stop="pause_turn", searches=4),
        resp(submit([
            {"url": "https://madeup.example/scoop", "relation": "cause", "published_at": iso(START - 900),
             "summary": "Claimed leak."},
            {"url": real, "relation": "consequence", "published_at": iso(START + 40 * 60), "summary": "Approved."},
        ], "Nothing public before the move.", ["Ask the agency when the decision was signed."]), stop="tool_use"),
    ])
    mv = pipeline.investigate_move(db, client, cfg, add_move(db))
    assert [c["model"] for c in client.calls] == [cfg.anthropic_model_quick] + [cfg.anthropic_model_deep] * 2
    # the paused turn was sent back so the agent could continue
    assert client.calls[2]["messages"][-1]["role"] == "assistant"
    assert mv["verdict"] == "unexplained"
    assert mv["lead_minutes"] == 40
    assert mv["next_recheck_ts"] == pytest.approx(NOW + 3600)
    md = open(mv["brief"]["path"]).read()
    assert "NOT FOUND in search results" in md and "40 minutes after the market moved" in md


def test_recheck_confirms_without_changing_verdict(env):
    cfg, db = env
    mv = add_move(db)
    db.update_move(mv["id"], status="investigated", verdict="unexplained", next_recheck_ts=NOW - 1)
    url = "https://www.wsj.com/health/approval"
    client = ScriptedClient([resp(search_result(url), submit([
        {"url": url, "relation": "consequence", "published_at": iso(START + 2 * 3600), "summary": "Approved."}]),
        stop="tool_use")])
    pipeline.work(db, client, cfg)
    mv = db.get_move(mv["id"])
    assert mv["verdict"] == "unexplained" and mv["lead_minutes"] == 120 and mv["rechecks_done"] == 1
    assert mv["next_recheck_ts"] == pytest.approx(NOW + 6 * 3600)
    assert "later confirmed" in brief.report_html(db)


def test_agent_that_never_submits_is_nudged_then_gives_up(env):
    cfg, db = env
    cfg.max_investigations_per_hour = 1  # no deep pass
    client = ScriptedClient([resp({"type": "text", "text": "Thinking..."})] * 8)
    mv = pipeline.investigate_move(db, client, cfg, add_move(db))
    assert mv["verdict"] == "unexplained"
    assert client.calls[1]["messages"][-1]["content"].startswith("Now call submit_findings")


# ---------- scheduled mode ----------

def test_scheduled_queue_and_record(env, tmp_path):
    from residual import scheduled
    cfg, db = env
    mv = add_move(db)
    q = scheduled.queue(db, cfg)
    assert [i["move_id"] for i in q] == [mv["id"]] and q[0]["kind"] == "investigate"
    assert "70.0¢" in q[0]["brief_for_agent"]
    url = "https://www.reuters.com/x"
    done = scheduled.record(db, cfg, mv["id"], "investigate", {
        "sources": [{"url": url, "relation": "cause", "published_at": iso(START - 60), "summary": "s"}],
        "assessment": "a", "check_next": [], "searched": [url], "queries": ["q"]})
    assert done["verdict"] == "explained"
    assert scheduled.queue(db, cfg) == []


def test_state_round_trip(tmp_path):
    from residual import scheduled
    p = tmp_path / "s.json.gz"
    assert scheduled.load_state(p) == {"saved_ts": {}, "prices": {}}
    scheduled.save_state(p, {"saved_ts": {"kalshi": 1.0}, "prices": {"kalshi": {"A": 0.5}}})
    assert scheduled.load_state(p)["prices"]["kalshi"]["A"] == 0.5
