"""Совместимость со Steam: прячем настоящий джойстик от Steam и SDL-игр стандартной переменной SDL.

SDL_GAMECONTROLLER_IGNORE_DEVICES понимают сам Steam и игры на SDL: они перестают видеть
физический джойстик и работают только с виртуальным геймпадом Konsol (без двойных нажатий).
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import time
import winreg

ENV_KEY = r"Environment"
VAR = "SDL_GAMECONTROLLER_IGNORE_DEVICES"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def _fmt(vid: int, pid: int) -> str:
    return f"0x{vid:04x}/0x{pid:04x}"


def _read() -> list[str]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VAR)
    except FileNotFoundError:
        return []
    return [part.strip().lower() for part in str(value).split(",") if part.strip()]


def _write(parts: list[str]) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, ENV_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if parts:
            winreg.SetValueEx(key, VAR, 0, winreg.REG_SZ, ",".join(parts))
        else:
            try:
                winreg.DeleteValue(key, VAR)
            except FileNotFoundError:
                pass
    # Сообщаем Windows, что переменные окружения изменились (новые программы увидят сразу).
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 3000, ctypes.byref(result))


def is_hidden(vid: int, pid: int) -> bool:
    return _fmt(vid, pid) in _read()


def clear_all() -> None:
    if _read():
        _write([])


def any_hidden() -> bool:
    return bool(_read())


def set_hidden(vid: int, pid: int, hidden: bool) -> None:
    parts = [p for p in _read() if p != _fmt(vid, pid)]
    if hidden:
        parts.append(_fmt(vid, pid))
    _write(parts)


def steam_running() -> bool:
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq steam.exe", "/NH"], capture_output=True, text=True,
                         creationflags=CREATE_NO_WINDOW).stdout.lower()
    return "steam.exe" in out


def steam_exe() -> str | None:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            value, _ = winreg.QueryValueEx(key, "SteamExe")
            return str(value) if value else None
    except FileNotFoundError:
        return None


def restart_steam() -> bool:
    """Закрывает Steam и запускает заново уже с актуальной переменной. Блокирует до ~25 с."""
    exe = steam_exe()
    if not exe:
        return False
    if steam_running():
        os.startfile("steam://exit")
        deadline = time.monotonic() + 25
        while steam_running() and time.monotonic() < deadline:
            time.sleep(0.5)
    env = os.environ.copy()
    parts = _read()
    if parts:
        env[VAR] = ",".join(parts)
    else:
        env.pop(VAR, None)
    subprocess.Popen([exe], env=env, creationflags=DETACHED_PROCESS, close_fds=True)
    return True


def open_controller_settings() -> None:
    os.startfile("steam://open/settings/controller")
