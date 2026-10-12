"""Offline checks for mixed output in the protected inking path."""
# pyright: reportMissingImports=false

import contextlib
import sys
import unittest
import unittest.mock
from pathlib import Path
from unittest.mock import call, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import monke


class InkVerificationTest(unittest.TestCase):
    def test_mixed_reply_verifies_prose_and_drawing_separately(self):
        prose = [(200, 300), (220, 300)]
        drawing = monke.Drawn([(200, 400), (220, 400)])
        with (patch.object(monke, "_on_monke", return_value=contextlib.nullcontext()),
              patch.object(monke, "SAFE_LAYER", False),
              patch.object(monke, "lamp"),
              patch.object(monke, "_echo_mark"),
              patch.object(monke, "_shot", return_value="SHOT") as shot,
              patch.object(monke.time, "sleep"),
              patch.object(monke, "_verify_ink") as verify):
            self.assertTrue(monke._ink([prose, drawing], 2400, 3, "test"))
        # one e-ink wait + ONE screenshot checks both (was two, ~2 s more);
        # the drawing is checked while the thin pen is still selected
        self.assertEqual(shot.call_count, 1)
        self.assertEqual(verify.call_args_list,
                         [call([drawing], "test", 2400, 3, shot="SHOT"),
                          call([prose], "test", 2400, 3, shot="SHOT")])

    def test_verify_counts_the_same_strokes_it_checks(self):
        # device 2026-09-29 log: "21/18 strokes missing" (missing counted
        # short strokes the total skipped)
        from PIL import Image
        blank = Image.new("RGB", (1404, 1872), "white")
        short = [[(100 + 10 * i, 500), (104 + 10 * i, 500), (108 + 10 * i, 500),
                  (112 + 10 * i, 500), (116 + 10 * i, 500)] for i in range(4)]
        missing, checked = monke._missing(short, blank)
        self.assertLessEqual(len(missing), checked)
        self.assertEqual((len(missing), checked), (4, 4))


class EraseCmdsTest(unittest.TestCase):
    # device 2026-09-29: every lamp 'erase move' costs ~44 ms whatever its
    # length; tracing all 1748 points of one greeting took 77 s
    def test_straight_erase_is_one_move(self):
        cmds = monke._erase_cmds([[(x, 500) for x in range(100, 300)]])
        self.assertEqual(cmds, "erase down 100 500\nerase move 299 500\nerase up\n")

    def test_curve_erase_stays_within_two_px(self):
        import math
        arc = [(round(400 + 60 * math.cos(t / 50)), round(400 + 60 * math.sin(t / 50)))
               for t in range(0, 300)]
        lines = monke._erase_cmds([arc]).splitlines()
        kept = [tuple(map(int, l.split()[2:4])) for l in lines if "down" in l or "move" in l]
        self.assertLess(len(kept), len(arc) // 4)

        def dist(p, a, b):
            (px, py), (ax, ay), (bx, by) = p, a, b
            dx, dy = bx - ax, by - ay
            t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / ((dx * dx + dy * dy) or 1)))
            return math.hypot(px - ax - t * dx, py - ay - t * dy)
        self.assertLessEqual(max(min(dist(p, a, b) for a, b in zip(kept, kept[1:]))
                                 for p in arc), 2.01)

    def test_single_point_still_erased(self):
        self.assertEqual(monke._erase_cmds([[(10, 10)]]),
                         "erase down 10 10\nerase move 12 12\nerase up\n")


class GreetLayerTest(unittest.TestCase):
    # device 2026-09-29: stroke-tracing erase of one greeting took 37-95 s
    # and still left residue; the greeting now lives on its own layer
    def test_fade_drops_the_greeting_layer_without_erasing(self):
        import layers
        with (patch.object(monke, "SAFE_LAYER", True),
              patch.object(monke, "lamp") as lamp,
              patch.object(layers, "drop", return_value=True) as drop):
            monke.fade_greeting([[(1, 1), (5, 5)]], "test")
        drop.assert_called_once_with("test", layers.GREET)
        lamp.assert_not_called()

    def test_fade_without_layers_traces_strokes(self):
        with (patch.object(monke, "SAFE_LAYER", False),
              patch.object(monke, "erase_strokes") as erase):
            monke.fade_greeting([[(1, 1), (5, 5)]], "test")
        erase.assert_called_once()

    def test_greeting_inks_on_the_greeting_layer(self):
        import layers
        ses = unittest.mock.MagicMock(name="ses", sid="")
        ses.name = "nb"
        with (patch.object(monke, "marker"),
              patch.object(monke, "_session_context", return_value=("", [])),
              patch.object(monke, "tablet_settings", return_value={}),
              patch.object(monke, "cloud_chat", return_value=lambda m: "hello"),
              patch.object(monke, "_place", return_value=[[(10, 200), (20, 200)]]),
              patch.object(monke, "_ink", return_value=True) as ink):
            monke._greet({}, 3, 1800, "test", [], 0, ses, None, {})
        self.assertEqual(ink.call_args.kwargs.get("layer"), layers.GREET)


