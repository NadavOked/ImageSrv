"""‏#1973: UPS דרך NUT — יומן בנפילת חשמל, עצירה גלויה וכיבוי בסוללה נמוכה.

הפרוטוקול נבדק מול upsd מזויף על סוקט TCP אמיתי (‏127.0.0.1); הכיבוי
מוזרק — שום בדיקה כאן אינה קוראת ל-systemctl."""

from __future__ import annotations

import socket
import threading

import pytest

from server import ups
from server.db import connect, set_settings


# --- upsd מזויף ------------------------------------------------------------------

class FakeUpsd:
    """מאזין על פורט פנוי ועונה ל-`LIST VAR` לפי `answer(ups_name) -> str`."""

    def __init__(self, answer) -> None:
        self.answer = answer
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(4)
        self.port = self.sock.getsockname()[1]
        self.requests: list[str] = []
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                line = conn.makefile("r").readline().strip()
                self.requests.append(line)
                conn.sendall(self.answer(line.split()[-1]).encode())

    def close(self) -> None:
        self.sock.close()


def listing(name: str, values: dict[str, str]) -> str:
    rows = "".join(f'VAR {name} {k} "{v}"\n' for k, v in values.items())
    return f"BEGIN LIST VAR {name}\n{rows}END LIST VAR {name}\n"


@pytest.fixture()
def upsd():
    servers = []

    def make(answer):
        server = FakeUpsd(answer)
        servers.append(server)
        return server
    yield make
    for server in servers:
        server.close()


def test_the_nut_protocol_is_read_from_a_real_socket(upsd):
    fake = upsd(lambda name: listing(name, {"ups.status": "OL CHRG", "battery.charge": "97",
                                             "battery.runtime": "2700"}))
    values = ups.read_vars(ups.Target("rack", "127.0.0.1", fake.port))
    assert fake.requests == ["LIST VAR rack"]
    r = ups.reading(values)
    assert r.status == ("OL", "CHRG") and r.charge == 97 and r.runtime_s == 2700
    assert not r.on_battery and not r.low
    assert ups.describe(r) == "על חשמל · סוללה 97% · ~45 דק' בסוללה"


@pytest.mark.parametrize("answer, words", [
    (lambda name: "ERR UNKNOWN-UPS\n", "UNKNOWN-UPS"),
    (lambda name: f"BEGIN LIST VAR {name}\nVAR {name} ups.status \"OL\"\n", "לפני סוף הרשימה"),
    (lambda name: "hello\n", "לא צפויה"),
])
def test_a_bad_answer_is_an_error_not_a_reading(upsd, answer, words):
    fake = upsd(answer)
    with pytest.raises(ups.UpsError, match=words):
        ups.read_vars(ups.Target("rack", "127.0.0.1", fake.port))


def test_no_upsd_is_an_error_not_a_reading():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    with pytest.raises(ups.UpsError, match="אין חיבור"):
        ups.read_vars(ups.Target("rack", "127.0.0.1", port), timeout=2)


def test_a_reading_without_status_is_not_on_mains():
    with pytest.raises(ups.UpsError, match="ups.status"):
        ups.reading({"battery.charge": "100"})


@pytest.mark.parametrize("text, expected", [
    ("ups@10.0.0.5", "ups@10.0.0.5"), ("rack-1@nut.college.local:3500", "rack-1@nut.college.local:3500"),
    ("ups@10.0.0.5:3493", "ups@10.0.0.5")])
def test_targets_parse(text, expected):
    assert str(ups.parse_target(text)) == expected


@pytest.mark.parametrize("text", ["", "10.0.0.5", "ups@", "u ps@host", "ups@host:0", "ups@ho$t"])
def test_bad_targets_are_refused(text):
    with pytest.raises(ValueError):
        ups.parse_target(text)


# --- המנטר ----------------------------------------------------------------------

@pytest.fixture()
def conn(tmp_path):
    db = connect(tmp_path / "imagectl.db")
    set_settings(db, {ups.MODE: "net", ups.TARGET: "rack@10.0.0.5"})
    return db


