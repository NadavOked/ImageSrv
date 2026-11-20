"""#2533 (PR #3063, ברזל 13/11, ריצה ח'): מגירה שנשלפה באמצע מעבר של מנוע החתיכות.

מה שנמדד על משכפל 2: נדב שלף את מגירה 3 ב-41% של מחיצה 3. `chunkcast` עצמו ידע תוך שניות
("the flush to the disk failed", ביומן הסוכן בין 07:02:27 ל-07:02:59 UTC) -- ואיש לא קרא את זה: הפסקים
נקראו רק כשהמעבר נגמר, ב-07:08:31. שש דקות המגירה אמרה "writing", לא היה צפצוף, והשרת -- שלא נאמר לו
שהמגירה נכשלה, כי לדיסק שיצא מ-sysfs אין serial להיקרא בו -- קרא לה "לא עמד בקצב השידור".

כאן רצה `chunk_partition_drawers` האמיתית (המעטפת האמיתית, `verdict.sh` האמיתי) מול `chunkcast`, שרת,
sysfs ו-dmesg מזויפים -- הרתמה של test_chunkrecv_2533.py. המעבר נמשך שבע שניות, והמגירה מתה שתי שניות
אחרי שהתחיל. מה שנבדק הוא מה שהמפעיל רואה ושומע, ומתי: הסיבה שנכתבה ליעד, הצפצוף, מה שנאמר לשרת, ומה
שנמסר למעבר הבא -- לא קוד יציאה.

**בקרה שלילית:** החזרת `chunkrecv.sh`, `verdict.sh`, `imagesrv-agent` ומחיקת `chunkwatch.sh` (כל קובצי
הייצור של התיקון) מפילה את כל הטסטים שכאן חוץ משני השומרים (`quiet`, וההמתנה לשרת), שעוברים בשני המצבים
בכוונה.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_agent import BASH
from test_chunkrecv_2533 import DONE, FETCH, RECEIVE1, _run

pytestmark = [
    requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן"),
    # #2118: "within three seconds" is measured in real seconds against a loop that ticks once a second. Under a
    # loaded machine (testrunner, 13/11: load 9, another request beside this one) one of them took five.
    pytest.mark.serial,
]

GONE = "הכונן נעלם מהמערכת (נשלף או נותק)"
UNREAD = "chunkcast's state was not read"
ZAP = "sgdisk --zap-all /dev"                          # the harness's sgdisk only traces: what wipe_written_gpt would run
SLOW_PASS = "REAL_SLEEP=1 TEST_START_CEILING=30"       # the loop's own seconds are real: "within seconds" is measured

#: how the drawer dies -> (the operator's sentence, the tune). The first four are the kernel's evidence,
#: the same drawer_watch reads on the udpcast path (#1598, #2045). The last three are chunkcast's verdict
#: alone, in the words of chunkcast.c: every failure it sees, not only a pull.
DEATHS = {
    "vanish": (GONE, "pulled"),
    "link_down": (GONE, "pulled"),
    "offline": ("הקרנל סימן את הכונן offline", "failure"),
    "write_error": ("הקרנל דחה כתיבה לכונן", "failure"),
    "flush": ("ה-fsync לדיסק נכשל (write_error)", "failure"),
    "write": ("שגיאת כתיבה לדיסק", "failure"),
    "hung": ("הדיסק הפסיק לקבל כתיבה ולא חזר", "failure"),
}
KERNEL = ("vanish", "link_down", "offline", "write_error")


def _between(trace: list[str], line: str) -> bool:
    """`line` was written after the drawer died and before the pass ended."""
    return line in trace and trace.index("DIED") < trace.index(line) < trace.index("PASS-END")


@pytest.mark.parametrize("death", sorted(DEATHS))
def test_a_drawer_that_dies_inside_a_pass_fails_by_name_in_seconds_and_the_other_finishes(tmp_path, death):
    """הליבה. המגירה נכשלת **בתוך** המעבר, שניות אחרי שמתה, בסיבה של מה שקרה לה -- ולא בסופו. המקלט אינו
    נעצר: המעבר מגיע לסופו והמגירה השנייה נחתמת."""
    why, tune = DEATHS[death]
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_DIES=sdb:{death} {SLOW_PASS}")
    trace = got["trace"]
    fails = [t for t in trace if t.startswith("FAIL sdb")]
    assert fails and why in fails[0], (fails, got["log"])
    assert _between(trace, fails[0]), trace                                  # not when the pass ended
    took = int((tmp_path / "failed_at.sdb").read_text()) - int((tmp_path / "died_at").read_text())
    assert 0 <= took <= 3, f"the drawer was failed {took}s after it died"
    assert not any("לא עמד בקצב" in t for t in fails), fails                 # never the stream's reason
    assert _between(trace, f"beep {tune}"), trace                           # heard as it happens (Nadav, 13/11)
    assert sum(t.startswith("beep") for t in trace) == 1, trace              # once, and by the right tune
    if death in KERNEL:
        assert (tmp_path / "targets" / "sdb" / "dead").read_text().startswith(why)
        assert (tmp_path / "targets" / "sdb" / "pulled").exists() == (why == GONE)
    if death == "hung":
        assert "write_error" not in fails[0] and "fsync" not in fails[0], fails   # a hung disk is not a failed flush
    # #1388: a drawer that failed half-written loses its GPT -- when it is still there to be wiped, and it alone
    assert trace.count(f"{ZAP}/sdb") == (0 if death == "vanish" else 1), trace
    assert f"{ZAP}/sda" not in trace
    # the others carry on: the receiver ran to its end, and the healthy drawer is whole and done
    assert "PASS-END" in trace and "DONE sda" in trace and "DONE sdb" not in trace, trace
    assert not any(t.startswith("FAIL sda") for t in trace) and trace[-1] == "rc=0", trace


def test_the_server_is_told_a_pulled_drawer_failed_though_its_serial_can_no_longer_be_read(tmp_path):
    """הסיבה השגויה ("לא עמד בקצב השידור") נולדה כאן: דיסק שיצא מ-sysfs אינו עונה על `disk_serial`, ולכן
    לא נכנס כלל להודעת `ended`. השרת המשיך לספור אותו כחי, ומצא שחסרים לו רוב החלקים. הוא מדווח ב-serial
    שבו נבחר (`bound`), כנכשל."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_DIES=sdb:vanish {SLOW_PASS}")
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    assert ended, got
    told = {t["serial"]: t["failed"] for t in ended[0]["targets"]}
    assert told == {"SER-sda": False, "SER-sdb": True}, told


