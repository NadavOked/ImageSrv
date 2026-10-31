"""‏#715 — הפצה ישירה ממחשב הבנייה: השרת מתזמר, המקור הוא הדיסק.

מה שנבדק כאן מול ה-API, כמו שזה קורה בחדר: פתיחת סבב עם
`source: {kind: build_disk}` — ‏WoL רק לנבחרים, משימת `direct_send` למחשב
הבנייה עם פרמטרי השידור של השרת; המניפסט החי מוגש למקבלים באותו נתיב
כמו אימג' מהספרייה; הגל אינו יוצא לפני המניפסט; המנוע של השרת **אינו**
משדר; הסבב יחיד ונסגר עם הגל; עצירה סוגרת גם את המשימה; והסירובים —
כל אחד בשמו.
"""

from __future__ import annotations

import json

import pytest
from conftest import hello_body, room_slots
from test_server_room import CLONER1, CLONER2, cloner_hello, report, room, room_server  # noqa: F401

from server.core.tasks import TOKEN_HEADER

BUILD = "aa:bb:cc:00:00:10"


def _build_hello(client, mac=BUILD, disks=("sda",)):
    body = hello_body(mac)
    body["disks"] = [
        {"dev": dev, "size_bytes": 256060514304, "model": "Build SSD",
         "serial": f"B-{dev}", "removable": False, "scheme": "gpt", "has_data": True}
        for dev in disks
    ]
    r = client.post("/api/v1/agent/hello", json=body)
    assert r.status_code == 200
    return r.json()


def _register_build(room_server, mac=BUILD):
    admin = room_server["admin"]
    assert admin.post("/api/console/machines", json={
        "mac": mac, "name": "build-01", "group_id": "grp_BUILD"}).status_code == 200
    _build_hello(room_server["anon"], mac)


def _open_direct(client, slots, disk="sda", mac=BUILD, target=None):
    body = {"source": {"kind": "build_disk", "mac": mac, "disk": disk},
            "target_slots": slots}
    if target is not None:
        body["target_drives"] = target
    return client.post("/api/console/room", json=body)


