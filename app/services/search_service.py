from __future__ import annotations

import time
from typing import Any

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

    tracks = response.tracks.results[:normalized_limit]
    results: list[dict[str, Any]] = []
    for track in tracks:
        if not track or not getattr(track, "id", None):
            continue
        album = track.albums[0] if track.albums else None
        duration_ms = int(getattr(track, "duration_ms", 0) or 0)
        cover_uri = getattr(track, "cover_uri", None)
        results.append(
            {
                "source": "yandex",
                "source_track_id": str(track.id),
                "title": track.title or "Без названия",
                "artists": ", ".join(artist.name for artist in (track.artists or [])) or "Неизвестный артист",
                "album": album.title if album else "",
                "duration_seconds": duration_ms // 1000,
                "cover_url": f"https://{cover_uri.replace('%%', '400x400')}" if cover_uri else "",
                "external_url": f"https://music.yandex.ru/album/{album.id}/track/{track.id}" if album else "",
            }
        )
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
        if not track or not getattr(track, "id", None):
            continue
        album = track.albums[0] if track.albums else None
        duration_ms = int(getattr(track, "duration_ms", 0) or 0)
        cover_uri = getattr(track, "cover_uri", None)
        results.append(
            {
                "source": "yandex",
                "source_track_id": str(track.id),
                "title": track.title or "Без названия",
                "artists": ", ".join(artist.name for artist in (track.artists or [])) or "Неизвестный артист",
                "album": album.title if album else "",
                "duration_seconds": duration_ms // 1000,
                "cover_url": f"https://{cover_uri.replace('%%', '400x400')}" if cover_uri else "",
                "external_url": f"https://music.yandex.ru/album/{album.id}/track/{track.id}" if album else "",
            }
        )
    _liked_cache = (time.time() + _LIKED_CACHE_TTL, results)
    return results


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
