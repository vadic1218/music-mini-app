from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"
DATA_DIR = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

DATABASE_PATH = Path(os.getenv("DATABASE_PATH", str(DATA_DIR / "mini_app.db"))).resolve()

APP_NAME = os.getenv("APP_NAME", "KSB Music Mini App")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
YANDEX_MUSIC_TOKEN = os.getenv("YANDEX_MUSIC_TOKEN", "").strip()
SEARCH_RESULTS_PER_SOURCE = max(5, int(os.getenv("SEARCH_RESULTS_PER_SOURCE", "20")))
LYRICS_RESULTS_LIMIT = max(5, int(os.getenv("LYRICS_RESULTS_LIMIT", "10")))
