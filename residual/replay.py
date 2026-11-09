"""Replay the frozen test set under each variant, within a dollar budget (see audit/PROTOCOL.md).

    python -m residual --data rdata replay --budget 40 [--variants A,B] [--items 26,9] [--out replay.md]

Each hunt runs against a fresh copy of the frozen database, as if at the frozen time, and is scored on
the protocol's measures. Every request goes into the ledger with the variant's name, so the audit report
and the reconciliation include the test spend. Results are appended to <data>/ledger/replay-<run>.jsonl.
"""

from __future__ import annotations

import copy
import json
import logging
import shutil
import tempfile
from pathlib import Path

from . import audit, hunter, ledger as L, stories, verdict as V
from .db import DB

log = logging.getLogger(__name__)
AUDIT = Path(__file__).resolve().parent.parent / "audit"


def _matches(text: str, groups: list[list[str]]) -> bool:
    t = (text or "").lower()
    return all(any(w in t for w in g) for g in groups)


def score(item: dict, st: dict, frozen_ts: float, ref: dict | None, ft: dict) -> dict:
    published = st["status"] in ("reported", "unclear")
    text = " ".join(st.get(k) or "" for k in ("headline", "dek", "summary"))
    head = " ".join(st.get(k) or "" for k in ("headline", "dek"))
    srcs = st.get("sources") or []
    causes = [s for s in srcs if V.counts_as(s) == "cause" and s.get("verified")]
    on_time = [s for s in causes if s.get("published_ts") and s["published_ts"] <= frozen_ts + 3600]
    match = _matches(text, ref["terms"]) if (ref and published) else None
    return {
        "published": published,
        "published_thursday_evidence": published and bool(on_time),
        "reference": bool(ref),
        "matches_reference": match,
        "wrong_story": bool(ref and published and not match),
        "unverified_citations": sum(1 for s in srcs if not s.get("verified")),
        "ft_overlap": published and any(_matches(head, g) for g in ft["subjects"].values()),
        "status": st["status"], "headline": st.get("headline"), "question": st.get("question"),
    }


def run(data: Path, cfg, client, budget: float, variants: list[str] | None = None, items: list[int] | None = None,
        audit_dir: Path = AUDIT, first_guess: float = 0.60) -> str:
    """first_guess: the cost assumed for a hunt before any has run (about Thursday's average on Opus);
    after that, the most expensive hunt so far. No hunt starts unless that much is left in the budget."""
    frozen = json.loads((audit_dir / "frozen" / "items.json").read_text())
    refs = json.loads((audit_dir / "frozen" / "reference.json").read_text())
    ft = json.loads((audit_dir / "frozen" / "benchmark_ft.json").read_text())
    allv = {k: v for k, v in json.loads((audit_dir / "variants.json").read_text()).items() if not k.startswith("_")}
    names = variants or list(allv)
    todo = [i for i in frozen["items"] if not items or i["story_id"] in items]
    prices = audit.load_prices(audit_dir / "prices.json")
    led = L.Ledger(Path(data) / "ledger", keep_days=cfg.ledger_keep_transcript_days)
    out = Path(data) / "ledger" / f"replay-{led.run}.jsonl"
    spent, costs, results, stopped = 0.0, [], [], None

    for item in todo:
        for name in names:
            guess = max(costs) if costs else first_guess  # don't start a hunt that could break the budget
            if spent + guess > budget:
                stopped = f"stopped before item {item['story_id']} / {name}: ${spent:.2f} spent of ${budget:.2f}"
                break
            v = copy.deepcopy(cfg)
            for k, val in allv[name].items():
                if k != "label":
                    setattr(v, k, val)
            before = len(L.read(led.folder))
            with tempfile.TemporaryDirectory() as tmp:
                db_path = Path(tmp) / "r.db"
                shutil.copy(audit_dir / "frozen" / "thursday.db", db_path)
                db = DB(str(db_path))
                err = None
                try:
                    f = hunter.hunt(client, v, item, led, now_ts=frozen["frozen_ts"], variant=name)
                except hunter.BillingError as e:
                    stopped = f"billing error at item {item['story_id']} / {name}: {e}"
                    break
                except Exception as e:  # one bad hunt shouldn't end the test
                    err, f = f"{type(e).__name__}: {str(e)[:200]}", None
                if f is None:
                    st = {"status": "failed" if err else "declined", "sources": []}
                else:
                    asked = [int(x) for x in f.get("merge_with") or []]
                    f["merge_with"] = []  # each hunt runs alone; see the protocol
                    st = stories.record(db, v, item["story_id"], "hunt", f, now=frozen["frozen_ts"])
                    led.outcome(item["story_id"], "hunt", st, variant=name, extra={"merge_asked": asked})
            new = L.read(led.folder)[before:]
            c = sum(sum(audit.cost(r, prices).values()) for r in new if r.get("usage"))
            spent += c
            costs.append(c)
            res = {"variant": name, "story_id": item["story_id"], "mode": item["mode"], "cost": round(c, 4),
                   "error": err, **score(item, st, frozen["frozen_ts"], refs.get(str(item["story_id"])), ft)}
            results.append(res)
            with open(out, "a") as fh:
                fh.write(json.dumps(res) + "\n")
            log.info("replay %s / %s: $%.3f %s | %s", name, item["story_id"], c, res["status"],
                     res["headline"] or res["question"] or "")
        if stopped:
            break
    return summarize(results, allv, spent, budget, stopped)


