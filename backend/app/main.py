import asyncio
from contextlib import asynccontextmanager
from html import escape

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, db, poller, spotify


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    task = asyncio.create_task(poller.run())
    yield
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


@app.get("/api/history")
def history(limit: int = 50):
    return db.recent(min(limit, 500))


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
    }


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
