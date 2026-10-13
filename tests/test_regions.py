"""host/regions.py: semantic page regions (R ids) for the model context and
deictic resolution ('the circuit in this area', 'the graph above', 'R2')."""
# pyright: reportMissingImports=false

import math
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import regions as rg


# ---- synthetic ink (page coordinates) ------------------------------------

def seg(x0, y0, x1, y1, n=10):
    return [(x0 + (x1 - x0) * i / (n - 1), y0 + (y1 - y0) * i / (n - 1)) for i in range(n)]


def zigzag(x0, x1, y, amp=15, teeth=6):
    pts, step = [], (x1 - x0) / (2 * teeth)
    for i in range(2 * teeth + 1):
        pts.append((x0 + i * step, y + (0 if i in (0, 2 * teeth) else (amp if i % 2 else -amp))))
    return pts


def circuit(x, y, w=400, h=260):
    """Battery + resistor loop: wires, a zigzag, two plates."""
    return [seg(x, y, x + 0.35 * w, y), zigzag(x + 0.35 * w, x + 0.65 * w, y),
            seg(x + 0.65 * w, y, x + w, y), seg(x + w, y, x + w, y + h),
            seg(x + w, y + h, x, y + h), seg(x, y + h, x, y + 0.6 * h),
            seg(x - 25, y + 0.6 * h, x + 25, y + 0.6 * h), seg(x - 12, y + 0.5 * h, x + 12, y + 0.5 * h),
            seg(x, y + 0.5 * h, x, y)]


def graph(x, y, w=420, h=300):
    """Axes (L at bottom-left), a smooth curve, a few ticks."""
    curve = [(x + 20 + t, y + h / 2 - 0.4 * h * math.sin(t / (w - 40) * 2 * math.pi))
             for t in range(0, int(w - 40), 10)]
    ticks = [seg(x + k, y + h - 8, x + k, y + h + 8, 3) for k in (100, 200, 300)]
    return [seg(x, y + h, x + w, y + h), seg(x, y, x, y + h), curve] + ticks


def glyph(x, y, s=24):
    return [(x + s * i / 8, y + s * (0.5 + 0.5 * math.sin(i * 1.3))) for i in range(9)]


def text(x, y, chars=14):
    return [glyph(x + 34 * i, y) for i in range(chars)]


def equation(x, y):
    """a = b + c with an equals sign (two short parallel bars)."""
    return [glyph(x, y), seg(x + 40, y + 8, x + 64, y + 8, 4), seg(x + 40, y + 18, x + 64, y + 18, 4),
            glyph(x + 80, y), seg(x + 118, y + 12, x + 138, y + 12, 4), seg(x + 128, y + 2, x + 128, y + 22, 4),
            glyph(x + 150, y)]


def box_of(strokes):
    xs = [p[0] for s in strokes for p in s]
    ys = [p[1] for s in strokes for p in s]
    return (min(xs), min(ys), max(xs) + 1, max(ys) + 1)


def by_kind(regions, kind, owner=None):
    return [r for r in regions if r.kind == kind and (owner is None or r.owner == owner)]


# ---- fixture pages -------------------------------------------------------

CIRC = circuit(120, 300)                    # 120..520 x 300..560
GRAPH = graph(800, 280)                     # 800..1220 x 280..588
NOTE = text(120, 800)                       # a line of prose
PROMPT_UNDER_CIRCUIT = text(140, 640, 10)   # written right below the circuit


