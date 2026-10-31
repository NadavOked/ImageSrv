"""‏#141: מכונה לא רשומה לא מקבלת GRUB בכלל — לא רק דיסק מקומי בתוכו.

‏dhcp.render() (‏test_server_dhcp.py) מוכיח את הצד הטהור: dhcp-boot דורש
tag:known. הבדיקות כאן מוכיחות את הצד השני — שהוספה/ייבוא/מחיקה של
מכונה מהקונסולה באמת כותבות מחדש את KNOWN_MACS_CONF ומבקשות מ-dnsmasq
לקרוא אותו מחדש, וש-**אף בדיקה אחרת בחבילה אינה נוגעת במכונה** כשהיא
לא ביקשה זאת במפורש: ה-hook הוא `None` כברירת מחדל, בדיוק כדי שהוספת
מכונה בעשרות בדיקות קיימות (‏setup_classroom) לא תכתוב ל-
/etc/imagesrv/known-macs ולא תריץ systemctl בכל ריצת pytest.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

#: הכרעת נדב 11/10: השרת מסרב לכיתות ב-v1. הבדיקות כאן יוצרות קבוצת
#: כיתה ובודקות את זרימת v1.2 — ולכן מדליקות את היכולת (`classrooms_on`).
pytestmark = pytest.mark.usefixtures("classrooms_on")

from fastapi.testclient import TestClient

from server.net import dhcp
from server.console import users
from server.app import create_app


def _events(server) -> list[str]:
    return [row["event"] for row in server["admin"].get("/api/console/journal").json()]


@pytest.fixture()
def known_macs_server(tmp_path, images_root, clock):
    """כמו הפיקסטורה `server` הרגילה, אבל עם known_macs_hooks מוזרק —
    ‏hook מזויף שרק רושם מה נכתב, ולעולם לא נוגע במכונה."""
    fake = {"applied": [], "error": None}
    hooks = {"apply": lambda text: (fake["applied"].append(text), fake["error"])[1]}
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, known_macs_hooks=hooks)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    return {"admin": admin, "fake": fake, "ctx": app.state.ctx}


# --- ברירת המחדל: hooks=None לא נוגע במכונה -----------------------------------


def test_adding_a_machine_with_no_hooks_injected_touches_nothing(server):
    """זו ברוב בדיקות החבילה — `server` הרגילה, בלי known_macs_hooks.
    הוספת מכונה לא זורקת ולא מנסה לכתוב לדיסק/systemctl (hooks=None)."""
    admin = server["admin"]
    assert admin.post(
        "/api/console/groups",
        json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"},
    ).status_code == 200
    r = admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:dd:ee:01", "name": "05", "group_id": "grp_LAB1",
    })
    assert r.status_code == 200, r.text
    assert "known_macs_apply_failed" not in _events(server)


# --- כתיבה בפועל, עם hooks מוזרקים -------------------------------------------


def test_add_machine_syncs_known_macs(known_macs_server):
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    r = admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:dd:ee:01", "name": "05", "group_id": "grp_LAB1",
    })
    assert r.status_code == 200
    assert fake["applied"], "לא נכתב קובץ known-macs אחרי הוספת מכונה"
    assert "aa:bb:cc:dd:ee:01,set:known" in fake["applied"][-1]


def test_import_syncs_known_macs_once_for_the_whole_batch(known_macs_server):
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    r = admin.post("/api/console/machines/import", json={
        "group_id": "grp_LAB1",
        "text": "b4:2e:99:07:1a:c4 05\nb4:2e:99:07:1a:c5 06\n",
    })
    assert r.status_code == 200 and r.json()["saved"] == 2
    text = fake["applied"][-1]
    assert "b4:2e:99:07:1a:c4,set:known" in text
    assert "b4:2e:99:07:1a:c5,set:known" in text


def test_import_that_saves_nothing_does_not_sync(known_macs_server):
    """כל השורות נדחו — אף MAC לא השתנה, ואין טעם לכתוב ולבקש reload.

    ‏fake["applied"] אינו ריק כשהבדיקה מתחילה: create_runtime עצמו כבר
    כתב סנכרון ראשוני ריק (עיקרון 5 — הקובץ נכתב מחדש בעליית השרת).
    מה שנבדק כאן הוא שהניסיון-שנכשל לא **הוסיף** כתיבה נוספת."""
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    before = len(fake["applied"])
    r = admin.post("/api/console/machines/import", json={
        "group_id": "grp_LAB1", "text": "not-a-mac 05\n",
    })
    assert r.status_code == 200 and r.json()["saved"] == 0
    assert len(fake["applied"]) == before


def test_delete_machine_syncs_known_macs_and_removes_it(known_macs_server):
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:dd:ee:01", "name": "05", "group_id": "grp_LAB1",
    })
    fake["applied"].clear()

    r = admin.delete("/api/console/machines/aa:bb:cc:dd:ee:01")
    assert r.status_code == 200
    assert fake["applied"], "המחיקה לא כתבה מחדש את known-macs"
    assert "aa:bb:cc:dd:ee:01" not in fake["applied"][-1]


def test_a_sync_failure_is_journaled_in_hebrew(known_macs_server):
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    fake["error"] = "dnsmasq לא הגיב ל-reload"
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    r = admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:dd:ee:01", "name": "05", "group_id": "grp_LAB1",
    })
    rows = admin.get("/api/console/journal").json()
    row = next(r for r in rows if r["event"] == "known_macs_apply_failed")
    assert "dnsmasq" in row["text"]
    # ‏#857: היומן לבדו קיבע שתיקה — התשובה חייבת להגיד את זה גם היא.
    assert r.status_code == 200 and r.json()["network"]["applied"] is False


# --- ‏#857: כשל ה-apply אינו נבלע ב-200 נקי ----------------------------------
#
# ה-DB עודכן והרשת לא: מכונה חדשה **לא תקבל GRUB** (fail-closed של #141
# פועל "נגדנו" בשקט), ומכונה שנמחקה עדיין תקבל. המפעיל ראה הצלחה. לא
# מגלגלים את ה-DB אחורה (המפעיל ביקש את המכונה) ולא מקפלים ל-500 סתמי:
# ‏200, והתשובה **מבחינה** — `network` אומר אם ה-DHCP עודכן ולמה לא.


def _add(admin, mac="aa:bb:cc:dd:ee:01"):
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    return admin.post("/api/console/machines", json={
        "mac": mac, "name": "05", "group_id": "grp_LAB1"})


def test_add_machine_reports_a_failed_dhcp_update(known_macs_server):
    """**לב #857.** המכונה נשמרה, ה-DHCP לא — והתשובה אומרת בדיוק את זה."""
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    fake["error"] = "dnsmasq לא הגיב ל-reload"
    r = _add(admin)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mac"] == "aa:bb:cc:dd:ee:01", "המכונה לא נשמרה"
    assert body["network"] == {"applied": False,
                               "error": "dnsmasq לא הגיב ל-reload"}, body
    macs = {m["mac"] for m in admin.get("/api/console/machines").json()}
    assert "aa:bb:cc:dd:ee:01" in macs, "ה-DB גולגל אחורה — אסור"


