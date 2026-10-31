"""מנוע השידור — הפעלת udp-sender כשסבב עובר ל"משדר".

הרצת התהליכים מוזרקת, ולכן כל הלוגיקה נבדקת בלי udpcast מותקן: מה
בדיוק בשורת הפקודה, באיזה סדר רצות המחיצות, ומה קורה כשמשהו נכשל.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.rounds import sender as sender_module
from server.library.images import ImageLibrary
from server.rounds.room import active_round, close_round, round_label
from server.rounds.sender import (
    DEFAULT_RETRIES_UNTIL_DROP,
    DEFAULT_START_TIMEOUT,
    SenderEngine,
)
from server.rounds.sessions import SessionError

from conftest import MANIFEST_256, hello_body, setup_classroom, write_image

#: לוג של שידור שהתחיל באמת — ברירת המחדל ל-Recorder, כדי שטסט שמצפה
#: ל-`done` לא ייכשל אחרי #438 רק כי לא כתב את הראיה החיובית.
TRANSFER_LOG = "Starting transfer:\n"
#: הלוג שנמדד על דביאן 13 מול udpcast 20120424 כשאיש לא הצטרף: קוד 0,
#: בלי Starting transfer ובלי "No participants... exiting".
NOBODY_JOINED_LOG = (
    "Udp-sender 20120424\n"
    "Using full duplex mode\n"
    "Using mcast address 234.44.3.1\n"
    "UDP sender for /tmp/nc.bin at 10.44.3.1 on eth3\n"
    "Broadcasting control to 10.44.3.255\n"
)


class FakeProcess:
    """תהליך מזויף: נחסם עד ש-`release` נקרא, בדיוק כמו udp-sender שמחכה."""

    def __init__(self, cmd, code=0, block=False, delay=0.0,
                 ignore_term=False, ignore_kill=False):
        self.cmd = cmd
        self.code = code
        self.terminated = False
        self.killed = False
        self.wait_calls = 0
        self.delay = delay          # כמה זמן `wait` באמת לוקח (#341)
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        # PID שלא קיים ב-/proc: אחרי poll() שאמר מת, אישור ה-PID לא
        # יתנגש בתהליך אמיתי על מעבדת ה-VM.
        self.pid = 10_000_000
        self._gate = threading.Event()
        if not block:
            self._gate.set()

    def wait(self):
        self.wait_calls += 1
        if self.delay:
            threading.Event().wait(self.delay)
        self._gate.wait(timeout=5)
        return self.code

    def poll(self):
        return self.code if self._gate.is_set() else None

    def terminate(self):
        self.terminated = True
        if not self.ignore_term:
            self.code = -15
            self._gate.set()

    def kill(self):
        self.killed = True
        if not self.ignore_kill:
            self.code = -9
            self._gate.set()

    def release(self):
        self._gate.set()


class Recorder:
    def __init__(self, code=0, block=False, delay=0.0, log_text=TRANSFER_LOG,
                 ignore_term=False, ignore_kill=False):
        self.commands: list[list[str]] = []
        self.processes: list[FakeProcess] = []
        self.code = code
        self.block = block
        self.delay = delay
        self.log_text = log_text
        self.ignore_term = ignore_term
        self.ignore_kill = ignore_kill
        self.spawned = threading.Event()

    def __call__(self, cmd):
        if self.log_text is not None:
            # לוג פר-מחיצה, בדיוק כמו run_process האמיתי: נגזר מ---file,
            # כך שהמנוע יקרא את הקובץ שהמחיצה הזו כתבה (ולא ידרוס קודמתה).
            sender_module.partition_log(
                cmd[cmd.index("--file") + 1]
            ).write_text(self.log_text, errors="replace")
        process = FakeProcess(
            cmd, self.code, self.block, self.delay,
            self.ignore_term, self.ignore_kill,
        )
        # ‏processes לפני commands: טסט שמחכה ל-len(commands) ומיד ניגש ל-processes[n]
        # נפל ב-IndexError תחת עומס (שער סבב 14 ו-15, 30/10) -- התהליך עוד לא נרשם.
        self.processes.append(process)
        self.commands.append(cmd)
        self.spawned.set()
        return process


@pytest.fixture()
def free_ports(monkeypatch):
    """‏`port_holders` קורא את `/proc/net/udp`, שאינו קיים בווינדוס — ושם
    כל טסט כאן מת בכשל **סביבתי** (#312) שקובר את ההתנהגות הנבדקת.

    מוחלף כאן **רק מקור האמת על הפורטים**: הלוגיקה של `_ports_are_free`
    ושל `_port_verdict` רצה כרגיל ומקבלת תשובה חיובית "נבדק ונמצא פנוי",
    ולא "לא הצלחנו לבדוק" שמקופל להצלחה (עיקרון 5).
    """
    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])


@pytest.fixture()
def library(tmp_path):
    write_image(tmp_path, MANIFEST_256)
    return ImageLibrary(tmp_path)


def wait_for(predicate, timeout=5.0):
    deadline = threading.Event()
    for _ in range(int(timeout / 0.02)):
        if predicate():
            return True
        deadline.wait(0.02)
    return False


# --- שורת הפקודה ------------------------------------------------------------


def test_one_transmission_per_partition_in_manifest_order(library, free_ports):
    """סעיף 7: שידור אחד לכל קובץ מחיצה, בסדר שבמניפסט."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 12})
    assert wait_for(lambda: engine.status()["state"] == "done")

    assert len(recorder.commands) == 2
    files = [cmd[cmd.index("--file") + 1] for cmd in recorder.commands]
    assert [f.rsplit("\\", 1)[-1].rsplit("/", 1)[-1] for f in files] == [
        "p1.esp.pcl.zst", "p3.win.pcl.zst",     # לפי סדר המניפסט
    ]


def test_the_command_carries_the_receiver_count_and_portbase(library, free_ports):
    # ‏portbase גבוה ולא זה של ההפצה: אפילו טסט שמעביר אותו במפורש לא
    # נוגע בפורטים שמכונה אמיתית מאזינה להם (#156).
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, portbase=21000, interface="eth1")
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 12})
    assert wait_for(lambda: engine.status()["state"] == "done")

    cmd = recorder.commands[0]
    assert cmd[0] == "udp-sender"
    assert cmd[cmd.index("--min-receivers") + 1] == "12"
    assert cmd[cmd.index("--portbase") + 1] == "21000"
    assert cmd[cmd.index("--interface") + 1] == "eth1"
    assert "--nokbd" in cmd                      # אין מקלדת בחדר השרתים


