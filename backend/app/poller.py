"""Consulta o Spotify em segundo plano, guarda o último estado, registra o histórico
e avisa as telas conectadas (SSE) quando algo muda."""

import asyncio
import json
import logging
import time

import httpx

from . import config, db, settings, spotify

# O Spotify limita chamadas de apps em modo de desenvolvimento e, se passar do limite, bloqueia
# por horas (Retry-After). Por isso a consulta é espaçada: o progresso é avançado localmente
# (current()) e uma consulta extra é marcada para o fim da faixa, para pegar a troca de música.
PLAYING_SECONDS = 5       # tocando
PAUSED_SECONDS = 10       # pausado ou parado há pouco
IDLE_SECONDS = 30         # nada tocando há mais de IDLE_AFTER
IDLE_AFTER = 10 * 60
BLOCK_FILE = config.DATA_DIR / "spotify_block.json"  # bloqueio sobrevive a um reinício do serviço
SEEK_TOLERANCE_MS = 2000  # diferença de progresso que indica um pulo na música
PAUSE_IDLE_SECONDS = 30   # Spotify pausado por mais que isso: a tela vai para a espera
ERRORS_TO_REPORT = 5      # falhas de rede seguidas antes de acusar erro (o Wi-Fi às vezes oscila)
log = logging.getLogger("hub.poller")

state = {
    "now": {"authorized": False},   # Spotify
    "mic": None,                    # faixa identificada pelo microfone (listener)
    "listening_since": None,        # início da escuta de música ainda não identificada (listener)
    "device": None,                 # último aparelho do Spotify visto (mesmo se ignorado)
    "paused_at": None,              # quando a faixa atual do Spotify foi pausada
    "fetched_at": 0.0,
    "last_ok": None,
    "last_error": None,
    "last_playing": 0.0,            # última vez que o Spotify estava tocando
    "blocked_until": None,          # Spotify pediu para esperar (HTTP 429) até este instante
    "poll_seconds": PLAYING_SECONDS,
    "started_at": time.time(),
}

_subscribers: set[asyncio.Queue] = set()


def current() -> dict:
    """O que a tela deve mostrar: o Spotify tocando tem prioridade; senão, a faixa do microfone.
    O progresso do Spotify é avançado até agora; pausado há muito tempo, conta como nada tocando."""
    authorized = state["now"].get("authorized", False)
    if not state["now"].get("playing") and state["mic"]:
        return dict(state["mic"], authorized=authorized)
    now = dict(state["now"])
    if now.get("title") and not now.get("playing") and state["paused_at"]:
        paused_ms = int((time.time() - state["paused_at"]) * 1000)
        if paused_ms > PAUSE_IDLE_SECONDS * 1000:
            return {"authorized": authorized, "playing": False}
        now["paused_ms"] = paused_ms
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


def publish(event: str, data: dict) -> None:
    for q in list(_subscribers):
        if q.full():  # tela lenta: descarta o evento mais antigo
            q.get_nowait()
        q.put_nowait((event, data))


def publish_now() -> None:
    publish("now", current())


def listening() -> dict:
    """Escuta de música pelo microfone ainda sem faixa identificada (a tela mostra "Ouvindo música...")."""
    since = state["listening_since"]
    return {"active": since is not None, "elapsed_ms": int((time.time() - since) * 1000) if since else 0}


def set_listening(active: bool) -> None:
    if active and state["listening_since"] is None:
        state["listening_since"] = time.time()
    elif not active and state["listening_since"] is not None:
        state["listening_since"] = None
    else:
        return
    publish("listening", listening())


def _changed(old: dict, new: dict, elapsed_ms: int) -> bool:
    keys = ("authorized", "id", "playing")
    if any(old.get(k) != new.get(k) for k in keys):
        return True
    if new.get("progress_ms") is None or old.get("progress_ms") is None:
        return False
    expected = old["progress_ms"] + (elapsed_ms if old.get("playing") else 0)
    return abs(new["progress_ms"] - expected) > SEEK_TOLERANCE_MS


