"""
plan_stream.py — the streamed smriti_plan JSON -> each block the moment it
closes (the JSON twin of harness.stream_units): inking starts at block 0,
not after the whole plan.

    s = Scanner(taken={"G1"})
    for chunk in deltas:
        for b in s.feed(chunk):      # canonical render_plan.Blocks (plan_schema.block)
            ink(b)
    s.close()                         # problems += truncation; s.header = {place, outline}

Stdlib character scanner: string / escape / depth state survives chunk
boundaries (a block split mid-string or mid-escape is fine). Top-level keys
other than "blocks" are parsed when their value closes (s.header). A root
that is a bare array is read as the block list. Text after the root closes
is ignored. A block that is not valid JSON is skipped with a reason (strict
decoding makes that a truncation case only).
"""

from __future__ import annotations

import json

import plan_schema


class Scanner:
    def __init__(self, taken=()):
        self.buf = ""
        self.i = 0                 # next char to scan
        self.depth = 0
        self.in_str = self.esc = False
        self.root = None           # "{" or "["
        self.done = False          # the root closed
        self.key = None            # current top-level key
        self.key_at = None         # start of a top-level key string
        self.val_at = None         # start of a top-level (non-blocks) value
        self.in_blocks = False     # inside the blocks array
        self.el_at = None          # start of the current block element
        self.raw: list = []        # every element as parsed JSON (tests, logs)
        self.header: dict = {}
        self.problems: list[str] = []
        self.taken = set(taken or ())
        self.saw_blocks = False
        self.n = 0                 # elements seen

    def feed(self, chunk: str) -> list:
        """More streamed text -> the canonical Blocks that just closed."""
        out = []
        for raw in self.elements(chunk):
            got, why = plan_schema.block(raw, self.taken)
            self.problems += [f"blocks[{self.n - 1}]: {w}" for w in why]
            out += got
        return out

    def elements(self, chunk: str) -> list:
        """More streamed text -> the raw block objects that just closed."""
        if self.done or not chunk:
            return []
        self.buf += chunk
        got, b = [], self.buf
        while self.i < len(b) and not self.done:
            c, i = b[self.i], self.i
            self.i += 1
            if self.in_str:
                if self.esc:
                    self.esc = False
                elif c == "\\":
                    self.esc = True
                elif c == '"':
                    self.in_str = False
                    if self.key_at is not None and self.depth == 1:
                        self.key = json.loads(b[self.key_at:i + 1])
                        self.key_at = None
                continue
            if self.root is None:                    # skip a ```json fence / junk before the root
                if c in "{[":
                    self.root, self.depth = c, 1
                    if c == "[":
                        self.in_blocks = self.saw_blocks = True
                continue
            if c == '"':
                self.in_str = True
                if self.depth == 1 and self.root == "{":
                    if self.key is None:
                        self.key_at = i
                    elif self.val_at is None:
                        self.val_at = i              # a string value ("place")
                continue
            if c in " \t\r\n":
                continue
            if self.depth == 1 and self.root == "{":
                if c == ":":
                    continue
                if c in ",}":
                    self._end_value(b[self.val_at:i] if self.val_at is not None else None)
                    if c == "}":
                        self.done = True
                    continue
                if self.key == "blocks" and c == "[":
                    self.in_blocks = self.saw_blocks = True
                    self.depth = 2
                    continue
                if self.val_at is None:
                    self.val_at = i
            if c in "{[":
                if self.in_blocks and self.depth == self._list_depth() and c == "{":
                    self.el_at = i
                self.depth += 1
            elif c in "}]":
                self.depth -= 1
                if self.in_blocks and self.depth == self._list_depth() and c == "}" \
                        and self.el_at is not None:
                    got.append(self._element(b[self.el_at:i + 1]))
                    self.el_at = None
                elif self.in_blocks and c == "]" and self.depth == self._list_depth() - 1:
                    self.in_blocks = False           # the blocks array closed
                    if self.root == "[":
                        self.done = True
                    else:
                        self.key, self.val_at = None, None
        return [g for g in got if g is not None]

    def _list_depth(self) -> int:
        return 1 if self.root == "[" else 2

    def _element(self, src: str):
        self.n += 1
        try:
            obj = json.loads(src)
        except ValueError as e:
            self.problems.append(f"blocks[{self.n - 1}]: bad JSON ({str(e)[:60]}) -> skipped")
            return None
        self.raw.append(obj)
        return obj

    def _end_value(self, src) -> None:
        if self.key is not None and self.key != "blocks" and src is not None:
            try:
                self.header[self.key] = json.loads(src)
            except ValueError:
                self.problems.append(f"header {self.key!r}: bad JSON -> ignored")
        self.key, self.val_at = None, None

    def close(self) -> list[str]:
        """End of stream: what was left open is reported, never inked."""
        if self.root is None:
            self.problems.append("no JSON plan in the reply")
        elif not self.done:
            self.problems.append(f"plan truncated: {len(self.raw)} closed blocks kept")
        return self.problems

    @property
    def place(self) -> str | None:
        return plan_schema.header(self.header)["place"]
