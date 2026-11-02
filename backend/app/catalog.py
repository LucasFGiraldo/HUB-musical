"""Catálogo de livros (Open Library), capas guardadas no servidor e leitura de código de barras.

O Open Library só é usado para buscar dados públicos dos livros; a estante do Lucas fica no HUB (shelf.py).
"""

import io
import ipaddress
import re
import socket
import time
import unicodedata
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image, ImageOps

from . import config

OL = "https://openlibrary.org"
COVERS_DIR = config.DATA_DIR / "covers"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
COVER_MAX_PX = 900

_client = httpx.Client(timeout=15, follow_redirects=True,
                       headers={"User-Agent": "HUB Musical (estante pessoal; contato via GitHub LucasFGiraldo)"})


class NotFound(Exception):
    pass


def fix_text(s: str | None) -> str:
    """Conserta acentos quebrados que às vezes vêm do Open Library ("JÃºnior" → "Júnior")."""
    if not s:
        return ""
    if "Ã" in s or "Â" in s:
        try:
            return s.encode("latin-1").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
    return s


def _plain(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s.casefold()) if not unicodedata.combining(c))


def _authors(names: list[str] | None) -> str:
    seen, out = set(), []
    for n in (fix_text(x) for x in names or []):
        if n and _plain(n) not in seen:
            seen.add(_plain(n))
            out.append(n)
    return ", ".join(out[:3])


def _isbn(values: list[str] | None) -> str | None:
    for v in values or []:
        if len(v) == 13:
            return v
    return (values or [None])[0]


def clean_isbn(q: str) -> str | None:
    digits = re.sub(r"[\s-]", "", q).upper()
    return digits if re.fullmatch(r"\d{13}|\d{9}[\dX]", digits) else None


def cover_url(cover_id: int | None, size: str = "M") -> str | None:
    return f"https://covers.openlibrary.org/b/id/{cover_id}-{size}.jpg" if cover_id else None


def search(q: str) -> list[dict]:
    """Livros que combinam com o título, autor ou ISBN; já na melhor edição em português, quando existe."""
    isbn = clean_isbn(q)
    query = f"isbn:{isbn}" if isbn else q
    fields = ("key,title,author_name,first_publish_year,cover_i,number_of_pages_median,"
              "editions,editions.key,editions.title,editions.publisher,editions.isbn,editions.cover_i,editions.language")
    r = _client.get(f"{OL}/search.json", params={"q": query, "lang": "pt", "limit": 12, "fields": fields})
    r.raise_for_status()
    results = []
    for doc in r.json().get("docs", []):
        ed = ((doc.get("editions") or {}).get("docs") or [{}])[0]
        cover = ed.get("cover_i") or doc.get("cover_i")
        results.append({
            "work": doc.get("key"), "edition": ed.get("key"),
            "title": fix_text(ed.get("title") or doc.get("title")), "authors": _authors(doc.get("author_name")),
            "publisher": fix_text((ed.get("publisher") or [None])[0]), "year": str(doc.get("first_publish_year") or ""),
            "pages": doc.get("number_of_pages_median"), "isbn": _isbn(ed.get("isbn")),
            "portuguese": "por" in (ed.get("language") or []),
            "cover_url": cover_url(cover, "L"), "thumb": cover_url(cover, "M"),
        })
    return results


