"""Bug #4: 'Can you represent the algorithm using graphs' was answered with
prose telling itself to draw. A flow/algorithm/block/phasor request is the
`diagram` route (not the math `graph` route), and a prose-only result for
it is replaced by a deterministic boxes-and-arrows fallback."""
# pyright: reportMissingImports=false

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import protocol
import render_plan as rp
from svg import svg_to_strokes

BUG4 = "Can you represent the algorithm using graphs"
PROSE = ("To represent the algorithm as a graph, I would draw a node for each step "
         "and connect them with arrows. 1. Read the input array. 2. Compare adjacent "
         "pairs. 3. Swap when out of order. 4. Repeat until sorted.")


class RouteTest(unittest.TestCase):
    def test_algorithm_as_graphs_is_a_diagram(self):
        self.assertEqual(rp.route(BUG4), "diagram")
        self.assertEqual(rp.route({"kind": "graph", "intent": "plot", "text": BUG4}), "diagram")

    def test_flow_block_phasor_requests_are_diagrams(self):
        for ask in ("Draw a flowchart for binary search", "Draw the block diagram of the PLL",
                    "Sketch the phasor diagram", "Show the steps of the process as a chart"):
            self.assertEqual(rp.route(ask), "diagram", ask)

    def test_function_and_data_plots_stay_graph(self):
        for ask in ("Plot y = sin(x), x from 0 to 5", "Plot |I| vs omega and mark resonance",
                    "Graph of exp(-x)", "Plot v_s(t) and i(t) on one graph",
                    "Plot the step response"):
            self.assertEqual(rp.route(ask), "graph", ask)

    def test_questions_and_circuits_are_not_diagrams(self):
        self.assertEqual(rp.route("Explain this diagram"), "text")
        self.assertEqual(rp.route("What is the time complexity of the algorithm?"), "text")
        self.assertEqual(rp.route("Draw the circuit"), "circuit")
        self.assertEqual(rp.route(""), "text")


class VisualGateTest(unittest.TestCase):
    def test_prose_only_reply_is_replaced_by_the_fallback_diagram(self):
        out, why = rp.enforce_visual(BUG4, PROSE)
        self.assertIn("prose-only", why)
        blocks = rp.normalize(out)
        self.assertEqual([b.kind for b in blocks], ["diagram"])
        svg = blocks[0].src
        labels = re.findall(r">([^<>]+)</text>", svg)
        self.assertEqual(labels[1:], ["Read the input array", "Compare adjacent pairs",
                                      "Swap when out of order", "Repeat until sorted"])
        self.assertEqual(svg.count("<rect"), 4)
        self.assertEqual(svg.count("<polygon"), 3)        # one arrowhead between steps

    def test_no_steps_falls_back_to_input_process_output(self):
        out, _ = rp.enforce_visual(BUG4, "I would draw it with boxes and arrows.")
        labels = re.findall(r">([^<>]+)</text>", out)
        self.assertEqual(labels[1:], ["Input", "Process", "Output"])

    def test_empty_reply_still_yields_a_visual(self):
        out, why = rp.enforce_visual(BUG4, "")
        self.assertIn("<svg", out)
        self.assertTrue(why)

    def test_visual_reply_passes_untouched(self):
        for reply in ("Here.\n<svg viewBox='0 0 4 4'><line x1='0' y1='0' x2='4' y2='4'/></svg>",
                      "$$\\begin{tikzpicture}\\node[draw]{A};\\end{tikzpicture}$$"):
            self.assertEqual(rp.enforce_visual(BUG4, reply), (reply, ""))

    def test_text_request_is_never_gated(self):
        self.assertEqual(rp.enforce_visual("What is a sort?", PROSE), (PROSE, ""))

    def test_fallback_is_bounded_and_deterministic(self):
        many = " ".join(f"{i}. step number {i} with a rather long description here"
                        for i in range(1, 21))
        a, _ = rp.enforce_visual(BUG4, many)
        self.assertEqual(a, rp.enforce_visual(BUG4, many)[0])
        labels = re.findall(r">([^<>]+)</text>", a)
        self.assertLessEqual(len(labels) - 1, rp.MAX_STEPS)
        self.assertTrue(all(len(t) <= rp.MAX_LABEL for t in labels[1:]))
        self.assertNotRegex(a, r"&(?!amp;)|[^\x00-\x7f]")

    def test_fallback_inks_without_latex(self):
        out, _ = rp.enforce_visual(BUG4, PROSE)
        strokes = svg_to_strokes(out, x=100, y=0, max_w=1200)
        self.assertGreater(len(strokes), 20)
        xs = [x for s in strokes for x, _ in s]
        self.assertLessEqual(max(xs), 1300)               # fits the page width
        self.assertGreater(max(xs) - min(xs), 900)        # not blown up past it either


class DescribeDiagramTest(unittest.TestCase):
    def test_tikz_flowchart_region_is_a_diagram_not_math(self):
        kind, _ = protocol.describe_reply(
            "Flow.\n$$\\begin{tikzpicture}\\node[draw] (a) {Start};\\end{tikzpicture}$$")
        self.assertEqual(kind, "diagram")


if __name__ == "__main__":
    unittest.main()
