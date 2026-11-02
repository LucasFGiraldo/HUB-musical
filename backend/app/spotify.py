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

# state -> code_verifier dos logins em andamento
_pending: dict[str, str] = {}


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
    resp = httpx.post(TOKEN_URL, timeout=10, data={
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.SPOTIFY_REDIRECT_URI,
        "client_id": config.SPOTIFY_CLIENT_ID,
        "code_verifier": verifier,
    })
    resp.raise_for_status()
    _save(resp.json())


def _save(tokens: dict) -> dict:
    tokens["expires_at"] = time.time() + tokens["expires_in"] - 60
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps(tokens))
    os.chmod(TOKEN_FILE, 0o600)
    return tokens


def _load() -> dict | None:
    if not TOKEN_FILE.exists():
        return None
    return json.loads(TOKEN_FILE.read_text())


def _access_token() -> str | None:
    tokens = _load()
    if tokens is None:
        return None
    if time.time() >= tokens["expires_at"]:
        resp = httpx.post(TOKEN_URL, timeout=10, data={
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
    resp = httpx.get(f"{API_URL}/me/player/currently-playing", timeout=10,
                     headers={"Authorization": f"Bearer {token}"})
    if resp.status_code == 204:
        return {"authorized": True, "playing": False}
    resp.raise_for_status()
    data = resp.json()
    item = data.get("item")
    if not item:
        return {"authorized": True, "playing": False}
    images = item["album"]["images"]
    return {
        "authorized": True,
        "playing": data.get("is_playing", False),
        "source": "spotify",
        "title": item["name"],
        "artists": [a["name"] for a in item["artists"]],
        "album": item["album"]["name"],
        "release_date": item["album"].get("release_date"),
        "cover": images[0]["url"] if images else None,
        "duration_ms": item["duration_ms"],
        "progress_ms": data.get("progress_ms"),
    }