def test_every_partition_sender_announces_itself_fast(library, free_ports):
    """udp-sender מחכה הודעת hello אחת (ברירת מחדל 2000ms) בין "מקבל התחבר"
    ל"משדר", ובכל מחיצה מחדש: ארבע מחיצות = 8ש' על ברזל (17/10,
    ses_fbd8f9ca). `--rexmit-hello-interval` מקטין את ההמתנה הזו (loopback:
    2.83ש' -> 1.02ש' ב-200). החבילה אומרת שהדגל קיים בכל מחיצה, והערך שלו
    מתחת לברירת המחדל של udpcast -- ולא שהוא בדיוק 200.

    **בקרה שלילית:** הסרת הדגל מ-`command_for` מפילה את שני ה-assert."""
    engine = SenderEngine(library, runner=Recorder())
    for number in (1, 2, 4):          # הראשונה, השנייה, והרביעית (`later`)
        cmd = engine.command_for("x", 1, number)
        assert "--rexmit-hello-interval" in cmd, (
            f"partition {number}: without it udp-sender waits udpcast's 2000ms"
            " default between the receiver connecting and the transfer starting")
        assert 0 < int(cmd[cmd.index("--rexmit-hello-interval") + 1]) < 2000


def test_the_bitrate_cap_reaches_the_command_when_configured(library, free_ports):
    """רשת מהירה מהדיסקים מפילה מקבלים ("Dropped by server", ‏#24) —
    הרסן עובר ל-udp-sender רק כשהוגדר, וברירת המחדל נקייה ממנו."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder, max_bitrate="200m")
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--max-bitrate") + 1] == "200m"

    bare = SenderEngine(library, runner=Recorder())
    assert "--max-bitrate" not in bare.command_for("x", 3)


def test_the_bitrate_cap_is_tunable_by_env(library, free_ports, monkeypatch):
    """הרסן ניתן לכוונון דרך `IMAGESRV_MAX_BITRATE` בלי בנייה מחדש —
    רשת מהירה מהדיסקים דורשת רסן, ולא רוצים לקבע 100m בהארד-קוד.

    **בקרה שלילית:** קריאת ה-env ב-`__init__` היא מה שמעבירה אותו
    לפקודה; התעלמות ממנה תשאיר את הפקודה בלי `--max-bitrate`."""
    monkeypatch.setenv("IMAGESRV_MAX_BITRATE", "150m")
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert "--max-bitrate" in cmd, "ה-env לא הגיע לפקודה"
    assert cmd[cmd.index("--max-bitrate") + 1] == "150m"


def test_the_drop_ceiling_keeps_the_slow_receiver_and_is_not_udpcast_default(
    library, free_ports,
):
    """‏11/09/2026 (הכרעת נדב): מאמצים את FOG/Clonezilla — **להאט למקבל
    האיטי ולא להפיל אותו** ("dynamic bitrate"). אימג' גדול (67GB דחוס)
    נפל ב-~90% בשידור: ‏back-pressure בזנב הפיל מקבלים איטיים-אך-חיים אחרי
    ‏15 בקשות ACK, המקבל שהופל המתין לנצח, ה---receive-timeout שלו נורה,
    והזרם נקטע. תקרת נטישה **גבוהה** משאירה את האיטי בקבוצה.

    ⚠️ **היפוך מודע של #437.** הערך היה 15 כדי שמקבל *מת* ייזרק תוך
    שניות ("כשל בתחנה אחת לא עוצר את השאר"). כעת מקבל מת מקפיא את החדר
    כ-190ש' לפני שהוא נזרק — מחיר שנדב הכריע לשלם כדי שאימג' הדגל לא
    ייכשל לגמרי. הערך ניתן לכוונון דרך `IMAGESRV_RETRIES_UNTIL_DROP`.

    ⚠️ **מה הטסט הזה בודק, ומה לא.** הוא בודק שהתקרה **גבוהה מספיק כדי
    לא להפיל מקבל איטי** ואינה נשארת על ברירת המחדל של udpcast. הוא
    **אינו** בודק על ברזל שהזרם באמת שורד מקבל איטי — לזה צריך שני
    `udp-receiver` אמיתיים, ו-`tests/hygiene.py` אוסר על pytest להריץ
    udpcast אמיתי (#79).

    **בקרה שלילית:** החזרת `DEFAULT_RETRIES_UNTIL_DROP` ל-15 (הערך הישן)
    מפילה את סף `>= 100`; הסרת הדגל מ-`command_for` מפילה את בדיקת
    הנוכחות."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert "--retries-until-drop" in cmd, (
        "בלי הדגל udpcast מוותר רק אחרי 200 בקשות — התקרה חייבת להיאמר במפורש"
    )
    dropped_after = int(cmd[cmd.index("--retries-until-drop") + 1])
    assert dropped_after == DEFAULT_RETRIES_UNTIL_DROP
    assert dropped_after >= 100, (
        f"{dropped_after} בקשות ACK נמוך מדי: מקבל איטי-אך-חי נזרק בזנב "
        "אימג' גדול, וזה הכשל של ~90% ש-11/09 בא לתקן"
    )
    # ‏--async מוותר על ה-ACK לגמרי: דאטגרם אבוד הופך לדיסק פגום בשקט,
    # וזה בדיוק מה שעיקרון 4 ("אין זריקת בלוק בשקט") אוסר. תקרה גבוהה
    # אינה ויתור על ה-ACK — היא רק ממתינה לאיטי יותר לפני שמוותרים עליו.
    assert "--async" not in cmd


