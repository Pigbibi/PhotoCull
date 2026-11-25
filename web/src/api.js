async function requestJson(url, options = {}) {
  const res = await fetch(url, {
    credentials: "same-origin",
    ...options,
    headers: {
      ...(options.body ? { "Content-Type": "application/json" } : {}),
      ...(options.headers || {}),
    },
  });

  const text = await res.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = null;
  }

  if (!res.ok) {
    const err = new Error(data?.error || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

export function fetchAuthMe() {
  return requestJson("/api/auth/me");
}

export function logout() {
  return requestJson("/api/auth/logout", { method: "POST" });
}

export function fetchOverview() {
  return requestJson("/api/overview");
}

export function fetchClusters(status = "pending_review") {
  return requestJson(`/api/clusters?status=${encodeURIComponent(status)}`);
}

export function approveCluster(clusterId, payload) {
  return requestJson(`/api/clusters/${encodeURIComponent(clusterId)}/approve`, {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export function approveAllRecommended() {
  return requestJson("/api/clusters/approve-all-recommended", {
    method: "POST",
  });
}

export function fetchLogs(limit = 40) {
  return requestJson(`/api/logs?limit=${limit}`);
}

export function previewUrl(previewKey) {
  return `/api/previews/${encodeURIComponent(previewKey)}`;
}
