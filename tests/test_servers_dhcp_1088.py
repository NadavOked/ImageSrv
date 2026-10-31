"""‏#1088: כרטיס וילן השרתים = לקוח DHCP; הקונסולה נקשרת לפי **שם כרטיס**
ומאזינה מחדש בהחלפת כתובת; רשת ההפצה מוגדרת **מהקונסולה** ולא במתקין.

ארבע קבוצות, כל אחת עם הראיה שלה:

1. ‏`ifaddr` — שם כרטיס → כתובת (שלושה מצבים), הדוגם, וקובץ החכירות.
2. ‏`ports.Listeners.rebind` — על סוקט אמיתי (loopback), כמו #996.
3. ‏`main` — ‏`--console-host eth0` מגיע למאזין הקונסולה כ**כתובת**; בלי
   `--server-url` השרת עולה במצב "לא הוגדרה" או מהרשומה בקונסולה.
4. ‏`deploy_net` + הקונסולה — הדלקת ה-DHCP הראשונה משלימה את ההתקנה, ורק
   כשהכרטיס נושא את הכתובת; ‏health, המתקין, היחידה, ו-pairing לפי שם.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import socket
import stat
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

import server.app
import server.main
from server.net import deploy_net, dhcp, ifaddr, netcfg, netcfg_host, ports
from server.system import health

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

REPO = Path(__file__).resolve().parent.parent
INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
UNIT = REPO / "deploy" / "imagesrv-server.service"


# --- 1. ifaddr ------------------------------------------------------------------


@pytest.mark.parametrize("value,expected", [
    ("eth0", True), ("ens18", True), ("eth1.700", True),
    ("10.44.12.10", False), ("127.0.0.1", False), ("::1", False),
    ("0.0.0.0", False), ("localhost", False), ("", False), (None, False),
    ("eth 0", False), ("eth0\ninterface=eth1", False),
])
def test_is_interface_name(value, expected):
    assert ifaddr.is_interface_name(value) is expected


def _ip_json(name: str, *addrs: str) -> str:
    return json.dumps([{"ifname": name, "addr_info": [
        {"family": "inet", "local": a} for a in addrs]}])


def test_read_ipv4_three_states():
    """כתובת · אין כתובת (None) · הבדיקה נכשלה (חריגה) — לא מקופלים."""
    assert ifaddr.read_ipv4("eth0", run=lambda a: _ip_json("eth0", "10.1.2.3")) == "10.1.2.3"
    assert ifaddr.read_ipv4("eth0", run=lambda a: _ip_json("eth0")) is None

    def broken(args):
        raise ifaddr.AddressLookupError("Device \"eth0\" does not exist")
    with pytest.raises(ifaddr.AddressLookupError):
        ifaddr.read_ipv4("eth0", run=broken)
    with pytest.raises(ifaddr.AddressLookupError):
        ifaddr.read_ipv4("eth0", run=lambda a: "not json")


def test_ipv4_of_prefers_a_global_non_deprecated_address():
    """‏#1121 ס' 5: חידוש lease = שתי כתובות לרגע, הישנה deprecated — לוקחים
    את החדשה; ‏scope link (169.254) נסוג מפני global; deprecated לבדה = None."""
    def nic(*addrs):
        return [{"ifname": "eth0", "addr_info": [{"family": "inet", **a} for a in addrs]}]
    old = {"local": "10.1.2.3", "scope": "global", "deprecated": True}
    new = {"local": "10.1.2.9", "scope": "global"}
    link = {"local": "169.254.7.7", "scope": "link"}
    assert ifaddr.ipv4_of("eth0", nic(old, new)) == "10.1.2.9"
    assert ifaddr.ipv4_of("eth0", nic(new, old)) == "10.1.2.9"
    assert ifaddr.ipv4_of("eth0", nic(link, new)) == "10.1.2.9"
    assert ifaddr.ipv4_of("eth0", nic(link)) == "169.254.7.7"
    assert ifaddr.ipv4_of("eth0", nic(old)) is None
    assert ifaddr.ipv4_of("eth0", nic({"local": "10.5.5.5"})) == "10.5.5.5"   # בלי scope — כמו קודם


def test_read_ipv4_asks_ip_for_that_device_only():
    seen = []
    ifaddr.read_ipv4("ens18", run=lambda a: seen.append(a) or _ip_json("ens18", "10.9.9.9"))
    assert seen == [["-4", "addr", "show", "dev", "ens18"]]


def test_watcher_reports_changes_including_first_lease_and_retries_failed_rebind():
    state = {"addr": None}
    changes = []
    ok = {"value": True}

    def on_change(old, new):
        changes.append((old, new))
        return ok["value"]

    w = ifaddr.AddressWatcher("eth0", current=None, on_change=on_change,
                              lookup=lambda name: state["addr"])
    assert w.check_once() is False and changes == []          # None → None: אין שינוי
    state["addr"] = "10.1.2.3"
    assert w.check_once() is True and changes == [(None, "10.1.2.3")]
    assert w.check_once() is False                            # יציב — לא מדווח שוב
    # rebind שנכשל: הכתובת הידועה נשארת הישנה, והדגימה הבאה מנסה שוב.
    ok["value"] = False
    state["addr"] = "10.1.2.9"
    assert w.check_once() is False and w.current == "10.1.2.3"
    ok["value"] = True
    assert w.check_once() is True and changes[-1] == ("10.1.2.3", "10.1.2.9")


def test_watcher_lookup_failure_is_not_an_address_change():
    """‏`ip` שנפל פעם אחת אינו "אין כתובת" — המאזין נשאר על הכתובת."""
    errors, changes = [], []

    def failing(name):
        raise ifaddr.AddressLookupError("boom")
    w = ifaddr.AddressWatcher("eth0", current="10.1.2.3",
                              on_change=lambda o, n: changes.append((o, n)),
                              lookup=failing, on_error=errors.append)
    assert w.check_once() is False
    assert changes == [] and errors == ["boom"] and w.current == "10.1.2.3"


def test_watcher_thread_survives_an_exception_in_on_change():
    """‏#1121 ס' 6: `on_change` שזרק (למשל journal על DB נעול) אינו הורג את
    הדוגם — השגיאה נרשמת, והדגימה הבאה מדווחת את השינוי."""
    import time
    errors, changes = [], []
    state = {"addr": "10.1.2.3", "raise": True}

    def on_change(old, new):
        if state["raise"]:
            state["raise"] = False
            raise RuntimeError("database is locked")
        changes.append((old, new))
        return True

    w = ifaddr.AddressWatcher("eth0", current=None, on_change=on_change,
                              lookup=lambda name: state["addr"],
                              on_error=errors.append, interval=0.01).start()
    deadline = time.monotonic() + 3
    while not changes and time.monotonic() < deadline:
        time.sleep(0.01)
    w.stop()
    assert w._thread.is_alive() or changes            # התהליכון לא מת על החריגה
    assert changes == [(None, "10.1.2.3")]
    assert errors and "database is locked" in errors[0]


LEASES = """
lease {
  interface "eth0";
  fixed-address 10.1.2.3;
  option dhcp-server-identifier 10.1.0.1;
  option host-name "imagesrv";
  renew 4 2026/09/18 06:00:00;
  expire 4 2026/09/18 12:00:00;
}
lease {
  interface "eth0";
  fixed-address 10.1.2.9;
  option dhcp-server-identifier 10.1.0.1;
  expire 4 2026/09/18 18:00:00;
}
"""


def test_dhclient_leases_take_the_last_lease():
    lease = ifaddr.parse_dhclient_leases(LEASES)
    assert lease.address == "10.1.2.9" and lease.server == "10.1.0.1"
    assert lease.expires.year == 2026 and lease.expires.hour == 18
    assert ifaddr.parse_dhclient_leases("") is None
    never = ifaddr.parse_dhclient_leases("lease { fixed-address 1.2.3.4; expire never; }")
    assert never.expires is None and never.expires_text() == "ללא תוקף"


def test_dhclient_lease_reads_the_interface_file(tmp_path):
    (tmp_path / "dhclient.eth0.leases").write_text(LEASES, encoding="utf-8")
    assert ifaddr.dhclient_lease("eth0", tmp_path).address == "10.1.2.9"
    assert ifaddr.dhclient_lease("eth1", tmp_path) is None


def test_servers_nic_status_three_states():
    lease = ifaddr.parse_dhclient_leases(LEASES)
    assert ifaddr.servers_nic_status("eth0", None, None, lookup_failed="ip נפל")[0] == "unknown"
    assert ifaddr.servers_nic_status("eth0", None, None)[0] == "warn"
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.9", lease, hostname="imagesrv")
    assert state == "ok" and "DHCP" in detail and "פג ב-" in detail and "imagesrv" in detail
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.9", None)
    assert state == "ok" and "סטטי" in detail
    state, detail = ifaddr.servers_nic_status("eth0", "10.1.2.3", lease)
    assert state == "warn" and "10.1.2.9" in detail


# --- 2. Listeners.rebind — סוקט אמיתי --------------------------------------------


async def _app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok"})


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _can_bind(host: str, port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
        return True


def _can_connect(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect((host, port))
        except OSError:
            return False
        return True


def _factories(host: str, port: int):
    return [lambda: uvicorn.Server(uvicorn.Config(
        _app, host=host, port=port, log_level="warning", lifespan="off"))]


async def _wait(predicate, timeout: float = 5.0) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


@pytest.fixture()
def second_loopback():
    if not _can_bind("127.0.0.2", 0):
        pytest.skip("127.0.0.2 אינו ניתן לקשירה במכונה הזו")
    return "127.0.0.2"


def test_rebind_moves_the_listener_to_the_new_address(second_loopback):
    """‏#1088: אותו פורט, כתובת אחרת — הישנה משוחררת (bind מצליח), החדשה
    עונה (connect מצליח). ראיה בסוקט, לא בהגדרה."""
    port = _free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_console", _factories("127.0.0.1", port), port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve())
        await _wait(lambda: mgr.is_open("http_console"))
        assert _can_connect("127.0.0.1", port)

        await mgr.rebind("http_console", hosts=[second_loopback],
                         factories=_factories(second_loopback, port))
        await _wait(lambda: mgr.is_open("http_console"))
        assert _can_bind("127.0.0.1", port), "הכתובת הישנה עדיין תפוסה"
        assert _can_connect(second_loopback, port)
        assert mgr.spec("http_console")["hosts"] == [second_loopback]
        assert [b[0] for b in mgr.bound("http_console")] == [second_loopback]
        mgr.stop()
        await task

    asyncio.run(scenario())


def test_queued_rebinds_only_the_last_address_takes_effect(second_loopback):
    """‏#1121 ס' 6: שני rebinds בתור מאחורי הנעילה — הישן (127.0.0.1) מוותר,
    והמאזין עולה פעם אחת על הכתובת האחרונה."""
    port = _free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_console", _factories("127.0.0.1", port), port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve())
        await _wait(lambda: mgr.is_open("http_console"))
        opened = []
        real_open = mgr._open

        async def counting_open(port_id):
            opened.append(mgr.spec(port_id)["hosts"][0])
            await real_open(port_id)
        mgr._open = counting_open
        async with mgr._lock:                                 # שניהם ממתינים מאחורינו
            stale = asyncio.ensure_future(mgr.rebind(
                "http_console", hosts=["127.0.0.1"], factories=_factories("127.0.0.1", port)))
            latest = asyncio.ensure_future(mgr.rebind(
                "http_console", hosts=[second_loopback],
                factories=_factories(second_loopback, port)))
            await asyncio.sleep(0.05)
        await asyncio.gather(stale, latest)
        await _wait(lambda: mgr.is_open("http_console"))
        assert opened == [second_loopback]
        assert mgr.spec("http_console")["hosts"] == [second_loopback]
        assert _can_connect(second_loopback, port)
        mgr.stop()
        await task

    asyncio.run(scenario())


def test_rebind_does_not_open_a_listener_the_operator_closed(second_loopback):
    port = _free_port()

    async def scenario():
        mgr = ports.Listeners(want_open=lambda p: False)
        mgr.add("http_console", _factories("127.0.0.1", port), port=port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve())
        await asyncio.sleep(0.1)
        assert not mgr.is_open("http_console")
        await mgr.rebind("http_console", hosts=[second_loopback],
                         factories=_factories(second_loopback, port))
        await asyncio.sleep(0.1)
        assert not mgr.is_open("http_console")
        assert _can_bind(second_loopback, port)
        mgr.stop()
        await task

    asyncio.run(scenario())


# --- 3. main ---------------------------------------------------------------------


def test_console_host_by_name_resolves_and_watches(tmp_path, monkeypatch):
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, run=None: "10.1.2.3")
    configs: dict[str, list[dict]] = {}
    captured: dict = {}
    started = []
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: (captured.update(kwargs=k), "rt")[1])
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    # #1310: the primary alive listener has its own test (test_primary_alive.py)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr(ifaddr.AddressWatcher, "start", lambda self: started.append(self) or self)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "eth0"])
    server.main.main()

    console_hosts = sorted(c["host"] for c in configs["console"])
    assert console_hosts == ["10.1.2.3", "127.0.0.1"]          # הכתובת, לא "eth0"
    assert all("ssl_certfile" in c for c in configs["console"])  # TLS דלוק על כרטיס
    assert [c["host"] for c in configs["agent"]] == ["10.44.12.10"]
    assert [w.name for w in started] == ["eth0"] and started[0].current == "10.1.2.3"
    # ‏SAN: הכתובת הנוכחית + שם המארח, לא שם הכרטיס.
    tls = captured["kwargs"]["console_tls"]
    assert "10.1.2.3" in tls.sans and "eth0" not in tls.sans
    assert socket.gethostname() in tls.sans
    # ‏health מקבל את שורת כתובת השרתים.
    assert callable(captured["kwargs"]["health_hooks"]["servers_nic"])


def test_console_host_by_name_without_a_lease_yet_is_loopback_only(tmp_path, monkeypatch):
    monkeypatch.setattr(ifaddr, "read_ipv4", lambda name, run=None: None)
    configs = {}
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: "rt")
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    # #1310: the primary alive listener has its own test (test_primary_alive.py)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    started = []
    monkeypatch.setattr(ifaddr.AddressWatcher, "start", lambda self: started.append(self) or self)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "ens18"])
    server.main.main()
    assert [c["host"] for c in configs["console"]] == ["127.0.0.1"]
    assert started and started[0].current is None                 # None→כתובת ייחשב שינוי


def test_console_host_by_name_when_ip_fails_is_a_parser_error(tmp_path, monkeypatch, capsys):
    def broken(name, run=None):
        raise ifaddr.AddressLookupError("Device \"eth9\" does not exist")
    monkeypatch.setattr(ifaddr, "read_ipv4", broken)
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: pytest.fail("הגיע ל-runtime"))
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img"),
        "--console-host", "eth9"])
    with pytest.raises(SystemExit) as exc:
        server.main.main()
    assert exc.value.code == 2
    assert "eth9" in capsys.readouterr().err


def _run_main_no_url(tmp_path, monkeypatch, interface_for=None):
    configs: dict = {}
    captured: dict = {}
    monkeypatch.setattr(server.app, "create_runtime",
                        lambda *a, **k: (captured.update(args=a, kwargs=k), "rt")[1])
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(uvicorn, "Config", lambda app, **kw: configs.setdefault(app, []).append(kw) or object())
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    # #1310: the primary alive listener has its own test (test_primary_alive.py)
    monkeypatch.setattr(server.main, "_primary_alive", lambda *a, **k: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", interface_for or (lambda url: None))
    monkeypatch.setattr(server.main, "_address_for_interface", lambda name: "10.44.9.10")
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "", "--console-tls", "off",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    server.main.main()
    return configs, captured


def test_without_server_url_and_without_a_console_record_the_server_is_unconfigured(
        tmp_path, monkeypatch, capsys):
    """התקנה נקייה (#1088): היחידה מעבירה ${IMAGESRV_URL} ריק. השרת עולה —
    סוכן על loopback בלבד, ומצב ההפצה נאמר: source=none."""
    configs, captured = _run_main_no_url(tmp_path, monkeypatch)
    assert captured["args"][2] == "http://127.0.0.1:8080"
    assert [c["host"] for c in configs["agent"]] == ["127.0.0.1"]
    assert captured["kwargs"]["interface"] is None
    deploy = captured["kwargs"]["deploy"]
    assert deploy.state.source == "none" and not deploy.state.configured
    assert "not configured" in capsys.readouterr().out


def test_without_server_url_the_console_record_wins(tmp_path, monkeypatch):
    """מה שהקונסולה רשמה בהדלקת ה-DHCP הראשונה הוא הכתובת בעלייה הבאה."""
    from server.db import connect
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    conn.close()
    configs, captured = _run_main_no_url(tmp_path, monkeypatch,
                                         interface_for=lambda url: "eth1")
    assert captured["args"][2] == "http://10.44.9.10:8080"
    assert captured["kwargs"]["interface"] == "eth1"
    assert [c["host"] for c in configs["agent"]] == ["10.44.9.10"]
    assert captured["kwargs"]["deploy"].state.source == "console"


def test_a_console_record_whose_address_left_the_nic_degrades_instead_of_dying(
        tmp_path, monkeypatch, capsys):
    """הכרטיס כבר אינו נושא את הכתובת — נפילה הייתה משאירה את המנהל בלי
    קונסולה; השרת עולה במצב none ואומר למה."""
    from server.db import connect
    data = tmp_path / "data"
    data.mkdir()
    conn = connect(data / "imagesrv.db")
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    conn.close()

    def no_nic(url):
        raise server.main.InterfaceDetectionError("אף כרטיס אינו נושא את 10.44.9.10")
    configs, captured = _run_main_no_url(tmp_path, monkeypatch, interface_for=no_nic)
    assert captured["args"][2] == "http://127.0.0.1:8080"
    assert captured["kwargs"]["deploy"].state.source == "none"
    assert "back to 'not configured'" in capsys.readouterr().out


def test_server_url_flag_still_wins_and_is_still_ip_only(tmp_path, monkeypatch):
    """‏#498 לא נשבר: עם הדגל — אותה בדיקה, אותה דחייה."""
    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: pytest.fail("הגיע ל-runtime"))
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://imagesrv.college:8080",
        "--data-dir", str(tmp_path / "data"), "--images", str(tmp_path / "img")])
    with pytest.raises(SystemExit):
        server.main.main()


