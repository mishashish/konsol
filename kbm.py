"""Режим «клавиатура и мышь»: джойстик нажимает клавиши и двигает мышь через SendInput."""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from engine import OutState

STICK_KEY_THRESHOLD = 0.5
WHEEL_REPEAT_S = 0.12

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x8
MOUSEEVENTF_MOVE = 0x1
MOUSE_FLAGS = {
    "left": (0x2, 0x4, 0), "right": (0x8, 0x10, 0), "middle": (0x20, 0x40, 0),
    "x1": (0x80, 0x100, 1), "x2": (0x80, 0x100, 2),
}
MOUSEEVENTF_WHEEL = 0x800
EXTENDED_VK = {0x21, 0x22, 0x23, 0x24, 0x25, 0x26, 0x27, 0x28, 0x2D, 0x2E, 0x5B, 0x5C, 0x6F, 0xA3, 0xA5}

ULONG_PTR = ctypes.c_size_t


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD), ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = ctypes.windll.user32


def _send(*inputs: INPUT) -> None:
    arr = (INPUT * len(inputs))(*inputs)
    _user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))


def _key(vk: int, down: bool) -> INPUT:
    scan = _user32.MapVirtualKeyW(vk, 0)
    flags = KEYEVENTF_SCANCODE | (0 if down else KEYEVENTF_KEYUP) | (KEYEVENTF_EXTENDEDKEY if vk in EXTENDED_VK else 0)
    inp = INPUT(type=INPUT_KEYBOARD)
    inp.u.ki = KEYBDINPUT(wVk=0 if scan else vk, wScan=scan, dwFlags=flags if scan else flags & ~KEYEVENTF_SCANCODE)
    return inp


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    inp = INPUT(type=INPUT_MOUSE)
    inp.u.mi = MOUSEINPUT(dx=dx, dy=dy, mouseData=ctypes.c_ulong(data & 0xFFFFFFFF).value, dwFlags=flags)
    return inp


def _press(bind: dict[str, Any], down: bool) -> None:
    if "vk" in bind:
        _send(_key(int(bind["vk"]), down))
        return
    button = bind.get("mouse", "")
    if button in MOUSE_FLAGS:
        down_flag, up_flag, data = MOUSE_FLAGS[button]
        _send(_mouse(down_flag if down else up_flag, data=data))


def _bind_key(bind: dict[str, Any]) -> str:
    return f"vk{bind['vk']}" if "vk" in bind else f"m{bind.get('mouse')}"


class KbmOutput:
    """Превращает состояние геймпада в нажатия клавиш/мыши; отпускает всё при выключении."""

    def __init__(self) -> None:
        self._held: dict[str, dict[str, Any]] = {}
        self._wheel_next: dict[str, float] = {}
        self._frac = [0.0, 0.0]
        self._last = time.perf_counter()

    def _active_binds(self, out: OutState, keys: dict[str, Any]) -> dict[str, dict[str, Any]]:
        stick = {"LX+": out.lx, "LX-": -out.lx, "LY+": out.ly, "LY-": -out.ly}
        active: list[str] = [n for n in out.pressed]
        active += [n for n, v in (("LT", out.lt), ("RT", out.rt)) if v > 0.5]
        active += [n for n, v in stick.items() if v > STICK_KEY_THRESHOLD]
        binds: dict[str, dict[str, Any]] = {}
        for name in active:
            bind = keys.get(name)
            if bind:
                binds[_bind_key(bind)] = bind
        return binds

    def apply(self, out: OutState, keys: dict[str, Any], mouse_speed: float) -> None:
        now = time.perf_counter()
        dt = min(0.05, now - self._last)
        self._last = now

        wanted = self._active_binds(out, keys)
        for k in list(self._held):
            if k not in wanted:
                bind = self._held.pop(k)
                self._wheel_next.pop(k, None)
                _press(bind, False)
        for k, bind in wanted.items():
            wheel = bind.get("mouse") in ("wheel_up", "wheel_down")
            if wheel:
                if now >= self._wheel_next.get(k, 0.0):
                    _send(_mouse(MOUSEEVENTF_WHEEL, data=120 if bind["mouse"] == "wheel_up" else -120))
                    self._wheel_next[k] = now + WHEEL_REPEAT_S
                self._held[k] = bind
            elif k not in self._held:
                self._held[k] = bind
                _press(bind, True)

        # Правый стик -> мышь; квадратичная кривая даёт точность при малых отклонениях.
        for i, v in enumerate((out.rx, -out.ry)):
            self._frac[i] += (v * abs(v)) * mouse_speed * dt
        dx, dy = int(self._frac[0]), int(self._frac[1])
        if dx or dy:
            self._frac[0] -= dx
            self._frac[1] -= dy
            _send(_mouse(MOUSEEVENTF_MOVE, dx, dy))

    def release_all(self) -> None:
        for bind in self._held.values():
            if bind.get("mouse") not in ("wheel_up", "wheel_down"):
                _press(bind, False)
        self._held.clear()
        self._wheel_next.clear()
        self._frac = [0.0, 0.0]
