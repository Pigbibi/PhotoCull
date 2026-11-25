-- PhotoCull D1 Schema

CREATE TABLE IF NOT EXISTS clusters (
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  album_name TEXT NOT NULL,
  captured_at TEXT,
  photo_count INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'pending_review'
    CHECK (status IN ('pending_review', 'approved', 'executing', 'completed', 'partial_failed')),
  approved_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
  updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_clusters_status_captured
  ON clusters (status, captured_at DESC);

CREATE TABLE IF NOT EXISTS photos (
  id TEXT PRIMARY KEY,
  cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  onedrive_item_id TEXT NOT NULL UNIQUE,
  onedrive_etag TEXT NOT NULL DEFAULT '',
  onedrive_path TEXT NOT NULL,
  filename TEXT NOT NULL,
  size_bytes INTEGER NOT NULL DEFAULT 0,
  sha256 TEXT NOT NULL DEFAULT '',
  taken_at TEXT,
  preview_key TEXT NOT NULL,
  recommended_action TEXT NOT NULL CHECK (recommended_action IN ('keep', 'trash')),
  user_action TEXT CHECK (user_action IN ('keep', 'trash') OR user_action IS NULL),
  ai_score REAL NOT NULL DEFAULT 0,
  ai_reason TEXT NOT NULL DEFAULT '',
  onedrive_status TEXT NOT NULL DEFAULT 'pending',
  gphotos_status TEXT NOT NULL DEFAULT 'pending',
  gphotos_media_item_id TEXT,
  error_message TEXT,
  sort_order INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_photos_cluster_sort
  ON photos (cluster_id, sort_order ASC);

CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  cluster_id TEXT NOT NULL REFERENCES clusters(id) ON DELETE CASCADE,
  photo_id TEXT,
  action TEXT NOT NULL,
  outcome TEXT NOT NULL,
  detail TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_execution_logs_created
  ON execution_logs (created_at DESC, id DESC);

CREATE TABLE IF NOT EXISTS sessions (
  id TEXT PRIMARY KEY,
  github_login TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
