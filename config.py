"""Настройки: режим эмуляции, раскладка (что на джойстике -> какая кнопка геймпада), опции."""

from __future__ import annotations

import copy
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VERSION = "1.0.0"
DATA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Konsol"
DATA_DIR.mkdir(parents=True, exist_ok=True)
SETTINGS_FILE = DATA_DIR / "settings.json"
LEGACY_MAPPING_FILE = DATA_DIR / "mapping.json"

MODES = {"xbox": "Xbox 360", "ps4": "PlayStation 4", "kbm": "Клавіатура і миша"}


def _vk(code: int, name: str) -> dict[str, Any]:
    return {"vk": code, "name": name}


def _mb(button: str, name: str) -> dict[str, Any]:
    return {"mouse": button, "name": name}


# Раскладка «как в обычном шутере» для игр без поддержки геймпада. Правый стик — мышь.
DEFAULT_KBM: dict[str, Any] = {
    "A": _vk(0x20, "Space"), "B": _vk(0xA2, "LCtrl"), "X": _vk(0x52, "R"), "Y": _vk(0x45, "E"),
    "LB": _vk(0x51, "Q"), "RB": _vk(0x46, "F"), "LT": _mb("right", "ПКМ"), "RT": _mb("left", "ЛКМ"),
    "BACK": _vk(0x09, "Tab"), "START": _vk(0x1B, "Esc"), "LS": _vk(0xA0, "LShift"), "RS": _vk(0x56, "V"),
    "TOUCHPAD": _vk(0x4D, "M"),
    "DPAD_UP": _vk(0x31, "1"), "DPAD_RIGHT": _vk(0x32, "2"), "DPAD_DOWN": _vk(0x33, "3"),
    "DPAD_LEFT": _vk(0x34, "4"),
    "LX-": _vk(0x41, "A"), "LX+": _vk(0x44, "D"), "LY+": _vk(0x57, "W"), "LY-": _vk(0x53, "S"),
}


@dataclass(frozen=True)
class Target:
    key: str
    xbox: str
    ps4: str
    is_axis: bool = False

    def label(self, mode: str) -> str:
        return self.ps4 if mode == "ps4" else self.xbox


TARGETS: tuple[Target, ...] = (
    Target("A", "A", "✕  Хрест"),
    Target("B", "B", "○  Коло"),
    Target("X", "X", "□  Квадрат"),
    Target("Y", "Y", "△  Трикутник"),
    Target("LB", "LB", "L1"),
    Target("RB", "RB", "R1"),
    Target("LT", "LT (курок)", "L2 (курок)"),
    Target("RT", "RT (курок)", "R2 (курок)"),
    Target("BACK", "Back", "Share"),
    Target("START", "Start", "Options"),
    Target("GUIDE", "Guide (Xbox)", "PS"),
    Target("TOUCHPAD", "Додаткова кнопка", "Тачпад (клік)"),
    Target("LS", "L3 (клік лів. стіка)", "L3 (клік лів. стіка)"),
    Target("RS", "R3 (клік пр. стіка)", "R3 (клік пр. стіка)"),
    Target("DPAD_UP", "Хрестовина ↑", "Хрестовина ↑"),
    Target("DPAD_DOWN", "Хрестовина ↓", "Хрестовина ↓"),
    Target("DPAD_LEFT", "Хрестовина ←", "Хрестовина ←"),
    Target("DPAD_RIGHT", "Хрестовина →", "Хрестовина →"),
    Target("LX", "Лівий стік → праворуч", "Лівий стік → праворуч", True),
    Target("LY", "Лівий стік ↑ вгору", "Лівий стік ↑ вгору", True),
    Target("RX", "Правий стік → праворуч", "Правий стік → праворуч", True),
    Target("RY", "Правий стік ↑ вгору", "Правий стік ↑ вгору", True),
)

# Типичная раскладка китайских "PC/PS3/Android Gamepad" (DragonRise/ShanWan) в режиме DirectInput.
DEFAULT_MAPPING: dict[str, Any] = {
    "Y": {"type": "button", "index": 0},
    "B": {"type": "button", "index": 1},
    "A": {"type": "button", "index": 2},
    "X": {"type": "button", "index": 3},
    "LB": {"type": "button", "index": 4},
    "RB": {"type": "button", "index": 5},
    "LT": {"type": "button", "index": 6},
    "RT": {"type": "button", "index": 7},
    "BACK": {"type": "button", "index": 8},
    "START": {"type": "button", "index": 9},
    "LS": {"type": "button", "index": 10},
    "RS": {"type": "button", "index": 11},
    "GUIDE": {"type": "button", "index": 12},
    "DPAD_UP": {"type": "hat", "index": 0, "value": [0, 1]},
    "DPAD_DOWN": {"type": "hat", "index": 0, "value": [0, -1]},
    "DPAD_LEFT": {"type": "hat", "index": 0, "value": [-1, 0]},
    "DPAD_RIGHT": {"type": "hat", "index": 0, "value": [1, 0]},
    "LX": {"type": "axis", "index": 0, "sign": 1},
    "LY": {"type": "axis", "index": 1, "sign": -1},
    "RX": {"type": "axis", "index": 2, "sign": 1},
    "RY": {"type": "axis", "index": 3, "sign": -1},
}


@dataclass
class Settings:
    mode: str = "xbox"
    deadzone: float = 0.08
    rumble: bool = True
    mapping: dict[str, Any] | None = None
    kbm: dict[str, Any] | None = None
    mouse_speed: int = 1400  # пикселей в секунду при полном отклонении правого стика

    def __post_init__(self) -> None:
        if self.mapping is None:
            self.mapping = copy.deepcopy(DEFAULT_MAPPING)
        if self.kbm is None:
            self.kbm = copy.deepcopy(DEFAULT_KBM)
        if self.mode not in MODES:
            self.mode = "xbox"


def load_settings() -> Settings:
    if SETTINGS_FILE.exists():
        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            return Settings(**{k: v for k, v in data.items() if k in Settings.__dataclass_fields__})
        except (ValueError, TypeError):
            pass
    if LEGACY_MAPPING_FILE.exists():
        return Settings(mapping=json.loads(LEGACY_MAPPING_FILE.read_text(encoding="utf-8")))
    return Settings()


def save_settings(settings: Settings) -> None:
    data = {"mode": settings.mode, "deadzone": settings.deadzone, "rumble": settings.rumble,
            "mapping": settings.mapping, "kbm": settings.kbm, "mouse_speed": settings.mouse_speed}
    SETTINGS_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def describe(src: dict[str, Any] | None) -> str:
    """Человеческое описание источника: «Кнопка 3», «Крестовина ↑», «Ось 2 −»."""
    if not src:
        return "— не призначено —"
    kind, idx = src["type"], src["index"]
    if kind == "button":
        return f"Кнопка {idx + 1}"
    if kind == "hat":
        arrows = {(0, 1): "↑", (0, -1): "↓", (-1, 0): "←", (1, 0): "→"}
        return f"Хрестовина {arrows.get(tuple(src['value']), str(src['value']))}"
    if kind == "axis":
        if "sign" in src:
            return f"Вісь {idx + 1}"
        return f"Вісь {idx + 1} {'+' if src.get('full', 1) > 0 else '−'}"
    return "?"
