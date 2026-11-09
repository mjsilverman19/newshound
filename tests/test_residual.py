"""Offline tests: synthetic prices for the detector, and a scripted stand-in for the Claude API."""

import json
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
    for i, p in enumerate([0.40, 0.41, 0.42]):
        db.add_snapshots([{**base, "price": p}], NOW - (30 - i * 10) * 60)
    assert detect.find_moves(db, "kalshi", [{**base, "price": 0.42}], NOW, cfg) == []
    assert not detect.eligible(market(category="Climate and Weather"), cfg, NOW)
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


# ---------- stories (scheduled mode) ----------

def story_move(db, market_id="KXTEST-1", event="KXTEST", dp=0.30, vol=20_000, vm=10.0):
    db.upsert_markets([market(market_id=market_id, event_id=event)])
    return db.add_move({"platform": "kalshi", "market_id": market_id, "question": f"Q {market_id}", "url": "u",
                        "detected_ts": NOW, "move_start_ts": START, "price_from": 0.40, "price_to": 0.40 + dp,
                        "signals": {"window_volume_usd": vol, "volume_multiple": vm}, "series": []})


def test_stories_group_by_event_and_score(env):
    from residual import stories
    cfg, db = env
    a = story_move(db, "A-1", "EV1")
    b = story_move(db, "A-2", "EV1", dp=0.10)
    c = story_move(db, "B-1", "EV2", dp=0.05, vol=500, vm=1)
    sids = stories.assign(db, [a, b, c], NOW)
    assert len(sids) == 2
    big = db.stories()[0]
    assert big["markets"] == 2 and big["money_usd"] == 40_000
    # 0.30*20000*sqrt(10) + 0.10*20000*sqrt(10) = 25,298 -> 10*log10(25,299) = 44.0
    assert big["score"] == pytest.approx(44.0, abs=0.1)
    small = db.stories()[1]
    assert small["score"] < cfg.hunt_min_score
    q = stories.queue(db, cfg)
    assert [i["story_id"] for i in q] == [big["id"]] and q[0]["kind"] == "hunt"
    assert "more likely" in q[0]["scent"] and "Resolves Yes if approved." in q[0]["scent"]


def test_record_reported_merge_and_rehunt_gate(env):
    from residual import scheduled, stories
    cfg, db = env
    s1, s2 = stories.assign(db, [story_move(db, "A-1", "EV1"), story_move(db, "B-1", "EV2")], NOW)
    url = "https://www.reuters.com/x"
    st = scheduled.record(db, cfg, s1, "hunt", {
        "headline": "Agency approves the drug", "dek": "d", "summary": "s", "why_it_matters": "w",
        "sources": [{"url": url, "relation": "cause", "published_at": iso(START - 60), "summary": "s"}],
        "merge_with": [s2], "searched": [url], "queries": ["q"]})
    assert st["status"] == "reported" and st["markets"] == 2 and db.get_story(s2) is None
    assert st["next_recheck_ts"] is None and st["sources"][0]["verified"]
    assert stories.queue(db, cfg) == []  # already hunted at this score


def test_unreported_story_gets_rechecked_and_confirmed(env):
    from residual import scheduled, stories
    cfg, db = env
    cfg.mode = "hourly"
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    st = scheduled.record(db, cfg, sid, "hunt", {"headline": "Traders raise the odds", "sources": [],
                                                 "searched": [], "queries": []})
    assert st["status"] == "unreported" and st["next_recheck_ts"] == pytest.approx(st["created_ts"] + 3600)
    db.update_story(sid, next_recheck_ts=NOW - 1)
    q = stories.queue(db, cfg)
    assert q[0]["kind"] == "recheck"
    url = "https://apnews.com/a"
    st = scheduled.record(db, cfg, sid, "recheck", {"sources": [
        {"url": url, "relation": "consequence", "published_at": iso(START + 45 * 60), "summary": "s"}],
        "searched": [url]})
    assert st["status"] == "unreported" and st["lead_minutes"] == 45 and st["rechecks_done"] == 1


def test_aggregators_and_partisan_blogs_never_explain_a_story(env):
    from residual import scheduled, stories
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    url = "https://townhall.com/news/x"
    st = scheduled.record(db, cfg, sid, "hunt", {
        "question": "Did the agency act?", "searched": [url],
        "sources": [{"url": url, "relation": "cause", "role": "denial", "published_at": iso(START - 600)}]})
    assert st["status"] == "unreported"
    assert st["sources"][0]["verified"] and st["sources"][0]["background_only"] and st["sources"][0]["role"] == "denial"


