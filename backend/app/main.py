import asyncio
import ipaddress
import json
from contextlib import asynccontextmanager
from html import escape

import httpx
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, PlainTextResponse,
                               RedirectResponse, Response, StreamingResponse)
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import auth, catalog, config, db, listener, lyrics, poller, settings, shelf, spotify


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init()
    shelf.init()
    tasks = [asyncio.create_task(poller.run()), asyncio.create_task(listener.run())]
    yield
    for task in tasks:
        task.cancel()


app = FastAPI(title="HUB Musical", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    # Capas vêm do Spotify/Shazam (https); todo o resto só do próprio servidor
    "Content-Security-Policy": "default-src 'self'; img-src 'self' https: data: blob:; script-src 'self' 'unsafe-inline'; "
                               "style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; "
                               "base-uri 'none'; form-action 'self'",
}


def _local_host(host: str) -> bool:
    """Só aceita endereços da rede de casa: nome .local, localhost ou IP privado. Barra o "DNS rebinding"
    (um site da internet que finge ser o servidor para mexer nele pelo seu navegador)."""
    if host.startswith("["):  # IPv6: [::1]:8888
        name = host[1:host.find("]")]
    else:
        name = host.rsplit(":", 1)[0]
    name = name.lower()
    if name == "localhost" or name.endswith((".local", ".localhost")):
        return True
    try:
        ip = ipaddress.ip_address(name)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def _from_this_machine(request: Request) -> bool:
    return bool(request.client) and ipaddress.ip_address(request.client.host).is_loopback


@app.middleware("http")
async def security(request: Request, call_next):
    if not _local_host(request.headers.get("host", "")):
        return PlainTextResponse("Endereço não permitido", status_code=400)
    response = await call_next(request)
    response.headers.update(SECURITY_HEADERS)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/auth/login")
def auth_login(request: Request):
    # Só pelo túnel SSH (o próprio servidor): ninguém da rede troca a conta do Spotify
    if not _from_this_machine(request):
        raise HTTPException(403, "Faça o login do Spotify pelo túnel SSH (veja o README)")
    return RedirectResponse(spotify.login_url())


@app.get("/callback")
def auth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if not _from_this_machine(request):
        raise HTTPException(403, "Faça o login do Spotify pelo túnel SSH (veja o README)")
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
            yield f"event: settings\ndata: {settings.get().model_dump_json()}\n\n"
            yield f"event: books\ndata: {json.dumps(shelf.snapshot())}\n\n"
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
    return db.stats(max(1, min(days, 365)))


@app.get("/api/devices")
def devices():
    """Aparelhos do Spotify já vistos (para escolher quais ignorar)."""
    seen = db.devices()
    current = poller.state["device"]
    if current and current not in seen:
        seen.insert(0, current)
    return {"current": current, "seen": seen}


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


# ---------- Estante de livros ----------
# Escritas pedem o PIN do painel; as funções são async para publicar na tela pelo laço principal.

def _require_pin(request: Request) -> None:
    if not auth.check(request.cookies.get(auth.COOKIE)):
        raise HTTPException(401, "pin")


def _book_in(data: dict) -> shelf.BookIn:
    try:
        return shelf.BookIn(**data)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False, include_input=False))


def _shelf_changed() -> None:
    poller.publish("books", shelf.snapshot())  # tela de espera
    poller.publish("library", {})              # painéis abertos recarregam


async def _image_body(request: Request) -> bytes:
    if int(request.headers.get("content-length") or 0) > catalog.MAX_IMAGE_BYTES:
        raise HTTPException(413, "Imagem grande demais (máximo 10 MB).")
    data = await request.body()
    if not data or len(data) > catalog.MAX_IMAGE_BYTES:
        raise HTTPException(413 if data else 400, "Imagem grande demais (máximo 10 MB)." if data else "Sem imagem.")
    return data


@app.get("/api/books")
def get_books():
    """Livros para a tela de espera (lendo agora + últimos lidos)."""
    return shelf.snapshot()


@app.get("/api/library")
def get_library(days: int = 30):
    """Resumo das leituras para o painel: lendo agora, contagens, páginas por dia e lidos."""
    return shelf.library(max(1, min(days, 365)))


@app.get("/api/shelf")
def get_shelf():
    return shelf.all_books()


@app.get("/api/catalog/search")
async def catalog_search(q: str):
    if len(q.strip()) < 2:
        return []
    try:
        return await asyncio.to_thread(catalog.search, q.strip()[:200])
    except httpx.HTTPError:
        raise HTTPException(502, "O Open Library não respondeu. Tente de novo em instantes.")


@app.get("/api/catalog/editions")
async def catalog_editions(work: str, authors: str = ""):
    try:
        return await asyncio.to_thread(catalog.editions, work, authors[:300])
    except catalog.NotFound as exc:
        raise HTTPException(404, str(exc))
    except httpx.HTTPError:
        raise HTTPException(502, "O Open Library não respondeu. Tente de novo em instantes.")


