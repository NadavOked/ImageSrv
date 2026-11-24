"""#2533, שלב 3: מנוע החתיכות בצד השרת (`server/rounds/chunksend.py`).

- #3173: מנוע החתיכות הוא המנוע היחיד; המתג "מנוע שידור" ו-`udp-sender` נמחקו.
- המשכפלים מתייצבים ב-HTTP, מעבר אחד משדר הכול, ושלב ההשלמה משדר
  שוב רק מה שחסר לשני מחשבים ומעלה, ונותן למחשב בודד להוריד את שלו.
- מקלט שנעלם אינו מעכב את האחרים; דיסק שלא הושלם בתקרה נכשל בשמו.

המנוע רץ כאן עם `chunkcast` מזויף (מוזרק, `Runner` למטה, ו-`fakesend.Recorder` באפליקציה): הלוגיקה
של ההתייצבות וההשלמה היא מה שנבדק. התהליכים האמיתיים — ב-test_chunkcast_2533.py.
"""

from __future__ import annotations

import json
import threading
import time
import types

import pytest

pytest.importorskip("fastapi")

from server.library import chunkformat
from server.brain import writerate, repairrule
from server.rounds import chunkctx, chunkledger, chunksend, indexprep, repair

import chunkcast_rig as C

from conftest import MANIFEST_256, hello_body
from test_server_room import (           # noqa: F401 — room_server הוא fixture
    CLONER1, CLONER2, room, room_body, room_server,
)

SID = "S-1"


class Proc:
    def __init__(self, code=0, block=False):
        self.code, self._done = code, threading.Event()
        if not block:
            self._done.set()

    def wait(self, timeout=None):
        assert self._done.wait(10), "the fake chunkcast was never released"
        return self.code

    def poll(self):
        return self.code if self._done.is_set() else None

    def terminate(self):
        self.code = -15
        self._done.set()

    kill = terminate


class Runner:
    def __init__(self, code=0, block=False):
        self.commands, self.processes, self.lists, self.code, self.block = [], [], [], code, block

    def __call__(self, cmd):
        # the list of a repair pass is a file the engine removes after the pass: read here, while it exists
        self.lists.append(open(cmd[cmd.index("--chunks-file") + 1], encoding="ascii").read().strip()
                          if "--chunks-file" in cmd else None)
        self.processes.append(Proc(self.code, self.block))
        self.commands.append(cmd)
        return self.processes[-1]


class Lib:
    """ספרייה עם אימג' אחד: מחיצה 3, עשר חתיכות, ואינדקס בפורמט 2 לידה."""

    def __init__(self, root, chunks=10, index=True):
        self.root, self.chunks = root, chunks
        (root / "p3.windows.pcl.zst").write_bytes(b"z" * chunks)      # a one-byte "frame" a chunk
        if index:
            (root / "p3.windows.pcl.zst.idx").write_text(
                f"IMAGESRV-IDX 2 raw 4194304 0 0 - {chunks * 4194304} {chunks} {chunks}\n"
                + "".join(f"{k} 1 {k:016x}\n" for k in range(chunks)), encoding="ascii")

    def get(self, image_id):
        # what the server writes into the manifest once it has derived and verified the index (stage 2)
        return {"id": image_id, "partitions": [{"index": 3, "file": "p3.windows.pcl.zst", "index_xxh64": "0" * 16,
                                                "readback_chunks": self.chunks},
                                               {"index": 4, "file": None}]}

    def file_path(self, image_id, name):
        return self.root / name


def wait_for(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.setattr(chunksend, "POLL_S", 0.01)
    monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0)

    class Rig:
        members = ["a", "b", "c"]
        events: list = []
        runner = Runner()

        def make(self, **kw):
            self.events, self.lib = [], kw.pop("lib", None) or Lib(tmp_path)
            self.engine = chunksend.ChunkEngine(
                self.lib, live_members=lambda sid: list(self.members), lost_wait=lambda: 1,
                write_floor=lambda: 50, runner=self.runner, max_wait=1, start_timeout=kw.pop("start_timeout", 1),
                wave_bitrate=lambda session: "700m", ledger=kw.pop("ledger", None) or C.MemLedger(),
                on_event=lambda event, detail, sid: self.events.append((event, detail, sid)), **kw)
            for mac in self.members if self.capable is None else self.capable:
                self.engine.note_agent(mac, ["chunk"])                # what their hello declared
            assert self.engine.start({"id": SID, "image_id": "img_1"}) is None
            return self.engine

        def tell(self, mac, pass_, state, disks, mbps=120, part=3, skipped=None, stray=None):
            """``skipped``: serial ← מה שהמקלט אמר שהדיסק לא הספיק לכתוב במעבר (#3141/#3170); חסר = לא נאמר.
            ``stray``: כמה חבילות מכתובת זרה המקלט זרק במעבר (#1079/#2485); חסר = לא נאמר."""
            return self.engine.report(SID, mac, {"part": part, "pass": pass_, "state": state,
                                                 **({"stray": stray} if stray is not None else {}), "targets": [
                {"serial": serial, "write_mbps": mbps, "durable": [{"part": part, "ranges": ranges}],
                 **({"skipped": skipped[serial]} if skipped and serial in skipped else {})}
                for serial, ranges in disks.items()]})

        def ask(self, mac, pass_, disks, skipped=None):
            """מדווח `ended` שוב ושוב, כמו הסוכן, עד שהתשובה אינה `wait`."""
            got = []
            assert wait_for(lambda: (got.append(self.tell(mac, pass_, "ended", disks, skipped=skipped))
                                     or got[-1].get("action") != "wait")), got[-1]
            return got[-1]

        def ask_part(self, mac, part, disks):
            """כמו `ask`, למחיצה אחרת (מעבר 1)."""
            got = []
            assert wait_for(lambda: (got.append(self.tell(mac, 1, "ended", disks, part=part))
                                     or got[-1].get("action") != "wait")), got[-1]
            return got[-1]

        def named(self, event):
            return [detail for e, detail, _sid in self.events if e == event]

    rig = Rig()
    rig.runner = Runner()
    rig.members = ["a", "b", "c"]
    rig.capable = None
    yield rig
    if hasattr(rig, "engine"):   # make() can fail before it is built (the base engine takes no `source`): that is a FAILED test, not an ERROR here
        rig.engine.close()


ALL = [[0, 9]]


def test_one_pass_then_only_what_is_missing_multicast_for_two_http_for_one(rig):
    engine = rig.make()
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    first = rig.runner.commands[0]
    assert first[:2] == ["chunkcast", "send"] and rig.runner.lists[0] is None
    assert first[first.index("--pass") + 1] == "1" and first[first.index("--kbps") + 1] == "700000"
    assert first[first.index("--to") + 1] == f"{chunksend.GROUP}:{engine.port}"
    assert first[first.index("--session") + 1] == engine.session_fields(SID)["chunk"]["key"]

    assert rig.tell("a", 1, "ended", {"A1": ALL})["action"] == "wait"      # not everyone has reported
    assert rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})["action"] == "wait"
    c = rig.ask("c", 1, {"C1": [[0, 4], [7, 8]]})
    assert c == {"ok": True, "action": "receive", "pass": 2}
    assert rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]}) == {"ok": True, "action": "receive", "pass": 2}
    whole = rig.tell("a", 1, "ended", {"A1": ALL})                         # whole: nothing to do this round
    assert whole["action"] == "wait" and whole["whole"] is True
    assert "whole" not in rig.tell("b", 1, "ended", {"B1": [[0, 4], [7, 9]]})

    for mac in ("b", "c"):
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    second = rig.runner.commands[1]
    assert rig.runner.lists[1] == "5-6" and "--chunks" not in second       # one resend repairs both; the list is a file
    assert second[second.index("--pass") + 1] == "2"
    assert wait_for(lambda: not __import__("os").path.exists(second[second.index("--chunks-file") + 1]))

    assert rig.tell("b", 2, "ended", {"B1": ALL})["action"] == "wait"
    assert rig.ask("c", 2, {"C1": [[0, 8]]}) == {"ok": True, "action": "fetch", "pass": 3, "ranges": [[9, 9]]}
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done" and rig.ask("b", 2, {"B1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert len(rig.runner.commands) == 2                                    # the lone chunk never went on the air
    assert len(rig.named("send_done")) == 1 and rig.named("send_failed") == []
    assert engine.status()["multicast_chunks"] == 2 and engine.status()["fetched_chunks"] == 1


def test_the_write_rate_is_the_slowest_disk_measured_and_the_floor_when_one_was_not(rig):
    rig.make()
    rig.tell("a", 1, "listening", {"A1": []}, mbps=140)
    rig.tell("b", 1, "listening", {"B1": []}, mbps=120)
    rig.tell("c", 1, "listening", {"C1": []}, mbps=200)
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    assert cmd[cmd.index("--write-kbps") + 1] == str(int(120 * writerate.MARGIN) * 8000)


def test_an_unmeasured_disk_means_the_floor_every_disk_passed_not_a_guess(rig):
    rig.make()
    rig.tell("a", 1, "listening", {"A1": []}, mbps=140)
    rig.tell("b", 1, "listening", {"B1": []}, mbps=None)
    rig.tell("c", 1, "listening", {"C1": []}, mbps=200)
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    assert cmd[cmd.index("--write-kbps") + 1] == str(50 * 8000)


