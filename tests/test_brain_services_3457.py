"""#3457 (K13): שירותי השרת שהסבב צריך -- המוח יודע, מסביר וממליץ. הוא לא מדליק ולא מכבה.

הלב: **בדיקה שנכשלה היא "לא נבדק"** -- לעולם לא "פעיל" ולא "כבוי" (עיקרון 5). `systemctl is-active` הוא פקודה ש-`3` בה
תשובה ("כבוי"), ו-`4` ("אין יחידה כזו") או פלט שאינו מוכר הם לא ראיה לאף אחד מהשניים.

**בקרה שלילית:** עם **כל** קובצי הייצור שהשינוי נגע בהם מוחזרים לבסיס של ה-PR (`server/brain/services.py` נמחק, וגם החיבורים ב-`server/app.py`,
`server/main.py`, `server/system/health.py`, `server/static/console.js`), הטסטים (neg) נופלים על התנהגות: אין שורת `brain_services` בבריאות,
אין endpoint (404 במקום 200/409), והכפתור אינו נבנה מ-`actions`. הטסטים הטהורים של `services` (`pure`) מדולגים שם כי אין מה לייבא --
וזה **אינו** בקרה שלילית ואינו נספר כאחת.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import db
from server.brain import memory, veto
from test_port_toggles import SS_DHCP_ONLY, SS_HEAD, SS_UDP, FakeDnsmasq, FakeListeners
from test_server_room import CLONER1, room_body, room_server   # noqa: F401 -- room_server הוא fixture

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

try:                                                  # בקרה שלילית: בבסיס אין את המודול
    from server.brain import services as S
except ImportError:                                   # pragma: no cover
    S = None

#: הטסטים הטהורים של המודול החדש. בבקרה השלילית הם מדולגים, ואלה שבאפליקציה אמיתית (neg) הם שנכשלים.
pure = pytest.mark.skipif(S is None, reason="negative control: server/brain/services.py is absent")

SS_NTP = SS_HEAD + 'UNCONN 0 0 0.0.0.0:123 0.0.0.0:* users:(("chronyd",pid=1,fd=5))\n'


def _hooks(*, udp=SS_UDP, tcp="listen", dnsmasq=(0, "active"), imagesrv=(0, "active"), dhcp_on=False):
    """hooks לקריאה, בלי systemctl ובלי ss אמיתיים. `tcp="listen"` = 7712 מאזין."""
    tcp = (SS_HEAD + 'LISTEN 0 2048 0.0.0.0:7712 0.0.0.0:* users:(("python3",pid=9,fd=7))\n') if tcp == "listen" else tcp
    units = {"dnsmasq": dnsmasq, "imagesrv-server": imagesrv}
    return {"ss": lambda: udp, "ss_tcp": lambda: tcp, "unit": lambda name: units[name], "dhcp_on": lambda: dhcp_on}


def _by(hooks):
    return {s.id: s for s in S.read(hooks)}


# --- בדיקה שנכשלה אינה "פעיל" ואינה "כבוי" ---------------------------------------------------------------------


@pytest.mark.parametrize("got, state", [
    ((0, "active"), "active"),
    ((3, "inactive"), "inactive"), ((3, "failed"), "inactive"),                  # 3 היא תשובה, לא כשל
    ((4, ""), "unchecked"), ((4, "inactive"), "unchecked"),                       # אין יחידה כזו: לא ראיה לכבוי
    ((0, ""), "unchecked"), ((0, "inactive"), "unchecked"), ((1, "active"), "unchecked"),
    ((3, "activating"), "unchecked"), ((3, ""), "unchecked"), ((255, "active"), "unchecked"),
    (None, "unchecked")])
@pure
def test_the_exit_code_and_the_output_together_are_the_evidence(got, state):
    assert S.unit_state(got)[0] == state


@pure
def test_a_systemctl_that_could_not_run_is_none_and_the_code_is_read_not_raised(monkeypatch):
    def run(exc=None, **done):
        def fake(cmd, **kw):
            assert kw["stdin"] is subprocess.DEVNULL and kw["timeout"] > 0           # בלי stdin מורש, עם תקרת זמן
            if exc:
                raise exc
            return subprocess.CompletedProcess(cmd, done["rc"], done["out"], "")
        monkeypatch.setattr(S.subprocess, "run", fake)
        return S.default_unit("dnsmasq")
    assert run(rc=3, out="inactive\n") == (3, "inactive")                               # לא נזרק על קוד שאינו אפס
    assert run(rc=0, out="active\n") == (0, "active")
    assert run(exc=FileNotFoundError()) is None
    assert run(exc=subprocess.TimeoutExpired("systemctl", 5)) is None


@pure
def test_an_unreadable_socket_table_leaves_every_port_service_unchecked_never_active():
    got = _by(_hooks(udp="", tcp=""))
    for sid in ("tftp", "dhcp", "http_boot", "ntp", "syslog"):
        assert got[sid].state == S.UNCHECKED, sid
    kind, reason = S.verdict(list(got.values()))
    assert kind == S.UNDECIDED and "לא נבדק" in reason


@pure
def test_a_hook_that_raises_is_unchecked_and_does_not_reach_the_caller():
    hooks = _hooks()
    hooks["unit"] = lambda name: (_ for _ in ()).throw(OSError("no systemctl"))
    hooks["ss"] = lambda: (_ for _ in ()).throw(OSError("no ss"))
    got = _by(hooks)
    assert got["dnsmasq"].state == S.UNCHECKED and got["tftp"].state == S.UNCHECKED


# --- מה הסבב צריך, וההכרעה -------------------------------------------------------------------------------------


@pure
def test_everything_running_is_ready_and_names_what_it_checked():
    kind, reason = S.verdict(S.read(_hooks()))
    assert kind == S.READY and "TFTP" in reason and "HTTP" in reason and "dnsmasq" in reason


@pure
def test_tftp_down_blocks_with_the_reason_in_hebrew_and_offers_the_button_for_it_only():
    got = _by(_hooks(udp=SS_DHCP_ONLY))
    kind, reason = S.verdict(list(got.values()))
    assert kind == S.BLOCKED and "TFTP כבוי, הסבב ייכשל" in reason
    assert [s.id for s in got.values() if s.startable] == ["tftp"]


@pure
def test_the_http_port_follows_the_listener_and_http_down_is_startable():
    hooks = _hooks(tcp=SS_HEAD)                                       # הטבלה נקראה ואין 7712
    got = _by(hooks)
    assert got["http_boot"].state == S.INACTIVE and got["http_boot"].startable
    hooks.update(ss_tcp=lambda: SS_HEAD + 'LISTEN 0 2048 0.0.0.0:9090 0.0.0.0:* users:(("python3",pid=9,fd=7))\n',
                 http_port=lambda: 9090)
    assert _by(hooks)["http_boot"].state == S.ACTIVE


@pure
def test_dhcp_is_needed_only_when_the_server_is_set_to_serve_it_and_never_has_a_button():
    off_by_design = _by(_hooks(udp=SS_NTP, dhcp_on=False))
    assert off_by_design["dhcp"].state == S.INACTIVE and not off_by_design["dhcp"].needed
    kind, reason = S.verdict([s for s in off_by_design.values() if s.id != "tftp"])
    assert kind == S.READY, reason                                                 # DHCP של המכללה: לא אזהרה
    meant = _by(_hooks(udp=SS_NTP, dhcp_on=True))
    assert meant["dhcp"].needed and not meant["dhcp"].startable
    assert "DHCP כבוי, הסבב ייכשל" in S.verdict(list(meant.values()))[1]
    unread = _by(_hooks(udp=SS_NTP, dhcp_on=None))                                 # לא ידוע אם הוא אמור לרוץ
    assert unread["dhcp"].state == S.UNCHECKED and unread["dhcp"].needed


@pure
def test_dnsmasq_down_blocks_but_has_no_button_and_ntp_syslog_and_imagesrv_never_decide():
    got = _by(_hooks(dnsmasq=(3, "inactive"), imagesrv=(3, "failed"), udp=SS_UDP))
    kind, reason = S.verdict(list(got.values()))
    assert kind == S.BLOCKED and "dnsmasq" in reason and "imagesrv" not in reason
    assert not got["dnsmasq"].startable and not got["imagesrv"].needed
    ok = _by(_hooks())
    assert ok["ntp"].state == S.INACTIVE and ok["syslog"].state == S.INACTIVE
    assert S.verdict(list(ok.values()))[0] == S.READY


@pure
def test_the_startable_list_is_closed_and_has_neither_dhcp_nor_dnsmasq():
    assert S.STARTABLE == ("tftp", "http_boot")


# --- שורת הבריאות -----------------------------------------------------------------------------------------------


@pure
def test_the_health_row_maps_the_verdict_and_carries_buttons_only_for_the_closed_list():
    ok = S.health_check(S.read(_hooks()))
    assert (ok["id"], ok["state"]) == ("brain_services", "ok") and "actions" not in ok
    down = S.health_check(S.read(_hooks(udp=SS_DHCP_ONLY, tcp=SS_HEAD)))
    assert down["state"] == "warn"
    assert [(a["id"], a["url"]) for a in down["actions"]] == [
        ("tftp", "/api/console/brain/services/tftp/start"), ("http_boot", "/api/console/brain/services/http_boot/start")]
    blind = S.health_check(S.read(_hooks(udp="", tcp="")))
    assert blind["state"] == "unknown" and "actions" not in blind
    dhcp = S.health_check(S.read(_hooks(udp=SS_NTP.replace("123", "69"), dhcp_on=True)))
    assert "actions" not in dhcp and "DHCP" in dhcp["detail"] and "במסלול הקיים" in dhcp["detail"]


# --- ההחלטה בצל ------------------------------------------------------------------------------------------------


@pytest.fixture()
def conn(tmp_path):
    if S is not None:
        S.forget()
    c = db.connect(tmp_path / "imagesrv.db")
    yield c
    if S is not None:
        S.forget()


def _rows(conn):
    return [d for d in memory.decisions(conn, rule="K13", limit=50) if d["action"] == "SERVICES_FOR_ROUND"]


@pure
def test_the_decision_is_recorded_in_shadow_with_every_service_in_the_evidence(conn):
    S.attach(conn, lambda: S.read(_hooks(udp=SS_DHCP_ONLY)))
    assert S.gate(conn, point="open_round") == S.BLOCKED
    (row,) = _rows(conn)
    assert (row["verdict"], row["mode"], row["point"]) == ("BLOCKED", "shadow", "open_round")
    assert "TFTP כבוי, הסבב ייכשל" in row["reason"] and row["outcome"] == S.SHADOW_HE
    assert "tftp=inactive: אף אחד לא מאזין על 69" in row["evidence"] and any(e.startswith("ntp=") for e in row["evidence"])
    assert row["line_he"].startswith("המוח (כללי) → שירותי השרת לסבב: חסום")


@pure
def test_without_a_reader_or_with_the_brain_off_nothing_is_recorded(conn):
    assert S.gate(conn, point="open_round") is None
    S.attach(conn, lambda: S.read(_hooks()))
    db.set_setting(conn, veto.SETTING, "off")
    assert S.gate(conn, point="open_round") is None and _rows(conn) == []


@pure
def test_the_read_is_cached_and_the_same_decision_is_recorded_once_per_window(conn):
    reads = []
    S.attach(conn, lambda: reads.append(1) or S.read(_hooks(udp=SS_DHCP_ONLY)))
    clock = iter([0.0, 3.0, 12.0, 40.0])
    for _ in range(4):
        S.gate(conn, point="attach_wave", mono=lambda: next(clock))
    assert len(reads) == 3                      # 0 ו-3 חולקים קריאה (מטמון 10 ש'); 12 ו-40 קוראים שוב
    assert len(_rows(conn)) == 2                # 0 ו-40: אותה החלטה נרשמת פעם בחלון של 30 ש'


@pure
def test_a_reader_that_raises_never_reaches_the_round(conn):
    S.attach(conn, lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    assert S.gate(conn, point="open_round") is None and _rows(conn) == []


@pure
def test_opening_a_round_records_the_services_and_still_opens_it(room_server):
    """(neg) בלי הקריאה ב-`open_round` אין שורת K13. ההחלטה אינה חוסמת: הסבב נפתח גם כש-TFTP כבוי."""
    conn = room_server["ctx"].conn
    S.forget()
    S.attach(conn, lambda: S.read(_hooks(udp=SS_DHCP_ONLY)))
    opened = room_server["deploy"].post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]}))
    assert opened.status_code == 200, opened.text
    (row,) = [d for d in _rows(conn) if d["point"] == "open_round"]
    assert row["verdict"] == "BLOCKED"
    S.forget()


# --- פעולת המפעיל: אפליקציה אמיתית, hooks מוזרקים ---------------------------------------------------------------


def _app(tmp_path: Path, images_root: Path, clock, *, listeners=None):
    from server.app import create_app
    from server.console import users
    from server.net import dhcp
    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)
    listeners = listeners or FakeListeners()
    ifaces = [{"name": "eth0", "state": "up", "mac": "aa", "addresses": ["10.44.0.1/24"]}]
    fake = {"ss": SS_DHCP_ONLY, "installer_conf": "port=0\ninterface=eth0\nbind-interfaces\n# enable-tftp moved (#1013)\n",
            "units": {"dnsmasq": (0, "active"), "imagesrv-server": (0, "active")}}
    hooks = {"ss": lambda: fake["ss"], "ss_tcp": listeners.ss_tcp, "nft_ruleset": lambda: None,
             "unit_active": lambda name: "active", "http_get": lambda url: 200, "http_size": lambda url: (200, 31_000_000),
             "interfaces": lambda: ifaces,
             "tftp_root": lambda: tftp, "udp_sender_pids": lambda: [], "port_listeners": listeners,
             "installer_conf": lambda: fake["installer_conf"],
             "brain_host": True, "brain_unit": lambda name: fake["units"][name]}
    dnsmasq = FakeDnsmasq(fake)
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", now_fn=clock, health_hooks=hooks,
                     dhcp_hooks={"apply": dnsmasq.apply, "apply_proxy": dnsmasq.apply_proxy,
                                 "interfaces": lambda: ifaces,
                                 "probe": lambda name: dhcp.ProbeResult(True, ()),
                                 "read_active_conf": lambda: "", "service_active": lambda unit: True})
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app, base_url="http://testserver:80"), TestClient(app, base_url="http://testserver:80")
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "anon": TestClient(app), "fake": fake, "conn": conn,
            "listeners": listeners, "dnsmasq": dnsmasq}


@pytest.fixture()
def live(tmp_path, images_root, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    if S is not None:
        S.forget()
    yield _app(tmp_path, images_root, clock)
    if S is not None:
        S.forget()


def _row(live):
    rows = {r["id"]: r for r in live["admin"].get("/api/console/health").json()}
    assert "brain_services" in rows, f"no brain_services row in the health: {sorted(rows)}"
    return rows["brain_services"]


def _events(live):
    return [r["event"] for r in live["conn"].execute("SELECT event FROM journal ORDER BY id DESC LIMIT 6").fetchall()]


def _start(live, sid, client="admin"):
    return live[client].post(f"/api/console/brain/services/{sid}/start")


def test_the_health_row_says_tftp_is_down_with_a_button_and_clicking_it_turns_it_on_and_proves_it(live):
    """(neg) על הקוד הישן אין שורה ואין endpoint."""
    row = _row(live)
    assert row["state"] == "warn" and "TFTP כבוי, הסבב ייכשל" in row["detail"]
    assert [a["id"] for a in row["actions"]] == ["tftp"]
    done = _start(live, "tftp")
    assert done.status_code == 200 and done.json()["ok"] is True and done.json()["verified"] is True, done.text
    assert len(live["dnsmasq"].applied) == 1                      # אותו רינדור של דף הפורטים, פעם אחת
    assert _row(live)["state"] == "ok"
    assert "port_toggle" in _events(live)


def test_a_start_that_did_not_take_is_not_ok_and_is_logged_as_unverified(live):
    live["dnsmasq"].fail = True
    got = _start(live, "tftp")
    assert got.status_code == 200 and got.json()["ok"] is False and got.json()["verified"] is False
    assert "dnsmasq לא הגיב" in got.json()["detail"]
    assert "port_unverified" in _events(live)


def test_http_is_started_through_the_listeners_manager_and_verified_from_the_socket(live):
    live["listeners"].open.discard("http_boot")
    live["fake"]["ss"] = SS_UDP
    assert [a["id"] for a in _row(live)["actions"]] == ["http_boot"]
    got = _start(live, "http_boot")
    assert got.json()["ok"] is True and live["listeners"].calls == [("http_boot", True)]


def test_a_listener_that_cannot_open_is_reported_by_name_not_as_started(live):
    live["listeners"].open.discard("http_boot")
    live["listeners"].fail_open.add("http_boot")
    got = _start(live, "http_boot").json()
    assert got["ok"] is False and "address already in use" in got["detail"]


def test_dhcp_and_dnsmasq_have_no_button_and_an_unknown_service_is_not_found(live):
    for sid in ("dhcp", "dnsmasq", "ntp", "syslog", "imagesrv"):
        got = _start(live, sid)
        assert got.status_code == 409, (sid, got.text)
    assert _start(live, "dhcp").json()["detail"].startswith("לשירות הזה אין כפתור")
    assert _start(live, "ssh").status_code == 404 and _start(live, "..%2Fetc").status_code in (404, 405)
    assert live["dnsmasq"].applied == []                          # שום דבר לא הופעל


def test_only_a_service_measured_down_is_started(live):
    live["fake"]["ss"] = SS_UDP                                   # TFTP כבר מאזין
    already = _start(live, "tftp")
    assert already.status_code == 409 and "כבר פעיל" in already.json()["detail"]
    live["fake"]["ss"] = ""                                       # הטבלה לא נקראה: לא ידוע, ולכן לא מדליקים
    blind = _start(live, "tftp")
    assert blind.status_code == 409 and "לא נבדק" in blind.json()["detail"]
    assert live["dnsmasq"].applied == []


def test_only_an_admin_may_start_a_service(live):
    assert _start(live, "tftp", client="deploy").status_code == 403
    assert _start(live, "tftp", client="anon").status_code == 401
    assert live["dnsmasq"].applied == []


def test_the_host_hooks_are_turned_on_only_by_main_and_without_them_there_is_no_row_and_no_systemctl(
        tmp_path, images_root, clock, monkeypatch):
    """(neg) בלי `brain_host` בהזרקה אין שורה, ו-`systemctl` לא רץ (בדיקות אינן נוגעות במכונה); `main.py` מדליק אותו."""
    from server.app import create_app
    from server.console import users
    called = []
    if S is not None:
        monkeypatch.setattr(S, "default_unit", lambda name: called.append(name))    # הקנרית: אף systemctl אינו נקרא
    quiet = {"ss": lambda: "", "ss_tcp": lambda: "", "nft_ruleset": lambda: None, "unit_active": lambda n: "active",
             "http_get": lambda u: 200, "http_size": lambda u: (200, 31_000_000), "interfaces": lambda: [],
             "tftp_root": lambda: tmp_path, "udp_sender_pids": lambda: []}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:7712", now_fn=clock, health_hooks=quiet)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert "brain_services" not in {r["id"] for r in client.get("/api/console/health").json()}
    assert client.post("/api/console/brain/services/tftp/start").status_code == 404 and not called
    main_text = (Path(__file__).resolve().parent.parent / "server" / "main.py").read_text(encoding="utf-8")
    assert '"brain_host": True' in main_text


# --- TFTP מפעיל מחדש את dnsmasq: לא מדליקים אותו כשיש גל או סבב חי -----------------------------------------------


def _busy(monkeypatch, why="יש סבב פעיל"):
    if S is not None:                                           # (בבקרה השלילית אין מה לתקן: ה-endpoint אינו קיים)
        monkeypatch.setattr(S, "busy_reason", lambda ctx: why)


def test_tftp_is_refused_with_the_reason_while_a_round_is_live_and_nothing_is_restarted(live, monkeypatch):
    """(neg) בלי החסימה ה-endpoint מדליק ומפעיל מחדש את dnsmasq."""
    _busy(monkeypatch)
    got = _start(live, "tftp")
    assert got.status_code == 409 and "יש סבב פעיל" in got.json()["detail"] and "dnsmasq" in got.json()["detail"], got.text
    assert live["dnsmasq"].applied == []
    from server.net import ports
    assert ports.enabled(live["conn"], "tftp") is True and "port:tftp" not in str(
        live["conn"].execute("SELECT key FROM settings WHERE key = 'port:tftp'").fetchall())      # המתג השמור לא נגעו בו
    assert "port_toggle" not in _events(live)


def test_the_health_button_of_tftp_is_disabled_with_the_reason_but_http_stays_on(live, monkeypatch):
    """(neg) בלי `busy` ב-`health_check` הכפתור פעיל תמיד."""
    _busy(monkeypatch)
    live["listeners"].open.discard("http_boot")                  # גם HTTP כבוי: שני הכפתורים מוצעים
    acts = {a["id"]: a for a in _row(live)["actions"]}
    assert acts["tftp"].get("disabled") is True and acts["tftp"].get("reason_he") == "יש סבב פעיל", acts
    assert not acts["http_boot"].get("disabled")
    assert _start(live, "http_boot").json()["ok"] is True       # פתיחת מאזין אינה מפעילה מחדש את dnsmasq


@pure
def test_a_live_round_wave_or_pull_makes_it_busy_and_a_check_that_failed_does_too(room_server):
    ctx = room_server["ctx"]
    assert S.busy_reason(ctx) is None
    assert room_server["deploy"].post("/api/console/room", json=room_body(room_server, 1, {CLONER1: ["S1"]})).status_code == 200
    assert S.busy_reason(ctx) == "יש סבב פעיל"

    class Broken:
        conn = None
    assert S.busy_reason(Broken()) == "לא ניתן לבדוק אם יש סבב פעיל"        # לא ניתן לבדוק = עסוק, לא פנוי


@pure
def test_a_finished_wave_kept_for_its_summary_is_not_live_and_a_pull_is():
    from types import SimpleNamespace
    from server.rounds import room_state

    class Store:
        def __init__(self, wave=None, spent=False, pulls=()):
            self.wave, self.spent, self.pulls = wave, spent, list(pulls)

        def active_broadcast(self):
            return self.wave

        def _spent(self, wave):
            return self.spent

        def active_pulls(self):
            return self.pulls
    real = room_state.active_round
    room_state.active_round = lambda conn: None
    try:
        assert S.busy_reason(SimpleNamespace(conn=None, store=Store(wave={"id": "w"}, spent=True))) is None
        assert S.busy_reason(SimpleNamespace(conn=None, store=Store(wave={"id": "w"}))) == "יש גל פעיל"
        assert S.busy_reason(SimpleNamespace(conn=None, store=Store(pulls=[1]))) == "יש משיכה פעילה"
    finally:
        room_state.active_round = real


@pure
def test_the_health_row_marks_only_the_dnsmasq_restarting_button_disabled():
    rows = S.read(_hooks(udp=SS_DHCP_ONLY, tcp=SS_HEAD))
    calm = {a["id"]: a for a in S.health_check(rows)["actions"]}
    busy = {a["id"]: a for a in S.health_check(rows, "יש סבב פעיל")["actions"]}
    assert not any(a.get("disabled") for a in calm.values())
    assert busy["tftp"]["disabled"] is True and busy["tftp"]["reason_he"] == "יש סבב פעיל" and not busy["http_boot"].get("disabled")
    assert S.RESTARTS_DNSMASQ == ("tftp",)
