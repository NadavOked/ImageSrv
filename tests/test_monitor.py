"""המוניטור מרחוק (#690): שער ההגדרות (admin בלבד, הדלקה בהקלדה),
ושער ה-WebSocket שסוגר אנונימי (4401) ו-deploy (4403) **לפני** שהוא
פותח חיבור TCP למכונה — ואז מעביר בייטים גולמיים של RFB ל-admin.
הסגירה קורית **אחרי** accept (‏#904 סעיף 4), כדי שהקוד והסיבה יגיעו
לדפדפן ולא ייבלעו ב-HTTP 403 של דחיית handshake.

מה שנבדק כאן הוא לא "האם ההגדרה נשמרה" אלא **מי מגיע לשירות ה-RFB**.
שער שנכשל ומחבר את מי שאסור לו הוא בדיוק המצב המסוכן: לכן הבדיקה
המרכזית מוודאת שה-connector **לא נקרא** כשההרשאה חסרה. הבקרה השלילית
(מחיקת בדיקת התפקיד) הופכת אותה מירוקה-תמיד לתופסת-באג.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as hmaclib
import struct
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server import auth, monitor
from server.db import net_seen, set_setting
from conftest import hello_body

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

MAC = "aa:bb:cc:dd:ee:ff"
IP = "10.44.12.50"
#: ‏FramebufferUpdateRequest (סוג 3, 10 בייט) — הודעת לקוח תקינה ומלאה,
#: מה ש-monitor.js שולח אחרי כל עדכון. ‏#1129: הגשר מעביר דפדפן→מכונה
#: בגבולות הודעה, ולכן "בייטים שרירותיים" אינם עוד קלט חוקי בטסטים.
FB_REQUEST = b"\x03\x01\x00\x00\x00\x00\x04\x00\x03\x00"


class FakeReader:
    """קורא RFB מזויף: מגיש את הבאנר פעם אחת, ואז חוסם עד ביטול —
    כדי שהצד השני (browser_to_machine) יספיק להעביר את בייטי הלקוח
    לפני שהזרם נסגר. ‏read שמחזיר מיד "" היה מסיים את הפרוקסי במרוץ."""

    def __init__(self):
        self._sent = False

    async def read(self, _n: int) -> bytes:
        if not self._sent:
            self._sent = True
            return b"RFB 003.008\n"
        await asyncio.Event().wait()   # נחסם עד שהמשימה מבוטלת ב-finally
        return b""


class FakeWriter:
    def __init__(self):
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def monitor_client(server, role: str, connector):
    ctx = server["ctx"]
    app = FastAPI()
    app.include_router(
        monitor.create_monitor_router(
            ctx,
            open_connection=connector,
        )
    )
    client = TestClient(app)
    client.cookies.set(
        auth.COOKIE_NAME,
        auth.issue(ctx.conn, "noc" if role == "admin" else "labtech", role),
    )
    return client


def prepare_build_machine(server):
    ctx = server["ctx"]
    response = server["admin"].post(
        "/api/console/machines",
        json={"mac": MAC, "name": "Monitor", "group_id": "grp_BUILD"},
    )
    assert response.status_code == 200
    net_seen(ctx.conn, MAC, IP)
    set_setting(ctx.conn, monitor.STATION_KEY, monitor.flag_json(True))


def test_monitor_settings_are_admin_only(server):
    assert server["anon"].get(
        "/api/console/monitor/settings"
    ).status_code == 401
    assert server["deploy"].get(
        "/api/console/monitor/settings"
    ).status_code == 403

    result = server["admin"].get("/api/console/monitor/settings")
    assert result.status_code == 200
    assert result.json()["port"] == monitor.MONITOR_PORT
    assert result.json()["enabled"] is False


def test_monitor_is_off_by_default_and_cmdline_flag_follows_the_switch():
    """ברירת מחדל כבוי: imagectl.monitor=1 נכנס לקרנל רק כשהמתג דלוק.
    station_cmdline גם מסיר דגל שהגיע ב-extra-cmdline כשהמתג כבוי."""
    extra = ("console=ttyS0,115200", "imagectl.monitor=1")
    assert monitor.station_cmdline(extra, False) == ("console=ttyS0,115200",)
    assert monitor.station_cmdline((), True) == (monitor.CMDLINE_PARAM,)
    assert monitor.station_cmdline(extra, True).count("imagectl.monitor=1") == 1


def test_settings_page_warns_that_enabling_exposes_5900():
    js = (Path(__file__).resolve().parent.parent
          / "server" / "static" / "console.js").read_text(encoding="utf-8")
    # ‏#1300: האזהרה עברה מהערה בראש הדף ל-title של המתג ולכרטיס הצפייה (console-full.html P.monitor)
    assert "כבוי כברירת מחדל — הדלקה חושפת 5900 על וילן ההפצה" in js
    assert "הדלקה חושפת 5900 על וילן ההפצה ותופסת באתחול הבא של כל מכונה" in js


def test_enabling_monitor_requires_explicit_confirmation(server):
    denied = server["admin"].put(
        "/api/console/monitor/settings",
        json={"enabled": True},
    )
    assert denied.status_code == 409

    enabled = server["admin"].put(
        "/api/console/monitor/settings",
        json={"enabled": True, "confirm": "imagectl.monitor"},
    )
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True


@pytest.mark.parametrize(
    ("role", "expected_code"),
    [("deploy", 4403)],
)
def test_websocket_behaviorally_rejects_non_admin_before_tcp(
    server,
    role,
    expected_code,
):
    """Negative control: removing the role guard makes this test connect."""
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, role, connector)

    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()      # ‏#904: הסגירה מגיעה אחרי accept

    assert caught.value.code == expected_code
    assert called is False


def test_websocket_rejects_anonymous_before_tcp(server):
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    app = FastAPI()
    app.include_router(
        monitor.create_monitor_router(
            server["ctx"],
            open_connection=connector,
        )
    )

    with pytest.raises(WebSocketDisconnect) as caught:
        with TestClient(app).websocket_connect(
            f"/api/console/monitor/{MAC}"
        ) as ws:
            ws.receive_bytes()      # ‏#904: הסגירה מגיעה אחרי accept

    assert caught.value.code == 4401
    assert called is False


def test_rejection_reaches_the_browser_as_a_close_code_not_http_403(server):
    """‏#904 סעיף 4: ‏`websocket.close` **לפני** accept הוא דחיית handshake לפי
    מפרט ASGI — uvicorn מגיש אותה כ-HTTP 403, והדפדפן רואה 1006 בלי קוד
    ובלי סיבה ("החיבור נסגר"). ה-TestClient מדמה את זה במדויק: סגירה לפני
    accept זורקת כבר ב-`websocket_connect` (הכניסה ל-with, ‏`_raise_on_close`);
    סגירה אחרי accept — הכניסה מצליחה (101) והקוד והסיבה מגיעים ב-receive.
    הבקרה השלילית (monitor.py של main) מפילה את זה בכניסה, לא ב-assert."""
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect("/api/console/monitor/not-a-mac") as websocket:
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()

    assert caught.value.code == 4404
    assert caught.value.reason == "מכונה לא מוכרת"
    assert called is False


@pytest.mark.parametrize("role, code, reason", [
    ("deploy", 4403, "פעולה למנהל בלבד"),
    (None, 4401, "נדרשת התחברות"),
])
def test_gate_rejection_also_arrives_after_accept_with_its_reason(
        server, role, code, reason):
    """‏#859 נשמר: השער עדיין סוגר 4401/4403 לפני TCP — רק שעכשיו הקוד
    והסיבה מגיעים לדפדפן במקום 403 אילם."""
    client = monitor_client(server, role or "admin", None)
    if role is None:
        client.cookies.clear()
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()
    assert (caught.value.code, caught.value.reason) == (code, reason)


def test_close_reason_fits_a_close_frame():
    """‏RFC 6455: סיבת סגירה היא ≤123 בייט UTF-8 — עברית היא 2 בייט לתו,
    ו-`websockets` זורק ProtocolError על סיבה ארוכה **אחרי** accept (לפני
    ‏#904 היא נזרקה לפח יחד עם ה-403, ולכן האורך לא נבדק מעולם)."""
    assert monitor.close_reason("מכונה לא מוכרת") == "מכונה לא מוכרת"
    long = "המוניטור דחה את סוד השרת: " + "א" * 200
    cut = monitor.close_reason(long)
    assert len(cut.encode("utf-8")) <= 123
    assert long.startswith(cut) and cut.endswith("א")


def test_admin_proxy_carries_raw_rfb_bytes(server):
    """‏#839: לחיצת-היד היא של הפרוקסי (ראו למטה); מה שאחריה עובר גולמי."""
    machine = prepare_secret_machine(server)
    opened = []

    async def connector(host, port):
        opened.append((host, port))
        return machine, machine

    client = monitor_client(server, "admin", connector)

    with client.websocket_connect(
        f"/api/console/monitor/{MAC}",
        subprotocols=["binary"],
    ) as websocket:
        assert websocket.accepted_subprotocol == "binary"
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        websocket.send_bytes(FB_REQUEST)

    assert opened == [(IP, monitor.MONITOR_PORT)]
    assert machine.writes[-1] == FB_REQUEST
    assert machine.closed is True


def test_proxy_retries_while_booting_monitor_has_not_bound_5900(server, monkeypatch):
    """The first hello precedes monitor_start: two refused connects are startup,
    not a terminal 4502.  Before the fix this closes on the first refusal."""
    machine = prepare_secret_machine(server)
    attempts = []
    monkeypatch.setattr(monitor, "MONITOR_CONNECT_BACKOFF", (0,))

    async def connector(host, port):
        attempts.append((host, port))
        if len(attempts) < 3:
            raise ConnectionRefusedError("5900 is not listening yet")
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT

    assert attempts == [(IP, monitor.MONITOR_PORT)] * 3


def test_proxy_caps_startup_retry_and_reports_the_last_reason(server, monkeypatch):
    prepare_secret_machine(server)
    monkeypatch.setattr(monitor, "MONITOR_CONNECT_WAIT_SECONDS", 0.01)
    monkeypatch.setattr(monitor, "MONITOR_CONNECT_BACKOFF", (0,))

    async def connector(_host, _port):
        raise ConnectionRefusedError("5900 עדיין לא מאזין")

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
            websocket.receive_bytes()

    assert caught.value.code == 4502
    assert "שירות המוניטור במכונה אינו זמין לאחר 15 שניות" in caught.value.reason
    assert "המכונה סירבה לחיבור" in caught.value.reason


def test_monitor_client_does_not_require_a_subprotocol():
    """‏#690: הלקוח פותח WebSocket **בלי** לבקש subprotocol.

    ‏`new WebSocket(url, "binary")` מחייב את השרת להחזיר "binary" בתשובת
    ה-101; ‏uvicorn עם מימוש ה-WS ‎wsproto **אינו** מהדהד את ה-subprotocol
    (‎websockets כן), ואז Chrome סוגר את החיבור מיד (1006 → "החיבור נסגר",
    מסך ריק). נמדד על הברזל: מול אותו שרת, ‎ws=websockets החזיר "binary"
    ו-ws=wsproto החזיר None. ‏RFB עובד על מסגרות בינאריות גולמיות בלי שום
    subprotocol, אז לא דורשים אותו — וזה חסין לשני המימושים.

    בקרה שלילית: החזרת ``, "binary"`` ל-new WebSocket מפילה את הבדיקה.
    """
    import re
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent
            / "server" / "static" / "monitor.js").read_text(encoding="utf-8")
    assert re.search(r"new WebSocket\s*\(", text), "אין קריאת new WebSocket ב-monitor.js"
    # ארגומנט שני (אחרי הפסיק) ל-new WebSocket הוא ה-subprotocol — אסור.
    assert not re.search(r"new WebSocket\s*\(\s*[\w.]+\([^)]*\)\s*,", text), (
        "monitor.js מבקש subprotocol מ-new WebSocket — wsproto לא מהדהד "
        "אותו ו-Chrome סוגר את החיבור (1006)")


