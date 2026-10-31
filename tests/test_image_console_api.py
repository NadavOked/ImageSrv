"""‏#972: מה שמגירת האימג' בקונסולה צריכה, ושלא היה לו מקור ב-``/api/console``.

שלושה נתונים, כל אחד עם "לא נמדד" נפרד מ"אפס" (עיקרון 5):

1. ‏``GET /api/console/images/{id}`` — המניפסט הציבורי (בלי ``_dir``).
   ‏``/api/v1`` נשאר של הסוכן בלבד (#738).
2. ‏``last_scrub`` ב-``/images`` — תוצאת האימות האחרונה, שמורה ב-DB
   (אין לה ייצוג טבעי כקובץ, עיקרון 3). ‏``null`` = לא אומת מאז שנכנס.
3. ‏``rounds_30d`` ב-``/images`` — כמה סבבים השתמשו באימג' ב-30 הימים
   האחרונים: סבבי ``sessions`` **וגם** סבבי חדר השיכפולים. סבב חדר הוא
   סבב אחד גם כשהוא רץ בכמה גלים — וכל גל הוא שורה ב-``sessions``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from conftest import MANIFEST_256, write_image

from server.rounds.room import CLONERS_GROUP

_MISSING = "field missing"


def _listed(server) -> dict:
    """הרשימה לפי מזהה. שדה שהשרת לא החזיר כלל הוא ``_MISSING`` — כדי
    שבקרה שלילית תיכשל על ``assert`` עם הערך, ולא על ``KeyError``."""
    rows = server["admin"].get("/api/console/images").json()
    return {m["id"]: {"last_scrub": _MISSING, "rounds_30d": _MISSING, **m} for m in rows}


def _ago(days: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def _group(conn, group_id: str, role: str) -> None:
    conn.execute("INSERT OR IGNORE INTO groups (id, label, role, sort) VALUES (?, ?, ?, 0)",
                 (group_id, group_id, role))


def _session(conn, sid: str, image_id: str, created_at: str,
             group_id: str = "g_lab", prefix: str = "LAB", state: str = "closed") -> None:
    _group(conn, group_id, "cloner" if group_id == CLONERS_GROUP else "classroom")
    conn.execute(
        "INSERT INTO sessions (id, group_id, image_id, prefix, expected_clients,"
        " wait_seconds, state, opened_by, created_at, last_join_at)"
        " VALUES (?, ?, ?, ?, 1, 60, ?, 'test', ?, 0)",
        (sid, group_id, image_id, prefix, state, created_at))


def _room_round(conn, rid: str, image_id: str, created_at: str) -> None:
    conn.execute(
        "INSERT INTO room_rounds (id, image_id, target_drives, state, opened_by, created_at)"
        " VALUES (?, ?, 6, 'closed', 'test', ?)", (rid, image_id, created_at))


# --- 1. המניפסט הציבורי --------------------------------------------------------


def test_the_console_serves_the_public_manifest_without_internal_paths(server, images_root):
    r = server["admin"].get("/api/console/images/img_7f3a91")
    assert r.status_code == 200, r.text
    body = r.json()
    assert not [k for k in body if k.startswith("_")], "internal keys leaked"
    assert str(images_root) not in r.text and "_dir" not in r.text
    # מה שהמגירה מציגה, כפי שהוא על הדיסק
    assert body["scheme"] == "gpt" and body["sector_size"] == 512
    assert body["created_by"] == "nadav"
    assert body["compression"] == "zstd-9" and body["partclone_version"] == "0.3.x"
    parts = {p["index"]: p for p in body["partitions"]}
    assert parts[3]["role"] == "windows" and parts[3]["fs"] == "ntfs"
    assert parts[3]["size_bytes"] == 254803968000
    assert parts[3]["used_bytes"] == 84509376512
    assert len(parts[3]["sha256"]) == 64


def test_the_manifest_keeps_null_as_null_and_passes_boot_ca_through(server, images_root):
    """‏``used_bytes: null`` (לא נמדד) אינו הופך ל-0, ו-``boot_ca``/‏``shrunk_from_bytes``
    מגיעים כמו שנכתבו — הקונסולה היא זו שמתרגמת, לא השרת."""
    manifest = json.loads(json.dumps(MANIFEST_256))
    manifest.update(id="img_0dd123", name="Unmeasured",
                    boot_ca=None, boot_ca_error="no ESP mounted")
    manifest["partitions"][1]["used_bytes"] = None
    manifest["partitions"][1]["shrunk_from_bytes"] = 511101108224
    write_image(images_root, manifest)
    body = server["admin"].get("/api/console/images/img_0dd123").json()
    win = next(p for p in body["partitions"] if p["index"] == 3)
    assert win["used_bytes"] is None
    assert win["shrunk_from_bytes"] == 511101108224
    assert body["boot_ca"] is None and body["boot_ca_error"] == "no ESP mounted"


def test_an_unknown_image_is_404_and_the_manifest_needs_a_login(server):
    assert server["admin"].get("/api/console/images/img_ffffff").status_code == 404
    assert server["anon"].get("/api/console/images/img_7f3a91").status_code == 401


# --- 2. תוצאת האימות האחרונה ---------------------------------------------------


def _state(entry) -> object:
    return entry["state"] if isinstance(entry, dict) else entry


def test_last_scrub_is_null_until_a_scrub_ran(server):
    assert {m["last_scrub"] for m in _listed(server).values()} == {None}


def test_a_scrub_is_saved_and_served_per_image(server):
    before = datetime.now(timezone.utc) - timedelta(seconds=5)
    assert server["admin"].post("/api/console/images/img_7f3a91/scrub").status_code == 200
    listed = _listed(server)
    saved = listed["img_7f3a91"]["last_scrub"]
    assert _state(saved) == "intact"
    assert datetime.fromisoformat(saved["ts"]) >= before
    assert listed["img_2c8e04"]["last_scrub"] is None, "only the scrubbed image"


def test_a_drifted_image_is_saved_as_drift_and_a_later_scrub_replaces_it(server, images_root):
    victim = images_root / "img_2c8e04" / "p3.win.pcl.zst"
    good = victim.read_bytes()
    victim.write_bytes(b"flipped")
    assert server["admin"].post("/api/console/images/scrub").json()["intact"] is False
    listed = _listed(server)
    assert _state(listed["img_2c8e04"]["last_scrub"]) == "drift"
    assert _state(listed["img_7f3a91"]["last_scrub"]) == "intact"
    victim.write_bytes(good)
    server["admin"].post("/api/console/images/img_2c8e04/scrub")
    assert _state(_listed(server)["img_2c8e04"]["last_scrub"]) == "intact"


def test_the_scrub_result_lives_in_the_database_file(server, tmp_path):
    """שמור = נקרא מחיבור אחר לאותו קובץ, כמו אחרי אתחול שרת — לא מזיכרון
    התהליך ולא מזיכרון הדפדפן (עיקרון 3: ‏DB, כי אין לזה ייצוג כקובץ)."""
    server["admin"].post("/api/console/images/img_7f3a91/scrub")
    raw = sqlite3.connect(tmp_path / "data" / "imagesrv.db")
    try:
        tables = {r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert "image_scrubs" in tables
        rows = raw.execute("SELECT image_id, state FROM image_scrubs").fetchall()
    finally:
        raw.close()
    assert rows == [("img_7f3a91", "intact")]


def test_deleting_an_image_forgets_its_scrub(server, images_root, tmp_path):
    """אותו מזהה שחוזר (העלאה של אותו tar) הוא קבצים אחרים — "אומת" של
    הקודם היה ראיה על קבצים שכבר אינם."""
    admin = server["admin"]
    admin.post("/api/console/images/img_7f3a91/scrub")
    tar = admin.get("/api/console/images/img_7f3a91/download").content
    r = admin.post("/api/console/images/img_7f3a91/delete",
                   json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 200
    assert admin.post("/api/console/images/upload", content=tar).status_code == 200
    assert _listed(server)["img_7f3a91"]["last_scrub"] is None


def test_an_image_in_use_is_still_refused_and_keeps_its_scrub(server, images_root):
    """‏#784 לא נשבר: 409, והתוצאה השמורה לא נמחקת על מחיקה שלא קרתה."""
    admin, ctx = server["admin"], server["ctx"]
    admin.post("/api/console/images/img_7f3a91/scrub")
    _group(ctx.conn, "g1", "classroom")
    ctx.conn.commit()
    ctx.store.open(group_id="g1", image_id="img_7f3a91", prefix="LAB1",
                   expected_clients=2, opened_by="test")
    r = admin.post("/api/console/images/img_7f3a91/delete",
                   json={"confirm_name": "Office 2024 Standard"})
    assert r.status_code == 409
    assert _state(_listed(server)["img_7f3a91"]["last_scrub"]) == "intact"


