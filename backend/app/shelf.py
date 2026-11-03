"""Estante de livros do HUB (SQLite): situação da leitura, marcadores, página atual e histórico de leituras.

Tabelas:
  books          um livro por linha (dados da edição + a leitura atual)
  book_reads     cada leitura terminada (um livro relido tem várias)
  book_progress  cada página salva, com a quantidade lida desde a anterior (para "páginas por dia")
"""

import csv
import io
import sqlite3
from contextlib import closing
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from . import db, settings

Status = Literal["want", "reading", "read", "rereading", "paused", "dropped"]
STATUS_PT = {"want": "Quero ler", "reading": "Lendo", "read": "Lido", "rereading": "Relendo",
             "paused": "Pausado", "dropped": "Abandonado"}
READING = ("reading", "rereading")
PACE_DAYS = 14  # ritmo = páginas lidas nos últimos 14 dias


class BookIn(BaseModel):
    """O que o painel pode mandar ao criar ou editar um livro (tudo opcional na edição)."""
    title: str | None = Field(None, min_length=1, max_length=300)
    authors: str | None = Field(None, max_length=300)
    publisher: str | None = Field(None, max_length=200)
    year: str | None = Field(None, pattern=r"^(\d{4})?$")
    pages: int | None = Field(None, ge=1, le=20000)
    isbn: str | None = Field(None, pattern=r"^([\dX]{10}|\d{13})?$")
    ol_key: str | None = Field(None, pattern=r"^(/books/OL\d+M)?$")
    status: Status | None = None
    page: int | None = Field(None, ge=0, le=20000)
    started_on: date | None = None
    finished_on: date | None = None
    rating: float | None = Field(None, ge=0, le=5)
    notes: str | None = Field(None, max_length=5000)
    owned: bool | None = None
    favorite: bool | None = None
    wishlist: bool | None = None
    lent_to: str | None = Field(None, max_length=100)

    @field_validator("rating")
    @classmethod
    def half_steps(cls, v):
        if v is not None and (v * 2) != int(v * 2):
            raise ValueError("a nota vai de meio em meio ponto")
        return v