def test_the_drop_ceiling_is_tunable_by_env(library, free_ports, monkeypatch):
    """‏`IMAGESRV_RETRIES_UNTIL_DROP` מאפשר למפעיל להחזיר את התקרה אם
    הקפיאה בת ה-190ש' על מקבל מת (מחיר ההיפוך של #437) גרועה מהתקלה
    שהיא מונעת — בלי בנייה מחדש.

    **בקרה שלילית:** קריאת ה-env ב-`__init__` היא מה שהופך אותה לפעילה;
    התעלמות ממנה תשאיר את הפקודה על 200 ותפיל את ההשוואה ל-'42'."""
    monkeypatch.setenv("IMAGESRV_RETRIES_UNTIL_DROP", "42")
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--retries-until-drop") + 1] == "42"


def test_a_nonpositive_retry_ceiling_falls_back_to_the_default(
    library, free_ports, monkeypatch,
):
    """‏`IMAGESRV_RETRIES_UNTIL_DROP=0` או `-5` הוא הגדרה שגויה, לא בקשה:
    udp-sender סופר int חתום, ו-`0`/שלילי זורקים את המקבל הראשון שמפספס
    ACK כמעט מיד — בדיוק כשל ה-90% שהערך קיים כדי למנוע. הערך נופל
    לברירת המחדל (200), לא עובר כמו-שהוא לפקודה.

    **בקרה שלילית:** הסרת ה-`if value < 1` תעביר `0`/`-5` ל-argv
    ותפיל את ההשוואה ל-'200'."""
    for bad in ("0", "-5"):
        monkeypatch.setenv("IMAGESRV_RETRIES_UNTIL_DROP", bad)
        recorder = Recorder()
        engine = SenderEngine(library, runner=recorder)
        engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
        assert wait_for(lambda: engine.status()["state"] == "done")
        cmd = recorder.commands[0]
        got = cmd[cmd.index("--retries-until-drop") + 1]
        assert got == str(DEFAULT_RETRIES_UNTIL_DROP), (
            f"IMAGESRV_RETRIES_UNTIL_DROP={bad!r} הגיע לפקודה כ-{got!r} — "
            "‏0/שלילי זורק מקבל מיד ומחזיר את כשל ה-90%"
        )


def test_a_round_with_no_joiners_still_asks_for_one_receiver(library, free_ports):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 0})
    assert wait_for(lambda: engine.status()["state"] == "done")
    cmd = recorder.commands[0]
    assert cmd[cmd.index("--min-receivers") + 1] == "1"


# --- כשלים ------------------------------------------------------------------


def test_the_command_caps_the_wait_for_the_first_receiver(library, free_ports):
    """‏#341: בלי `--start-timeout` שידור שאיש לא הצטרף אליו נתקע לנצח —
    ‏`--max-wait` מתחיל לספור רק מהמקבל הראשון."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")

    cmd = recorder.commands[0]
    assert "--start-timeout" in cmd
    assert cmd[cmd.index("--start-timeout") + 1] == str(DEFAULT_START_TIMEOUT)


def test_later_partitions_wait_longer_for_the_room_than_the_first(library, free_ports):
    """‏#957 (FOG GH-536): למחיצה הראשונה כל המכונות מגיעות יחד מהרשת,
    וההמתנה שם היא "המפעיל לא התחיל" — 120 מהמקבל הראשון, 180 בסך הכול.
    מהשנייה כל מכונה מגיעה כשסיימה **לכתוב** את הקודמת, ומגירה איטית
    מאחרת ביותר מ-120 שניות אחרי המהירה; השולח היה מתחיל את ה-ESP
    בלעדיה. לכן `--max-wait` למחיצות 2+ הוא `max(600, max_wait)`,
    ו-`--start-timeout` — שב-udpcast מגביל את **כל** שלב ההמתנה, לא רק
    את המקבל הראשון — מכיל אותו: 600 + 180.

    **בקרה שלילית:** על `command_for` הישן שתי הפקודות זהות (120/180),
    וההשוואה של הפקודה השנייה נופלת."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")

    first, second = recorder.commands
    assert first[first.index("--max-wait") + 1] == "120"
    assert first[first.index("--start-timeout") + 1] == "180"
    assert second[second.index("--max-wait") + 1] == "600"
    assert second[second.index("--start-timeout") + 1] == "780"
    # שאר הדגלים לא זזים בין המחיצות — רק שתי ההמתנות (והקובץ).
    def without(cmd, flags=("--max-wait", "--start-timeout", "--file")):
        out, skip = [], False
        for word in cmd:
            if skip or word in flags:
                skip = word in flags
                continue
            out.append(word)
        return out
    assert without(first) == without(second)


def test_a_first_wait_longer_than_ten_minutes_is_not_shortened_for_later_partitions():
    """‏`max(600, max_wait)`: מפעיל שהגדיר המתנה ארוכה מעשר דקות לראשונה
    מקבל לפחות אותה גם לבאות — הבאות לעולם אינן קצרות מהראשונה."""
    engine = SenderEngine(library=None, max_wait=900, start_timeout=180)
    later = engine.command_for(Path("p2.pcl.zst"), 2, number=2)
    assert later[later.index("--max-wait") + 1] == "900"
    assert later[later.index("--start-timeout") + 1] == "1080"
    first = engine.command_for(Path("p1.pcl.zst"), 2, number=1)
    assert first[first.index("--max-wait") + 1] == "900"
    assert first[first.index("--start-timeout") + 1] == "180"
    # ומה שמחשב הבנייה מקבל (ממשק 3) הוא אותם מספרים בדיוק.
    params = engine.multicast_params()
    assert (params["max_wait_later"], params["start_timeout_later"]) == (900, 1080)


def test_nobody_joining_fails_and_says_that_nobody_joined(library, free_ports):
    """‏#438: udp-sender יוצא 0 בלי Starting transfer כשאיש לא הצטרף.

    זה הצורה שנמדדה מול udpcast 20120424 — לא קוד 1, ולא הזנב.
    """
    recorder = Recorder(code=0, log_text=NOBODY_JOINED_LOG)
    engine = SenderEngine(library, runner=recorder, start_timeout=0.02)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")

    error = engine.status()["error"]
    assert "אף מחשב לא הצטרף" in error
    assert "0.02" in error                 # התקרה נאמרת, ולא רק שפקעה
    assert "אימג'" in error or "פגום" in error
    assert len(recorder.commands) == 1     # לא ממשיכים למחיצה הבאה


