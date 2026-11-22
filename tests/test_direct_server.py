"""#715 — הפצה ישירה ממחשב הבנייה: השרת מתזמר, המקור הוא הדיסק.

מה שנבדק כאן מול ה-API, כמו שזה קורה בחדר: פתיחת סבב עם
`source: {kind: build_disk}` — WoL רק לנבחרים, משימת `direct_send` למחשב
הבנייה; המניפסט החי מוגש למקבלים באותו נתיב כמו אימג' מהספרייה; הגל אינו
יוצא לפני המניפסט; המנוע של השרת מתזמר ו**אינו** משדר בעצמו; הסבב יחיד
ונסגר עם הגל; עצירה סוגרת גם את המשימה; והסירובים — כל אחד בשמו.

#3173: הפצה ישירה היא חתיכות בלבד (#3161). מחשב הבנייה מעלה את אינדקס
החתיכות (ואת רשימת ה-BLAKE3) לפני המניפסט, `ChunkEngine` של השרת מתזמר את
הגל, ומחשב הבנייה משדר רק את ההזמנה שהמנוע נותן לו (`task.direct.chunk.order`).
"""

from __future__ import annotations

import json

import pytest
from conftest import hello_body, room_slots
from fakesend import Cloners, wait_for
from test_fanout import xxh64_reference
from test_server_room import CLONER1, CLONER2, cloner_hello, report, room, room_server  # noqa: F401

from server.core.tasks import TOKEN_HEADER

