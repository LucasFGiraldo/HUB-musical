import asyncio
import json
from contextlib import asynccontextmanager
from html import escape

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import config, db, listener, lyrics, poller, settings, spotify


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    tasks = [asyncio.create_task(poller.run()), asyncio.create_task(listener.run())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="HUB Musical", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/login")
def auth_login():
    return RedirectResponse(spotify.login_url())


@app.get("/callback")
def auth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return HTMLResponse(f"<p>O Spotify recusou o login: {escape(error)}</p>", status_code=400)
    if not code or not state:
        raise HTTPException(400, "Faltam parâmetros do Spotify")
    try:
        spotify.exchange_code(code, state)
    except ValueError:
        raise HTTPException(400, "Sessão de login expirada; abra /auth/login de novo")
    return HTMLResponse("<p>Spotify conectado. Pode fechar esta aba.</p>")


@app.get("/api/now-playing")
def now_playing():
    return poller.current()


@app.get("/api/stream")
async def stream():
    """Eventos em tempo real: "now" (mudou a música/estado) e "history" (nova música registrada)."""
    async def events():
        q = poller.subscribe()
        try:
            yield f"event: now\ndata: {json.dumps(poller.current())}\n\n"
            yield f"event: listening\ndata: {json.dumps(poller.listening())}\n\n"
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(), timeout=15)
                    yield f"event: {event}\ndata: {json.dumps(data)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"  # mantém a conexão viva
        finally:
            poller.unsubscribe(q)

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/history")
def history(limit: int = 50):
    return db.recent(min(limit, 500))


@app.get("/api/recent")
def recent(limit: int = 5):
    """Últimas músicas sem repetição, para a tela de espera."""
    return db.recent_unique(min(limit, 20))


@app.get("/api/lyrics")
def get_lyrics(track: str):
    """Letra sincronizada da música do Spotify que está tocando (`track` = id do Spotify)."""
    now = poller.state["now"]
    if not settings.get().lyrics_enabled or now.get("id") != track or not now.get("title"):
        return {"lines": None}
    try:
        return {"lines": lyrics.for_track(now)}
    except lyrics.Unavailable as exc:
        raise HTTPException(503, f"Letras indisponíveis agora: {exc}")


@app.get("/api/stats")
def stats(days: int = 30):
    return db.stats(days)


@app.get("/api/status")
def status():
    s = poller.state
    return {
        "spotify_authorized": s["now"].get("authorized", False),
        "last_ok": s["last_ok"],
        "last_error": s["last_error"],
        "started_at": s["started_at"],
        "poll_seconds": poller.POLL_SECONDS,
        "mic": listener.status,
        "spotify_device": s["device"],
    }


@app.get("/api/settings")
def get_settings():
    return settings.get()


@app.put("/api/settings")
def put_settings(changes: dict = Body(...)):
    try:
        return settings.update(changes)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