# --- 2א. אימות בכל מיקומי האחסון (#1215) ----------------------------------------


def _location(conn, loc_id: str, mount: Path, state: str, cached: list | None = None) -> Path:
    """מיקום NFS נוסף כפי ש-``storage_locations`` שומר אותו; מחזיר את תיקיית
    האימג'ים שלו (``<mount>/images``)."""
    conn.execute(
        "INSERT INTO storage_locations (id, name, type, mount_point, state, state_since,"
        " created_at, last_images_json) VALUES (?, ?, 'nfs', ?, ?, 'x', 'x', ?)",
        (loc_id, loc_id, str(mount), state, json.dumps(cached or [])))
    conn.commit()
    return mount / "images"


def test_an_image_on_a_second_location_is_scrubbed_and_saved(server, tmp_path):
    """הספרייה של הקונסולה מכירה שני מיקומים; האימות עובר על שניהם, כל אימג'
    בתיקייה שבה הוא נמצא בפועל, ושניהם מקבלים ``last_scrub``."""
    remote = _location(server["ctx"].conn, "loc_nas", tmp_path / "nas", "connected")
    write_image(remote, {**MANIFEST_256, "id": "img_aa11bb", "name": "On the NAS"})
    admin = server["admin"]

    one = admin.post("/api/console/images/img_aa11bb/scrub")
    assert one.status_code == 200, one.text
    assert [(i["id"], i["state"]) for i in one.json()["images"]] == [("img_aa11bb", "intact")]

    every = admin.post("/api/console/images/scrub").json()
    assert every["intact"] is True
    assert {i["id"]: i["state"] for i in every["images"]} == {
        "img_7f3a91": "intact", "img_2c8e04": "intact", "img_aa11bb": "intact"}
    listed = _listed(server)
    assert _state(listed["img_aa11bb"]["last_scrub"]) == "intact"
    assert _state(listed["img_7f3a91"]["last_scrub"]) == "intact"

    # הקבצים באמת נקראו מהמיקום הנוסף: בייט הפוך שם הוא drift, לא intact.
    (remote / "img_aa11bb" / "p3.win.pcl.zst").write_bytes(b"flipped")
    assert admin.post("/api/console/images/img_aa11bb/scrub").json()["intact"] is False
    assert _state(_listed(server)["img_aa11bb"]["last_scrub"]) == "drift"


