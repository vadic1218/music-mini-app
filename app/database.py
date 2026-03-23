from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

from .config import DATABASE_PATH


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def init(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS users (
                    telegram_user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    last_name TEXT,
                    language_code TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS library_tracks (
                    source TEXT NOT NULL,
                    source_track_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    artists TEXT NOT NULL,
                    album TEXT,
                    duration_seconds INTEGER DEFAULT 0,
                    cover_url TEXT,
                    external_url TEXT,
                    source_meta TEXT,
                    bucket TEXT NOT NULL DEFAULT 'library',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (source, source_track_id, bucket)
                );

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sync_type TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    existing_count INTEGER NOT NULL DEFAULT 0,
                    removed_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                """
            )

            columns = {row["name"] for row in conn.execute("PRAGMA table_info(library_tracks)").fetchall()}
            if "source_meta" not in columns:
                conn.execute("ALTER TABLE library_tracks ADD COLUMN source_meta TEXT")

    def upsert_user(self, user_payload: dict) -> None:
        if not user_payload or "id" not in user_payload:
            return
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO users (
                    telegram_user_id, username, first_name, last_name, language_code, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name,
                    last_name = excluded.last_name,
                    language_code = excluded.language_code,
                    updated_at = excluded.updated_at
                """,
                (
                    user_payload.get("id"),
                    user_payload.get("username"),
                    user_payload.get("first_name"),
                    user_payload.get("last_name"),
                    user_payload.get("language_code"),
                    utcnow(),
                ),
            )

    def save_track(self, track: dict, bucket: str = "library") -> None:
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO library_tracks (
                    source, source_track_id, title, artists, album,
                    duration_seconds, cover_url, external_url, source_meta, bucket,
                    is_active, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(source, source_track_id, bucket) DO UPDATE SET
                    title = excluded.title,
                    artists = excluded.artists,
                    album = excluded.album,
                    duration_seconds = excluded.duration_seconds,
                    cover_url = excluded.cover_url,
                    external_url = excluded.external_url,
                    source_meta = excluded.source_meta,
                    is_active = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    track["source"],
                    str(track["source_track_id"]),
                    track["title"],
                    track["artists"],
                    track.get("album"),
                    int(track.get("duration_seconds") or 0),
                    track.get("cover_url"),
                    track.get("external_url"),
                    json.dumps(track.get("source_meta") or {}, ensure_ascii=False),
                    bucket,
                    now,
                    now,
                ),
            )

    def sync_bucket(self, tracks: Iterable[dict], bucket: str) -> dict:
        tracks = list(tracks)
        now = utcnow()
        incoming_keys = {(track["source"], str(track["source_track_id"])) for track in tracks}
        new_count = 0
        existing_count = 0

        with self.connect() as conn:
            existing_rows = conn.execute(
                """
                SELECT source, source_track_id
                FROM library_tracks
                WHERE bucket = ?
                """,
                (bucket,),
            ).fetchall()

            existing_keys = {(row["source"], row["source_track_id"]) for row in existing_rows}
            removed_keys = existing_keys - incoming_keys

            conn.execute(
                "UPDATE library_tracks SET is_active = 0, updated_at = ? WHERE bucket = ?",
                (now, bucket),
            )

            for track in tracks:
                key = (track["source"], str(track["source_track_id"]))
                if key in existing_keys:
                    existing_count += 1
                else:
                    new_count += 1

                conn.execute(
                    """
                    INSERT INTO library_tracks (
                        source, source_track_id, title, artists, album,
                        duration_seconds, cover_url, external_url, source_meta, bucket,
                        is_active, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(source, source_track_id, bucket) DO UPDATE SET
                        title = excluded.title,
                        artists = excluded.artists,
                        album = excluded.album,
                        duration_seconds = excluded.duration_seconds,
                        cover_url = excluded.cover_url,
                        external_url = excluded.external_url,
                        source_meta = excluded.source_meta,
                        is_active = 1,
                        updated_at = excluded.updated_at
                    """,
                    (
                        track["source"],
                        str(track["source_track_id"]),
                        track["title"],
                        track["artists"],
                        track.get("album"),
                        int(track.get("duration_seconds") or 0),
                        track.get("cover_url"),
                        track.get("external_url"),
                        json.dumps(track.get("source_meta") or {}, ensure_ascii=False),
                        bucket,
                        now,
                        now,
                    ),
                )

            conn.execute(
                """
                INSERT INTO sync_runs (
                    sync_type, total_count, new_count, existing_count, removed_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (bucket, len(tracks), new_count, existing_count, len(removed_keys), now),
            )

        return {
            "total": len(tracks),
            "new_count": new_count,
            "existing_count": existing_count,
            "removed_count": len(removed_keys),
        }

    def list_tracks(self, bucket: str = "library", limit: int = 200) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT source, source_track_id, title, artists, album,
                       duration_seconds, cover_url, external_url, source_meta, bucket, updated_at
                FROM library_tracks
                WHERE bucket = ? AND is_active = 1
                ORDER BY updated_at DESC, title COLLATE NOCASE ASC
                LIMIT ?
                """,
                (bucket, limit),
            ).fetchall()
        tracks = []
        for row in rows:
            item = dict(row)
            raw_meta = item.get("source_meta")
            try:
                item["source_meta"] = json.loads(raw_meta) if raw_meta else {}
            except json.JSONDecodeError:
                item["source_meta"] = {}
            tracks.append(item)
        return tracks

    def stats(self) -> dict:
        with self.connect() as conn:
            library_count = conn.execute(
                "SELECT COUNT(*) FROM library_tracks WHERE bucket = 'library' AND is_active = 1"
            ).fetchone()[0]
            liked_count = conn.execute(
                "SELECT COUNT(*) FROM library_tracks WHERE bucket = 'liked' AND is_active = 1"
            ).fetchone()[0]
            user_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            last_sync = conn.execute(
                "SELECT sync_type, total_count, new_count, existing_count, removed_count, created_at "
                "FROM sync_runs ORDER BY id DESC LIMIT 1"
            ).fetchone()

        return {
            "library_count": library_count,
            "liked_count": liked_count,
            "user_count": user_count,
            "last_sync": dict(last_sync) if last_sync else None,
        }


db = Database(DATABASE_PATH)
