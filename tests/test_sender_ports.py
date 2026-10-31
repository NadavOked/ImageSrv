"""הפורטים של ההפצה: מי נוגע בהם, ומה קורה כשהם תפוסים (#156).

יתום `udp-sender` מריצת טסטים שנקטעה שרד יום וחמש שעות והחזיק את
פורטי ההפצה של השרת. ההפצה הבאה הייתה נכשלת, והאבחון היה נראה כמו
תקלת רשת. שתי הגנות, ושתיהן נבדקות כאן:

1. **אף טסט אינו נוגע בפורטי ההפצה** — לא בקוד המקור של החבילה, ולא
   במנועים שנבנים בפועל בריצה (כולל זה ש-`create_app` בונה בעצמו).
   יתום שאינו יכול להתנגש הוא יתום לא מזיק, וזו ההגנה היחידה שאינה
   תלויה בכך שמישהו ינקה.
2. **השרת מסרב לשדר על פורט תפוס ונוקב במי שמחזיק אותו** — וגם מסרב
   כשהבדיקה עצמה לא הצליחה לרוץ, כי "לא ידוע" אינו "פנוי" (עיקרון 5).

הפורט התפוס בבדיקות הוא סוקט שהטסט פותח בעצמו על פורט ארעי גבוה
שהקרנל הקצה לו — לעולם לא פורט הפצה, ולעולם לא תהליך של מישהו אחר.
"""

from __future__ import annotations

import contextlib
import os
import re
import socket
from pathlib import Path

import pytest

import hygiene
from native import requires_native

pytest.importorskip("fastapi")

from server.rounds import sender                                     # noqa: E402
from server.library.images import ImageLibrary                        # noqa: E402
from server.rounds.sender import SenderEngine                        # noqa: E402

from conftest import MANIFEST_256, write_image                # noqa: E402
from test_sender import Recorder, wait_for                    # noqa: E402

#: פורטי ההפצה האמיתיים. זו ההופעה **היחידה** המותרת שלהם בכל `tests/`,
#: והסורק שלמטה מדלג עליה לפי הסימון שבסוף השורה.
PRODUCTION_PORTS = (9000, 9001)   # פורט-הפצה-מכוון

#: הרצפה שממנה ואילך portbase נחשב "לא מזיק" — מעל כל פורט מוכר.
HIGH_PORT_FLOOR = 20000


