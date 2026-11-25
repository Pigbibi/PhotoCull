from __future__ import annotations

import io
import stat
import tempfile
import unittest
import urllib.parse
from pathlib import Path

from processor.oauth_helper import (
    GPHOTOS_APPENDONLY_SCOPE,
    OAuthHelperError,
    build_gphotos_auth_url,
    exchange_gphotos_code,
    run_gphotos_auth,
    run_onedrive_device_auth,
    upsert_env_values,
)


class OAuthHelperTests(unittest.TestCase):
    def test_onedrive_device_auth_polls_pending_and_returns_refresh_token(self) -> None:
        calls: list[tuple[str, dict]] = []
        sleeps: list[float] = []
        buf = io.StringIO()

        def fake_post(url: str, form: dict) -> tuple[int, dict]:
            calls.append((url, form))
            if url.endswith("/devicecode"):
                return 200, {
                    "device_code": "dc_123",
                    "user_code": "ABCD-EFGH",
                    "verification_uri": "https://microsoft.com/devicelogin",
                    "interval": 2,
                    "expires_in": 60,
                    "message": "Open https://microsoft.com/devicelogin and enter ABCD-EFGH",
                }
            token_attempt = sum(1 for u, _ in calls if u.endswith("/token"))
            if token_attempt == 1:
                return 400, {"error": "authorization_pending"}
            if token_attempt == 2:
                return 400, {"error": "slow_down"}
            return 200, {"access_token": "at_ok", "refresh_token": "ms_rt_final"}

        token = run_onedrive_device_auth(
            "ms_cid",
            tenant="consumers",
            http_post=fake_post,
            sleep_fn=sleeps.append,
            out_stream=buf,
        )
        self.assertEqual(token, "ms_rt_final")
        self.assertIn("ABCD-EFGH", buf.getvalue())
        self.assertEqual(sleeps, [2, 2, 7])

    def test_gphotos_auth_url_uses_appendonly_scope_only(self) -> None:
        url = build_gphotos_auth_url("gcid", "http://127.0.0.1:8765/callback", "st_1")
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query)
        self.assertEqual(qs["scope"], [GPHOTOS_APPENDONLY_SCOPE])
        self.assertNotIn("readonly", qs["scope"][0])
        self.assertEqual(qs["access_type"], ["offline"])
        self.assertEqual(qs["prompt"], ["consent"])

    def test_gphotos_manual_auth_extracts_code_from_redirect_url(self) -> None:
        buf = io.StringIO()
        captured_urls: list[str] = []

        def fake_post(url: str, form: dict) -> tuple[int, dict]:
            self.assertEqual(form["code"], "4/auth_code_xyz")
            return 200, {"access_token": "at", "refresh_token": "gp_rt_999"}

        def fake_input(_prompt: str) -> str:
            line = next(l for l in buf.getvalue().splitlines() if l.startswith("https://"))
            captured_urls.append(line)
            qs = urllib.parse.parse_qs(urllib.parse.urlparse(line).query)
            state = qs["state"][0]
            return f"http://127.0.0.1:8765/callback?state={state}&code=4/auth_code_xyz"

        token = run_gphotos_auth(
            "gcid",
            "gsec",
            port=8765,
            manual=True,
            http_post=fake_post,
            input_fn=fake_input,
            out_stream=buf,
        )
        self.assertEqual(token, "gp_rt_999")
        self.assertEqual(len(captured_urls), 1)

    def test_exchange_gphotos_code_requires_refresh_token(self) -> None:
        with self.assertRaises(OAuthHelperError):
            exchange_gphotos_code(
                "gcid",
                "gsec",
                "code1",
                "http://127.0.0.1:8765/callback",
                http_post=lambda _u, _f: (200, {"access_token": "only_access"}),
            )

    def test_upsert_env_values_preserves_existing_and_sets_0600(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("# comment\nMS_CLIENT_ID=abc\nMS_REFRESH_TOKEN=old\n", encoding="utf-8")
            upsert_env_values(
                env_path,
                {"MS_REFRESH_TOKEN": "new_ms", "GOOGLE_REFRESH_TOKEN": "new_gp"},
            )
            content = env_path.read_text(encoding="utf-8")
            self.assertIn("MS_CLIENT_ID=abc", content)
            self.assertIn("MS_REFRESH_TOKEN=new_ms", content)
            self.assertIn("GOOGLE_REFRESH_TOKEN=new_gp", content)
            self.assertEqual(stat.S_IMODE(env_path.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
