"""Leituras do Hardcover (API GraphQL oficial): o que está sendo lido e os livros lidos.

Token em HARDCOVER_TOKEN no .env (deploy/hardcover-token.sh). Só leitura: escopos read:library e read:me:content.
Consulta a cada REFRESH_SECONDS (cada consulta conta 1 das 5.000 diárias do plano grátis), ou na hora pelo
botão "Atualizar agora" do painel.
"""

import asyncio
import datetime as dt
import logging
import time

import httpx

from . import config, poller, settings

URL = "https://api.hardcover.app/v1/graphql"
REFRESH_SECONDS = 300
MANUAL_MIN_SECONDS = 30   # "Atualizar agora" no máximo a cada 30 s
READING_MAX = 10
READ_MAX = 60             # lidos guardados para as estatísticas do painel
log = logging.getLogger("hub.books")

QUERY = """
query HubLeituras {
  me {
    lendo: user_books(where: {status_id: {_eq: 2}}, order_by: {updated_at: desc_nulls_last}, limit: %d) { ...ub }
    lidos: user_books(where: {status_id: {_eq: 3}}, order_by: [{last_read_date: desc_nulls_last}, {updated_at: desc_nulls_last}], limit: %d) { ...ub }
    quero: user_books_aggregate(where: {status_id: {_eq: 1}}) { aggregate { count } }
    lidos_total: user_books_aggregate(where: {status_id: {_eq: 3}}) { aggregate { count } }
  }
}
fragment ub on user_books {
  id
  rating
  last_read_date
  edition { title pages image { url } }
  book { title pages image { url } contributions(limit: 2) { author { name } } }
  user_book_reads(order_by: {id: desc}, limit: 1) { progress_pages }
}
""" % (READING_MAX, READ_MAX)

state = {"status": "sem token", "reading": [], "read": [], "want_count": 0, "read_count": 0,
         "updated_at": None, "manual_at": 0.0}
_client = httpx.Client(timeout=20, headers={"User-Agent": "HUB Musical (tela pessoal de musica e leituras)"})  # cabeçalho: só ASCII
_wake = asyncio.Event()


def snapshot() -> dict:
    """O que a tela de espera mostra, conforme os ajustes: os "lendo" primeiro, os lidos completam as capas."""
    s = settings.get()
    reading = state["reading"][:s.books_covers]
    read = state["read"][:s.books_covers - len(reading)] if s.books_show_read else []
    return {"reading": reading, "read": read}


def library(days: int) -> dict:
    """Resumo para o painel: lendo agora, contagens e os lidos no período."""
    since = (dt.date.today() - dt.timedelta(days=days - 1)).isoformat()
    in_period = [b for b in state["read"] if b.get("finished") and b["finished"] >= since]
    return {
        "status": state["status"], "updated_at": state["updated_at"],
        "reading": state["reading"], "recent_read": state["read"][:20],
        "want_count": state["want_count"], "read_count": state["read_count"],
        "period_read": len(in_period), "period_pages": sum(b.get("pages") or 0 for b in in_period),
    }


def _item(ub: dict, reading: bool) -> dict:
    book, ed = ub.get("book") or {}, ub.get("edition") or {}
    image = (ed.get("image") or {}).get("url") or (book.get("image") or {}).get("url")
    authors = [c["author"]["name"] for c in book.get("contributions") or [] if (c.get("author") or {}).get("name")]
    pages = ed.get("pages") or book.get("pages")
    item = {"id": ub.get("id"), "title": ed.get("title") or book.get("title") or "", "authors": ", ".join(authors),
            "cover": image, "reading": reading, "pages": pages}
    if reading:
        reads = ub.get("user_book_reads") or []
        done = reads[0].get("progress_pages") if reads else None
        item["pages_read"] = done
        item["progress"] = round(min(1, done / pages), 3) if pages and done else None
    else:
        item["finished"] = ub.get("last_read_date")
        item["rating"] = float(ub["rating"]) if ub.get("rating") is not None else None
    return item


def _count(agg: dict | None) -> int:
    return ((agg or {}).get("aggregate") or {}).get("count") or 0


def fetch() -> dict:
    r = _client.post(URL, json={"query": QUERY}, headers={"Authorization": f"Bearer {config.HARDCOVER_TOKEN}"})
    r.raise_for_status()
    body = r.json()
    if body.get("errors"):
        raise ValueError(body["errors"][0].get("message", "erro na consulta"))
    me = body["data"]["me"]
    me = me[0] if isinstance(me, list) else me
    return {
        "reading": [_item(ub, True) for ub in me.get("lendo") or []],
        "read": [_item(ub, False) for ub in me.get("lidos") or []],
        "want_count": _count(me.get("quero")), "read_count": _count(me.get("lidos_total")),
    }


def refresh_now() -> int:
    """Pede uma consulta imediata; devolve quantos segundos faltam se ainda não pode (0 = pedido aceito)."""
    wait = int(state["manual_at"] + MANUAL_MIN_SECONDS - time.time())
    if wait > 0:
        return wait
    state["manual_at"] = time.time()
    _wake.set()
    return 0


async def run() -> None:
    if not config.HARDCOVER_TOKEN:
        return  # sem token: a tela fica só com as músicas
    while True:
        delay = REFRESH_SECONDS
        _wake.clear()
        try:
            data = await asyncio.to_thread(fetch)
            before = snapshot()
            state.update(data, status="ok", updated_at=time.time())
            if snapshot() != before:
                poller.publish("books", snapshot())
            poller.publish("library", {})  # o painel recarrega o resumo
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
        try:
            await asyncio.wait_for(_wake.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