def test_monitor_client_shows_the_servers_close_reason():
    """‏#904 סעיף 4: הסיבה העברית שהשרת שולח ב-close היא מה שהמפעיל רואה —
    הטבלה הקשיחה בלקוח היא גיבוי לסיבה ריקה בלבד. ו-`monitor.html` הוקפץ
    (‏JS ישן מול שרת חדש — gotcha ידוע; ‏monitor.js נטען משם, לא מ-index)."""
    import re
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "server" / "static"
    js = (static / "monitor.js").read_text(encoding="utf-8")
    assert "event.reason ||" in js
    page = (static / "monitor.html").read_text(encoding="utf-8")
    versions = {tuple(int(x) for x in v.split("."))
                for v in re.findall(r"\?v=([0-9.]+)", page)}
    assert versions and min(versions) >= (3, 28), versions


def test_monitor_machines_list_is_admin_only_and_server_decides_online(server):
    """‏#822: דף המוניטור מציג רק build/cloner, ו"מחובר" מגיע מהשרת —
    אותו חלון (90ש') שבו ‏`_target` מסכים לפתוח חיבור. מחשב כיתה אינו
    ברשימה כלל, ומכונה שלא נראתה מעולם היא ‏`online: False` עם ‏`ip: None`
    — לא "לא ידוע שנראה כמחובר" (עיקרון 5).

    בקרה שלילית: ‏`seen_within` שמחזירה תמיד True מפילה את ההשוואה עם
    ‏`now_fn` המאוחר; הסרת ‏`WHERE g.role IN (...)` מכניסה את מכונת הכיתה.
    """
    from datetime import datetime, timedelta, timezone

    ctx = server["ctx"]
    assert server["deploy"].get(
        "/api/console/monitor/machines"
    ).status_code == 403

    prepare_build_machine(server)                      # MAC נראה עכשיו
    assert server["admin"].post(
        "/api/console/machines",
        json={"mac": "aa:bb:cc:dd:ee:01", "name": "Never", "group_id": "grp_CLONERS"},
    ).status_code == 200
    from conftest import setup_classroom
    lab = setup_classroom(server)                      # שתי מכונות כיתה, רשומות
    net_seen(ctx.conn, lab["mac1"], "10.44.12.51")     # ומחוברת — ועדיין לא ברשימה

    def client_at(now):
        app = FastAPI()
        app.include_router(monitor.create_monitor_router(ctx, now_fn=lambda: now))
        c = TestClient(app)
        c.cookies.set(auth.COOKIE_NAME, auth.issue(ctx.conn, "noc", "admin"))
        return c

    fresh = client_at(datetime.now(timezone.utc)).get(
        "/api/console/monitor/machines").json()
    by_mac = {m["mac"]: m for m in fresh}
    assert set(by_mac) == {MAC, "aa:bb:cc:dd:ee:01"}, "רק build/cloner"
    assert by_mac[MAC] == {"mac": MAC, "name": "Monitor", "role": "build",
                           "ip": IP, "online": True, "prompt": None}   # #906
    assert by_mac["aa:bb:cc:dd:ee:01"]["online"] is False
    assert by_mac["aa:bb:cc:dd:ee:01"]["ip"] is None

    stale = client_at(datetime.now(timezone.utc) + timedelta(seconds=200)).get(
        "/api/console/monitor/machines").json()
    assert {m["mac"]: m["online"] for m in stale} == {
        MAC: False, "aa:bb:cc:dd:ee:01": False}, "אחרי 90ש' — לא מחובר"


