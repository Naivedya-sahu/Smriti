"""rebase.py — scroll-aware continuation: reply units anchored in PAGE
coordinates once, re-mapped to whatever view is on screen now.

Device numbers (2026-09-30 22:30, scrolled page): view right -468, top
-10354; registered offset (935, -10354), i.e. y maps as y + top and x is
not derivable from the fields. Sim: right = -702, top = -scroll."""
# pyright: reportMissingImports=false

import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import rebase as rb
import vectors

DEVICE = {"pageId": "p1", "scale": 1, "left": 0, "right": -468, "top": -10354}
FLAT = {"pageId": "p1", "scale": 1, "left": 0, "right": -702, "top": 0}


def sim_view(scroll, page="p1"):
    return {"pageId": page, "scale": 1, "left": 0, "right": -702, "top": -scroll}


def plan_of(n=6, y0=300, h=80, gap=40, x=-600, w=900, page="p1"):
    """n units stacked down the page from page y y0."""
    return rb.Plan(page, [rb.Unit(x, y0 + i * (h + gap), w, h) for i in range(n)])


class TestViewTransform(unittest.TestCase):
    def roundtrip(self, t):
        rng = np.random.default_rng(3)
        for sx, sy in rng.uniform([0, 0], [1404, 1872], (200, 2)):
            px, py = t.screen_to_page(sx, sy)
            bx, by = t.page_to_screen(px, py)
            self.assertLessEqual(abs(bx - sx), 1)
            self.assertLessEqual(abs(by - sy), 1)

    def test_roundtrip_device_scrolled_fields(self):
        self.roundtrip(rb.ViewTransform.from_view(DEVICE))

    def test_roundtrip_device_registered(self):
        self.roundtrip(rb.ViewTransform.from_view(DEVICE, offset=(935, -10354)))

    def test_roundtrip_unscrolled(self):
        self.roundtrip(rb.ViewTransform.from_view(FLAT))

    def test_y_maps_as_y_plus_top(self):
        t = rb.ViewTransform.from_view(DEVICE)
        self.assertEqual(t.page_to_screen(0, 10354 + 500)[1], 500)

    def test_registered_offset_wins_and_trusts_x(self):
        t = rb.ViewTransform.from_view(DEVICE, offset=(935, -10354))
        self.assertTrue(t.x_trusted)
        self.assertEqual(t.page_to_screen(-400, 10900), (535, 546))
        self.assertFalse(rb.ViewTransform.from_view(DEVICE).x_trusted)

    def test_vectors_offset_keys_are_read(self):
        t = rb.ViewTransform.from_view(dict(DEVICE, ox=935, oy=-10354))
        self.assertEqual((t.ox, t.oy), (935, -10354))

    def test_matches_vectors_to_screen(self):
        v = dict(DEVICE, ox=935, oy=-10354)
        t = rb.ViewTransform.from_view(v)
        stroke = [(-400.0, 10900.0), (-380.5, 10910.2), (0.0, 11000.0)]
        self.assertEqual(t.stroke_to_screen(stroke), vectors.to_screen(stroke, v))

    def test_sim_mapping(self):
        t = rb.ViewTransform.from_view(sim_view(900))
        self.assertEqual(t.page_to_screen(-702, 900), (0, 0))

    def test_visible_page_rect(self):
        t = rb.ViewTransform.from_view(DEVICE, offset=(935, -10354))
        self.assertEqual(t.visible_page_rect(), (-935, 10354, -935 + 1404, 10354 + 1872))

    def test_scale(self):
        t = rb.ViewTransform.from_view(dict(FLAT, scale=2))
        self.assertEqual(t.page_to_screen(-702 + 10, 20), (20, 40))
        self.assertEqual(t.screen_to_page(20, 40), (-692, 20))

    def test_registered_offset_keeps_scale_and_roundtrips(self):
        t = rb.ViewTransform.from_view(dict(FLAT, scale=2), offset=(-702, -300))
        self.assertEqual(t.scale, 2)
        self.roundtrip(t)

    def test_registered_offset_scale_matches_vectors(self):
        v = dict(FLAT, scale=2, ox=-702, oy=-300)
        stroke = [(-700.5, 299.5), (-692, 320), (0, 1000)]
        self.assertEqual(rb.ViewTransform.from_view(v).stroke_to_screen(stroke),
                         vectors.to_screen(stroke, v))


