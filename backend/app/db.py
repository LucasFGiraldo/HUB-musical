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
                duration_ms INTEGER,
                device TEXT
            )""")
        if "device" not in {r["name"] for r in conn.execute("PRAGMA table_info(plays)")}:
            conn.execute("ALTER TABLE plays ADD COLUMN device TEXT")
        # Letras já buscadas; synced é NULL quando o LRCLIB não tem a música
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lyrics (
                track_id TEXT PRIMARY KEY,
                synced TEXT,
                fetched_at TEXT NOT NULL
            )""")


def add_play(track: dict) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT INTO plays (played_at, source, track_id, title, artists, album, cover, duration_ms, device)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                datetime.now(timezone.utc).isoformat(timespec="seconds"),
                track["source"],
                track.get("id"),
                track["title"],
                ", ".join(track["artists"]),
                track.get("album"),
                track.get("cover"),
                track.get("duration_ms"),
                track.get("device"),
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


def recent_unique(limit: int = 5) -> list[dict]:
    """Últimas músicas sem repetição (a mais recente de cada título+artista), só as que têm capa."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT played_at, source, title, artists, album, cover FROM plays WHERE id IN ("
            "  SELECT MAX(id) FROM plays WHERE cover IS NOT NULL GROUP BY lower(title), lower(artists)"
            ") ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_lyrics(track_id: str) -> tuple[bool, str | None]:
    """(já buscada?, letra sincronizada em JSON ou None)."""
    with closing(_connect()) as conn:
        row = conn.execute("SELECT synced FROM lyrics WHERE track_id = ?", (track_id,)).fetchone()
    return (row is not None), (row["synced"] if row else None)


def save_lyrics(track_id: str, synced: str | None) -> None:
    with closing(_connect()) as conn, conn:
        conn.execute(
            "INSERT OR REPLACE INTO lyrics (track_id, synced, fetched_at) VALUES (?, ?, ?)",
            (track_id, synced, datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )


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
