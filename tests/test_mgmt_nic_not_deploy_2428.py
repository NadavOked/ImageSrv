"""‏#2428: כרטיס הניהול אינו נעשה רשת ההפצה בשום מסלול, ו-WoL בלי כרטיס הפצה
אינו יוצא לרשת המכללה.

1. ‏`PUT /net/config/{n}/deploy` מסרב לכרטיס הניהול (#1263), אבל ההדלקה הראשונה
   מלשונית ה-DHCP (‏`PUT /net/interfaces/{n}` → ‏`deploy_net.complete`) בדקה רק
   שהכרטיס נושא את `server_ip`. כשהמתקין לא רשם כרטיס ניהול (או שהרישום לא
   נקרא), eth0 — הכרטיס שהקונסולה נקשרת אליו — נעשה רשת ההפצה: ‏dnsmasq,
   ‏67/69/8080 ומולטיקאסט ברשת המכללה (ה-PoC של סריקה 6, ממצא F5).
2. ‏WoL: כשרשת ההפצה לא הוגדרה (מצב המסירה) לא נבנה שולח כפוי, והשידור הלך
   לפי טבלת הניתוב — לרשת המכללה (ממצא F6).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.net import deploy_net
from server.net.wol import magic_packet

from test_servers_dhcp_1088 import (       # noqa: F401 — הפיקסטורה מיובאת בשמה
    GOOD, _complete_ctx, _complete_hooks, console,
)

ON_ETH0 = {**GOOD, "range_start": "10.1.2.50", "range_end": "10.1.2.200",
           "gateway": "10.1.2.1", "server_ip": "10.1.2.3", "confirm": "eth0"}


def test_first_dhcp_enable_on_the_console_nic_is_refused_and_nothing_moves(console):
    """ה-PoC: הרישום של המתקין ריק, ‏`servers_interface="eth0"`. קודם — 200,
    ‏`deploy:interface=eth0` וחומת אש עם deploy_if == servers_if == eth0."""
    build, fake = console
    client, ctx, deploy = build("none")
    response = client.put("/api/console/net/interfaces/eth0", json=ON_ETH0)
    assert response.status_code == 409, response.text
    assert "כרטיס הניהול" in response.json()["detail"]
    assert deploy_net.stored(ctx.conn) == (None, None)
    assert deploy.state.configured is False
    assert fake["deploy_calls"] == [] and fake["applied"] == []


def test_dhcp_on_the_console_nic_is_refused_after_install_too(console):
    """לא רק בהדלקה הראשונה: גם כשרשת ההפצה ביחידה (cli), ‏DHCP על eth0 נדחה."""
    build, fake = console
    client, _ctx, _deploy = build("cli")
    response = client.put("/api/console/net/interfaces/eth0", json=ON_ETH0)
    assert response.status_code == 409, response.text
    assert fake["applied"] == []


def test_complete_refuses_the_console_nic_before_any_step(tmp_path: Path):
    """השומר יושב גם ב-`complete` עצמו — כל מסלול שמשלים את ההתקנה עובר בו."""
    from server.db import connect
    conn = connect(tmp_path / "x.db")
    calls: list = []
    result = deploy_net.complete(_complete_ctx(tmp_path, _complete_hooks(calls)), conn,
                                 interface="eth0", server_ip="10.1.2.3")
    assert result["ok"] is False, result
    assert any("כרטיס הניהול" in e for e in result["errors"])
    assert calls == [] and deploy_net.stored(conn) == (None, None)


def test_first_dhcp_enable_on_the_deploy_nic_still_completes(console):
    """הצד החיובי — עובר גם בלי התיקון: eth1 נושא את 10.44.9.10."""
    build, fake = console
    client, ctx, _deploy = build("none")
    response = client.put("/api/console/net/interfaces/eth1",
                          json={**GOOD, "confirm": "eth1"})
    assert response.status_code == 200, response.text
    assert deploy_net.stored(ctx.conn) == ("eth1", "http://10.44.9.10:8080")


# --- WoL --------------------------------------------------------------------


def test_without_a_deploy_nic_the_server_refuses_wol_by_name(tmp_path, monkeypatch):
    """בלי `interface` השרת אינו נופל לשולח לפי טבלת הניתוב — הוא בונה שולח
    שמסרב, עם הסיבה שהמסך יציג."""
    from server import app as app_module

    pinned = []
    monkeypatch.setattr(app_module.wol, "broadcast_sender",
                        lambda interface=None, **kw: pinned.append(interface) or (lambda p: None))
    app = app_module.create_app(tmp_path / "d", tmp_path / "i", "http://10.44.0.1:8080")
    send = app.state.runtime.wol_send
    assert send is not None, "בלי כרטיס הפצה השרת נפל לשולח שיוצא לפי טבלת הניתוב"
    assert pinned == []
    with pytest.raises(OSError, match="רשת ההפצה לא הוגדרה"):
        send(magic_packet("aa:bb:cc:dd:ee:01"))
