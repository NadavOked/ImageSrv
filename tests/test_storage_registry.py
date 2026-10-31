"""Storage Nodes — tracer #2 (#655 / #727): מרשם הקבוצות והמשניים.

CRUD לקבוצות, ניהול רשומות משניים **קיימות** (שינוי שם, שיוך, השבתה,
מחיקה), וסדר תצוגה — עם מטריצת אכיפה מלאה: admin+ראשי עובד, deploy
מקבל 403, ומשני מקבל 409 גם ב-route וגם בקריאה ישירה לשכבת ה-service.

אין כאן הוספת משני: היצירה היא enrollment (שלב נפרד בשרשרת). כמו
ב-#723, הטסטים מזריקים שורות משניים ישירות לטבלה.
"""

from __future__ import annotations

import pytest

from server.interserver import storage_nodes
from server.db import now_iso, set_setting


def _add_node(conn, node_id="node1", label="סניף א'",
              base_url=None, group_id=None, disabled_at=None):
    conn.execute(
        "INSERT INTO storage_nodes"
        " (id, label, base_url, group_id, credential_ref, enrolled_at,"
        "  disabled_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (node_id, label, base_url or f"https://{node_id}.example",
         group_id, f"cred:{node_id}", now_iso(), disabled_at),
    )
    conn.commit()


def _make_secondary(conn):
    set_setting(conn, storage_nodes.ROLE_KEY, "secondary")


# --- קבוצות: CRUD -----------------------------------------------------------

def test_group_create_list_and_default_sort(server):
    admin, conn = server["admin"], server["ctx"].conn
    first = admin.post("/api/console/storage-node-groups",
                       json={"label": "צפון"})
    second = admin.post("/api/console/storage-node-groups",
                        json={"label": "דרום"})
    assert first.status_code == 200 and second.status_code == 200
    gid1, gid2 = first.json()["id"], second.json()["id"]

    groups = admin.get("/api/console/storage-node-groups").json()
    assert [g["id"] for g in groups] == [gid1, gid2]        # לפי sort עולה
    assert [g["sort"] for g in groups] == [1, 2]
    assert all(g["nodes"] == 0 for g in groups)


def test_group_create_rejects_empty_label(server):
    admin = server["admin"]
    assert admin.post("/api/console/storage-node-groups",
                      json={"label": "  "}).status_code == 400


def test_group_rename(server):
    admin = server["admin"]
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    assert admin.put(f"/api/console/storage-node-groups/{gid}",
                     json={"label": "המרכז"}).status_code == 200
    groups = admin.get("/api/console/storage-node-groups").json()
    assert groups[0]["label"] == "המרכז"


def test_group_rename_missing_is_404(server):
    assert server["admin"].put("/api/console/storage-node-groups/nope",
                               json={"label": "x"}).status_code == 404


def test_group_reorder(server):
    admin = server["admin"]
    ids = [admin.post("/api/console/storage-node-groups",
                      json={"label": f"ק{i}"}).json()["id"] for i in range(3)]
    reversed_ids = list(reversed(ids))
    assert admin.post("/api/console/storage-node-groups/order",
                      json={"ids": reversed_ids}).status_code == 200
    groups = admin.get("/api/console/storage-node-groups").json()
    assert [g["id"] for g in groups] == reversed_ids


def test_group_delete(server):
    admin = server["admin"]
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    assert admin.delete(
        f"/api/console/storage-node-groups/{gid}").status_code == 200
    assert admin.get("/api/console/storage-node-groups").json() == []


def test_group_delete_missing_is_404(server):
    assert server["admin"].delete(
        "/api/console/storage-node-groups/nope").status_code == 404


# --- ניהול משניים קיימים ----------------------------------------------------

def test_nodes_list_includes_group_label(server):
    admin, conn = server["admin"], server["ctx"].conn
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    _add_node(conn, group_id=gid)
    nodes = admin.get("/api/console/storage-nodes").json()
    assert len(nodes) == 1
    assert nodes[0]["group_id"] == gid
    assert nodes[0]["group_label"] == "צפון"
    assert nodes[0]["disabled_at"] is None


