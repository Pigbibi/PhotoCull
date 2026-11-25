const SESSION_COOKIE = "photocull_session";
const OAUTH_STATE_COOKIE = "photocull_oauth_state";
const SESSION_TTL_SECONDS = 7 * 24 * 60 * 60;

function base64UrlEncode(bytes) {
  let binary = "";
  for (let i = 0; i < bytes.length; i += 1) {
    binary += String.fromCharCode(bytes[i]);
  }
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function base64UrlDecode(str) {
  const padded = str.replace(/-/g, "+").replace(/_/g, "/") + "===".slice((str.length + 3) % 4);
  const binary = atob(padded);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

export function parseCookies(cookieHeader) {
  const out = {};
  if (!cookieHeader) return out;
  for (const part of cookieHeader.split(";")) {
    const idx = part.indexOf("=");
    if (idx <= 0) continue;
    const key = part.slice(0, idx).trim();
    const val = part.slice(idx + 1).trim();
    out[key] = decodeURIComponent(val);
  }
  return out;
}

export function parseAllowedLogins(env) {
  const raw = env?.ALLOWED_GITHUB_LOGINS || "";
  return raw
    .split(",")
    .map((s) => s.trim().toLowerCase())
    .filter((s) => s && s !== "your_github_username");
}

export async function timingSafeEqualStr(a, b) {
  if (typeof a !== "string" || typeof b !== "string" || !a || !b) {
    return false;
  }
  const enc = new TextEncoder();
  const [hashA, hashB] = await Promise.all([
    crypto.subtle.digest("SHA-256", enc.encode(a)),
    crypto.subtle.digest("SHA-256", enc.encode(b)),
  ]);
  const bytesA = new Uint8Array(hashA);
  const bytesB = new Uint8Array(hashB);
  let diff = a.length ^ b.length;
  for (let i = 0; i < bytesA.length; i += 1) {
    diff |= bytesA[i] ^ bytesB[i];
  }
  return diff === 0;
}

async function importHmacKey(secret) {
  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(secret),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign", "verify"],
  );
}

export async function signPayload(payloadObj, secret) {
  if (!secret) throw new Error("SESSION_SECRET is required");
  const jsonBytes = new TextEncoder().encode(JSON.stringify(payloadObj));
  const payloadB64 = base64UrlEncode(jsonBytes);
  const key = await importHmacKey(secret);
  const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(payloadB64));
  const sigB64 = base64UrlEncode(new Uint8Array(sig));
  return `${payloadB64}.${sigB64}`;
}

export async function verifySignedPayload(token, secret) {
  if (!token || !secret || typeof token !== "string") return null;
  const dot = token.indexOf(".");
  if (dot <= 0) return null;
  const payloadB64 = token.slice(0, dot);
  const sigB64 = token.slice(dot + 1);
  try {
    const key = await importHmacKey(secret);
    const ok = await crypto.subtle.verify(
      "HMAC",
      key,
      base64UrlDecode(sigB64),
      new TextEncoder().encode(payloadB64),
    );
    if (!ok) return null;
    const jsonStr = new TextDecoder().decode(base64UrlDecode(payloadB64));
    return JSON.parse(jsonStr);
  } catch {
    return null;
  }
}

