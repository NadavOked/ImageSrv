"""#3164: למידת אובדן הרשת (netloss, #1846) גם בגל של מנוע החתיכות.

**הפער:** `netloss` נשען על `loss_blocks` של udp-sender; ל-chunkcast אין שידורים חוזרים, ו-`record_stream` אינו
רץ לגל חתיכות -- ולכן קפיצה באבדן (סוויץ'/כבל/IGMP) לא זוהתה בשום גל חתיכות.

**המדד** (ראו `server/learn/netloss.py`): `abandoned` -- חתיכות שהמקלט נטש באמצע (G7, `udp_drops`) -- סכום על
המחשבים שבגל, לכל GB שהם קיבלו (`delivered`). הבסיס: גלי חתיכות בלבד, על אותו כרטיס, באותו מספר מגירות ובאותו
קצב. גל שנרשם בו `chunk_net_gap` (#3141) אינו מלמד ואינו מואשם.

**מה אינו נבדק כאן:** שהמדד מגיב לאבדן אמיתי (netem על ה-VM, ברזל) -- `untested-on-metal`. המספרים כאן הם
גלים **מדומים** בשיעור ידוע, ומוכיחים רק שהחישוב, הבסיס והחיווט נכונים.
"""

from __future__ import annotations

import json
import time

import pytest

from server import db
from server.learn import netloss as learn_netloss, server_io
from server.rounds import drive_rates, room

MAC1 = "78:ac:c0:9b:11:c2"
MAC2 = "78:ac:c0:9b:11:c3"
GB = 10 ** 9
RECEIVED_GB = 40                  # כמה GB קיבל כל מחשב בכל גל מדומה
_seq = iter(range(10 ** 6))


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagesrv.db")


def _decision(sid, *, rate, drawers, interface, direct, machines):
    roster = [{"mac": mac, "serial": f"{sid}-{mac[-2:]}-{k}", "model": "M"}
              for mac in machines for k in range(drawers)]
    out = {"max_bitrate": rate, "roster": roster,
           "link": {"interface": interface, "speed_mbps": 1000, "why": None, "applied": True}}
    if direct:
        out["direct"] = True
    return out


def _wave(conn, per_gb: float | None, *, ended=True, gap=False, machines=(MAC1,), rate="400m",
          drawers=2, interface="eth1", direct=False, softnet=0, nic=0, rcvbuf=0, abandoned=None,
          drops=True) -> str:
    """גל חתיכות מדומה שנסגר: כל מחשב קיבל `RECEIVED_GB` ונטש `per_gb` חתיכות לכל GB.
    `wave_rates` נכתב ישירות (כמו ב-`test_learn_netloss_1846`); היומן -- כמו שהמנוע כותב אותו."""
    sid = f"ses_c{next(_seq):05d}"
    machines = tuple(machines)
    lost = round(per_gb * RECEIVED_GB) if abandoned is None and per_gb is not None else abandoned
    dropped = {mac: {"rcvbuf": rcvbuf, "inerrors": rcvbuf, "failed_drawers": 0,
                     **({} if lost is None else {"abandoned": lost}),
                     **({} if softnet is None else {"softnet": softnet}),
                     **({} if nic is None else {"nic": nic})}
               for mac in machines}
    delivered = {mac: {"drawers": drawers, "done": drawers, "disk_failed": 0, "stream_failed": 0,
                       "unknown_failed": 0, "written_bytes": drawers * RECEIVED_GB * GB}
                 for mac in machines}
    conn.execute("INSERT INTO wave_rates (session_id, decision, at, drops, delivered)"
                 " VALUES (?,?,?,?,?)",
                 (sid, json.dumps(_decision(sid, rate=rate, drawers=drawers, interface=interface,
                                            direct=direct, machines=machines)), db.now_iso(),
                  json.dumps(dropped) if drops else None, json.dumps(delivered)))
    conn.commit()
    db.journal(conn, "send_start", f"{sid} img partitions=1 engine=chunk", round_id=sid)
    if gap:
        db.journal(conn, "chunk_net_gap", f"{MAC1}: 12 חלקים לא הגיעו למחשב בשידור הראשון", round_id=sid)
    if ended:
        db.journal(conn, "send_done", f"{sid} {{}}", round_id=sid)
    return sid


