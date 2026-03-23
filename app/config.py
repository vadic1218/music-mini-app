from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

from dotenv import load_dotenv


def _sqlite_path_works(candidate: Path) -> bool:
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(candidate)
        connection.execute("CREATE TABLE IF NOT EXISTS _probe (id INTEGER)")
        connection.commit()
        connection.close()
        return True
    except sqlite3.Error:
        return False


def _default_local_data_dir(base_dir: Path) -> Path:
    return Path(tempfile.gettempdir()) / "KSBMusicMiniApp"


BASE_DIR = Path(__file__).resolve().parent.parent
APP_DIR = BASE_DIR / "app"
STATIC_DIR = APP_DIR / "static"
load_dotenv(BASE_DIR / ".env")

requested_data_dir = Path(os.getenv("DATA_DIR", str(BASE_DIR / "data"))).resolve()
requested_database_path = Path(os.getenv("DATABASE_PATH", str(requested_data_dir / "mini_app.db"))).resolve()

if not _sqlite_path_works(requested_database_path):
    DATA_DIR = _default_local_data_dir(BASE_DIR).resolve()
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH = (DATA_DIR / "mini_app.db").resolve()
else:
    DATA_DIR = requested_data_dir
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DATABASE_PATH = requested_database_path

APP_NAME = os.getenv("APP_NAME", "KSB Music Mini App")
APP_BASE_URL = os.getenv("APP_BASE_URL", "").rstrip("/")
TELEGRAM_BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
YANDEX_MUSIC_TOKEN = os.getenv("YANDEX_MUSIC_TOKEN", "").strip()
SEARCH_RESULTS_PER_SOURCE = max(5, int(os.getenv("SEARCH_RESULTS_PER_SOURCE", "20")))
LYRICS_RESULTS_LIMIT = max(5, int(os.getenv("LYRICS_RESULTS_LIMIT", "10")))
