from __future__ import annotations

import os

from flask import Flask, jsonify, request, send_from_directory

from app.config import (
    APP_BASE_URL,
    APP_NAME,
    DATA_DIR,
    DATABASE_PATH,
    STATIC_DIR,
    TELEGRAM_BOT_TOKEN,
    YANDEX_MUSIC_TOKEN,
)
from app.database import db
from app.services.lyrics_service import get_lyrics
from app.services.playback_service import resolve_stream_url
from app.services.search_service import get_yandex_liked_tracks, search_tracks
from app.services.telegram_auth import validate_init_data


app = Flask(
    APP_NAME,
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.json.ensure_ascii = False

db.init()


@app.before_request
def ensure_database() -> None:
    db.init()


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/api/health")
def health():
    return jsonify(
        {
            "ok": True,
            "app_name": APP_NAME,
            "base_url": APP_BASE_URL,
            "data_dir": str(DATA_DIR),
            "database_path": str(DATABASE_PATH),
            "sources": {
                "yandex": bool(YANDEX_MUSIC_TOKEN),
                "youtube": True,
                "lyrics": True,
            },
            "stats": db.stats(),
        }
    )


@app.post("/api/session")
def upsert_session():
    payload = request.get_json(silent=True) or {}
    init_data = payload.get("init_data")
    user = payload.get("user")

    if init_data and TELEGRAM_BOT_TOKEN:
        if not validate_init_data(init_data, TELEGRAM_BOT_TOKEN):
            return jsonify({"detail": "Неверные данные Telegram WebApp."}), 401

    if user:
        db.upsert_user(user)

    return jsonify({"ok": True})


@app.get("/api/search")
def api_search():
    query = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "all").strip()
    limit = int(request.args.get("limit") or 20)
    if not query:
        return jsonify({"detail": "Нужен поисковый запрос."}), 400

    results = search_tracks(query, source=source, limit=limit)
    return jsonify({"query": query, "source": source, "total": len(results), "results": results})


@app.get("/api/lyrics")
def api_lyrics():
    query = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "auto").strip()
    if not query:
        return jsonify({"detail": "Нужен запрос для текста песни."}), 400

    payload, error = get_lyrics(query, source=source)
    if not payload:
        return jsonify({"query": query, "source": source, "found": False, "error": error})

    return jsonify({"query": query, "source": source, "found": True, "lyrics": payload})


@app.get("/api/library")
def api_library():
    bucket = (request.args.get("bucket") or "library").strip()
    query = (request.args.get("query") or "").strip()
    limit = int(request.args.get("limit") or 2000)
    downloaded_only = (request.args.get("downloaded_only") or "").strip().lower() in {"1", "true", "yes"}
    return jsonify(
        {
            "bucket": bucket,
            "query": query,
            "downloaded_only": downloaded_only,
            "tracks": db.list_tracks(bucket=bucket, limit=limit, query=query, downloaded_only=downloaded_only),
        }
    )


@app.post("/api/library/tracks")
def api_save_track():
    payload = request.get_json(silent=True) or {}
    bucket = payload.get("bucket", "library")
    required_fields = ["source", "source_track_id", "title", "artists"]
    if any(not payload.get(field) for field in required_fields):
        return jsonify({"detail": "Недостаточно данных для сохранения трека."}), 400

    db.save_track(payload, bucket=bucket)
    return jsonify({"ok": True})


@app.post("/api/library/mark-downloaded")
def api_mark_downloaded():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source")
    source_track_id = payload.get("source_track_id")
    bucket = payload.get("bucket", "library")
    if not source or not source_track_id:
        return jsonify({"detail": "Недостаточно данных для отметки скачивания."}), 400

    db.mark_download_requested(source, source_track_id, bucket=bucket)
    return jsonify({"ok": True})


@app.post("/api/playback-url")
def api_playback_url():
    track = request.get_json(silent=True) or {}
    required_fields = ["source", "source_track_id"]
    if any(not track.get(field) for field in required_fields):
        return jsonify({"detail": "Недостаточно данных для воспроизведения."}), 400

    stream_url = resolve_stream_url(track)
    if not stream_url:
        return jsonify({"detail": "Не удалось получить поток для этого трека."}), 404

    return jsonify({"ok": True, "stream_url": stream_url})


@app.post("/api/download-url")
def api_download_url():
    track = request.get_json(silent=True) or {}
    required_fields = ["source", "source_track_id"]
    if any(not track.get(field) for field in required_fields):
        return jsonify({"detail": "Недостаточно данных для скачивания."}), 400

    stream_url = resolve_stream_url(track)
    if not stream_url:
        return jsonify({"detail": "Не удалось получить ссылку на скачивание."}), 404

    artists = (track.get("artists") or "Unknown Artist").strip()
    title = (track.get("title") or "Track").strip()
    safe_name = "".join(char if char.isalnum() or char in " _-()." else "_" for char in f"{artists} - {title}.mp3").strip()
    return jsonify({"ok": True, "download_url": stream_url, "filename": safe_name or "track.mp3"})


@app.post("/api/liked/sync")
def api_sync_liked():
    tracks = get_yandex_liked_tracks()
    if not tracks:
        return jsonify(
            {
                "ok": False,
                "message": "Не удалось получить лайки Яндекс.Музыки. Проверьте токен.",
                "result": None,
            }
        )

    result = db.sync_bucket(tracks, bucket="liked")
    library_new_tracks: list[dict] = []
    for track in tracks:
        existed = db.has_track(track.get("source"), track.get("source_track_id"), bucket="library")
        db.save_track(track, bucket="library")
        if not existed:
            library_new_tracks.append(track)

    return jsonify(
        {
            "ok": True,
            "message": "Синхронизация лайков завершена.",
            "result": result,
            "tracks": db.list_tracks(bucket="liked", limit=2000),
            "library_new_tracks": library_new_tracks,
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
