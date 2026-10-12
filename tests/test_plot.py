"""Overlay needs the old graph on screen (run 2026-09-30 turn 06: the graph
was on the previous page -> a red curve with no axes on a blank page)."""
# pyright: reportMissingImports=false

import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import graph
import monke
import screen


def ses_with_graph():
    ses = types.SimpleNamespace(log_turn=lambda *a: None)
    ses.graph = graph.Graph.new("10*(1-exp(-x/2))", 0, 10, y=900)
    return ses


def run_overlay(ses, shot):
    inked = []
    with patch.object(monke, "_ink", lambda st, *a, **k: inked.append(st) or True):
        monke._plot({"text": "add y = 10*exp(-x/2)", "latex": ""}, ses, "rm2",
                    150, 1780, 0, shot)
    return [s for batch in inked for s in batch]


@unittest.skipUnless(__import__("tex").have_pdflatex(), "no pdflatex on this machine")
class OverlayTest(unittest.TestCase):
    def test_graph_on_screen_gets_only_the_new_curve(self):
        ses = ses_with_graph()
        page = Image.new("RGB", (screen.CANVAS_W, screen.CANVAS_H), "white")
        from capture import render_strokes
        drawn = render_strokes(graph.strokes(ses.graph))       # the old graph, inked
        page.paste(drawn.convert("RGB"), (0, 0), drawn.convert("L").point(lambda v: 255 - v))
        new_only = run_overlay(ses, page)
        self.assertEqual(len(ses.graph.curves), 2)
        full = graph.strokes(ses.graph)
        self.assertLess(len(new_only), len(full))

    def test_graph_off_screen_is_redrawn_whole(self):
        ses = ses_with_graph()
        blank = Image.new("RGB", (screen.CANVAS_W, screen.CANVAS_H), "white")
        st = run_overlay(ses, blank)
        self.assertEqual(len(ses.graph.curves), 2)
        self.assertGreaterEqual(len(st), len(graph.strokes(ses.graph)) - 2)   # axes + y1 + y2


    def test_question_ink_inside_the_old_box_is_not_the_graph(self):
        # run 12 (device): "add y = ..." written where the old graph sat on
        # the previous page counted as the graph -> lone curve again
        from PIL import ImageDraw
        ses = ses_with_graph()
        page = Image.new("RGB", (screen.CANVAS_W, screen.CANVAS_H), "white")
        pts = [p for s in graph.strokes(ses.graph) for p in s]
        x0, y0 = min(x for x, _ in pts), min(y for _, y in pts)
        ImageDraw.Draw(page).rectangle((x0 + 40, y0 + 20, x0 + 500, y0 + 70), fill="red")
        st = run_overlay(ses, page)
        self.assertGreaterEqual(len(st), len(graph.strokes(ses.graph)) - 2)


class GraphLabelTest(unittest.TestCase):
    # Navy 2026-09-30: each graph carries a label so a prompt can say which
    # graph to add to ("add y = ... to G1")
    def test_target_parsed_and_stripped(self):
        self.assertEqual(graph.target("add y = exp(-x) to G2"), ("G2", "add y = exp(-x)"))
        self.assertEqual(graph.target("add y = x, x from 0 to 3 on graph g1"), ("G1", "add y = x, x from 0 to 3"))
        self.assertEqual(graph.target("y = x^2"), (None, "y = x^2"))

    def test_label_is_the_figure_title(self):
        g = graph.Graph.new("x", 0, 1)
        g.label = "G3"
        self.assertIn("title={G3}", g.tex())

    @unittest.skipUnless(__import__("tex").have_pdflatex(), "no pdflatex on this machine")
    def test_add_goes_to_the_named_graph(self):
        ses = types.SimpleNamespace(log_turn=lambda *a: None)
        with patch.object(monke, "_ink", lambda st, *a, **k: True):
            monke._plot({"text": "y = x, x from 0 to 5", "latex": ""}, ses, "rm2", 150, 1780, 0, None)
            monke._plot({"text": "y = x^2, x from 0 to 5", "latex": ""}, ses, "rm2", 900, 1780, 0, None)
            monke._plot({"text": "add y = 2*x to G1", "latex": ""}, ses, "rm2", 150, 1780, 0, None)
        self.assertEqual(sorted(ses.graphs), ["G1", "G2"])
        self.assertEqual(ses.graphs["G1"].curves, ["x", "2*x"])
        self.assertEqual(ses.graphs["G2"].curves, ["x^2"])
        self.assertIn("G1: y1 = x, y2 = 2*x", monke.graphs_context(ses))


if __name__ == "__main__":
    unittest.main()