def test_record_sets_slug_once_and_checks_the_image(env):
    from residual import scheduled, stories
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    url = "https://www.reuters.com/world/agency-approves"
    src = {"url": url, "relation": "cause", "role": "original report", "outlet": "Reuters",
           "published_at": iso(START - 60)}
    st = scheduled.record(db, cfg, sid, "hunt", {
        "headline": "Agency approves the drug", "slug": "agency-approves-drug", "dateline": "Washington",
        "sources": [src], "searched": [url],
        "image": {"url": "https://img.example.com/a.jpg", "source_url": "https://invented.example.com/x"}})
    assert st["slug"] == "agency-approves-drug" and st["image"] is None  # image page never came up in a search
    st = scheduled.record(db, cfg, sid, "hunt", {
        "headline": "Agency approves the drug after review", "sources": [src], "searched": [url],
        "image": {"url": "https://img.example.com/a.jpg", "source_url": url, "credit": "Jane Doe/Reuters"}})
    assert st["slug"] == "agency-approves-drug" and st["image"]["credit"] == "Jane Doe/Reuters"


def test_site_shows_what_the_hunt_caught_and_nothing_about_the_markets(env, tmp_path):
    from residual import scheduled, site, stories
    cfg, db = env
    a, b, c = stories.assign(db, [story_move(db, "A-1", "EV1"), story_move(db, "B-1", "EV2"),
                                  story_move(db, "C-1", "EV3", dp=0.05, vol=500, vm=1)], NOW)
    url = "https://apnews.com/article/agency"
    scheduled.record(db, cfg, a, "hunt", {
        "headline": "Agency approves the drug", "dek": "The first approval of its kind.", "dateline": "Washington",
        "summary": "On Thursday the agency approved it.", "why_it_matters": "Patients get access.",
        "sources": [{"url": url, "relation": "cause", "role": "original report", "outlet": "AP",
                     "published_at": iso(START - 60)}], "searched": [url]})
    scheduled.record(db, cfg, b, "hunt", {"headline": "Traders raise the odds", "question": "Has the merger stalled?",
                                          "trail_note": "The shift came late in the day.", "sources": [],
                                          "searched": ["https://example.com/a"]})
    assert stories.unlabeled(db, cfg)[0]["story_id"] == c
    stories.label(db, c, "Nigeria's 2027 race")
    out = tmp_path / "site"
    files = site.build(db, cfg, out, NOW)
    front = (out / "index.html").read_text()
    st = db.get_story(a)
    page = (out / site.story_path(st) / "index.html").read_text()
    assert "Agency approves the drug" in front and "Has the merger stalled?" not in front
    assert "Traders raise the odds" not in front and "Q B-1" not in front and "Q C-1" not in front
    assert "Also watching" not in front
    assert "The shift came late in the day." not in front and "rechecking at" not in front
    for path in out.rglob("*"):
        if path.is_file():
            rendered = path.read_text()
            assert "Has the merger stalled?" not in rendered
            assert "Nigeria&#x27;s 2027 race" not in rendered
            assert "On the trail" not in rendered
    for text in (front, page):
        text = text.split("</style>", 1)[1]
        assert "·" not in text and "<svg class=\"chart" not in text and "$" not in text and "%" not in text
    assert "Why it matters" in page and "Original report" in page and "Washington" in page
    assert "Why the money moved" not in page and "What to check next" not in page
    assert "feed.xml" in files and "archive/index.html" in files and ".nojekyll" in files
    assert "<link>https://mjsilverman19.github.io/newshound/" in (out / "feed.xml").read_text()


def test_state_round_trip(tmp_path):
    from residual import scheduled
    p = tmp_path / "s.json.gz"
    assert scheduled.load_state(p) == {"saved_ts": {}, "prices": {}}
    scheduled.save_state(p, {"saved_ts": {"kalshi": 1.0}, "prices": {"kalshi": {"A": 0.5}}})
    assert scheduled.load_state(p)["prices"]["kalshi"]["A"] == 0.5


# ---------- scheduled mode on the Claude API ----------

class _Stream:
    def __init__(self, msg):
        self.msg = msg

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.msg


