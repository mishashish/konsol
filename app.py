"""Konsol — окно приложения: превращает любой USB-джойстик в Xbox 360, PS4 или клавиатуру с мышью."""

from __future__ import annotations

import copy
import ctypes
import logging
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox
from typing import Any

import customtkinter as ctk
from PIL import Image, ImageDraw

import autostart
import installer
import instance
import steam
from binder import Binder, is_active, same_source
from config import (DATA_DIR, DEFAULT_KBM, DEFAULT_MAPPING, MODES, TARGETS, VERSION, describe, load_settings,
                    save_settings)
from engine import Engine, OutState, Snapshot, vigem_installer
from pad_view import CARD, PadView

APP_NAME = "Konsol"
BG, SIDEBAR, ROW = "#15171c", "#1a1d23", "#262a32"
FG, DIM = "#eef0f3", "#8a909c"
GREEN, RED, ACCENT, ACCENT_HOVER, ORANGE = "#3ccf6e", "#ef5b5b", "#4c8dff", "#3a73d9", "#ffb020"
PILL, PILL_ACTIVE = "#323743", "#2f6f45"
ICON_FILE = DATA_DIR / "konsol.ico"

MODE_INFO = {
    "xbox": ("🎮", "Для більшості ігор з геймпадом: Fortnite, GTA V, FIFA, Minecraft, Rocket League…"),
    "ps4": ("🎮", "Гра покаже кнопки PlayStation (✕ ○ □ △). Якщо гра не реагує — обери Xbox 360."),
    "kbm": ("⌨", "Для ігор БЕЗ підтримки геймпада: лівий стік = WASD, правий = миша, курки = кліки.\n"
                 "Клавіші змінюються у вкладці «Клавіші». Гра має бути на передньому плані."),
}

log = logging.getLogger("konsol")


def make_icon(size: int = 64) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    s = size / 64
    d.rounded_rectangle((4 * s, 16 * s, 60 * s, 48 * s), radius=14 * s, fill=(76, 141, 255))
    d.rectangle((14 * s, 29 * s, 26 * s, 35 * s), fill="white")
    d.rectangle((17 * s, 26 * s, 23 * s, 38 * s), fill="white")
    for cx, cy in ((44, 27), (50, 33), (44, 39), (38, 33)):
        d.ellipse(((cx - 3) * s, (cy - 3) * s, (cx + 3) * s, (cy + 3) * s), fill="white")
    return img