def test_node_relabel(server):
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    assert admin.put("/api/console/storage-nodes/node1",
                     json={"label": "HAIFA"}).status_code == 200
    assert admin.get("/api/console/storage-nodes").json()[0]["label"] == "HAIFA"


def test_node_relabel_missing_is_404(server):
    assert server["admin"].put("/api/console/storage-nodes/ghost",
                               json={"label": "x"}).status_code == 404


def test_node_assign_and_clear_group(server):
    admin, conn = server["admin"], server["ctx"].conn
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    _add_node(conn)
    assert admin.put("/api/console/storage-nodes/node1",
                     json={"group_id": gid}).status_code == 200
    assert admin.get("/api/console/storage-nodes").json()[0]["group_id"] == gid
    # ניתוק — group_id=null
    assert admin.put("/api/console/storage-nodes/node1",
                     json={"group_id": None}).status_code == 200
    assert admin.get("/api/console/storage-nodes").json()[0]["group_id"] is None


def test_node_assign_unknown_group_is_400(server):
    conn = server["ctx"].conn
    _add_node(conn)
    assert server["admin"].put("/api/console/storage-nodes/node1",
                               json={"group_id": "nope"}).status_code == 400


def test_node_disable_and_reenable(server):
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    assert storage_nodes.enabled_node_count(conn) == 1
    assert admin.post("/api/console/storage-nodes/node1/disabled",
                      json={"disabled": True}).status_code == 200
    assert admin.get("/api/console/storage-nodes").json()[0]["disabled_at"]
    assert storage_nodes.enabled_node_count(conn) == 0
    # ההשבתה מסתירה מיד את היכולת (הדגל סופר NULL בלבד)
    assert admin.get("/api/console/me").json()[
        "capabilities"]["interbranch_transfer"] is False
    assert admin.post("/api/console/storage-nodes/node1/disabled",
                      json={"disabled": False}).status_code == 200
    assert admin.get("/api/console/storage-nodes").json()[0]["disabled_at"] is None
    assert storage_nodes.enabled_node_count(conn) == 1


def test_node_delete(server):
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    assert admin.delete("/api/console/storage-nodes/node1").status_code == 200
    assert admin.get("/api/console/storage-nodes").json() == []


def test_list_nodes_exposes_node_id(server):
    """‏#954 גל 7: ``node_id`` (המזהה הנגזר מ-SPKI, ``sn_<hex>``) קיים
    בעמודה מאז ה-enrollment (#883) — טבלת "סניפים" בקונסולה מציגה אותו
    ליד השם, ולכן ``list_nodes`` חייב לחשוף אותו."""
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    conn.execute("UPDATE storage_nodes SET node_id = ? WHERE id = ?",
                 ("sn_0123456789abcdef", "node1"))
    conn.commit()
    body = admin.get("/api/console/storage-nodes").json()
    assert body[0]["node_id"] == "sn_0123456789abcdef"


def test_node_delete_missing_is_404(server):
    assert server["admin"].delete(
        "/api/console/storage-nodes/ghost").status_code == 404


# --- מחיקת קבוצה מנתקת את המשניים שבה (FK: ON DELETE SET NULL) --------------

def test_group_delete_sets_member_nodes_group_null(server):
    admin, conn = server["admin"], server["ctx"].conn
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    _add_node(conn, node_id="n1", group_id=gid)
    _add_node(conn, node_id="n2", group_id=gid)
    admin.delete(f"/api/console/storage-node-groups/{gid}")
    # המשניים נשארו — רק השיוך נופל ל-NULL, לא הרשומה.
    nodes = {n["id"]: n for n in admin.get("/api/console/storage-nodes").json()}
    assert set(nodes) == {"n1", "n2"}
    assert nodes["n1"]["group_id"] is None
    assert nodes["n2"]["group_id"] is None


# --- מטריצת אכיפה: 403 ל-deploy, 409 למשני, ב-route ובשכבת ה-service ---------

