"""#3673 (אפיק #3653): **פירוט בשאלה** (`ask.Rows`, `.ba-rows` במוקאפ המאושר 20/11) -- הרכיב המשותף של ארבעת ה-Issues.

הפירוט הוא **תצוגה בלבד**: טבלה קטנה ("מה נמצא", "מה ייכתב", "הרשומות שיימחקו") שהכלל מצרף לשאלה. שום פעולה אינה נגזרת ממנו --
הפעולה היא האפשרות שנבחרה, ולכן פירוט פגום לעולם אינו עוצר שאלה (עיקרון 1) ואינו משנה את מה שהתשובה עושה.

מה נבדק:
- הפירוט נשמר עם השאלה, חוזר ב-``view`` (קונסולה) וב-``for_machine`` (ה-hello של הסוכן), ושורד חיבור חדש ל-DB.
- שאלה בלי פירוט -- ``rows`` הוא ``None``; שורה שנכתבה לפני העמודה (``''``) נקראת כך, לא כשגיאה.
- פירוט פגום נדחה **בבנייה** (עד ארבע עמודות, שמונה שורות, תא ריק, סגנון לא מוכר, שורה בלי כל התאים).
- פירוט שנשמר פגום (יד שנגעה ב-DB) אינו מפיל את השאלה: היא מוצגת בלי טבלה, והתשובה עדיין נקלטת.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from server import db
from server.brain import ask

NOW = datetime(2026, 11, 20, 12, 0, 0, tzinfo=timezone.utc)
MAC = "aa:bb:cc:00:00:01"
WIPE = ask.Option("wipe", "למחוק את המידע הישן", destructive=True, verb_he="מחיקת המידע",
                  consequence_he="המידע הישן יימחק ולא יהיה אפשר לשחזר אותו")
SKIP = ask.Option("skip", "לדלג על הדיסק")
SPEC = ask.Spec(point="A11", title_he="נמצא על הדיסק מידע של מערך RAID ישן", options=(WIPE, SKIP), safe_default="skip",
                ttl_s=1800)


def C(*a, **kw):                     # נבנה בתוך הטסט, לא בייבוא: בקוד בלי הרכיב נכשל הערך, לא האיסוף
    return ask.Cell(*a, **kw)


def rows():
    return ask.Rows("מה נמצא", ("מגירה", "דיסק", "סוג המידע"),
                    ((C("2"), C("WDC WD5000AAKX", sub="500 GB", sub_style="ltr"), C("Intel RAID (IMSM)")),),
                    more_he="המחיצות הקודמות נשארות כפי שהן.")


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "ask.db")


def opened(conn, **kw):
    return ask.open_ask(conn, SPEC, entity=("machine", MAC), now=NOW, **kw)


def test_the_rows_are_stored_with_the_question_and_the_console_view_returns_them_whole(conn):
    ask_id = opened(conn, rows=rows())
    got = ask.open_asks(conn, NOW)[0]
    assert got["id"] == ask_id
    assert got["rows"]["title_he"] == "מה נמצא"
    assert got["rows"]["cols"] == ["מגירה", "דיסק", "סוג המידע"]
    assert got["rows"]["rows"][0][1] == {"text": "WDC WD5000AAKX", "style": "", "tone": "", "sub": "500 GB",
                                         "sub_style": "ltr", "arrow": False}
    assert got["rows"]["more_he"] == "המחיצות הקודמות נשארות כפי שהן."


def test_the_machine_gets_the_same_rows_in_its_hello(conn):
    opened(conn, rows=rows())
    sent = ask.for_machine(conn, MAC, NOW)
    assert [a["rows"]["title_he"] for a in sent] == ["מה נמצא"]


def test_the_rows_survive_a_new_connection_to_the_database(tmp_path):
    path = tmp_path / "ask.db"
    c = db.connect(path)
    opened(c, rows=rows())
    c.close()
    assert ask.open_asks(db.connect(path), NOW)[0]["rows"]["cols"] == ["מגירה", "דיסק", "סוג המידע"]


def test_a_question_without_rows_has_none_and_a_row_from_before_the_column_reads_the_same(conn):
    ask_id = opened(conn)
    assert ask.open_asks(conn, NOW)[0]["rows"] is None
    conn.execute("UPDATE brain_asks SET rows_json = '' WHERE id = ?", (ask_id,))
    assert ask.open_asks(conn, NOW)[0]["rows"] is None


def test_rows_written_unreadable_do_not_stop_the_question_or_its_answer(conn):
    ask_id = opened(conn, rows=rows())
    conn.execute("UPDATE brain_asks SET rows_json = '{not json' WHERE id = ?", (ask_id,))
    assert ask.open_asks(conn, NOW)[0]["rows"] is None
    done = ask.answer(conn, ask_id, "skip", by="nadav", role=ask.ADMIN, now=NOW)
    assert done["state"] == "answered" and done["rows"] is None


def test_the_answer_is_the_option_not_the_rows(conn):
    ask_id = opened(conn, rows=rows())
    res = ask.answer(conn, ask_id, "wipe", by="nadav", role=ask.ADMIN, now=NOW)
    assert res["answer"] == "wipe"
    assert ask.resolution(conn, ask_id, NOW).option == "wipe"


@pytest.mark.parametrize("build", [
    lambda: ask.Rows("", ("a",), ((C("x"),),)),                                       # בלי כותרת
    lambda: ask.Rows("t" * 37, ("a",), ((C("x"),),)),                                 # כותרת ארוכה מ-36
    lambda: ask.Rows("t", (), ((),)),                                                 # בלי עמודות
    lambda: ask.Rows("t", ("a", "b", "c", "d", "e"), ((C("x"),) * 5,)),               # חמש עמודות
    lambda: ask.Rows("t", ("a",), ()),                                                # בלי שורות
    lambda: ask.Rows("t", ("a",), tuple((C("x"),) for _ in range(6))),                # שש שורות: השאר ב-more_he
    lambda: ask.Rows("t", ("a", "b"), ((C("x"),),)),                                  # שורה בלי כל התאים
    lambda: ask.Rows("t", ("a",), ((C(""),),)),                                       # תא ריק
    lambda: ask.Rows("t", ("a",), ((C("x" * 61),),)),                                 # תא ארוך
    lambda: ask.Rows("t", ("a",), ((C("x", style="bold"),),)),                        # סגנון לא מוכר
    lambda: ask.Rows("t", ("a",), ((C("x", tone="green"),),)),                        # גוון לא מוכר
    lambda: ask.Rows("t", ("a",), ((C("x"),),), more_he="m" * 121),                   # הערת תחתית ארוכה
])
def test_rows_the_mockup_cannot_draw_are_refused_when_they_are_built(build):
    with pytest.raises(ValueError):
        build()


def test_five_rows_and_four_columns_are_the_most(conn):
    big = ask.Rows("t", ("a", "b", "c", "d"), tuple((C("1"), C("2"), C("3"), C("4")) for _ in range(5)))
    assert opened(conn, rows=big) is not None
    assert len(ask.open_asks(conn, NOW)[0]["rows"]["rows"]) == 5
