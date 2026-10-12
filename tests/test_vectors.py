"""Vector hybrid: the page's own strokes (.rm) join the screenshot for
placement (run 14: a reply inked over a thin graph pixels missed).
Fixtures: page 49 of 'Test' after run 14 turn 06 (G1 + overlay), its .rm
and the screenshot taken right after."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import screen
import vectors

FIX = Path(__file__).parent / "fixtures"
VIEW = {"doc": "d", "name": "Test", "page": 48, "pages": 49, "pageId": "p",
        "scale": 1, "left": -352, "right": -1052, "top": 0}


class VectorTest(unittest.TestCase):
    def setUp(self):
        self.layers = vectors.parse((FIX / "g1-overlay.rm").read_bytes())
        self.shot = Image.open(FIX / "g1-overlay.png")

    def test_layers_by_name(self):
        self.assertEqual(sorted(self.layers), ["Layer 1", "Monke"])
        self.assertGreater(len(self.layers["Monke"]), 50)

    def test_mapping_lands_on_screen_ink(self):
        self.assertGreater(vectors.agree(self.layers, VIEW, screen.ink_mask(self.shot)), 0.9)
        off = dict(VIEW, right=-702)                    # wrong page width
        self.assertLess(vectors.agree(self.layers, off, screen.ink_mask(self.shot)), 0.5)

    def test_stroke_mask_unions_vector_ink(self):
        blank = Image.new("RGB", (screen.CANVAS_W, screen.CANVAS_H), "white")
        blank.info["vectors"] = lambda pix: vectors.mask(self.layers, VIEW)
        self.assertGreater(screen.ink_floor(blank), 800)    # graph known from vectors alone

    def test_context_names_the_hierarchy(self):
        c = vectors.context(VIEW, self.layers)
        self.assertIn("Notebook 'Test', page 49 of 49", c)
        self.assertIn("Monke's replies", c)

    def test_monke_shot_attaches_vectors_and_rejects_bad_mapping(self):
        import monke
        with (patch.object(vectors, "view", return_value=dict(VIEW, right=-702)),
              patch.object(vectors, "page", return_value=self.layers)):
            img = self.shot.copy()
            monke._attach_vectors(img, "rm2")
            self.assertIsNone(img.info["vectors"](screen.ink_mask(img)))   # mapping mismatch
        with (patch.object(vectors, "view", return_value=VIEW),
              patch.object(vectors, "page", return_value=self.layers)):
            img = self.shot.copy()
            monke._attach_vectors(img, "rm2")
            self.assertIsNotNone(img.info["vectors"](screen.ink_mask(img)))
            self.assertEqual(img.info["view"]["pageId"], "p")


class ViewGuardTest(unittest.TestCase):
    # the reply must land on the question's page (page id decides)
    V0 = dict(VIEW)

    def guard(self, views):
        import monke
        it = iter(views)
        monke._TURN["view"] = self.V0
        with (patch.object(vectors, "view", lambda host: next(it)),
              patch.object(monke, "_shot", side_effect=AssertionError("pixel test used")),
              patch("time.sleep", lambda s: None)):
            return monke._same_page([[(10, 10)]])

    def test_same_page_same_view_needs_no_screenshot(self):
        self.assertTrue(self.guard([self.V0]))

    def test_other_page_is_refused(self):
        self.assertFalse(self.guard([dict(self.V0, pageId="other")]))

    def test_scrolled_page_is_not_inked_and_does_not_wait(self):
        # no wait/hold (terminated by Navy); a scroll moves screen coordinates
        self.assertFalse(self.guard([dict(self.V0, top=-900)]))


class NotebookContextTest(unittest.TestCase):
    # notebook -> page -> screen: the reader knows what earlier pages hold
    def test_turns_are_tagged_by_page_and_summarised(self):
        import tempfile
        import types
        import monke
        ses = types.SimpleNamespace(dir=Path(tempfile.mkdtemp()), name="t", pages={},
                                    save=lambda: None)
        for page, q in ((47, "explain thevenin theorem"), (47, "find Vout"), (48, "sensor -> amp -> ADC"),
                        (49, "plot y = x")):
            monke._TURN["view"] = dict(VIEW, page=page - 1)
            monke.NotebookSession.log_turn(ses, "Navy", q)
        monke._TURN["view"] = dict(VIEW, page=48)
        c = monke.notebook_context(ses)
        self.assertIn("p47: explain thevenin theorem; find Vout", c)
        self.assertIn("p48: sensor -> amp -> ADC", c)
        self.assertNotIn("p49", c)                      # the current page is the screen's job
        self.assertIn("(p47,", (ses.dir / "transcript.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()


class PageLogTest(unittest.TestCase):
    """run 10: 'summarize this page' on a scrolled page answered 'MOSFET…'
    — the writer never saw the page. It gets this page's turns now."""
    def test_only_this_page_and_drawings_collapsed(self):
        import tempfile
        import types
        import monke
        d = Path(tempfile.mkdtemp())
        (d / "transcript.md").write_text(
            "---\nnotebook: t\n---\n"
            "\n## Navy (p54, 19:27)\nwhat is 13 log 3\n"
            "\n## Navy (p55, 19:31)\n#P BMS circuit for a keyboard\n"
            "\n## Monke (p55, 19:32)\nKeyboard BMS.\n\n" r"$$\begin{circuitikz}\draw (0,0);\end{circuitikz}$$" "\n"
            "\nTP4056 charges.\n", encoding="utf-8")
        monke._TURN["view"] = dict(VIEW, page=54)
        log = monke.page_log(types.SimpleNamespace(dir=d))
        self.assertIn("BMS circuit", log)
        self.assertIn("TP4056 charges", log)
        self.assertIn("[drawing]", log)
        self.assertNotIn("13 log 3", log)
        self.assertNotIn("circuitikz", log)


class ScrollIsNotPageTurnTest(unittest.TestCase):
    """run 9-10: vertical scrolls logged 'page turned' — floor and echo mask
    reset on the same page. The page id decides."""
    def check(self, v, last):
        import monke
        with patch.object(vectors, "view", return_value=v):
            return monke._page_turned("rm2", last)

    def test_same_page_id_is_a_scroll(self):
        self.assertEqual(self.check(dict(VIEW, pageId="p"), "p"), (False, "p"))

    def test_new_page_id_is_a_turn(self):
        self.assertEqual(self.check(dict(VIEW, pageId="q"), "p"), (True, "q"))

    def test_unreadable_view_counts_as_a_turn(self):
        self.assertEqual(self.check(None, "p"), (True, None))