def test_a_receiver_that_went_silent_does_not_hold_the_others(rig):
    """מקלט שלא דיווח על סוף המעבר: אחרי "המתנה למחשב שהתנתק" ממשיכים בלעדיו."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    began = time.monotonic()
    assert rig.ask("b", 1, {"B1": ALL})["action"] == "done"                 # c never said a word
    assert 0.9 < time.monotonic() - began < 4
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_receiver_that_vanished_and_came_back_is_repaired_in_the_next_round(rig):
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    assert rig.ask("b", 1, {"B1": [[0, 4], [7, 9]]}) == {"ok": True, "action": "fetch", "pass": 2,
                                                         "ranges": [[5, 6]]}
    # c was away for the whole wait and reports only now, with what it had flushed
    assert rig.tell("c", 1, "ended", {"C1": [[0, 2]]})["action"] == "wait"
    rig.tell("b", 2, "ended", {"B1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 2]]}) == {"ok": True, "action": "fetch", "pass": 3,
                                                 "ranges": [[3, 9]]}
    assert rig.ask("c", 3, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert len(rig.runner.commands) == 1


def test_a_slow_drive_is_not_failed_it_completes_and_its_neighbours_finish(rig):
    """#3170 מול #3295 (נדב 15/11, אפשרות ב'): הדיסק פיגר -- 9 מתוך 10 החלקים שהגיעו לא נכתבו -- וכתב בכ-11MB/s מול 108 שהזרם
    ביקש, מתחת למהירות המינימלית לכונן (כאן 50). אין לו כשל משלו, ולכן הוא **אינו נכשל** ואינו מקבל `fail` (הסוכן היה מוחק לו
    את ה-GPT, והחדר פותח גל 2 ומעיר את המשכפל): מה שחסר לו משלים, ושכניו מסיימים. ביומן: `chunk_disk_slow`, לא `chunk_disk_failed`."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    b = rig.ask("b", 1, {"B1": [[0, 0]], "B2": ALL}, skipped={"B1": 9, "B2": 0})
    assert b == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[1, 9]]}, b          # no `fail`: it completes
    (slow,) = rig.named("chunk_disk_slow")
    assert slow.startswith("b B1:") and "פיגר" in slow and "(50MB/s)" in slow and "אינו נכשל" in slow, slow
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []
    done = rig.ask("b", 2, {"B1": ALL, "B2": ALL})
    assert done["action"] == "done" and "fail" not in done, done
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == [] and len(rig.runner.commands) == 1       # no second wave of sending either


def test_a_machine_cut_off_the_network_in_the_first_pass_completes_from_the_server_and_does_not_fail(rig):
    """#3141 (מעבדת ה-VM 14/11, room_d3ade77c): משכפל נותק לארבע דקות באמצע המעבר הראשון וחזר עם 211 מתוך 525 חלקים,
    והסוכן אמר `0 skipped` -- הדיסק לא פיגר; החבילות לא הגיעו. השרת קרא לזה "לא עמד בקצב", הכשיל את הדיסק, וה-GPT נמחק.
    הכרעת נדב (א): הוא משלים לבד מהשרת את מה שפספס, כמו מגירה שנשלפה וחזרה, וביומן נרשם "ניתוק רשת"."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.members = ["a", "b"]                                # c is lost: the server no longer counts it alive
    time.sleep(0.15)
    rig.members = ["a", "b", "c"]                           # ...and it is back, with what it had flushed
    assert rig.tell("c", 1, "ended", {"C1": [[0, 2]]}, skipped={"C1": 0})["action"] == "wait"
    rig.tell("b", 1, "ended", {"B1": ALL})
    c = rig.ask("c", 1, {"C1": [[0, 2]]}, skipped={"C1": 0})
    assert c == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[3, 9]]}, c          # no `fail`
    assert rig.named("chunk_disk_failed") == [], rig.named("chunk_disk_failed")
    (gap,) = rig.named("chunk_net_gap")
    assert gap.startswith("c: 7 חלקים") and "ניתוק רשת" in gap, gap
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done") and rig.named("send_failed") == []


def test_chunks_lost_on_the_network_are_named_as_that_and_completed_whatever_their_number(rig):
    """#3141: גם בלי שהשרת ראה ניתוק -- חבילות שאבדו -- החוסר מושלם, והסיבה אינה "דיסק איטי"."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    c = rig.ask("c", 1, {"C1": [[0, 1]]}, skipped={"C1": 1})   # 8 missing, 1 of them the drive's -- it still wrote ~72MB/s, above 50
    assert c == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[2, 9]]}, c
    (gap,) = rig.named("chunk_net_gap")
    assert gap.startswith("c: 7 חלקים") and "אבדו ברשת" in gap and "ניתוק" not in gap, gap
    assert rig.named("chunk_disk_failed") == []
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_drive_that_fell_behind_but_writes_above_the_minimum_completes(rig):
    """#3170: "מקובל" -- החדר אינו מאט, והסף הוא המהירות המינימלית שבהגדרות, לא "חצי מהחתיכות". הדיסק לקח 4 מתוך 5
    שהגיעו (כ-86MB/s מול 108 שהזרם ביקש), מעל 50: הוא משלים, גם כשחסרות לו 6 מתוך 10."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    c = rig.ask("c", 1, {"C1": [[0, 3]]}, skipped={"C1": 1})
    assert c == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[4, 9]]}, c
    assert rig.named("chunk_disk_failed") == []
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_nobody_listening_is_a_failed_wave_by_name_not_an_empty_success(rig):
    engine = rig.make()
    assert wait_for(lambda: rig.named("send_failed"), 5) and engine.status()["state"] == "failed"
    assert rig.runner.commands == []
    (why,) = rig.named("send_failed")
    assert "אף מחשב לא התייצב" in why
    assert engine.report(SID, "a", {"part": 3, "pass": 1, "state": "listening", "targets": []}) == {
        "ok": True, "action": "closed"}


def test_no_report_after_the_pass_is_not_nothing_to_repair(rig):
    """עיקרון 5: אף אחד לא אמר מה נכתב — זה לא "הכול שלם"."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_failed"), 5) and engine.status()["state"] == "failed"
    assert "אף מחשב לא דיווח על סיום המעבר" in rig.named("send_failed")[0]
    assert rig.named("send_done") == []


def test_ranges_that_are_not_the_contracts_are_refused_and_not_merged(rig):
    rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    bad = rig.tell("b", 1, "ended", {"B1": [[0, 10]]})                      # chunk 10 of 10 does not exist
    assert bad["ok"] is False and bad["code"] == "bad_durable"
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    assert rig.ask("b", 1, {"B1": [[0, 8]]}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[9, 9]]}


def test_an_image_without_a_format_2_index_is_refused_by_name(rig, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    engine = rig.make(lib=Lib(old, index=False))
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "אין אינדקס" in rig.named("send_failed")[0] and rig.runner.commands == []


def test_a_sender_that_exits_non_zero_fails_the_wave(rig):
    rig.runner = Runner(code=1)
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "chunkcast send נכשל" in rig.named("send_failed")[0]


def test_closing_the_round_stops_the_sender_and_is_not_done(rig):
    rig.runner = Runner(block=True)
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.processes) == 1)
    engine.stop(SID)
    assert rig.runner.processes[0].code == -15
    assert wait_for(lambda: engine.status()["state"] == "stopped")
    assert rig.named("send_done") == [] and rig.named("send_failed") == []
    assert engine.session_fields(SID) == {}


def test_session_fields_are_only_for_the_wave_this_engine_sends(rig):
    engine = rig.make()
    fields = engine.session_fields(SID)
    assert fields["engine"] == "chunk" and fields["chunk"]["group"] == chunksend.GROUP
    assert len(fields["chunk"]["key"]) == 16 and int(fields["chunk"]["key"], 16) >= 0
    assert engine.session_fields("another") == {}


def test_every_event_the_engine_writes_has_a_hebrew_label():
    from server.console import journal_he
    for event in ("send_start", "send_done", "send_failed", "send_stopped", "chunk_repair", "chunk_disk_failed",
                  "chunk_net_gap", "chunk_disk_slow", "chunk_disk_held", "chunk_stray_source"):
        assert event in journal_he.EVENTS_HE, event


# --- גל חדר, מקצה לקצה דרך האפליקציה -------------------------------------------------

