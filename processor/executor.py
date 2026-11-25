from __future__ import annotations

import hashlib
from typing import Any

from processor.ai_cull import cull_cluster
from processor.cluster import (
    cluster_photos,
    compute_dhash,
    extract_taken_at,
    make_contact_sheet,
    make_preview_jpeg,
)
from processor.config import Config
from processor.state_db import StateDB


def _default_album_from_taken_at(taken_at: str) -> str:
    prefix = (taken_at or "")[:7]
    if len(prefix) == 7 and prefix[4] == "-":
        return f"{prefix} 旅拍精选"
    return "旅拍精选"


def _cluster_id_for(items: list[dict[str, Any]]) -> str:
    joined = "|".join(sorted(str(i["id"]) for i in items))
    return "cl_" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def scan_and_submit(
    cfg: Config,
    state_db: StateDB,
    onedrive: Any,
    worker_client: Any,
) -> dict[str, int]:
    remote_items = onedrive.list_photos_recursive(cfg.onedrive_source_dirs)
    pending_items: list[dict[str, Any]] = []
    skipped_existing = 0

    for item in remote_items:
        existing = state_db.get_scanned_item(item["id"])
        if existing and existing.get("status") in {"submitted", "approved", "completed"}:
            skipped_existing += 1
            continue

        raw_preview_source = onedrive.download_preview_or_content(item["id"])
        taken_at = extract_taken_at(raw_preview_source, fallback_iso=item.get("created_dt"))
        dhash_val = compute_dhash(raw_preview_source)
        preview_jpeg = make_preview_jpeg(raw_preview_source)

        state_db.upsert_scanned_item(
            onedrive_item_id=item["id"],
            path=item["path"],
            name=item["name"],
            size_bytes=item["size"],
            etag=item["etag"],
            sha256=item.get("sha256", ""),
            taken_at=taken_at,
            dhash=f"{dhash_val:016x}",
            status="discovered",
        )

        pending_items.append(
            {
                "id": item["id"],
                "filename": item["name"],
                "path": item["path"],
                "size": item["size"],
                "etag": item["etag"],
                "sha256": item.get("sha256", ""),
                "taken_at": taken_at,
                "dhash": dhash_val,
                "preview_jpeg": preview_jpeg,
            }
        )

    clusters = cluster_photos(
        pending_items,
        window_seconds=cfg.burst_window_seconds,
        max_hamming=cfg.dhash_max_distance,
    )

    submitted_clusters = 0
    submitted_photos = 0

    for group in clusters:
        if len(group) == 1 and not cfg.include_singletons:
            continue

        cluster_id = _cluster_id_for(group)
        first_taken = group[0]["taken_at"]
        default_album = _default_album_from_taken_at(first_taken)

        if len(group) == 1:
            ai_res = {
                "suggested_album": default_album,
                "picks": [
                    {
                        "index": 1,
                        "action": "keep",
                        "score": 90.0,
                        "reason": "独立拍摄，建议保留并同步精选",
                    }
                ],
            }
            title = f"{first_taken[:16].replace('T', ' ')} · 单张"
        else:
            contact_sheet = make_contact_sheet([m["preview_jpeg"] for m in group])
            ai_res = cull_cluster(
                contact_sheet,
                len(group),
                default_album,
                provider=cfg.ai_provider,
                api_key=cfg.ai_api_key,
                model=cfg.ai_model,
                base_url=cfg.ai_base_url,
            )
            title = f"{first_taken[:16].replace('T', ' ')} · 连拍 {len(group)} 张"

        picks_by_idx = {p["index"]: p for p in ai_res["picks"]}
        photo_payloads = []
        for idx, member in enumerate(group, start=1):
            preview_key = f"{cluster_id}/{member['id']}.jpg"
            worker_client.upload_preview(preview_key, member["preview_jpeg"])
            pick = picks_by_idx.get(idx, {"action": "keep", "score": 50.0, "reason": "默认保留"})
            photo_payloads.append(
                {
                    "id": f"ph_{hashlib.sha256(member['id'].encode('utf-8')).hexdigest()[:16]}",
                    "onedrive_item_id": member["id"],
                    "onedrive_etag": member["etag"],
                    "onedrive_path": member["path"],
                    "filename": member["filename"],
                    "size_bytes": member["size"],
                    "sha256": member["sha256"],
                    "taken_at": member["taken_at"],
                    "preview_key": preview_key,
                    "recommended_action": pick["action"],
                    "ai_score": pick["score"],
                    "ai_reason": pick["reason"],
                    "sort_order": idx - 1,
                }
            )

        worker_client.submit_cluster(
            {
                "id": cluster_id,
                "title": title,
                "album_name": ai_res["suggested_album"],
                "captured_at": first_taken,
                "photos": photo_payloads,
            }
        )

        for member in group:
            state_db.mark_item_status(member["id"], "submitted", cluster_id=cluster_id)

        submitted_clusters += 1
        submitted_photos += len(group)

    return {
        "scanned_remote": len(remote_items),
        "skipped_existing": skipped_existing,
        "submitted_clusters": submitted_clusters,
        "submitted_photos": submitted_photos,
    }


