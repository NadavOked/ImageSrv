"""#2023 (נדב 15/11): בהשלמה עוצרים רק את המגירה שנפגעה, והמוח מחליט. החיווט בצד השרת של מנוע החתיכות.

PR 1 (בצל): ההחלטה נרשמת ושום דבר לא משתנה. PR 2 (הביצוע, מאחורי `brain_drawer_hold`, כבויה כברירת מחדל):

1. **כבוי -- שום דבר לא משתנה.** המוח מחזיר דבר ריק, והתשובה לסוכן, הסבב והכשלים כמו תמיד, גם כשהמוח זורק חריגה.
2. **דלוק, `CONTINUE` -- מגירה אחת משלימה ברקע והאחרות מקבלות את המחיצה הבאה.** התשובה ל-`fetching` נושאת `hold`, המגירה יוצאת
   מהמחיצה (ורק ממנה), והשרת סוגר אותה בלעדיה. רק כשיש מי שממתין, ורק אם למשכפל שלה נשארת מגירה אחרת בזרם.
3. **דלוק, `DROP` -- הדיסק נכשל בשמו רק אחרי שתי הערכות רצופות**, ורק כשיש מי שממתין.
4. **השתיקה נשארת כמו היום** (`FETCH_SLOW_S`, `_fail_silent`), גם כשהמתג דלוק.
5. **הורדה איטית שמתקדמת אינה נכשלת** (#3170/#3295), כשהמוח כבוי או שלא החליט `DROP` פעמיים.

ההחלטה עצמה (`T_continue` מול `T_redo`) והיסטרזיס של `DROP` בקובץ `test_brain_drawer_hold_2023.py`; הצד של הסוכן (`hold` -> השלמה
ברקע) ב-`test_chunk_drawer_hold_exec_2023.py`.
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from server.rounds import chunksend

from test_chunksend_2533 import ALL, SID, rig, wait_for   # noqa: F401 -- rig is a fixture
from test_chunksend_2533 import (
    _TwoParts, _c_failed_alone_and_the_wave_went_on, _fetching_count, _one_machine_left_to_fetch,
    _thousands_left_to_fetch,
)
from test_server_room import room_server  # noqa: F401 -- shared fixture


def test_the_engine_hands_every_fetching_report_to_the_brain_with_what_it_measured(rig, tmp_path):
    """(neg) A drive of `c` completes 5..9 of partition 3 while partition 5 is still ahead: the brain is told what is left on
    the drive, what grew on it since the fetch began, what `apply` took from the stream, how long it has run, the chunks of
    the partitions after this one (a drawer the others left behind completes those too), and the whole image's size and
    rate -- never a number the engine did not read."""
    calls: list[dict] = []
    engine = rig.make(lib=_TwoParts(tmp_path))
    engine.drawer_hold = lambda **kw: calls.append(kw)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 4]]}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[5, 9]]}
    time.sleep(0.2)
    assert _fetching_count(rig, 7, ((0, 6),), received=2) == {"ok": True, "action": "wait"}
    fetching = [c for c in calls if not c["ended"] and c["mac"] == "c"]
    assert fetching, calls
    got = fetching[-1]
    assert (got["session_id"], got["part"], got["mac"]) == (SID, 3, "c")
    assert got["drawers"] == [{"serial": "C1", "remaining": 3, "grown": 2}], got["drawers"]
    assert got["received"] == 2 and 0 < got["elapsed_s"] < 5
    assert got["later_chunks"] == 10 and got["parts"] == 2
    assert got["chunks_total"] == 20 and got["zbytes_total"] == 20 and got["bits"] == 700 * 10 ** 6
    # the fetch ends, whole: the brain is told once more, with nothing left on any drive -- to close what it decided
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert any(c["ended"] and c["mac"] == "c" and c["drawers"] == [] for c in calls), calls


def test_the_answer_to_the_agent_and_the_wave_do_not_depend_on_the_brain(rig):
    """The guard: a brain that raises changes nothing -- the answer is the same `wait`, the wave goes on, and `c` finishes."""
    def boom(**_kw):
        raise RuntimeError("the brain is down")

    engine = _one_machine_left_to_fetch(rig)
    engine.drawer_hold = boom
    time.sleep(0.1)
    assert _fetching_count(rig, 7, ((0, 6),)) == {"ok": True, "action": "wait"}
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done"
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []


def test_a_fetch_the_brain_is_not_asked_about_is_not_a_fetch_at_all(rig):
    """The guard: only a machine that fetches is judged -- `a` reporting `ended` in pass 1 is not a completion."""
    calls: list[dict] = []
    engine = _one_machine_left_to_fetch(rig)
    engine.drawer_hold = lambda **kw: calls.append(kw)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("a", 1, "listening", {"A1": []})
    assert calls == []


def test_the_engine_measures_the_overhead_of_a_partition_from_the_end_of_the_one_before(rig, tmp_path):
    """(neg) Partition 3 is whole on all three machines; the engine goes on to 5 and says how long that took -- once, and not
    before partition 3 (nothing came before it). That is the overhead the brain adds to a rewrite for every partition."""
    gaps: list[tuple[str, float]] = []
    engine = rig.make(lib=_TwoParts(tmp_path))
    engine.part_gap = lambda sid, seconds: gaps.append((sid, seconds))
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for mac in rig.members:
        assert rig.ask(mac, 1, {mac.upper() + "1": ALL})["action"] == "done"
    assert gaps == [], "a gap was measured before the second partition"
    time.sleep(0.2)                                         # the machines take their time to listen to partition 5
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, part=5)
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    assert len(gaps) == 1 and gaps[0][0] == SID and gaps[0][1] >= 0.2, gaps


def test_a_brain_that_cannot_measure_the_gap_does_not_stop_the_wave(rig, tmp_path):
    def boom(*_a):
        raise RuntimeError("the brain is down")

    engine = rig.make(lib=_TwoParts(tmp_path))
    engine.part_gap = boom
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    for mac in rig.members:
        assert rig.ask(mac, 1, {mac.upper() + "1": ALL})["action"] == "done"
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []}, part=5)
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    for mac in rig.members:
        assert rig.ask_part(mac, 5, {mac.upper() + "1": ALL})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done") and rig.named("send_failed") == []


def test_silence_still_fails_the_downloader_alone_with_the_brain_wired(rig):
    """The second decision: silence is today's rule (`FETCH_SLOW_S`, `_fail_silent`) and the brain does not touch it. A fetch whose
    count stands still has no rate -- the brain is told so (no progress), and `c` fails by name, alone, as before."""
    from server.brain import drawerhold
    verdicts: list[str] = []

    def judged(**kw):
        for d in kw["drawers"]:
            verdicts.append(drawerhold.judge(
                remaining=d["remaining"], grown=d["grown"], received=kw["received"], elapsed_s=kw["elapsed_s"],
                later_chunks=kw["later_chunks"], chunks_total=kw["chunks_total"], zbytes_total=kw["zbytes_total"],
                bits=kw["bits"], parts=kw["parts"], overhead_s=0.5).verdict)

    engine = _one_machine_left_to_fetch(rig)
    engine.drawer_hold = judged
    for _ in range(8):
        time.sleep(0.3)
        _fetching_count(rig, 6)
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")
    assert verdicts and set(verdicts) == {"UNDECIDED"}, verdicts       # nothing measured: nothing decided, never a DROP


def test_a_progressing_slow_drive_is_not_failed_even_when_the_brain_would_drop_it(rig, tmp_path, monkeypatch):
    """The third decision (#3170/#3295 stand): `apply` takes a chunk every 0.3 s, far under the minimum -- the drive is noted
    slow for FETCH_SLOW_S and is NOT failed, whatever the brain says. Here the brain says DROP (it is a shadow: nothing moves)."""
    from server.brain import drawerhold
    monkeypatch.setattr(chunksend, "FETCH_SLOW_S", 1.0)
    verdicts: list[str] = []

    def judged(**kw):
        for d in kw["drawers"]:
            verdicts.append(drawerhold.judge(
                remaining=d["remaining"], grown=d["grown"], received=kw["received"], elapsed_s=kw["elapsed_s"],
                later_chunks=kw["later_chunks"], chunks_total=kw["chunks_total"], zbytes_total=kw["zbytes_total"],
                bits=kw["bits"], parts=kw["parts"], overhead_s=0.5).verdict)

    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    engine.drawer_hold = judged
    for taken in range(1, 9):
        time.sleep(0.3)
        _fetching_count(rig, 4000, ((0, 3999),), received=taken)
    assert "DROP" in verdicts, verdicts                                 # the brain would write it again...
    assert any(d.startswith("c C1:") and "איטית" in d for d in rig.named("chunk_disk_slow")), rig.events
    assert rig.named("chunk_disk_failed") == [] and rig.named("send_failed") == []     # ...and nothing was failed
    assert engine.status()["state"] == "repairing"
    assert rig.ask("c", 2, {"C1": everything})["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")


# --- ההגדרה והחיווט באפליקציה --------------------------------------------------------------------------


def test_the_setting_is_slow_by_default_and_takes_only_off_slow_or_on(server):
    """(neg) `brain_drawer_hold`: GET says `slow` before anyone touched it (#3605: only a drive the brain marked slow is held); `off`/`slow`/`on`
    are taken; anything else is a 400 in Hebrew and nothing is stored; `deploy` is refused (403)."""
    admin, deploy = server["admin"], server["deploy"]
    assert admin.get("/api/console/settings").json()["brain_drawer_hold"] == "slow"
    assert deploy.post("/api/console/settings", json={"brain_drawer_hold": "on"}).status_code == 403
    assert admin.post("/api/console/settings", json={"brain_drawer_hold": "on"}).status_code == 200
    assert admin.get("/api/console/settings").json()["brain_drawer_hold"] == "on"
    for bad in ("enforce", "true", "", "ON", 1, True):
        got = admin.post("/api/console/settings", json={"brain_drawer_hold": bad})
        assert got.status_code == 400 and "off, slow או on" in got.json()["detail"], (bad, got.text)
        assert admin.get("/api/console/settings").json()["brain_drawer_hold"] == "on"
    assert admin.post("/api/console/settings", json={"brain_drawer_hold": "off"}).status_code == 200
    assert admin.get("/api/console/settings").json()["brain_drawer_hold"] == "off"


def test_the_change_of_the_setting_reads_in_hebrew_in_the_journal(server):
    """(neg) `setting_change` says what was changed and to what, in words (and `test_journal_events_he` holds the label)."""
    admin, ctx = server["admin"], server["ctx"]
    assert admin.post("/api/console/settings", json={"brain_drawer_hold": "on"}).status_code == 200
    from server.console.journal_he import SETTINGS_HE
    assert SETTINGS_HE["brain_drawer_hold"] == "המוח: השלמה מעכבת רק את המגירה שנפגעה"
    assert ctx.conn.execute("SELECT detail FROM journal WHERE event = 'setting_change' ORDER BY id DESC LIMIT 1"
                            ).fetchone()["detail"].startswith("brain_drawer_hold=on")


def test_the_app_wires_the_brain_to_the_chunk_engine(room_server):
    """(neg) The server connects the brain to the engine: the engine has a hook to tell it each completion, and one to tell it the
    gap between two partitions."""
    chunk = room_server["ctx"].chunk
    assert getattr(chunk, "drawer_hold", None) is not None and getattr(chunk, "part_gap", None) is not None


# --- הביצוע: brain_drawer_hold = on (#2023, PR 2) ------------------------------------------------------------------

CONTINUE, DROP = "CONTINUE", "DROP"


def _only_fetching(verdicts: dict):
    """A brain that says `verdicts` of whatever is fetching, and nothing at the end of a fetch -- what `DrawerHold.evaluate` does."""
    return lambda **kw: {} if kw["ended"] else verdicts


def _ended(rig, mac: str, rnd: int, disks: dict, part: int = 3):
    """`ended` again and again, as the agent asks, until the answer is not `wait`."""
    got: list = []
    assert wait_for(lambda: (got.append(rig.tell(mac, rnd, "ended", disks, part=part)) or got[-1].get("action") != "wait")), got[-1]
    return got[-1]


def _fetching_c(rig, fetched: int = 7, received: int = 2) -> dict:
    """`c` fetches C1's missing 5..9 of partition 3 (pass 2); its second drawer, C2, is whole and says nothing new."""
    return rig.engine.report(SID, "c", {"part": 3, "pass": 2, "state": "fetching", "fetched": fetched, "received": received, "targets": [
        {"serial": "C1", "write_mbps": 120, "durable": [{"part": 3, "ranges": [[0, fetched - 1]]}]},
        {"serial": "C2", "write_mbps": 120}]})


def _c_fetches_c1(rig, tmp_path, verdicts):
    """Two partitions (3 and 5). In 3, `a` and `b` are whole and `c` -- two drawers -- lacks 5..9 on C1 and fetches it."""
    engine = rig.make(lib=_TwoParts(tmp_path))
    engine.drawer_hold = _only_fetching(verdicts)
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": [], **({"C2": []} if mac == "c" else {})})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    rig.tell("b", 1, "ended", {"B1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 4]], "C2": ALL}) == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[5, 9]]}
    assert wait_for(lambda: (3, "c") in engine._wave.fetching)
    return engine


