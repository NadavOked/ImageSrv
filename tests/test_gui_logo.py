"""#1302 (נדב 12/10): האפשרות להחליף לוגו הוסרה. הגואי הקטן מצייר תמיד את
הסמל הקבוע של ImageSrv, ולוגו מכללה שהועלה בקונסולה (#1168) אינו נמשך ואינו
מצויר עוד — הסרה מהקוד, לא הסתרה.

צד השרת (`GET /api/v1/agent/branding/logo`, server/branding.py והקונסולה)
הוסר בשלב ב' — ראו test_console_brand.py; כאן נבדק שאף רכיב בצד הסוכן/הגואי
אינו קורא לו."""

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui" / "src"
AGENT = REPO / "agent"


def test_nothing_on_the_agent_side_fetches_an_uploaded_logo():
    for path in sorted(AGENT.rglob("*")):
        if path.is_file() and path.suffix in {"", ".sh", ".c", ".py"}:
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "branding/logo" not in text, path
            assert "gui_fetch_logo" not in text, path
            assert "logo.png" not in text, path


def test_the_gui_has_no_logo_key_and_always_draws_the_imagesrv_lockup():
    ui_h = (GUI / "ui.h").read_text(encoding="utf-8")
    state_c = (GUI / "state.c").read_text(encoding="utf-8")
    screens = (GUI / "screens.c").read_text(encoding="utf-8")
    assert not re.search(r"\bchar logo\[", ui_h)
    assert 'KEY("logo")' not in state_c
    assert "cairo_image_surface_create_from_png" not in screens
    assert "logo_surface" not in screens
    header = screens[screens.index("static double draw_header("):]
    header = header[:header.index("\n}\n")]
    assert "right=draw_brand_lockup_r(cr,right,(N_HEADER_H-brand_symbol_height(5))/2,5,1,0)-22;" in header
    assert "if (logo)" not in header and "else" not in header.split("draw_brand_lockup_r", 1)[0][-80:]
