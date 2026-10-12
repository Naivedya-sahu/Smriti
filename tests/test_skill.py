"""The model prompts come from skill/smriti/ (host/skill.py), not from
constants. Snapshot of every rule the constants carried at 4142f07
(monke.MONKE_SYSTEM, harness.READ_SYSTEM / SKILLS / AUTO_ASK / PROFILES):
each idea must still reach the model in the composed text. No rule lost."""
# pyright: reportMissingImports=false

import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "host"))

import harness
import monke

# ── monke.MONKE_SYSTEM @ 4142f07: rule -> keywords (regex, case-insensitive) ──
CORE = {
    "persona": [r"Monke", r"diary", r"spirit"],
    "red is the newest writing": [r"RED", r"black", r"context"],
    "continuous per notebook": [r"continuous per notebook", r"consistent"],
    "voice": [r"terse", r"primal-wise", r"warm but blunt", r"short sentences"],
    "who Navy is": [r"electrical engineer", r"IIT Delhi", r"journaling", r"habits"],
    "page size": [r"1404 x 1872"],
    "placed automatically": [r"never set coordinates"],
    "room budget": [r"90 ?px", r"150-350", r"one equation or one small drawing"],
    "answer the content": [r"CONTENT", r"sharp friend"],
    "35 words": [r"35 words", r"no greetings?", r"no sign-off"],
    "ASCII only": [r"ASCII only", r"emoji", r"markdown", r"unicode"],
    "$$ for maths": [r"\$\$", r"amsmath", r"ALL maths", r"\\frac"],
    "circuitikz math-mode labels": [r"circuitikz", r"math mode", r"l=\$R_1\$"],
    "sin(deg(x))": [r"pgfplots", r"sin\(deg\(x\)\)", r"degrees"],
    "svg element list": [r'<svg viewBox="0 0 400 300">', r"\bline\b", r"\brect\b", r"\bcircle\b",
                         r"\bellipse\b", r"\bpolyline\b", r"\bpolygon\b", r"\bpath\b", r"\btext\b",
                         r"no fill"],
    "one block per drawing": [r"block per drawing", r"prose stays outside", r"logged"],
    "unreadable page": [r"unreadable"],
}
BY_KIND = {            # the long form of a rule lives in its kind module
    "circuit": {
        "bare Omega fails": [r"\\Omega", r"fails to compile", r"v=\$12\\,V\$"],
        "op-amp anchors": [r"node\[op amp\]\(A\)\{\} FIRST", r"\(A\.-\)", r"\(A\.\+\)", r"\(A\.out\)",
                           r"right-angle", r"absolute coordinates", r"Feedback",
                           r"coordinate\(f\)", r"v_\{out\}"]},
    "graph": {"pgfplots example": [r"\\begin\{tikzpicture\}\\begin\{axis\}", r"axis lines=middle",
                                   r"\\addplot\[domain=0:5,samples=60\]\{exp\(-x\)\}"]},
    "sketch": {"label every block": [r"Label EVERY block", r'text-anchor="middle"', r"1-3 words",
                                     r"M L H V C Q Z"]},
    "text": {"OCR errors": [r"OCR", r"errors"]},
}
ALL = dict(CORE, **{n: kw for rules in BY_KIND.values() for n, kw in rules.items()})

# ── harness.READ_SYSTEM @ 4142f07 ──
READ = {
    "two images": [r"FOCUS", r"OVERVIEW", r"scaled up", r"boxed"],
    "red newest, black context": [r"RED", r"newest", r"black", r"context"],
    "one JSON object": [r"ONLY", r"one JSON object", r"no prose", r"no code fence"],
    "fields": [r'"kind": "text\|math\|circuit\|graph\|sketch\|mixed"', r'"text"', r'"latex"',
               r"no \$ delimiters", r'"circuit"', r"labels as written",
               r'"intent": "question\|note\|solve\|plot\|draw\|write', r'"summary"'],
    "#P kept": [r"#P", r"instruction"],
    "[?] for illegible": [r"\[\?\]", r"illegible", r"(not|never) guess"],
}
SPP_READ = {"SPP fields": [r'"target"', r'"target_kind"', r'"target_desc"', r"refine", r"<page"]}

