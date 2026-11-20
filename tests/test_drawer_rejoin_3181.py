"""#3181 (כותב A, צד הסוכן): מגירה שנשלפה וחזרה נמצאת **כשהקרנל אומר**, מצטרפת מיד לשידור שרץ, ומשלימה רק את מה שהחמיצה.

עד כאן (`drawerback.sh`, #3112) מגירה שחזרה נמצאה רק בנקודות קבועות -- אחרי תשובה של השרת, לפני מחיצה, לפני הקריאה
החוזרת -- והורידה ב-HTTP, ברקע, את **כל** שאר המחיצה, כולל החתיכות שהסבב עוד לא שידר. ברזל 14/11: הקרנל זיהה את
`sdd` תוך שנייה מההחזרה, ובמשך 17 דקות לא נכתבה עליו שורה.

כאן רצות `chunk_partition_drawers`, `drawer_back`, `drawer_plug_tick` ו-`_db_part` האמיתיות (`chunkrecv.sh`,
`drawerback.sh`, `drawerplug.sh`, `drawerfill.sh`) מול הרתמה של test_chunkrecv_2533.py: `chunkcast`, השרת, sysfs,
dmesg ואירוע הקרנל (`$CHUNK_DIR/plug`, מה ש-`imagesrv-uevent` כותב) מזויפים. `neighborfill_run` של כותב B מזויפת
**לפי החוזה שלה** (הרשימה נשארת עם מה שעוד חסר), או אינה מוגדרת כלל.

מה שנבדק הוא מה שהמפעיל והשרת רואים:

1. הזיהוי בתוך המעבר, באירוע -- לא אחרי שהמעבר נגמר, ולא רק כשהשרת עונה משהו אחר מ-`wait`.
2. המדידה: כמה חתיכות חסרות לפי ה-ledger מול האינדקס, **אחרי** ההצטרפות, ביומן ובדיווח (`rejoin`).
3. ההצטרפות: מקלט משלה על אותו מעבר, למגירה הזו בלבד, בזמן שהמעבר של הסבב רץ.
4. ההשלמה: קודם `neighborfill_run`, ומה שנשאר ב-HTTP. אין `neighborfill_run` = נאמר בשם, והכול ב-HTTP.
   רשימה שהשכנה מחזירה ואינה חלק ממה שקיבלה אינה ראיה לכלום.
5. מגירה שנשלפה מדווחת `pulled` (נשלפה, ממתינה) ליד `failed` -- ומגירה שנכשלה מסיבה אחרת, לא.

**בקרה שלילית:** החזרת `chunkrecv.sh`, `drawerback.sh`, `progress.sh` ו-`imagesrv-agent` לבסיס ומחיקת `drawerplug.sh`
ו-`drawerfill.sh` מפילה כאן את כל הטסטים חוץ מהשומר `test_a_drawer_that_failed_for_another_reason_is_not_called_pulled`
(בלי `pulled` בשני המצבים).

ההנחה שההצטרפות נשענת עליה -- שני מקלטים על אותה קבוצה מקבלים שניהם כל חבילה -- נמדדת עם `chunkcast` האמיתי
ב-test_chunkcast_two_receivers_3181.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_chunkrecv_2533 as base
from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_chunk_drawer_returns_2533 import ZAP
from test_chunkrecv_2533 import DONE, RECEIVE1, _run

# the passes are real seconds, and what is asserted is their order -- never how many seconds: not load-sensitive (#2118)
pytestmark = requires_native(("bash", BASH), "jq", why="התשובות של השרת נקראות ב-jq, כמו בסוכן")

PULLED = "RECV_DIES=sdb:vanish RECV_PARTIAL=1 REAL_SLEEP=1 TEST_START_CEILING=30"
# it is pulled four seconds into the pass, pushed back four seconds later as sdc -- the kernel says so -- and the pass goes on
# for eight more. Listening for itself it gets chunks 3-4 (SIDE_STATE): chunk 2 went by while it was out
MID = f"{PULLED} RECV_BACK_AS=sdc PLUG_EVENT=1 RECV_TAIL=8"
GOT_3_4 = "SIDE_STATE='4 0-1,3-4'"
GOT_3 = "SIDE_STATE='3 0-1,3-3'"
WAIT_WHOLE = {"ok": True, "action": "wait", "whole": True}


def _side(trace: list[str]) -> list[str]:
    return [t for t in trace if t.startswith("chunkcast recv") and "/back." in t]


def _applies(trace: list[str]) -> list[str]:
    return [t for t in trace if t.startswith("chunkcast apply")]


def _ranges(tmp_path) -> str:
    return (tmp_path / "chunk" / "back.sdc" / "fetch.ranges").read_text().strip()


def test_a_drawer_pushed_back_inside_a_pass_is_found_at_once_and_listens_to_the_pass_on_the_air(tmp_path):
    """הליבה: sdb נשלפה באמצע המעבר וחזרה כ-sdc, והמעבר נמשך עוד שמונה שניות. היא נמצאת **בתוך** המעבר, לפי
    ה-serial; מקלט משלה מאזין לאותו מעבר, למגירה הזו בלבד; ומה שנשאר לה אחריו -- חתיכה אחת, לא שלוש -- הוא כל מה
    שמורד ב-HTTP."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{MID} {GOT_3_4}")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0", got
    side = _side(trace)
    assert len(side) == 1, trace
    assert "--pass 1" in side[0] and "--part 3" in side[0] and "/dev/sdc3" in side[0] and "/dev/sda3" not in side[0], side
    assert "back.sdc/p3.state" in side[0], side                             # a state of its own: none of the round's files
    # found, joined and listening while the round's pass still runs
    assert trace.index("BACK sdc") < trace.index("SET sdc writing") < trace.index(side[0]) < trace.index("SIDE-END") \
        < trace.index("PASS-END"), trace
    applies = _applies(trace)
    assert len(applies) == 1 and "/dev/sdc3" in applies[0] and "/dev/sda3" not in applies[0], applies
    assert _ranges(tmp_path) == "2-2"                                       # what it missed, and no more
    assert "DONE sdc" in trace and "DONE sda" in trace and not any(t.startswith("sgdisk") for t in trace), trace
    assert "המגירה sdc מצטרפת לשידור החי של מחיצה 3 (מעבר 1)" in log, log
    assert "מדידה: למגירה sdc חסרות 1 מתוך 5 חתיכות במחיצה 3" in log, log
    assert "neighborfill_run אינה מוגדרת בסוכן הזה" in log and "כל החסר של sdc מושלם ב-HTTP" in log, log
    assert not (tmp_path / "targets" / "sdc" / "rejoin").exists()          # whole: the report says nothing more of it
    assert not (tmp_path / "targets" / "sdc" / "catchup").exists()


