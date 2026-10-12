"""Offline regression tests for screenshot-derived placement facts."""

import io
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import screen


class ScreenAlgorithmsTest(unittest.TestCase):
    def setUp(self):
        self.blank = Image.new("L", (screen.CANVAS_W, screen.CANVAS_H), 255)

    def test_blank_page_has_no_ink_and_full_free_band(self):
        self.assertEqual(screen.ink_floor(self.blank), 0)
        self.assertEqual(screen.free_bands(self.blank), [(0, screen.CANVAS_H)])

    def test_inked_row_splits_free_space_at_exact_boundary(self):
        page = self.blank.copy()
        ImageDraw.Draw(page).rectangle((200, 500, 400, 500), fill=0)
        self.assertEqual(screen.ink_floor(page), 500)
        self.assertEqual(screen.free_bands(page), [(0, 500), (501, screen.CANVAS_H)])

    def test_toolbar_and_eye_pixels_are_not_page_ink(self):
        page = self.blank.copy()
        draw = ImageDraw.Draw(page)
        draw.rectangle((0, 100, 100, 1700), fill=0)
        draw.rectangle((200, 0, 400, 80), fill=0)
        draw.rectangle((1300, 1780, 1380, 1840), fill=0)
        self.assertEqual(screen.ink_floor(page), 0)
        self.assertEqual(screen.free_bands(page), [(0, screen.CANVAS_H)])
        self.assertFalse(screen.ink_mask(page).any())

    def test_page_diff_ignores_ui_and_below_threshold_changes(self):
        after = self.blank.copy()
        draw = ImageDraw.Draw(after)
        draw.rectangle((0, 200, 100, 800), fill=0)
        draw.rectangle((200, 300, 209, 309), fill=0)
        self.assertIsNone(screen.page_diff(self.blank, after))

    def test_page_diff_returns_exact_meaningful_change_box(self):
        after = self.blank.copy()
        draw = ImageDraw.Draw(after)
        draw.rectangle((0, 200, 100, 800), fill=0)
        draw.rectangle((200, 300, 224, 319), fill=0)
        self.assertEqual(screen.page_diff(self.blank, after), (200, 300, 224, 319))

    def test_dense_noise_frame_rejected_without_grid_template(self):
        frame = self.blank.copy()
        ImageDraw.Draw(frame).rectangle((0, 200, screen.CANVAS_W - 1, 700), fill=0)
        self.assertFalse(screen.looks_valid(frame, template="none"))
        self.assertTrue(screen.looks_valid(self.blank, template="none"))

    def test_screenshot_keeps_png_color_for_model_input(self):
        page = self.blank.convert("RGB")
        ImageDraw.Draw(page).rectangle((200, 300, 300, 350), fill=(15, 130, 210))
        png = io.BytesIO()
        page.save(png, format="PNG")
        with patch("urllib.request.urlopen", side_effect=[
            io.BytesIO(b'{"token":"dummy"}'), io.BytesIO(png.getvalue())
        ]) as fetch:
            result = screen.gms_screenshot("https://example.invalid")
        self.assertEqual(result.mode, "RGB")
        self.assertEqual(result.getpixel((250, 325)), (15, 130, 210))
        self.assertEqual(fetch.call_count, 2)

    def test_colored_page_uses_luminance_for_validity(self):
        page = self.blank.convert("RGB")
        ImageDraw.Draw(page).rectangle((200, 300, 900, 750), fill=(0, 255, 255))
        self.assertTrue(screen.looks_valid(page.convert("L"), template="none"))
        self.assertTrue(screen.looks_valid(page, template="none"))

    def test_page_diff_accepts_colored_screenshots(self):
        before = self.blank.convert("RGB")
        after = before.copy()
        ImageDraw.Draw(after).rectangle((200, 300, 224, 319), fill=(0, 0, 255))
        self.assertEqual(screen.page_diff(before, after), (200, 300, 224, 319))


