"""Фоновый движок: читает физический джойстик и ведёт виртуальный Xbox 360 / DualShock 4 (ViGEmBus)."""

from __future__ import annotations

import importlib
import importlib.util
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import joy
from config import Settings
from joy import pygame
from kbm import KbmOutput

POLL_HZ = 500
DRIVER_RETRY_S = 2.0
MAX_PADS = 4

log = logging.getLogger("konsol")

BUTTON_NAMES = (
    "A", "B", "X", "Y", "LB", "RB", "BACK", "START", "LS", "RS", "GUIDE", "TOUCHPAD",
    "DPAD_UP", "DPAD_DOWN", "DPAD_LEFT", "DPAD_RIGHT",
)


@dataclass
class RawState:
    buttons: list[bool] = field(default_factory=list)
    axes: list[float] = field(default_factory=list)
    hats: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class OutState:
    pressed: frozenset[str] = frozenset()
    lt: float = 0.0
    rt: float = 0.0
    lx: float = 0.0
    ly: float = 0.0
    rx: float = 0.0
    ry: float = 0.0


@dataclass
class Snapshot:
    driver_ok: bool
    device: str | None
    virtual_mode: str | None  # "xbox"/"ps4", если виртуальный геймпад создан
    raw: RawState
    out: OutState
    device_id: tuple[int, int] | None = None


def vigem_installer() -> Path | None:
    spec = importlib.util.find_spec("vgamepad")
    if not spec or not spec.origin:
        return None
    msi = Path(spec.origin).parent / "win" / "vigem" / "install" / "x64" / "ViGEmBusSetup_x64.msi"
    return msi if msi.exists() else None


def read_raw(j: pygame.joystick.JoystickType) -> RawState:
    return RawState(
        buttons=[bool(j.get_button(i)) for i in range(j.get_numbuttons())],
        axes=[j.get_axis(i) for i in range(j.get_numaxes())],
        hats=[j.get_hat(i) for i in range(j.get_numhats())],
    )


def _axis_level(raw: float, src: dict[str, Any]) -> float:
    rest, full = src.get("rest", 0.0), src.get("full", float(src.get("sign", 1)))
    if full == rest:
        return 0.0
    return min(1.0, max(0.0, (raw - rest) / (full - rest)))


def level(raw: RawState, src: dict[str, Any] | None) -> float:
    if not src:
        return 0.0
    kind, idx = src["type"], src["index"]
    if kind == "button":
        return float(idx < len(raw.buttons) and raw.buttons[idx])
    if kind == "hat":
        if idx >= len(raw.hats):
            return 0.0
        hx, hy = raw.hats[idx]
        vx, vy = src["value"]
        return float((vx == 0 or hx == vx) and (vy == 0 or hy == vy))
    if kind == "axis" and idx < len(raw.axes):
        return _axis_level(raw.axes[idx], src)
    return 0.0


def _stick(raw: RawState, src: dict[str, Any] | None, deadzone: float) -> float:
    if not src or src.get("type") != "axis" or src["index"] >= len(raw.axes):
        return 0.0
    v = raw.axes[src["index"]] * src.get("sign", 1)
    if abs(v) < deadzone:
        return 0.0
    v = (abs(v) - deadzone) / (1 - deadzone) * (1 if v > 0 else -1)
    return max(-1.0, min(1.0, v))


def compute_output(raw: RawState, settings: Settings) -> OutState:
    m, dz = settings.mapping or {}, settings.deadzone
    return OutState(
        pressed=frozenset(n for n in BUTTON_NAMES if level(raw, m.get(n)) > 0.5),
        lt=level(raw, m.get("LT")),
        rt=level(raw, m.get("RT")),
        lx=_stick(raw, m.get("LX"), dz),
        ly=_stick(raw, m.get("LY"), dz),
        rx=_stick(raw, m.get("RX"), dz),
        ry=_stick(raw, m.get("RY"), dz),
    )


