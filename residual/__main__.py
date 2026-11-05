"""Command line.

Continuous mode (needs ANTHROPIC_API_KEY for investigations):
  python -m residual run                     poll, flag and investigate on a loop
  python -m residual run --no-investigate    just watch and flag
  python -m residual once                    one cycle, then exit
  python -m residual investigate 12          investigate one move by id

Scheduled mode (Claude in a scheduled session does the investigating; see AGENT.md):
  python -m residual --data DIR scan                    compare prices with the last run, flag moves
  python -m residual --data DIR queue --out queue.json  moves to investigate or re-check now
  python -m residual --data DIR record 12 findings.json --kind investigate|recheck

Both:
  python -m residual report --out report.html
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import brief, detect, pipeline, scheduled
from .config import Config
from .db import DB


def main(argv=None):
    ap = argparse.ArgumentParser(prog="residual", description="Prediction-market moves that nothing public explains.")
    ap.add_argument("--config", help="TOML file overriding the defaults in config.py")
    ap.add_argument("--data", help="folder for the database, briefs and saved prices (overrides those paths)")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("run", "once"):
        p = sub.add_parser(name)
        p.add_argument("--no-investigate", action="store_true", help="flag moves but do not call Claude")
    sub.add_parser("investigate").add_argument("move_id", type=int)
    sub.add_parser("scan")
    sub.add_parser("queue").add_argument("--out", default="queue.json")
    p = sub.add_parser("record")
    p.add_argument("move_id", type=int)
    p.add_argument("findings", help="JSON file: sources, assessment, check_next, searched")
    p.add_argument("--kind", choices=["investigate", "recheck"], required=True)
    sub.add_parser("report").add_argument("--out", default="report.html")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = Config.load(args.config)
    if args.data:
        d = Path(args.data)
        d.mkdir(parents=True, exist_ok=True)
        cfg.db_path, cfg.briefs_dir, cfg.state_path = str(d / "residual.db"), str(d / "briefs"), str(d / "prices.json.gz")
    db = DB(cfg.db_path)

    if args.cmd == "report":
        Path(args.out).write_text(brief.report_html(db))
        print(f"wrote {args.out}")
        return
    if args.cmd == "scan":
        ids = scheduled.scan(db, cfg, Path(cfg.state_path))
        print(f"{len(ids)} new moves flagged")
        return
    if args.cmd == "queue":
        q = scheduled.queue(db, cfg)
        Path(args.out).write_text(json.dumps(q, indent=2))
        print(f"{len(q)} items ({sum(i['kind'] == 'investigate' for i in q)} new, "
              f"{sum(i['kind'] == 'recheck' for i in q)} re-checks) written to {args.out}")
        return
    if args.cmd == "record":
        findings = json.loads(Path(args.findings).read_text())
        mv = scheduled.record(db, cfg, args.move_id, args.kind, findings)
        extra = f", confirmed {mv['lead_minutes']:.0f} min after the move" if mv.get("confirmed_ts") else ""
        print(f"move {mv['id']}: {mv['verdict']}{extra} | {mv['question']} | {mv['url']}")
        return

    client = None
    if args.cmd == "investigate" or not getattr(args, "no_investigate", False):
        try:
            from .investigate import make_client
            client = make_client()
        except Exception as e:
            sys.exit(f"Could not start the Claude client ({e}). Set ANTHROPIC_API_KEY, "
                     "or use --no-investigate to only watch markets.")

    if args.cmd == "investigate":
        mv = db.get_move(args.move_id)
        if not mv:
            sys.exit(f"no move {args.move_id}")
        done = pipeline.investigate_move(db, client, cfg, mv)
        print(f"{done['verdict']}: {done['brief']['path']}")
        return

    while True:
        started = time.time()
        detect.poll(db, cfg)
        if client:
            pipeline.work(db, client, cfg)
        if args.cmd == "once":
            break
        time.sleep(max(5, cfg.poll_seconds - (time.time() - started)))


if __name__ == "__main__":
    main()