# --- 4. deploy_net + הקונסולה -------------------------------------------------------


def test_resolve_precedence(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    assert deploy_net.resolve(conn, "http://1.2.3.4:8080", "eth0").source == "cli"
    assert deploy_net.resolve(conn, None, None).source == "none"
    deploy_net.record(conn, "eth1", "http://10.44.9.10:8080")
    st = deploy_net.resolve(conn, None, None)
    assert (st.source, st.interface, st.url) == ("console", "eth1", "http://10.44.9.10:8080")
    assert deploy_net.resolve(conn, "http://1.2.3.4:8080", None).source == "cli"   # הדגל גובר
    assert st.public()["hint"] is None
    assert "בחר כרטיס" in deploy_net.DeployState("none", None, None).public()["hint"]


def test_write_grub_cfg_uses_the_installers_generator(tmp_path):
    from boot.grub_menu import GrubConfig, render_bootstrap
    assert deploy_net.write_grub_cfg("http://10.44.9.10:8080", tmp_path) is None
    text = (tmp_path / "grub" / "grub.cfg").read_text(encoding="ascii")
    assert text == render_bootstrap(GrubConfig(server_base="http://10.44.9.10:8080"))
    assert "set imagesrv_fallback=10.44.9.10:8080" in text


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX umask and modes")
@pytest.mark.parametrize("existing", [False, True], ids=["new", "existing-0600"])
def test_1817_write_grub_cfg_is_0644_under_private_umask(tmp_path, existing):
    from boot.grub_menu import GrubConfig, render_bootstrap

    path = tmp_path / "grub" / "grub.cfg"
    path.parent.mkdir()
    if existing:
        path.write_text("old config", encoding="ascii")
        path.chmod(0o600)
    previous_umask = os.umask(0o077)
    try:
        error = deploy_net.write_grub_cfg("http://10.44.9.10:8080", tmp_path)
    finally:
        os.umask(previous_umask)
    assert error is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert path.read_text(encoding="ascii") == render_bootstrap(
        GrubConfig(server_base="http://10.44.9.10:8080"))


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX umask and modes")
def test_1817_write_grub_cfg_creates_a_traversable_grub_dir_under_private_umask(tmp_path):
    previous_umask = os.umask(0o077)
    try:
        error = deploy_net.write_grub_cfg("http://10.44.9.10:8080", tmp_path)
    finally:
        os.umask(previous_umask)
    assert error is None
    assert stat.S_IMODE((tmp_path / "grub").stat().st_mode) == 0o755
    assert stat.S_IMODE((tmp_path / "grub" / "grub.cfg").stat().st_mode) == 0o644


def test_regenerate_firewall_skips_when_the_installer_wrote_none(tmp_path):
    assert deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0",
                                          conf=tmp_path / "nftables.conf") is None
    assert not (tmp_path / "nftables.conf").exists()


