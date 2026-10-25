"""‏#1837 — חיבור חוזר למוניטור בניסיון הראשון.

משכפל 2, ‏19/10: אחרי כל סשן מאומת הסוכן הגריל סוד חדש, והשרת למד אותו
רק ב-hello הבא. כל חיבור חוזר לפני ה-hello נדחה ב-`password check failed!`
— שש דחיות ברצף במעבר מהיר בין שלוש מכונות. התיקון: הסוד הבא **נגזר**
בשני הצדדים מאותו סוד ומאותו challenge (HMAC-SHA256(סוד,
‏"imagectl-monitor-rekey"‖challenge)[:16]), ברגע האימות. ‏`RekeyingMachine`
כאן הוא המראה של `agent/monitor.c` — הגזירה כתובה בו **בנפרד** מ-
‏`server.monitor.next_secret`, כדי שהטסט לא יאשר את עצמו;
‏`test_monitor_native.py` מריץ את הבינארי האמיתי מול אותו וקטור.

בקרה שלילית: על הקוד שלפני התיקון (‏`server/*` ו-`agent/monitor.c` של
הבסיס) החיבור השני נסגר 4512, ה-hello הישן דורס, והסיבה מגיעה באנגלית.
"""

from __future__ import annotations

import hashlib
import hmac as hmaclib
import re
import struct

import pytest

pytest.importorskip("fastapi")

from starlette.websockets import WebSocketDisconnect

from server import monitor
from server import db as db_module
from test_monitor import (CHALLENGE, MAC, SECRET, SERVER_INIT, FakeMachine,
                          browser_handshake, hello_with_secret, hmac16,
                          monitor_client, prepare_secret_machine, stored_secret)

#: וקטור ידוע של הגזירה — חושב פעם אחת, לא בזמן הטסט. ‏`test_monitor_native`
#: מוודא שהבינארי של הסוכן כותב לקובץ הסוד בדיוק את זה.
KNOWN_NEXT = "fe24991e4bf38d8e110c7f8010d5806b"


def rekey(secret: str, challenge: bytes) -> str:
    """‏`rekey_secret` של monitor.c, כתוב כאן בנפרד מהשרת."""
    return hmaclib.new(bytes.fromhex(secret), b"imagectl-monitor-rekey" + challenge,
                       hashlib.sha256).digest()[:16].hex()


class RekeyingMachine(FakeMachine):
    """מוניטור מזויף **מחמיר**: בודק את ה-HMAC מול הסוד שלו (‏`state`, משותף
    לכל החיבורים — כמו התהליך האחד במכונה), ובהצלחה עובר לסוד הבא."""

    def __init__(self, state: dict):
        super().__init__()
        self.state = state
        self.verdict: int | None = None

    def accepts(self, response: bytes) -> bool:
        return hmaclib.compare_digest(response, hmac16(self.state["secret"], CHALLENGE))

    def write(self, payload: bytes) -> None:
        if (self.response is None and len(payload) == 16
                and b"\x02" in self.writes):
            self.writes.append(payload)
            self.response = payload
            ok = self.accepts(payload)
            if ok:
                self.state["secret"] = rekey(self.state["secret"], CHALLENGE)
            self.verdict = 0 if ok else 1
            reply = struct.pack(">I", self.verdict)
            if not ok:
                reason = b"password check failed!"
                reply += struct.pack(">I", len(reason)) + reason
            self._reply(reply)
            return
        super().write(payload)


def stored_prev(server) -> str | None:
    row = server["ctx"].conn.execute(
        "SELECT monitor_secret_prev FROM net_devices WHERE mac = ?", (MAC,)
    ).fetchone()
    return row["monitor_secret_prev"] if row else None


def watch_once(client) -> None:
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT


# --- ‏#1837 -------------------------------------------------------------------


def test_next_secret_matches_the_known_vector_and_the_agent_mirror():
    """השרת והמראה של הסוכן גוזרים אותו סוד — ושניהם שונים מהסוד הקודם
    ומהתשובה ל-challenge (הסוד הבא אינו משהו שעבר על החוט)."""
    assert monitor.next_secret(SECRET, CHALLENGE) == KNOWN_NEXT
    assert rekey(SECRET, CHALLENGE) == KNOWN_NEXT
    assert KNOWN_NEXT != SECRET
    assert bytes.fromhex(KNOWN_NEXT) != hmac16(SECRET, CHALLENGE)


