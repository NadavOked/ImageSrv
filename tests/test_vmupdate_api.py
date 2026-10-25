"""‏#1334 — ה-API של עדכון ה-VM: מי מגיע, ומה הוא מקבל כשמסורב.

שני שערים: ה-HTTP (‏admin_only — ‏deploy ‏403, אנונימי ‏401) וה-WebSocket
של המסך, שעובר בשער של המוניטור **לפני** שנפתח ה-socket של ה-VNC. הבדיקה
המרכזית של ה-WS היא שה-connector **לא נקרא** כשההרשאה חסרה.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

pytest.importorskip("fastapi")

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server import auth, vmdisk, vmupdate
from server.db import set_setting
from server.vmupdate_api import create_vmupdate_viewer_router, qemu_handshake
from test_vmupdate import FakeMachine, FakeQemu, fake_build, wait_for

BASE = "/api/console/vmupdate"


@pytest.fixture()
def vm(server, tmp_path, monkeypatch):
    engine = server["app"].state.runtime.vmupdate
    ovmf = tmp_path / "ovmf"
    ovmf.mkdir()
    for name in ("OVMF_CODE_4M.fd", "OVMF_VARS_4M.fd", "OVMF_CODE_4M.secboot.fd",
                 "OVMF_VARS_4M.ms.fd"):
        (ovmf / name).write_bytes(b"fw")
    qemu = FakeQemu()
    engine.machine = FakeMachine()
    set_setting(engine.conn, vmupdate.NIC_KEY, "eth0")   # ‏#1468: "כרטיס לעדכוני VM"
    engine.ovmf_dir = ovmf
    engine._popen = qemu.popen
    engine._qmp = qemu.qmp
    monkeypatch.setattr(vmdisk, "build_disk", fake_build)
    yield {"engine": engine, "qemu": qemu}
    for proc in qemu.procs:
        proc.finish(0)


def test_every_endpoint_is_admin_only(server, vm):
    for method, path in (("get", "/status"), ("get", "/settings"), ("put", "/settings"),
                         ("post", "/start"), ("post", "/shutdown"), ("post", "/force-off"),
                         ("post", "/boot"), ("post", "/save"), ("post", "/discard")):
        kwargs = {"json": {}} if method in ("put", "post") else {}
        assert getattr(server["anon"], method)(BASE + path, **kwargs).status_code == 401, path
        assert getattr(server["deploy"], method)(BASE + path, **kwargs).status_code == 403, path
    assert vm["qemu"].procs == []


def test_start_status_and_a_refusal_in_the_shared_error_shape(server, vm):
    admin = server["admin"]
    assert admin.get(BASE + "/status").json()["state"] == "idle"
    started = admin.post(BASE + "/start", json={"image_id": "img_7f3a91"})
    assert started.status_code == 202 and started.json()["ok"] is True
    wait_for(vm["engine"], "running")
    status = admin.get(BASE + "/status").json()
    assert status["image_id"] == "img_7f3a91" and status["resources"]["accel"] == "kvm"
    again = admin.post(BASE + "/start", json={"image_id": "img_2c8e04"})
    assert again.status_code == 409
    assert again.json()["code"] == "busy" and again.json()["ok"] is False


def test_refusal_carries_machine_readable_numbers(server, vm):
    vm["engine"].machine = FakeMachine(mem=8000)
    refused = server["admin"].post(BASE + "/start", json={"image_id": "img_7f3a91"})
    assert refused.status_code == 503
    body = refused.json()
    assert body["code"] == "memory" and body["details"]["available_mib"] == 8000


def test_settings_round_trip_and_validation(server, vm):
    admin = server["admin"]
    assert admin.get(BASE + "/settings").json() == {"memory_mib": 16384, "cpus": 4}
    assert admin.put(BASE + "/settings", json={"memory_mib": 8192}).json()["memory_mib"] == 8192
    bad = admin.put(BASE + "/settings", json={"cpus": 0})
    assert bad.status_code == 400 and bad.json()["code"] == "settings"


def test_save_rejects_a_bad_name_by_name(server, vm):
    admin = server["admin"]
    admin.post(BASE + "/start", json={"image_id": "img_7f3a91"})
    wait_for(vm["engine"], "running")
    admin.post(BASE + "/shutdown")
    wait_for(vm["engine"], "stopped")
    bad = admin.post(BASE + "/save", json={"name": " leading"})
    assert bad.status_code == 400 and bad.json()["code"] == "name"


# --- ‏#1535: הסירוב ללקוח נבנה מהכלל, לא מטקסט החריגה; הטקסט — ליומן, בשורה אחת ---


def _api_log(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == "imagectl.vmupdate_api"]


def test_a_settings_refusal_states_the_rule_and_not_the_exception_1535(server, vm, caplog):
    with caplog.at_level(logging.INFO, logger="imagectl.vmupdate_api"):
        bad = server["admin"].put(BASE + "/settings", json={"evil\r\nkey": 1})
    body = bad.json()
    assert bad.status_code == 400 and body["code"] == "settings"
    assert "evil" not in body["error"], body["error"]
    assert "memory_mib (מספר שלם בין 1024" in body["error"] and "cpus (מספר שלם בין 1 ל-256)" in body["error"]
    logged = _api_log(caplog)
    assert len(logged) == 1 and "evil" in logged[0], logged
    assert "\n" not in logged[0] and "\r" not in logged[0], logged


def test_a_bad_version_name_is_refused_by_the_rule_without_echoing_it_1535(server, vm, caplog):
    admin = server["admin"]
    admin.post(BASE + "/start", json={"image_id": "img_7f3a91"})
    wait_for(vm["engine"], "running")
    admin.post(BASE + "/shutdown")
    wait_for(vm["engine"], "stopped")
    with caplog.at_level(logging.INFO, logger="imagectl.vmupdate_api"):
        bad = admin.post(BASE + "/save", json={"name": "x\nFORGED<b>"})
    body = bad.json()
    assert bad.status_code == 400 and body["code"] == "name"
    assert "FORGED" not in body["error"] and body["error"].startswith("שם הגרסה אינו תקין — מותרים")
    logged = _api_log(caplog)
    assert len(logged) == 1 and "FORGED" in logged[0] and "\n" not in logged[0], logged


# --- ה-WebSocket ----------------------------------------------------------------


class QemuReader:
    """ה-VNC של QEMU: באנר, סוג None יחיד, ‏SecurityResult OK — ואז חוסם."""

    def __init__(self):
        self.chunks = [b"RFB 003.008\n", b"\x01\x01", b"\x00\x00\x00\x00"]

    async def read(self, n):
        if self.chunks:
            head = self.chunks[0]
            take, rest = head[:n], head[n:]
            if rest:
                self.chunks[0] = rest
            else:
                self.chunks.pop(0)
            return take
        await asyncio.Event().wait()
        return b""


class Writer:
    def __init__(self):
        self.writes = []

    def write(self, data):
        self.writes.append(data)

    async def drain(self):
        return None

    def close(self):
        return None

    async def wait_closed(self):
        return None


def viewer_client(server, engine, role, connector):
    app = FastAPI()
    app.include_router(create_vmupdate_viewer_router(server["ctx"], engine, open_unix=connector))
    client = TestClient(app)
    client.cookies.set(auth.COOKIE_NAME, auth.issue(
        server["ctx"].conn, "noc" if role == "admin" else "labtech", role))
    return client


@pytest.mark.parametrize("role,code", [("deploy", 4403), (None, 4401)])
def test_viewer_gate_closes_before_the_vnc_socket_is_opened(server, vm, role, code):
    calls = []

    async def connector(path):
        calls.append(path)
        return QemuReader(), Writer()

    if role is None:
        app = FastAPI()
        app.include_router(create_vmupdate_viewer_router(server["ctx"], vm["engine"],
                                                         open_unix=connector))
        client = TestClient(app)
    else:
        client = viewer_client(server, vm["engine"], role, connector)
    with client.websocket_connect(BASE + "/viewer") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == code
    assert calls == []


def test_viewer_without_a_running_vm_is_4409(server, vm):
    client = viewer_client(server, vm["engine"], "admin", None)
    with client.websocket_connect(BASE + "/viewer") as ws:
        with pytest.raises(WebSocketDisconnect) as closed:
            ws.receive_bytes()
    assert closed.value.code == 4409


def test_viewer_bridges_the_qemu_rfb_for_an_admin(server, vm, monkeypatch):
    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)
    writer = Writer()

    async def connector(path):
        assert path == str(sock)
        return QemuReader(), writer

    presence = []
    monkeypatch.setattr(vm["engine"], "viewer_join",
                        lambda user, address: presence.append(("join", user, address)) or "t1")
    monkeypatch.setattr(vm["engine"], "viewer_leave",
                        lambda token: presence.append(("leave", token)))
    client = viewer_client(server, vm["engine"], "admin", connector)
    with client.websocket_connect(BASE + "/viewer") as ws:
        assert ws.receive_bytes() == b"RFB 003.008\n"     # הפרוקסי מול הדפדפן
        ws.send_bytes(b"RFB 003.008\n")
        assert ws.receive_bytes() == b"\x01\x01"
        ws.send_bytes(b"\x01")
        assert ws.receive_bytes() == b"\x00\x00\x00\x00"
        ws.send_bytes(b"\x01")                            # ClientInit → QEMU
    # QEMU קיבל: הגרסה, בחירת None, ואז ה-ClientInit מהדפדפן.
    assert writer.writes[:3] == [b"RFB 003.008\n", b"\x01", b"\x01"]
    # הנוכחות: נרשם בפתיחה (מי ומאיפה) ויצא בסגירה — לפס "גם X צופה".
    assert presence == [("join", "noc", "testclient"), ("leave", "t1")]


def test_qemu_handshake_refuses_a_vnc_that_demands_a_password():
    class PasswordOnly(QemuReader):
        def __init__(self):
            self.chunks = [b"RFB 003.008\n", b"\x01\x02"]

    from server.monitor import MachineAuthError
    with pytest.raises(MachineAuthError):
        asyncio.run(qemu_handshake(PasswordOnly(), Writer()))


# --- הכרעה 6: מנהל שני צופה בלבד, ובקשת שליטה ----------------------------------


def second_admin(server):
    from server import users
    from conftest import complete_console_login
    users.create(server["ctx"].conn, "dana", "admin-pass-456", "admin", by="test",
                 check_policy=False)
    client = TestClient(server["app"])
    complete_console_login(client, server["ctx"].conn, "dana", "admin-pass-456")
    return client


def test_control_request_and_approval_over_http(server, vm):
    admin, dana = server["admin"], second_admin(server)
    admin.post(BASE + "/start", json={"image_id": "img_7f3a91"})
    wait_for(vm["engine"], "running")
    admin.get(BASE + "/status")
    seen = dana.get(BASE + "/status").json()
    assert seen["controller"] == "noc" and seen["you_control"] is False
    assert dana.post(BASE + "/shutdown").json()["code"] == "view_only"
    assert dana.post(BASE + "/control/request").json()["control_request"]["by"] == "dana"
    assert admin.get(BASE + "/status").json()["control_request"]["by"] == "dana"
    assert dana.post(BASE + "/control/answer", json={"approve": True}).status_code == 403
    assert admin.post(BASE + "/control/answer", json={"approve": "yes"}).status_code == 400
    admin.post(BASE + "/control/answer", json={"approve": True})
    assert dana.get(BASE + "/status").json()["you_control"] is True
    assert dana.post(BASE + "/shutdown").status_code == 200


def test_a_viewer_who_does_not_control_cannot_type(server, vm, monkeypatch):
    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)
    monkeypatch.setattr(vm["engine"], "controls", lambda user: False)
    writer = Writer()

    async def connector(path):
        return QemuReader(), writer

    client = viewer_client(server, vm["engine"], "admin", connector)
    key = b"\x04\x01\x00\x00\x00\x00\x00\x61"           # KeyEvent 'a'
    pointer = b"\x05\x01\x00\x10\x00\x10"               # PointerEvent
    update = b"\x03\x00\x00\x00\x00\x00\x04\x00\x03\x00"  # FramebufferUpdateRequest
    with client.websocket_connect(BASE + "/viewer") as ws:
        ws.receive_bytes()
        ws.send_bytes(b"RFB 003.008\n")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        ws.send_bytes(b"\x01" + key + pointer + update)
    sent = b"".join(writer.writes[3:])
    assert update in sent                     # המסך מתעדכן גם לצופה
    assert key not in sent and pointer not in sent   # אבל המקלדת והעכבר לא עוברים


# --- #1392: מה ש-noVNC 1.6 שולח בפועל מול QEMU 10 -----------------------------
#
# נמדד 13/10 על שרת ת"א (QEMU 10.0.13, ‏rfbprobe מול vnc.sock): אחרי
# SetEncodings של noVNC, ‏QEMU שולח ServerCutText **מורחב** (caps, אורך -8)
# ומאשר את -258 (QEMU Extended Key Event). noVNC 1.6 עונה ל-caps ב-ClientCutText
# מורחב (אורך -8 כ-s32), ומאז כל מקש נשלח כהודעה 255 ולא 4. הגשר קרא את -8
# כ-4GB: כל בקשת עדכון, עכבר ומקש אחריו נבלעו, והחיבור לא נסגר — מסך שחור
# בחיבור שנפתח עם ההדלקה (התמונה הראשונה הייתה שחורה), ובלי קלט אחרי רענון.

PIXEL_FORMAT = b"\x00\x00\x00\x00" + bytes([32, 24, 0, 1, 0, 255, 0, 255, 0, 255, 16, 8, 0, 0, 0, 0])
NOVNC_ENCODINGS = [7, 21, 16, 6, 5, 1, 0, -32 + 6, -256 + 2, -223, -224, -258, -261,
                   -308, -309, -312, -313, -307, 0xC0A1E5CE - (1 << 32), -316, -240, -239]
SET_ENCODINGS = (b"\x02\x00" + len(NOVNC_ENCODINGS).to_bytes(2, "big")
                 + b"".join(e.to_bytes(4, "big", signed=True) for e in NOVNC_ENCODINGS))
FULL_UPDATE = b"\x03\x00\x00\x00\x00\x00\x05\x00\x03\x20"           # 1280x800
CLIPBOARD_CAPS = (b"\x06\x00\x00\x00" + (-8).to_bytes(4, "big", signed=True)
                  + b"\x1f\x00\x00\x01" + b"\x00\x00\x00\x00")      # extendedClipboardCaps
NEXT_UPDATE = b"\x03\x01\x00\x00\x00\x00\x05\x00\x03\x20"           # incremental
MOUSE = b"\x05\x00\x02\x80\x01\x90"
SHIFT_DOWN = b"\xff\x00\x00\x01" + (0xFFE1).to_bytes(4, "big") + (0x2A).to_bytes(4, "big")
SHIFT_UP = b"\xff\x00\x00\x00" + (0xFFE1).to_bytes(4, "big") + (0x2A).to_bytes(4, "big")


def novnc_session(server, vm, monkeypatch, *, controls: bool):
    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)
    monkeypatch.setattr(vm["engine"], "controls", lambda user: controls)
    writer = Writer()

    async def connector(path):
        return QemuReader(), writer

    client = viewer_client(server, vm["engine"], "admin", connector)
    with client.websocket_connect(BASE + "/viewer") as ws:
        ws.receive_bytes()
        ws.send_bytes(b"RFB 003.008\n")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        # noVNC שולח כל הודעה במסגרת משלה (`sock.flush()` אחרי כל הודעה).
        for frame in (b"\x01", PIXEL_FORMAT, SET_ENCODINGS, FULL_UPDATE, CLIPBOARD_CAPS,
                      NEXT_UPDATE, MOUSE, SHIFT_DOWN, SHIFT_UP, NEXT_UPDATE):
            ws.send_bytes(frame)
    closed = [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'vmupdate_viewer_closed'")]
    return writer.writes[3:], closed   # אחרי גרסה, None ו-ClientInit


def test_novnc_1_6_sequence_reaches_qemu_and_the_bridge_stays_open(server, vm, monkeypatch):
    writes, closed = novnc_session(server, vm, monkeypatch, controls=True)
    # כל הודעה כיחידה, בסדר — חוץ מה-ClientCutText, שאינו עובר לעולם (#1129).
    assert writes == [PIXEL_FORMAT, SET_ENCODINGS, FULL_UPDATE,
                      NEXT_UPDATE, MOUSE, SHIFT_DOWN, SHIFT_UP, NEXT_UPDATE]
    # הדפדפן הוא שסגר, לא הגשר (‏refused = ‏1002 על 255 / ‏4403 על ה-caps).
    assert closed == ["browser"]


def test_novnc_1_6_keys_and_mouse_are_dropped_for_a_viewer_who_does_not_control(
        server, vm, monkeypatch):
    writes, closed = novnc_session(server, vm, monkeypatch, controls=False)
    assert writes == [PIXEL_FORMAT, SET_ENCODINGS, FULL_UPDATE,
                      NEXT_UPDATE, NEXT_UPDATE]
    assert closed == ["browser"]


# --- #1394: רק ביטול ש-viewer_sessions עשה הוא "ההתחברות בוטלה" ----------------
#
# ‏7b5a51f2 קרא לכל CancelledError ‏session_revoked. אבל ה-endpoint מבוטל גם
# בפירוק החיבור (#725 — ראו ה-except החיצוני), ואז היומן אמר "ההתחברות בוטלה"
# על צופה שאיש לא ביטל. נמדד: ‏TestClient מבטל את המשימה מיד אחרי ה-disconnect,
# ובלינוקס הביטול הקדים אותו — ‏['session_revoked'] במקום ['browser'].


def _closed_details(server):
    return [r["detail"] for r in server["ctx"].conn.execute(
        "SELECT detail FROM journal WHERE event = 'vmupdate_viewer_closed'")]


@pytest.mark.parametrize("revoked", [True, False])
def test_only_a_revocation_is_journaled_as_a_revoked_session(server, vm, monkeypatch, revoked):
    import time
    from server import viewer_sessions

    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)

    async def connector(path):
        return QemuReader(), Writer()

    client = viewer_client(server, vm["engine"], "admin", connector)
    token = client.cookies.get(auth.COOKIE_NAME)
    with client.websocket_connect(BASE + "/viewer") as ws:
        ws.receive_bytes()
        ws.send_bytes(b"RFB 003.008\n")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")                    # ClientInit — הגשר פתוח
        if revoked:
            assert viewer_sessions.revoke(token) is True
            with pytest.raises(WebSocketDisconnect) as gone:
                ws.receive_bytes()
            assert gone.value.code == 4401
        else:
            # ביטול שאינו מ-viewer_sessions: מה שהשרת עושה בפירוק החיבור.
            _user, loop, task = viewer_sessions._tasks[token]
            loop.call_soon_threadsafe(task.cancel)
        deadline = time.monotonic() + 5
        while not _closed_details(server) and time.monotonic() < deadline:
            time.sleep(0.1)
    assert _closed_details(server) == (["session_revoked"] if revoked else ["browser"])


class ClosingWriter(Writer):
    """ה-socket ל-QEMU, עם ראיה חיובית שנסגר."""

    def __init__(self):
        super().__init__()
        self.closed = False

    def close(self):
        self.closed = True


def test_console_logout_closes_the_open_vm_viewer_and_its_qemu_socket_1394(
        server, vm, monkeypatch):
    """הגדרת "גמור" של #1394, דרך ה-HTTP ולא דרך `viewer_sessions` ישירות:
    צופה פתוח → ‏`POST /api/console/logout` באותה עוגייה → הגשר נסגר ב-4401
    (‏"session_revoked" ביומן) וה-socket ל-QEMU נסגר. בלי החיווט ב-logout
    הגשר נשאר פתוח עד idle (נמדד 13/10: ‏4.5 דק' אחרי היציאה)."""
    import time

    sock = vm["engine"].workdir / "vnc.sock"
    monkeypatch.setattr(vm["engine"], "vnc_socket", lambda: sock)
    qemu = ClosingWriter()

    async def connector(path):
        return QemuReader(), qemu

    client = viewer_client(server, vm["engine"], "admin", connector)
    console = TestClient(server["app"])
    console.cookies.set(auth.COOKIE_NAME, client.cookies.get(auth.COOKIE_NAME))
    with client.websocket_connect(BASE + "/viewer") as ws:
        ws.receive_bytes()
        ws.send_bytes(b"RFB 003.008\n")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")
        ws.receive_bytes()
        ws.send_bytes(b"\x01")                    # ClientInit — הגשר פתוח
        assert console.post("/api/console/logout").status_code == 200
        # מחכים ליומן ולא ל-receive: גשר שלא נסגר היה חוסם את ה-receive לנצח.
        deadline = time.monotonic() + 5
        while not _closed_details(server) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _closed_details(server) == ["session_revoked"]
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_bytes()
        assert gone.value.code == 4401
    assert qemu.closed


# --- הכרעה 2: גרסאות וברירת מחדל -------------------------------------------------


def _version(root, base, new_id, version, previous):
    import copy
    from conftest import write_image
    manifest = copy.deepcopy(base)
    manifest.update(id=new_id, name=f"Office v{version}", version=version,
                    previous_id=previous, lineage=base["id"], origin="vm")
    write_image(root, manifest)


def test_a_saved_version_is_the_default_and_the_old_one_can_be_restored(server, vm):
    from conftest import MANIFEST_256
    root = server["ctx"].library.root
    _version(root, MANIFEST_256, "img_00a002", 2, "img_7f3a91")
    admin = server["admin"]
    listed = admin.get("/api/console/images/img_7f3a91/versions").json()
    assert [v["version"] for v in listed["versions"]] == [2, 1]
    assert listed["default_id"] == "img_00a002" and listed["lineage"] == "img_7f3a91"
    assert listed["versions"][0]["origin"] == "vm" and listed["versions"][1]["origin"] == "capture"
    # מחשב בנייה מקבל את ברירת המחדל בלבד
    library = server["ctx"].library
    assert "img_00a002" in library.allowed_for_disks(None)
    assert "img_7f3a91" not in library.allowed_for_disks(None)
    back = admin.put("/api/console/images/img_7f3a91/default")
    assert back.json()["default_id"] == "img_7f3a91"
    assert "img_7f3a91" in library.allowed_for_disks(None)
    assert "img_00a002" not in library.allowed_for_disks(None)
    assert library.get("img_00a002") is not None      # אף גרסה לא נמחקה
    rows = server["ctx"].conn.execute(
        "SELECT event, detail FROM journal WHERE event = 'image_default_set'").fetchall()
    assert rows and "img_00a002 → img_7f3a91" in rows[-1][1]
    assert server["deploy"].put("/api/console/images/img_7f3a91/default").status_code == 403