def test_with_the_switch_on_one_drawer_completes_in_the_background_and_the_others_get_the_next_partition(rig, tmp_path):
    """(neg) The point of #2023. `c`'s C1 completes 5..9 over HTTP while `a`, `b` and `c`'s own C2 are whole and idle. The brain says
    CONTINUE, there are those waiting, and `c` keeps C2 in the stream: the server answers the `fetching` report with `hold: [C1]`,
    takes C1 out of partition 3 and closes it as soon as `c` says `ended` without it -- `a` and `b` are told `done` and listen to
    partition 5 while C1 completes in the background. Nobody waits for the drive."""
    told: list[tuple] = []
    engine = _c_fetches_c1(rig, tmp_path, {"C1": (CONTINUE, "ההשלמה ממשיכה")})
    engine.drawer_held = lambda *a: told.append(a)
    assert rig.tell("a", 1, "ended", {"A1": ALL})["action"] == "wait"                    # before the hold: `a` waits for the drive
    assert _fetching_c(rig) == {"ok": True, "action": "wait", "hold": ["C1"]}
    assert _fetching_c(rig, 8, 3)["hold"] == ["C1"]                                      # asked again: the same, and no second event
    assert rig.ask("c", 2, {"C2": ALL})["action"] == "done"                               # C1 is not in its report: it is not the partition's any more
    assert rig.ask("a", 1, {"A1": ALL})["action"] == "done" and rig.ask("b", 1, {"B1": ALL})["action"] == "done"
    assert (3, "c", "C1") in engine._wave.held_disks and ("c", "C1") not in engine._wave.failed_disks     # held, NOT failed
    assert len(rig.named("chunk_disk_held")) == 1 and rig.named("chunk_disk_held")[0].startswith("c C1:"), rig.events
    assert rig.named("chunk_disk_failed") == [] and told == [(SID, 3, "c", "C1", CONTINUE)], (rig.events, told)
    for mac, disks in (("a", {"A1": []}), ("b", {"B1": []}), ("c", {"C2": []})):
        rig.tell(mac, 1, "listening", disks, part=5)                                       # the next partition: the stream goes on without C1
    assert wait_for(lambda: len(rig.runner.commands) == 2), "partition 5 did not start while C1 completes"
    for mac, disks in (("a", {"A1": ALL}), ("b", {"B1": ALL}), ("c", {"C2": ALL})):
        assert rig.ask_part(mac, 5, disks)["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done") and rig.named("send_failed") == []


