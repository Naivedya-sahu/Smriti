"""monke._Streamer on rebase.py: a streamed reply's parts are anchored in
page coordinates and re-mapped to the view on screen before each one inks
(Navy scrolled, wrote into the next part's band, reached the page bottom,
turned the page). One test per wiring gap (docs/lanes/rebase-WIRING-REPORT.md)."""
# pyright: reportMissingImports=false

import contextlib
import io
import sys
import tomllib
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import monke  # noqa: E402
import rebase  # noqa: E402
import vectors  # noqa: E402
from ink import load_styles  # noqa: E402

SIM = {"pageId": "p1", "scale": 1, "left": 0, "right": -702, "top": 0}
DEVICE = {"pageId": "p1", "scale": 1, "left": 0, "right": -468, "top": -10354}


def line(y, x0=200, x1=600):
    return [(x, y) for x in range(x0, x1, 4)]


Q = [line(200)]                                  # Navy's question, screen px


def shot(*strokes, width=4, **info):
    img = Image.new("RGB", (1404, 1872), (255, 255, 255))
    d = ImageDraw.Draw(img)
    for s in strokes:
        d.line(s, fill=(0, 0, 0), width=width)
    img.info.update(info)
    return img


def unit(h=60):
    """lay(y, log): a two-line part with its top at y."""
    return lambda y, log: [line(y), line(y + h)]


class Wired(unittest.TestCase):
    def setUp(self):
        self.lamp = MagicMock()
        self.frames = []
        self.view = dict(SIM)
        self.out = io.StringIO()
        st = contextlib.ExitStack()
        self.addCleanup(st.close)
        for name, val in (("lamp", self.lamp), ("qml_eval", MagicMock(return_value="2")),
                          ("_same_page", MagicMock(return_value=True)),
                          ("_on_monke", MagicMock(return_value=contextlib.nullcontext())),
                          ("_shot", lambda *a: self.frames.pop(0) if self.frames else None),
                          ("_echo_mark", MagicMock()), ("_set_stage", MagicMock())):
            st.enter_context(patch.object(monke, name, val))
        st.enter_context(patch.object(monke.time, "sleep"))
        st.enter_context(patch.object(vectors, "view", lambda host: self.view))
        st.enter_context(patch.dict(monke._TURN, view=dict(SIM), offset=None))
        st.enter_context(contextlib.redirect_stdout(self.out))

    def streamer(self, y=300, q=Q):
        return monke._Streamer(q, load_styles()["cursive"], y, 1780, "h", 3, None,
                               lambda *a: None, None)

    def inked_ys(self):
        return sorted({int(ln.split()[3]) for c in self.lamp.call_args_list
                       for ln in c.args[0].splitlines() if ln.startswith("pen down")})


class PageHeight(Wired):
    """gap 1: [monke] page_height, one default (monke.PAGE_HEIGHT)."""

    def test_documented_default_is_the_code_default(self):
        cfg = tomllib.loads((ROOT / "config.example.toml").read_text(encoding="utf-8"))
        self.assertEqual(cfg["monke"]["page_height"], monke.PAGE_HEIGHT)

    def test_page_end_stops_the_reply(self):
        self.frames = [shot(*Q)]
        with patch.object(monke, "PAGE_HEIGHT", 1000):
            s = self.streamer(y=900)
            self.assertIsNone(s._put(unit()))
        self.assertTrue(s.stopped)
        self.lamp.assert_not_called()
        self.assertIn("no room left on the page", self.out.getvalue())