def _complete_ctx(tmp_path, hooks, servers_interface="eth0"):
    return deploy_net.DeployContext(
        state=deploy_net.DeployState("none", None, None), agent_port=8080,
        tftp_root=tmp_path / "tftp", repo_dir=REPO, servers_interface=servers_interface,
        primary_ip=None, hooks=hooks)


def _complete_hooks(calls, *, fw="", restart=None):
    return {
        "write_grub_cfg": lambda url, root: calls.append(("grub", url, root)) or None,
        "enable_dnsmasq": lambda: calls.append(("dnsmasq",)) or None,
        "firewall": lambda repo, deploy_if, servers_if, primary_ip: calls.append(
            ("fw", deploy_if, servers_if, primary_ip)) or (fw or None),
        "restart_server": lambda: calls.append(("restart",)) or restart,
    }


def _fake_nft(calls, *, load_fails=False):
    """‏`_run` מדומה: המחולל מחזיר ruleset, ‏`nft -c` עובר, ‏`nft -f` על
    ה-candidate נכשל לפי `load_fails`; ‏`nft -f` על הקובץ הישן (החזרה) עובר."""
    def run(cmd, **kw):
        calls.append(list(cmd))
        if cmd[0] == "sh":
            return True, "table inet imagesrv { }\n", ""
        if cmd[:2] == ["nft", "-c"]:
            return True, "", ""
        if cmd[:2] == ["nft", "-f"] and cmd[2].endswith(".candidate"):
            return (False, "", "Error: could not process rule") if load_fails else (True, "", "")
        if cmd[:2] == ["nft", "-f"]:
            return True, "", ""
        raise AssertionError(cmd)
    return run


def test_regenerate_firewall_replaces_the_file_only_after_the_kernel_took_it(tmp_path, monkeypatch):
    """‏#1121 ס' 2: הקובץ הישן נדרס רק אחרי `nft -f` שהצליח — ודרך candidate."""
    conf = tmp_path / "nftables.conf"
    conf.write_text("# ImageSrv old\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(deploy_net, "_run", _fake_nft(calls))
    assert deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0",
                                          conf=conf) is None
    assert conf.read_text(encoding="utf-8") == "table inet imagesrv { }\n"
    nft = [c for c in calls if c[0] == "nft"]
    assert nft[0][:2] == ["nft", "-c"] and nft[1][:2] == ["nft", "-f"]
    assert nft[1][2].endswith(".candidate")                 # לא הקובץ החי
    assert not conf.with_name("nftables.conf.candidate").exists()


def test_regenerate_firewall_keeps_the_old_file_and_restores_the_old_ruleset_when_load_fails(
        tmp_path, monkeypatch):
    """‏`nft -c` עובר ו-`nft -f` נכשל → /etc/nftables.conf הישן נשאר, ה-ruleset
    הישן נטען חזרה ממנו, וה-candidate אינו נשאר על הדיסק."""
    conf = tmp_path / "nftables.conf"
    conf.write_text("# ImageSrv old\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(deploy_net, "_run", _fake_nft(calls, load_fails=True))
    err = deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if="eth0", conf=conf)
    assert err and "could not process rule" in err and "הוחזר" in err
    assert conf.read_text(encoding="utf-8") == "# ImageSrv old\n"
    assert calls[-1] == ["nft", "-f", str(conf)]                # ההחזרה — מהקובץ הישן
    assert not conf.with_name("nftables.conf.candidate").exists()


@pytest.mark.parametrize("services", [None, [("eth1", ["netboot"]), ("eth0", ["console"])]])
def test_proxy_1829_regeneration_passes_interface_flags(tmp_path, monkeypatch, services):
    conf = tmp_path / "nftables.conf"
    conf.write_text("# ImageSrv old\n", encoding="utf-8")
    calls = []
    monkeypatch.setattr(deploy_net, "_run", _fake_nft(calls))
    error = deploy_net.regenerate_firewall(
        REPO, deploy_if="eth1", servers_if="eth0", conf=conf,
        primary_ip="10.1.2.3", services=services, proxy_interfaces=["eth1", "eth2"])
    assert error is None
    assert calls[0][-6:] == ["--primary-ip", "10.1.2.3", "--proxy-if", "eth1", "--proxy-if", "eth2"]
    assert ("--per-nic" in calls[0]) is (services is not None)
    assert conf.read_text(encoding="utf-8") == "table inet imagesrv { }\n"


@pytest.mark.parametrize("classrooms", [False, True])
def test_proxy_1829_saved_modes_and_installer_use_the_same_edition_gate(
        tmp_path, monkeypatch, capsys, classrooms):
    from server.core import capabilities
    from server.db import connect, set_setting
    monkeypatch.setattr(capabilities, "CLASSROOMS", classrooms)
    db_path = tmp_path / "imagesrv.db"
    conn = connect(db_path)
    for cfg in (dhcp.InterfaceConfig("eth1", proxy=True),
                dhcp.InterfaceConfig("eth2", enabled=True),
                dhcp.InterfaceConfig("eth3", enabled=True, proxy=True),
                dhcp.InterfaceConfig("eth4")):
        set_setting(conn, dhcp.SETTING_PREFIX + cfg.name, cfg.to_json())
    assert deploy_net.firewall_proxy_interfaces(conn) == (["eth1"] if classrooms else [])
    text = INSTALLER.read_text(encoding="utf-8")
    snippet = text.split("<<'PROXYFWEOF'\n", 1)[1].split("\nPROXYFWEOF", 1)[0]
    monkeypatch.setattr("sys.argv", ["-", str(REPO), str(db_path)])
    exec(compile(snippet, str(INSTALLER), "exec"), {})
    assert capsys.readouterr().out == ("eth1\n" if classrooms else "")
    assert 'PROXY_FOR_NFT="$(console_proxy_interfaces_from_db)" || die' in text
    assert 'NFT_ARGS+=(--proxy-if "$PROXY_NIC")' in text
    conn.close()


@pytest.mark.parametrize("raw,name", [("{broken", "eth1"), ('{"proxy": true}', "eth1\neth0")])
def test_proxy_1829_unreadable_or_unsafe_settings_stop_completion(tmp_path, monkeypatch, raw, name):
    from server.core import capabilities
    from server.db import connect, set_setting
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, dhcp.SETTING_PREFIX + name, raw)
    calls = []
    result = deploy_net.complete(_complete_ctx(tmp_path, _complete_hooks(calls)), conn,
                                 interface="eth1", server_ip="10.44.9.10")
    assert result["ok"] is False and "proxy" in result["errors"][0]
    assert calls == []
    assert deploy_net.stored(conn) == (None, None)
    conn.close()