def test_a_drawer_held_in_one_partition_is_a_drawer_like_the_others_in_the_next_one(rig, tmp_path):
    """(neg) `held_disks` is per partition: C1 completed in the background, caught up, and joined partition 5's stream -- and there it
    lacks chunks like any drawer, and the server plans them for it. A hold that stuck to the wave would leave it unrepaired."""
    engine = _c_fetches_c1(rig, tmp_path, {"C1": (CONTINUE, "ההשלמה ממשיכה")})
    assert _fetching_c(rig)["hold"] == ["C1"]
    assert rig.ask("c", 2, {"C2": ALL})["action"] == "done"
    for mac in ("a", "b"):
        assert rig.ask(mac, 1, {mac.upper() + "1": ALL})["action"] == "done"
    for mac, disks in (("a", {"A1": []}), ("b", {"B1": []}), ("c", {"C1": [], "C2": []})):
        rig.tell(mac, 1, "listening", disks, part=5)
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    rig.tell("a", 1, "ended", {"A1": ALL}, part=5)
    rig.tell("b", 1, "ended", {"B1": ALL}, part=5)
    got = _ended(rig, "c", 1, {"C1": [[0, 4]], "C2": ALL}, part=5)
    assert got == {"ok": True, "action": "fetch", "pass": 2, "ranges": [[5, 9]]}, got


