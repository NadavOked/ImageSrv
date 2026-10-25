"""‏#1179 שלב 2: כתיבה מהראשי על המשני — רשימה מותרת, בערוץ 8443.

אותם שני שרתים אמיתיים של ``test_secondary_console_relay``. הראשי מעביר
``POST/PUT/DELETE /api/console/storage-nodes/{id}/console/<נתיב>`` רק כשהזוג
``(שיטה, נתיב)`` ב-``CONSOLE_WRITES``; המשני מריץ אותו בתוך התהליך בזהות
``primary:<user>``. מוכח: (א) הפעולה קורית **במשני** ולא בראשי; (ב) רשומת
יומן בשני הצדדים — ``peer_write`` בראשי (מי בראשי, איזה שרת, מה יצא) ובמשני
(‏``primary:<user>``); (ג) נתיב מחוץ לרשימה, גוף גדול מהתקרה וערך שעבר
``defang`` — נדחים **בלי לשאול**; (ד) לא נשלחה / לא ידוע אם בוצעה — שני
מצבים גלויים (502); (ה) 401 של המשני אינו עובר כ-401.
"""

from __future__ import annotations

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import interserver_api, interserver_auth, storage_client, storage_nodes, users
from test_secondary_console_relay import _parent, secondary              # noqa: F401
from test_storage_transfer import paired                                  # noqa: F401

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

LOOKALIKE = "‹img›"   # מה ש-defang כותב במקום <img>


def _write(server, nid, method, path, body=None, raw=None):
    url = f"/api/console/storage-nodes/{nid}/console{path}"
    if raw is not None:
        return server["admin"].request(method, url, content=raw,
                                       headers={"Content-Type": "application/json"})
    return server["admin"].request(method, url, json=body)


def _journal(conn, event="peer_write"):
    return [(r["user"], r["detail"]) for r in conn.execute(
        "SELECT user, detail FROM journal WHERE event = ? ORDER BY id", (event,))]


def _untouched(server, paired):
    return storage_nodes.contact_fields(server["ctx"].conn, paired)


# --- משתמשים ------------------------------------------------------------------

def test_a_user_is_created_on_the_secondary_and_journaled_on_both_sides(server, secondary,
                                                                        paired):
    resp = _write(server, paired, "POST", "/users",
                  {"username": "haifa-op", "password": "Haifa!pass#12345", "role": "deploy"})
    assert resp.status_code == 200, resp.text
    assert users.lookup(secondary["conn"], "haifa-op") is not None
    assert all(u["username"] != "haifa-op" for u in users.list_users(server["ctx"].conn)), \
        "הראשי לא נגע במשתמשים שלו"
    primary_rows = _journal(server["ctx"].conn)
    assert primary_rows and primary_rows[-1][0] == "noc"
    assert "HAIFA" in primary_rows[-1][1] and "POST /users → 200" in primary_rows[-1][1]
    assert _journal(secondary["conn"])[-1] == ("primary:noc", "POST /users → 200")
    # הנתיב עצמו רושם בשם מי שביצע — ‏primary:noc, לא משתמש מקומי של המשני
    assert any(u == "primary:noc" for u, _ in secondary["conn"].execute(
        "SELECT user, detail FROM journal WHERE event != 'peer_write'").fetchall())


def test_a_user_is_edited_and_deleted_on_the_secondary(server, secondary, paired):
    users.create(secondary["conn"], "temp", "Temp-pass-12345", "deploy", by="test",
                 check_policy=False)
    assert _write(server, paired, "PUT", "/users/temp", {"role": "admin"}).status_code == 200
    assert users.lookup(secondary["conn"], "temp") is not None
    assert next(u for u in users.list_users(secondary["conn"]) if u["username"] == "temp")["role"] == "admin"
    assert _write(server, paired, "DELETE", "/users/temp").status_code == 200
    assert all(u["username"] != "temp" for u in users.list_users(secondary["conn"]))
    assert [d for _, d in _journal(secondary["conn"])] == ["PUT /users/temp → 200",
                                                          "DELETE /users/temp → 200"]


def test_a_refusal_of_the_secondary_reaches_the_page_with_its_detail(server, secondary, paired):
    resp = _write(server, paired, "DELETE", "/users/nobody")
    assert resp.status_code == 404 and resp.json()["detail"]
    assert _journal(secondary["conn"])[-1] == ("primary:noc", "DELETE /users/nobody → 404")


