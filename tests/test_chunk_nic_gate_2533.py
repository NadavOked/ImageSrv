"""#2533 מול #705 ו-#2668: מנוע החתיכות מציית לאותם כרטיסים כמו udp-sender, ונעצר בכל מקום שהוא נעצר.

- "שידור כבוי בכרטיס" (#705): גל chunk על כרטיס שבו המפעיל כיבה את השידור מסורב בשמו, באותו נוסח
  של udpcast (`NicServices.multicast_refused`), ושום `chunkcast send` אינו מופעל.
- כרטיס לכל גל (#2668): הכרטיס נקבע מהמקלטים של הגל, כמו ב-udpcast, ולא מזמן הבנייה של המנוע.
- עצירה: UPS (`stop_all_work`), "עצור סבב" בחדר וסגירת סבב בקונסולה עוצרים גם את `chunkcast send`,
  ו"נסגר" רק עם ראיה שהוא מת (#439).

#3173: udp-sender נמחק, ומנוע החתיכות הוא היחיד. הנוסח של הסירוב הוא של `NicServices.multicast_refused`.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.agent.identity import LeaseFile
from server.app import create_runtime
from server.rounds import chunksend, room
from server.rounds.sessions import SessionError
from server.system import ups

import chunkcast_rig as C
from conftest import MANIFEST_256, write_image
from test_chunksend_2533 import Lib, Proc, Runner, _room_wave, wait_for
from test_multicast_nics_2668 import enable
from test_nic_services import NICS, rig  # noqa: F401 -- shared fixture
from test_server_room import CLONER1, room_server  # noqa: F401 -- shared fixture

SID = "S-1"


def chunk_wave(svc, root, addresses=(), override=None, select=True, interface="ens33"):
    """גל של מחשב אחד ("a") במנוע החתיכות, עם השער והבחירה מחוברים כמו ב-`server.app`."""
    runner, events = Runner(), []
    engine = chunksend.ChunkEngine(
        Lib(root), live_members=lambda sid: ["a"], lost_wait=lambda: 1, write_floor=lambda: 50,
        runner=runner, interface=interface, max_wait=1, start_timeout=1,
        wave_bitrate=lambda session: "700m", ledger=C.MemLedger(),
        on_event=lambda event, detail, sid: events.append((event, detail)))
    engine.gate = svc.multicast_refused
    if select:
        engine.select_interface = lambda session: svc.multicast_interface(
            lambda: list(addresses), override, default="ens33")
    engine.note_agent("a", ["chunk"])
    assert engine.start({"id": SID, "image_id": "img_1"}) is None
    return engine, runner, events


def sent_on(engine, runner):
    """המחשב מתייצב; הכרטיס שבפקודה של המעבר הראשון."""
    assert engine.report(SID, "a", {"part": 3, "pass": 1, "state": "listening",
                                    "targets": [{"serial": "A1", "durable": [{"part": 3, "ranges": []}]}]}
                         )["action"] == "receive"
    assert wait_for(lambda: runner.commands), engine.status()
    cmd = runner.commands[0]
    return cmd[cmd.index("--iface") + 1] if "--iface" in cmd else None


# --- השער: שידור כבוי בכרטיס (#705) ---------------------------------------------------------------


@pytest.mark.parametrize("select", [False, True], ids=["fixed-nic", "override"])
def test_a_chunk_wave_on_a_nic_with_multicast_off_is_refused_by_name_and_nothing_is_sent(rig, tmp_path, select):
    """הכרטיס של הגל (קבוע, או `--multicast-interface` מפורש) כבוי לשידור: הגל נכשל בנוסח של `NicServices.multicast_refused`."""
    svc = rig["svc"]
    enable(svc, "ens34")                                   # ens33 off; the operator saved, so the gate is live
    engine, runner, events = chunk_wave(svc, tmp_path, override="ens33", select=select)
    assert wait_for(lambda: engine.status()["state"] == "failed"), engine.status()
    said = svc.multicast_refused("ens33")
    assert said and "שידור כבוי בכרטיס ens33" in said
    assert engine.status()["error"] == said and ("send_failed", said) in events
    assert runner.commands == []
    engine.close()


# --- הכרטיס של כל גל (#2668) -------------------------------------------------------------------------


@pytest.mark.parametrize("enabled,addresses,override,expected", [
    (("ens34",), (), None, "ens34"),
    (("ens33", "ens34"), ("10.44.0.100",), None, "ens34"),
    (("ens33", "ens34"), ("10.10.10.100",), None, "ens33"),
    (("ens33", "ens34"), ("10.44.0.100",), "ens33", "ens33"),
])
def test_a_chunk_wave_goes_out_on_the_nic_chosen_for_it(rig, tmp_path, enabled, addresses, override, expected):
    enable(rig["svc"], *enabled)
    engine, runner, _events = chunk_wave(rig["svc"], tmp_path, addresses, override)
    assert sent_on(engine, runner) == expected
    engine.stop(SID)
    engine.close()


def test_the_nic_is_chosen_again_for_every_wave_not_once_at_startup(rig, tmp_path):
    svc = rig["svc"]
    for name in ("ens34", "ens33"):
        enable(svc, name)
        engine, runner, _events = chunk_wave(svc, tmp_path, interface="ens35")
        assert sent_on(engine, runner) == name
        engine.stop(SID)
        engine.close()


@pytest.mark.parametrize("enabled,addresses,reason", [
    ((), (), "no NIC has multicast enabled"),
    (("ens33", "ens34"), ("10.44.0.100", "10.10.10.100"), "span multiple NICs"),
])
def test_a_chunk_wave_whose_nic_cannot_be_settled_fails_by_name_before_a_byte(rig, tmp_path,
                                                                               enabled, addresses, reason):
    enable(rig["svc"], *enabled)
    engine, runner, events = chunk_wave(rig["svc"], tmp_path, addresses)
    assert wait_for(lambda: engine.status()["state"] == "failed"), engine.status()
    assert reason in engine.status()["error"] and runner.commands == []
    assert [e for e, _d in events] == ["send_failed"]
    engine.close()


@pytest.mark.parametrize("enabled,override,state,error", [
    (("ens34",), "ens33", "failed", "שידור כבוי בכרטיס ens33"),
    (("ens34",), None, "failed", "אין אינדקס"),            # the NIC passed; the image has no index here
])
def test_the_runtime_wires_the_gate_and_the_selection_into_the_chunk_engine(tmp_path, enabled, override,
                                                                            state, error):
    images = tmp_path / "images"
    write_image(images, MANIFEST_256, index=False)         # the second case: the NIC passes, and the image has no index
    leases = tmp_path / "runtime.leases"
    mac = "00:11:22:33:44:55"
    leases.write_text(f"0 {mac} 10.44.0.100 receiver *\n", encoding="utf-8")
    runner = Runner()
    runtime = create_runtime(tmp_path / "data", images, "http://10.10.10.8:7712",
                             interface="ens33", multicast_interface_override=override,
                             sender_runner=runner, nic_hooks={"interfaces": lambda: NICS},
                             identity_hooks={"leases": LeaseFile(leases)},
                             health_hooks={"interfaces": lambda: NICS})
    enable(runtime.ctx.nic_services, *enabled)
    conn = runtime.conn
    conn.execute("INSERT INTO groups (id, label, role) VALUES ('g2533', 'test', 'cloner')")
    conn.commit()
    sid = runtime.ctx.store.open("grp_CLONERS", "img_7f3a91", "ROOM", 1, "test")   # the only group a wave opens for (#3173)
    conn.execute("INSERT INTO session_members (session_id, mac, updated_at) VALUES (?, ?, '')", (sid, mac))
    conn.commit()
    chunk = runtime.ctx.chunk
    chunk.wave_bitrate = lambda session: "700m"
    assert chunk.start({"id": sid, "image_id": "img_7f3a91"}) is None
    assert wait_for(lambda: chunk.status()["state"] == state), chunk.status()
    assert error in chunk.status()["error"] and runner.commands == []
    if override is None:
        assert chunk.interface == "ens34"                  # chosen from the receiver's lease, not the startup NIC
    chunk.close()


# --- עצירה: כל מקום שעצר את udp-sender עוצר את מנוע החתיכות ----------------------------------------------


class Stubborn(Proc):
    """chunkcast send שאינו מת מ-TERM ולא מ-KILL."""

    def terminate(self):
        pass

    kill = terminate

    def release(self):
        self._done.set()


class StubbornRunner(Runner):
    def __call__(self, cmd):
        self.processes.append(Stubborn(block=True))
        self.commands.append(cmd)
        return self.processes[-1]


def _sending(room_server, runner):
    """גל חדר במנוע החתיכות, כש-`chunkcast send` של המעבר הראשון רץ (חסום) ברגע זה."""
    ctx = room_server["ctx"]
    ctx.chunk.runner = runner
    wave, _hello = _room_wave(room_server)
    first = next(p["index"] for p in MANIFEST_256["partitions"] if p.get("file"))
    answer = room_server["anon"].post("/api/v1/agent/chunk-pass", json={
        "mac": CLONER1, "session_id": wave, "part": first, "pass": 1, "state": "listening",
        "targets": [{"serial": "S1", "write_mbps": 150}, {"serial": "S2", "write_mbps": 150}]})
    assert answer.status_code == 200 and answer.json()["action"] == "receive", answer.text
    assert wait_for(lambda: runner.processes), ctx.chunk.status()
    return ctx, wave, runner.processes[0]


def _stopped(process) -> bool:
    return wait_for(lambda: process.poll() is not None, 2)


def test_ups_stopping_all_work_stops_chunkcast_too(room_server):
    ctx, wave, process = _sending(room_server, Runner(block=True))
    try:
        assert wave in ups.stop_all_work(ctx, "סוללת ה-UPS נמוכה")
        assert _stopped(process), "chunkcast send still on the air after the UPS stopped all work"
    finally:
        ctx.chunk.stop()


def test_stopping_a_room_round_whose_wave_already_failed_stops_chunkcast(room_server):
    """#439 במנוע החתיכות: גל שכבר אינו open/running אינו קורא ל-`on_closed`, והעצירה המפורשת היא ההגנה."""
    ctx, wave, process = _sending(room_server, Runner(block=True))
    try:
        ctx.conn.execute("UPDATE sessions SET state = 'failed' WHERE id = ?", (wave,))
        ctx.conn.commit()
        room.close_round(ctx, "noc", MANIFEST_256["name"])
        assert _stopped(process), "the room round closed and chunkcast send is still on the air"
    finally:
        ctx.chunk.stop()


