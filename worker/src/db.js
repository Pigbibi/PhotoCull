const VALID_ACTIONS = new Set(["keep", "trash"]);
const VALID_CLUSTER_STATUSES = new Set([
  "pending_review",
  "approved",
  "executing",
  "completed",
  "partial_failed",
]);

function nowIso() {
  return new Date().toISOString();
}

export async function getOverview(db) {
  const clusterStats = await db
    .prepare(
      `SELECT
        SUM(CASE WHEN status = 'pending_review' THEN 1 ELSE 0 END) AS pending_clusters,
        SUM(CASE WHEN status IN ('approved', 'executing') THEN 1 ELSE 0 END) AS queued_clusters,
        SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_clusters
      FROM clusters`,
    )
    .first();

  const photoStats = await db
    .prepare(
      `SELECT
        SUM(CASE WHEN c.status = 'pending_review' AND COALESCE(p.user_action, p.recommended_action) = 'trash' THEN 1 ELSE 0 END) AS pending_trash_photos,
        SUM(CASE WHEN c.status = 'pending_review' AND COALESCE(p.user_action, p.recommended_action) = 'trash' THEN p.size_bytes ELSE 0 END) AS pending_trash_bytes,
        SUM(CASE WHEN p.gphotos_status = 'uploaded' THEN 1 ELSE 0 END) AS synced_gphotos_photos,
        SUM(CASE WHEN p.onedrive_status IN ('trashed', 'staged') THEN p.size_bytes ELSE 0 END) AS freed_bytes
      FROM photos p
      JOIN clusters c ON c.id = p.cluster_id`,
    )
    .first();

  return {
    pending_clusters: Number(clusterStats?.pending_clusters || 0),
    queued_clusters: Number(clusterStats?.queued_clusters || 0),
    completed_clusters: Number(clusterStats?.completed_clusters || 0),
    pending_trash_photos: Number(photoStats?.pending_trash_photos || 0),
    pending_trash_bytes: Number(photoStats?.pending_trash_bytes || 0),
    synced_gphotos_photos: Number(photoStats?.synced_gphotos_photos || 0),
    freed_bytes: Number(photoStats?.freed_bytes || 0),
  };
}

export async function listClusters(db, status = "pending_review") {
  let clusterQuery;
  if (status === "history") {
    clusterQuery = db.prepare(
      `SELECT * FROM clusters
       WHERE status IN ('approved', 'executing', 'completed', 'partial_failed')
       ORDER BY updated_at DESC, captured_at DESC
       LIMIT 50`,
    );
  } else if (status === "all") {
    clusterQuery = db.prepare(
      `SELECT * FROM clusters
       ORDER BY captured_at DESC, created_at DESC
       LIMIT 50`,
    );
  } else {
    const safeStatus = VALID_CLUSTER_STATUSES.has(status) ? status : "pending_review";
    clusterQuery = db
      .prepare(
        `SELECT * FROM clusters
         WHERE status = ?
         ORDER BY captured_at DESC, created_at DESC
         LIMIT 50`,
      )
      .bind(safeStatus);
  }

  const clusterResult = await clusterQuery.all();
  const clusters = clusterResult?.results || [];
  if (clusters.length === 0) return [];

  const placeholders = clusters.map(() => "?").join(", ");
  const ids = clusters.map((c) => c.id);
  const photoResult = await db
    .prepare(
      `SELECT * FROM photos
       WHERE cluster_id IN (${placeholders})
       ORDER BY sort_order ASC, filename ASC`,
    )
    .bind(...ids)
    .all();

  const byCluster = new Map();
  for (const photo of photoResult?.results || []) {
    if (!byCluster.has(photo.cluster_id)) {
      byCluster.set(photo.cluster_id, []);
    }
    byCluster.get(photo.cluster_id).push({
      ...photo,
      effective_action: photo.user_action || photo.recommended_action,
    });
  }

  return clusters.map((c) => ({
    ...c,
    photos: byCluster.get(c.id) || [],
  }));
}

export async function getClusterById(db, clusterId) {
  const cluster = await db
    .prepare("SELECT * FROM clusters WHERE id = ?")
    .bind(clusterId)
    .first();
  if (!cluster) return null;

  const photoResult = await db
    .prepare(
      "SELECT * FROM photos WHERE cluster_id = ? ORDER BY sort_order ASC, filename ASC",
    )
    .bind(clusterId)
    .all();

  return {
    ...cluster,
    photos: (photoResult?.results || []).map((p) => ({
      ...p,
      effective_action: p.user_action || p.recommended_action,
    })),
  };
}