class QmlEvalLatencyTest(unittest.TestCase):
    # device 2026-09-29: 14 QML calls per ink at ~1.05 s each; the result
    # poll stepped 0.5 s on top of the eye's own timer
    def test_result_is_polled_finely(self):
        seen = {}

        def run(host, script, stdin=b"", timeout=30):
            seen["cmd"] = script
            return 0, b""
        import tablet
        with patch.object(tablet, "run", side_effect=run):
            monke.qml_eval("test", "1")
        self.assertIn("sleep 0.1;", seen["cmd"])
        self.assertIn("-lt 60 ]", seen["cmd"])      # same 6 s budget


class TabletSettingsTest(unittest.TestCase):
    # device 2026-09-30: one ssh timeout returned {} -> route fell back to
    # local (refused) then Gemini (106 s read) although the app said sonnet
    def test_unreachable_tablet_keeps_last_known_settings(self):
        import tablet
        import tempfile
        monke._SETTINGS_SEEN.clear()
        with (patch.object(monke, "_SETTINGS_FILE", Path(tempfile.mkdtemp()) / "s.json"),
              patch.object(tablet, "run",
                           side_effect=[(0, b'{"writer": "sonnet"}'), TimeoutError("t"),
                                        ConnectionError("gone"), (0, b"")])):
            self.assertEqual(monke.tablet_settings("t"), {"writer": "sonnet"})
            self.assertEqual(monke.tablet_settings("t"), {"writer": "sonnet"})
            self.assertEqual(monke.tablet_settings("t"), {"writer": "sonnet"})
            self.assertEqual(monke.tablet_settings("t"), {})   # app really unset

    def test_fresh_daemon_on_a_dropped_link_uses_settings_saved_on_disk(self):
        # device 2026-09-30 03:13: USB link reset as the daemon started; the
        # first settings read failed with nothing in memory -> {} -> the
        # greeting silently went local + Gemini although the app said sonnet
        import io
        import tablet
        import tempfile
        f = Path(tempfile.mkdtemp()) / "s.json"
        with patch.object(monke, "_SETTINGS_FILE", f):
            monke._SETTINGS_SEEN.clear()
            with patch.object(tablet, "run", return_value=(0, b'{"writer": "sonnet"}')):
                monke.tablet_settings("t")                  # earlier daemon run
            monke._SETTINGS_SEEN.clear()                    # daemon restarted
            out = io.StringIO()
            with (patch.object(tablet, "run", side_effect=TimeoutError("t")),
                  patch("sys.stdout", out)):
                self.assertEqual(monke.tablet_settings("t"), {"writer": "sonnet"})
            self.assertIn("unreachable", out.getvalue())    # said, not silent


class DoctorTest(unittest.TestCase):
    # one command that names the broken link and its fix (device 2026-09-30:
    # a USB reset + no daemon + a stopped session all looked like "no reply")
    def test_default_uses_configured_capture_host(self):
        import tempfile
        cfg = Path(tempfile.mkdtemp()) / "config.toml"
        cfg.write_text('[capture]\nendpoints = [{ name = "tailnet", host = "root@100.64.0.10", screen_url = "https://100.64.0.10:2001" }]\n')
        out = unittest.mock.MagicMock(returncode=0, stdout=b"")
        import link
        with (patch.object(monke, "REPO_CFG", cfg),
              patch.object(link, "reachable", return_value=True),
              patch.object(monke.subprocess, "run", return_value=out) as run,
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke, "qml_eval", return_value=None)):
            monke.doctor(print_=False)
        self.assertIn("root@100.64.0.10", run.call_args.args[0])

    def test_tablet_gone_gives_fails_with_fixes_not_a_crash(self):
        import subprocess as sp
        with (patch.object(monke.subprocess, "run", side_effect=sp.TimeoutExpired("ssh", 8)),
              patch.object(monke, "_shot", return_value=None),
              patch.object(monke, "qml_eval", return_value=None)):
            rows = monke.doctor("rm2", print_=False)
        self.assertTrue(rows)
        self.assertFalse(rows[0][1])                         # tablet link: FAIL
        self.assertTrue(all(isinstance(fix, str) and fix for _, ok, fix in rows if not ok))

    def test_ipv4_mapped_client_address_matches(self):
        out = unittest.mock.MagicMock(returncode=0, stdout=(
            b"eye=1\narmed=1\nstate=idle\npoints=10.11.99.6:7333\n"
            b"client=::ffff:10.11.99.6\nrss=230000\n"))
        with (patch.object(monke.subprocess, "run", return_value=out),
              patch.object(monke, "_shot", return_value=object()),
              patch.object(monke, "qml_eval", return_value="1"),
              patch.object(monke, "tablet_settings", return_value={"writer": "sonnet"})):
            rows = dict((n, ok) for n, ok, _ in monke.doctor("rm2", print_=False))
        self.assertTrue(rows["eye points at this laptop"])


