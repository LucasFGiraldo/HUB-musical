"""Histórico de músicas tocadas (SQLite em data/hub.db)."""

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone

from . import config

DB_FILE = config.DATA_DIR / "hub.db"


def _connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init() -> None:
    with closing(_connect()) as conn, conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS plays (
                id INTEGER PRIMARY KEY,
                played_at TEXT NOT NULL,
                source TEXT NOT NULL,
                track_id TEXT,
                title TEXT NOT NULL,
                artists TEXT NOT NULL,
                album TEXT,
                cover TEXT,
                duration_ms INTEGER
            )""")


def add_play(track: dict) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO plays (played_at, source, track_id, title, artists, album, cover, duration_ms)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                track["source"],
                track.get("id"),
                track["title"],
                ", ".join(track["artists"]),
                track.get("album"),
                track.get("cover"),
                track.get("duration_ms"),
            ),
        )


def last_track_id() -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT track_id FROM plays ORDER BY id DESC LIMIT 1").fetchone()
    return row["track_id"] if row else None


def recent(limit: int = 50) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT played_at, source, title, artists, album, cover FROM plays ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def stats(days: int = 30, top: int = 5) -> dict:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    with closing(_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        rows = conn.execute(
            "SELECT artists, COUNT(*) AS plays FROM plays WHERE played_at >= ?"
            " GROUP BY artists ORDER BY plays DESC LIMIT ?",
            (since, top),
        ).fetchall()
    return {"total_plays": total, "days": days, "top_artists": [dict(r) for r in rows]}
