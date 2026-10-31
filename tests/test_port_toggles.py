"""‏#996: מתג הדלקה/כיבוי לכל פורט — ‏`PUT /api/console/ports/{id}` ומה
ש-`/ports` מחזיר לצידו (`enabled`/`bind`/`toggle`/`off_means`).

אותו דפוס hooks מוזרק כמו test_console_ports.py: ‏`ss`/`ss_tcp` מזויפים,
ומנהל המאזינים מזויף — רושם מה נתבקש ומחזיר "מאזין" רק למה שפתוח. ככה
"‏`ss` לא מראה מאזין אחרי כיבוי" נמדד באותו מקום שהמתג נגע בו. הסוקט
האמיתי נבדק ב-test_port_listeners.py.

עיקרון 5 — שלושה מצבים, לא שניים: "כבוי" (המתג) ≠ "לא מאזין" (נמדד ב-ss)
‏≠ "לא נקרא" (ss חסר). כל אחד מהם צבע משלו, ואף אחד אינו ירוק.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import dhcp  # noqa: E402

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None

SS_HEAD = "State  Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
SS_DHCP_ONLY = SS_HEAD + (
    'UNCONN 0      0            0.0.0.0:67        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=4))\n')
SS_UDP = SS_DHCP_ONLY + (
    'UNCONN 0      0            0.0.0.0:69        0.0.0.0:*     users:(("dnsmasq",pid=612,fd=6))\n')

#: קובץ המתקין **הישן** (עד v0.47.5) — enable-tftp פעיל; והחדש (#1013) — הערה בלבד.
INSTALLER_OLD = "port=0\ninterface=eth0\nbind-interfaces\n\nenable-tftp\ntftp-root=/srv/tftp\n"
INSTALLER_NEW = "port=0\ninterface=eth0\nbind-interfaces\n# enable-tftp moved (#1013)\n"

PORTS = {"http_boot": 8080, "http_console": 8081, "interserver": 8443}
HOSTS = {"http_boot": ["0.0.0.0"], "http_console": ["10.44.0.1", "127.0.0.1"],
         "interserver": ["10.30.0.8"]}


class FakeListeners:
    """המנהל המזויף: `set_open`/`request` רושמים, `is_open`/`bound` עונים."""

    def __init__(self, ids=("http_boot", "http_console", "interserver")):
        self.open = set(ids)
        self.calls: list[tuple[str, bool]] = []
        self.fail_open: set[str] = set()
        self.ids_ = list(ids)

    def ids(self):
        return list(self.ids_)

    def spec(self, port_id):
        return {"port": PORTS[port_id], "hosts": HOSTS[port_id]}

    def is_open(self, port_id):
        return port_id in self.open

    def bound(self, port_id):
        if port_id not in self.open:
            return []
        return [(h, PORTS[port_id]) for h in HOSTS[port_id]]

    def request(self, port_id, want, timeout=None):
        from server.net.ports import ListenerError
        self.calls.append((port_id, want))
        if want and port_id in self.fail_open:
            raise ListenerError(f"{port_id}: address already in use")
        (self.open.add if want else self.open.discard)(port_id)

    def ss_tcp(self) -> str:
        lines = [SS_HEAD]
        for pid in sorted(self.open):
            for host in HOSTS[pid]:
                lines.append(f'LISTEN 0      2048         {host}:{PORTS[pid]}   '
                             f'0.0.0.0:*     users:(("python3",pid=900,fd=7))\n')
        return "".join(lines)


class FakeDnsmasq:
    """‏#1013: dnsmasq מזויף — ה-`apply` מקבל את הקובץ שהשרת רינדר, ומה
    ש-`ss -ulnp` "רואה" נגזר ממנו: שורת `enable-tftp` פעילה = מאזין על 69.
    ככה "ss לא מראה 69 אחרי כיבוי" נמדד באותו מקום שהמתג נגע בו."""

    def __init__(self, fake: dict):
        self.fake = fake
        self.applied: list[str] = []
        self.proxy: list[tuple[str, bool]] = []
        self.fail = False

    def apply(self, text: str) -> str | None:
        from server.net import dhcp
        self.applied.append(text)
        if self.fail:
            return "dnsmasq לא הגיב ל-restart: (מזויף)"
        self.fake["ss"] = SS_UDP if dhcp.installer_serves_tftp(text) else SS_DHCP_ONLY
        return None

    def apply_proxy(self, text: str, active: bool) -> str | None:
        self.proxy.append((text, active))
        return None


def _build(tmp_path: Path, images_root: Path, clock, listeners, *, base_port=80,
           installer_conf: str | None = INSTALLER_NEW):
    from server.console import users
    from server.app import create_app

    tftp = tmp_path / "tftp"
    tftp.mkdir(parents=True)
    fake = {"ss": SS_UDP, "http": 200,
            "interfaces": [{"name": "eth0", "state": "up", "mac": "aa",
                            "addresses": ["10.44.0.1/24"]}],
            "udp_sender_pids": [], "ss_tcp": None,
            "nft_ruleset": None}
    hooks = {
        "ss": lambda: fake["ss"],
        "ss_tcp": lambda: (fake["ss_tcp"] if fake["ss_tcp"] is not None
                           else (listeners.ss_tcp() if listeners else SS_HEAD)),
        "unit_active": lambda name: "active",
        "http_get": lambda url: fake["http"],
        "http_size": lambda url: (200, 31_000_000),
        "interfaces": lambda: fake["interfaces"],
        "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: fake["udp_sender_pids"],
        # ‏#996: מנהל המאזינים מוזרק כמו כל השאר — None = אין מנהל בתהליך
        # הזה (כמו ב-create_app של הבדיקות), והמתג חייב לומר זאת.
        "port_listeners": listeners,
        "nft_ruleset": lambda: fake["nft_ruleset"],
        # ‏#1013: קובץ המתקין — מוזרק, כי במעבדה הקובץ האמיתי קיים (ועד
        # שהמתקין ירוץ שם מחדש הוא נושא enable-tftp).
        "installer_conf": lambda: fake["installer_conf"],
    }
    fake["installer_conf"] = installer_conf
    dnsmasq = FakeDnsmasq(fake)
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, health_hooks=hooks,
                     # ‏#1013: המתג של 69 כותב קובץ dnsmasq — לעולם לא את האמיתי.
                     dhcp_hooks={"apply": dnsmasq.apply, "apply_proxy": dnsmasq.apply_proxy,
                                 "interfaces": lambda: fake["interfaces"],
                                 "probe": lambda name: dhcp.ProbeResult(True, ()),
                                 "read_active_conf": lambda: "",
                                 "service_active": lambda unit: True})
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    base = f"http://testserver:{base_port}"
    admin, deploy = TestClient(app, base_url=base), TestClient(app, base_url=base)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    deploy.post("/api/console/login",
                json={"username": "labtech", "password": "deploy-pass-1"})
    return {"admin": admin, "deploy": deploy, "fake": fake, "conn": conn,
            "listeners": listeners, "app": app, "dnsmasq": dnsmasq}


@pytest.fixture()
def toggles(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    return _build(tmp_path, images_root, clock, FakeListeners())


def by_id(rows):
    return {r["id"]: r for r in rows}


def rows_of(t):
    resp = t["admin"].get("/api/console/ports")
    assert resp.status_code == 200
    return by_id(resp.json())


def server_name(t) -> str:
    return t["admin"].get("/api/console/me").json()["server_name"]


# --- מה ש-/ports מחזיר עכשיו --------------------------------------------------


def test_every_row_carries_the_switch_fields(toggles):
    rows = rows_of(toggles)
    for row in rows.values():
        for key in ("enabled", "bind", "toggle", "off_means", "toggle_url",
                    "confirm_word", "listening"):
            assert key in row, f"{row['id']} is missing {key}"
        assert row["toggle"] in ("api", "confirm", "none")
        assert row["off_means"]


def test_every_switch_row_warns_before_shutdown_in_the_69_wording(toggles):
    """נדב 19/09: "על כל פורט אזהרה" — לא רק 69. כל שורה עם מתג נושאת
    `warning_he` בניסוח "כיבוי X … " שהקונסולה מציגה לפני ההקלדה/האישור;
    שורה בלי מתג (`none`) לא מציגה אזהרת כיבוי כי אין מה לכבות."""
    rows = rows_of(toggles)
    with_switch = [r for r in rows.values() if r["toggle"] != "none"]
    assert len(with_switch) >= 6, [r["id"] for r in with_switch]
    for row in with_switch:
        assert row.get("warning_he"), f"{row['id']} has no warning_he"
        assert row["warning_he"].startswith("כיבוי "), (row["id"], row["warning_he"])
    for row in rows.values():
        if row["toggle"] == "none":
            assert not row.get("warning_he"), row["id"]


def test_the_missing_rows_are_there_now(toggles):
    """‏DHCP 67 ובין-שרתים 8443 — חסרו ב-/ports. (‏#1266: SSH יצא מהמוצר.)"""
    rows = rows_of(toggles)
    assert rows["dhcp"]["port"] == "67" and rows["dhcp"]["proto"] == "udp"
    assert rows["interserver"]["port"] == "8443"


def test_bind_comes_from_the_socket_table_not_from_configuration(toggles):
    rows = rows_of(toggles)
    assert rows["http_console"]["bind"] == ["10.44.0.1:8081", "127.0.0.1:8081"]
    assert rows["http_console"]["listening"] is True
    assert rows["http_console"]["state"] == "ok"
    assert rows["tftp"]["bind"] == ["0.0.0.0:69"]
    assert rows["dhcp"]["bind"] == ["0.0.0.0:67"]


def test_our_ports_point_at_the_ports_api_and_say_who_needs_the_name(toggles, classrooms_on):   # ‏#1303: 4011 — v1.2
    rows = rows_of(toggles)
    name = server_name(toggles)
    for pid in ("http_boot", "http_console"):
        assert rows[pid]["toggle"] == "confirm"
        assert rows[pid]["confirm_word"] == name
        assert rows[pid]["toggle_url"] == f"/api/console/ports/{pid}"
    assert rows["interserver"]["toggle"] == "api"
    assert rows["interserver"]["toggle_url"] == "/api/console/ports/interserver"
    # למי שיש כבר מתג במקום אחר — מצביעים לשם, לא משכפלים.
    assert rows["dhcp"]["toggle_url"] == "/api/console/net/interfaces/{name}"
    assert rows["dhcp"]["toggle"] == "confirm"
    assert rows["pxe_proxy"]["toggle_url"] == "/api/console/net/interfaces/{name}"
    assert rows["monitor"]["toggle_url"] == "/api/console/monitor/settings"
    assert rows["monitor"]["confirm_word"] == "imagesrv.monitor"
    # ולמי שאין מתג — אומרים למה.
    # ‏#1013: TFTP הפך למתג "confirm" (הקלדת שם השרת); מולטיקאסט נשאר בלי מתג.
    assert rows["tftp"]["toggle"] == "confirm" and rows["multicast"]["toggle"] == "none"
    assert rows["tftp"]["toggle_url"] == "/api/console/ports/tftp"
    assert rows["multicast"]["enabled"] is None


# --- שלושה מצבים, שלושה צבעים -------------------------------------------------


def test_enabled_but_nobody_listens_is_red_not_off(toggles):
    toggles["fake"]["ss_tcp"] = SS_HEAD           # הטבלה נקראה, ריקה
    rows = rows_of(toggles)
    assert rows["interserver"]["enabled"] is True
    assert rows["interserver"]["listening"] is False
    assert rows["interserver"]["state"] == "bad"


def test_disabled_but_still_listening_is_red(toggles):
    from server.net import ports
    ports.set_enabled(toggles["conn"], "interserver", False)
    rows = rows_of(toggles)                        # ה-fake עדיין "מאזין"
    assert rows["interserver"]["enabled"] is False and rows["interserver"]["listening"] is True
    assert rows["interserver"]["state"] == "bad"


def test_socket_table_unreadable_is_unknown_never_green(toggles):
    toggles["fake"]["ss_tcp"] = ""                 # ss לא רץ
    rows = rows_of(toggles)
    for pid in ("http_boot", "http_console", "interserver"):
        assert rows[pid]["listening"] is None
        assert rows[pid]["state"] == "unknown"
        assert "לא נקרא" in rows[pid]["detail"]


def test_bind_is_null_when_not_read_and_empty_only_when_read_and_not_listening(toggles, classrooms_on):   # ‏#1303: 4011 — v1.2
    """‏#1039: ‏`bind` הגיע כ-`[]` גם כשטבלת הסוקטים לא נקראה — "לא נקרא"
    קופל ל"לא מאזין", והקונסולה לא יכלה להבחין (עיקרון 5)."""
    fake = toggles["fake"]
    fake["ss"], fake["ss_tcp"] = "", ""               # ss לא רץ, בשני הפרוטוקולים
    rows = rows_of(toggles)
    for pid in ("tftp", "dhcp", "pxe_proxy", "http_boot", "http_console", "interserver"):
        assert rows[pid]["bind"] is None, (pid, rows[pid]["bind"])

    fake["ss"], fake["ss_tcp"] = SS_HEAD, SS_HEAD     # נקרא, ואף אחד לא מאזין
    rows = rows_of(toggles)
    for pid in ("tftp", "dhcp", "pxe_proxy", "http_boot", "http_console", "interserver"):
        assert rows[pid]["bind"] == [], (pid, rows[pid]["bind"])


# --- המתג ---------------------------------------------------------------------


def test_api_port_off_closes_the_listener_and_reads_back(toggles):
    """הגדרת ה"גמור" של #996: כיבוי 8443 → ‏ss (מוזרק) לא מראה מאזין +
    ‏`/ports.enabled=false`; הדלקה → חוזר."""
    resp = toggles["admin"].put("/api/console/ports/interserver", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["applied"] is True
    assert body["port"]["enabled"] is False and body["port"]["listening"] is False
    assert toggles["listeners"].calls == [("interserver", False)]
    rows = rows_of(toggles)
    assert rows["interserver"]["enabled"] is False
    assert rows["interserver"]["bind"] == [] and rows["interserver"]["listening"] is False
    assert rows["interserver"]["state"] == "off"
    assert "מפעיל" in rows["interserver"]["detail"]

    resp = toggles["admin"].put("/api/console/ports/interserver", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["ok"] is True
    rows = rows_of(toggles)
    assert rows["interserver"]["enabled"] is True and rows["interserver"]["listening"] is True
    assert rows["interserver"]["state"] == "ok"


def test_the_state_is_saved_so_it_survives_a_restart(toggles):
    from server.net import ports
    assert ports.enabled(toggles["conn"], "interserver") is True
    toggles["admin"].put("/api/console/ports/interserver", json={"enabled": False})
    assert ports.enabled(toggles["conn"], "interserver") is False
    journal = [r["event"] for r in toggles["conn"].execute(
        "SELECT event FROM journal ORDER BY id DESC LIMIT 3").fetchall()]
    assert "port_toggle" in journal


def test_http_boot_off_needs_the_server_name(toggles):
    """‏8080 כבוי = אין PXE ואין סוכנים — הרסני, מאחורי הקלדת שם (עיקרון 7)."""
    put = lambda body: toggles["admin"].put("/api/console/ports/http_boot", json=body)  # noqa: E731
    assert put({"enabled": False}).status_code == 400
    assert put({"enabled": False, "confirm": "wrong"}).status_code == 400
    assert toggles["listeners"].calls == []
    resp = put({"enabled": False, "confirm": server_name(toggles)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["port"]["off_means"]
    assert toggles["listeners"].calls == [("http_boot", False)]
    # הדלקה חזרה היא הכיוון אל ברירת המחדל — בלי הקלדה.
    assert put({"enabled": True}).status_code == 200


def test_a_disabled_boot_port_is_off_by_operator_in_health_not_a_warning(toggles):
    toggles["admin"].put("/api/console/ports/http_boot",
                         json={"enabled": False, "confirm": server_name(toggles)})
    toggles["fake"]["http"] = None                 # ואכן לא עונה
    checks = {c["id"]: c for c in toggles["admin"].get("/api/console/health").json()}
    assert checks["server"]["state"] == "off"
    assert "מפעיל" in checks["server"]["detail"]
    rows = rows_of(toggles)
    assert rows["http_boot"]["state"] == "off"


def test_the_console_cannot_be_closed_through_itself(tmp_path, images_root, clock):
    """"הדלת האחרונה" — בקשה שמגיעה דרך 8081 לסגור את 8081 מסורבת בשמו."""
    t = _build(tmp_path, images_root, clock, FakeListeners(), base_port=8081)
    resp = t["admin"].put("/api/console/ports/http_console",
                          json={"enabled": False, "confirm": server_name(t)})
    assert resp.status_code == 409, resp.text
    assert "8081" in resp.json()["detail"]
    assert t["listeners"].calls == []
    assert t["listeners"].is_open("http_console")


def test_api_port_needs_no_name(toggles):
    assert toggles["admin"].put("/api/console/ports/interserver",
                                json={"enabled": False}).status_code == 200


def test_deploy_user_gets_403_and_the_listener_is_untouched(toggles):
    resp = toggles["deploy"].put("/api/console/ports/interserver", json={"enabled": False})
    assert resp.status_code == 403
    assert toggles["listeners"].calls == []


def test_unknown_port_is_404(toggles):
    assert toggles["admin"].put("/api/console/ports/nope",
                                json={"enabled": False}).status_code == 404


def test_rows_toggled_elsewhere_refuse_here_and_point_there(toggles):
    for pid, url in (("dhcp", "/api/console/net/interfaces/{name}"),
                     ("monitor", "/api/console/monitor/settings")):
        resp = toggles["admin"].put(f"/api/console/ports/{pid}", json={"enabled": False})
        assert resp.status_code == 409, pid
        assert url in resp.json()["detail"]
    resp = toggles["admin"].put("/api/console/ports/multicast", json={"enabled": False})
    assert resp.status_code == 409
    assert toggles["listeners"].calls == []


def test_open_that_fails_to_bind_is_reported_not_faked(toggles):
    """‏DB אומר "דלוק", הפורט תפוס על ידי אחר: ‏ok=false עם הסיבה, המצב
    השמור נשאר "דלוק" (זו הבקשה), ו-/ports מראה את הפער באדום."""
    toggles["admin"].put("/api/console/ports/interserver", json={"enabled": False})
    toggles["listeners"].fail_open.add("interserver")
    resp = toggles["admin"].put("/api/console/ports/interserver", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False and body["applied"] is False
    assert "already in use" in body["detail"]
    rows = rows_of(toggles)
    assert rows["interserver"]["enabled"] is True and rows["interserver"]["listening"] is False
    assert rows["interserver"]["state"] == "bad"


def test_without_a_listener_manager_the_switch_says_it_did_not_apply(
        tmp_path, images_root, clock):
    """‏create_app בבדיקות אינו מריץ מאזינים — המתג נשמר, ואומר במפורש
    שלא הוחל עכשיו. לא "ok" על מה שלא קרה (עיקרון 5)."""
    t = _build(tmp_path, images_root, clock, None)
    resp = t["admin"].put("/api/console/ports/http_boot",
                          json={"enabled": False, "confirm": server_name(t)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is False and body["applied"] is False
    assert body["port"]["enabled"] is False
    from server.net import ports
    assert ports.enabled(t["conn"], "http_boot") is False


def test_interserver_without_a_listener_has_no_switch(tmp_path, images_root, clock):
    """‏8443 עולה רק על משני עם --interserver-host; בלעדיו השורה קיימת,
    אומרת שאינה מוגדרת, ואין לה מתג."""
    t = _build(tmp_path, images_root, clock,
               FakeListeners(("http_boot", "http_console")))
    rows = rows_of(t)
    assert rows["interserver"]["toggle"] == "none"
    assert rows["interserver"]["state"] == "off"
    t2 = _build(tmp_path / "b", images_root, clock,
                FakeListeners(("http_boot", "http_console", "interserver")))
    rows = rows_of(t2)
    assert rows["interserver"]["toggle"] == "api"
    assert rows["interserver"]["bind"] == ["10.30.0.8:8443"]
    assert rows["interserver"]["state"] == "ok"


# --- ‏#1013: TFTP 69 — מתג מאחורי הקלדת שם השרת, dnsmasq בצד השרת -------------


def test_tftp_row_carries_a_confirm_switch_and_the_warning(toggles):
    """אותה צורה כמו 8080/8081 (#996): toggle/enabled/confirm_word/confirm_when,
    ובנוסף warning_he — הטקסט שהקונסולה מציגה לפני הקלדת השם (נדב 19/09)."""
    row = rows_of(toggles)["tftp"]
    assert row["toggle"] == "confirm" and row["enabled"] is True
    assert row["toggle_url"] == "/api/console/ports/tftp"
    assert row["confirm_word"] == server_name(toggles) and row["confirm_when"] == "off"
    assert row["warning_he"] == ("כיבוי 69 (TFTP) עוצר את ה-PXE: מחשבי בנייה ושיכפול "
                                 "לא יעלו מהשרת.")
    assert row["state"] == "ok" and row["listening"] is True and row["bind"] == ["0.0.0.0:69"]


def test_tftp_off_rewrites_dnsmasq_and_reads_back_from_ss(toggles):
    """הגדרת ה"גמור": כיבוי → הקובץ שהשרת מרנדר בלי enable-tftp, dnsmasq
    מופעל מחדש (hook), ו-ss (מוזרק) לא מראה 69; הדלקה → חוזר. הראיה היא
    הסוקט, לא ה-DB."""
    from server.net import dhcp
    put = lambda body: toggles["admin"].put("/api/console/ports/tftp", json=body)  # noqa: E731
    resp = put({"enabled": False, "confirm": server_name(toggles)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True and body["applied"] is True and body["verified"] is True
    assert len(toggles["dnsmasq"].applied) == 1
    written = toggles["dnsmasq"].applied[-1]
    assert not dhcp.installer_serves_tftp(written) and "TFTP off" in written
    assert not dhcp.installer_serves_tftp(toggles["dnsmasq"].proxy[-1][0])
    assert toggles["listeners"].calls == []                 # לא מאזין של התהליך
    row = rows_of(toggles)["tftp"]
    assert row["enabled"] is False and row["listening"] is False
    assert row["state"] == "off" and "מפעיל" in row["detail"]
    # הדלקה חזרה — בלי הקלדה (הכיוון אל ברירת המחדל)
    resp = put({"enabled": True})
    assert resp.status_code == 200 and resp.json()["ok"] is True
    written = toggles["dnsmasq"].applied[-1]
    assert dhcp.installer_serves_tftp(written) and "tftp-root=/srv/tftp" in written
    row = rows_of(toggles)["tftp"]
    assert row["enabled"] is True and row["state"] == "ok"


def test_tftp_off_needs_the_server_name(toggles):
    put = lambda body: toggles["admin"].put("/api/console/ports/tftp", json=body)  # noqa: E731
    assert put({"enabled": False}).status_code == 400
    assert put({"enabled": False, "confirm": "wrong"}).status_code == 400
    assert toggles["dnsmasq"].applied == []
    assert rows_of(toggles)["tftp"]["enabled"] is True


def test_tftp_switch_survives_a_restart(toggles):
    from server.net import ports
    toggles["admin"].put("/api/console/ports/tftp",
                         json={"enabled": False, "confirm": server_name(toggles)})
    assert ports.enabled(toggles["conn"], "tftp") is False


def test_tftp_three_measured_states_off_is_never_faked(toggles):
    """עיקרון 5: "כבוי" (המתג) ≠ "לא מאזין" ≠ "לא נקרא" — ופער נצבע אדום."""
    # 1. המתג דלוק, אף אחד לא מגיש — אדום (כמו עד היום)
    toggles["fake"]["ss"] = SS_DHCP_ONLY
    row = rows_of(toggles)["tftp"]
    assert row["state"] == "bad" and row["listening"] is False and row["enabled"] is True
    # 2. המתג כבוי אבל dnsmasq עדיין מגיש (הכיבוי לא תפס) — אדום, לא "כבוי"
    toggles["dnsmasq"].fail = True
    resp = toggles["admin"].put("/api/console/ports/tftp",
                                json={"enabled": False, "confirm": server_name(toggles)})
    assert resp.status_code == 200 and resp.json()["ok"] is False
    assert "dnsmasq" in resp.json()["detail"]
    toggles["fake"]["ss"] = SS_UDP
    row = rows_of(toggles)["tftp"]
    assert row["enabled"] is False and row["listening"] is True
    assert row["state"] == "bad" and "לא תפס" in row["detail"]
    journal = [r["event"] for r in toggles["conn"].execute(
        "SELECT event FROM journal ORDER BY id DESC LIMIT 4").fetchall()]
    assert "port_unverified" in journal
    # 3. טבלת הסוקטים לא נקראה — לא ידוע, לא ירוק ולא "כבוי"
    toggles["fake"]["ss"] = ""
    row = rows_of(toggles)["tftp"]
    assert row["state"] == "unknown" and row["listening"] is None


def test_old_installer_file_means_no_switch_and_says_why(tmp_path, images_root, clock):
    """שדרוג בלי להריץ את המתקין (המעבדה): imagesrv.conf עדיין נושא
    enable-tftp, ו-dnsmasq מצטבר — המתג לא יכול לכבות. אז אין מתג, בשם;
    ‏PUT מסורב; המצב הנמדד נשאר ss."""
    t = _build(tmp_path, images_root, clock, FakeListeners(), installer_conf=INSTALLER_OLD)
    row = rows_of(t)["tftp"]
    assert row["toggle"] == "none" and row["enabled"] is None
    assert "imagesrv.conf" in row["off_means"] and "המתקין" in row["off_means"]
    assert row["state"] == "ok" and row["listening"] is True
    resp = t["admin"].put("/api/console/ports/tftp",
                          json={"enabled": False, "confirm": server_name(t)})
    assert resp.status_code == 409 and t["dnsmasq"].applied == []


def test_unreadable_installer_file_is_not_treated_as_switchable(tmp_path, images_root, clock):
    """"לא הצלחנו לקרוא" אינו "אין שם enable-tftp" (עיקרון 5, הרחבה 5א)."""
    t = _build(tmp_path, images_root, clock, FakeListeners(), installer_conf=None)
    row = rows_of(t)["tftp"]
    assert row["toggle"] == "none" and "לא נקרא" in row["off_means"]
    assert t["admin"].put("/api/console/ports/tftp",
                          json={"enabled": False, "confirm": server_name(t)}).status_code == 409


def test_no_installer_file_at_all_is_switchable(tmp_path, images_root, clock):
    """אין קובץ (ראיה חיובית, `""`) — אין מה שיישא enable-tftp: יש מתג."""
    t = _build(tmp_path, images_root, clock, FakeListeners(), installer_conf="")
    assert rows_of(t)["tftp"]["toggle"] == "confirm"


def test_tftp_switch_is_admin_only(toggles):
    resp = toggles["deploy"].put("/api/console/ports/tftp",
                                 json={"enabled": False, "confirm": server_name(toggles)})
    assert resp.status_code == 403 and toggles["dnsmasq"].applied == []


def test_dhcp_apply_from_the_net_tab_honours_the_tftp_switch(toggles):
    """מקום אחד כותב את הקובץ: כיבוי TFTP ואחריו הדלקת DHCP מהלשונית — הקובץ
    החדש עדיין בלי enable-tftp (אחרת שינוי DHCP היה מדליק 69 בשקט)."""
    from server.net import dhcp
    toggles["admin"].put("/api/console/ports/tftp",
                         json={"enabled": False, "confirm": server_name(toggles)})
    resp = toggles["admin"].put("/api/console/net/interfaces/eth0", json={
        "enabled": True, "range_start": "10.44.0.50", "range_end": "10.44.0.200",
        "netmask": "255.255.255.0", "server_ip": "10.44.0.1", "confirm": "eth0",
        "ignore_existing": True})
    assert resp.status_code == 200, resp.text
    written = toggles["dnsmasq"].applied[-1]
    assert "dhcp-range=set:if-eth0" in written and not dhcp.installer_serves_tftp(written)
    assert rows_of(toggles)["tftp"]["state"] == "off"
