"""‏#1179: הקונסולה של המשני דרך הראשי — GET מרשימה מותרת, בערוץ 8443.

שני שרתים אמיתיים על loopback: הראשי (``server``) והמשני — אפליקציה מלאה
(‏``create_app``) עם המאזין הבין-שרתי ``console_app=app``, כמו ``main.py``.
הראשי מעביר ``GET /api/console/storage-nodes/{id}/console/<נתיב>``; המשני
מריץ את ה-GET בתוך התהליך בזהות ``primary:<user>`` ומחזיר מעטפת.

מה שמוכח: (א) מה שחוזר לראשי **זהה** למה שהקונסולה של המשני עצמה מחזירה,
כולל הכותרות שהעמודים קוראים; (ב) רק GET מהרשימה — נתיב אחר 404 בלי לשאול;
(ג) לא ענה / SPKI שגוי / סירוב הערוץ = 502 עם הסיבה, ו-401 של המשני אינו
עובר כ-401 (היה שולח את הדפדפן לכניסה לראשי); (ד) טיפוס עליון שגוי = 502;
(ה) משני עוין אינו יכול להעביר תחביר HTML — אף מחרוזת אינה נושאת ``<>"'`&``;
(ו) מפתח הזהות ב-scope אינו ניתן לזיוף מבקשת HTTP.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db
from server.interserver import interserver_api, interserver_auth, peer_safe, storage_nodes
from test_storage_transfer import _peer, paired                # noqa: F401 — fixture

try:
    from fastapi.testclient import TestClient
except Exception:                                             # noqa: BLE001
    TestClient = None


pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

ROOT = Path(__file__).resolve().parents[1]
EVIL = "<img src=x onerror=alert(1)>"
NIC = {"name": "eth0", "state": "up", "mac": "aa:bb:cc:00:30:08",
       "addresses": ["10.30.0.8/24"], "speed_mbps": 1000}


@pytest.fixture()
def secondary(tmp_path, clock):
    """משני מלא, עם hooks מזויפים לכל מה שנוגע במכונה (hostguard, #113)."""
    if TestClient is None:
        pytest.skip("fastapi is required")
    from conftest import complete_console_login                # noqa: PLC0415
    from server.console import users                                   # noqa: PLC0415
    from server.app import create_app                          # noqa: PLC0415
    from test_sender import Recorder                           # noqa: PLC0415
    from test_server_health import SS_DNSMASQ                  # noqa: PLC0415

    root = tmp_path / "sec_images"
    root.mkdir()
    tftp = tmp_path / "sec_tftp"
    (tftp / "grub").mkdir(parents=True)
    health_hooks = {
        "ss": lambda: SS_DNSMASQ, "ss_tcp": lambda: "", "installer_conf": lambda: "",
        "nft_ruleset": lambda: "table inet imagesrv {}",
        "unit_active": lambda name: "active", "http_get": lambda url: 200,
        "http_size": lambda url: (200, 9_000_000), "http_text": lambda url: (200, ""),
        "interfaces": lambda: [NIC], "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: [], "uptime": lambda: 600}
    app = create_app(tmp_path / "sec", root, "http://10.30.0.8:8080",
                     now_fn=clock, sender_runner=Recorder(block=True),
                     health_hooks=health_hooks,
                     dhcp_hooks={"interfaces": lambda: [NIC], "service_active": lambda unit: True,
                                 "read_active_conf": lambda: "interface=eth0"})
    rt = app.state.runtime
    conn = rt.ctx.conn
    users.create(conn, "haifa", "admin-pass-123", "admin", by="test", check_policy=False)
    admin = TestClient(app)
    complete_console_login(admin, conn, "haifa", "admin-pass-123")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec", host_sans=["127.0.0.1"])
    server = interserver_api.InterserverTLSServer(
        rt.ctx, "127.0.0.1", 0, ident["cert_pem"], ident["key_pem"],
        data_dir=str(tmp_path / "sec"), console_app=app).start()
    yield {"conn": conn, "ctx": rt.ctx, "ident": ident, "server": server, "app": app,
           "port": server.port, "root": root, "admin": admin}
    server.stop()
    rt.ctx.sender.stop()


def _stable(body):
    """שדות שזזים בין שתי קריאות — לא חלק מההשוואה.

    ``free_bytes`` ב-``/overview`` הוא המקום הפנוי בדיסק.
    ``now`` הוא שעון השרת ברגע הקריאה (``now_iso()``, #1301) — ``/overview``
    ו-``/update/status``. שתי הקריאות נופלות לפעמים משני צידי גבול שנייה (#1783).
    """
    if isinstance(body, dict):
        if "now" in body:
            body = {k: v for k, v in body.items() if k != "now"}
        storage = body.get("storage")
        if isinstance(storage, dict):
            body = {**body, "storage": {k: v for k, v in storage.items()
                                        if k != "free_bytes"}}
    return body


def _status_clock(monkeypatch):
    """שעון מוזרק ל-``GET /update/status``: קבוע בתוך קריאה, קופץ שנייה בין הקריאות.

    הראיה שההזרקה פגעה בתשובה עצמה היא ש-``now`` שווה לחותמת שנקבעה — שעון
    אמיתי לא יחזיר אותה (#1783).
    """
    from datetime import datetime, timedelta, timezone
    from server import update as update_mod

    base = datetime(2026, 10, 19, 5, 57, 55, tzinfo=timezone.utc)
    first = base.isoformat(timespec="seconds")
    second = (base + timedelta(seconds=1)).isoformat(timespec="seconds")
    holder = {"now": first}
    monkeypatch.setattr(update_mod, "now_iso", lambda: holder["now"])
    return first, second, holder


def _get(server, nid, path):
    return server["admin"].get(f"/api/console/storage-nodes/{nid}/console{path}")


#: נתיבים שמוחזרים 200 גם במשני ריק — ההשוואה מול הקונסולה שלו.
PARITY = ("/overview", "/machines", "/groups", "/disk-failures", "/shrink-records", "/net",
          "/net/deploy", "/net/interfaces", "/room", "/room/history", "/room/driver-models",
          "/images", "/folders", "/tasks", "/health", "/ports", "/storage-locations",
          "/drivers", "/monitor/machines", "/monitor/settings", "/users", "/journal",
          "/journal/events", "/settings", "/update", "/update/status",
          "/tls", "/hostname", "/sessions")


@pytest.mark.parametrize("path", PARITY)
def test_a_relayed_get_is_what_the_secondarys_own_console_answers(server, secondary, paired,
                                                                 path, monkeypatch):
    # ‏#1783: בלי הקפיצה הטסט ירוק גם כשההשוואה עדיין כוללת את ``now``.
    clock = None
    if path == "/update/status":
        first, second, clock = _status_clock(monkeypatch)
    own = secondary["admin"].get("/api/console" + path)
    assert own.status_code == 200, own.text
    if clock is not None:
        assert own.json().get("now") == first, own.text
        clock["now"] = second
    relayed = _get(server, paired, path)
    assert relayed.status_code == 200, relayed.text
    if clock is not None:
        assert relayed.json().get("now") == second, relayed.text
    assert _stable(relayed.json()) == _stable(peer_safe.defang(own.json()))
    assert isinstance(relayed.json(), interserver_api.console_get_type(path))
    for name in interserver_api.CONSOLE_HEADERS:
        if name == "x-health-checked-at":
            # שעת המדידה — שתי מדידות, ייתכן בשנייה אחרת; מה שנבדק: עברה כשהייתה.
            assert bool(relayed.headers.get(name)) == bool(own.headers.get(name)), name
        else:
            assert relayed.headers.get(name) == own.headers.get(name), name


def test_the_secondarys_data_is_not_the_primarys(server, secondary, paired):
    from server.agent import registry                                # noqa: PLC0415
    registry.add_machine(secondary["conn"], "aa:bb:cc:dd:ee:31", "H31", "grp_BUILD", "test")
    macs = [m["mac"] for m in _get(server, paired, "/machines").json()]
    assert macs == ["aa:bb:cc:dd:ee:31"]
    assert server["admin"].get("/api/console/machines").json() == []


def test_query_strings_reach_the_secondary(server, secondary, paired):
    db.journal(secondary["conn"], "test_event", "שורה בחיפה", "haifa")
    rows = _get(server, paired, "/journal?limit=1").json()
    assert len(rows) == 1


@pytest.mark.parametrize("path", ("/me", "/journal.csv", "/machines.csv",
                                  "/net/interfaces/eth0/probe", "/update/backup",
                                  "/images/x/download", "/../users", "/login"))
def test_only_allowlisted_gets_are_relayed(server, secondary, paired, path):
    before = storage_nodes.contact_fields(server["ctx"].conn, paired)
    assert _get(server, paired, path).status_code == 404
    assert storage_nodes.contact_fields(server["ctx"].conn, paired) == before, "המשני לא נשאל"


#: נתיב שמתאים לרשימה כמחרוזת גולמית והופך לנתיב אחר אחרי פענוח (ביקורת אבטחה).
ENCODED = ("/images/%2e%2e%2fusers", "/images/%2e%2e", "/images/a%2fb", "/images/%252e%252e",
           "/images/a%5cb", "/images/a\\b", "/storage-locations/%2E%2E%2Fsettings",
           "/images/.", "/images/..")


@pytest.mark.parametrize("path", ENCODED)
def test_an_encoded_or_traversal_path_is_refused_before_the_console_runs(path, monkeypatch):
    """שכבת ה-service במשני: לא ``console_get_type`` ולא ``run_console_get``
    מקבלים נתיב שאינו זהה לעצמו אחרי פענוח — הקונסולה אינה נקראת בכלל."""
    assert interserver_api.console_get_type(path) is None
    called = []
    monkeypatch.setattr("asyncio.run", lambda coro: called.append(coro.close()))
    status, _, _ = interserver_api.run_console_get(object(), path, "", "primary:noc")
    assert status == 404 and called == []


@pytest.mark.parametrize("path", ENCODED[:7])
def test_an_encoded_path_from_the_browser_is_refused_without_asking(server, secondary,
                                                                   paired, path):
    before = storage_nodes.contact_fields(server["ctx"].conn, paired)
    resp = server["admin"].get(f"/api/console/storage-nodes/{paired}/console{path}")
    assert resp.status_code == 404, resp.text
    assert storage_nodes.contact_fields(server["ctx"].conn, paired) == before, "המשני לא נשאל"


def test_an_oversized_console_body_is_refused(monkeypatch):
    monkeypatch.setattr(interserver_api, "MAX_CONSOLE_BODY", 10)

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"[" + b"1," * 50 + b"1]"})

    status, _, body = interserver_api.run_console_get(app, "/users", "", "primary:noc")
    assert status == 500 and "גדולה" in body["detail"]


