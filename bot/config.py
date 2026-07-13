"""Application configuration loaded from environment / .env."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_ID = int(os.getenv("ADMIN_ID", "5949258698") or "5949258698")
# Cloud hosts (Railway/Render/Koyeb) inject PORT
WEB_PORT = int(os.getenv("PORT") or os.getenv("WEB_PORT", "8080") or "8080")
WEB_HOST = os.getenv("WEB_HOST", "0.0.0.0").strip() or "0.0.0.0"
ADMIN_ONLY = os.getenv("ADMIN_ONLY", "false").strip().lower() in {"1", "true", "yes", "on"}
DEFAULT_SPLIT_SECONDS = int(os.getenv("DEFAULT_SPLIT_SECONDS", "30") or "30")
MAX_TELEGRAM_MB = float(os.getenv("MAX_TELEGRAM_MB", "49") or "49")
MAX_TELEGRAM_BYTES = int(MAX_TELEGRAM_MB * 1024 * 1024)

DOWNLOADS_DIR = BASE_DIR / "downloads"
DATA_DIR = BASE_DIR / "data"
STATS_FILE = DATA_DIR / "stats.json"

DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# URL pattern (http/https)
URL_REGEX = r"https?://[^\s<>\"']+"