def _hello(room_server, engines, joining=None):
    """hello של המשכפל עם שתי המגירות שלו, ועם מה שהאימג' שלו יודע לקבל (`engines`)."""
    body = hello_body(CLONER1)
    if joining is not None:
        body["joining"] = joining
    body["disks"] = [{"dev": f"sd{chr(ord('a') + i)}", "size_bytes": 256060514304, "model": "Drawer SSD",
                      "serial": serial, "removable": False, "scheme": "gpt", "has_data": False, "port": i + 1}
                     for i, serial in enumerate(["S1", "S2"])]
    if engines is None:
        body.pop("engines", None)               # an agent image from before the engine: no `engines` at all
    else:
        body["engines"] = list(engines)
    response = room_server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _room_wave(room_server, engines=("chunk",)):
    """סבב חדר שיכפול עם משכפל אחד ושתי מגירות, עד שהגל רץ. מחזיר (מזהה הגל, ה-hello)."""
    opened = room_server["deploy"].post("/api/console/room",
                                        json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = _hello(room_server, engines)["session"]["id"]
    _hello(room_server, engines, joining=False)
    assert room(room_server["deploy"])["round"]["wave_state"] == "running"
    return wave, _hello(room_server, engines, joining=False)


def test_a_cloner_with_an_old_agent_image_fails_a_chunk_wave_by_name(room_server):
    ctx = room_server["ctx"]
    opened = room_server["deploy"].post("/api/console/room",
                                        json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    _hello(room_server, None)                                                # no `engines` in its hello
    _hello(room_server, None, joining=False)
    assert wait_for(lambda: ctx.chunk.status() and ctx.chunk.status()["state"] == "failed")
    assert "אינו מכיר את מנוע החתיכות" in ctx.chunk.status()["error"]
    assert room_server["recorder"].commands == []


def _index_the_image(ctx, chunks: int = 1):
    """מה ששלב 2 עושה אחרי קליטה: אינדקס ליד כל קובץ מחיצה, ובמניפסט מה שנרשם כשהוא אומת.
    ``chunks`` > 1: המחיצה הראשונה היא של `chunks` חתיכות (פריים של בית אחד לחתיכה).
    #3173: `conftest.write_image` כבר כותב אינדקס של חתיכה אחת; כאן -- לטסט שצריך יותר ממנה."""
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    manifest_file = ctx.library.file_path("img_7f3a91", next(
        p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))).parent / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for part in manifest["partitions"]:
        if part.get("file"):
            part.update(index_xxh64="0" * 16, readback_chunks=chunks if part["index"] == first else 1,
                        chunk_b3="b" * 64)                       # #3332: a wave goes out only with the BLAKE3 list too
    manifest["image_format"] = 2
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    for part in MANIFEST_256["partitions"]:
        if part.get("file"):
            path = ctx.library.file_path("img_7f3a91", part["file"])
            if part["index"] == first and chunks > 1:
                path.write_bytes(b"z" * chunks)
                path.with_name(path.name + ".idx").write_text(
                    f"IMAGESRV-IDX 2 raw 4194304 0 0 - {chunks * 4194304} {chunks} {chunks}" + chr(10)
                    + "".join(f"{k} 1 {k:016x}" + chr(10) for k in range(chunks)), encoding="ascii")
                path.with_name(path.name + ".b3").write_bytes(b"\0" * 32 * chunks)
                continue
            path.with_name(path.name + ".idx").write_text(
                "IMAGESRV-IDX 2 raw 4194304 0 0 - 26 1 26\n0 26 0000000000000000\n", encoding="ascii")
            path.with_name(path.name + ".b3").write_bytes(b"\0" * 32)


def test_with_the_switch_on_a_room_wave_is_the_chunk_engines_and_hello_says_so(room_server):
    """#3173: אין מתג -- מנוע החתיכות הוא היחיד. השם נשאר, כדי שההיסטוריה של הטסט תימצא."""
    ctx, recorder, anon = room_server["ctx"], room_server["recorder"], room_server["anon"]
    wave, hello = _room_wave(room_server)
    chunk = hello["session"]["chunk"]
    assert hello["session"]["engine"] == "chunk" and chunk["port"] == ctx.chunk.port
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))

    def tell(mac):
        return anon.post("/api/v1/agent/chunk-pass", json={
            "mac": mac, "session_id": wave, "part": first, "pass": 1, "state": "listening",
            "targets": [{"serial": "S1", "write_mbps": 150}, {"serial": "S2", "write_mbps": 150}]})

    stranger = tell(CLONER2)                                                # registered, not in this wave
    assert stranger.status_code == 403 and stranger.json()["code"] == "not_member"
    answer = tell(CLONER1)
    assert answer.status_code == 200 and answer.json() == {"ok": True, "action": "receive", "pass": 1}
    assert recorder.spawned.wait(timeout=5)
    cmd = recorder.commands[0]
    assert cmd[:2] == ["chunkcast", "send"] and cmd[cmd.index("--session") + 1] == chunk["key"]
    assert all(c[:2] == ["chunkcast", "send"] for c in recorder.commands), recorder.commands   # nothing but the engine ran
    assert ctx.chunk.status()["session_id"] == wave
    ctx.chunk.stop(wave)


def test_the_partition_file_route_answers_a_range_with_exactly_those_bytes(server):
    """סיכון 11 בתכנון, שלא אומת עד כאן: השלמה למחשב בודד היא `Range` על קובץ המחיצה
    (חוזה הפורמט, סעיף 8). 206, ובדיוק הבתים שהתבקשו — לא הקובץ כולו ב-200."""
    from conftest import PARTITION_BYTES
    name = next(p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))
    got = server["anon"].get(f"/api/v1/images/img_7f3a91/files/{name}", headers={"Range": "bytes=3-9"})
    assert got.status_code == 206, got.status_code
    assert got.content == PARTITION_BYTES[3:10]
    assert got.headers["content-range"] == f"bytes 3-9/{len(PARTITION_BYTES)}"


# --- ממצאי הסקירה (cffe9589) --------------------------------------------------------------

def test_an_index_missing_for_a_later_partition_refuses_the_wave_before_a_byte(rig, tmp_path):
    """סקירה 4: לא באמצע הגל, אחרי שמחיצה אחת כבר נכתבה."""
    class Two(Lib):
        def get(self, image_id):
            return {"id": image_id, "partitions": [
                {"index": 3, "file": "p3.windows.pcl.zst", "index_xxh64": "0" * 16, "readback_chunks": 10},
                {"index": 5, "file": "p5.recovery.pcl.zst", "index_xxh64": "0" * 16, "readback_chunks": 1}]}
    two = tmp_path / "two"
    two.mkdir()
    lib = Two(two)
    (two / "p5.recovery.pcl.zst").write_bytes(b"z")              # a file, and no index beside it
    engine = rig.make(lib=lib)
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "p5.recovery.pcl.zst" in rig.named("send_failed")[0] and "דבר לא שודר" in rig.named("send_failed")[0]
    assert rig.runner.commands == [] and rig.named("send_start") == []


def test_hello_names_the_engine_and_the_same_key_before_the_engine_has_started(tmp_path):
    """סקירה 5: ה-commit ל-`running` קודם ל-`on_running`. hello שנכנס ביניהם מקבל את המנוע
    ואת אותו מספר סשן שהשולח ישדר בו -- ולא `{}` שמשאיר את המשכפל ב-udp-receiver."""
    wanted = {"S-9"}
    engine = chunksend.ChunkEngine(Lib(tmp_path), live_members=lambda sid: ["a"], lost_wait=lambda: 1,
                                   write_floor=lambda: 50, runner=Runner(block=True), max_wait=1, start_timeout=1,
                                   wave_bitrate=lambda s: "700m", wants=lambda sid: sid in wanted,
                                   ledger=C.MemLedger())
    before = engine.session_fields("S-9")
    assert before["engine"] == "chunk" and len(before["chunk"]["key"]) == 16
    assert engine.session_fields("S-other") == {}                          # a wave the switch does not cover
    engine.note_agent("a", ["chunk"])
    assert engine.start({"id": "S-9", "image_id": "img_1"}) is None
    try:
        assert engine.session_fields("S-9") == before
        engine.report("S-9", "a", {"part": 3, "pass": 1, "state": "listening", "targets": []})
        assert wait_for(lambda: len(engine.runner.commands) == 1)
        cmd = engine.runner.commands[0]
        assert cmd[cmd.index("--session") + 1] == before["chunk"]["key"]
    finally:
        engine.close()
    assert engine.session_fields("S-9") == {}                              # stopped: nothing to receive


def test_a_machine_that_reports_after_the_partition_closed_fetches_what_it_lacks(rig):
    """סקירה 7: מקלט שאיבד את חבילת הסוף מדווח אחרי שהמחיצה נסגרה. הוא אינו מקבל 400 ואינו
    נכשל: הוא מוריד את מה שחסר לו, ורק תקרת הניסיונות מכשילה את הדיסק, בשמו."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    assert rig.ask("b", 1, {"B1": ALL})["action"] == "done"                 # closed without c
    late = rig.tell("c", 1, "ended", {"C1": [[0, 6]]})
    assert late == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[7, 9]]}
    assert rig.tell("c", 2, "ended", {"C1": ALL}) == {"ok": True, "action": "done"}
    assert wait_for(lambda: engine.status()["state"] == "done")
    answer = {}
    for attempt in range(1, repairrule.MAX_ROUNDS + 3):                        # d never gets there
        answer = rig.tell("d", attempt, "ended", {"D1": [[0, 6]]})
        if answer["action"] == "done":
            break
    assert answer["action"] == "done" and "ניסיונות השלמה" in answer["fail"]["D1"]


def test_a_member_whose_agent_never_declared_the_engine_fails_the_wave_by_name(rig):
    """סקירה 11: סוכן מאימג' ישן היה מריץ udp-receiver מול chunkcast. הגל מסורב בשמו, לפני הבייט הראשון."""
    rig.capable = ["a", "b"]
    engine = rig.make()
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "(c)" in rig.named("send_failed")[0] and "אינו מכיר את מנוע החתיכות" in rig.named("send_failed")[0]
    assert rig.runner.commands == []


def test_the_server_waits_for_an_ended_at_least_as_long_as_a_receiver_holds_a_silent_stream():
    """סקירה 7: הסוכן מחזיק מקלט שהזרם שלו השתתק lost + 120 ש' (`timing.sh`) לפני שהוא מדווח."""
    assert chunksend.ENDED_GRACE_S >= 120


@pytest.mark.parametrize("damage", ["row_offset", "row_missing", "row_hash", "file_size", "extra_line", "leading_zero"])
def test_an_index_damaged_after_its_first_line_refuses_the_wave_before_a_byte(rig, tmp_path, damage):
    """סקירה שנייה: שורת כותרת תקינה אינה אינדקס תקין. אינדקס שנפגם בהמשך היה מתחיל גל, ואז
    כל מקלט היה מקבל 404 על האינדקס. כל כללי העקביות של החוזה (סעיף 4) נבדקים לפני הגל."""
    bad = tmp_path / "bad"
    bad.mkdir()
    lib = Lib(bad)
    idx = bad / "p3.windows.pcl.zst.idx"
    lines = idx.read_text(encoding="ascii").splitlines()
    assert chunkformat.read_index(bad / "p3.windows.pcl.zst").chunks == 10
    if damage == "row_offset":
        lines[4] = "9 1 " + "0" * 16
    elif damage == "row_missing":
        del lines[-1]
    elif damage == "row_hash":
        lines[2] = "1 1 XYZ"
    elif damage == "extra_line":
        lines.append("10 1 " + "0" * 16)
    elif damage == "leading_zero":
        lines[1] = "00 1 " + "0" * 16
    if damage == "file_size":
        (bad / "p3.windows.pcl.zst").write_bytes(b"z" * 11)
    else:
        idx.write_text("\n".join(lines) + "\n", encoding="ascii")
    assert chunkformat.read_index(bad / "p3.windows.pcl.zst") is None
    engine = rig.make(lib=lib)
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "אין אינדקס מאומת" in rig.named("send_failed")[0]
    assert rig.runner.commands == [] and rig.named("send_start") == []


