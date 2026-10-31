"""‏#552 — מה המפעיל רואה בפעולה שנמשכת 75 דקות.

**נמדד על ברזל, 08/09/2026.** נדב פתח סבב, הסתכל על המסך, וראה:

```
Drives:     0 of 5 written, 5 to go
```

**זה מה שהמסך הראה, וזה מה שהוא היה מראה במשך 75 דקות** — המונה סופר
מגירות ש**סיימו**, ולכן הוא עולה מ-0 ל-5 רק בסוף. באותו רגע השרת כבר
ידע `HP1 writing 15.2GB / 134.1GB`, כל חמש שניות. **הנתונים היו קיימים
ומעודכנים. הם לא הוצגו.**

## ⚠️ למה זה `bug` ולא `enhancement`

| המצב | מה המסך מראה |
|---|---|
| הכול רץ מצוין ב-1.9% | ‏`0 of 5 written` |
| הכול תקוע ולא זז מאז שהתחיל | ‏`0 of 5 written` |

מפעיל שאינו יכול להבחין ביניהם **יעצור סבב תקין** — ועצירה באמצע
כתיבה משאירה מגירה במצב חלקי.

## ⚠️⚠️ והמדידה ששינתה את התכנון

התכנון הראשון נשען על `updated_at`, ועל סף של 30 שניות. **‏1,896
דגימות מאותו סבב הפריכו את שניהם:**

- ‏**המגירה שמתה רועננה את `updated_at` כל 5 שניות במשך 32 דקות**
  בזמן שהמונה שלה קפא. סימון שנשען עליה לעולם לא היה נדלק על
  המגירה היחידה שבאמת מתה.
- ‏**מגירה בריאה עצרה עד 120 שניות** (חציון 5, ‏p95 5). סף של 30
  היה נדלק על שלוש מארבע.

לכן: המדד הוא `bytes_written` שאינו גדל, והסף הוא 180 שניות.

מאז 11/10 (GUI בלבד) מסך הטקסט של החדר (`roomdraw.sh`) הוסר, ואיתו בדיקות
הרינדור שלו שהיו כאן. מה שנשאר הוא צד השרת — החותמת והסף.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server.agent import reports
from server.rounds import room



@pytest.fixture(autouse=True)
def ticking_clock(monkeypatch):
    """⚠️ **השעון נשלט, ולא נמדד.**

    בלי זה הטסט המרכזי כאן — 372 הדיווחים של המגירה שמתה — **עובר גם
    כשהכלל שבור**: כל האיטרציות רצות באותה שנייה, ולכן `now_iso()`
    מחזירה את אותה מחרוזת, והשוויון מתקיים במקרה ולא בזכות הקוד.

    נתפס בבקרה שלילית: מוטציה ש"חותמת תמיד עכשיו" הפילה שני טסטים
    ‏**והשאירה את הזה ירוק.** שעון שמתקדם בשנייה בכל קריאה הופך את
    השוויון לראיה.
    """
    counter = {"n": 0}

    def tick() -> str:
        counter["n"] += 1
        return f"2026-09-08T07:{counter['n'] // 60:02d}:{counter['n'] % 60:02d}+00:00"

    monkeypatch.setattr(reports, "now_iso", tick)
    return tick


def stamp(previous: list | None, targets: list) -> list:
    return reports.stamp_movement(
        json.dumps(previous) if previous is not None else None, targets)


def moved_at(entries: list, dev: str) -> str:
    return next(t["moved_at"] for t in entries if t["dev"] == dev)


# --- הכלל: הבייטים, לא זמן הדיווח -------------------------------------------


def test_a_drawer_whose_bytes_did_not_grow_keeps_its_old_stamp():
    """זו כל הנקודה. הדיווח מגיע כל שתי שניות בין אם משהו נכתב ובין
    אם לא — ולכן חותמת שזזה עם הדיווח אינה מודדת התקדמות."""
    before = [{"dev": "sda", "bytes_written": 5000, "moved_at": "2026-09-08T06:00:00+00:00"}]
    after = stamp(before, [{"dev": "sda", "bytes_written": 5000}])
    assert moved_at(after, "sda") == "2026-09-08T06:00:00+00:00"


def test_a_drawer_whose_bytes_grew_gets_a_fresh_stamp():
    before = [{"dev": "sda", "bytes_written": 5000, "moved_at": "2026-09-08T06:00:00+00:00"}]
    after = stamp(before, [{"dev": "sda", "bytes_written": 5001}])
    assert moved_at(after, "sda") != "2026-09-08T06:00:00+00:00"


def test_the_drawer_that_died_on_08_09_would_have_been_caught():
    """**התרחיש שנמדד, ולא תרחיש שהומצא.** ‏`18:40 sda` הקפיאה את
    המונה שלה ב-07:04 והמשיכה לדווח `writing` עד 07:35 — 31 דקות,
    ‏372 דיווחים. **כל אחד מהם חידש את `updated_at`.**

    הלולאה כאן היא בדיוק זה: אותו מספר, שוב ושוב. החותמת חייבת
    להישאר על הדיווח הראשון."""
    entries = stamp(None, [{"dev": "sda", "bytes_written": 41_000_000_000}])
    first = moved_at(entries, "sda")
    for _ in range(372):
        entries = stamp(entries, [{"dev": "sda", "bytes_written": 41_000_000_000,
                                   "state": "writing"}])
    assert moved_at(entries, "sda") == first


def test_a_drawer_reported_for_the_first_time_is_not_born_stalled():
    entries = stamp(None, [{"dev": "sdb", "bytes_written": 0}])
    assert moved_at(entries, "sdb")


def test_a_counter_that_went_backwards_is_movement_not_a_freeze():
    """מונה שירד אינו קיפאון — משהו התחיל מחדש. סימון "תקוע" עליו
    היה אזעקת שווא על מגירה שדווקא עובדת."""
    before = [{"dev": "sda", "bytes_written": 41_000_000_000,
               "moved_at": "2026-09-08T06:00:00+00:00"}]
    after = stamp(before, [{"dev": "sda", "bytes_written": 12}])
    assert moved_at(after, "sda") != "2026-09-08T06:00:00+00:00"


def test_a_corrupt_previous_field_pushes_the_stamp_forward():
    """עיקרון 5: ‏`targets_json` פגום הוא "לא ידענו", לא "לא הייתה
    תנועה". חותמת ישנה שנשמרת עליו הייתה מדליקה אזעקת קיפאון על כל
    המגירות בגלל שדה שבור במסד."""
    after = stamp(None, [{"dev": "sda", "bytes_written": 7}])
    assert moved_at(after, "sda")
    broken = reports.stamp_movement("{not json", [{"dev": "sda", "bytes_written": 7}])
    assert moved_at(broken, "sda")


def test_each_drawer_is_stamped_on_its_own():
    """מגירה אחת שקפאה אינה מקפיאה את השאר, ולהפך. בסבב 08/09 שתי
    מגירות בריאות ואחת מתה חלקו את אותה מכונה ואת אותו דיווח."""
    before = [
        {"dev": "sda", "bytes_written": 100, "moved_at": "2026-09-08T06:00:00+00:00"},
        {"dev": "sdb", "bytes_written": 100, "moved_at": "2026-09-08T06:00:00+00:00"},
    ]
    after = stamp(before, [{"dev": "sda", "bytes_written": 100},
                           {"dev": "sdb", "bytes_written": 200}])
    assert moved_at(after, "sda") == "2026-09-08T06:00:00+00:00"
    assert moved_at(after, "sdb") != "2026-09-08T06:00:00+00:00"


# --- מה שהקונסולה מקבלת -----------------------------------------------------


def _drawers(disks: list[dict], targets: list[dict]):
    class _Conn:
        def execute(self, *_a, **_k):
            class _R:
                def fetchone(self_inner):
                    return {"disks_json": json.dumps(disks)}
            return _R()

    return room.drawer_list(_Conn(), "aa:bb:cc:dd:ee:ff", set(),
                            dict(targets_json=json.dumps(targets)))


def test_the_bytes_reach_the_console_per_drawer():
    """הם היו בשרת כל הזמן — ‏`targets_json` — ופשוט לא יצאו החוצה."""
    drawers = _drawers(
        [{"dev": "sda", "port": 1, "serial": "naa.1", "model": "M"}],
        [{"dev": "sda", "state": "writing",
          "bytes_written": 15_200_000_000, "bytes_total": 134_100_000_000}],
    )
    assert drawers[0]["bytes_written"] == 15_200_000_000
    assert drawers[0]["bytes_total"] == 134_100_000_000


def test_a_drawer_with_no_stamp_reports_unknown_and_not_zero():
    """⚠️ ‏`None` ו-`0` הם שני מצבים שונים. ‏`0` הוא "נמדד, זזה עכשיו";
    ‏`None` הוא "אין חותמת". מסך שמצייר `None` כאפס אומר "הכול זז"
    על מגירה שלא נמדדה כלל."""
    drawers = _drawers(
        [{"dev": "sda", "port": 1, "serial": "naa.1", "model": "M"}],
        [{"dev": "sda", "state": "writing", "bytes_written": 1}],
    )
    assert drawers[0]["stalled_s"] is None


def test_a_stamp_that_does_not_parse_is_unknown_and_not_zero():
    drawers = _drawers(
        [{"dev": "sda", "port": 1, "serial": "naa.1", "model": "M"}],
        [{"dev": "sda", "state": "writing", "moved_at": "yesterday-ish"}],
    )
    assert drawers[0]["stalled_s"] is None


def test_a_fresh_stamp_is_a_small_number_of_seconds():
    from server.db import now_iso
    drawers = _drawers(
        [{"dev": "sda", "port": 1, "serial": "naa.1", "model": "M"}],
        [{"dev": "sda", "state": "writing", "moved_at": now_iso()}],
    )
    assert drawers[0]["stalled_s"] is not None
    assert drawers[0]["stalled_s"] < 5


# --- מי אשם: המגירה או הזרם --------------------------------------------------
#
# ⚠️ **זו ההבחנה שכמעט פספסתי, והיא נתפסה במדידה ולא בהיגיון.**
# בניתי תווית `STALLED` שמודבקת למגירה — ואז ספרנו את 1,896 הדגימות
# של 08/09 ומצאנו שהעצירות **משותפות**:
#
#     11:c2/sdb  קפאה ב-32 דגימות  →  11:c2/sdc קפאה ב-100% מהן
#                                     18:40/sda  ב-93%
#                                     18:40/sdb  ב-81%
#     26 דגימות שבהן כל **חמש** המגירות קפאו יחד
#
# ‏`18:40` ו-`11:c2` הן **שתי מכונות פיזיות שונות**. ‏Garbage collection
# של כונן אינו מסתנכרן עם כונן במארז אחר.
#
# כלומר תווית "כונן תקוע" על עצירה משותפת היא **בדיוק אותה שגיאה**
# כמו `fanout: buffer overrun (drive too slow)` — היא מאשימה כונן על
# תקלה של הזרם, ושולחת אדם להחליף חומרה תקינה.


def _writing(**over) -> dict:
    return {"state": "writing", "stalled_s": 5, **over}


def _view(*drawers: dict) -> list:
    return [{"name": "HP1", "drawer_list": list(drawers)}]


def test_all_drawers_frozen_together_is_the_stream():
    assert room._stream_stalled(_view(_writing(stalled_s=400),
                                      _writing(stalled_s=400))) is True


def test_one_drawer_frozen_alone_is_that_drawer():
    """מגירה בודדת שעצרה בזמן שהשאר כותבות היא **המגירה**. אין כאן
    ראיה לזרם, ולכן אין להסיק עליו."""
    assert room._stream_stalled(_view(_writing(stalled_s=400),
                                      _writing(stalled_s=5))) is False


def test_the_stall_must_cross_machines_or_it_is_not_the_stream():
    """המדידה שהולידה את זה: ארבע מגירות בשתי מכונות. הפונקציה
    סופרת את כל הכותבות בחדר, ולכן שתי מכונות נכללות."""
    machines = [{"name": "HP1", "drawer_list": [_writing(stalled_s=400)]},
                {"name": "HP2", "drawer_list": [_writing(stalled_s=400)]}]
    assert room._stream_stalled(machines) is True


def test_a_single_writing_drawer_proves_nothing_about_the_stream():
    """⚠️ מגירה אחת שעצרה אינה "כולן עצרו". בלי רוב אין הכרעה."""
    assert room._stream_stalled(_view(_writing(stalled_s=999))) is False


def test_an_unmeasured_drawer_cannot_vote():
    """עיקרון 5: ‏`None` הוא "לא נמדד", לא "לא עצורה". מגירה שאין לה
    חותמת אינה יכולה להשתתף בהכרעה ש**כולן** עצרו."""
    assert room._stream_stalled(_view(_writing(stalled_s=400),
                                      _writing(stalled_s=None))) is False


def test_drawers_that_finished_do_not_count():
    """רק מי שכותבת עכשיו. מגירה שסיימה אינה "עצורה"."""
    machines = _view(_writing(stalled_s=400), _writing(stalled_s=400),
                     {"state": "done", "stalled_s": 9999})
    assert room._stream_stalled(machines) is True


# --- הסף עצמו ---------------------------------------------------------------


def _stall_threshold() -> int:
    # #418 put ROOM_STALL_S in roomdraw.sh, the text room screen; it was
    # removed on 11/10 (GUI only). The server's own threshold is the one left.
    return room.STALL_SECONDS


def test_the_threshold_clears_the_slowest_healthy_drawer_measured():
    """⚠️ **הסף אינו בחירה — הוא נגזרת של מדידה.** מגירה בריאה עצרה
    ‏**‏120 שניות** בסבב 08/09. סף של 30, שהיה התכנון לפני המדידה,
    היה נדלק על שלוש מארבע המגירות הבריאות.

    הטסט הזה נופל אם מישהו יוריד את הסף מתחת למה שנמדד."""
    assert _stall_threshold() > 120


def test_the_threshold_still_catches_the_drawer_that_actually_died():
    """והצד השני: סף שנועד לא להרעיש חייב עדיין לתפוס. המגירה שמתה
    עצרה **‏31 דקות** — פי עשר מהסף."""
    assert _stall_threshold() < 31 * 60