def test_an_unreadable_sender_log_is_not_a_successful_send(
    library, free_ports, monkeypatch, tmp_path,
):
    """קובץ לוג חסר אינו "איש לא הצטרף" ואינו הצלחה (#438, עיקרון 5)."""
    monkeypatch.setattr(sender_module, "SENDER_LOG", tmp_path / "missing.log")
    recorder = Recorder(code=0, log_text=None)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    error = engine.status()["error"]
    assert "לא הצלחנו לקרוא" in error
    assert "אף מחשב לא הצטרף" not in error


def test_a_started_transfer_that_then_fails_is_not_blamed_on_nobody_joining(
    library, free_ports,
):
    """מחיצה שכן התחילה להשתדר ונפלה — הלוג מכיל Starting transfer —
    אינה 'אף מחשב לא הצטרף'. זה אבחון שלא נבדק (עיקרון 5, ‏#438)."""
    recorder = Recorder(code=1, log_text="Starting transfer: /x\nbroken\n")
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")

    error = engine.status()["error"]
    assert "אף מחשב לא הצטרף" not in error
    assert "קוד 1" in error


def test_a_failed_partition_stops_the_round(library, free_ports):
    recorder = Recorder(code=3)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    # לא ממשיכים למחיצה הבאה אחרי כשל.
    assert len(recorder.commands) == 1
    assert "קוד 3" in engine.status()["error"]
    # כישלון מהיר אינו פקיעת המתנה: אסור לתלות בו אבחון שלא נבדק.
    assert "אף מחשב לא הצטרף" not in engine.status()["error"]


def test_each_partition_writes_its_own_log_so_earlier_evidence_survives(
    library, free_ports,
):
    """באג הראיות (11/09): ‏run_process פתח קובץ לוג יחיד ב-"wb" לכל
    מחיצה, ולכן תחילת המחיצה השנייה מחקה את לוג הראשונה — הראיה מדוע נפל
    השידור הגדול נמחקה במחיצה שאחריה. כעת לוג נפרד לכל מחיצה.

    **בקרה שלילית:** החזרת הגזירה כך ש-`partition_log` תחזיר תמיד את
    ‏`SENDER_LOG` (קובץ יחיד, ההתנהגות הישנה) גורמת ל-log1 == log2,
    ומחיצה 2 שכותבת 'boom' דורסת את ה-Starting transfer של מחיצה 1 —
    ואז שתי הטענות למטה נופלות."""
    calls: list[str] = []

    def runner(cmd):
        part = cmd[cmd.index("--file") + 1]
        calls.append(part)
        first = len(calls) == 1
        sender_module.partition_log(part).write_text(
            TRANSFER_LOG if first else "Starting transfer: /x\nboom\n",
            errors="replace",
        )
        return FakeProcess(cmd, code=0 if first else 3)

    engine = SenderEngine(library, runner=runner)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")

    assert len(calls) == 2                       # שתי המחיצות רצו
    log1 = sender_module.partition_log(calls[0])
    log2 = sender_module.partition_log(calls[1])
    assert log1 != log2                          # קובץ נפרד לכל מחיצה
    # הראיה של מחיצה 1 שרדה את השידור של מחיצה 2:
    assert sender_module.TRANSFER_STARTED in log1.read_text()


def test_same_partition_basename_on_different_disks_keeps_both_logs(
    monkeypatch, tmp_path,
):
    """שני דיסקים רשאים לשאת אותו basename; רכיב הדיסק מבדיל את הראיות."""
    monkeypatch.setattr(sender_module, "SENDER_LOG", tmp_path / "sender.log")
    relative_a = Path("sda") / "p3.win.pcl.zst"
    relative_b = Path("sdb") / "p3.win.pcl.zst"
    full_a = tmp_path / "img_7f3a91" / relative_a
    full_b = tmp_path / "img_7f3a91" / relative_b

    log_a = sender_module.partition_log(full_a)
    log_b = sender_module.partition_log(full_b)
    assert log_a == sender_module.partition_log(relative_a)
    assert log_b == sender_module.partition_log(relative_b)
    assert log_a != log_b

    log_a.write_text("evidence from sda", encoding="utf-8")
    log_b.write_text("evidence from sdb", encoding="utf-8")
    assert log_a.read_text(encoding="utf-8") == "evidence from sda"
    assert log_b.read_text(encoding="utf-8") == "evidence from sdb"


def test_a_missing_image_fails_before_spawning_anything(library):
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_ghost", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert recorder.commands == []


def test_a_missing_partition_file_fails_loudly(tmp_path, free_ports):
    write_image(tmp_path, MANIFEST_256)
    (tmp_path / MANIFEST_256["id"] / "p3.win.pcl.zst").unlink()
    recorder = Recorder()
    engine = SenderEngine(ImageLibrary(tmp_path), runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert wait_for(lambda: engine.status()["state"] == "failed")
    assert "p3.win.pcl.zst" in engine.status()["error"]
    assert len(recorder.commands) == 1           # הראשונה כן שודרה


def test_stopping_terminates_the_running_transmission(library, free_ports):
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine.status()["state"] == "sending")

    engine.stop("ses_1")
    assert wait_for(lambda: recorder.processes[0].terminated)
    assert wait_for(lambda: engine.status()["state"] == "stopped")
    # לא עוברים למחיצה הבאה אחרי עצירה.
    assert len(recorder.commands) == 1


