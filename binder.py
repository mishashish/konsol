"""Назначение: ждём, что пользователь нажмёт/отклонит на джойстике, и запоминаем это."""

from __future__ import annotations

import time
from typing import Any

from config import TARGETS
from engine import OutState, RawState

BIND_TIMEOUT_S = 10.0
AXIS_THRESHOLD = 0.6


def is_active(key: str, out: OutState) -> bool:
    values = {"LT": out.lt, "RT": out.rt, "LX": out.lx, "LY": out.ly, "RX": out.rx, "RY": out.ry}
    if key in values:
        return abs(values[key]) > 0.3
    return key in out.pressed


def same_source(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if a.get("type") != b.get("type") or a.get("index") != b.get("index"):
        return False
    if a["type"] == "hat":
        return a.get("value") == b.get("value")
    if a["type"] == "axis":
        return a.get("full") == b.get("full") and a.get("sign") == b.get("sign")
    return True


class Binder:
    def __init__(self, keys: list[str], rest: list[float]) -> None:
        self.keys = keys
        self.total = len(keys)
        self.rest = rest
        self.phase = "listen"
        self.deadline = time.monotonic() + BIND_TIMEOUT_S

    @property
    def key(self) -> str:
        return self.keys[0]

    @property
    def step(self) -> int:
        return self.total - len(self.keys) + 1

    def is_axis(self) -> bool:
        return next(t.is_axis for t in TARGETS if t.key == self.key)

    def skip(self) -> None:
        if self.phase == "listen":
            self.deadline = 0.0

    def seconds_left(self) -> int:
        return max(0, int(self.deadline - time.monotonic()) + 1)

    def tick(self, raw: RawState) -> tuple[str, dict[str, Any] | None] | None:
        """Возвращает ("assign", src) / ("skip", None) / ("done", None) или None, если ждём."""
        if self.phase == "release":
            if not self._idle(raw):
                return None
            self.keys.pop(0)
            if not self.keys:
                return ("done", None)
            self.phase = "listen"
            self.deadline = time.monotonic() + BIND_TIMEOUT_S
            return None
        src = self._detect(raw, self.is_axis())
        if src is not None:
            self.phase = "release"
            return ("assign", src)
        if time.monotonic() >= self.deadline:
            self.phase = "release"
            return ("skip", None)
        return None

    def _idle(self, raw: RawState) -> bool:
        if any(raw.buttons) or any(h != (0, 0) for h in raw.hats):
            return False
        return all(abs(a - r) < 0.3 for a, r in zip(raw.axes, self.rest))

    def _detect(self, raw: RawState, axis_only: bool) -> dict[str, Any] | None:
        if not axis_only:
            for i, pressed in enumerate(raw.buttons):
                if pressed:
                    return {"type": "button", "index": i}
            for i, (hx, hy) in enumerate(raw.hats):
                if (hx, hy) != (0, 0):
                    return {"type": "hat", "index": i, "value": [hx, hy]}
        axes = raw.axes[: len(self.rest)]
        if not axes:
            return None
        best = max(range(len(axes)), key=lambda i: abs(axes[i] - self.rest[i]))
        delta = axes[best] - self.rest[best]
        if abs(delta) <= AXIS_THRESHOLD:
            return None
        if axis_only:
            return {"type": "axis", "index": best, "sign": 1 if axes[best] > 0 else -1}
        return {"type": "axis", "index": best, "rest": round(self.rest[best], 3), "full": 1.0 if delta > 0 else -1.0}
