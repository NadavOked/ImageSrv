"""‏#1039: שני פערי ה-API של אובייקט הרשת — מי מחובר לקונסולה, ומי חכור.

‏`GET /api/console/sessions` (admin): ‏`[{user, ip, since, last_seen}]`.
השרת **אינו** כותב ל-DB בכל בקשה (נדב): ‏`last_seen` מתעדכן לכל היותר
פעם בדקה לכל session — נמדד כאן בשעון מוזרק, מול הטבלה עצמה.

‏`GET /api/console/net/interfaces/{n}/leases` (admin): קובץ החכירות של
dnsmasq, מסונן לרשתות של הכרטיס. עיקרון 5: קובץ שלא נקרא הוא
‏`checked:false` + ‏`reason` + ‏`leases:null` — לא רשימה ריקה. הקובץ מוזרק
(‏`identity_hooks["leases"]`, כמו `dhcp_hooks`); הבדיקות לעולם לא קוראות
את ‏`/var/lib/misc/dnsmasq.leases` של המכונה שהן רצות עליה.
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

IFACES = [{"name": "eth0", "state": "up", "mac": "aa", "addresses": ["10.44.0.1/24"]},
          {"name": "eth1", "state": "up", "mac": "bb", "addresses": []}]

LEASES = (
    "1790000000 a0:48:1c:8a:18:40 10.44.0.118 cloner1 01:a0:48:1c:8a:18:40\n"
    "0 de:ad:be:ef:00:01 10.44.0.199 * *\n"
    "1790000500 11:22:33:44:55:66 192.168.7.20 office *\n"
    "duid 00:01:00:01:2c:00:00:00:bc:24:11:00:00:01\n"
)


def _build(tmp_path: Path, images_root: Path, clock, *, leases: object = "file"):
    from server.agent import identity
    from server.console import users
    from server.app import create_app

    lease_path = tmp_path / "dnsmasq.leases"
    lease_path.write_text(LEASES, encoding="utf-8", newline="\n")
    identity_hooks = None
    if leases == "file":
        identity_hooks = {"leases": identity.LeaseFile(lease_path)}
    elif leases is not None:
        identity_hooks = {"leases": leases}
    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock,
        identity_hooks=identity_hooks,
        dhcp_hooks={"apply": lambda text: pytest.fail("בדיקה נגעה ב-dnsmasq"),
                    "apply_proxy": lambda text, active: pytest.fail("בדיקה נגעה ב-dnsmasq"),
                    "interfaces": lambda: IFACES,
                    "probe": lambda name: dhcp.ProbeResult(True, ()),
                    "read_active_conf": lambda: "",
                    "service_active": lambda unit: True})
    conn = app.state.ctx.conn
    users.create(conn, "noc", "admin-pass-123", "admin", by="test", check_policy=False)
    users.create(conn, "noc2", "admin-pass-456", "admin", by="test", check_policy=False)
    users.create(conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, admin2, deploy = TestClient(app), TestClient(app), TestClient(app)
    for client, name, pw in ((admin, "noc", "admin-pass-123"), (admin2, "noc2", "admin-pass-456"),
                             (deploy, "labtech", "deploy-pass-1")):
        assert client.post("/api/console/login",
                           json={"username": name, "password": pw}).status_code == 200
    return {"admin": admin, "admin2": admin2, "deploy": deploy, "conn": conn,
            "lease_path": lease_path, "app": app}


@pytest.fixture()
def net(tmp_path: Path, images_root: Path, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    return _build(tmp_path, images_root, clock)


@pytest.fixture()
def presence_clock(monkeypatch):
    """השעון של טבלת ה-sessions — מוזרק, כדי ש"פעם בדקה" יימדד ולא יחכה."""
    import importlib
    import time

    now = {"t": time.time()}
    try:
        presence = importlib.import_module("server.console.console_presence")
    except ImportError:
        # בקרה שלילית: בלי המודול הבדיקות נופלות על ההתנהגות (404, אין
        # שורה), לא על ImportError בפיקסטורה.
        return now
    monkeypatch.setattr(presence, "_now", lambda: now["t"])
    monkeypatch.setattr(presence, "_written", {})
    return now


def _rows(conn) -> list[dict]:
    import sqlite3
    try:
        return [dict(r) for r in conn.execute(
            "SELECT username, ip, since, last_seen FROM console_sessions ORDER BY username")]
    except sqlite3.OperationalError:          # אין טבלה = אין sessions רשומים
        return []


# --- sessions -------------------------------------------------------------------


def test_admin_sees_who_is_connected_from_where_and_since_when(net, presence_clock):
    net["admin"].get("/api/console/me")
    net["admin2"].get("/api/console/me")
    resp = net["admin"].get("/api/console/sessions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert sorted(s["user"] for s in body) == ["noc", "noc2"]
    for s in body:
        assert set(s) == {"user", "ip", "since", "last_seen"}
        assert s["ip"] == "testclient"               # ‏request.client.host של TestClient
        assert s["since"] and s["last_seen"]
        assert s["since"] <= s["last_seen"]


def test_sessions_is_admin_only(net, presence_clock):
    assert net["deploy"].get("/api/console/sessions").status_code == 403
    assert TestClient(net["app"]).get("/api/console/sessions").status_code == 401


def test_last_seen_is_written_at_most_once_a_minute(net, presence_clock):
    """נדב: השרת לא כותב ל-DB בכל בקשה. בקשה אחרי 30 שניות אינה נוגעת
    בשורה; אחרי דקה — כן."""
    net["admin"].get("/api/console/me")
    first = [r for r in _rows(net["conn"]) if r["username"] == "noc"]
    assert len(first) == 1

    presence_clock["t"] += 30
    for _ in range(5):
        net["admin"].get("/api/console/me")
    assert [r for r in _rows(net["conn"]) if r["username"] == "noc"] == first

    presence_clock["t"] += 31
    net["admin"].get("/api/console/me")
    after = [r for r in _rows(net["conn"]) if r["username"] == "noc"]
    assert len(after) == 1
    assert after[0]["since"] == first[0]["since"]
    assert after[0]["last_seen"] > first[0]["last_seen"]


def test_logout_and_revocation_remove_the_session(net, presence_clock):
    from server.console import users
    net["admin"].get("/api/console/me")
    net["admin2"].get("/api/console/me")
    assert net["admin2"].post("/api/console/logout").status_code == 200
    assert [s["user"] for s in net["admin"].get("/api/console/sessions").json()] == ["noc"]

    # ביטול כל ה-sessions של המשתמש (‏auth_epoch, ‏#1075) — שורה שנשארת
    # הייתה "מחובר" על עוגייה שכבר אינה תקפה.
    net["admin2"].post("/api/console/login",
                       json={"username": "noc2", "password": "admin-pass-456"})
    net["admin2"].get("/api/console/me")
    users.bump_auth_epoch(net["conn"], "noc2")
    assert [s["user"] for s in net["admin"].get("/api/console/sessions").json()] == ["noc"]


def test_logout_revokes_a_copied_cookie_and_only_that_one(net, presence_clock, monkeypatch):
    """‏#1633: logout מחק רק את העוגייה בדפדפן. העוגייה חסרת-מצב, ולכן עותק
    שנגנב לפני היציאה נשאר תקף עד 12 שעות אחריה. אחרי התיקון השרת דוחה את
    העותק — ורק אותו: כניסה אחרת של אותו משתמש (דפדפן שני) נשארת מחוברת.

    הכניסה השנייה מוזזת בחמש שניות: אין nonce בעוגייה, ושתי כניסות של אותו
    משתמש באותה שנייה מקבלות עוגייה זהה — ו-logout של אחת מבטל את שתיהן."""
    import time
    from types import SimpleNamespace

    from server.console import auth
    stolen = net["admin2"].cookies.get(auth.COOKIE_NAME)
    assert stolen
    later = time.time() + 5
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: later))
    other = TestClient(net["app"])
    assert other.post("/api/console/login",
                      json={"username": "noc2", "password": "admin-pass-456"}).status_code == 200
    attacker = TestClient(net["app"])
    attacker.cookies.set(auth.COOKIE_NAME, stolen)
    assert attacker.get("/api/console/me").status_code == 200, "העותק תקף לפני היציאה"

    assert net["admin2"].post("/api/console/logout").status_code == 200
    assert attacker.get("/api/console/me").status_code == 401, "עותק העוגייה עדיין תקף אחרי logout"
    assert other.get("/api/console/me").status_code == 200, "logout ניתק גם דפדפן אחר של המשתמש"
    assert auth.check(net["conn"], stolen) is None


# --- leases ---------------------------------------------------------------------


def test_leases_of_a_nic_come_from_the_dnsmasq_file_filtered_to_its_network(net):
    resp = net["admin"].get("/api/console/net/interfaces/eth0/leases")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["interface"] == "eth0"
    assert body["checked"] is True and body["reason"] == ""
    assert body["path"] == str(net["lease_path"])
    by_mac = {row["mac"]: row for row in body["leases"]}
    assert set(by_mac) == {"a0:48:1c:8a:18:40", "de:ad:be:ef:00:01"}, "192.168.7.20 is not on eth0"
    assert by_mac["a0:48:1c:8a:18:40"]["ip"] == "10.44.0.118"
    assert by_mac["a0:48:1c:8a:18:40"]["hostname"] == "cloner1"
    assert by_mac["a0:48:1c:8a:18:40"]["expires"].startswith("2026-09-21T")
    assert by_mac["de:ad:be:ef:00:01"]["hostname"] is None, '"*" = no hostname'
    assert by_mac["de:ad:be:ef:00:01"]["expires"] is None, "0 = infinite lease"


def test_unreadable_lease_file_is_not_an_empty_list(net):
    net["lease_path"].unlink()
    body = net["admin"].get("/api/console/net/interfaces/eth0/leases").json()
    assert body["checked"] is False
    assert body["reason"]
    assert body["leases"] is None


def test_file_read_but_no_lease_on_this_network_is_an_empty_list(net):
    net["lease_path"].write_text("1790000500 11:22:33:44:55:66 192.168.7.20 office *\n",
                                 encoding="utf-8", newline="\n")
    body = net["admin"].get("/api/console/net/interfaces/eth0/leases").json()
    assert body["checked"] is True and body["leases"] == []


def test_nic_without_an_address_cannot_be_matched_and_says_so(net):
    body = net["admin"].get("/api/console/net/interfaces/eth1/leases").json()
    assert body["checked"] is False and body["leases"] is None
    assert body["reason"]


def test_no_lease_source_in_this_run_is_unchecked(tmp_path, images_root, clock):
    if TestClient is None:
        pytest.skip("fastapi is required")
    t = _build(tmp_path, images_root, clock, leases=None)
    body = t["admin"].get("/api/console/net/interfaces/eth0/leases").json()
    assert body["checked"] is False and body["leases"] is None and body["reason"]


def test_leases_is_admin_only_and_rejects_a_bad_name(net):
    assert net["deploy"].get("/api/console/net/interfaces/eth0/leases").status_code == 403
    assert net["admin"].get("/api/console/net/interfaces/bad%20name/leases").status_code == 400