def _base(conn, **kw):
    """שלושה גלים רגועים: 0.10 / 0.125 / 0.15 חתיכות לכל GB -- חציון 0.125."""
    for per_gb in (0.1, 0.125, 0.15):
        _wave(conn, per_gb, **kw)


def _assess(conn, sid) -> dict:
    got = learn_netloss.assess(conn, sid)
    assert got is not None, "the chunk wave was not a measurement at all"
    return got


def _jumps(conn) -> list[str]:
    return [r["detail"] for r in conn.execute("SELECT detail FROM journal WHERE event = 'net_loss_jump'")]


# --- המדד: מה נמדד, ואילו גלים הם מדידה ---------------------------------------------------------------

def test_a_chunk_wave_with_a_known_loss_yields_a_loss_record(conn):
    sid = _wave(conn, 0.5, machines=(MAC1, MAC2))          # 0.5 חתיכות/GB לכל מחשב, שני מחשבים
    got = _assess(conn, sid)
    assert got["kind"] == "chunk" and got["abandoned"] == 2 * 20          # 20 חתיכות לכל מחשב
    assert got["loss"] == pytest.approx(0.5)                              # סכום החתיכות / סכום ה-GB
    assert (got["interface"], got["drawers"], got["rate"]) == ("eth1", 4, "400m")


def test_a_clean_chunk_wave_has_a_zero_record_and_never_a_jump(conn):
    for _ in range(3):
        _wave(conn, 0.0)
    sid = _wave(conn, 0.0)
    got = _assess(conn, sid)
    assert got["loss"] == 0 and got["baseline"] == 0 and got["state"] == "ok"
    assert _jumps(conn) == []


def test_the_gb_a_machine_received_is_what_one_drawer_wrote_not_the_sum_of_drawers(conn):
    """4 מגירות באותו מחשב כתבו אותו זרק -- כל אחת. אם הן נספרו כולן, האבדן לכל GB היה קטן פי 4."""
    sid = _wave(conn, 0.5, drawers=4)
    assert _assess(conn, sid)["loss"] == pytest.approx(0.5)


# --- הקפיצה מול הבסיס -------------------------------------------------------------------------------

def test_a_jump_against_a_baseline_of_three_waves_is_journaled(conn):
    _base(conn)
    sid = _wave(conn, 2.0)                                  # פי 16 מהחציון, ומעל הרצפה
    got = _assess(conn, sid)
    assert got["state"] == "jump" and got["waves"] == 3
    assert got["baseline"] == pytest.approx(0.125)
    assert got["loss"] > learn_netloss.CHUNK_FLOOR_PER_GB


def test_within_the_baseline_is_ok_and_silent(conn):
    _base(conn)
    sid = _wave(conn, 0.3)                                  # פי 2.4 -- לא קפיצה
    assert _assess(conn, sid)["state"] == "ok"


def test_a_zero_baseline_does_not_turn_one_chunk_into_a_jump(conn):
    """כמו הרצפה של udpcast: מתחת לרצפה אין קפיצה, גם מול בסיס אפס; מעליה יש."""
    for _ in range(3):
        _wave(conn, 0.0)
    floor = learn_netloss.CHUNK_FLOOR_PER_GB
    assert _assess(conn, _wave(conn, floor / 2))["state"] == "ok"
    assert _assess(conn, _wave(conn, floor * 2))["state"] == "jump"


def test_fewer_than_three_waves_is_unchecked_not_fine(conn):
    _wave(conn, 0.1)
    _wave(conn, 0.12)
    got = _assess(conn, _wave(conn, 5.0))
    assert got["state"] == "unchecked" and got["waves"] == 2 and got["baseline"] is None


