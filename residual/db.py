"""SQLite storage: price snapshots, market metadata, flagged moves, investigations."""

from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS markets (
    platform TEXT, market_id TEXT, question TEXT, url TEXT, event_id TEXT, event_title TEXT,
    category TEXT, end_ts REAL, rules TEXT, extra TEXT, updated_ts REAL,
    PRIMARY KEY (platform, market_id)
);
CREATE TABLE IF NOT EXISTS snapshots (
    platform TEXT, market_id TEXT, ts REAL, price REAL, volume24h REAL, liquidity REAL
);
CREATE INDEX IF NOT EXISTS snap_idx ON snapshots (platform, market_id, ts);
CREATE TABLE IF NOT EXISTS moves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT, market_id TEXT, question TEXT, url TEXT,
    detected_ts REAL, move_start_ts REAL, price_from REAL, price_to REAL,
    signals TEXT, series TEXT,
    status TEXT DEFAULT 'new',          -- new | investigated | error
    verdict TEXT,                       -- explained | unexplained | unclear
    confirmed_ts REAL,                  -- first report found later, for unexplained moves
    lead_minutes REAL,
    rechecks_done INTEGER DEFAULT 0,
    next_recheck_ts REAL,
    brief TEXT
);
CREATE INDEX IF NOT EXISTS moves_mkt ON moves (platform, market_id, detected_ts);
CREATE TABLE IF NOT EXISTS investigations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    move_id INTEGER, stage TEXT, ran_ts REAL, model TEXT,
    findings TEXT, searched TEXT, usage TEXT
);
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT,                           -- Eastern date the story was first flagged, YYYY-MM-DD
    created_ts REAL, updated_ts REAL,
    score REAL, raw REAL,               -- importance index (0-100) and the underlying dollar-points
    money_usd REAL, markets INTEGER, max_points REAL,
    status TEXT DEFAULT 'pending',      -- pending | reported | unreported | quiet
    headline TEXT, dek TEXT, summary TEXT, why_moved TEXT,
    sources TEXT, check_next TEXT,
    hunted_ts REAL, hunted_score REAL,
    confirmed_ts REAL, lead_minutes REAL,
    rechecks_done INTEGER DEFAULT 0, next_recheck_ts REAL
);
"""


class DB:
    def __init__(self, path: str):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r["name"] for r in self.conn.execute("PRAGMA table_info(moves)")}
        if "story_id" not in cols:  # databases from before stories existed
            self.conn.execute("ALTER TABLE moves ADD COLUMN story_id INTEGER")
            self.conn.commit()

    # markets and snapshots
    def upsert_markets(self, rows: list[dict]):
        now = time.time()
        self.conn.executemany(
            """INSERT INTO markets VALUES (:platform,:market_id,:question,:url,:event_id,:event_title,
                   :category,:end_ts,:rules,:extra,:updated_ts)
               ON CONFLICT(platform, market_id) DO UPDATE SET question=excluded.question, url=excluded.url,
                   event_id=excluded.event_id, event_title=excluded.event_title, category=excluded.category,
                   end_ts=excluded.end_ts, rules=excluded.rules, extra=excluded.extra, updated_ts=excluded.updated_ts""",
            [{**r, "extra": json.dumps(r.get("extra") or {}), "updated_ts": now} for r in rows],
        )
        self.conn.commit()

    def add_snapshots(self, rows: list[dict], ts: float):
        self.conn.executemany(
            "INSERT INTO snapshots VALUES (?,?,?,?,?,?)",
            [(r["platform"], r["market_id"], ts, r["price"], r.get("volume24h"), r.get("liquidity")) for r in rows],
        )
        self.conn.commit()

    def prune_snapshots(self, older_than_s: float = 2 * 86400):
        self.conn.execute("DELETE FROM snapshots WHERE ts < ?", (time.time() - older_than_s,))
        self.conn.commit()

    def series(self, platform: str, market_id: str, since_ts: float) -> list[tuple[float, float]]:
        cur = self.conn.execute(
            "SELECT ts, price FROM snapshots WHERE platform=? AND market_id=? AND ts>=? ORDER BY ts",
            (platform, market_id, since_ts),
        )
        return [(r["ts"], r["price"]) for r in cur]

    def window_series_all(self, platform: str, since_ts: float) -> dict[str, list[tuple[float, float]]]:
        cur = self.conn.execute(
            "SELECT market_id, ts, price FROM snapshots WHERE platform=? AND ts>=? ORDER BY ts",
            (platform, since_ts),
        )
        out: dict[str, list[tuple[float, float]]] = {}
        for r in cur:
            out.setdefault(r["market_id"], []).append((r["ts"], r["price"]))
        return out

    def market(self, platform: str, market_id: str):
        r = self.conn.execute("SELECT * FROM markets WHERE platform=? AND market_id=?", (platform, market_id)).fetchone()
        if not r:
            return None
        d = dict(r)
        d["extra"] = json.loads(d["extra"] or "{}")
        return d

    def event_siblings(self, platform: str, event_id: str, exclude: str) -> list[dict]:
        cur = self.conn.execute(
            "SELECT market_id, question FROM markets WHERE platform=? AND event_id=? AND market_id<>?",
            (platform, event_id, exclude),
        )
        return [dict(r) for r in cur]

    # moves
    def last_move(self, platform: str, market_id: str):
        return self.conn.execute(
            "SELECT * FROM moves WHERE platform=? AND market_id=? ORDER BY detected_ts DESC LIMIT 1",
            (platform, market_id),
        ).fetchone()

    def add_move(self, m: dict) -> int:
        cur = self.conn.execute(
            """INSERT INTO moves (platform, market_id, question, url, detected_ts, move_start_ts,
                   price_from, price_to, signals, series) VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (m["platform"], m["market_id"], m["question"], m["url"], m["detected_ts"], m["move_start_ts"],
             m["price_from"], m["price_to"], json.dumps(m["signals"]), json.dumps(m["series"])),
        )
        self.conn.commit()
        return cur.lastrowid

    def update_move(self, move_id: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in fields.values()]
        self.conn.execute(f"UPDATE moves SET {cols} WHERE id=?", (*vals, move_id))
        self.conn.commit()

    def get_move(self, move_id: int) -> dict | None:
        r = self.conn.execute("SELECT * FROM moves WHERE id=?", (move_id,)).fetchone()
        return _move(r) if r else None

    def moves(self, where: str = "1=1", params: tuple = (), limit: int = 500) -> list[dict]:
        cur = self.conn.execute(f"SELECT * FROM moves WHERE {where} ORDER BY detected_ts DESC LIMIT ?", (*params, limit))
        return [_move(r) for r in cur]

    def add_investigation(self, move_id: int, stage: str, model: str, findings: dict, searched: list, usage: dict):
        self.conn.execute(
            "INSERT INTO investigations (move_id, stage, ran_ts, model, findings, searched, usage) VALUES (?,?,?,?,?,?,?)",
            (move_id, stage, time.time(), model, json.dumps(findings), json.dumps(searched), json.dumps(usage)),
        )
        self.conn.commit()

    def investigations(self, move_id: int) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM investigations WHERE move_id=? ORDER BY ran_ts", (move_id,))
        out = []
        for r in cur:
            d = dict(r)
            for k in ("findings", "searched", "usage"):
                d[k] = json.loads(d[k] or "null")
            out.append(d)
        return out

    def investigations_since(self, ts: float) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM investigations WHERE ran_ts>=?", (ts,)).fetchone()[0]


