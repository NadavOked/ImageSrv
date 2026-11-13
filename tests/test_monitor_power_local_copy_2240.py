"""#2240 — כפתורי הכוח של המוניטור באמצע שיכפול מקומי (הכרעה 03/11, אפשרות ב׳).

עד כאן `POST /monitor/{mac}/power` כתב את אסימון הכוח ל-monitor.c, שמריץ
`poweroff -f` — בלי האזהרה של חלון המחשב (#1367) ובלי `lc_power_commit`: על
משכפל 2 (31/10) יעד שהיה באמצע אימות עלה כ-Windows תקין. עכשיו:

* `…/power` מסרב (409, `local_copy: true`) כשהמחשב באמצע שיכפול מקומי, והפריים
  אינו נכתב — אלא אם המפעיל בחר "המחשב לא עונה" (`immediate: true`, כפתור החירום).
* `…/agent-power` = "כבה/אתחל בכל זאת": `request_power(confirmed=True)` — ה-hello הבא
  נושא `power_action` + `power_confirmed`, והסוכן מסמן את היעדים לפני הכיבוי.
* `local_copy` ברשימת המוניטור — הדף מזהיר כבר בלחיצה הראשונה.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")

from server.console import monitor  # noqa: E402
from server.interserver import api as interserver_api  # noqa: E402
from server.rounds import room_console  # noqa: E402
from test_monitor import (  # noqa: E402
    MAC, SERVER_INIT, browser_handshake, journal_rows, monitor_client, prepare_secret_machine,
)


def _copying(server, copying: bool = True) -> None:
    """מה ש-hello עם `local_clone: "copying"` משאיר ב-net_devices (attended.sh)."""
    conn = server["ctx"].conn
    conn.execute("UPDATE net_devices SET local_clone = ?, last_seen = ? WHERE mac = ?",
                 ("copying" if copying else None, datetime.now(timezone.utc).isoformat(), MAC))
    conn.commit()


def _client(server):
    machine = prepare_secret_machine(server)

    async def connector(_host, _port):
        return machine, machine

    return machine, monitor_client(server, "admin", connector)


def _frames(machine) -> list[bytes]:
    return [w for w in machine.writes if w.startswith(b"\x06")]


def _pending(server):
    return server["ctx"].conn.execute(
        "SELECT action, confirmed FROM power_requests WHERE mac = ?", (MAC,)).fetchone()


POWER = f"/api/console/monitor/{MAC}/power"
AGENT = f"/api/console/monitor/{MAC}/agent-power"


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
def test_power_over_a_local_copy_is_refused_and_never_reaches_the_machine(server, action):
    machine, client = _client(server)
    _copying(server)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        refused = client.post(POWER, json={"action": action, "confirm": "Monitor"})
        assert refused.status_code == 409, refused.text
        assert refused.json() == {"detail": monitor.POWER_OVER_LOCAL_COPY, "local_copy": True}
    assert _frames(machine) == []
    assert _pending(server) is None
    assert journal_rows(server, "monitor_power") == []


@pytest.mark.parametrize("action", ["poweroff", "reboot"])
def test_confirmed_power_over_a_local_copy_goes_through_the_agent(server, action):
    """"כבה בכל זאת": בקשה מאושרת בערוץ ה-hello, לא פריים — והיא נמסרת כ-confirmed."""
    machine, client = _client(server)
    _copying(server)
    ok = client.post(AGENT, json={"action": action, "confirm": "Monitor", "confirm_local_copy": True})
    assert ok.status_code == 200, ok.text
    assert ok.json() == {"requested": 1, "action": action,
                         "ttl_s": room_console.POWEROFF_TTL_SECONDS}
    assert _frames(machine) == []
    assert room_console.take_power(server["ctx"].conn, MAC) == (action, True)
    assert journal_rows(server, "monitor_power") == [
        (f"{MAC} {action} — דרך הסוכן, באישור מעל שיכפול מקומי", "noc")]


def test_the_agent_path_takes_a_dashed_mac(server):
    """דרך משני ה-MAC נוסע במקפים — `:` אינו עובר את בדיקת הנתיב של הערוץ הבין-שרתי."""
    _machine, client = _client(server)
    _copying(server)
    dashed = f"/api/console/monitor/{MAC.replace(':', '-')}/agent-power"
    ok = client.post(dashed, json={"action": "poweroff", "confirm": "Monitor",
                                   "confirm_local_copy": True})
    assert ok.status_code == 200, ok.text
    assert _pending(server)["confirmed"] == 1


def test_the_agent_path_refuses_what_was_not_confirmed_over_a_local_copy(server):
    _machine, client = _client(server)
    _copying(server)
    body = {"action": "poweroff", "confirm": "Monitor"}
    assert client.post(AGENT, json=body).status_code == 400                       # בלי הדגל
    assert client.post(AGENT, json={**body, "confirm_local_copy": "true"}).status_code == 400
    assert client.post(AGENT, json={**body, "confirm_local_copy": True,
                                    "action": "halt"}).status_code == 400
    assert client.post(AGENT, json={**body, "confirm_local_copy": True,
                                    "confirm": "monitor"}).status_code == 403
    assert monitor_client(server, "deploy", None).post(
        AGENT, json={**body, "confirm_local_copy": True}).status_code == 403
    assert _pending(server) is None
    # השיכפול הסתיים בינתיים: לא `poweroff -f` במקום, ולא בקשה — 409 בגלוי.
    _copying(server, copying=False)
    late = client.post(AGENT, json={**body, "confirm_local_copy": True})
    assert late.status_code == 409 and late.json()["detail"] == monitor.NOT_IN_LOCAL_COPY
    assert _pending(server) is None
    assert journal_rows(server, "monitor_power") == []


def test_immediate_power_over_a_local_copy_is_still_the_emergency_button(server):
    """"המחשב לא עונה? כיבוי מיידי": הפריים נכתב כמו לפני, ונרשם שזה היה באמצע שיכפול."""
    machine, client = _client(server)
    _copying(server)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        ok = client.post(POWER, json={"action": "poweroff", "confirm": "Monitor", "immediate": True})
        assert ok.status_code == 200, ok.text
    assert _frames(machine) == [monitor.power_frame("poweroff")]
    assert _pending(server) is None
    assert journal_rows(server, "monitor_power") == [
        (f"{MAC} poweroff — מיידי, באמצע שיכפול מקומי, בלי סימון היעדים", "noc")]


def test_power_without_a_local_copy_is_unchanged(server):
    """הצד החיובי: מחשב שאינו משכפל — הפריים נכתב בלי שאלה, גם בלי `immediate`."""
    machine, client = _client(server)
    _copying(server, copying=False)
    with client.websocket_connect(f"/api/console/monitor/{MAC}") as websocket:
        browser_handshake(websocket)
        assert websocket.receive_bytes() == SERVER_INIT
        assert client.post(POWER, json={"action": "reboot", "confirm": "Monitor"}).status_code == 200
    assert _frames(machine) == [monitor.power_frame("reboot")]
    assert journal_rows(server, "monitor_power") == [(f"{MAC} reboot", "noc")]


def test_the_monitor_list_says_a_local_copy_is_writing(server):
    prepare_secret_machine(server)
    _copying(server)
    rows = {r["mac"]: r for r in monitor.machine_rows(server["ctx"].conn)}
    assert rows[MAC]["local_copy"] is True
    _copying(server, copying=False)
    rows = {r["mac"]: r for r in monitor.machine_rows(server["ctx"].conn)}
    assert rows[MAC]["local_copy"] is False


@pytest.mark.parametrize(("path", "allowed"), [
    ("/monitor/aa-bb-cc-dd-ee-01/agent-power", True),
    ("/monitor/aa-bb-cc-dd-ee-01/power", False),       # poweroff -f נשאר במנהרה בלבד
    ("/monitor/aa:bb:cc:dd:ee:01/agent-power", False),  # `:` אינו עובר את בדיקת הנתיב
])
def test_only_the_agent_path_is_open_to_the_primary(path, allowed):
    assert interserver_api.console_write_allowed("POST", path) is allowed
