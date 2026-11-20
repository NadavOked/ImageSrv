"""#3064, שלב 1 (ג): ה-fixture של 13/11 מול מנוע החתיכות האמיתי (`chunksend.ChunkEngine`), עם הווטו מחובר.

**שני הכשלים של 13/11:**

1. **room_714f980d, `later_start_timeout` של p4 (D16).** משכפל אחד בגל. p3 נגמרה, ואז המשכפל משלים מגירה
   שחזרה ואינו אומר `listening` על p4. ה-progress שלו מגיע כל הזמן. אחרי ההמתנה (על הברזל 780 ש') הגל כולו
   נכשל, ומגירה תקינה ב-96% נמחקה.
2. **ההשלמה הארוכה (D15).** מעבדת ה-VM, 13/11: הורדה ארוכה ב-HTTP בסבב השלמה, בלי דיווח שהיומן גדל, והשרת
   הכשיל את הדיסק כ"שתק" -- בזמן שהמשכפל מדווח progress.

**מה נבדק, בכל אחד:**
- ``shadow`` (ברירת המחדל): הסבב נסגר / הדיסק נכשל **בדיוק כמו היום**, ונרשמת שורה "הווטו היה חוסם".
- ``enforce``: הפעולה ההרסנית **לא** מתבצעת. כשהמשכפל חוזר ומדווח, הגל ממשיך ונגמר.
- בלי ראיה (ה-progress בן 400 ש'): ב-enforce הפעולה מתבצעת. הווטו אינו "לעולם לא".

הזמנים מקוצרים (ההמתנה של p4 היא 2 ש' ולא 780), והסדר הוא מה שנבדק.

**(שלב 2) D19:** ראו הסעיף האחרון.

**בקרה שלילית:** הסרת ה-hook ב-`_send` (D16 ו-D15) מפילה את טסטי ה-enforce (הגל נכשל / הדיסק נכשל) ואת טסטי
ה-shadow (אין שורת "הווטו היה חוסם").
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import veto
from server.rounds import chunksend
from server.rounds import sender as udpcast

import chunkcast_rig as C
from test_chunksend_2533 import Runner, wait_for

SID = "S-1311"
ALL = [[0, 9]]


class Lib:
    """אימג' עם שתי מחיצות משודרות, p3 ו-p4 (על הברזל: p2 ו-p4), עשר חתיכות כל אחת."""

    def __init__(self, root, chunks=10):
        self.root, self.chunks = root, chunks
        for n in (3, 4):
            (root / f"p{n}.pcl.zst").write_bytes(b"z" * chunks)
            (root / f"p{n}.pcl.zst.idx").write_text(
                f"IMAGESRV-IDX 2 raw 4194304 0 0 - {chunks * 4194304} {chunks} {chunks}\n"
                + "".join(f"{k} 1 {k:016x}\n" for k in range(chunks)), encoding="ascii")

    def get(self, image_id):
        return {"id": image_id, "partitions": [
            {"index": n, "file": f"p{n}.pcl.zst", "index_xxh64": "0" * 16, "readback_chunks": self.chunks}
            for n in (3, 4)]}

    def file_path(self, image_id, name):
        return self.root / name


def ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


