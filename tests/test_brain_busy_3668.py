"""#3668 (S50, T2; אפיק #3653): "עסוק" בהגדרה אחת במוח, בצל -- ההתנהגות זהה להיום, והמוח רושם מה היה מחליט.

מה נבדק:

1. **שקילות.** העתק קפוא של `busy_reason` כפי שהיה לפני #3668 (`legacy_busy_reason`) מול `auto_reboot.busy_reason` של היום, על כל
   שילוב של חמשת החושים (כולל חוש שזורק, ו-`include_update` דלוק וכבוי): אותו ערך, או אותה חריגה. ושורת הצל של המוח אומרת את אותו דבר
   (`DEFER` עם אותה סיבה, `PROCEED` כשפנוי, `UNDECIDED` כשחוש זרק) ו-`agree=1`.
2. **רישום.** כל בדיקה כותבת שורה אחת ב-`brain_decisions` (כלל `B3668a`, פעולה `RESTART_GATE`, `mode=shadow`), והשואל נקוב בשמו:
   האתחול המתוזמן, "החל עדכון" (דרך `update.restart_blocker`) ו-`tools/server-upgrade.sh` (`B3668b`, בתהליך משלו).
3. **ברירת המחדל הבטוחה.** `PROCEED` רק כשכל החושים נקראו: חוש שחסר או שזרק הוא `UNDECIDED`, לא "פנוי" (עיקרון 5). הקוד הקיים עדיין זורק
   ושומר על הסירוב בשמו.
4. **צל אמיתי.** מתג `off` = לא נקרא ולא נרשם; כתיבה מהסקריפט אינה סוגרת "החלטות יתומות" של השרת שרץ.
5. **הקטלוג** מצביע על הקוד, ושני הפריטים נושאים `mode = "shadow"`.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import json
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from server import auto_reboot, update
from server.brain import memory, veto
from server.db import connect, set_setting
from test_update import _enable, update_server  # noqa: F401 -- update_server הוא fixture

REPO = Path(__file__).resolve().parent.parent
MAC = "aa:bb:cc:00:36:68"
RULE, RULE_UPGRADE = "B3668a", "B3668b"


def brain():
    """מודול המוח של הכלל. חסר = כשל בשמו, לא ImportError שמסתיר שהטסט לא רץ."""
    try:
        from server.brain import busy
    except ImportError:
        pytest.fail("server/brain/busy.py אינו קיים: אין כלל 'עסוק' במוח")
    return busy


class Boom(RuntimeError):
    """חוש שלא הצליח לקרוא."""


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "busy.db")


def legacy_busy_reason(rt, now=None, *, include_update=True):
    """`auto_reboot.busy_reason` כפי שהיה בדיוק לפני #3668 (העתק קפוא). זה האורקל של השקילות: הוא אינו נטען משום מקום אחר."""
    conn = rt.conn
    in_db = auto_reboot.busy_db.reason(conn)
    if in_db:
        return in_db
    streams = auto_reboot.inflight.running()
    if streams:
        return " ו".join(streams) + " רצה"
    if include_update and auto_reboot.update_verdict(rt, now)[0]:
        return "עדכון שרת רץ"
    vm = getattr(rt, "vmupdate", None)
    if vm is not None:
        state = vm.status(None).get("state")
        if state not in ("idle", "saved", "error"):
            return f"עדכון VM במצב {state}"
    rows = conn.execute(
        "SELECT n.mac, n.local_clone, n.last_seen FROM net_devices n"
        " JOIN machines m ON m.mac = n.mac WHERE n.local_clone IS NOT NULL")
    copying = [row["mac"] for row in rows
               if auto_reboot.room.local_copy_now(row["local_clone"], row["last_seen"])]
    if copying:
        return f"מחשב מדווח על שיכפול מקומי: {', '.join(copying)}"
    return None


