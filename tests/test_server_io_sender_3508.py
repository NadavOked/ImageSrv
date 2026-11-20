"""#3508: `server_disk` is not "the library disk looked busy" -- it needs evidence that the sender WAITED for it.

`server_io.measure` said `server_disk` for any wave whose library disk was 20% busy with 10ms per request, over the whole wave. LabRoot,
15/11 (`lab/metal-1115-compare/sender-vs-disk.txt`): the new engine's waves had util 36% and await 15.6-15.9ms -- and `chunkcast send` slept 74% of the
time for its own pacing (`--write-kbps`) and waited for the read 16%; the disk was idle 64% of the stream. K8, the learning and the read ceiling
were all fed `server_disk`.

For a chunk wave (`need_sender=True`, as `server.app.on_chunk_event` calls it) the label needs the sender's own count (`rate_outcome`:
`sendstats`, `first_pass_s`). **#3553: the evidence is how long it WAITED for the library file** (`server_io.library_wait`), not how long it
slept for its pacing -- the engine sleeps 57-69% of the first pass on purpose, so the sleep decided nothing and 22 of 24 waves of 16/11 came out
"not decided". Waited more than `LIBRARY_WAIT_MAX`: `server_disk`. Waited less: `ok` (`looked` says what the thresholds saw). Not measured: "not
decided" -- `basis` empty, `undecided` says why -- never `server_disk` and never `ok`. udpcast waves and uploads do not ask: unchanged.
The real waves of the metal are in `test_library_wait_3553.py`.

**Negative control:** `server/learn/server_io.py` and `server/app.py` back at the base -- `measure` has no `need_sender`, and the app's
`server_io.end` does not pass the sender's evidence; every test but the two guards fails.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from server import db
from server.learn import server_io
from server.rounds import chunk_rate

from test_server_room import room_server   # noqa: F401 -- shared fixture

SECONDS = 400.0


def _snap(at, dev="nvme0n1p2", r=0, w=0, io=0, rios=0, wios=0, ios_ms=0):
    return {"at": at, "device": dev, "read_sectors": r, "write_sectors": w, "io_ms": io, "read_ios": rios, "write_ios": wios,
            "ios_ms": ios_ms, "psi_some_us": 0, "psi_full_us": 0}


def _busy():
    """400 s of the metal's disk: 36% busy, 15.6ms a request (the thresholds, 20% and 10ms, both passed)."""
    return (_snap(0), _snap(SECONDS, r=15_700_000_000 // 512, io=144_000, rios=10_000, ios_ms=156_000))


def _sender(waited: float | None, reader: str = "inline", seconds: float = SECONDS, asleep: float = 0.74) -> dict:
    """`rate_outcome` of a first pass of `seconds`: the sender waited `waited` of it for the library file (the inline reader's `read_ms`, or the
    reader thread's `readq_empty_ms` -- the thread's own `read_ms` is most of the pass, as on the metal) and slept `asleep` of it for `--write-kbps`."""
    ms = None if waited is None else int(seconds * 1000 * waited)
    stats = None if ms is None else ({"read_ms": ms, "readq_empty_ms": 0, "reader": "inline"} if reader == "inline"
                                     else {"read_ms": int(seconds * 1000 * 0.9), "readq_empty_ms": ms, "reader": "thread"})
    return {"first_pass_s": seconds, "paced_ms": {"wire": 0, "disk": int(seconds * 1000 * asleep)}, "sendstats": stats}


def test_the_thresholds_alone_still_say_server_disk_where_the_sender_is_not_asked():
    """Guard, both states: a udpcast wave and an upload do not ask for the sender's evidence."""
    assert server_io.measure(*_busy(), seconds=SECONDS)["basis"] == "server_disk"


def test_the_scenario_of_the_metal_a_busy_disk_and_a_sender_asleep_for_its_pacing_that_did_not_wait_for_it_is_ok():
    """#3553: 16/11, the engine's default -- util 36%, 15.6ms a request, the sender asleep 74% for its own pacing and waiting 21% for the file."""
    got = server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(0.21), need_sender=True)
    assert got["basis"] == "ok" and got["looked"] == "server_disk" and "undecided" not in got
    assert got["library_wait"] == 0.21
    assert got["util"] == 0.36 and got["await_ms"] == 15.6                # what was measured is kept


def test_a_sender_that_waited_for_the_file_is_the_evidence_and_the_label_stands():
    got = server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(0.40, asleep=0.0), need_sender=True)
    assert got["basis"] == "server_disk" and "undecided" not in got and got["library_wait"] == 0.4
    asleep = server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(0.40, asleep=0.74), need_sender=True)
    assert asleep["basis"] == "server_disk"                                 # asleep or not: the sleep decides nothing about the disk