def _token(room_server, task_id):
    row = room_server["ctx"].conn.execute(
        "SELECT token FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return row["token"]


def _live_manifest(sha="ab" * 32):
    return {"schema": 1, "family": 256, "scheme": "gpt", "sector_size": 512,
            "source_disk_bytes": 256060514304, "min_target_bytes": 1073741824,
            "disk_guid": "047B3400-0000-0000-0000-0000003AEE00",
            "partitions": [{
                "index": 1, "type_guid": "C12A7328-F81F-11D2-BA4B-00A0C93EC93B",
                "role": "esp", "fs": "vfat", "start_sector": 2048,
                "size_bytes": 104857600, "used_bytes": 1, "file": "p1.esp.pcl.zst",
                "sha256": sha, "expandable": False}],
            "total_compressed_bytes": 4096, "compression": "zstd-3"}


def _setup(room_server, slots=None):
    """מחשב בנייה + שני משכפלים עם מגירות; פותח סבב ישיר על הנבחרים."""
    admin, anon = room_server["admin"], room_server["anon"]
    _register_build(room_server)
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    cloner_hello(anon, CLONER2, ["S3", "S4"])
    room_server["woken"].clear()
    r = _open_direct(admin, slots or [{"mac": CLONER1, "ports": [1, 2]}])
    assert r.status_code == 200, r.json()
    return r.json()


def _put_manifest(room_server, task_id, manifest=None, token=None):
    head = {TOKEN_HEADER: _token(room_server, task_id) if token is None else token}
    return room_server["anon"].put(
        f"/api/v1/direct/{task_id}/manifest",
        content=json.dumps(manifest or _live_manifest()).encode(), headers=head)


# --- פתיחה: מי מוער, מי מקבל מה ----------------------------------------------


def test_open_wakes_only_the_selected_and_hands_the_build_machine_a_task(room_server):
    opened = _setup(room_server)
    admin, anon = room_server["admin"], room_server["anon"]
    # ‏WoL: חבילה אחת — למשכפל שנבחר, לא לכל החדר.
    assert len(room_server["woken"]) == 1
    view = room(admin)["round"]
    assert view["source"]["kind"] == "build_disk"
    assert view["source"]["mac"] == BUILD and view["source"]["disk"] == "sda"
    assert view["source"]["manifest_ready"] is False
    assert view["target_drives"] == 2 and view["image_name"] == "build-01:sda"
    assert view["image_id"] == opened["image_id"] and opened["image_id"].startswith("live_")

    # מחשב הבנייה: משימת direct_send עם הגל ופרמטרי השידור של השרת.
    task = _build_hello(anon)["task"]
    assert task["type"] == "direct_send" and task["id"] == opened["task_id"]
    assert task["disk"] == "sda" and task["image_id"] == opened["image_id"]
    assert task["direct"]["session_id"] == opened["wave_session_id"]
    assert task["direct"]["session_state"] == "open"
    mc = task["direct"]["multicast"]
    assert set(mc) == {"portbase", "min_receivers", "max_wait", "start_timeout",
                       "max_wait_later", "start_timeout_later",   # ‏#957
                       "retries_until_drop", "max_bitrate"}
    assert mc["portbase"] == room_server["ctx"].sender.portbase
    assert mc["max_wait_later"] == room_server["ctx"].sender.max_wait_later
    assert mc["start_timeout_later"] == room_server["ctx"].sender.start_timeout_later

    # המשכפל שנבחר מצטרף ורואה את הפורטים שלו; זה שלא נבחר — אין לו סבב.
    joined = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert joined["session"]["image_id"] == opened["image_id"]
    assert joined["session"]["target_ports"] == [1, 2]
    assert cloner_hello(anon, CLONER2, ["S3", "S4"])["session"] is None
    assert _build_hello(anon)["task"]["direct"]["receivers"] == 1
    assert _build_hello(anon)["task"]["direct"]["multicast"]["min_receivers"] == 1


def test_refusals_each_by_name(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _register_build(room_server)
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    slots = [{"mac": CLONER1, "ports": [1]}]
    # לא מחשב בנייה
    r = _open_direct(admin, slots, mac=CLONER2)
    assert r.status_code == 400 and "מחשב בנייה" in r.json()["detail"]
    # דיסק שלא דווח
    r = _open_direct(admin, slots, disk="sdz")
    assert r.status_code == 400 and "sdz" in r.json()["detail"]
    # בלי בחירת יעדים — אין "כל הדיסקים"
    assert _open_direct(admin, []).status_code == 400
    assert _open_direct(admin, None).status_code == 400
    # יעד שאינו הסט הנבחר — סבב יחיד
    r = _open_direct(admin, slots, target=5)
    assert r.status_code == 400 and "סבב יחיד" in r.json()["detail"]
    # פורט לא מחובר
    assert _open_direct(admin, [{"mac": CLONER1, "ports": [3]}]).status_code == 400
    # שום דבר לא נפתח בדרך
    assert room(admin)["round"] is None
    assert room_server["ctx"].conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
    # ועכשיו כן — ואז השני נדחה: משימה פתוחה / סבב פעיל
    assert _open_direct(admin, slots).status_code == 200
    assert _open_direct(admin, slots).status_code == 409
    # deploy מורשה לפתוח (ROOM_OPERATOR_ROLES), אנונימי לא
    assert room_server["anon"].post("/api/console/room", json={
        "source": {"kind": "build_disk", "mac": BUILD, "disk": "sda"},
        "target_slots": slots}).status_code == 401


# --- המניפסט החי והגל ---------------------------------------------------------


def test_the_wave_waits_for_the_manifest_and_the_server_engine_never_sends(room_server):
    opened = _setup(room_server)
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    task_id, image_id = opened["task_id"], opened["image_id"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])          # מצטרף; 2 מגירות = היעד
    cloner_hello(anon, CLONER1, ["S1", "S2"])          # הדופק שהיה מוציא גל מספרייה
    # מוכן לפי מגירות — אבל בלי מניפסט הגל נשאר פתוח, גם על "התחל עכשיו".
    assert room(admin)["round"]["ready_drives"] == 2
    assert room(admin)["round"]["wave_state"] == "open"
    r = admin.post("/api/console/room/start")
    assert r.status_code == 409 and "המניפסט" in r.json()["detail"]
    assert anon.get(f"/api/v1/images/{image_id}/manifest").status_code == 404

    assert _put_manifest(room_server, task_id).status_code == 200
    assert room(admin)["round"]["source"]["manifest_ready"] is True
    assert room(admin)["round"]["source"]["task_state"] == "running"
    # המקבל מושך את המניפסט החי באותו נתיב כמו אימג' מהספרייה.
    served = anon.get(f"/api/v1/images/{image_id}/manifest")
    assert served.status_code == 200
    assert served.json()["partitions"][0]["sha256"] == "ab" * 32
    assert served.json()["id"] == image_id

    # הדופק הבא מוציא את הגל — והמנוע של השרת לא הופעל.
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert room(admin)["round"]["wave_state"] == "running"
    assert ctx.sender.status() is None
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "send_delegated" in events and "send_start" not in events
    # מחשב הבנייה רואה running ומשדר.
    direct = _build_hello(anon)["task"]["direct"]
    assert direct["session_state"] == "running" and direct["receivers"] == 1


def test_a_bad_manifest_fails_the_task_and_a_foreign_token_is_refused(room_server):
    opened = _setup(room_server)
    task_id = opened["task_id"]
    assert _put_manifest(room_server, task_id, token="x" * 48).status_code == 403
    assert _put_manifest(room_server, task_id, token="").status_code == 401
    bad = _live_manifest(sha="not-a-sha")
    r = _put_manifest(room_server, task_id, manifest=bad)
    assert r.status_code == 400 and "sha256" in r.json()["detail"]
    row = room_server["ctx"].conn.execute(
        "SELECT state, error FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assert row["state"] == "failed" and "sha256" in row["error"]
    assert room(room_server["admin"])["round"]["source"]["task_state"] == "failed"
    # משימה שנכשלה אינה פתוחה — הנתיב עונה 404 גם עם האסימון הנכון.
    assert _put_manifest(room_server, task_id).status_code == 404


def test_a_manifest_bound_to_another_machine_is_refused(room_server):
    """‏#381 בנתיב החי: `machine_mac` זר → 400 בשם, המשימה failed, אין מניפסט."""
    opened = _setup(room_server)
    bound = {**_live_manifest(), "machine_mac": "aa:bb:cc:00:00:99"}
    r = _put_manifest(room_server, opened["task_id"], manifest=bound)
    assert r.status_code == 400 and "aa:bb:cc:00:00:99" in r.json()["detail"]
    assert room(room_server["admin"])["round"]["source"]["manifest_ready"] is False
    assert room_server["anon"].get(
        f"/api/v1/images/{opened['image_id']}/manifest").status_code == 404


# --- הסיום: סבב יחיד, המשימה נסגרת עם הסבב --------------------------------------


def _run_wave(room_server):
    opened = _setup(room_server)
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    return opened


def test_source_progress_sending_and_done_close_the_task(room_server):
    opened = _run_wave(room_server)
    anon, ctx = room_server["anon"], room_server["ctx"]
    head = {TOKEN_HEADER: _token(room_server, opened["task_id"])}
    r = anon.post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "sending",
        "targets": [{"dev": "sda", "bytes_written": 2048, "bytes_total": 4096,
                     "state": "waiting"}]}, headers=head)
    assert r.status_code == 200, r.json()
    assert ctx.conn.execute("SELECT state FROM tasks WHERE id = ?",
                            (opened["task_id"],)).fetchone()["state"] == "running"
    r = anon.post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 4096, "bytes_total": 4096,
                     "state": "done"}]}, headers=head)
    assert r.status_code == 200
    assert ctx.conn.execute("SELECT state FROM tasks WHERE id = ?",
                            (opened["task_id"],)).fetchone()["state"] == "done"
    # הקליטה לא השתנתה: done מהסוכן אינו סוגר משימת capture.
    row = ctx.conn.execute("SELECT type FROM tasks WHERE id = ?",
                           (opened["task_id"],)).fetchone()
    assert row["type"] == "direct_send"


