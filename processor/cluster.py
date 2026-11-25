from __future__ import annotations

import io
from datetime import datetime, timezone
from typing import Any

from PIL import Image, ImageDraw, ImageFont, ImageOps

EXIF_DATE_TAGS = (36867, 36868, 306)  # DateTimeOriginal, DateTimeDigitized, DateTime


def _parse_iso_timestamp(ts_str: str | None) -> float:
    if not ts_str:
        return 0.0
    normalized = ts_str.strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).timestamp()
    except ValueError:
        return 0.0


def extract_taken_at(raw_bytes: bytes, fallback_iso: str | None = None) -> str:
    """Extract EXIF capture timestamp as UTC ISO string, falling back safely."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as img:
            exif = img.getexif()
            for tag_id in EXIF_DATE_TAGS:
                val = exif.get(tag_id)
                if isinstance(val, str) and val.strip():
                    cleaned = val.strip().split("\x00", 1)[0]
                    try:
                        dt = datetime.strptime(cleaned, "%Y:%m:%d %H:%M:%S")
                        return dt.replace(tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                    except ValueError:
                        continue
    except Exception:
        pass

    if fallback_iso:
        return fallback_iso
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compute_dhash(raw_bytes: bytes) -> int:
    """Compute a 64-bit difference hash (dHash) after honoring EXIF orientation."""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        oriented = ImageOps.exif_transpose(img)
        gray = oriented.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
        get_pixels = getattr(gray, "get_flattened_data", gray.getdata)
        pixels = list(get_pixels())

    bits = 0
    for row in range(8):
        offset = row * 9
        for col in range(8):
            left = pixels[offset + col]
            right = pixels[offset + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming_distance(hash_a: int, hash_b: int) -> int:
    return (hash_a ^ hash_b).bit_count()


def cluster_photos(
    items: list[dict[str, Any]],
    window_seconds: int = 90,
    max_hamming: int = 12,
) -> list[list[dict[str, Any]]]:
    """
    Group photos taken within `window_seconds` and visually similar (`<= max_hamming`)
    into burst clusters, preserving chronological order.
    Each item must contain `taken_at` (ISO string) and `dhash` (int or hex str).
    """
    if not items:
        return []

    def _dhash_int(item: dict[str, Any]) -> int:
        val = item["dhash"]
        return int(val, 16) if isinstance(val, str) else int(val)

    sorted_items = sorted(
        items,
        key=lambda x: (_parse_iso_timestamp(x.get("taken_at")), str(x.get("filename", ""))),
    )

    clusters: list[list[dict[str, Any]]] = []
    for item in sorted_items:
        item_ts = _parse_iso_timestamp(item.get("taken_at"))
        item_hash = _dhash_int(item)
        placed = False

        # Check recent open clusters whose latest photo is within the time window.
        for cluster in reversed(clusters):
            last_ts = _parse_iso_timestamp(cluster[-1].get("taken_at"))
            if abs(item_ts - last_ts) > window_seconds:
                break
            if any(hamming_distance(item_hash, _dhash_int(m)) <= max_hamming for m in cluster):
                cluster.append(item)
                placed = True
                break

        if not placed:
            clusters.append([item])

    return clusters


def make_preview_jpeg(raw_bytes: bytes, max_edge: int = 960, quality: int = 84) -> bytes:
    """Create a lightweight EXIF-stripped JPEG preview safe for cloud review."""
    with Image.open(io.BytesIO(raw_bytes)) as img:
        oriented = ImageOps.exif_transpose(img).convert("RGB")
        oriented.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
        clean = Image.new("RGB", oriented.size)
        clean.paste(oriented)
        out = io.BytesIO()
        clean.save(out, format="JPEG", quality=quality, optimize=True)
        return out.getvalue()


def _load_label_font(size: int = 24) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def make_contact_sheet(
    image_bytes_list: list[bytes],
    labels: list[str] | None = None,
    thumb_size: tuple[int, int] = (480, 360),
    cols: int = 2,
) -> bytes:
    """Arrange burst images into a numbered contact sheet for multimodal comparison."""
    if not image_bytes_list:
        raise ValueError("image_bytes_list cannot be empty")

    count = len(image_bytes_list)
    cols = max(1, min(cols, count))
    rows = (count + cols - 1) // cols
    cell_w, cell_h = thumb_size
    pad = 12
    sheet_w = cols * cell_w + (cols + 1) * pad
    sheet_h = rows * cell_h + (rows + 1) * pad

    sheet = Image.new("RGB", (sheet_w, sheet_h), color=(20, 24, 30))
    draw = ImageDraw.Draw(sheet)
    font = _load_label_font(24)

    for idx, raw in enumerate(image_bytes_list):
        r = idx // cols
        c = idx % cols
        x0 = pad + c * (cell_w + pad)
        y0 = pad + r * (cell_h + pad)

        with Image.open(io.BytesIO(raw)) as img:
            tile = ImageOps.exif_transpose(img).convert("RGB")
            tile.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)

        offset_x = x0 + (cell_w - tile.width) // 2
        offset_y = y0 + (cell_h - tile.height) // 2
        sheet.paste(tile, (offset_x, offset_y))

        tag = labels[idx] if labels and idx < len(labels) else f"#{idx + 1}"
        draw.rounded_rectangle([x0 + 8, y0 + 8, x0 + 78, y0 + 44], radius=8, fill=(0, 0, 0))
        draw.text((x0 + 18, y0 + 13), tag, fill=(255, 255, 255), font=font)

    out = io.BytesIO()
    sheet.save(out, format="JPEG", quality=85)
    return out.getvalue()