def test_the_repair_stage_reads_the_ledger_and_a_disk_without_one_fails_by_name(rig):
    """שלב 2+3: מה שעמיד הוא מה שביומן. דיסק שהיומן אינו מכיר (אין לו מספר סידורי וגודל
    ב-hello, או שיש לו תאום) אינו "חסר לו הכול" ואינו מושלם בניחוש: הוא נכשל בשמו, ושכניו מסיימים."""
    ledger = C.MemLedger(refuse={"B2"})
    engine = rig.make(ledger=ledger)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("c", 1, "ended", {"C1": ALL})
    b = rig.ask("b", 1, {"B1": ALL, "B2": ALL})
    assert b["action"] == "done" and set(b["fail"]) == {"B2"} and "אין יומן כתיבה" in b["fail"]["B2"]
    assert ledger.durable(SID, "b", "B1", 3) == [(0, 9)] and ledger.durable(SID, "b", "B2", 3) == []
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_an_index_the_manifest_does_not_vouch_for_refuses_the_wave(rig, tmp_path):
    """האינדקס שליד הקובץ שלם, אבל המניפסט אינו נושא את מה שהשרת רושם כשהוא מאמת אותו
    (`readback_chunks`): זה התנאי שבו `…/index/<n>` מחזיר 404 למקלטים, ולכן הגל מסורב מראש."""
    class Unverified(Lib):
        def get(self, image_id):
            manifest = super().get(image_id)
            del manifest["partitions"][0]["readback_chunks"]
            return manifest
    plain = tmp_path / "plain"
    plain.mkdir()
    engine = rig.make(lib=Unverified(plain))
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    assert "אין אינדקס מאומת" in rig.named("send_failed")[0] and rig.runner.commands == []


def test_the_real_ledger_is_one_table_with_two_writers_and_the_engine_reads_it(room_server):
    """שלב 2+3, מול הטבלה האמיתית (`DbLedger` על `chunk_ledger`): מה ש-`chunk-pass` מדווח נרשם תחת מזהה הגל;
    מה שדיווח ההתקדמות (הכותב של שלב 2) רשם — המנוע קורא כאותה שורה; שורה של סבב אחר אינה ראיה;
    ודיסק שה-hello לא נשא את המספר הסידורי שלו, או טווח שמחוץ למחיצה, מסורבים בשמם ואינם נרשמים."""
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave, _hello_answer = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    ledger, size = ctx.chunk.ledger, 256060514304
    assert isinstance(ledger, chunksend.DbLedger) and ledger.durable(wave, CLONER1, "S1", part) == []

    def tell(state, targets):
        return anon.post("/api/v1/agent/chunk-pass", json={
            "mac": CLONER1, "session_id": wave, "part": part, "pass": 1, "state": state, "targets": targets})
    assert tell("listening", [{"serial": "S1"}, {"serial": "S2"}]).json()["action"] == "receive"
    assert room_server["recorder"].spawned.wait(timeout=5)
    assert tell("ended", [{"serial": "S1", "durable": [{"part": part, "ranges": [[0, 0]]}]},
                          {"serial": "S2", "durable": [{"part": part, "ranges": []}]}]).status_code == 200
    row = chunkledger.of_disk(ctx.conn, "S1", size)[part]
    assert row["ranges"] == [(0, 0)] and (row["round_id"], row["mac"]) == (wave, CLONER1)
    assert ledger.durable(wave, CLONER1, "S1", part) == [(0, 0)] and ledger.durable(wave, CLONER1, "S2", part) == []
    assert ledger.durable("ses_another", CLONER1, "S1", part) == []        # another round's row: not evidence

    progress = anon.post("/api/v1/agent/progress", json={
        "session_id": wave, "mac": CLONER1, "state": "writing", "targets": [
            {"dev": "sdb", "bytes_written": 10, "bytes_total": 100, "state": "writing",
             "durable": [{"part": part, "ranges": [[0, 0]]}]}]})
    assert progress.status_code == 200, progress.text
    assert ledger.durable(wave, CLONER1, "S2", part) == [(0, 0)]           # stage 2's writer, this reader: one row

    assert "does not know this disk" in ledger.record(wave, "img_7f3a91", CLONER1, "NOT-IN-HELLO", part, [(0, 0)])
    assert "outside the partition" in ledger.record(wave, "img_7f3a91", CLONER1, "S1", part, [(0, 5)])
    assert chunkledger.of_disk(ctx.conn, "S1", size)[part]["ranges"] == [(0, 0)]
    ctx.chunk.stop(wave)


def test_a_large_image_with_scattered_loss_is_repaired_and_not_refused(rig, tmp_path):
    """אימג' של 200GB הוא 47,500 חתיכות. שני משכפלים שהדיסק שלהם פיגר דילגו על חתיכה מכל עשר:
    4,750 טווחים נפרדים בדיווח `durable` אחד. הדיווח מתקבל (התקרה היא מה שמחיצה יכולה להכיל,
    לא 4096), ומה שחסר לשניהם משודר שוב במעבר אחד — רשימה של 4,750 חתיכות, בקובץ."""
    chunks = 47_500
    big = tmp_path / "big"
    big.mkdir()
    rig.members = ["a", "b"]
    engine = rig.make(lib=Lib(big, chunks=chunks))
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    holed = [[10 * i, 10 * i + 8] for i in range(chunks // 10)]             # every tenth chunk was skipped
    assert len(holed) == 4750
    first = rig.tell("a", 1, "ended", {"A1": holed})
    assert first.get("action") == "wait", first                             # accepted; b has not reported yet
    assert rig.ask("b", 1, {"B1": holed}) == {"ok": True, "action": "receive", "pass": 2}
    assert rig.ask("a", 1, {"A1": holed}) == {"ok": True, "action": "receive", "pass": 2}
    for mac in rig.members:
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    resent = rig.runner.lists[1].split(",")
    assert len(resent) == 4750 and resent[0] == "9-9" and resent[-1] == f"{chunks - 1}-{chunks - 1}"
    whole = [[0, chunks - 1]]
    assert rig.tell("a", 2, "ended", {"A1": whole})["action"] == "wait"
    assert rig.ask("b", 2, {"B1": whole})["action"] == "done" and rig.ask("a", 2, {"A1": whole})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.status()["multicast_chunks"] == 4750 and rig.named("chunk_disk_failed") == []


def test_a_report_larger_than_the_body_cap_is_refused_whole_and_by_code(room_server):
    """התקרה שהדלתא של 13/11 באה לא לחצות, נעוצה כאן כדי שלא תשתנה בשקט: `chunk-pass` עובר בתקרת הגוף של
    נתיבי הסוכן (1MiB, #2410). רשימה מלאה של שלוש מגירות במקרה הגרוע של אימג' 200GB (23,750 טווחים כל אחת)
    היא פחות מ-1MiB; של ארבע — יותר, וההודעה נדחית כולה ב-413 `payload_too_large`, לא נחתכת ולא נרשמת
    בחלקה. הסוכן אינו בונה הודעה כזו עוד (test_chunk_delta_2533.py); מי שכן שולח אותה מקבל את אותה תשובה."""
    from server.agent import body as agent_body
    ctx, anon = room_server["ctx"], room_server["anon"]
    wave, _hello_answer = _room_wave(room_server)
    part = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    worst = "[" + ",".join(f"[{2 * i},{2 * i}]" for i in range(23_750)) + "]"         # as the agent writes it

    def report(drawers):
        targets = ",".join(f'{{"serial":"S{n}","write_mbps":150,"failed":false,"durable":[{{"part":{part},"ranges":{worst}}}]}}'
                           for n in range(1, drawers + 1))
        return (f'{{"mac":"{CLONER1}","session_id":"{wave}","part":{part},"pass":1,"state":"ended",'
                f'"targets":[{targets}]}}').encode("ascii")
    assert len(report(3)) < agent_body.MAX_BYTES < len(report(4))
    headers = {"Content-Type": "application/json"}
    fits = anon.post("/api/v1/agent/chunk-pass", content=report(3), headers=headers)
    assert fits.status_code == 400 and fits.json()["code"] == "bad_durable"       # read and judged (this image has 1 chunk)
    over = anon.post("/api/v1/agent/chunk-pass", content=report(4), headers=headers)
    assert over.status_code == 413 and over.json()["code"] == "payload_too_large"
    assert chunkledger.of_disk(ctx.conn, "S1", 256060514304) == {}
    ctx.chunk.stop(wave)


def _capture_without_derived_index(ctx):
    """the manifest of an image just captured: it carries the `index_xxh64` the agent signed, no index was derived.
    #3173: `conftest.write_image` writes an image whose index was already derived; what the derivation leaves (the
    `.idx` and `.b3` beside each file, the `readback_*` and `chunk_b3` fields) is taken away here."""
    manifest_file = ctx.library.file_path("img_7f3a91", next(
        p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))).parent / "manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    for part in manifest["partitions"]:
        if part.get("file"):
            part["index_xxh64"] = "0" * 16                # the capture's word; nothing derived, nothing verified
            for derived in ("readback_xxh64", "readback_chunks", "readback_bytes", "readback_chunk_bytes", "chunk_b3"):
                part.pop(derived, None)
            for suffix in (".idx", ".b3"):
                (manifest_file.parent / (part["file"] + suffix)).unlink(missing_ok=True)
    manifest["image_format"] = 2
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")


def test_an_image_whose_index_cannot_be_derived_fails_the_round_by_name_before_the_wave(room_server, monkeypatch):
    """אימג' בפורמט 2 שנקלט זה עתה: המניפסט נושא את `index_xxh64` שהסוכן חתם, ושום אינדקס לא נגזר ליד הקובץ.
    עד #3137 הגל יצא, והמנוע הכשיל אותו ("עדיין נבנה אחרי הקליטה"). עכשיו הגל ממתין, והשרת גוזר את האינדקס
    מיד (test_wave_after_capture_3137.py). כאן הקבצים אינם פריימים של פורמט 2 בכלל: הגזירה נכשלת, הסבב נכשל
    בשמו ועם הסיבה **לפני** שהגל יוצא, ושום שולח אינו מופעל."""
    ctx, recorder = room_server["ctx"], room_server["recorder"]
    _capture_without_derived_index(ctx)
    # #3702: the derivation is real, but it starts only once the cloner has joined. `gate` starts it inside the first
    # hello's pulse, which runs before the join: a derivation that fails there (and these files fail in milliseconds)
    # fails the round first, nobody joins, and `chunk-pass` below answers 403 `not_member`
    # (test_a_round_failed_before_the_first_hello_has_no_member).
    joined, derive_now = threading.Event(), indexprep.IndexPrep._derive

    def derive_after_join(self, job):
        joined.wait(10)
        derive_now(self, job)
    monkeypatch.setattr(indexprep.IndexPrep, "_derive", derive_after_join)
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = opened.json()["wave_session_id"]
    _hello(room_server, ("chunk",))
    joined.set()

    def failed():
        _hello(room_server, ("chunk",), joining=False)                  # the room's pulse
        return room(room_server["deploy"])["round"]["state"] == "failed"
    assert wait_for(failed, 10), room(room_server["deploy"])["round"]
    reason = room(room_server["deploy"])["round"]["failed_reason"]
    assert reason.startswith("הכנת האימג' לשידור נכשלה") and "עדיין נבנה" not in reason, reason
    assert ctx.chunk.status() is None, "the engine was started for an image whose index is not verified"
    assert recorder.commands == [], "a sender was started for an image whose index is not verified"
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    told = room_server["anon"].post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": first, "pass": 1, "state": "listening",
        "targets": [{"serial": "S1"}, {"serial": "S2"}]})
    assert told.status_code == 200 and told.json()["action"] == "closed", (told.status_code, told.text)


