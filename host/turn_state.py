"""Canonical, atomically-published host turn state."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

STAGES = frozenset({"idle", "looking", "settling", "reading", "thinking", "confirming", "inking"})


class TurnState:
    def __init__(self, path: Path):
        self.path = path
        self._status = "idle"

    def set(self, stage: str, reason: str = "") -> str:
        if stage == "error":
            status = "error" + (f" {' '.join(reason.split())}" if reason else "")
        elif stage in STAGES and not reason:
            status = stage
        else:
            raise ValueError(f"invalid turn state: {stage} {reason}".strip())
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(status + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_name, self.path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)
        self._status = status
        return status

    def status(self) -> str:
        return self._status


class SettleGate:
    """Require idle time followed by a pen-up quiet settling interval."""

    def __init__(self, idle_seconds: float, settle_seconds: float):
        self.idle_seconds = idle_seconds
        self.settle_seconds = settle_seconds
        self.last_event: float | None = None
        self.deadline: float | None = None

    def note_event(self, now: float) -> None:
        self.last_event = now
        self.deadline = None

    def poll(self, now: float, stroke_active: bool) -> bool:
        if stroke_active or self.last_event is None:
            self.deadline = None
            return False
        if now - self.last_event < self.idle_seconds:
            return False
        if self.deadline is None:
            self.deadline = now + self.settle_seconds
            return False
        return now >= self.deadline

    def reset(self) -> None:
        self.last_event = self.deadline = None

