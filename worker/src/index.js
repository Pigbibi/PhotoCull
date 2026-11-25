import { getSession, handleAuthRoute, isProcessorAuthorized } from "./auth.js";
import {
  approveAllRecommended,
  approveCluster,
  getApprovedClustersForProcessor,
  getClusterById,
  getOverview,
  listClusters,
  listLogs,
  recordClusterResult,
  upsertClusterFromProcessor,
} from "./db.js";

function sanitizePreviewKey(rawKey) {
  const decoded = decodeURIComponent(rawKey || "").replace(/^\/+/, "");
  if (!decoded || decoded.includes("..") || decoded.length > 200) {
    return null;
  }
  return decoded;
}

async function readJsonBody(request) {
  try {
    return await request.json();
  } catch {
    return null;
  }
}

export async function handleRequest(request, env) {
  const url = new URL(request.url);
  const method = request.method.toUpperCase();

  if (url.pathname.startsWith("/api/auth/")) {
    const authResponse = await handleAuthRoute(request, env, url);
    if (authResponse) return authResponse;
  }

  if (url.pathname.startsWith("/api/processor/")) {
    const ok = await isProcessorAuthorized(request, env);
    if (!ok) {
      return Response.json({ error: "Unauthorized processor token" }, { status: 401 });
    }

    if (url.pathname.startsWith("/api/processor/previews/") && method === "PUT") {
      if (!env?.PREVIEWS) {
        return Response.json({ error: "R2 PREVIEWS binding missing" }, { status: 500 });
      }
      const key = sanitizePreviewKey(url.pathname.slice("/api/processor/previews/".length));
      if (!key) {
        return Response.json({ error: "Invalid preview key" }, { status: 400 });
      }
      const body = await request.arrayBuffer();
      const contentType = request.headers.get("Content-Type") || "image/jpeg";
      await env.PREVIEWS.put(key, body, {
        httpMetadata: { contentType },
      });
      return Response.json({ ok: true, key });
    }

    if (url.pathname === "/api/processor/clusters" && method === "POST") {
      const body = await readJsonBody(request);
      if (!body) return Response.json({ error: "Invalid JSON body" }, { status: 400 });
      const res = await upsertClusterFromProcessor(env.DB, body);
      if (res.error) return Response.json({ error: res.error }, { status: res.status || 400 });
      return Response.json(res, { status: res.skipped ? 200 : 201 });
    }

    if (url.pathname === "/api/processor/approved" && method === "GET") {
      const clusters = await getApprovedClustersForProcessor(env.DB);
      return Response.json({ clusters });
    }

    const resultMatch = url.pathname.match(/^\/api\/processor\/clusters\/([^/]+)\/result$/);
    if (resultMatch && method === "POST") {
      const clusterId = decodeURIComponent(resultMatch[1]);
      const body = await readJsonBody(request);
      if (!body) return Response.json({ error: "Invalid JSON body" }, { status: 400 });
      const res = await recordClusterResult(env.DB, env.PREVIEWS, clusterId, body);
      if (res.error) return Response.json({ error: res.error }, { status: res.status || 400 });
      return Response.json(res);
    }

    return Response.json({ error: "Processor endpoint not found" }, { status: 404 });
  }

  if (url.pathname.startsWith("/api/")) {
    const session = await getSession(request, env);
    if (!session) {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }

    if (url.pathname === "/api/overview" && method === "GET") {
      const overview = await getOverview(env.DB);
      return Response.json(overview);
    }

    if (url.pathname === "/api/clusters" && method === "GET") {
      const status = url.searchParams.get("status") || "pending_review";
      const clusters = await listClusters(env.DB, status);
      return Response.json({ clusters });
    }

    if (url.pathname === "/api/clusters/approve-all-recommended" && method === "POST") {
      const res = await approveAllRecommended(env.DB);
      return Response.json(res);
    }

    const approveMatch = url.pathname.match(/^\/api\/clusters\/([^/]+)\/approve$/);
    if (approveMatch && method === "POST") {
      const clusterId = decodeURIComponent(approveMatch[1]);
      const body = await readJsonBody(request);
      if (!body) return Response.json({ error: "Invalid JSON body" }, { status: 400 });
      const res = await approveCluster(env.DB, clusterId, {
        albumName: body.album_name,
        decisions: body.decisions,
      });
      if (res.error) return Response.json({ error: res.error }, { status: res.status || 400 });
      return Response.json(res);
    }

    const detailMatch = url.pathname.match(/^\/api\/clusters\/([^/]+)$/);
    if (detailMatch && method === "GET") {
      const clusterId = decodeURIComponent(detailMatch[1]);
      const cluster = await getClusterById(env.DB, clusterId);
      if (!cluster) return Response.json({ error: "Cluster not found" }, { status: 404 });
      return Response.json({ cluster });
    }

    if (url.pathname.startsWith("/api/previews/") && method === "GET") {
      if (!env?.PREVIEWS) {
        return Response.json({ error: "R2 PREVIEWS binding missing" }, { status: 500 });
      }
      const key = sanitizePreviewKey(url.pathname.slice("/api/previews/".length));
      if (!key) return Response.json({ error: "Invalid preview key" }, { status: 400 });

      const obj = await env.PREVIEWS.get(key);
      if (!obj) return new Response("Preview not found", { status: 404 });

      const headers = new Headers();
      headers.set("Content-Type", obj.httpMetadata?.contentType || "image/jpeg");
      headers.set("Cache-Control", "private, max-age=3600");
      return new Response(obj.body, { status: 200, headers });
    }

    if (url.pathname === "/api/logs" && method === "GET") {
      const limit = url.searchParams.get("limit") || "50";
      const logs = await listLogs(env.DB, limit);
      return Response.json({ logs });
    }

    return Response.json({ error: "API route not found" }, { status: 404 });
  }

  if (env?.ASSETS) {
    return env.ASSETS.fetch(request);
  }
  return new Response("Not Found", { status: 404 });
}

export default {
  async fetch(request, env) {
    return handleRequest(request, env);
  },
};
