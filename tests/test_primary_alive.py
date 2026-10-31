"""‏#1310: "אתה שם?" — מה שהראשי עונה על 8443, ורק זה.

הכרעת נדב 12/10 (אפשרות א'): מתקין המשני שואל את הראשי אם הוא חי, כדי
ש"בדוק חיבור" יוכל להיות ירוק. לפני #1310 הגשר שאל `https://<ראשי>:8443/
health/live` — נתיב שאינו קיים בשום מקום (`/health/live` חי רק תחת
`/api/console` על 8081, ובראשי 8443 לא האזין כלל), ולכן הבדיקה נכשלה
**תמיד**, גם בתצורה הנכונה.

מה שנבדק כאן:

* התשובה היא **בדיוק** `{name, version, fingerprint}` — לא `ok`, לא
  `version_error`, לא שום שדה של `/api/console/health/live`.
* שום נתיב אחר אינו נגיש על המאזין: לא `/docs`/`/openapi.json` של
  FastAPI, לא הקונסולה, לא הסוכן, לא נתיבי ה-interserver של המשני; ושיטה
  אחרת על הנתיב היחיד = 405.
* על TLS אמיתי: הטביעה בגוף = SHA-256 של ה-SPKI שה-handshake הציג = זהות
  הראשי הבין-שרתית (המפתח שהמשני יצמיד ב-pairing). ‏HTTP פשוט אינו נענה.
* ‏`main` מרים את המאזין על כרטיס הקונסולה של **ראשי** בלבד: לא במשני,
  ולא כשהקונסולה על loopback בלבד.
"""

from __future__ import annotations

import json
import socket
import ssl
import threading
import urllib.request
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from server import db
from server.interserver import interserver_auth, primary_alive, storage_nodes

SPKI = "ab" * 32


def _app(name="imagesrv-ta", version="v0.60.0", fingerprint=SPKI):
    return primary_alive.create_alive_app(name=lambda: name, version=lambda: version,
                                          fingerprint=fingerprint)


def test_alive_answers_exactly_name_version_fingerprint():
    with TestClient(_app()) as client:
        resp = client.get(primary_alive.ALIVE_PATH)
    assert resp.status_code == 200
    assert resp.json() == {"name": "imagesrv-ta", "version": "v0.60.0", "fingerprint": SPKI}


def test_a_version_that_was_not_read_is_null_not_an_empty_string():
    with TestClient(_app(version=None)) as client:
        body = client.get(primary_alive.ALIVE_PATH).json()
    assert set(body) == {"name", "version", "fingerprint"}
    assert body["version"] is None


@pytest.mark.parametrize("path", [
    "/", "/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect",
    "/health/live", "/api/console/health/live", "/api/console/me",
    "/api/console/login", "/api/console/health", "/api/console/ports",
    "/api/v1/agent/hello", "/boot/grub.cfg", "/api/interserver/v1/images",
    "/api/interserver/v1/check", "/api/interserver/v1/alive/x",
])
def test_nothing_else_is_reachable_on_the_listener(path):
    with TestClient(_app()) as client:
        assert client.get(path).status_code == 404, path


@pytest.mark.parametrize("method", ["post", "put", "delete", "patch"])
def test_the_one_path_is_read_only(method):
    with TestClient(_app()) as client:
        assert getattr(client, method)(primary_alive.ALIVE_PATH).status_code == 405


def test_the_app_carries_exactly_one_route():
    """הראיה הישירה ש"אין עוד": כל ה-routes של האפליקציה — אחד."""
    paths = [getattr(r, "path", None) for r in _app().routes]
    assert paths == [primary_alive.ALIVE_PATH]


# ---------- TLS אמיתי ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_primary(tmp_path):
    """המאזין כפי ש-main בונה אותו: זהות בין-שרתית אמיתית + uvicorn TLS."""
    uvicorn = pytest.importorskip("uvicorn")
    conn = db.connect(tmp_path / "p.db")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "data", host_sans=["127.0.0.1"])
    port = _free_port()
    config = primary_alive.server_config(_app(fingerprint=ident["server_spki"]), ident,
                                         "127.0.0.1", port)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        thread.join(0.05)
    assert server.started, "uvicorn לא עלה"
    yield {"port": port, "ident": ident}
    server.should_exit = True
    thread.join(5)


