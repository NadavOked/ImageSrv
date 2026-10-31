"""‏#988 — נראות ראשי/משני לפי תפקיד: ארבעת הכללים של נדב (17/09) בטבלה אחת.

1. admin בראשי רואה גם את המשניים (סניפים, מכונות המשני, מוניטור דרך
   הראשי, העברות).
2. ~~deploy בראשי רואה רק מה שמותר לו (אימג'ים, סבבים) מהראשי בלבד~~ —
   **בוטל ב-#1073 (18/09): למשתמש הפצה אין קונסולה.** מה שנשאר לו הוא
   allowlist הקיוסק (אימג'ים, סבבים, חדר) ממחשב הבנייה; כאן, על
   `create_app` המשולב, הקיוסק קודם לקונסולה — ולכן `/images` 200 ו-
   `/overview` 403 `deploy_no_console`. ‏`test_roles_v1.py` הוא הצד המלא.
3. admin במשני רואה רק את השרת שלו — אין עץ סניפים, ואין "ראשי" מלבד
   מצב ה-pairing.
4. ~~deploy במשני — אותה הגבלה כמו deploy בראשי~~ — אותו ביטול (#1073).

הטבלה היא **תפקיד-שרת × תפקיד-משתמש × מסלול**, וכל תא הוא קוד תשובה
מהשרת — לא הסתרה בקונסולה. ההסתרה בעץ (#954 גל 7) נבנית לפי אותם
קודים: ‏403 (deploy) ו-409 (משני) שניהם "אין צומת", ולכן הקודים כאן
הם החוזה של הקונסולה, לא פרט מימוש.

‏WebSocket: השער נסגר **אחרי** accept (#904) בקוד 4403/4409, ולכן התא
הוא קוד הסגירה. deploy נסגר לפני שהשרת פותח TCP/מנהרה לאיש.
"""

from __future__ import annotations

import pytest

from server.interserver import storage_nodes
from server.db import now_iso, set_setting

try:
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
except Exception:                                             # noqa: BLE001
    TestClient = None

NODE = "node1"
MAC = "aa:bb:cc:dd:ee:01"
LOGINS = {"admin": ("noc", "admin-pass-123"),
          "deploy": ("labtech", "deploy-pass-1")}


@pytest.fixture()
def clients(server):
    """לקוח מחובר לכל תפקיד, מ-loopback — כדי ש-``/storage-pairing-window``
    (‏loopback בלבד, #740) ייבחן על התפקיד ולא על כתובת ה-peer.
    משני אחד רשום (פעיל, עם credential שאינו קיים — אין רשת בטסט)."""
    conn = server["ctx"].conn
    conn.execute(
        "INSERT INTO storage_nodes (id, label, base_url, credential_ref,"
        " enrolled_at) VALUES (?, ?, ?, ?, ?)",
        (NODE, "סניף א'", "https://node1.example:8443/api/interserver/v1",
         "cred:missing", now_iso()))
    conn.commit()
    from conftest import complete_console_login
    out = {}
    for role, (user, pw) in LOGINS.items():
        client = TestClient(server["app"], client=("127.0.0.1", 40000))
        complete_console_login(client, conn, user, pw)
        out[role] = client
    return out


def _http(client, method, path):
    resp = getattr(client, method)(path, **({"json": {}} if method == "post" else {}))
    return resp.status_code


def _ws(client, _method, path):
    with pytest.raises(WebSocketDisconnect) as e:
        with client.websocket_connect(path) as ws:
            ws.receive_bytes()
    return e.value.code


#: (מסלול, ‏method, ‏קורא) → תאים: ראשי/admin, ראשי/deploy, משני/admin, משני/deploy.
#:
#: ‏admin בראשי "עובר את השער" — הקוד שם הוא מה שהמסלול עונה **אחרי**
#: הרשאה: ‏400 = גוף ריק ל-enroll/preview, ‏409 = חלון pairing אינו של
#: ראשי, ‏4500 = המשני הרשום בטסט אינו ניתן לפנייה (credential חסר).
#: ‏admin במשני עובר את השער רק בחלון ה-pairing (כלל 3) ובמוניטור המקומי.
ROWS = [
    ("/api/console/storage-nodes", "get", _http,
     (200, 403, 409, 403)),
    ("/api/console/storage-node-groups", "get", _http,
     (200, 403, 409, 403)),
    ("/api/console/storage-transfers", "get", _http,
     (200, 403, 409, 403)),
    (f"/api/console/storage-nodes/{NODE}/machines", "get", _http,
     (200, 403, 409, 403)),
    (f"/api/console/storage-nodes/{NODE}/images", "get", _http,
     (200, 403, 409, 403)),
    # ‏#1017: בדיקת חיבור בלבד — אותה שורה בטבלה כמו /machines (200 עם
    # connected:false על credential חסר — לא 5xx).
    (f"/api/console/storage-nodes/{NODE}/check", "post", _http,
     (200, 403, 409, 403)),
    (f"/api/console/storage-nodes/{NODE}/pull", "post", _http,
     (400, 403, 409, 403)),
    ("/api/console/storage-nodes/enroll/preview", "post", _http,
     (400, 403, 409, 403)),
    ("/api/console/storage-pairing-window", "get", _http,
     (409, 403, 200, 403)),
    ("/api/console/monitor/machines", "get", _http,
     (200, 403, 200, 403)),
    (f"/api/console/monitor/{MAC}", "ws", _ws,
     (4404, 4403, 4404, 4403)),
    (f"/api/console/storage-nodes/{NODE}/monitor/{MAC}", "ws", _ws,
     (4500, 4403, 4409, 4403)),
    # מה ש-deploy **כן** רואה — allowlist הקיוסק (ממחשב הבנייה) בלבד.
    ("/api/console/images", "get", _http,
     (200, 200, 200, 200)),
    # ‏#1073: קונסולה בלבד → deploy 403 (deploy_no_console), בראשי ובמשני.
    ("/api/console/overview", "get", _http,
     (200, 403, 200, 403)),
]