def _next_delay(now: dict, t: float) -> float:
    if now.get("playing"):
        delay = PLAYING_SECONDS
        if now.get("duration_ms") and now.get("progress_ms") is not None:
            left = (now["duration_ms"] - now["progress_ms"]) / 1000
            delay = min(delay, max(1.0, left + 1))  # logo depois do fim da faixa
        return delay
    return IDLE_SECONDS if t - state["last_playing"] > IDLE_AFTER else PAUSED_SECONDS


def _block_message(until: float) -> str:
    return f"Spotify limitou as consultas até {time.strftime('%d/%m %H:%M', time.localtime(until))}"


def _set_block(until: float | None) -> None:
    state["blocked_until"] = until
    try:
        if until is None:
            BLOCK_FILE.unlink(missing_ok=True)
        else:
            BLOCK_FILE.write_text(json.dumps({"until": until}))
    except OSError:
        log.exception("não consegui gravar o bloqueio do Spotify")


def _load_block() -> None:
    try:
        until = json.loads(BLOCK_FILE.read_text())["until"]
    except (OSError, ValueError, KeyError):
        return
    if until > time.time():
        state["blocked_until"] = until
        state["last_error"] = _block_message(until)


async def run() -> None:
    last_id = await asyncio.to_thread(db.last_track_id)
    failures = 0
    _load_block()
    while True:
        until = state["blocked_until"]
        if until and until > time.time():  # respeita o Retry-After: insistir só estende o bloqueio
            await asyncio.sleep(until - time.time())
        delay = PAUSED_SECONDS
        try:
            now = await asyncio.to_thread(spotify.now_playing)
            state["device"] = now.get("device") or state["device"]
            ignored = {d.casefold() for d in settings.get().ignored_devices}
            if now.get("device") and now["device"].casefold() in ignored:
                now = {"authorized": True, "playing": False}  # tocando num aparelho ignorado (ex.: carro)
            t = time.time()
            old, old_t = state["now"], state["fetched_at"]
            if now.get("playing") or not now.get("title"):
                state["paused_at"] = None
            elif old.get("playing") or old.get("id") != now.get("id") or state["paused_at"] is None:
                state["paused_at"] = t
            state.update(now=now, fetched_at=t, last_ok=t, last_error=None)
            if state["blocked_until"]:
                _set_block(None)
                log.info("Spotify voltou a responder")
            if now.get("playing"):
                state["last_playing"] = t
            delay = _next_delay(now, t)
            failures = 0
            if _changed(old, now, int((t - old_t) * 1000)):
                publish_now()
            if now.get("playing") and now.get("id") and now["id"] != last_id:
                await asyncio.to_thread(db.add_play, now)
                last_id = now["id"]
                publish("history", {})
        except httpx.HTTPStatusError as exc:
            state["last_error"] = f"HTTP {exc.response.status_code}"
            if exc.response.status_code == 429:
                try:
                    wait = int(exc.response.headers.get("Retry-After", 60)) + 1
                except ValueError:
                    wait = 60
                until = time.time() + wait
                _set_block(until)
                state["last_error"] = _block_message(until)
                log.warning("Spotify pediu para esperar %s s (até %s)", wait, time.ctime(until))
                # Sem dados do Spotify enquanto isso: a tela vai para a espera e o microfone pode assumir
                if state["now"].get("playing") or state["now"].get("title"):
                    state["now"] = {"authorized": True, "playing": False}
                    state["paused_at"] = None
                    publish_now()
                delay = 0  # o topo do laço espera o bloqueio
        except httpx.TransportError as exc:  # rede oscilando: só acusa se persistir
            failures += 1
            if failures >= ERRORS_TO_REPORT:
                state["last_error"] = f"sem conexão com o Spotify ({type(exc).__name__})"
            if failures in (1, ERRORS_TO_REPORT):
                log.warning("falha de rede ao consultar o Spotify (%s seguidas): %r", failures, exc)
        except Exception as exc:  # o laço não pode morrer
            log.exception("falha ao consultar o Spotify")
            state["last_error"] = str(exc)
        state["poll_seconds"] = delay
        await asyncio.sleep(delay)
