"""Самоустановка exe в %LOCALAPPDATA%\\Programs\\Konsol, ярлыки, автозапуск и удаление."""

from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import sys
import winreg
from pathlib import Path

import autostart
from config import DATA_DIR

INSTALL_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Programs" / "Konsol"
INSTALLED_EXE = INSTALL_DIR / "Konsol.exe"
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008


def _powershell(script: str) -> None:
    encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                   creationflags=CREATE_NO_WINDOW, check=False)


def _shortcut_paths() -> list[Path]:
    desktop = Path(os.environ.get("USERPROFILE", Path.home())) / "Desktop"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            desktop = Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0]))
    except OSError:
        pass
    start_menu = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return [desktop / "Konsol.lnk", start_menu / "Konsol.lnk"]


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def is_installed_copy() -> bool:
    return Path(sys.executable).resolve() == INSTALLED_EXE.resolve()


def install() -> Path:
    """Копирует текущий exe в папку установки, делает ярлыки и включает автозапуск."""
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sys.executable, INSTALLED_EXE)
    lines = ["$s = New-Object -ComObject WScript.Shell"]
    for link in _shortcut_paths():
        link.parent.mkdir(parents=True, exist_ok=True)
        lines += [
            f"$l = $s.CreateShortcut('{link}')",
            f"$l.TargetPath = '{INSTALLED_EXE}'",
            f"$l.WorkingDirectory = '{INSTALL_DIR}'",
            "$l.Description = 'Konsol — будь-який джойстик як Xbox / PS4'",
            "$l.Save()",
        ]
    _powershell("\n".join(lines))
    autostart.set_enabled(True, INSTALLED_EXE)
    return INSTALLED_EXE


def launch(exe: Path) -> None:
    subprocess.Popen([str(exe)], creationflags=DETACHED_PROCESS, close_fds=True)


def vigem_product_code() -> str | None:
    root = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, root) as key:
        for i in range(winreg.QueryInfoKey(key)[0]):
            name = winreg.EnumKey(key, i)
            try:
                with winreg.OpenKey(key, name) as sub:
                    if "Virtual Gamepad Emulation Bus" in str(winreg.QueryValueEx(sub, "DisplayName")[0]):
                        return name
            except OSError:
                continue
    return None


def uninstall(remove_driver: bool) -> None:
    """Убирает автозапуск, ярлыки, настройки; папку с exe удаляет после выхода программы."""
    import steam

    autostart.set_enabled(False)
    steam.clear_all()
    for link in _shortcut_paths():
        link.unlink(missing_ok=True)
    if remove_driver and (code := vigem_product_code()):
        subprocess.Popen(["msiexec", "/x", code])
    logging.shutdown()  # иначе открытый konsol.log не даст удалить папку настроек
    shutil.rmtree(DATA_DIR, ignore_errors=True)
    if INSTALL_DIR.exists():
        subprocess.Popen(f'cmd /c timeout /t 3 /nobreak >nul & rmdir /s /q "{INSTALL_DIR}"',
                         creationflags=CREATE_NO_WINDOW | DETACHED_PROCESS, close_fds=True)
