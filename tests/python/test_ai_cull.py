from __future__ import annotations

import json
import unittest
import urllib.error

from processor.ai_cull import FALLBACK_REASON, cull_cluster, parse_cull_response


class AiCullTests(unittest.TestCase):
    def test_gemini_cull_parses_picks_and_album(self) -> None:
        captured = {}

        def fake_post(url: str, headers: dict, payload: dict) -> dict:
            captured["url"] = url
            captured["payload"] = payload
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {
                                    "text": json.dumps(
                                        {
                                            "suggested_album": "2026-10 川西旅拍",
                                            "picks": [
                                                {"index": 1, "action": "keep", "score": 94, "reason": "清晰构图好"},
                                                {"index": 2, "action": "trash", "score": 61, "reason": "人物闭眼"},
                                            ],
                                        }
                                    )
                                }
                            ]
                        }
                    }
                ]
            }

        res = cull_cluster(
            b"fake-jpeg",
            2,
            "2026-10 旅拍精选",
            provider="gemini",
            api_key="test-key",
            model="gemini-2.5-flash",
            http_post=fake_post,
        )
        self.assertIn("generativelanguage.googleapis.com", captured["url"])
        self.assertEqual(res["suggested_album"], "2026-10 川西旅拍")
        self.assertEqual(res["picks"][0]["action"], "keep")
        self.assertEqual(res["picks"][1]["action"], "trash")

    def test_openai_cull_parses_markdown_fenced_json(self) -> None:
        def fake_post(url: str, headers: dict, payload: dict) -> dict:
            self.assertEqual(headers["Authorization"], "Bearer sk-test")
            return {
                "choices": [
                    {
                        "message": {
                            "content": '```json\n{"picks":[{"index":1,"action":"trash","score":40,"reason":"糊片"},{"index":2,"action":"keep","score":88,"reason":"对焦准确"}]}\n```'
                        }
                    }
                ]
            }

        res = cull_cluster(
            b"fake-jpeg",
            2,
            "2026-10 旅拍精选",
            provider="openai",
            api_key="sk-test",
            model="gpt-4o-mini",
            http_post=fake_post,
        )
        self.assertEqual(res["suggested_album"], "2026-10 旅拍精选")
        self.assertEqual(res["picks"][0]["action"], "trash")
        self.assertEqual(res["picks"][1]["action"], "keep")

    def test_malformed_json_and_errors_default_to_keep(self) -> None:
        parsed = parse_cull_response("not json at all", 3, "默认相册")
        self.assertEqual([p["action"] for p in parsed["picks"]], ["keep", "keep", "keep"])
        self.assertEqual(parsed["picks"][0]["reason"], FALLBACK_REASON)

        def failing_post(url: str, headers: dict, payload: dict) -> dict:
            raise urllib.error.URLError("network down")

        res = cull_cluster(
            b"fake-jpeg",
            2,
            "默认相册",
            provider="gemini",
            api_key="test-key",
            model="gemini-2.5-flash",
            http_post=failing_post,
        )
        self.assertEqual([p["action"] for p in res["picks"]], ["keep", "keep"])

    def test_missing_index_defaults_to_keep(self) -> None:
        parsed = parse_cull_response(
            json.dumps({"picks": [{"index": 1, "action": "trash", "score": 30, "reason": "过曝"}]}),
            2,
            "默认相册",
        )
        self.assertEqual(parsed["picks"][0]["action"], "trash")
        self.assertEqual(parsed["picks"][1]["action"], "keep")


if __name__ == "__main__":
    unittest.main()
