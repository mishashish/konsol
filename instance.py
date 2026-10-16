"""Один экземпляр: второй запуск открывает окно первого (или просит его выйти при обновлении)."""

from __future__ import annotations

import ctypes
import threading
import time
from typing import Callable

_k32 = ctypes.windll.kernel32
_k32.CreateEventW.restype = ctypes.c_void_p
_k32.OpenEventW.restype = ctypes.c_void_p
_k32.CreateMutexW.restype = ctypes.c_void_p
_k32.OpenMutexW.restype = ctypes.c_void_p

MUTEX = "Local\\konsol_x360_app"
EVENTS = {"show": "Local\\konsol_show", "quit": "Local\\konsol_quit"}
EVENT_MODIFY_STATE, SYNCHRONIZE = 0x0002, 0x00100000
ERROR_ALREADY_EXISTS = 183
INFINITE = 0xFFFFFFFF

_mutex: int | None = None


def acquire() -> bool:
    """True, если мы первый экземпляр."""
    global _mutex
    _mutex = _k32.CreateMutexW(None, False, MUTEX)
    return _k32.GetLastError() != ERROR_ALREADY_EXISTS


def running() -> bool:
    handle = _k32.OpenMutexW(SYNCHRONIZE, False, MUTEX)
    if handle:
        _k32.CloseHandle(ctypes.c_void_p(handle))
        return True
    return False


def signal(name: str) -> bool:
    handle = _k32.OpenEventW(EVENT_MODIFY_STATE, False, EVENTS[name])
    if not handle:
        return False
    _k32.SetEvent(ctypes.c_void_p(handle))
    _k32.CloseHandle(ctypes.c_void_p(handle))
    return True


def wait_exit(timeout_s: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while running() and time.monotonic() < deadline:
        time.sleep(0.2)
    return not running()


def listen(on_event: Callable[[str], None]) -> None:
    """Фоновые потоки, которые ждут сигналы от других запусков."""
    for name, event_name in EVENTS.items():
        handle = _k32.CreateEventW(None, False, False, event_name)

        def worker(h: int = handle, n: str = name) -> None:
            while True:
                _k32.WaitForSingleObject(ctypes.c_void_p(h), INFINITE)
                on_event(n)

        threading.Thread(target=worker, daemon=True, name=f"konsol-{name}").start()