def test_a_round_failed_before_the_first_hello_has_no_member(room_server, monkeypatch):
    """#3702: the other order of the test above, made certain. The derivation ends inside `gate` (the thread runs where it
    is started), so the first hello's pulse fails the round before the cloner joins: the hello says nothing of a wave,
    the cloner is no member, and its `chunk-pass` is refused by name -- it boots its local disk (principle 1)."""
    class Inline:                       # a thread that has already finished when `start()` returns
        def __init__(self, target, args, daemon, name):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(indexprep, "threading", types.SimpleNamespace(Lock=threading.Lock, Thread=Inline))
    ctx = room_server["ctx"]
    _capture_without_derived_index(ctx)
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 2, {CLONER1: ["S1", "S2"]}))
    assert opened.status_code == 200, opened.text
    wave = opened.json()["wave_session_id"]
    answer = _hello(room_server, ("chunk",))
    assert room(room_server["deploy"])["round"]["state"] == "failed" and answer.get("session") is None, answer
    assert ctx.conn.execute("SELECT 1 FROM session_members WHERE session_id = ?", (wave,)).fetchone() is None
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    told = room_server["anon"].post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": first, "pass": 1, "state": "listening",
        "targets": [{"serial": "S1"}, {"serial": "S2"}]})
    assert told.status_code == 403 and told.json()["code"] == "not_member", (told.status_code, told.text)


# --- the adversarial review of #3063 (13/11) -------------------------------------------


def _one_machine_left_to_fetch(rig):
    """Three machines; after the first pass only `c` lacks chunks (5..9): it fetches them alone, and it is
    the only machine the server waits for. The server's ceiling for its `ended` is 1 second here."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 4]]}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[5, 9]]}
    return engine


def test_a_fetch_longer_than_the_servers_ceiling_does_not_fail_the_wave_while_it_advances(rig):
    """סקירה, ממצא 1. ההורדה של מחשב בודד ארוכה מ"המתנה למחשב שהתנתק" (כאן שנייה אחת), והוא היחיד
    שהשרת מחכה לו. הוא מדווח `fetching` כשחתיכות נעשות עמידות: כל דיווח שהוסיף ליומן מאפס את ההמתנה,
    הגל אינו נכשל, והמכונות השלמות אינן מקבלות `closed`."""
    engine = _one_machine_left_to_fetch(rig)
    for last in (5, 6, 7, 8):                       # 2 seconds of a download that advances: twice the ceiling
        time.sleep(0.5)
        assert rig.tell("c", 2, "fetching", {"C1": [[0, last]]}) == {"ok": True, "action": "wait"}
        waiting = rig.tell("a", 1, "ended", {"A1": ALL})
        assert waiting["action"] == "wait" and waiting["whole"] is True, waiting
    assert engine.status()["state"] == "repairing" and rig.named("send_failed") == []
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.status()["repair_rounds"] == 1 and rig.named("chunk_disk_failed") == []


def _c_failed_alone_and_the_wave_went_on(rig, engine, why: str) -> None:
    """What the QA rule asks ("a failure in one station does not stop the others"): the wave is done,
    nobody was told `closed`, `c`'s disk is named with the reason, and `c` hears it when it comes back."""
    assert engine.status()["state"] == "done" and rig.named("send_failed") == [], (engine.status(), rig.events)
    named = [d for d in rig.named("chunk_disk_failed") if d.startswith("c C1:")]
    assert len(named) == 1 and why in named[0], rig.named("chunk_disk_failed")
    assert rig.tell("a", 1, "ended", {"A1": ALL})["action"] == "done"
    assert rig.tell("b", 1, "ended", {"B1": ALL})["action"] == "done"
    late = rig.tell("c", 2, "ended", {"C1": [[0, 4]]})
    assert late["action"] == "done" and why in late["fail"]["C1"], late       # it failed by name, and is told so


def test_a_fetch_that_reports_without_advancing_fails_alone_and_the_wave_goes_on(rig):
    """`fetching` שאינו מוסיף דבר ליומן אינו ראיה שההורדה חיה, ואינו מחזיק את הגל. אבל מי שנכשל הוא
    **המוריד, בשמו, ורק הוא** (סקירה שנייה, ממצא א): עד כאן הגל כולו נכשל, וגם המכונות השלמות קיבלו
    `closed`. הגל מסתיים בזמן שהדיווחים הריקים עוד מגיעים."""
    engine = _one_machine_left_to_fetch(rig)
    for _ in range(8):                              # 2.4 seconds of reports, never a pause as long as the ceiling
        time.sleep(0.3)
        rig.tell("c", 2, "fetching", {"C1": [[0, 4]]})          # nothing new
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")


def test_a_fetcher_that_says_nothing_fails_alone_and_the_wave_goes_on(rig):
    """סקירה שנייה, ממצא א (`repro_fetch.py`): המוריד היחיד שותק. אחרי התקרה הוא נכשל בשמו; `a` ו-`b`
    השלמים מסיימים את הגל."""
    engine = _one_machine_left_to_fetch(rig)
    assert wait_for(lambda: engine.status()["state"] == "done", 4), engine.status()
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")


def test_a_fetcher_that_died_fails_alone_and_nobody_waits_out_the_ceiling_for_it(rig):
    """ממצא א (`repro_lost.py`): המוריד מת באמצע ההורדה וסומן `lost`. אין למי לחכות, ולכן השרת אינו
    יושב עד התקרה: הגל מסתיים מיד, והמכונות השלמות אינן מקבלות `closed`."""
    engine = _one_machine_left_to_fetch(rig)
    rig.members = ["a", "b"]                        # c is no longer a live member of the round
    began = time.monotonic()
    assert wait_for(lambda: engine.status()["state"] == "done", 2), engine.status()
    assert time.monotonic() - began < 0.9, "the server sat out the ceiling for a machine that is gone"
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")


def test_listeners_that_were_all_lost_fail_by_name_and_the_whole_machine_goes_on_to_the_next_partition(rig, tmp_path):
    """ממצא א, בסבב מולטיקאסט: `b` ו-`c` מקבלים מעבר השלמה ואובדים שניהם. `a` השלם אינו נענש: המחיצה
    נסגרת בלעדיהם, והגל ממשיך למחיצה הבאה. שם רק `a` מתייצב, והיא משודרת לו."""
    class Two(Lib):
        def get(self, image_id):
            return {"id": image_id, "partitions": [
                {"index": 3, "file": "p3.windows.pcl.zst", "index_xxh64": "0" * 16, "readback_chunks": 10},
                {"index": 5, "file": "p5.recovery.pcl.zst", "index_xxh64": "0" * 16, "readback_chunks": 1}]}
    two = tmp_path / "two"
    two.mkdir()
    lib = Two(two)
    (two / "p5.recovery.pcl.zst").write_bytes(b"z")
    (two / "p5.recovery.pcl.zst.idx").write_text(
        "IMAGESRV-IDX 2 raw 4194304 0 0 - 4194304 1 1\n0 1 0000000000000000\n", encoding="ascii")
    engine = rig.make(lib=lib)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    lacks = [[0, 4], [7, 9]]
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": lacks})
    assert rig.ask("c", 1, {"C1": lacks}) == {"ok": True, "action": "receive", "pass": 2}
    rig.members = ["a"]                             # both listeners are lost before the repair pass

    assert wait_for(lambda: engine.status()["partition"] == 5, 5), engine.status()
    assert rig.named("send_failed") == []
    failed = rig.named("chunk_disk_failed")
    assert len(failed) == 2 and all("לא דיווח על סיום מעבר ההשלמה" in d for d in failed), failed
    # nobody was left to listen to the repair pass: none was sent into the air
    assert [c[c.index("--pass") + 1] for c in rig.runner.commands if c[c.index("--part") + 1] == "3"] == ["1"]
    assert rig.tell("a", 1, "listening", {"A1": []}, part=5) == {"ok": True, "action": "receive", "pass": 1}
    assert wait_for(lambda: any(c[c.index("--part") + 1] == "5" for c in rig.runner.commands))
    assert rig.ask_part("a", 5, {"A1": [[0, 0]]})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("send_failed") == [] and len(rig.named("send_done")) == 1