def world(monkeypatch, conn, *, db=None, streams=(), update_running=False, vm=None, copying=False, raises=None):
    """מצב של השרת לפי החושים. ``raises`` = שם החוש שזורק (``db``/``streams``/``update``/``vm``/``local_copy``)."""
    def sensor(name, value):
        def read(*args, **kwargs):
            if raises == name:
                raise Boom(name)
            return value
        return read

    monkeypatch.setattr(auto_reboot.busy_db, "reason", sensor("db", db))
    monkeypatch.setattr(auto_reboot.inflight, "running", sensor("streams", list(streams)))
    monkeypatch.setattr(auto_reboot, "update_verdict", sensor("update", (update_running, None)))
    rt = SimpleNamespace(conn=conn)
    if vm is not None or raises == "vm":
        rt.vmupdate = SimpleNamespace(status=sensor("vm", {"state": vm or "idle"}))
    if copying or raises == "local_copy":
        conn.execute("INSERT OR IGNORE INTO groups (id, label, role) VALUES ('g3668', 'g', 'cloner')")
        conn.execute("INSERT OR IGNORE INTO machines (mac, suffix, group_id, added_at) VALUES (?, '01', 'g3668', ?)",
                     (MAC, datetime.now(timezone.utc).isoformat()))
        conn.execute("INSERT OR REPLACE INTO net_devices (mac, ip, last_seen, local_clone) VALUES (?, '10.0.0.9', ?, 'copying')",
                     (MAC, datetime.now(timezone.utc).isoformat()))
        conn.commit()
        if raises == "local_copy":
            monkeypatch.setattr(auto_reboot.room, "local_copy_now", sensor("local_copy", True))
    return rt


def rows(conn, rule=RULE):
    return memory.decisions(conn, kind="general", rule=rule, limit=50)


def outcome_of(call):
    """(ערך, None) או (None, שם החריגה)."""
    try:
        return call(), None
    except Boom as exc:
        return None, type(exc).__name__


#: כל מצב של החושים. הסדר הוא של החושים: ``db`` ראשון, ``local_copy`` אחרון.
SCENARIOS = {
    "free": {},
    "db": {"db": "יש סבב חדר פתוח או רץ"},
    "streams": {"streams": ["קליטת אימג'", "העלאת אימג'"]},
    "update": {"update_running": True},
    "vm_downloading": {"vm": "downloading"},
    "vm_idle": {"vm": "idle"},
    "vm_saved": {"vm": "saved"},
    "vm_error": {"vm": "error"},
    "copying": {"copying": True},
    "db_and_streams": {"db": "סבב", "streams": ["קליטת אימג'"]},
    "streams_and_update": {"streams": ["קליטת אימג'"], "update_running": True},
    "update_and_vm": {"update_running": True, "vm": "applying"},
    "vm_and_copying": {"vm": "applying", "copying": True},
    "everything": {"db": "סבב", "streams": ["x"], "update_running": True, "vm": "applying", "copying": True},
    "db_raises": {"raises": "db"},
    "streams_raise": {"raises": "streams"},
    "update_raises": {"raises": "update"},
    "vm_raises": {"raises": "vm"},
    "copy_raises": {"raises": "local_copy"},
    "db_busy_before_streams_raise": {"db": "סבב", "raises": "streams"},
    "streams_busy_before_update_raises": {"streams": ["x"], "raises": "update"},
}


