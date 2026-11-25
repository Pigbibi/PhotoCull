from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class StateDB:
    """Local SQLite store for scan state and Google Photos upload idempotency."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scanned_items (
                onedrive_item_id TEXT PRIMARY KEY,
                path TEXT NOT NULL,
                name TEXT NOT NULL,
                size_bytes INTEGER NOT NULL DEFAULT 0,
                etag TEXT NOT NULL DEFAULT '',
                sha256 TEXT NOT NULL DEFAULT '',
                taken_at TEXT,
                dhash TEXT,
                status TEXT NOT NULL DEFAULT 'discovered',
                cluster_id TEXT,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gphotos_albums (
                album_title TEXT PRIMARY KEY,
                album_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS gphotos_uploads (
                onedrive_item_id TEXT PRIMARY KEY,
                gphotos_media_item_id TEXT NOT NULL,
                album_id TEXT,
                album_title TEXT,
                uploaded_at TEXT NOT NULL
            );
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def get_scanned_item(self, onedrive_item_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM scanned_items WHERE onedrive_item_id = ?",
            (onedrive_item_id,),
        ).fetchone()
        return dict(row) if row else None

    def upsert_scanned_item(
        self,
        *,
        onedrive_item_id: str,
        path: str,
        name: str,
        size_bytes: int,
        etag: str,
        sha256: str,
        taken_at: str,
        dhash: str,
        status: str = "discovered",
        cluster_id: str | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO scanned_items (
                onedrive_item_id, path, name, size_bytes, etag, sha256,
                taken_at, dhash, status, cluster_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(onedrive_item_id) DO UPDATE SET
                path = excluded.path,
                name = excluded.name,
                size_bytes = excluded.size_bytes,
                etag = excluded.etag,
                sha256 = excluded.sha256,
                taken_at = excluded.taken_at,
                dhash = excluded.dhash,
                status = excluded.status,
                cluster_id = COALESCE(excluded.cluster_id, scanned_items.cluster_id),
                updated_at = excluded.updated_at
            """,
            (
                onedrive_item_id,
                path,
                name,
                size_bytes,
                etag,
                sha256,
                taken_at,
                dhash,
                status,
                cluster_id,
                _utc_now(),
            ),
        )
        self.conn.commit()

    def mark_item_status(
        self, onedrive_item_id: str, status: str, cluster_id: str | None = None
    ) -> None:
        self.conn.execute(
            """
            UPDATE scanned_items
            SET status = ?,
                cluster_id = COALESCE(?, cluster_id),
                updated_at = ?
            WHERE onedrive_item_id = ?
            """,
            (status, cluster_id, _utc_now(), onedrive_item_id),
        )
        self.conn.commit()

    def get_gphotos_album(self, album_title: str) -> str | None:
        row = self.conn.execute(
            "SELECT album_id FROM gphotos_albums WHERE album_title = ?",
            (album_title,),
        ).fetchone()
        return str(row["album_id"]) if row else None

    def record_gphotos_album(self, album_title: str, album_id: str) -> None:
        self.conn.execute(
            """
            INSERT INTO gphotos_albums (album_title, album_id, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(album_title) DO UPDATE SET album_id = excluded.album_id
            """,
            (album_title, album_id, _utc_now()),
        )
        self.conn.commit()

    def get_gphotos_upload(self, onedrive_item_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM gphotos_uploads WHERE onedrive_item_id = ?",
            (onedrive_item_id,),
        ).fetchone()
        return dict(row) if row else None

    def record_gphotos_upload(
        self,
        *,
        onedrive_item_id: str,
        gphotos_media_item_id: str,
        album_id: str | None,
        album_title: str | None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO gphotos_uploads (
                onedrive_item_id, gphotos_media_item_id, album_id, album_title, uploaded_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(onedrive_item_id) DO UPDATE SET
                gphotos_media_item_id = excluded.gphotos_media_item_id,
                album_id = excluded.album_id,
                album_title = excluded.album_title,
                uploaded_at = excluded.uploaded_at
            """,
            (onedrive_item_id, gphotos_media_item_id, album_id, album_title, _utc_now()),
        )
        self.conn.commit()

    def summary_counts(self) -> dict[str, int]:
        scanned = self.conn.execute(
            "SELECT status, COUNT(*) AS cnt FROM scanned_items GROUP BY status"
        ).fetchall()
        gp = self.conn.execute("SELECT COUNT(*) AS cnt FROM gphotos_uploads").fetchone()
        out = {str(r["status"]): int(r["cnt"]) for r in scanned}
        out["gphotos_uploaded"] = int(gp["cnt"]) if gp else 0
        return out