# ── harness.AUTO_ASK / PROFILES + SKILLS[kind]["read"]: the stage-1 ask per profile ──
ASK = {
    "auto": [r"Transcribe handwriting exactly", r"exact LaTeX", r"only components you see",
             r"Never invent"],
    "handwriting": [r"handwritten prose", r"word for word", r"line breaks as ' / '"],
    "math": [r"Treat the focus as mathematics", r"exact LaTeX", r"'latex'", r"no \$ delimiters",
             r"superscripts, subscripts, fractions, Greek"],
    "graph": [r"function or data to plot", r"y = <expression in x>, x from <a> to <b>",
              r"leading 'add'", r"add/also/overlay", r"trailing 'to G<n>'", r"axes names",
              r"key points", r"ranges"],
    "circuit": [r"electrical circuit", r"only the components you SEE",
                r"label/value exactly as written",
                r"resistor <label>; capacitor <label>; NPN transistor", r"Never invent values",
                r"leave a value out"],
    "diagram": [r"block diagram", r"blocks and arrows", r"'A -> B -> C'",
                r"shapes and how they connect"],
}
PROFILE_KIND = {"auto": None, "handwriting": "text", "math": "math", "graph": "graph",
                "circuit": "circuit", "diagram": "sketch"}

# ── harness.SKILLS[kind]["reply"]: stage-2 prompt + system for that kind ──
REPLY = {
    "text": [r"Answer in prose", r"Maths only if it genuinely helps"],
    "math": [r"Work it in \$\$\.\.\.\$\$ \(amsmath\)", r"prose to one line"],
    "circuit": [r"ANSWER the question first", r"with units", r"ONLY if asked to draw",
                r"\\begin\{circuitikz\}", r"math mode", r"R, C, L, V \(uppercase\), I, sV, npn, D"],
    "graph": [r"pgfplots", r"\\begin\{tikzpicture\}\\begin\{axis\}", r"sin\(deg\(x\)\)"],
    "sketch": [r"ANSWER what was asked about the diagram in prose first",
               r'<svg viewBox="0 0 400 300">', r"only if asked to draw", r"label every block",
               r"<text"],
    "mixed": [r"Prose plus at most one drawing or equation block"],
}


def missing(text: str, rules: dict) -> list[str]:
    flat = " ".join(text.replace("`", "").split())      # markdown code marks are not content
    return [f"{name}: /{kw}/" for name, kws in rules.items() for kw in kws
            if not re.search(kw, flat, re.I)]


def ask_for(profile: str) -> str:
    """The stage-1 request the reader gets under this profile (system + user)."""
    from PIL import Image
    seen = []

    def chat(messages):
        seen.extend(messages)
        return "{}"
    harness.read(Image.new("RGB", (40, 40), "white"), None, chat, profile=profile)
    return seen[0]["content"] + "\n" + seen[1]["content"][0]["text"]


def reading(kind: str, **kw) -> dict:
    return dict(harness.parse_reading('{"kind": "%s", "text": "x"}' % kind), **kw)


class NoRuleLostTest(unittest.TestCase):
    def test_monke_system_keeps_every_rule(self):
        self.assertEqual(missing(monke.MONKE_SYSTEM, ALL), [])

    def test_read_system_keeps_every_rule(self):
        self.assertEqual(missing(harness.READ_SYSTEM, READ), [])

    def test_stage1_ask_per_profile(self):
        for profile, kws in ASK.items():
            with self.subTest(profile):
                self.assertEqual(missing(ask_for(profile), {profile: kws}), [])

    def test_profiles_map_to_kinds(self):
        self.assertEqual({p: v[1] for p, v in harness.PROFILES.items()}, PROFILE_KIND)
        self.assertEqual(harness.PROFILES["auto"][0], "")
        self.assertEqual(set(harness.SKILLS), set(harness.KINDS))