def test_add_machine_reports_a_successful_dhcp_update(known_macs_server):
    r = _add(known_macs_server["admin"])
    assert r.status_code == 200
    assert r.json()["network"] == {"applied": True, "error": None}


def test_add_machine_without_hooks_reports_network_null(server):
    """בלי hooks (בדיקות; ריצה ללא dnsmasq) — לא הופעל ולא נכשל: `null`."""
    r = _add(server["admin"])
    assert r.status_code == 200
    assert "network" in r.json() and r.json()["network"] is None


def test_delete_machine_reports_a_failed_dhcp_update(known_macs_server):
    """מחיקה שלא הגיעה ל-dnsmasq = מכונה שנמחקה ועדיין מקבלת GRUB."""
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    assert _add(admin).status_code == 200
    fake["error"] = "known-macs: Permission denied"
    r = admin.delete("/api/console/machines/aa:bb:cc:dd:ee:01")
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "network": {
        "applied": False, "error": "known-macs: Permission denied"}}


def test_delete_machine_reports_a_successful_dhcp_update(known_macs_server):
    admin = known_macs_server["admin"]
    assert _add(admin).status_code == 200
    r = admin.delete("/api/console/machines/aa:bb:cc:dd:ee:01")
    assert r.json() == {"ok": True, "network": {"applied": True, "error": None}}