def test_stop_escalates_to_sigkill_when_sigterm_is_ignored(
    library, free_ports, monkeypatch,
):
    """udpcast חוסם SIGTERM ב-doTransfer — TERM לבד משאיר יתום (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    recorder = Recorder(block=True, ignore_term=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine._process is not None)

    left = engine.stop("ses_1")
    process = recorder.processes[0]
    assert process.terminated
    assert process.killed
    assert left is None
    assert process.poll() is not None


def test_stop_does_not_claim_success_when_the_pid_is_still_alive(
    library, free_ports, monkeypatch,
):
    """‏"שלחנו SIGKILL" אינו "התהליך מת" — בלי ראיה זו אינה הצלחה (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    monkeypatch.setattr(sender_module, "STOP_KILL_WAIT", 0.05, raising=False)
    recorder = Recorder(block=True, ignore_term=True, ignore_kill=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine._process is not None)

    left = engine.stop("ses_1")
    process = recorder.processes[0]
    assert left == process.pid
    assert process.killed
    process.release()


# --- החיבור לסבב האמיתי ------------------------------------------------------


def test_the_round_starts_the_sender_and_closing_stops_it(server_with_sender, free_ports):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]

    # ה-hello הראשון מצרף; הבא כבר מוצא סבב בשל ומפעיל את השידור.
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: len(recorder.commands) >= 1)

    status = server["admin"].get("/api/console/overview").json()["sender"]
    assert status["session_id"] == session
    assert status["partitions"] == 2

    server["admin"].post(f"/api/console/sessions/{session}/close",
                         json={"confirm_name": "Office 2024 Standard"})
    assert wait_for(lambda: recorder.processes[0].terminated)


def test_a_member_that_failed_is_not_waited_for_in_the_next_partition(
    server_with_sender, free_ports,
):
    """‏#1430 (מעבדת ה-VM, 15/10, ‏`ses_989ab0ed`): מכונה אחת מתוך שתיים
    דיווחה `failed` (שתי המגירות שלה נפסלו בבדיקת הכתיבה), והשולח המשיך
    לבקש **2** מקבלים: 120ש' במחיצה 1, ו-600ש' במחיצה 2 — שבהן המכונה
    התקינה פקעה. כשל במכונה אחת עצר את השנייה.

    כאן: שני חברים, השידור של מחיצה 1 יצא עם 2, ואחד מהם מדווח `failed`
    לפני שמחיצה 2 יוצאת — ומחיצה 2 מבקשת מקבל אחד.

    **בקרה שלילית:** על הקוד הישן ‏`receivers` נקבע פעם אחת מ-`joined`,
    ומחיצה 2 יוצאת עם `--min-receivers 2`."""
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).json()["id"]
    recorder.block = True       # מחיצה 1 "משדרת" עד שהטסט משחרר אותה

    for mac in (ids["mac1"], ids["mac2"], ids["mac1"]):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(mac))
    assert recorder.spawned.wait(timeout=5)
    first = recorder.commands[0]
    assert first[first.index("--min-receivers") + 1] == "2"

    assert server["anon"].post("/api/v1/agent/progress", json={
        "session_id": session, "mac": ids["mac2"], "state": "failed",
        "targets": [{"dev": "sda", "bytes_written": 0, "bytes_total": 1,
                     "state": "failed", "error": "איטי"}],
    }).json()["ok"]
    recorder.processes[0].release()

    assert wait_for(lambda: len(recorder.commands) >= 2)
    second = recorder.commands[1]
    assert second[second.index("--min-receivers") + 1] == "1", second
    # ההמתנה הארוכה של #957 נשארת — רק מי שממתינים לו השתנה.
    assert second[second.index("--max-wait") + 1] == "600"
    recorder.processes[1].release()


def test_an_unreadable_live_count_keeps_the_waves_count(library, free_ports):
    """‏#1430, עיקרון 5: ספירה שנכשלה אינה "אף אחד לא חי" ואינה 1 — היא
    הצילום של הגל, כמו לפני התיקון. **ושומר:** ספירה תקינה כן מגיעה לפקודה."""
    recorder = Recorder()

    def broken(_session_id):
        raise RuntimeError("database is locked")

    engine = SenderEngine(library, runner=recorder, live_receivers=broken)
    engine.start({"id": "ses_1", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert {c[c.index("--min-receivers") + 1] for c in recorder.commands} == {"3"}

    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder,
                          live_receivers=lambda _session_id: 0)
    engine.start({"id": "ses_2", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert {c[c.index("--min-receivers") + 1] for c in recorder.commands} == {"1"}


def test_sender_failure_is_a_terminal_round_and_member_boots_local(
    server_with_sender, free_ports,
):
    """בקרה שלילית #854: על main ‏``send_failed`` רק נרשם ביומן;
    הטענות למטה מוצאות ``running`` ושידור פעיל, ולכן נכשלות."""
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]
    recorder.code = 3

    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    recorder.processes[0].release()
    ctx = server["ctx"]
    assert wait_for(lambda: ctx.conn.execute(
        "SELECT state FROM sessions WHERE id = ?", (session,)
    ).fetchone()["state"] == "failed")

    failed = ctx.conn.execute(
        "SELECT state, failed_reason FROM sessions WHERE id = ?", (session,)
    ).fetchone()
    member = ctx.conn.execute(
        "SELECT state, error FROM session_members WHERE session_id = ? AND mac = ?",
        (session, ids["mac1"]),
    ).fetchone()
    assert failed["state"] == "failed" and failed["failed_reason"]
    assert member["state"] == "failed" and member["error"] == failed["failed_reason"]
    assert ctx.store.active_broadcast() is None
    reboot = server["anon"].post(
        "/api/v1/agent/hello", json=hello_body(ids["mac1"])
    ).json()
    assert reboot["session"] is None and reboot["task"] is None
    shown = server["admin"].get("/api/console/overview").json()["session"]
    assert shown["state"] == "failed" and shown["failed_reason"]


def test_the_journal_reports_the_broadcast_in_hebrew(server_with_sender, free_ports):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert wait_for(lambda: any(
        r["event"] == "send_start"
        for r in server["admin"].get("/api/console/journal").json()))

    row = next(r for r in server["admin"].get("/api/console/journal").json()
               if r["event"] == "send_start")
    assert row["label"] == "השידור יצא לדרך"
    assert "Office 2024 Standard" in row["text"]
    assert "2 מחיצות" in row["text"]
    assert "img_" not in row["text"]


def _active_room_with_closed_wave(ctx, wave_id: str) -> None:
    """סבב חדר פעיל שגל השידור שלו כבר סגור — הנתיב שבו close_round
    לא היה קורא ל-store.close, ולכן sender.stop לא רץ (#439)."""
    ctx.conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (wave_id,))
    ctx.conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state,"
        " wave_session_id, opened_by, created_at) VALUES (?, ?, ?, 'active', ?, ?, ?)",
        ("room_438", "img_7f3a91", 2, wave_id, "noc", "2026-01-01T00:00:00"),
    )
    ctx.conn.commit()