# --- #839/#1077: הפרוקסי מזדהה מול המוניטור ב-HMAC, לא בסוד גולמי ---------
#
# ‏5900 קשוב לכל הווילן, ולכן "רק ה-WebSocket של ה-admin מגיע לשם" לא היה
# נכון מעולם. המוניטור מציע רק את סוג-אבטחה 2 (מסגור VNC Authentication),
# והתשובה ל-challenge היא HMAC-SHA256(סוד, challenge) הקטום ל-16 בייטים.
# סוכן בלי monitor_auth: hmac מסורב לפני TCP. הדפדפן רואה את אותה
# לחיצת-יד None שראה עד היום — החוזה מולו לא זז.

SECRET = "00112233445566778899aabbccddeeff"
SECRET_BYTES = bytes.fromhex(SECRET)
CHALLENGE = bytes(range(16))
#: HMAC-SHA256(SECRET, CHALLENGE)[:16] — וקטור ידוע, לא חישוב בזמן הטסט.
KNOWN_HMAC = bytes.fromhex("a5ce9cbf7c63cbedf3403e594d04b0ef")
SERVER_INIT = b"\x00\x40\x00\x20" + b"\x00" * 16 + b"\x00\x00\x00\x00"


def hmac16(secret: str, challenge: bytes) -> bytes:
    return hmaclib.new(bytes.fromhex(secret), challenge,
                       hashlib.sha256).digest()[:16]


class FakeMachine:
    """מוניטור מזויף בצד המכונה: קורא + כותב על אותו אובייקט, ומגיב
    ללחיצת-היד צעד-צעד לפי מה שהפרוקסי כתב. ‏`read` חוסם כשאין מה להגיש
    (כמו FakeReader) — כך הממסר לא נסגר במרוץ."""

    def __init__(self, *, offered: bytes = b"\x02", result: int = 0,
                 reason: bytes = b"", after_auth: bytes = SERVER_INIT):
        self.writes: list[bytes] = []
        self.closed = False
        self.response: bytes | None = None
        self._offered, self._result = offered, result
        self._reason, self._after_auth = reason, after_auth
        self._pending: list[bytes] = [b"RFB 003.008\n"]
        self._ready: asyncio.Event | None = None

    async def read(self, n: int) -> bytes:
        """לכל היותר n בייטים, כמו StreamReader.read; השארית נשמרת."""
        if self._ready is None:
            self._ready = asyncio.Event()
        while not self._pending:
            self._ready.clear()
            await self._ready.wait()
        chunk = self._pending[0]
        if len(chunk) <= n:
            return self._pending.pop(0)
        self._pending[0] = chunk[n:]
        return chunk[:n]

    def _reply(self, chunk: bytes) -> None:
        self._pending.append(chunk)
        if self._ready is not None:
            self._ready.set()

    def write(self, payload: bytes) -> None:
        self.writes.append(payload)
        if payload == b"RFB 003.008\n":
            self._reply(bytes([len(self._offered)]) + self._offered)
        elif payload == b"\x02":
            self._reply(CHALLENGE)
        elif (self.response is None and len(payload) == 16
              and b"\x02" in self.writes):
            self.response = payload
            verdict = struct.pack(">I", self._result)
            if self._result:
                verdict += struct.pack(">I", len(self._reason)) + self._reason
            self._reply(verdict)
        elif payload.startswith(b"\x01") and self.response is not None:
            self._reply(self._after_auth)         # ClientInit → ServerInit

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


