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
        conn.execute("CREATE INDEX IF NOT EXISTS plays_track_time ON plays (track_id, played_at)")
        # Letras já buscadas; synced é NULL quando o LRCLIB não tem a música
        conn.execute("""
            CREATE TABLE IF NOT EXISTS lyrics (
                track_id TEXT PRIMARY KEY,
                synced TEXT,
                fetched_at TEXT NOT NULL
            )""")


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def add_play(track: dict, played_at: datetime | None = None, conn: sqlite3.Connection | None = None) -> None:
    if conn is None:
        with closing(_connect()) as conn, conn:
            return add_play(track, played_at, conn)
    conn.execute(
        "INSERT INTO plays (played_at, source, track_id, title, artists, album, cover, duration_ms, device)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            _iso(played_at or datetime.now(timezone.utc)),
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


def add_recent(tracks: list[dict]) -> int:
    """Histórico do Spotify (o que tocou fora de casa ou escapou da consulta ao vivo). Pula as faixas que
    já estão registradas perto do mesmo horário; devolve quantas entraram."""
    added = 0
    with closing(_connect()) as conn, conn:
        for t in tracks:
            at = datetime.fromisoformat(t["played_at"].replace("Z", "+00:00"))
            # O Spotify marca o horário perto do fim da faixa; a consulta ao vivo registra no início
            margin = timedelta(milliseconds=t.get("duration_ms") or 0) + timedelta(minutes=3)
            if conn.execute("SELECT 1 FROM plays WHERE track_id = ? AND played_at BETWEEN ? AND ?",
                            (t["id"], _iso(at - margin), _iso(at + timedelta(minutes=3)))).fetchone():
                continue
            add_play(t, at, conn)
            added += 1
    return added


def last_track_id() -> str | None:
    with closing(_connect()) as conn:
        row = conn.execute("SELECT track_id FROM plays ORDER BY played_at DESC, id DESC LIMIT 1").fetchone()
    return row["track_id"] if row else None


def recent(limit: int = 50) -> list[dict]:
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT played_at, source, title, artists, album, cover, device FROM plays"
            " ORDER BY played_at DESC, id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def devices() -> list[str]:
    """Aparelhos do Spotify que já tocaram algo."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT device FROM plays WHERE device IS NOT NULL GROUP BY device ORDER BY MAX(id) DESC"
        ).fetchall()
    return [r["device"] for r in rows]


def recent_unique(limit: int = 5) -> list[dict]:
    """Últimas músicas sem repetição (a mais recente de cada título+artista), só as que têm capa."""
    with closing(_connect()) as conn:
        rows = conn.execute(
            "SELECT MAX(played_at) AS played_at, source, title, artists, album, cover FROM plays"
            " WHERE cover IS NOT NULL GROUP BY lower(title), lower(artists) ORDER BY played_at DESC LIMIT ?",
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


def stats(days: int = 30, top: int = 6) -> dict:
    """Resumo do período: totais, por fonte, por dia (horário local do servidor), artistas e músicas."""
    since_dt = datetime.now(timezone.utc) - timedelta(days=days)
    since = since_dt.isoformat(timespec="seconds")
    with closing(_connect()) as conn:
        total = conn.execute("SELECT COUNT(*) FROM plays").fetchone()[0]
        period = conn.execute("SELECT COUNT(*) FROM plays WHERE played_at >= ?", (since,)).fetchone()[0]
        by_source = {r["source"]: r["n"] for r in conn.execute(
            "SELECT source, COUNT(*) AS n FROM plays WHERE played_at >= ? GROUP BY source", (since,))}
        per_day = {r["day"]: r["n"] for r in conn.execute(
            "SELECT date(played_at, 'localtime') AS day, COUNT(*) AS n FROM plays"
            " WHERE played_at >= ? GROUP BY day", (since,))}
        per_hour = {r["hour"]: r["n"] for r in conn.execute(
            "SELECT CAST(strftime('%H', played_at, 'localtime') AS INTEGER) AS hour, COUNT(*) AS n FROM plays"
            " WHERE played_at >= ? GROUP BY hour", (since,))}
        artists = conn.execute(
            "SELECT artists, COUNT(*) AS plays FROM plays WHERE played_at >= ?"
            " GROUP BY lower(artists) ORDER BY plays DESC, MAX(id) DESC LIMIT ?", (since, top)).fetchall()
        tracks = conn.execute(
            "SELECT title, artists, cover, COUNT(*) AS plays FROM plays WHERE played_at >= ?"
            " GROUP BY lower(title), lower(artists) ORDER BY plays DESC, MAX(id) DESC LIMIT ?",
            (since, top)).fetchall()
    today = datetime.now().date()  # o servidor roda no fuso de Brasília
    days_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    return {
        "days": days,
        "total_plays": total,
        "period_plays": period,
        "by_source": {"spotify": by_source.get("spotify", 0), "mic": by_source.get("mic", 0)},
        "per_day": [{"day": d, "plays": per_day.get(d, 0)} for d in days_list],
        "per_hour": [{"hour": h, "plays": per_hour.get(h, 0)} for h in range(24)],
        "top_artists": [dict(r) for r in artists],
        "top_tracks": [dict(r) for r in tracks],
    }