def test_when_the_only_machine_of_a_repair_round_is_gone_and_nobody_is_left_the_wave_fails_by_name(rig):
    """הצד השני של אותו כלל: הגל נכשל רק כשלא נשאר אף מחשב חי עם דיסק שלא נכשל."""
    rig.members = ["a"]
    engine = rig.make()
    rig.tell("a", 1, "listening", {"A1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.ask("a", 1, {"A1": [[0, 4]]})["action"] == "fetch"
    rig.members = []
    assert wait_for(lambda: engine.status()["state"] == "failed", 3), engine.status()
    assert "לא נשאר אף מחשב חי" in rig.named("send_failed")[0]
    assert any(d.startswith("a A1:") for d in rig.named("chunk_disk_failed"))


def test_a_fetch_that_trickles_is_noted_as_slow_and_not_failed_and_the_wave_waits_for_it(rig, monkeypatch):
    """#3170 מול #3295 (נדב 15/11, אפשרות ב'): הורדה שהקצב המצטבר שלה נמוך מסף בדיקת הכתיבה (כאן 50MB/s) במשך `FETCH_SLOW_S`
    (כאן שנייה; 10 דקות באמת) נרשמת פעם אחת כ"איטית" -- ו**אינה נכשלת**: הגל ממתין לה כל עוד היא מתקדמת, והדיסק מסיים שלם.
    כאן: חתיכה של 4MB כל 0.4 שניות, 10MB/s."""
    monkeypatch.setattr(chunksend, "FETCH_SLOW_S", 1.0)
    engine = _one_machine_left_to_fetch(rig)
    for last in (5, 6, 7, 8):
        time.sleep(0.4)
        rig.tell("c", 2, "fetching", {"C1": [[0, last]]})       # it advances: this is not silence
    assert engine.status()["state"] == "repairing", engine.status()      # ...and the wave waits for it
    assert rig.named("send_failed") == [] and rig.named("chunk_disk_failed") == []
    (noted,) = [d for d in rig.named("chunk_disk_slow") if d.startswith("c C1:")]      # once, not on every report
    assert "איטית" in noted and "מתחת לסף 50MB/s" in noted and "אינו נכשל" in noted, noted
    whole = rig.ask("c", 2, {"C1": ALL})
    assert whole["action"] == "done" and "fail" not in whole, whole         # no `fail`: nothing for the agent to wipe
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == []


def test_a_fetch_that_writes_fast_and_flushes_rarely_is_not_failed_as_slow(rig, tmp_path, monkeypatch):
    """#3189: הקצב של הורדה נמדד ממה שהדיסק **כותב**, לא רק ממה שעבר flush. על הברזל 17 flushes ל-8,155 חתיכות: היומן
    זז 551 חתיכות ב-10 דקות ("3.9MB/s, מתחת לסף 74"), בזמן ש-`/sys/block/sda/stat` ו-`received` אמרו 40MiB/s. כאן
    היומן אינו זז כלל, ו-`apply` לוקח 300 חתיכות כל 0.3 שניות -- הרבה מעל 50, יותר מ-`FETCH_SLOW_S` (שנייה)."""
    monkeypatch.setattr(chunksend, "FETCH_SLOW_S", 1.0)
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    for taken in range(300, 2700, 300):
        time.sleep(0.3)
        assert _fetching_count(rig, 4000, ((0, 3999),), received=taken) == {"ok": True, "action": "wait"}
    assert rig.named("chunk_disk_failed") == [], rig.named("chunk_disk_failed")
    assert engine.status()["state"] == "repairing"
    assert rig.ask("c", 2, {"C1": everything})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_fetch_that_is_slow_in_what_it_writes_too_is_noted_as_slow_and_not_failed(rig, tmp_path, monkeypatch):
    """השומר של #3189: `received` אינו פטור. הורדה ש-`apply` לוקח בה חתיכה כל 0.3 שניות (כ-14MB/s, מתחת ל-50) נרשמת כאיטית
    (פעם אחת, בשמה), גם כשהמספר שלה גדל -- ומאז #3170/#3295 (נדב 15/11) **אינה נכשלת**: הגל ממתין לה, והיא מסיימת."""
    monkeypatch.setattr(chunksend, "FETCH_SLOW_S", 1.0)
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    for taken in range(1, 9):
        time.sleep(0.3)
        _fetching_count(rig, 4000, ((0, 3999),), received=taken)
    assert any(d.startswith("c C1:") and "איטית" in d for d in rig.named("chunk_disk_slow")), rig.events
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []
    assert engine.status()["state"] == "repairing"
    assert rig.ask("c", 2, {"C1": everything})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_listeners_wait_for_a_fetch_that_advances_and_no_repair_round_is_burnt(rig):
    """סקירה, ממצא 1, עם מאזינים: `a` ו-`b` מקבלים מעבר השלמה, `c` מוריד לבד. המאזינים מסיימים מיד;
    ההורדה נמשכת פי שניים מהתקרה. בלי הדיווחים השרת היה מתכנן שוב ושוב את `c` עד `MAX_REPAIRS`."""
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    lacks_5 = [[0, 4], [6, 9]]
    rig.tell("a", 1, "ended", {"A1": lacks_5})
    rig.tell("b", 1, "ended", {"B1": lacks_5})
    assert rig.ask("c", 1, {"C1": [[0, 5]]}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[6, 9]]}
    for mac in ("a", "b"):
        assert rig.ask(mac, 1, {mac.upper() + "1": lacks_5})["action"] == "receive"
        rig.tell(mac, 2, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    for mac in ("a", "b"):
        done = rig.tell(mac, 2, "ended", {mac.upper() + "1": ALL})
        # whole, and its part of this round done: neither wait is counted by the agent
        assert done["action"] == "wait" and done["whole"] is True and done["others"] is True, done
    for last in (6, 7, 8):                          # 1.5 seconds: longer than the ceiling
        time.sleep(0.5)
        rig.tell("c", 2, "fetching", {"C1": [[0, last]]})
    assert engine.status()["repair_rounds"] == 1, "the server planned again while the fetch was advancing"
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine.status()["repair_rounds"] == 1 and len(rig.runner.commands) == 2


def test_a_machine_that_did_its_part_of_the_round_is_told_the_wait_is_for_others(rig):
    """`others`: המחשב דיווח `ended` על הסבב הנוכחי ועדיין חסר לו משהו; השרת מחכה לאחרים לפני שיתכנן.
    ההמתנה הזו אינה שלו, והסוכן אינו סופר אותה מול התקרה שלו (כמו `whole`)."""
    rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    first = rig.tell("a", 1, "ended", {"A1": [[0, 4]]})
    assert first["action"] == "wait" and first["others"] is True and "whole" not in first, first


def test_a_wave_is_refused_before_anyone_lines_up_when_the_sender_is_not_there(rig):
    """סקירה, ממצא 6: `chunkcast` חסר או אינו רץ. הגל מסורב בשמו מיד — לפני ההתייצבות ולפני בדיקת
    המחיצות — ולא אחרי שכל המשכפלים כבר מאזינים."""
    began = time.monotonic()
    engine = rig.make(binary_check=lambda: chunksend.NOT_INSTALLED)
    assert wait_for(lambda: rig.named("send_failed"), 2) and time.monotonic() - began < 0.9   # start_timeout is 1s
    assert engine.status()["state"] == "failed" and rig.runner.commands == []
    assert "אינו מותקן בשרת" in rig.named("send_failed")[0] and "דבר לא שודר" in rig.named("send_failed")[0]
    assert rig.tell("a", 1, "listening", {"A1": []}) == {"ok": True, "action": "closed"}


def test_the_sender_is_asked_whether_it_runs_not_whether_it_exists():
    """`binary_problem`: ראיה חיובית. קובץ שקיים ואינו רץ (קוד 127 מהמעטפת, ספרייה חסרה) אינו שולח."""
    import subprocess

    def answers(returncode=0, stdout="", stderr="", raises=None):
        def run(cmd, **_kwargs):
            assert cmd == ["chunkcast", "--version"]
            if raises is not None:
                raise raises
            return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)
        return chunksend.binary_problem(run)
    assert answers(stdout="chunkcast 1\n") is None
    assert answers(raises=FileNotFoundError("chunkcast")) == chunksend.NOT_INSTALLED
    assert "PermissionError" in answers(raises=PermissionError("denied"))
    assert "TimeoutExpired" in answers(raises=subprocess.TimeoutExpired("chunkcast", 10))
    broken = answers(returncode=127, stderr="chunkcast: error while loading shared libraries: libzstd.so.1\n")
    assert "אינו מותקן בשרת, או שאינו ניתן להרצה" in broken and "קוד 127" in broken and "libzstd.so.1" in broken
    assert "קוד 0" in answers(stdout="something else\n")          # it ran, and it is not chunkcast


@pytest.mark.parametrize("code", [126, 127])
def test_the_shells_exit_code_for_a_program_that_did_not_run_is_named_as_that(rig, code):
    """השולח רץ דרך `nice`/`ionice`: בינארי חסר אינו `OSError` אלא קוד 127 של המעטפת. ההודעה אומרת
    שהשולח אינו מותקן — לא "השידור נכשל (קוד 127)"."""
    rig.runner = Runner(code=code)
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: rig.named("send_failed")) and engine.status()["state"] == "failed"
    (why,) = rig.named("send_failed")
    assert "לא רץ" in why and "אינו מותקן בשרת" in why and f"קוד {code}" in why and "נכשל על" not in why