class RegisteredOffset(Wired):
    """gap 2: x is trusted only with a registered offset on a scrolled page."""

    def test_turn_offset_feeds_the_transform(self):
        monke._TURN.update(view=dict(DEVICE), offset=(935, -10354))
        vt = self.streamer().vt
        self.assertEqual((vt.ox, vt.oy, vt.x_trusted), (935, -10354, True))
        monke._TURN.update(offset=None)
        self.assertFalse(self.streamer().vt.x_trusted)

    def test_turn_start_registers_the_prompt_offset(self):
        """the real call site: _reply's turn start sets _TURN['offset'] from #P."""
        page = {"Layer 1": [[(x - 935.0, y + 10354.0) for x, y in s] for s in Q]}
        monke._turn_start(shot(*Q, view=dict(DEVICE), layers=page), Q, "h")
        self.assertEqual((monke._TURN["offset"], monke._TURN["view"]["top"]), ((935, -10354), -10354))
        self.assertTrue(self.streamer().vt.x_trusted)
        monke._turn_start(None, Q, "h")
        self.assertEqual((monke._TURN["offset"], monke._TURN["view"]), (None, None))

    def test_prompt_offset_registered_at_turn_start(self):
        page = {"Layer 1": [[(x - 935.0, y + 10354.0) for x, y in s] for s in Q]}
        self.assertEqual(monke._turn_offset(Q, page), (935, -10354))
        self.assertIsNone(monke._turn_offset(Q, None))
        self.assertIsNone(monke._turn_offset(None, page))

    def test_page_file_ink_only_with_trusted_x(self):
        navy = [(-500.0, 11500.0), (0.0, 11500.0)]
        frame = shot(line(800), layers={"Layer 1": [navy], "Monke": [[(0.0, 0.0), (9.0, 9.0)]]})
        trusted = rebase.ViewTransform.from_view(DEVICE, offset=(935, -10354))
        self.assertIn(navy, monke._navy_ink(frame, trusted, []))
        guessed = rebase.ViewTransform.from_view(DEVICE)
        got = monke._navy_ink(frame, guessed, [])
        self.assertNotIn(navy, got)
        self.assertNotIn([(0.0, 0.0), (9.0, 9.0)], monke._navy_ink(frame, trusted, []))
        # screenshot ink is always there, in page coordinates
        ys = [p[1] for s in got for p in s]
        self.assertTrue(ys and min(ys) >= 800 + 10354 - 3 and max(ys) <= 800 + 10354 + 3)

    def test_own_ink_is_not_navy_ink(self):
        vt = rebase.ViewTransform.from_view(SIM)
        self.assertEqual(monke._navy_ink(shot(line(800)), vt, [line(800)]), [])


class Scroll(Wired):
    def test_follow_registered_offset_at_scale_two_moves_pending_screen_anchor(self):
        """View-shift deltas are screen pixels; registered offsets are page
        units. A 100-page-px scroll is a 200-screen-px move at scale two."""
        monke._TURN.update(view=dict(SIM, scale=2), offset=(0, 0))
        self.view = dict(SIM, scale=2, top=-100)
        s = self.streamer(y=600)
        s.inked = [line(400)]
        s.anchors.units.append(rebase.Unit(200, 300, 400, 60))
        with patch.object(rebase, "detect_view_moved", return_value=(True, None)), \
                patch.object(rebase, "view_shift", return_value=(-200, 0)):
            self.assertTrue(s._follow(shot()))
        self.assertEqual((s.vt.oy, s.y), (-100, 400))
        self.assertEqual(s.vt.page_to_screen(200, 300)[1], 400)

    def test_scrolled_part_inks_at_its_rebased_spot(self):
        q = [line(900), line(960, 220, 580)]
        self.frames = [shot(line(400), line(460, 220, 580))]         # Navy scrolled 500
        self.view = dict(SIM, top=-500)
        s = self.streamer(y=1000, q=q)
        s._put(unit())
        self.assertEqual(self.inked_ys(), [500, 560])
        self.assertEqual(s.vt.oy, -500)
        self.assertIn("scrolled", self.out.getvalue())

    def test_unknown_offset_reregisters_from_the_view_fields(self):
        """gap 3: the ink left the screen: view fields give dy; not an abort."""
        self.frames = [shot()]
        self.view = dict(SIM, top=-800)
        s = self.streamer(y=1000)
        self.assertTrue(s._follow(shot()))
        self.assertEqual((s.vt.oy, s.y, s.stopped), (-800, 200, False))
        self.assertEqual(s.q[0][0], (200, -600))

    def test_unknown_offset_and_fields_reanchor_under_visible_ink(self):
        frame = shot([(1000, 700), (1000, 760)])
        s = self.streamer(y=1000)
        s.inked = [line(900)]
        self.assertTrue(s._follow(frame))
        self.assertEqual((s.y, s.inked, s.stopped, s.moved), (810, [], False, True))

    def test_reanchor_moves_the_pending_part_under_visible_ink(self):
        s = self.streamer(y=1000)
        s.inked = [line(900)]
        s.anchors.units.append(rebase.Unit(200, 1000, 400, 60))
        self.assertTrue(s._follow(shot([(1000, 700), (1000, 760)])))
        self.assertEqual(s.anchors.pending()[0][1].y, s.vt.screen_to_page(0, 810)[1])

    def test_reanchor_once_not_every_poll(self):
        """offset unknown: x no longer trusted, and the stale screen spots are
        dropped (else every _await_scroll poll re-anchored again)."""
        frame = shot([(1000, 700), (1000, 760)])
        s = self.streamer(y=1000)
        s.inked = [line(900)]
        self.assertTrue(s.vt.x_trusted)
        self.assertTrue(s._follow(frame) and s._follow(frame) and s._follow(frame))
        self.assertEqual(self.out.getvalue().count("under the ink now on screen"), 1)
        self.assertFalse(s.vt.x_trusted)

    def test_navy_ink_in_the_band_pushes_the_part_under_it(self):
        self.frames = [shot(*Q, line(330, 300, 500))]
        s = self.streamer(y=300)
        s._put(unit())
        self.assertEqual(self.inked_ys()[0], 330 + 2 + 40)       # 4 px pen: ink ends at 332

    def test_part_taller_than_the_screen_is_logged_not_waited_for(self):
        """gap 4"""
        self.frames = [shot(*Q)]
        s = self.streamer(y=300)
        self.assertIsNone(s._put(unit(h=1700)))
        self.assertTrue(s.stopped)
        self.lamp.assert_not_called()
        self.assertIn("taller than the screen", self.out.getvalue())

    def test_page_turn_stops_the_reply(self):
        """gap 5: another page id never gets ink, moved or not."""
        self.frames = [shot()]
        self.view = dict(SIM, pageId="p2")
        s = self.streamer()
        self.assertIsNone(s._put(unit()))
        self.assertTrue(s.stopped and s.blocked)
        self.frames = [shot(*Q, view=dict(SIM, pageId="p2"))]
        s = self.streamer()
        self.assertIsNone(s._put(unit()))
        self.assertTrue(s.stopped)
        self.lamp.assert_not_called()


