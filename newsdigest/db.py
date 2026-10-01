"""SQLite state: seen items, digest history, small key/value state."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    key TEXT PRIMARY KEY,
    source TEXT,
    title TEXT,
    first_seen TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS digests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    n_stories INTEGER NOT NULL,
    n_good_news INTEGER,          -- NULL when the section was not due
    delivered_via TEXT
);
"""


class DB:
    def __init__(self, path: Path | str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(SCHEMA)

    def is_seen(self, key: str) -> bool:
        return self.conn.execute("SELECT 1 FROM seen WHERE key = ?", (key,)).fetchone() is not None

    def mark_seen(self, items) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self.conn.executemany(
            "INSERT OR IGNORE INTO seen (key, source, title, first_seen) VALUES (?, ?, ?, ?)",
            [(i.key, i.source, i.title, now) for i in items],
        )
        self.conn.commit()

    def record_digest(self, n_stories: int, n_good_news: int | None, delivered_via: str) -> None:
        self.conn.execute(
            "INSERT INTO digests (created_at, n_stories, n_good_news, delivered_via) VALUES (?, ?, ?, ?)",
            (datetime.now(timezone.utc).isoformat(), n_stories, n_good_news, delivered_via),
        )
        self.conn.commit()

    def last_digest_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(created_at) FROM digests").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None

    def last_good_news_at(self) -> datetime | None:
        row = self.conn.execute("SELECT MAX(created_at) FROM digests WHERE n_good_news IS NOT NULL").fetchone()
        return datetime.fromisoformat(row[0]) if row and row[0] else None
