"""render_plan.normalize: a model reply -> ordered, coloured, serializable
Blocks (slice 1 of the tool-calling middleware), proven on the canonical
series-RLC reply (tests/fixtures/ac_rlc_reply.md)."""
# pyright: reportMissingImports=false

import json
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "host"))

import place
import render_plan as rp
import tex

FIXTURE = ROOT / "tests" / "fixtures" / "ac_rlc_reply.md"
PREVIEW = ROOT / "build" / "ac_rlc_preview.png"


def _overlap(a, b):
    return not (a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1])


class NormalizeTest(unittest.TestCase):
    def test_kinds_in_reply_order(self):
        reply = ("Ohm's law.\n$$V = IR$$\n"
                 "$$\\begin{circuitikz}\\draw (0,0) to[R] (2,0);\\end{circuitikz}$$\n"
                 "$$\\begin{tikzpicture}\\begin{axis}\\addplot{x};\\end{axis}\\end{tikzpicture}$$\n"
                 "<svg viewBox=\"0 0 10 10\"><rect x=\"1\" y=\"1\" width=\"5\" height=\"5\"/></svg>\n"
                 "Done.")
        self.assertEqual([b.kind for b in rp.normalize(reply)],
                         ["text", "latex", "circuit", "graph", "diagram", "text"])

    def test_tikz_without_axes_is_a_diagram(self):
        b, = rp.normalize("$$\\begin{tikzpicture}\\node[draw] (a) {Start};\\end{tikzpicture}$$")
        self.assertEqual((b.kind, b.role, b.color), ("diagram", "diagram", 0))

    def test_same_input_same_output_and_serializable(self):
        text = FIXTURE.read_text(encoding="utf-8")
        a, b = rp.normalize(text), rp.normalize(text)
        self.assertEqual(a, b)
        dumped = json.dumps([x.to_dict() for x in a])
        self.assertEqual(json.loads(dumped)[0]["kind"], "text")

    def test_directive_lines_are_not_blocks(self):
        blocks = rp.normalize("@place below N1\nHello.")
        self.assertEqual([(b.kind, b.src) for b in blocks], [("text", "Hello.")])

    def test_empty_reply_is_no_blocks(self):
        self.assertEqual(rp.normalize(""), [])
        self.assertEqual(rp.normalize("   \n\n"), [])


class MalformedTest(unittest.TestCase):
    def test_unclosed_math_is_skipped_with_a_reason_never_inked_as_text(self):
        blocks, skipped = rp.parse("Before.\n$$\\frac{1}{2")
        self.assertEqual([b.src for b in blocks], ["Before."])
        self.assertEqual(len(skipped), 1)
        self.assertIn("unclosed $$", skipped[0]["reason"])

    def test_unclosed_svg_is_skipped(self):
        blocks, skipped = rp.parse("See <svg viewBox='0 0 4 4'><line x1='0'")
        self.assertTrue(all("<svg" not in b.src for b in blocks))
        self.assertIn("unclosed <svg", skipped[0]["reason"])

    def test_bad_json_is_skipped_good_json_steps_become_a_diagram(self):
        blocks, skipped = rp.parse("```json\n{\"steps\": [\"Read\", \"Sort\"\n```")
        self.assertEqual(blocks, [])
        self.assertTrue(skipped[0]["reason"].startswith("json"))
        b, = rp.normalize('```json\n{"title": "Sort", "steps": ["Read", "Sort", "Print"]}\n```')
        self.assertEqual((b.kind, b.role), ("diagram", "diagram"))
        self.assertIn("<svg", b.src)
        self.assertIn(">Sort<", b.src)

    def test_graph_without_curves_keeps_source_and_says_why(self):
        b, = rp.normalize("$$\\begin{tikzpicture}\\begin{axis}\\end{axis}\\end{tikzpicture}$$")
        self.assertEqual((b.kind, b.curves), ("graph", ()))
        self.assertIn("no curves", b.reason)

    def test_fenced_latex_and_bare_circuit_are_tolerated(self):
        kinds = [b.kind for b in rp.normalize(
            "```latex\n\\begin{circuitikz}\\draw (0,0) to[R] (2,0);\\end{circuitikz}\n```\n"
            "Also \\begin{circuitikz}\\draw (0,0) to[C] (2,0);\\end{circuitikz}")]
        self.assertEqual(kinds, ["circuit", "text", "circuit"])


