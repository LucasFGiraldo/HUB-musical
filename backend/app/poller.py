"""Consulta o Spotify em segundo plano, guarda o último estado, registra o histórico
e avisa as telas conectadas (SSE) quando algo muda."""

import asyncio
import logging
import time

import httpx

from . import db, spotify

POLL_SECONDS = 1
SEEK_TOLERANCE_MS = 2000  # diferença de progresso que indica um pulo na música
log = logging.getLogger("hub.poller")

state = {
    "now": {"authorized": False},
    "fetched_at": 0.0,
    "last_ok": None,
    "last_error": None,
    "started_at": time.time(),
}

_subscribers: set[asyncio.Queue] = set()


def current() -> dict:
    """Último estado, com o progresso avançado até agora."""
    now = dict(state["now"])
    if now.get("playing") and now.get("progress_ms") is not None:
        elapsed = int((time.time() - state["fetched_at"]) * 1000)
        now["progress_ms"] = min(now["duration_ms"], now["progress_ms"] + elapsed)
    return now


def subscribe() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=10)
    _subscribers.add(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    _subscribers.discard(q)


def _publish(event: str, data: dict) -> None:
    for q in list(_subscribers):
        if q.full():  # tela lenta: descarta o evento mais antigo
            q.get_nowait()
        q.put_nowait((event, data))


def _changed(old: dict, new: dict, elapsed_ms: int) -> bool:
    keys = ("authorized", "id", "playing")
    if any(old.get(k) != new.get(k) for k in keys):
        return True
    if new.get("progress_ms") is None or old.get("progress_ms") is None:
        return False
    expected = old["progress_ms"] + (elapsed_ms if old.get("playing") else 0)
    return abs(new["progress_ms"] - expected) > SEEK_TOLERANCE_MS


async def run() -> None:
    last_id = await asyncio.to_thread(db.last_track_id)
    while True:
        delay = POLL_SECONDS
        try:
            now = await asyncio.to_thread(spotify.now_playing)
            t = time.time()
            old, old_t = state["now"], state["fetched_at"]
            state.update(now=now, fetched_at=t, last_ok=t, last_error=None)
            if _changed(old, now, int((t - old_t) * 1000)):
                _publish("now", current())
            if now.get("playing") and now.get("id") and now["id"] != last_id:
                await asyncio.to_thread(db.add_play, now)
                last_id = now["id"]
                _publish("history", {})
        except httpx.HTTPStatusError as exc:
            state["last_error"] = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 429:
                delay = int(exc.response.headers.get("Retry-After", 30))
                log.warning("Spotify pediu para esperar %s s", delay)
        except Exception as exc:  # o laço não pode morrer
            log.exception("falha ao consultar o Spotify")
            state["last_error"] = str(exc)
        await asyncio.sleep(delay)