@pytest.mark.parametrize("include_update", [True, False], ids=["with_update", "no_update"])
@pytest.mark.parametrize("name", list(SCENARIOS))
def test_the_busy_answer_is_what_it_was_and_the_brain_row_says_the_same(monkeypatch, conn, name, include_update):
    rt = world(monkeypatch, conn, **SCENARIOS[name])
    want = outcome_of(lambda: legacy_busy_reason(rt, include_update=include_update))
    assert not rows(conn), "האורקל הקפוא אינו נוגע במוח"
    got = outcome_of(lambda: auto_reboot.busy_reason(rt, include_update=include_update))
    assert got == want, f"{name}: השתנתה ההתנהגות של busy_reason: {got} במקום {want}"
    [row] = rows(conn)
    assert (row["mode"], row["action"], row["point"]) == ("shadow", "RESTART_GATE", "auto_reboot")
    verdict = {(None, None): "PROCEED"}.get(want) or ("UNDECIDED" if want[1] else "DEFER")
    assert row["verdict"] == verdict, (name, row)
    if verdict == "DEFER":
        assert row["reason"] == want[0], "הסיבה היא של הקוד הקיים, מילה במילה"
    assert "agree=1" in row["evidence"], row["evidence"]
    assert f"include_update={int(include_update)}" in row["evidence"]
    assert f"legacy={'exception' if want[1] else 'none' if want[0] is None else want[0]}" in row["evidence"]


def test_the_first_busy_sensor_in_the_order_decides(monkeypatch, conn):
    rt = world(monkeypatch, conn, db="סבב", streams=["קליטת אימג'"], update_running=True, vm="applying", copying=True)
    assert auto_reboot.busy_reason(rt) == "סבב"
    [row] = rows(conn)
    assert row["verdict"] == "DEFER" and row["evidence"][:3] == ["site=auto_reboot", "include_update=1", "sensor=db"]


def test_a_sensor_that_cannot_read_is_undecided_not_free_and_the_caller_still_raises(monkeypatch, conn):
    rt = world(monkeypatch, conn, raises="streams")
    with pytest.raises(Boom):
        auto_reboot.busy_reason(rt)
    [row] = rows(conn)
    assert row["verdict"] == "UNDECIDED" and "Boom" in row["reason"], row
    assert "db=free" in row["evidence"] and "streams=unread" in row["evidence"], row["evidence"]
    assert "legacy=exception" in row["evidence"] and "agree=1" in row["evidence"]


@pytest.mark.parametrize("seen", [{}, {"db": None}, {"db": None, "streams": None}, {"db": None, "streams": None, "update": None},
                                  {"db": None, "streams": None, "update": None, "vm": None}], ids=lambda s: f"read{len(s)}")
def test_proceed_needs_every_sensor_read(seen):
    busy = brain()
    reading = busy.decide(seen)
    assert reading.verdict == "UNDECIDED", "חושים שלא נקראו אינם 'פנוי'"
    assert busy.decide({**seen, "streams": None, "update": None, "vm": None, "local_copy": None, "db": None}).verdict == "PROCEED"


def test_the_update_sensor_is_not_waited_for_by_the_update_itself():
    busy = brain()
    seen = {"db": None, "streams": None, "vm": None, "local_copy": None}
    assert busy.decide(seen, include_update=False).verdict == "PROCEED"
    assert busy.decide(seen, include_update=True).verdict == "UNDECIDED", "מי ששואל גם על עדכון חייב לקרוא אותו"


def test_the_restart_blocker_of_the_update_asks_the_same_verdict_and_says_who_asked(monkeypatch, conn):
    rt = world(monkeypatch, conn, db="סבב חדר")
    assert update.restart_blocker(rt) == "סבב חדר"
    [row] = rows(conn)
    assert row["point"] == "update" and row["verdict"] == "DEFER" and "include_update=0" in row["evidence"]


def test_apply_update_refused_while_busy_leaves_a_deferred_row_for_the_update(update_server):
    ctx = update_server["app"].state.ctx
    ctx.conn.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at, wave_session_id)"
                     " VALUES ('rr3668', 'img', '[]', 'active', 'noc', '2026-11-18T08:00:00+00:00', NULL)")
    ctx.conn.commit()
    _enable(update_server["admin"])
    reply = update_server["admin"].post("/api/console/update/apply", json={"tag": "v0.25.0"})
    assert reply.status_code == 409 and "סבב חדר" in reply.json()["detail"], reply.text
    [row] = rows(ctx.conn)
    assert (row["point"], row["verdict"]) == ("update", "DEFER") and "סבב חדר" in row["reason"], row
    assert update_server["calls"]["run"] == 0