# --- הגנות: כל אחת נדחית לפני שהמשני נשאל ---------------------------------------

@pytest.mark.parametrize("method,path", [
    ("POST", "/users/haifa/reset-password"), ("POST", "/images/img_1/delete"),
    ("PUT", "/ports/interserver"), ("DELETE", "/machines/aa:bb"), ("POST", "/groups"),
    ("PUT", "/users/%2e%2e"), ("POST", "/users/%2e%2e/settings"), ("POST", "/me/password"),
    ("POST", "/room/answer"), ("POST", "/tls"), ("POST", "/hostname"), ("POST", "/machines/aa:bb/reboot"),
])
def test_a_write_outside_the_allowlist_is_404_without_asking(server, secondary, paired,
                                                             method, path):
    before = _untouched(server, paired)
    assert _write(server, paired, method, path, {}).status_code == 404
    assert _untouched(server, paired) == before
    assert _journal(secondary["conn"]) == []


def test_writes_are_admin_only(server, paired):
    resp = server["deploy"].post(f"/api/console/storage-nodes/{paired}/console/users", json={})
    assert resp.status_code == 403


@pytest.mark.parametrize("path", ["/room", "/room/close"])
def test_secondary_round_control_is_admin_only_on_a_primary(server, secondary, paired, path):
    """הנתיבים החדשים אינם משנים את שער הכניסה: deploy=403, משני=409,
    ובשני המצבים המשני המרוחק אינו נשאל."""
    from server import db                                      # noqa: PLC0415
    url = f"/api/console/storage-nodes/{paired}/console{path}"
    before = _untouched(server, paired)
    assert server["deploy"].post(url, json={}).status_code == 403
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    assert server["admin"].post(url, json={}).status_code == 409
    assert _untouched(server, paired) == before
    assert _journal(secondary["conn"]) == []


def test_writes_go_only_from_a_primary(server, secondary, paired):
    """שרת שאינו standalone אינו מנהל ילדים — 409, והמשני לא נשאל (ההיררכיה חד-כיוונית)."""
    from server import db                                      # noqa: PLC0415
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    before = _untouched(server, paired)
    assert _write(server, paired, "POST", "/users", {"username": "x"}).status_code == 409
    assert _untouched(server, paired) == before
    assert _journal(secondary["conn"]) == []


def test_a_body_over_the_cap_is_refused_without_asking(server, secondary, paired):
    before = _untouched(server, paired)
    big = b'{"username": "' + b"x" * (interserver_api.MAX_CONSOLE_WRITE_BODY + 1) + b'"}'
    assert _write(server, paired, "POST", "/users", raw=big).status_code == 413
    assert _untouched(server, paired) == before


def test_a_value_that_went_through_defang_is_never_written_back(server, secondary, paired):
    """הטופס התמלא מתשובה שעברה ``defang``; שמירה כפי שהיא הייתה כותבת למשני ‹
    במקום < בלי שהמפעיל נגע בשדה. הראשי מסרב בגלוי (409), והמשני לא נשאל."""
    before = _untouched(server, paired)
    resp = _write(server, paired, "PUT", "/users/haifa", {"role": "admin", "note": LOOKALIKE})
    assert resp.status_code == 409 and "הוחלף בתצוגה" in resp.json()["detail"]
    assert _untouched(server, paired) == before
    assert _journal(secondary["conn"]) == []


def test_a_secondary_that_does_not_answer_is_502_not_sent_and_journaled(server, secondary,
                                                                       paired):
    secondary["server"].stop()
    resp = _write(server, paired, "POST", "/users", {"username": "x", "password": "y", "role": "admin"})
    assert resp.status_code == 502 and "לא נשלחה" in resp.json()["detail"]
    assert "לא נשלחה" in _journal(server["ctx"].conn)[-1][1]


def test_a_drop_after_sending_is_502_unknown_not_failed(server, secondary, paired, monkeypatch):
    def drop(self, *a, **k):
        raise storage_client.InterserverClientError("החיבור נסגר לפני סוף הכותרות")
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "send_answer", drop)
    resp = _write(server, paired, "POST", "/users", {"username": "x", "password": "y", "role": "admin"})
    assert resp.status_code == 502 and "לא ידוע אם הפעולה בוצעה" in resp.json()["detail"]
    assert "לא ידוע" in _journal(server["ctx"].conn)[-1][1]


