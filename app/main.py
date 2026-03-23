from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.config import APP_NAME, APP_BASE_URL, STATIC_DIR, TELEGRAM_BOT_TOKEN, YANDEX_MUSIC_TOKEN
from app.database import db
from app.services.lyrics_service import get_lyrics
from app.services.search_service import get_yandex_liked_tracks, search_tracks
from app.services.telegram_auth import validate_init_data


class SessionPayload(BaseModel):
    init_data: str | None = None
    user: dict | None = None


class SaveTrackPayload(BaseModel):
    source: str
    source_track_id: str | int
    title: str
    artists: str
    album: str | None = ""
    duration_seconds: int = 0
    cover_url: str | None = ""
    external_url: str | None = ""
    bucket: str = Field(default="library", pattern="^(library|liked)$")


app = FastAPI(title=APP_NAME)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
def on_startup() -> None:
    db.init()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(STATIC_DIR) / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "app_name": APP_NAME,
        "base_url": APP_BASE_URL,
        "sources": {
            "yandex": bool(YANDEX_MUSIC_TOKEN),
            "youtube": True,
            "lyrics": True,
        },
        "stats": db.stats(),
    }


@app.post("/api/session")
def upsert_session(payload: SessionPayload) -> dict:
    if payload.init_data and TELEGRAM_BOT_TOKEN:
        if not validate_init_data(payload.init_data, TELEGRAM_BOT_TOKEN):
            raise HTTPException(status_code=401, detail="Неверные данные Telegram WebApp.")

    if payload.user:
        db.upsert_user(payload.user)

    return {"ok": True}


@app.get("/api/search")
def api_search(q: str, source: str = "all", limit: int = 20) -> dict:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Нужен поисковый запрос.")
    results = search_tracks(query, source=source, limit=limit)
    return {"query": query, "source": source, "total": len(results), "results": results}


@app.get("/api/lyrics")
def api_lyrics(q: str, source: str = "auto") -> dict:
    query = q.strip()
    if not query:
        raise HTTPException(status_code=400, detail="Нужен запрос для текста песни.")
    payload, error = get_lyrics(query, source=source)
    if not payload:
        return {"query": query, "source": source, "found": False, "error": error}
    return {"query": query, "source": source, "found": True, "lyrics": payload}


@app.get("/api/library")
def api_library(bucket: str = "library", limit: int = 200) -> dict:
    return {"bucket": bucket, "tracks": db.list_tracks(bucket=bucket, limit=limit)}


@app.post("/api/library/tracks")
def api_save_track(payload: SaveTrackPayload) -> dict:
    db.save_track(payload.model_dump(), bucket=payload.bucket)
    return {"ok": True}


@app.post("/api/liked/sync")
def api_sync_liked() -> dict:
    tracks = get_yandex_liked_tracks()
    if not tracks:
        return {
            "ok": False,
            "message": "Не удалось получить лайки Яндекс.Музыки. Проверьте токен.",
            "result": None,
        }
    result = db.sync_bucket(tracks, bucket="liked")
    return {
        "ok": True,
        "message": "Синхронизация лайков завершена.",
        "result": result,
        "tracks": db.list_tracks(bucket="liked", limit=50),
    }
