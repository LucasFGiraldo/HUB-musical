from html import escape

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import config, spotify

app = FastAPI(title="HUB Musical")


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
    try:
        return spotify.now_playing()
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"Erro ao falar com o Spotify: {exc}")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
