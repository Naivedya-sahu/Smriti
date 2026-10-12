"""#P prompts (Navy 2026-09-30): writing that starts with #P is an
instruction to Monke. The marker is stripped before answering, and once the
reply is inked the prompt's own strokes are deleted natively (selection
tool + addSelectionRect + deleteSelectedItems(false), device-probed), only
when exactly the prompt's strokes are selected."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness
import monke
import vectors

VIEW = {"pageId": "p", "scale": 1, "right": -1052, "top": 0}
STROKES = [[(300, 300), (500, 300)], [(300, 330), (500, 330)], [(300, 360), (520, 360)]]


class MarkerTest(unittest.TestCase):
    def r(self, text):
        return harness.parse_reading('{"kind": "text", "text": ' + __import__("json").dumps(text) + "}")

    def test_marker_detected_and_stripped(self):
        for t in ("#P add y = x to G1", "# P add y = x to G1", "#p: add y = x to G1"):
            ok, r = harness.strip_prompt(self.r(t))
            self.assertTrue(ok, t)
            self.assertEqual(r["text"], "add y = x to G1")

    def test_plain_writing_is_not_a_prompt(self):
        ok, r = harness.strip_prompt(self.r("Pressure #P is high"))
        self.assertFalse(ok)
        self.assertEqual(r["text"], "Pressure #P is high")


def rm(strokes):
    """screen strokes -> page-file strokes (x + right)"""
    return [[(x + VIEW["right"], y) for x, y in s] for s in strokes]


class ErasePromptTest(unittest.TestCase):
    def run_erase(self, layers, count="3", tool="primary"):
        sent = []
        reads = iter([count, "0"])                 # selection, then proof after delete

        def qml(host, js, timeout=6):
            sent.append(js)
            if "activeTool='selection'" in js:
                return tool
            if js.startswith("String(sceneController.selectionItemCount)"):
                return next(reads)
            return "1"
        with (patch.object(vectors, "view", return_value=VIEW),
              patch.object(vectors, "page", return_value=layers),
              patch.object(monke, "_shot", return_value=None),   # the offset needs no screenshot
              patch.object(monke, "qml_eval", qml),
              patch("time.sleep", lambda s: None)):
            ok = monke.erase_prompt(STROKES, "rm2")
        return ok, sent

    def test_vectors_match_and_count_match_deletes(self):
        ok, sent = self.run_erase({"Layer 1": rm(STROKES), "Monke": rm([[(310, 320), (330, 320)]])})
        self.assertTrue(ok)                                    # Monke's ink is not Navy's selection
        self.assertIn("addSelectionRect(Qt.rect(-762, 290, 240, 80), 0)", " ".join(sent))
        self.assertTrue(any("deleteSelectedItems(false)" in js for js in sent))
        self.assertIn('activeTool = "primary"', sent[-1])

    def test_neighbour_in_the_box_stops_before_selecting(self):
        ok, sent = self.run_erase({"Layer 1": rm(STROKES + [[(400, 345), (420, 345)]])})
        self.assertFalse(ok)
        self.assertEqual(sent, [])                              # never touched the selection

    def test_live_count_mismatch_keeps_everything(self):
        ok, sent = self.run_erase({"Layer 1": rm(STROKES)}, count="4")
        self.assertFalse(ok)
        self.assertTrue(any("clearSelectedItems" in js for js in sent))
        self.assertFalse(any("deleteSelectedItems" in js for js in sent))

    def test_prompt_not_flushed_yet_waits_then_gives_up(self):
        ok, sent = self.run_erase({"Layer 1": rm(STROKES[:1])})
        self.assertFalse(ok)
        self.assertEqual(sent, [])

    def test_navy_selection_active_is_left_alone(self):
        ok, sent = self.run_erase({"Layer 1": rm(STROKES)}, tool="busy")
        self.assertFalse(ok)
        self.assertEqual(len(sent), 1)

    def test_offset_comes_from_the_prompts_own_strokes(self):
        # sim hash_p 2026-10-04: scrolled 600 px, the page file without the
        # fresh reply — matching page to screenshot found nothing, #P kept.
        # The prompt's strokes are in the page file: pairing them is exact.
        page = [[(x - 1052, y + 600) for x, y in s] for s in STROKES]
        ok, sent = self.run_erase({"Layer 1": page + [[(-900, 100), (-700, 180)]], "Monke": []})
        self.assertTrue(ok)
        self.assertIn("addSelectionRect(Qt.rect(-762, 890, 240, 80), 0)", " ".join(sent))

    def test_unreadable_view_is_not_erased(self):
        with (patch.object(vectors, "view", return_value=None),
              patch.object(monke, "qml_eval", side_effect=AssertionError("qml"))):
            self.assertFalse(monke.erase_prompt(STROKES, "rm2"))


if __name__ == "__main__":
    unittest.main()


class EraseAfterInkTest(unittest.TestCase):
    """run 7 (2026-09-30): a page turn read floor 1849, the reply inked above
    it, the floor never moved -> the #P was kept. Signal = reply inked."""
    def after(self, inked):
        monke._TURN.update(prompt=STROKES, inked=inked)
        with patch.object(monke, "erase_prompt") as er:
            monke._erase_answered_prompt("rm2")
        return er

    def test_inked_reply_erases_even_when_floor_did_not_move(self):
        self.after(True).assert_called_once_with(STROKES, "rm2")

    def test_reply_not_inked_keeps_the_prompt(self):
        self.after(False).assert_not_called()