def test_deploy_forbidden_on_every_endpoint(server):
    deploy = server["deploy"]
    assert deploy.get("/api/console/storage-node-groups").status_code == 403
    assert deploy.post("/api/console/storage-node-groups",
                       json={"label": "x"}).status_code == 403
    assert deploy.get("/api/console/storage-nodes").status_code == 403
    assert deploy.put("/api/console/storage-nodes/n1",
                      json={"label": "x"}).status_code == 403
    assert deploy.delete("/api/console/storage-nodes/n1").status_code == 403


def test_secondary_role_is_409_at_route(server):
    admin, conn = server["admin"], server["ctx"].conn
    _make_secondary(conn)
    assert admin.get("/api/console/storage-node-groups").status_code == 409
    assert admin.post("/api/console/storage-node-groups",
                      json={"label": "x"}).status_code == 409
    assert admin.get("/api/console/storage-nodes").status_code == 409
    assert admin.delete("/api/console/storage-nodes/n1").status_code == 409


#: משתמש מנהל ומשתמש deploy כפי שהם מגיעים משכבת ה-auth: (שם, תפקיד).
_ADMIN = ("noc", "admin")
_DEPLOY = ("labtech", "deploy")


def test_secondary_role_is_forbidden_at_service_layer(server):
    """אכיפה בשכבת ה-service, לא רק ב-route: קריאה ישירה על משני
    נכשלת בדיוק כמו דרך ה-API (ההיררכיה חד-כיוונית). המשתמש הוא admin,
    כדי שהתפקיד המותקן (secondary) הוא מה שנופל ולא ה-RBAC."""
    conn = server["ctx"].conn
    _make_secondary(conn)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.create_group(conn, "x", _ADMIN)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.edit_node(conn, "n1", _ADMIN, label="x")
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.set_node_disabled(conn, "n1", True, _ADMIN)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.delete_node(conn, "n1", _ADMIN)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.reorder_groups(conn, [], _ADMIN)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.delete_group(conn, "g1", _ADMIN)
    # #732: גם ה-listings אוכפים בשכבת ה-service.
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.list_groups(conn, _ADMIN)
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.list_nodes(conn, _ADMIN)


def test_deploy_forbidden_at_service_layer(server):
    """#732: deploy נחסם גם בשכבת ה-service (NodeManagementUnauthorized),
    לא רק ב-route. השרת ראשי — כך שה-RBAC הוא מה שנופל, לא התפקיד."""
    conn = server["ctx"].conn
    assert storage_nodes.role(conn) == "standalone"
    with pytest.raises(storage_nodes.NodeManagementUnauthorized):
        storage_nodes.create_group(conn, "x", _DEPLOY)
    with pytest.raises(storage_nodes.NodeManagementUnauthorized):
        storage_nodes.list_groups(conn, _DEPLOY)
    with pytest.raises(storage_nodes.NodeManagementUnauthorized):
        storage_nodes.list_nodes(conn, _DEPLOY)
    with pytest.raises(storage_nodes.NodeManagementUnauthorized):
        storage_nodes.edit_node(conn, "n1", _DEPLOY, label="x")
    with pytest.raises(storage_nodes.NodeManagementUnauthorized):
        storage_nodes.delete_node(conn, "n1", _DEPLOY)


def test_standalone_admin_can_manage(server):
    """הצד החיובי של המטריצה: admin על שרת ראשי (ברירת המחדל) עובד."""
    admin = server["admin"]
    assert storage_nodes.role(server["ctx"].conn) == "standalone"
    assert admin.post("/api/console/storage-node-groups",
                      json={"label": "צפון"}).status_code == 200


# --- #732/#3: עריכת משני אטומית — הכל-או-כלום -------------------------------