def execute_approved(
    cfg: Config,
    state_db: StateDB,
    onedrive: Any,
    gphotos: Any | None,
    worker_client: Any,
) -> dict[str, int]:
    approved_clusters = worker_client.fetch_approved_clusters()
    processed_clusters = 0
    trashed_count = 0
    uploaded_count = 0
    failed_count = 0

    for cluster in approved_clusters:
        cluster_id = cluster["id"]
        album_name = cluster.get("album_name") or "旅拍精选"
        results = []
        any_failure = False

        for photo in cluster.get("photos", []):
            photo_id = photo["id"]
            item_id = photo["onedrive_item_id"]
            etag = photo.get("onedrive_etag") or ""
            action = photo.get("effective_action") or photo.get("user_action") or photo["recommended_action"]

            onedrive_status = photo.get("onedrive_status") or "pending"
            gphotos_status = photo.get("gphotos_status") or "pending"
            media_item_id = photo.get("gphotos_media_item_id")
            error_message = None

            if action == "trash":
                if onedrive_status not in {"trashed", "staged"}:
                    try:
                        if cfg.onedrive_trash_mode == "staging_folder":
                            onedrive.move_to_staging_folder(
                                item_id, etag=etag, staging_dir=cfg.onedrive_staging_dir
                            )
                            onedrive_status = "staged"
                        else:
                            onedrive.move_to_recycle_bin(item_id, etag=etag)
                            onedrive_status = "trashed"
                        trashed_count += 1
                        state_db.mark_item_status(item_id, "completed", cluster_id=cluster_id)
                    except Exception as exc:
                        onedrive_status = "failed"
                        error_message = str(exc)
                        any_failure = True
                        failed_count += 1
                gphotos_status = "skipped"
            else:
                onedrive_status = "kept"
                if cfg.gphotos_enabled and gphotos is not None:
                    if gphotos_status != "uploaded":
                        try:
                            raw_original = onedrive.download_original(item_id)
                            up = gphotos.upload_photo_idempotent(
                                state_db,
                                onedrive_item_id=item_id,
                                filename=photo["filename"],
                                raw_bytes=raw_original,
                                album_title=album_name,
                            )
                            media_item_id = up.get("media_item_id")
                            gphotos_status = "uploaded"
                            if up.get("status") == "uploaded":
                                uploaded_count += 1
                            state_db.mark_item_status(item_id, "completed", cluster_id=cluster_id)
                        except Exception as exc:
                            gphotos_status = "failed"
                            error_message = str(exc)
                            any_failure = True
                            failed_count += 1
                else:
                    gphotos_status = "skipped"
                    state_db.mark_item_status(item_id, "completed", cluster_id=cluster_id)

            results.append(
                {
                    "photo_id": photo_id,
                    "onedrive_status": onedrive_status,
                    "gphotos_status": gphotos_status,
                    "gphotos_media_item_id": media_item_id,
                    "error_message": error_message,
                }
            )

        worker_client.report_cluster_result(
            cluster_id,
            {
                "status": "partial_failed" if any_failure else "completed",
                "delete_previews": not any_failure,
                "results": results,
            },
        )
        processed_clusters += 1

    return {
        "processed_clusters": processed_clusters,
        "trashed_count": trashed_count,
        "uploaded_count": uploaded_count,
        "failed_count": failed_count,
    }