export async function approveCluster(db, clusterId, { albumName, decisions } = {}) {
  const existing = await getClusterById(db, clusterId);
  if (!existing) {
    return { error: "Cluster not found", status: 404 };
  }
  if (!["pending_review", "approved"].includes(existing.status)) {
    return { error: `Cluster is ${existing.status}, cannot modify`, status: 409 };
  }

  const now = nowIso();
  const nextAlbum =
    typeof albumName === "string" && albumName.trim()
      ? albumName.trim()
      : existing.album_name;

  for (const photo of existing.photos) {
    const decision = decisions?.[photo.id] ?? photo.user_action ?? photo.recommended_action;
    if (!VALID_ACTIONS.has(decision)) {
      return { error: `Invalid action '${decision}' for photo ${photo.id}`, status: 400 };
    }
    await db
      .prepare("UPDATE photos SET user_action = ? WHERE id = ? AND cluster_id = ?")
      .bind(decision, photo.id, clusterId)
      .run();
  }

  await db
    .prepare(
      `UPDATE clusters
       SET album_name = ?, status = 'approved', approved_at = ?, updated_at = ?
       WHERE id = ?`,
    )
    .bind(nextAlbum, now, now, clusterId)
    .run();

  await db
    .prepare(
      `INSERT INTO execution_logs (cluster_id, photo_id, action, outcome, detail, created_at)
       VALUES (?, NULL, 'approve', 'ok', ?, ?)`,
    )
    .bind(clusterId, `Confirmed album "${nextAlbum}"`, now)
    .run();

  return { cluster: await getClusterById(db, clusterId) };
}

export async function approveAllRecommended(db) {
  const pendingResult = await db
    .prepare("SELECT id, album_name FROM clusters WHERE status = 'pending_review'")
    .all();
  const pending = pendingResult?.results || [];
  const now = nowIso();

  for (const c of pending) {
    await db
      .prepare(
        `UPDATE photos
         SET user_action = recommended_action
         WHERE cluster_id = ? AND user_action IS NULL`,
      )
      .bind(c.id)
      .run();

    await db
      .prepare(
        `UPDATE clusters
         SET status = 'approved', approved_at = ?, updated_at = ?
         WHERE id = ?`,
      )
      .bind(now, now, c.id)
      .run();

    await db
      .prepare(
        `INSERT INTO execution_logs (cluster_id, photo_id, action, outcome, detail, created_at)
         VALUES (?, NULL, 'approve_all', 'ok', ?, ?)`,
      )
      .bind(c.id, `Auto-confirmed AI recommendations for "${c.album_name}"`, now)
      .run();
  }

  return { approved_count: pending.length };
}

export async function listLogs(db, limit = 50) {
  const safeLimit = Math.min(Math.max(Number(limit) || 50, 1), 200);
  const res = await db
    .prepare(
      `SELECT l.*, c.title AS cluster_title
       FROM execution_logs l
       LEFT JOIN clusters c ON c.id = l.cluster_id
       ORDER BY l.created_at DESC, l.id DESC
       LIMIT ?`,
    )
    .bind(safeLimit)
    .all();
  return res?.results || [];
}

