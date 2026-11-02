"""Consulta o Spotify em segundo plano, guarda o último estado e registra o histórico."""

import asyncio
import logging
import time

import httpx

from . import db, spotify

POLL_SECONDS = 5
log = logging.getLogger("hub.poller")

state = {
    "now": {"authorized": False},
    "fetched_at": 0.0,
    "last_ok": None,
    "last_error": None,
    "started_at": time.time(),
}


def current() -> dict:
    """Último estado, com o progresso avançado até agora."""
    now = dict(state["now"])
    if now.get("playing") and now.get("progress_ms") is not None:
        elapsed = int((time.time() - state["fetched_at"]) * 1000)
        now["progress_ms"] = min(now["duration_ms"], now["progress_ms"] + elapsed)
    return now


async def run() -> None:
    last_id = await asyncio.to_thread(db.last_track_id)
    while True:
        delay = POLL_SECONDS
        try:
            now = await asyncio.to_thread(spotify.now_playing)
            state.update(now=now, fetched_at=time.time(), last_ok=time.time(), last_error=None)
            if now.get("playing") and now.get("id") and now["id"] != last_id:
                await asyncio.to_thread(db.add_play, now)
                last_id = now["id"]
        except httpx.HTTPStatusError as exc:
            state["last_error"] = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 429:
                delay = int(exc.response.headers.get("Retry-After", 30))
        except Exception as exc:  # o laço não pode morrer
            log.exception("falha ao consultar o Spotify")
            state["last_error"] = str(exc)
        await asyncio.sleep(delay)