def test_the_chunk_engine_does_not_send_beside_another_round_of_its_own(rig):
    """סקירה, ממצא 7: שני שידורים אינם יוצאים במקביל (אותה רשת, אותם דיסקים בשרת, וקצב גל שנקבע לשולח אחד).
    #3173: עד המחיקה זה נבדק בין שני המנועים; עכשיו יש מנוע אחד, וגל של סבב אחר בזמן שהוא משדר מסורב בשמו,
    ושום `chunkcast` נוסף אינו מופעל."""
    rig.runner = Runner(block=True)
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    refused = engine.start({"id": "S-other", "image_id": "img_1"})
    assert refused and "S-other" in refused and f"המנוע עדיין משדר את הסבב {SID}" in refused, refused
    assert engine.status()["session_id"] == SID and engine.status()["state"] == "sending"
    assert len(rig.runner.commands) == 1, rig.runner.commands


def test_the_next_wave_right_after_a_stop_is_not_refused(rig):
    """#3173: the room closes a wave (`on_closed` -> `stop`) and the next one may go running a moment later. `stop` does
    not wait for the sending thread; the thread still writes its end (`send_stopped`: the journal, the rate's sample).
    Here that end takes 0.3 s, as a journal under load does. The next wave waits for a thread that was told to stop --
    it is not refused with "the engine is still sending", and the round does not fail by that name.
    **Negative control:** on the code before the fix `start` answers that refusal."""
    rig.runner = Runner(block=True)
    engine = rig.make()
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    record = engine.on_event

    def slow_end(event, detail, sid):
        if event == "send_stopped":
            time.sleep(0.3)
        record(event, detail, sid)
    engine.on_event = slow_end
    assert engine.stop(SID) is None
    assert engine.start({"id": "S-2", "image_id": "img_1"}) is None
    assert engine.status()["session_id"] == "S-2"
    assert rig.named("send_stopped") == [SID], rig.events


def test_a_wave_closed_before_its_start_arrived_sends_nothing(rig):
    """#2427 (the old sender had it; #3173 ports it): the commit to `running` comes before `on_running`, and a close
    that falls between them reached `stop` for a wave the engine had not heard of yet -- a no-op. The `start` that came
    after it sent a closed wave, and held the next one off until "nobody listened". Now that `start` is refused by name.
    **Negative control:** on the code before the fix the closed wave is started."""
    engine = rig.make()
    assert engine.stop(SID) is None and wait_for(lambda: not engine._thread.is_alive())   # an idle engine
    assert engine.stop("S-closed") is None                    # the close, before its start
    refused = engine.start({"id": "S-closed", "image_id": "img_1"})
    assert refused and "S-closed" in refused and "נסגר לפני שהשידור שלו התחיל" in refused, refused
    assert engine.status()["session_id"] == SID and not engine._thread.is_alive()   # nothing started
    # the guard: another wave still starts, and the closed one is refused once, not forever
    assert engine.start({"id": "S-3", "image_id": "img_1"}) is None


def test_a_thousand_ranges_of_one_file_do_not_scan_the_library_a_thousand_times(server, monkeypatch):
    """סקירה, ממצא 3, הצד של השרת: השלמה של חוסר מפוזר היא אלפי בקשות Range לאותו קובץ, ו-`file_path`
    סורק את הספרייה כולה. הנתיב נשמר לכמה שניות; קובץ שנמחק בינתיים הוא 404, לא 500 ולא מהמטמון."""
    from conftest import PARTITION_BYTES
    ctx = server["ctx"]
    name = next(p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))
    real, calls = ctx.library.file_path, []

    def counted(image_id, filename):
        calls.append(filename)
        return real(image_id, filename)
    monkeypatch.setattr(ctx.library, "file_path", counted)
    for k in range(1000):
        got = server["anon"].get(f"/api/v1/images/img_7f3a91/files/{name}", headers={"Range": f"bytes={k % 20}-{k % 20}"})
        assert got.status_code == 206 and got.content == PARTITION_BYTES[k % 20:k % 20 + 1]
    assert len(calls) == 1, f"{len(calls)} lookups of the library for 1000 ranges of one file"
    assert server["anon"].get("/api/v1/images/img_7f3a91/files/not-declared.bin").status_code == 404
    real("img_7f3a91", name).unlink()
    gone = server["anon"].get(f"/api/v1/images/img_7f3a91/files/{name}", headers={"Range": "bytes=0-0"})
    assert gone.status_code == 404, gone.status_code