def prepare_secret_machine(server, secret: str | None = SECRET,
                           auth: str | None = "hmac") -> FakeMachine:
    prepare_build_machine(server)
    if secret is not None:
        net_seen(server["ctx"].conn, MAC, IP, monitor_secret=secret,
                 monitor_auth=auth)
    return FakeMachine()


def browser_handshake(websocket) -> None:
    """מה ש-monitor.js עושה, בייט-בייט: גרסה, None, ‏ClientInit."""
    assert websocket.receive_bytes() == b"RFB 003.008\n"
    websocket.send_bytes(b"RFB 003.008\n")
    assert websocket.receive_bytes() == b"\x01\x01"      # רק None מוצע לדפדפן
    websocket.send_bytes(b"\x01")
    assert websocket.receive_bytes() == b"\x00\x00\x00\x00"
    websocket.send_bytes(b"\x01")                         # ClientInit: shared


def test_hmac_response_matches_the_known_vector():
    """וקטור ידוע: HMAC-SHA256(secret, challenge) קטום ל-16. הסוד הגולמי
    אינו התשובה — זו הבקרה השלילית של #1077 על צד השרת."""
    assert monitor.hmac_response(SECRET, CHALLENGE) == KNOWN_HMAC
    assert hmac16(SECRET, CHALLENGE) == KNOWN_HMAC
    assert KNOWN_HMAC != SECRET_BYTES
    assert len(KNOWN_HMAC) == 16


def test_proxy_authenticates_to_the_machine_with_hmac_not_the_raw_secret(server):
    """הראיה החיובית: המוניטור המזויף קיבל HMAC על ה-challenge, לא את
    16 בייטי הסוד, והדפדפן — שלא ראה את הסוד — קיבל ServerInit אחריו."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        websocket.send_bytes(FB_REQUEST)

    assert machine.writes[:3] == [b"RFB 003.008\n", b"\x02", KNOWN_HMAC]
    assert machine.response == KNOWN_HMAC
    assert machine.response != SECRET_BYTES
    assert b"\x01" in machine.writes[3:]                  # ה-ClientInit של הדפדפן
    assert machine.writes[-1] == FB_REQUEST
    assert machine.closed is True


def test_proxy_forwards_bytes_that_arrived_with_the_handshake(server):
    """מסגרת WebSocket אחת שמכילה גם את בחירת האבטחה וגם את ClientInit:
    השארית אחרי לחיצת-היד חייבת להגיע למכונה, לא להיבלע."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        assert websocket.receive_bytes() == b"RFB 003.008\n"
        websocket.send_bytes(b"RFB 003.008\n")
        assert websocket.receive_bytes() == b"\x01\x01"
        websocket.send_bytes(b"\x01" + b"\x01" + FB_REQUEST)
        assert websocket.receive_bytes() == b"\x00\x00\x00\x00"
        assert websocket.receive_bytes() == SERVER_INIT

    assert b"".join(machine.writes[3:]) == b"\x01" + FB_REQUEST


