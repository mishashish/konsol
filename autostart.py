"""Автозапуск приложения при входе в Windows (HKCU\\...\\Run)."""

from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "KonsolX360Bridge"


def command(exe: Path | None = None) -> str:
    if exe is not None or getattr(sys, "frozen", False):
        return f'"{exe or sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    app = Path(__file__).with_name("app.py").resolve()
    return f'"{pythonw}" "{app}" --minimized'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return value == command()
    except FileNotFoundError:
        return False


def set_enabled(enabled: bool, exe: Path | None = None) -> None:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command(exe))
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
