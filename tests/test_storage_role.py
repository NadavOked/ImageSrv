"""Storage Nodes — tracer 1.1 (#655 / #723).

שלוש קבוצות: (א) קריאת התפקיד fail-closed, (ב) מטריצת ה-capability
של ‏``/me``, ו-(ג) מיגרציית הטבלאות (אידמפוטנטית). כולן על השרת המלא
דרך פיקסצ'ר ``server`` — עם לקוחות מחוברים כ-admin וכ-deploy
(‏#1073: ל-deploy אין `/me` — הקונסולה סגורה בפניו, 403).
"""

from __future__ import annotations

import pytest

from server.interserver import storage_nodes
from server.db import SCHEMA, now_iso, set_setting


def _add_node(conn, node_id="node1", base_url="https://sec1.example",
              disabled_at=None):
    """משני רשום אחד. ‏disabled_at=None → פעיל; חותמת → מושבת."""
    conn.execute(
        "INSERT INTO storage_nodes"
        " (id, label, base_url, credential_ref, enrolled_at, disabled_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (node_id, "סניף א'", base_url, f"cred:{node_id}", now_iso(), disabled_at),
    )
    conn.commit()


def _cap(client) -> bool:
    response = client.get("/api/console/me")
    assert response.status_code == 200
    return response.json()["capabilities"]["interbranch_transfer"]


# --- (א) קריאת התפקיד fail-closed ------------------------------------------

def test_role_missing_setting_is_standalone(server):
    # הפיקסצ'ר אינו מעביר storage_role, ולכן אין ערך כתוב — fail-closed.
    assert storage_nodes.role(server["ctx"].conn) == "standalone"


def test_role_written_malformed_value_fails_closed(server):
    # ‏#746: ערך שנכתב ואינו תפקיד מוכר (פגום/זר/"primary"/ריק) נכשל-סגור
    # ל-secondary — המצב שאינו מנהל משניים — ולא ל-standalone המורשה-יותר.
    conn = server["ctx"].conn
    for bad in ("banana", "primary", ""):
        set_setting(conn, storage_nodes.ROLE_KEY, bad)
        assert storage_nodes.role(conn) == "secondary"


def test_role_secondary_is_read_back(server):
    conn = server["ctx"].conn
    set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    assert storage_nodes.role(conn) == "secondary"


def test_forged_role_value_cannot_manage_nodes(server):
    # ‏#746 (בקרה שלילית): ערך role זר שנכתב אינו פותח ניהול משניים.
    # לפני התיקון "primary" היה מוכרע כ-standalone ו-assert עובר בשקט.
    conn = server["ctx"].conn
    set_setting(conn, storage_nodes.ROLE_KEY, "primary")
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_nodes.assert_can_manage_nodes(conn, ("noc", "admin"))


# --- (ב) מטריצת ה-capability של /me -----------------------------------------

def test_me_standalone_zero_nodes_is_false(server):
    assert _cap(server["admin"]) is False
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: אין /me ל-deploy


def test_me_standalone_one_enabled_node_admin_true_deploy_false(server):
    _add_node(server["ctx"].conn)
    assert _cap(server["admin"]) is True
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: אין /me ל-deploy


def test_me_standalone_one_disabled_node_is_false(server):
    _add_node(server["ctx"].conn, disabled_at=now_iso())
    assert _cap(server["admin"]) is False
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: אין /me ל-deploy


def test_me_secondary_with_enabled_node_is_false(server):
    conn = server["ctx"].conn
    _add_node(conn)
    set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    # משני אינו דוחף מעלה — גם עם משני "רשום" (מזויף) הדגל כבוי.
    assert _cap(server["admin"]) is False
    assert server["deploy"].get("/api/console/me").status_code == 403   # #1073: אין /me ל-deploy


# --- (ג) מיגרציית הטבלאות ---------------------------------------------------

def _tables(conn) -> set[str]:
    return {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}


def test_migration_creates_storage_tables_idempotently(server):
    conn = server["ctx"].conn
    assert "storage_nodes" in _tables(conn)
    assert "storage_node_groups" in _tables(conn)
    # אידמפוטנטי: הרצה חוזרת של הסכימה אינה זורקת ואינה מוחקת (IF NOT EXISTS).
    conn.executescript(SCHEMA)
    assert "storage_nodes" in _tables(conn)
    assert "storage_node_groups" in _tables(conn)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(storage_nodes)")}
    assert {"id", "label", "base_url", "group_id", "tls_fingerprint",
            "credential_ref", "enrolled_at", "disabled_at"} <= cols


# --- ולידציית התצורה (משני מחייב כתובת שרת ראשי) ----------------------------

def test_normalize_config_secondary_requires_primary_url():
    import pytest
    with pytest.raises(storage_nodes.StorageConfigError):
        storage_nodes.normalize_config("secondary", "")
    with pytest.raises(storage_nodes.StorageConfigError):
        storage_nodes.normalize_config("secondary", "   ")


def test_normalize_config_standalone_drops_primary_url():
    role_value, url = storage_nodes.normalize_config("standalone",
                                                     "https://ignored.example")
    assert role_value == "standalone"
    assert url == ""


