from __future__ import annotations

import re
import time
from typing import Any
from urllib.parse import parse_qs, urlparse

import yt_dlp
from yandex_music import Client

from app.config import SEARCH_RESULTS_PER_SOURCE, YANDEX_MUSIC_TOKEN


_ym_client: Client | None = None
_SEARCH_CACHE_TTL = 120
_LIKED_CACHE_TTL = 90
_search_cache: dict[tuple[str, str, int], tuple[float, list[dict[str, Any]]]] = {}
_liked_cache: tuple[float, list[dict[str, Any]]] | None = None


def _cache_get(cache: dict, key):
    item = cache.get(key)
    if not item:
        return None
    expires_at, payload = item
    if expires_at < time.time():
        cache.pop(key, None)
        return None
    return payload


def _cache_set(cache: dict, key, payload, ttl: int):
    cache[key] = (time.time() + ttl, payload)


def _serialize_yandex_track(track) -> dict[str, Any] | None:
    if not track or not getattr(track, "id", None):
        return None
    album = track.albums[0] if track.albums else None
    duration_ms = int(getattr(track, "duration_ms", 0) or 0)
    cover_uri = getattr(track, "cover_uri", None)
    return {
        "source": "yandex",
        "source_track_id": str(track.id),
        "title": track.title or "Без названия",
        "artists": ", ".join(artist.name for artist in (track.artists or [])) or "Неизвестный артист",
        "album": album.title if album else "",
        "duration_seconds": duration_ms // 1000,
        "cover_url": f"https://{cover_uri.replace('%%', '400x400')}" if cover_uri else "",
        "external_url": f"https://music.yandex.ru/album/{album.id}/track/{track.id}" if album else "",
    }


def get_ym_client() -> Client | None:
    global _ym_client
    if _ym_client is not None:
        return _ym_client
    if not YANDEX_MUSIC_TOKEN:
        return None
    try:
        _ym_client = Client(YANDEX_MUSIC_TOKEN).init()
        return _ym_client
    except Exception:
        return None


def search_yandex_music(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    normalized_limit = limit or SEARCH_RESULTS_PER_SOURCE
    cache_key = ("yandex", query.strip().lower(), normalized_limit)
    cached = _cache_get(_search_cache, cache_key)
    if cached is not None:
        return cached

    client = get_ym_client()
    if not client:
        return []

    try:
        response = client.search(query, type_="track", page=0)
        if not response or not response.tracks or not response.tracks.results:
            return []
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for track in response.tracks.results[:normalized_limit]:
        serialized = _serialize_yandex_track(track)
        if serialized:
            results.append(serialized)
    _cache_set(_search_cache, cache_key, results, _SEARCH_CACHE_TTL)
    return results


def get_yandex_liked_tracks() -> list[dict[str, Any]]:
    global _liked_cache
    if _liked_cache and _liked_cache[0] >= time.time():
        return _liked_cache[1]

    client = get_ym_client()
    if not client:
        return []
    try:
        likes = client.users_likes_tracks()
        tracks = likes.fetch_tracks() if likes else []
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    for track in tracks or []:
        serialized = _serialize_yandex_track(track)
        if serialized:
            results.append(serialized)
    _liked_cache = (time.time() + _LIKED_CACHE_TTL, results)
    return results


def parse_yandex_playlist_url(url: str) -> tuple[str, str] | None:
    if not url or "music.yandex" not in url:
        return None
    parsed = urlparse(url.strip())
    path_match = re.search(r"/users/([^/]+)/playlists/(\d+)", parsed.path)
    if path_match:
        return path_match.group(1), path_match.group(2)

    query = parse_qs(parsed.query)
    owner = (query.get("owner") or [None])[0]
    kind = (query.get("kinds") or query.get("kind") or [None])[0]
    if owner and kind:
        return owner, str(kind)
    return None


def get_yandex_playlist_tracks(url: str) -> dict[str, Any]:
    client = get_ym_client()
    if not client:
        return {"ok": False, "message": "Яндекс.Музыка не настроена.", "tracks": []}

    parsed = parse_yandex_playlist_url(url)
    if not parsed:
        return {"ok": False, "message": "Не удалось распознать ссылку на плейлист Яндекс.Музыки.", "tracks": []}

    owner, kind = parsed
    try:
        playlist = client.users_playlists(kind=kind, user_id=owner)
        if not playlist:
            return {"ok": False, "message": "Плейлист не найден.", "tracks": []}
        track_items = playlist.fetch_tracks() or []
    except Exception as error:
        return {"ok": False, "message": f"Не удалось загрузить плейлист: {error}", "tracks": []}

    results: list[dict[str, Any]] = []
    for item in track_items:
        track = getattr(item, "track", None)
        if track is None and hasattr(item, "fetch_track"):
            try:
                track = item.fetch_track()
            except Exception:
                track = None
        serialized = _serialize_yandex_track(track)
        if serialized:
            results.append(serialized)

    return {
        "ok": True,
        "message": "Плейлист загружен.",
        "playlist_title": getattr(playlist, "title", "Плейлист"),
        "owner": owner,
        "kind": str(kind),
        "tracks": results,
    }


def search_youtube_music(query: str, limit: int | None = None) -> list[dict[str, Any]]:
    max_results = limit or SEARCH_RESULTS_PER_SOURCE
    cache_key = ("youtube", query.strip().lower(), max_results)
    cached = _cache_get(_search_cache, cache_key)
    if cached is not None:
        return cached

    options = {
        "quiet": True,
        "extract_flat": True,
        "default_search": "ytsearch",
        "noplaylist": True,
        "skip_download": True,
        "nocheckcertificate": True,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(f"ytsearch{max_results}:{query}", download=False)
    except Exception:
        return []

    entries = info.get("entries") if info else []
    results: list[dict[str, Any]] = []
    for video in entries or []:
        if not video:
            continue
        video_id = video.get("id")
        if not video_id:
            continue
        thumbnail = ""
        thumbnails = video.get("thumbnails") or []
        if thumbnails:
            thumbnail = thumbnails[-1].get("url", "")
        results.append(
            {
                "source": "youtube",
                "source_track_id": video_id,
                "title": video.get("title") or "Без названия",
                "artists": video.get("uploader") or "YouTube",
                "album": "",
                "duration_seconds": int(video.get("duration") or 0),
                "cover_url": thumbnail,
                "external_url": f"https://www.youtube.com/watch?v={video_id}",
            }
        )
    _cache_set(_search_cache, cache_key, results, _SEARCH_CACHE_TTL)
    return results


def search_tracks(query: str, source: str = "all", limit: int | None = None) -> list[dict[str, Any]]:
    source = (source or "all").lower()
    if source == "yandex":
        return search_yandex_music(query, limit=limit)
    if source == "youtube":
        return search_youtube_music(query, limit=limit)

    yandex_results = search_yandex_music(query, limit=limit)
    youtube_results = search_youtube_music(query, limit=limit)
    return yandex_results + youtube_results