def font(size: int = 13, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI", size=size, weight="bold" if bold else "normal")


class App:
    def __init__(self, start_hidden: bool) -> None:
        self.settings = load_settings()
        self.engine = Engine(self.settings)
        self.engine.start()
        self.events: queue.Queue[str] = queue.Queue()
        self.binder: Binder | None = None
        self.key_capture: str | None = None
        self.tray: Any = None
        self._cache: dict[int, dict[str, Any]] = {}

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        self.root = ctk.CTk(fg_color=BG)
        self.root.title(APP_NAME)
        self.root.geometry("980x640")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        make_icon(256).save(ICON_FILE, sizes=[(16, 16), (32, 32), (48, 48), (256, 256)])
        # customtkinter ставит свою иконку с задержкой — перебиваем после него.
        self.root.after(300, lambda: self.root.iconbitmap(str(ICON_FILE)))

        self._build_sidebar()
        self.content = ctk.CTkFrame(self.root, fg_color=BG, corner_radius=0)
        self.content.pack(side="left", fill="both", expand=True, padx=20, pady=16)
        self.pages: dict[str, ctk.CTkFrame] = {
            "home": self._build_home(),
            "layout": self._build_layout(),
            "keys": self._build_keys(),
            "steam": self._build_steam(),
            "settings": self._build_settings(),
        }
        self.show_page("home")
        self._last_device: str | None = None
        self._start_tray()
        instance.listen(self.events.put)
        if start_hidden:
            self.root.withdraw()
        self.root.after(30, self._refresh)

    # ================= helpers =================
    def _set(self, widget: Any, **kw: Any) -> None:
        """configure только при изменении — иначе customtkinter перерисовывается 30 раз в секунду."""
        cache = self._cache.setdefault(id(widget), {})
        changed = {k: v for k, v in kw.items() if cache.get(k) != v}
        if changed:
            widget.configure(**changed)
            cache.update(changed)

    def _card(self, parent: Any, **kw: Any) -> ctk.CTkFrame:
        return ctk.CTkFrame(parent, fg_color=CARD, corner_radius=14, **kw)

    def _title(self, parent: Any, text: str, sub: str = "") -> None:
        ctk.CTkLabel(parent, text=text, font=font(22, True), text_color=FG, anchor="w").pack(
            fill="x", pady=(0, 0 if sub else 12))
        if sub:
            ctk.CTkLabel(parent, text=sub, font=font(13), text_color=DIM, anchor="w",
                         justify="left").pack(fill="x", pady=(0, 12))

    # ================= sidebar =================
    def _build_sidebar(self) -> None:
        bar = ctk.CTkFrame(self.root, fg_color=SIDEBAR, corner_radius=0, width=200)
        bar.pack(side="left", fill="y")
        bar.pack_propagate(False)
        ctk.CTkLabel(bar, text="🎮  Konsol", font=font(20, True), text_color=FG).pack(pady=(22, 24), padx=18,
                                                                                      anchor="w")
        self.nav: dict[str, ctk.CTkButton] = {}
        for key, text in (("home", "🏠   Головна"), ("layout", "🕹   Розкладка"),
                          ("keys", "⌨   Клавіші"), ("steam", "🎮   Steam"), ("settings", "⚙   Налаштування")):
            btn = ctk.CTkButton(bar, text=text, anchor="w", height=40, corner_radius=10, font=font(14),
                                fg_color="transparent", hover_color=ROW, text_color=FG,
                                command=lambda k=key: self.show_page(k))
            btn.pack(fill="x", padx=12, pady=3)
            self.nav[key] = btn

        ctk.CTkFrame(bar, fg_color="transparent").pack(fill="both", expand=True)
        self.side_status = ctk.CTkLabel(bar, text="", font=font(12), text_color=DIM, justify="left", anchor="w")
        self.side_status.pack(fill="x", padx=18, pady=(0, 6))
        ctk.CTkLabel(bar, text="Хрестик вікна — згорнути\nв трей (усе працює)",
                     font=font(11), text_color=DIM, justify="left", anchor="w").pack(fill="x", padx=18, pady=(0, 18))

    def show_page(self, key: str) -> None:
        if self.binder is not None and key != "layout":
            self.cancel_binding()
        if self.key_capture is not None:
            self.cancel_key_capture()
        for name, page in self.pages.items():
            page.pack_forget()
            self.nav[name].configure(fg_color=ACCENT if name == key else "transparent",
                                     hover_color=ACCENT_HOVER if name == key else ROW)
        self.pages[key].pack(fill="both", expand=True)
        self.current_page = key

    # ================= главная =================
    def _build_home(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")

        self.hero = self._card(page)
        self.hero.pack(fill="x")
        hero_in = ctk.CTkFrame(self.hero, fg_color="transparent")
        hero_in.pack(fill="x", padx=20, pady=16)
        self.hero_dot = ctk.CTkLabel(hero_in, text="●", font=font(30), text_color=RED, width=30)
        self.hero_dot.pack(side="left", padx=(0, 14))
        texts = ctk.CTkFrame(hero_in, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True)
        self.hero_title = ctk.CTkLabel(texts, text="…", font=font(20, True), text_color=FG, anchor="w")
        self.hero_title.pack(fill="x")
        self.hero_sub = ctk.CTkLabel(texts, text="", font=font(13), text_color=DIM, anchor="w", justify="left")
        self.hero_sub.pack(fill="x")
        self.hero_btn = ctk.CTkButton(hero_in, text="Встановити драйвер", height=38, corner_radius=10,
                                      font=font(13, True), command=self.install_driver)

        mode_card = self._card(page)
        mode_card.pack(fill="x", pady=14)
        ctk.CTkLabel(mode_card, text="Чим джойстик буде для ігор", font=font(15, True), text_color=FG,
                     anchor="w").pack(fill="x", padx=20, pady=(14, 8))
        self.mode_names = {title: key for key, title in MODES.items()}
        self.mode_switch = ctk.CTkSegmentedButton(mode_card, values=list(MODES.values()), height=40,
                                                  font=font(14, True), corner_radius=10,
                                                  command=lambda title: self.change_mode(self.mode_names[title]))
        self.mode_switch.set(MODES[self.settings.mode])
        self.mode_switch.pack(fill="x", padx=20)
        self.mode_desc = ctk.CTkLabel(mode_card, text="", font=font(13), text_color=DIM, anchor="w", justify="left")
        self.mode_desc.pack(fill="x", padx=20, pady=(10, 14))

        pad_card = self._card(page)
        pad_card.pack(fill="both", expand=True)
        top = ctk.CTkFrame(pad_card, fg_color="transparent")
        top.pack(fill="x", padx=20, pady=(12, 0))
        ctk.CTkLabel(top, text="Перевірка: тисни кнопки на джойстику", font=font(15, True), text_color=FG,
                     anchor="w").pack(side="left")
        ctk.CTkButton(top, text="Кнопки переплутані? → Розкладка", height=30, corner_radius=8, font=font(12),
                      fg_color=ROW, hover_color=PILL, command=lambda: self.show_page("layout")).pack(side="right")
        self.home_pad = PadView(pad_card)
        self.home_pad.pack(pady=(4, 10))
        return page

    # ================= раскладка =================
    def _build_layout(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._title(page, "Розкладка джойстика",
                    "Якщо в грі кнопки переплутані — пройди швидке налаштування: програма підсвітить кнопку,\n"
                    "а ти натиснеш її на джойстику. Потрібно один раз.")

        wizard = self._card(page)
        wizard.pack(fill="x")
        inner = ctk.CTkFrame(wizard, fg_color="transparent")
        inner.pack(fill="x", padx=16, pady=12)
        self.layout_pad = PadView(inner, scale=0.8)
        self.layout_pad.pack(side="left")
        side = ctk.CTkFrame(inner, fg_color="transparent")
        side.pack(side="left", fill="both", expand=True, padx=(16, 0))
        self.wiz_step = ctk.CTkLabel(side, text="", font=font(12), text_color=DIM, anchor="w")
        self.wiz_step.pack(fill="x", pady=(10, 0))
        self.wiz_text = ctk.CTkLabel(side, text="Швидке налаштування\nзайме близько хвилини", font=font(18, True),
                                     text_color=FG, anchor="w", justify="left", wraplength=300)
        self.wiz_text.pack(fill="x", pady=(4, 4))
        self.wiz_hint = ctk.CTkLabel(side, text="", font=font(12), text_color=DIM, anchor="w", justify="left",
                                     wraplength=300)
        self.wiz_hint.pack(fill="x")
        buttons = ctk.CTkFrame(side, fg_color="transparent")
        buttons.pack(fill="x", side="bottom", pady=(0, 6))
        self.wiz_start = ctk.CTkButton(buttons, text="▶  Почати налаштування", height=40, corner_radius=10,
                                       font=font(14, True), command=self.bind_all)
        self.wiz_start.pack(side="left")
        self.wiz_skip = ctk.CTkButton(buttons, text="Пропустити", height=40, corner_radius=10, font=font(13),
                                      fg_color=ROW, hover_color=PILL,
                                      command=lambda: self.binder and self.binder.skip())

        list_head = ctk.CTkFrame(page, fg_color="transparent")
        list_head.pack(fill="x", pady=(14, 4))
        ctk.CTkLabel(list_head, text="Або зміни одну кнопку:", font=font(14, True), text_color=FG).pack(side="left")
        ctk.CTkButton(list_head, text="Скинути розкладку", height=28, corner_radius=8, font=font(12),
                      fg_color=ROW, hover_color=PILL, command=self.reset_mapping).pack(side="right")

        scroll = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14)
        scroll.pack(fill="both", expand=True)
        self.rows: dict[str, tuple[ctk.CTkFrame, ctk.CTkLabel, ctk.CTkLabel, ctk.CTkButton]] = {}
        for target in TARGETS:
            row = ctk.CTkFrame(scroll, fg_color=ROW, corner_radius=8, height=36)
            row.pack(fill="x", padx=6, pady=2)
            name = ctk.CTkLabel(row, text="", font=font(13), text_color=FG, anchor="w", width=220)
            name.pack(side="left", padx=12)
            bound = ctk.CTkLabel(row, text="", font=font(12), text_color=FG, fg_color=PILL, corner_radius=6,
                                 width=170, height=26)
            bound.pack(side="left", padx=6, pady=5)
            ctk.CTkButton(row, text="✕", width=30, height=26, corner_radius=6, font=font(12), fg_color="transparent",
                          hover_color=PILL, text_color=DIM,
                          command=lambda k=target.key: self.clear_binding(k)).pack(side="right", padx=(0, 8))
            btn = ctk.CTkButton(row, text="Змінити", width=100, height=26, corner_radius=6, font=font(12),
                                command=lambda k=target.key: self.start_binding([k]))
            btn.pack(side="right", padx=4)
            self.rows[target.key] = (row, name, bound, btn)
        return page

    # ================= клавиши =================
    def _build_keys(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._title(page, "Клавіші для ігор без геймпада",
                    "Працює в режимі «Клавіатура і миша». Клацни по клавіші і натисни нову на клавіатурі\n"
                    "(або клацни в синій смузі потрібною кнопкою миші). Правий клік по клавіші — очистити.")
        self.keys_notice = self._card(page)
        notice_in = ctk.CTkFrame(self.keys_notice, fg_color="transparent")
        notice_in.pack(fill="x", padx=16, pady=10)
        ctk.CTkLabel(notice_in, text="Зараз обрано інший режим — ці клавіші не використовуються.",
                     font=font(13), text_color=ORANGE).pack(side="left")
        ctk.CTkButton(notice_in, text="Увімкнути «Клавіатура і миша»", height=30, corner_radius=8, font=font(12, True),
                      command=lambda: self.change_mode("kbm")).pack(side="right")

        self.capture_zone = ctk.CTkLabel(page, text="⌨  Натисни клавішу  —  або клацни ТУТ потрібною кнопкою миші / "
                                                    "покрути коліщатко", font=font(14, True), text_color="white",
                                         fg_color=ACCENT, corner_radius=12, height=56, cursor="hand2")
        for seq, button in (("<Button-1>", "left"), ("<Button-2>", "middle"), ("<Button-3>", "right"),
                            ("<Button-4>", "x1"), ("<Button-5>", "x2")):
            self.capture_zone.bind(seq, lambda _e, b=button: self._assign_mouse(b))
        self.capture_zone.bind("<MouseWheel>",
                               lambda e: self._assign_mouse("wheel_up" if e.delta > 0 else "wheel_down"))

        head = ctk.CTkFrame(page, fg_color="transparent")
        head.pack(fill="x", pady=(10, 4))
        self.keys_head = head
        ctk.CTkLabel(head, text="Кнопка джойстика  →  клавіша в грі", font=font(14, True),
                     text_color=FG).pack(side="left")
        ctk.CTkButton(head, text="Скинути клавіші", height=28, corner_radius=8, font=font(12), fg_color=ROW,
                      hover_color=PILL, command=self.reset_keys).pack(side="right")

        scroll = ctk.CTkScrollableFrame(page, fg_color=CARD, corner_radius=14)
        scroll.pack(fill="both", expand=True)
        self.keys_scroll = scroll
        self.key_buttons: dict[str, tuple[ctk.CTkButton, str]] = {}
        for target in TARGETS:
            row = ctk.CTkFrame(scroll, fg_color=ROW, corner_radius=8, height=36)
            row.pack(fill="x", padx=6, pady=2)
            label = target.xbox if target.key != "TOUCHPAD" else "Додаткова кнопка (тачпад)"
            label = {"LX": "Лівий стік  ← →", "LY": "Лівий стік  ↑ ↓",
                     "RX": "Правий стік  ← →", "RY": "Правий стік  ↑ ↓"}.get(target.key, label)
            ctk.CTkLabel(row, text=label, font=font(13), text_color=FG, anchor="w", width=240).pack(side="left",
                                                                                                  padx=12)
            if target.key in ("RX", "RY"):
                ctk.CTkLabel(row, text="🖱  рух миші", font=font(12), text_color=DIM).pack(side="left", pady=5)
                continue
            slots = {"LX": (("LX-", "←"), ("LX+", "→")), "LY": (("LY+", "↑"), ("LY-", "↓"))}.get(
                target.key, ((target.key, ""),))
            for kbm_key, arrow in slots:
                kb = ctk.CTkButton(row, text="", width=120 if arrow else 246, height=26, corner_radius=6,
                                   font=font(12, True), fg_color=PILL, hover_color=ACCENT_HOVER,
                                   command=lambda k=kbm_key: self.start_key_capture(k))
                kb.bind("<Button-3>", lambda _e, k=kbm_key: self.clear_key(k))
                kb.pack(side="left", padx=(0, 6), pady=5)
                self.key_buttons[kbm_key] = (kb, arrow)
        return page

    # ================= steam =================
    def _build_steam(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._title(page, "Ігри Steam", "Щоб Steam і ігри бачили лише геймпад Konsol — без подвійних натискань "
                                        "і плутанини\nзі справжнім джойстиком.")

        card = self._card(page)
        card.pack(fill="x")
        self.steam_switch = self._switch_row(
            card, "Режим для Steam (рекомендовано)",
            "Steam і ігри перестануть бачити справжній джойстик — лишиться лише віртуальний.",
            steam.any_hidden(), self.toggle_steam_mode)
        bottom = ctk.CTkFrame(card, fg_color="transparent")
        bottom.pack(fill="x", padx=20, pady=(0, 14))
        self.steam_status = ctk.CTkLabel(bottom, text="", font=font(12), text_color=DIM, anchor="w",
                                         justify="left")
        self.steam_status.pack(side="left", fill="x", expand=True)
        self.steam_restart = ctk.CTkButton(bottom, text="⟳  Перезапустити Steam", height=34, corner_radius=10,
                                           font=font(13, True), command=self.restart_steam)
        self.steam_restart.pack(side="right")

        guide = self._card(page)
        guide.pack(fill="x", pady=14)
        head = ctk.CTkFrame(guide, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(14, 6))
        ctk.CTkLabel(head, text="Один раз перевір у самому Steam", font=font(15, True),
                     text_color=FG).pack(side="left")
        ctk.CTkButton(head, text="Відкрити налаштування контролера Steam", height=32, corner_radius=8, font=font(12),
                      fg_color=ROW, hover_color=PILL, command=steam.open_controller_settings).pack(side="right")
        steps = (
            "1.  Steam → Налаштування → Контролер.",
            "2.  «Підтримка звичайних геймпадів» (Generic Gamepad) — ВИМК.",
            "3.  Режим Xbox 360 у Konsol → «Підтримка Xbox» у Steam можна лишити як є.\n"
            "     Режим PlayStation 4 → «Підтримка PlayStation» — УВІМК.",
            "4.  Не працює в одній грі? ПКМ по грі → Властивості → Контролер →\n"
            "     спробуй «Увімкнути Steam Input», а якщо стало гірше — «Вимкнути».",
        )
        for step in steps:
            ctk.CTkLabel(guide, text=step, font=font(13), text_color=FG, anchor="w",
                         justify="left").pack(fill="x", padx=20, pady=2)
        ctk.CTkLabel(guide, text="Для Steam найкраще режим Xbox 360 — його розуміють майже всі ігри.",
                     font=font(12), text_color=DIM, anchor="w").pack(fill="x", padx=20, pady=(8, 14))
        return page

    def _update_steam(self, snap: Snapshot) -> None:
        if snap.device_id is None:
            text, color = "Підключи джойстик, щоб увімкнути режим.", DIM
        elif steam.is_hidden(*snap.device_id):
            text, color = (f"Увімкнено: Steam не бачить «{snap.device}».\n"
                           "Якщо Steam уже був відкритий — перезапусти його кнопкою справа."), GREEN
        else:
            text, color = "Вимкнено: Steam бачить і справжній джойстик, і віртуальний.", ORANGE
        self._set(self.steam_status, text=text, text_color=color)

    def toggle_steam_mode(self) -> None:
        snap = self.engine.snapshot()
        enabled = bool(self.steam_switch.get())
        if snap.device_id is None:
            messagebox.showinfo(APP_NAME, "Спочатку підключи джойстик.")
            self.steam_switch.deselect() if enabled else self.steam_switch.select()
            return
        steam.set_hidden(*snap.device_id, enabled)
        if steam.steam_running() and messagebox.askyesno(
                APP_NAME, "Steam зараз відкритий. Перезапустити його, щоб зміни застосувалися?"):
            self.restart_steam()

    def restart_steam(self) -> None:
        self._set(self.steam_restart, text="Перезапускаю…", state="disabled")

        def work() -> None:
            ok = steam.restart_steam()
            self.events.put("steam_ok" if ok else "steam_fail")

        threading.Thread(target=work, daemon=True).start()

    # ================= настройки =================
    def _build_settings(self) -> ctk.CTkFrame:
        page = ctk.CTkFrame(self.content, fg_color="transparent")
        self._title(page, "Налаштування")

        sticks = self._card(page)
        sticks.pack(fill="x")
        self.deadzone_slider = self._slider_row(
            sticks, "Мертва зона стіків", "Якщо персонаж сам іде або камера повзе — збільш (зазвичай 8–12%).",
            0, 40, round(self.settings.deadzone * 100), self.change_deadzone, "%")
        self.mouse_slider = self._slider_row(
            sticks, "Швидкість миші", "Режим «Клавіатура і миша»: швидкість правого стіка.",
            200, 4000, self.settings.mouse_speed, self.change_mouse_speed, "", step=100)

        other = self._card(page)
        other.pack(fill="x", pady=14)
        self.rumble_switch = self._switch_row(other, "Вібрація", "Якщо джойстик її підтримує.",
                                              self.settings.rumble, self.change_rumble)
        self.autostart_switch = self._switch_row(other, "Запускати разом із Windows",
                                                 "Програма стартує в треї, джойстик одразу працює.",
                                                 autostart.is_enabled(), self.toggle_autostart)

        tools = ctk.CTkFrame(page, fg_color="transparent")
        tools.pack(fill="x")
        ctk.CTkButton(tools, text="Перевірка геймпада Windows", height=36, corner_radius=10, font=font(13),
                      fg_color=ROW, hover_color=PILL,
                      command=lambda: subprocess.Popen(["control", "joy.cpl"])).pack(side="left")
        ctk.CTkButton(tools, text="Скинути все за замовчуванням", height=36, corner_radius=10, font=font(13),
                      fg_color=ROW, hover_color=PILL, command=self.reset_all).pack(side="left", padx=10)
        ctk.CTkButton(tools, text="Видалити Konsol", height=36, corner_radius=10, font=font(13),
                      fg_color=ROW, hover_color="#5a2a2a", text_color=RED,
                      command=self.uninstall).pack(side="right")
        ctk.CTkLabel(page, text="Якщо стіки не працюють — натисни на джойстику кнопку ANALOG / MODE.",
                     font=font(12), text_color=DIM, anchor="w").pack(fill="x", pady=(14, 0))
        where = installer.INSTALL_DIR if installer.is_installed_copy() else "запущено не з папки встановлення"
        ctk.CTkLabel(page, text=f"Konsol {VERSION}  ·  {where}", font=font(11), text_color=DIM,
                     anchor="w").pack(fill="x", side="bottom")
        return page

    def _slider_row(self, parent: Any, title: str, sub: str, lo: int, hi: int, value: int,
                    command: Any, unit: str, step: int = 1) -> tuple[ctk.CTkSlider, ctk.CTkLabel, str]:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=12)
        texts = ctk.CTkFrame(row, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(texts, text=title, font=font(14, True), text_color=FG, anchor="w").pack(fill="x")
        ctk.CTkLabel(texts, text=sub, font=font(12), text_color=DIM, anchor="w").pack(fill="x")
        value_label = ctk.CTkLabel(row, text=f"{value}{unit}", font=font(14, True), text_color=FG, width=60)
        value_label.pack(side="right")
        slider = ctk.CTkSlider(row, from_=lo, to=hi, number_of_steps=(hi - lo) // step, width=260,
                               command=lambda v: command(int(round(v)), value_label, unit))
        slider.set(value)
        slider.pack(side="right", padx=10)
        return slider, value_label, unit

    def _switch_row(self, parent: Any, title: str, sub: str, value: bool, command: Any) -> ctk.CTkSwitch:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=20, pady=10)
        texts = ctk.CTkFrame(row, fg_color="transparent")
        texts.pack(side="left", fill="x", expand=True)
        ctk.CTkLabel(texts, text=title, font=font(14, True), text_color=FG, anchor="w").pack(fill="x")
        ctk.CTkLabel(texts, text=sub, font=font(12), text_color=DIM, anchor="w").pack(fill="x")
        switch = ctk.CTkSwitch(row, text="", command=command, switch_width=46, switch_height=24)
        switch.select() if value else switch.deselect()
        switch.pack(side="right")
        return switch

    # ================= обновление =================
    def _refresh(self) -> None:
        while not self.events.empty():
            event = self.events.get()
            if event == "show":
                self.show()
            elif event == "quit":
                self.quit()
                return
            elif event.startswith("mode:"):
                self.change_mode(event.split(":", 1)[1])
            elif event in ("steam_ok", "steam_fail"):
                self._set(self.steam_restart, text="⟳  Перезапустити Steam", state="normal")
                if event == "steam_fail":
                    messagebox.showerror(APP_NAME, "Не знайшов Steam на цьому комп'ютері.")

        snap = self.engine.snapshot()
        if snap.virtual_mode is not None or snap.device is None:
            self._notify_device(snap)
        if self.binder is not None:
            self._tick_binder(snap)
        if self.root.state() != "withdrawn":
            self._update_status(snap)
            mode = self.settings.mode
            if self.current_page == "home":
                self.home_pad.draw(snap.out, mode)
            elif self.current_page == "layout":
                self.layout_pad.draw(snap.out, mode, self.binder.key if self.binder else None)
                self._update_rows(snap.out)
            elif self.current_page == "keys":
                self._update_keys()
            elif self.current_page == "steam":
                self._update_steam(snap)
        self.root.after(30, self._refresh)

    def _update_status(self, snap: Snapshot) -> None:
        mode = self.settings.mode
        need_driver = not snap.driver_ok and mode != "kbm"
        if need_driver:
            state = (RED, "Потрібен драйвер", "Без нього ігри не побачать геймпад. Встановлення — один клік.")
        elif snap.device is None:
            state = (ORANGE, "Підключи джойстик", "Встроми провід у USB — усе увімкнеться само.")
        elif snap.virtual_mode is None:
            state = (ORANGE, "Запускаю…", "Створюю віртуальний геймпад.")
        elif mode == "kbm":
            state = (GREEN, "Усе готово — можна грати", f"{snap.device}  →  клавіатура і миша")
        else:
            state = (GREEN, "Усе готово — можна грати", f"{snap.device}  →  ігри бачать {MODES[mode]}")
        color, title, sub = state
        self._set(self.hero_dot, text_color=color)
        self._set(self.hero_title, text=title)
        self._set(self.hero_sub, text=sub)
        if need_driver and not self.hero_btn.winfo_ismapped():
            self.hero_btn.pack(side="right")
        elif not need_driver and self.hero_btn.winfo_ismapped():
            self.hero_btn.pack_forget()
        self._set(self.side_status, text=f"● {title}", text_color=color)
        self._set(self.mode_desc, text=MODE_INFO[mode][1])

    def _update_rows(self, out: OutState) -> None:
        mapping = self.settings.mapping or {}
        mode = self.settings.mode
        for target in TARGETS:
            row, name, bound, btn = self.rows[target.key]
            hidden = target.key == "TOUCHPAD" and mode == "xbox"
            if hidden == row.winfo_ismapped():
                row.pack_forget() if hidden else row.pack(fill="x", padx=6, pady=2)
            self._set(name, text=target.label(mode))
            if self.binder is not None and self.binder.key == target.key:
                self._set(bound, text="чекаю…", fg_color=ORANGE)
                self._set(btn, text="Скасувати")
            else:
                self._set(bound, text=describe(mapping.get(target.key)),
                          fg_color=PILL_ACTIVE if is_active(target.key, out) else PILL)
                self._set(btn, text="Змінити")

        if self.binder is not None:
            target = next(t for t in TARGETS if t.key == self.binder.key)
            verb = "Відхили до упору" if target.is_axis else "Натисни на джойстику"
            self._set(self.wiz_step, text=f"Крок {self.binder.step} з {self.binder.total}")
            where = {"A": "нижня з 4 справа", "B": "права з 4 справа", "X": "ліва з 4 справа",
                     "Y": "верхня з 4 справа"}.get(target.key)
            self._set(self.wiz_text, text=f"{verb}:\n{target.label(mode)}" + (f"  ({where})" if where else ""))
            self._set(self.wiz_hint, text=f"Підсвічено на малюнку. Немає такої кнопки — «Пропустити» "
                                          f"(авто через {self.binder.seconds_left()} с).")
        else:
            self._set(self.wiz_step, text="")
            self._set(self.wiz_text, text=self._wiz_message)
            self._set(self.wiz_hint, text="Відпусти стіки перед стартом. Якщо стіки не рухаються — "
                                          "натисни на джойстику ANALOG / MODE.")

    _wiz_message = "Швидке налаштування\nзайме близько хвилини"

    def _update_keys(self) -> None:
        kbm_mode = self.settings.mode == "kbm"
        if kbm_mode == self.keys_notice.winfo_ismapped():
            if kbm_mode:
                self.keys_notice.pack_forget()
            else:
                self.keys_notice.pack(fill="x", before=self.keys_head)
        capturing = self.key_capture is not None
        if capturing != self.capture_zone.winfo_ismapped():
            if capturing:
                self.capture_zone.pack(fill="x", pady=(6, 0), before=self.keys_head)
            else:
                self.capture_zone.pack_forget()
        keys = self.settings.kbm or {}
        for kbm_key, (kb, arrow) in self.key_buttons.items():
            if self.key_capture == kbm_key:
                self._set(kb, text="тисни клавішу…", fg_color=ORANGE)
            else:
                name = (keys.get(kbm_key) or {}).get("name", "—")
                self._set(kb, text=f"{arrow}  {name}".strip(), fg_color=PILL)

    # ================= назначение кнопок джойстика =================
    def start_binding(self, keys: list[str]) -> None:
        if self.binder is not None:
            self.cancel_binding()
            return
        snap = self.engine.snapshot()
        if snap.device is None:
            messagebox.showinfo(APP_NAME, "Спочатку підключи джойстик.")
            return
        self.binder = Binder(keys, list(snap.raw.axes))
        self.engine.paused = True
        self._set(self.wiz_start, text="■  Зупинити", fg_color=RED, hover_color="#c94848")
        self.wiz_skip.pack(side="left", padx=8)

    def _tick_binder(self, snap: Snapshot) -> None:
        assert self.binder is not None
        if snap.device is None:
            self.cancel_binding("Джойстик відключився")
            return
        key = self.binder.key
        result = self.binder.tick(snap.raw)
        if result is None:
            return
        action, src = result
        if action == "assign" and src is not None:
            mapping = self.settings.mapping or {}
            for other, other_src in list(mapping.items()):
                if other != key and other_src and same_source(other_src, src):
                    mapping.pop(other)
            mapping[key] = src
            self._save()
        elif action == "done":
            self.cancel_binding("Готово! ✓\nРозкладку збережено.")

    def cancel_binding(self, message: str = "Швидке налаштування\nзайме близько хвилини") -> None:
        self.binder = None
        self.engine.paused = False
        self._wiz_message = message
        self._set(self.wiz_start, text="▶  Почати налаштування", fg_color=ACCENT, hover_color=ACCENT_HOVER)
        self.wiz_skip.pack_forget()

    def bind_all(self) -> None:
        if self.binder is not None:
            self.cancel_binding("Зупинено")
            return
        self.start_binding([t.key for t in TARGETS if t.key != "TOUCHPAD" or self.settings.mode != "xbox"])

    def clear_binding(self, key: str) -> None:
        (self.settings.mapping or {}).pop(key, None)
        self._save()

    def reset_mapping(self) -> None:
        if messagebox.askyesno(APP_NAME, "Повернути стандартну розкладку джойстика?"):
            self.settings.mapping = copy.deepcopy(DEFAULT_MAPPING)
            self._save()

    # ================= клавиши =================
    def start_key_capture(self, kbm_key: str) -> None:
        if self.key_capture is not None:
            same = self.key_capture == kbm_key
            self.cancel_key_capture()
            if same:
                return
        self.key_capture = kbm_key
        self.engine.paused = True
        # Откладываем, чтобы клик по самой кнопке не засчитался.
        self.root.after(50, lambda: self.root.bind("<KeyPress>", self._on_key))

    def cancel_key_capture(self) -> None:
        self.key_capture = None
        self.engine.paused = False
        self.root.unbind("<KeyPress>")

    def _finish_key(self, bind: dict[str, Any]) -> None:
        if self.key_capture is None:
            return
        (self.settings.kbm or {})[self.key_capture] = bind
        self._save()
        self.cancel_key_capture()

    def _on_key(self, event: tk.Event) -> str:
        vk = int(event.keycode)
        side = {"Shift_L": (0xA0, "LShift"), "Shift_R": (0xA1, "RShift"), "Control_L": (0xA2, "LCtrl"),
                "Control_R": (0xA3, "RCtrl"), "Alt_L": (0xA4, "LAlt"), "Alt_R": (0xA5, "RAlt")}
        names = {0x20: "Space", 0x0D: "Enter", 0x1B: "Esc", 0x09: "Tab", 0x08: "Backspace", 0x14: "CapsLock",
                 0x25: "←", 0x26: "↑", 0x27: "→", 0x28: "↓", 0x2D: "Insert", 0x2E: "Delete", 0x24: "Home",
                 0x23: "End", 0x21: "PgUp", 0x22: "PgDn"}
        if event.keysym in side:
            vk, name = side[event.keysym]
        elif 0x30 <= vk <= 0x5A:
            name = chr(vk)
        elif 0x70 <= vk <= 0x87:
            name = f"F{vk - 0x6F}"
        elif 0x60 <= vk <= 0x69:
            name = f"Num{vk - 0x60}"
        else:
            name = names.get(vk, event.keysym)
        self._finish_key({"vk": vk, "name": name})
        return "break"

    def _assign_mouse(self, button: str) -> None:
        names = {"left": "ЛКМ", "right": "ПКМ", "middle": "СКМ", "x1": "Миша 4", "x2": "Миша 5",
                 "wheel_up": "Коліщатко ↑", "wheel_down": "Коліщатко ↓"}
        self._finish_key({"mouse": button, "name": names[button]})

    def clear_key(self, kbm_key: str) -> None:
        (self.settings.kbm or {}).pop(kbm_key, None)
        self._save()

    def reset_keys(self) -> None:
        if messagebox.askyesno(APP_NAME, "Повернути стандартні клавіші (WASD, миша…)?"):
            self.settings.kbm = copy.deepcopy(DEFAULT_KBM)
            self._save()

    # ================= опции =================
    def _save(self) -> None:
        save_settings(self.settings)
        self.engine.set_settings(self.settings)

    def change_mode(self, mode: str) -> None:
        if self.binder is not None:
            self.cancel_binding()
        if self.key_capture is not None:
            self.cancel_key_capture()
        self.settings.mode = mode
        self.mode_switch.set(MODES[mode])
        self._save()

    def change_deadzone(self, value: int, label: ctk.CTkLabel, unit: str) -> None:
        self.settings.deadzone = value / 100
        self._set(label, text=f"{value}{unit}")
        self._save()

    def change_mouse_speed(self, value: int, label: ctk.CTkLabel, unit: str) -> None:
        self.settings.mouse_speed = value
        self._set(label, text=f"{value}{unit}")
        self._save()

    def change_rumble(self) -> None:
        self.settings.rumble = bool(self.rumble_switch.get())
        self._save()

    def toggle_autostart(self) -> None:
        autostart.set_enabled(bool(self.autostart_switch.get()))

    def reset_all(self) -> None:
        if messagebox.askyesno(APP_NAME, "Скинути розкладку, клавіші і налаштування?"):
            mode = self.settings.mode
            self.settings.mapping = copy.deepcopy(DEFAULT_MAPPING)
            self.settings.kbm = copy.deepcopy(DEFAULT_KBM)
            self.settings.deadzone, self.settings.mouse_speed, self.settings.rumble = 0.08, 1400, True
            self.settings.mode = mode
            self._save()
            for (slider, label, unit), value in ((self.deadzone_slider, 8), (self.mouse_slider, 1400)):
                slider.set(value)
                self._set(label, text=f"{value}{unit}")
            self.rumble_switch.select()

    def uninstall(self) -> None:
        if not messagebox.askyesno(APP_NAME, "Видалити Konsol з комп'ютера?\n\n"
                                             "Приберуться автозапуск, ярлики, налаштування і «Режим для Steam»."):
            return
        remove_driver = messagebox.askyesno(
            APP_NAME, "Видалити і драйвер віртуального геймпада (ViGEmBus)?\n\n"
                      "«Ні» — якщо ним користуються інші програми.")
        installer.uninstall(remove_driver)
        messagebox.showinfo(APP_NAME, "Konsol видалено.")
        self.quit()

    def install_driver(self) -> None:
        msi = vigem_installer()
        if msi is None:
            messagebox.showerror(APP_NAME, "Встановник драйвера не знайдено.")
            return
        subprocess.Popen(["msiexec", "/i", str(msi)])

    # ================= трей / окно =================
    def _start_tray(self) -> None:
        import pystray

        # pystray смотрит на число аргументов колбэка, поэтому без default-аргументов в лямбдах.
        def pick(mode: str) -> Any:
            return lambda: self.events.put(f"mode:{mode}")

        def checked(mode: str) -> Any:
            return lambda _item: self.settings.mode == mode

        modes = pystray.Menu(*(pystray.MenuItem(title, pick(mode), checked=checked(mode), radio=True)
                               for mode, title in MODES.items()))
        menu = pystray.Menu(
            pystray.MenuItem("Відкрити", lambda: self.events.put("show"), default=True),
            pystray.MenuItem("Режим", modes),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Вихід", lambda: self.events.put("quit")),
        )
        self.tray = pystray.Icon(APP_NAME, make_icon(64), "Konsol — геймпад", menu)
        self.tray.run_detached()

    def _notify_device(self, snap: Snapshot) -> None:
        if snap.device == self._last_device:
            return
        if self.tray is not None:
            try:
                if snap.device:
                    what = "клавіатура і миша" if self.settings.mode == "kbm" else MODES[self.settings.mode]
                    self.tray.notify(f"Підключено {snap.device}. Для ігор: {what}.", APP_NAME)
                elif self._last_device:
                    self.tray.notify("Джойстик відключено.", APP_NAME)
            except Exception:  # noqa: BLE001
                log.exception("Уведомление не показано")
        self._last_device = snap.device

    def show(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def hide(self) -> None:
        if self.binder is not None:
            self.cancel_binding()
        if self.key_capture is not None:
            self.cancel_key_capture()
        self.root.withdraw()

    def quit(self) -> None:
        self.engine.stop()
        self.engine.join(timeout=2)
        if self.tray is not None:
            self.tray.stop()
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        handlers=[logging.FileHandler(DATA_DIR / "konsol.log", encoding="utf-8")],
    )


def main() -> None:
    _setup_logging()
    if installer.is_frozen() and not installer.is_installed_copy() and "--portable" not in sys.argv:
        # Запуск скачанного exe = установка/обновление: закрываем старую копию и ставим эту.
        if instance.running():
            instance.signal("quit")
            if not instance.wait_exit():
                ctypes.windll.user32.MessageBoxW(
                    None, "Закрий Konsol у треї (ПКМ → Вихід) і запусти ще раз.", APP_NAME, 0x30)
                return
        try:
            installer.launch(installer.install())
        except OSError:
            log.exception("Установка не удалась")
            ctypes.windll.user32.MessageBoxW(None, "Не вийшло встановити Konsol.", APP_NAME, 0x10)
        return
    if not instance.acquire():
        instance.signal("show")
        return
    try:
        App(start_hidden="--minimized" in sys.argv).run()
    except Exception:
        log.exception("Приложение упало")
        raise


if __name__ == "__main__":
    main()