def test_proxy_1829_completion_preserves_existing_proxy_rules(tmp_path, monkeypatch):
    from server.core import capabilities
    from server.db import connect, set_setting
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    conn = connect(tmp_path / "imagesrv.db")
    set_setting(conn, "dhcp:eth2", dhcp.InterfaceConfig("eth2", proxy=True).to_json())
    calls, firewalls = [], []
    hooks = _complete_hooks(calls)
    hooks["firewall"] = lambda repo, **kw: firewalls.append(kw)
    ctx = _complete_ctx(tmp_path, hooks)
    ctx.services = lambda interface: [(interface, ["netboot"]), ("eth2", ["netboot"])]
    result = deploy_net.complete(ctx, conn, interface="eth1", server_ip="10.44.9.10")
    assert result["ok"] is True
    assert firewalls[0]["proxy_interfaces"] == ["eth2"]
    assert firewalls[0]["services"] == [("eth1", ["netboot"]), ("eth2", ["netboot"])]
    conn.close()


def test_restart_is_scheduled_under_a_unique_transient_unit(monkeypatch):
    """‏#1121 ס' 3: שתי השלמות בתוך חלון ה-3 ש' — שתי יחידות שונות, לא
    "Unit imagesrv-deploy-restart already exists"."""
    calls = []
    monkeypatch.setattr(deploy_net, "_run", lambda cmd, **kw: calls.append(cmd) or (True, "", ""))
    assert deploy_net.restart_server_later() is None
    assert deploy_net.restart_server_later() is None
    units = [next(a for a in c if a.startswith("--unit=")) for c in calls]
    assert all(u.startswith("--unit=imagesrv-deploy-restart") for u in units)
    assert units[0] != units[1]
    assert all("--collect" in c and c[-3:] == ["systemctl", "restart", "imagesrv-server"]
               for c in calls)


def test_complete_applies_every_step_and_commits_last(tmp_path):
    """‏#1121: ‏`deploy:*` נרשם **אחרי** שכל השלבים הצליחו, לא לפניהם."""
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    calls = []
    seen_at_restart = {}
    hooks = _complete_hooks(calls)
    hooks["restart_server"] = lambda: (calls.append(("restart",)),
                                       seen_at_restart.update(stored=deploy_net.stored(conn)),
                                       None)[2]
    result = deploy_net.complete(_complete_ctx(tmp_path, hooks), conn,
                                 interface="eth1", server_ip="10.44.9.10")
    assert [c[0] for c in calls] == ["grub", "dnsmasq", "fw", "restart"]
    assert calls[2] == ("fw", "eth1", "eth0", None)
    assert seen_at_restart["stored"] == (None, None)        # עוד לא נרשם בזמן התזמון
    assert deploy_net.stored(conn) == ("eth1", "http://10.44.9.10:8080")
    assert result == {"ok": True, "url": "http://10.44.9.10:8080", "interface": "eth1",
                      "errors": [], "restarting": True, "recorded": True}


def test_complete_failure_mid_way_stops_without_restart_and_without_commit(tmp_path):
    """חומת האש נפלה → אין אתחול, אין `deploy:*`, והשגיאה נקובה בשם."""
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    calls = []
    result = deploy_net.complete(_complete_ctx(tmp_path, _complete_hooks(calls, fw="nft נפל")),
                                 conn, interface="eth1", server_ip="10.44.9.10")
    assert [c[0] for c in calls] == ["grub", "dnsmasq", "fw"]     # בלי restart
    assert deploy_net.stored(conn) == (None, None)
    assert result["ok"] is False and result["errors"] == ["חומת אש: nft נפל"]
    assert result["restarting"] is False and result["recorded"] is False


def test_complete_restart_failure_is_not_committed_either(tmp_path):
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    calls = []
    result = deploy_net.complete(
        _complete_ctx(tmp_path, _complete_hooks(calls, restart="systemd-run חסר")),
        conn, interface="eth1", server_ip="10.44.9.10")
    assert deploy_net.stored(conn) == (None, None)
    assert result["ok"] is False and result["errors"] == ["אתחול: systemd-run חסר"]


def test_complete_without_a_servers_nic_stops_only_when_a_firewall_is_installed(tmp_path):
    """‏`--console-host` שאינו שם כרטיס: עם חומת אש מותקנת — עוצר בשם; בלי
    חומת אש (‏--no-firewall) — אין מה לחולל, וההשלמה ממשיכה."""
    conf = tmp_path / "nftables.conf"
    assert deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if=None,
                                          conf=conf) is None
    conf.write_text("# ImageSrv\n", encoding="utf-8")
    err = deploy_net.regenerate_firewall(REPO, deploy_if="eth1", servers_if=None, conf=conf)
    assert err and "כרטיס השרתים אינו ידוע" in err
    assert conf.read_text(encoding="utf-8") == "# ImageSrv\n"


def test_render_adds_the_deploy_interface_line_when_set_from_the_console():
    """‏R20-F1 במסירה: imagesrv.conf של המתקין בלי interface= — השורה
    נכתבת בקובץ של הקונסולה, גם בלי אף DHCP דלוק, ולא פעמיים."""
    text = dhcp.render([], deploy_interface="eth1")
    assert "bind-interfaces" in text and "interface=eth1" in text
    good = dhcp.InterfaceConfig("eth1", enabled=True, range_start="10.44.9.50",
                                range_end="10.44.9.200", server_ip="10.44.9.10")
    text = dhcp.render([good], deploy_interface="eth1")
    assert text.count("interface=eth1") == 1 and text.count("bind-interfaces") == 1
    assert "interface=" not in dhcp.render([])                 # בלי הפצה — כמו קודם
    with pytest.raises(ValueError):
        dhcp.render([], deploy_interface="eth1\ninterface=eth0")


def test_render_pins_tftp_to_loopback_when_no_file_names_an_interface():
    """‏#1078-S / R20-F5: dnsmasq בלי אף `interface=` מאזין על **כל**
    הכרטיסים חוץ מה-`except-interface` — ו-`restart` (שהקונסולה מריצה)
    מעלה גם יחידה כבויה. ‏`pin_tftp` = אין קובץ אחר שנושא את כרטיס ההפצה
    (‏source ≠ cli), ולכן בלי שם — loopback בלבד, לא כרטיס השרתים."""
    proxied = dhcp.InterfaceConfig("eth1", proxy=True, server_ip="10.44.9.10")
    good = dhcp.InterfaceConfig("eth1", enabled=True, range_start="10.44.9.50",
                                range_end="10.44.9.200", server_ip="10.44.9.10")
    # רשת ההפצה טרם הוגדרה, ו-69 הודלק בדף הפורטים
    text = dhcp.render([], tftp_root="/srv/tftp", pin_tftp=True)
    assert "enable-tftp" in text
    assert "interface=lo" in text.splitlines() and "bind-interfaces" in text
    # כרטיס ההפצה במצב proxy: הראשי מוציא אותו, ולא מאזין על השאר
    text = dhcp.render([proxied], tftp_root="/srv/tftp", deploy_interface="eth1",
                       pin_tftp=True)
    assert "except-interface=eth1" in text and "interface=lo" in text.splitlines()
    # שומרים: כרטיס בשם — אין צורך ב-lo, והתוכן כמו קודם
    for named in (dhcp.render([], tftp_root="/srv/tftp", deploy_interface="eth1",
                              pin_tftp=True),
                  dhcp.render([good], tftp_root="/srv/tftp", pin_tftp=True)):
        assert "interface=lo" not in named.splitlines()
    assert "interface=" not in dhcp.render([], pin_tftp=True)         # 69 כבוי
    assert "interface=" not in dhcp.render([], tftp_root="/srv/tftp")  # cli — כמו קודם


@pytest.mark.parametrize("source,pinned", [("none", True), ("console", True),
                                            ("cli", False)])
def test_render_dnsmasq_pins_tftp_unless_the_unit_names_the_deploy_nic(
        tmp_path, images_root, clock, source, pinned):
    """‏cli = המתקין כתב `interface=<הפצה>` ב-imagesrv.conf (המעבדה) — לא
    נוגעים בנתיב שעובד. ‏console/none = הקובץ של הקונסולה הוא היחיד, ובלי
    שם בו dnsmasq היה מגיש TFTP על כרטיס השרתים."""
    from server.console import console_dhcp
    from server.app import create_app
    from server.db import set_setting
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080",
                     now_fn=clock)
    conn = app.state.ctx.conn
    set_setting(conn, dhcp.SETTING_PREFIX + "eth1",
                dhcp.InterfaceConfig("eth1", proxy=True, server_ip="10.44.9.10").to_json())
    named = source != "none"
    deploy = deploy_net.DeployContext(
        state=deploy_net.DeployState(source, "eth1" if named else None,
                                     "http://10.44.9.10:8080" if named else None),
        agent_port=8080, tftp_root=Path("/srv/tftp"), repo_dir=REPO)
    main_text, _proxy_text = console_dhcp.render_dnsmasq(app.state.ctx, deploy)
    conn.close()
    assert "enable-tftp" in main_text
    assert ("interface=lo" in main_text.splitlines()) is pinned


