"""Leituras do Hardcover (API GraphQL oficial): o que está sendo lido e os últimos livros lidos.

Token em HARDCOVER_TOKEN no .env (deploy/hardcover-token.sh). Só leitura: escopos read:library e read:me:content.
Consulta a cada REFRESH_SECONDS (cada consulta conta 1 das 5.000 diárias do plano grátis).
"""

import asyncio
import logging
import time

import httpx

from . import config, poller

URL = "https://api.hardcover.app/v1/graphql"
REFRESH_SECONDS = 300
READING_MAX = 3   # "lendo" aparece primeiro; os lidos completam até TOTAL_MAX capas
TOTAL_MAX = 5
log = logging.getLogger("hub.books")

QUERY = """
query HubLeituras {
  me {
    lendo: user_books(where: {status_id: {_eq: 2}}, order_by: {updated_at: desc_nulls_last}, limit: %d) { ...ub }
    lidos: user_books(where: {status_id: {_eq: 3}}, order_by: [{last_read_date: desc_nulls_last}, {updated_at: desc_nulls_last}], limit: %d) { ...ub }
  }
}
fragment ub on user_books {
  id
  last_read_date
  edition { title pages image { url } }
  book { title pages image { url } contributions(limit: 2) { author { name } } }
  user_book_reads(order_by: {id: desc}, limit: 1) { progress_pages }
}
""" % (READING_MAX, TOTAL_MAX)

state = {"status": "sem token", "reading": [], "read": [], "updated_at": None}
_client = httpx.Client(timeout=20, headers={"User-Agent": "HUB Musical (tela pessoal de música e leituras)"})


def snapshot() -> dict:
    return {"reading": state["reading"], "read": state["read"]}


def _item(ub: dict, reading: bool) -> dict:
    book, ed = ub.get("book") or {}, ub.get("edition") or {}
    image = (ed.get("image") or {}).get("url") or (book.get("image") or {}).get("url")
    authors = [c["author"]["name"] for c in book.get("contributions") or [] if (c.get("author") or {}).get("name")]
    item = {"id": ub.get("id"), "title": ed.get("title") or book.get("title") or "", "authors": ", ".join(authors),
            "cover": image, "reading": reading}
    if reading:
        pages = ed.get("pages") or book.get("pages")
        reads = ub.get("user_book_reads") or []
        done = reads[0].get("progress_pages") if reads else None
        item["progress"] = round(min(1, done / pages), 3) if pages and done else None
    else:
        item["finished"] = ub.get("last_read_date")
    return item


def fetch() -> tuple[list, list]:
    r = _client.post(URL, json={"query": QUERY}, headers={"Authorization": f"Bearer {config.HARDCOVER_TOKEN}"})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise ValueError(body["errors"][0].get("message", "erro na consulta"))
    me = body["data"]["me"]
    me = me[0] if isinstance(me, list) else me
    reading = [_item(ub, True) for ub in me.get("lendo") or []]
    read = [_item(ub, False) for ub in me.get("lidos") or []][:TOTAL_MAX - len(reading)]
    return reading, read


async def run() -> None:
    if not config.HARDCOVER_TOKEN:
        return  # sem token: a tela fica só com as músicas
    while True:
        delay = REFRESH_SECONDS
        try:
            reading, read = await asyncio.to_thread(fetch)
            changed = (reading, read) != (state["reading"], state["read"])
            state.update(status="ok", reading=reading, read=read, updated_at=time.time())
            if changed:
                poller.publish("books", snapshot())
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            state["status"] = {401: "token expirado ou inválido", 403: "token sem permissão de leitura",
                               429: "limite de consultas atingido"}.get(code, f"HTTP {code}")
            if code == 401:
                delay = 3600  # não adianta insistir até trocar o token
            log.warning("Hardcover respondeu %s: %s", code, exc.response.text[:200])
        except Exception as exc:  # rede ou mudança na API: mantém os últimos livros na tela
            state["status"] = f"erro: {exc}"[:120]
            log.warning("falha ao consultar o Hardcover: %r", exc)
        await asyncio.sleep(delay)