def test_input_sent_before_the_version_is_what_closes_unexpected_security_type(server):
    """rc6, 18/10 (משכפל 2): "שגיאה: סוג אבטחה לא צפוי". הסיבה הזאת נכתבת
    רק **אחרי** שהפרוקסי הזדהה מול המכונה (סוד ישן היה נסגר 4512 לפני כן),
    והיא אומרת שהבייט ה-13 מהדפדפן אינו 1. כך בדיוק נראה זרם שבו monitor.js
    שלח PointerEvent בזמן "מתחבר…" (‏#1686: עד 15 ש' כשהמכונה עולה) — הבייטים
    האלה הם מה ש-monitor_client.test.cjs תפס לפני התיקון. הלקוח הוא שתוקן
    (קלט רק אחרי לחיצת היד); הפרוקסי נשאר קפדן, וזה מה שהטסט הזה מקבע."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        websocket.send_bytes(b"\x05\x00\x00\x0a\x00\x0a")   # עכבר, לפני הגרסה
        assert websocket.receive_bytes() == b"RFB 003.008\n"
        websocket.send_bytes(b"RFB 003.008\n")
        assert websocket.receive_bytes() == b"\x01\x01"
        websocket.send_bytes(b"\x01")
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_bytes()
    assert closed.value.code == 1002
    assert closed.value.reason == "סוג אבטחה לא צפוי"
    assert machine.writes[:3] == [b"RFB 003.008\n", b"\x02", KNOWN_HMAC], (
        "the machine side had authenticated: the secret was not the cause")


def test_proxy_never_falls_back_to_none_security(server):
    """מוניטור ישן (או מתחזה) שמציע רק None: הפרוקסי סוגר ב-4512 ואינו
    שולח `\\x01` לעולם — אחרת סוכן שלא שודרג היה פותח מסלול לא מאומת."""
    machine = prepare_secret_machine(server)
    machine._offered = b"\x01"

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()      # ‏#904: הסגירה מגיעה אחרי accept

    assert caught.value.code == monitor.WS_MACHINE_AUTH
    assert b"\x01" not in machine.writes
    assert machine.closed is True


def test_proxy_closes_when_the_machine_rejects_the_secret(server):
    machine = prepare_secret_machine(server)
    machine._result, machine._reason = 1, b"secret mismatch"

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()      # ‏#904: הסגירה מגיעה אחרי accept

    assert caught.value.code == monitor.WS_MACHINE_AUTH
    # ‏#1837: בעברית, בלי הסיבה האנגלית של LibVNCServer.
    assert caught.value.reason == monitor.MACHINE_REJECTED_SECRET
    assert "secret mismatch" not in caught.value.reason
    assert machine.closed is True


def test_proxy_refuses_before_tcp_when_the_machine_reported_no_secret(server):
    """סוכן ישן (או שורה שנמחקה בקונסולה): אין סוד → אין חיבור TCP בכלל."""
    prepare_secret_machine(server, secret=None)
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()      # ‏#904: הסגירה מגיעה אחרי accept

    assert caught.value.code == monitor.WS_MACHINE_AUTH
    assert called is False


def test_proxy_refuses_an_agent_without_hmac_auth_before_tcp(server):
    """סוכן שדיווח סוד בלי monitor_auth: hmac — מסורב בקול, בלי TCP,
    ובלי נפילה לשליחת הסוד הגולמי (עיקרון 5)."""
    prepare_secret_machine(server, auth=None)
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()

    assert caught.value.code == monitor.WS_MACHINE_AUTH
    assert "HMAC" in caught.value.reason
    assert called is False


def hello_with_secret(server, secret, auth=None):
    body = hello_body(MAC)
    if secret is not None:
        body["monitor_secret"] = secret
    if auth is not None:
        body["monitor_auth"] = auth
    response = server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    return response.json()


def stored_secret(server):
    row = server["ctx"].conn.execute(
        "SELECT monitor_secret FROM net_devices WHERE mac = ?", (MAC,)
    ).fetchone()
    return row["monitor_secret"] if row else None


def stored_auth(server):
    row = server["ctx"].conn.execute(
        "SELECT monitor_auth FROM net_devices WHERE mac = ?", (MAC,)
    ).fetchone()
    return row["monitor_auth"] if row else None


@pytest.mark.parametrize("bad", [
    "0011223344556677",                       # קצר
    SECRET.upper(),                           # לא קנוני
    "zz112233445566778899aabbccddeeff",       # לא hex
    42,
])
def test_hello_ignores_a_malformed_monitor_secret(server, bad):
    hello_with_secret(server, bad)
    assert stored_secret(server) is None


def test_hello_stores_and_rotates_the_monitor_secret(server):
    """אתחול = סוד חדש. ה-hello השני מגיע בתוך חלון החניקה של net_seen
    (#136) ובכל זאת חייב להיכתב — סוד ישן בשורה הוא מוניטור שלא ייפתח."""
    hello_with_secret(server, SECRET, auth="hmac")
    assert stored_secret(server) == SECRET
    assert stored_auth(server) == "hmac"
    rotated = "ffeeddccbbaa99887766554433221100"
    hello_with_secret(server, rotated, auth="hmac")
    assert stored_secret(server) == rotated
    hello_with_secret(server, None)                     # דופק בלי השדה
    assert stored_secret(server) == rotated             # נשמר, לא נמחק
    assert stored_auth(server) == "hmac"


def test_hello_without_monitor_auth_is_stored_as_legacy_not_hmac(server):
    """סוכן ישן ששולח סוד בלי monitor_auth: הסוד נשמר, ה-auth לא — והפרוקסי
    יסרב. לא ממציאים hmac בהיעדר השדה (עיקרון 5)."""
    hello_with_secret(server, SECRET)
    assert stored_secret(server) == SECRET
    assert stored_auth(server) is None


def test_monitor_secret_never_leaves_the_server(server):
    """הסוד נכנס דרך hello ויוצא רק אל 5900 של אותה מכונה. אף תשובת
    קונסולה — גם של admin — אינה נושאת אותו."""
    prepare_secret_machine(server)
    admin = server["admin"]
    for path in ("/api/console/monitor/machines", "/api/console/net",
                 "/api/console/machines"):
        response = admin.get(path)
        assert response.status_code == 200, path
        assert "monitor_secret" not in response.text, path
        assert SECRET not in response.text, path


# --- #906: hello בזמן המתנה לאדם — `waiting_for` + `prompt` -------------------

def hello_waiting(server, prompt, waiting_for="operator", **extra):
    body = {**hello_body(MAC), "joining": False, "prompt": prompt, **extra}
    if waiting_for is not None:
        body["waiting_for"] = waiting_for
    response = server["anon"].post("/api/v1/agent/hello", json=body)
    assert response.status_code == 200
    return response.json()


def console_prompt(server) -> str | None:
    rows = server["admin"].get("/api/console/machines").json()
    (row,) = [r for r in rows if r["mac"] == MAC]
    return row["prompt"]


def monitor_row(server) -> dict:
    rows = server["admin"].get("/api/console/monitor/machines").json()
    (row,) = [r for r in rows if r["mac"] == MAC]
    return row


def test_a_waiting_hello_keeps_the_machine_seen_and_names_the_question(server):
    """נמדד 16/09: בזמן השאלה האדומה `last_seen` קפא והמוניטור נחסם. ‏hello
    עם `waiting_for: operator` מעדכן `last_seen` כרגיל (המכונה מחוברת,
    המוניטור נפתח) והשאלה מוצגת ב-`/api/console/machines` וב-`/monitor/machines`.
    בקרה שלילית: על main ‏`prompt` אינו קיים בתשובה (KeyError)."""
    prepare_build_machine(server)
    hello_waiting(server, "Disk 1: failed the previous clone")
    assert console_prompt(server) == "Disk 1: failed the previous clone"
    row = monitor_row(server)
    assert row["online"] is True
    assert row["prompt"] == "Disk 1: failed the previous clone"


def test_a_hello_without_the_field_clears_the_question(server):
    """המפעיל ענה והסוכן חזר ללולאה הרגילה: ה-hello הבא מגיע **בתוך** חלון
    החניקה של net_seen (#136) ובכל זאת מנקה — שאלה שנענתה ונשארת על
    המסך שולחת את המפעיל למכונה שאין בה כלום."""
    prepare_build_machine(server)
    hello_waiting(server, "Disk 1: SMART fail (pending)")
    assert console_prompt(server) == "Disk 1: SMART fail (pending)"
    server["anon"].post("/api/v1/agent/hello", json=hello_body(MAC))
    assert console_prompt(server) is None
    assert monitor_row(server)["prompt"] is None


@pytest.mark.parametrize("prompt, waiting_for", [
    ("Disk 1: x", None),            # prompt בלי waiting_for
    ("Disk 1: x", "server"),        # לא ממתינה לאדם
    (42, "operator"),               # לא מחרוזת
    ("   ", "operator"),            # ריק
])
def test_a_malformed_waiting_hello_stores_no_question(server, prompt, waiting_for):
    prepare_build_machine(server)
    hello_waiting(server, prompt, waiting_for)
    assert console_prompt(server) is None


def test_the_console_translates_only_the_agent_fixed_prompts():
    """‏#908/#912: התפריט ומסך הכניסה של הסוכן שולחים מילה קבועה ב-ASCII
    (‏`menu`, ‏`signin`) — הקונסולה מתרגמת אותה לעברית; כל שאלה אחרת היא
    השורה שעל המסך ומוצגת כמו שהיא. בדיקת תוכן — אין דפדפן בחבילה; וה-`?v=`
    הוקפץ (JS ישן מול API חדש — gotcha ידוע)."""
    import re   # noqa: PLC0415
    from pathlib import Path   # noqa: PLC0415
    static = Path(__file__).resolve().parent.parent / "server" / "static"
    js = (static / "console.js").read_text(encoding="utf-8")
    assert 'menu: "תפריט"' in js and 'signin: "כניסה"' in js
    # #1713 (f1fef165) removed the uncalled waitingHtml, which read PROMPT_HE[m.prompt];
    # the prompt is shown through promptHe(m.prompt), whose first step is the fixed word.
    assert "if (PROMPT_HE[p]) return PROMPT_HE[p];" in js
    assert "promptHe(m.prompt)" in js
    page = (static / "index.html").read_text(encoding="utf-8")
    versions = {tuple(int(x) for x in v.split(".")) for v in re.findall(r"\?v=([0-9.]+)", page)}
    assert len(versions) == 1 and min(versions) >= (6, 1), versions


def test_a_long_question_is_cut_not_refused(server):
    from server.api import PROMPT_MAX_CHARS
    prepare_build_machine(server)
    hello_waiting(server, "x" * (PROMPT_MAX_CHARS + 50))
    assert console_prompt(server) == "x" * PROMPT_MAX_CHARS


# --- #1129: Origin, כוח בשרת, יומן, idle ---------------------------------------
#
# ‏R48: האישור בהקלדת שם היה UI בלבד — הדפדפן שלח `imagectl-power:` על ה-WS
# והשרת גישר גולמי. עכשיו הגשר מסרב ל-ClientCutText מהדפדפן, והפקודה עוברת
# רק ב-`POST /monitor/{mac}/power` שמשווה את השם לשם **מהטבלה**. ‏Origin זר
# נסגר לפני accept; חיבור וסגירה נרשמים ביומן; 10 דק' בלי תעבורה סוגרות.


def journal_rows(server, event: str) -> list[tuple[str, str]]:
    return [(r["detail"], r["user"]) for r in server["ctx"].conn.execute(
        "SELECT detail, user FROM journal WHERE event = ? ORDER BY id", (event,))]


@pytest.mark.parametrize("origin", ["http://evil.example", "null",
                                    "http://testserver.evil.example"])
def test_foreign_origin_is_refused_before_accept_and_before_tcp(server, origin):
    """‏SameSite=lax אינו מגן על WebSocket cross-site בכל הדפדפנים: דף זר
    עם ה-cookie של המנהל היה מקבל 101 ואת ה-RFB. ‏Origin שאינו ה-Host
    נסגר 4403 **לפני** accept — ה-TestClient
    זורק כבר בכניסה ל-with — ולפני TCP. הבקרה השלילית: main בלי
    `origin_allowed` מגיע ל-4404 אחרי accept (המכונה אינה רשומה)."""
    called = False

    async def connector(_host, _port):
        nonlocal called
        called = True
        return FakeReader(), FakeWriter()

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}",
                                      headers={"origin": origin}):
            pass
    assert caught.value.code == monitor.WS_FORBIDDEN
    assert caught.value.reason == monitor.ORIGIN_REFUSED
    assert called is False