class VerifyAfterScroll(Wired):
    def test_measured_view_move_never_redraws(self):
        """sim page_fills_up: Navy scrolled between colour batches of a graph;
        the frame missing at the shifted spot was redrawn there (duplicate
        frame + orphan). A measured move = nothing redrawn."""
        strokes = [[(100 + 40 * i + k, 700 + 5 * k) for k in range(0, 40, 5)]
                   for i in range(10)]
        moved = monke._at(strokes, -498)
        monke._verify_ink(strokes, "h", 2400, 3, shot=shot(*moved[1:]))
        self.lamp.assert_not_called()
        self.assertIn("nothing redrawn", self.out.getvalue())

    def test_scroll_between_colour_batches_moves_the_rest_of_the_part(self):
        """sim page_fills_up: Navy's scroll landed between the frame (black)
        and the curve (blue) of one graph; the curve inked at the old screen
        spot, 500 px under its frame on the page."""
        q = [line(900), line(960, 220, 580)]
        frame = [monke.Drawn(line(1000), 0), monke.Drawn(line(1060), 0)]
        curve = [monke.Drawn(line(1030, 220, 580), 6)]
        up = lambda ss: monke._at(ss, -500)                         # noqa: E731
        self.frames = [shot(*q), shot(*up(q), *up(frame))]
        self.view = dict(SIM, top=-500)
        s = self.streamer(y=1000, q=q)
        s._put(lambda y, log: frame + curve)
        self.assertEqual(self.inked_ys(), [530, 1000, 1060])
        self.assertEqual(s.y, 1060 - 500 + 40)

    def test_strokes_off_the_screen_after_a_scroll_are_logged_not_kept(self):
        """a scroll between colour batches pushed part of the curve past the
        screen edge: it is not inked, not kept as inked, and logged."""
        q = [line(900), line(960, 220, 580)]
        frame = [monke.Drawn(line(1000), 0), monke.Drawn(line(1060), 0)]
        curve = [monke.Drawn(line(1030, 220, 580), 6), monke.Drawn(line(1700, 220, 580), 6)]
        down = lambda ss: monke._at(ss, 300)                        # noqa: E731
        def partial():
            self.frames = [shot(*q), shot(*down(q), *down(frame))]
            self.view = dict(SIM, top=300)
            s = self.streamer(y=1000, q=q)
            s._put(lambda y, log: frame + curve)
            return s

        s = partial()
        self.assertEqual(self.inked_ys(), [1000, 1060, 1330])
        self.assertEqual(sorted(st[0][1] for st in s.inked), [1300, 1330, 1360])   # screen now
        self.assertTrue(all(0 <= y < monke.CANVAS_H for st in s.inked for _, y in st))
        self.assertIn("1 strokes off the screen", self.out.getvalue())
        self.assertFalse(s.anchors.units[0].done)
        with patch.object(monke, "SCROLL_WAIT", 0):
            self.assertIsNone(s._put(lambda y, log: [line(y)]))
        self.assertEqual(self.inked_ys(), [1000, 1060, 1330])
        self.assertTrue(s.partial)
        s.vt = rebase.ViewTransform.from_view(SIM, offset=(702, 0))
        self.assertEqual([min(y for _, y in st) for st in s._put(lambda y, log: [line(y)])], [1700])
        self.assertTrue(s.anchors.units[0].done)
        self.assertEqual(self.inked_ys(), [1000, 1060, 1330, 1700])

        for page_height, height, reason in ((1750, None, "no room left on the page"),
                                            (0, monke.CANVAS_H, "taller than the screen")):
            s = partial()
            s.anchors.units[0].y = 1700
            s.vt = rebase.ViewTransform.from_view(SIM, offset=(702, -1600))
            if height is not None:
                s.anchors.units[0].h = height
            self.lamp.reset_mock()
            self.frames = [shot()]
            before = self.out.getvalue().count("no room for the next part")
            with patch.object(monke, "PAGE_HEIGHT", page_height):
                self.assertIsNone(s._put(lambda y, log: [line(y)]))
            self.assertTrue(s.stopped)
            self.assertIn(reason, self.out.getvalue())
            self.assertEqual(self.out.getvalue().count("no room for the next part"), before + 1)
            self.assertFalse(self.lamp.called)
            self.frames = [shot()]
            self.assertIsNone(s._put(lambda y, log: [line(y)]))
            self.assertEqual(self.out.getvalue().count("no room for the next part"), before + 1)
            self.assertEqual(len(self.frames), 1)

    def test_small_measured_shift_is_jitter_and_redraws(self):
        """|dx|, |dy| <= VERIFY_JITTER: detection jitter, not a view move —
        a real render miss is still repaired."""
        strokes = [line(700 + 30 * i) for i in range(10)]
        frame = shot(*strokes[5:])
        for d, redraw in (((monke.VERIFY_JITTER, -monke.VERIFY_JITTER), True),
                          ((monke.VERIFY_JITTER + 1, 0), False), ((0, -monke.VERIFY_JITTER - 1), False)):
            self.lamp.reset_mock()
            with patch.object(rebase, "view_shift", return_value=d):
                monke._verify_ink(strokes, "h", 2400, 3, shot=frame)
            self.assertEqual(self.lamp.called, redraw, d)
        self.assertIn("5/10 strokes missing on screen — redrawing", self.out.getvalue())
        self.assertIn("nothing redrawn", self.out.getvalue())

    def test_page_bottom_waits_for_the_second_swipe(self):
        """sim page_fills_up: Navy scrolls in two swipes ~1.5 s apart; one
        still frame after swipe 1 inked the graph's frame, swipe 2 then moved
        its curve 500 px down the page. Ink after 2 s of a still view."""
        q = [line(1500), line(1560, 220, 580)]
        up = lambda dy: shot(*monke._at(q, -dy))                     # noqa: E731
        self.frames = [shot(*q), up(400), up(400), up(900), up(900), up(900)]
        self.view = dict(SIM, top=-900)
        s = self.streamer(y=1760, q=q)
        clock = iter(range(0, 10**6, 1))
        with patch.object(monke, "SCROLL_WAIT", 20), \
                patch.object(monke.time, "time", lambda: next(clock)):
            s._put(unit())
        self.assertEqual(self.inked_ys(), [860, 920])
        self.assertEqual(s.vt.oy, -900)


if __name__ == "__main__":
    unittest.main()
