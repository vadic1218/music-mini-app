from __future__ import annotations

from typing import Any
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from yandex_music.exceptions import NotFoundError

from app.config import LYRICS_RESULTS_LIMIT
from app.services.search_service import get_ym_client, search_yandex_music


def normalize_match_text(value: str | None) -> str:
    value = (value or "").lower().replace("ё", "е")
    chars = []
    for char in value:
        chars.append(char if char.isalnum() or char.isspace() else " ")
    return " ".join("".join(chars).split())


def score_song_match(query: str, title: str | None, artist: str | None = None) -> int:
    normalized_query = normalize_match_text(query)
    normalized_title = normalize_match_text(title)
    normalized_artist = normalize_match_text(artist)
    combined = f"{normalized_artist} {normalized_title}".strip()
    if not normalized_query:
        return 0

    score = 0
    if normalized_query == normalized_title:
        score += 120
    if normalized_query == combined:
        score += 160
    if normalized_query in combined:
        score += 80

    title_tokens = set(normalized_title.split())
    artist_tokens = set(normalized_artist.split())
    for token in normalized_query.split():
        if len(token) <= 1:
            continue
        if token in title_tokens:
            score += 18
        elif token in artist_tokens:
            score += 12
        elif token in combined:
            score += 6
    return score


def build_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/137.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "ru,en-US;q=0.9,en;q=0.8",
    }


def extract_genius_lyrics(html: str) -> str:
    soup = BeautifulSoup(html, "html.parser")
    containers = soup.select('[data-lyrics-container="true"]')
    if containers:
        return "\n".join(node.get_text("\n", strip=True) for node in containers if node).strip()
    legacy = soup.select_one("div.lyrics")
    if legacy:
        return legacy.get_text("\n", strip=True).strip()
    return ""


def search_genius_urls(query: str) -> list[tuple[str, str]]:
    response = requests.get(
        "https://html.duckduckgo.com/html/",
        params={"q": f"site:genius.com {query} lyrics"},
        headers=build_headers(),
        timeout=(10, 30),
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    hits: list[tuple[str, str]] = []
    for link in soup.select("a.result__a"):
        href = link.get("href")
        title = link.get_text(" ", strip=True)
        if not href:
            continue
        href = unquote(href)
        if "genius.com" not in href or not href.endswith("-lyrics"):
            continue
        hits.append((href, title))
    return hits


def get_lyrics_from_yandex(query: str) -> tuple[dict[str, Any] | None, str | None]:
    client = get_ym_client()
    if not client:
        return None, "Яндекс.Музыка не настроена."

    candidates = sorted(
        search_yandex_music(query, limit=LYRICS_RESULTS_LIMIT),
        key=lambda item: score_song_match(query, item.get("title"), item.get("artists")),
        reverse=True,
    )
    for candidate in candidates:
        try:
            lyrics_meta = client.tracks_lyrics(candidate["source_track_id"], format="TEXT")
            if not lyrics_meta:
                continue
            text = lyrics_meta.fetch_lyrics().strip()
            if not text:
                continue
            return {
                "title": candidate["title"],
                "artists": candidate["artists"],
                "source": "Яндекс.Музыка",
                "text": text,
            }, None
        except NotFoundError:
            continue
        except Exception:
            continue

    return None, "Текст в Яндекс.Музыке не найден."


def get_lyrics_from_genius(query: str) -> tuple[dict[str, Any] | None, str | None]:
    try:
        hits = search_genius_urls(query)
    except Exception as exc:
        return None, f"Genius error: {exc}"

    ranked_hits = sorted(
        hits,
        key=lambda item: score_song_match(
            query,
            item[1],
            item[0].replace("https://genius.com/", "").replace("-lyrics", "").replace("-", " "),
        ),
        reverse=True,
    )
    for url, fallback_title in ranked_hits[:LYRICS_RESULTS_LIMIT]:
        try:
            page = requests.get(url, headers=build_headers(), timeout=(10, 30))
            page.raise_for_status()
        except Exception:
            continue
        text = extract_genius_lyrics(page.text)
        if not text:
            continue

        soup = BeautifulSoup(page.text, "html.parser")
        meta_title = soup.find("meta", property="og:title")
        title = fallback_title
        artists = "Unknown Artist"
        if meta_title and meta_title.get("content"):
            content = meta_title["content"].replace(" Lyrics", "").strip()
            if " by " in content:
                title, artists = [part.strip() for part in content.split(" by ", 1)]
            else:
                title = content

        if score_song_match(query, title, artists) < 40:
            continue

        return {
            "title": title,
            "artists": artists,
            "source": "Genius",
            "text": text,
        }, None

    return None, "Текст на Genius не найден."


def get_lyrics(query: str, source: str = "auto") -> tuple[dict[str, Any] | None, str | None]:
    source = (source or "auto").lower()
    if source == "yandex":
        return get_lyrics_from_yandex(query)
    if source == "genius":
        return get_lyrics_from_genius(query)

    payload, error = get_lyrics_from_yandex(query)
    if payload:
        return payload, None

    payload, genius_error = get_lyrics_from_genius(query)
    if payload:
        return payload, None

    return None, genius_error or error or "Текст песни не найден."