def test_import_reports_a_failed_dhcp_update(known_macs_server):
    admin, fake = known_macs_server["admin"], known_macs_server["fake"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    fake["error"] = "dnsmasq לא הגיב ל-reload"
    r = admin.post("/api/console/machines/import", json={
        "group_id": "grp_LAB1", "text": "b4:2e:99:07:1a:c4 05\n"})
    assert r.status_code == 200 and r.json()["saved"] == 1
    assert r.json()["network"] == {"applied": False,
                                   "error": "dnsmasq לא הגיב ל-reload"}


def test_import_that_saves_nothing_reports_network_null(known_macs_server):
    """אין מה להחיל — לא הופעל (כמו בלי hooks), לא "הצליח"."""
    admin = known_macs_server["admin"]
    admin.post("/api/console/groups",
              json={"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    r = admin.post("/api/console/machines/import", json={
        "group_id": "grp_LAB1", "text": "not-a-mac 05\n"})
    assert r.json()["saved"] == 0 and r.json()["network"] is None


# --- ‏#857: הקונסולה מציגה את זה --------------------------------------------


STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
WARNING = "המכונה נשמרה, אבל ה-DHCP לא עודכן"


def test_both_console_screens_warn_when_the_dhcp_update_failed():
    """כל זרימה שמוסיפה/מוחקת מכונה — הוספה, הדבקה, הסרה מהמגירה, הסרה
    מרובה — קוראת את `network.error` ומציגה את האזהרה. מאז #954 גל 3 כולן
    ב-`console.js` (‏`machines.js` נמחק). בדיקת תוכן: אין דפדפן בחבילה,
    וה-JS הוא vanilla."""
    console_js = (STATIC / "console.js").read_text(encoding="utf-8")
    assert "r.network" in console_js and WARNING in console_js
    assert not (STATIC / "machines.js").exists(), "machines.js חזר — מי קורא לו?"
    calls = console_js.count("dhcpNotice(") - console_js.count("function dhcpNotice(")
    assert calls >= 4, f"console.js בולע את כשל ה-DHCP ({calls} קריאות)"


def test_the_console_assets_were_bumped_together():
    """‏gotcha ידוע: JS ישן מול API חדש. כל ה-`?v=` ב-index.html זהים
    ו-≥ 4.6 (הבאמפ של #857)."""
    import re   # noqa: PLC0415
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    versions = set(re.findall(r"\?v=([0-9.]+)", page))
    assert len(versions) == 1, versions
    version = versions.pop()
    major, minor = (int(x) for x in version.split("."))
    assert (major, minor) >= (4, 6), version


# --- מכונה לא רשומה: /boot/menu עדיין מתעד ביומן (רשת ביטחון שנייה) ----------


def test_menu_request_for_an_unknown_mac_is_journaled_on_the_deploy_vlan(server):
    """‏#141: גם אם DHCP לא עצר אותה (תרחיש 3 — proxy מרשת זרה), בקשת
    התפריט על וילן ההפצה עצמה עדיין נרשמת ביומן — לא רק hello."""
    r = server["anon"].get("/boot/menu?mac=aa:bb:cc:dd:ee:99")
    assert r.status_code == 200
    assert "menuentry \"Boot from local disk\"" in r.text
    assert "unknown_mac" in _events(server)
