"""#2872: מסך קליטת האימג' בגואי של מחשב הבנייה נגיש במקלדת.

בלי עכבר (KVM) אי אפשר היה לבחור דיסק, ובטופס ה-Tab דילג על בורר התיקייה
ועל "+ חדשה". חוזה המקור (רץ בכל מקום; בניית הגואי והרינדור הם בטסטים
של המעבדה): הדיסקים נבחרים בחצים/ספרות/Enter לפי אותו סדר שמצויר, התיקייה
ו"+ חדשה" נכנסות ל-Tab, והשורה התחתונה אומרת את המקשים.
"""

from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "native-gui" / "src"


def _text(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def test_tab_order_reaches_the_folder_select_and_the_new_folder_button():
    main = _text("main.c")
    block = main[main.index("case SCREEN_PICK:\n") if "case SCREEN_PICK:\n" in main
                 else main.index("case SCREEN_PICK:\r\n"):]
    block = block[:block.index("break;")]
    assert "HIT_NAME" in block and "HIT_DESC" in block
    assert "HIT_FOLDER;" in block and "HIT_NEWFOLDER;" in block


def test_the_disks_are_picked_from_the_keyboard_in_drawn_order():
    main, cap = _text("main.c"), _text("screens_capture.c")
    assert "int pick_disk_order(const App *a, int *idx)" in cap
    assert "pick_disk_order(a, idx)" in cap          # the picker draws from the same list
    assert main.count("pick_disk_order(a, idx)") >= 2   # ... and the keys walk it
    assert "e->ch >= '1' && e->ch <= '9'" in main[main.index("#2872: the capture picker by keyboard"):][:400]
    assert "pick_step_disk(a, 1)" in main            # Enter on the list


def test_the_folder_select_and_new_folder_have_keys_and_a_focus_ring():
    main, cap, widgets = _text("main.c"), _text("screens_capture.c"), _text("widgets.c")
    assert "a->folder_sel = at < 1" in main                  # arrows on the folder select
    assert "a->focus == HIT_NEWFOLDER && e->ch == ' '" in main   # Space
    assert "a->focus == HIT_NEWFOLDER" in cap                # the ring round the button
    assert "a->dd_open == id || a->focus == id" in widgets   # the ring round the select


def test_the_footer_says_the_keys():
    screens = _text("screens.c")
    line = [l for l in screens.splitlines() if "case SCREEN_PICK:return a->chosen_disk>=0" in l][0]
    assert "↑ ↓ / 1–9  בחירת דיסק" in line and "רווח  תיקייה חדשה" in line
