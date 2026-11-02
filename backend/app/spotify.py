"""Spotify: login PKCE (só Client ID, sem Client Secret) e leitura da música atual."""

import base64
import hashlib
import json
import os
import secrets
import time
from urllib.parse import urlencode

import httpx

from . import config

AUTH_URL = "https://accounts.spotify.com/authorize"
TOKEN_URL = "https://accounts.spotify.com/api/token"
API_URL = "https://api.spotify.com/v1"
SCOPES = "user-read-currently-playing user-read-playback-state"
TOKEN_FILE = config.DATA_DIR / "spotify_tokens.json"

# Conexão reaproveitada (keep-alive): evita um handshake TLS a cada consulta
_client = httpx.Client(timeout=5)
# state -> code_verifier dos logins em andamento
_pending: dict[str, str] = {}
# Tokens em memória; o arquivo só é lido na primeira vez
_tokens: dict | None = None


def login_url() -> str:
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(16)
    _pending[state] = verifier
    params = {
        "client_id": config.SPOTIFY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
        "state": state,
        "scope": SCOPES,
    }
    return f"{AUTH_URL}?{urlencode(params)}"


def exchange_code(code: str, state: str) -> None:
    verifier = _pending.pop(state, None)
    if verifier is None:
        raise ValueError("state desconhecido")
    resp = _client.post(TOKEN_URL, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "client_id": config.SPOTIFY_CLIENT_ID,
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    _save(resp.json())


def _save(tokens: dict) -> dict:
    global _tokens
    tokens["expires_at"] = time.time() + tokens["expires_in"] - 60
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens))
    os.chmod(TOKEN_FILE, 0o600)
    _tokens = tokens
    return tokens


def _load() -> dict | None:
    global _tokens
    if _tokens is None and TOKEN_FILE.exists():
        _tokens = json.loads(TOKEN_FILE.read_text())
    return _tokens


def _access_token() -> str | None:
    tokens = _load()
    if tokens is None:
        return None
    if time.time() >= tokens["expires_at"]:
        resp = _client.post(TOKEN_URL, data={
            "grant_type": "refresh_token",
            "refresh_token": tokens["refresh_token"],
            "client_id": config.SPOTIFY_CLIENT_ID,
        })
        resp.raise_for_status()
        new = resp.json()
        # O Spotify pode ou não devolver um refresh_token novo
        new.setdefault("refresh_token", tokens["refresh_token"])
        tokens = _save(new)
    return tokens["access_token"]


def now_playing() -> dict:
    token = _access_token()
    if token is None:
        return {"authorized": False}
    # /me/player (em vez de currently-playing) também informa em qual aparelho está tocando
    resp = _client.get(f"{API_URL}/me/player", headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 204:
        return {"authorized": True, "playing": False}
    resp.raise_for_status()
    data = resp.json()
    item = data.get("item")
    if not item or item.get("type") != "track":  # nada, anúncio ou podcast
        return {"authorized": True, "playing": False}
    images = item["album"]["images"]
    device = data.get("device") or {}
    return {
        "authorized": True,
        "playing": data.get("is_playing", False),
        "source": "spotify",
        "device": device.get("name"),
        "device_type": device.get("type"),
        "id": item.get("id"),
        "title": item["name"],
        "artists": [a["name"] for a in item["artists"]],
        "album": item["album"]["name"],
        "release_date": item["album"].get("release_date"),
        "cover": images[0]["url"] if images else None,
        "duration_ms": item["duration_ms"],
        "progress_ms": data.get("progress_ms"),
    }