GOOD = dict(
    enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
    netmask="255.255.255.0", gateway="10.44.9.1", dns=["10.44.0.5"],
    lease="12h", server_ip="10.44.9.10",
)


@pytest.fixture()
def console(tmp_path: Path, images_root: Path, clock):
    """שרת עם שני כרטיסים; eth1 נושא את 10.44.9.10, eth0 בלי הכתובת הזו."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    fake = {
        "interfaces": [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00", "addresses": ["10.1.2.3/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01", "addresses": ["10.44.9.10/24"]},
        ],
        "applied": [], "deploy_calls": [],
    }
    hooks = {
        "interfaces": lambda: fake["interfaces"],
        "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: fake["applied"].append(text) or None,
        "apply_proxy": lambda text, active: None,
        "read_active_conf": lambda: "", "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    deploy_hooks = {
        "write_grub_cfg": lambda url, root: fake["deploy_calls"].append(("grub", url)) or None,
        "enable_dnsmasq": lambda: fake["deploy_calls"].append(("dnsmasq",)) or None,
        "firewall": lambda repo, **kw: fake["deploy_calls"].append(("fw", kw)) or fake.get("fw_error"),
        "restart_server": lambda: fake["deploy_calls"].append(("restart",)) or None,
    }

    def build(source: str):
        state = (deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080") if source == "cli"
                 else deploy_net.DeployState("none", None, None))
        ctx = deploy_net.DeployContext(state=state, agent_port=8080, tftp_root=tmp_path / "tftp",
                                       repo_dir=REPO, servers_interface="eth0", hooks=deploy_hooks)
        app = create_app(tmp_path / f"data-{source}", images_root, "http://127.0.0.1:8080",
                         now_fn=clock, dhcp_hooks=hooks, deploy=ctx)
        users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
        client = TestClient(app)
        client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
        return client, app.state.ctx, ctx
    return build, fake


@pytest.mark.parametrize("per_nic", [False, True])
def test_proxy_1829_console_toggle_regenerates_and_removes_rule(console, monkeypatch, per_nic):
    from server.core import capabilities
    from server.db import set_setting
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    build, fake = console
    client, ctx, _deploy = build("cli")
    if per_nic:
        from server.net import nic_services
        set_setting(ctx.conn, nic_services.SETTING_PREFIX + "eth1", json.dumps({"services": ["netboot"]}))
    for proxy in (True, False):
        response = client.put("/api/console/net/interfaces/eth1", json={
            "proxy": proxy, "server_ip": "10.44.9.10", "confirm": "eth1",
            "confirm_proxy_broken": True})
        assert response.status_code == 200, response.text
        assert response.json()["ok"] is True, response.text
        fw = [args for kind, args in fake["deploy_calls"] if kind == "fw"]
        assert len(fw) == (1 if proxy else 2)
        assert fw[-1].get("proxy_interfaces", []) == (["eth1"] if proxy else [])
        assert ("services" in fw[-1]) is per_nic


@pytest.mark.parametrize("failure", ["return", "raise", "missing"])
def test_proxy_1829_console_reports_firewall_failure(console, monkeypatch, failure):
    from server.core import capabilities
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    build, fake = console
    client, ctx, _deploy = build("cli")
    if failure == "return":
        fake["fw_error"] = "nft refused candidate"
    elif failure == "raise":
        def broken(_nic):
            raise OSError("firewall settings unreadable")
        ctx.nic_services.hooks["firewall"] = broken
    else:
        ctx.nic_services.hooks.pop("firewall")
    response = client.put("/api/console/net/interfaces/eth1", json={
        "proxy": True, "server_ip": "10.44.9.10", "confirm": "eth1", "confirm_proxy_broken": True})
    assert response.status_code == 200, response.text
    assert response.json()["ok"] is False
    assert "חומת אש" in response.json()["apply_error"]


def test_proxy_1829_forgetting_an_interface_removes_its_firewall_rule(console, monkeypatch):
    from server.core import capabilities
    from server.db import set_setting
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    build, fake = console
    client, ctx, _deploy = build("cli")
    set_setting(ctx.conn, "dhcp:eth1", dhcp.InterfaceConfig("eth1", proxy=True).to_json())
    response = client.delete("/api/console/net/interfaces/eth1")
    assert response.status_code == 200 and response.json()["ok"] is True
    fw = [args for kind, args in fake["deploy_calls"] if kind == "fw"]
    assert len(fw) == 1 and fw[0].get("proxy_interfaces", []) == []


def test_first_dhcp_enable_completes_the_install_and_restarts(console):
    build, fake = console
    client, ctx, deploy_ctx = build("none")
    assert client.get("/api/console/net/deploy").json()["configured"] is False

    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deploy"] == {"ok": True, "url": "http://10.44.9.10:8080", "interface": "eth1",
                              "errors": [], "restarting": True, "recorded": True}
    assert deploy_net.stored(ctx.conn) == ("eth1", "http://10.44.9.10:8080")
    assert [c[0] for c in fake["deploy_calls"]] == ["grub", "dnsmasq", "fw", "restart"]
    assert fake["deploy_calls"][2][1] == {"deploy_if": "eth1", "servers_if": "eth0", "primary_ip": None}
    # קובץ ה-dnsmasq שנכתב נושא את כרטיס ההפצה (R20-F1 במסירה).
    assert "interface=eth1" in fake["applied"][-1]
    assert len(fake["applied"]) == 1                       # כתיבה אחת, לא שתיים
    assert client.get("/api/console/net/deploy").json()["source"] == "console"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "deploy_net_set" in events


def test_first_deploy_from_the_deploy_window_derives_the_address_then_completes(tmp_path, images_root, clock):
    """‏#1263 על שרת נקי (מסירה): חלון "רשת הפצה" שולח רשת + טווח בלבד. השרת
    גוזר את הכתובת הראשונה, כותב ומחיל אותה על הכרטיס, ורק **אחרי** שהכרטיס
    נושא אותה — ההדלקה הראשונה משלימה את ההתקנה (‏#1088), שדורשת בדיוק את זה.
    בעבר זה היה שני מסכים ("עריכת כתובת" ואז "DHCP") בסדר שהמפעיל היה צריך לדעת."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    fake = {"interfaces": [
                {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00", "addresses": ["10.1.2.3/24"]},
                {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01", "addresses": []}],
            "confs": {}, "applied": [], "deploy_calls": []}

    def ifup(name):                     # המכונה המדומה: הכתובת שבקובץ עולה על הכרטיס
        address = re.search(r"^    address (\S+)", fake["confs"][name], re.M).group(1)
        next(n for n in fake["interfaces"] if n["name"] == name)["addresses"] = [f"{address}/24"]

    dhcp_hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: fake["applied"].append(text) or None,
        "apply_proxy": lambda text, active: None,
        "read_active_conf": lambda: "", "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    netcfg_hooks = {
        "interfaces": lambda: [dict(n) for n in fake["interfaces"]],
        "netcfg_read_conf": lambda name, root=None: fake["confs"].get(name),
        "netcfg_write_conf": lambda name, text, root=None: fake["confs"].__setitem__(name, text),
        "netcfg_write_resolv": lambda text, path=None: None,
        "netcfg_apply": lambda name: ifup(name),
        "netcfg_state": lambda: netcfg_host.LiveState(
            True, {n["name"]: list(n["addresses"]) for n in fake["interfaces"]}),
        "netcfg_sourced": lambda *a: True,
        "netcfg_timer_active": lambda *a: (True, "active"),
        "netcfg_now": clock, "netcfg_boot_id": lambda *a: "boot-A",
        "netcfg_local_address": lambda request: "10.1.2.3",
        "netcfg_foreign_manager": lambda: None,   # #1558: ifupdown, לא המכונה המריצה
        "netcfg_management": lambda: "eth0",
    }
    deploy_hooks = {
        "write_grub_cfg": lambda url, root: fake["deploy_calls"].append(("grub", url)) or None,
        "enable_dnsmasq": lambda: fake["deploy_calls"].append(("dnsmasq",)) or None,
        "firewall": lambda repo, **kw: fake["deploy_calls"].append(("fw", kw)) or None,
        "restart_server": lambda: fake["deploy_calls"].append(("restart",)) or None,
    }
    ctx = deploy_net.DeployContext(state=deploy_net.DeployState("none", None, None), agent_port=8080,
                                   tftp_root=tmp_path / "tftp", repo_dir=REPO,
                                   servers_interface="eth0", hooks=deploy_hooks)
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=dhcp_hooks, netcfg_hooks=netcfg_hooks, deploy=ctx,
                     netcfg_state_dir=tmp_path / "netstate")
    users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})

    r = client.put("/api/console/net/config/eth1/deploy", json={
        "network": "10.44.9.0", "netmask": "255.255.255.0", "range_start": "10.44.9.50",
        "range_end": "10.44.9.200", "lease": "12h", "confirm": "eth1"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["server_ip"] == "10.44.9.1"
    assert body["deploy"]["ok"] is True and body["deploy"]["url"] == "http://10.44.9.1:8080"
    assert [c[0] for c in fake["deploy_calls"]] == ["grub", "dnsmasq", "fw", "restart"]
    assert deploy_net.stored(app.state.ctx.conn) == ("eth1", "http://10.44.9.1:8080")
    assert "interface=eth1" in fake["applied"][-1]
    # כרטיס הניהול (eth0) — אסור להיות רשת ההפצה, גם ב-API
    refused = client.put("/api/console/net/config/eth0/deploy", json={
        "network": "10.1.2.0", "netmask": "255.255.255.0", "range_start": "10.1.2.50",
        "range_end": "10.1.2.90", "confirm": "eth0"})
    assert refused.status_code == 409 and "כרטיס הניהול" in refused.json()["detail"]

def test_first_dhcp_enable_that_fails_to_complete_writes_nothing_and_can_be_retried(console):
    """‏#1121: חומת האש נפלה באמצע ההשלמה → 500 בשם, **שום דבר** לא נרשם
    (לא `deploy:*`, לא `dhcp:eth1`), dnsmasq לא נכתב, השרת לא מאותחל —
    והניסיון הבא הוא שוב "הדלקה ראשונה" שמשלימה."""
    from server.db import get_setting
    build, fake = console
    client, ctx, deploy_ctx = build("none")
    fake["fw_error"] = "nft -f נפל"
    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 500, r.text
    assert "nft -f נפל" in r.json()["detail"] and "לא נרשם דבר" in r.json()["detail"]
    assert deploy_net.stored(ctx.conn) == (None, None)
    assert get_setting(ctx.conn, dhcp.SETTING_PREFIX + "eth1") is None
    assert [c[0] for c in fake["deploy_calls"]] == ["grub", "dnsmasq", "fw"]     # בלי restart
    assert fake["applied"] == []
    assert deploy_ctx.state.source == "none"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "deploy_net_failed" in events and "deploy_net_set" not in events
    # תיקנו את חומת האש — ההדלקה הבאה משלימה, כאילו הייתה הראשונה.
    fake["fw_error"] = None
    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 200 and r.json()["deploy"]["ok"] is True
    assert deploy_net.stored(ctx.conn) == ("eth1", "http://10.44.9.10:8080")
    assert [c[0] for c in fake["deploy_calls"]][3:] == ["grub", "dnsmasq", "fw", "restart"]


def test_full_dhcp_on_another_nic_after_console_deploy_is_409(console, classrooms_on):
    """‏#1121 ס' 4: אחרי `source=console` על eth1, DHCP **מלא** על eth0 (רשת
    המכללה) נדחה בשם — ושום דבר לא נכתב; ‏proxy על eth0 מותר."""
    from server.db import get_setting
    build, fake = console
    client, ctx, _ = build("none")
    assert client.put("/api/console/net/interfaces/eth1",
                      json={**GOOD, "confirm": "eth1"}).status_code == 200
    applied_before = len(fake["applied"])
    r = client.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409, r.text
    assert "כרטיס ההפצה (eth1)" in r.json()["detail"] and "eth0" in r.json()["detail"]
    assert get_setting(ctx.conn, dhcp.SETTING_PREFIX + "eth0") is None
    assert len(fake["applied"]) == applied_before
    assert deploy_net.stored(ctx.conn) == ("eth1", "http://10.44.9.10:8080")
    r = client.put("/api/console/net/interfaces/eth0",
                   json={"proxy": True, "confirm": "eth0", "confirm_proxy_broken": True})
    assert r.status_code == 200, r.text


def test_first_enable_lock_refuses_a_concurrent_first_enable(console, monkeypatch):
    """הנעילה סביב ההדלקה הראשונה: כשהיא תפוסה — 409, לא השלמה כפולה."""
    from server.console import console_dhcp
    build, fake = console
    client, ctx, _ = build("none")
    assert console_dhcp._first_enable_lock.acquire(blocking=False)
    try:
        r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    finally:
        console_dhcp._first_enable_lock.release()
    assert r.status_code == 409 and "בעיצומה" in r.json()["detail"]
    assert fake["deploy_calls"] == [] and deploy_net.stored(ctx.conn) == (None, None)
    assert client.put("/api/console/net/interfaces/eth1",
                      json={**GOOD, "confirm": "eth1"}).status_code == 200


def test_first_dhcp_enable_refuses_a_nic_that_does_not_carry_the_address(console):
    """הסדר של נדב: כתובת → DHCP. בלי הכתובת על הכרטיס — 409, ושום דבר
    לא נרשם (לא deploy ולא dhcp:eth0), כי אחרת השרת לא היה עולה."""
    build, fake = console
    client, ctx, _ = build("none")
    r = client.put("/api/console/net/interfaces/eth0", json={**GOOD, "confirm": "eth0"})
    assert r.status_code == 409
    assert "10.44.9.10" in r.json()["detail"] and "10.1.2.3" in r.json()["detail"]
    assert deploy_net.stored(ctx.conn) == (None, None)
    from server.db import get_setting
    assert get_setting(ctx.conn, dhcp.SETTING_PREFIX + "eth0") is None
    assert fake["deploy_calls"] == [] and fake["applied"] == []


def test_dhcp_enable_with_a_cli_address_does_not_touch_the_install(console):
    build, fake = console
    client, ctx, _ = build("cli")
    assert client.get("/api/console/net/deploy").json() == {
        "configured": True, "source": "cli", "interface": "eth1",
        "url": "http://10.44.9.10:8080", "hint": None}
    r = client.put("/api/console/net/interfaces/eth1", json={**GOOD, "confirm": "eth1"})
    assert r.status_code == 200 and "deploy" not in r.json()
    assert fake["deploy_calls"] == [] and deploy_net.stored(ctx.conn) == (None, None)
    # עם הדגל — imagesrv.conf של המתקין נושא את interface=; כאן לא מכפילים.
    assert "Deploy interface, set from the console" not in fake["applied"][-1]


# --- health -----------------------------------------------------------------------


def _health_hooks(deploy_state, servers_nic=None, tftp_root=None):
    hooks = health.default_hooks()
    hooks.update({
        "ss": lambda: "State Recv-Q Send-Q Local Address:Port Peer\n",
        "ss_tcp": lambda: "",
        "unit_active": lambda name: "inactive",
        "http_get": lambda url: None, "http_size": lambda url: (None, None),
        "interfaces": lambda: [{"name": "eth0", "state": "up"}],
        "tftp_root": lambda: tftp_root or Path("/nonexistent-tftp"),
        "udp_sender_pids": lambda: [], "nft_ruleset": lambda: None,
        "shim_src": lambda: "/nonexistent-shim",
        "deploy": (lambda: deploy_state) if deploy_state is not None else None,
        "servers_nic": (lambda: servers_nic) if servers_nic is not None else None,
    })
    return hooks


@pytest.fixture()
def health_ctx(tmp_path, images_root, clock):
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock)
    return app.state.ctx


def test_health_says_deploy_not_configured_instead_of_red_pxe_rows(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(deploy_net.DeployState("none", None, None)),
        "http://127.0.0.1:8080")}
    assert rows["deploy_net"]["state"] == "warn" and "בחר כרטיס" in rows["deploy_net"]["detail"]
    dependent = ("dhcp_port", "tftp_port", "dnsmasq", "boot_files", "server", "identity")
    assert all(rows[name]["state"] == "not_applicable" for name in dependent)
    assert all(rows[name]["detail"] == "לא ישים עד שתוגדר רשת ההפצה"
               for name in dependent)
    assert not any(k.startswith("ssh") for k in rows)   # #1266


