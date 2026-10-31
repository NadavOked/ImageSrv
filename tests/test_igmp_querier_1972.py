"""‏#1972: השרת כ-IGMP querier בכרטיס ההפצה — שליחה, בחירה מול querier אחר,
ושורת הבריאות. הסוקט מזויף (סוקט raw דורש root); הבחירה והחבילה אמיתיות."""

from __future__ import annotations

import socket
import struct
import threading

import pytest

from server.net import igmp_querier as q
from server.db import connect, set_setting


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class FakeSocket:
    """‏`script`: רשימת פריטים לכל `recvfrom` — bytes (חבילה שהגיעה) או מספר
    (שניות שעוברות בלי חבילה). כשהיא נגמרת — `stop` נדלק."""

    def __init__(self, clock: Clock, script: list, stop: threading.Event,
                 sent_len: int | None = None) -> None:
        self.clock, self.script, self.stop = clock, list(script), stop
        self.sent: list[tuple[float, bytes, tuple]] = []
        self.sent_len = sent_len
        self.closed = False

    def sendto(self, data: bytes, addr: tuple) -> int:
        self.sent.append((self.clock.now, data, addr))
        return len(data) if self.sent_len is None else self.sent_len

    def settimeout(self, value: float) -> None:
        pass

    def recvfrom(self, size: int):
        if not self.script:
            self.stop.set()
            raise socket.timeout()
        item = self.script.pop(0)
        if isinstance(item, (int, float)):
            self.clock.now += item
            raise socket.timeout()
        return item, ("0.0.0.0", 0)

    def close(self) -> None:
        self.closed = True


def ip_packet(src: str, igmp: bytes, *, options: bytes = q.ROUTER_ALERT) -> bytes:
    ihl = 5 + len(options) // 4
    header = struct.pack("!BBHHHBBH4s4s", (4 << 4) | ihl, 0, ihl * 4 + len(igmp), 0, 0, 1,
                         socket.IPPROTO_IGMP, 0, socket.inet_aton(src),
                         socket.inet_aton("224.0.0.1"))
    return header + options + igmp


REPORT = struct.pack("!BBH4s", 0x16, 0, 0, socket.inet_aton("239.1.1.1"))


@pytest.fixture()
def conn(tmp_path):
    return connect(tmp_path / "imagesrv.db")


def run(querier: q.Querier, clock: Clock, script: list, **kw) -> FakeSocket:
    stop = threading.Event()
    holder = {}

    def opener(interface, address):
        holder["sock"] = FakeSocket(clock, script, stop, **kw)
        holder["args"] = (interface, address)
        return holder["sock"]

    querier.opener = opener
    querier.cycle(stop, seconds=10_000)
    assert holder["args"] == ("eth-deploy", "10.44.12.10")
    assert holder["sock"].closed
    return holder["sock"]


def make(conn, clock, **kw) -> q.Querier:
    return q.Querier(conn, "eth-deploy", lambda: "10.44.12.10", clock=clock, **kw)


# --- החבילה ---------------------------------------------------------------------

def test_the_query_is_an_igmpv2_general_query_with_a_valid_checksum():
    packet = q.query_packet()
    assert len(packet) == 8
    assert packet[0] == 0x11 and packet[1] == 100
    assert packet[4:] == bytes(4)                      # כללית — קבוצה 0.0.0.0
    assert q.checksum(packet) == 0                     # checksum תקין מסתכם ל-0


def test_an_incoming_query_is_recognised_and_a_report_is_not():
    assert q.query_source(ip_packet("10.44.12.1", q.query_packet())) == "10.44.12.1"
    assert q.query_source(ip_packet("10.44.12.1", q.query_packet(), options=b"")) == "10.44.12.1"
    assert q.query_source(ip_packet("10.44.12.50", REPORT)) is None
    assert q.query_source(b"\x45" + bytes(10)) is None


# --- השליחה -----------------------------------------------------------------------

def test_it_queries_at_once_and_then_every_interval(conn):
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [60, 60, 4, 1, 125])
    times = [t - 1000.0 for t, _data, _addr in sock.sent]
    assert times == [0.0, 125.0, 250.0]
    assert all(addr == ("224.0.0.1", 0) and data == q.query_packet()
               for _t, data, addr in sock.sent)
    state, detail = querier.health()
    assert state == "ok" and "פעיל על eth-deploy" in detail


def test_a_lower_querier_silences_the_server_until_it_goes_quiet(conn):
    clock = Clock()
    querier = make(conn, clock)
    lower = ip_packet("10.44.12.1", q.query_packet())
    sock = run(querier, clock, [lower, 130, 120])
    # שאילתה ראשונה יצאה לפני שנשמע ה-querier; אחריה — שקט עד שהוא נעלם.
    assert [t - 1000.0 for t, *_ in sock.sent] == [0.0]
    state, detail = querier.health()
    assert state == "ok" and "יש querier אחר ברשת (10.44.12.1)" in detail
    clock.now += q.OTHER_PRESENT
    sock = run(querier, clock, [1])
    assert len(sock.sent) == 1, "אחרי שה-querier האחר שתק — השרת חוזר לשאול"


def test_a_higher_querier_does_not_silence_the_server(conn):
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [ip_packet("10.44.12.200", q.query_packet()), 130])
    assert len(sock.sent) == 2
    state, detail = querier.health()
    assert state == "ok" and "querier נוסף (10.44.12.200)" in detail


def test_our_own_query_is_not_another_querier(conn):
    clock = Clock()
    querier = make(conn, clock)
    run(querier, clock, [ip_packet("10.44.12.10", q.query_packet())])
    assert querier.other is None