@pytest.mark.parametrize("headers", [{}, {"origin": "http://testserver"},
                                     {"origin": "HTTP://TestServer"}])
def test_same_origin_or_no_origin_passes_the_origin_gate(server, headers):
    """‏monitor.js שולח את Origin של השרת עצמו; לקוח שאינו דפדפן (wsmon.py)
    אינו שולח כלל. שניהם עוברים את השער ומגיעים לשלב הבא (4404 — MAC
    לא מוכר — מגיע **אחרי** accept, כלומר השער לא עצר אותם)."""
    client = monitor_client(server, "admin", None)
    with client.websocket_connect("/api/console/monitor/not-a-mac",
                                  headers=headers) as websocket:
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()
    assert caught.value.code == 4404


def test_origin_allowed_matches_host_only():
    assert monitor.origin_allowed({"host": "10.44.12.1:8081"}) is True
    assert monitor.origin_allowed({"host": "10.44.12.1:8081",
                                   "origin": "http://10.44.12.1:8081"}) is True
    assert monitor.origin_allowed({"host": "10.44.12.1:8081",
                                   "origin": "http://10.44.12.1"}) is False
    assert monitor.origin_allowed({"host": "10.44.12.1:8081",
                                   "origin": "http://evil.example"}) is False
    assert monitor.origin_allowed({"origin": "http://x"}) is False   # אין Host


def test_power_token_over_the_websocket_is_refused_and_never_reaches_the_machine(server):
    """‏ClientCutText (סוג 6) מהדפדפן — הערוץ הישן של `imagectl-power:` —
    נסגר 4403 בשם, והפריים אינו נכתב למכונה. הודעות לקוח רגילות לפניו
    (FramebufferUpdateRequest) כן מגיעות: הסינון הוא לפי סוג, לא "הכול"."""
    machine = prepare_secret_machine(server)
    # הבקרה השלילית: מוניטור שמקבל ClientCutText עונה — ואז הדפדפן מקבל
    # "leaked" במקום סגירה (כשל התנהגותי, לא המתנה אינסופית).
    original_write = machine.write

    def leaking_write(payload: bytes) -> None:
        original_write(payload)
        if payload.startswith(b"\x06"):
            machine._reply(b"leaked")
    machine.write = leaking_write
    power_off = b"\x06\x00\x00\x00" + struct.pack(">I", 23) + b"imagectl-power:poweroff"

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        websocket.send_bytes(FB_REQUEST)
        websocket.send_bytes(power_off)
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()

    assert (caught.value.code, caught.value.reason) == (
        monitor.WS_FORBIDDEN, monitor.POWER_OVER_WS_REFUSED)
    assert FB_REQUEST in machine.writes
    assert not any(w.startswith(b"\x06") for w in machine.writes)
    assert not any(b"imagectl-power" in w for w in machine.writes)
    assert machine.closed is True
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} refused", "noc")]


