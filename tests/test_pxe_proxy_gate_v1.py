"""הכרעת נדב 11/10: ‏PXE proxy מסורב **בשרת** כש-`capabilities.classrooms()` כבוי.

‏proxy (‏#36) קיים בשביל תחנות הכיתה שמקבלות כתובת מה-DHCP של המכללה —
כלומר הוא חלק מהכיתות, ו-v1 בלי כיתות. עד כאן רק הממשק הסתיר אותו, ו-
‏`PUT /api/console/net/interfaces/{n}` עם ‏proxy=true עדיין הדליק אותו
(ממצא סקר השאריות, 11/10).

‏409 עם סיבה כשהיכולת כבויה; כיבוי proxy (‏proxy=false) ו-DHCP מלא לא
מושפעים. בדיקות ‏#36 הקיימות מדליקות את היכולת ב-monkeypatch (v1.2).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from server.core import capabilities

from test_server_dhcp import GOOD, dhcp_server  # noqa: F401 — fixture

PROXY = {"proxy": True, "server_ip": "10.44.1.10",
         "confirm": "eth1", "confirm_proxy_broken": True}


def test_proxy_is_refused_while_classrooms_are_off(dhcp_server):  # noqa: F811
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert capabilities.classrooms() is False, "v1 — הבדיקה מניחה כיתות כבויות"
    r = admin.put("/api/console/net/interfaces/eth1", json=PROXY)
    assert r.status_code == 409, r.text
    assert "proxy" in r.json()["detail"] and "כיתות" in r.json()["detail"]
    assert not fake["proxy_applied"], "ה-proxy הוחל למרות הסירוב"
    eth1 = [i for i in admin.get("/api/console/net/interfaces").json()
            if i["name"] == "eth1"][0]
    assert eth1["proxy"] is False


def test_full_dhcp_and_proxy_off_still_work_while_classrooms_are_off(dhcp_server):  # noqa: F811
    admin = dhcp_server["admin"]
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    assert admin.put("/api/console/net/interfaces/eth1",
                     json={"proxy": False}).status_code == 200


def test_proxy_works_when_classrooms_are_on(dhcp_server, monkeypatch):  # noqa: F811
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    r = dhcp_server["admin"].put("/api/console/net/interfaces/eth1", json=PROXY)
    assert r.status_code == 200, r.text
    assert dhcp_server["fake"]["proxy_applied"][-1][1] is True


# --- #1303: רשומת proxy ישנה ב-DB, ונתיב הקריאה --------------------------------


def _stale_proxy_row(ctx) -> None:
    """רשומת proxy=true שנשמרה לפני הגידור של 11/10 (שרת המעבדה, למשל):
    נכתבת ישר ל-DB, כי ה-API כבר מסרב לכתוב אותה."""
    from server.net import dhcp
    from server.db import set_setting
    stale = dhcp.InterfaceConfig(name="eth1", enabled=False, proxy=True,
                                 server_ip="10.44.1.10")
    set_setting(ctx.conn, dhcp.SETTING_PREFIX + "eth1", stale.to_json())


def test_a_stale_proxy_row_is_not_applied_while_classrooms_are_off(dhcp_server):  # noqa: F811
    """‏#1303: ‏`apply_dnsmasq` רינדר והפעיל את ה-proxy לפי ה-DB בלי לבדוק את
    היכולת — שינוי בכרטיס **אחר** היה מדליק את `imagesrv-proxy` על v1."""
    admin, fake = dhcp_server["admin"], dhcp_server["fake"]
    assert capabilities.classrooms() is False, "v1 — הבדיקה מניחה כיתות כבויות"
    _stale_proxy_row(dhcp_server["ctx"])
    assert admin.put("/api/console/net/interfaces/eth0",
                     json={**GOOD, "confirm": "eth0"}).status_code == 200
    text, active = fake["proxy_applied"][-1]
    assert active is False, "היחידה של ה-proxy הופעלה על v1"
    assert "interface=eth1" not in text, text


def test_a_stale_proxy_row_still_applies_when_classrooms_are_on(  # noqa: F811
        dhcp_server, monkeypatch):
    """הרדיוס: ב-v1.2 אותה רשומה היא proxy חוקי, והוא מוחל כרגיל."""
    monkeypatch.setattr(capabilities, "CLASSROOMS", True)
    _stale_proxy_row(dhcp_server["ctx"])
    assert dhcp_server["admin"].put("/api/console/net/interfaces/eth0",
                                    json={**GOOD, "confirm": "eth0"}).status_code == 200
    text, active = dhcp_server["fake"]["proxy_applied"][-1]
    assert active is True and "interface=eth1" in text


def test_proxy_support_is_not_served_while_classrooms_are_off(dhcp_server):  # noqa: F811
    """‏#1303: ‏`GET /net/proxy-support` לא היה מגודר. ‏v1 בלי proxy — הנתיב
    אינו קיים (404, כמו ארגז הכלים), ובלי לחשוף את גרסת ה-dnsmasq."""
    r = dhcp_server["admin"].get("/api/console/net/proxy-support")
    assert r.status_code == 404, r.text
    assert "2.91" not in r.text
