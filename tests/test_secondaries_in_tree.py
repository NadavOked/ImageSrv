"""‏#936: השרתים המשניים בעץ הניווט נבנים מה-API, לא מ-HTML סטטי.

נדב ראה בעץ "שרת משנה — חיפה" עם "יוגדר בהמשך" ושאל "לא חיברת את השרת
השני?" — המשני *כן* היה מחובר (#655); מה שישב בעץ היה שארית העיצוב.

שלושה דברים נבדקים כאן:

1. תוכן סטטי — אין בעץ צומת משני קשיח, אין "יוגדר בהמשך", ושם הראשי
   אינו "תל אביב" מהעיצוב. הצמתים באים מ-`populateSidebarSecondaries`.
2. ‏`/me` נושא `server_name` לכל משתמש מחובר: שם המארח — ולעולם לא ריק
   (הצומת העליון חייב שם ממקור).
3. נדב 17/10: שם אחד — שם השרת **הוא** שם המארח. ‏`POST /settings` עם
   `server_name` נדחה (400) ומפנה ל-`/hostname`; לחיצה כפולה בעץ פותחת את
   חלון "שינוי שם השרת" (‏`hostnameSheet`).
"""

from __future__ import annotations

import socket
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"


def _read(name: str) -> str:
    return (STATIC / name).read_text(encoding="utf-8")


def test_index_html_has_no_static_secondary_server():
    page = _read("index.html")
    assert "יוגדר בהמשך" not in page
    assert "חיפה" not in page
    assert "srvHaifa" not in page
    assert "תל אביב" not in page, "שם הראשי מגיע מ-/me, לא מהעיצוב"
    assert 'id="secondaryServers"' in page
    assert 'id="primaryServerName"' in page
    # צומת שרת סטטי אחד בלבד — הראשי. המשניים נבנים בזמן ריצה.
    assert page.count('class="inventory-node server-node') == 1


def test_console_js_builds_secondaries_from_the_api_and_names_from_me():
    js = _read("console.js")
    assert "populateSidebarSecondaries" in js
    assert 'api("/storage-nodes")' in js
    assert "ME.server_name" in js
    assert "imagesrv-server-names" not in js, "שם השרת נשמר בשרת, לא ב-localStorage"
    # נדב 17/10: הלחיצה הכפולה בעץ פותחת את אותו חלון כמו "שינוי שם…" בהגדרות — ‏POST /hostname.
    assert "hostnameSheet(true)" in js
    assert 'post("/hostname", { hostname: v.hostname })' in js
    assert "server_name: name" not in js, "no separate display-name setting is written any more"


def test_me_reports_a_server_name_for_every_user(server):
    from server.console import users

    admin, app = server["admin"], server["app"]
    me = admin.get("/api/console/me").json()
    assert me["server_name"] == socket.gethostname(), "בלי הגדרה — שם המארח"
    assert me["server_name"]

    # ‏#1073: "לכל משתמש" = לכל משתמש **קונסולה**; ל-deploy אין /me (403).
    users.create(app.state.ctx.conn, "noc2", "admin-pass-456", "admin", by="test",
                 is_builtin=True, check_policy=False)
    from fastapi.testclient import TestClient

    second = TestClient(app)
    second.post("/api/console/login", json={"username": "noc2", "password": "admin-pass-456"})
    assert second.get("/api/console/me").json()["server_name"] == socket.gethostname()
    assert server["deploy"].get("/api/console/me").status_code == 403


def test_server_name_is_the_host_name_and_is_not_a_separate_setting(server):
    """נדב 17/10: שם אחד לתצוגה ולרשת. ‏`POST /settings` עם `server_name` נדחה
    בשם (לא נבלע ב-200), ‏`/me` ממשיך להחזיר את שם המארח — גם כשבמסד יש
    ערך ישן מ-#936."""
    from server.db import set_setting

    admin, deploy = server["admin"], server["deploy"]
    resp = admin.post("/api/console/settings", json={"server_name": "שרת ראשי — קמפוס"})
    assert resp.status_code == 400 and "/hostname" in resp.json()["detail"]
    set_setting(server["app"].state.ctx.conn, "server_name", "ערך ישן מ-#936")
    assert admin.get("/api/console/me").json()["server_name"] == socket.gethostname()
    assert admin.get("/api/console/settings").json()["server_name"] == socket.gethostname()
    # ‏#1073: deploy אינו רואה את העץ (אין לו קונסולה) ואינו משנה את השם.
    assert deploy.get("/api/console/me").status_code == 403
    assert deploy.post("/api/console/settings",
                       json={"server_name": "x"}).status_code == 403


def test_settings_reports_the_same_server_name_the_tree_shows(server):
    """‏#1300: שדה "שם השרת" בהגדרות הופיע ריק, בזמן שהעץ והכותרת הראו את
    שם המארח (`/me` נופל אליו כשאין הגדרה, ‏`/settings` החזיר None). השדה
    מציג את מה שהשרת באמת משתמש בו — אותו ערך כמו `/me`."""
    admin = server["admin"]
    shown = admin.get("/api/console/me").json()["server_name"]
    assert shown == socket.gethostname()
    assert admin.get("/api/console/settings").json()["server_name"] == shown