def test_close_round_stops_the_sender_even_when_the_wave_is_already_closed(
    server_with_sender, free_ports,
):
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    ctx = server["ctx"]
    assert wait_for(lambda: ctx.sender._process is not None)

    _active_room_with_closed_wave(ctx, session)
    # ‏#533: הסגירה דורשת את שם האימג'. נגזר מהסבב דרך `round_label`
    # ולא מחרוזת קבועה — טסט שמקבע תווית נשבר כשהתווית משתנה,
    # ומסתיר בדיוק את מה שהוא אמור לשמור עליו.
    close_round(ctx, "noc", round_label(ctx, active_round(ctx.conn)))
    assert wait_for(lambda: recorder.processes[0].terminated)
    row = ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = 'room_438'"
    ).fetchone()
    assert row["state"] == "closed"


def test_close_round_does_not_report_ok_when_the_sender_is_still_alive(
    server_with_sender, monkeypatch, free_ports,
):
    """‏{"ok": true} כשהמשדר חי הוא בדיוק עיקרון 5 בכיוון ההפוך (#439)."""
    monkeypatch.setattr(sender_module, "STOP_TERM_WAIT", 0.05, raising=False)
    monkeypatch.setattr(sender_module, "STOP_KILL_WAIT", 0.05, raising=False)
    server, recorder = server_with_sender
    recorder.ignore_term = True
    recorder.ignore_kill = True
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    ctx = server["ctx"]
    assert wait_for(lambda: ctx.sender._process is not None)

    _active_room_with_closed_wave(ctx, session)
    _typed = round_label(ctx, active_round(ctx.conn))
    with pytest.raises(SessionError, match="udp-sender עדיין רץ"):
        close_round(ctx, "noc", _typed)
    row = ctx.conn.execute(
        "SELECT state FROM room_rounds WHERE id = 'room_438'"
    ).fetchone()
    assert row["state"] == "active"
    recorder.processes[0].release()


# --- #773: סגירה ישנה אינה הורגת session חדש --------------------------------


def test_a_stale_stop_does_not_kill_a_newer_session(library, free_ports):
    """**#773.** thread שסוגר את session A ומתעכב לפני ה-callback, בזמן
    ש-session B כבר משדר, היה מדליק את ה-stop של B ומוריד שידור חי —
    B נשאר `running` אך השידור נעצר. `stop` עם `session_id` שאינו
    ה-session הרץ הוא no-op."""
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_B", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine.status()["state"] == "sending")

    # הסגירה הישנה של session A — אסור שתיגע ב-B.
    left = engine.stop("ses_A")
    assert left is None                              # no-op, לא הרג כלום
    assert not recorder.processes[0].terminated, "‏stop ישן הרג שידור חי"
    assert engine.status()["state"] == "sending"
    assert not engine._stop.is_set(), "‏stop ישן הדליק את דגל העצירה של B"

    # והסגירה הנכונה של B עדיין עובדת.
    engine.stop("ses_B")
    assert wait_for(lambda: recorder.processes[0].terminated)
    assert wait_for(lambda: engine.status()["state"] == "stopped")
    recorder.processes[0].release()


def test_stop_with_no_session_id_still_stops_unconditionally(library, free_ports):
    """רדיוס הפגיעה: `stop()` בלי ארגומנט (סגירה מפורשת) עדיין עוצר —
    השומר של #773 חוסם רק `session_id` שאינו תואם, לא `None`."""
    recorder = Recorder(block=True)
    engine = SenderEngine(library, runner=recorder)
    engine.start({"id": "ses_C", "image_id": "img_7f3a91", "joined": 2})
    assert recorder.spawned.wait(timeout=5)
    assert wait_for(lambda: engine.status()["state"] == "sending")

    engine.stop()
    assert wait_for(lambda: recorder.processes[0].terminated)
    assert wait_for(lambda: engine.status()["state"] == "stopped")


def test_shutdown_reaps_a_sender_that_has_already_exited(library):
    """#1382: an exited udp-sender is still our child and must be waited."""
    process = FakeProcess(["udp-sender"])
    engine = SenderEngine(library, runner=lambda _cmd: process)
    with engine._lock:
        engine._process = process

    assert engine.close() is None
    assert process.wait_calls == 1


def test_a_simulated_sender_never_runs_udp_sender_and_never_fails(library, monkeypatch):
    """‏#854 המשך: בסימולציה (ווינדוס, ‏CI בלי udpcast) אין udp-sender ואין
    טבלת ‎/proc/net/udp — ומאז שכשל השולח הוא כשל הסבב, הסימולציה הייתה
    נופלת. ‏`simulate=True`: אף בדיקת פורט, אף תהליך, אירוע `send_simulated`
    ביומן, "משדר" עד שהסבב נסגר — ואז `send_stopped`, לא `send_failed`."""
    events = []
    recorder = Recorder()
    # בדיקת הפורט חייבת לא לרוץ בכלל — ‏OSError כאן היה כישלון
    monkeypatch.setattr(sender_module, "_inodes_on_port", lambda port: None)
    engine = SenderEngine(library, runner=recorder, simulate=True,
                          on_event=lambda e, d: events.append(e))
    engine.start({"id": "ses_sim", "image_id": "img_7f3a91", "joined": 3})
    assert wait_for(lambda: engine.status()["state"] == "sending")
    assert recorder.commands == []
    assert "send_simulated" in events and "send_failed" not in events
    engine.stop("ses_sim")
    assert wait_for(lambda: "send_stopped" in events)
    assert "send_failed" not in events