@pytest.fixture()
def wave(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(chunksend, "POLL_S", 0.01)
    monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0)
    # raising=False: in the negative control (the hook removed) the constant does not exist, and the failure
    # has to be the behaviour, not an AttributeError here
    monkeypatch.setattr(chunksend, "VETO_RECHECK_S", 0.2, raising=False)
    monkeypatch.setattr(udpcast, "LATER_MAX_WAIT", 1)        # p4's wait: 1 + 1 = 2 s (on the metal 600 + 180)
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    veto.forget()
    conn = db.connect(tmp_path / "brain.db")
    conn.execute("PRAGMA foreign_keys = OFF")

    class Wave:
        events: list = []

        def __init__(self):
            self.conn, self.members = conn, []

        def member(self, mac, age=2.0, drawers=(("sda", "writing"), ("sdc", "writing"))):
            """the machine's last progress report, `age` seconds ago, with its drawers (as `targets_json`)"""
            self.members.append(mac)
            conn.execute("INSERT OR REPLACE INTO session_members (session_id, mac, state, updated_at, targets_json)"
                         " VALUES (?, ?, 'writing', ?, ?)",
                         (SID, mac, ago(age), json.dumps([{"dev": d, "serial": f"{mac}-{d}", "state": s}
                                                          for d, s in drawers])))
            conn.commit()

        offset = 0.0

        def start(self, mode=None, **kw):
            if mode is not None:
                db.set_setting(conn, veto.SETTING, mode)
            self.runner = Runner()
            self.engine = chunksend.ChunkEngine(
                Lib(tmp_path), live_members=lambda sid: list(self.members), lost_wait=lambda: 1,
                write_floor=lambda: 50, runner=self.runner, max_wait=1, start_timeout=1,
                wave_bitrate=lambda session: "700m", ledger=C.MemLedger(),
                on_event=lambda event, detail, sid: self.events.append((event, detail)), **kw)
            self.engine.veto = lambda **kw: veto.check(conn, **kw).proceed     # what server.app wires
            self.engine.veto_settle = lambda **kw: veto.settle(conn, **kw)
            for mac in self.members:
                self.engine.note_agent(mac, ["udpcast", "chunk"])
            assert self.engine.start({"id": SID, "image_id": "img_1"}) is None

        def tell(self, mac, pass_, state, disks, part):
            return self.engine.report(SID, mac, {"part": part, "pass": pass_, "state": state, "targets": [
                {"serial": serial, "write_mbps": 120, "durable": [{"part": part, "ranges": ranges}]}
                for serial, ranges in disks.items()]})

        def ask(self, mac, pass_, disks, part, seconds=30):
            # #3202: the answer is an event of the engine's thread, which may be inside a veto question that
            # stalls on its journal commit. The wait returns the moment the answer comes; `seconds` is only how
            # long a wave that never answers takes to say so
            got = []
            assert wait_for(lambda: (got.append(self.tell(mac, pass_, "ended", disks, part))
                                     or got[-1].get("action") != "wait"), seconds), got[-1]
            return got[-1]

        def named(self, event):
            return [detail for e, detail in self.events if e == event]

        def state(self):
            return self.engine.status()["state"]

        def would_block(self, point):
            return [json.loads(r.getMessage()[6:]) for r in caplog.records
                    if r.name == "imagesrv.brain" and f'"point": "{point}"' in r.getMessage()
                    and '"rule": "K2"' in r.getMessage() and '"verdict": "HOLD"' in r.getMessage()]

    w = Wave()
    w.events = []
    yield w
    w.engine.close()
    veto.forget()


@pytest.fixture(params=[0, 4], ids=["journal-at-once", "journal-stalled-4s"])
def journal_stall(request, monkeypatch):
    """#3202: the veto's journal commit takes `stall` seconds -- a commit stuck on a saturated disk (the queue's
    failure of #3176, 14/11). The line is written by the engine's thread AFTER the veto logged its HOLD, so a test
    that reads it after a fixed wait reads nothing. 0 = the commit is at once, as always."""
    real = veto.journal

    def stalled(*args, **kwargs):
        time.sleep(request.param)
        return real(*args, **kwargs)

    if request.param:
        monkeypatch.setattr(veto, "journal", stalled)
    return request.param


def _p3_done(w, mac="cloner2"):
    """p3 של הסבב: המשכפל מאזין, המעבר משודר, והוא מדווח שהכול עמיד."""
    disks = {f"{mac}-sda": ALL}
    assert w.tell(mac, 1, "listening", {f"{mac}-sda": []}, part=3)["action"] == "receive"
    assert wait_for(lambda: len(w.runner.commands) == 1)
    assert w.ask(mac, 1, disks, part=3)["action"] == "done"


# --- 1. room_714f980d: p4 finds no listener (D16) ------------------------------------------------------------------


def test_1311_p4_shadow_closes_the_round_as_today_and_says_the_veto_would_have_blocked(wave):
    wave.member("cloner2")
    wave.start()                                    # no setting: shadow
    _p3_done(wave)
    # the machine completes the drawer that came back, and says nothing about p4
    assert wait_for(lambda: wave.state() == "failed", 10)
    assert len(wave.named("send_failed")) == 1 and "אף מחשב לא התייצב" in wave.named("send_failed")[0]
    hold = wave.would_block("D16")
    assert len(hold) == 1 and hold[0]["say"] == "הווטו היה חוסם" and hold[0]["action"] == "FAIL_WAVE"
    assert hold[0]["part"] == 4 and hold[0]["target"] == "cloner2" and "cloner2=ALIVE" in hold[0]["reason"]
    assert hold[0]["trigger"].startswith("later_start_timeout 2s")       # on the metal: 780s


