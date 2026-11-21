"""#3064, שלב 2 -- **V6**: ראיית חיים מבייטים שהשרת עצמו הגיש (`server/brain/served.py`, ההוק ב-`server/agent/api.py`).

- המונה: מתי הוגש בייט אחרון ולמי, ספירה לפי הבייטים שנשלחו בפועל (גם `Range`, 206), ולא תשובה שנכשלה (404).
- הייחוס: כתובת -> MAC-ים מקובץ החכירות, ברגע ההחלטה. **"לא הצלחנו לייחס" אינו "אין ראיה"** (עיקרון 5):
  כתובת עם בייטים שאין לה חכירה, או שהחכירות לא נקראות, נרשמת כחוש `served_http` שלא נקרא, והמשכפל UNKNOWN.
- ההחלטה: משכפל ששותק בכל ערוץ אחר אבל השרת מגיש לו בייטים -- הווטו מחזיק (ב-enforce) ואינו מכשיל.
  בלי הבייטים, אותו משכפל LOST והפעולה מתבצעת. משכפל **אחר** שמוריד כרגע אינו ראיה על חבר הגל (I3).

**בקרה שלילית:** הסרת הספירה ב-`api.py` (חזרה ל-`FileResponse` רגיל) מפילה את טסטי ה-HTTP וטסט ה-end-to-end;
הסרת `served=` מה-`from_db` ב-`veto.check` מפילה את טסטי ההחלטה.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

pytest.importorskip("fastapi")

from conftest import PARTITION_BYTES, setup_classroom
from server import db
from server.brain import decide as brain, observe, veto
from server.brain.served import Served
from test_machine_identity import (BUILD, IMAGE, MAC1, STATION_IP, identity_server,  # noqa: F401
                                   write_leases)

W = "W-2"
NOW = 1_800_000_000.0
CREDS = {"username": "labtech", "password": "deploy-pass-1"}


# --- the counter -----------------------------------------------------------------------------------------------


def test_the_counter_remembers_the_last_byte_and_the_total_per_source():
    t = [NOW]
    served = Served(clock=lambda: t[0])
    served.note("10.0.0.5", 1000)
    t[0] += 40
    served.note("10.0.0.5", 500)
    served.note("10.0.0.6", 7)
    assert served.total("10.0.0.5") == 1500 and served.total("10.0.0.6") == 7 and served.total("10.9.9.9") == 0
    t[0] += 10
    assert served.sources(t[0], 180) == [("10.0.0.5", 10.0), ("10.0.0.6", 10.0)]
    assert served.sources(t[0], 5) == []            # outside the window: no evidence


def test_nothing_is_counted_for_zero_bytes_or_no_address():
    served = Served(clock=lambda: NOW)
    for ip, n in (("10.0.0.5", 0), ("10.0.0.5", -3), ("", 100)):
        served.note(ip, n)
    assert served.sources(NOW, 180) == []


def test_a_counter_that_fails_never_raises_into_the_download():
    def broken():
        raise RuntimeError("clock")
    Served(clock=broken).note("10.0.0.5", 100)       # must not raise


def test_the_counter_is_bounded_and_forgets_the_oldest_source():
    t = [NOW]
    served = Served(clock=lambda: t[0], max_sources=3, keep_s=10_000)
    for i in range(5):
        t[0] += 1
        served.note(f"10.0.0.{i}", 1)
    assert [ip for ip, _ in served.sources(t[0], 100)] == ["10.0.0.2", "10.0.0.3", "10.0.0.4"]


def test_attribution_gives_every_leased_mac_the_evidence_and_nothing_unknown():
    served = Served(clock=lambda: NOW - 12)
    served.note("10.0.0.5", 100)
    got, unknown = served.attribute(NOW, 180, lambda ip: ("aa:aa", "bb:bb"))
    assert got == [("aa:aa", "served", 12.0, "http bytes to 10.0.0.5"),
                   ("bb:bb", "served", 12.0, "http bytes to 10.0.0.5")]
    assert unknown == []


@pytest.mark.parametrize("resolver", [None, lambda ip: None, lambda ip: (), lambda ip: 1 / 0],
                         ids=["no source of leases", "leases file unreadable", "no lease on the address", "resolver raised"])
def test_bytes_that_cannot_be_attributed_are_an_unknown_sense_not_silence(resolver):
    served = Served(clock=lambda: NOW - 5)
    served.note("10.0.0.5", 100)
    got, unknown = served.attribute(NOW, 180, resolver)
    assert got == [] and unknown == ["served_http"]


def test_an_old_source_that_cannot_be_attributed_is_not_an_unknown_sense():
    """אין בייטים בחלון = אין מה לייחס. "לא ידוע" רק כשיש בייטים שלא ידוע של מי."""
    served = Served(clock=lambda: NOW - 5000)
    served.note("10.0.0.5", 100)
    assert served.attribute(NOW, 180, None) == ([], [])


# --- the HTTP hook ---------------------------------------------------------------------------------------------


def _partition_file():
    from test_chunksend_2533 import MANIFEST_256
    return next(p["file"] for p in MANIFEST_256["partitions"] if p.get("file"))


def test_the_partition_file_route_counts_exactly_the_bytes_it_sent(server):
    anon, ctx = server["anon"], server["ctx"]
    url = f"/api/v1/images/img_7f3a91/files/{_partition_file()}"
    before = ctx.served.total("testclient")
    got = anon.get(url, headers={"Range": "bytes=3-9"})
    assert got.status_code == 206 and got.content == PARTITION_BYTES[3:10]
    assert ctx.served.total("testclient") - before == 7
    full = anon.get(url)
    assert full.status_code == 200 and full.content == PARTITION_BYTES
    assert ctx.served.total("testclient") - before == 7 + len(PARTITION_BYTES)


def test_a_refused_or_missing_file_counts_nothing(server):
    anon, ctx = server["anon"], server["ctx"]
    assert anon.get("/api/v1/images/img_7f3a91/files/not-in-the-manifest").status_code == 404
    assert anon.get("/api/v1/images/img_unknown/manifest").status_code == 404
    assert ctx.served.total("testclient") == 0


def test_the_counter_is_wired_into_the_app_and_the_brain_reads_it_from_the_connection(server):
    ctx = server["ctx"]
    assert isinstance(ctx.served, Served)
    assert callable(getattr(ctx.conn, "brain_served", None))


def test_a_download_to_a_leased_machine_becomes_evidence_for_its_mac(identity_server):  # noqa: F811
    s = identity_server
    setup_classroom(s)
    assert s["station"].post("/api/v1/agent/pulls", json={"mac": MAC1, "image_id": IMAGE, **CREDS}).status_code == 200
    got = s["station"].get(f"/api/v1/images/{IMAGE}/files/p1.esp.pcl.zst")
    assert got.status_code == 200
    evidence, unknown = s["ctx"].conn.brain_served(time.time(), 180)
    assert unknown == []
    # both machines leased to the address carry it (the conservative reading: more evidence, never less)
    assert sorted(mac for mac, *_ in evidence) == sorted([MAC1, BUILD])
    assert all(kind == "served" and age < 5 and source == f"http bytes to {STATION_IP}"
               for _mac, kind, age, source in evidence)


def test_a_download_to_an_address_without_a_lease_is_an_unknown_sense(identity_server):  # noqa: F811
    s = identity_server
    setup_classroom(s)
    assert s["station"].post("/api/v1/agent/pulls", json={"mac": MAC1, "image_id": IMAGE, **CREDS}).status_code == 200
    assert s["station"].get(f"/api/v1/images/{IMAGE}/files/p1.esp.pcl.zst").status_code == 200
    write_leases(s["leases"], {})                     # the file is read, and holds nobody
    evidence, unknown = s["ctx"].conn.brain_served(time.time(), 180)
    assert evidence == [] and unknown == ["served_http"]
    s["leases"].unlink()                              # the file is not readable at all
    assert s["ctx"].conn.brain_served(time.time(), 180) == ([], ["served_http"])


# --- the decision ----------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    veto.forget()
    c = db.connect(tmp_path / "brain.db")
    c.execute("PRAGMA foreign_keys = OFF")
    yield c
    veto.forget()


def ago(seconds: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _iso(age: float) -> str:
    return datetime.fromtimestamp(NOW - age, timezone.utc).isoformat(timespec="seconds")


def member(conn, mac: str, age: float) -> None:
    conn.execute("INSERT INTO session_members (session_id, mac, state, updated_at, targets_json)"
                 " VALUES (?, ?, 'writing', ?, ?)",
                 (W, mac, ago(age), json.dumps([{"dev": "sda", "serial": f"{mac}-1", "state": "writing"}])))
    conn.commit()


def source_of(evidence, unknown=()):
    return lambda now, window: (list(evidence), list(unknown))


def ask(conn, **kw):
    return veto.check(conn, action="FAIL_DISKS", rule="D15", round_id=W, targets=["aa"], reason="silent", **kw)


def test_a_machine_silent_everywhere_but_served_bytes_is_held_not_failed(conn, caplog):
    """13/11: ההשלמה ב-HTTP לא מדווחת ל-ledger, וה-progress לא מגיע. הבייטים שהשרת עצמו כתב הם הראיה."""
    caplog.set_level(logging.INFO, logger="imagesrv.brain")
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    assert ask(conn).proceed is True                              # no evidence at all: LOST, as before
    veto.forget()
    got = ask(conn, served=source_of([("aa", observe.SERVED, 4.0, "http bytes to 10.0.0.5")]))
    assert got.proceed is False and got.would_block is True and "aa=ALIVE" in got.reason
    line = [json.loads(r.getMessage()[6:]) for r in caplog.records if '"rule": "K2"' in r.getMessage()][-1]
    assert any(e.startswith("served@4s (http bytes to 10.0.0.5)") for e in line["evidence"])


def test_served_bytes_count_as_work_so_a_machine_that_only_downloads_is_not_stalled():
    cfg = brain.Config()
    one = [{"mac": "aa", "state": "writing", "updated_at": _iso(1), "targets_json": None}]
    fresh = observe.build(now=NOW, round_id=W, members=one, served=[("aa", observe.SERVED, 2.0, "http bytes to x")])
    assert brain.liveness(fresh.cloner("aa"), NOW, cfg)[0] == brain.ALIVE
    # it talks (progress 1 s ago) but the last byte served is old: the work did not move -- the kind is WORK
    old = observe.build(now=NOW, round_id=W, members=one, served=[("aa", observe.SERVED, 400.0, "http bytes to x")])
    assert brain.liveness(old.cloner("aa"), NOW, cfg)[0] == brain.STALLED


def test_evidence_of_another_machine_is_not_evidence_for_a_member_of_the_wave(conn):
    """I3: משכפל אחר שמוריד עכשיו אינו מחזיק את הפעולה על aa, ואינו מוסיף שורת K1 לצילום."""
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = ask(conn, served=source_of([("zz", observe.SERVED, 1.0, "http bytes to 10.0.0.9")]))
    assert got.proceed is True and got.would_block is False
    snap = observe.build(now=NOW, round_id=W,
                         members=[{"mac": "aa", "state": "writing", "updated_at": _iso(400), "targets_json": None}],
                         served=[("zz", observe.SERVED, 1.0, "x")])
    assert [c.mac for c in snap.cloners] == ["aa"]


def test_bytes_that_cannot_be_attributed_make_the_machine_unknown_and_hold_in_enforce(conn):
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = ask(conn, served=source_of([], ["served_http"]))
    assert got.proceed is False and "aa=UNKNOWN" in got.reason and "served_http" in got.reason


def test_a_served_source_that_fails_is_unknown_not_clean(conn):
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    got = ask(conn, served=lambda now, window: 1 / 0)
    assert got.proceed is False and "aa=UNKNOWN" in got.reason


def test_the_source_attached_to_the_connection_is_used_without_being_passed(conn):
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    veto.attach(conn, source_of([("aa", observe.SERVED, 3.0, "http bytes to 10.0.0.5")]))
    assert ask(conn).proceed is False


def test_a_connection_that_cannot_carry_the_source_is_the_stage_one_brain(tmp_path):
    raw = sqlite3.connect(tmp_path / "raw.db")
    veto.attach(raw, source_of([]))                  # sqlite3.Connection takes no attributes: logged, not raised
    assert getattr(raw, "brain_served", None) is None


def test_the_served_window_is_the_lost_window_so_old_bytes_are_not_alive(conn):
    seen = []
    member(conn, "aa", 400)
    db.set_setting(conn, veto.SETTING, "enforce")
    db.set_setting(conn, "lost_wait_seconds", "300")
    ask(conn, served=lambda now, window: seen.append(window) or ([], []))
    assert seen == [300]