def test_health_keeps_the_red_rows_when_deploy_is_configured(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080")),
        "http://10.44.9.10:8080")}
    assert "deploy_net" not in rows
    assert rows["tftp_port"]["state"] == "bad"
    assert rows["dnsmasq"]["state"] == "bad"
    assert rows["server"]["state"] == "bad"
    assert "grub/grub.cfg" in rows["boot_files"]["detail"]


def test_health_servers_nic_row_only_when_injected(health_ctx):
    rows = {c["id"]: c for c in health.collect(
        health_ctx, _health_hooks(None, ("ok", "כתובת השרתים (eth0): 10.1.2.3 (DHCP, פג ב-18/09 12:00)")),
        "http://10.44.9.10:8080")}
    assert rows["servers_nic"]["state"] == "ok" and "DHCP" in rows["servers_nic"]["detail"]
    rows = {c["id"] for c in health.collect(health_ctx, _health_hooks(None), "http://10.44.9.10:8080")}
    assert "servers_nic" not in rows and "deploy_net" not in rows


# --- netcfg: hostname ב-DHCP, וקליטת הכרטיס מהקובץ הראשי -------------------------------


def test_dhcp_stanza_sends_the_hostname():
    cfg = netcfg.NetConfig("eth0", mode=netcfg.MODE_DHCP)
    text = netcfg.render(cfg, hostname="imagesrv")
    assert "iface eth0 inet manual\n    up /usr/sbin/dhcpcd -4 -b -h imagesrv eth0\n" in text
    assert "hostname" not in netcfg.render(cfg)
    assert "hostname" not in netcfg.render(
        netcfg.NetConfig("eth1", mode=netcfg.MODE_STATIC, address="10.44.9.10"), hostname="imagesrv")


