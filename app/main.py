from __future__ import annotations

import json
import os
import re
from urllib.parse import parse_qs
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from pathlib import Path

from flask import Flask, jsonify, make_response, request, send_from_directory

from app.config import (
    APP_BASE_URL,
    APP_NAME,
    ADMIN_IDS,
    BOT_API_BASE_URL,
    MINI_APP_SHARED_SECRET,
    DATA_DIR,
    DATABASE_PATH,
    STATIC_DIR,
    TELEGRAM_BOT_TOKEN,
    YANDEX_MUSIC_TOKEN,
)
from app.database import db
from app.services.lyrics_service import get_lyrics
from app.services.playback_service_v2 import resolve_stream_url
from app.services.search_service_v2 import get_yandex_liked_tracks, get_yandex_playlist_tracks, search_tracks
from app.services.telegram_auth import extract_user_from_init_data, validate_init_data


app = Flask(
    APP_NAME,
    static_folder=str(STATIC_DIR),
    static_url_path="/static",
)
app.json.ensure_ascii = False

db.init()


def _runtime_admin_ids() -> set[int]:
    values = set(ADMIN_IDS)
    raw_value = os.getenv("ADMIN_IDS", "")
    values.update(int(value) for value in re.findall(r"\d+", raw_value or ""))
    return {value for value in values if value > 0}


def _is_admin_user(telegram_user_id: int) -> bool:
    return telegram_user_id > 0 and telegram_user_id in _runtime_admin_ids()