@pytest.mark.parametrize("header, close_code, close_reason", [
    (b"\x06\x00\x00\x00" + struct.pack(">I", 1 << 30),
     monitor.WS_FORBIDDEN, monitor.POWER_OVER_WS_REFUSED),   # רגיל: 1GB "בדרך"
    (b"\x06\x00\x00\x00" + struct.pack(">i", -(1 << 30)),
     1009, "הודעת RFB גדולה מדי"),                          # מורחב, ענק
])
def test_client_cut_text_is_refused_on_its_header_not_after_its_body(
        server, header, close_code, close_reason):
    """‏#1392: ההחלטה על ClientCutText נופלת כשהאורך ידוע (8 בייטים), ולא
    אחרי שכל הגוף הגיע — אורך שמבטיח 1GB אינו משאיר את הגשר ממתין ובולע
    (רגיל → ‏4403 כמו תמיד; מורחב ענק → ‏1009 בשם)."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        websocket.send_bytes(header)
        # המתנה לסגירת השרת מוכיחה שהכותרת לבדה הכריעה, וגם מונעת מהיציאה
        # מה-context להתחרות בקריאת הכותרת ולרשום ניתוק רגיל של הדפדפן.
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()
    assert (caught.value.code, caught.value.reason) == (close_code, close_reason)
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} refused", "noc")]
    assert not any(w.startswith(b"\x06") for w in machine.writes)


def test_client_messages_are_forwarded_whole_even_when_split_across_frames(server):
    """הגשר מצרף מסגרות: הודעה שהגיעה בשני חלקים נכתבת למכונה כיחידה
    אחת, ושתי הודעות במסגרת אחת נכתבות בנפרד. סוג לא מוכר → 1002."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    key_event = b"\x04\x01\x00\x00\x00\x00\xff\x0d"     # KeyEvent Enter down
    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        websocket.send_bytes(FB_REQUEST[:4])
        websocket.send_bytes(FB_REQUEST[4:] + key_event)
        websocket.send_bytes(b"\x63garbage")
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()

    assert caught.value.code == 1002
    assert machine.writes[-2:] == [FB_REQUEST, key_event]


def test_client_message_length_table():
    assert monitor.client_message_length(b"\x00") == 20
    assert monitor.client_message_length(b"\x03") == 10
    assert monitor.client_message_length(b"\x04") == 8
    assert monitor.client_message_length(b"\x05") == 6
    assert monitor.client_message_length(b"\x02\x00") is None
    assert monitor.client_message_length(b"\x02\x00\x00\x03") == 16
    assert monitor.client_message_length(b"\x06\x00\x00") is None
    assert monitor.client_message_length(monitor.power_frame("reboot")) == 8 + len(b"imagectl-power:reboot")
    with pytest.raises(ValueError):
        monitor.client_message_length(b"\x63")


def test_client_message_length_knows_what_novnc_1_6_sends():
    """‏#1392: noVNC 1.6 (מסך ה-VM, #1334) שולח הודעות ש-monitor.js לא שלח
    מעולם. כל אחת בגבול הנכון — אחרת הגשר ממתין לבייטים שלא יגיעו ובולע
    את כל מה שאחריה, בלי לסגור (המסך קופא, המקלדת לא עוברת)."""
    # ClientCutText מורחב (Extended Clipboard): אורך **שלילי** כ-s32. noVNC
    # עונה כך ל-caps ש-QEMU 10 שולח מיד אחרי SetEncodings. ‏-8 אינו 4GB.
    caps = b"\x06\x00\x00\x00" + struct.pack(">i", -8) + b"\x1f\x00\x00\x01" + b"\x00" * 4
    assert monitor.client_message_length(caps) == 16
    # QEMU Extended Key Event (255, תת-סוג 0) — כל מקש אחרי ש-QEMU אישר -258.
    shift = b"\xff\x00\x00\x01" + struct.pack(">II", 0xffe1, 0x2a)
    assert monitor.client_message_length(shift) == 12
    assert monitor.client_message_length(b"\xff") is None
    with pytest.raises(ValueError):
        monitor.client_message_length(b"\xff\x01\x00\x00")   # QEMU audio — לא נשלח
    # ClientFence (248): ‏9 בייטים + מטען באורך הבייט התשיעי.
    assert monitor.client_message_length(b"\xf8\x00\x00\x00\x00\x00\x00\x00") is None
    assert monitor.client_message_length(b"\xf8" + b"\x00" * 7 + b"\x05") == 14
    # EnableContinuousUpdates (150): קבוע.
    assert monitor.client_message_length(b"\x96") == 10


def test_power_frame_is_client_cut_text_with_the_token():
    frame = monitor.power_frame("reboot")
    assert frame == b"\x06\x00\x00\x00" + struct.pack(">I", 21) + b"imagectl-power:reboot"
    assert monitor.power_frame("poweroff").endswith(b"imagectl-power:poweroff")


def test_power_endpoint_is_admin_only_and_verifies_the_server_side_name(server):
    """‏403 בלי השם הקנוני (`suffix` מהטבלה — לא `?name=` מהכתובת), גם
    כשמוניטור פתוח; 409 בלי מוניטור פתוח; deploy → 403, אנונימי → 401;
    פעולה לא מוכרת → 400. שום דבר מזה אינו נוגע במכונה."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    path = f"/api/console/monitor/{MAC}/power"

    assert client.post(path, json={"action": "reboot", "confirm": "Monitor"}).status_code == 409
    assert client.post(path, json={"action": "reboot", "confirm": "monitor"}).status_code == 403
    assert client.post(path, json={"action": "halt", "confirm": "Monitor"}).status_code == 400
    assert client.post("/api/console/monitor/not-a-mac/power",
                       json={"action": "reboot", "confirm": "Monitor"}).status_code == 404
    assert monitor_client(server, "deploy", connector).post(
        path, json={"action": "reboot", "confirm": "Monitor"}).status_code == 403
    anon = monitor_client(server, "admin", connector)
    anon.cookies.clear()
    assert anon.post(path, json={"action": "reboot", "confirm": "Monitor"}).status_code == 401

    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        wrong = client.post(path, json={"action": "poweroff", "confirm": "monitor"})
        assert wrong.status_code == 403
        assert "הוקלד" not in wrong.json()["detail"]   # #1850: מאז #1270 איש אינו מקליד
        assert wrong.json()["detail"] == monitor.POWER_NAME_STALE
        assert not any(w.startswith(b"\x06") for w in machine.writes)
    assert journal_rows(server, "monitor_power") == []


def test_power_endpoint_writes_the_token_to_the_machine_and_journals_who(server):
    """הראיה החיובית: עם מוניטור פתוח והשם הנכון, הפריים
    ‏ClientCutText(`imagectl-power:reboot`) נכתב למכונה **מהשרת**,
    ‏`monitor_power` נרשם עם המשתמש וה-MAC, וה-WS של הדפדפן נשאר פתוח."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    path = f"/api/console/monitor/{MAC}/power"
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        ok = client.post(path, json={"action": "reboot", "confirm": "Monitor"})
        assert ok.status_code == 200, ok.text
        assert ok.json() == {"ok": True, "action": "reboot"}
        websocket.send_bytes(FB_REQUEST)          # הגשר עדיין חי אחרי הפקודה
    assert monitor.power_frame("reboot") in machine.writes
    assert machine.writes[-1] == FB_REQUEST
    assert journal_rows(server, "monitor_power") == [(f"{MAC} reboot", "noc")]
    # אחרי הסגירה אין גשר, ולכן אין למי לכתוב — 409, לא פקודה למכונה מתה.
    assert client.post(path, json={"action": "reboot", "confirm": "Monitor"}).status_code == 409


