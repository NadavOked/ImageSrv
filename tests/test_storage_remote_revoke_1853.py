"""‏#1853 — המוניטור **דרך שרת משנה** נסגר כשההתחברות לקונסולה בוטלה.

אותם שני שרתים אמיתיים על loopback כמו ב-``test_storage_remote``. בקרה שלילית:
על הבסיס (round/26) הגשר מהראשי נשאר פתוח אחרי logout — היומן אינו רושם
סגירה תוך 5 ש' והמנהרה למכונה אינה נסגרת.
"""

from __future__ import annotations

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from server.console import auth
from server.interserver import auth as interserver_auth
from test_monitor_revoke_1853 import SESSION_ENDED, _wait_closed
from test_storage_remote import (MAC, SERVER_INIT, _browser_handshake, _ws_path,  # noqa: F401
                                 machine)
from test_storage_transfer import paired, secondary                              # noqa: F401

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)


def test_console_logout_closes_the_monitor_through_a_secondary(server, secondary, paired,
                                                               machine):
    admin = server["admin"]
    console = TestClient(server["app"])
    console.cookies.set(auth.COOKIE_NAME, admin.cookies.get(auth.COOKIE_NAME))
    with admin.websocket_connect(_ws_path(paired)) as ws:
        _browser_handshake(ws)
        assert ws.receive_bytes() == SERVER_INIT
        assert console.post("/api/console/logout").status_code == 200
        assert _wait_closed(server) == [f"{MAC} session_revoked node={paired}"], \
            "the monitor bridge through the secondary stayed open after logout"
        with pytest.raises(WebSocketDisconnect) as gone:
            ws.receive_bytes()
        assert gone.value.code == 4401 and gone.value.reason == SESSION_ENDED
    assert machine.closed.wait(5), "the tunnel to the machine stayed open"