class APIClient:
    """Stands in for anthropic.Anthropic(): beta.messages.stream for hunts, messages.create for labels."""

    def __init__(self, hunts, labels=None):
        self.hunts, self.labels, self.calls = list(hunts), labels, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
        self.messages = SimpleNamespace(create=self._create)

    def _stream(self, **kw):
        self.calls.append({**kw, "messages": list(kw["messages"])})
        return _Stream(self.hunts.pop(0))

    def _create(self, **kw):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=json.dumps(self.labels or {"labels": []}))])


def api_resp(*blocks, stop="tool_use"):
    return SimpleNamespace(content=[SimpleNamespace(**b) if isinstance(b, dict) else b for b in blocks],
                           stop_reason=stop, stop_details=None,
                           usage=SimpleNamespace(input_tokens=100, output_tokens=50, cache_read_input_tokens=0,
                                                 server_tool_use=SimpleNamespace(web_search_requests=1,
                                                                                 web_fetch_requests=0)))


def api_search(*urls):
    return {"type": "web_search_tool_result", "tool_use_id": "s1",
            "content": [SimpleNamespace(type="web_search_result", url=u, title=u, page_age=None) for u in urls]}


def test_api_hunt_checks_sources_against_what_the_api_returned(env, monkeypatch):
    from residual import hunter, stories
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    real = "https://apnews.com/article/agency-approves"
    monkeypatch.setattr("residual.images.lead_image",
                        lambda url: {"source_url": url, "url": "https://img.apnews.com/a.jpg", "credit": "AP"})
    client = APIClient([
        api_resp(api_search(real), {"type": "tool_use", "id": "t1", "name": "lead_image", "input": {"url": real}}),
        api_resp({"type": "tool_use", "id": "t2", "name": "submit_story", "input": {
            "headline": "Agency approves the drug", "dek": "d", "summary": "s", "why_it_matters": "w",
            "dateline": "Washington", "slug": "agency-approves-drug",
            "image": {"url": "https://img.apnews.com/a.jpg", "source_url": real, "credit": "AP"},
            "sources": [
                {"url": real, "relation": "cause", "role": "original report", "published_at": iso(START - 60),
                 "summary": "Approved."},
                {"url": "https://madeup.example/scoop", "relation": "cause", "role": "original report",
                 "published_at": iso(START - 900), "summary": "Invented."}],
            "check_next": []}}),
    ])
    item = stories.queue(db, cfg)[0]
    f = hunter.hunt(client, cfg, item)
    # the second call answered the image tool, and the model never got to claim what it searched
    assert client.calls[1]["messages"][-1]["content"][0]["tool_use_id"] == "t1"
    assert {s["url"] for s in f["searched"]} == {real}
    st = stories.record(db, cfg, sid, "hunt", f)
    assert st["status"] == "reported" and st["image"]["credit"] == "AP"
    verified = {s["url"]: s["verified"] for s in st["sources"]}
    assert verified[real] and not verified["https://madeup.example/scoop"]
    assert client.calls[0]["fallbacks"] == "default" and client.calls[0]["model"] == cfg.hunt_model


def test_api_hunt_resumes_paused_turns_nudges_once_and_labels(env):
    from residual import hunter, stories
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    item = stories.queue(db, cfg)[0]
    client = APIClient([api_resp(api_search("https://x.com/a"), stop="pause_turn"),
                        api_resp({"type": "text", "text": "Nothing."}, stop="end_turn"),
                        api_resp({"type": "text", "text": "Still nothing."}, stop="end_turn")])
    f = hunter.hunt(client, cfg, item)
    assert client.calls[1]["messages"][-1]["role"] == "assistant"  # paused turn sent back as is
    assert client.calls[2]["messages"][-1]["content"].startswith("Now call submit_story")
    assert f["sources"] == [] and len(client.calls) == 3

    (small,) = stories.assign(db, [story_move(db, "Z-1", "EVZ", dp=0.05, vol=500, vm=1)], NOW)
    client = APIClient([], labels={"labels": [{"story_id": small, "topic": "the agency's review"},
                                              {"story_id": 999, "topic": "not a story"}]})
    assert hunter.label_small(client, cfg, db) == 1 and db.get_story(small)["topic"] == "the agency's review"


def test_x_post_time():
    from residual import hunter
    assert hunter._x_post_time("1853571232563740821").startswith("2024-11-04")