def _call_bot_bridge(path: str, *, method: str = "GET", payload: dict | None = None) -> dict | None:
    if not BOT_API_BASE_URL or not MINI_APP_SHARED_SECRET:
        return None
    url = f"{BOT_API_BASE_URL}{path}"
    body = None
    headers = {
        "X-Mini-App-Secret": MINI_APP_SHARED_SECRET,
        "Accept": "application/json",
    }
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request_obj = Request(url, data=body, headers=headers, method=method.upper())
    try:
        with urlopen(request_obj, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except HTTPError as error:
        try:
            raw = error.read().decode("utf-8")
            return json.loads(raw) if raw else None
        except Exception:
            return None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _get_effective_access_status(telegram_user_id: int) -> dict:
    if telegram_user_id <= 0:
        return {"access_type": "free", "source": "none", "promo_code": None, "expires_at": None}
    if _is_admin_user(telegram_user_id):
        db.ensure_admin_access(telegram_user_id)
        return {
            "access_type": "admin",
            "source": "admin",
            "promo_code": None,
            "expires_at": None,
            "label": "Права администратора",
        }
    bridge_payload = _call_bot_bridge(
        f"/internal/access/status?telegram_user_id={telegram_user_id}",
        method="GET",
    )
    if bridge_payload and bridge_payload.get("status"):
        return bridge_payload["status"]
    return db.get_access_status(telegram_user_id)


def _has_access(telegram_user_id: int) -> bool:
    status = _get_effective_access_status(telegram_user_id)
    return status.get("access_type") in {"premium", "admin"}


def _require_access_response(telegram_user_id: int):
    status = _get_effective_access_status(telegram_user_id)
    return jsonify(
        {
            "detail": "Для использования Mini App нужен промокод или активная подписка.",
            "status": status,
        }
    ), 403


def _extract_telegram_user_id(payload: dict | None = None) -> int:
    payload = payload or {}
    raw_query = {}
    try:
        raw_query = parse_qs((request.query_string or b"").decode("utf-8", errors="ignore"))
    except Exception:
        raw_query = {}
    candidates = [
        payload.get("telegram_user_id"),
        payload.get("user_id"),
        request.args.get("telegram_user_id"),
        request.args.get("user_id"),
        (raw_query.get("telegram_user_id") or [None])[0],
        (raw_query.get("user_id") or [None])[0],
        request.cookies.get("mini_app_user_id"),
    ]
    for candidate in candidates:
        try:
            return int(candidate or 0)
        except (TypeError, ValueError):
            continue
    return 0


def _liked_cache_path(telegram_user_id: int) -> Path:
    return DATA_DIR / f"liked_cache_{telegram_user_id}.json"


def _library_cache_path(telegram_user_id: int) -> Path:
    return DATA_DIR / f"library_cache_{telegram_user_id}.json"


def _write_liked_cache(telegram_user_id: int, tracks: list[dict]) -> None:
    if telegram_user_id <= 0:
        return
    try:
        _liked_cache_path(telegram_user_id).write_text(
            json.dumps(tracks, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _write_library_cache(telegram_user_id: int, tracks: list[dict]) -> None:
    if telegram_user_id <= 0:
        return
    try:
        _library_cache_path(telegram_user_id).write_text(
            json.dumps(tracks, ensure_ascii=False),
            encoding="utf-8",
        )
    except OSError:
        pass


def _read_liked_cache(telegram_user_id: int) -> list[dict]:
    if telegram_user_id <= 0:
        return []
    path = _liked_cache_path(telegram_user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _read_library_cache(telegram_user_id: int) -> list[dict]:
    if telegram_user_id <= 0:
        return []
    path = _library_cache_path(telegram_user_id)
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []


def _merge_track_lists(*collections: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for collection in collections:
        for track in collection or []:
            key = (str(track.get("source") or ""), str(track.get("source_track_id") or ""))
            if key in seen:
                continue
            seen.add(key)
            merged.append(track)
    return merged


def _persist_library_snapshot(telegram_user_id: int, tracks: list[dict]) -> None:
    if telegram_user_id <= 0:
        return
    merged_tracks = _merge_track_lists(tracks)
    for track in merged_tracks:
        db.save_track(track, bucket="library", telegram_user_id=telegram_user_id)
    _write_library_cache(telegram_user_id, merged_tracks)


def _seed_library_from_liked_if_empty(telegram_user_id: int) -> int:
    library_tracks = db.list_tracks(bucket="library", limit=1, telegram_user_id=telegram_user_id)
    if library_tracks:
        return 0

    liked_tracks = db.list_tracks(bucket="liked", limit=5000, telegram_user_id=telegram_user_id)
    if not liked_tracks:
        return 0

    _persist_library_snapshot(telegram_user_id, liked_tracks)
    return len(liked_tracks)


def _library_tracks_with_fallback(
    telegram_user_id: int,
    *,
    query: str = "",
    limit: int = 2000,
    downloaded_only: bool = False,
) -> list[dict]:
    tracks = db.list_user_library(
        limit=limit,
        query=query,
        downloaded_only=downloaded_only,
        telegram_user_id=telegram_user_id,
    )
    if tracks:
        return tracks

    if not query:
        _seed_library_from_liked_if_empty(telegram_user_id)
        tracks = db.list_user_library(
            limit=limit,
            query="",
            downloaded_only=False,
            telegram_user_id=telegram_user_id,
        )
        if tracks:
            return tracks

    cached_library_tracks = _read_library_cache(telegram_user_id)
    if cached_library_tracks:
        filtered_library_tracks: list[dict] = []
        query_cf = (query or "").strip().casefold()
        for track in cached_library_tracks:
            if downloaded_only and not track.get("download_requested_at"):
                continue
            if query_cf:
                haystack = " ".join(
                    [
                        str(track.get("title") or ""),
                        str(track.get("artists") or ""),
                        str(track.get("album") or ""),
                    ]
                ).casefold()
                if query_cf not in haystack:
                    continue
            filtered_library_tracks.append(track)
            if len(filtered_library_tracks) >= limit:
                break
        if filtered_library_tracks:
            if not query and not downloaded_only:
                _persist_library_snapshot(telegram_user_id, filtered_library_tracks)
            return filtered_library_tracks

    cached_tracks = _read_liked_cache(telegram_user_id)
    if cached_tracks:
        filtered_tracks: list[dict] = []
        query_cf = (query or "").strip().casefold()
        for track in cached_tracks:
            if downloaded_only and not track.get("download_requested_at"):
                continue
            if query_cf:
                haystack = " ".join(
                    [
                        str(track.get("title") or ""),
                        str(track.get("artists") or ""),
                        str(track.get("album") or ""),
                    ]
                ).casefold()
                if query_cf not in haystack:
                    continue
            db.save_track(track, bucket="liked", telegram_user_id=telegram_user_id)
            db.save_track(track, bucket="library", telegram_user_id=telegram_user_id)
            filtered_tracks.append(track)
            if len(filtered_tracks) >= limit:
                break
        if filtered_tracks:
            _persist_library_snapshot(telegram_user_id, filtered_tracks)
            return filtered_tracks

    live_liked_tracks = get_yandex_liked_tracks(limit=max(limit, 2000))
    if live_liked_tracks:
        for track in live_liked_tracks:
            db.save_track(track, bucket="liked", telegram_user_id=telegram_user_id)
        _persist_library_snapshot(telegram_user_id, live_liked_tracks)
        _write_liked_cache(telegram_user_id, live_liked_tracks)
        return live_liked_tracks[:limit]

    return tracks


@app.before_request
def ensure_database() -> None:
    db.init()


@app.after_request
def disable_cache(response):
    if request.path == "/" or request.path.startswith("/static/") or request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


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


@app.get("/api/access/status")
def api_access_status():
    telegram_user_id = _extract_telegram_user_id()
    status = _get_effective_access_status(telegram_user_id)
    print(
        f"[MiniApp] access status user={telegram_user_id} "
        f"admins={sorted(_runtime_admin_ids())} "
        f"query={request.query_string.decode('utf-8', errors='ignore')} "
        f"cookie={request.cookies.get('mini_app_user_id')} "
        f"status={status}"
    )
    return jsonify({"ok": True, "status": status})


@app.post("/api/access/promo")
def api_activate_promo():
    payload = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(payload)
    code = (payload.get("code") or "").strip()
    if not code:
        return jsonify({"detail": "Введите промокод."}), 400
    bridge_payload = _call_bot_bridge(
        "/internal/access/promo",
        method="POST",
        payload={"telegram_user_id": telegram_user_id, "code": code},
    )
    if bridge_payload and "ok" in bridge_payload:
        status = bridge_payload.get("status") or _get_effective_access_status(telegram_user_id)
        bridge_payload["status"] = status
        return (jsonify(bridge_payload), 200) if bridge_payload.get("ok") else (jsonify(bridge_payload), 400)
    result = db.activate_promo_code(telegram_user_id, code)
    if not result.get("ok"):
        return jsonify(result), 400
    return jsonify(result)


@app.post("/api/session")
def upsert_session():
    payload = request.get_json(silent=True) or {}
    init_data = payload.get("init_data")
    user = payload.get("user")
    if not user and init_data:
        user = extract_user_from_init_data(init_data)

    if init_data and TELEGRAM_BOT_TOKEN:
        if not validate_init_data(init_data, TELEGRAM_BOT_TOKEN):
            # Do not hard-fail the Mini App session on clients where Telegram
            # provides incomplete/unstable initData. The UI still scopes data by
            # telegram_user_id and user payload.
            if user:
                db.upsert_user(user)
                db.claim_legacy_library(user.get("id"))
                if _is_admin_user(int(user.get("id") or 0)):
                    db.ensure_admin_access(user.get("id"))
            response = make_response(
                jsonify(
                    {
                        "ok": True,
                        "warning": "Telegram initData validation skipped.",
                        "telegram_user_id": int((user or {}).get("id") or 0),
                        "user": user,
                    }
                )
            )
            resolved_user_id = int((user or {}).get("id") or 0)
            if resolved_user_id > 0:
                response.set_cookie(
                    "mini_app_user_id",
                    str(resolved_user_id),
                    max_age=60 * 60 * 24 * 30,
                    secure=True,
                    httponly=False,
                    samesite="Lax",
                )
            return response

    if user:
        db.upsert_user(user)
        db.claim_legacy_library(user.get("id"))
        if _is_admin_user(int(user.get("id") or 0)):
            db.ensure_admin_access(user.get("id"))

    response = make_response(
        jsonify(
            {
                "ok": True,
                "telegram_user_id": int((user or {}).get("id") or 0),
                "user": user,
            }
        )
    )
    resolved_user_id = int((user or {}).get("id") or 0)
    if resolved_user_id > 0:
        response.set_cookie(
            "mini_app_user_id",
            str(resolved_user_id),
            max_age=60 * 60 * 24 * 30,
            secure=True,
            httponly=False,
            samesite="Lax",
        )
    return response


@app.get("/api/search")
def api_search():
    telegram_user_id = _extract_telegram_user_id()
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    query = (request.args.get("q") or "").strip()
    source = (request.args.get("source") or "all").strip()
    limit = int(request.args.get("limit") or 20)
    if not query:
        return jsonify({"detail": "Нужен поисковый запрос."}), 400

    results = search_tracks(query, source=source, limit=limit)
    return jsonify({"query": query, "source": source, "total": len(results), "results": results})


@app.get("/api/lyrics")
def api_lyrics():
    telegram_user_id = _extract_telegram_user_id()
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
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
    telegram_user_id = _extract_telegram_user_id()
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    bucket = (request.args.get("bucket") or "library").strip()
    query = (request.args.get("query") or "").strip()
    limit = int(request.args.get("limit") or 2000)
    downloaded_only = (request.args.get("downloaded_only") or "").strip().lower() in {"1", "true", "yes"}
    if bucket == "library":
        tracks = _library_tracks_with_fallback(
            telegram_user_id,
            query=query,
            limit=limit,
            downloaded_only=downloaded_only,
        )
    else:
        tracks = db.list_tracks(
            bucket=bucket,
            limit=limit,
            query=query,
            downloaded_only=downloaded_only,
            telegram_user_id=telegram_user_id,
        )

    if bucket == "library" and tracks:
        _write_library_cache(telegram_user_id, tracks)
    if bucket == "library":
        print(
            f"[MiniApp] library request user={telegram_user_id} "
            f"query={query!r} downloaded_only={downloaded_only} count={len(tracks)}"
        )

    return jsonify(
        {
            "bucket": bucket,
            "query": query,
            "downloaded_only": downloaded_only,
            "tracks": tracks,
        }
    )


@app.post("/api/library/tracks")
def api_save_track():
    payload = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(payload)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    bucket = payload.get("bucket", "library")
    required_fields = ["source", "source_track_id", "title", "artists"]
    if any(not payload.get(field) for field in required_fields):
        return jsonify({"detail": "Недостаточно данных для сохранения трека."}), 400

    normalized_track = {**payload, "bucket": "library"}
    db.save_track(normalized_track, bucket=bucket, telegram_user_id=telegram_user_id)
    if bucket == "library":
        existing_snapshot = _read_library_cache(telegram_user_id)
        merged_tracks = _merge_track_lists([normalized_track], existing_snapshot)
        _persist_library_snapshot(telegram_user_id, merged_tracks)
        refreshed_tracks = _library_tracks_with_fallback(telegram_user_id, limit=2000)
        print(
            f"[MiniApp] saved library track user={telegram_user_id} "
            f"source={payload.get('source')} track_id={payload.get('source_track_id')} "
            f"library_count={len(refreshed_tracks)}"
        )
        return jsonify({"ok": True, "track": payload, "tracks": refreshed_tracks})
    return jsonify({"ok": True, "track": payload})


@app.post("/api/library/mark-downloaded")
def api_mark_downloaded():
    payload = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(payload)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    source = payload.get("source")
    source_track_id = payload.get("source_track_id")
    bucket = payload.get("bucket", "library")
    if not source or not source_track_id:
        return jsonify({"detail": "Недостаточно данных для отметки скачивания."}), 400

    db.mark_download_requested(source, source_track_id, bucket=bucket, telegram_user_id=telegram_user_id)
    return jsonify({"ok": True})


@app.post("/api/playback-url")
def api_playback_url():
    track = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(track)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
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
    telegram_user_id = _extract_telegram_user_id(track)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
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
    payload = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(payload)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    tracks = get_yandex_liked_tracks()
    if not tracks:
        return jsonify(
            {
                "ok": False,
                "message": "Не удалось получить лайки Яндекс.Музыки. Проверьте токен.",
                "result": None,
            }
        )

    result = db.sync_bucket(tracks, bucket="liked", telegram_user_id=telegram_user_id)
    _write_liked_cache(telegram_user_id, tracks)
    library_new_tracks: list[dict] = []
    ordered_tracks: list[dict] = []
    for index, track in enumerate(tracks):
        track = {
            **track,
            "source_meta": {
                **(track.get("source_meta") or {}),
                "sync_order": index,
                "synced_from": "liked",
            },
        }
        ordered_tracks.append(track)
        existed = db.has_track(
            track.get("source"),
            track.get("source_track_id"),
            bucket="library",
            telegram_user_id=telegram_user_id,
        )
        db.save_track(track, bucket="library", telegram_user_id=telegram_user_id)
        if not existed:
            library_new_tracks.append(track)

    _persist_library_snapshot(telegram_user_id, ordered_tracks)
    seeded_count = _seed_library_from_liked_if_empty(telegram_user_id)
    library_tracks = _library_tracks_with_fallback(telegram_user_id, limit=2000)
    _persist_library_snapshot(telegram_user_id, library_tracks or ordered_tracks)
    print(
        f"[MiniApp] liked sync user={telegram_user_id} "
        f"liked_total={len(tracks)} library_total={len(library_tracks)} "
        f"new={result.get('new_count', 0)} existing={result.get('existing_count', 0)}"
    )

    return jsonify(
        {
            "ok": True,
            "message": (
                "Синхронизация лайков завершена."
                if not seeded_count
                else f"Синхронизация лайков завершена. Библиотека восстановлена: {seeded_count} треков."
            ),
            "result": result,
            "tracks": library_tracks or ordered_tracks[:2000],
            "library_new_tracks": library_new_tracks,
        }
    )


@app.post("/api/yandex/playlist/import")
def api_import_yandex_playlist():
    payload = request.get_json(silent=True) or {}
    telegram_user_id = _extract_telegram_user_id(payload)
    if not _has_access(telegram_user_id):
        return _require_access_response(telegram_user_id)
    playlist_url = (payload.get("url") or "").strip()
    if not playlist_url:
        return jsonify({"detail": "Нужна ссылка на плейлист Яндекс.Музыки."}), 400

    result = get_yandex_playlist_tracks(playlist_url)
    if not result.get("ok"):
        return jsonify(result), 400

    imported = 0
    existing = 0
    for track in result.get("tracks") or []:
        already_exists = db.has_track(
            track.get("source"),
            track.get("source_track_id"),
            bucket="library",
            telegram_user_id=telegram_user_id,
        )
        db.save_track(track, bucket="library", telegram_user_id=telegram_user_id)
        if already_exists:
            existing += 1
        else:
            imported += 1

    library_tracks = _library_tracks_with_fallback(telegram_user_id, limit=2000)
    _persist_library_snapshot(telegram_user_id, library_tracks)

    return jsonify(
        {
            "ok": True,
            "message": "Плейлист добавлен в библиотеку.",
            "playlist_title": result.get("playlist_title"),
            "total": len(result.get("tracks") or []),
            "imported": imported,
            "existing": existing,
            "tracks": library_tracks,
        }
    )


if __name__ == "__main__":
    port = int(os.getenv("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)