class VirtualPad:
    """Виртуальный геймпад для одного физического джойстика."""

    def __init__(self, vg: Any, j: pygame.joystick.JoystickType, mode: str, rumble: bool) -> None:
        self.vg, self.j, self.mode, self.rumble_enabled = vg, j, mode, rumble
        self.pad = vg.VDS4Gamepad() if mode == "ps4" else vg.VX360Gamepad()
        self._lock = threading.Lock()
        self._rumble = (0, 0)
        self._rumble_applied = (0, 0)
        if mode == "ps4":
            B, S = vg.DS4_BUTTONS, vg.DS4_SPECIAL_BUTTONS
            self.buttons = {
                "A": B.DS4_BUTTON_CROSS, "B": B.DS4_BUTTON_CIRCLE, "X": B.DS4_BUTTON_SQUARE,
                "Y": B.DS4_BUTTON_TRIANGLE, "LB": B.DS4_BUTTON_SHOULDER_LEFT, "RB": B.DS4_BUTTON_SHOULDER_RIGHT,
                "BACK": B.DS4_BUTTON_SHARE, "START": B.DS4_BUTTON_OPTIONS,
                "LS": B.DS4_BUTTON_THUMB_LEFT, "RS": B.DS4_BUTTON_THUMB_RIGHT,
            }
            self.special = {"GUIDE": S.DS4_SPECIAL_BUTTON_PS, "TOUCHPAD": S.DS4_SPECIAL_BUTTON_TOUCHPAD}
            D = vg.DS4_DPAD_DIRECTIONS
            self.dpad = {
                (0, 0): D.DS4_BUTTON_DPAD_NONE, (0, 1): D.DS4_BUTTON_DPAD_NORTH,
                (1, 1): D.DS4_BUTTON_DPAD_NORTHEAST, (1, 0): D.DS4_BUTTON_DPAD_EAST,
                (1, -1): D.DS4_BUTTON_DPAD_SOUTHEAST, (0, -1): D.DS4_BUTTON_DPAD_SOUTH,
                (-1, -1): D.DS4_BUTTON_DPAD_SOUTHWEST, (-1, 0): D.DS4_BUTTON_DPAD_WEST,
                (-1, 1): D.DS4_BUTTON_DPAD_NORTHWEST,
            }
        else:
            B = vg.XUSB_BUTTON
            self.buttons = {
                "A": B.XUSB_GAMEPAD_A, "B": B.XUSB_GAMEPAD_B, "X": B.XUSB_GAMEPAD_X, "Y": B.XUSB_GAMEPAD_Y,
                "LB": B.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": B.XUSB_GAMEPAD_RIGHT_SHOULDER,
                "BACK": B.XUSB_GAMEPAD_BACK, "START": B.XUSB_GAMEPAD_START,
                "LS": B.XUSB_GAMEPAD_LEFT_THUMB, "RS": B.XUSB_GAMEPAD_RIGHT_THUMB, "GUIDE": B.XUSB_GAMEPAD_GUIDE,
                "DPAD_UP": B.XUSB_GAMEPAD_DPAD_UP, "DPAD_DOWN": B.XUSB_GAMEPAD_DPAD_DOWN,
                "DPAD_LEFT": B.XUSB_GAMEPAD_DPAD_LEFT, "DPAD_RIGHT": B.XUSB_GAMEPAD_DPAD_RIGHT,
            }
            self.special = {}

        # vgamepad требует точную сигнатуру без аннотаций, поэтому обычная функция, а не метод.
        def on_rumble(client, target, large_motor, small_motor, led_number, user_data):  # noqa: ANN001, ANN202
            with self._lock:
                self._rumble = (large_motor, small_motor)

        self.pad.register_notification(callback_function=on_rumble)

    def apply(self, out: OutState) -> None:
        pad = self.pad
        for name, button in self.buttons.items():
            if name in out.pressed:
                pad.press_button(button=button)
            else:
                pad.release_button(button=button)
        pad.left_trigger_float(value_float=out.lt)
        pad.right_trigger_float(value_float=out.rt)
        if self.mode == "ps4":
            for name, button in self.special.items():
                if name in out.pressed:
                    pad.press_special_button(special_button=button)
                else:
                    pad.release_special_button(special_button=button)
            for name, button, value in (("LT", self.vg.DS4_BUTTONS.DS4_BUTTON_TRIGGER_LEFT, out.lt),
                                        ("RT", self.vg.DS4_BUTTONS.DS4_BUTTON_TRIGGER_RIGHT, out.rt)):
                if value > 0.1:
                    pad.press_button(button=button)
                else:
                    pad.release_button(button=button)
            p = out.pressed
            dx = ("DPAD_RIGHT" in p) - ("DPAD_LEFT" in p)
            dy = ("DPAD_UP" in p) - ("DPAD_DOWN" in p)
            pad.directional_pad(direction=self.dpad[(dx, dy)])
            # У DualShock 4 ось Y направлена вниз.
            pad.left_joystick_float(x_value_float=out.lx, y_value_float=-out.ly)
            pad.right_joystick_float(x_value_float=out.rx, y_value_float=-out.ry)
        else:
            pad.left_joystick_float(x_value_float=out.lx, y_value_float=out.ly)
            pad.right_joystick_float(x_value_float=out.rx, y_value_float=out.ry)
        pad.update()

        with self._lock:
            rumble = self._rumble if self.rumble_enabled else (0, 0)
        if rumble != self._rumble_applied:
            self._rumble_applied = rumble
            if rumble == (0, 0):
                self.j.stop_rumble()
            else:
                self.j.rumble(rumble[0] / 255, rumble[1] / 255, 0)

    def close(self) -> None:
        self.j.stop_rumble()
        self.pad.reset()
        self.pad.update()
        del self.pad