def test_an_image_on_an_unavailable_location_is_unavailable_not_intact(server, tmp_path):
    """"לא הצלחנו לבדוק" אינו "תקין" (עיקרון 5): האימג' מופיע בתשובה במצב
    משלו, האימות כולו אינו ``intact``, ותוצאה אמיתית קודמת לא נדרסת."""
    cached = {**MANIFEST_256, "id": "img_cc22dd", "name": "Offline"}
    _location(server["ctx"].conn, "loc_off", tmp_path / "off", "unreachable", [cached])
    admin = server["admin"]
    conn = server["ctx"].conn
    conn.execute("INSERT INTO image_scrubs (image_id, ts, state) VALUES ('img_cc22dd', 'then', 'drift')")
    conn.commit()

    every = admin.post("/api/console/images/scrub").json()
    states = {i["id"]: i["state"] for i in every["images"]}
    assert states.get("img_cc22dd") == "unavailable", states
    assert every["intact"] is False
    one = admin.post("/api/console/images/img_cc22dd/scrub")
    assert one.status_code == 200, one.text
    assert one.json()["images"][0]["state"] == "unavailable"
    assert _listed(server)["img_cc22dd"]["last_scrub"] == {"ts": "then", "state": "drift"}


# --- 3. סבבים ב-30 הימים האחרונים ----------------------------------------------


def test_no_rounds_is_a_measured_zero(server):
    assert {m["rounds_30d"] for m in _listed(server).values()} == {0}


def test_classroom_rounds_inside_the_window_are_counted(server):
    conn = server["ctx"].conn
    _session(conn, "s_new", "img_7f3a91", _ago(1))
    _session(conn, "s_mid", "img_7f3a91", _ago(29), state="failed")
    _session(conn, "s_old", "img_7f3a91", _ago(31))
    _session(conn, "s_other", "img_2c8e04", _ago(2))
    conn.commit()
    listed = _listed(server)
    assert listed["img_7f3a91"]["rounds_30d"] == 2
    assert listed["img_2c8e04"]["rounds_30d"] == 1


def test_a_room_round_counts_once_however_many_waves_it_ran(server):
    """סבב חדר עם שלושה גלים = שלוש שורות ``sessions`` (‏``ROOM``, קבוצת
    המשכפלים) + שורה אחת ב-``room_rounds``. הספירה היא 1."""
    conn = server["ctx"].conn
    _room_round(conn, "room_1", "img_2c8e04", _ago(3))
    for wave in range(3):
        _session(conn, f"w{wave}", "img_2c8e04", _ago(3), group_id=CLONERS_GROUP,
                 prefix="ROOM")
    _room_round(conn, "room_old", "img_2c8e04", _ago(45))
    conn.commit()
    assert _listed(server)["img_2c8e04"]["rounds_30d"] == 1


def test_an_unreadable_timestamp_makes_the_count_unknown_not_smaller(server):
    conn = server["ctx"].conn
    _session(conn, "s_ok", "img_7f3a91", _ago(1))
    _session(conn, "s_bad", "img_7f3a91", "yesterday-ish")
    _session(conn, "s_other", "img_2c8e04", _ago(1))
    conn.commit()
    listed = _listed(server)
    assert listed["img_7f3a91"]["rounds_30d"] is None
    assert listed["img_2c8e04"]["rounds_30d"] == 1
