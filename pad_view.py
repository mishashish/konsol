"""Рисунок геймпада на Canvas: живое состояние кнопок и подсветка «что нажать сейчас»."""

from __future__ import annotations

import tkinter as tk

from engine import OutState

CARD = "#1f2229"
BODY, BODY_EDGE = "#2b2f38", "#3a3f4b"
OFF, DIM, FG = "#3d424e", "#8a909c", "#eef0f3"
GREEN, RED, BLUE, YELLOW, PINK = "#3ccf6e", "#ef5b5b", "#4c8dff", "#f1c232", "#e07ad0"
HIGHLIGHT = "#ffb020"

WIDTH, HEIGHT = 470, 260

BODY_POINTS = (
    120, 48, 350, 48, 405, 62, 445, 150, 452, 218, 418, 248, 378, 236, 338, 196,
    132, 196, 92, 236, 52, 248, 18, 218, 25, 150, 65, 62,
)


def _layout(ps: bool) -> dict[str, tuple[float, float, float]]:
    """Ключ -> (x, y, радиус) элемента на рисунке."""
    if ps:
        pos = {"DPAD": (125, 105), "LSTICK": (180, 165), "RSTICK": (290, 165), "FACE": (345, 105),
               "BACK": (168, 70), "START": (302, 70), "GUIDE": (235, 140), "TOUCHPAD": (235, 88)}
    else:
        pos = {"LSTICK": (125, 105), "DPAD": (180, 165), "RSTICK": (290, 165), "FACE": (345, 105),
               "BACK": (205, 105), "GUIDE": (235, 80), "START": (265, 105), "TOUCHPAD": (235, 88)}
    dx, dy = pos["DPAD"]
    fx, fy = pos["FACE"]
    lx, ly = pos["LSTICK"]
    rx, ry = pos["RSTICK"]
    return {
        "DPAD_UP": (dx, dy - 20, 10), "DPAD_DOWN": (dx, dy + 20, 10),
        "DPAD_LEFT": (dx - 20, dy, 10), "DPAD_RIGHT": (dx + 20, dy, 10),
        "Y": (fx, fy - 24, 13), "B": (fx + 24, fy, 13), "A": (fx, fy + 24, 13), "X": (fx - 24, fy, 13),
        "LS": (lx, ly, 28), "LX": (lx, ly, 28), "LY": (lx, ly, 28),
        "RS": (rx, ry, 28), "RX": (rx, ry, 28), "RY": (rx, ry, 28),
        "BACK": (*pos["BACK"], 9), "START": (*pos["START"], 9), "GUIDE": (*pos["GUIDE"], 12),
        "TOUCHPAD": (*pos["TOUCHPAD"], 0),
        "LB": (120, 42, 0), "RB": (350, 42, 0), "LT": (120, 22, 0), "RT": (350, 22, 0),
    }


class PadView(tk.Canvas):
    def __init__(self, master: tk.Misc, bg: str = CARD, scale: float = 1.0) -> None:
        super().__init__(master, width=int(WIDTH * scale), height=int(HEIGHT * scale), bg=bg, highlightthickness=0)
        self.zoom = scale

    def draw(self, out: OutState, mode: str, highlight: str | None = None) -> None:
        ps = mode == "ps4"
        pos = _layout(ps)
        on = lambda key: GREEN if key in out.pressed else OFF  # noqa: E731
        small = ("Segoe UI", 8)
        self.delete("all")

        # Курки и бамперы
        for key, value, label in (("LT", out.lt, "L2" if ps else "LT"), ("RT", out.rt, "R2" if ps else "RT")):
            x, y, _ = pos[key]
            self.create_rectangle(x - 40, y - 6, x + 40, y + 6, fill=OFF, outline="")
            if value > 0.02:
                self.create_rectangle(x - 40, y - 6, x - 40 + 80 * value, y + 6, fill=GREEN, outline="")
            self.create_text(x + (52 if key == "RT" else -52), y, text=label, fill=DIM, font=small)
        for key, label in (("LB", "L1" if ps else "LB"), ("RB", "R1" if ps else "RB")):
            x, y, _ = pos[key]
            self.create_rectangle(x - 45, y - 7, x + 45, y + 7, fill=on(key), outline="")
            self.create_text(x, y, text=label, fill=FG, font=small)

        self.create_polygon(BODY_POINTS, smooth=True, fill=BODY, outline=BODY_EDGE, width=2)

        if ps:
            x, y, _ = pos["TOUCHPAD"]
            self.create_rectangle(x - 45, y - 26, x + 45, y + 26, fill=on("TOUCHPAD"), outline=BODY_EDGE)

        for key, sx, sy in (("LS", out.lx, out.ly), ("RS", out.rx, out.ry)):
            x, y, r = pos[key]
            self.create_oval(x - r, y - r, x + r, y + r, fill="#22252d", outline=BODY_EDGE, width=2)
            px, py = x + sx * 15, y - sy * 15
            self.create_oval(px - 17, py - 17, px + 17, py + 17, fill=on(key) if key in out.pressed else "#555b68",
                             outline="")

        for key in ("DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT"):
            x, y, r = pos[key]
            self.create_rectangle(x - r, y - r, x + r, y + r, fill=on(key), outline="")

        faces = {"Y": ("△", GREEN), "B": ("○", RED), "A": ("✕", BLUE), "X": ("□", PINK)} if ps else \
                {"Y": ("Y", YELLOW), "B": ("B", RED), "A": ("A", GREEN), "X": ("X", BLUE)}
        for key, (text, color) in faces.items():
            x, y, r = pos[key]
            pressed = key in out.pressed
            self.create_oval(x - r, y - r, x + r, y + r, fill=color if pressed else OFF, outline="")
            self.create_text(x, y, text=text, fill=FG if pressed else color, font=("Segoe UI Semibold", 10))

        labels = {"BACK": "Share" if ps else "Back", "START": "Options" if ps else "Start",
                  "GUIDE": "PS" if ps else "Xbox"}
        for key, label in labels.items():
            x, y, r = pos[key]
            self.create_oval(x - r, y - r, x + r, y + r, fill=on(key), outline="")
            self.create_text(x, y + r + 9, text=label, fill=DIM, font=small)

        if highlight and highlight in pos:
            x, y, r = pos[highlight]
            if highlight in ("LT", "RT", "LB", "RB"):
                w, h = 50, 11
                self.create_rectangle(x - w, y - h, x + w, y + h, outline=HIGHLIGHT, width=3)
            elif highlight == "TOUCHPAD":
                self.create_rectangle(x - 50, y - 31, x + 50, y + 31, outline=HIGHLIGHT, width=3)
            else:
                r += 6
                self.create_oval(x - r, y - r, x + r, y + r, outline=HIGHLIGHT, width=3)
            arrow = {"LX": "→", "RX": "→", "LY": "↑", "RY": "↑"}.get(highlight)
            if arrow:
                self.create_text(x + (r + 14 if arrow == "→" else 0), y - (r + 12 if arrow == "↑" else 0),
                                 text=arrow, fill=HIGHLIGHT, font=("Segoe UI Semibold", 16))
        if self.zoom != 1.0:
            self.scale("all", 0, 0, self.zoom, self.zoom)
