from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


class WorkerClientError(RuntimeError):
    pass


class WorkerClient:
    """HTTP client talking to the PhotoCull Cloudflare Worker processor API."""

    def __init__(self, base_url: str, processor_token: str, timeout: int = 30) -> None:
        if not base_url or not processor_token:
            raise ValueError("WORKER_BASE_URL and PROCESSOR_TOKEN are required")
        self.base_url = base_url.rstrip("/")
        self.processor_token = processor_token
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        *,
        data: bytes | None = None,
        json_body: dict[str, Any] | None = None,
        content_type: str = "application/json",
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.processor_token}",
            "User-Agent": "PhotoCull-Processor/1.0",
        }
        payload = data
        if json_body is not None:
            payload = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        elif data is not None:
            headers["Content-Type"] = content_type

        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                raw = resp.read().decode("utf-8")
                return json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise WorkerClientError(f"Worker {method} {path} failed ({exc.code}): {detail}") from exc

    def upload_preview(self, key: str, jpeg_bytes: bytes) -> dict[str, Any]:
        encoded_key = urllib.parse.quote(key, safe="/")
        return self._request(
            "PUT",
            f"/api/processor/previews/{encoded_key}",
            data=jpeg_bytes,
            content_type="image/jpeg",
        )

    def submit_cluster(self, cluster_payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/processor/clusters", json_body=cluster_payload)

    def fetch_approved_clusters(self) -> list[dict[str, Any]]:
        resp = self._request("GET", "/api/processor/approved")
        return list(resp.get("clusters", []))

    def report_cluster_result(self, cluster_id: str, result_payload: dict[str, Any]) -> dict[str, Any]:
        encoded_id = urllib.parse.quote(cluster_id, safe="")
        return self._request(
            "POST",
            f"/api/processor/clusters/{encoded_id}/result",
            json_body=result_payload,
        )