function buildCookieHeader(name, value, { maxAge, isHttps }) {
  const parts = [
    `${name}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
  ];
  if (typeof maxAge === "number") {
    parts.push(`Max-Age=${maxAge}`);
  }
  if (isHttps) {
    parts.push("Secure");
  }
  return parts.join("; ");
}

export async function getSession(request, env) {
  if (env?.DEV_BYPASS_USER && String(env.DEV_BYPASS_USER).trim()) {
    return { login: String(env.DEV_BYPASS_USER).trim(), devBypass: true };
  }

  const secret = env?.SESSION_SECRET;
  if (!secret) return null;

  const cookies = parseCookies(request.headers.get("Cookie"));
  const token = cookies[SESSION_COOKIE];
  if (!token) return null;

  const payload = await verifySignedPayload(token, secret);
  if (!payload || !payload.login || !payload.exp) return null;
  if (Date.now() > Number(payload.exp)) return null;

  const allowed = parseAllowedLogins(env);
  if (allowed.length > 0 && !allowed.includes(String(payload.login).toLowerCase())) {
    return null;
  }

  if (payload.sid && env?.DB) {
    const row = await env.DB.prepare(
      "SELECT id FROM sessions WHERE id = ? AND expires_at > ?",
    )
      .bind(payload.sid, new Date().toISOString())
      .first();
    if (!row) return null;
  }

  return { login: payload.login, sid: payload.sid || null };
}

export async function isProcessorAuthorized(request, env) {
  const expected = env?.PROCESSOR_TOKEN;
  if (!expected) return false;
  const auth = request.headers.get("Authorization") || "";
  if (!auth.startsWith("Bearer ")) return false;
  const provided = auth.slice(7).trim();
  return timingSafeEqualStr(provided, String(expected));
}

function resolveOrigin(request, env) {
  if (env?.APP_ORIGIN && !String(env.APP_ORIGIN).includes("your-subdomain")) {
    return String(env.APP_ORIGIN).replace(/\/$/, "");
  }
  return new URL(request.url).origin;
}

export async function handleAuthRoute(request, env, url) {
  const isHttps = url.protocol === "https:";
  const method = request.method.toUpperCase();

  if (url.pathname === "/api/auth/me" && method === "GET") {
    const session = await getSession(request, env);
    const loginEnabled = Boolean(env?.GITHUB_CLIENT_ID && env?.GITHUB_CLIENT_SECRET);
    if (!session) {
      return Response.json({ authenticated: false, login_enabled: loginEnabled });
    }
    return Response.json({
      authenticated: true,
      user: { login: session.login, dev_bypass: Boolean(session.devBypass) },
      login_enabled: loginEnabled,
    });
  }

  if (url.pathname === "/api/auth/login" && method === "GET") {
    if (!env?.GITHUB_CLIENT_ID || !env?.GITHUB_CLIENT_SECRET || !env?.SESSION_SECRET) {
      return Response.json(
        { error: "GitHub OAuth is not configured on Worker secrets" },
        { status: 503 },
      );
    }
    const state = crypto.randomUUID();
    const stateCookieValue = await signPayload(
      { state, exp: Date.now() + 10 * 60 * 1000 },
      env.SESSION_SECRET,
    );
    const origin = resolveOrigin(request, env);
    const redirectUri = `${origin}/api/auth/callback`;
    const authUrl = new URL("https://github.com/login/oauth/authorize");
    authUrl.searchParams.set("client_id", env.GITHUB_CLIENT_ID);
    authUrl.searchParams.set("redirect_uri", redirectUri);
    authUrl.searchParams.set("scope", "read:user");
    authUrl.searchParams.set("state", state);

    return new Response(null, {
      status: 302,
      headers: {
        Location: authUrl.toString(),
        "Set-Cookie": buildCookieHeader(OAUTH_STATE_COOKIE, stateCookieValue, {
          maxAge: 600,
          isHttps,
        }),
      },
    });
  }

  if (url.pathname === "/api/auth/callback" && method === "GET") {
    if (!env?.GITHUB_CLIENT_ID || !env?.GITHUB_CLIENT_SECRET || !env?.SESSION_SECRET) {
      return Response.json({ error: "OAuth not configured" }, { status: 503 });
    }
    const code = url.searchParams.get("code");
    const state = url.searchParams.get("state");
    const cookies = parseCookies(request.headers.get("Cookie"));
    const signedState = await verifySignedPayload(
      cookies[OAUTH_STATE_COOKIE],
      env.SESSION_SECRET,
    );

    if (!code || !state || !signedState || signedState.state !== state || Date.now() > signedState.exp) {
      return Response.json({ error: "Invalid OAuth state" }, { status: 400 });
    }

    const tokenRes = await fetch("https://github.com/login/oauth/access_token", {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        client_id: env.GITHUB_CLIENT_ID,
        client_secret: env.GITHUB_CLIENT_SECRET,
        code,
      }),
    });
    const tokenData = await tokenRes.json();
    if (!tokenRes.ok || !tokenData.access_token) {
      return Response.json({ error: "GitHub token exchange failed" }, { status: 502 });
    }

    const userRes = await fetch("https://api.github.com/user", {
      headers: {
        Authorization: `Bearer ${tokenData.access_token}`,
        Accept: "application/vnd.github+json",
        "User-Agent": "PhotoCull-Worker",
      },
    });
    const userData = await userRes.json();
    const login = userData?.login;
    const allowed = parseAllowedLogins(env);
    if (!login || allowed.length === 0 || !allowed.includes(String(login).toLowerCase())) {
      return Response.json({ error: "GitHub account not in allowlist" }, { status: 403 });
    }

    const sid = crypto.randomUUID();
    const expiresAtMs = Date.now() + SESSION_TTL_SECONDS * 1000;
    if (env.DB) {
      await env.DB.prepare(
        "INSERT INTO sessions (id, github_login, expires_at) VALUES (?, ?, ?)",
      )
        .bind(sid, login, new Date(expiresAtMs).toISOString())
        .run();
    }

    const sessionToken = await signPayload(
      { sid, login, exp: expiresAtMs },
      env.SESSION_SECRET,
    );

    const headers = new Headers({ Location: "/" });
    headers.append(
      "Set-Cookie",
      buildCookieHeader(SESSION_COOKIE, sessionToken, {
        maxAge: SESSION_TTL_SECONDS,
        isHttps,
      }),
    );
    headers.append(
      "Set-Cookie",
      buildCookieHeader(OAUTH_STATE_COOKIE, "", { maxAge: 0, isHttps }),
    );
    return new Response(null, { status: 302, headers });
  }

  if (url.pathname === "/api/auth/logout" && method === "POST") {
    const session = await getSession(request, env);
    if (session?.sid && env?.DB) {
      await env.DB.prepare("DELETE FROM sessions WHERE id = ?").bind(session.sid).run();
    }
    return new Response(JSON.stringify({ ok: true }), {
      status: 200,
      headers: {
        "Content-Type": "application/json",
        "Set-Cookie": buildCookieHeader(SESSION_COOKIE, "", { maxAge: 0, isHttps }),
      },
    });
  }

  return null;
}