def events(conn) -> list[str]:
    return [r["event"] for r in conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'ups_%' ORDER BY id")]


class Script:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.order: list[str] = []

    def read(self, target):
        item = self.answers.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def stop_work(self, reason):
        self.order.append(("stop", reason))
        return ["s1"]

    def poweroff(self):
        self.order.append(("poweroff",))


def monitor(conn, script: Script) -> ups.UpsMonitor:
    return ups.UpsMonitor(conn, stop_work=script.stop_work, poweroff=script.poweroff,
                          read=script.read)


OL = {"ups.status": "OL", "battery.charge": "100"}
OB = {"ups.status": "OB DISCHRG", "battery.charge": "60", "battery.runtime": "600"}
OB_LB = {"ups.status": "OB DISCHRG LB", "battery.charge": "9", "battery.runtime": "90"}


def test_a_power_cut_and_its_return_are_journaled_once_each(conn):
    script = Script(OL, OB, OB, OL)
    m = monitor(conn, script)
    for _ in range(4):
        m.tick()
    assert events(conn) == ["ups_on_battery", "ups_power_restored"]
    assert script.order == [], "על סוללה בלי LB — אין כיבוי"


def test_low_battery_stops_work_visibly_then_powers_off(conn):
    script = Script(OL, OB, OB_LB)
    m = monitor(conn, script)
    for _ in range(3):
        m.tick()
    assert [step[0] for step in script.order] == ["stop", "poweroff"]
    assert "סוללת ה-UPS נמוכה" in script.order[0][1]
    assert events(conn) == ["ups_on_battery", "ups_low_battery", "ups_shutdown"]
    assert m.health()[0] == "bad"


def test_forced_shutdown_from_the_nut_primary_powers_off(conn):
    script = Script({"ups.status": "OL FSD"})
    monitor(conn, script).tick()
    assert [step[0] for step in script.order] == ["stop", "poweroff"]


def test_low_battery_while_charging_on_mains_does_not_power_off(conn):
    script = Script({"ups.status": "OL CHRG LB", "battery.charge": "15"})
    monitor(conn, script).tick()
    assert script.order == []


def test_an_unreadable_ups_never_powers_the_server_off(conn):
    script = Script(ups.UpsError("אין חיבור"), ups.UpsError("אין חיבור"), OL)
    m = monitor(conn, script)
    m.tick()
    state, detail = m.health()
    assert state == "bad" and "לא נקרא" in detail and "אין חיבור" in detail
    m.tick()
    m.tick()
    assert script.order == []
    assert events(conn) == ["ups_unreachable", "ups_reachable"]
    assert m.health()[0] == "ok"


def test_a_failed_poweroff_is_said_and_retried(conn):
    script = Script(OB_LB, OB_LB)

    def broken():
        script.order.append(("poweroff",))
        raise RuntimeError("systemctl: Access denied")

    m = ups.UpsMonitor(conn, stop_work=script.stop_work, poweroff=broken, read=script.read)
    m.tick()
    state, detail = m.health()
    assert state == "bad" and "הכיבוי נכשל" in detail and "Access denied" in detail
    m.tick()
    assert [s[0] for s in script.order].count("poweroff") == 2
    assert events(conn).count("ups_low_battery") == 1
    assert "ups_shutdown_failed" in events(conn)


def test_health_without_a_ups_is_not_applicable(tmp_path):
    db = connect(tmp_path / "x.db")
    m = ups.UpsMonitor(db, stop_work=lambda r: [], poweroff=lambda: None, read=lambda t: OL)
    m.tick()
    assert m.health()[0] == "not_applicable"


def test_health_on_battery_is_amber_with_the_charge(conn):
    m = monitor(conn, Script(OB))
    m.tick()
    state, detail = m.health()
    assert state == "warn" and "החשמל נפל" in detail and "60%" in detail


# --- עצירה גלויה של מה שרץ -----------------------------------------------------------

def test_stop_all_work_fails_sessions_and_captures_by_name(server):
    from conftest import setup_classroom
    from test_capture import make_task, setup_build_machine

    ids = setup_classroom(server)
    opened = server["deploy"].post("/api/console/sessions", json={
        "group_id": ids["group"], "image_id": "img_7f3a91"})
    assert opened.status_code == 200, opened.text
    task = make_task(server, setup_build_machine(server)).json()
    conn = server["ctx"].conn
    conn.execute("UPDATE tasks SET state = 'running' WHERE id = ?", (task["id"],))
    conn.commit()

    stopped = ups.stop_all_work(server["ctx"], "סוללת ה-UPS נמוכה")
    session = conn.execute("SELECT id, state, failed_reason FROM sessions").fetchone()
    assert session["state"] == "failed" and session["failed_reason"] == "סוללת ה-UPS נמוכה"
    row = conn.execute("SELECT state, error FROM tasks WHERE id = ?", (task["id"],)).fetchone()
    assert (row["state"], row["error"]) == ("failed", "סוללת ה-UPS נמוכה")
    assert set(stopped) == {session["id"], task["id"]}
    assert events(conn).count("ups_work_stopped") == 2


# --- הגדרה מהקונסולה -----------------------------------------------------------------

class Host:
    def __init__(self, read=None) -> None:
        self.calls: list = []
        self._read = read or (lambda target: OL)

    def hooks(self) -> dict:
        return {"write": lambda files: self.calls.append(("write", dict(files))),
                "start_usb": lambda: self.calls.append(("start_usb",)),
                "stop_usb": lambda: self.calls.append(("stop_usb",)),
                "read": lambda target: (self.calls.append(("read", str(target))), self._read(target))[1],
                "sleep": lambda s: None}


def test_net_mode_writes_nothing_on_the_host(tmp_path):
    db = connect(tmp_path / "x.db")
    host = Host()
    result = ups.apply(db, "net", "rack@10.0.0.5", host.hooks(), "noc")
    assert result == {"read": "על חשמל · סוללה 100%", "error": None}
    assert host.calls == [("read", "rack@10.0.0.5")]
    assert ups.configured(db) == ("net", ups.Target("rack", "10.0.0.5"))


def test_usb_mode_writes_nut_and_reads_back_from_loopback(tmp_path):
    db = connect(tmp_path / "x.db")
    host = Host()
    ups.apply(db, "usb", "", host.hooks(), "noc")
    files = host.calls[0][1]
    assert "MODE=standalone" in files["nut.conf"]
    assert "driver = usbhid-ups" in files["ups.conf"] and "[imagesrv]" in files["ups.conf"]
    assert "LISTEN 127.0.0.1 3493" in files["upsd.conf"]
    assert host.calls[1:] == [("start_usb",), ("read", "imagesrv@127.0.0.1")]


def test_leaving_usb_turns_nut_off(tmp_path):
    db = connect(tmp_path / "x.db")
    set_settings(db, {ups.MODE: "usb"})
    host = Host()
    ups.apply(db, "none", "", host.hooks(), "noc")
    assert host.calls[0][0] == "write" and "MODE=none" in host.calls[0][1]["nut.conf"]
    assert host.calls[1] == ("stop_usb",)


def test_an_unreadable_ups_after_apply_is_returned_not_hidden(tmp_path):
    db = connect(tmp_path / "x.db")

    def refuse(target):
        raise ups.UpsError("ERR DRIVER-NOT-CONNECTED")
    result = ups.apply(db, "usb", "", Host(refuse).hooks(), "noc")
    assert result["read"] is None and "DRIVER-NOT-CONNECTED" in result["error"]


def test_the_console_endpoint_validates_and_applies(server):
    from server import console_ups

    admin = server["admin"]
    host = Host()
    # הראוטר של השרת נבנה עם ה-hooks האמיתיים — בונים אחד עם הזיופים, מול אותו ctx.
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(console_ups.create_ups_router(server["ctx"], host.hooks()))
    client = TestClient(app)
    client.cookies = admin.cookies
    assert client.post("/api/console/ups", json={"mode": "ups"}).status_code == 400
    assert client.post("/api/console/ups", json={"mode": "net", "target": "bad"}).status_code == 400
    ok = client.post("/api/console/ups", json={"mode": "net", "target": "rack@10.0.0.5"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["mode"] == "net" and ok.json()["applied"]["read"].startswith("על חשמל")
    assert server["deploy"].get("/api/console/ups").status_code == 403
    assert admin.get("/api/console/ups").json()["target"] == "rack@10.0.0.5"


def test_main_wires_the_monitor_and_the_health_row(monkeypatch):
    from types import SimpleNamespace

    from server import main

    started = []
    monkeypatch.setattr(ups.UpsMonitor, "run_forever",
                        lambda self, stop=None, interval=0: started.append(self))
    runtime = SimpleNamespace(health_hooks={}, conn=None, ctx=SimpleNamespace(ups=None))
    main._start_ups_monitor(runtime)
    for t in threading.enumerate():
        if t.name == "ups-monitor":
            t.join(timeout=5)
    assert started and runtime.ctx.ups is started[0]
    assert runtime.health_hooks["ups"] == started[0].health


def test_nut_is_installed_by_the_installer_and_carried_on_the_iso():
    """‏#1803: שרת קיים מקבל חבילה חדשה מכפתור העדכון רק כשהיא ב-packages.txt."""
    from test_installer_packages import installer_pkgs
    from test_iso_build import packages_txt

    for name in ("nut-client", "nut-server"):
        assert name in installer_pkgs(), f"{name} חסר ב-PKGS של המתקין"
        assert name in packages_txt(), f"{name} חסר ב-tools/iso/packages.txt"