BUILD = "aa:bb:cc:00:00:10"
#: #3161: האינדקס ש-`_put_manifest` מעלה לפני המניפסט: מחיצה 1, עשר חתיכות של "פריים" בן בית אחד.
CH = 4194304
CHUNKS = 10
HEAD = b"\x28\xb5\x2f\xfd" + b"head-frame-of-the-test" * 3


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
    return {"schema": 1, "image_format": 2, "family": 256, "scheme": "gpt", "sector_size": 512,
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


def _index(chunks: int = CHUNKS, kind: str = "raw") -> bytes:
    """A consistent format-2 index: one-byte "frames", as test_chunksend_2533's library has."""
    head = len(HEAD) if kind == "partclone" else 0
    first = (f"IMAGESRV-IDX 2 partclone {CH} 200 {head} {'ab' * 8} {chunks * CH} {chunks} {head + chunks}\n"
             if kind == "partclone" else f"IMAGESRV-IDX 2 raw {CH} 0 0 - {chunks * CH} {chunks} {chunks}\n")
    return (first + "".join(f"{head + k} 1 {k:016x}\n" for k in range(chunks))).encode("ascii")


def _b3_list(index: bytes) -> bytes:
    """#3332: the BLAKE3 list a source uploads beside its index -- 32 bytes a chunk (what they hold is not read here)."""
    return bytes(range(32)) * int(index.split(b"\n", 1)[0].split(b" ")[8])


def _b3_of(data: bytes) -> str:
    from server.agent import readbackhash   # noqa: PLC0415
    tool = readbackhash.b3_tool()
    assert tool is not None, "the tests' BLAKE3 tool (conftest._b3_tool) was not built"
    return readbackhash.b3_hex(data, tool)


def _manifest(index: bytes | None, **more) -> dict:
    """The live manifest that signs `index` (`index_xxh64`) and its BLAKE3 list (`chunk_b3`, #3332)."""
    manifest = _live_manifest()
    if index is not None:
        manifest["partitions"][0]["index_xxh64"] = xxh64_reference(index)
        manifest["partitions"][0]["chunk_b3"] = _b3_of(_b3_list(index))        # #3332
    manifest["partitions"][0].update(more)
    return manifest


def _upload(room_server, task_id: str, what: str, part: int, body: bytes, *, b3: bool = True):
    """`b3`: an index taken is followed by its BLAKE3 list, as the source uploads it (#3332, directchunk.sh)."""
    put = room_server["anon"].put(f"/api/v1/direct/{task_id}/{what}/{part}", content=body,
                                  headers={TOKEN_HEADER: _token(room_server, task_id)})
    if what == "index" and b3 and put.status_code == 200:
        listed = room_server["anon"].put(f"/api/v1/direct/{task_id}/b3/{part}", content=_b3_list(body),
                                         headers={TOKEN_HEADER: _token(room_server, task_id)})
        assert listed.status_code == 200, listed.text
    return put


def _put_manifest(room_server, task_id, manifest=None, token=None):
    """`manifest=None` is what a build machine sends (#3161, #3173): the chunk index of partition 1 and its BLAKE3
    list first, and then the manifest that signs them -- the wave of the chunk engine. A direct send is chunks only:
    a manifest of a source that uploaded nothing (`manifest=_live_manifest()`) is accepted, and its wave then fails
    by name when it would go out (`directchunk.refusal`).

    The upload's own answer is not asserted here: a task that is no longer open refuses it as it refuses the
    manifest (404), and the manifest's answer is what every caller asserts. A refused upload leaves a manifest that
    signs nothing that was uploaded -- the wave fails by name, never quietly."""
    if manifest is None:
        index = _index()
        _upload(room_server, task_id, "index", 1, index)
        manifest = _manifest(index)
    head = {TOKEN_HEADER: _token(room_server, task_id) if token is None else token}
    return room_server["anon"].put(
        f"/api/v1/direct/{task_id}/manifest",
        content=json.dumps(manifest).encode(), headers=head)


def _cloners(room_server, opened, members=None) -> Cloners:
    """The cloners of a direct wave, speaking the chunk protocol (`fakesend.Cloners`): partition 1 of `_index()`."""
    return Cloners(room_server["anon"], opened["wave_session_id"],
                   members or {CLONER1: ["S1", "S2"]}, parts=[(1, CHUNKS)])


def _order(room_server) -> dict:
    """The pass the engine ordered the source to send (`task.direct.chunk.order`), once there is one."""
    got = {}

    def ordered():
        got["order"] = (_build_hello(room_server["anon"])["task"]["direct"].get("chunk") or {}).get("order")
        return got["order"] is not None

    assert wait_for(ordered), "the engine never ordered the source to send"
    return got["order"]


# --- פתיחה: מי מוער, מי מקבל מה ----------------------------------------------


def test_open_wakes_only_the_selected_and_hands_the_build_machine_a_task(room_server):
    opened = _setup(room_server)
    admin, anon = room_server["admin"], room_server["anon"]
    # WoL: חבילה אחת — למשכפל שנבחר, לא לכל החדר.
    assert len(room_server["woken"]) == 1
    view = room(admin)["round"]
    assert view["source"]["kind"] == "build_disk"
    assert view["source"]["mac"] == BUILD and view["source"]["disk"] == "sda"
    assert view["source"]["manifest_ready"] is False
    assert view["target_drives"] == 2 and view["image_name"] == "build-01:sda"
    assert view["image_id"] == opened["image_id"] and opened["image_id"].startswith("live_")

    # מחשב הבנייה: משימת direct_send עם הגל. #3173: בלי פרמטרים של udp-sender (`multicast`) -- לפני
    # המניפסט הוא מתבקש להעלות את אינדקסי החתיכות שלו (#3161), והשידור עצמו הוא הזמנה מהמנוע.
    task = _build_hello(anon)["task"]
    assert task["type"] == "direct_send" and task["id"] == opened["task_id"]
    assert task["disk"] == "sda" and task["image_id"] == opened["image_id"]
    assert task["direct"]["session_id"] == opened["wave_session_id"]
    assert task["direct"]["session_state"] == "open"
    assert "multicast" not in task["direct"]
    assert task["direct"]["engine"] == "chunk"

    # המשכפל שנבחר מצטרף ורואה את הפורטים שלו; זה שלא נבחר — אין לו סבב.
    joined = cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert joined["session"]["image_id"] == opened["image_id"]
    assert joined["session"]["target_ports"] == [1, 2]
    assert cloner_hello(anon, CLONER2, ["S3", "S4"])["session"] is None
    assert _build_hello(anon)["task"]["direct"]["receivers"] == 1


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

    # הדופק הבא מוציא את הגל — המנוע של השרת מתזמר אותו, ושום שולח אינו רץ בשרת (#3161).
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    assert room(admin)["round"]["wave_state"] == "running"
    assert ctx.chunk.status()["session_id"] == opened["wave_session_id"]
    _cloners(room_server, opened).listen(1)
    assert _order(room_server)["part"] == 1                   # השידור הוא הזמנה למחשב הבנייה
    assert room_server["recorder"].commands == []            # ולא `chunkcast send` בשרת
    events = [r["event"] for r in ctx.conn.execute("SELECT event FROM journal")]
    assert "send_delegated" in events
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
    """#381 בנתיב החי: `machine_mac` זר → 400 בשם, המשימה failed, אין מניפסט."""
    opened = _setup(room_server)
    bound = {**_live_manifest(), "machine_mac": "aa:bb:cc:00:00:99"}
    r = _put_manifest(room_server, opened["task_id"], manifest=bound)
    assert r.status_code == 400 and "aa:bb:cc:00:00:99" in r.json()["detail"]
    assert room(room_server["admin"])["round"]["source"]["manifest_ready"] is False
    assert room_server["anon"].get(
        f"/api/v1/images/{opened['image_id']}/manifest").status_code == 404


def _joined_then(room_server, manifest=None, raw=None):
    """סבב ישיר, המשכפל הצטרף, ואז PUT של מניפסט שהשרת דוחה (#2906)."""
    opened = _setup(room_server)
    anon, conn = room_server["anon"], room_server["ctx"].conn
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    if raw is not None:
        head = {TOKEN_HEADER: _token(room_server, opened["task_id"])}
        r = anon.put(f"/api/v1/direct/{opened['task_id']}/manifest",
                     content=raw, headers=head)
    else:
        r = _put_manifest(room_server, opened["task_id"], manifest=manifest)
    assert r.status_code == 400, r.text
    return opened, conn, r.json()["detail"]


def _assert_round_and_wave_failed(room_server, opened, conn, detail):
    """#2906: המשימה, הסבב והגל נכשלים יחד — והמשכפל שהצטרף רואה כשל בשם הסיבה."""
    assert conn.execute("SELECT state FROM tasks WHERE id = ?",
                        (opened["task_id"],)).fetchone()["state"] == "failed"
    rr = conn.execute("SELECT state, failed_reason FROM room_rounds"
                      " WHERE source_task_id = ?", (opened["task_id"],)).fetchone()
    assert rr["state"] == "failed"
    assert "מניפסט שנדחה" in rr["failed_reason"] and detail in rr["failed_reason"]
    wave = conn.execute("SELECT state, failed_reason FROM sessions WHERE id = ?",
                        (opened["wave_session_id"],)).fetchone()
    assert wave["state"] == "failed" and wave["failed_reason"] == rr["failed_reason"]
    member = conn.execute("SELECT state, error FROM session_members WHERE session_id = ?",
                          (opened["wave_session_id"],)).fetchone()
    assert member["state"] == "failed" and member["error"] == rr["failed_reason"]
    view = room(room_server["admin"])["round"]
    assert view["state"] == "failed" and view["failed_reason"] == rr["failed_reason"]


def test_a_rejected_manifest_fails_the_round_and_the_wave_too(room_server):
    """#2906: מניפסט עם sha256 פגום — לא רק המשימה: הסבב `failed` והמשכפל שהצטרף
    אינו ממתין לעד (קודם: `tasks=failed`, `room_rounds=active`, `sessions=open`)."""
    opened, conn, detail = _joined_then(room_server, _live_manifest(sha="not-a-sha"))
    _assert_round_and_wave_failed(room_server, opened, conn, detail)


def test_malformed_json_fails_the_round_and_the_wave_too(room_server):
    opened, conn, detail = _joined_then(room_server, raw=b"{not json")
    _assert_round_and_wave_failed(room_server, opened, conn, detail)


def test_a_manifest_the_restore_refuses_fails_the_round_and_the_wave_too(room_server):
    bound = {**_live_manifest(), "machine_mac": "aa:bb:cc:00:00:99"}
    opened, conn, detail = _joined_then(room_server, bound)
    _assert_round_and_wave_failed(room_server, opened, conn, detail)


def test_a_failed_round_frees_the_slot_for_a_new_round(room_server):
    """הסבב שנכשל משחרר את חריץ המולטיקאסט: סבב חדש נפתח, בלי "עצור סבב" ידני."""
    _joined_then(room_server, _live_manifest(sha="not-a-sha"))
    r = _open_direct(room_server["admin"], [{"mac": CLONER1, "ports": [1, 2]}])
    assert r.status_code == 200, r.json()


def test_a_good_manifest_does_not_fail_the_round(room_server):
    """שומר נגד תיקון-יתר: מניפסט תקין משאיר את הסבב `active` והגל `open`."""
    opened = _setup(room_server)
    cloner_hello(room_server["anon"], CLONER1, ["S1", "S2"])
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    conn = room_server["ctx"].conn
    assert conn.execute("SELECT state FROM room_rounds WHERE source_task_id = ?",
                        (opened["task_id"],)).fetchone()["state"] == "active"


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
    status = ctx.chunk.status()
    assert status is not None and status["image_id"] == "img_7f3a91"


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
    כלל. מעכשיו — קצב הפתיחה. #3173: הקצב מגיע למקור בהזמנה של המנוע (`kbps`),
    ולא בפרמטרים של udp-sender בגל הפתוח."""
    assert room_server["ctx"].chunk.max_bitrate is None
    opened = _running_direct(room_server)
    _cloners(room_server, opened).listen(1)
    assert _order(room_server)["kbps"] == 600_000            # 600m -- לעולם לא "בלי תקרה"


def test_the_running_direct_wave_gets_the_decided_rate_and_the_room_says_why(room_server):
    ctx, admin = room_server["ctx"], room_server["admin"]
    opened = _running_direct(room_server)
    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "running"
    _cloners(room_server, opened).listen(1)
    assert _order(room_server)["kbps"] == 600_000
    shown = room(admin)["round"]["wave_rate"]
    assert shown["max_bitrate"] == "600m" and shown["basis"] == "start"
    assert shown["direct"] is True and shown["projected"] is False
    detail = ctx.conn.execute("SELECT detail FROM journal WHERE event = 'wave_rate'"
                              ).fetchone()["detail"]
    assert detail.startswith(opened["wave_session_id"])


def test_the_build_machine_respects_a_configured_cap(room_server):
    room_server["ctx"].chunk.max_bitrate = "200m"
    opened = _running_direct(room_server)
    _cloners(room_server, opened).listen(1)
    assert _order(room_server)["kbps"] == 200_000
