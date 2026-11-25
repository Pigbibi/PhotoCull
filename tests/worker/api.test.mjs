import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { DatabaseSync } from "node:sqlite";
import test from "node:test";
import { fileURLToPath } from "node:url";

import { signPayload } from "../../worker/src/auth.js";
import { handleRequest } from "../../worker/src/index.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT_DIR = path.resolve(__dirname, "../..");
const MIGRATION_SQL = fs.readFileSync(
  path.join(ROOT_DIR, "migrations/0001_init.sql"),
  "utf8",
);

function createMockD1() {
  const sqlite = new DatabaseSync(":memory:");
  sqlite.exec("PRAGMA foreign_keys = ON;");
  sqlite.exec(MIGRATION_SQL);

  function wrapStmt(sql, params = []) {
    return {
      bind(...bound) {
        return wrapStmt(sql, bound);
      },
      async first() {
        const stmt = sqlite.prepare(sql);
        const row = stmt.get(...params);
        return row ?? null;
      },
      async all() {
        const stmt = sqlite.prepare(sql);
        const results = stmt.all(...params);
        return { results };
      },
      async run() {
        const stmt = sqlite.prepare(sql);
        const info = stmt.run(...params);
        return { success: true, meta: info };
      },
    };
  }

  return {
    sqlite,
    prepare(sql) {
      return wrapStmt(sql);
    },
  };
}

function createMockR2() {
  const store = new Map();
  return {
    store,
    async put(key, body, opts = {}) {
      const buf = body instanceof ArrayBuffer ? new Uint8Array(body) : new Uint8Array(body);
      store.set(key, {
        body: buf,
        httpMetadata: opts.httpMetadata || {},
      });
    },
    async get(key) {
      const item = store.get(key);
      if (!item) return null;
      return {
        body: item.body,
        httpMetadata: item.httpMetadata,
      };
    },
    async delete(key) {
      store.delete(key);
    },
  };
}

function makeEnv(db, previews) {
  return {
    DB: db,
    PREVIEWS: previews,
    SESSION_SECRET: "test-session-secret-0123456789abcdef",
    PROCESSOR_TOKEN: "test-processor-token-0123456789abcdef",
    ALLOWED_GITHUB_LOGINS: "pigbibi",
    APP_ORIGIN: "https://photocull.example",
  };
}

async function createUserCookie(env, login = "Pigbibi") {
  const sid = "sess_test_1";
  const expires = new Date(Date.now() + 3600_000).toISOString();
  await env.DB.prepare("INSERT INTO sessions (id, github_login, expires_at) VALUES (?, ?, ?)")
    .bind(sid, login, expires)
    .run();
  const signed = await signPayload(
    { sid, login, exp: Date.now() + 3600_000 },
    env.SESSION_SECRET,
  );
  return `photocull_session=${encodeURIComponent(signed)}`;
}

test("unauthorized user and processor requests return 401", async () => {
  const db = createMockD1();
  const r2 = createMockR2();
  const env = makeEnv(db, r2);

  for (const urlPath of [
    "/api/overview",
    "/api/clusters",
    "/api/previews/cl_1/ph_1.jpg",
    "/api/logs",
  ]) {
    const res = await handleRequest(new Request(`https://photocull.example${urlPath}`), env);
    assert.equal(res.status, 401, `${urlPath} should return 401`);
  }

  const badProc = await handleRequest(
    new Request("https://photocull.example/api/processor/approved", {
      headers: { Authorization: "Bearer wrong-token" },
    }),
    env,
  );
  assert.equal(badProc.status, 401);
});