class Build(unittest.TestCase):
    def setUp(self):
        self.regs = rg.build(CIRC + GRAPH + NOTE, [])

    def test_three_regions_with_kinds(self):
        kinds = sorted(r.kind for r in self.regs)
        self.assertEqual(kinds, ["circuit", "graph", "text"])

    def test_ids_are_R_numbers_in_reading_order(self):
        self.assertEqual(sorted(r.id for r in self.regs), ["R1", "R2", "R3"])
        ordered = sorted(self.regs, key=lambda r: (r.bbox[1], r.bbox[0]))
        self.assertEqual([r.id for r in ordered], ["R1", "R2", "R3"])

    def test_bbox_is_page_coords_and_counts(self):
        c = by_kind(self.regs, "circuit")[0]
        self.assertEqual(c.owner, "navy")
        self.assertEqual(c.stroke_count, len(CIRC))
        x0, y0, x1, y1 = c.bbox
        self.assertLessEqual(x0, 120 - 25)
        self.assertLessEqual(y0, 300 - 15)
        self.assertGreaterEqual(x1, 520)
        self.assertGreaterEqual(y1, 560)

    def test_negative_and_scrolled_page_coords_kept(self):
        regs = rg.build(circuit(-600, 2400), [])
        self.assertEqual(len(regs), 1)
        self.assertLess(regs[0].bbox[0], 0)
        self.assertGreater(regs[0].bbox[1], 1872)

    def test_math_line(self):
        regs = rg.build(equation(200, 1000), [])
        self.assertEqual([r.kind for r in regs], ["math"])

    def test_diagram_closed_shapes(self):
        def square(x, y, s=120):
            return [(x, y), (x + s, y), (x + s, y + s), (x, y + s), (x, y + 2)]
        circle = [(600 + 80 * math.cos(t / 20 * 2 * math.pi), 400 + 80 * math.sin(t / 20 * 2 * math.pi))
                  for t in range(21)]
        regs = rg.build([square(400, 300), circle, seg(520, 360, 520, 360, 2)], [], gap=200)
        self.assertEqual([r.kind for r in regs], ["diagram"])

    def test_monke_regions_owned_by_monke(self):
        regs = rg.build(CIRC, [{"box": (800, 280, 1220, 588), "type": "graph", "id": "G1",
                                "text": "y = sin(x)", "turn": 2},
                               {"box": (100, 1300, 900, 1400)}])
        mk = [r for r in regs if r.owner == "monke"]
        self.assertEqual(sorted(r.kind for r in mk), ["graph", "monke_reply"])
        g = by_kind(regs, "graph")[0]
        self.assertEqual(g.label, "G1")
        self.assertEqual(g.desc, "y = sin(x)")

    def test_exclude_box_drops_focus_strokes(self):
        regs = rg.build(CIRC + PROMPT_UNDER_CIRCUIT, [], exclude_box=box_of(PROMPT_UNDER_CIRCUIT))
        self.assertEqual([r.kind for r in regs], ["circuit"])

    def test_deterministic(self):
        again = rg.build(CIRC + GRAPH + NOTE, [])
        self.assertEqual(again, self.regs)

    def test_navy_strokes_filters_monke_layers(self):
        layers = {"Layer 1": CIRC, "Monke": GRAPH, "Monke-greet": NOTE}
        self.assertEqual(rg.navy_strokes(layers), CIRC)

    def test_build_2000_strokes_fast(self):
        strokes = [glyph(40 + 26 * (i % 50), 60 + 45 * (i // 50)) for i in range(2000)]
        rg.build(strokes[:50], [])                     # warm imports
        t0 = time.perf_counter()
        regs = rg.build(strokes, [])
        dt = time.perf_counter() - t0
        print(f"\n[timing] build(2000 strokes) = {dt * 1000:.1f} ms, {len(regs)} regions")
        self.assertLess(dt, 0.2)
        self.assertGreaterEqual(len(regs), 1)


class StableIds(unittest.TestCase):
    def test_ids_stable_when_unrelated_region_added(self):
        first = rg.build(CIRC + GRAPH, [], turn=1)
        ids = {r.kind: r.id for r in first}
        second = rg.build(text(100, 40) + CIRC + GRAPH, [], prior=first, turn=2)   # new ink ABOVE both
        again = {r.kind: r.id for r in second}
        self.assertEqual(again["circuit"], ids["circuit"])
        self.assertEqual(again["graph"], ids["graph"])
        self.assertEqual(again["text"], "R3")
        new = by_kind(second, "text")[0]
        self.assertEqual((new.created, new.updated), (2, 2))
        old = by_kind(second, "circuit")[0]
        self.assertEqual((old.created, old.updated), (1, 1))

    def test_id_kept_when_region_grows(self):
        first = rg.build(CIRC, [], turn=1)
        grown = rg.build(CIRC + [seg(520, 430, 600, 430)], [], prior=first, turn=3)
        self.assertEqual(grown[0].id, first[0].id)
        self.assertEqual((grown[0].created, grown[0].updated), (1, 3))

    def test_model_kind_survives(self):
        first = rg.build(NOTE, [])
        hinted = rg.hint(first, first[0].id, kind="math", desc="v = u + at")
        again = rg.build(NOTE, [], prior=hinted)
        self.assertEqual((again[0].kind, again[0].desc), ("math", "v = u + at"))

    def test_removed_region_id_not_reused(self):
        first = rg.build(CIRC + GRAPH, [])
        only_graph = rg.build(GRAPH, [], prior=first)
        later = rg.build(GRAPH + NOTE, [], prior=only_graph)
        gid = by_kind(first, "graph")[0].id
        self.assertEqual(by_kind(later, "graph")[0].id, gid)
        self.assertNotIn(by_kind(later, "text")[0].id, {r.id for r in first})


class Resolve(unittest.TestCase):
    def test_circuit_in_this_area_below_circuit(self):
        regs = rg.build(CIRC + GRAPH + NOTE, [])
        rid, conf, why = rg.resolve("redraw the circuit in this area", regs,
                                    last_prompt_bbox=box_of(PROMPT_UNDER_CIRCUIT))
        self.assertEqual(rid, by_kind(regs, "circuit")[0].id, why)
        self.assertGreaterEqual(conf, 0.7)

    def test_two_circuits_ambiguous(self):
        regs = rg.build(circuit(100, 300) + circuit(880, 300), [])
        prompt = (520, 700, 880, 740)                   # centred under both
        rid, conf, why = rg.resolve("solve this circuit", regs, last_prompt_bbox=prompt)
        self.assertIsNone(rid, why)
        self.assertIn("ambiguous", why)

    def test_two_circuits_left_one(self):
        regs = rg.build(circuit(100, 300) + circuit(880, 300), [])
        rid, _, why = rg.resolve("the left one", regs, last_prompt_bbox=(520, 700, 880, 740))
        self.assertEqual(rid, min(regs, key=lambda r: r.bbox[0]).id, why)

    def test_graph_above_not_below(self):
        up, down = graph(300, 150), graph(300, 1300)
        regs = rg.build(up + down, [])
        rid, conf, why = rg.resolve("add y = x to the graph above", regs,
                                    last_prompt_bbox=(300, 900, 900, 950))
        self.assertEqual(rid, min(regs, key=lambda r: r.bbox[1]).id, why)
        self.assertGreaterEqual(conf, 0.7)

    def test_explicit_id(self):
        regs = rg.build(CIRC + GRAPH + NOTE, [])
        self.assertEqual(rg.resolve("what is R2?", regs)[:2], ("R2", 1.0))

    def test_unknown_explicit_id(self):
        regs = rg.build(CIRC, [])
        self.assertIsNone(rg.resolve("redo R9", regs)[0])

    def test_my_circuit_excludes_monke(self):
        regs = rg.build(circuit(100, 1300), [{"box": (100, 600, 500, 860), "type": "circuit"}])
        rid, _, why = rg.resolve("check my circuit", regs, last_prompt_bbox=(100, 900, 500, 940))
        r = next(r for r in regs if r.id == rid)
        self.assertEqual(r.owner, "navy", why)

    def test_your_graph_includes_monke(self):
        regs = rg.build(graph(100, 700), [{"box": (100, 1400, 600, 1700), "type": "graph", "id": "G1"}])
        rid, _, why = rg.resolve("extend your graph", regs, last_prompt_bbox=(100, 1080, 500, 1120))
        r = next(r for r in regs if r.id == rid)
        self.assertEqual((r.owner, r.kind), ("monke", "graph"), why)

    def test_no_kind_no_place_is_none(self):
        regs = rg.build(CIRC + GRAPH, [])
        self.assertIsNone(rg.resolve("what is 2 + 2", regs)[0])

    def test_twelve_fixture_phrases(self):
        page = rg.build(CIRC + GRAPH + NOTE + equation(800, 1000) + circuit(700, 1400),
                        [{"box": (100, 1400, 560, 1700), "type": "graph", "id": "G1", "text": "y = x^2"}])
        k = {(r.kind, r.owner, r.bbox[1] > 1200): r.id for r in page}
        circ_top, circ_low = k[("circuit", "navy", False)], k[("circuit", "navy", True)]
        g_navy, g_monke = k[("graph", "navy", False)], k[("graph", "monke", True)]
        eq, note = k[("math", "navy", False)], k[("text", "navy", False)]
        under_top_circ = box_of(PROMPT_UNDER_CIRCUIT)
        mid = (600, 1200, 900, 1240)
        cases = [
            ("the circuit in this area", dict(last_prompt_bbox=under_top_circ), circ_top),
            ("this circuit", dict(pen_point=(900, 1450)), circ_low),
            ("the circuit below", dict(last_prompt_bbox=mid), circ_low),
            ("the circuit above", dict(last_prompt_bbox=mid), circ_top),
            ("my graph", dict(last_prompt_bbox=mid), g_navy),
            ("your graph", dict(last_prompt_bbox=mid), g_monke),
            ("that equation", dict(last_prompt_bbox=mid), eq),
            ("the note above", dict(last_prompt_bbox=(100, 900, 500, 940)), note),
            ("here", dict(pen_point=(1000, 430)), g_navy),
            ("G1", {}, g_monke),
            (f"explain {circ_low}", {}, circ_low),
            ("the left graph", dict(last_prompt_bbox=mid), g_monke),
        ]
        got = [(p, rg.resolve(p, page, **kw)) for p, kw, _ in cases]
        right = sum(1 for (p, (rid, _, _)), (_, _, want) in zip(got, cases) if rid == want)
        self.assertEqual(right, 12, "\n".join(f"{p}: {r}" for p, r in got))


class ContextBlock(unittest.TestCase):
    def setUp(self):
        strokes = []
        for i in range(12):                                  # 3 x 4 grid of mixed kinds
            x, y = 60 + 460 * (i % 3), 80 + 440 * (i // 3)
            strokes += (circuit(x, y, 300, 200) if i % 3 == 0 else
                        graph(x, y, 300, 220) if i % 3 == 1 else text(x, y + 80, 8))
        self.regs = rg.build(strokes, [])
        self.assertEqual(len(self.regs), 12)

    def test_within_budget_and_has_focus(self):
        for focus in [r.id for r in self.regs]:
            out = rg.context_block(self.regs, focus=focus, budget_chars=700)
            self.assertLessEqual(len(out), 700)
            self.assertIn(f'id="{focus}"', out)
            self.assertTrue(out.startswith("<regions") and out.endswith("</regions>"))

    def test_tiny_budget_keeps_focus_nearest(self):
        last = max(self.regs, key=lambda r: (r.bbox[1], r.bbox[0]))
        out = rg.context_block(self.regs, focus=(last.bbox[0] + 5, last.bbox[3] + 10), budget_chars=160)
        self.assertLessEqual(len(out), 160)
        self.assertIn(f'id="{last.id}"', out)
        self.assertIn('near="focus"', out)

    def test_sorted_by_distance_to_focus_box(self):
        first = min(self.regs, key=lambda r: (r.bbox[1], r.bbox[0]))
        out = rg.context_block(self.regs, focus=(first.bbox[0], first.bbox[3] + 20,
                                                 first.bbox[2], first.bbox[3] + 60), budget_chars=5000)
        ids = [line.split('id="')[1].split('"')[0] for line in out.splitlines() if 'id="' in line]
        self.assertEqual(ids[0], first.id)
        self.assertEqual(len(ids), 12)

    def test_fields(self):
        out = rg.context_block(self.regs[:1], budget_chars=700)
        r = self.regs[0]
        self.assertIn(f'kind="{r.kind}" owner="navy" box="{",".join(map(str, r.bbox))}"', out)

    def test_empty(self):
        self.assertEqual(rg.context_block([], budget_chars=700), "<regions/>")


class Directives(unittest.TestCase):
    def setUp(self):
        self.regs = rg.build(CIRC + GRAPH + NOTE, [])

    def test_target_maps_to_bbox(self):
        r2 = next(r for r in self.regs if r.id == "R2")
        hit = rg.target("@target R2\nThe current is 2 A.", self.regs)
        self.assertEqual(hit, ("R2", r2.bbox))

    def test_target_label_and_unknown(self):
        regs = rg.build(CIRC, [{"box": (800, 280, 1220, 588), "type": "graph", "id": "G1"}])
        g = by_kind(regs, "graph")[0]
        self.assertEqual(rg.target("@target G1\nok", regs), (g.id, g.bbox))
        self.assertIsNone(rg.target("@target R9\nok", regs))
        self.assertIsNone(rg.target("no directive", regs))

    def test_directive_boxes_place(self):
        out = rg.directive_boxes("@place below R1\n@refine R3 as circuit\nbody", self.regs)
        r1 = next(r for r in self.regs if r.id == "R1")
        self.assertEqual(out[0]["box"], r1.bbox)
        self.assertEqual(out[1]["op"], "refine")
        self.assertIn("box", out[1])

    def test_boxes_dict_compatible(self):
        b = rg.boxes(self.regs)
        self.assertEqual(set(b), {"R1", "R2", "R3"})
        self.assertTrue(all(len(v) == 4 and all(isinstance(n, int) for n in v) for v in b.values()))

    def test_to_screen_box(self):
        r = self.regs[0]
        v = {"scale": 1, "right": -702, "top": 100}
        x0, y0, x1, y1 = rg.to_screen_box(r.bbox, v)
        self.assertEqual((x0, y0), (r.bbox[0] + 702, r.bbox[1] - 100))

    def test_to_page_box_inverts(self):
        r = self.regs[0]
        for v in ({"scale": 1, "right": -702, "top": 100}, {"ox": 702, "oy": -340}):
            self.assertEqual(rg.to_page_box(rg.to_screen_box(r.bbox, v), v), r.bbox)


if __name__ == "__main__":
    unittest.main()