def _econnreset(*_a, **_k):
    from OpenSSL import SSL                                       # noqa: PLC0415
    raise SSL.SysCallError(104, "ECONNRESET")


def test_a_reset_while_the_request_is_still_being_written_is_not_sent(server, secondary,
                                                                     paired, monkeypatch):
    # ‏ECONNRESET בתוך ה-sendall: הבקשה לא נכתבה במלואה, והמשני אינו מריץ בקשה
    # בלי כותרות שלמות או בלי הגוף המוצהר (#1122) — "לא נשלחה", לא "לא ידוע".
    # ‏#1286: הלקוח כותב ב-``send`` (לא-חוסם, דרך select) ולא ב-``sendall``.
    from OpenSSL import SSL                                       # noqa: PLC0415
    monkeypatch.setattr(SSL.Connection, "send", _econnreset)
    resp = _write(server, paired, "POST", "/users", {"username": "x", "password": "y", "role": "admin"})
    assert resp.status_code == 502 and "לא נשלחה" in resp.json()["detail"], resp.json()
    assert "ECONNRESET" in resp.json()["detail"]
    assert "לא נשלחה" in _journal(server["ctx"].conn)[-1][1]
    assert users.lookup(secondary["conn"], "x") is None, "המשני הריץ בקשה שלא נשלחה"


def test_a_reset_after_the_request_was_fully_written_is_unknown(server, secondary, paired,
                                                               monkeypatch):
    # אותו ECONNRESET — אבל אחרי שה-sendall הושלם: אולי המשני כבר ביצע.
    monkeypatch.setattr(storage_client, "_read_http_response", _econnreset)
    resp = _write(server, paired, "POST", "/users", {"username": "x", "password": "y", "role": "admin"})
    assert resp.status_code == 502 and "לא ידוע אם הפעולה בוצעה" in resp.json()["detail"]
    assert "לא ידוע" in _journal(server["ctx"].conn)[-1][1]


def test_a_stopped_interserver_listener_stops_accepting(secondary):
    # ‏close() לבדו אינו מעיר accept() חסום בלינוקס: המאזין המשיך לקבל חיבור
    # אחד, השלים TLS וסגר בלי לקרוא — הראשי ראה ECONNRESET אחרי השליחה.
    srv = secondary["server"]
    srv.stop()
    srv._thread.join(timeout=5)
    assert not srv._thread.is_alive(), "לולאת ה-accept עדיין חיה אחרי stop()"


def test_a_401_of_the_secondary_never_reaches_the_browser_as_401(server, secondary, paired,
                                                                monkeypatch):
    monkeypatch.setattr(interserver_api, "run_console_write",
                        lambda app, m, p, q, u, b: (401, {}, {"detail": "לא מחובר"}))
    resp = _write(server, paired, "POST", "/users", {"username": "x"})
    assert resp.status_code == 502


# --- שכבת ה-service במשני -----------------------------------------------------------

def test_the_secondary_checks_token_allowlist_user_and_cap_before_running(server, secondary,
                                                                         paired, monkeypatch):
    ran = []
    monkeypatch.setattr(interserver_api, "_run_console",
                        lambda *a: ran.append(a) or (200, {}, {"ok": True}))
    cred = _parent(server, paired)
    call = dict(app=secondary["app"], peer=cred["peer"], query="", protocol_version="2.1")

    def write(**kw):
        args = {**call, "method": "POST", "path": "/users", "acting_user": "noc",
                "body": b"{}", "token": cred["token"], **kw}
        with pytest.raises(interserver_api.PairError) as e:
            interserver_api.console_write(secondary["ctx"], args.pop("app"), args.pop("peer"), **args)
        return e.value.status

    assert write(token="0" * 64) == 401
    assert write(method="POST", path="/images/x/delete") == 404
    assert write(method="GET") == 404
    assert write(path="/users/%2e%2e", method="PUT") == 404
    assert write(acting_user="noc admin") == 400
    assert write(body=b"x" * (interserver_api.MAX_CONSOLE_WRITE_BODY + 1)) == 413
    assert ran == [], "שום כתיבה לא רצה"
    out = interserver_api.console_write(secondary["ctx"], secondary["app"], cred["peer"],
                                        method="POST", path="/users", query="", acting_user="noc",
                                        body=b"{}", token=cred["token"], protocol_version="2.1")
    assert out["status"] == 200 and ran[0][1:3] == ("POST", "/users")
    assert ran[0][4] == "primary:noc"
    # גם כשהנתיב עצמו אינו כותב יומן — המשני רושם peer_write
    assert _journal(secondary["conn"])[-1] == ("primary:noc", "POST /users → 200")