class SkillLoaderTest(unittest.TestCase):
    def test_constants_are_the_loader(self):
        import skill
        self.assertEqual(monke.MONKE_SYSTEM, skill.reply_system(None))
        self.assertEqual(harness.READ_SYSTEM, skill.read_system())
        self.assertEqual(harness.AUTO_ASK, skill.auto_ask())
        for k in harness.KINDS:
            self.assertEqual(harness.SKILLS[k], skill.kind_hints(k))

    def test_every_kind_keeps_the_core_and_its_own_rules(self):
        import skill
        for k in harness.KINDS:
            with self.subTest(k):
                # text / mixed say what Navy wrote, not what the reply needs
                # ("draw an op-amp filter" is text): they keep every drawing rule
                own = ALL if k in ("text", "mixed") else dict(CORE, **BY_KIND.get(k, {}))
                self.assertEqual(missing(skill.reply_system(k), own), [])

    def test_one_kind_module_per_drawing_kind(self):
        import skill
        self.assertNotIn("op amp", skill.reply_system("math"))
        self.assertNotIn("Label EVERY block", skill.reply_system("circuit"))

    def test_reply_hint_reaches_the_writer(self):
        import skill
        for k, kws in REPLY.items():
            with self.subTest(k):
                r = reading(k)
                text = (harness.respond_prompt(r, None, "", "") + "\n"
                        + skill.reply_system(harness.reply_kind(r, "auto")))
                self.assertEqual(missing(text, {k: kws}), [])

    def test_human_only_parts_stay_out(self):
        import skill
        s = skill.reply_system(None)
        self.assertNotIn("Loading (daemon side)", s)
        self.assertNotIn("version: 0.1.0", s)                 # YAML frontmatter
        self.assertFalse(s.startswith("---"))
        # the reason the circuit example has no values must not carry them in
        self.assertNotIn("4ohm", skill.kind_hints("circuit")["read"])
        self.assertNotIn("4ohm", ask_for("circuit"))
        self.assertNotIn("\n", skill.kind_hints("graph")["reply"])

    def test_read_system_carries_spp(self):
        import skill
        self.assertEqual(missing(skill.read_system(), SPP_READ), [])
        self.assertNotIn("Persona", skill.read_system())      # stage 1 stays short

    def test_reply_system_carries_protocol(self):
        import skill
        for kw in ("@target", "@place", "@refine", "<page", "NEVER erased"):
            self.assertIn(kw, skill.reply_system("graph"))

    def test_reload_on_mtime_and_env_override(self):
        import shutil
        import skill
        with tempfile.TemporaryDirectory() as d:
            dst = Path(d) / "smriti"
            shutil.copytree(skill.ROOT, dst)
            with patch.dict(os.environ, {"SMRITI_SKILL_DIR": str(dst)}):
                self.assertEqual(skill.kind_hints("mixed"), harness.SKILLS["mixed"])
                f = dst / "kinds" / "mixed.md"
                f.write_text("# kind: mixed\n\n## Read\nRead it.\n\n## Reply\nSay ook.\n",
                             encoding="utf-8")
                os.utime(f, ns=(1, 1))                        # coarse clocks: force a new mtime
                self.assertEqual(skill.kind_hints("mixed"), {"read": "Read it.", "reply": "Say ook."})
                self.assertIn("Say ook.", skill.reply_system("mixed"))
        self.assertEqual(skill.kind_hints("mixed"), harness.SKILLS["mixed"])


class ReplyKindTest(unittest.TestCase):
    """profile kind, else the target's kind when refining, else the reading's kind."""

    def test_order(self):
        refine = reading("text", intent="refine", target="N1", target_kind="graph")
        self.assertEqual(harness.reply_kind(refine, "circuit"), "circuit")
        self.assertEqual(harness.reply_kind(refine, "auto"), "graph")
        self.assertEqual(harness.reply_kind(reading("math", target_kind="graph"), "auto"), "math")
        self.assertEqual(harness.reply_kind(reading("text", intent="refine"), "auto"), "text")

    def test_refine_gets_the_target_kinds_hint(self):
        refine = reading("text", intent="refine", target="N1", target_kind="graph")
        self.assertIn(harness.SKILLS["graph"]["reply"], harness.respond_prompt(refine, None, "", ""))


if __name__ == "__main__":
    unittest.main()
