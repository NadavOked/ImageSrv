"""‏#1858 (מוקאפ 07/11, נדב) — הגואי של המשכפל: "נצפה F1" במגירה, ואזהרת F1 בספירה לפני כיבוי.

* **מגירה:** דיסק מסומן (שדות 14-16 של `cdisk=`) אדום, וכתוב בה "נצפה F1" — לא "SMART תקול".
* **ספירה בסוף משימה (#1560):** אותה תיבה כמו בחלון ה-idle (`f1_notice`), כשיש דיסק שידוע שעוצר — מסומן, או SMART
  אדום מסיבה של F1. בלי דיסק כזה (גם עם דיסק אדום משום שהוא לקריאה בלבד) המסך כמו היום.
* **חלון ה-idle:** הדיסק המסומן מצוין שם גם הוא, והכלל הישן (`app_f1_disk`) לא השתנה.

הציור נבדק בפיקסלים (צבע `--clr-warning-soft`, שרק תיבת ה-F1 מציירת) בצילום של הגואי עצמו; ה-HTML של המוקאפ
(`docs/design/mockups-1107b`) הוא הייחוס לעיצוב, ולא נבדק כאן. מחרוזות הגואי נבדקות בטקסט המקור.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token
from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "native-gui" / "src"

HEALTHY = "cdisk=1|sda|WDC WD5000AAKX|500107862016|passed||3.0|3.0|0|1|SATA 0|clean||||\n"


def _evo(smart: str, cause: str, f1: str = "||") -> str:
    return f"cdisk=2|sdb|Samsung SSD 870 EVO|500107862016|{smart}|{cause}|3.0|3.0|0|2|SATA 1|clean||{f1}\n"


MARKED = _evo("fail", "f1_observed", "2026-10-24|manual|passed")
MARKED_OVER_RED = _evo("fail", "health_failed", "2026-10-24|auto|fail")
SMART_RED = _evo("fail", "health_failed")
READONLY = _evo("fail", "readonly")


def _count(tmp_path: Path, name: str, cdisks: str, card: str) -> int:
    out = tmp_path / name
    end = f"end_poweroff={int(time.time()) + 20}|drawers|||0\n"
    idle = "idle_poweroff_at=1792900000|300\n"
    extra = end if card == "end-countdown" else idle
    _render(_BIN[0], out, BASE_STATE + HEALTHY + cdisks + extra)
    _w, _h, ch, px = _png_rgb(out / f"card-{card}-light.png")
    return _count_color(px, ch, _token("--clr-warning-soft"), tol=4)


_BIN: list[Path] = []


@pytest.fixture()
def gui(tmp_path):
    _BIN[:] = [_build_gui(tmp_path)]
    return tmp_path


@GUI_TOOLS
def test_the_end_count_warns_for_a_marked_disk_and_for_a_smart_red_one(gui):
    base = _count(gui, "none", "", "end-countdown")
    for name, disks in (("marked", MARKED), ("marked_red", MARKED_OVER_RED), ("smart_red", SMART_RED)):
        assert _count(gui, name, disks, "end-countdown") - base > 4000, name


@GUI_TOOLS
def test_the_end_count_is_as_today_without_an_f1_disk(gui):
    """בלי דיסק שידוע שעוצר המסך זהה: דיסק תקין, ודיסק אדום משום שהוא לקריאה בלבד (אינו F1)."""
    none = _count(gui, "none", "", "end-countdown")
    assert _count(gui, "readonly", READONLY, "end-countdown") == none
    assert _count(gui, "healthy_only", "", "end-countdown") == none


@GUI_TOOLS
def test_the_idle_window_names_a_marked_disk_too(gui):
    base = _count(gui, "none", "", "standby")
    assert _count(gui, "marked", MARKED, "standby") - base > 4000
    assert _count(gui, "readonly", READONLY, "standby") - base < 1500   # הכלל הישן: לקריאה בלבד אינו F1


@GUI_TOOLS
def test_the_bay_of_a_marked_disk_is_red_not_a_smart_fault_text(gui):
    """המגירה נצבעת אדום כמו כל אדום; הטקסט (בקוד): "נצפה F1" ולא "SMART תקול"."""
    healthy = _count(gui, "h", HEALTHY, "standby")
    marked = _count(gui, "m", MARKED, "standby")
    assert healthy != marked
    standby = (SRC / "screens_standby.c").read_text(encoding="utf-8")
    chip = standby[standby.index("static const char *chip_of("):]
    chip = chip[:chip.index("\n}\n")]
    assert 'f1_marked(d) ? "נצפה F1" : chip_he(d->smart)' in chip.replace("\n", " ")


def test_the_gui_text_is_the_mockups(request):
    screens = (SRC / "screens.c").read_text(encoding="utf-8")
    assert "בהדלקה הבאה ה-BIOS יעצור על F1" in screens
    assert "נצפה F1 ב-" in screens and "הוציאו אותו לפני ההדלקה הבאה, או לחצו F1 ליד המחשב כשהוא נדלק." in screens
    standby = (SRC / "screens_standby.c").read_text(encoding="utf-8")
    assert "נצפה F1 בהדלקה (" in standby and "סומן אוטומטית" in standby and "סומן בקונסולה" in standby
    assert "SMART של הדיסק תקין" in standby


def test_the_end_dialog_and_the_idle_window_share_one_box():
    """הקופסה אחת (`f1_notice`): שתי הספירות לא יסתדרו על שני נוסחים."""
    ask = (SRC / "screens_ask.c").read_text(encoding="utf-8")
    assert "f1_notice(a, cr, 0, 0, inner, 0)" in ask and "f1_notice(a, cr, box.x + ASK_PAD" in ask
    screens = (SRC / "screens.c").read_text(encoding="utf-8")
    assert screens.count("double f1_notice(") == 1 and "f1_notice(a,cr,b.x+24,y,in,1)" in screens


def test_a_held_end_has_no_box():
    """במצב "מוחזק" (#2024) הפס העליון נשאר משפט אחד -- ה-F1 מסומן במגירה עצמה, לא בקופסה."""
    ask = (SRC / "screens_ask.c").read_text(encoding="utf-8")
    body = ask[ask.index("void draw_end_dialog("):]
    assert body.index("end_held") < body.index("f1_notice(")


def test_the_state_parser_takes_only_a_well_formed_mark():
    state = (SRC / "state.c").read_text(encoding="utf-8")
    assert 'strspn(f[13], "0123456789-") == 10' in state and '!strcmp(f[14], "manual") || !strcmp(f[14], "auto")' in state
    assert '"f1_observed"' in state
