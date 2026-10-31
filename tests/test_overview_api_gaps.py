"""‏#968 — פערי ה-API של הסקירה הכללית: ‏uptime ו-deploy_ip ב-`/me`,
‏severity בכל שורת `/journal`, ו-`folder` ב-`/tasks`.

עיקרון 5 בכל שדה: מה שלא נמדד מוחזר ``null`` ומוצג בשם — לא 0, לא
127.0.0.1, ולא "info" שנראה כמו סיווג.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import deploy_net
from server.system import health
from server.console import journal_he
from server.db import journal

try:
    from fastapi.testclient import TestClient
except ImportError:                                   # pragma: no cover
    TestClient = None


# --- יחידה: קריאת /proc/uptime ------------------------------------------------


def test_read_uptime_parses_the_first_number_and_truncates(tmp_path: Path):
    path = tmp_path / "uptime"
    path.write_text("532802.37 4155698.11\n", encoding="ascii")
    assert health.read_uptime(str(path)) == 532802


def test_read_uptime_unreadable_is_none_not_zero(tmp_path: Path):
    assert health.read_uptime(str(tmp_path / "missing")) is None
    bad = tmp_path / "bad"
    bad.write_text("not-a-number\n", encoding="ascii")
    assert health.read_uptime(str(bad)) is None
    empty = tmp_path / "empty"
    empty.write_text("", encoding="ascii")
    assert health.read_uptime(str(empty)) is None


def test_default_hooks_carry_the_uptime_reader():
    """ההזרקה לבדיקות עוברת דרך אותו מילון כמו שאר קריאות המכונה."""
    assert health.default_hooks()["uptime"] is health.read_uptime


# --- יחידה: deploy_ip ----------------------------------------------------------


def test_deploy_ip_is_the_host_of_server_base():
    state = deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080")
    assert deploy_net.deploy_ip("http://10.44.9.10:8080", state) == "10.44.9.10"
    assert deploy_net.deploy_ip("http://10.44.9.10:8080", None) == "10.44.9.10"


def test_deploy_ip_is_null_when_the_deploy_network_is_not_configured():
    """‏#1088: בלי רשת הפצה ‏server_base הוא loopback — וזו לא כתובת שתחנה
    רואה. ‏null, לא "127.0.0.1"."""
    none = deploy_net.DeployState("none", None, None)
    assert deploy_net.deploy_ip("http://127.0.0.1:8080", none) is None


# --- יחידה: severity -----------------------------------------------------------


@pytest.mark.parametrize("event,level", [
    ("capture_failed", "err"), ("login_failed", "err"), ("agent_role_refused", "err"),
    ("pull_refused", "err"), ("room_wave_lost", "err"), ("disk_failure", "err"),
    ("storage_transfer_failed", "err"),
    ("unknown_mac", "warn"), ("boot_loop_unverified", "warn"),
    ("net_rollback_unreadable", "warn"), ("report_from_nonmember", "warn"),
    ("capture_cancel", "warn"), ("dhcp_proxy_risk", "warn"), ("send_stopped", "warn"),
    ("capture_done", "ok"), ("client_done", "ok"), ("net_confirmed", "ok"),
    ("disk_failure_cleared", "ok"), ("login", "ok"), ("drivers_staged", "ok"),
    ("wol_sent", "ok"),
    ("session_open", "info"), ("machine_add", "info"), ("image_edit", "info"),
    ("no_such_event", "info"),
])
def test_severity_table(event, level):
    assert journal_he.severity(event) == level


def test_every_severity_entry_is_a_known_event():
    """אירוע שלא ב-EVENTS_HE אינו יכול לקבל חומרה — טבלה שמצביעה על אירוע
    שאינו קיים היא היוריסטיקה מתה בתחפושת."""
    listed = (journal_he._SEVERITY_ERR | journal_he._SEVERITY_WARN
              | journal_he._SEVERITY_OK)
    unknown = sorted(listed - set(journal_he.EVENTS_HE))
    assert unknown == [], unknown
    assert not (journal_he._SEVERITY_ERR & journal_he._SEVERITY_WARN)
    assert not (journal_he._SEVERITY_ERR & journal_he._SEVERITY_OK)
    assert not (journal_he._SEVERITY_WARN & journal_he._SEVERITY_OK)


# --- דרך השרת ------------------------------------------------------------------


@pytest.fixture()
def gap_server(tmp_path: Path, images_root: Path, clock):
    """שרת עם uptime מוזרק (‏health_hooks) ורשת הפצה מ-CLI; ‏``build``
    בונה גם שרת שרשת ההפצה שלו טרם הוגדרה."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.console import users
    from server.app import create_app

    reading = {"uptime": 532802}
    hooks = {"uptime": lambda: reading["uptime"]}

    def build(source: str, name: str):
        state = (deploy_net.DeployState("cli", "eth1", "http://10.44.9.10:8080")
                 if source == "cli" else deploy_net.DeployState("none", None, None))
        deploy = deploy_net.DeployContext(
            state=state, agent_port=8080, tftp_root=tmp_path / "tftp",
            repo_dir=tmp_path, servers_interface="eth0")
        base = "http://10.44.9.10:8080" if source == "cli" else "http://127.0.0.1:8080"
        app = create_app(tmp_path / f"data-{name}", images_root, base,
                         now_fn=clock, health_hooks=hooks, deploy=deploy)
        users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                     check_policy=False)
        client = TestClient(app)
        assert client.post("/api/console/login",
                           json={"username": "noc", "password": "admin-pass-123"}
                           ).status_code == 200
        return client, app.state.ctx

    return build, reading


