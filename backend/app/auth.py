"""PIN do painel: protege as mudanças de ajustes contra qualquer aparelho da rede.

O PIN fica só como hash (PBKDF2) em data/painel_pin.json. Quem acerta o PIN recebe um cookie assinado
que vale 30 dias. Esqueceu o PIN? Apague data/painel_pin.json no servidor e crie outro no painel.
"""

import hashlib
import hmac
import json
import secrets
import time

from . import config

FILE = config.DATA_DIR / "painel_pin.json"
COOKIE = "hub_painel"
SESSION_SECONDS = 30 * 24 * 3600
MAX_FAILS = 5
LOCK_SECONDS = 300
_ITERATIONS = 200_000

_fails: dict[str, tuple[int, float]] = {}  # ip -> (erros seguidos, bloqueado até)


def _load() -> dict | None:
    try:
        return json.loads(FILE.read_text())
    except (FileNotFoundError, ValueError):
        return None


def _hash(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), _ITERATIONS).hex()


def pin_set() -> bool:
    return _load() is not None


def valid_pin(pin: str) -> bool:
    return pin.isdigit() and 4 <= len(pin) <= 8


def create(pin: str) -> str:
    """Cria o PIN (só quando ainda não existe) e devolve o cookie da sessão."""
    salt = secrets.token_hex(16)
    data = {"salt": salt, "hash": _hash(pin, salt), "secret": secrets.token_hex(32)}
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(data))
    FILE.chmod(0o600)
    return _token(data["secret"])


def locked_for(ip: str) -> int:
    """Segundos que faltam para esse aparelho poder tentar de novo (0 = liberado)."""
    _, until = _fails.get(ip, (0, 0))
    return max(0, int(until - time.time()))


def login(pin: str, ip: str) -> str | None:
    """Confere o PIN; devolve o cookie ou None. Erros demais bloqueiam o aparelho por 5 min."""
    data = _load()
    if not data:
        return None
    if hmac.compare_digest(_hash(pin, data["salt"]), data["hash"]):
        _fails.pop(ip, None)
        return _token(data["secret"])
    count = _fails.get(ip, (0, 0))[0] + 1
    _fails[ip] = (0, time.time() + LOCK_SECONDS) if count >= MAX_FAILS else (count, 0)
    return None


def _token(secret: str) -> str:
    expires = str(int(time.time()) + SESSION_SECONDS)
    sig = hmac.new(bytes.fromhex(secret), expires.encode(), hashlib.sha256).hexdigest()
    return f"{expires}.{sig}"


def check(token: str | None) -> bool:
    data = _load()
    if not data or not token or "." not in token:
        return False
    expires, sig = token.split(".", 1)
    good = hmac.new(bytes.fromhex(data["secret"]), expires.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(sig, good) and expires.isdigit() and int(expires) > time.time()
