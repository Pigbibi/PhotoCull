from __future__ import annotations

import http.server
import json
import os
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, TextIO
import sys

GPHOTOS_APPENDONLY_SCOPE = "https://www.googleapis.com/auth/photoslibrary.appendonly"
ONEDRIVE_SCOPES = "Files.ReadWrite offline_access"


class OAuthHelperError(RuntimeError):
    pass


def _default_form_post(url: str, form: dict[str, str], timeout: int = 30) -> tuple[int, dict[str, Any]]:
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"error": "http_error", "error_description": raw}
        return exc.code, parsed


def run_onedrive_device_auth(
    client_id: str,
    *,
    tenant: str = "consumers",
    client_secret: str = "",
    http_post: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    out_stream: TextIO = sys.stdout,
    max_wait_seconds: int = 900,
) -> str:
    """Run Microsoft OAuth2 device code flow and return a OneDrive refresh_token."""
    if not client_id:
        raise OAuthHelperError("MS_CLIENT_ID is required")

    post_fn = http_post or _default_form_post
    clean_tenant = tenant or "consumers"
    device_url = f"https://login.microsoftonline.com/{clean_tenant}/oauth2/v2.0/devicecode"
    token_url = f"https://login.microsoftonline.com/{clean_tenant}/oauth2/v2.0/token"

    status, dc = post_fn(
        device_url,
        {"client_id": client_id, "scope": ONEDRIVE_SCOPES},
    )
    if status >= 400 or "device_code" not in dc:
        raise OAuthHelperError(f"Failed to start Microsoft device auth: {dc}")

    verify_uri = dc.get("verification_uri") or "https://microsoft.com/devicelogin"
    user_code = dc.get("user_code", "")
    interval = int(dc.get("interval") or 5)
    expires_in = min(int(dc.get("expires_in") or max_wait_seconds), max_wait_seconds)
    msg = dc.get("message") or f"Open {verify_uri} and enter code: {user_code}"
    out_stream.write(f"{msg}\n")
    out_stream.flush()

    deadline = time.monotonic() + expires_in
    while time.monotonic() < deadline:
        sleep_fn(interval)
        token_form: dict[str, str] = {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": client_id,
            "device_code": str(dc["device_code"]),
        }
        if client_secret:
            token_form["client_secret"] = client_secret

        t_status, t_data = post_fn(token_url, token_form)
        if t_status < 400 and t_data.get("refresh_token"):
            return str(t_data["refresh_token"])

        err = str(t_data.get("error") or "")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if t_status < 400 and t_data.get("access_token") and not t_data.get("refresh_token"):
            raise OAuthHelperError("Token response did not include refresh_token (check offline_access scope)")
        raise OAuthHelperError(f"Microsoft device auth failed ({err or t_status}): {t_data}")

    raise OAuthHelperError("Microsoft device code flow timed out before authorization completed")


def build_gphotos_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    """Build Google OAuth2 URL strictly limited to photoslibrary.appendonly."""
    if not client_id:
        raise OAuthHelperError("GOOGLE_CLIENT_ID is required")
    params = urllib.parse.urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GPHOTOS_APPENDONLY_SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


def exchange_gphotos_code(
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    *,
    http_post: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
) -> str:
    """Exchange a Google OAuth authorization code for a refresh_token."""
    if not client_id or not client_secret:
        raise OAuthHelperError("GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are required")
    if not code:
        raise OAuthHelperError("Authorization code is empty")

    post_fn = http_post or _default_form_post
    status, data = post_fn(
        "https://oauth2.googleapis.com/token",
        {
            "client_id": client_id,
            "client_secret": client_secret,
            "code": code,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    if status >= 400 or not data.get("refresh_token"):
        raise OAuthHelperError(f"Google OAuth code exchange failed ({status}): {data}")
    return str(data["refresh_token"])


def _extract_code_from_user_input(raw_input: str, expected_state: str) -> str:
    cleaned = raw_input.strip()
    if not cleaned:
        raise OAuthHelperError("No authorization code or redirect URL provided")
    if "://" in cleaned or cleaned.startswith("/"):
        parsed = urllib.parse.urlparse(cleaned)
        qs = urllib.parse.parse_qs(parsed.query)
        if qs.get("error"):
            raise OAuthHelperError(f"Google OAuth returned error: {qs['error'][0]}")
        got_state = (qs.get("state") or [""])[0]
        if got_state and got_state != expected_state:
            raise OAuthHelperError("OAuth state mismatch")
        codes = qs.get("code") or []
        if not codes:
            raise OAuthHelperError("Redirect URL missing ?code= parameter")
        return codes[0]
    return cleaned


def run_gphotos_auth(
    client_id: str,
    client_secret: str,
    *,
    port: int = 8765,
    manual: bool = False,
    http_post: Callable[[str, dict[str, str]], tuple[int, dict[str, Any]]] | None = None,
    input_fn: Callable[[str], str] = input,
    out_stream: TextIO = sys.stdout,
) -> str:
    """Run Google Photos OAuth (loopback HTTP server or manual URL paste) and return refresh_token."""
    redirect_uri = f"http://127.0.0.1:{port}/callback"
    state = secrets.token_urlsafe(24)
    auth_url = build_gphotos_auth_url(client_id, redirect_uri, state)

    out_stream.write("Open the following URL in your browser and authorize PhotoCull:\n\n")
    out_stream.write(f"{auth_url}\n\n")
    out_stream.flush()

    if manual:
        pasted = input_fn("Paste the full redirected URL (or code) here: ")
        code = _extract_code_from_user_input(pasted, state)
        return exchange_gphotos_code(
            client_id, client_secret, code, redirect_uri, http_post=http_post
        )

    captured: dict[str, str] = {}

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/callback":
                self.send_response(404)
                self.end_headers()
                return
            qs = urllib.parse.parse_qs(parsed.query)
            if qs.get("error"):
                captured["error"] = qs["error"][0]
            elif (qs.get("state") or [""])[0] != state:
                captured["error"] = "state_mismatch"
            elif qs.get("code"):
                captured["code"] = qs["code"][0]
            else:
                captured["error"] = "missing_code"

            body = (
                "<html><body><h3>PhotoCull authorization received. You can close this window.</h3></body></html>"
                if "code" in captured
                else "<html><body><h3>Authorization failed. Check terminal output.</h3></body></html>"
            ).encode("utf-8")
            self.send_response(200 if "code" in captured else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    server.timeout = 300
    try:
        out_stream.write(f"Waiting for callback on {redirect_uri} ...\n")
        out_stream.flush()
        while not captured:
            server.handle_request()
    finally:
        server.server_close()

    if "error" in captured:
        raise OAuthHelperError(f"Google OAuth callback error: {captured['error']}")

    return exchange_gphotos_code(
        client_id,
        client_secret,
        captured["code"],
        redirect_uri,
        http_post=http_post,
    )


def upsert_env_values(env_path: Path, updates: dict[str, str]) -> None:
    """Update or append KEY=VALUE pairs in a dotenv file and enforce 0600 permissions."""
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.is_file() else []
    remaining = dict(updates)
    out_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in remaining:
                out_lines.append(f"{key}={remaining.pop(key)}")
                continue
        out_lines.append(line)

    for key, value in remaining.items():
        out_lines.append(f"{key}={value}")

    env_path.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    os.chmod(env_path, 0o600)
