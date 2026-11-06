"""Build the frozen test set from a snapshot of Thursday's data (run once; the output is committed).

    python3 audit/freeze.py <snapshot residual.db> audit/frozen

Copies the snapshot, blanks every hunt result for Thursday's stories (so neither the scent nor the list
of other stories gives away an answer), and writes the queue items as the hunter would have received
them, with the time they were frozen at.
"""

import json
import shutil
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from residual import stories  # noqa: E402
from residual.config import Config  # noqa: E402
from residual.db import DB  # noqa: E402

DAY = "2026-11-05"
DAY_ITEMS = [26, 9, 8, 27, 16, 28, 29, 30, 31, 33, 34, 35, 24, 36, 38, 40]   # day-mode topics, ranks 1-20
HOURLY_ITEMS = [6, 11, 20, 21]                                             # hourly-era stories hunted Thursday
BLANK = dict(status="pending", headline=None, dek=None, summary=None, why_moved=None, why_it_matters=None,
             dateline=None, slug=None, question=None, trail_note=None, topic=None, image=None, sources="[]",
             check_next="[]", checked_domains="[]", aliases="[]", hunted_ts=None, hunted_score=None,
             confirmed_ts=None, lead_minutes=None, rechecks_done=0, next_recheck_ts=None)


def main(src, out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    db_path = out / "thursday.db"
    shutil.copy(src, db_path)
    c = sqlite3.connect(db_path)
    sets = ", ".join(f"{k}=?" for k in BLANK)
    c.execute(f"UPDATE stories SET {sets} WHERE day=?", (*BLANK.values(), DAY))
    c.execute("DELETE FROM investigations")
    c.commit()
    c.execute("VACUUM")
    c.close()

    db = DB(str(db_path))
    todays = db.stories("day=?", (DAY,))
    frozen_ts = max((s.get("scent_data") or {}).get("updated_ts") or 0 for s in todays)
    others = stories.others_today(db, todays)
    items = []
    for sid in DAY_ITEMS + HOURLY_ITEMS:
        s = db.get_story(sid)
        moves = db.story_moves(sid)
        sd = s.get("scent_data") or {}
        from residual import day
        items.append({
            "story_id": sid, "kind": "hunt", "score": s["score"],
            "mode": "day" if sd else "hourly", "strength": sd.get("strength"),
            "move_start_utc": _utc(min(m["move_start_ts"] for m in moves)),
            "scent": day.scent(db, s) if sd else stories.scent(db, s),
            "other_stories_today": [o for o in others if o["story_id"] != sid],
        })
    (out / "items.json").write_text(json.dumps({"frozen_ts": frozen_ts, "frozen_utc": _utc(frozen_ts),
                                                "day": DAY, "items": items}, indent=2))
    print(f"{len(items)} items frozen at {_utc(frozen_ts)}; db {db_path.stat().st_size:,} bytes")


def _utc(ts):
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


if __name__ == "__main__":
    Config()  # fail early if the package doesn't import
    main(sys.argv[1], sys.argv[2])