def test_another_card_rate_or_drawer_count_is_not_a_baseline(conn):
    _base(conn)
    assert _assess(conn, _wave(conn, 2.0, rate="500m"))["state"] == "unchecked"
    assert _assess(conn, _wave(conn, 2.0, drawers=4))["state"] == "unchecked"
    assert _assess(conn, _wave(conn, 2.0, interface="eth2"))["state"] == "unchecked"


def test_udpcast_waves_are_not_the_baseline_of_a_chunk_wave_and_the_other_way_round(conn):
    """#3164 (מה שאסור): לא משווים אבדן של chunkcast מול בסיס שנלמד מ-udpcast. שני המנועים אינם ברי השוואה."""
    sent = 19_500_000_000
    blocks = -(-sent // drive_rates.UDPCAST_BLOCK)
    udp = []
    for loss in (0.004, 0.005, 0.006):
        sid = f"ses_u{next(_seq):05d}"
        conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                     (sid, json.dumps(_decision(sid, rate="400m", drawers=2, interface="eth1",
                                                direct=False, machines=(MAC1,))), db.now_iso()))
        conn.commit()
        drive_rates.record_stream(conn, sid, {"sent_bytes": sent, "loss_blocks": round(blocks * loss),
                                              "wall_s": 400, "ended": "send_done",
                                              "dropped_clients": 0, "timeouts": 10})
        udp.append(sid)
    chunk = _wave(conn, 2.0)
    assert _assess(conn, chunk)["state"] == "unchecked" and _assess(conn, chunk)["waves"] == 0
    for _ in range(3):
        _wave(conn, 0.1)
    later = _wave(conn, 0.1)
    assert _assess(conn, later)["waves"] == 4                    # ארבעת גלי החתיכות הקודמים, בלי שלושת גלי udpcast
    assert learn_netloss.assess(conn, udp[-1])["kind"] == "udpcast"
    assert learn_netloss.assess(conn, udp[-1])["waves"] == 2     # ושני גלי udpcast הקודמים, בלי חתיכות


# --- מה שאינו מדידה ---------------------------------------------------------------------------------

def test_a_wave_with_an_unread_counter_is_not_a_measurement(conn):
    """מונה שלא נקרא אינו אפס (עיקרון 5): מחשב אחד בלי `abandoned`, או בלי דיווח, מפיל את הגל כולו."""
    _base(conn)
    assert learn_netloss.assess(conn, _wave(conn, None, machines=(MAC1, MAC2), abandoned=None)) is None
    sid = _wave(conn, 0.1, machines=(MAC1, MAC2))
    drops = json.loads(conn.execute("SELECT drops FROM wave_rates WHERE session_id = ?", (sid,)).fetchone()[0])
    drops[MAC2]["abandoned"] = None                              # דיווח "לא נקרא"
    conn.execute("UPDATE wave_rates SET drops = ? WHERE session_id = ?", (json.dumps(drops), sid))
    assert learn_netloss.assess(conn, sid) is None
    assert learn_netloss.assess(conn, _wave(conn, 0.1, drops=False)) is None      # `drops` ריק (הגל לא נסגר בחדר)


def test_a_wave_that_did_not_end_in_send_done_is_not_a_measurement(conn):
    _base(conn)
    assert learn_netloss.assess(conn, _wave(conn, 5.0, ended=False)) is None
    nxt = _wave(conn, 0.1)
    assert _assess(conn, nxt)["waves"] == 3                      # והוא לא נכנס לבסיס


def test_a_machine_with_no_finished_drawer_makes_the_wave_unread(conn):
    sid = _wave(conn, 0.5)
    conn.execute("UPDATE wave_rates SET delivered = ? WHERE session_id = ?",
                 (json.dumps({MAC1: {"drawers": 2, "done": 0, "written_bytes": 0}}), sid))
    assert learn_netloss.assess(conn, sid) is None


def test_a_direct_wave_is_not_a_measurement(conn):
    assert learn_netloss.assess(conn, _wave(conn, 0.5, direct=True)) is None


