"""#1386 "בטל דיסק זה" — הגואי הנייטיב, לפי המוקאפ המאושר
(`docs/design/native/cancel-drawer-1386/mockup.html`, PR #1617).

- שורת מגירה **שכותבת** מקבלת עמודה חמישית: "בטל…" במילים אדומות, עם מקש
  המגירה בשבב (במחשב השיכפול). שורה שאינה כותבת — אין כפתור.
- החלון: ההשלכות, "המשך לכתוב" (Esc) ו"בטל את דיסק N" באדום מלא (Del — לא
  Enter). במחשב הבנייה X פותח אותו עם בחירה בין הכותבים.
- אחרי: שורה אפורה, תג "בוטל", פס ריק — לא אדום (זה לא כשל).
- הגואי שולח `drawer-cancel|<dev>` (מחשב השיכפול) או `room-drawer-cancel` עם
  `cloner=` ו-`dev=` (מחשב הבנייה); הגשר מאמת שוב.

הבדיקות: רינדור `--png` (בהיר) מקובץ מצב, וספירת צבעי הטוקנים.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import _build_gui, _count_color, _pkgconfig, _png_rgb, _token
from test_room_precheck_native_432_65 import _render
from test_room_screens_lc_design import CDISKS

SRC = Path(__file__).resolve().parent.parent / "native-gui" / "src"

_GUI = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)

G = 1_000_000_000


def _drawer(port: int, dev: str, state: str, done: float, cancel: str = "", at: str = "") -> str:
    return (f"drawer={port}|{dev}|{state}|{int(done * 19.5 * G)}|{int(19.5 * G)}||"
            f"{68_000_000 if state == 'writing' else -1}|{120 if state == 'writing' else -1}|{port}|SATA {port - 1}"
            f"|{cancel}|{at}\n")


CLONER = {
    "writing": _drawer(1, "sda", "writing", .63) + _drawer(2, "sdb", "writing", .41),
    "done": _drawer(1, "sda", "done", 1) + _drawer(2, "sdb", "done", 1),
    "cancelled": _drawer(1, "sda", "writing", .63) + _drawer(2, "sdb", "cancelled", 0, "ממסך מחשב השיכפול", "1792249560"),
    "failed": _drawer(1, "sda", "writing", .63) + _drawer(2, "sdb", "failed", .41),
    "cancelling": _drawer(1, "sda", "writing", .63) + _drawer(2, "sdb", "writing", .41, "על ידי nadav"),
}


def _cloner(binary, tmp_path: Path, state: str, card: str = "cloner") -> Path:
    _render(binary, "cloner=office365\n" + CDISKS + CLONER[state] + "message=x|y\n", tmp_path / state, "1920x1080")
    return tmp_path / f"{state}-{card}-light.png"


def _count(path: Path, token: str, tol: int = 6) -> int:
    _w, _h, ch, px = _png_rgb(path)
    return _count_color(px, ch, _token(token), tol=tol)


@_GUI
def test_a_writing_drawer_has_the_red_cancel_words_and_a_finished_one_has_none(tmp_path):
    binary = _build_gui(tmp_path)
    writing, done = _cloner(binary, tmp_path, "writing"), _cloner(binary, tmp_path, "done")
    # "בטל…" in --clr-danger-fg, once per writing row; a done round has no red words
    # (anti-aliased words: near the token, not only its exact colour)
    assert _count(writing, "--clr-danger-fg", tol=60) > _count(done, "--clr-danger-fg", tol=60) + 50


@_GUI
def test_a_cancelled_drawer_is_grey_with_an_empty_bar_not_red(tmp_path):
    binary = _build_gui(tmp_path)
    w, c, f = (_cloner(binary, tmp_path, s) for s in ("writing", "cancelled", "failed"))
    # one blue bar gone (the cancelled disk is empty now, not "41%")
    assert _count(w, "--clr-action") > _count(c, "--clr-action") + 1000
    # and it is not a failure: no red bar, edge or LED the way a failed drawer has
    assert _count(f, "--clr-danger") > _count(c, "--clr-danger") + 1000


@_GUI
def test_the_window_has_the_red_button_and_cancelling_is_orange(tmp_path):
    binary = _build_gui(tmp_path)
    _cloner(binary, tmp_path, "writing")
    window = tmp_path / "writing-cloner-cancel-light.png"
    plain = tmp_path / "writing-cloner-light.png"
    assert _count(window, "--clr-danger-fill") > _count(plain, "--clr-danger-fill") + 3000
    going = _cloner(binary, tmp_path, "cancelling")
    assert _count(going, "--clr-warning") > _count(plain, "--clr-warning") + 100


BUILD = ("room_label=מחשבי שיכפול\n"
         "machine=1|3c:52:82:a1:00:21|1|1|0|writing|37|1|\nmachine_drawers=0|2\n"
         "room_drawer=0|1|S1|Samsung SSD 870 EVO|500107862016|passed|0|1||||ok||||37|{s1}|sda|{c1}\n"
         "room_drawer=0|2|S2|WDC WD5000AAKX|500107862016|passed|0|1||||ok||||41|{s2}|sdb||0\n"
         "round=office365|1|0|0|4|0|4|192|68000000|300|running\nmessage=x|y\n")


@_GUI
def test_the_build_machine_rows_and_the_x_window(tmp_path):
    binary = _build_gui(tmp_path)
    _render(binary, BUILD.format(s1="writing", s2="writing", c1="|0"), tmp_path / "w", "1920x1080")
    _render(binary, BUILD.format(s1="cancelled", s2="writing", c1="בוטל ממחשב הבנייה (noc) · 16:06|1"),
            tmp_path / "c", "1920x1080")
    _render(binary, BUILD.format(s1="done", s2="done", c1="|0"), tmp_path / "d", "1920x1080")
    live_w, live_c, live_d = (tmp_path / f"{k}-room-live-light.png" for k in "wcd")
    # writing rows carry "בטל…" (and the foot "בטל דיסק… X"); nothing writing -- neither
    # (anti-aliased words: near the token, not only its exact colour)
    assert _count(live_w, "--clr-danger-fg", tol=60) > _count(live_d, "--clr-danger-fg", tol=60) + 150
    # the cancelled row: its bar is gone, and it is not drawn red
    assert _count(live_w, "--clr-action") > _count(live_c, "--clr-action") + 500
    assert _count(live_c, "--clr-danger") <= _count(live_w, "--clr-danger") + 50
    # X: the window with the choice among the writers (the chosen row in the soft red)
    assert _count(tmp_path / "w-room-cancel-light.png", "--clr-danger-soft") > _count(live_w, "--clr-danger-soft") + 3000


def test_the_window_confirms_with_del_never_enter_and_names_what_it_cancels():
    dlg = (SRC / "canceldlg.c").read_text(encoding="utf-8")
    handle = dlg[dlg.index("int cdlg_handle("):dlg.index("/* ---- drawing")]
    assert "KEYSYM_DELETE) go = 1" in handle and "KEYSYM_ESC) go = 0" in handle
    assert "KEYSYM_ENTER" not in handle, "Enter must not wipe a disk (mockup: Del)"
    assert '"המשך לכתוב", "Esc"' in dlg and '"Del", BTN_DANGER' in dlg
    assert not re.search(r'"ביטול"', dlg), 'no "ביטול" button next to "בטל" (mockup)'
    main = (SRC / "main.c").read_text(encoding="utf-8")
    assert 'emit("room-drawer-cancel"); emit_kv("cloner", d.mac); emit_kv("dev", d.dev)' in main
    assert '"drawer-cancel|%s", d.dev' in main
    assert "case KEY_DELETE:" in (SRC / "input.c").read_text(encoding="utf-8")
