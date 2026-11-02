"""Letras sincronizadas (LRC) do LRCLIB, com cache no SQLite.
Só música do Spotify: o Shazam não informa o ponto da faixa, então não há como sincronizar."""

import json
import logging
import re

import httpx

from . import db

LRCLIB = "https://lrclib.net/api"
HEADERS = {"User-Agent": "HUB-Musical (https://github.com/LucasFGiraldo/HUB-musical)"}
_client = httpx.Client(timeout=8, headers=HEADERS)
_LINE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]\s*(.*)")
log = logging.getLogger("hub.lyrics")


class Unavailable(Exception):
    """O LRCLIB não respondeu agora; tentar de novo depois (não vai para o cache)."""


def _parse(lrc: str) -> list[dict]:
    lines = []
    for raw in lrc.splitlines():
        m = _LINE.match(raw.strip())
        if m:
            lines.append({"t": int((int(m[1]) * 60 + float(m[2])) * 1000), "text": m[3].strip()})
    return sorted(lines, key=lambda l: l["t"])


def _clean_title(title: str) -> str:
    """Tira sufixos como "- Ao Vivo", "- Remastered 2011" e "(feat. ...)"."""
    title = re.split(r"\s+-\s+", title)[0]
    return re.sub(r"\s*[\(\[][^)\]]*[\)\]]\s*$", "", title).strip()


def _get(path: str, params: dict):
    try:
        resp = _client.get(f"{LRCLIB}/{path}", params=params)
    except httpx.HTTPError as exc:
        raise Unavailable(str(exc))
    if resp.status_code == 404:
        return None
    if resp.status_code != 200:
        raise Unavailable(f"HTTP {resp.status_code}")
    data = resp.json()
    return None if isinstance(data, dict) and data.get("statusCode") else data


def _lookup(track: dict) -> str | None:
    artist = track["artists"][0]
    duration = round((track.get("duration_ms") or 0) / 1000)
    for title in dict.fromkeys([track["title"], _clean_title(track["title"])]):
        base = {"artist_name": artist, "track_name": title}
        found = _get("get", {**base, "album_name": track.get("album") or "", "duration": duration})
        if found and found.get("syncedLyrics"):
            return found["syncedLyrics"]
        results = _get("search", base) or []
        # Prefere a gravação com duração parecida (evita versões ao vivo/remix com letra deslocada)
        results = [r for r in results if r.get("syncedLyrics")
                   and abs((r.get("duration") or 0) - duration) <= 3]
        if results:
            return results[0]["syncedLyrics"]
    return None


def for_track(track: dict) -> list[dict] | None:
    """Linhas [{t, text}] ou None se não houver letra sincronizada. Lança Unavailable se o serviço falhar."""
    cached, stored = db.get_lyrics(track["id"])
    if cached:
        return json.loads(stored) if stored else None
    lrc = _lookup(track)
    lines = _parse(lrc) if lrc else []
    db.save_lyrics(track["id"], json.dumps(lines) if lines else None)
    return lines or None