def test_merging_into_a_new_story_keeps_the_published_one_and_its_url(env, tmp_path):
    from residual import scheduled, site, stories
    cfg, db = env
    (old,) = stories.assign(db, [story_move(db, "A-1", "EV1")], NOW)
    url = "https://www.reuters.com/world/rubio-denies"
    st = scheduled.record(db, cfg, old, "hunt", {
        "headline": "State Department denies Rubio report", "slug": "rubio-denies", "summary": "s",
        "sources": [{"url": url, "relation": "cause", "published_at": iso(START - 60), "summary": "s"}],
        "searched": [url]})
    assert st["status"] == "reported"
    (old_lead,) = stories.assign(db, [story_move(db, "C-1", "EV3")], NOW)
    db.update_story(old_lead, slug="vance-lead", headline="Rubio report denied again", status="unclear")
    (new,) = stories.assign(db, [story_move(db, "B-1", "EV2")], NOW)
    db.update_story(new, created_ts=NOW + 3600)  # flagged in a later run
    # a later hunt of the new story finds only an undated report, and says all three are the same news
    other = "https://apnews.com/article/rubio"
    st = scheduled.record(db, cfg, new, "hunt", {
        "headline": "Rubio stays, State Department says", "merge_with": [old, old_lead],
        "sources": [{"url": other, "relation": "cause", "published_at": None, "summary": "s"}],
        "searched": [other]})
    assert st["id"] == old and db.get_story(new) is None and db.get_story(old_lead) is None
    assert st["slug"] == "rubio-denies" and st["markets"] == 3
    assert st["status"] == "reported"  # the earlier, verified report still stands
    assert {s["url"] for s in st["sources"]} == {url, other}
    day = stories.et_day(NOW)
    assert st["aliases"] == [f"{day}/vance-lead"]
    out = tmp_path / "site"
    site.build(db, cfg, out, NOW)
    moved = (out / day.replace("-", "/") / "vance-lead" / "index.html").read_text()
    assert f'url=/newshound/{day.replace("-", "/")}/rubio-denies/' in moved


# ---------- recalibration: macro markets, grouping by name, front-page money floor ----------

def test_macro_markets_kept_but_price_feeds_excluded():
    cfg = Config()
    base = {"platform": "kalshi", "liquidity": 50_000, "end_ts": NOW + 60 * 86400}
    fed = dict(base, category="Financials", question="Will the Fed cut rates in December?")
    assert detect.eligible(fed, cfg, NOW)
    soon = dict(fed, end_ts=NOW + 2 * 86400)
    assert not detect.eligible(soon, cfg, NOW)          # resolves too soon: tracks the live price
    feed = dict(base, category="Financials", question="Will Apple close above $250?")
    assert not detect.eligible(feed, cfg, NOW)          # finance market, but not about the economy
    btc = dict(base, category="Crypto, Financials", question="Will there be a recession in 2027?")
    assert not detect.eligible(btc, cfg, NOW)           # crypto stays out whatever the question
    daily = dict(base, category="Equities", question="Nasdaq up or down on Friday?")
    assert not detect.eligible(daily, cfg, NOW)


def test_regroup_merges_stories_sharing_a_name(env):
    from residual import stories
    cfg, db = env
    ids = []
    for mid, ev, q in [("R-1", "EV1", "Will Marco Rubio leave the Trump administration?"),
                       ("V-1", "EV2", "Vance's Republican nomination odds hit 40%?"),
                       ("X-1", "EV3", "Will Gadi Eisenkot become prime minister?")]:
        m = story_move(db, mid, ev)
        db.upsert_markets([market(market_id=mid, event_id=ev, event_title=f"event {ev}", question=q)])
        db.conn.execute("UPDATE moves SET question=? WHERE id=?", (q, m)); db.conn.commit()
        ids.append(m)
    stories.assign(db, ids, NOW)
    assert len(db.stories()) == 3
    r, v, x = (db.get_move(i)["story_id"] for i in ids)
    db.update_story(v, headline="State Department denies report that Rubio will quit")
    groups = stories.regroup(db, cfg, stories.et_day(NOW))
    assert groups == [sorted([r, v])]
    assert len(db.stories()) == 2                       # Trump alone never ties stories together


def test_front_page_lead_needs_money_floor():
    from residual import site
    cfg = Config()
    thin = {"score": 40, "money_usd": 3_000}
    heavy = {"score": 30, "money_usd": 25_000}
    assert site.ranked([thin, heavy], cfg) == [heavy, thin]