@pytest.mark.parametrize("again", [{"ok": True, "action": "receive", "pass": 2}, FETCH], ids=["receive", "fetch"])
def test_a_drawer_pushed_back_in_under_its_own_name_is_completed_and_joins_the_next_pass_or_fetch(tmp_path, again):
    """המגירה נשלפה והוחזרה באותו מעבר, באותו שם, ו-`chunkcast` לא שם לב (לא היה לו מה לכתוב לה בינתיים).
    עד #3108 היא נשארה נכשלת לתמיד. מאז (drawerback.sh) היא נמצאת לפי ה-serial שלה, ומה שחסר לה (חתיכות 1, 3-4)
    מורד אליה בלבד **ברקע** (#3112): המעבר הבא או ההורדה של הסבב רצים בלעדיה ואינם מחכים לה, והיא נחתמת.
    היא נכשלה פעם אחת -- כשנשלפה -- וה-GPT שלה אינו נמחק: היא לא נכשלה שוב."""
    answers = [RECEIVE1, again, again, DONE] if again["action"] == "receive" else [RECEIVE1, again, DONE]
    got = _run(tmp_path, answers, env=f"RECV_DIES=sdb:vanish RECV_BACK=1 RECV_UNNOTICED=1 "
                                      f"RECV_COUNT=2 RECV_RANGES=0-0,2-2 {SLOW_PASS}")
    trace = got["trace"]
    assert "BACK" in trace and trace[-1] == "rc=0", got
    later = [t for t in trace if t.startswith(("chunkcast recv", "chunkcast apply"))][1:]
    mine = [t for t in later if "/dev/sdb3" in t]
    assert len(mine) == 1 and mine[0].startswith("chunkcast apply") and "/dev/sda3" not in mine[0], later
    assert all("/dev/sda3" in t for t in later if t not in mine), later    # the round's own runs: without it
    assert "DONE sda" in trace and "DONE sdb" in trace
    assert sum(t.startswith("FAIL sdb") for t in trace) == 1                # failed once, by its own reason
    assert GONE in next(t for t in trace if t.startswith("FAIL sdb"))
    assert not any(t.startswith("sgdisk") for t in trace), trace             # nor is its GPT touched once it is back


