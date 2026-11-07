"""שלב 1 (מוקאפ הקונסולה): שלוש הכניסות לחלון (sheet / openModalContent / openDrawer) יושבות על מסגרת אחת.

הבדיקה סטטית: מבנה ה-HTML של ה-modal והמגירה, רוחבי הסוגים, ושכל חלון בקוד עובר דרך אחת משלוש הכניסות
(אין חלון רביעי שמצייר לעצמו). ההתנהגות עצמה נבדקת ב-windows_frame.test.cjs.
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
INDEX = (STATIC / "index.html").read_text(encoding="utf-8")
CSS = (STATIC / "console.css").read_text(encoding="utf-8")
JS = {p.name: p.read_text(encoding="utf-8") for p in STATIC.glob("*.js")}


def _block(start: str, end: str) -> str:
    return INDEX[INDEX.index(start):INDEX.index(end, INDEX.index(start))]


def test_modal_and_drawer_share_one_frame_with_title_sub_and_x():
    modal = _block('<div class="modal-backdrop" id="modal">', '<div class="toast"')
    drawer = _block('<aside class="drawer', "</aside>")
    for name, html in (("modal", modal), ("drawer", drawer)):
        assert 'class="n-h' in html, name
        assert 'class="n-x"' in html and 'aria-label="סגור"' in html, f"{name}: X תמיד"
        assert "<h2 " in html and "<p " in html, f"{name}: כותרת ושורת משנה"
        assert 'class="n-f-bar' in html, f"{name}: פס תחתון קבוע"
    assert "<header" not in modal + drawer, "ל-<header> יש כלל גלובלי (סרגל כהה); המסגרת משתמשת ב-div"
    assert 'id="drawerTabs"' in drawer and 'id="drawerFoot"' in drawer, "לשוניות ופס פעולות במגירה"


def test_three_widths_by_kind():
    assert ".n-narrow{--w:440px}.n-mid{--w:600px}.n-wide{--w:860px}" in CSS
    assert "--w:480px;width:min(480px,92vw)" in CSS, "המגירה ברוחב קבוע"


def test_every_window_goes_through_one_of_the_three_entry_points():
    # אף קובץ אחר לא כותב ישירות ל-#modalBody / #sheet / #drawerBody
    for name, text in JS.items():
        if name == "console.js":
            continue
        assert not re.search(r'getElementById\("(modalBody|drawerBody|sheet)"\)\.innerHTML', text), name
    js = JS["console.js"]
    assert len(re.findall(r"\bsheet\(\{", js)) + len(re.findall(r"confirmSheet\(", js)) >= 40
    for fn in ("function sheet(", "function openModalContent(", "function openDrawer("):
        assert fn in js, fn
    # הרסני = כפתור אדום מלא; הרסני במגירה = אדום ומופרד
    assert ".win .n-f-bar .btn.danger{background:var(--clr-danger-fill)" in CSS
    assert ".btn.danger.ghost-dng{background:transparent" in CSS