class DotGridValidityTest(unittest.TestCase):
    @staticmethod
    def dots():
        img = Image.new("RGB", (1404, 1872), "white")
        d = ImageDraw.Draw(img)
        for y in range(20, 1872, 42):
            for x in range(10, 1404, 42):
                d.rectangle((x, y, x + 1, y + 1), fill=(90, 90, 90))
        return img, d

    def test_clean_dot_page_valid(self):
        self.assertTrue(screen.looks_valid(self.dots()[0]))

    def test_real_frame_with_erase_residue_stays_valid(self):
        # device 2026-09-29: leftover erase fragments across ~1000 columns
        # (one row band) broke the column period (0.16) -> a clean page was
        # rejected, gms restarted and the erase recheck was skipped
        img = Image.open(Path(__file__).parent / "fixtures" / "erase-residue.png")
        self.assertTrue(screen.looks_valid(img))

    def test_shifted_wraparound_frame_still_rejected(self):
        import numpy as np
        a = np.asarray(self.dots()[0]).copy()
        a = np.roll(a.reshape(-1, 3), 777, axis=0).reshape(a.shape)   # row wrap
        noisy = np.random.default_rng(3).integers(0, 255, a.shape[:2]) < 90
        a[noisy] = 0
        self.assertFalse(screen.looks_valid(Image.fromarray(a)))

if __name__ == "__main__":
    unittest.main()


class FramebufferTest(unittest.TestCase):
    # goMarkableStream replaced as the default source (2026-09-30): the frame
    # is read from xochitl's memory over the persistent shell, 0.45 s vs 1.2 s,
    # pid resolved per call (gms cached it -> stale/offset frames)
    def raw(self):
        import gzip
        import numpy as np
        a = np.full((screen.CANVAS_H, screen.CANVAS_W, 4), 255, np.uint8)
        a[10, 20] = (0, 0, 255, 255)                  # BGRA red
        return gzip.compress(b"\0" * 8 + a.tobytes() + b"\0" * 100)

    def test_decode_bgra_to_rgb(self):
        img = screen.fb_decode(self.raw())
        self.assertEqual(img.size, (screen.CANVAS_W, screen.CANVAS_H))
        self.assertEqual(img.getpixel((20, 10)), (255, 0, 0))
        self.assertEqual(img.getpixel((0, 0)), (255, 255, 255))

    def test_screenshot_uses_framebuffer_not_http(self):
        import tablet
        with (patch.object(tablet, "run", return_value=(0, self.raw())),
              patch("urllib.request.urlopen", side_effect=AssertionError("http"))):
            self.assertEqual(screen.screenshot().getpixel((20, 10)), (255, 0, 0))

    def test_short_read_is_no_frame(self):
        import gzip
        import tablet
        with (patch.object(tablet, "run", return_value=(0, gzip.compress(b"x" * 99))),
              patch("urllib.request.urlopen", side_effect=OSError("no gms"))):
            self.assertIsNone(screen.screenshot())


class ThinInkTest(unittest.TestCase):
    # run 14 (device): a thin Monke-layer graph (5-19 dark px per row) read as
    # free space under the 80 px/row dot-grid threshold -> question and reply
    # inked on top of it
    def dotted(self):
        from PIL import ImageDraw
        page = Image.new("L", (screen.CANVAS_W, screen.CANVAS_H), 255)
        d = ImageDraw.Draw(page)
        for y in range(20, screen.CANVAS_H, 42):
            for x in range(20, screen.CANVAS_W, 42):
                d.rectangle((x, y, x + 1, y + 1), fill=0)      # 2x2 dots, like the device
        return page

    def test_dot_grid_alone_is_blank(self):
        self.assertEqual(screen.ink_floor(self.dotted()), 0)
        self.assertEqual(screen.free_bands(self.dotted()), [(0, screen.CANVAS_H)])

    def test_thin_curve_is_ink(self):
        from PIL import ImageDraw
        page = self.dotted()
        ImageDraw.Draw(page).line([(300, 600), (500, 700), (900, 760)], fill=0, width=1)
        self.assertGreaterEqual(screen.ink_floor(page), 755)
        self.assertFalse(any(a < 650 < b for a, b in screen.free_bands(page)))

    def test_real_frame_graph_rows_are_not_free(self):
        img = Image.open(Path(__file__).parent / "fixtures" / "thin-graph-under-reply.png")
        for a, b in screen.free_bands(img):
            self.assertFalse(a < 600 < b or a < 700 < b, (a, b))

    def test_page_turn_label_and_scrollbar_are_ui(self):
        """run 7: ~5 s after a page turn xochitl shows 'Page n of m' + a
        scrollbar; unmasked they read as floor 1849 on a blank page."""
        img = Image.open(Path(__file__).parent / "fixtures" / "page-turn-label.png")
        self.assertEqual(screen.ink_floor(img), 0)
