from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request
from typing import Any, Callable

FALLBACK_REASON = "AI 未能可靠判定，默认保留供人工确认"


def _strip_code_fence(text: str) -> str:
    cleaned = text.strip()
    match = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, flags=re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else cleaned


def default_keep_result(count: int, default_album: str, reason: str = FALLBACK_REASON) -> dict[str, Any]:
    return {
        "suggested_album": default_album,
        "picks": [
            {
                "index": i + 1,
                "action": "keep",
                "score": 50.0,
                "reason": reason,
            }
            for i in range(count)
        ],
    }


def parse_cull_response(raw_text: str, count: int, default_album: str) -> dict[str, Any]:
    try:
        parsed = json.loads(_strip_code_fence(raw_text))
    except (json.JSONDecodeError, TypeError):
        return default_keep_result(count, default_album)

    if not isinstance(parsed, dict) or not isinstance(parsed.get("picks"), list):
        return default_keep_result(count, default_album)

    by_index: dict[int, dict[str, Any]] = {}
    for entry in parsed["picks"]:
        if not isinstance(entry, dict):
            continue
        try:
            idx = int(entry.get("index", 0))
        except (TypeError, ValueError):
            continue
        if 1 <= idx <= count:
            action = str(entry.get("action", "keep")).strip().lower()
            if action not in {"keep", "trash"}:
                action = "keep"
            try:
                score = float(entry.get("score", 50))
            except (TypeError, ValueError):
                score = 50.0
            score = max(0.0, min(100.0, score))
            reason = str(entry.get("reason") or "").strip() or (
                "建议保留" if action == "keep" else "连拍相似且质量略逊"
            )
            by_index[idx] = {
                "index": idx,
                "action": action,
                "score": score,
                "reason": reason,
            }

    if not by_index:
        return default_keep_result(count, default_album)

    picks = []
    for idx in range(1, count + 1):
        picks.append(
            by_index.get(
                idx,
                {
                    "index": idx,
                    "action": "keep",
                    "score": 50.0,
                    "reason": FALLBACK_REASON,
                },
            )
        )

    album = str(parsed.get("suggested_album") or "").strip() or default_album
    return {"suggested_album": album, "picks": picks}


def _default_http_post(url: str, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def build_cull_prompt(count: int, default_album: str) -> str:
    return (
        f"这是一组包含 {count} 张照片的旅行连拍拼图，每张图左上角标注了编号 #1 到 #{count}。\n"
        "请从对焦清晰度、主体姿态/闭眼、构图平衡和杂物干扰四个维度对比：\n"
        "1. 为每张照片给出评分 score (0-100)、建议动作 action ('keep' 或 'trash') 和简短中文理由 reason（20字以内）。\n"
        "2. 同一场景连拍通常保留最佳的 1-2 张，其余建议 trash；如果不确定或各有所长，宁可多保留。\n"
        f"3. 给这组照片建议一个简洁的 Google Photos 相册名 suggested_album（默认 '{default_album}'）。\n"
        "严格返回合法 JSON，不要输出任何额外解释：\n"
        '{"suggested_album":"...","picks":[{"index":1,"action":"keep","score":92,"reason":"主体清晰构图最佳"}]}'
    )


def cull_cluster(
    contact_sheet_jpeg: bytes,
    count: int,
    default_album: str,
    *,
    provider: str,
    api_key: str,
    model: str,
    base_url: str = "",
    http_post: Callable[[str, dict[str, str], dict[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not api_key:
        return default_keep_result(count, default_album, "未配置 AI_API_KEY，默认保留供人工确认")

    post_fn = http_post or _default_http_post
    prompt = build_cull_prompt(count, default_album)
    b64_img = base64.b64encode(contact_sheet_jpeg).decode("ascii")

    try:
        if provider == "gemini":
            root = base_url or "https://generativelanguage.googleapis.com"
            url = f"{root}/v1beta/models/{model}:generateContent?key={api_key}"
            payload = {
                "contents": [
                    {
                        "parts": [
                            {"text": prompt},
                            {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}},
                        ]
                    }
                ],
                "generationConfig": {"responseMimeType": "application/json"},
            }
            resp = post_fn(url, {}, payload)
            text = (
                resp.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            )
            return parse_cull_response(text, count, default_album)

        root = base_url or "https://api.openai.com/v1"
        url = f"{root}/chat/completions"
        payload = {
            "model": model,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"},
                        },
                    ],
                }
            ],
        }
        resp = post_fn(url, {"Authorization": f"Bearer {api_key}"}, payload)
        text = resp.get("choices", [{}])[0].get("message", {}).get("content", "")
        return parse_cull_response(text, count, default_album)
    except (urllib.error.URLError, urllib.error.HTTPError, KeyError, IndexError, TimeoutError, ValueError):
        return default_keep_result(count, default_album)