class TestPlan(unittest.TestCase):
    def test_from_screen_anchors_once_in_page(self):
        t = rb.ViewTransform.from_view(sim_view(500))
        p = rb.Plan.from_screen("p1", [(100, 200, 50, 30)], t)
        u = p.units[0]
        self.assertEqual((u.x, u.y, u.w, u.h), (100 - 702, 700, 50, 30))

    def test_mark_done_and_pending(self):
        p = plan_of(4)
        p.mark_done(0)
        p.mark_done(1)
        self.assertEqual([i for i, _ in p.pending()], [2, 3])


class TestRebase(unittest.TestCase):
    def test_scroll_down_800_shifts_minus_800(self):
        p = plan_of(6)
        p.mark_done(0)
        old, new = sim_view(0), sim_view(800)
        before = rb.rebase(p, old, old, {})
        after = rb.rebase(p, old, new, {})
        self.assertFalse(after.abort)
        self.assertEqual(after.dy, -800)
        self.assertEqual(len(after.units), 5)
        for a, b in zip(before.units, after.units):
            self.assertEqual(b.screen[1] - a.screen[1], -800)
            self.assertEqual(b.screen[0], a.screen[0])

    def test_scroll_up(self):
        p = plan_of(3, y0=1500)
        r = rb.rebase(p, sim_view(1000), sim_view(400), {})
        self.assertEqual(r.dy, 600)
        self.assertEqual(r.units[0].screen[1], 1500 - 400)

    def test_classification(self):
        p = rb.Plan("p1", [rb.Unit(-600, 100, 500, 80),      # above (scroll 500)
                           rb.Unit(-600, 460, 500, 80),      # partly (crosses top)
                           rb.Unit(-600, 900, 500, 80),      # visible
                           rb.Unit(-600, 500 + 1750, 500, 80),  # crosses the bottom 1780
                           rb.Unit(-600, 500 + 1900, 500, 80)])  # below
        r = rb.rebase(p, sim_view(0), sim_view(500), {}, bottom=1780)
        self.assertEqual([u.state for u in r.units],
                         [rb.ABOVE, rb.PARTLY, rb.VISIBLE, rb.PARTLY, rb.BELOW])

    def test_navy_ink_in_unit3_band_pushes_rest_down(self):
        p = plan_of(6)                                        # units at y 300, 420, 540 ...
        p.mark_done(0)
        p.mark_done(1)
        old = [(u.x, u.y, u.w, u.h) for u in p.units]
        navy = {"Layer 1": [[(-500.0, 550.0), (-300.0, 600.0), (-200.0, 640.0)]],
                "Monke": [[(-500.0, 800.0), (-300.0, 820.0)]]}  # Monke ink never pushes
        r = rb.rebase(p, sim_view(0), sim_view(0), navy, gap=30)
        self.assertEqual(r.shift_from, 2)
        self.assertEqual(r.shift, 640 + 30 - 540)
        units = r.plan.units
        self.assertEqual([(u.x, u.y) for u in units[:2]], [(o[0], o[1]) for o in old[:2]])
        ys = [u.y for u in units[2:]]
        self.assertEqual(ys[0], 670)
        self.assertEqual(np.diff(ys).tolist(), [120] * 3)      # spacing kept
        for u in units[2:]:
            self.assertGreater(u.y, 640)                       # never over Navy ink
        self.assertEqual(p.units[2].y, 540)                    # input plan untouched

    def test_ink_beside_band_no_shift(self):
        p = plan_of(4, x=-600, w=300)
        navy = {"Layer 1": [[(200.0, 430.0), (300.0, 470.0)]]}   # right of the column
        r = rb.rebase(p, sim_view(0), sim_view(0), navy)
        self.assertEqual(r.shift, 0)
        self.assertIsNone(r.shift_from)

    def test_two_ink_blobs_no_overlap(self):
        p = plan_of(6)
        navy = {"Layer 1": [[(-500.0, 440.0), (-300.0, 460.0)],
                            [(-500.0, 820.0), (-300.0, 900.0)]]}
        r = rb.rebase(p, sim_view(0), sim_view(0), navy, gap=20)
        boxes = rb._ink_boxes(navy)
        for u in r.plan.units:
            for b in boxes:
                self.assertFalse(rb._hit((u.x, u.y, u.x + u.w, u.y + u.h), b, 0))
        ys = [u.y for u in r.plan.units]
        self.assertTrue(all(b - a >= 120 for a, b in zip(ys, ys[1:])))

    def test_done_unit_overlapping_ink_is_left_alone(self):
        p = plan_of(3)
        p.mark_done(0)
        navy = {"Layer 1": [[(-500.0, 310.0), (-300.0, 330.0)]]}  # on top of a done unit
        r = rb.rebase(p, sim_view(0), sim_view(0), navy)
        self.assertEqual(r.shift, 0)

    def test_unit_scrolled_past_is_pulled_to_the_top_row(self):
        # Navy scrolled 800 past the next unit's spot: it (and the rest, spacing
        # kept) goes on at the first usable row of the view, never above it
        r = rb.rebase(plan_of(3), sim_view(0), sim_view(800), [], top=150)
        self.assertEqual([u.screen[1] for u in r.units], [150, 270, 390])
        self.assertEqual((r.shift, r.shift_from), (650, 0))
        self.assertEqual(r.units[0].state, rb.VISIBLE)

    def test_pulled_unit_still_dodges_navy_ink(self):
        ink = [[(-650, 940), (100, 1000)]]                      # page y 940-1000 = screen 140-200
        r = rb.rebase(plan_of(2), sim_view(0), sim_view(800), ink, top=150)
        self.assertEqual(r.units[0].screen[1], 240)

    def test_page_change_aborts(self):
        r = rb.rebase(plan_of(3), sim_view(0), sim_view(0, page="p2"), {})
        self.assertTrue(r.abort)
        self.assertEqual(r.units, [])

    def test_plan_page_mismatch_aborts(self):
        r = rb.rebase(plan_of(3, page="p9"), sim_view(0), sim_view(0), {})
        self.assertTrue(r.abort)

    def test_unreadable_view_aborts(self):
        self.assertTrue(rb.rebase(plan_of(3), sim_view(0), None, {}).abort)

    def test_page_turn_helper(self):
        self.assertTrue(rb.page_turn(plan_of(2), sim_view(0, page="p3")).abort)
        self.assertFalse(rb.page_turn(plan_of(2), sim_view(0)).abort)

    def test_200_units_under_10ms(self):
        p = plan_of(200, h=40, gap=10)
        rng = np.random.default_rng(5)
        navy = {"Layer 1": [[(float(x), float(y)), (float(x) + 80, float(y) + 30)]
                            for x, y in rng.uniform([-700, 0], [600, 10000], (100, 2))]}
        rb.rebase(p, sim_view(0), sim_view(800), navy)        # warm
        t = time.perf_counter()
        r = rb.rebase(p, sim_view(0), sim_view(800), navy)
        ms = (time.perf_counter() - t) * 1000
        self.assertEqual(len(r.units), 200)
        self.assertLess(ms, 10, f"{ms:.2f} ms")