def _move(r) -> dict:
    d = dict(r)
    for k in ("signals", "series", "brief"):
        d[k] = json.loads(d[k]) if d.get(k) else None
    return d


# ---------- stories ----------

STORY_JSON = ("sources", "check_next")


def _story(r) -> dict:
    d = dict(r)
    for k in STORY_JSON:
        d[k] = json.loads(d[k]) if d.get(k) else []
    return d


def _story_methods():
    def add_story(self, s: dict) -> int:
        cols = list(s)
        cur = self.conn.execute(
            f"INSERT INTO stories ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            [json.dumps(v) if k in STORY_JSON else v for k, v in s.items()])
        self.conn.commit()
        return cur.lastrowid

    def update_story(self, story_id: int, **fields):
        if not fields:
            return
        cols = ", ".join(f"{k}=?" for k in fields)
        vals = [json.dumps(v) if k in STORY_JSON else v for k, v in fields.items()]
        self.conn.execute(f"UPDATE stories SET {cols} WHERE id=?", (*vals, story_id))
        self.conn.commit()

    def get_story(self, story_id: int):
        r = self.conn.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
        return _story(r) if r else None

    def stories(self, where: str = "1=1", params: tuple = (), order: str = "score DESC", limit: int = 500):
        cur = self.conn.execute(f"SELECT * FROM stories WHERE {where} ORDER BY {order} LIMIT ?", (*params, limit))
        return [_story(r) for r in cur]

    def story_moves(self, story_id: int) -> list[dict]:
        cur = self.conn.execute("SELECT * FROM moves WHERE story_id=? ORDER BY detected_ts", (story_id,))
        return [_move(r) for r in cur]

    def delete_story(self, story_id: int):
        self.conn.execute("DELETE FROM stories WHERE id=?", (story_id,))
        self.conn.commit()

    for f in (add_story, update_story, get_story, stories, story_moves, delete_story):
        setattr(DB, f.__name__, f)


_story_methods()