@app.post("/api/catalog/isbn-photo")
async def isbn_photo(request: Request):
    _require_pin(request)
    data = await _image_body(request)
    try:
        isbn = await asyncio.to_thread(catalog.isbn_from_photo, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if not isbn:
        raise HTTPException(404, "Não achei o código de barras. Tente uma foto mais de perto, com boa luz.")
    return {"isbn": isbn}


@app.post("/api/shelf")
async def add_book(request: Request, payload: dict = Body(...)):
    """Adiciona um livro. `cover_url` (capa do Open Library) é baixada e guardada no servidor."""
    _require_pin(request)
    cover = payload.pop("cover_url", None)
    data = _book_in(payload)
    if data.ol_key and (not data.pages or not data.year):
        extra = await asyncio.to_thread(catalog.edition_details, data.ol_key)
        data = data.model_copy(update={k: v for k, v in extra.items() if v and not getattr(data, k)})
    try:
        book_id = await asyncio.to_thread(shelf.create, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    if cover:
        try:
            img = await asyncio.to_thread(catalog.download_image, cover)
            shelf.set_cover(book_id, await asyncio.to_thread(catalog.save_cover, book_id, img))
        except (ValueError, httpx.HTTPError):
            pass  # sem capa: dá para pôr depois pela ficha
    _shelf_changed()
    return shelf.get(book_id)


@app.patch("/api/shelf/{book_id}")
async def edit_book(book_id: int, request: Request, payload: dict = Body(...)):
    _require_pin(request)
    try:
        book = await asyncio.to_thread(shelf.update, book_id, _book_in(payload))
    except KeyError:
        raise HTTPException(404, "Livro não encontrado.")
    _shelf_changed()
    return book


@app.delete("/api/shelf/{book_id}")
async def remove_book(book_id: int, request: Request):
    _require_pin(request)
    await asyncio.to_thread(shelf.delete, book_id)
    catalog.delete_cover(book_id)
    _shelf_changed()
    return {"ok": True}


@app.put("/api/shelf/{book_id}/cover")
async def upload_cover(book_id: int, request: Request):
    """Capa enviada pelo painel (foto do celular ou imagem salva), no corpo da requisição."""
    _require_pin(request)
    if not shelf.get(book_id):
        raise HTTPException(404, "Livro não encontrado.")
    data = await _image_body(request)
    try:
        shelf.set_cover(book_id, await asyncio.to_thread(catalog.save_cover, book_id, data))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    _shelf_changed()
    return shelf.get(book_id)


@app.post("/api/shelf/{book_id}/cover-url")
async def cover_from_link(book_id: int, request: Request, url: str = Body(..., embed=True)):
    _require_pin(request)
    if not shelf.get(book_id):
        raise HTTPException(404, "Livro não encontrado.")
    try:
        img = await asyncio.to_thread(catalog.download_image, url.strip())
        shelf.set_cover(book_id, await asyncio.to_thread(catalog.save_cover, book_id, img))
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    except httpx.HTTPError:
        raise HTTPException(502, "Não consegui baixar a imagem desse link.")
    _shelf_changed()
    return shelf.get(book_id)


@app.delete("/api/shelf/{book_id}/cover")
async def remove_cover(book_id: int, request: Request):
    _require_pin(request)
    catalog.delete_cover(book_id)
    shelf.set_cover(book_id, None)
    _shelf_changed()
    return shelf.get(book_id)


@app.get("/api/shelf/export.csv")
def export_shelf(request: Request):
    _require_pin(request)  # tem as observações pessoais
    return Response(shelf.export_csv(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="minhas-leituras.csv"'})


@app.get("/covers/{book_id}.jpg")
def cover_file(book_id: int):
    path = catalog.COVERS_DIR / f"{book_id}.jpg"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})  # ?v= muda a cada troca


@app.get("/api/settings")
def get_settings():
    return settings.get()


@app.get("/api/settings/defaults")
def default_settings():
    return settings.Settings()


@app.get("/api/pin")
def pin_status(request: Request):
    return {"pin_set": auth.pin_set(), "ok": auth.check(request.cookies.get(auth.COOKIE))}


@app.post("/api/pin")
def pin_login(request: Request, pin: str = Body(..., embed=True)):
    """Cria o PIN (se ainda não existe) ou confere o PIN; acertou, ganha o cookie de 30 dias."""
    ip = request.client.host if request.client else "?"
    if wait := auth.locked_for(ip):
        raise HTTPException(429, f"Tentativas demais. Tente de novo em {wait // 60 + 1} min.")
    if not auth.valid_pin(pin):
        raise HTTPException(422, "O PIN tem de 4 a 8 números.")
    token = auth.login(pin, ip) if auth.pin_set() else auth.create(pin)
    if not token:
        raise HTTPException(401, "PIN incorreto.")
    response = JSONResponse({"ok": True})
    response.set_cookie(auth.COOKIE, token, max_age=auth.SESSION_SECONDS, httponly=True, samesite="strict")
    return response


@app.put("/api/settings")
async def put_settings(request: Request, changes: dict = Body(...)):
    # async: publish() mexe nas filas do asyncio e precisa rodar no laço principal
    if not auth.check(request.cookies.get(auth.COOKIE)):
        raise HTTPException(401, "pin")
    try:
        saved = settings.update(changes)
    except ValidationError as exc:
        raise HTTPException(422, exc.errors(include_url=False, include_context=False))
    poller.publish("settings", saved.model_dump())  # a tela aplica na hora (ex.: horários do tema)
    poller.publish("books", shelf.snapshot())        # quantidade de capas / mostrar lidos
    return saved


@app.get("/painel")
def painel():
    return FileResponse(config.FRONTEND_DIR / "painel.html")


app.mount("/", StaticFiles(directory=config.FRONTEND_DIR, html=True), name="frontend")