class TestContinuation(unittest.TestCase):
    def test_ink_now(self):
        c = rb.continuation(plan_of(3), sim_view(0), page_height=10000, bottom_margin=92)
        self.assertEqual((c.decision, c.resume_y, c.index), ("ink_now", 300, 0))

    def test_wait_for_scroll_resume_y(self):
        p = plan_of(20)                                        # units run to y ~2700
        for i in range(14):
            p.mark_done(i)                                     # next: y 300 + 14*120 = 1980
        c = rb.continuation(p, sim_view(0), page_height=10000, bottom_margin=92)
        self.assertEqual(c.decision, "wait_for_scroll")
        self.assertEqual(c.resume_y, 1980)
        self.assertEqual(c.direction, "down")
        # after Navy scrolls 400 the same unit inks at its rebased spot
        c2 = rb.continuation(p, sim_view(400), page_height=10000, bottom_margin=92)
        self.assertEqual((c2.decision, c2.resume_y), ("ink_now", 1980))
        self.assertEqual(c2.screen[1], 1580)

    def test_page_full(self):
        p = plan_of(3, y0=1800)
        c = rb.continuation(p, sim_view(0), page_height=1872, bottom_margin=92)
        self.assertEqual(c.decision, "page_full")
        self.assertEqual(c.resume_y, 1800)

    def test_above_waits_for_scroll_up(self):
        c = rb.continuation(plan_of(3), sim_view(1000), page_height=10000, bottom_margin=92)
        self.assertEqual((c.decision, c.direction), ("wait_for_scroll", "up"))

    def test_unit_taller_than_the_screen_is_page_full(self):
        # gap 4: it can never be wholly visible, so waiting for a scroll is forever
        p = rb.Plan("p1", [rb.Unit(-600, 300, 900, 1700)])
        c = rb.continuation(p, sim_view(0), page_height=float("inf"), bottom_margin=92,
                            screen_top=150)
        self.assertEqual(c.decision, "page_full")
        self.assertIn("taller than the screen", c.reason)
        ok = rb.Plan("p1", [rb.Unit(-600, 160, 900, 1600)])
        self.assertEqual(rb.continuation(ok, sim_view(0), float("inf"), 92, screen_top=150).decision,
                         "ink_now")

    def test_done(self):
        p = plan_of(1)
        p.mark_done(0)
        self.assertEqual(rb.continuation(p, sim_view(0), 10000, 92).decision, "done")

    def test_other_page_aborts(self):
        c = rb.continuation(plan_of(2), sim_view(0, page="p2"), 10000, 92)
        self.assertEqual(c.decision, "abort")


