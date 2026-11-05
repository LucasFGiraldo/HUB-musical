"""Cores principais da capa do disco, para o fundo da tela acompanhar a música.

A capa do Spotify/Shazam vem de outro domínio e o navegador não deixa ler os pixels dela (CORS),
então a conta é feita aqui: a imagem vira 48x48, é reduzida a poucas cores e as mais presentes
e vivas ganham. Fica em memória por link, porque a mesma capa volta várias vezes.
"""
import colorsys
import io
from collections import OrderedDict

from PIL import Image

from . import catalog

_cache: OrderedDict[str, list[str]] = OrderedDict()
CACHE_SIZE = 300


def _hsv(rgb):
    return colorsys.rgb_to_hsv(*(c / 255 for c in rgb))


def _distinct(a, b) -> bool:
    """Duas cores parecidas demais (mesmo tom e mesmo brilho) contam como uma só."""
    (h1, s1, v1), (h2, s2, v2) = _hsv(a), _hsv(b)
    dh = min(abs(h1 - h2), 1 - abs(h1 - h2))
    return dh > 0.06 or abs(v1 - v2) > 0.25 or abs(s1 - s2) > 0.35


def extract(data: bytes, count: int = 3) -> list[str]:
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img.thumbnail((48, 48))
    q = img.quantize(colors=12, method=Image.Quantize.MEDIANCUT)
    pal = q.getpalette()
    total = img.width * img.height
    groups = []  # o median cut divide uma mancha de cor em vários tons quase iguais: junta de volta
    for n, idx in sorted(q.getcolors(), reverse=True):
        rgb = tuple(pal[idx * 3: idx * 3 + 3])
        for g in groups:
            if not _distinct(rgb, g[1]):
                g[0] += n
                break
        else:
            groups.append([n, rgb])
    scored = []
    for n, rgb in groups:
        _, s, v = _hsv(rgb)
        share = n / total
        # quase preto, quase branco e cinza contam pouco: a capa "toda preta com um detalhe" deve dar o detalhe
        life = (0.15 + s) * (0.3 if v < 0.12 or (v > 0.95 and s < 0.08) else 1)
        scored.append((share * life, share, rgb))
    scored.sort(reverse=True)
    chosen = []
    for _, share, rgb in scored:
        if share >= 0.02 and all(_distinct(rgb, c) for c in chosen):
            chosen.append(rgb)
        if len(chosen) == count:
            break
    if not chosen:
        chosen = [scored[0][2]]
    while len(chosen) < count:  # capa de uma cor só: variações dela
        h, s, v = _hsv(chosen[0])
        step = 0.18 * len(chosen) * (1 if v < 0.5 else -1)  # clara escurece, escura clareia
        v2 = max(0.0, min(1.0, v + step))
        chosen.append(tuple(round(c * 255) for c in colorsys.hsv_to_rgb((h + 0.03 * len(chosen)) % 1, s, v2)))
    return ["#%02x%02x%02x" % c for c in chosen]


def for_url(url: str) -> list[str]:
    if url in _cache:
        _cache.move_to_end(url)
        return _cache[url]
    colors = extract(catalog.download_image(url))
    _cache[url] = colors
    if len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return colors