# --- ניתוק (#3141) ובעיית הדיסק של השרת (#1884): לא מלמדים, ולא מואשמת הרשת --------------------------------

def test_a_wave_with_a_net_gap_does_not_blame_the_network_and_is_not_a_baseline(conn):
    _base(conn)
    gapped = _wave(conn, 9.0, gap=True)                          # פי 70 -- אבל זה ניתוק מכוון
    got = _assess(conn, gapped)
    assert got["state"] == "net_gap" and got["baseline"] is None
    assert learn_netloss.evaluate_chunk(conn, gapped)["state"] == "net_gap"
    assert _jumps(conn) == []
    nxt = _wave(conn, 0.1)
    got = _assess(conn, nxt)
    assert got["state"] == "ok" and got["waves"] == 3            # שלושת הרגועים, בלי הגל עם הניתוק
    assert got["baseline"] == pytest.approx(0.125)               # והחציון לא זז


def test_a_server_bound_chunk_wave_is_not_blamed_on_the_network_and_is_not_a_baseline(conn):
    _base(conn)
    slow = _wave(conn, 3.0)
    conn.execute("INSERT INTO server_io (kind, ref, started_at, start_json, ended_at, result_json, basis)"
                 " VALUES ('wave', ?, ?, '{}', ?, '{}', 'server_disk')", (slow, db.now_iso(), db.now_iso()))
    conn.commit()
    assert server_io.bound(conn, slow) is True
    assert _assess(conn, slow)["state"] == "server_disk"
    assert _assess(conn, _wave(conn, 0.1))["waves"] == 3


# --- מה שהמקלטים עצמם זרקו (`local`): מוצג ליד האבדן, אינו מכריע ---------------------------------------

def test_the_receivers_own_drops_are_the_sum_of_rcvbuf_softnet_and_nic_per_gb(conn):
    sid = _wave(conn, 0.5, machines=(MAC1, MAC2), rcvbuf=10, softnet=20, nic=30)
    got = _assess(conn, sid)
    assert got["local"] == pytest.approx(2 * 60 / (2 * RECEIVED_GB))      # `inerrors` אינו נספר: הוא כולל את `rcvbuf`


def test_one_unread_local_counter_makes_local_unread_but_still_measures_the_loss(conn):
    """עיקרון 5: סכום חלקי היה נקרא "המקלט נקי". והאבדן עצמו נשאר מדידה -- `local` אינו מכריע."""
    sid = _wave(conn, 0.5, softnet=None)                          # `softnet` חסר בדיווח
    got = _assess(conn, sid)
    assert got["local"] is None and got["loss"] == pytest.approx(0.5)
    sid = _wave(conn, 0.5, machines=(MAC1, MAC2), nic=None)
    assert _assess(conn, sid)["local"] is None


def test_a_jump_is_a_jump_whatever_the_receivers_own_counters_say(conn):
    _base(conn, softnet=0, nic=0)
    sid = _wave(conn, 2.0, softnet=400, nic=400)
    got = _assess(conn, sid)
    assert got["state"] == "jump" and got["local"] > 0 and got["local_baseline"] == 0


# --- היומן והמסך ------------------------------------------------------------------------------------

