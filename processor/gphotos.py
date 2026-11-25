from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from processor.state_db import StateDB


class GooglePhotosError(RuntimeError):
    pass


def _guess_mime_type(filename: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext == ".png":
        return "image/png"
    if ext == ".webp":
        return "image/webp"
    if ext in {".heic", ".heif"}:
        return "image/heic"
    return "image/jpeg"


class GooglePhotosClient:
    """
    Google Photos client restricted to `photoslibrary.appendonly`.
    Reads/deletes against arbitrary historical items are intentionally not supported.
    """

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        timeout: int = 60,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.timeout = timeout
        self._access_token: str | None = None

    def _refresh_access_token(self) -> str:
        if not self.client_id or not self.client_secret or not self.refresh_token:
            raise GooglePhotosError(
                "GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, and GOOGLE_REFRESH_TOKEN are required"
            )
        data = urllib.parse.urlencode(
            {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "grant_type": "refresh_token",
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            "https://oauth2.googleapis.com/token",
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise GooglePhotosError(f"Google OAuth refresh failed ({exc.code}): {detail}") from exc

        token = payload.get("access_token")
        if not token:
            raise GooglePhotosError("Google OAuth response missing access_token")
        self._access_token = str(token)
        return self._access_token

    def _get_token(self) -> str:
        return self._access_token or self._refresh_access_token()

    def _post_json(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(2):
            token = self._get_token()
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._refresh_access_token()
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise GooglePhotosError(f"Google Photos POST {url} failed ({exc.code}): {detail}") from exc
        raise GooglePhotosError("Google Photos POST exhausted retries")

    def _upload_raw_bytes(self, filename: str, raw_bytes: bytes) -> str:
        mime = _guess_mime_type(filename)
        for attempt in range(2):
            token = self._get_token()
            req = urllib.request.Request(
                "https://photoslibrary.googleapis.com/v1/uploads",
                data=raw_bytes,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/octet-stream",
                    "X-Goog-Upload-Content-Type": mime,
                    "X-Goog-Upload-Protocol": "raw",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return resp.read().decode("utf-8").strip()
            except urllib.error.HTTPError as exc:
                if exc.code == 401 and attempt == 0:
                    self._refresh_access_token()
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise GooglePhotosError(f"Google Photos upload bytes failed ({exc.code}): {detail}") from exc
        raise GooglePhotosError("Google Photos upload bytes exhausted retries")

    def get_or_create_album(self, state_db: StateDB, title: str) -> str:
        clean_title = title.strip() or "旅拍精选"
        cached = state_db.get_gphotos_album(clean_title)
        if cached:
            return cached

        resp = self._post_json(
            "https://photoslibrary.googleapis.com/v1/albums",
            {"album": {"title": clean_title}},
        )
        album_id = str(resp.get("id") or "")
        if not album_id:
            raise GooglePhotosError(f"Album creation missing id: {resp}")
        state_db.record_gphotos_album(clean_title, album_id)
        return album_id

    def upload_photo_idempotent(
        self,
        state_db: StateDB,
        *,
        onedrive_item_id: str,
        filename: str,
        raw_bytes: bytes,
        album_title: str | None = None,
    ) -> dict[str, Any]:
        existing = state_db.get_gphotos_upload(onedrive_item_id)
        if existing:
            return {
                "status": "skipped",
                "media_item_id": existing["gphotos_media_item_id"],
                "album_id": existing.get("album_id"),
            }

        album_id = self.get_or_create_album(state_db, album_title) if album_title else None
        upload_token = self._upload_raw_bytes(filename, raw_bytes)

        body: dict[str, Any] = {
            "newMediaItems": [
                {
                    "simpleMediaItem": {
                        "uploadToken": upload_token,
                        "fileName": filename,
                    }
                }
            ]
        }
        if album_id:
            body["albumId"] = album_id

        resp = self._post_json(
            "https://photoslibrary.googleapis.com/v1/mediaItems:batchCreate",
            body,
        )
        results = resp.get("newMediaItemResults") or []
        if not results:
            raise GooglePhotosError(f"batchCreate returned no results: {resp}")

        first = results[0]
        status = first.get("status") or {}
        if status.get("code") not in (None, 0):
            raise GooglePhotosError(
                f"batchCreate item failed ({status.get('code')}): {status.get('message')}"
            )

        media_item_id = str(first.get("mediaItem", {}).get("id") or "")
        if not media_item_id:
            raise GooglePhotosError(f"batchCreate missing mediaItem.id: {first}")

        state_db.record_gphotos_upload(
            onedrive_item_id=onedrive_item_id,
            gphotos_media_item_id=media_item_id,
            album_id=album_id,
            album_title=album_title,
        )
        return {
            "status": "uploaded",
            "media_item_id": media_item_id,
            "album_id": album_id,
        }