COLUMNS = (("standalone", "admin"), ("standalone", "deploy"),
           ("secondary", "admin"), ("secondary", "deploy"))


@pytest.mark.parametrize(
    "row", ROWS, ids=[f"{m}:{p.removeprefix('/api/console/')}" for p, m, _, _ in ROWS])
def test_visibility_matrix(server, clients, row):
    """שורה אחת מהטבלה — ארבעת התאים שלה על שרת אחד, ומדווחים **כל** תא
    שסטה, לא רק הראשון. (פרמטר לכל תא היה בונה שרת מלא 52 פעמים.)"""
    path, method, call, expected = row
    mismatched = []
    for (server_role, user_role), want in zip(COLUMNS, expected):
        set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, server_role)
        got = call(clients[user_role], method, path)
        if got != want:
            mismatched.append(f"{server_role}/{user_role}: got {got}, want {want}")
    assert not mismatched, f"{method.upper()} {path}: " + "; ".join(mismatched)


@pytest.mark.parametrize(("server_role", "user_role", "caps"), [
    ("standalone", "admin",
     {"interbranch_transfer": True, "enroll_secondary": True, "open_local_pairing": False,
      "classrooms": False, "tools": False}),
    # ‏#1073: ל-deploy אין `/me` בכלל — הקונסולה סגורה בפניו (403).
    ("standalone", "deploy", None),
    ("secondary", "admin",
     {"interbranch_transfer": False, "enroll_secondary": False, "open_local_pairing": True,
      "classrooms": False, "tools": False}),
    ("secondary", "deploy", None),
])
def test_me_capabilities_follow_the_same_table(server, clients, server_role, user_role, caps):
    """הדגלים שמהם הקונסולה בונה את העץ — אותה טבלה, מהצד של ``/me``.
    ‏admin בראשי מקבל ``interbranch_transfer`` כי יש משני פעיל אחד."""
    set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, server_role)
    resp = clients[user_role].get("/api/console/me")
    if caps is None:
        assert resp.status_code == 403, resp.text
        return
    me = resp.json()
    assert me["role"] == user_role
    assert me["capabilities"] == caps


# --- v1 בלי ארגז הכלים (הכרעת נדב 19/09; v1.1 = הכלים, #649/#1104/#928) ---------

TOOLS_BODY = {"build": [], "student": []}


def test_tools_capability_is_hardcoded_off_in_v1():
    """לא הגדרה למפעיל — קבוע בקוד, ליד `classrooms`. v1.1 מדליק."""
    from server.core import capabilities
    assert capabilities.TOOLS is False
    assert capabilities.tools() is False


def test_the_tools_api_does_not_exist_in_v1_404_by_name(server, clients):
    """הדף אינו במהדורה — 404 בשם, **לפני** הזדהות: admin, וגם אנונימי
    (שהיה מקבל 401), רואים נתיב שאינו קיים. deploy נשאר 403 —
    `deploy_no_console` (#1073) סוגר לו את הקונסולה כולה לפני הניתוב.
    **בקרה שלילית:** בלי `_edition_gate` admin מקבל 200 ואנונימי 401."""
    anon = TestClient(server["app"], client=("127.0.0.1", 40001))
    for who, client in (("admin", clients["admin"]), ("anon", anon)):
        r = client.get("/api/console/tools/catalog")
        assert r.status_code == 404, (who, r.status_code, r.text)
        assert "v1.1" in r.json()["detail"], (who, r.text)
        r = client.put("/api/console/tools/selection", json=TOOLS_BODY)
        assert r.status_code == 404, (who, r.status_code, r.text)
    assert clients["deploy"].get("/api/console/tools/catalog").status_code == 403


def test_turning_the_tools_flag_on_brings_the_api_and_the_capability_back(server, clients, monkeypatch):
    """המסלול ש-v1.1 מדליק: אותו קוד, הדגל True → ‏/me אומר `tools: true`,
    הקטלוג נקרא (admin 200), וההרשאות חוזרות למקומן (deploy 403)."""
    from server.core import capabilities
    monkeypatch.setattr(capabilities, "TOOLS", True)
    assert clients["admin"].get("/api/console/me").json()["capabilities"]["tools"] is True
    assert clients["admin"].get("/api/console/tools/catalog").status_code == 200
    assert clients["deploy"].get("/api/console/tools/catalog").status_code == 403


def test_index_html_gates_the_tools_tree_node_behind_data_cap():
    """כמו כיתות (#1081): הצומת מתחיל מוסתר, ו-`data-cap="tools"` הוא מה
    ש-showApp מדליק לפי `/me`."""
    from pathlib import Path
    page = (Path(__file__).resolve().parent.parent / "server" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'class="inventory-node hidden" data-page="tools" data-admin data-cap="tools"' in page
