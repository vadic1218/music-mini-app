from __future__ import annotations

import time
from typing import Any

import yt_dlp

from app.services.search_service import get_ym_client

_PLAYBACK_CACHE_TTL = 900
_playback_cache: dict[str, tuple[float, str]] = {}


def _cache_key(source: str, source_track_id: str | None = None, external_url: str | None = None) -> str:
    return f"{source}:{source_track_id or ''}:{external_url or ''}"


def _cache_get(key: str) -> str | None:
    item = _playback_cache.get(key)
    if not item:
        return None
    expires_at, value = item
    if expires_at < time.time():
        _playback_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: str) -> str:
    _playback_cache[key] = (time.time() + _PLAYBACK_CACHE_TTL, value)
    return value


def get_yandex_stream_url(track_id: str | int) -> str | None:
    key = _cache_key("yandex", str(track_id))
    cached = _cache_get(key)
    if cached:
        return cached

    client = get_ym_client()
    if not client:
        return None

    try:
        tracks = client.tracks([str(track_id)])
        if not tracks:
            return None
        track = tracks[0]
        download_info = track.get_download_info()
        if not download_info:
            return None
        mp3_infos = [info for info in download_info if getattr(info, "codec", "") == "mp3"]
        best_info = max(mp3_infos or download_info, key=lambda item: getattr(item, "bitrate_in_kbps", 0))
        direct = best_info.get_direct_link()
        return _cache_set(key, direct) if direct else None
    except Exception:
        return None


def get_youtube_stream_url(video_id: str | None = None, external_url: str | None = None) -> str | None:
    target = external_url or (f"https://www.youtube.com/watch?v={video_id}" if video_id else None)
    if not target:
        return None
    key = _cache_key("youtube", video_id, external_url)
    cached = _cache_get(key)
    if cached:
        return cached

    options = {
        "quiet": True,
        "skip_download": True,
        "noplaylist": True,
        "nocheckcertificate": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(target, download=False)
    except Exception:
        return None

    formats = (info or {}).get("formats") or []
    audio_formats = [fmt for fmt in formats if fmt.get("acodec") != "none" and fmt.get("url")]
    if not audio_formats:
        url = (info or {}).get("url") or None
        return _cache_set(key, url) if url else None

    def format_rank(fmt: dict[str, Any]) -> tuple[int, int]:
        return int(fmt.get("abr") or 0), int(fmt.get("filesize") or fmt.get("filesize_approx") or 0)

    best_format = max(audio_formats, key=format_rank)
    url = best_format.get("url")
    return _cache_set(key, url) if url else None


def resolve_stream_url(track: dict[str, Any]) -> str | None:
    source = (track.get("source") or "").lower()
    source_track_id = track.get("source_track_id")
    external_url = track.get("external_url")

    if source == "yandex":
        return get_yandex_stream_url(source_track_id)
    if source == "youtube":
        return get_youtube_stream_url(video_id=str(source_track_id), external_url=external_url)
    return None