def test_with_the_switch_off_nothing_changes_the_others_still_wait_for_the_slow_drive(rig, tmp_path):
    """The guard, stated: the brain says nothing to execute (`DrawerHold.evaluate` with the setting off) -- the answer is the plain `wait`,
    `a`, whole, waits for `c`'s drive, and the wave ends done. This is the test that was `not_wired_yet` in the shadow change."""
    calls: list[dict] = []
    engine = _c_fetches_c1(rig, tmp_path, {})
    engine.drawer_hold = lambda **kw: calls.append(kw) or {}
    assert _fetching_c(rig) == {"ok": True, "action": "wait"} and calls, calls
    waiting = rig.tell("a", 1, "ended", {"A1": ALL})
    assert waiting["action"] == "wait" and waiting["whole"] is True, waiting      # `a` is whole and still waits for the drive
    assert rig.ask("c", 2, {"C1": ALL, "C2": ALL})["action"] == "done"
    for mac, disks in (("a", {"A1": []}), ("b", {"B1": []}), ("c", {"C1": [], "C2": []})):
        rig.tell(mac, 1, "listening", disks, part=5)
    assert wait_for(lambda: len(rig.runner.commands) == 2)
    for mac, disks in (("a", {"A1": ALL}), ("b", {"B1": ALL}), ("c", {"C1": ALL, "C2": ALL})):
        assert rig.ask_part(mac, 5, disks)["action"] == "done"
    assert wait_for(lambda: engine.status()["state"] == "done")
    assert engine._wave.held_disks == set() and rig.named("chunk_disk_held") == [] and rig.named("chunk_disk_failed") == []