def summarize(results: list[dict], allv: dict, spent: float, budget: float, stopped: str | None) -> str:
    lines = ["# Replay results", "", f"Spent ${spent:.2f} of ${budget:.2f}." + (f" {stopped}." if stopped else ""), "",
             "| Variant | Hunts | $ | $/hunt | Published | On Thursday's evidence | Ref. matches | Wrong story | "
             "Unverified cites | FT overlap | $/ref. match |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    by = {}
    for r in results:
        by.setdefault(r["variant"], []).append(r)
    stats = {}
    for name in allv:
        g = by.get(name) or []
        if not g:
            continue
        s = {"n": len(g), "cost": sum(r["cost"] for r in g), "pub": sum(r["published"] for r in g),
             "thu": sum(r["published_thursday_evidence"] for r in g), "ref": sum(bool(r["matches_reference"]) for r in g),
             "wrong": sum(r["wrong_story"] for r in g), "unv": sum(r["unverified_citations"] for r in g),
             "ft": sum(bool(r["ft_overlap"]) for r in g)}
        stats[name] = s
        lines.append(f"| {name}: {allv[name]['label']} | {s['n']} | {s['cost']:.2f} | {s['cost'] / s['n']:.3f} | {s['pub']} | "
                     f"{s['thu']} | {s['ref']} | {s['wrong']} | {s['unv']} | {s['ft']} | "
                     f"{s['cost'] / s['ref'] if s['ref'] else float('nan'):.3f} |")
    a = stats.get("A")
    if a and stats.get("A2"):
        noise = abs(a["ref"] - stats["A2"]["ref"])
        ok = [n for n, s in stats.items() if n not in ("A", "A2") and s["n"] == a["n"]
              and s["ref"] >= a["ref"] - min(noise, 1) and s["wrong"] <= a["wrong"]
              and s["unv"] <= a["unv"] + max(1, a["n"] // 20)]
        lines += ["", f"Noise (A against A2): {noise} reference match(es).",
                  "Acceptable under the protocol's rule: " + (", ".join(ok) if ok else "none") + "."]
        if ok:
            best = min(ok, key=lambda n: stats[n]["cost"] / max(stats[n]["ref"], 1))
            saving = 1 - (stats[best]["cost"] / max(stats[best]["ref"], 1)) / (a["cost"] / max(a["ref"], 1))
            lines.append(f"Cheapest acceptable by cost per reference match: {best} ({allv[best]['label']}), "
                         f"{saving:.0%} cheaper than A per match.")
    return "\n".join(lines)
