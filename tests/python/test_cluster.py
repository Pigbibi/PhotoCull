from __future__ import annotations

import io
import unittest

from PIL import Image

from processor.cluster import (
    cluster_photos,
    compute_dhash,
    extract_taken_at,
    hamming_distance,
    make_contact_sheet,
    make_preview_jpeg,
)


def _make_gradient_jpeg(invert: bool = False, with_exif: bool = False) -> bytes:
    img = Image.new("RGB", (120, 90))
    pixels = []
    for y in range(90):
        for x in range(120):
            val = int((x / 119.0) * 255)
            if invert:
                val = 255 - val
            pixels.append((val, val // 2, (val + y) % 256))
    img.putdata(pixels)

    buf = io.BytesIO()
    if with_exif:
        exif = Image.Exif()
        exif[36867] = "2026:10:03 14:22:10"
        exif[271] = "Xiaomi"
        img.save(buf, format="JPEG", exif=exif.tobytes())
    else:
        img.save(buf, format="JPEG")
    return buf.getvalue()


class ClusterTests(unittest.TestCase):
    def test_dhash_similarity_and_difference(self) -> None:
        img_a = _make_gradient_jpeg(invert=False)
        img_b = _make_gradient_jpeg(invert=True)
        hash_a = compute_dhash(img_a)
        hash_b = compute_dhash(img_b)

        self.assertEqual(hamming_distance(hash_a, hash_a), 0)
        self.assertGreater(hamming_distance(hash_a, hash_b), 20)

    def test_extract_taken_at_and_strip_exif_in_preview(self) -> None:
        raw_with_exif = _make_gradient_jpeg(with_exif=True)
        self.assertEqual(extract_taken_at(raw_with_exif), "2026-10-03T14:22:10Z")

        preview = make_preview_jpeg(raw_with_exif, max_edge=80)
        with Image.open(io.BytesIO(preview)) as out_img:
            self.assertLessEqual(max(out_img.size), 80)
            exif = out_img.getexif()
            self.assertEqual(len(exif), 0)

    def test_cluster_photos_groups_by_time_and_dhash(self) -> None:
        h_scene1 = compute_dhash(_make_gradient_jpeg(invert=False))
        h_scene2 = compute_dhash(_make_gradient_jpeg(invert=True))

        items = [
            {"id": "1", "filename": "a.jpg", "taken_at": "2026-10-03T10:00:00Z", "dhash": h_scene1},
            {"id": "2", "filename": "b.jpg", "taken_at": "2026-10-03T10:00:15Z", "dhash": h_scene1 ^ 0x1},
            {"id": "3", "filename": "c.jpg", "taken_at": "2026-10-03T10:00:25Z", "dhash": h_scene2},
            {"id": "4", "filename": "d.jpg", "taken_at": "2026-10-03T10:10:00Z", "dhash": h_scene1},
        ]
        clusters = cluster_photos(items, window_seconds=90, max_hamming=12)

        self.assertEqual(len(clusters), 3)
        self.assertEqual([i["id"] for i in clusters[0]], ["1", "2"])
        self.assertEqual([i["id"] for i in clusters[1]], ["3"])
        self.assertEqual([i["id"] for i in clusters[2]], ["4"])

    def test_make_contact_sheet_outputs_valid_jpeg(self) -> None:
        imgs = [_make_gradient_jpeg(), _make_gradient_jpeg(invert=True), _make_gradient_jpeg()]
        sheet = make_contact_sheet(imgs, thumb_size=(200, 150), cols=2)
        with Image.open(io.BytesIO(sheet)) as im:
            self.assertEqual(im.format, "JPEG")
            self.assertGreater(im.width, 300)
            self.assertGreater(im.height, 250)


if __name__ == "__main__":
    unittest.main()