def test_nobody_waiting_for_the_drive_means_no_hold_and_no_drop(rig):
    """(neg) One machine, one drawer: nobody is waiting for C1, so holding it frees no one, and dropping it would lose a drive for
    nothing. Whatever the brain says (CONTINUE, or DROP twice), the answer is the plain `wait` and the drive goes on completing."""
    rig.members = ["c"]
    engine = rig.make()
    rig.tell("c", 1, "listening", {"C1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    assert rig.ask("c", 1, {"C1": [[0, 4]]})["action"] == "fetch"
    assert wait_for(lambda: (3, "c") in engine._wave.fetching)
    for verdict in (CONTINUE, DROP, DROP):
        engine.drawer_hold = _only_fetching({"C1": (verdict, "x")})
        assert _fetching_count(rig, 7, ((0, 6),)) == {"ok": True, "action": "wait"}
    assert rig.named("chunk_disk_held") == [] and rig.named("chunk_disk_failed") == [] and engine._wave.failed_disks == {}
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done" and wait_for(lambda: engine.status()["state"] == "done")


def test_a_machines_last_drawer_is_not_held_because_it_would_have_nothing_to_feed_the_next_partition(rig):
    """(neg) `a` is whole and waiting, but `c` has ONE drawer: held, the agent would have no drawer in the stream
    (`chunk_partition_drawers`: "no drawers left") and the server would wait for its `listening` on the next partition. No hold: the
    others wait as they do today, and C1 completes in the foreground."""
    rig.members = ["a", "c"]
    engine = rig.make()
    engine.drawer_hold = _only_fetching({"C1": (CONTINUE, "ההשלמה ממשיכה")})
    for mac in rig.members:
        rig.tell(mac, 1, "listening", {mac.upper() + "1": []})
    assert wait_for(lambda: len(rig.runner.commands) == 1)
    rig.tell("a", 1, "ended", {"A1": ALL})
    assert rig.ask("c", 1, {"C1": [[0, 4]]})["action"] == "fetch"
    assert wait_for(lambda: (3, "c") in engine._wave.fetching)
    assert _fetching_count(rig, 7, ((0, 6),)) == {"ok": True, "action": "wait"} and engine._wave.held_disks == set()
    assert rig.ask("c", 2, {"C1": ALL})["action"] == "done" and rig.ask("a", 1, {"A1": ALL})["action"] == "done"


def test_the_brain_is_not_asked_to_hold_what_it_did_not_judge_continue(rig, tmp_path):
    """The guard: UNDECIDED is not in what the brain returns, and a serial the brain names that is not fetching here is not held."""
    engine = _c_fetches_c1(rig, tmp_path, {"NOT-HERE": (CONTINUE, "x"), "C2": ("UNDECIDED", "x"), "C1": ("MAYBE", "x")})
    assert _fetching_c(rig) == {"ok": True, "action": "wait"}
    assert engine._wave.held_disks == set() and engine._wave.failed_disks == {}


def test_a_drop_is_executed_only_after_the_brain_said_it_twice_and_somebody_waits(rig, tmp_path):
    """(neg) The real brain, the setting on. `c` fetches 4,000 chunks at a few a second, `a` and `b` are whole and wait, and the overhead
    per partition was measured (0 s): rewriting the drive is far shorter than completing it -- DROP. The first report only counts it;
    the second fails C1 by name, in the answer to that same `fetching` report (`fail`), with the brain's sentence, and the wave goes on."""
    from server import db
    from server.brain import drawerhold, memory
    conn = db.connect(tmp_path / "imagesrv.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    hold = drawerhold.DrawerHold(conn, mode_fn=lambda: "shadow", hold=lambda: "on")
    hold.gap(SID, 0.0)
    engine, everything = _thousands_left_to_fetch(rig, tmp_path)
    engine.drawer_hold, engine.drawer_held = hold.evaluate, hold.executed
    time.sleep(0.3)
    assert _fetching_count(rig, 4001, ((0, 4000),), received=1) == {"ok": True, "action": "wait"}      # DROP once: counted, not executed
    assert rig.named("chunk_disk_failed") == []
    time.sleep(0.3)
    got = _fetching_count(rig, 4002, ((0, 4001),), received=2)
    assert got["action"] == "wait" and list(got["fail"]) == ["C1"] and "ייכתב בגל הבא" in got["fail"]["C1"], got
    (failed,) = rig.named("chunk_disk_failed")
    assert failed.startswith("c C1:") and "ייכתב בגל הבא" in failed, failed
    assert ("c", "C1") in engine._wave.failed_disks and engine._wave.held_disks == set()
    rows = [d for d in memory.decisions(conn, rule="K3", limit=20) if d["action"] == "DRAWER_HOLD"]
    assert rows and rows[0]["outcome"] == "בוצע: הדיסק נכשל בשמו וייכתב בגל הבא", rows[0]       # the decision is closed by what was done
    assert "execution_wired=yes" in rows[0]["evidence"] and "execution=on" in rows[0]["evidence"], rows[0]["evidence"]
    late = rig.tell("c", 2, "ended", {"C1": [[0, 4001]]})                                      # the agent says `ended` with C1 failed
    assert late["action"] in ("done", "wait") and wait_for(lambda: rig.tell("a", 1, "ended", {"A1": everything}).get("action") == "done")
    assert rig.tell("b", 1, "ended", {"B1": everything})["action"] == "done" and wait_for(lambda: engine.status()["state"] == "done")
    assert rig.named("send_failed") == []


def test_silence_still_fails_the_downloader_with_the_switch_on_and_a_brain_that_would_hold(rig, tmp_path):
    """The silence rule is today's (`FETCH_SLOW_S`, `_fail_silent`), and the execution does not touch it: the real brain, the setting on,
    a fetch whose count stands still has no rate -- UNDECIDED, nothing is held or dropped -- and `c` fails by name, alone, as before."""
    from server import db
    from server.brain import drawerhold
    conn = db.connect(tmp_path / "imagesrv.db")
    conn.execute("PRAGMA foreign_keys = OFF")
    hold = drawerhold.DrawerHold(conn, mode_fn=lambda: "shadow", hold=lambda: "on")
    hold.gap(SID, 0.0)
    engine = _one_machine_left_to_fetch(rig)
    engine.drawer_hold = hold.evaluate
    for _ in range(8):
        time.sleep(0.3)
        got = _fetching_count(rig, 6)
        assert "hold" not in got and "fail" not in got, got
    _c_failed_alone_and_the_wave_went_on(rig, engine, "לא הסתיימה ולא התקדמה")
    assert engine._wave.held_disks == set()