def test_a_machine_whose_fetch_has_ended_is_not_noted_as_slow_while_another_still_fetches(rig, monkeypatch):
    """הכלל של הורדה איטית חל על מי שעוד מוריד. ההורדה של `b` נגמרה מיד בלי להביא את החתיכה (החיבור נפל):
    זה ניסיון השלמה שנכשל, והוא יקבל עוד אחד. `c` ממשיך להוריד, בקצב שמעל הסף, יותר מ-`FETCH_SLOW_S`.
    המספרים של `b` אינם מתעדכנים עוד -- ואסור שייקראו כ"אפס MB/s במשך כל הזמן הזה"."""
    monkeypatch.setattr(chunksend, "FETCH_SLOW_S", 1.0)
    engine = rig.make()
    engine.write_floor = lambda: 1                  # 1MB/s: a chunk every 0.5s (8MB/s) is well above it
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    lacks_5 = [[0, 4], [6, 9]]
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": lacks_5})                                # lacks 5, alone
    assert rig.ask("c", 1, {"C1": [[0, 5]]})["action"] == "fetch"             # lacks 6..9, alone
    assert rig.ask("b", 1, {"B1": lacks_5}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[5, 5]]}
    assert rig.tell("b", 2, "ended", {"B1": lacks_5})["action"] == "wait"     # its fetch ended, and brought nothing
    for last in (6, 7, 8):
        time.sleep(0.5)
        rig.tell("c", 2, "fetching", {"C1": [[0, last]]})
    assert rig.named("chunk_disk_failed") == [], "a machine whose fetch had ended was failed as slow"
    assert not [d for d in rig.named("chunk_disk_slow") if d.startswith("b ")], "a fetch that had ended was noted as slow"
    rig.tell("c", 2, "ended", {"C1": ALL})
    assert rig.ask("b", 2, {"B1": lacks_5}) == {"ok": True, "action": "fetch", "pass": 3, "ranges": [[5, 5]]}   # another try
    assert rig.ask("b", 3, {"B1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []


# --- #3112: a long fetch is not silence, whatever the ledger says -----------------------------------------


def _fetching_count(rig, fetched, ranges=((0, 4),), received=None):
    """A `fetching` report as the agent sends it from #3112 on: the ledger's delta, and `fetched` -- the chunks
    durable on its disks by `chunkcast apply`'s own state file. Here the ledger never moves (the VM lab, 13/11).
    `received` (#3149): the chunks `apply` has taken from the stream, flushed or not."""
    body = {"part": 3, "pass": 2, "state": "fetching", "fetched": fetched, "targets": [
        {"serial": "C1", "write_mbps": 120, "durable": [{"part": 3, "ranges": [list(r) for r in ranges]}]}]}
    if received is not None:
        body["received"] = received
    return rig.engine.report(SID, "c", body)


def test_a_fetch_whose_count_grows_holds_the_wave_though_the_ledger_does_not_move(rig):
    """מעבדת ה-VM 13/11 (room_4772cdef): הורדה של 2,751 טווחים, והגל נסגר כ"שתק" אחרי 6.7 דקות -- "לא נשאר אף
    מחשב חי". כאן היומן לא זז כלל (כל דיווח חוזר על 0-4), אבל המספר שהסוכן קורא בקובץ המצב של `apply` גדל: 2.4
    שניות של הורדה, יותר מכפליים מהתקרה (שנייה) -- הגל ממתין, ואף אחד אינו נכשל."""
    engine = _one_machine_left_to_fetch(rig)
    for n in (6, 7, 8, 9, 10, 11, 12, 13):
        time.sleep(0.3)
        assert _fetching_count(rig, n) == {"ok": True, "action": "wait"}
    assert engine.status()["state"] == "repairing" and rig.named("send_failed") == [], rig.events
    assert rig.named("chunk_disk_failed") == [], rig.named("chunk_disk_failed")
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


def test_a_fetch_whose_count_stands_still_is_silence_and_fails_alone(rig):
    """השומר: אותו מספר שוב ושוב אינו ראיה שההורדה חיה (עיקרון 5). המוריד נכשל בשמו ולבדו, והגל ממשיך."""
    engine = _one_machine_left_to_fetch(rig)
    for _ in range(8):
        time.sleep(0.3)
        _fetching_count(rig, 6)
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")


# --- #3149: the first word of a long fetch comes before its first flush, and a fetch that moves is not stuck ----------


def _thousands_left_to_fetch(rig, tmp_path, chunks=8000, held=4000):
    """The VM lab, 14/11 (room_a6cf9813): p3 of a Windows image on two drawers, 7,935 chunks left for `c` to fetch.
    `a` and `b` are whole; `c` holds `held` chunks and fetches the rest. The server's ceiling is 1 second here."""
    engine = rig.make(lib=Lib(tmp_path, chunks=chunks))
    everything = [[0, chunks - 1]]
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": everything})
    rig.tell("b", 1, "ended", {"B1": everything})
    assert rig.ask("c", 1, {"C1": [[0, held - 1]]}) == {
        "ok": True, "action": "fetch", "pass": 2, "ranges": [[held, chunks - 1]]}
    return engine, everything


def test_a_fetch_of_thousands_whose_first_flush_comes_after_the_ceiling_still_ends_done(rig, tmp_path):
    """#3149 (מעבדת ה-VM 14/11): ההורדה של 7,935 חתיכות על דיסק thin איטי. `apply` לקח 2,569 חתיכות מהזרם בתוך 342 שניות,
    אבל ה-flush הראשון חזר רק אז -- ובקובץ המצב לא זז דבר, ולכן הדיווח הראשון (`fetched`) הגיע 340 שניות אחרי שהורדה
    התחילה, והתקרה של השרת 330. הסבב נכשל 9 שניות לפני, וה-GPT נמחק בשתי המגירות. כאן: 2.4 שניות (פי 2.4 מהתקרה) של דיווחים
    שה-`fetched` שלהם עומד על מה שהיה, והיומן אינו זז, ו-`received` גדל; אחריהם ה-flush הראשון -- והסבב מסתיים done, בלי כשל."""
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    for taken in range(300, 2700, 300):                      # 8 reports: `apply` takes 300 chunks between two of them
        time.sleep(0.3)
        assert _fetching_count(rig, 4000, ((0, 3999),), received=taken) == {"ok": True, "action": "wait"}
    assert engine.status()["state"] == "repairing" and rig.named("send_failed") == [], rig.events
    assert rig.named("chunk_disk_failed") == [], rig.named("chunk_disk_failed")
    assert _fetching_count(rig, 6700, ((0, 6699),), received=2900)["action"] == "wait"      # the first flush is back
    assert rig.ask("c", 2, {"C1": everything})["action"] == "done"
    assert rig.ask("a", 1, {"A1": everything})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []


def test_chunks_taken_are_proof_of_life_and_never_durable(rig, tmp_path):
    """`received` מזיז את השעון ותו לא: הוא אינו נכנס ליומן, ואינו מעלה את מה שהשרת סופר כעמיד. החתיכות שנלקחו ולא נעשו עמידות
    עדיין חסרות -- סבב שמסתיים עכשיו (`ended` בלי אותן) מתכנן אותן מחדש, כי מה שלא עבר flush אינו מה שהדיסק מחזיק."""
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    assert repair.count(engine.ledger.durable(SID, "c", "C1", 3)) == 4000
    for taken in (500, 1000, 1500):
        time.sleep(0.2)
        _fetching_count(rig, 4000, ((0, 3999),), received=taken)
    assert repair.count(engine.ledger.durable(SID, "c", "C1", 3)) == 4000, (
        "chunks taken from the stream became durable in the ledger before any flush said so")
    assert engine.status()["state"] == "repairing"
    late = rig.ask("c", 2, {"C1": [[0, 5999]]})          # the pass ended with 6,000 durable: 2,000 are still missing
    assert late == {"ok": True, "action": "fetch", "pass": 3, "ranges": [[6000, 7999]]}, late


def test_a_fetch_that_takes_no_more_chunks_and_flushes_nothing_is_still_silence(rig, tmp_path):
    """השומר: אותו `received` ואותו `fetched` שוב ושוב אינם התקדמות. תקרה מוחלטת נשארת להורדה שתקועה באמת, והמוריד נכשל
    בשמו ולבדו כמו תמיד -- `received` שאינו גדל לא הופך אותו לחי."""
    engine, _everything = _thousands_left_to_fetch(rig, tmp_path)
    for _ in range(8):
        time.sleep(0.3)
        _fetching_count(rig, 4000, ((0, 3999),), received=700)
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")


def test_the_ceiling_follows_the_rhythm_a_fetch_was_measured_at(rig, tmp_path, monkeypatch):
    """#3149: התקרה אינה מספר קבוע להורדה שמתקדמת בקפיצות. כאן התקרה 1.5 שניות (`ENDED_GRACE_S` 0.5), ההתקדמות הראשונה
    באה אחרי שנייה, והבאה 2.2 שניות אחריה -- יותר מהתקרה, פחות מפי שלושה מהפער שנמדד (3 שניות). ההורדה חיה, והסבב מסתיים."""
    monkeypatch.setattr(chunksend, "ENDED_GRACE_S", 0.5)
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    time.sleep(1.0)
    assert _fetching_count(rig, 4000, ((0, 3999),), received=400)["action"] == "wait"
    time.sleep(2.2)                                           # 2.2 > 1.5: a fixed ceiling would have closed this fetch 0.7 seconds ago
    assert engine.status()["state"] == "repairing" and rig.named("chunk_disk_failed") == [], rig.events
    assert _fetching_count(rig, 6000, ((0, 5999),), received=1800)["action"] == "wait"
    assert rig.ask("c", 2, {"C1": everything})["action"] == "done"
    assert rig.ask("a", 1, {"A1": everything})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []


class _TwoParts(Lib):
    """Two chunk partitions, 3 and 5: the second is a "later" one, with the longer settling wait."""

    def __init__(self, root):
        super().__init__(root)
        (root / "p5.data.pcl.zst").write_bytes(b"z" * self.chunks)
        (root / "p5.data.pcl.zst.idx").write_text((root / "p3.windows.pcl.zst.idx").read_text(), encoding="ascii")

    def get(self, image_id):
        got = super().get(image_id)
        got["partitions"] = [got["partitions"][0], {**got["partitions"][0], "index": 5, "file": "p5.data.pcl.zst"}]
        return got


def test_a_later_partition_nobody_settles_on_names_the_wait_it_waited(rig, monkeypatch, tmp_path):
    """#3112, G3 (ברזל 13/11, room_714f980d): הסבב נסגר אחרי 780 שניות -- `later_start_timeout`, ההמתנה לכך שמשכפל
    יתחיל להאזין למחיצה **מאוחרת** -- וההודעה אמרה "תוך 180 שניות": המספר של המחיצה הראשונה. כאן הראשונה 1 שנייה
    והמאוחרת 2, ואיש אינו מאזין למחיצה 5: ההודעה אומרת 2."""
    monkeypatch.setattr(chunkctx, "LATER_MAX_WAIT", 1)
    engine = rig.make(lib=_TwoParts(tmp_path))
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for mac in rig.members:
        assert rig.ask(mac, 1, {mac.upper() + "1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "failed", 6), engine.status()
    failed = rig.named("send_failed")
    assert failed and "p5.data.pcl.zst תוך 2 שניות" in failed[0], failed


def test_a_receiver_that_dropped_packets_of_another_source_is_named_once_per_growth_and_zero_is_no_event(rig):
    """#1079/#2485: `stray` ב-`ended` של מעבר. 0 הוא ראיה ואינו אירוע; מספר חיובי הוא אירוע אחד, וההודעה החוזרת
    (`ended` נשלח שוב ושוב עד שהתשובה אינה `wait`) אינה כותבת אותו שוב; מספר שגדל כותב שוב. חסר, שלילי או
    שאינו מספר שלם = לא נאמר, ובלי אירוע. `listening` אינו נושא אותו."""
    rig.make()
    assert rig.tell("a", 1, "listening", {"A1": []}, stray=9)["action"] == "receive"
    for mac in ("b", "c"):          # the wave is alive only while everyone listens: alone, `start_timeout` closes it (and `report` answers "closed")
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.named("chunk_stray_source") == []
    for stray in (None, 0, -3, True, "7", 2.5):
        assert rig.tell("a", 1, "ended", {"A1": ALL}, stray=stray)["action"] != "closed"
    assert rig.named("chunk_stray_source") == [], rig.named("chunk_stray_source")
    for stray in (8, 8, 8):
        rig.tell("a", 1, "ended", {"A1": ALL}, stray=stray)
    (named,) = rig.named("chunk_stray_source")
    assert named.startswith("a:") and "8 חבילות" in named and "מחיצה 3" in named and "מעבר 1" in named, named
    rig.tell("a", 1, "ended", {"A1": ALL}, stray=13)
    assert len(rig.named("chunk_stray_source")) == 2 and "13 חבילות" in rig.named("chunk_stray_source")[1]


def test_the_wave_names_the_servers_source_and_the_sender_binds_to_it(rig):
    """#1079/#2485: מה שהמקלטים מקבלים ב-hello (`session.chunk.source`) הוא אותה כתובת שהשולח נקשר אליה (`--from`)."""
    engine = rig.make(source="10.20.0.1")
    assert engine.session_fields(SID)["chunk"]["source"] == "10.20.0.1"
    for mac in rig.members:
        assert rig.tell(mac, 1, "listening", {mac.upper() + "1": []})["action"] == "receive"
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    cmd = rig.runner.commands[0]
    assert cmd[cmd.index("--from") + 1] == "10.20.0.1"


def test_no_source_means_no_field_and_no_from(rig):
    """אין כתובת שתחנה רואה: השדה אינו נשלח, והשולח אינו נקשר (הסוכן אומר זאת באזהרה)."""
    engine = rig.make()
    assert "source" not in engine.session_fields(SID)["chunk"]
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert "--from" not in rig.runner.commands[0]


@pytest.mark.parametrize("base,source", [
    ("http://10.20.0.1:8080", "10.20.0.1"), ("http://10.20.0.1", "10.20.0.1"),
    ("http://127.0.0.1:8080", None), ("http://0.0.0.0:8080", None),
    ("http://server.example:8080", None), ("http://[fe80::1]:8080", None), ("", None), (None, None)])
def test_source_of_is_the_numeric_address_of_the_server_url(base, source):
    assert chunksend.source_of(base) == source
