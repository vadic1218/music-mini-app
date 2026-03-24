from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from .config import DATABASE_PATH

DEFAULT_PROMO_CODES = [
    {
        "code": "WELCOME",
        "subscription_type": "premium",
        "max_uses": 1,
        "days": 30,
        "description": "Промокод WELCOME - 1 использование",
    },
    {
        "code": "TEST123",
        "subscription_type": "premium",
        "max_uses": 1,
        "days": 30,
        "description": "Тестовый промокод TEST123",
    },
    {
        "code": "V1_GAN13",
        "subscription_type": "premium",
        "max_uses": 1,
        "days": None,
        "description": "Вечный промокод V1_GAN13",
    },
    {
        "code": "FREEMUSIC",
        "subscription_type": "premium",
        "max_uses": 1,
        "days": 30,
        "description": "Промокод FREEMUSIC - 1 использование",
    },
]


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._initialized = False

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
        if self._initialized:
            return
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

                CREATE TABLE IF NOT EXISTS sync_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL DEFAULT 0,
                    sync_type TEXT NOT NULL,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    new_count INTEGER NOT NULL DEFAULT 0,
                    existing_count INTEGER NOT NULL DEFAULT 0,
                    removed_count INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS promo_codes (
                    code TEXT PRIMARY KEY,
                    subscription_type TEXT NOT NULL DEFAULT 'premium',
                    max_uses INTEGER NOT NULL DEFAULT 1,
                    uses_count INTEGER NOT NULL DEFAULT 0,
                    expiry_date TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    description TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS access_grants (
                    telegram_user_id INTEGER PRIMARY KEY,
                    access_type TEXT NOT NULL DEFAULT 'free',
                    source TEXT NOT NULL DEFAULT 'none',
                    promo_code TEXT,
                    expires_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )

            table_exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'library_tracks'"
            ).fetchone()
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(library_tracks)").fetchall()} if table_exists else set()

            if not table_exists or "telegram_user_id" not in columns:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS library_tracks_v2 (
                        telegram_user_id INTEGER NOT NULL DEFAULT 0,
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
                        download_requested_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (telegram_user_id, source, source_track_id, bucket)
                    );
                    """
                )
                if table_exists:
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO library_tracks_v2 (
                            telegram_user_id, source, source_track_id, title, artists, album,
                            duration_seconds, cover_url, external_url, source_meta, bucket,
                            is_active, download_requested_at, created_at, updated_at
                        )
                        SELECT
                            0, source, source_track_id, title, artists, album,
                            duration_seconds, cover_url, external_url, source_meta, bucket,
                            is_active, download_requested_at, created_at, updated_at
                        FROM library_tracks
                        """
                    )
                    conn.execute("DROP TABLE library_tracks")
                conn.execute("ALTER TABLE library_tracks_v2 RENAME TO library_tracks")

            columns = {row["name"] for row in conn.execute("PRAGMA table_info(library_tracks)").fetchall()}
            if "source_meta" not in columns:
                conn.execute("ALTER TABLE library_tracks ADD COLUMN source_meta TEXT")
            if "download_requested_at" not in columns:
                conn.execute("ALTER TABLE library_tracks ADD COLUMN download_requested_at TEXT")
            if "telegram_user_id" not in columns:
                conn.execute("ALTER TABLE library_tracks ADD COLUMN telegram_user_id INTEGER NOT NULL DEFAULT 0")

            sync_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sync_runs)").fetchall()}
            if "telegram_user_id" not in sync_columns:
                conn.execute("ALTER TABLE sync_runs ADD COLUMN telegram_user_id INTEGER NOT NULL DEFAULT 0")

            conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_library_tracks_user_bucket_active_updated
                ON library_tracks(telegram_user_id, bucket, is_active, updated_at DESC);

                CREATE INDEX IF NOT EXISTS idx_library_tracks_user_bucket_title
                ON library_tracks(telegram_user_id, bucket, title COLLATE NOCASE);
                """
            )
            self._seed_default_promos(conn)
        self._initialized = True

    def _seed_default_promos(self, conn) -> None:
        now = utcnow()
        for promo in DEFAULT_PROMO_CODES:
            expiry_date = None
            if promo["days"]:
                expiry_date = (
                    datetime.now(timezone.utc).replace(microsecond=0) +
                    timedelta(days=int(promo["days"]))
                ).isoformat(sep=" ")
            conn.execute(
                """
                INSERT OR IGNORE INTO promo_codes (
                    code, subscription_type, max_uses, uses_count, expiry_date,
                    is_active, description, created_at, updated_at
                )
                VALUES (?, ?, ?, 0, ?, 1, ?, ?, ?)
                """,
                (
                    promo["code"],
                    promo["subscription_type"],
                    promo["max_uses"],
                    expiry_date,
                    promo["description"],
                    now,
                    now,
                ),
            )

    @staticmethod
    def _normalize_user_id(telegram_user_id: int | str | None) -> int:
        try:
            return int(telegram_user_id or 0)
        except (TypeError, ValueError):
            return 0

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

    def claim_legacy_library(self, telegram_user_id: int | str | None) -> int:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        if normalized_user_id <= 0:
            return 0
        with self.connect() as conn:
            user_count = conn.execute(
                "SELECT COUNT(*) FROM library_tracks WHERE telegram_user_id = ?",
                (normalized_user_id,),
            ).fetchone()[0]
            if user_count:
                return 0

            legacy_count = conn.execute(
                "SELECT COUNT(*) FROM library_tracks WHERE telegram_user_id = 0"
            ).fetchone()[0]
            claimed_count = conn.execute(
                "SELECT COUNT(DISTINCT telegram_user_id) FROM library_tracks WHERE telegram_user_id != 0"
            ).fetchone()[0]
            if not legacy_count or claimed_count:
                return 0

            conn.execute(
                "UPDATE library_tracks SET telegram_user_id = ? WHERE telegram_user_id = 0",
                (normalized_user_id,),
            )
            conn.execute(
                "UPDATE sync_runs SET telegram_user_id = ? WHERE telegram_user_id = 0",
                (normalized_user_id,),
            )
        return int(legacy_count)

    def save_track(self, track: dict, bucket: str = "library", telegram_user_id: int | str | None = None) -> None:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        now = utcnow()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO library_tracks (
                    telegram_user_id, source, source_track_id, title, artists, album,
                    duration_seconds, cover_url, external_url, source_meta, bucket,
                    is_active, download_requested_at, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(telegram_user_id, source, source_track_id, bucket) DO UPDATE SET
                    title = excluded.title,
                    artists = excluded.artists,
                    album = excluded.album,
                    duration_seconds = excluded.duration_seconds,
                    cover_url = excluded.cover_url,
                    external_url = excluded.external_url,
                    source_meta = excluded.source_meta,
                    is_active = 1,
                    download_requested_at = COALESCE(excluded.download_requested_at, library_tracks.download_requested_at),
                    updated_at = excluded.updated_at
                """,
                (
                    normalized_user_id,
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
                    track.get("download_requested_at"),
                    now,
                    now,
                ),
            )

    def has_track(
        self,
        source: str,
        source_track_id: str | int,
        bucket: str = "library",
        telegram_user_id: int | str | None = None,
    ) -> bool:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM library_tracks
                WHERE telegram_user_id = ? AND source = ? AND source_track_id = ? AND bucket = ? AND is_active = 1
                LIMIT 1
                """,
                (normalized_user_id, source, str(source_track_id), bucket),
            ).fetchone()
        return bool(row)

    def sync_bucket(self, tracks: Iterable[dict], bucket: str, telegram_user_id: int | str | None = None) -> dict:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        tracks = list(tracks)
        now = utcnow()
        incoming_keys = {(track["source"], str(track["source_track_id"])) for track in tracks}
        new_count = 0
        existing_count = 0
        new_tracks: list[dict] = []

        with self.connect() as conn:
            existing_rows = conn.execute(
                """
                SELECT source, source_track_id
                FROM library_tracks
                WHERE telegram_user_id = ? AND bucket = ?
                """,
                (normalized_user_id, bucket),
            ).fetchall()

            existing_keys = {(row["source"], row["source_track_id"]) for row in existing_rows}
            removed_keys = existing_keys - incoming_keys

            conn.execute(
                "UPDATE library_tracks SET is_active = 0, updated_at = ? WHERE telegram_user_id = ? AND bucket = ?",
                (now, normalized_user_id, bucket),
            )

            for track in tracks:
                key = (track["source"], str(track["source_track_id"]))
                if key in existing_keys:
                    existing_count += 1
                else:
                    new_count += 1
                    new_tracks.append(track)

                conn.execute(
                    """
                    INSERT INTO library_tracks (
                        telegram_user_id, source, source_track_id, title, artists, album,
                        duration_seconds, cover_url, external_url, source_meta, bucket,
                        is_active, download_requested_at, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    ON CONFLICT(telegram_user_id, source, source_track_id, bucket) DO UPDATE SET
                        title = excluded.title,
                        artists = excluded.artists,
                        album = excluded.album,
                        duration_seconds = excluded.duration_seconds,
                        cover_url = excluded.cover_url,
                        external_url = excluded.external_url,
                        source_meta = excluded.source_meta,
                        is_active = 1,
                        download_requested_at = COALESCE(excluded.download_requested_at, library_tracks.download_requested_at),
                        updated_at = excluded.updated_at
                    """,
                    (
                        normalized_user_id,
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
                        track.get("download_requested_at"),
                        now,
                        now,
                    ),
                )

            conn.execute(
                """
                INSERT INTO sync_runs (
                    telegram_user_id, sync_type, total_count, new_count, existing_count, removed_count, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (normalized_user_id, bucket, len(tracks), new_count, existing_count, len(removed_keys), now),
            )

        return {
            "total": len(tracks),
            "new_count": new_count,
            "existing_count": existing_count,
            "removed_count": len(removed_keys),
            "new_tracks": new_tracks,
        }

    def list_tracks(
        self,
        bucket: str = "library",
        limit: int = 200,
        query: str | None = None,
        downloaded_only: bool = False,
        telegram_user_id: int | str | None = None,
    ) -> list[dict]:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        normalized_query = (query or "").strip().casefold()
        with self.connect() as conn:
            downloaded_clause = " AND download_requested_at IS NOT NULL" if downloaded_only else ""
            base_limit = max(limit * 5, 2000) if normalized_query else limit
            rows = conn.execute(
                f"""
                SELECT source, source_track_id, title, artists, album,
                       duration_seconds, cover_url, external_url, source_meta, bucket, updated_at, download_requested_at
                FROM library_tracks
                WHERE telegram_user_id = ? AND bucket = ? AND is_active = 1
                {downloaded_clause}
                ORDER BY updated_at DESC, title COLLATE NOCASE ASC
                LIMIT ?
                """,
                (normalized_user_id, bucket, base_limit),
            ).fetchall()
        tracks = []
        for row in rows:
            item = dict(row)
            raw_meta = item.get("source_meta")
            try:
                item["source_meta"] = json.loads(raw_meta) if raw_meta else {}
            except json.JSONDecodeError:
                item["source_meta"] = {}
            if normalized_query:
                haystack = " ".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("artists") or ""),
                        str(item.get("album") or ""),
                    ]
                ).casefold()
                if normalized_query not in haystack:
                    continue
            tracks.append(item)
            if len(tracks) >= limit:
                break
        return tracks

    def list_user_library(
        self,
        limit: int = 2000,
        query: str | None = None,
        downloaded_only: bool = False,
        telegram_user_id: int | str | None = None,
    ) -> list[dict]:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        normalized_query = (query or "").strip().casefold()
        with self.connect() as conn:
            downloaded_clause = " AND download_requested_at IS NOT NULL" if downloaded_only else ""
            base_limit = max(limit * 5, 2000)
            rows = conn.execute(
                f"""
                SELECT source, source_track_id, title, artists, album,
                       duration_seconds, cover_url, external_url, source_meta, bucket, updated_at, download_requested_at
                FROM library_tracks
                WHERE telegram_user_id = ?
                  AND bucket IN ('library', 'liked')
                  AND is_active = 1
                  {downloaded_clause}
                ORDER BY
                  CASE bucket WHEN 'library' THEN 0 ELSE 1 END,
                  updated_at DESC,
                  title COLLATE NOCASE ASC
                LIMIT ?
                """,
                (normalized_user_id, base_limit),
            ).fetchall()

        tracks: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            item = dict(row)
            raw_meta = item.get("source_meta")
            try:
                item["source_meta"] = json.loads(raw_meta) if raw_meta else {}
            except json.JSONDecodeError:
                item["source_meta"] = {}

            if normalized_query:
                haystack = " ".join(
                    [
                        str(item.get("title") or ""),
                        str(item.get("artists") or ""),
                        str(item.get("album") or ""),
                    ]
                ).casefold()
                if normalized_query not in haystack:
                    continue

            key = (str(item.get("source") or ""), str(item.get("source_track_id") or ""))
            if key in seen:
                continue
            seen.add(key)

            item["bucket"] = "library"
            tracks.append(item)
            if len(tracks) >= limit:
                break
        return tracks

    def mark_download_requested(
        self,
        source: str,
        source_track_id: str | int,
        bucket: str = "library",
        telegram_user_id: int | str | None = None,
    ) -> None:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE library_tracks
                SET download_requested_at = ?, updated_at = ?
                WHERE telegram_user_id = ? AND source = ? AND source_track_id = ? AND bucket = ? AND is_active = 1
                """,
                (utcnow(), utcnow(), normalized_user_id, source, str(source_track_id), bucket),
            )

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

    def get_access_status(self, telegram_user_id: int | str | None) -> dict:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        if normalized_user_id <= 0:
            return {"access_type": "free", "source": "none", "promo_code": None, "expires_at": None}
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT access_type, source, promo_code, expires_at, updated_at
                FROM access_grants
                WHERE telegram_user_id = ?
                """,
                (normalized_user_id,),
            ).fetchone()
        return dict(row) if row else {"access_type": "free", "source": "none", "promo_code": None, "expires_at": None}

    def activate_promo_code(self, telegram_user_id: int | str | None, code: str) -> dict:
        normalized_user_id = self._normalize_user_id(telegram_user_id)
        promo_code = (code or "").strip().upper()
        if normalized_user_id <= 0 or not promo_code:
            return {"ok": False, "message": "Неверные данные для активации промокода."}

        with self.connect() as conn:
            existing_access = conn.execute(
                "SELECT access_type, source, promo_code, expires_at FROM access_grants WHERE telegram_user_id = ?",
                (normalized_user_id,),
            ).fetchone()
            if existing_access and existing_access["access_type"] == "premium":
                return {
                    "ok": False,
                    "message": "У вас уже активирован доступ в Mini App.",
                    "status": dict(existing_access),
                }

            promo = conn.execute(
                """
                SELECT code, subscription_type, max_uses, uses_count, expiry_date, is_active, description
                FROM promo_codes
                WHERE code = ?
                """,
                (promo_code,),
            ).fetchone()
            if not promo:
                return {"ok": False, "message": "Промокод не найден."}
            if not promo["is_active"]:
                return {"ok": False, "message": "Промокод отключен."}
            if promo["max_uses"] is not None and promo["uses_count"] >= promo["max_uses"]:
                return {"ok": False, "message": "Промокод уже израсходован."}

            expires_at = None
            if promo_code != "V1_GAN13":
                expires_at = promo["expiry_date"]

            now = utcnow()
            conn.execute(
                """
                INSERT INTO access_grants (telegram_user_id, access_type, source, promo_code, expires_at, updated_at)
                VALUES (?, 'premium', 'promo', ?, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    access_type = excluded.access_type,
                    source = excluded.source,
                    promo_code = excluded.promo_code,
                    expires_at = excluded.expires_at,
                    updated_at = excluded.updated_at
                """,
                (normalized_user_id, promo_code, expires_at, now),
            )
            conn.execute(
                "UPDATE promo_codes SET uses_count = uses_count + 1, updated_at = ? WHERE code = ?",
                (now, promo_code),
            )

            status = {
                "access_type": "premium",
                "source": "promo",
                "promo_code": promo_code,
                "expires_at": expires_at,
            }
            return {
                "ok": True,
                "message": "Промокод успешно активирован.",
                "status": status,
            }


db = Database(DATABASE_PATH)