@pytest.fixture()
def library(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    return ImageLibrary(tmp_path)


def bindable(port: int) -> bool:
    """אפשר לתפוס את הפורט הזה עכשיו? בדיקה עצמאית של הטסט עצמו, כדי
    שהתרחיש לא ייבנה בעזרת הקוד שהוא בא לבדוק."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind(("127.0.0.1", port))
    except OSError:
        return False
    finally:
        probe.close()
    return True


@contextlib.contextmanager
def busy_pair(offset: int):
    """זוג פורטים שבו רק אחד תפוס — ושהטסט עצמו מחזיק אותו.

    ‏`offset` הוא המקום של הפורט התפוס בזוג: 0 הוא ה-portbase עצמו,
    1 הוא `portbase+1`. מחזיר `(portbase, הפורט התפוס)`.
    """
    for _ in range(20):
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        holder.bind(("127.0.0.1", 0))
        held = holder.getsockname()[1]
        base = held - offset
        # ‏65535 הוא הפורט האחרון: `base+1` מעליו אינו פורט, ו-`bind`
        # עליו זורק `OverflowError` ולא `OSError`.
        if base < 1 or base + 1 > 65535:
            holder.close()
            continue
        if bindable(base + 1 if offset == 0 else base):
            try:
                yield base, held
            finally:
                holder.close()
            return
        holder.close()
    pytest.fail("לא הצלחנו להעמיד זוג פורטים שבו רק אחד תפוס")


# --- אף טסט אינו נוגע בפורטי ההפצה -------------------------------------------


def test_the_run_assigns_a_high_random_portbase_to_every_engine(library):
    """המנוע שנבנה בלי לבקש portbase מקבל את זה של הריצה, לא של ההפצה.

    זה המסלול שהשאיר את היתום: אף טסט לא ביקש portbase, ולכן כולם
    קיבלו את ברירת המחדל — שהיא פורט ההפצה.
    """
    assigned = getattr(hygiene, "test_portbase", None)
    assert assigned is not None, "הריצה לא הקצתה portbase לטסטים (#156)"
    assert assigned >= HIGH_PORT_FLOOR
    assert assigned not in PRODUCTION_PORTS

    engine = SenderEngine(library, runner=Recorder())
    assert engine.portbase == assigned
    cmd = engine.command_for("x", 1)
    assert cmd[cmd.index("--portbase") + 1] == str(assigned)


def test_the_engine_that_create_app_builds_also_gets_the_run_portbase(server):
    """‏`create_app` בונה מנוע משלו בלי לשאול — וזה המנוע מ-#156."""
    assigned = getattr(hygiene, "test_portbase", None)
    assert assigned is not None, "הריצה לא הקצתה portbase לטסטים (#156)"
    assert server["ctx"].sender.portbase == assigned
    assert server["ctx"].sender.portbase not in PRODUCTION_PORTS


def test_no_python_file_under_tests_asks_for_a_production_port():
    """סריקת מקור: אף שורת פייתון בחבילה לא נוקבת בפורטי ההפצה.

    ראיה חיובית ולא הבטחה — טסט שיכתוב מחר את פורט ההפצה במפורש ייתפס
    כאן, ולא מול כיתה. (והסורק תפס כך גם את השורה הזאת עצמה.)

    **הגבול מדויק ולא נדיב:** כל `*.py` תחת `tests/`, כולל תת-תיקיות
    (‏`fixtures/make_fixture.py`), ולא קבצים אחרים. הקובץ היחיד שאינו
    פייתון בחבילה הוא בינארי (`fixtures/system-mini.hiv`), ואין לו
    שורות לסרוק.
    """
    pattern = re.compile(r"\b(?:%s)\b" % "|".join(str(p) for p in PRODUCTION_PORTS))
    root = Path(__file__).parent
    scanned, offenders = 0, []
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        scanned += 1
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if pattern.search(line) and "פורט-הפצה-מכוון" not in line:
                offenders.append(f"{path.relative_to(root)}:{number}: {line.strip()}")
    # סורק שלא סרק כלום נראה בדיוק כמו סורק שאין לו מה למצוא.
    assert scanned > 50, f"הסריקה כיסתה {scanned} קבצים בלבד"
    assert not offenders, "טסטים שנוקבים בפורט ההפצה (#156):\n" + "\n".join(offenders)


def test_the_precheck_documents_that_udp_sender_binds_only_portbase_plus_one():
    """‏#342: מי שתופס את portbase כדי לשחזר התנגשות לא יראה אחת.

    נמדד ב-`ss -ulnp` במעבדה: udp-sender נקשר רק ל-portbase+1, לא
    ל-portbase. הבדיקה על שני הפורטים נשארת — הגנה מפני שינוי עתידי
    ב-udpcast. מה שנבדק כאן הוא שההתנהגות כתובה ליד הבדיקה, כדי
    שהבא לא יבזבז סיבוב.
    """
    text = Path(sender.__file__).read_text(encoding="utf-8")
    start = text.index("def _ports_are_free")
    end = text.index("def _port_verdict")
    precheck = text[start:end]
    assert "#342" in precheck, "ההערה על הקשירה בפועל חסרה ליד הבדיקה"
    assert "נקשר רק" in precheck, precheck
    assert "portbase+1" in precheck
    # הבדיקה עצמה עדיין על שני הפורטים — לא מצמצמים אותה למדידה.
    assert "self.portbase, self.portbase + 1" in precheck


# --- פורט תפוס: סירוב, ושם המחזיק --------------------------------------------


@requires_native(paths=("/proc/net/udp",), why="זיהוי פורט תפוס קורא ב-/proc")  # issue-312-windows-env
@pytest.mark.parametrize("offset", [0, 1])
def test_a_busy_udpcast_port_refuses_the_broadcast(library, offset):
    """שני הפורטים נבדקים — גם אם udp-sender נקשר בפועל רק ל-+1 (#342)."""
    with busy_pair(offset) as (base, held):
        recorder = Recorder()
        engine = SenderEngine(library, runner=recorder, portbase=base)
        engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})

        assert wait_for(lambda: engine.status()["state"] == "failed")
        error = engine.status()["error"]
        assert f"פורט {held} תפוס" in error, error
        assert recorder.commands == [], "יצא שידור על פורט תפוס"