def test_the_room_closing_a_chunk_wave_journals_a_jump_once(conn, monkeypatch):
    """החיווט האמיתי: `room._record_wave_stats` -- `drive_rates.record_wave` כותב את `drops` ו-`delivered` מהחברים,
    ואחריו האבדן מוערך. גל שנסגר פעמיים -- שורה אחת."""
    for module, name in ((room.learn_ports, "record_wave"), (room.learn_models, "record_wave"),
                         (room.learn_tuning, "record_wave")):
        monkeypatch.setattr(module, name, lambda *a, **k: 0)

    def close(per_gb):
        sid = f"ses_r{next(_seq):05d}"
        decision = _decision(sid, rate="400m", drawers=2, interface="eth1", direct=False, machines=(MAC1,))
        conn.execute("INSERT INTO wave_rates (session_id, decision, at) VALUES (?,?,?)",
                     (sid, json.dumps(decision), db.now_iso()))
        conn.commit()
        db.journal(conn, "send_done", f"{sid} {{}}", round_id=sid)
        members = [{"mac": MAC1, "udp_drops_json": json.dumps(
            {"rcvbuf": 0, "inerrors": 0, "abandoned": round(per_gb * RECEIVED_GB)}),
            "targets_json": json.dumps([
                {"serial": d["serial"], "dev": f"sd{k}", "state": "done", "bytes_written": RECEIVED_GB * GB,
                 "first_moved_at": "2026-11-14T10:00:00", "moved_at": "2026-11-14T10:10:00"}
                for k, d in enumerate(decision["roster"])])}]
        room._record_wave_stats(conn, sid, members)
        return sid, members

    for per_gb in (0.1, 0.125, 0.15):
        close(per_gb)
    assert _jumps(conn) == []
    sid, members = close(2.0)
    [line] = _jumps(conn)
    assert line.startswith(f"{sid} if=eth1 drawers=2 rate=400m loss=2.00/GB baseline=0.12/GB (3 waves)")
    room._record_wave_stats(conn, sid, members)                 # נסגר שוב
    assert len(_jumps(conn)) == 1


def _server_io_reads(monkeypatch, slow: bool):
    """`server_io.begin`/`end` -- the real ones, on a library disk the test sets: busy, and `slow` = a request takes
    20ms (against 1ms). The verdict comes from the library disk's own counters (#3365), never from the pressure
    counters of the host (`/proc/pressure/io` is system-wide: on a loaded Linux runner it said `server_disk` and the wave
    was, rightly, not blamed on the network -- the test then failed by the load of the host it ran on, 1114d p1)."""
    seen = []

    def snapshot(root, proc=None):
        seen.append(1)
        end = len(seen) > 1
        return {"at": time.time(), "device": "sdb", "read_sectors": 0, "write_sectors": 0,
                "io_ms": 100_000 if end else 0, "read_ios": 1000 if end else 0, "write_ios": 0,
                "ios_ms": (20_000 if slow else 1_000) if end else 0,
                "psi_some_us": 10 ** 9 if end else 0, "psi_full_us": 10 ** 9 if end else 0}   # pressure: always high
    monkeypatch.setattr(server_io, "snapshot", snapshot)


def test_the_chunk_engine_ending_a_wave_evaluates_it_through_the_apps_own_event_hook(room_server, monkeypatch):
    """`server.app.on_chunk_event`: `send_done` מעריך את האבדן. ה-`drops` כבר נרשם (החדר סגר קודם) -- והאירוע
    האחרון מבין השניים הוא זה שרואה את שניהם. הדיסק של הספרייה לא איטי כאן, בלי קשר למארח (גם כש-PSI גבוה)."""
    _server_io_reads(monkeypatch, False)
    ctx = room_server["ctx"]
    _base(ctx.conn)
    sid = _wave(ctx.conn, 2.0, ended=False)                      # `send_done` עוד לא נרשם
    server_io.begin(ctx.conn, "wave", sid, ctx.library.root)
    assert _jumps(ctx.conn) == []
    ctx.chunk.on_event("send_done", f"{sid} {{}}", sid)
    [line] = _jumps(ctx.conn)
    assert line.startswith(f"{sid} if=eth1 drawers=2 rate=400m loss=2.00/GB")