def test_console_preview_writes_the_hostname_into_the_dhcp_file(tmp_path, images_root, clock):
    from server.console import users
    from server.app import create_app
    app = create_app(tmp_path / "data", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     netcfg_hooks={"hostname": lambda: "imagesrv-haifa",
                                   "interfaces": lambda: [{"name": "eth0", "state": "up", "addresses": []}]})
    users.create(app.state.ctx.conn, "noc", "Admin-pass-123!", "admin", by="test")
    client = TestClient(app)
    client.post("/api/console/login", json={"username": "noc", "password": "Admin-pass-123!"})
    r = client.post("/api/console/net/config/eth0/preview", json={"mode": "dhcp"})
    assert r.status_code == 200, r.text
    assert "up /usr/sbin/dhcpcd -4 -b -h imagesrv-haifa eth0" in r.json()["after"]


MAIN_IFACES = """# The primary network interface
auto lo eth0
iface lo inet loopback

allow-hotplug eth0
iface eth0 inet dhcp
    hostname debian

iface eth1 inet static
    address 10.44.9.10
    netmask 255.255.255.0
"""


def test_comment_out_stanzas_removes_only_that_interface():
    text, changed = netcfg_host.comment_out_stanzas(MAIN_IFACES, "eth0")
    assert changed
    live = [l for l in text.splitlines() if l.strip() and not l.startswith("#")]
    assert "auto lo" in live and "iface lo inet loopback" in live
    assert "iface eth1 inet static" in live and "    address 10.44.9.10" in live
    assert not any(l.split()[:2] == ["iface", "eth0"] for l in live)
    assert "allow-hotplug eth0" not in live
    assert "# imagesrv: iface eth0 inet dhcp" in text
    assert netcfg_host.comment_out_stanzas(MAIN_IFACES, "eth9") == (MAIN_IFACES, False)


def test_adopt_interface_backs_up_comments_out_sources_and_writes(tmp_path):
    main = tmp_path / "interfaces"
    root = tmp_path / "interfaces.d"
    main.write_text(MAIN_IFACES, encoding="utf-8")
    text = netcfg.render(netcfg.NetConfig("eth0", mode="dhcp"), hostname="imagesrv")
    notes, error = netcfg_host.adopt_interface("eth0", text, main=main, root=root, backup_suffix="t")
    assert error is None
    assert (tmp_path / "interfaces.pre-imagesrv.t").read_text(encoding="utf-8") == MAIN_IFACES
    new_main = main.read_text(encoding="utf-8")
    assert f"source {str(root).rstrip('/')}/*" in new_main
    assert "# imagesrv: iface eth0 inet dhcp" in new_main
    assert (root / "imagesrv-eth0").read_text(encoding="utf-8") == text
    # ריצה שנייה — אין מה להעיר, אין גיבוי נוסף, הקובץ נכתב שוב.
    notes2, error2 = netcfg_host.adopt_interface("eth0", text, main=main, root=root, backup_suffix="u")
    assert error2 is None and not (tmp_path / "interfaces.pre-imagesrv.u").exists()


# --- המתקין והיחידה -------------------------------------------------------------


def _installer_block(tag: str) -> str:
    text = INSTALLER.read_text(encoding="utf-8")
    match = re.search(rf"<<{tag}\n(.*?)\n{tag}\b", text, flags=re.DOTALL)
    assert match, f"לא נמצא heredoc {tag} בסקריפט ההתקנה"
    return match.group(1)


def test_installer_netcfg_block_writes_dhcp_with_hostname_and_records_it(tmp_path, monkeypatch):
    """הבלוק המוטמע במתקין (NETEOF) מורץ כמו שהוא — סטייה בין המתקין לקוד
    היא בדיוק החור (כמו test_installer_admin)."""
    from server.db import connect, get_setting
    code = _installer_block("NETEOF").replace("$APP_DIR", REPO.as_posix()) \
        .replace("$DATA_DIR", tmp_path.as_posix())
    main, root = tmp_path / "interfaces", tmp_path / "interfaces.d"
    main.write_text(MAIN_IFACES, encoding="utf-8")
    real = netcfg_host.adopt_interface
    monkeypatch.setattr(netcfg_host, "adopt_interface",
                        lambda name, text, **kw: real(name, text, main=main, root=root))
    monkeypatch.setattr(socket, "gethostname", lambda: "imagesrv-haifa")
    for k, v in {"SERVERS_IF": "eth0", "SERVERS_MODE": "dhcp", "SERVERS_ADDR": "",
                 "SERVERS_MASK": "", "SERVERS_GW": "", "SERVERS_DNS": ""}.items():
        monkeypatch.setenv(k, v)
    exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    written = (root / "imagesrv-eth0").read_text(encoding="utf-8")
    assert "iface eth0 inet manual" in written
    assert "up /usr/sbin/dhcpcd -4 -b -h imagesrv-haifa eth0" in written
    assert "# imagesrv: iface eth0 inet dhcp" in main.read_text(encoding="utf-8")
    conn = connect(tmp_path / "imagesrv.db")
    assert json.loads(get_setting(conn, netcfg.SETTING_PREFIX + "eth0"))["mode"] == "dhcp"


def test_installer_netcfg_block_refuses_a_bad_static_address(tmp_path, monkeypatch):
    code = _installer_block("NETEOF").replace("$APP_DIR", REPO.as_posix()) \
        .replace("$DATA_DIR", tmp_path.as_posix())
    monkeypatch.setattr(netcfg_host, "adopt_interface",
                        lambda *a, **k: pytest.fail("כתב קובץ להגדרה פסולה"))
    for k, v in {"SERVERS_IF": "eth0", "SERVERS_MODE": "static", "SERVERS_ADDR": "10.44.9",
                 "SERVERS_MASK": "", "SERVERS_GW": "", "SERVERS_DNS": ""}.items():
        monkeypatch.setenv(k, v)
    with pytest.raises(SystemExit) as exc:
        exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    assert "אינה תקינה" in str(exc.value)


def _netcfg_block(tmp_path, monkeypatch, mode: str, dns: str, resolv_text: str | None = None):
    """‏#2138: הבלוק NETEOF כמו שהוא, מול resolv.conf בתיקייה זמנית (‏RESOLV_CONF
    מוחלף במודול — הבלוק מעביר אותו במפורש, ולא נשען על ברירת המחדל)."""
    code = _installer_block("NETEOF").replace("$APP_DIR", REPO.as_posix()) \
        .replace("$DATA_DIR", tmp_path.as_posix())
    main, root = tmp_path / "interfaces", tmp_path / "interfaces.d"
    main.write_text(MAIN_IFACES, encoding="utf-8")
    resolv = tmp_path / "resolv.conf"
    if resolv_text is not None:
        resolv.write_text(resolv_text, encoding="utf-8")
    real = netcfg_host.adopt_interface
    monkeypatch.setattr(netcfg_host, "adopt_interface",
                        lambda name, text, **kw: real(name, text, main=main, root=root))
    monkeypatch.setattr(netcfg_host, "RESOLV_CONF", str(resolv))
    static = {"SERVERS_ADDR": "10.68.0.82", "SERVERS_MASK": "255.255.255.0", "SERVERS_GW": "10.68.0.1"}
    env = {"SERVERS_IF": "eth0", "SERVERS_MODE": mode, "SERVERS_DNS": dns,
           **(static if mode == "static" else {"SERVERS_ADDR": "", "SERVERS_MASK": "", "SERVERS_GW": ""})}
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return code, resolv


def test_installer_static_nic_writes_the_dns_from_the_screen_to_resolv_conf(tmp_path, monkeypatch, capsys):
    """rc3 (Hyper-V, 24/10): ‏servers_dns=10.68.0.1 ב-answers, ואין
    ‏`/etc/resolv.conf` אחרי firstboot — ‏`getent hosts` החזיר 2. ברשת בלי DHCP
    אף אחד אחר לא כותב אותו."""
    code, resolv = _netcfg_block(tmp_path, monkeypatch, "static", "10.68.0.1, 1.1.1.1")
    exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    servers = [line.split()[1] for line in resolv.read_text(encoding="utf-8").splitlines()
               if line.startswith("nameserver ")]
    assert servers == ["10.68.0.1", "1.1.1.1"]
    assert f"נכתב {resolv}: 10.68.0.1, 1.1.1.1" in capsys.readouterr().out


@pytest.mark.parametrize("mode, dns", [("dhcp", ""), ("static", "")])
def test_installer_leaves_resolv_conf_to_dhcpcd_or_alone_without_dns(tmp_path, monkeypatch, mode, dns):
    """‏DHCP: ה-hook של dhcpcd הוא הבעלים של הקובץ. סטטי בלי DNS: אין מה
    לכתוב, ומה שיש (lease זמני של firstboot) אינו נמחק."""
    before = "# Generated by dhcpcd from eth0.dhcp\nnameserver 192.168.68.1\n"
    code, resolv = _netcfg_block(tmp_path, monkeypatch, mode, dns, resolv_text=before)
    exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    assert resolv.read_text(encoding="utf-8") == before


def test_installer_resolv_conf_that_does_not_read_back_fails_by_name(tmp_path, monkeypatch):
    """עיקרון 5: ההצלחה היא הקובץ שנקרא בחזרה, לא "write_resolv החזיר None"."""
    code, resolv = _netcfg_block(tmp_path, monkeypatch, "static", "10.68.0.1")
    monkeypatch.setattr(netcfg_host, "write_resolv", lambda text, path: None)   # "wrote" nothing
    with pytest.raises(SystemExit) as exc:
        exec(compile(code, "deploy/setup-boot-server.sh", "exec"), {})
    assert str(resolv) in str(exc.value)


