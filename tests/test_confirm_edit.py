"""A confirm-window edit must fully replace the model's reading in stage 2
(device 2026-09-29: edit R2 4k->6k, reply still used 4k from the summary)."""
# pyright: reportMissingImports=false

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness


class ConfirmEditTest(unittest.TestCase):
    def test_edit_replaces_stale_summary(self):
        r = {"kind": "math", "text": "find Vout if Vin=12 R1=2k R2=4k",
             "latex": "V_{out}=V_{in}R_2/(R_1+R_2)", "circuit": "",
             "intent": "solve", "summary": "find Vout with R2=4k"}
        e = harness.apply_edit(r, "find Vout if Vin=12 R1=2k R2=6k")
        p = harness.respond_prompt(e, None, "", "")
        self.assertIn("R2=6k", p)
        self.assertNotIn("4k", p)
        self.assertEqual(e["kind"], "math")

    def test_reading_json_followed_by_latex_prose_parses(self):
        # device 2026-09-29: sonnet sent JSON, then '---' and a worked answer
        # with braces; the greedy match swallowed it -> no confirm popup
        raw = ('{"kind": "math", "text": "find Vout", "latex": "V=IR", '
               '"circuit": "", "intent": "solve", "summary": "divider"}\n---\n'
               'Voltage divider: $$V = 12 \\dfrac{4k}{2k+4k}$$')
        r = harness.parse_reading(raw)
        self.assertEqual((r["kind"], r["intent"], r["text"]), ("math", "solve", "find Vout"))


class EditSplitTest(unittest.TestCase):
    # run 13 (device): the popup shows 'text | maths: latex'; the edit came
    # back whole in `text`, so the plotter could not parse it -> model tikz,
    # no stored graph -> the next 'add y = ...' drew a lone curve
    R = {"kind": "graph", "text": "y = 10*(1-exp(-x/2)), x from 0 to 10",
         "latex": r"y = 10\left(1 - e^{-x/2}\right), \quad x \in [0,\,10]",
         "circuit": "", "intent": "plot", "summary": "s"}

    def test_edit_splits_back_into_fields(self):
        e = harness.apply_edit(self.R, "y = x^2, x from 0 to 3 | maths: y = x^2 | circuit: none")
        self.assertEqual((e["text"], e["latex"], e["circuit"], e["summary"]),
                         ("y = x^2, x from 0 to 3", "y = x^2", "none", ""))

    def test_range_edited_only_in_maths_wins(self):
        import graph
        e = harness.apply_edit(self.R, harness.describe(self.R).replace(r"[0,\,10]", r"[0,\,15]"))
        self.assertEqual(graph.parse(e["text"])[1:3], (0.0, 15.0))


class RepairCheckTest(unittest.TestCase):
    # device 2026-09-30: the repair check compiled the bare $$ body, so every
    # maths block failed "Missing $ inserted" and cost a model call (~8 s)
    def test_math_block_is_checked_as_the_renderer_compiles_it(self):
        seen = []

        def compile_fn(src):
            seen.append(src)
            if "\\begin" not in src and not src.startswith("$"):
                raise RuntimeError("Missing $ inserted")

        def chat(m):
            raise AssertionError("repair called for a valid block")
        out = harness.repair_latex("Thevenin: $$V_{th}$$ done", chat, compile_fn)
        self.assertEqual(out, "Thevenin: $$V_{th}$$ done")
        self.assertEqual(seen, ["$V_{th}$"])


class BlockSrcTest(unittest.TestCase):
    # device 2026-09-30: $$\[V_{out} = ... = 8 V\]$$ was wrapped into
    # $\[...\]$ -> pdflatex failed -> the answer was never inked
    def test_inner_display_delimiters_are_unwrapped(self):
        from tex import block_src
        for src in (r"\[V = IR\]", r"\(V = IR\)", "$V = IR$", " V = IR "):
            self.assertEqual(block_src(src), "$V = IR$")
        env = r"\begin{circuitikz}\draw (0,0) to[R] (2,0);\end{circuitikz}"
        self.assertEqual(block_src(env), env)

    # live 2026-10-10 (ChatGPT): $$\begin{aligned}..\end{aligned}$$ was passed
    # through bare -> "\begin{aligned} allowed only in math mode" -> no ink
    def test_math_only_environments_go_into_math_mode(self):
        from tex import block_src
        body = r"\begin{aligned} f_c&=\frac{1}{2\pi RC}\\ &\approx 1.59\,\mathrm{mHz} \end{aligned}"
        self.assertEqual(block_src(body), "$\\displaystyle " + body + "$")
        for env in ("cases", "pmatrix", "bmatrix", "matrix", "gathered", "split", "array"):
            b = rf"\begin{{{env}}} a \end{{{env}}}"
            self.assertTrue(block_src(b).startswith("$\\displaystyle \\begin"), env)

    def test_display_environments_become_their_inline_twin(self):
        from tex import block_src
        self.assertEqual(block_src(r"\begin{align*} a&=b \end{align*}"),
                         r"$\displaystyle \begin{aligned} a&=b \end{aligned}$")
        self.assertEqual(block_src(r"\begin{equation} a=b \end{equation}"), "$\\displaystyle a=b$")
        self.assertEqual(block_src(r"\[\begin{aligned} a \end{aligned}\]"),
                         r"$\displaystyle \begin{aligned} a \end{aligned}$")
        tbl = r"\begin{tabular}{cc} 1 & 2 \end{tabular}"
        self.assertEqual(block_src(tbl), tbl)                  # text-mode env: as is


class WriterChatTest(unittest.TestCase):
    def test_cloud_writer_routes_repair_through_claude(self):
        from unittest.mock import patch
        import monke
        with patch.object(monke, "cloud_chat", return_value="C") as cc:
            f = monke._writer_chat({"writer": "sonnet", "escalate": ""}, None, "h", "repair")
        self.assertEqual(f, "C")
        self.assertEqual(cc.call_args.args[:4], ("sonnet", None, "h", "repair"))


if __name__ == "__main__":
    unittest.main()