def init() -> None:
    with closing(db._connect()) as conn, conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                authors TEXT NOT NULL DEFAULT '',
                publisher TEXT, year TEXT, pages INTEGER, isbn TEXT, ol_key TEXT,
                cover INTEGER,                      -- versão da capa em data/covers/<id>.jpg (NULL = sem capa)
                status TEXT NOT NULL DEFAULT 'want',
                page INTEGER NOT NULL DEFAULT 0,
                started_on TEXT, finished_on TEXT, rating REAL,
                notes TEXT NOT NULL DEFAULT '',
                owned INTEGER NOT NULL DEFAULT 0, favorite INTEGER NOT NULL DEFAULT 0,
                wishlist INTEGER NOT NULL DEFAULT 0, lent_to TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS book_reads (
                id INTEGER PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                started_on TEXT, finished_on TEXT, rating REAL, pages INTEGER
            );
            CREATE TABLE IF NOT EXISTS book_progress (
                id INTEGER PRIMARY KEY,
                book_id INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
                day TEXT NOT NULL,                  -- data local (AAAA-MM-DD)
                page INTEGER NOT NULL,
                pages_read INTEGER NOT NULL
            );
        """)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _cover(row) -> str | None:
    return f"/covers/{row['id']}.jpg?v={row['cover']}" if row["cover"] else None


def _book(row: sqlite3.Row) -> dict:
    b = dict(row)
    b["cover_url"] = _cover(row)
    for k in ("owned", "favorite", "wishlist"):
        b[k] = bool(b[k])
    b["progress"] = round(min(1, b["page"] / b["pages"]), 3) if b["pages"] and b["status"] in READING else None
    return b


def get(book_id: int) -> dict | None:
    with closing(db._connect()) as conn:
        row = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
    return _book(row) if row else None


def all_books() -> list[dict]:
    with closing(db._connect()) as conn:
        rows = conn.execute("SELECT * FROM books ORDER BY updated_at DESC").fetchall()
    return [_book(r) for r in rows]


def create(data: BookIn) -> int:
    if not data.title:
        raise ValueError("O livro precisa de um título.")
    now = _now()
    with closing(db._connect()) as conn, conn:
        cur = conn.execute(
            "INSERT INTO books (title, authors, publisher, year, pages, isbn, ol_key, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (data.title.strip(), (data.authors or "").strip(), data.publisher, data.year or None, data.pages,
             data.isbn or None, data.ol_key or None, now, now))
        book_id = cur.lastrowid
    # Situação, página, datas e marcadores passam pelas mesmas regras da edição
    rest = data.model_dump(exclude_none=True, exclude={"title", "authors", "publisher", "year", "pages", "isbn", "ol_key"})
    if rest:
        update(book_id, BookIn(**rest), new=True)
    return book_id


def update(book_id: int, data: BookIn, new: bool = False) -> dict:
    """Aplica as mudanças com as regras da estante:
    - salvar uma página maior registra as páginas lidas hoje;
    - virar "Lido" guarda a leitura (data de fim = hoje, se vazia) e completa a página;
    - "Relendo" começa uma leitura nova do zero, mantendo as anteriores;
    - sair de "Lido" para outra situação (exceto "Relendo") desfaz a última leitura registrada (correção)."""
    old = get(book_id)
    if not old:
        raise KeyError(book_id)
    ch = data.model_dump(exclude_unset=True)
    for k in ("started_on", "finished_on"):
        if k in ch and ch[k] is not None:
            ch[k] = ch[k].isoformat()
    for k in ("publisher", "year", "isbn", "ol_key", "lent_to"):
        if k in ch and ch[k] == "":
            ch[k] = None
    today = date.today().isoformat()
    status, was = ch.get("status", old["status"]), old["status"]
    pages = ch.get("pages", old["pages"])

    with closing(db._connect()) as conn, conn:
        if status != was:
            if status in READING and was not in READING:
                if status == "rereading":
                    ch.setdefault("page", 0)
                    ch["started_on"] = ch.get("started_on") or today
                    ch["finished_on"] = None
                else:
                    ch["started_on"] = ch.get("started_on") or old["started_on"] or today
            if status == "read":
                ch["finished_on"] = ch.get("finished_on") or (None if new else today)
                if pages:
                    ch.setdefault("page", pages)
            if was == "read" and status != "rereading":
                last = conn.execute("SELECT id FROM book_reads WHERE book_id = ? ORDER BY id DESC LIMIT 1",
                                    (book_id,)).fetchone()
                if last:
                    conn.execute("DELETE FROM book_reads WHERE id = ?", (last["id"],))

        # Páginas lidas (só quando a página avança; cadastrar um livro antigo como lido não conta)
        page = ch.get("page", old["page"])
        tracked = old["page"] > 0 or (status in READING)
        if not new and page > old["page"] and tracked:
            conn.execute("INSERT INTO book_progress (book_id, day, page, pages_read) VALUES (?, ?, ?, ?)",
                         (book_id, today, page, page - old["page"]))

        if ch:
            ch["updated_at"] = _now()
            cols = ", ".join(f"{k} = ?" for k in ch)
            conn.execute(f"UPDATE books SET {cols} WHERE id = ?", (*ch.values(), book_id))

        # A leitura terminada fica em book_reads (a primeira vez que vira "Lido", ou ajustes de data/nota depois)
        cur = conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone()
        if status == "read":
            vals = (cur["started_on"], cur["finished_on"], cur["rating"], cur["pages"])
            if was != "read":
                conn.execute("INSERT INTO book_reads (book_id, started_on, finished_on, rating, pages) VALUES (?, ?, ?, ?, ?)",
                             (book_id, *vals))
            else:
                conn.execute("UPDATE book_reads SET started_on = ?, finished_on = ?, rating = ?, pages = ?"
                             " WHERE id = (SELECT MAX(id) FROM book_reads WHERE book_id = ?)", (*vals, book_id))
    return get(book_id)


def set_cover(book_id: int, version: int | None) -> None:
    with closing(db._connect()) as conn, conn:
        conn.execute("UPDATE books SET cover = ?, updated_at = ? WHERE id = ?", (version, _now(), book_id))


def delete(book_id: int) -> None:
    with closing(db._connect()) as conn, conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM books WHERE id = ?", (book_id,))


# ---------- Tela e painel ----------

def snapshot() -> dict:
    """O que a tela de espera mostra: "lendo" primeiro; os últimos lidos completam as capas."""
    s = settings.get()
    with closing(db._connect()) as conn:
        reading = conn.execute(
            "SELECT * FROM books WHERE status IN ('reading', 'rereading') AND cover IS NOT NULL"
            " ORDER BY updated_at DESC LIMIT ?", (s.books_covers,)).fetchall()
        left = s.books_covers - len(reading) if s.books_show_read else 0
        read = conn.execute(
            "SELECT b.* FROM books b JOIN book_reads r ON r.book_id = b.id WHERE b.cover IS NOT NULL"
            " AND b.status NOT IN ('reading', 'rereading')"
            " GROUP BY b.id ORDER BY MAX(COALESCE(r.finished_on, '')) DESC, MAX(r.id) DESC LIMIT ?",
            (max(0, left),)).fetchall()
        # Páginas lidas: soma das páginas dos livros lidos (relendo também já foi lido)
        pages_read = conn.execute(
            "SELECT COALESCE(SUM(pages), 0) FROM books WHERE status IN ('read', 'rereading')").fetchone()[0]
    item = lambda r, now: {"id": r["id"], "title": r["title"], "cover": _cover(r), "reading": now,
                           "status": r["status"], "favorite": bool(r["favorite"]),
                           "progress": round(min(1, r["page"] / r["pages"]), 3) if now and r["pages"] else None}
    return {"reading": [item(r, True) for r in reading], "read": [item(r, False) for r in read],
            "pages_read": pages_read}


def _pace(conn) -> float:
    since = (date.today() - timedelta(days=PACE_DAYS - 1)).isoformat()
    total = conn.execute("SELECT COALESCE(SUM(pages_read), 0) FROM book_progress WHERE day >= ?", (since,)).fetchone()[0]
    return total / PACE_DAYS


def library(days: int) -> dict:
    """Resumo para o painel: lendo agora (com previsão de término), contagens, páginas por dia e lidos."""
    today = date.today()
    since = (today - timedelta(days=days - 1)).isoformat()
    with closing(db._connect()) as conn:
        pace = _pace(conn)
        reading = [_book(r) for r in conn.execute(
            "SELECT * FROM books WHERE status IN ('reading', 'rereading') ORDER BY updated_at DESC")]
        counts = {r["status"]: r["n"] for r in conn.execute("SELECT status, COUNT(*) AS n FROM books GROUP BY status")}
        owned = conn.execute("SELECT COUNT(*) FROM books WHERE owned = 1").fetchone()[0]
        per_day = {r["day"]: r["n"] for r in conn.execute(
            "SELECT day, SUM(pages_read) AS n FROM book_progress WHERE day >= ? GROUP BY day", (since,))}
        finished = conn.execute("SELECT COUNT(*) FROM book_reads WHERE finished_on >= ?", (since,)).fetchone()[0]
        # Livros terminados por mês, nos últimos 12 meses
        months = []
        y, m = today.year, today.month
        for _ in range(12):
            months.insert(0, f"{y:04d}-{m:02d}")
            y, m = (y, m - 1) if m > 1 else (y - 1, 12)
        per_month = {r["m"]: r["n"] for r in conn.execute(
            "SELECT substr(finished_on, 1, 7) AS m, COUNT(*) AS n FROM book_reads WHERE finished_on >= ? GROUP BY m",
            (months[0] + "-01",))}
        recent = conn.execute(
            "SELECT r.finished_on, r.rating, b.id, b.title, b.authors, b.cover FROM book_reads r JOIN books b ON b.id = r.book_id"
            " ORDER BY COALESCE(r.finished_on, '') DESC, r.id DESC LIMIT 20").fetchall()
    for b in reading:
        left = (b["pages"] or 0) - b["page"]
        b["days_left"] = round(left / pace) if pace > 0 and b["pages"] and left > 0 else None
    days_list = [(today - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
    return {
        "reading": reading,
        "counts": {k: counts.get(k, 0) for k in STATUS_PT}, "total": sum(counts.values()), "owned": owned,
        "period_finished": finished, "period_pages": sum(per_day.values()),
        "pace": round(pace, 1),
        "per_day": [{"day": d, "pages": per_day.get(d, 0)} for d in days_list],
        "per_month": [{"month": mo, "books": per_month.get(mo, 0)} for mo in months],
        "recent_read": [{"id": r["id"], "title": r["title"], "authors": r["authors"], "finished": r["finished_on"],
                         "rating": r["rating"], "cover_url": _cover(r)} for r in recent],
    }


def export_csv() -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Título", "Autores", "Editora", "Ano", "Páginas", "ISBN", "Situação", "Página atual", "Início",
                "Fim", "Nota", "Tenho", "Favorito", "Desejado", "Emprestado para", "Observações", "Vezes lido"])
    with closing(db._connect()) as conn:
        reads = {r["book_id"]: r["n"] for r in conn.execute("SELECT book_id, COUNT(*) AS n FROM book_reads GROUP BY book_id")}
        for b in conn.execute("SELECT * FROM books ORDER BY title COLLATE NOCASE"):
            w.writerow([b["title"], b["authors"], b["publisher"] or "", b["year"] or "", b["pages"] or "", b["isbn"] or "",
                        STATUS_PT[b["status"]], b["page"], b["started_on"] or "", b["finished_on"] or "",
                        "" if b["rating"] is None else str(b["rating"]).replace(".", ","),
                        "sim" if b["owned"] else "", "sim" if b["favorite"] else "", "sim" if b["wishlist"] else "",
                        b["lent_to"] or "", b["notes"], reads.get(b["id"], 0)])
    return "﻿" + out.getvalue()  # BOM: o Excel abre os acentos certos