class SamePageTest(unittest.TestCase):
    # device 2026-09-30 run 7: Navy swiped during turn 04's confirm; the reply
    # was placed by the OLD page and inked onto the new one
    def setUp(self):
        from PIL import Image, ImageDraw
        self.q = [[(200, 300), (400, 300)], [(200, 340), (400, 340)]]
        self.page = Image.new("RGB", (1404, 1872), "white")
        d = ImageDraw.Draw(self.page)
        for st in self.q:
            d.line(st[0] + st[1], fill="red", width=3)
        self.blank = Image.new("RGB", (1404, 1872), "white")

    def test_question_still_visible(self):
        with patch.object(monke, "_shot", return_value=self.page):
            self.assertTrue(monke._same_page(self.q))

    def test_highlighter_question_counts_as_ink(self):
        from PIL import Image, ImageDraw
        page = Image.new("RGB", (1404, 1872), "white")
        d = ImageDraw.Draw(page)
        for st in self.q:
            d.line(st[0] + st[1], fill=(255, 237, 0), width=8)
        with patch.object(monke, "_shot", return_value=page):
            self.assertTrue(monke._same_page(self.q))

    def test_page_turned_away(self):
        with patch.object(monke, "_shot", return_value=self.blank):
            self.assertFalse(monke._same_page(self.q))

    def test_no_frame_is_not_proof(self):
        with patch.object(monke, "_shot", return_value=None):
            self.assertFalse(monke._same_page(self.q))


class GreetRouteTest(unittest.TestCase):
    def test_greeting_uses_the_app_writer_not_local_config(self):
        # device 2026-09-29: app said writer=sonnet, greeting still went to
        # another model -> no greeting at all. The greeting follows the app's writer.
        ses = unittest.mock.MagicMock(name="ses", sid="")
        ses.name = "nb"
        cloud = unittest.mock.MagicMock(return_value="hello Navy")
        with (patch.object(monke, "marker"),
              patch.object(monke, "_session_context", return_value=("", [])),
              patch.object(monke, "tablet_settings", return_value={"writer": "sonnet"}),
              patch.dict(monke.CLAUDE_CFG, {"models": ["sonnet"]}),
              patch.object(monke, "cloud_chat", return_value=cloud) as cc,
              patch.object(monke, "_place", return_value=None)):
            monke._greet({}, 3, 1800, "test", [], 0, ses, None, {})
        self.assertEqual(cc.call_args.args[0], "sonnet")
        cloud.assert_called_once()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(__import__("tex").have_pdflatex(), "no pdflatex on this machine")
class SmallGlyphTest(unittest.TestCase):
    """run 7-9: '7 × 8' inked as a 1 px slash, '6.2038' as '6 2038' — at
    300 dpi the skeleton collapses × and '.' to 2-point strokes."""
    def small(self, src):
        from tex import tex_to_strokes
        st = tex_to_strokes(src)
        return st, [s for s in st if max(x for x, _ in s) - min(x for x, _ in s) < 3
                    and max(y for _, y in s) - min(y for _, y in s) < 3]

    def test_times_is_two_crossing_strokes(self):
        st, tiny = self.small(r"$7 \times 8$")
        self.assertEqual(tiny, [])
        # 7, ×, 8 left to right: × = the strokes between the 7 and the 8
        mid = [s for s in st if 150 < sum(x for x, _ in s) / len(s) < 190]
        self.assertGreaterEqual(len(mid), 2, mid)

    def test_decimal_point_is_a_visible_dot(self):
        st, tiny = self.small(r"$6.2$")
        self.assertEqual(tiny, [])
        self.assertEqual(len(st), 3, [len(s) for s in st])     # 6 . 2


class GreetingNotReadTest(unittest.TestCase):
    """run 10: 'Navy back.' / 'Monke awake.' were read into Navy's question —
    the greeting sits just above his line, inside the focus crop."""
    def test_greeting_blanked_in_the_vision_copy(self):
        import io
        from PIL import Image, ImageDraw
        shot = Image.new("RGB", (1404, 1872), "white")
        greet = [[(200, 300), (600, 300)]]
        ImageDraw.Draw(shot).line(greet[0], fill="black", width=4)
        png, raw = monke._page_context([[(200, 400), (600, 400)]], shot, hide=greet)
        img = Image.open(io.BytesIO(png)).convert("RGB")
        self.assertEqual(img.getpixel((400, 300)), (255, 255, 255))
        self.assertEqual(img.getpixel((400, 400)), (220, 0, 0))
        self.assertEqual(raw.getpixel((400, 300)), (0, 0, 0))   # placement still sees it
