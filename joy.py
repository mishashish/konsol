"""Чтение физического (DirectInput) джойстика через pygame/SDL."""

from __future__ import annotations

import os

# Без этого SDL не отдаёт события джойстика, когда окно не в фокусе (а окна у нас нет).
os.environ["SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS"] = "1"
os.environ["PYGAME_HIDE_SUPPORT_PROMPT"] = "1"
# «Режим для Steam» прячет настоящий джойстик от SDL-программ — но не от нас самих.
os.environ.pop("SDL_GAMECONTROLLER_IGNORE_DEVICES", None)

import pygame  # noqa: E402

VIRTUAL_NAME_MARKERS = ("xbox", "xinput", "x-box", "x360", "ps4 controller")
# VID:PID, которые выдаёт ViGEmBus: Xbox 360 и DualShock 4.
VIRTUAL_IDS = {(0x045E, 0x028E), (0x054C, 0x05C4)}


def init() -> None:
    pygame.display.init()  # нужен для очереди событий (подключение/отключение)
    pygame.joystick.init()


def usb_id(joy: pygame.joystick.JoystickType) -> tuple[int, int]:
    """VID/PID из SDL GUID (байты 4-5 и 8-9, little-endian)."""
    g = joy.get_guid()
    try:
        return int(g[10:12] + g[8:10], 16), int(g[18:20] + g[16:18], 16)
    except ValueError:
        return 0, 0


def is_physical(joy: pygame.joystick.JoystickType) -> bool:
    """Отсекаем Xbox/DS4-устройства, иначе мы подхватим собственный виртуальный геймпад."""
    name = joy.get_name().lower()
    if any(marker in name for marker in VIRTUAL_NAME_MARKERS):
        return False
    return usb_id(joy) not in VIRTUAL_IDS


def find_physical() -> pygame.joystick.JoystickType | None:
    for i in range(pygame.joystick.get_count()):
        joy = pygame.joystick.Joystick(i)
        if is_physical(joy):
            return joy
    return None