# ---------- day mode ----------

def day_row(mid, ev, q, chg, week, tags="Politics; Fed", title=None, price=0.5):
    return {"platform": "polymarket", "market_id": mid, "question": q, "url": "https://polymarket.com/x",
            "event_id": ev, "event_title": title or q, "category": tags, "end_ts": NOW + 60 * 86400,
            "rules": "Resolves Yes if it happens.", "extra": {"volume1wk": week, "one_day_change": chg},
            "price": price, "volume24h": week / 7, "liquidity": 10_000}


def test_day_scores_by_net_change_times_normal_dollars():
    from residual import day
    cfg = Config()
    rows = [day_row("a", "E1", "Another Fed rate hike in 2026?", -0.14, 7 * 41_000),
            day_row("b", "E2", "Will 2 Fed rate hikes happen in 2026?", -0.02, 7 * 50_000),   # too small a move
            day_row("c", "E3", "Jordan vs. Indonesia exact score?", 0.20, 7 * 6_000, tags="Sports; Soccer"),
            day_row("d", "E4", "Will it rain in Paris?", 0.10, 7 * 9_000, tags="Weather")]
    ms = day.score_markets(rows, cfg, NOW)
    assert [m["market_id"] for m in ms] == ["a"]
    assert ms[0]["s"] == pytest.approx(0.14 * 41_000)


def test_day_groups_by_specific_tags_not_broad_names():
    from residual import day
    cfg = Config()
    rows = [day_row("a", "E1", "Will Anthropic IPO by December 31?", -0.18, 7 * 20_000, tags="Finance; Anthropic IPO; Dario"),
            day_row("b", "E2", "How much will Anthropic raise?", -0.17, 7 * 5_000, tags="IPO; Anthropic IPO; Dario"),
            day_row("c", "E3", "Netanyahu out by June 30, 2027?", 0.14, 7 * 22_000, tags="Israel; Politics"),
            day_row("d", "E4", "US-Iran Hormuz agreement by November 30?", -0.08, 7 * 39_000, tags="Iran; Oil; Strait of Hormuz")]
    topics = day.group(day.score_markets(rows, cfg, NOW), cfg)
    assert [sorted(e["event_id"] for e in t["events"]) for t in topics] == [["E1", "E2"], ["E4"], ["E3"]]
    assert day.strength(topics[0], cfg) in ("moderate", "strong")


def test_day_topics_become_stories_and_published_ones_are_not_rehunted(env):
    from residual import day, stories
    cfg, db = env
    rows = [day_row("a", "E1", "Another Fed rate hike in 2026?", -0.14, 7 * 400_000)]
    topics = day.group(day.score_markets(rows, cfg, NOW), cfg)
    (sid,) = day.attach(db, cfg, topics, NOW)
    st = db.get_story(sid)
    assert st["scent_data"]["rank"] == 1 and st["score"] >= cfg.day_hunt_min_score
    q = stories.queue(db, cfg)
    assert q[0]["mode"] == "day" and "Another Fed rate hike" in q[0]["scent"] and "Find:" in q[0]["scent"]
    db.update_story(sid, status="reported", headline="Fed hike bets fade", hunted_ts=NOW, hunted_score=st["score"])
    topics = day.group(day.score_markets([day_row("a", "E1", "Another Fed rate hike in 2026?", -0.15, 7 * 400_000)], cfg, NOW), cfg)
    assert day.attach(db, cfg, topics, NOW + 3600) == [sid]          # the same story, not a new one
    assert stories.queue(db, cfg) == []                               # small change: no second hunt


def test_day_sources_from_earlier_days_are_background(env):
    from residual import day, scheduled
    cfg, db = env
    topics = day.group(day.score_markets([day_row("a", "E1", "Another Fed rate hike in 2026?", -0.14, 7 * 400_000)], cfg, NOW), cfg)
    (sid,) = day.attach(db, cfg, topics, NOW)
    old, new = "https://www.reuters.com/old", "https://www.reuters.com/new"
    st = scheduled.record(db, cfg, sid, "hunt", {
        "headline": "h", "summary": "s", "searched": [old], "queries": [],
        "sources": [{"url": old, "relation": "cause", "published_at": iso(NOW - 5 * 86400), "summary": "s"}]})
    assert st["status"] == "unreported"                               # a five-day-old report can't explain today
    st = scheduled.record(db, cfg, sid, "hunt", {
        "headline": "h", "summary": "s", "searched": [new], "queries": [],
        "sources": [{"url": new, "relation": "cause", "published_at": iso(NOW - 3600), "summary": "s"}]})
    assert st["status"] == "reported"