test("full cluster lifecycle: ingest -> preview -> approve -> execute -> complete", async () => {
  const db = createMockD1();
  const r2 = createMockR2();
  const env = makeEnv(db, r2);
  const procHeaders = {
    Authorization: `Bearer ${env.PROCESSOR_TOKEN}`,
  };

  // 1. Processor uploads preview images
  const jpegBytes = new Uint8Array([0xff, 0xd8, 0xff, 0xd9]);
  for (const key of ["cl_1/ph_1.jpg", "cl_1/ph_2.jpg"]) {
    const putRes = await handleRequest(
      new Request(`https://photocull.example/api/processor/previews/${key}`, {
        method: "PUT",
        headers: { ...procHeaders, "Content-Type": "image/jpeg" },
        body: jpegBytes,
      }),
      env,
    );
    assert.equal(putRes.status, 200);
  }

  // 2. Processor submits burst cluster
  const ingestRes = await handleRequest(
    new Request("https://photocull.example/api/processor/clusters", {
      method: "POST",
      headers: { ...procHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        id: "cl_1",
        title: "2026-10-03 14:22 · 连拍 2 张",
        album_name: "2026-10 川西旅拍",
        captured_at: "2026-10-03T14:22:00Z",
        photos: [
          {
            id: "ph_1",
            onedrive_item_id: "OD_1",
            onedrive_etag: '"e1"',
            onedrive_path: "/Pictures/Camera Roll/IMG_1.jpg",
            filename: "IMG_1.jpg",
            size_bytes: 4_000_000,
            taken_at: "2026-10-03T14:22:00Z",
            preview_key: "cl_1/ph_1.jpg",
            recommended_action: "keep",
            ai_score: 92,
            ai_reason: "主体清晰",
            sort_order: 0,
          },
          {
            id: "ph_2",
            onedrive_item_id: "OD_2",
            onedrive_etag: '"e2"',
            onedrive_path: "/Pictures/Camera Roll/IMG_2.jpg",
            filename: "IMG_2.jpg",
            size_bytes: 3_500_000,
            taken_at: "2026-10-03T14:22:02Z",
            preview_key: "cl_1/ph_2.jpg",
            recommended_action: "trash",
            ai_score: 54,
            ai_reason: "轻微抖动",
            sort_order: 1,
          },
        ],
      }),
    }),
    env,
  );
  assert.equal(ingestRes.status, 201);

  // 3. Authenticated user checks overview, preview, and approves with custom album
  const userCookie = await createUserCookie(env);
  const ovRes = await handleRequest(
    new Request("https://photocull.example/api/overview", {
      headers: { Cookie: userCookie },
    }),
    env,
  );
  assert.equal(ovRes.status, 200);
  const ov = await ovRes.json();
  assert.equal(ov.pending_clusters, 1);
  assert.equal(ov.pending_trash_bytes, 3_500_000);

  const prevRes = await handleRequest(
    new Request("https://photocull.example/api/previews/cl_1%2Fph_1.jpg", {
      headers: { Cookie: userCookie },
    }),
    env,
  );
  assert.equal(prevRes.status, 200);
  assert.equal(prevRes.headers.get("Cache-Control"), "private, max-age=3600");

  const approveRes = await handleRequest(
    new Request("https://photocull.example/api/clusters/cl_1/approve", {
      method: "POST",
      headers: { Cookie: userCookie, "Content-Type": "application/json" },
      body: JSON.stringify({
        album_name: "2026-10 稻城亚丁",
        decisions: { ph_1: "keep", ph_2: "trash" },
      }),
    }),
    env,
  );
  assert.equal(approveRes.status, 200);
  const approvedBody = await approveRes.json();
  assert.equal(approvedBody.cluster.status, "approved");
  assert.equal(approvedBody.cluster.album_name, "2026-10 稻城亚丁");

  // 4. Processor fetches approved clusters
  const pollRes = await handleRequest(
    new Request("https://photocull.example/api/processor/approved", {
      headers: procHeaders,
    }),
    env,
  );
  assert.equal(pollRes.status, 200);
  const polled = await pollRes.json();
  assert.equal(polled.clusters.length, 1);
  assert.equal(polled.clusters[0].photos[1].effective_action, "trash");

  // 5. Processor reports execution completion
  const resultRes = await handleRequest(
    new Request("https://photocull.example/api/processor/clusters/cl_1/result", {
      method: "POST",
      headers: { ...procHeaders, "Content-Type": "application/json" },
      body: JSON.stringify({
        status: "completed",
        delete_previews: true,
        results: [
          {
            photo_id: "ph_1",
            onedrive_status: "kept",
            gphotos_status: "uploaded",
            gphotos_media_item_id: "GP_MEDIA_1",
          },
          {
            photo_id: "ph_2",
            onedrive_status: "trashed",
            gphotos_status: "skipped",
          },
        ],
      }),
    }),
    env,
  );
  assert.equal(resultRes.status, 200);
  const resultBody = await resultRes.json();
  assert.equal(resultBody.cluster.status, "completed");

  // R2 previews cleaned up on completion
  assert.equal(r2.store.size, 0);

  // Overview reflects synced & freed metrics
  const ov2 = await (
    await handleRequest(
      new Request("https://photocull.example/api/overview", {
        headers: { Cookie: userCookie },
      }),
      env,
    )
  ).json();
  assert.equal(ov2.pending_clusters, 0);
  assert.equal(ov2.completed_clusters, 1);
  assert.equal(ov2.synced_gphotos_photos, 1);
  assert.equal(ov2.freed_bytes, 3_500_000);
});

test("approve-all-recommended approves all pending clusters", async () => {
  const db = createMockD1();
  const r2 = createMockR2();
  const env = makeEnv(db, r2);
  const procHeaders = {
    Authorization: `Bearer ${env.PROCESSOR_TOKEN}`,
    "Content-Type": "application/json",
  };

  await handleRequest(
    new Request("https://photocull.example/api/processor/clusters", {
      method: "POST",
      headers: procHeaders,
      body: JSON.stringify({
        id: "cl_bulk",
        title: "2026-10-04 · 单张",
        album_name: "2026-10 旅拍精选",
        photos: [
          {
            id: "ph_bulk",
            onedrive_item_id: "OD_BULK",
            filename: "IMG_BULK.jpg",
            preview_key: "cl_bulk/ph_bulk.jpg",
            recommended_action: "keep",
          },
        ],
      }),
    }),
    env,
  );

  const userCookie = await createUserCookie(env);
  const res = await handleRequest(
    new Request("https://photocull.example/api/clusters/approve-all-recommended", {
      method: "POST",
      headers: { Cookie: userCookie },
    }),
    env,
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.approved_count, 1);
});
