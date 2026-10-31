"""‏#1275 — סבב מהספרייה מהגואי הנייטיב: הדיסקים שסומנו בגריד (#695) הם
היעדים, ולא "כל המגירות".

הבאג: ‏`open_round` ב-`main.c` שלח ל-`room-open` רק `image=` ו-`target=`,
והגשר (`guibridge.sh`) בנה `{"image_id","target_drives"}` בלבד. השרת קיבל
סבב בלי `target_slots` — ‏`_selected_ports` = ‏`None` = כל המגירות — וכתב
לכל מגירה מוכנה בזמן שהמפעיל ראה בחירה בגריד (עיקרון 4/5).

מה נבדק:

1. **הגשר** (הרצה אמיתית של `gui_records` → `gui_dispatch` → `room_open`,
   רק ה-POST מדומה): בחירה של 1 מ-3 → ‏`target_slots` באורך 1; בלי `slots=`
   → הגוף **בדיוק** כמו היום (אין שדה); ‏`slots=` פגום → כלום לא נשלח.
2. **הגואי** (על המקור — אין מהדר בווינדוס; הקומפילציה על ה-Testrunner):
   ‏`open_round` שולח `slots=` כשיש בחירה, וסירוב גלוי כשהבחירה לא נכנסה
   לרשומה — לא נפילה ל"בלי שדה" = כל המגירות.

השרת כבר מכבד `target_slots` בסבב ספרייה — ‏`test_server_room.py`
(‏`test_target_slots_stored_and_hello_returns_only_this_machines_ports`,
‏`test_drawer_list_marks_selected_and_filters_fresh`); לא נדרש שינוי שם.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
MAIN_C = (SRC / "main.c").read_text(encoding="utf-8")

bridge_tools = requires_native(("bash", BASH), "awk")

C1 = "aa:bb:cc:00:00:21"


def _bridge(tmp_path: Path, record: str) -> tuple[int, str | None]:
    """מריץ רשומת גואי אחת דרך הגשר האמיתי. מחזיר (rc, הגוף שנשלח או None)."""
    run = tmp_path / "run"
    (run / "gui").mkdir(parents=True)
    rec = tmp_path / "record"
    rec.write_bytes(record.encode("utf-8"))
    out = tmp_path / "out.txt"
    lib = posix(flat_lib())
    script = f"""
        RUN_DIR={posix(run)}; GUI_DIR={posix(run)}/gui
        . {lib}/common.sh
        LOG_FILE={posix(run)}/agent.log
        . {lib}/roomflow.sh
        . {lib}/guibridge.sh
        gui_role() {{ GUI_ROLE=admin; }}
        console_post() {{ cp "$2" {posix(run)}/posted.json; printf 200; }}
        console_say() {{ :; }}
        sleep() {{ :; }}
        gui_records < {posix(rec)}
        rc=$?
        # gui_records reports a failed action through gui_error, not its rc:
        # the error file is the evidence the action was refused.
        [ -s "$GUI_DIR/error" ] && rc=1
        exit $rc
    """
    sf = tmp_path / "t.sh"
    sf.write_text(script, encoding="utf-8")
    with out.open("w", encoding="utf-8") as fh:
        r = subprocess.run([BASH, str(sf)], stdout=fh, stderr=subprocess.STDOUT,
                           stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=60)
    posted = run / "posted.json"
    return r.returncode, (posted.read_text(encoding="utf-8") if posted.exists() else None)


@bridge_tools
def test_one_of_three_ticked_goes_as_target_slots_of_length_one(tmp_path):
    """הבקרה השלילית: על הקוד הישן אין `target_slots` בגוף — כל המגירות."""
    rc, body = _bridge(tmp_path, f"room-open\nimage=img_1\ntarget=3\nslots={C1}@2\n\n")
    assert rc == 0 and body is not None, body
    sent = json.loads(body)
    assert sent["image_id"] == "img_1" and sent["target_drives"] == 3
    assert sent.get("target_slots") == [{"mac": C1, "ports": [2]}], body


@bridge_tools
def test_two_machines_keep_their_own_ports(tmp_path):
    rc, body = _bridge(tmp_path, f"room-open\nimage=img_1\ntarget=4\n"
                                 f"slots={C1}@1,3/aa:bb:cc:00:00:22@2\n\n")
    assert rc == 0 and body is not None
    assert json.loads(body)["target_slots"] == [
        {"mac": C1, "ports": [1, 3]}, {"mac": "aa:bb:cc:00:00:22", "ports": [2]}]


@bridge_tools
def test_no_selection_posts_nothing(tmp_path):
    """הכרעת נדב 11/10: "פתח סבב" בלי דיסק מסומן חסום. רשומה בלי `slots=`
    (או ריקה) אינה נשלחת כסבב "כל המגירות" — השרת עדיין מקבל שדה חסר
    כסבב ישן (תאימות API), ולכן החסימה כאן ובגואי."""
    for record in ("room-open\nimage=img_1\ntarget=3\n\n",
                   "room-open\nimage=img_1\ntarget=3\nslots=\n\n"):
        rc, body = _bridge(tmp_path / str(len(record)), record)
        assert body is None, (record, body)
        assert rc != 0, record


@bridge_tools
def test_a_malformed_selection_posts_nothing(tmp_path):
    """בחירה שלא נקראה אינה "בלי בחירה" — לא נשלח כלום (עיקרון 5)."""
    for bad in (f"{C1}@1;rm -rf x", "junk", f"{C1}@", "@1", f"{C1}@1/{C1}"):
        rc, body = _bridge(tmp_path / bad.replace("/", "_").replace(";", "_")
                           .replace(" ", "_").replace(":", "_").replace("@", "_"),
                           f"room-open\nimage=img_1\ntarget=3\nslots={bad}\n\n")
        assert body is None, (bad, body)
        assert rc != 0, bad


# --- הגואי: open_round על המקור ---------------------------------------------


def function_body(text: str, name: str) -> str:
    m = re.search(r"\n(?:static )?\w[\w \*]*\b" + re.escape(name) + r"\([^)]*\)\s*\{", text)
    assert m, f"{name} לא נמצאה"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


def test_open_round_emits_the_ticked_drawers_as_slots():
    body = function_body(MAIN_C, "open_round")
    assert "room_selection_encode(a, slots, sizeof slots)" in body
    emit = body.index('emit("room-open")')
    kv = body.index('emit_kv("slots", slots);')
    assert emit < kv < body.index("emit_end()")
    # תמיד: אין יותר רשומת room-open בלי slots= (הכרעת נדב 11/10).
    assert "if (drawers > 0) emit_kv" not in body


NEED_ONE = "יש לסמן לפחות דיסק אחד"


def test_open_round_refuses_no_selection_and_a_selection_that_did_not_fit():
    """הכרעת נדב 11/10: בלי דיסק מסומן — הודעה ואין `room-open`. וקטיעה
    (‏-1) אינה "אין בחירה" שנבלעת — גם היא מסורבת לפני השליחה."""
    body = function_body(MAIN_C, "open_round")
    emit = body.index('emit("room-open")')
    refuse = body.index("drawers <= 0")
    assert refuse < emit
    assert NEED_ONE in body[refuse:emit] and "return;" in body[refuse:emit]
    enc = function_body(MAIN_C, "room_selection_encode")
    assert "return -1;" in enc
    assert "return 0;   /* truncated" not in enc


def test_open_direct_refuses_with_the_same_sentence():
    body = function_body(MAIN_C, "open_direct")
    refuse = body.index("drawers <= 0")
    assert refuse < body.index('emit("direct-open")')
    assert NEED_ONE in body[refuse:body.index('emit("direct-open")')]


def test_entering_the_room_screen_starts_with_no_overrides():
    """בחירה שנשארה ממסך קודם (הפצה ישירה) אינה הופכת ליעדי הסבב הזה."""
    body = function_body(MAIN_C, "choose_card")
    room_case = re.search(r"case HIT_ROOM:[^\n]*", body).group(0)
    assert "a->nroom_selection = 0" in room_case