def test_monitor_connect_and_close_are_journaled_with_user_and_mac(server):
    """"מי חיבר מוניטור למי ומתי" — `monitor_connected` בפתיחה (אחרי אימות
    המכונה, לפני הגשר) ו-`monitor_closed` עם הסיבה. סגירה מהדפדפן =
    ``browser``; סגירה מצד המכונה = ``machine``."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
    assert journal_rows(server, "monitor_connected") == [(MAC, "noc")]
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} browser", "noc")]

    # דחייה לפני הגשר (deploy) אינה "חיבור" — אין שורה.
    with pytest.raises(WebSocketDisconnect):
        with monitor_client(server, "deploy", connector).websocket_connect(
                f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()
    assert len(journal_rows(server, "monitor_connected")) == 1


def test_machine_socket_closing_mid_session_gets_a_distinct_browser_close(server):
    """A dead machine must not leave its last framebuffer looking live."""
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        machine._reply(b"")
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()

    assert (caught.value.code, caught.value.reason) == (
        monitor.WS_MACHINE_GONE, monitor.MACHINE_GONE_REASON)
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} machine", "noc")]


def test_idle_bridge_closes_both_sides_and_releases_the_machine(server, monkeypatch):
    """‏WS שנשאר פתוח בלי בייט לאף כיוון: אחרי IDLE_SECONDS הדפדפן מקבל
    4408 בשם, ה-TCP למכונה נסגר, ‏`monitor_closed … idle` נרשם — והמנעול
    משתחרר: חיבור שני לאותה מכונה מתקבל (לא 4409)."""
    monkeypatch.setattr(monitor, "IDLE_SECONDS", 0.3, raising=False)
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        with pytest.raises(WebSocketDisconnect) as caught:
            websocket.receive_bytes()
    assert caught.value.code == monitor.WS_IDLE
    assert "בלי תעבורה" in caught.value.reason
    assert machine.closed is True
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} idle", "noc")]

    second = FakeMachine()                        # המכונה כבר רשומה עם הסוד

    async def connector2(_host, _port):
        return second, second

    with monitor_client(server, "admin", connector2).websocket_connect(
            f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT     # לא 4409


def test_traffic_resets_the_idle_clock(server, monkeypatch):
    """תעבורה מהדפדפן בתוך החלון דוחה את הסגירה — הטיימר הוא "בלי תעבורה",
    לא "מאז הפתיחה"."""
    import time
    monkeypatch.setattr(monitor, "IDLE_SECONDS", 0.4, raising=False)
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        for _ in range(3):
            time.sleep(0.25)
            websocket.send_bytes(FB_REQUEST)
    assert journal_rows(server, "monitor_closed") == [(f"{MAC} browser", "noc")]
    assert machine.writes.count(FB_REQUEST) == 3


def test_journal_translates_monitor_events_to_hebrew(server):
    from server.journal_he import EVENTS_HE, JournalTranslator
    prepare_build_machine(server)
    tr = JournalTranslator(server["ctx"].conn, server["ctx"].library)
    for event in ("monitor_connected", "monitor_closed", "monitor_power"):
        assert event in EVENTS_HE
    who = tr._machines[MAC]                        # "Monitor · <שם הקבוצה>"
    assert who.startswith("Monitor · ")
    assert tr.translate("monitor_connected", MAC) == (
        EVENTS_HE["monitor_connected"], who)
    assert tr.translate("monitor_closed", f"{MAC} idle")[1] == (
        f"{who} · נסגר אחרי 10 דק' בלי תעבורה")
    assert tr.translate("monitor_closed", f"{MAC} refused")[1] == (
        f"{who} · נסגר — הודעה אסורה מהדפדפן")
    assert tr.translate("monitor_power", f"{MAC} poweroff")[1] == f"{who} · כיבוי"
    assert tr.translate("monitor_power", f"{MAC} reboot node=n0de")[1] == (
        f"{who} · הפעלה מחדש · node=n0de")


def test_monitor_fullscreen_is_the_whole_page_with_a_floating_bar():
    """stage 3 (console-full.html #fs): "מסך מלא" מעביר את **הדף כולו** (documentElement) למסך מלא — לא רק
    את .mon-wrap — כדי שחלון האישור של כיבוי/הפעלה מחדש (מחוץ ל-.mon-wrap) ייראה; הסרגל הופך לכדור צף
    שמוסתר עד תזוזת עכבר. דפדפן בלי fullscreenEnabled מקבל כפתור מנוטרל עם הסבר."""
    from pathlib import Path

    static = Path(__file__).resolve().parent.parent / "server" / "static"
    page = (static / "monitor.html").read_text(encoding="utf-8")
    js = (static / "monitor.js").read_text(encoding="utf-8")
    assert 'id="fs-btn"' in page
    assert "html:fullscreen .mon-bar" in page and "html:fullscreen.bar-on .mon-bar" in page
    assert "document.documentElement.requestFullscreen()" in js
    assert "mon-wrap.requestFullscreen" not in js and ".mon-wrap').requestFullscreen" not in js
    assert "document.fullscreenEnabled" in js and "2500" in js