def test_1311_p4_enforce_does_not_close_the_round_and_it_goes_on_when_the_machine_settles(wave, journal_stall):
    wave.member("cloner2")
    wave.start("enforce")
    _p3_done(wave)
    # the hold is an event, not an age (#3202, as #3176): the engine asks the veto when p4's wait (2 s) passes and
    # journals its HOLD. Reading the journal after a fixed 4 s failed whenever the engine thread was late or the
    # commit stalled
    assert wait_for(lambda: _veto_said(wave), 30), "the veto never journalled its hold"
    # held, not failed, a few of its own rechecks later (VETO_RECHECK_S is 0.2 s here)
    assert not wait_for(lambda: wave.state() == "failed", 1)
    assert wave.named("send_failed") == [] and wave.state() == "sending"
    said = _veto_said(wave)
    assert len(said) == 1 and said[0].startswith("הווטו חסם D16 FAIL_WAVE"), said
    # the drawer completed: the machine settles on p4, and p4 is sent and ends
    assert wave.tell("cloner2", 1, "listening", {"cloner2-sda": []}, part=4)["action"] == "receive"
    assert wait_for(lambda: len(wave.runner.commands) == 2, 30)
    assert wave.runner.commands[1][wave.runner.commands[1].index("--part") + 1] == "4"
    assert wave.ask("cloner2", 1, {"cloner2-sda": ALL}, part=4)["action"] == "done"
    assert wait_for(lambda: wave.state() == "done", 30) and wave.named("send_failed") == []


def test_1311_p4_enforce_without_any_evidence_the_round_fails(wave):
    """(ג) של I18: אין שום ראיה -- ה-progress האחרון לפני 400 ש', וגם הודעת ה-`ended` של p3 (שעון המנוע קופץ
    400 ש' קדימה אחרי p3) -- הווטו אינו מחזיק, והגל נכשל כמו היום."""
    wave.member("cloner2", age=400)
    wave.start("enforce", clock=lambda: time.monotonic() + wave.offset)
    _p3_done(wave)
    wave.offset = 400.0
    assert wait_for(lambda: wave.state() == "failed", 10)
    assert "אף מחשב לא התייצב" in wave.named("send_failed")[0]


# --- 2. the long completion: a repair download that reports nothing back (D15) ---------------------------------------


def _round2_fetch(w):
    """a שלם; ל-cloner2 חסרות חתיכות 5-9 ב-p3, והוא היחיד שחסר לו: מוריד ב-HTTP (סבב 2)."""
    w.tell("a", 1, "listening", {"a-sda": []}, part=3)
    w.tell("cloner2", 1, "listening", {"cloner2-sda": []}, part=3)
    assert wait_for(lambda: len(w.runner.commands) == 1)
    w.tell("a", 1, "ended", {"a-sda": ALL}, part=3)
    got = w.ask("cloner2", 1, {"cloner2-sda": [[0, 4]]}, part=3)
    assert got["action"] == "fetch" and got["ranges"] == [[5, 9]], got


def test_long_completion_shadow_fails_the_disk_as_today_and_says_the_veto_would_have_blocked(wave):
    wave.member("a")
    wave.member("cloner2")
    wave.start()
    _round2_fetch(wave)
    assert wait_for(lambda: wave.named("chunk_disk_failed"), 10)
    assert "cloner2 cloner2-sda" in wave.named("chunk_disk_failed")[0]
    hold = wave.would_block("D15")
    assert hold and hold[0]["action"] == "FAIL_DISKS" and hold[0]["say"] == "הווטו היה חוסם"
    assert hold[0]["target"] == "cloner2"


