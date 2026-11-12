"""#2913: restart של dnsmasq שנכשל מחזיר את הקובץ הישן — וה-DB נשאר עם החדש.

מה שנבדק כאן הוא **מה שהמפעיל רואה**: אחרי שמירה שה-restart שלה נכשל, הקובץ על
הדיסק הוא הישן (ו-dnsmasq עולה איתו), וה-DB נושא את הטווח החדש. עד #2913
`dhcp_diverged` השווה רק "האם הכרטיס מופיע בקובץ" — שניהם הכילו אותו, ולכן
הקונסולה הציגה "משרת" בלי סימן סטייה, בזמן שהלקוחות קיבלו את הטווח הישן.

אין כאן שום מגע במכונה: `dhcp.apply` האמיתי (כולל החזרת הקובץ) רץ על קובץ בתיקיית
tmp, ו-`systemctl`/`dnsmasq --test` מוחלפים בכפילים (`dhcp_hooks`, כמו בכל טסט DHCP).
"""

from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
except ImportError:                                           # pragma: no cover
    TestClient = None

from server.net import dhcp, dhcp_host

GOOD = dict(
    enabled=True, range_start="10.44.9.50", range_end="10.44.9.200",
    netmask="255.255.255.0", gateway="10.44.9.1", dns=["10.44.0.5"],
    lease="12h", server_ip="10.44.9.10",
)
RESTART_FAILED = "שירות הרשת dnsmasq לא הגיב ל-restart — הפרטים ביומן השרת"


@pytest.fixture()
def lab(tmp_path: Path, images_root: Path, clock, monkeypatch):
    """שרת אחד, קובץ conf אמיתי בתיקיית tmp, ו-`systemctl restart` שהבדיקה שולטת בו."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from server.app import create_app
    from server.console import users

    conf = tmp_path / "etc" / "imagesrv-dhcp.conf"
    state = {"restarts": [], "script": []}      # script: מה כל restart ממוספר מחזיר

    def fake_systemctl(action, unit):
        if action == "restart" and unit == "dnsmasq":
            state["restarts"].append(conf.read_text(encoding="utf-8")
                                     if conf.exists() else None)
            return state["script"].pop(0) if state["script"] else None
        return None

    monkeypatch.setattr(dhcp_host, "_systemctl", fake_systemctl)
    monkeypatch.setattr(dhcp_host, "_dnsmasq_test", lambda cmd: None)
    hooks = {
        "interfaces": lambda: [
            {"name": "eth0", "state": "up", "mac": "aa:aa:aa:aa:aa:00",
             "addresses": ["10.44.9.10/24"]},
            {"name": "eth1", "state": "up", "mac": "aa:aa:aa:aa:aa:01",
             "addresses": ["10.44.1.10/24"]}],
        "probe": lambda name: dhcp.ProbeResult(True, (), ""),
        "apply": lambda text: dhcp_host.apply(text, conf),
        "apply_proxy": lambda text, active: None,
        "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
        "read_active_conf": lambda: dhcp_host.read_active_conf(conf),
        "service_active": lambda unit: True,
    }
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, dhcp_hooks=hooks,
                     nic_hooks={"firewall": lambda nic: None})
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "conf": conf, "state": state, "ctx": app.state.ctx}


def _save(admin, **change):
    return admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0", **change})


def _row(admin) -> dict:
    return {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}["eth0"]


def test_a_clean_save_is_not_diverged(lab):
    """השומר נגד תיקון-יתר: הקובץ שנכתב זהה למה שה-DB מרנדר — אין סטייה."""
    assert _save(lab["admin"]).json()["ok"] is True
    row = _row(lab["admin"])
    assert row["dhcp_live"]["state"] == "serving"
    assert row["dhcp_diverged"] is False


@pytest.mark.parametrize("change,needle_old,needle_new", [
    ({"range_end": "10.44.9.180"}, "10.44.9.200", "10.44.9.180"),
    ({"gateway": "10.44.9.254"}, "option:router,10.44.9.1\n", "10.44.9.254"),
    ({"lease": "1h"}, ",12h", ",1h"),
])
def test_a_failed_restart_that_restores_the_old_file_shows_as_diverged(
        lab, change, needle_old, needle_new):
    admin, conf = lab["admin"], lab["conf"]
    assert _save(admin).json()["ok"] is True                   # הקובץ הישן, בריא
    lab["state"]["script"] = [RESTART_FAILED]                  # restart ראשון נכשל, השני עובר

    reply = _save(admin, **change).json()

    # מה שקרה בפועל: ה-DB חדש, הקובץ על הדיסק הישן, ו-dnsmasq עלה איתו.
    assert reply["ok"] is False and "הקודם הוחזר" in reply["apply_error"]
    disk = conf.read_text(encoding="utf-8")
    assert needle_old in disk and needle_new not in disk
    # הסיבה שהטסט הזה קיים: לפני #2913 שני אלה היו `serving` ו-False.
    assert reply["interface"]["dhcp_live"]["state"] == "serving"
    assert reply["interface"]["dhcp_diverged"] is True
    row = _row(admin)
    assert row["dhcp_live"]["state"] == "serving"
    assert row["dhcp_diverged"] is True


def test_saving_again_after_a_failed_restart_clears_the_divergence(lab):
    admin = lab["admin"]
    assert _save(admin).json()["ok"] is True
    lab["state"]["script"] = [RESTART_FAILED]
    assert _save(admin, range_end="10.44.9.180").json()["ok"] is False
    assert _row(admin)["dhcp_diverged"] is True

    assert _save(admin, range_end="10.44.9.180").json()["ok"] is True   # ניסיון חוזר מצליח
    assert "10.44.9.180" in lab["conf"].read_text(encoding="utf-8")
    assert _row(admin)["dhcp_diverged"] is False


def test_a_divergence_on_one_card_is_not_painted_on_the_others(lab):
    """הסטייה לכל כרטיס לפי הקטע שלו בקובץ: כרטיס כבוי לא הופך 'לא תואם' בגלל שכן."""
    admin = lab["admin"]
    assert _save(admin).json()["ok"] is True
    lab["state"]["script"] = [RESTART_FAILED]
    assert _save(admin, range_end="10.44.9.180").json()["ok"] is False
    rows = {r["name"]: r for r in admin.get("/api/console/net/interfaces").json()}
    assert rows["eth0"]["dhcp_diverged"] is True
    assert [n for n, r in rows.items() if n != "eth0" and r["dhcp_diverged"]] == []


def test_a_render_that_cannot_run_is_not_reported_as_no_divergence(lab):
    """עיקרון 5: 'לא הצלחנו לרנדר מה שאמור לרוץ' אינו 'בדקנו — תקין'.
    רשומה **דלוקה** פסולה ב-DB (שם כרטיס עם רווח) מכשילה את ה-render כולו."""
    from server.db import set_setting
    admin = lab["admin"]
    assert _save(admin).json()["ok"] is True
    set_setting(lab["ctx"].conn, "dhcp:eth9 bad", dhcp.InterfaceConfig("eth9 bad", enabled=True).to_json())

    row = _row(admin)
    assert row["dhcp_live"]["content_checked"] is False
    assert row["dhcp_live"]["detail"]
