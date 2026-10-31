"""‏#705: ה-API של השירותים לכל כרטיס — admin בלבד, וחוזה התשובות.

הכתובת המקומית של החיבור (`scope["server"]`) היא מה שמכריע "אתה מחובר דרך
הכרטיס הזה" — ‏TestClient ממלא אותה מה-`base_url`, ולכן לקוח על
‏`https://10.10.10.8:8081` מתנהג כמו דפדפן שמחובר דרך ens33.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from conftest import complete_console_login  # noqa: E402
from test_nic_services import NICS  # noqa: E402

URL = "/api/console/net/services"


@pytest.fixture()
def rig(server):
    ctx = server["ctx"]
    nic = ctx.nic_services
    applied, scheduled = [], []
    nic.base.console, nic.base.deploy, nic.base.interserver = "ens33", "ens34", "ens33"
    nic.hooks.update({
        "interfaces": lambda: [dict(n) for n in NICS],
        "schedule": lambda s, fn: scheduled.append(fn),
        "listeners": lambda n: applied.append(n.current_plan()["hosts"]) or None,
        "dnsmasq": lambda n: None, "timesync": lambda n: None,   # #1773: chrony per NIC
        "firewall": lambda n: None})

    def via(address: str) -> TestClient:
        client = TestClient(server["app"], base_url=f"https://{address}:8081")
        complete_console_login(client, ctx.conn, "noc", "admin-pass-123")
        return client
    return {**server, "nic": nic, "applied": applied, "scheduled": scheduled, "via": via}


def test_admin_only(rig):
    assert rig["anon"].get(URL).status_code == 401
    assert rig["deploy"].get(URL).status_code == 403
    assert rig["deploy"].put(f"{URL}/ens35", json={"services": ["console"]}).status_code == 403
    assert rig["deploy"].post(f"{URL}/confirm").status_code == 403
    assert rig["deploy"].post(f"{URL}/revert").status_code == 403
    assert rig["applied"] == [] and not rig["nic"].edited()


def test_read_shows_todays_defaults_and_who_you_are(rig):
    body = rig["via"]("10.10.10.8").get(URL).json()
    rows = {r["name"]: r for r in body["nics"]}
    assert body["services"] == ["console", "monitor", "multicast", "netboot", "interserver", "ntp"]   # #1773
    assert body["edited"] is False and body["you"]["interface"] == "ens33"
    assert rows["ens33"]["services"] == ["console", "monitor", "interserver"]
    assert rows["ens34"]["services"] == ["multicast", "netboot"]
    assert rows["ens35"]["services"] == [] and rows["ens35"]["configured"] is False
    assert body["pending"] is None and body["window_seconds"] == 60


def test_last_console_is_locked(rig):
    r = rig["via"]("10.10.10.8").put(f"{URL}/ens33", json={"services": ["monitor"]})
    assert r.status_code == 409 and r.json()["code"] == "last_console"
    assert "הפעל קונסולה בכרטיס אחר קודם" in r.json()["detail"]


def test_handoff_flow_from_the_nic_you_are_on(rig):
    on33 = rig["via"]("10.10.10.8")
    assert on33.put(f"{URL}/ens35", json={"services": ["console", "monitor"]}).status_code == 200
    r = on33.put(f"{URL}/ens33", json={"services": ["monitor", "interserver"]})
    assert r.status_code == 409 and r.json()["code"] == "needs_handoff"
    assert r.json()["go"] == ["https://10.20.0.8:8081"]
    r = on33.put(f"{URL}/ens33", json={"services": ["monitor", "interserver"], "handoff": True})
    assert r.status_code == 200, r.text
    assert r.json()["pending"]["seconds_left"] == 60 and r.json()["go"] == ["https://10.20.0.8:8081"]
    on35 = rig["via"]("10.20.0.8")
    assert on35.get(URL).json()["pending"]["interface"] == "ens33"
    assert on35.post(f"{URL}/confirm").json()["ok"] is True
    assert on35.get(URL).json()["pending"] is None
    assert on35.post(f"{URL}/confirm").status_code == 409


def test_unknown_service_and_nic(rig):
    client = rig["admin"]
    assert client.put(f"{URL}/ens35", json={"services": ["ssh"]}).status_code == 400
    assert client.put(f"{URL}/eth9", json={"services": []}).status_code == 404
    assert client.put(f"{URL}/ens35", json={"services": "console"}).status_code == 400


def test_failed_apply_is_502_and_the_db_is_back(rig):
    rig["nic"].hooks["dnsmasq"] = lambda n: "systemctl restart dnsmasq נכשל"
    r = rig["via"]("10.10.10.8").put(f"{URL}/ens35", json={"services": ["console"]})
    assert r.status_code == 502 and r.json()["code"] == "apply_failed"
    assert "dnsmasq" in r.json()["detail"]
    assert rig["nic"].edited() is False


def test_monitor_websocket_is_gated_only_after_an_edit(rig):
    """המוניטור עובר ב-8081 — השער נאכף לפי הכתובת המקומית של החיבור, לפני
    שהשרת פותח TCP למכונה (כאן: לפני בדיקת ה-MAC). ‏TestClient מדווח
    ‏`server=testserver` ב-WebSocket — כתובת שאינה של אף כרטיס, ולכן אחרי
    עריכה היא נחסמת (עיקרון 5: "לא ידוע" אינו "מותר"); לפני עריכה — היום."""
    from starlette.websockets import WebSocketDisconnect

    from server.console import auth
    client = rig["via"]("10.10.10.8")

    def close_code():
        cookie = f"{auth.COOKIE_NAME}={client.cookies.get(auth.COOKIE_NAME)}"
        with client.websocket_connect("/api/console/monitor/not-a-mac",
                                      headers={"cookie": cookie}) as sock:
            with pytest.raises(WebSocketDisconnect) as caught:
                sock.receive_bytes()
        return caught.value.code, caught.value.reason
    assert close_code()[0] == 4404, "not edited: the monitor behaves exactly as today"
    assert client.put(f"{URL}/ens35", json={"services": ["console"]}).status_code == 200
    code, reason = close_code()
    assert code == 4403 and "לא ניתן לקבוע דרך איזה כרטיס" in reason
