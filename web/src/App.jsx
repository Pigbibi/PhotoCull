import React, { useCallback, useEffect, useState } from "react";
import {
  approveAllRecommended,
  approveCluster,
  fetchAuthMe,
  fetchClusters,
  fetchLogs,
  fetchOverview,
  logout,
  previewUrl,
} from "./api.js";

function formatBytes(bytes) {
  const num = Number(bytes || 0);
  if (num <= 0) return "0 MB";
  const mb = num / (1024 * 1024);
  if (mb < 1024) return `${mb.toFixed(1)} MB`;
  return `${(mb / 1024).toFixed(2)} GB`;
}

function statusBadge(status) {
  switch (status) {
    case "approved":
      return { text: "待 Mac mini 执行", cls: "warn" };
    case "executing":
      return { text: "执行中", cls: "warn" };
    case "completed":
      return { text: "已同步清理", cls: "keep" };
    case "partial_failed":
      return { text: "部分失败", cls: "trash" };
    default:
      return { text: "待确认", cls: "warn" };
  }
}

export default function App() {
  const [auth, setAuth] = useState({ loading: true, authenticated: false, login_enabled: false, user: null });
  const [tab, setTab] = useState("pending");
  const [overview, setOverview] = useState(null);
  const [pendingClusters, setPendingClusters] = useState([]);
  const [historyClusters, setHistoryClusters] = useState([]);
  const [logs, setLogs] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [busyId, setBusyId] = useState(null);
  const [notice, setNotice] = useState(null);
  const [error, setError] = useState(null);
  const [lightboxPhoto, setLightboxPhoto] = useState(null);

  const loadData = useCallback(async () => {
    setError(null);
    try {
      const me = await fetchAuthMe();
      setAuth({ loading: false, ...me });
      if (!me.authenticated) return;

      const [ov, pendingRes, historyRes, logsRes] = await Promise.all([
        fetchOverview(),
        fetchClusters("pending_review"),
        fetchClusters("history"),
        fetchLogs(30),
      ]);
      setOverview(ov);
      const pending = pendingRes.clusters || [];
      setPendingClusters(pending);
      setHistoryClusters(historyRes.clusters || []);
      setLogs(logsRes.logs || []);

      setDrafts((prev) => {
        const next = {};
        for (const c of pending) {
          const existing = prev[c.id];
          const decisions = {};
          for (const p of c.photos) {
            decisions[p.id] = existing?.decisions?.[p.id] || p.user_action || p.recommended_action;
          }
          next[c.id] = {
            album_name: existing?.album_name ?? c.album_name,
            decisions,
          };
        }
        return next;
      });
    } catch (err) {
      setAuth((prev) => ({ ...prev, loading: false }));
      setError(err.message || "加载数据失败");
    }
  }, []);

  useEffect(() => {
    loadData();
  }, [loadData]);

  function togglePhoto(clusterId, photoId) {
    setDrafts((prev) => {
      const current = prev[clusterId];
      if (!current) return prev;
      const curAction = current.decisions[photoId] || "keep";
      return {
        ...prev,
        [clusterId]: {
          ...current,
          decisions: {
            ...current.decisions,
            [photoId]: curAction === "keep" ? "trash" : "keep",
          },
        },
      };
    });
  }

  function updateAlbumName(clusterId, value) {
    setDrafts((prev) => ({
      ...prev,
      [clusterId]: {
        ...(prev[clusterId] || {}),
        album_name: value,
      },
    }));
  }

  async function handleConfirmCluster(cluster) {
    const draft = drafts[cluster.id];
    if (!draft) return;
    setBusyId(cluster.id);
    setError(null);
    setNotice(null);
    try {
      await approveCluster(cluster.id, {
        album_name: draft.album_name,
        decisions: draft.decisions,
      });
      setNotice(`已确认「${cluster.title}」，Mac mini 下次轮询将自动清理并同步精选。`);
      await loadData();
    } catch (err) {
      setError(err.message || "确认失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handleApproveAll() {
    setBusyId("ALL");
    setError(null);
    setNotice(null);
    try {
      const res = await approveAllRecommended();
      setNotice(`已按 AI 推荐批量确认 ${res.approved_count || 0} 组连拍。`);
      await loadData();
    } catch (err) {
      setError(err.message || "批量确认失败");
    } finally {
      setBusyId(null);
    }
  }

  async function handleLogout() {
    await logout();
    await loadData();
  }

  if (auth.loading) {
    return (
      <div className="app-shell">
        <div className="empty-card">
          <p>正在连接 PhotoCull...</p>
        </div>
      </div>
    );
  }

  if (!auth.authenticated) {
    return (
      <div className="app-shell">
        <div className="login-card">
          <h2>PhotoCull · 旅拍精选台</h2>
          <p>
            OneDrive 原片由 Mac mini 自动聚类与 AI 初筛，确认后移入回收站并同步精选到 Google Photos。
          </p>
          {auth.login_enabled ? (
            <a className="btn btn-primary" href="/api/auth/login">
              使用 GitHub 登录
            </a>
          ) : (
            <p>Worker 尚未配置 GitHub OAuth 密钥，请按 docs/setup.md 完成配置。</p>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand">
          <h1>PhotoCull</h1>
          <p>{auth.user?.login ? `@${auth.user.login}` : "旅拍精选台"}</p>
        </div>
        <div className="topbar-actions">
          <button className="btn btn-ghost" type="button" onClick={loadData}>
            刷新
          </button>
          <button className="btn btn-ghost" type="button" onClick={handleLogout}>
            退出
          </button>
        </div>
      </header>

      <section className="stats-grid" aria-label="概览统计">
        <div className="stat-card">
          <div className="stat-label">待审组数</div>
          <div className="stat-value">{overview?.pending_clusters ?? 0}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">预计释放</div>
          <div className="stat-value">{formatBytes(overview?.pending_trash_bytes)}</div>
        </div>
        <div className="stat-card">
          <div className="stat-label">已传 Google Photos</div>
          <div className="stat-value">{overview?.synced_gphotos_photos ?? 0}</div>
        </div>
      </section>

      <div className="tabs" role="tablist">
        <button
          type="button"
          className={`tab-btn ${tab === "pending" ? "active" : ""}`}
          onClick={() => setTab("pending")}
        >
          待审 ({pendingClusters.length})
        </button>
        <button
          type="button"
          className={`tab-btn ${tab === "history" ? "active" : ""}`}
          onClick={() => setTab("history")}
        >
          已确认 / 记录 ({historyClusters.length})
        </button>
      </div>

      {error && (
        <div className="banner error" role="alert">
          <span>{error}</span>
          <button className="btn btn-ghost" type="button" onClick={() => setError(null)}>
            关闭
          </button>
        </div>
      )}

      {notice && (
        <div className="banner" role="status">
          <span>{notice}</span>
          <button className="btn btn-ghost" type="button" onClick={() => setNotice(null)}>
            知道了
          </button>
        </div>
      )}

      {tab === "pending" && (
        <>
          {pendingClusters.length > 0 && (
            <div className="bulk-bar">
              <span>轻点照片可切换「保留 / 淘汰」</span>
              <button
                type="button"
                className="btn btn-secondary"
                disabled={busyId === "ALL"}
                onClick={handleApproveAll}
              >
                {busyId === "ALL" ? "提交中..." : "全部按 AI 推荐确认"}
              </button>
            </div>
          )}

          {pendingClusters.length === 0 ? (
            <div className="empty-card">
              <h2>当前没有待审连拍</h2>
              <p>小米 14T 新拍的照片备份到 OneDrive 后，Mac mini 后台会在下次扫描后自动推送到此处。</p>
            </div>
          ) : (
            <div className="cluster-list">
              {pendingClusters.map((cluster) => {
                const draft = drafts[cluster.id] || { album_name: cluster.album_name, decisions: {} };
                let keepCount = 0;
                let trashCount = 0;
                for (const p of cluster.photos) {
                  const act = draft.decisions[p.id] || p.recommended_action;
                  if (act === "keep") keepCount += 1;
                  else trashCount += 1;
                }

                return (
                  <article className="cluster-card" key={cluster.id}>
                    <div className="cluster-header">
                      <div>
                        <h2 className="cluster-title">{cluster.title}</h2>
                        <div className="cluster-meta">
                          共 {cluster.photos.length} 张 · 保留 {keepCount} · 淘汰 {trashCount}
                        </div>
                      </div>
                    </div>

                    <div className="album-row">
                      <label htmlFor={`album-${cluster.id}`}>Google 相册</label>
                      <input
                        id={`album-${cluster.id}`}
                        className="album-input"
                        value={draft.album_name || ""}
                        onChange={(e) => updateAlbumName(cluster.id, e.target.value)}
                      />
                    </div>

                    <div className="photo-grid">
                      {cluster.photos.map((photo) => {
                        const action = draft.decisions[photo.id] || photo.recommended_action;
                        const isKeep = action === "keep";
                        const aiDiffers = action !== photo.recommended_action;
                        return (
                          <div
                            key={photo.id}
                            role="button"
                            tabIndex={0}
                            className={`photo-tile ${isKeep ? "keep" : "trash"}`}
                            onClick={() => togglePhoto(cluster.id, photo.id)}
                            onKeyDown={(e) => {
                              if (e.key === "Enter" || e.key === " ") {
                                e.preventDefault();
                                togglePhoto(cluster.id, photo.id);
                              }
                            }}
                          >
                            <div className="photo-media-wrap">
                              <img
                                src={previewUrl(photo.preview_key)}
                                alt={photo.filename}
                                loading="lazy"
                              />
                              <span className="score-pill">评分 {Math.round(photo.ai_score)}</span>
                              <button
                                type="button"
                                className="zoom-btn"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  setLightboxPhoto(photo);
                                }}
                              >
                                放大
                              </button>
                            </div>
                            <div className="photo-body">
                              <div className="badge-row">
                                <span className={`badge ${isKeep ? "keep" : "trash"}`}>
                                  {isKeep ? "保留" : "淘汰"}
                                </span>
                                <span className="ai-tag">
                                  {aiDiffers
                                    ? `AI原判:${photo.recommended_action === "keep" ? "保留" : "淘汰"}`
                                    : photo.recommended_action === "keep"
                                      ? "AI 推荐保留"
                                      : "AI 建议淘汰"}
                                </span>
                              </div>
                              <div className="photo-reason">
                                {photo.ai_reason || "待确认"}
                              </div>
                              <div className="photo-file">
                                {photo.filename} · {formatBytes(photo.size_bytes)}
                              </div>
                            </div>
                          </div>
                        );
                      })}
                    </div>

                    <div className="cluster-footer">
                      <button
                        type="button"
                        className="btn btn-primary"
                        disabled={busyId === cluster.id}
                        onClick={() => handleConfirmCluster(cluster)}
                      >
                        {busyId === cluster.id
                          ? "提交中..."
                          : `确认本组（保留 ${keepCount} · 淘汰 ${trashCount}）`}
                      </button>
                    </div>
                  </article>
                );
              })}
            </div>
          )}
        </>
      )}

      {tab === "history" && (
        <div className="cluster-list">
          {historyClusters.length === 0 ? (
            <div className="empty-card">
              <h2>暂无已确认记录</h2>
              <p>确认待审组后，处理进度与同步状态会显示在这里。</p>
            </div>
          ) : (
            historyClusters.map((c) => {
              const sb = statusBadge(c.status);
              return (
                <article className="history-item" key={c.id}>
                  <div className="history-top">
                    <div>
                      <strong>{c.title}</strong>
                      <div className="cluster-meta">相册：{c.album_name}</div>
                    </div>
                    <span className={`badge ${sb.cls}`}>{sb.text}</span>
                  </div>
                  <div className="history-photos">
                    {c.photos.map((p) => (
                      <div className="history-photo-row" key={p.id}>
                        <span>{p.filename}</span>
                        <span>
                          {p.effective_action === "keep" ? "保留" : "淘汰"} · OneDrive:{p.onedrive_status} · GP:{p.gphotos_status}
                        </span>
                      </div>
                    ))}
                  </div>
                </article>
              );
            })
          )}

          {logs.length > 0 && (
            <section className="log-list">
              <h3>最近执行日志</h3>
              {logs.map((l) => (
                <div className="log-row" key={l.id}>
                  [{l.outcome}] {l.cluster_title || l.cluster_id} · {l.action}: {l.detail}
                </div>
              ))}
            </section>
          )}
        </div>
      )}

      {lightboxPhoto && (
        <div className="lightbox" role="dialog" onClick={() => setLightboxPhoto(null)}>
          <img src={previewUrl(lightboxPhoto.preview_key)} alt={lightboxPhoto.filename} />
          <div className="lightbox-caption">
            {lightboxPhoto.filename} — {lightboxPhoto.ai_reason}（轻点空白处关闭）
          </div>
        </div>
      )}
    </div>
  );
}