def test_two_connections_in_a_row_without_a_hello_both_authenticate(server):
    """**הבאג עצמו.** שני חיבורים ברצף, אפס hello ביניהם: שניהם מקבלים
    ServerInit בניסיון הראשון. עד #1837 השני נסגר 4512 ("password check
    failed!"), כי המכונה עברה לסוד שהשרת עוד לא ידע."""
    prepare_secret_machine(server)
    state = {"secret": SECRET}
    machines: list[RekeyingMachine] = []

    async def connector(_host, _port):
        machine = RekeyingMachine(state)
        machines.append(machine)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    watch_once(client)
    watch_once(client)
    watch_once(client)

    assert [m.verdict for m in machines] == [0, 0, 0]
    assert stored_secret(server) == state["secret"]      # השרת והמכונה באותו סוד


def test_a_response_captured_from_an_earlier_session_is_rejected(server):
    """ההגנה מפני שידור חוזר נשמרת. ה-challenge כאן **קבוע** — המקרה הגרוע
    ביותר — ובכל זאת התשובה שנלכדה בסשן הראשון נדחית אחריו: היא נשענת על
    סוד שהמכונה כבר עזבה."""
    prepare_secret_machine(server)
    state = {"secret": SECRET}
    machines: list[RekeyingMachine] = []

    async def connector(_host, _port):
        machine = RekeyingMachine(state)
        machines.append(machine)
        return machine, machine

    watch_once(monitor_client(server, "admin", connector))
    captured = machines[0].response
    assert captured == hmac16(SECRET, CHALLENGE)
    assert RekeyingMachine(state).accepts(captured) is False


def test_a_hello_built_before_the_rekey_does_not_bring_the_old_secret_back(server):
    """‏hello שהסוכן בנה לפני האימות והגיע אחריו נושא את הסוד הקודם. דריסה
    בו הייתה מחזירה את ה-"bad HMAC" בדיוק כשהמפעיל חוזר למכונה. הוא נזנח
    פעם אחת; ‏hello שני עם אותו סוד הוא ראיה שהמכונה באמת נשארה עליו."""
    prepare_secret_machine(server)
    state = {"secret": SECRET}

    async def connector(_host, _port):
        machine = RekeyingMachine(state)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    watch_once(client)
    assert stored_secret(server) == KNOWN_NEXT
    assert stored_prev(server) == SECRET

    hello_with_secret(server, SECRET, auth="hmac")       # בנוי לפני האימות
    assert stored_secret(server) == KNOWN_NEXT
    watch_once(client)                                    # ועדיין מתחבר מיד

    hello_with_secret(server, SECRET, auth="hmac")       # ‏hello אחרי הניקוי
    assert stored_secret(server) == SECRET


def test_a_rekey_does_not_overwrite_a_secret_a_newer_hello_brought(server):
    """המכונה אותחלה (סוד חדש ב-hello) — אימות ישן אינו דורס אותו."""
    prepare_secret_machine(server)
    fresh = "ffeeddccbbaa99887766554433221100"
    hello_with_secret(server, fresh, auth="hmac")
    assert db_module.monitor_rekeyed(server["ctx"].conn, MAC, SECRET, KNOWN_NEXT) is False
    assert stored_secret(server) == fresh


def test_a_rejected_secret_reaches_the_bar_in_hebrew(server):
    """הסיבה שהמפעיל רואה היא עברית, בלי "password check failed!" של
    LibVNCServer — זו גלשה על שם המכונה בפס העליון."""
    prepare_secret_machine(server)
    state = {"secret": "00" * 16}                         # המכונה על סוד אחר

    async def connector(_host, _port):
        machine = RekeyingMachine(state)
        return machine, machine

    client = monitor_client(server, "admin", connector)
    with pytest.raises(WebSocketDisconnect) as caught:
        with client.websocket_connect(f"/api/console/monitor/{MAC}") as ws:
            ws.receive_bytes()
    assert caught.value.code == monitor.WS_MACHINE_AUTH
    assert not re.search(r"[A-Za-z]", caught.value.reason), caught.value.reason
    assert "דחתה את אימות השרת" in caught.value.reason, caught.value.reason


def test_the_status_line_cannot_spill_over_the_machine_name():
    """הפס העליון: הסטטוס בשורה אחת שמתכווצת (…), והטקסט המלא ב-title."""
    from pathlib import Path
    static = Path(__file__).resolve().parent.parent / "server" / "static"
    page = (static / "monitor.html").read_text(encoding="utf-8")
    status_rule = re.search(r"\.status \{([^}]*)\}", page).group(1)
    for needed in ("min-width: 0", "white-space: nowrap", "text-overflow: ellipsis",
                   "overflow: hidden"):
        assert needed in status_rule, needed
    js = (static / "monitor.js").read_text(encoding="utf-8")
    assert "statusEl.title = text" in js