# --- #732/#1: ריסטארט בלי הדגל אינו דורס secondary שמור ---------------------

def _app_on(data_dir, images_root, clock, **kw):
    """אפליקציה מלאה על ``data_dir`` נתון, עם שולח מזויף — כדי לבנות
    שתי הפעלות רצופות על אותו קובץ נתונים (סימולציית ריסטארט)."""
    from server.app import create_app
    from test_sender import Recorder                       # noqa: PLC0415
    return create_app(data_dir, images_root, "http://10.44.12.10:8080",
                      now_fn=clock, sender_runner=Recorder(block=True), **kw)


def test_restart_without_flag_keeps_stored_secondary(tmp_path, images_root, clock):
    """שרת שאותחל כ-secondary, ואז עלה מחדש **בלי** ``--storage-role``,
    נשאר secondary. עד #732 ``main`` העביר תמיד "standalone" (ברירת
    המחדל של הדגל) והריסטארט דרס את התפקיד השמור בשקט — fail-open."""
    from server.main import build_parser
    data_dir = tmp_path / "data"

    app1 = _app_on(data_dir, images_root, clock,
                   storage_role="secondary", primary_url="http://parent:8080")
    try:
        assert storage_nodes.role(app1.state.ctx.conn) == "secondary"
    finally:
        app1.state.ctx.sender.stop()

    # "ריסטארט" — בדיוק כמו main בונה argv בלי הדגל, ומעביר את הערך הלאה.
    args = build_parser().parse_args(["--server-url", "http://10.44.12.10:8080"])
    assert args.storage_role is None                       # הראיה על התיקון ב-main
    app2 = _app_on(data_dir, images_root, clock,
                   storage_role=args.storage_role, primary_url=args.primary_url)
    try:
        assert storage_nodes.role(app2.state.ctx.conn) == "secondary"
        assert storage_nodes.primary_url(app2.state.ctx.conn) == "http://parent:8080"
    finally:
        app2.state.ctx.sender.stop()


def test_storage_role_flag_defaults_to_none():
    """הראיה הישירה על התיקון ב-main: ברירת המחדל של הדגל היא None,
    ולכן create_app לא כותב תפקיד בהפעלה בלי הדגל (app.py:100)."""
    from server.main import build_parser
    args = build_parser().parse_args(["--server-url", "http://10.44.12.10:8080"])
    assert args.storage_role is None
    assert args.primary_url is None


# --- #732/#5: טבלת אחסון חלקית קיימת נכשלת בקול באתחול ----------------------

def test_partial_storage_nodes_table_fails_loud(tmp_path):
    """טבלת ``storage_nodes`` שקדמה לפיצ'ר וחסרה עמודות אינה ממוגרת
    ע"י CREATE TABLE IF NOT EXISTS. במקום ``no such column`` מאוחר מול
    כיתה — כשל בקול באתחול (#732)."""
    import sqlite3

    import pytest

    from server import db

    path = tmp_path / "imagesrv.db"
    raw = sqlite3.connect(path)
    # טבלה חלקית: יש id+label, חסרות base_url/…/disabled_at.
    raw.execute("CREATE TABLE storage_nodes (id TEXT PRIMARY KEY, label TEXT)")
    raw.commit()
    raw.close()

    with pytest.raises(db.SchemaError) as excinfo:
        db.connect(path)
    assert "disabled_at" in str(excinfo.value)


def test_fresh_db_passes_storage_schema_check(tmp_path):
    """הצד החיובי: בסיס חדש עובר את הבדיקה ועולה כרגיל."""
    from server import db
    conn = db.connect(tmp_path / "fresh.db")
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(storage_nodes)")}
    assert "disabled_at" in cols


# --- #732/#7: persist_config אטומי — role+url יחד --------------------------

def test_persist_config_atomic_rolls_back_role_on_url_failure(server, monkeypatch):
    """אם כתיבת primary_url נכשלת, גם כתיבת role מתגלגלת — לא נשאר
    תפקיד חדש עם כתובת ישנה (מצב לא-עקבי שנקרא כתצורה תקינה)."""
    import sqlite3

    import pytest

    conn = server["ctx"].conn
    storage_nodes.persist_config(conn, "secondary", "http://p1:8080")
    assert storage_nodes.role(conn) == "secondary"
    assert storage_nodes.primary_url(conn) == "http://p1:8080"

    orig = conn.execute
    calls = {"settings_inserts": 0}

    def flaky(sql, parameters=()):
        if sql.lstrip().startswith("INSERT INTO settings"):
            calls["settings_inserts"] += 1
            if calls["settings_inserts"] == 2:     # הכתיבה השנייה (url) נכשלת
                raise sqlite3.OperationalError("disk full (מדומה)")
        return orig(sql, parameters)

    monkeypatch.setattr(conn, "execute", flaky)
    with pytest.raises(sqlite3.OperationalError):
        storage_nodes.persist_config(conn, "standalone", None)
    monkeypatch.undo()

    # אטומי: התפקיד לא הפך ל-standalone, והכתובת נשמרה.
    assert storage_nodes.role(conn) == "secondary"
    assert storage_nodes.primary_url(conn) == "http://p1:8080"