def test_the_share_is_the_one_of_the_library_wait_and_a_sender_exactly_at_it_did_not_wait():
    assert server_io.LIBRARY_WAIT_MAX == 0.25
    for waited, basis in ((0.25, "ok"), (0.26, "server_disk")):
        assert server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(waited), need_sender=True)["basis"] == basis


def test_the_sleep_share_of_k8_is_still_its_own_and_unchanged():
    """`SENDER_ASLEEP_MAX` is K8's (`chunk_rate.server_release`, `netcap._pushed`): #3553 moved the disk label off it, not the constant."""
    assert server_io.SENDER_ASLEEP_MAX == (100 - chunk_rate.CUT_PERCENT) / 100 == 0.25
    assert server_io.sender_asleep(_sender(0.0, asleep=0.25)) is None
    assert server_io.sender_asleep(_sender(0.0, asleep=0.26)) == "sender_paced"
    assert server_io.sender_asleep({"first_pass_s": SECONDS}) == "sender_not_measured"


def test_a_reader_thread_is_waited_for_by_its_empty_queue_not_by_its_own_reads():
    """The thread's `read_ms` runs beside the sending (metal `RT-1`: 110.6s of 321); the sender waits only when the queue is empty."""
    assert server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(0.02, reader="thread"), need_sender=True)["basis"] == "ok"
    assert server_io.measure(*_busy(), seconds=SECONDS, sender=_sender(0.40, reader="thread"), need_sender=True)["basis"] == "server_disk"


@pytest.mark.parametrize("sender", [
    None, {}, _sender(None), {"first_pass_s": SECONDS}, {"sendstats": {"read_ms": 0, "readq_empty_ms": 0, "reader": "inline"}},
    {"first_pass_s": 0, "sendstats": {"read_ms": 0, "readq_empty_ms": 0, "reader": "inline"}},
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": 0, "readq_empty_ms": 0, "reader": None}},          # the reader is not known
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": 0, "readq_empty_ms": 0}},
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": 0, "readq_empty_ms": None, "reader": "thread"}},   # a thread whose queue wait was not read
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": None, "readq_empty_ms": 0, "reader": "inline"}},
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": -1, "readq_empty_ms": 0, "reader": "inline"}},
    {"first_pass_s": SECONDS, "sendstats": {"read_ms": True, "readq_empty_ms": 0, "reader": "inline"}},
    {"first_pass_s": SECONDS, "paced_ms": {"wire": 0, "disk": 0}},                       # slept for nothing, and no `sendstats`: not measured
])
def test_a_sender_whose_wait_was_not_read_is_not_evidence(sender):
    """"Not measured" is not "did not wait": neither `server_disk` nor `ok`."""
    got = server_io.measure(*_busy(), seconds=SECONDS, sender=sender, need_sender=True)
    assert got["basis"] is None and got["undecided"] == "sender_not_measured" and got["looked"] == "server_disk"
    assert got["library_wait"] is None


def test_a_quiet_disk_is_ok_whatever_the_sender_says_and_an_unread_disk_is_still_unread():
    calm = server_io.measure(_snap(0), _snap(SECONDS, io=4_000, rios=1000, ios_ms=1_000), seconds=SECONDS, sender=None, need_sender=True)
    assert calm["basis"] == "ok" and "undecided" not in calm
    unread = server_io.measure(_snap(0, io=None), _snap(SECONDS, io=None, rios=1000, ios_ms=1_000), sender=None, need_sender=True)
    assert unread["basis"] is None and "undecided" not in unread          # the disk was not read: "not measured", as before


# --- a wave, from `end` to the learning and the health line -----------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _end(conn, tmp_path, monkeypatch, sid, sender, need_sender=True):
    start, end = _busy()
    monkeypatch.setattr(server_io, "snapshot", lambda root, proc=server_io.PROC: end)
    conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json) VALUES ('wave', ?, 'x', ?)", (sid, json.dumps(start)))
    conn.commit()
    return server_io.end(conn, "wave", sid, tmp_path, seconds=SECONDS, sender=sender, need_sender=need_sender)