def test_the_round_is_single_and_closes_with_the_wave_even_on_a_failed_drawer(room_server):
    opened = _run_wave(room_server)
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    report(anon, opened["wave_session_id"], CLONER1, {"sda": "done", "sdb": "failed"},
           top="partial")
    cloner_hello(anon, CLONER1, ["S1", "S2"])          # הדופק שסוגר את הגל
    assert room(admin)["round"] is None                 # אין גל שני
    closed = ctx.conn.execute("SELECT state, written_drives FROM room_rounds"
                              " WHERE id = ?", (opened["id"],)).fetchone()
    assert closed["state"] == "closed" and closed["written_drives"] == 1
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "room_done" in events and events.count("room_wave") == 0
    # המשימה של המקור **אינה** נסגרת עם הגל: הדיווח הסופי של מחשב הבנייה
    # (done/failed) הוא שסוגר אותה — גל שנגמר לפני שהמקור אמר את דברו
    # (המקבלים מדווחים done כשסיימו לכתוב) אינו ראיה שהמקור סיים.
    assert ctx.conn.execute("SELECT state FROM tasks WHERE id = ?",
                            (opened["task_id"],)).fetchone()["state"] == "running"
    head = {TOKEN_HEADER: _token(room_server, opened["task_id"])}
    assert anon.post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 4096, "bytes_total": 4096,
                     "state": "done"}]}, headers=head).status_code == 200
    assert _build_hello(anon)["task"] is None