@requires_native(paths=("/proc/net/udp",), why="זיהוי המחזיק קורא ב-/proc")
def test_the_refusal_names_the_process_that_holds_the_port(library):
    """"פורט ההפצה תפוס על ידי PID 278696" במקום "תקלת רשת"."""
    with busy_pair(0) as (base, held):
        engine = SenderEngine(library, runner=Recorder(), portbase=base)
        engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
        assert wait_for(lambda: engine.status()["state"] == "failed")
        assert f"PID {os.getpid()}" in engine.status()["error"]


@requires_native(paths=("/proc/net/udp",), why="הבדיקה קוראת ב-/proc")
def test_the_port_scan_reads_the_holder_and_reads_the_release():
    """שני הכיוונים על אותו פורט: תפוס בשם ה-PID, ואחרי סגירה — ריק."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    try:
        assert sender.port_holders(port) == [os.getpid()]
    finally:
        holder.close()
    assert sender.port_holders(port) == []


def test_a_table_that_could_not_be_read_is_not_an_empty_table(tmp_path, monkeypatch):
    """טבלה שקיימת ולא נקראה אינה "אין שם אף אחד" (עיקרון 5).

    ‏`/proc/net/udp` ו-`/proc/net/udp6` הן שתי טבלאות; דילוג שקט על אחת
    מהן היה מחזיר את התשובה החלקית של השנייה — "פנוי" על סמך חצי בדיקה.
    (תיקייה במקום קובץ היא כשל קריאה אמיתי, לא הזרקה.)
    """
    monkeypatch.setattr(sender, "UDP_TABLES", (tmp_path,))
    with pytest.raises(OSError):
        sender.port_holders(HIGH_PORT_FLOOR)


HEADER = "  sl  local_address rem_address st tx_rx tr tm retr uid to inode ref ptr drops"

#: שתי צורות של שורה שלא נבדקה: כזאת שלא ידענו לפענח, וכזאת שנקטעה.
UNREADABLE_ROWS = [
    "  1: NOTHEX:NOPORT 00000000:0000 07 0:0 00:0 0 0 0 424242 2 0 0",
    "  1: 0100007F:1F90 00000000:0000",
]


@pytest.mark.parametrize("row", UNREADABLE_ROWS)
def test_a_row_we_cannot_read_is_not_a_row_without_the_port(tmp_path, monkeypatch, row):
    """שורה שלא נבדקה — לא פוענחה או נקטעה — ואולי דווקא היא זו
    שמחזיקה את הפורט. בלי זה החריגה הייתה בורחת מתהליכון הרקע (או
    שהשורה הייתה נספרת כ"לא רלוונטית"), והשידור היה יוצא לדרך."""
    table = tmp_path / "udp"
    table.write_text(f"{HEADER}\n{row}\n", encoding="utf-8")
    monkeypatch.setattr(sender, "UDP_TABLES", (table,))
    with pytest.raises(OSError):
        sender.port_holders(HIGH_PORT_FLOOR)


def test_a_blank_line_is_not_an_unreadable_row(tmp_path, monkeypatch):
    """שורה ריקה אינה שורת סוקט — ולכן היא לא הופכת כל בדיקה לכישלון."""
    table = tmp_path / "udp"
    table.write_text(f"{HEADER}\n\n", encoding="utf-8")
    monkeypatch.setattr(sender, "UDP_TABLES", (table,))
    assert sender.port_holders(HIGH_PORT_FLOOR) == []


def test_a_machine_with_no_kernel_table_refuses_to_guess(tmp_path, monkeypatch):
    """בלי טבלת קרנל אין תשובה — לא "פנוי", וגם לא ניחוש מ-bind.

    ‏bind מוצלח היה ראיה חלקית בלבד: סוקט בדיקה נקשר לכתובת אחת ומפספס
    מי שתפס את הפורט על כרטיס אחר, ו-bind לכל הכרטיסים אסור כאן. ראיה
    חלקית שמוצגת כתשובה היא בדיוק מה שעיקרון 5 אוסר.
    """
    monkeypatch.setattr(sender, "UDP_TABLES", (tmp_path / "no-such-table",))
    with pytest.raises(OSError):
        sender.port_holders(HIGH_PORT_FLOOR)


@pytest.mark.parametrize("failure", [OSError, ValueError, RuntimeError])
def test_a_check_that_could_not_run_is_not_a_free_port(library, monkeypatch, failure):
    """עיקרון 5: "לא הצלחנו לבדוק" אינו "בדקנו והכל תקין".

    ולא רק `OSError`: שורה לא צפויה ב-`/proc` היא `ValueError`. חריגה
    שתברח מכאן מתה בתהליכון הרקע, והסבב נשאר תקוע ב-"starting" — כלומר
    "לא הצלחנו לבדוק" מוצג כ"עדיין עובד", וזה עיקרון 5 הפוך.
    """
    def blind(port):
        raise failure("הבדיקה עצמה נפלה")

    monkeypatch.setattr(sender, "port_holders", blind)
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, portbase=HIGH_PORT_FLOOR)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})

    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert "לא הצלחנו לבדוק" in engine.status()["error"]
    assert recorder.commands == [], "שידרנו בלי לדעת אם הפורט פנוי"


def test_a_busy_port_with_an_unidentified_holder_still_refuses(library, monkeypatch):
    """סוקט של משתמש אחר: אין לנו הרשאה לקרוא את ה-fd שלו, והפורט
    עדיין תפוס. "לא ידוע מי" אינו "אין אף אחד".

    **וזו מגבלה, לא הצלחה:** ‏#156 ביקש הודעה שנוקבת ב-PID, ובמצב הזה
    אין PID לנקוב בו. השרת רץ כ-root ולכן ברוב המקרים כן יזהה; כשלא —
    הוא אומר "בדקנו ולא זיהינו" ואינו ממציא מזהה. הטסט מוודא את
    ההתנהגות הזאת **ואת הפער שבצידה**, לא מברך עליה.
    """
    monkeypatch.setattr(sender, "port_holders",
                        lambda port: [sender.UNKNOWN_HOLDER])
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, portbase=HIGH_PORT_FLOOR)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})

    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert "תפוס" in engine.status()["error"]
    assert "PID" not in engine.status()["error"]     # לא ממציאים מזהה
    assert recorder.commands == []


# --- ומה שנכנס בחלון: udp-sender נכשל, ומי מחזיק את הפורט (#202) -------------
#
# הבדיקה המקדימה היא צילום רגע: הסוקט שלה נסגר לפני ש-udp-sender נקשר,
# ובחלון שביניהם תהליך אחר יכול לתפוס את הפורט. את החלון אי אפשר לסגור —
# udp-sender צריך את הפורט לעצמו — ולכן מה שנבדק כאן הוא **ההודעה**
# שבנתיב הכישלון, ולא ריפוי. שלושה מצבים, שלוש הודעות, ואף אחד מהם אינו
# מקופל לאחר: נמצא מחזיק · נבדק ונמצא פנוי · הבדיקה עצמה לא רצה.


@contextlib.contextmanager
def free_pair():
    """‏portbase ששני פורטיו פנויים, כך שהבדיקה המקדימה **עוברת**.

    בלי זה הכישלון שנבדק כאן היה נתפס בבדיקה המקדימה, והנתיב שהטסט בא
    לבדוק לא היה נרוץ בכלל.
    """
    for _ in range(20):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.bind(("127.0.0.1", 0))
        base = probe.getsockname()[1]
        probe.close()
        if base + 1 <= 65535 and bindable(base) and bindable(base + 1):
            yield base
            return
    pytest.fail("לא הצלחנו להעמיד זוג פורטים פנוי")


@requires_native(paths=("/proc/net/udp",), why="זיהוי המחזיק קורא ב-/proc")
def test_a_failure_after_the_precheck_still_names_who_holds_the_port(
        library, tmp_path, monkeypatch):
    """החלון עצמו: הבדיקה המקדימה עברה, ואז נתפס הפורט ו-udp-sender נכשל.

    בלי בדיקה שנייה בנתיב הכישלון ההודעה חוזרת להיות אטומה — בדיוק
    המצב שלפני #156, שבו האבחון נראה כמו תקלת רשת. ודווקא המקרה הנדיר
    הוא זה שנשאר בלי הודעה, והנדיר הוא מה שקורה מול כיתה.
    """
    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "no-such-log")
    with free_pair() as base:
        holder = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        recorder = Recorder(code=1)

        def racing(cmd):
            # תפיסת הפורט **אחרי** הבדיקה המקדימה ולפני "השידור": זה
            # החלון, והוא נפתח כאן במכוון ולא במרוץ תלוי-תזמון.
            # ‏#342: udp-sender נקשר בפועל רק ל-portbase+1. תפיסת
            # portbase כאן לא הייתה יוצרת התנגשות בכלל — זה קרה
            # בניסיון השחזור הראשון. לשחזור: לתפוס את +1, לא את הבסיס.
            if not recorder.commands:
                holder.bind(("0.0.0.0", base + 1))
            return recorder(cmd)

        engine = SenderEngine(library, runner=racing, portbase=base)
        engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
        try:
            assert wait_for(lambda: engine.status()["state"] == "failed")
            error = engine.status()["error"]
            assert f"פורט {base + 1}" in error, error
            assert f"PID {os.getpid()}" in error, error
        finally:
            holder.close()


def test_a_failure_on_free_ports_does_not_invent_a_holder(
        library, tmp_path, monkeypatch):
    """‏udp-sender נכשל גם מסיבות אחרות. "בדקנו ולא מצאנו" אינו "היה תפוס".

    ההודעה חייבת לומר שהפורטים פנויים ושהסיבה אינה ידועה — ולא להמציא
    מחזיק כדי שיהיה מה לכתוב.
    """
    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "no-such-log")
    monkeypatch.setattr(sender, "port_holders", lambda port: [])
    engine = SenderEngine(library, runner=Recorder(code=1),
                          portbase=HIGH_PORT_FLOOR)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})

    assert wait_for(lambda: engine.status()["state"] == "failed")
    error = engine.status()["error"]
    assert "פנוי" in error, error
    assert "PID" not in error, error                 # לא ממציאים מחזיק


@pytest.mark.parametrize("failure", [OSError, ValueError, RuntimeError])
def test_a_failure_whose_port_check_could_not_run_says_exactly_that(
        library, tmp_path, monkeypatch, failure):
    """המצב השלישי: הבדיקה השנייה עצמה לא רצה (עיקרון 5).

    "לא הצלחנו לבדוק" אינו "הפורטים פנויים", וגם אינו "היה תפוס" —
    ושלושתם אינם אותה הודעה.
    """
    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "no-such-log")
    calls: list[int] = []

    def blind_after_the_precheck(port):
        calls.append(port)
        if len(calls) > 2:            # שתי הראשונות הן הבדיקה המקדימה
            raise failure("הבדיקה עצמה נפלה")
        return []

    monkeypatch.setattr(sender, "port_holders", blind_after_the_precheck)
    engine = SenderEngine(library, runner=Recorder(code=1),
                          portbase=HIGH_PORT_FLOOR)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})

    assert wait_for(lambda: engine.status()["state"] == "failed")
    error = engine.status()["error"]
    assert "לא הצלחנו לבדוק" in error, error
    assert "פנוי" not in error, error                # לא מקופל ל"פנוי"