def test_installer_text_deploy_is_optional_and_servers_nic_is_dhcp_by_default():
    text = INSTALLER.read_text(encoding="utf-8")
    assert re.search(r"--interface\|--deploy-if\)", text)
    assert re.search(r"--servers-mode\)", text) and 'SERVERS_MODE="dhcp"' in text
    assert "לא עכשיו" in text
    assert 'CONSOLE_HOST="${CONSOLE_HOST:-$SERVERS_IF}"' in text
    # בלי כרטיס הפצה: אין interface=, dnsmasq כבוי, grub.cfg לא נכתב, חומת אש בלי --deploy-if.
    assert 'NFT_ARGS+=(--deploy-if "$DEPLOY_FOR_NFT")' in text          # ‏#1121 ס' 7
    assert "systemctl disable --now dnsmasq" in text
    assert "grub/grub.cfg לא נכתב" in text
    assert 'VERIFY_URL="${SERVER_URL:-http://127.0.0.1:${PORT:-8080}}"' in text
    # ‏--interface ליחידה רק כשיש כרטיס; --console-host תמיד.
    assert re.search(r'if \[\[ -n "\$IFACE" \]\]; then\n\s+STORAGE_ARGS\+=" --interface \$IFACE"', text)
    assert 'STORAGE_ARGS+=" --console-host $CONSOLE_HOST"' in text
    assert "getent ahostsv4" in text                                # ראשי בשם → כתובת לחומת האש


def test_unit_no_longer_requires_imagesrv_url_and_can_write_the_tftp_root():
    text = UNIT.read_text(encoding="utf-8")
    assert "ExecStartPre" not in text
    assert "${IMAGESRV_URL}" in text
    rw = next(l for l in text.splitlines() if l.startswith("ReadWritePaths="))
    assert "-/srv/tftp" in rw


def test_server_main_accepts_an_empty_server_url_argument():
    """היחידה מעבירה ${IMAGESRV_URL} ריק כארגומנט ריק אחד — הפרסר מקבל."""
    args = server.main.build_parser().parse_args(["--server-url", ""])
    assert args.server_url == "" and args.tftp_root == "/srv/tftp"
    assert server.main.build_parser().parse_args([]).server_url == ""


# --- pairing לפי FQDN --------------------------------------------------------------


def test_interserver_url_accepts_a_hostname_and_the_client_resolves_per_connection(monkeypatch):
    from server.interserver import interserver_auth, storage_client
    host, port, _ = interserver_auth.parse_interserver_url("https://imagesrv.college.local:8443/")
    assert (host, port) == ("imagesrv.college.local", 8443)
    seen = []

    def fake_connect(addr, timeout=None):
        seen.append(addr)
        raise OSError("לא מתחברים בבדיקה")
    monkeypatch.setattr(socket, "create_connection", fake_connect)
    with pytest.raises(Exception):
        storage_client.fetch_server_spki(host, port, timeout=0.1)
    assert seen == [("imagesrv.college.local", 8443)]          # השם, לא IP שנפתר מראש


def test_the_parent_is_authenticated_by_certificate_not_by_address():
    """ה-SPKI-pin אינו תלוי בכתובת: ל-`TlsPeer` אין שדה כתובת בכלל, ולכן
    חילוף כתובת של הראשי אינו נוגע בטרסט. הראיה ההתנהגותית — #740."""
    from dataclasses import fields
    from server.interserver import interserver_api
    assert {f.name for f in fields(interserver_api.TlsPeer)} == {"tls_version", "cert_der", "exporter"}
    from server.interserver.storage_nodes import normalize_config
    assert normalize_config("secondary", "https://imagesrv.college.local:8443")[1] == \
        "https://imagesrv.college.local:8443"


# --- ‏#1013: קובץ ה-dnsmasq הראשי נגזר מה-DB בעליית השרת --------------------------


def _startup(tmp_path, images_root, clock, *, source: str, on_disk: str | None,
             sync: bool = True, tftp_root: Path | None = None):
    """מרים runtime כמו main (sync_dnsmasq=True) עם dnsmasq מזויף, ומחזיר
    מה נכתב. ‏`on_disk` = מה ש-read_active_conf "רואה" (None = אין קובץ)."""
    from server.app import create_app
    applied: list[str] = []
    hooks = {
        "interfaces": lambda: [], "probe": lambda name: dhcp.ProbeResult(True, ()),
        "apply": lambda text: applied.append(text) or None,
        "apply_proxy": lambda text, active: pytest.fail("העלייה אינה נוגעת ב-proxy"),
        "read_active_conf": lambda: on_disk, "service_active": lambda unit: True,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
    }
    state = {"cli": deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080"),
             "console": deploy_net.DeployState("console", "eth1", "http://10.44.9.10:8080"),
             "none": deploy_net.DeployState("none", None, None)}[source]
    ctx = deploy_net.DeployContext(state=state, agent_port=8080,
                                   tftp_root=tftp_root or Path("/srv/tftp"), repo_dir=REPO)
    app = create_app(tmp_path / f"data-{source}-{sync}", images_root, "http://127.0.0.1:8080",
                     now_fn=clock, dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=sync)
    return applied, app.state.ctx


def test_startup_writes_tftp_into_the_console_file_on_a_fresh_install(tmp_path, images_root, clock):
    """התקנה טרייה עם כרטיס הפצה (cli): המתקין כבר אינו כותב enable-tftp,
    ו-dnsmasq שלו עלה בלי TFTP. השרת, בעלייה, כותב את הקובץ שלו — עם
    TFTP ובלי DHCP — ומפעיל את dnsmasq (hook). זה מה שמעלה 69."""
    applied, ctx = _startup(tmp_path, images_root, clock, source="cli", on_disk=None)
    assert len(applied) == 1
    text = applied[0]
    assert dhcp.installer_serves_tftp(text) and "tftp-root=/srv/tftp" in text
    assert "dhcp-range" not in text and "interface=" not in text        # cli: interface= במתקין
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal").fetchall()]
    assert "dhcp_synced_at_startup" in events


def test_startup_does_not_touch_dnsmasq_when_the_file_already_matches(tmp_path, images_root, clock):
    """אתחול שגרתי של השרת אינו מפיל את dnsmasq: אותו תוכן = אפס כתיבות."""
    applied, _ = _startup(tmp_path, images_root, clock, source="cli", on_disk=None)
    same = applied[0]
    applied2, ctx = _startup(tmp_path / "again", images_root, clock, source="cli", on_disk=same)
    assert applied2 == []
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal").fetchall()]
    assert "dhcp_synced_at_startup" not in events


def test_startup_leaves_dnsmasq_alone_until_the_deploy_network_is_configured(tmp_path, images_root, clock):
    """‏#1088: בלי רשת הפצה dnsmasq נשאר כבוי עד ההדלקה הראשונה — restart
    מהעלייה היה מעלה אותו על כל הכרטיסים (בלי interface=)."""
    applied, _ = _startup(tmp_path, images_root, clock, source="none", on_disk=None)
    assert applied == []


def test_startup_sync_is_opt_in_like_known_macs(tmp_path, images_root, clock):
    """ברירת המחדל של create_runtime אינה נוגעת ב-dnsmasq — רק main מדליק."""
    applied, _ = _startup(tmp_path, images_root, clock, source="cli", on_disk=None, sync=False)
    assert applied == []


def test_startup_honours_the_tftp_switch_and_the_console_deploy_interface(tmp_path, images_root, clock):
    """מה שנשמר ב-DB הוא מה שנכתב: מתג כבוי → בלי enable-tftp; כרטיס הפצה
    מהקונסולה → interface= בקובץ הזה; tftp_root מההתקנה."""
    from server.net import ports as ports_mod
    from server.app import create_app
    applied: list[str] = []
    hooks = {"interfaces": lambda: [], "probe": lambda name: dhcp.ProbeResult(True, ()),
             "apply": lambda text: applied.append(text) or None,
             "apply_proxy": lambda text, active: None,
             "read_active_conf": lambda: None, "service_active": lambda unit: True}
    ctx = deploy_net.DeployContext(
        state=deploy_net.DeployState("console", "eth1", "http://10.44.9.10:8080"),
        agent_port=8080, tftp_root=Path("/data/tftp"), repo_dir=REPO)
    # ריצה ראשונה: המתג נשמר כבוי (כאילו המפעיל כיבה לפני האתחול)
    data = tmp_path / "data"
    app = create_app(data, images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx)
    ports_mod.set_enabled(app.state.ctx.conn, "tftp", False)
    app.state.ctx.conn.close()
    app = create_app(data, images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=True)
    assert len(applied) == 1
    text = applied[0]
    assert not dhcp.installer_serves_tftp(text) and "TFTP off" in text
    assert "interface=eth1" in text
    app.state.ctx.conn.close()
    # ומתג דלוק עם שורש לא-ברירת-מחדל
    applied.clear()
    app = create_app(tmp_path / "data2", images_root, "http://127.0.0.1:8080", now_fn=clock,
                     dhcp_hooks=hooks, deploy=ctx, sync_dnsmasq=True)
    assert "tftp-root=/data/tftp" in applied[0]
