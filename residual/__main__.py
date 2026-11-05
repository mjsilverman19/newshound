"""Command line.

Continuous mode (needs ANTHROPIC_API_KEY for investigations):
  python -m residual run                     poll, flag and investigate on a loop
  python -m residual run --no-investigate    just watch and flag
  python -m residual once                    one cycle, then exit
  python -m residual investigate 12          investigate one move by id

Scheduled mode (Claude in a scheduled session does the investigating; see AGENT.md):
  python -m residual --data DIR scan                    compare prices with the last run, flag moves
  python -m residual --data DIR queue --out queue.json  stories to hunt or re-check now
  python -m residual --data DIR record 7 findings.json --kind hunt|recheck
  python -m residual --data DIR unlabeled               small stories that still need a topic label
  python -m residual --data DIR label 9 "Nigeria's 2027 race"
  python -m residual image https://example.com/article  a news article's lead image and credit

Scheduled mode on the Claude API (needs ANTHROPIC_API_KEY; see .github/workflows/hourly.yml):
  python -m residual --data DIR cycle [--summary FILE]  scan, label, hunt and record in one go

Both:
  python -m residual site --out DIR [--clean]    build the public site (GitHub Pages)
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from . import detect, pipeline, scheduled, site, stories
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
    p.add_argument("story_id", type=int)
    p.add_argument("findings", help="JSON file: see AGENT.md")
    p.add_argument("--kind", choices=["hunt", "recheck"], required=True)
    sub.add_parser("cycle", help="scan, label, hunt and record using the Claude API").add_argument(
        "--summary", help="also write a Markdown summary of the run to this file")
    sub.add_parser("unlabeled")
    p = sub.add_parser("label")
    p.add_argument("story_id", type=int)
    p.add_argument("topic")
    sub.add_parser("image").add_argument("url")
    p = sub.add_parser("site")
    p.add_argument("--out", default="site")
    p.add_argument("--clean", action="store_true", help="remove everything in --out except .git and CNAME first")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    cfg = Config.load(args.config)
    if args.cmd == "image":
        from .images import lead_image
        try:
            print(json.dumps(lead_image(args.url), indent=2))
        except Exception as ex:  # blocked, timed out, not HTML: the agent moves on to another source
            print(json.dumps({"source_url": args.url, "url": None, "error": str(ex)[:200]}, indent=2))
        return
    if args.data:
        d = Path(args.data)
        d.mkdir(parents=True, exist_ok=True)
        cfg.db_path, cfg.briefs_dir, cfg.state_path = str(d / "residual.db"), str(d / "briefs"), str(d / "prices.json.gz")
    db = DB(cfg.db_path)

    if args.cmd == "site":
        files = site.build(db, cfg, args.out, clean=args.clean)
        print(f"wrote {len(files)} files to {args.out}")
        return
    if args.cmd == "unlabeled":
        print(json.dumps(stories.unlabeled(db, cfg), indent=2))
        return
    if args.cmd == "label":
        st = stories.label(db, args.story_id, args.topic)
        print(f"story {st['id']}: {st['topic']}")
        return
    if args.cmd == "scan":
        ids = scheduled.scan(db, cfg, Path(cfg.state_path))
        print(f"{len(ids)} new moves flagged")
        return
    if args.cmd == "queue":
        q = scheduled.queue(db, cfg)
        Path(args.out).write_text(json.dumps(q, indent=2))
        print(f"{len(q)} stories ({sum(i['kind'] == 'hunt' for i in q)} to hunt, "
              f"{sum(i['kind'] == 'recheck' for i in q)} re-checks) written to {args.out}")
        return
    if args.cmd == "cycle":
        from . import hunter
        from .investigate import make_client
        try:
            client = make_client()
        except Exception as e:
            sys.exit(f"Could not start the Claude client ({e}). Set ANTHROPIC_API_KEY.")
        report = hunter.cycle(db, cfg, client)
        text = hunter.summary(db, cfg, report)
        if args.summary:
            Path(args.summary).write_text(text + "\n")
        print(text)
        return
    if args.cmd == "record":
        findings = json.loads(Path(args.findings).read_text())
        st = scheduled.record(db, cfg, args.story_id, args.kind, findings)
        extra = f", first reported {st['lead_minutes']:.0f} min after the markets moved" if st.get("confirmed_ts") else ""
        print(f"story {st['id']} ({st['score']:.0f}): {st['status']}{extra} | {st['headline'] or st['question']}"
              f"{' | /' + st['day'].replace('-', '/') + '/' + st['slug'] + '/' if st.get('slug') else ''}"
              f"{' | image' if st.get('image') else ''}")
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