class TestViewShift(unittest.TestCase):
    """gap 3: detect_view_moved can't place the ink (large scroll, little
    ink on screen) -> the view fields re-register y; else None = the caller
    re-anchors under the visible ink. Never a reason to abort."""

    def test_image_estimate_wins(self):
        self.assertEqual(rb.view_shift((True, -500.4, 1.2), sim_view(0), sim_view(800)), (-500, 1))

    def test_fields_refine_an_agreeing_image_estimate(self):
        # pixel medians are +-1 px; the view fields are exact when they agree
        self.assertEqual(rb.view_shift((True, -500.6, 0.4), sim_view(0), sim_view(500)), (-500, 0))

    def test_not_moved(self):
        self.assertEqual(rb.view_shift((False, 0.0, 0.0), sim_view(0), sim_view(0)), (0, 0))

    def test_unknown_offset_falls_back_to_view_fields(self):
        self.assertEqual(rb.view_shift((True, None, None), sim_view(100), sim_view(900)), (-800, 0))
        self.assertEqual(rb.view_shift((True, None, None), DEVICE, dict(DEVICE, top=-10954)),
                         (-600, 0))

    def test_unknown_offset_and_no_field_change_is_none(self):
        self.assertIsNone(rb.view_shift((True, None, None), sim_view(100), sim_view(100)))
        self.assertIsNone(rb.view_shift((True, None, None), None, sim_view(100)))
        self.assertIsNone(rb.view_shift((True, None, None), {"pageId": "p1"}, sim_view(100)))


def strokes_pts(rng, n=40):
    pts = []
    for _ in range(n):
        x, y = rng.uniform([100, 100], [1200, 1500])
        pts += [(x + 6 * k, y + 15 * np.sin(k / 2)) for k in range(20)]
    return np.array(pts)


class TestDetectViewMoved(unittest.TestCase):
    def observed(self, exp, dx, dy, rng, missing=0.3, outliers=0.1):
        keep = exp[rng.random(len(exp)) >= missing] + (dx, dy)
        keep = keep + rng.integers(-1, 2, keep.shape)
        noise = rng.uniform([0, 0], [1404, 1872], (int(outliers * len(exp)), 2))
        return np.vstack([keep, noise])

    def test_recovers_dy_with_missing_and_outliers(self):
        rng = np.random.default_rng(7)
        exp = strokes_pts(rng)
        for dy in (-800, -500, -37, 250, 600):
            moved, edy, edx = rb.detect_view_moved(exp, self.observed(exp, 0, dy, rng))
            self.assertTrue(moved)
            self.assertLessEqual(abs(edy - dy), 2, (dy, edy))
            self.assertLessEqual(abs(edx), 2)

    def test_recovers_dx_and_dy(self):
        rng = np.random.default_rng(8)
        exp = strokes_pts(rng)
        moved, edy, edx = rb.detect_view_moved(exp, self.observed(exp, 120, -300, rng))
        self.assertTrue(moved)
        self.assertLessEqual(abs(edy + 300), 2)
        self.assertLessEqual(abs(edx - 120), 2)

    def test_partial_redraw_is_not_a_move(self):
        rng = np.random.default_rng(9)
        exp = strokes_pts(rng)
        moved, edy, edx = rb.detect_view_moved(exp, self.observed(exp, 0, 0, rng, missing=0.5))
        self.assertFalse(moved)
        self.assertLessEqual(abs(edy), 2)

    def test_accepts_mask(self):
        rng = np.random.default_rng(10)
        exp = strokes_pts(rng)
        obs = self.observed(exp, 0, -400, rng).astype(int)
        m = np.zeros((1872, 1404), bool)
        ok = (obs[:, 0] >= 0) & (obs[:, 0] < 1404) & (obs[:, 1] >= 0) & (obs[:, 1] < 1872)
        m[obs[ok, 1], obs[ok, 0]] = True
        moved, edy, _ = rb.detect_view_moved(exp, m)
        self.assertTrue(moved)
        self.assertLessEqual(abs(edy + 400), 2)

    def test_nothing_on_screen_is_moved_unknown(self):
        rng = np.random.default_rng(11)
        self.assertEqual(rb.detect_view_moved(strokes_pts(rng), np.zeros((0, 2))),
                         (True, None, None))


