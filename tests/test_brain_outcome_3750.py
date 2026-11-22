"""#3750 (אפיק #3653): כל שורה ש-`brain_decisions` מקבל נסגרת, או מסומנת במפורש ש"אין לה תוצאה בכוונה" (`n/a: <סיבה>`).

ריק (`outcome IS NULL`) פירושו "עוד פתוחה או שלא נסגרה". ברזל r40 (19/11) הראה אותו בכל שורות K-WRITE, K-RATE-BOUNDS, K-FLOOR ו-S14
של K-REPAIR, וברוב K5, B29, B4, B2, B18 ו-R4: "בכוונה אין" ו"לא נסגר" נראו אותו דבר (עיקרון 5).

שני חלקים:

1. **סטטי, על כל הכותבים.** כל קריאה ל-`memory.record` / `silence.record` בשרת חייבת אחד מאלה: `outcome=` (תוצאה, או `memory.na(...)`);
   או שהמספר שחזר ממנה נשמר והקובץ קורא ל-`settle`/`resolve`; או (`silence.record` על `UNDECIDED`) שהשורה מסומנת n/a בתוך `silence.record`.
   קריאה שזורקת את המספר בלי `outcome=` אינה יכולה להיסגר לעולם, ולכן היא הכשל. הטסט נופל בשם הקובץ והשורה.
2. **התנהגותי, על הכללים שבגוף ה-Issue.** אחרי שהמוח החליט, ב-`brain_decisions` אין שורה ריקה, ושורת "לא הוחלט" היא n/a.

**בקרה שלילית:** קובצי הייצור שהתיקון נגע בהם מוחזרים כולם לבסיס; ראו את גוף ה-PR.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from server import db
from server.brain import floorrule, memory, repairrule, silence, writerate

REPO = Path(__file__).resolve().parent.parent
SERVER = REPO / "server"
WRITERS = {"memory", "silence"}
CLOSERS = {"settle", "resolve", "settled"}
#: נסגרים בחיפוש השורות הפתוחות של הגל/הסבב (`outcome_state == "open"`) ולא לפי המספר שחזר: `line.py` (B24 נסגר בגל הבא, ה-ANSWER כשהמפעיל עונה)
#: ו-`linkwarn.py` (בסוף הגל). המספר נזרק בכוונה, והקובץ סוגר.
CLOSED_BY_LOOKUP = {"server/brain/line.py", "server/brain/linkwarn.py"}


def _calls():
    """(קובץ, שורה, האם `outcome=`, האם `**`, האם המספר נשמר, האם בתוכה UNDECIDED, האם הקובץ סוגר, האם outcome הוא None מילולי)."""
    out = []
    for path in sorted(SERVER.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        if "record(" not in src:
            continue
        tree = ast.parse(src)
        parent = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        closes = any(isinstance(n, ast.Call) and (getattr(n.func, "attr", None) in CLOSERS or getattr(n.func, "id", None) in CLOSERS)
                     for n in ast.walk(tree))
        for n in ast.walk(tree):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "record"
                    and isinstance(n.func.value, ast.Name) and n.func.value.id in WRITERS):
                continue
            kws = {k.arg: k.value for k in n.keywords}
            undecided = any(isinstance(m, ast.Attribute) and m.attr == "UNDECIDED" or isinstance(m, ast.Name) and m.id == "UNDECIDED"
                            for a in n.args for m in ast.walk(a))
            lit_none = "outcome" in kws and isinstance(kws["outcome"], ast.Constant) and kws["outcome"].value is None
            out.append((path.relative_to(REPO).as_posix(), n.lineno, "outcome" in kws, None in kws,
                        not isinstance(parent[n], ast.Expr), undecided and n.func.value.id == "silence", closes, lit_none))
    return out


CALLS = _calls()


def test_the_scan_found_the_writers():
    # סריקה שלא מצאה כלום הייתה עוברת הכול (עיקרון 5)
    assert len(CALLS) > 80
    assert {f for f, *_ in CALLS} >= {"server/brain/writerate.py", "server/brain/floorrule.py", "server/brain/repairrule.py",
                                      "server/brain/fill.py", "server/brain/peer.py", "server/brain/joinwatch.py"}


@pytest.mark.parametrize("site", CALLS, ids=[f"{f}:{n}" for f, n, *_ in CALLS])
def test_every_decision_row_is_closed_or_marked_not_applicable(site):
    file, line, has_outcome, has_star, kept, undecided, closes, lit_none = site
    where = f"{file}:{line}"
    assert not lit_none, f"{where}: outcome=None מילולי = שורה ריקה"
    if has_outcome or undecided:
        return                          # outcome= (או n/a), או UNDECIDED ש-`silence.record` מסמן n/a
    assert (kept or file in CLOSED_BY_LOOKUP) and closes, (f"{where}: השורה נרשמת בלי outcome= והמספר שלה נזרק (או שאין בקובץ settle/resolve): "
                             "היא לא תיסגר לעולם. סגרו אותה עם התוצאה, או `outcome=memory.na('<סיבה>')`")
    del has_star


@pytest.fixture()
def conn(tmp_path):
    silence.forget()
    floorrule.forget()
    repairrule.forget()
    yield db.connect(tmp_path / "imagesrv.db")
    silence.forget()
    floorrule.forget()
    repairrule.forget()


def _rows(conn, rule):
    return [dict(r) for r in conn.execute("SELECT * FROM brain_decisions WHERE rule = ? ORDER BY id", (rule,))]


def test_write_base_and_bounds_close_with_what_the_engine_was_given(conn):
    base = writerate.decide_base(conn, [("A", 200), ("B", 300)], {}, 40, round_id="W-1", part=0)
    got = writerate.decide_bounds(conn, 400_000, base.kbps, 40, None, round_id="W-1", part=0, file_bytes=None, chunks=None,
                                  full_pass=True, chunk_bytes=4 << 20)
    [b] = _rows(conn, "K-WRITE")
    [r] = _rows(conn, "K-RATE-BOUNDS")
    assert b["outcome"] and f"--write-kbps {base.kbps}" in b["outcome"], b
    assert r["outcome"] and f"--rate-min {got.low}" in r["outcome"] and f"--rate-max {got.high}" in r["outcome"], r


def test_the_floor_closes_with_the_value_or_the_refusal(conn):
    asked = dict(rotational=1, role="target", serial="S1", where="m S1")
    floorrule.decide_disk(conn, None, support=True, rates=lambda: [], **asked)
    floorrule.decide_disk(conn, None, support=False, rates=lambda: [], **{**asked, "serial": "S2"})
    ok, no = _rows(conn, "K-FLOOR")
    assert ok["outcome"] and "MB/s" in ok["outcome"], ok
    assert no["verdict"] == floorrule.REFUSED and "נדחה" in no["outcome"], no


def test_the_answer_to_the_cloner_closes(conn):
    call = repairrule.decide_answer(conn, part_done=True, late_ranges=[], no_lacks=True, has_mark=True, ended_now=False, planned=True,
                                    in_receive=False, fetch=None, rnd=1, wave_round=1, mac="aa:bb", round_id="W-1")
    [row] = _rows(conn, "K-REPAIR")
    assert row["verdict"] == call.verdict and row["outcome"] and call.verdict in row["outcome"], row


def test_a_decision_that_could_not_be_made_is_marked_not_applicable_not_left_empty(conn):
    silence.undecided(conn, "K-WRITE", "S3", "WRITE_BASE", "W-9", "boom")
    [row] = _rows(conn, "K-WRITE")
    assert row["verdict"] == silence.UNDECIDED and row["outcome"].startswith(memory.NA_PREFIX), row


def test_no_row_of_the_four_rules_is_left_empty(conn):
    writerate.decide_base(conn, [], {}, 40, round_id="W-2", part=1)
    writerate.decide_bounds(conn, 0, 0, 40, None, round_id="W-2", part=1, file_bytes=None, chunks=None, full_pass=False,
                            chunk_bytes=4 << 20)
    floorrule.decide_disk(conn, 50, rotational=0, role="target", support=True, rates=lambda: [], serial="S3")
    empty = conn.execute("SELECT rule, verdict FROM brain_decisions WHERE outcome IS NULL").fetchall()
    assert empty == [], [tuple(r) for r in empty]
    assert conn.execute("SELECT count(*) FROM brain_decisions").fetchone()[0] == 3