def test_closing_a_session_that_is_already_closed_stops_chunkcast(room_server):
    ctx, wave, process = _sending(room_server, Runner(block=True))
    try:
        ctx.conn.execute("UPDATE sessions SET state = 'closed' WHERE id = ?", (wave,))
        ctx.conn.commit()
        closed = room_server["deploy"].post(f"/api/console/sessions/{wave}/close",
                                            json={"confirm_name": MANIFEST_256["name"]})
        assert closed.status_code == 200, closed.text
        assert _stopped(process), "the session closed and chunkcast send is still on the air"
    finally:
        ctx.chunk.stop()


def test_a_room_round_is_not_closed_while_chunkcast_survives_the_stop(room_server):
    """"נסגר" רק עם ראיה (#439): `chunkcast send` שלא מת — הסגירה נכשלת בשמו, והסבב אינו `closed`."""
    runner = StubbornRunner()
    ctx, wave, process = _sending(room_server, runner)
    try:
        ctx.conn.execute("UPDATE sessions SET state = 'failed' WHERE id = ?", (wave,))
        ctx.conn.commit()
        with pytest.raises(SessionError, match="chunkcast send עדיין רץ"):
            room.close_round(ctx, "noc", MANIFEST_256["name"])
        state = ctx.conn.execute("SELECT state FROM room_rounds ORDER BY rowid DESC LIMIT 1").fetchone()["state"]
        assert state != "closed"
    finally:
        process.release()
        ctx.chunk.stop()