def test_the_off_switch_reads_nothing_and_records_nothing(monkeypatch, conn):
    set_setting(conn, veto.SETTING, "off")
    rt = world(monkeypatch, conn, db="סבב")
    assert auto_reboot.busy_reason(rt) == "סבב"
    assert rows(conn) == []


def test_the_scheduler_tick_defers_for_the_reason_the_brain_recorded(monkeypatch, conn):
    set_setting(conn, auto_reboot.MODE, "daily")
    set_setting(conn, auto_reboot.TIME, "03:00")
    rt = world(monkeypatch, conn, streams=["קליטת אימג'"])
    rebooted = []
    scheduler = auto_reboot.Scheduler(rt, reboot=lambda: rebooted.append(1),
                                      now=lambda: datetime(2026, 11, 18, 3, 0, 0))
    assert scheduler.tick() == "deferred"
    assert rebooted == [], "הקוד הקיים עדיין מחליט: עסוק = לא מפעילים מחדש"
    [row] = rows(conn)
    assert (row["point"], row["verdict"], row["reason"]) == ("auto_reboot", "DEFER", "קליטת אימג' רצה")


# --- tools/server-upgrade.sh (T2): תהליך משלו, אותו פסק --------------------------------------------------------

def test_the_upgrade_script_asks_the_same_verdict_in_its_own_process_and_leaves_the_servers_open_decisions_open(tmp_path):
    from test_installer_1131 import _fake_repo, _run_upgrade, _seed_db, _upgrade_fakes, BASH, calls
    if not BASH:
        pytest.skip("server-upgrade.sh is bash")

    bindir = tmp_path / "bin"
    bindir.mkdir()
    data = tmp_path / "data"
    _seed_db(data)
    db = connect(data / "imagesrv.db")
    db.execute("INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at, wave_session_id)"
               " VALUES ('rr3668', 'img', '[]', 'active', 'noc', '2026-11-18T08:00:00+00:00', NULL)")
    # החלטה פתוחה של השרת שרץ: תהליך אחר אינו סוגר אותה כ"יתומה"
    open_id = memory.record(db, mode="shadow", rule="K7", action="HTTP_BUDGET", verdict="CUT", reason="פתוחה",
                            at="2026-11-18T00:00:00+00:00")
    assert isinstance(open_id, int)
    db.commit()
    db.close()
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode != 0
    assert not [c for c in calls(tmp_path) if c.startswith(("git checkout", "systemctl restart"))]

    db = connect(data / "imagesrv.db")
    [row] = rows(db, RULE_UPGRADE)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "RESTART_GATE", "server_upgrade", "DEFER"), row
    assert "סבב חדר" in row["reason"] and "agree=1" in row["evidence"] and "include_update=0" in row["evidence"], row
    still = db.execute("SELECT outcome FROM brain_decisions WHERE id = ?", (open_id,)).fetchone()
    assert still["outcome"] is None, "הסקריפט סגר החלטה פתוחה של השרת כ'יתומה'"


# --- הקטלוג ---------------------------------------------------------------------------------------------------

def test_the_catalog_has_both_points_in_shadow_and_pointing_at_the_code():
    with (REPO / "server" / "brain" / "catalog.toml").open("rb") as f:
        items = {it["id"]: it for it in tomllib.load(f)["item"]}
    for rule, file, symbol in ((RULE, "server/brain/busy.py", "decide"), (RULE_UPGRADE, "tools/server-upgrade.sh", "busy_reason")):
        item = items[rule]
        assert (item["mode"], item["layer"], item["file"], item["symbol"]) == ("shadow", "decisions", file, symbol), item
    busy = brain()
    assert busy.RULE == RULE and busy.RULE_UPGRADE == RULE_UPGRADE
    assert json.dumps(busy.SITES) == json.dumps(["auto_reboot", "update", "server_upgrade"])