# --- ברזל 17/10: שאלת SMART פתוחה עוצרת את שעון השולח -------------------------

SMART_ASK = {"nonce": "3-2", "port": 2, "verdict": "failed_last", "reason": "נכשל בשיכפול הקודם"}


def _journal_events(ctx) -> list[str]:
    return [r["event"] for r in ctx.conn.execute("SELECT event FROM journal ORDER BY id")]


def test_the_sender_clock_does_not_run_while_a_cloner_is_asked_about_a_disk(
    server_with_sender, free_ports, monkeypatch,
):
    """ברזל 17/10 (‏ses_d6e79f65): ‏send_start ב-08:13:09, המכונה עצרה על חלון
    SMART, ‏smart_answered ב-08:16:00, ו-send_failed ב-08:16:10 — 180 שניות
    בדיוק מ-send_start: ‏`--start-timeout` של udp-sender רץ מתחת לשאלה, והמכונה
    שענתה מצאה שולח שכבר יצא. כאן: כל עוד השאלה פתוחה **אף udp-sender לא
    מופעל**; התשובה משחררת אותו, והיומן אומר את שניהם.

    **בקרה שלילית:** על הקוד הישן השולח מופעל מיד עם תחילת הגל."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    server, recorder = server_with_sender
    ctx = server["ctx"]
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    for _ in range(2):    # מצטרף ושואל; ה-hello השני מוצא סבב בשל ומתחיל את הגל
        server["anon"].post("/api/v1/agent/hello",
                            json={**hello_body(ids["mac1"]), "ask": SMART_ASK})
    assert wait_for(lambda: "send_held_smart" in _journal_events(ctx))
    assert "send_start" in _journal_events(ctx)
    threading.Event().wait(0.3)
    assert recorder.commands == [], "udp-sender הופעל בזמן שהמכונה ממתינה לאדם"

    server["anon"].post("/api/v1/agent/hello", json={
        **hello_body(ids["mac1"]), "ask": SMART_ASK,
        "ask_answered": {"nonce": "3-2", "answer": "skip"}})
    assert recorder.spawned.wait(timeout=5)
    events = _journal_events(ctx)
    assert events.index("send_held_smart") < events.index("send_hold_released")
    first = recorder.commands[0]
    assert first[first.index("--start-timeout") + 1] == str(int(DEFAULT_START_TIMEOUT))
    recorder.processes[0].release()


def test_without_an_open_question_the_sender_starts_at_once(server_with_sender, free_ports):
    """הצד השני: בלי שאלה פתוחה — שום המתנה ושום אירוע חדש ביומן (עובר
    בשני המצבים)."""
    server, recorder = server_with_sender
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    for _ in range(2):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    assert "send_held_smart" not in _journal_events(server["ctx"])
    recorder.processes[0].release()


def test_closing_the_round_during_the_hold_never_starts_udp_sender(library, free_ports, monkeypatch):
    """עצירה בזמן ההמתנה: ‏send_stopped, ואף תהליך לא הופעל."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    events = []
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder,
                          on_event=lambda e, d: events.append(e))
    engine.pending_asks = lambda _sid: ["78:ac:c0:9b:11:c2"]
    engine.start({"id": "ses_hold", "image_id": "img_7f3a91", "joined": 1})
    assert wait_for(lambda: "send_held_smart" in events)
    engine.stop("ses_hold")
    assert wait_for(lambda: "send_stopped" in events)
    assert recorder.commands == [] and "send_failed" not in events


def test_an_unreadable_question_list_does_not_hold_the_sender_forever(library, free_ports):
    """עיקרון 5 לשני הכיוונים: קריאה שנכשלה אינה "יש שאלה" לנצח — משדרים
    כמו לפני התיקון, והיומן של השרת אומר למה."""
    recorder = Recorder()
    engine = SenderEngine(library, runner=recorder)

    def broken(_sid):
        raise RuntimeError("database is locked")

    engine.pending_asks = broken
    engine.start({"id": "ses_x", "image_id": "img_7f3a91", "joined": 1})
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert len(recorder.commands) == 2


def test_a_question_open_past_its_ceiling_releases_only_that_machine(
    server_with_sender, free_ports, monkeypatch,
):
    """סקירת אבטחה 17/10: שאלה שאיש לא עונה עליה אינה מחזיקה את הגל לנצח.
    מעל `ASK_HOLD_MAX_SECONDS` **רק המכונה ששואלת** משתחררת — ‏`failed`
    עם הסיבה, ‏`smart_ask_expired` ביומן — והשולח יוצא לשאר הגל
    (‏`--min-receivers 1`).

    **בקרה שלילית:** על הקוד הישן אין עצירה ואין שחרור: השולח יוצא עם 2,
    ושתי המכונות נשארות חברות חיות."""
    from server.disks import smart_ask
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    server, recorder = server_with_sender
    ctx = server["ctx"]
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).json()["id"]
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "ask": SMART_ASK})
    ctx.conn.execute("UPDATE smart_asks SET asked_at = ? WHERE mac = ?", (
        (datetime.now(timezone.utc)
         - timedelta(seconds=smart_ask.ASK_HOLD_MAX_SECONDS + 60)).isoformat(), ids["mac1"]))
    ctx.conn.commit()
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac2"]))
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "ask": SMART_ASK})

    assert recorder.spawned.wait(timeout=5)
    first = recorder.commands[0]
    assert first[first.index("--min-receivers") + 1] == "1", first
    states = {r["mac"]: (r["state"], r["error"] or "") for r in ctx.conn.execute(
        "SELECT mac, state, error FROM session_members WHERE session_id = ?", (session,))}
    assert states[ids["mac1"]][0] == "failed" and "שוחררה מהגל" in states[ids["mac1"]][1], states
    assert states[ids["mac2"]][0] != "failed", states
    assert "smart_ask_expired" in _journal_events(ctx)
    recorder.processes[0].release()


