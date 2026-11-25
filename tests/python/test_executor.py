from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from processor.config import Config
from processor.executor import execute_approved
from processor.gphotos import GooglePhotosClient
from processor.state_db import StateDB


class FakeOneDrive:
    def __init__(self) -> None:
        self.deleted: list[tuple[str, str]] = []
        self.staged: list[tuple[str, str, str]] = []
        self.downloads: list[str] = []

    def move_to_recycle_bin(self, item_id: str, etag: str = "") -> None:
        self.deleted.append((item_id, etag))

    def move_to_staging_folder(self, item_id: str, etag: str = "", staging_dir: str = "/_PhotoCull_Trash") -> None:
        self.staged.append((item_id, etag, staging_dir))

    def download_original(self, item_id: str) -> bytes:
        self.downloads.append(item_id)
        return b"RAW-JPEG-BYTES"


class FakeWorkerClient:
    def __init__(self, approved: list[dict]) -> None:
        self.approved = approved
        self.reported: list[tuple[str, dict]] = []

    def fetch_approved_clusters(self) -> list[dict]:
        return self.approved

    def report_cluster_result(self, cluster_id: str, payload: dict) -> dict:
        self.reported.append((cluster_id, payload))
        return {"ok": True}


class FakeGooglePhotos(GooglePhotosClient):
    def __init__(self) -> None:
        super().__init__(client_id="cid", client_secret="csec", refresh_token="rtok")
        self.raw_uploads: list[tuple[str, bytes]] = []
        self.posts: list[tuple[str, dict]] = []
        self._counter = 0

    def _upload_raw_bytes(self, filename: str, raw_bytes: bytes) -> str:
        self.raw_uploads.append((filename, raw_bytes))
        return f"tok_{len(self.raw_uploads)}"

    def _post_json(self, url: str, payload: dict) -> dict:
        self.posts.append((url, payload))
        if url.endswith("/v1/albums"):
            return {"id": "ALBUM_1"}
        self._counter += 1
        return {
            "newMediaItemResults": [
                {
                    "status": {"message": "Success"},
                    "mediaItem": {"id": f"GP_MEDIA_{self._counter}"},
                }
            ]
        }


def _make_cfg(db_path: Path, trash_mode: str = "recycle_bin") -> Config:
    return Config(
        ms_client_id="ms",
        ms_client_secret="",
        ms_tenant="consumers",
        ms_refresh_token="rt",
        onedrive_source_dirs=("/Pictures/Camera Roll",),
        onedrive_trash_mode=trash_mode,
        onedrive_staging_dir="/_PhotoCull_Trash",
        gphotos_enabled=True,
        google_client_id="gc",
        google_client_secret="gs",
        google_refresh_token="gr",
        ai_provider="gemini",
        ai_api_key="",
        ai_model="gemini-2.5-flash",
        ai_base_url="",
        worker_base_url="https://worker.example",
        processor_token="tok",
        burst_window_seconds=90,
        dhash_max_distance=12,
        include_singletons=True,
        local_state_db=db_path,
    )


class ExecutorTests(unittest.TestCase):
    def test_gphotos_two_step_upload_and_idempotent_skip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = StateDB(Path(tmp) / "state.sqlite3")
            gp = FakeGooglePhotos()

            first = gp.upload_photo_idempotent(
                db,
                onedrive_item_id="OD_1",
                filename="IMG_001.jpg",
                raw_bytes=b"abc",
                album_title="2026-10 旅拍精选",
            )
            self.assertEqual(first["status"], "uploaded")
            self.assertEqual(first["media_item_id"], "GP_MEDIA_1")
            self.assertEqual(len(gp.raw_uploads), 1)
            self.assertEqual(len(gp.posts), 2)  # album create + batchCreate

            second = gp.upload_photo_idempotent(
                db,
                onedrive_item_id="OD_1",
                filename="IMG_001.jpg",
                raw_bytes=b"abc",
                album_title="2026-10 旅拍精选",
            )
            self.assertEqual(second["status"], "skipped")
            self.assertEqual(second["media_item_id"], "GP_MEDIA_1")
            self.assertEqual(len(gp.raw_uploads), 1)
            db.close()

    def test_execute_approved_recycle_bin_and_gphotos(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            db = StateDB(db_path)
            cfg = _make_cfg(db_path, trash_mode="recycle_bin")
            onedrive = FakeOneDrive()
            gp = FakeGooglePhotos()
            worker = FakeWorkerClient(
                [
                    {
                        "id": "cl_1",
                        "album_name": "2026-10 旅拍精选",
                        "photos": [
                            {
                                "id": "ph_keep",
                                "onedrive_item_id": "OD_KEEP",
                                "onedrive_etag": '"etag-1"',
                                "filename": "keep.jpg",
                                "recommended_action": "keep",
                                "effective_action": "keep",
                                "onedrive_status": "pending",
                                "gphotos_status": "pending",
                            },
                            {
                                "id": "ph_trash",
                                "onedrive_item_id": "OD_TRASH",
                                "onedrive_etag": '"etag-2"',
                                "filename": "trash.jpg",
                                "recommended_action": "trash",
                                "effective_action": "trash",
                                "onedrive_status": "pending",
                                "gphotos_status": "pending",
                            },
                        ],
                    }
                ]
            )

            summary = execute_approved(cfg, db, onedrive, gp, worker)
            self.assertEqual(summary["trashed_count"], 1)
            self.assertEqual(summary["uploaded_count"], 1)
            self.assertEqual(summary["failed_count"], 0)
            self.assertEqual(onedrive.deleted, [("OD_TRASH", '"etag-2"')])
            self.assertEqual(onedrive.downloads, ["OD_KEEP"])

            cid, report = worker.reported[0]
            self.assertEqual(cid, "cl_1")
            self.assertEqual(report["status"], "completed")
            self.assertTrue(report["delete_previews"])
            db.close()

    def test_execute_approved_staging_folder_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "state.sqlite3"
            db = StateDB(db_path)
            cfg = _make_cfg(db_path, trash_mode="staging_folder")
            onedrive = FakeOneDrive()
            worker = FakeWorkerClient(
                [
                    {
                        "id": "cl_2",
                        "album_name": "2026-10 旅拍精选",
                        "photos": [
                            {
                                "id": "ph_trash",
                                "onedrive_item_id": "OD_TRASH_2",
                                "onedrive_etag": '"etag-9"',
                                "filename": "blur.jpg",
                                "recommended_action": "trash",
                                "effective_action": "trash",
                                "onedrive_status": "pending",
                                "gphotos_status": "pending",
                            }
                        ],
                    }
                ]
            )

            summary = execute_approved(cfg, db, onedrive, None, worker)
            self.assertEqual(summary["trashed_count"], 1)
            self.assertEqual(onedrive.staged, [("OD_TRASH_2", '"etag-9"', "/_PhotoCull_Trash")])
            self.assertEqual(worker.reported[0][1]["results"][0]["onedrive_status"], "staged")
            db.close()


if __name__ == "__main__":
    unittest.main()