def test_long_completion_enforce_does_not_fail_the_disk_and_the_download_ends(wave, journal_stall):
    wave.member("a")
    wave.member("cloner2")
    wave.start("enforce")
    _round2_fetch(wave)
    # the hold is an event (#3202, as #3176): the engine asks the veto when the repair pass's ceiling passes and
    # journals its HOLD. A fixed 3 s of "nothing failed" passes whatever the engine did, and the answer to `ask`
    # below has no 5 s to wait in when the engine is inside that question
    assert wait_for(lambda: _veto_said(wave), 30), "the veto never journalled its hold"
    assert not wait_for(lambda: wave.named("chunk_disk_failed"), 1)
    assert wave.state() == "repairing"
    # the download ends: everything is durable, and the partition is done without failing anyone
    assert wave.ask("cloner2", 2, {"cloner2-sda": ALL}, part=3)["action"] == "done"
    assert wave.named("chunk_disk_failed") == []


def test_long_completion_enforce_isolation_the_healthy_machine_is_not_failed_or_held(wave):
    """I3: ההחזקה היא על cloner2 בלבד. ל-a אין מה לחכות לו, ואף דיסק שלו לא נכשל."""
    wave.member("a")
    wave.member("cloner2")
    wave.start("enforce")
    _round2_fetch(wave)
    assert not wait_for(lambda: wave.named("chunk_disk_failed"), 2)
    whole = wave.tell("a", 1, "ended", {"a-sda": ALL}, part=3)
    assert whole["action"] == "wait" and whole.get("whole") is True and "fail" not in whole


# --- 3. D19 (stage 2): no machine with a disk left, but a live one has not finished the pass ------------------------
# a's only disk failed by itself (the agent said so); b is alive and still completing a drawer, and has not said `ended`.
# Today the wave fails ("no live machine with a disk left") the moment the ended-ceiling passes. In enforce the veto
# waits for b's `ended` and the plan is made again -- with b's disk in it.