def test_edit_node_bad_group_leaves_label_unchanged(server):
    """שם + שיוך לקבוצה שגויה בבקשה אחת: הכל נכשל, השם לא משתנה.
    עד #732 השם נכתב ו-commit לפני שהשיוך נכשל — הצלחה חלקית."""
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn, label="סניף א'")
    resp = admin.put("/api/console/storage-nodes/node1",
                     json={"label": "HAIFA", "group_id": "nope"})
    assert resp.status_code == 400
    # הטרנזאקציה התגלגלה — השם המקורי נשמר.
    assert admin.get("/api/console/storage-nodes").json()[0]["label"] == "סניף א'"


def test_edit_node_valid_label_and_group_together(server):
    """הצד החיובי: שם + שיוך תקין באותה בקשה עוברים שניהם."""
    admin, conn = server["admin"], server["ctx"].conn
    gid = admin.post("/api/console/storage-node-groups",
                     json={"label": "צפון"}).json()["id"]
    _add_node(conn, label="סניף א'")
    assert admin.put("/api/console/storage-nodes/node1",
                     json={"label": "HAIFA", "group_id": gid}).status_code == 200
    node = admin.get("/api/console/storage-nodes").json()[0]
    assert node["label"] == "HAIFA" and node["group_id"] == gid


# --- #732/#4: פרסור בוליאני קפדני ל-disabled --------------------------------

@pytest.mark.parametrize("bad", ["false", "true", "0", "1", 1, 0, {}, [], None])
def test_disabled_rejects_non_boolean(server, bad):
    """כל ערך שאינו בוליאני אמיתי נדחה ב-400 ואינו משנה מצב.
    ``bool("false")`` הוא True — הבאג ש-#732 סוגר."""
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    resp = admin.post("/api/console/storage-nodes/node1/disabled",
                      json={"disabled": bad})
    assert resp.status_code == 400
    # לא נגע במצב: המשני נשאר פעיל.
    assert storage_nodes.enabled_node_count(conn) == 1


def test_disabled_accepts_real_booleans(server):
    """הצד החיובי: true משבית, false מפעיל מחדש."""
    admin, conn = server["admin"], server["ctx"].conn
    _add_node(conn)
    assert admin.post("/api/console/storage-nodes/node1/disabled",
                      json={"disabled": True}).status_code == 200
    assert storage_nodes.enabled_node_count(conn) == 0
    assert admin.post("/api/console/storage-nodes/node1/disabled",
                      json={"disabled": False}).status_code == 200
    assert storage_nodes.enabled_node_count(conn) == 1


# --- #732/#6: reorder דורש את כל המזהים, בלי כפילות -------------------------

def _three_groups(admin) -> list[str]:
    return [admin.post("/api/console/storage-node-groups",
                       json={"label": f"ק{i}"}).json()["id"] for i in range(3)]


def test_reorder_rejects_partial_list(server):
    admin = server["admin"]
    ids = _three_groups(admin)
    assert admin.post("/api/console/storage-node-groups/order",
                      json={"ids": ids[:2]}).status_code == 400
    # הסדר לא השתנה (sort לפי יצירה).
    groups = admin.get("/api/console/storage-node-groups").json()
    assert [g["id"] for g in groups] == ids


def test_reorder_rejects_empty_list_when_groups_exist(server):
    admin = server["admin"]
    _three_groups(admin)
    assert admin.post("/api/console/storage-node-groups/order",
                      json={"ids": []}).status_code == 400


def test_reorder_rejects_duplicate_ids(server):
    admin = server["admin"]
    ids = _three_groups(admin)
    dup = [ids[0], ids[0], ids[1], ids[2]]
    assert admin.post("/api/console/storage-node-groups/order",
                      json={"ids": dup}).status_code == 400


def test_reorder_accepts_exact_set(server):
    """הצד החיובי: בדיוק כל המזהים, כל אחד פעם — עובר."""
    admin = server["admin"]
    ids = _three_groups(admin)
    reversed_ids = list(reversed(ids))
    assert admin.post("/api/console/storage-node-groups/order",
                      json={"ids": reversed_ids}).status_code == 200
    groups = admin.get("/api/console/storage-node-groups").json()
    assert [g["id"] for g in groups] == reversed_ids