def test_a_write_outside_the_write_allowlist_is_404(server, paired):
    """‏#1179 שלב 2: כתיבה עוברת רק מ-``CONSOLE_WRITES`` (``test_secondary_console_writes``)."""
    for method in ("post", "put", "delete"):
        resp = getattr(server["admin"], method)(
            f"/api/console/storage-nodes/{paired}/console/journal")
        assert resp.status_code == 404, method


def test_route_guards(server, secondary, paired):
    assert server["deploy"].get(
        f"/api/console/storage-nodes/{paired}/console/users").status_code == 403
    assert _get(server, "nope", "/users").status_code == 404
    storage_nodes.set_node_disabled(server["ctx"].conn, paired, True, ("noc", "admin"))
    resp = _get(server, paired, "/users")
    assert resp.status_code == 409 and "מושבת" in resp.json()["detail"]
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    assert _get(server, paired, "/users").status_code == 409


def test_a_secondary_that_does_not_answer_is_502_with_the_reason(server, secondary, paired):
    secondary["server"].stop()
    resp = _get(server, paired, "/users")
    assert resp.status_code == 502 and "לא ענה" in resp.json()["detail"]
    assert storage_nodes.contact_fields(server["ctx"].conn, paired)["last_error"]


def test_a_wrong_pin_is_502_with_the_reason(server, secondary, paired):
    conn = server["ctx"].conn
    with db._write_lock, db.writing(conn):
        conn.execute("UPDATE storage_nodes SET pinned_spki = ? WHERE id = ?",
                     ("ab" * 32, paired))
    resp = _get(server, paired, "/users")
    assert resp.status_code == 502 and "SPKI" in resp.json()["detail"]