def _veto_said(w):
    """the operator's lines of the veto (hold / release), oldest first"""
    return [r[0] for r in w.conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto' ORDER BY id")]


def _held_now(action="FAIL_WAVE", part=3):
    """the veto's open holds of the wave on `part` (under its lock: the engine thread writes the registry)"""
    with veto._held_lock:
        return [k for k in veto._held if k[:3] == (SID, action, part)]


def _a_failed_b_silent(w):
    w.tell("a", 1, "listening", {"a-sda": []}, part=3)
    w.tell("b", 1, "listening", {"b-sda": []}, part=3)
    assert wait_for(lambda: len(w.runner.commands) == 1)
    got = w.engine.report(SID, "a", {"part": 3, "pass": 1, "state": "ended", "targets": [
        {"serial": "a-sda", "failed": True, "write_mbps": 120, "durable": [{"part": 3, "ranges": []}]}]})
    assert got.get("ok") is True, got


def test_D19_shadow_fails_the_wave_as_today_and_says_the_veto_would_have_blocked(wave):
    wave.member("a")
    wave.member("b")
    wave.start()
    _a_failed_b_silent(wave)
    assert wait_for(lambda: wave.state() == "failed", 10)
    assert "לא נשאר אף מחשב חי" in wave.named("send_failed")[0]
    hold = wave.would_block("D19")
    assert len(hold) == 1 and hold[0]["say"] == "הווטו היה חוסם" and hold[0]["target"] == "b"
    assert hold[0]["part"] == 3 and "b=ALIVE" in hold[0]["reason"]


def test_D19_enforce_waits_for_the_ended_and_plans_again_with_the_machine_that_finished(wave, journal_stall):
    wave.member("a")
    wave.member("b")
    wave.start("enforce")
    _a_failed_b_silent(wave)
    # the hold is an event, not an age (#3176): the engine asks the veto when the ended-ceiling passes and journals
    # its HOLD. Reading the journal after a fixed 3 s failed whenever the engine thread was late or the commit
    # stalled (an fsync on a saturated disk: the HOLD was logged, the row came after the read)
    assert wait_for(lambda: _veto_said(wave), 30), "the veto never journalled its hold"
    # held, not failed, a few of its own rechecks later (VETO_RECHECK_S is 0.2 s here)
    assert not wait_for(lambda: wave.state() == "failed", 1)
    assert wave.named("send_failed") == [] and wave.state() == "sending"
    said = _veto_said(wave)
    assert len(said) == 1 and said[0].startswith("הווטו חסם D19 FAIL_WAVE"), said
    # b finishes the pass with everything durable: the plan is made again, p3 is done, and p4 goes on with b
    assert wave.tell("b", 1, "ended", {"b-sda": ALL}, part=3).get("ok") is True
    assert wave.tell("b", 1, "listening", {"b-sda": []}, part=4)["action"] == "receive"
    assert wait_for(lambda: len(wave.runner.commands) == 2, 30)
    assert wave.runner.commands[1][wave.runner.commands[1].index("--part") + 1] == "4"
    assert wave.named("send_failed") == []
    # the hold ended because the report arrived: the operator sees a release line, and the registry forgot it
    said = [r[0] for r in wave.conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto' ORDER BY id")]
    assert len(said) == 2 and said[1].startswith("הווטו שחרר FAIL_WAVE") and "הדיווח שהמתנו לו הגיע" in said[1], said
    assert not [k for k in veto._held if k[:3] == (SID, "FAIL_WAVE", 3)]


def test_D19_enforce_without_any_evidence_the_wave_fails_as_today(wave):
    """אין שום ראיה על b: ה-progress בן 400 ש', והודעת ה-listening שלו מהמנוע (שעון המנוע קופץ 400 ש' קדימה)."""
    wave.member("a")
    wave.member("b", age=400)
    wave.start("enforce", clock=lambda: time.monotonic() + wave.offset)
    wave.tell("a", 1, "listening", {"a-sda": []}, part=3)
    wave.tell("b", 1, "listening", {"b-sda": []}, part=3)
    assert wait_for(lambda: len(wave.runner.commands) == 1)
    wave.offset = 400.0
    wave.engine.report(SID, "a", {"part": 3, "pass": 1, "state": "ended", "targets": [
        {"serial": "a-sda", "failed": True, "write_mbps": 120, "durable": [{"part": 3, "ranges": []}]}]})
    assert wait_for(lambda: wave.state() == "failed", 10)
    assert "לא נשאר אף מחשב חי" in wave.named("send_failed")[0]


def test_D19_hold_ends_at_the_ceiling_and_the_wave_then_fails_by_name(wave, journal_stall):
    """כל החזקה נגמרת (KT6): b חי אבל אינו מסיים, ובתקרה הווטו משחרר והגל נכשל כמו היום -- בשם ועם סיבה."""
    wave.member("a")
    wave.member("b")
    wave.start("enforce")
    clock = [1000.0]
    wave.engine.veto = lambda **kw: veto.check(wave.conn, mono=lambda: clock[0], **kw).proceed
    _a_failed_b_silent(wave)
    # the ceiling counts from the first question: wait for the hold to exist (#3176), not for a fixed 2 s -- a clock
    # moved before the engine's first question made the hold start at the new time, and nothing ever ended it
    assert wait_for(lambda: _held_now(), 30), "the veto never held"
    assert not wait_for(lambda: wave.state() == "failed", 1)
    clock[0] += 1801                          # the default ceiling is 1800 s
    assert wait_for(lambda: wave.state() == "failed", 30)
    assert "לא נשאר אף מחשב חי" in wave.named("send_failed")[0]
    said = [r[0] for r in wave.conn.execute("SELECT detail FROM journal WHERE event = 'brain_veto' ORDER BY id")]
    assert said[0].startswith("הווטו חסם D19") and said[-1].startswith("הווטו שחרר D19") and "התקרה" in said[-1]


def test_D19_does_not_wait_again_for_a_machine_whose_disks_D15_just_failed(wave):
    """a machine D15 failed by name has nothing left to finish: a second hold on it (D19) would be a second ceiling
    on the same silence. One machine, silent in the repair pass: D15 is asked, D19 is not."""
    wave.member("cloner2")
    wave.start()
    wave.tell("cloner2", 1, "listening", {"cloner2-sda": []}, part=3)
    assert wait_for(lambda: len(wave.runner.commands) == 1)
    assert wave.ask("cloner2", 1, {"cloner2-sda": [[0, 4]]}, part=3)["action"] == "fetch"
    assert wait_for(lambda: wave.state() == "failed", 10)
    assert "cloner2 cloner2-sda" in wave.named("chunk_disk_failed")[0]
    assert len(wave.would_block("D15")) >= 1
    assert wave.would_block("D19") == []
