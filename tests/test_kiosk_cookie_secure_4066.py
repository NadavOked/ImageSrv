"""#4066: עוגיית הקיוסק במאזין הסוכן (:7712) נושאת ``Secure`` כשהחיבור הוא HTTPS.

מאזין הסוכן מוגש ב-HTTPS (``server/main.py``, זהות ה-TLS של ``server/agent/tls.py``), אבל
``create_kiosk_router`` בונה את ראוטר הקונסולה בלי ``tls`` (ה-``tls`` הוא של הקונסולה, :443,
בתעודה אחרת), ו-``auth.cookie_flags`` קבע ``secure = tls is not None``. לכן העוגייה יצאה בלי
``Secure``. התיקון: הכניסה של הקיוסק קובעת ``Secure`` לפי הסכמה של החיבור שהבקשה באה בו.

מה נבדק, ומה לא:

* HTTPS (לקוח ``https://``, וגם שקע אמיתי מול ``uvicorn`` עם זהות ה-TLS של הסוכן): ``Secure``,
  ``HttpOnly``, ``SameSite=lax`` — והעוגייה חוזרת ופותחת את נתיבי מחשב הבנייה.
* HTTP (פיתוח, loopback, המעטפת המשולבת): **בלי** ``Secure``, והכניסה והשימוש בעוגייה עובדים.
* ה-``tls`` של הקונסולה אינו מועבר לראוטר הקיוסק: ``/me`` (שמדווח את טביעת האצבע) אינו מוגש בפורט
  הסוכן בכלל, ועוגיית הקונסולה (``tls`` שלה) לא השתנתה (``test_console_tls.py``).

בקרה שלילית: ראו גוף ה-PR — כל קובצי הייצור שהתיקון נגע בהם הוחזרו, והטסטים כאן נכשלו.
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.app import create_agent_app
from server.console import auth

LOGIN = {"username": "labtech", "password": "deploy-pass-1"}   # deploy: `conftest._build_server`


def _attrs(set_cookie: str) -> dict[str, str]:
    """``Name=value; HttpOnly; SameSite=lax`` -> שמות התכונות באותיות קטנות."""
    parts = [p.strip() for p in set_cookie.split(";")][1:]
    return {p.partition("=")[0].lower(): p.partition("=")[2] for p in parts}


def _agent(server, scheme: str) -> TestClient:
    return TestClient(create_agent_app(server["app"].state.runtime), base_url=f"{scheme}://testserver")


def test_kiosk_cookie_is_secure_over_https(server):
    client = _agent(server, "https")
    login = client.post("/api/console/login", json=LOGIN)
    assert login.status_code == 200, login.text
    attrs = _attrs(login.headers["set-cookie"])
    assert "secure" in attrs, login.headers["set-cookie"]
    assert "httponly" in attrs
    assert attrs["samesite"].lower() == "lax"
    # העוגייה חוזרת (httpx שולח Secure רק על https) ופותחת את נתיבי מחשב הבנייה.
    assert client.cookies.get(auth.COOKIE_NAME)
    assert client.get("/api/console/images").status_code == 200
    assert client.get("/api/console/folders").status_code == 200


def test_kiosk_cookie_is_not_secure_over_plain_http(server):
    """פיתוח/loopback ב-HTTP: עוגיית ``Secure`` על http נזרקת והכניסה לא הייתה עובדת."""
    client = _agent(server, "http")
    login = client.post("/api/console/login", json=LOGIN)
    assert login.status_code == 200, login.text
    attrs = _attrs(login.headers["set-cookie"])
    assert "secure" not in attrs, login.headers["set-cookie"]
    assert "httponly" in attrs and attrs["samesite"].lower() == "lax"
    assert client.get("/api/console/images").status_code == 200
    assert client.put("/api/console/me/theme", json={"theme": "dark"}).status_code == 200


def test_the_agent_port_does_not_serve_me(server):
    """ה-``tls`` של הקונסולה אינו נכנס לראוטר הקיוסק, ו-``/me`` (``tls.public_info``) אינו בו."""
    client = _agent(server, "https")
    assert client.post("/api/console/login", json=LOGIN).status_code == 200
    assert client.get("/api/console/me").status_code == 404


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _serve(app, **tls):
    import uvicorn

    from server.main import agent_uvicorn_config

    port = _free_port()
    srv = uvicorn.Server(agent_uvicorn_config(app, "127.0.0.1", port, **tls))
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not srv.started:
        assert time.monotonic() < deadline and thread.is_alive()
        time.sleep(0.02)
    return srv, thread, port


def _login_on_socket(port: int, context: ssl.SSLContext | None) -> http.client.HTTPResponse:
    cls = http.client.HTTPSConnection if context else http.client.HTTPConnection
    kwargs = {"context": context} if context else {}
    conn = cls("127.0.0.1", port, timeout=5, **kwargs)
    try:
        conn.request("POST", "/api/console/login", json.dumps(LOGIN), {"Content-Type": "application/json"})
        response = conn.getresponse()
        response.read()
        return response
    finally:
        conn.close()


def test_real_agent_listener_sets_secure_on_https_and_not_on_http(server, tmp_path):
    """השקע האמיתי: אותה אפליקציית סוכן, מאזין HTTPS עם זהות הסוכן, ומאזין HTTP."""
    from server.agent import tls as agent_tls

    identity, _ = agent_tls.ensure_identity(tmp_path)
    app = create_agent_app(server["app"].state.runtime)
    context = ssl.create_default_context(cafile=str(identity.ca_path))
    context.check_hostname = False        # הכתובת כאן 127.0.0.1; אימות השם נבדק ב-test_agent_tls_3580
    started = []
    try:
        https = _serve(app, **identity.uvicorn_kwargs())
        started.append(https)
        plain = _serve(app)
        started.append(plain)
        secured = _login_on_socket(https[2], context)
        unsecured = _login_on_socket(plain[2], None)
    finally:
        for srv, thread, _port in started:
            srv.should_exit = True
            thread.join(5)
    assert secured.status == 200 and unsecured.status == 200
    secure_attrs = _attrs(secured.getheader("set-cookie"))
    assert {"secure", "httponly"} <= set(secure_attrs) and secure_attrs["samesite"].lower() == "lax"
    assert "secure" not in _attrs(unsecured.getheader("set-cookie"))