def test_me_reports_machine_uptime_from_the_injected_reader(gap_server):
    build, reading = gap_server
    client, _ = build("cli", "a")
    assert client.get("/api/console/me").json()["uptime_seconds"] == 532802
    reading["uptime"] = 532900                      # נקרא מחדש בכל קריאה, לא מונח
    assert client.get("/api/console/me").json()["uptime_seconds"] == 532900
    reading["uptime"] = None                        # לא נקרא → null, לא 0
    assert client.get("/api/console/me").json()["uptime_seconds"] is None


def test_me_reports_the_deploy_ip_the_stations_see(gap_server):
    build, _ = gap_server
    client, _ = build("cli", "a")
    assert client.get("/api/console/me").json()["deploy_ip"] == "10.44.9.10"


def test_me_deploy_ip_is_null_when_the_deploy_network_is_not_configured(gap_server):
    build, _ = gap_server
    client, _ = build("none", "b")
    me = client.get("/api/console/me").json()
    assert "deploy_ip" in me and me["deploy_ip"] is None


def test_me_without_injection_returns_null_not_a_guess(server):
    """‏create_app בלי ``deploy`` ובלי hook — ‏deploy_ip מ-server_base (אין
    הקשר שאומר "לא הוגדרה"), ‏uptime מברירת המחדל (‏/proc/uptime: מספר על
    לינוקס, ‏null בתחנת פיתוח) — לעולם לא ערך מומצא."""
    me = server["admin"].get("/api/console/me").json()
    assert me["deploy_ip"] == "10.44.12.10"
    assert me["uptime_seconds"] is None or isinstance(me["uptime_seconds"], int)


def test_journal_rows_carry_the_server_side_severity(server):
    conn = server["ctx"].conn
    journal(conn, "capture_failed", "tsk_1 sha256 mismatch", "noc")
    journal(conn, "unknown_mac", "de:ad:be:ef:00:01", "")
    journal(conn, "capture_done", "tsk_2", "noc")
    journal(conn, "machine_add", "aa:bb:cc:dd:ee:ff name=x", "noc")
    rows = {r["event"]: r for r in server["admin"].get("/api/console/journal").json()}
    assert rows["capture_failed"]["severity"] == "err"
    assert rows["unknown_mac"]["severity"] == "warn"
    assert rows["capture_done"]["severity"] == "ok"
    assert rows["machine_add"]["severity"] == "info"
    for row in rows.values():
        assert row["severity"] in journal_he.SEVERITY_LEVELS
        # ‏#968: "רק להוסיף שדה" — המבנה הקיים לא זז. ‏#1008 הוסיף ``id``.
        assert set(row) == {"id", "ts", "user", "event", "label", "text", "severity"}


def test_tasks_carry_the_capture_folder(server):
    admin = server["admin"]
    assert admin.post("/api/console/groups",
                      json={"id": "grp_BUILD", "label": "בנייה", "role": "build"}
                      ).status_code in (200, 409)
    assert admin.post("/api/console/machines",
                      json={"mac": "aa:bb:cc:dd:ee:10", "name": "1",
                            "group_id": "grp_BUILD"}).status_code == 200
    r = admin.post("/api/console/tasks/capture",
                   json={"mac": "aa:bb:cc:dd:ee:10", "name": "Office 2024",
                         "disk": "sda", "folder": "Office"})
    assert r.status_code == 200, r.text
    tasks = admin.get("/api/console/tasks").json()
    assert tasks[0]["id"] == r.json()["id"]
    assert tasks[0]["folder"] == "Office"


def test_tasks_folder_is_empty_string_for_the_library_root(server):
    """קליטה ישנה בלי תיקייה = שורש הספרייה — ‏"" מפורש, לא ‏null ולא
    "לא ידוע". ‏#1251: קליטה חדשה בלי תיקייה נדחית (‏test_capture.py), ולכן
    השורה הישנה נכתבת ישירות ל-DB — כמו משימה שנוצרה לפני ההכרעה."""
    admin = server["admin"]
    admin.post("/api/console/groups",
               json={"id": "grp_BUILD", "label": "בנייה", "role": "build"})
    assert admin.post("/api/console/machines",
                      json={"mac": "aa:bb:cc:dd:ee:11", "name": "2",
                            "group_id": "grp_BUILD"}).status_code == 200
    assert admin.post("/api/console/tasks/capture",
                      json={"mac": "aa:bb:cc:dd:ee:11", "name": "Kali",
                            "disk": "sda", "folder": "Kali"}).status_code == 200
    conn = server["ctx"].conn
    conn.execute("UPDATE tasks SET folder = '' WHERE mac = 'aa:bb:cc:dd:ee:11'")
    conn.commit()
    assert admin.get("/api/console/tasks").json()[0]["folder"] == ""