def test_stopping_the_round_needs_the_source_label_and_cancels_the_task(room_server):
    opened = _run_wave(room_server)
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    assert admin.post("/api/console/room/close",
                      json={"confirm_name": opened["image_id"]}).status_code == 400
    assert admin.post("/api/console/room/close",
                      json={"confirm_name": "build-01:sda"}).status_code == 200
    assert room(admin)["round"] is None
    assert ctx.conn.execute("SELECT state FROM tasks WHERE id = ?",
                            (opened["task_id"],)).fetchone()["state"] == "cancelled"
    assert _build_hello(anon)["task"] is None
    # אחרי שהסבב נסגר אפשר לפתוח סבב מספרייה רגיל — והמקור שלו הוא הספרייה.
    assert admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": room_slots(ctx.conn, {CLONER1: ["S1", "S2"]})}).status_code == 200
    assert room(admin)["round"]["source"] == {"kind": "library"}


def test_a_library_round_still_starts_the_server_engine(room_server):
    """בקרה: המנוע של השרת עדיין משדר סבב מספרייה — ההאצלה היא רק לדיסק חי."""
    admin, anon, ctx = room_server["admin"], room_server["anon"], room_server["ctx"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert admin.post("/api/console/room", json={
        "image_id": "img_7f3a91", "target_drives": 2,
        "target_slots": [{"mac": CLONER1, "ports": [1, 2]}]}).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])          # מצטרף
    cloner_hello(anon, CLONER1, ["S1", "S2"])          # הדופק שמוציא את הגל
    assert room(admin)["round"]["wave_state"] == "running"
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "send_delegated" not in events
    assert ctx.sender.status() is not None


# --- קצב אוטומטי (הכרעת נדב 11/10): גם הזרם ממחשב הבנייה עובר ברשת -----------


def _running_direct(room_server):
    opened = _setup(room_server)
    anon = room_server["anon"]
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert room(room_server["admin"])["round"]["wave_state"] == "running"
    return opened


def test_without_a_configured_cap_the_build_machine_sends_at_600m(room_server):
    """עד היום: בלי `IMAGESRV_MAX_BITRATE` מחשב הבנייה שידר בלי `--max-bitrate`
    כלל. מעכשיו — קצב הפתיחה, כבר בגל הפתוח (לפני שנבחר לו קצב)."""
    assert room_server["ctx"].sender.max_bitrate is None
    _setup(room_server)
    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "open"
    assert direct["multicast"]["max_bitrate"] == "600m"


def test_the_running_direct_wave_gets_the_decided_rate_and_the_room_says_why(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    opened = _running_direct(room_server)
    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "running"
    assert direct["multicast"]["max_bitrate"] == "600m"
    shown = room(admin)["round"]["wave_rate"]
    assert shown["max_bitrate"] == "600m" and shown["basis"] == "start"
    assert shown["direct"] is True and shown["projected"] is False
    detail = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                              ).fetchone()["detail"]
    assert detail.startswith(opened["wave_session_id"])


def test_the_build_machine_respects_a_configured_cap(room_server):
    room_server["ctx"].sender.max_bitrate = "200m"
    _running_direct(room_server)
    assert _build_hello(room_server["anon"])["task"]["direct"]["multicast"]["max_bitrate"] == "200m"