def editions(work: str, authors: str = "") -> list[dict]:
    """Todas as edições de uma obra (as em português primeiro), para escolher a tradução/editora certa."""
    if not re.fullmatch(r"/works/OL\d+W", work or ""):
        raise NotFound("obra inválida")
    r = _client.get(f"{OL}{work}/editions.json", params={"limit": 80})
    if r.status_code == 404:
        raise NotFound("obra não encontrada")
    r.raise_for_status()
    out = []
    for e in r.json().get("entries", []):
        langs = [l.get("key", "").rsplit("/", 1)[-1] for l in e.get("languages") or []]
        cover = next((c for c in e.get("covers") or [] if c and c > 0), None)
        out.append({
            "work": work, "edition": e.get("key"), "title": fix_text(e.get("title")), "authors": authors,
            "publisher": fix_text((e.get("publishers") or [None])[0]),
            "year": (re.search(r"\d{4}", e.get("publish_date") or "") or [""])[0],
            "pages": e.get("number_of_pages"), "isbn": _isbn((e.get("isbn_13") or []) + (e.get("isbn_10") or [])),
            "portuguese": "por" in langs, "cover_url": cover_url(cover, "L"), "thumb": cover_url(cover, "M"),
        })
    out.sort(key=lambda e: (not e["portuguese"], e["cover_url"] is None))
    return out


def edition_details(edition: str) -> dict:
    """Páginas e ano de uma edição (a busca não traz esses dados)."""
    if not re.fullmatch(r"/books/OL\d+M", edition or ""):
        return {}
    try:
        r = _client.get(f"{OL}{edition}.json")
        r.raise_for_status()
        e = r.json()
    except (httpx.HTTPError, ValueError):
        return {}
    return {"pages": e.get("number_of_pages"), "year": (re.search(r"\d{4}", e.get("publish_date") or "") or [""])[0]}


# ---------- Capas ----------

def _public_host(host: str) -> bool:
    """Só baixa de endereços da internet (nunca da rede de casa nem do próprio servidor)."""
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            return False
    return True


def download_image(url: str) -> bytes:
    """Baixa uma imagem de um link colado pelo usuário, com limite de tamanho e sem seguir para a rede local."""
    for _ in range(4):  # até 3 redirecionamentos, conferindo cada destino
        p = urlparse(url)
        if p.scheme not in ("http", "https") or not p.hostname or not _public_host(p.hostname):
            raise ValueError("Esse link não é um endereço público de imagem.")
        with httpx.stream("GET", url, timeout=15, follow_redirects=False,
                          headers={"User-Agent": "Mozilla/5.0 (HUB Musical)"}) as r:
            if r.is_redirect:
                url = urljoin(url, r.headers.get("location", ""))
                continue
            if r.status_code != 200:
                raise ValueError(f"O site respondeu {r.status_code}.")
            if not r.headers.get("content-type", "").startswith("image/"):
                raise ValueError("Esse link não é de uma imagem.")
            data = bytearray()
            for chunk in r.iter_bytes():
                data += chunk
                if len(data) > MAX_IMAGE_BYTES:
                    raise ValueError("Imagem grande demais (máximo 10 MB).")
            return bytes(data)
    raise ValueError("Redirecionamentos demais.")


def save_cover(book_id: int, data: bytes) -> int:
    """Confere que é mesmo uma imagem, reduz e guarda como JPEG em data/covers. Devolve a versão (para o cache)."""
    if len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Imagem grande demais (máximo 10 MB).")
    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # foto do celular em pé
        img = img.convert("RGB")
    except Exception:
        raise ValueError("Não consegui ler essa imagem.")
    img.thumbnail((COVER_MAX_PX, COVER_MAX_PX))
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    img.save(COVERS_DIR / f"{book_id}.jpg", "JPEG", quality=85, optimize=True)
    return int(time.time())


def delete_cover(book_id: int) -> None:
    (COVERS_DIR / f"{book_id}.jpg").unlink(missing_ok=True)


# ---------- Código de barras ----------

def isbn_from_photo(data: bytes) -> str | None:
    """Lê o código de barras (EAN-13 978/979) de uma foto do verso do livro."""
    import zxingcpp  # só carrega quando alguém usa

    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("L")
    except Exception:
        raise ValueError("Não consegui ler essa imagem.")
    img.thumbnail((2000, 2000))
    for code in zxingcpp.read_barcodes(img):
        text = re.sub(r"\D", "", code.text)
        if len(text) == 13 and text.startswith(("978", "979")):
            return text
    return None