def handwriting(lines, y0, text="It maps a signal from time to frequency. Inverse brings it back."):
    """Real cursive strokes (ink.render), `lines` lines from screen y0."""
    from ink import load_styles, render
    style = load_styles()["cursive"]
    return [s for i in range(lines) for s in render(text[i * 7:] + text[:i * 7], style,
                                                     x=130, y=y0 + 90 * i)]


def raster(strokes, dy=0):
    from PIL import Image, ImageDraw
    img = Image.new("1", (1404, 1872), 0)
    d = ImageDraw.Draw(img)
    for s in strokes:
        if len(s) > 1:
            d.line([(x, y + dy) for x, y in s], fill=1, width=4)
    return np.asarray(img, dtype=bool)


def pts(strokes):
    return [p for s in strokes for p in s]


class TestDetectAmongText(unittest.TestCase):
    """Sim 2026-10-12: on real cursive the densest vote block was often other
    text, not the ink Monke expects: 'scrolled -209 px' with no scroll, +139
    for a 700 px scroll. The ink still in place, or the offset where most
    of it is found, must win."""

    def test_ink_in_place_amid_dense_text_is_not_moved(self):
        ok = handwriting(1, 1000, "ok")
        seen = raster(handwriting(6, 300) + ok)
        self.assertEqual(rb.detect_view_moved(pts(ok), seen), (False, 0.0, 0.0))

    def test_scroll_amid_dense_text_finds_the_true_offset(self):
        reply = handwriting(4, 900)
        seen = raster(reply, -700) | raster(handwriting(5, 700, "Second answer, different words here."))
        moved, dy, dx = rb.detect_view_moved(pts(reply), seen)
        self.assertTrue(moved)
        self.assertLessEqual(abs(dy + 700), 2, dy)

    def test_support_scores_an_offset(self):
        reply = handwriting(2, 900)
        seen = raster(reply, -500)
        self.assertGreater(rb.support(pts(reply), seen, -500, 0), 0.9)
        self.assertLess(rb.support(pts(reply), seen, 0, 0), 0.2)

    def test_blind_screen_zones_do_not_count(self):
        # screen.UI_BOXES are blanked in the ink mask: ink scrolled under
        # xochitl's top row is not 'missing' there (sim scroll_between_parts)
        reply = handwriting(1, 510)                     # scrolls to rows ~10-90: all under the UI row
        seen = raster(reply, -500).copy()
        seen[:100] = False
        blind = [(0, 0, 1404, 100)]
        self.assertLess(rb.support(pts(reply), seen, -500, 0), 0.5)
        s = rb.support(pts(reply), seen, -500, 0, blind=blind)
        self.assertTrue(s is None or s > 0.9, s)
        self.assertEqual(rb.view_shift((True, None, None), sim_view(0), sim_view(500), pts(reply),
                                       seen, blind=blind), (-500, 0))

    def test_view_shift_takes_the_best_supported_candidate(self):
        reply = handwriting(3, 900)
        seen = raster(reply, -500) | raster(handwriting(4, 100, "other ink"))
        got = rb.view_shift((True, -209.0, 0.0), sim_view(0), sim_view(500), pts(reply), seen)
        self.assertEqual(got, (-500, 0))
        # neither candidate finds the ink: unknown, the caller re-anchors
        self.assertIsNone(rb.view_shift((True, -209.0, 0.0), sim_view(0), sim_view(300),
                                        pts(reply), raster([])))


if __name__ == "__main__":
    unittest.main()
