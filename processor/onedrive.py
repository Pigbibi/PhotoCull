from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}


class OneDriveError(RuntimeError):
    pass


class OneDriveClient:
    """Microsoft Graph client for OneDrive photo discovery, download, and safe trash."""

    def __init__(
        self,
        *,
        client_id: str,
        refresh_token: str,
        tenant: str = "consumers",
        client_secret: str = "",
        timeout: int = 45,
    ) -> None:
        self.client_id = client_id
        self.refresh_token = refresh_token
        self.tenant = tenant or "consumers"
        self.client_secret = client_secret
        self.timeout = timeout
        self._access_token: str | None = None

    def _refresh_access_token(self) -> str:
        if not self.client_id or not self.refresh_token:
            raise OneDriveError("MS_CLIENT_ID and MS_REFRESH_TOKEN are required")
        url = f"https://login.microsoftonline.com/{self.tenant}/oauth2/v2.0/token"
        form: dict[str, str] = {
            "client_id": self.client_id,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token",
            "scope": "Files.ReadWrite offline_access",
        }
        if self.client_secret:
            form["client_secret"] = self.client_secret
        data = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise OneDriveError(f"Microsoft OAuth refresh failed ({exc.code}): {detail}") from exc

        token = payload.get("access_token")
        if not token:
            raise OneDriveError("Microsoft OAuth response missing access_token")
        self._access_token = str(token)
        if payload.get("refresh_token"):
            self.refresh_token = str(payload["refresh_token"])
        return self._access_token

    def _get_token(self) -> str:
        return self._access_token or self._refresh_access_token()

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
        expect_json: bool = True,
    ) -> Any:
        for attempt in range(2):
            token = self._get_token()
            headers = {"Authorization": f"Bearer {token}"}
            if extra_headers:
                headers.update(extra_headers)
            body_bytes = None
            if json_body is not None:
                body_bytes = json.dumps(json_body).encode("utf-8")
                headers["Content-Type"] = "application/json"

            req = urllib.request.Request(url, data=body_bytes, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    if not expect_json:
                        return raw
                    return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as exc:
                # Only refresh token once on 401 for non-destructive reads; never retry DELETE.
                if exc.code == 401 and attempt == 0 and method != "DELETE":
                    self._refresh_access_token()
                    continue
                detail = exc.read().decode("utf-8", errors="replace")
                raise OneDriveError(f"Graph {method} {url} failed ({exc.code}): {detail}") from exc

    def _list_children_url(self, folder_path: str) -> str:
        clean = "/" + folder_path.strip("/")
        encoded = urllib.parse.quote(clean, safe="/")
        if clean == "/":
            return "https://graph.microsoft.com/v1.0/me/drive/root/children?$top=200"
        return f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}:/children?$top=200"

    @staticmethod
    def _is_photo_item(item: dict[str, Any]) -> bool:
        if "folder" in item or "file" not in item:
            return False
        name = str(item.get("name", ""))
        ext = Path(name).suffix.lower()
        mime = str(item.get("file", {}).get("mimeType", "")).lower()
        return ext in IMAGE_EXTENSIONS or mime.startswith("image/")

    def list_photos_recursive(self, source_dirs: tuple[str, ...]) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        def walk_url(next_url: str, prefix: str) -> None:
            url: str | None = next_url
            while url:
                data = self._request("GET", url)
                for item in data.get("value", []):
                    item_id = str(item.get("id", ""))
                    name = str(item.get("name", ""))
                    if "folder" in item and item_id:
                        child_url = (
                            f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/children?$top=200"
                        )
                        walk_url(child_url, f"{prefix.rstrip('/')}/{name}")
                    elif item_id and item_id not in seen_ids and self._is_photo_item(item):
                        seen_ids.add(item_id)
                        hashes = item.get("file", {}).get("hashes", {})
                        results.append(
                            {
                                "id": item_id,
                                "name": name,
                                "path": f"{prefix.rstrip('/')}/{name}",
                                "size": int(item.get("size", 0)),
                                "etag": str(item.get("eTag") or item.get("cTag") or ""),
                                "sha256": str(hashes.get("sha256Hash") or ""),
                                "created_dt": str(
                                    item.get("photo", {}).get("takenDateTime")
                                    or item.get("fileSystemInfo", {}).get("createdDateTime")
                                    or item.get("createdDateTime")
                                    or ""
                                ),
                            }
                        )
                url = data.get("@odata.nextLink")

        for folder in source_dirs:
            walk_url(self._list_children_url(folder), "/" + folder.strip("/"))
        return results

    def download_preview_or_content(self, item_id: str) -> bytes:
        thumb_url = (
            f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/thumbnails/0/large/content"
        )
        try:
            return self._request("GET", thumb_url, expect_json=False)
        except OneDriveError:
            return self.download_original(item_id)

    def download_original(self, item_id: str) -> bytes:
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}/content"
        return self._request("GET", url, expect_json=False)

    def move_to_recycle_bin(self, item_id: str, etag: str = "") -> None:
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}"
        headers = {"If-Match": etag} if etag else {}
        self._request("DELETE", url, extra_headers=headers, expect_json=False)

    def ensure_folder(self, folder_path: str) -> str:
        clean = folder_path.strip("/")
        if not clean:
            data = self._request("GET", "https://graph.microsoft.com/v1.0/me/drive/root")
            return str(data["id"])

        encoded = urllib.parse.quote("/" + clean, safe="/")
        try:
            data = self._request(
                "GET", f"https://graph.microsoft.com/v1.0/me/drive/root:{encoded}"
            )
            return str(data["id"])
        except OneDriveError:
            parts = clean.split("/")
            parent_id = "root"
            for part in parts:
                url = (
                    "https://graph.microsoft.com/v1.0/me/drive/root/children"
                    if parent_id == "root"
                    else f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children"
                )
                created = self._request(
                    "POST",
                    url,
                    json_body={
                        "name": part,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "replace",
                    },
                )
                parent_id = str(created["id"])
            return parent_id

    def move_to_staging_folder(
        self, item_id: str, etag: str = "", staging_dir: str = "/_PhotoCull_Trash"
    ) -> None:
        folder_id = self.ensure_folder(staging_dir)
        url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item_id}"
        headers = {"If-Match": etag} if etag else {}
        self._request(
            "PATCH",
            url,
            json_body={"parentReference": {"id": folder_id}},
            extra_headers=headers,
        )