export async function upsertClusterFromProcessor(db, payload) {
  if (!payload?.id || !payload?.title || !Array.isArray(payload?.photos) || payload.photos.length === 0) {
    return { error: "Missing required cluster fields or photos", status: 400 };
  }

  const existing = await db
    .prepare("SELECT id, status FROM clusters WHERE id = ?")
    .bind(payload.id)
    .first();
  if (existing && existing.status !== "pending_review") {
    return { cluster: await getClusterById(db, payload.id), skipped: true };
  }

  const now = nowIso();
  const albumName =
    typeof payload.album_name === "string" && payload.album_name.trim()
      ? payload.album_name.trim()
      : "旅拍精选";

  await db
    .prepare(
      `INSERT INTO clusters (id, title, album_name, captured_at, photo_count, status, created_at, updated_at)
       VALUES (?, ?, ?, ?, ?, 'pending_review', ?, ?)
       ON CONFLICT(id) DO UPDATE SET
         title = excluded.title,
         album_name = excluded.album_name,
         captured_at = excluded.captured_at,
         photo_count = excluded.photo_count,
         updated_at = excluded.updated_at`,
    )
    .bind(
      payload.id,
      payload.title,
      albumName,
      payload.captured_at || now,
      payload.photos.length,
      now,
      now,
    )
    .run();

  for (let i = 0; i < payload.photos.length; i += 1) {
    const p = payload.photos[i];
    const rec = VALID_ACTIONS.has(p.recommended_action) ? p.recommended_action : "keep";
    await db
      .prepare(
        `INSERT INTO photos (
           id, cluster_id, onedrive_item_id, onedrive_etag, onedrive_path,
           filename, size_bytes, sha256, taken_at, preview_key,
           recommended_action, ai_score, ai_reason, sort_order, created_at
         ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(onedrive_item_id) DO UPDATE SET
           cluster_id = excluded.cluster_id,
           onedrive_etag = excluded.onedrive_etag,
           onedrive_path = excluded.onedrive_path,
           filename = excluded.filename,
           size_bytes = excluded.size_bytes,
           sha256 = excluded.sha256,
           taken_at = excluded.taken_at,
           preview_key = excluded.preview_key,
           recommended_action = excluded.recommended_action,
           ai_score = excluded.ai_score,
           ai_reason = excluded.ai_reason,
           sort_order = excluded.sort_order
         WHERE photos.onedrive_status = 'pending' AND photos.gphotos_status = 'pending'`,
      )
      .bind(
        p.id,
        payload.id,
        p.onedrive_item_id,
        p.onedrive_etag || "",
        p.onedrive_path || p.filename,
        p.filename,
        Number(p.size_bytes || 0),
        p.sha256 || "",
        p.taken_at || null,
        p.preview_key,
        rec,
        Number(p.ai_score || 0),
        p.ai_reason || "",
        Number.isFinite(p.sort_order) ? p.sort_order : i,
        now,
      )
      .run();
  }

  return { cluster: await getClusterById(db, payload.id), skipped: false };
}

export async function getApprovedClustersForProcessor(db) {
  return listClusters(db, "approved");
}

export async function recordClusterResult(db, r2Bucket, clusterId, payload) {
  const existing = await getClusterById(db, clusterId);
  if (!existing) {
    return { error: "Cluster not found", status: 404 };
  }

  const now = nowIso();
  const results = Array.isArray(payload?.results) ? payload.results : [];
  let hasFailure = false;

  for (const item of results) {
    if (!item?.photo_id) continue;
    const onedriveStatus = item.onedrive_status || "kept";
    const gphotosStatus = item.gphotos_status || "skipped";
    const errorMsg = item.error_message || null;
    if (onedriveStatus === "failed" || gphotosStatus === "failed") {
      hasFailure = true;
    }

    await db
      .prepare(
        `UPDATE photos
         SET onedrive_status = ?,
             gphotos_status = ?,
             gphotos_media_item_id = COALESCE(?, gphotos_media_item_id),
             error_message = ?
         WHERE id = ? AND cluster_id = ?`,
      )
      .bind(
        onedriveStatus,
        gphotosStatus,
        item.gphotos_media_item_id || null,
        errorMsg,
        item.photo_id,
        clusterId,
      )
      .run();

    const outcome =
      onedriveStatus === "failed" || gphotosStatus === "failed" ? "error" : "ok";
    const detail = `OneDrive:${onedriveStatus} / GooglePhotos:${gphotosStatus}${errorMsg ? ` (${errorMsg})` : ""}`;

    await db
      .prepare(
        `INSERT INTO execution_logs (cluster_id, photo_id, action, outcome, detail, created_at)
         VALUES (?, ?, 'execute_photo', ?, ?, ?)`,
      )
      .bind(clusterId, item.photo_id, outcome, detail, now)
      .run();
  }

  const nextStatus =
    payload?.status === "partial_failed" || hasFailure ? "partial_failed" : "completed";
  const completedAt = nextStatus === "completed" ? now : null;

  await db
    .prepare(
      `UPDATE clusters
       SET status = ?, completed_at = COALESCE(?, completed_at), updated_at = ?
       WHERE id = ?`,
    )
    .bind(nextStatus, completedAt, now, clusterId)
    .run();

  await db
    .prepare(
      `INSERT INTO execution_logs (cluster_id, photo_id, action, outcome, detail, created_at)
       VALUES (?, NULL, 'cluster_complete', ?, ?, ?)`,
    )
    .bind(
      clusterId,
      nextStatus === "completed" ? "ok" : "error",
      `Cluster finished with status ${nextStatus}`,
      now,
    )
    .run();

  if (nextStatus === "completed" && payload?.delete_previews !== false && r2Bucket) {
    for (const p of existing.photos) {
      if (p.preview_key) {
        try {
          await r2Bucket.delete(p.preview_key);
        } catch {
          // Best-effort cleanup; ignore missing or transient R2 deletion issues.
        }
      }
    }
  }

  return { cluster: await getClusterById(db, clusterId) };
}