def _answers(monkeypatch, status, body, headers=None):
    """המשני (באותו תהליך) "מחזיר" תשובה אחרת — משני עוין או שבור."""
    monkeypatch.setattr(interserver_api, "run_console_get",
                        lambda app, path, query, user: (status, headers or {}, body))


def test_a_401_or_500_of_the_secondary_never_reaches_the_browser_as_is(server, secondary,
                                                                      paired, monkeypatch):
    _answers(monkeypatch, 401, {"detail": "לא מחובר"})
    resp = _get(server, paired, "/users")
    assert resp.status_code == 502 and "401" in resp.json()["detail"]
    _answers(monkeypatch, 500, {"detail": "boom"})
    assert _get(server, paired, "/users").status_code == 502


def test_a_404_of_the_secondary_passes_with_its_detail(server, secondary, paired):
    resp = _get(server, paired, "/images/img_nope")
    assert resp.status_code == 404 and resp.json()["detail"]


def test_a_top_level_type_that_is_not_the_routes_is_unexpected(server, secondary, paired,
                                                              monkeypatch):
    _answers(monkeypatch, 200, {"machines": []})
    resp = _get(server, paired, "/machines")
    assert resp.status_code == 502 and "תשובה לא צפויה" in resp.json()["detail"]


def test_a_hostile_answer_carries_no_html_syntax(server, secondary, paired, monkeypatch):
    """מחרוזת במקום מספר, ``{"length": …}`` במקום רשימה, HTML במפתח ובכותרת —
    שום מחרוזת שחוזרת לדפדפן אינה נושאת ``<>"'`&``."""
    _answers(monkeypatch, 200, [{"name": EVIL, "files": {"length": EVIL}, "images": EVIL,
                                 EVIL: [EVIL, 1, True, None], "n": 5}],
             {"x-health-checked-at": EVIL})
    resp = _get(server, paired, "/drivers")
    assert resp.status_code == 200
    strings = []

    def walk(v):
        if isinstance(v, str):
            strings.append(v)
        elif isinstance(v, list):
            [walk(x) for x in v]
        elif isinstance(v, dict):
            [walk(k) or walk(x) for k, x in v.items()]
    walk(resp.json())
    assert strings and not any(set(x) & set("<>\"'`&") for x in strings), strings
    body = resp.json()[0]
    assert body["n"] == 5 and body["files"]["length"] == peer_safe.defang(EVIL)
    assert body[peer_safe.defang(EVIL)][1:] == [1, True, None]
    assert "x-health-checked-at" not in resp.headers, "כותרת שאינה זמן/מספר אינה עוברת"