class ColorRoleTest(unittest.TestCase):
    def test_role_colors(self):
        reply = ("Text.\n$$V = IR$$\n$$\\begin{circuitikz}\\draw (0,0) to[R] (2,0);"
                 "\\end{circuitikz}$$\n$$\\boxed{V = 5}$$")
        self.assertEqual([(b.kind, b.role, b.color) for b in rp.normalize(reply)],
                         [("text", "prose", 0), ("latex", "equation", 0),
                          ("circuit", "circuit", 0), ("latex", "answer", 7)])

    def test_curves_cycle_blue_red_gray_with_distinct_dashes(self):
        body = "".join(f"\\addplot[black]{{x+{i}}};" for i in range(4))
        b, = rp.normalize(f"$$\\begin{{tikzpicture}}\\begin{{axis}}{body}"
                          "\\end{axis}\\end{tikzpicture}$$")
        self.assertEqual([c for c, _ in b.curves], [6, 7, 1, 6])
        dashes = [d for _, d in b.curves]
        self.assertEqual(len(set(dashes[:3])), 3)          # e-ink: dash tells them apart
        self.assertNotIn("dotted", " ".join(dashes))       # dots do not survive tracing
        self.assertNotIn("black", b.src.split("\\begin{axis}")[1].split("\\end{axis}")[0]
                         .replace("!black", ""))
        self.assertEqual(b.src.count("\\addplot["), 4)

    def test_restyle_is_idempotent(self):
        src = "\\begin{tikzpicture}\\begin{axis}\\addplot[red,thick]{x};\\end{axis}\\end{tikzpicture}"
        once = rp.restyle(src)
        self.assertEqual(rp.restyle(once), once)

    def test_marker_plots_are_not_curves(self):
        b, = rp.normalize("$$\\begin{tikzpicture}\\begin{axis}\\addplot{x};"
                          "\\addplot[only marks, mark=*] coordinates {(1,1)};"
                          "\\end{axis}\\end{tikzpicture}$$")
        self.assertEqual(len(b.curves), 1)

    def test_answer_latex_is_typeset_red(self):
        self.assertIn("\\color{red}", rp.tex_source("\\boxed{V = 5}"))
        self.assertNotIn("\\color", rp.tex_source("V = 5"))

    def test_gray_key_inks_with_the_gray_pen(self):
        rgb = np.full((40, 60, 3), 255, np.uint8)
        rgb[10, 5:25] = (0, 0, 0)
        rgb[20, 5:25] = (0, 153, 0)          # xcolor green!60!black = the gray key
        rgb[30, 5:25] = (0, 0, 255)
        m = tex.pen_masks(rgb)
        self.assertEqual(sorted(m), [0, 1, 6])
        self.assertTrue(m[1][20, 10] and not m[0][20, 10])
        self.assertEqual(rp.TEX_COLOR[1], tex.GRAY_KEY)


class PackTest(unittest.TestCase):
    def test_boxes_never_overlap_each_other_or_ink_and_stay_on_the_page(self):
        mask = np.zeros((place.H, place.W), bool)
        mask[150:300, 200:900] = True                     # Navy's question
        occ = place.occupancy(mask)
        sizes = [(900, 120), (560, 380), (560, 380), (900, 200)]
        spots = place.pack(sizes, occ, floor=300, pair=(False, False, True, False))
        boxes = [(s.x, s.y, s.x + round(w * s.k), s.y + round(h * s.k))
                 for s, (w, h) in zip(spots, sizes) if s]
        self.assertEqual(len(boxes), 4)
        for i, a in enumerate(boxes):
            self.assertTrue(occ.free(a), a)
            self.assertTrue(120 <= a[0] and 100 <= a[1] and a[2] <= place.W and a[3] <= place.H)
            for b in boxes[i + 1:]:
                self.assertFalse(_overlap(a, b), (a, b))
        self.assertEqual(spots[2].y, spots[1].y)          # second graph beside the first

    def test_no_room_is_none_not_an_overlap(self):
        occ = place.occupancy(np.ones((place.H, place.W), bool))
        self.assertEqual(place.pack([(300, 200)], occ), [None])


class AcFixtureTest(unittest.TestCase):
    """The canonical AC problem: series RLC, Vs = 10 V rms, w = 1000 rad/s."""

    def setUp(self):
        self.blocks = rp.normalize(FIXTURE.read_text(encoding="utf-8"))

    def test_kinds_order_roles_colors(self):
        self.assertEqual([(b.kind, b.role, b.color) for b in self.blocks], [
            ("text", "prose", 0), ("circuit", "circuit", 0), ("text", "prose", 0),
            ("latex", "equation", 0), ("latex", "equation", 0),
            ("graph", "graph", 0), ("graph", "graph", 0),
            ("text", "answer", 7), ("latex", "answer", 7)])

    def test_two_graphs_get_distinct_labels_and_curve_styles(self):
        g1, g2 = [b for b in self.blocks if b.kind == "graph"]
        self.assertEqual((g1.label, g2.label), ("G1", "G2"))
        self.assertEqual(len(g1.curves), 2)               # v_s and i on one axes
        self.assertNotEqual(g1.curves[0], g1.curves[1])   # the model wrote blue twice
        self.assertEqual([c for c, _ in g1.curves], [6, 7])
        self.assertEqual(len(g2.curves), 1)               # the w0 dot is a marker
        self.assertIn("title={G1}", g1.src)
        labels = [b.label for b in rp.normalize(FIXTURE.read_text(encoding="utf-8"),
                                                taken=("G1",)) if b.kind == "graph"]
        self.assertEqual(labels, ["G2", "G3"])            # session graphs are never reused

    @unittest.skipUnless(tex.have_pdflatex(), "pdflatex not installed")
    def test_render_place_and_preview(self):
        mask = np.zeros((place.H, place.W), bool)
        mask[130:250, 150:1000] = True                    # the problem Navy wrote
        placed, overflow = rp.layout(self.blocks, mask, floor=250)
        kinds = {p["block"].kind for p in placed}
        self.assertTrue({"circuit", "graph"} <= kinds, (kinds, overflow))
        occ = place.occupancy(mask)
        boxes = [p["box"] for p in placed]
        for i, a in enumerate(boxes):
            self.assertTrue(occ.free(a), (placed[i]["block"].kind, a))
            for b in boxes[i + 1:]:
                self.assertFalse(_overlap(a, b), (a, b))
        graphs = rp.session_entries(placed)
        self.assertEqual(sorted(graphs), ["G1", "G2"])
        self.assertFalse(_overlap(graphs["G1"]["box"], graphs["G2"]["box"]))
        circuit = next(p["box"] for p in placed if p["block"].kind == "circuit")
        self.assertFalse(any(_overlap(circuit, g["box"]) for g in graphs.values()))
        colors = {getattr(s, "color", 0) for p in placed if p["block"].label == "G1"
                  for s in p["strokes"]}
        self.assertTrue({6, 7} <= colors, colors)         # both curve pens survive render
        rp.preview(placed, PREVIEW, mask)
        self.assertTrue(PREVIEW.exists())


if __name__ == "__main__":
    unittest.main()
