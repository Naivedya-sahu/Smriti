"""skill.py — the model prompts, composed from skill/smriti/ (the single source).

The endpoint cannot load a native skill (`claude -p --tools ""`, one turn:
skill/smriti/REFINEMENT.md), so the same files are composed here into the
system prompts, deterministically:

  reply_system(kind)  SKILL.md + references/protocol.md + ONE kinds/<k>.md
  read_system()       the stage-1 reading schema + the SPP page map / target
  kind_hints(kind)    {"read", "reply"}: the "## Read" / "## Reply" of a kind
  auto_ask()          the short stage-1 ask when no profile is set

Files are re-read when their mtime changes. SMRITI_SKILL_DIR overrides the
folder (prompt experiments without touching the repo).
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "skill" / "smriti"
KINDS = ("text", "math", "circuit", "graph", "sketch", "mixed")
_CACHE: dict = {}                      # path -> (mtime, text)


def _text(rel: str) -> str:
    p = Path(os.environ.get("SMRITI_SKILL_DIR") or ROOT) / rel
    mt = p.stat().st_mtime_ns
    hit = _CACHE.get(str(p))
    if not hit or hit[0] != mt:
        hit = _CACHE[str(p)] = (mt, p.read_text(encoding="utf-8"))
    return hit[1]


def _sections(md: str, mark: str = "##") -> tuple[str, dict[str, str]]:
    """(text before the first heading, {heading: body}) at one heading level."""
    parts = re.split(rf"^{mark} +(.+)$", md, flags=re.M)
    return parts[0].strip(), dict(zip(parts[1::2], (b.strip() for b in parts[2::2])))


def _find(secs: dict[str, str], prefix: str) -> str:
    return next(b for t, b in secs.items() if t.startswith(prefix))


def _core() -> str:
    """SKILL.md for the model: no YAML frontmatter, no 'Loading (daemon
    side)' section (that one is for humans)."""
    md = re.sub(r"\A---\n.*?\n---\n", "", _text("SKILL.md"), flags=re.S)
    return re.sub(r"^## Loading.*?(?=^## |\Z)", "", md, flags=re.M | re.S).strip()


def _hint(body: str) -> str:
    """One prompt line: a paragraph in (...) is the reason a rule exists,
    for humans (the circuit one quotes the very values a 4B model copied)."""
    body = re.sub(r"^\(.*?\)[ \t]*$", "", body, flags=re.M | re.S)
    return " ".join(body.replace("`", "").split())


def kind_hints(kind: str | None) -> dict[str, str]:
    _, secs = _sections(_text(f"kinds/{kind if kind in KINDS else 'mixed'}.md"))
    return {"read": _hint(secs.get("Read", "")), "reply": _hint(secs.get("Reply", ""))}


def auto_ask() -> str:
    _, secs = _sections(_core())
    return _hint(_find(_sections(_find(secs, "Stage 1"), "###")[1], "Auto ask"))


def profiles() -> dict[str, str]:
    """App profile -> kind module, from each module's '# kind: k (profile: p)'."""
    out = {}
    for k in KINDS:
        if m := re.match(r"# kind: \w+ \(profile: (\w+)\)", _text(f"kinds/{k}.md")):
            out[m[1]] = k
    return out


def read_system() -> str:
    """Stage 1: the reading schema, the <page> map and the target fields."""
    _, core = _sections(_core())
    _, spp = _sections(_text("references/protocol.md"))
    stage1 = _sections(_find(core, "Stage 1"), "###")[0]
    return "\n\n".join([stage1] + [f"## {t}\n{b}" for t, b in spp.items()
                                   if t.startswith(("1.", "2."))])


def reply_system(kind: str | None = None) -> str:
    """Stage 2 / greeting: core + protocol + the kind's Reply and rules.
    A drawing kind brings only its own module (A/B 2026-09-26: all hints at
    once hurt). text / mixed / None say what Navy WROTE, not what the reply
    needs ('draw an op-amp filter' is text), so they keep every module's
    rules: no rule the old one-piece prompt had is lost on such a turn."""
    parts = [_core(), _text("references/protocol.md").strip()]
    one = kind in KINDS and kind not in ("text", "mixed")
    for k in ([kind] if one else KINDS):
        head, secs = _sections(_text(f"kinds/{k}.md"))
        keep = [f"## {t}\n{b}" for t, b in secs.items()
                if t != "Read" and (t != "Reply" or k == kind)]
        if keep:
            parts.append("\n\n".join([head] + keep))
    return "\n\n".join(parts)


def merged_system(kind: str | None = None) -> str:
    """One-call turn contract: structured reading first, normal reply second."""
    contract = ("First the JSON object, then a blank line, then the reply. "
                "If intent is plot/graph, or the page is unreadable, stop after the JSON.")
    return "\n\n".join((read_system(), reply_system(kind), contract))