def test_defang_keeps_the_text_readable_and_the_types():
    assert peer_safe.defang({"a": ["x<y", 1, 2.5, False, None]}) == {"a": ["x‹y", 1, 2.5,
                                                                           False, None]}
    assert peer_safe.defang("חיפה — Office 365") == "חיפה — Office 365"


# --- שכבת ה-service במשני ------------------------------------------------------

def _parent(server, paired) -> dict:
    node = storage_nodes.node_row(server["ctx"].conn, paired)
    return {"token": interserver_auth.load_credential(node["credential_ref"]),
            "peer": _peer(storage_nodes.identity(server["ctx"].conn)["cert_pem"])}


def test_the_get_runs_as_primary_user_only_after_the_bound_token(server, secondary, paired,
                                                                monkeypatch):
    seen = []
    monkeypatch.setattr(interserver_api, "run_console_get",
                        lambda app, path, query, user: seen.append((path, user)) or (200, {}, []))
    cred = _parent(server, paired)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.console_get(secondary["ctx"], secondary["app"], cred["peer"],
                                    path="/users", query="", acting_user="noc",
                                    token="0" * 64, protocol_version="2.1")
    assert e.value.status == 401 and seen == []
    for bad in ("", "noc admin", "x" * 65):
        with pytest.raises(interserver_api.PairError) as e:
            interserver_api.console_get(secondary["ctx"], secondary["app"], cred["peer"],
                                        path="/users", query="", acting_user=bad,
                                        token=cred["token"], protocol_version="2.1")
        assert e.value.status == 400
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.console_get(secondary["ctx"], secondary["app"], cred["peer"],
                                    path="/me", query="", acting_user="noc",
                                    token=cred["token"], protocol_version="2.1")
    assert e.value.status == 404 and seen == []
    _get(server, paired, "/users")
    assert seen == [("/users", "primary:noc")]


def test_the_peer_identity_cannot_come_from_an_http_request(secondary):
    """הזהות יושבת ב-scope; כותרת או עוגייה בשם הזה אינן זהות."""
    anon = TestClient(secondary["app"])
    key = "imagesrv.peer_user"
    assert anon.get("/api/console/users", headers={key: "primary:x"}).status_code == 401
    anon.cookies.set(key, "primary:x")
    assert anon.get("/api/console/users").status_code == 401


def test_main_hands_the_console_app_to_the_interserver_listener():
    """‏main.py אינו נבדק ב-pytest; בלי זה כל GET דרך הערוץ עונה 503."""
    source = (ROOT / "server" / "main.py").read_text(encoding="utf-8")
    assert "console_app=console_app" in source
