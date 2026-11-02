import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")

SPOTIFY_CLIENT_ID = os.environ.get("SPOTIFY_CLIENT_ID", "")
SPOTIFY_REDIRECT_URI = os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8888/callback")
HARDCOVER_TOKEN = os.environ.get("HARDCOVER_TOKEN", "").removeprefix("Bearer ").strip()
DATA_DIR =(ROOT / os.environ.get("HUB_DATA_DIR", "data")).resolve()
FRONTEND_DIR = ROOT / "frontend"