def test_fingerprint_is_the_key_the_tls_handshake_shows(live_primary):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", live_primary["port"]), timeout=5) as raw:
        with ctx.wrap_socket(raw) as tls:
            der = tls.getpeercert(binary_form=True)
            tls.sendall(f"GET {primary_alive.ALIVE_PATH} HTTP/1.1\r\nHost: x\r\n"
                        "Connection: close\r\n\r\n".encode())
            reply = b""
            while chunk := tls.recv(65536):
                reply += chunk
    head, _, body = reply.partition(b"\r\n\r\n")
    assert head.startswith(b"HTTP/1.1 200")
    got = json.loads(body)
    assert got["fingerprint"] == interserver_auth.spki_sha256(der)
    assert got["fingerprint"] == live_primary["ident"]["server_spki"]


def test_plain_http_gets_no_answer(live_primary):
    with pytest.raises(Exception):
        urllib.request.urlopen(
            f"http://127.0.0.1:{live_primary['port']}{primary_alive.ALIVE_PATH}", timeout=5)


# ---------- main: ראשי בלבד, על כרטיס הקונסולה ----------

def _run_main(tmp_path: Path, monkeypatch, role: str, *argv: str) -> dict:
    """``main`` עם runtime אמיתי-מינימלי: ה-DB של התפקיד וה-data dir; כל
    השאר (אפליקציות, uvicorn, הלולאה) מנוטרל. מחזיר את ה-Config לכל app."""
    import asyncio

    import uvicorn

    import server.app
    import server.main

    data = tmp_path / "data"
    data.mkdir()
    conn = db.connect(data / "imagesrv.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, role)

    class Runtime:
        pass
    rt = Runtime()
    rt.conn = conn
    configs: dict = {}

    def fake_config(app, **kwargs):
        configs.setdefault(app, []).append(kwargs)
        return object()

    monkeypatch.setattr(server.app, "create_runtime", lambda *a, **k: rt)
    monkeypatch.setattr(server.app, "create_agent_app", lambda rt: "agent")
    monkeypatch.setattr(server.app, "create_console_app", lambda rt: "console")
    monkeypatch.setattr(server.app, "create_alive_app",
                        lambda rt, fp: configs.setdefault("fingerprint", fp) and "alive",
                        raising=False)
    monkeypatch.setattr(uvicorn, "Config", fake_config)
    monkeypatch.setattr(uvicorn, "Server", lambda config: object())
    monkeypatch.setattr(server.main, "serve_all", lambda servers: None)
    monkeypatch.setattr(asyncio, "run", lambda coro=None: None)
    monkeypatch.setattr(server.main, "_interface_for", lambda url: None)
    monkeypatch.setattr("sys.argv", [
        "server.main", "--server-url", "http://10.44.12.10:8080",
        "--data-dir", str(data), "--images", str(tmp_path / "img"), *argv])
    server.main.main()
    configs["conn"] = conn
    return configs


def test_main_puts_the_alive_listener_on_the_primary_console_card(tmp_path, monkeypatch):
    configs = _run_main(tmp_path, monkeypatch, "standalone", "--console-host", "10.10.10.8")
    alive = configs["alive"]
    assert [(c["host"], c["port"]) for c in alive] == [("10.10.10.8", 8443)], \
        "רק על כרטיס השרתים — לא על loopback ולא על כרטיס ההפצה"
    ident = storage_nodes.identity(configs["conn"])
    assert configs["fingerprint"] == ident["server_spki"]
    assert alive[0]["ssl_certfile"] == ident["server_cert_ref"]
    assert alive[0]["ssl_keyfile"] == ident["server_key_ref"]


def test_a_secondary_never_opens_the_alive_listener(tmp_path, monkeypatch):
    configs = _run_main(tmp_path, monkeypatch, "secondary", "--console-host", "10.20.10.8")
    assert "alive" not in configs and "fingerprint" not in configs


def test_console_on_loopback_only_means_no_alive_listener(tmp_path, monkeypatch):
    configs = _run_main(tmp_path, monkeypatch, "standalone")
    assert "alive" not in configs