def test_day_links_the_same_subject_across_platforms():
    from residual import day
    cfg = Config()
    pm = day_row("a", "E1", "Will J.D. Vance win the 2028 Republican presidential nomination?", 0.06, 7 * 63_000,
                 title="Republican Presidential Nominee 2028")
    k = {**day_row("b", "K1", "2028 Republican presidential nominee [J.D. Vance]", 0.0, 7 * 20_000,
                   tags="Elections", title="2028 Republican presidential nominee"),
         "platform": "kalshi", "extra": {"previous_price": 0.30}, "price": 0.37, "volume24h": 50_000}
    counted = {**day_row("c", "K2", "South Dakota Senate margin of victory [10+]", 0.4, 7 * 9_000, tags="Elections"),
               "platform": "kalshi", "extra": {"previous_price": 0.2}, "price": 0.6, "volume24h": 20_000}
    topics = day.group(day.score_markets([pm, k, counted], cfg, NOW), cfg)
    assert len(topics) == 1 and {e["platform"] for e in topics[0]["events"]} == {"polymarket", "kalshi"}


def test_day_mode_ranks_day_scored_stories_before_hourly_ones():
    from residual import site
    cfg = Config()
    hourly = {"score": 46, "money_usd": 30_000, "scent_data": None}
    daily = {"score": 43, "money_usd": 140_000, "scent_data": {"rank": 1}}
    assert site.ranked([hourly, daily], cfg) == [daily, hourly]


# ---------- cost ledger ----------

def test_ledger_records_every_request_with_full_usage_and_composition(env, tmp_path):
    from residual import hunter, stories, ledger as L
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    item = stories.queue(db, cfg)[0]
    r1 = api_resp(api_search("https://apnews.com/a"), stop="pause_turn")
    r1.usage.cache_creation_input_tokens = 9000
    r1.usage.cache_creation = SimpleNamespace(ephemeral_5m_input_tokens=9000, ephemeral_1h_input_tokens=0)
    r1.model = "claude-sonnet-5-5"   # a fallback served it
    client = APIClient([r1, api_resp({"type": "tool_use", "id": "t", "name": "submit_story",
                                      "input": {"question": "Q?", "sources": []}})])
    client.messages.count_tokens = lambda **kw: SimpleNamespace(input_tokens=4000)
    led = L.Ledger(tmp_path / "ledger")
    f = hunter.hunt(client, cfg, item, led)
    rows = L.read(tmp_path / "ledger")
    assert [r["turn"] for r in rows] == [0, 1] and all(r["purpose"] == "hunt" and r["story_id"] == sid for r in rows)
    assert rows[0]["usage"]["cache_creation_input_tokens"] == 9000 and rows[0]["usage"]["cache_write_5m_tokens"] == 9000
    assert rows[0]["model"] == "claude-sonnet-5-5" and rows[0]["model_requested"] == cfg.hunt_model
    assert rows[0]["context_tokens_exact"] == 4000 and sum(rows[0]["context_tokens_by_part"].values()) == pytest.approx(4000, abs=10)
    assert rows[1]["context_chars"]["search_results"] > 0 and rows[0]["context_chars"]["search_results"] == 0
    assert rows[0]["context_chars"]["scent"] > 0 and rows[0]["context_chars"]["rules"] == len(hunter.HUNT_RULES)
    assert f["usage"]["cache_creation_input_tokens"] == 9000 and f["usage"]["turns"] == 2
    assert list((tmp_path / "ledger" / "transcripts").rglob("*.json.gz"))


def test_cycle_stops_at_the_first_billing_error(env, tmp_path, monkeypatch):
    from residual import hunter, stories
    cfg, db = env
    cfg.db_path = str(tmp_path / "residual.db")
    for i in range(2):
        stories.assign(db, [story_move(db, f"B-{i}", f"EVB{i}")], NOW)
    monkeypatch.setattr("residual.scheduled.scan", lambda db, cfg, state: [])
    calls = []

    def broke(**kw):
        calls.append(1)
        raise RuntimeError("Error code: 400 - Your credit balance is too low to access the Anthropic API.")
    client = APIClient([])
    client.beta.messages.stream = broke
    report = hunter.cycle(db, cfg, client)
    assert report["billing_error"] and len(calls) == 1      # the second story was not tried


