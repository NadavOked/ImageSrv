"""#408: קליטה בלי `used_bytes` מציגה קצב — לא מסך קפוא.

הגואי אינו מתקמפל בווינדוס; הבדיקה על המקור, הקומפילציה על ה-Testrunner."""

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui" / "src"


def test_the_capture_screen_shows_a_rate_when_there_is_no_denominator():
    """#408: קליטה בלי `used_bytes` (NTFS מלוכלך) הציגה מסך קפוא — יש מונה
    אבל אין מכנה. הגואי מודד קצב משתי קריאות מצב עוקבות (bytes + updated=)
    ומציג אותו במקום "סה״כ (משוער)" כשאין אחוז; אינו מוצג כ-0 לפני שנמדד."""
    main_c = (GUI / "main.c").read_text(encoding="utf-8")
    cap = (GUI / "screens_capture.c").read_text(encoding="utf-8")
    ui_h = (GUI / "ui.h").read_text(encoding="utf-8")
    assert "double capture_rate_bps;" in ui_h
    assert "a->capture_rate_bps = (double)(a->st.bytes - a->rate_prev_bytes) / (double)(a->st.updated - a->rate_prev_updated);" in main_c
    assert "else if (a->st.bytes < a->rate_prev_bytes) a->capture_rate_bps = 0;" in main_c, "מונה שירד = משימה חדשה, לא קצב שלילי"
    assert 'if (a->capture_rate_bps > 0) {' in cap, "קצב מוצג רק אחרי שנמדד — לא 0"
    assert 'else             draw_metric(cr,t,(Rect){xr-2*mw-N_METRIC_GAP,y,mw,mh},"קצב",rate);' in cap
    assert 'if (s->pct >= 0) draw_metric(cr,t,(Rect){xr-2*mw-N_METRIC_GAP,y,mw,mh},"סה״כ (משוער)",total);' in cap