def test_an_undecided_wave_is_not_bound_is_journaled_as_such_and_the_health_line_says_it(conn, tmp_path, monkeypatch):
    got = _end(conn, tmp_path, monkeypatch, "ses_a", _sender(None))
    assert got["undecided"] == "sender_not_measured"
    assert server_io.bound(conn, "ses_a") is False
    row = conn.execute("SELECT basis, result_json FROM server_io WHERE ref = 'ses_a'").fetchone()
    assert row["basis"] is None and json.loads(row["result_json"])["undecided"] == "sender_not_measured"
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'server_disk_slow'").fetchone() is None
    [line] = conn.execute("SELECT detail, round_id FROM journal WHERE event = 'server_disk_undecided'").fetchall()
    assert line["detail"].startswith("wave ses_a read=") and line["detail"].endswith("why=sender_not_measured") and line["round_id"] == "ses_a"
    assert "libwait=לא נמדד%" in line["detail"]
    [check] = server_io.health_checks(conn)
    assert check["state"] == "off" and "לא נקבע" in check["detail"] and "חיכה לקובץ הספרייה" in check["detail"]


def test_a_busy_wave_the_sender_did_not_wait_for_is_ok_not_bound_and_the_health_line_says_why(conn, tmp_path, monkeypatch):
    """#3553: the disk looked busy, the sender slept for its pacing and waited 21% -- decided (`ok`), not bound, nothing journaled as slow."""
    got = _end(conn, tmp_path, monkeypatch, "ses_ok", _sender(0.21))
    assert got["basis"] == "ok" and server_io.bound(conn, "ses_ok") is False
    assert conn.execute("SELECT basis FROM server_io WHERE ref = 'ses_ok'").fetchone()["basis"] == "ok"
    assert conn.execute("SELECT 1 FROM journal WHERE event IN ('server_disk_slow', 'server_disk_undecided')").fetchone() is None
    [check] = server_io.health_checks(conn)
    assert check["state"] == "ok" and "השולח חיכה לקובץ רק 21%" in check["detail"]


def test_a_wave_the_sender_waited_for_is_bound_and_journaled_slow(conn, tmp_path, monkeypatch):
    got = _end(conn, tmp_path, monkeypatch, "ses_b", _sender(0.40))
    assert got["basis"] == "server_disk" and server_io.bound(conn, "ses_b") is True
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'server_disk_undecided'").fetchone() is None
    assert conn.execute("SELECT 1 FROM journal WHERE event = 'server_disk_slow'").fetchone() is not None
    assert server_io.health_checks(conn)[0]["state"] == "warn"


def test_a_udpcast_wave_does_not_ask_the_sender_guard(conn, tmp_path, monkeypatch):
    """Guard, both states: without `need_sender` the label is the thresholds', as before."""
    start, end = _busy()
    monkeypatch.setattr(server_io, "snapshot", lambda root, proc=server_io.PROC: end)
    conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json) VALUES ('wave', 'ses_u', 'x', ?)", (json.dumps(start),))
    conn.commit()
    got = server_io.end(conn, "wave", "ses_u", tmp_path, seconds=SECONDS)
    assert got["basis"] == "server_disk" and "library_wait" not in got


def test_the_server_gives_the_chunk_wave_end_the_evidence_of_its_sender(room_server, monkeypatch):
    """(neg) `server.app.on_chunk_event`: a wave the engine does not know has no `sendstats` -- not decided, not `server_disk`."""
    ctx = room_server["ctx"]
    start, end = _busy()
    monkeypatch.setattr(server_io, "snapshot", lambda root, proc=server_io.PROC: end)
    ctx.conn.execute("INSERT OR REPLACE INTO server_io (kind, ref, started_at, start_json) VALUES ('wave', 'ses_app', 'x', ?)",
                     (json.dumps(start),))
    ctx.conn.commit()
    ctx.chunk.on_event("send_done", "x", "ses_app")
    assert server_io.bound(ctx.conn, "ses_app") is False
    assert json.loads(ctx.conn.execute("SELECT result_json FROM server_io WHERE ref = 'ses_app'").fetchone()["result_json"]
                      )["undecided"] == "sender_not_measured"