def test_a_machine_asking_question_after_question_is_released_after_one_ceiling(
    server_with_sender, free_ports, monkeypatch,
):
    """סקירה שנייה 17/10: מכונה ששואלת שלוש שאלות ברצף (nonce חדש לכל דיסק,
    ‏`asked_at` מתאפס בכל אחת) אינה מחזיקה את הגל 3×35 דקות. התקרה נספרת
    מתחילת הגל: כשהיא חלפה, המכונה משתחררת גם ששאלתה הנוכחית צעירה.

    **בקרה שלילית:** על התקרה-לפי-שאלה (‏19a65ad1) השולח ממשיך להמתין; על
    main השולח יוצא מיד עם 2 מקבלים ואיש אינו משתחרר."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    from server.disks import smart_ask
    server, recorder = server_with_sender
    ctx = server["ctx"]
    ids = setup_classroom(server)
    session = server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 2},
    ).json()["id"]
    ask = lambda nonce: {**SMART_ASK, "nonce": nonce}          # noqa: E731
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "ask": ask("1-1")})
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac2"]))
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "ask": ask("1-1")})
    assert wait_for(lambda: "send_held_smart" in _journal_events(ctx))
    for nonce in ("2-2", "3-3"):     # דיסק אחרי דיסק — כל שאלה מאפסת את asked_at
        server["anon"].post("/api/v1/agent/hello", json={
            **hello_body(ids["mac1"]), "ask": ask(nonce)})
    assert recorder.commands == []
    ctx.conn.execute("UPDATE sessions SET started_at = ? WHERE id = ?", (
        (datetime.now(timezone.utc)
         - timedelta(seconds=smart_ask.ASK_HOLD_MAX_SECONDS + 60)).isoformat(), session))
    ctx.conn.commit()

    assert recorder.spawned.wait(timeout=5)
    first = recorder.commands[0]
    assert first[first.index("--min-receivers") + 1] == "1", first
    state = ctx.conn.execute("SELECT state FROM session_members WHERE session_id = ? AND mac = ?",
                             (session, ids["mac1"])).fetchone()["state"]
    assert state == "failed"
    recorder.processes[0].release()


@pytest.mark.parametrize("delay", [0.0, 2.0])
def test_a_question_that_arrives_after_udp_sender_started_rewinds_partition_one(
    server_with_sender, free_ports, monkeypatch, delay,
):
    """ברזל rc2 (17/10, ‏ses_80844e82): ‏send_start ב-14:08:10 והשאלה נרשמה
    **באותה שנייה** (‏asked_at 14:08:10) — ההמתנה לפני ההפעלה כבר עברה,
    udp-sender עלה, ו-send_failed ב-14:11:10 בלי `send_held_smart`. שאלה
    שמגיעה 0–2 שניות **אחרי** שהשולח עלה, כל עוד הוא לא התחיל לשדר (אין
    Starting transfer), הורגת אותו, הגל ממתין, והתשובה מפעילה את מחיצה 1
    מחדש עם שעון מלא.

    **בקרה שלילית:** על הקוד שלפני (בדיקה אחת לפני ההפעלה) udp-sender
    הראשון אינו נהרג, ושום הפעלה שנייה אינה קורית."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    monkeypatch.setattr(sender_module, "PACE_POLL", 0.02)
    server, recorder = server_with_sender
    ctx = server["ctx"]
    recorder.log_text = NOBODY_JOINED_LOG          # מחכה למקבלים — עוד לא שידר בייט
    ids = setup_classroom(server)
    server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    )
    for _ in range(2):
        server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    assert recorder.spawned.wait(timeout=5)
    assert "send_held_smart" not in _journal_events(ctx)      # לא הייתה שאלה כשעלה
    threading.Event().wait(delay)
    server["anon"].post("/api/v1/agent/hello", json={**hello_body(ids["mac1"]), "ask": SMART_ASK})

    assert wait_for(lambda: recorder.processes[0].terminated), "udp-sender של מחיצה 1 לא נהרג"
    assert wait_for(lambda: "send_held_smart" in _journal_events(ctx))
    threading.Event().wait(0.3)
    assert len(recorder.commands) == 1, "הופעל שוב בזמן שהשאלה פתוחה"

    recorder.log_text = TRANSFER_LOG
    server["anon"].post("/api/v1/agent/hello", json={
        **hello_body(ids["mac1"]), "ask": SMART_ASK,
        "ask_answered": {"nonce": "3-2", "answer": "skip"}})
    assert wait_for(lambda: len(recorder.commands) == 2)
    first, again = recorder.commands
    assert first[first.index("--file") + 1] == again[again.index("--file") + 1]   # שוב מחיצה 1
    assert again[again.index("--start-timeout") + 1] == str(int(DEFAULT_START_TIMEOUT))
    assert "send_failed" not in _journal_events(ctx)


def test_a_question_after_the_transfer_started_does_not_kill_it(library, free_ports, monkeypatch):
    """הצד השני: אחרי Starting transfer השולח **אינו** נהרג — מקבלים כבר
    כותבים, ושאלה של מכונה אחרת אינה סיבה לקטוע אותם (עובר בשני המצבים)."""
    monkeypatch.setattr(sender_module, "ASK_HOLD_POLL_S", 0.02)
    monkeypatch.setattr(sender_module, "PACE_POLL", 0.02)
    recorder = Recorder(block=True)                 # TRANSFER_LOG: כבר משדר
    asks: list[str] = []
    engine = SenderEngine(library, runner=recorder)
    engine.pending_asks = lambda _sid: list(asks)
    engine.start({"id": "ses_t", "image_id": "img_7f3a91", "joined": 1})
    assert recorder.spawned.wait(timeout=5)
    asks.append("78:ac:c0:9b:11:c2")
    threading.Event().wait(0.3)
    assert not recorder.processes[0].terminated
    asks.clear()
    recorder.processes[0].release()
    assert wait_for(lambda: len(recorder.commands) == 2)
    recorder.processes[1].release()
    assert wait_for(lambda: engine.status()["state"] == "done")
