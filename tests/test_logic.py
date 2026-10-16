from __future__ import annotations

import time

import pytest

import config
from binder import Binder, is_active, same_source
from config import DEFAULT_KBM, Settings, describe
from engine import OutState, RawState, compute_output
from joy import usb_id
from kbm import KbmOutput


def raw(buttons: dict[int, bool] | None = None, axes: list[float] | None = None,
        hat: tuple[int, int] = (0, 0)) -> RawState:
    b = [False] * 13
    for i, v in (buttons or {}).items():
        b[i] = v
    return RawState(buttons=b, axes=axes or [0.0, 0.0, 0.0, 0.0], hats=[hat])


def test_idle_gives_neutral_output() -> None:
    out = compute_output(raw(), Settings())
    assert out == OutState()


def test_default_mapping_buttons_and_hat() -> None:
    out = compute_output(raw({2: True, 7: True}, hat=(1, 1)), Settings())
    assert {"A", "DPAD_UP", "DPAD_RIGHT"} <= out.pressed
    assert out.rt == 1.0


def test_stick_y_is_inverted_and_deadzone_applies() -> None:
    s = Settings(deadzone=0.1)
    out = compute_output(raw(axes=[0.05, -1.0, 0.0, 0.0]), s)
    assert out.lx == 0.0
    assert out.ly == pytest.approx(1.0)


def test_axis_as_trigger_uses_rest_and_full() -> None:
    s = Settings()
    assert s.mapping is not None
    s.mapping["LT"] = {"type": "axis", "index": 2, "rest": -1.0, "full": 1.0}
    assert compute_output(raw(axes=[0, 0, 0.0, 0]), s).lt == pytest.approx(0.5)


def test_binder_detects_button_then_waits_release() -> None:
    b = Binder(["A", "B"], [0.0] * 4)
    assert b.tick(raw()) is None
    assert b.tick(raw({4: True})) == ("assign", {"type": "button", "index": 4})
    assert b.tick(raw({4: True})) is None
    assert b.tick(raw()) is None and b.key == "B"


def test_binder_detects_stick_direction() -> None:
    b = Binder(["LY"], [0.0] * 4)
    assert b.tick(raw(axes=[0, -0.9, 0, 0])) == ("assign", {"type": "axis", "index": 1, "sign": -1})


def test_binder_skip_and_done() -> None:
    b = Binder(["A"], [0.0] * 4)
    b.skip()
    assert b.tick(raw()) == ("skip", None)
    assert b.tick(raw()) == ("done", None)


def test_same_source() -> None:
    assert same_source({"type": "button", "index": 1}, {"type": "button", "index": 1})
    assert not same_source({"type": "hat", "index": 0, "value": [0, 1]}, {"type": "hat", "index": 0, "value": [1, 0]})


def test_is_active() -> None:
    assert is_active("A", OutState(pressed=frozenset({"A"})))
    assert is_active("LX", OutState(lx=-0.9))
    assert not is_active("RT", OutState(rt=0.1))


def test_describe() -> None:
    assert describe(None) == "— не призначено —"
    assert describe({"type": "button", "index": 0}) == "Кнопка 1"
    assert describe({"type": "hat", "index": 0, "value": [0, 1]}) == "Хрестовина ↑"


class _FakeJoy:
    def __init__(self, guid: str) -> None:
        self._guid = guid

    def get_guid(self) -> str:
        return self._guid


def test_usb_id_from_sdl_guid() -> None:
    assert usb_id(_FakeJoy("03008a51632500007505000000000000")) == (0x2563, 0x0575)
    assert usb_id(_FakeJoy("03008fe54c050000c405000000016800")) == (0x054C, 0x05C4)


def test_kbm_active_binds_for_stick_and_triggers() -> None:
    k = KbmOutput()
    binds = k._active_binds(OutState(ly=0.9, lx=-0.9, rt=1.0), DEFAULT_KBM)
    names = {b["name"] for b in binds.values()}
    assert names == {"W", "A", "ЛКМ"}


def test_settings_roundtrip(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(config, "SETTINGS_FILE", tmp_path / "settings.json")
    monkeypatch.setattr(config, "LEGACY_MAPPING_FILE", tmp_path / "mapping.json")
    s = Settings(mode="ps4", deadzone=0.12, mouse_speed=900)
    config.save_settings(s)
    loaded = config.load_settings()
    assert (loaded.mode, loaded.deadzone, loaded.mouse_speed) == ("ps4", 0.12, 900)
    assert loaded.kbm == DEFAULT_KBM


def test_binder_timeout_is_reasonable() -> None:
    b = Binder(["A"], [0.0])
    assert 0 < b.deadline - time.monotonic() <= 10.5