def test_the_switch_turns_sending_off_and_says_so(conn):
    set_setting(conn, q.SETTING, "false")
    clock = Clock()
    querier = make(conn, clock)
    sock = run(querier, clock, [130, ip_packet("10.44.12.1", q.query_packet())])
    assert sock.sent == []
    state, detail = querier.health()
    assert state == "off" and detail.startswith("כבוי בהגדרות")
    assert "querier אחר ברשת: 10.44.12.1" in detail


def test_a_nic_with_multicast_off_is_not_queried(conn):
    clock = Clock()
    querier = make(conn, clock, gate=lambda nic: f"שידור כבוי בכרטיס {nic}")
    sock = run(querier, clock, [130])
    assert sock.sent == []
    assert querier.health()[0] == "off"


# --- כשלים: נאמרים, לא נבלעים ------------------------------------------------------

def test_a_short_send_is_a_failure_not_active(conn):
    clock = Clock()
    querier = make(conn, clock)
    with pytest.raises(OSError):
        run(querier, clock, [1], sent_len=3)
    assert querier.last_sent is None


def test_run_forever_reports_a_failure_on_the_health_screen(conn, monkeypatch):
    clock = Clock()
    stop = threading.Event()
    querier = q.Querier(conn, "eth-deploy", lambda: None, clock=clock)
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    querier.run_forever(stop)
    state, detail = querier.health()
    assert state == "bad" and "אין כתובת IPv4" in detail


def test_a_query_that_stopped_going_out_turns_red(conn):
    clock = Clock()
    querier = make(conn, clock)
    run(querier, clock, [1])
    clock.now += 2 * q.QUERY_INTERVAL + 5
    state, detail = querier.health()
    assert state == "bad" and "לא נשלחה שאילתה" in detail


def test_before_the_first_query_the_state_is_unknown_not_ok(conn):
    assert make(conn, Clock()).health()[0] == "unknown"


# --- חומת האש: "לא נשמע" אינו "אין" ---------------------------------------------------

RULES_WITH = 'table inet imagesrv {\n\tchain input {\n\t\tiifname "eth-deploy" ip protocol igmp accept\n\t}\n}\n'
RULES_WITHOUT = 'table inet imagesrv {\n\tchain input {\n\t\tiifname "eth-deploy" udp dport 9000-9001 accept\n\t}\n}\n'  # פורט-הפצה-מכוון: טקסט חומת אש


def test_firewall_hearing_has_three_states():
    assert q.firewall_hears(RULES_WITH, "eth-deploy") is True
    assert q.firewall_hears(RULES_WITHOUT, "eth-deploy") is False
    assert q.firewall_hears("", "eth-deploy") is True          # אין טבלה שלנו — אין drop שלנו
    assert q.firewall_hears(None, "eth-deploy") is None


@pytest.mark.parametrize("hears, state", [(True, "ok"), (False, "warn"), (None, "warn")])
def test_active_without_hearing_others_is_not_plain_green(conn, hears, state):
    clock = Clock()
    querier = make(conn, clock, hears=lambda: hears)
    run(querier, clock, [1])
    assert querier.health()[0] == state


# --- הגדרות, בריאות, main ------------------------------------------------------------

def test_the_setting_is_a_switch(server):
    admin = server["admin"]
    assert admin.post("/api/console/settings", json={"igmp_querier": "maybe"}).status_code == 400
    assert admin.post("/api/console/settings", json={"igmp_querier": False}).status_code == 200
    assert admin.get("/api/console/settings").json()["igmp_querier"] == "false"
    assert not q.enabled(server["ctx"].conn)


def test_the_health_screen_carries_the_querier_row(tmp_path, images_root):
    from fastapi.testclient import TestClient

    from server.console import users
    from server.app import create_app

    hooks = {"ss": lambda: "", "nft_ruleset": lambda: "", "unit_active": lambda name: "",
             "http_get": lambda url: 200, "http_size": lambda url: (404, 9),
             "interfaces": lambda: [{"name": "eth-deploy", "state": "up", "addresses": []}],
             "tftp_root": lambda: tmp_path / "tftp", "udp_sender_pids": lambda: [],
             "igmp_querier": lambda: ("ok", "פעיל על eth-deploy")}
    app = create_app(tmp_path / "data", images_root, "http://10.0.0.1:8080",
                     health_hooks=hooks)
    client = TestClient(app)
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True,
                 check_policy=False)
    assert client.post("/api/console/login",
                       json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    rows = {c["id"]: c for c in client.get("/api/console/health").json()}
    assert rows["igmp_querier"]["state"] == "ok"
    assert rows["igmp_querier"]["detail"] == "פעיל על eth-deploy"


def test_main_states_not_applicable_without_a_deploy_network():
    from types import SimpleNamespace

    from server.net import deploy_net
    from server import main

    runtime = SimpleNamespace(health_hooks={})
    main._start_igmp_querier(runtime, deploy_net.DeployState("none", None, None), None)
    assert runtime.health_hooks["igmp_querier"]()[0] == "not_applicable"


def test_main_starts_the_querier_on_the_deploy_nic(monkeypatch):
    from types import SimpleNamespace

    from server.net import deploy_net
    from server import main

    started = []
    monkeypatch.setattr(q.Querier, "run_forever", lambda self, stop=None: started.append(self))
    runtime = SimpleNamespace(health_hooks={}, conn=None,
                              ctx=SimpleNamespace(nic_services=None))
    main._start_igmp_querier(runtime, deploy_net.DeployState("cli", "eth-deploy", "http://x"),
                             "eth-deploy")
    for t in threading.enumerate():
        if t.name == "igmp-querier":
            t.join(timeout=5)
    assert len(started) == 1 and started[0].interface == "eth-deploy"
    assert runtime.health_hooks["igmp_querier"] == started[0].health