def test_run_console_write_rechecks_the_allowlist_and_the_cap():
    assert interserver_api.run_console_write(object(), "POST", "/images/x/delete", "", "p", b"")[0] == 404
    big = b"x" * (interserver_api.MAX_CONSOLE_WRITE_BODY + 1)
    assert interserver_api.run_console_write(object(), "POST", "/users", "", "p", big)[0] == 413


@pytest.mark.parametrize(("method", "path", "allowed"), [
    ("POST", "/room", True), ("POST", "/room/close", True),
    ("GET", "/room", False), ("PUT", "/room", False),
    ("POST", "/room/start", False), ("POST", "/room/wake", False),
    ("POST", "/room/answer", False), ("POST", "/room/poweroff", False),
    ("POST", "/room/close/extra", False), ("POST", "/monitor/aa:bb/power", False),
])
def test_only_open_and_stop_are_added_to_the_secondary_room_write_allowlist(method, path, allowed):
    assert interserver_api.console_write_allowed(method, path) is allowed


# --- הגדרות -----------------------------------------------------------------------

def test_a_setting_is_written_on_the_secondary_not_the_primary(server, secondary, paired):
    from server.db import get_setting                          # noqa: PLC0415
    # נדב 17/10: שם השרת הוא שם המארח ואינו הגדרה — הטסט כותב הגדרה רגילה.
    resp = _write(server, paired, "POST", "/settings", {"session_wait_seconds": "420"})
    assert resp.status_code == 200, resp.text
    assert get_setting(secondary["conn"], "session_wait_seconds") == "420"
    assert get_setting(server["ctx"].conn, "session_wait_seconds") != "420"
    assert ("primary:noc", "session_wait_seconds=420") in _journal(secondary["conn"], "setting_change")
    assert _journal(secondary["conn"])[-1] == ("primary:noc", "POST /settings → 200")
    assert "POST /settings → 200" in _journal(server["ctx"].conn)[-1][1]


def test_an_unknown_setting_is_refused_by_the_secondary_itself(server, secondary, paired):
    resp = _write(server, paired, "POST", "/settings", {"interserver_token": "x"})
    assert resp.status_code == 400 and "לא מוכרת" in resp.json()["detail"]


# --- רשת ------------------------------------------------------------------------

def test_a_nic_is_added_described_and_forgotten_on_the_secondary(server, secondary, paired):
    from server.db import get_setting                          # noqa: PLC0415
    assert _write(server, paired, "POST", "/net/interfaces",
                  {"name": "eth9", "description": "וילן 30"}).status_code == 200
    assert get_setting(secondary["conn"], "nicdesc:eth9") == "וילן 30"
    assert get_setting(server["ctx"].conn, "nicdesc:eth9") is None
    assert _write(server, paired, "PUT", "/net/interfaces/eth9/description",
                  {"description": "וילן 31"}).status_code == 200
    assert get_setting(secondary["conn"], "nicdesc:eth9") == "וילן 31"
    assert _write(server, paired, "DELETE", "/net/interfaces/eth9").status_code == 200
    assert get_setting(secondary["conn"], "nicdesc:eth9") is None
    assert [d for _, d in _journal(secondary["conn"])] == [
        "POST /net/interfaces → 200", "PUT /net/interfaces/eth9/description → 200",
        "DELETE /net/interfaces/eth9 → 200"]


@pytest.mark.parametrize("method,path,allowed", [
    ("POST", "/net/config/eth0/preview", True), ("PUT", "/net/config/eth0", True),
    ("PUT", "/net/config/eth0/deploy", True), ("PUT", "/net/config/eth0/second", True),
    ("POST", "/net/config/confirm", True), ("PUT", "/net/interfaces/eth0", True),
    ("PUT", "/ports/interserver", False), ("PUT", "/ports/tftp", False),
    ("POST", "/net/interfaces/eth0/probe", False), ("DELETE", "/net/config/eth0", False),
])
def test_the_network_write_allowlist(method, path, allowed):
    assert interserver_api.console_write_allowed(method, path) is allowed