def test_audit_prices_reports_and_reconciles(tmp_path):
    from residual import audit, ledger as L
    prices = audit.load_prices()
    row = {"model": "claude-opus-5-5", "usage": {
        "input_tokens": 1_000_000, "cache_creation_input_tokens": 1_000_000, "cache_write_5m_tokens": 1_000_000,
        "cache_write_1h_tokens": 0, "cache_read_input_tokens": 1_000_000, "output_tokens": 1_000_000, "web_searches": 1000}}
    c = audit.cost(row, prices)
    assert c == pytest.approx({"uncached_input": 4.0, "cache_writes": 5.0, "cache_reads": 0.2, "output": 20.0, "searches": 10.0})
    haiku = {"model": "claude-haiku-5-5", "usage": {**row["usage"], "web_searches": 0}}
    assert audit.cost(haiku, prices)["output"] == 2.5          # over 100k prompt tokens: the long tier

    led = L.Ledger(tmp_path / "ledger")
    u = {"input_tokens": 10, "cache_creation_input_tokens": 20_000, "cache_write_5m_tokens": 20_000,
         "cache_write_1h_tokens": 0, "cache_read_input_tokens": 50_000, "output_tokens": 2_000, "web_searches": 3,
         "web_fetches": 1, "service_tier": "standard"}
    for turn in range(2):
        line = {"ts": NOW, "run": led.run, "purpose": "hunt", "variant": None, "story_id": 7, "kind": "hunt",
                "turn": turn, "model_requested": "claude-opus-5-5", "model": "claude-opus-5-5", "usage": u,
                "context_tokens_by_part": {"rules": 1_000, "search_results": 9_000}, "context_tokens_exact": 10_000}
        with open(tmp_path / "ledger" / "calls-2026-11-05.jsonl", "a") as fh:
            fh.write(json.dumps(line) + "\n")
    led.outcome(7, "hunt", {"id": 7, "status": "reported", "sources": [{"url": "https://a.com/x", "verified": True,
                                                                         "relation": "cause"}]})
    text = audit.report(tmp_path / "ledger")
    per_turn = 20_000 * 5e-6 + 50_000 * 0.2e-6 + 10 * 4e-6 + 2_000 * 20e-6 + 0.03
    assert f"${2 * per_turn / 1:.3f}" in text                   # cost per published story, one story
    assert "search results" in text and "| 1 | 1 |" in text
    csvp = tmp_path / "cost.csv"
    csvp.write_text(f"Date,Cost (USD)\n2026-11-05,{2 * per_turn:.4f}\n")
    rec = audit.reconcile(tmp_path / "ledger", "2026-11-05", "2026-11-06", str(csvp))
    assert "Within 5%" in rec


def test_replay_scores_variants_on_the_frozen_set_within_budget(tmp_path):
    from pathlib import Path
    from residual import replay
    audit_dir = Path(__file__).resolve().parent.parent / "audit"
    frozen = json.loads((audit_dir / "frozen" / "items.json").read_text())
    good = "https://www.axios.com/2026/11/04/hakeem-jeffries-speaker-house-democrats-letter"

    class Client:
        def __init__(self):
            self.n = 0
            self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self._stream))
            self.messages = SimpleNamespace(count_tokens=lambda **kw: SimpleNamespace(input_tokens=5000))

        def _stream(self, **kw):
            self.n += 1
            sub = {"type": "tool_use", "id": f"t{self.n}", "name": "submit_story", "input": {
                "headline": "Jeffries launches bid for speaker", "dek": "d", "summary": "Jeffries asked for support.",
                "why_it_matters": "w", "dateline": "Washington", "slug": f"jeffries-{self.n}",
                "sources": [{"url": good, "relation": "cause", "role": "original report",
                             "published_at": "2026-11-04T15:00:00-05:00", "summary": "s"},
                            {"url": "https://invented.example/x", "relation": "cause", "published_at": None, "summary": "s"}]}}
            r = api_resp(api_search(good), sub)
            r.usage.output_tokens = 10_000      # $0.20 on Opus, $0.10 on Sonnet, plus one search
            return _Stream(r)

    cfg = Config()
    text = replay.run(tmp_path, cfg, Client(), budget=0.70, variants=["A", "A2", "B"], items=[26, 9])
    rows = [json.loads(x) for x in next((tmp_path / "ledger").glob("replay-*.jsonl")).read_text().splitlines()]
    # item 26 under A, A2, B: 0.21 + 0.21 + 0.11; item 9 / A would need up to 0.21 more: over budget, so stop
    assert [(r["variant"], r["story_id"]) for r in rows] == [("A", 26), ("A2", 26), ("B", 26)]
    r = rows[0]
    assert r["published"] and r["matches_reference"] and not r["wrong_story"] and r["unverified_citations"] == 1
    assert r["published_thursday_evidence"] and r["ft_overlap"]
    assert rows[2]["cost"] < rows[0]["cost"]
    assert "stopped before item 9" in text and "| B: Sonnet |" in text
    assert frozen["items"][0]["story_id"] == 26