def test_a_chunk_wave_the_servers_own_disk_held_back_is_not_blamed_on_the_network_by_the_hook(room_server, monkeypatch):
    """The other side of the test above: through the same hook, a wave whose span the server spent waiting on its
    own library disk (`server_disk`, #1884) is not evaluated -- no `net_loss_jump`, however high the loss."""
    _server_io_reads(monkeypatch, True)                          # a library disk that is busy and slow
    ctx = room_server["ctx"]
    # #3508/#3553: in a chunk wave `server_disk` needs the sender's own evidence (`sendstats`) that it WAITED for the library file for more
    # than `LIBRARY_WAIT_MAX` of the first pass: this inline sender waited 4s of 5s, so the library disk is what held the stream back.
    monkeypatch.setattr(ctx.chunk, "rate_outcome", lambda session_id: {
        "first_pass_s": 5.0, "paced_ms": {"wire": 0, "disk": 0}, "sendstats": {"read_ms": 4000, "readq_empty_ms": 0, "reader": "inline"}})
    _base(ctx.conn)
    sid = _wave(ctx.conn, 2.0, ended=False)
    server_io.begin(ctx.conn, "wave", sid, ctx.library.root)
    ctx.chunk.on_event("send_done", f"{sid} {{}}", sid)
    assert server_io.bound(ctx.conn, sid) is True
    assert _assess(ctx.conn, sid)["state"] == "server_disk"
    assert _jumps(ctx.conn) == []


def test_a_chunk_wave_that_failed_or_was_stopped_is_not_evaluated(room_server, monkeypatch):
    _server_io_reads(monkeypatch, False)
    ctx = room_server["ctx"]
    _base(ctx.conn)
    for event in ("send_failed", "send_stopped"):
        sid = _wave(ctx.conn, 5.0, ended=False)
        server_io.begin(ctx.conn, "wave", sid, ctx.library.root)
        ctx.chunk.on_event(event, sid, sid)
    assert _jumps(ctx.conn) == []


def test_the_health_row_of_a_chunk_wave(conn):
    assert learn_netloss.health_checks(conn) == []
    _base(conn)
    _wave(conn, 2.0, softnet=300, nic=300)
    (row,) = learn_netloss.health_checks(conn)
    assert row["id"] == "net_loss:eth1" and row["state"] == "warn"
    assert "מנוע חתיכות" in row["label"]
    assert row["detail"].startswith("האבדן ברשת ההפצה קפץ — סוויץ'/כבל/IGMP: 2.00/GB מול בסיס 0.12/GB")
    assert "המקלטים עצמם זרקו" in row["detail"]
    _wave(conn, 0.1)
    (row,) = learn_netloss.health_checks(conn)
    assert row["state"] == "ok"
    _wave(conn, 9.0, gap=True)
    (row,) = learn_netloss.health_checks(conn)
    assert row["state"] == "off" and "חלקים לא הגיעו למחשב ברשת" in row["detail"]


def test_the_health_row_says_unread_when_the_receivers_own_drops_were_not_read(conn):
    _base(conn)
    _wave(conn, 2.0, softnet=None)
    (row,) = learn_netloss.health_checks(conn)
    assert row["state"] == "warn" and "לא נקרא" in row["detail"]


# --- ה-udpcast לא זז ---------------------------------------------------------------------------------

def test_the_udpcast_end_of_a_wave_ignores_chunk_waves_and_the_chunk_end_ignores_udpcast(conn):
    _base(conn)
    sid = _wave(conn, 2.0)
    assert learn_netloss.evaluate(conn, sid) is None             # `evaluate` הוא של השולח, ולא של החדר
    assert _jumps(conn) == []
    assert learn_netloss.evaluate_chunk(conn, sid)["state"] == "jump"
    assert len(_jumps(conn)) == 1
    assert learn_netloss.evaluate_chunk(conn, sid)["state"] == "jump"      # שנית -- בלי שורה נוספת
    assert len(_jumps(conn)) == 1


def test_the_journal_line_of_a_chunk_jump_is_translated(conn):
    from server.console import journal_he_detail
    _base(conn)
    _wave(conn, 2.0)
    learn_netloss.evaluate_chunk(conn, conn.execute("SELECT session_id FROM wave_rates ORDER BY rowid DESC"
                                                    ).fetchone()[0])
    [line] = _jumps(conn)
    he = journal_he_detail.he_detail("net_loss_jump", line)
    assert "לפי 3 גלים" in he and "(waves)" not in he


# fixture משותף של שרת החדר -- מנוע החתיכות האמיתי מחובר ב-`server.app`
from test_server_room import room_server  # noqa: E402,F401