# --- the outage tune (#2067) in a chunk pass -----------------------------------------------------------


def test_a_stream_that_goes_quiet_inside_a_chunk_pass_is_heard_as_a_network_outage(tmp_path):
    """צפצוף ניתוק הרשת שואל "האם מחיצה באמצע קליטה" בשני קבצים ש-`drawers.sh` מחזיק (`bytes.raw`,
    `source.rc`), ומסלול החתיכות לא כתב אותם: כבל שנשלף באמצע מעבר לא נשמע. כאן ה-`stream_beep_tick` האמיתי
    רץ פעם בשנייה, כמו בקיוסק; החתיכות הראשונות מגיעות, ואז שבע שניות לא זז דבר (הסף כאן 3). המגירות אינן
    נכשלות -- שקט אינו מוות -- אבל הניתוק נשמע, פעם אחת, בתוך המעבר."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_DIES=sdb:quiet NET_TICK=1 NETLOST_AFTER_S=3 {SLOW_PASS}")
    trace = got["trace"]
    assert UNREAD not in got["log"]                                          # a state that was read is not said to be unread
    assert _between(trace, "beep netlost"), trace
    assert trace.count("beep netlost") == 1 and "beep failure" not in trace and "beep pulled" not in trace, trace
    assert "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), trace


def test_waiting_for_the_server_after_a_pass_ended_is_not_a_network_outage(tmp_path):
    """השומר, ועובר גם בלי התיקון: המעבר נגמר, והמכונה מחכה שמונה שניות למכונות אחרות (`wait`, `whole`). שום דבר
    לא זז, והכבל במקומו: אין צפצוף ניתוק (#2067: ההמתנה בין מחיצות אינה ניתוק)."""
    waiting = {"ok": True, "action": "wait", "poll": 2, "whole": True}
    trace = _run(tmp_path, [RECEIVE1, *[waiting] * 8, DONE], env=f"NET_TICK=1 NETLOST_AFTER_S=3 {SLOW_PASS}")["trace"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace, trace
    assert not any(t.startswith("beep") for t in trace), trace


def test_a_drawer_that_is_only_quiet_inside_a_pass_is_not_failed(tmp_path):
    """השומר מפני תיקון-יתר, ועובר גם בלי התיקון: שקט אינו מוות (#660). מעבר של שבע שניות שבו לא קרה למגירה
    דבר -- היא אינה נכשלת, אין צפצוף, ושתיהן נחתמות."""
    trace = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_DIES=sdb:quiet {SLOW_PASS}")["trace"]
    assert "PASS-END" in trace and "DONE sda" in trace and "DONE sdb" in trace, trace
    assert not any(t.startswith(("FAIL", "beep")) for t in trace), trace
    assert trace[-1] == "rc=0"


@pytest.mark.parametrize("how", ["cp", "awk"])
def test_a_state_that_cannot_be_read_is_said_once_a_pass_and_is_not_taken_for_no_stream(tmp_path, how):
    """סינון על #3080 (עיקרון 5). קובץ המצב של `chunkcast` קיים, והמעקב אינו מצליח לקרוא אותו: ההעתקה שלו נכשלת
    (`cp`), או ספירת החתיכות העמידות (`awk`). עד כאן זה קופל ל-0, כלומר ל"הזרם לא הגיע", וצפצוף הניתוק שתק בלי
    שאיש ידע שלא נבדק דבר. עכשיו זה נאמר ביומן, **פעם אחת** במעבר של תשע שניות (לא בכל שנייה), ובזמן הזה
    המעבר אינו נחשב "באמצע קליטה": אין צפצוף ניתוק על זרם שאיש לא הצליח להסתכל עליו. המגירות אינן נפגעות."""
    got = _run(tmp_path, [RECEIVE1, DONE],
               env=f"RECV_DIES=sdb:quiet NET_TICK=1 NETLOST_AFTER_S=3 STATE_UNREAD={how} {SLOW_PASS}")
    trace = got["trace"]
    assert got["log"].count(UNREAD) == 1, got["log"]
    assert "partition 3" in next(line for line in got["log"].splitlines() if UNREAD in line)
    assert not any(t.startswith("beep") for t in trace), trace
    assert "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), trace


# --- the adversarial review of #3080 (Codex, 13/11): three findings, each with its test ------------------


def test_a_wipe_held_by_one_dying_disk_does_not_leave_the_next_drawer_unwatched(tmp_path):
    """ממצא 1. המגירה הראשונה נכשלת (`chunkcast`: ה-flush נכשל), ומחיקת ה-GPT שלה נתקעת על הדיסק הגוסס עצמו --
    כאן לדקה, על ברזל בלי סוף. שתי שניות אחר כך נשלפת מגירה שנייה. המעקב קרא ל-`fail_written_target` בתוך הלולאה
    שלו, ולכן עמד: המגירה השנייה לא זוהתה ולא נשמעה. היא נכשלת בשמה תוך שניות, עם הצפצוף של שליפה."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"RECV_DIES=sda:flush ZAP_HANGS=sda RECV_DIES2=sdb:vanish {SLOW_PASS}")
    trace = got["trace"]
    assert f"{ZAP}/sda" in trace, trace                                      # the wipe did begin, and is held
    pulled = [t for t in trace if t.startswith("FAIL sdb")]
    assert pulled and GONE in pulled[0], (trace, got["log"])
    assert trace.index(f"{ZAP}/sda") < trace.index("DIED2") < trace.index(pulled[0]) < trace.index("PASS-END"), trace
    took = int((tmp_path / "failed_at.sdb").read_text()) - int((tmp_path / "died2_at").read_text())
    assert 0 <= took <= 3, f"the second drawer was failed {took}s after it was pulled"
    assert trace.index("DIED2") < trace.index("beep pulled") < trace.index("PASS-END"), trace
    assert sum(t.startswith("FAIL sda") for t in trace) == 1 and trace.count("beep failure") == 1, trace   # failed once


def test_chunks_that_were_durable_before_the_watch_began_still_make_the_pass_mid_receive(tmp_path):
    """ממצא 2. המקלט רץ לפני שהמעקב מתחיל (`listening` נאמר לשרת ביניהם), והחתיכות הראשונות נעשות עמידות בחלון הזה.
    הבסיס נקרא כשהמעקב התחיל, ולכן כבר כלל אותן: הרשת נפלה מיד אחר כך, שום דבר לא עלה מעל הבסיס, והניתוק לא
    נשמע. הבסיס הוא מה שהמעבר **הקודם** של המחיצה השאיר (כאן: כלום), לא מה שנקרא באיחור."""
    trace = _run(tmp_path, [RECEIVE1, DONE],
                 env=f"RECV_DIES=sdb:quiet RECV_EARLY=1 NET_TICK=1 NETLOST_AFTER_S=3 {SLOW_PASS}")["trace"]
    assert trace.count("beep netlost") == 1 and trace.index("beep netlost") < trace.index("PASS-END"), trace
    assert "DONE sda" in trace and "DONE sdb" in trace and not any(t.startswith("FAIL") for t in trace), trace


def test_a_repair_pass_is_mid_receive_though_a_failed_drawer_held_chunks_in_the_last_one(tmp_path):
    """ממצא 3. במעבר הראשון שתי המגירות קיבלו שתי חתיכות, ואחת נכשלה. קובץ המצב של מעבר ההשלמה מכיל רק את
    המגירה החיה, והבסיס סכם את שתיהן (4): החיה הגיעה ל-3, פחות מהבסיס, והמעבר לא נחשב "באמצע קליטה" לעולם.
    ההשוואה היא ליעד, ורק ליעדים ש-`chunkcast` עוד כותב: 3 יותר מ-2, הזרם הגיע, וכשהוא נעצר זה נשמע."""
    again = {"ok": True, "action": "receive", "pass": 2}
    trace = _run(tmp_path, [RECEIVE1, again, again, DONE],
                 env=f"RECV_DIES=sdb:flush RECV2_QUIET=1 RECV_COUNT=2 RECV_RANGES=0-0,2-2 "
                     f"NET_TICK=1 NETLOST_AFTER_S=3 {SLOW_PASS}")["trace"]
    assert "QUIET2" in trace and "PASS2-END" in trace, trace
    in_repair = trace[trace.index("QUIET2"):trace.index("PASS2-END")]
    assert in_repair.count("beep netlost") == 1, trace
    assert "DONE sda" in trace and "DONE sdb" not in trace and trace[-1] == "rc=0", trace