def test_a_broken_ledger_never_breaks_a_hunt(env, tmp_path, monkeypatch):
    from residual import hunter, stories, ledger as L
    cfg, db = env
    stories.assign(db, [story_move(db)], NOW)
    item = stories.queue(db, cfg)[0]
    monkeypatch.setattr(L, "composition", lambda *a: 1 / 0)
    client = APIClient([api_resp({"type": "tool_use", "id": "t", "name": "submit_story",
                                  "input": {"question": "Q?", "sources": []}})])
    f = hunter.hunt(client, cfg, item, L.Ledger(tmp_path / "ledger"))
    assert f["question"] == "Q?"


@pytest.mark.parametrize("relation", ["report", "consequence", "cause"])
def test_recent_news_can_publish_without_proving_market_causation(env, relation):
    from residual import stories, site
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    url = "https://apnews.com/article/actual-development"
    # A later report can qualify immediately, without waiting for a recheck.
    f = {"headline": "Lawmakers demand policy commitments", "summary": "Lawmakers made demands today.",
         "searched": [url], "sources": [{"url": url, "relation": relation,
         "published_at": iso(NOW - 60)}]}
    st = stories.record(db, cfg, sid, "hunt", f, now=NOW)
    assert st["status"] == "reported" and site.is_story(st)
    assert st["next_recheck_ts"] is None


@pytest.mark.parametrize("change", ["stale", "future", "undated", "unverified", "blocked", "background", "odds", "incomplete"])
def test_lower_publication_bar_preserves_evidence_requirements(env, change):
    from residual import stories, site
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    url = "https://apnews.com/article/news"
    src = {"url": url, "relation": "report", "published_at": iso(NOW - 60)}
    f = {"headline": "Agency announces a decision", "summary": "The agency announced its decision.",
         "sources": [src], "searched": [url]}
    if change == "stale": src["published_at"] = iso(NOW - 73 * 3600)
    if change == "future": src["published_at"] = iso(NOW + 3600)
    if change == "undated": src["published_at"] = None
    if change == "unverified": f["searched"] = []
    if change == "blocked":
        src["url"] = "https://townhall.com/news/example"
        f["searched"] = [src["url"]]
    if change == "background": src["relation"] = "background"
    if change == "odds": src["relation"] = "market_coverage"
    if change == "incomplete": del f["summary"]
    st = stories.record(db, cfg, sid, "hunt", f, now=NOW)
    assert st["status"] == "unreported" and not site.is_story(st)


def test_recheck_publishes_complete_news_and_lead_only_days_are_private(env, tmp_path):
    from residual import stories, site
    cfg, db = env
    (sid,) = stories.assign(db, [story_move(db)], NOW)
    stories.record(db, cfg, sid, "hunt", {"question": "Private research question", "sources": []}, now=NOW)
    out = tmp_path / "site"
    site.build(db, cfg, out, NOW)
    assert not (out / site.day_path(db.get_story(sid)["day"]) / "index.html").exists()
    url = "https://apnews.com/article/new-report"
    st = stories.record(db, cfg, sid, "recheck", {
        "headline": "Agency releases findings", "summary": "The findings document a new development.",
        "sources": [{"url": url, "relation": "report", "published_at": iso(NOW - 30)}],
        "searched": [url]}, now=NOW)
    assert site.is_story(st) and st["next_recheck_ts"] is None
    site.build(db, cfg, out, NOW, clean=True)
    assert "Agency releases findings" in (out / "feed.xml").read_text()
    assert all("Private research question" not in p.read_text() for p in out.rglob("*.html"))
