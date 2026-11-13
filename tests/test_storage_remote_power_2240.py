"""#2240 במסלול המשני — מוניטור מהראשי למכונה של המשני שבאמצע שיכפול מקומי.

`storage.remote_power` כתב את אסימון הכוח למנהרה בלי לשאול: `poweroff -f` עקף את
`lc_power_commit` גם כאן. עכשיו (כמו `monitor.power`): `local_copy` שהמשני מדווח
ב-`/machines` → 409 בלי `immediate`; "כבה בכל זאת" עובר ב-relay הכללי
(`CONSOLE_WRITES`) ל-`…/agent-power` **של המשני** — נבדק ב-``test_secondary_console_writes``,
שם למשני יש אפליקציית קונסולה. כאן: אותם שני שרתים כמו ב-``test_storage_remote``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server.console import monitor  # noqa: E402
from server.interserver import auth as interserver_auth  # noqa: E402
from test_storage_remote import (  # noqa: E402,F401 — fixtures
    MAC, SERVER_INIT, _browser_handshake, _one_event_loop, _ws_path, machine,
)
from test_storage_transfer import paired, secondary  # noqa: E402,F401 — fixtures

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)


def _copying(secondary) -> None:
    """מה ש-hello עם `local_clone: "copying"` משאיר אצל המשני."""
    conn = secondary["conn"]
    conn.execute("UPDATE net_devices SET local_clone = 'copying', last_seen = ? WHERE mac = ?",
                 (datetime.now(timezone.utc).isoformat(), MAC))
    conn.commit()


def _journal(conn, event="monitor_power"):
    return [(r["detail"], r["user"]) for r in conn.execute(
        "SELECT detail, user FROM journal WHERE event = ? ORDER BY id", (event,))]


def test_remote_power_over_a_local_copy_is_refused_and_the_tunnel_stays_silent(
        server, secondary, paired, machine):
    _copying(secondary)
    admin = server["admin"]
    path = f"/api/console/storage-nodes/{paired}/monitor/{MAC}/power"
    with _one_event_loop(admin):
        with admin.websocket_connect(_ws_path(paired)) as ws:
            _browser_handshake(ws)
            assert ws.receive_bytes() == SERVER_INIT
            refused = admin.post(path, json={"action": "poweroff", "confirm": "Remote"})
            assert refused.status_code == 409, refused.text
            assert refused.json() == {"detail": monitor.POWER_OVER_LOCAL_COPY, "local_copy": True}
            # כפתור החירום: "המחשב לא עונה" — הפריים נוסע במנהרה כמו לפני.
            ok = admin.post(path, json={"action": "poweroff", "confirm": "Remote", "immediate": True})
            assert ok.status_code == 200, ok.text
            assert ws.receive_bytes() == b"echo:" + monitor.power_frame("poweroff")
    assert machine.closed.wait(5)
    assert machine.writes.count(monitor.power_frame("poweroff")) == 1
    assert _journal(server["ctx"].conn) == [
        (f"{MAC} poweroff node={paired} — מיידי, באמצע שיכפול מקומי, בלי סימון היעדים", "noc")]