def test_the_server_hears_once_that_a_drawer_joined_inside_a_pass_is_out_of_the_waves_plan(tmp_path):
    """השרת שומע על מגירה שיצאה רק בדיווח של סוף המעבר, ומגירה שמשלימה לבדה אינה בדיווח הזה. מגירה שחזרה **לפני**
    הדיווח לא הייתה נשמעת כלל -- והשרת היה מתכנן לה את ההשלמה של הגל. היא נאמרת פעם אחת, כ-`failed`."""
    got = _run(tmp_path, [RECEIVE1, {"ok": True, "action": "receive", "pass": 2}, DONE], env=f"{MID} {GOT_3_4}")
    assert got["trace"][-1] == "rc=0", got
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    first = {t["serial"]: t for t in ended[0]["targets"]}
    assert first["SER-sdb"]["failed"] is True and "pulled" not in first["SER-sdb"], ended[0]   # back: not waited for
    assert first["SER-sda"]["failed"] is False
    assert [t["serial"] for t in ended[-1]["targets"]] == ["SER-sda"], ended[-1]               # once
    assert not (tmp_path / "targets" / "sdc" / "untold").exists()


def test_what_it_missed_comes_from_a_neighbour_first_and_nothing_is_fetched_when_the_neighbour_has_it_all(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{MID} {GOT_3_4} NEIGHBOR=all")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    assert "neighborfill sdc 2-2," in trace, trace                          # the drawer, and the list of what it lacks
    assert trace.index("SIDE-END") < trace.index("neighborfill sdc 2-2,"), trace
    assert _applies(trace) == [] and not any(t.startswith("curl") for t in trace), trace
    assert "/dev/sdc3 ok 5 0-4" in (tmp_path / "chunk" / "back.sdc" / "p3.state").read_text().splitlines()
    assert (tmp_path / "targets" / "sdc" / "ledger" / "durable.3").read_text().split() == ["0-4"]   # its ledger follows
    assert "מחיצה 3: 1 מתוך 1 חתיכות הושלמו ל-sdc מהמגירות השכנות; 0 נשארו ל-HTTP" in log, log
    # the measure, as the progress report carried it while the neighbour was read
    seen = json.loads((tmp_path / "rejoin.seen").read_text())
    away = seen.pop("away_s")
    assert seen == {"part": 3, "missing": 1, "chunks": 5, "step": "neighbor"}, seen
    assert isinstance(away, int) and 0 <= away < 60, away


def test_what_a_neighbour_did_not_bring_is_fetched_over_http(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{MID} {GOT_3} NEIGHBOR=left:4-4")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    assert "neighborfill sdc 2-2,4-4," in trace, trace
    assert len(_applies(trace)) == 1 and _ranges(tmp_path) == "4-4", trace   # chunk 2 was the neighbour's: not asked again
    assert "מחיצה 3: 1 מתוך 2 חתיכות הושלמו ל-sdc מהמגירות השכנות; 1 נשארו ל-HTTP" in log, log


@pytest.mark.parametrize("how, said", [
    ("left:0-0", "אינה חלק מהרשימה שקיבלה"),          # a chunk it was never asked for: the list proves nothing
    ("left:2-2,4-4,4-4", "אינה חלק מהרשימה שקיבלה"),  # not a list of ascending runs
    ("unread", "אף חתיכה לא הושלמה ל-sdc מהשכנות"),    # rc 2: the file is as it was
])
def test_a_neighbours_word_that_is_no_evidence_brings_nothing_and_all_of_it_is_fetched(tmp_path, how, said):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{MID} {GOT_3} NEIGHBOR={how}")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    assert said in got["log"], got["log"]
    assert _ranges(tmp_path) == "2-2,4-4", trace


def test_its_own_receiver_does_not_outlive_the_rounds_pass(tmp_path):
    """המקלט שלה התחיל ולא נגמר עם המעבר (כאן: 40 שניות). אחרי שהמקלט של הסבב נגמר הוא נעצר, וההשלמה ממשיכה
    ממה שעמיד אצלה -- היא אינה מחכה לו."""
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{PULLED} RECV_BACK_AS=sdc PLUG_EVENT=1 RECV_TAIL=3 SIDE_S=40 DRAWER_LIVE_TAIL_S=1")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and len(_side(trace)) == 1 and "DONE sdc" in trace, got
    assert "SIDE-END" not in trace, trace
    assert _ranges(tmp_path) == "2-4"                                       # what it had before the pull stays; the rest is fetched


def test_a_drawer_pushed_back_while_the_machine_waits_for_the_server_is_found_then(tmp_path):
    """המעבר נגמר, המכונה שלמה והשרת עונה `wait` (הוא מחכה למכונות אחרות). המגירה חוזרת שתי שניות אחר כך: היא
    נמצאת באירוע, בתוך ההמתנה -- לא כשהשרת יענה משהו אחר."""
    got = _run(tmp_path, [RECEIVE1] + [WAIT_WHOLE] * 8 + [DONE], env=f"{PULLED} PLUG_EVENT=1 RECV_BACK_LATE=2:sdc")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "DONE sdc" in trace, got
    last_question = len(trace) - 1 - trace[::-1].index("POST ended 3")
    assert trace.index("BACK sdc") < trace.index("SET sdc writing") < last_question, trace
    assert _side(trace) == [], trace                                        # no pass on the air: nothing to listen to
    waiting = [b for b in got["bodies"] if b["state"] == "ended"][0]
    out = {t["serial"]: t for t in waiting["targets"]}["SER-sdb"]
    assert out["failed"] is True and out["pulled"] is True, waiting         # pulled, and still waited for


def test_another_disk_pushed_in_is_said_when_the_kernel_says_so_and_is_not_written(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env=f"{MID} RECV_BACK_SERIAL=SER-other")
    trace, log = got["trace"], got["log"]
    assert trace[-1] == "rc=0" and "DONE sda" in trace, got
    said = "דיסק אחר הוכנס באמצע הסבב (sdc, serial SER-other)"
    assert log.count(said) == 1, log
    assert log.index(said) < log.index("partition 3: chunkcast recv pass 1 rc="), log   # inside the pass, not after it
    assert not any(t.startswith(("SET sdc", "DONE sdc", "chunkcast apply")) for t in trace) and _side(trace) == [], trace
    assert (tmp_path / "targets" / "sdb" / "state").read_text().strip() == "failed"


def test_a_drawer_that_failed_for_another_reason_is_not_called_pulled(tmp_path):
    got = _run(tmp_path, [RECEIVE1, DONE], env="RECV_DIES=sdb:write_error REAL_SLEEP=1 TEST_START_CEILING=30")
    ended = [b for b in got["bodies"] if b["state"] == "ended"]
    out = {t["serial"]: t for t in ended[0]["targets"]}["SER-sdb"]
    assert out["failed"] is True and "pulled" not in out, ended[0]


# --- "cancel this disk" while a neighbour is read: every writer below the completion stops before the GPT goes ---------

STUB = 'cancel_pending() { trace "cancel_pending"; }'
REAL = r'''cancel_pending() {
    trace "cancel_pending"
    [ -f "$RUN_DIR/targets/sdc/catchup" ] && [ ! -e "$RUN_DIR/cancelled.once" ] || return 0
    _w=0; until [ -f "$RUN_DIR/neighbour.started" ] || [ "$_w" -ge 80 ]; do command sleep 0.1; _w=$((_w + 1)); done
    : > "$RUN_DIR/cancelled.once"; echo "על ידי nadav" > "$RUN_DIR/targets/sdc/cancel"
    cancel_drawer sdc; trace "CANCELLED rc=$?"
}'''


def _gone(pid: int) -> bool:
    """The process is no more: not in /proc, or a zombie nobody has collected yet."""
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] == "Z"
    except FileNotFoundError:
        return True


def test_cancel_stops_the_neighbours_fill_too_before_the_gpt_goes(tmp_path, monkeypatch):
    """#3112 עצר את ה-subshell של ההשלמה ואת ה-`chunkcast apply` שלו לפי קובץ pid. ההשלמה מהשכנות מריצה כותבים
    שאין לקובץ הזה pid שלהם: כל מה שמתחת ל-subshell נעצר איתו, לפני שה-GPT נמחק."""
    assert STUB in base.HARNESS
    monkeypatch.setattr(base, "HARNESS", base.HARNESS.replace(STUB, REAL))
    got = _run(tmp_path, [RECEIVE1, {"ok": True, "action": "receive", "pass": 2}, DONE],
               env=f"{PULLED} RECV_BACK_AS=sdc NEIGHBOR=slow")
    trace = got["trace"]
    assert trace[-1] == "rc=0" and "CANCELLED rc=0" in trace and "SET sdc cancelled" in trace, got
    assert trace.count(f"{ZAP}sdc") == 1 and "NEIGHBOUR-END" not in trace and "DONE sdc" not in trace, trace
    assert (tmp_path / "neighbour.pid").exists(), "the neighbour was never read"
    assert _gone(int((tmp_path / "neighbour.pid").read_text())), "the neighbour's own writer is still running"
    assert "DONE sda" in trace and f"{ZAP}sda" not in trace, trace


# --- the progress report (progress.sh, drawer_back_json) ------------------------------------------------------------

REPORT = r'''
export RUN_DIR={run} SYSROOT={run}/sysroot LOG_FILE={run}/agent.log
. {agent}/lib/core/common.sh; {sources}
disk_serial() {{ cat "$SYSROOT/sys/block/$1/serial" 2>/dev/null; }}; disk_port() {{ cat "$SYSROOT/sys/block/$1/port" 2>/dev/null; }}
build_progress ses_1 aa:bb:cc:00:00:21
'''


def _report(tmp_path, top: str) -> dict:
    run = tmp_path / "run"
    for dev in ("sda", "sdb", "sdc", "sdd"):
        t = run / "targets" / dev
        t.mkdir(parents=True, exist_ok=True)
        (t / "bytes.0").write_text("0")
        (t / "total").write_text("1000")
    (run / "state").write_text(top)
    (run / "targets/sda/state").write_text("writing")
    # sdb: pulled, and the round still waits for it
    (run / "targets/sdb/state").write_text("failed")
    (run / "targets/sdb/pulled").write_text("")
    (run / "targets/sdb/error").write_text("הכונן נעלם מהמערכת (נשלף או נותק)")
    # sdc: it came back (the round chose it as sdx), and completes partition 3
    (run / "targets/sdc/state").write_text("writing")
    (run / "targets/sdc/dev").write_text("sdx\n")
    (run / "targets/sdc/away").write_text("21\n")
    (run / "targets/sdc/seen").write_text("SER-C|3|3\n")                    # as target_ident_keep left it: serial|port|ata
    (run / "targets/sdc/rejoin").write_text('{"part":3,"missing":412,"chunks":18254,"away_s":21,"step":"neighbor"}\n')
    # sdd: pulled, back, and its completion failed -- nobody waits for it
    (run / "targets/sdd/state").write_text("failed")
    (run / "targets/sdd/pulled").write_text("")
    (run / "targets/sdd/back_failed").write_text("")
    out = sh(REPORT.format(run=posix(run), agent=posix(AGENT),
                           sources=agent_sources("sysinfo", "diskinfo", "progress", "failmark", "drawerplug")))
    return {t["dev"]: t for t in json.loads(out)["targets"]}


def test_the_report_tells_pulled_and_waiting_from_failed_and_carries_the_measure_of_a_drawer_that_is_back(tmp_path):
    by = _report(tmp_path, "writing")
    assert by["sdb"]["state"] == "failed" and by["sdb"]["pulled"] is True, by["sdb"]   # the wire's word stays (#3139)
    assert "pulled" not in by["sda"] and "pulled" not in by["sdd"] and "pulled" not in by["sdx"], by
    assert by["sdx"]["rejoin"] == {"part": 3, "missing": 412, "chunks": 18254, "away_s": 21, "step": "neighbor"}
    assert by["sdx"]["serial"] == "SER-C" and by["sdx"]["port"] == 3, by["sdx"]       # known by its serial, not by the name
    assert "rejoin" not in by["sda"] and "detached" not in by["sda"], by["sda"]        # it never left: nothing of a return


def test_past_the_write_nobody_waits_for_a_pulled_drawer_any_more(tmp_path):
    by = _report(tmp_path, "verifying")
    assert by["sdb"]["state"] == "failed" and "pulled" not in by["sdb"], by["sdb"]