class Engine(threading.Thread):
    def __init__(self, settings: Settings) -> None:
        super().__init__(daemon=True, name="konsol-engine")
        self._lock = threading.Lock()
        self._settings = settings
        self._stop_event = threading.Event()
        self._snapshot = Snapshot(False, None, None, RawState(), OutState())
        self.paused = False  # во время назначения кнопки игра получает «ничего не нажато»

    def set_settings(self, settings: Settings) -> None:
        with self._lock:
            self._settings = replace(settings, mapping=dict(settings.mapping or {}), kbm=dict(settings.kbm or {}))

    def snapshot(self) -> Snapshot:
        with self._lock:
            return self._snapshot

    def stop(self) -> None:
        self._stop_event.set()

    def _try_driver(self) -> Any | None:
        try:
            return importlib.import_module("vgamepad")
        except Exception as exc:  # noqa: BLE001
            log.warning("ViGEmBus недоступен: %s", exc)
            return None

    def run(self) -> None:
        joy.init()
        vg = self._try_driver()
        next_driver_try = time.monotonic() + DRIVER_RETRY_S
        sticks: dict[int, pygame.joystick.JoystickType] = {}
        pads: dict[int, VirtualPad] = {}
        clock = pygame.time.Clock()
        kbm = KbmOutput()

        while not self._stop_event.is_set():
            for ev in pygame.event.get():
                if ev.type == pygame.JOYDEVICEADDED:
                    j = pygame.joystick.Joystick(ev.device_index)
                    if joy.is_physical(j):
                        sticks[j.get_instance_id()] = j
                        log.info("Подключён '%s'", j.get_name())
                elif ev.type == pygame.JOYDEVICEREMOVED:
                    sticks.pop(ev.instance_id, None)
                    if ev.instance_id in pads:
                        pads.pop(ev.instance_id).close()
                        log.info("Джойстик отключён, виртуальный геймпад удалён")

            with self._lock:
                settings = self._settings

            if vg is None and time.monotonic() >= next_driver_try:
                vg = self._try_driver()
                next_driver_try = time.monotonic() + DRIVER_RETRY_S
            kbm_mode = settings.mode == "kbm"
            if kbm_mode and pads:
                for pad in pads.values():
                    pad.close()
                pads.clear()
            if not kbm_mode:
                kbm.release_all()
            if vg is not None and not kbm_mode:
                for iid in list(pads):
                    if pads[iid].mode != settings.mode:
                        pads.pop(iid).close()
                for iid, j in sticks.items():
                    if iid not in pads and len(pads) < MAX_PADS:
                        try:
                            pads[iid] = VirtualPad(vg, j, settings.mode, settings.rumble)
                            log.info("Создан виртуальный %s для '%s'", settings.mode, j.get_name())
                        except Exception:
                            log.exception("Не удалось создать виртуальный геймпад")
                            time.sleep(1)
                    if iid in pads:
                        pads[iid].rumble_enabled = settings.rumble

            first: tuple[str, RawState, OutState] | None = None
            first_id: tuple[int, int] | None = None
            for iid, j in sticks.items():
                raw = read_raw(j)
                out = compute_output(raw, settings)
                if iid in pads:
                    pads[iid].apply(OutState() if self.paused else out)
                if first is None:
                    first = (j.get_name(), raw, out)
                    first_id = joy.usb_id(j)
            if kbm_mode:
                if first is None or self.paused:
                    kbm.release_all()
                else:
                    kbm.apply(first[2], settings.kbm or {}, settings.mouse_speed)

            with self._lock:
                self._snapshot = Snapshot(
                    driver_ok=vg is not None,
                    device=first[0] if first else None,
                    virtual_mode=settings.mode if pads or (kbm_mode and first) else None,
                    raw=first[1] if first else RawState(),
                    out=first[2] if first else OutState(),
                    device_id=first_id,
                )
            clock.tick(POLL_HZ)

        kbm.release_all()
        for pad in pads.values():
            pad.close()
