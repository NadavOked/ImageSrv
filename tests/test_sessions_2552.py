"""#2552: מסך סשנים פעילים למנהל -- רשימה, ניתוק סשן בודד, ו"צא מכל המכשירים".

השרת כבר ידע לבטל עוגייה (``auth.revoke``, #1633) ולבטל את כל העוגיות של משתמש
(``bump_auth_epoch``). מה שנוסף: ``GET /sessions`` מחזיר מזהה קצר (``sid``, ששה
תווים מה-sha256), ו-``DELETE /sessions/{sid}`` מנתק לפיו -- העוגייה נדחית **מיד**.

הכלל שנבדק לאורך הקובץ: הטוקן והגיבוב המלא לא יוצאים מהשרת.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.console import auth, presence, users, viewer_sessions
from server.db import _write_lock, writing

LIST = "/api/console/sessions"
SID_LEN = 6            # חוזה המסך (המוקאפ): ששה תווים. קבוע כאן ולא נקרא מהקוד, כדי שהבקרה השלילית תיפול על התנהגות


def _login(app, name: str, password: str, monkeypatch, shift: int) -> TestClient:
    """כניסה נוספת לאותו משתמש. אין nonce בעוגייה, ושתי כניסות באותה שנייה מקבלות
    עוגייה זהה -- לכן כל כניסה מוזזת בשניות (כמו ב-test_network_api_1039)."""
    later = time.time() + shift
    monkeypatch.setattr(auth, "time", SimpleNamespace(time=lambda: later))
    client = TestClient(app)
    assert client.post("/api/console/login",
                       json={"username": name, "password": password}).status_code == 200
    return client


@pytest.fixture()
def net(server, monkeypatch):
    """noc (המחובר, מנהל) + dana עם שני דפדפנים + sivan (מנהל שלישי) + labtech (הפצה)."""
    conn = server["ctx"].conn
    for name in ("dana", "sivan"):
        users.create(conn, name, f"{name}-pass-123", "admin", by="test", check_policy=False)
    dana1 = _login(server["app"], "dana", "dana-pass-123", monkeypatch, 5)
    dana2 = _login(server["app"], "dana", "dana-pass-123", monkeypatch, 11)
    sivan = _login(server["app"], "sivan", "sivan-pass-123", monkeypatch, 17)
    for client in (server["admin"], dana1, dana2, sivan):
        assert client.get("/api/console/me").status_code == 200   # נרשם ב-console_sessions
    return {**server, "conn": conn, "dana1": dana1, "dana2": dana2, "sivan": sivan}


def _token(client) -> str:
    return client.cookies.get(auth.COOKIE_NAME)


def _sid(client) -> str:
    return hashlib.sha256(_token(client).encode()).hexdigest()[:SID_LEN]


def _listed(net) -> list[dict]:
    r = net["admin"].get(LIST)
    assert r.status_code == 200, r.text
    return r.json()


def _journal(conn, event: str) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT user, detail FROM journal WHERE event = ?", (event,))]


# --- הרשימה ---------------------------------------------------------------------


def test_list_has_a_short_id_a_role_and_marks_only_this_session(net):
    body = _listed(net)
    assert len(body) == 4
    for s in body:
        assert set(s) == {"sid", "user", "role", "ip", "since", "last_seen", "current", "monitor"}
        assert len(s["sid"]) == SID_LEN and set(s["sid"]) <= set("0123456789abcdef")
    assert sorted(s["sid"] for s in body) == sorted(
        _sid(net[k]) for k in ("admin", "dana1", "dana2", "sivan"))
    assert [s["user"] for s in body if s["current"]] == ["noc"]
    assert {s["user"]: s["role"] for s in body} == {"noc": "admin", "dana": "admin", "sivan": "admin"}
    assert all(s["monitor"] == 0 for s in body)


def test_the_response_never_carries_the_cookie_or_the_full_hash(net):
    text = net["admin"].get(LIST).text
    for key in ("admin", "dana1", "dana2", "sivan"):
        token = _token(net[key])
        assert token not in text
        assert hashlib.sha256(token.encode()).hexdigest() not in text
        assert token.rpartition("|")[2] not in text           # גם לא חתימת ה-HMAC


def test_sessions_are_admin_only_on_read_and_on_revoke(net):
    sid = _sid(net["dana1"])
    assert net["deploy"].get(LIST).status_code == 403
    assert net["deploy"].delete(f"{LIST}/{sid}").status_code == 403
    assert TestClient(net["app"]).get(LIST).status_code == 401
    assert TestClient(net["app"]).delete(f"{LIST}/{sid}").status_code == 401
    assert net["dana1"].get("/api/console/me").status_code == 200, "הסירוב ניתק בכל זאת"


def test_both_routes_demand_the_admin_dependency_not_only_the_console_wall(net):
    """``console_only`` כבר מחזיר 403 ל-deploy בכל נתיב קונסולה, ולכן הבדיקה למעלה לא תיפול אם
    ``admin_only`` יוסר מהנתיב. ההרשאה נבדקת בשרת, בנתיב עצמו (הוראת ה-Issue) -- וכאן נבדק שהיא שם."""
    found = {}
    for route in net["app"].routes:
        path = getattr(route, "path", "")
        if path in ("/api/console/sessions", "/api/console/sessions/{sid}"):
            for method in route.methods - {"HEAD"}:
                found[(method, path)] = {d.call.__name__ for d in route.dependant.dependencies}
    for key in (("GET", "/api/console/sessions"), ("DELETE", "/api/console/sessions/{sid}")):
        assert key in found, (key, sorted(found))
        assert "admin_only" in found[key], (key, found[key])


# --- ניתוק סשן בודד -------------------------------------------------------------


def test_revoking_a_session_rejects_its_cookie_at_once(net):
    """הלב של ה-Issue: אחרי ה-DELETE הבקשה הבאה של אותו דפדפן היא 401, בלי המתנה."""
    victim = net["dana1"]
    assert victim.get("/api/console/me").status_code == 200
    r = net["admin"].delete(f"{LIST}/{_sid(victim)}")
    assert r.status_code == 200, r.text
    assert r.json() == {"user": "dana", "sid": _sid(victim)}
    assert victim.get("/api/console/me").status_code == 401, "העוגייה שנותקה עדיין תקפה"
    assert auth.check(net["conn"], _token(victim)) is None
    assert _sid(victim) not in [s["sid"] for s in _listed(net)]


def test_revoking_one_session_leaves_the_other_sessions_of_the_same_user(net):
    net["admin"].delete(f"{LIST}/{_sid(net['dana1'])}")
    assert net["dana2"].get("/api/console/me").status_code == 200
    assert net["sivan"].get("/api/console/me").status_code == 200
    assert sorted(s["user"] for s in _listed(net)) == ["dana", "noc", "sivan"]


def test_a_revoked_cookie_is_not_listed_even_if_a_late_request_rewrites_its_row(net):
    """בקשה שכבר עברה את בדיקת העוגייה כשנותקה עוד כותבת ``last_seen`` -- השורה חוזרת.
    הרשימה לא מראה עוגייה שהשרת דוחה."""
    victim = net["dana1"]
    net["admin"].delete(f"{LIST}/{_sid(victim)}")
    presence._written.clear()
    presence.touch(net["conn"], _token(victim), "dana", "10.0.0.9", auth.TTL_SECONDS)
    assert net["conn"].execute(
        "SELECT COUNT(*) FROM console_sessions WHERE token_hash = ?",
        (hashlib.sha256(_token(victim).encode()).hexdigest(),)).fetchone()[0] == 1
    assert _sid(victim) not in [s["sid"] for s in _listed(net)]


def test_an_admin_may_revoke_another_admin_even_the_only_active_session(net, monkeypatch):
    """החלטת נדב 06/11 (שאלה 3): מותר. ניתוק לא מוחק כלום, והוא נכנס שוב עם הסיסמה."""
    assert net["admin"].delete(f"{LIST}/{_sid(net['sivan'])}").status_code == 200
    assert net["sivan"].get("/api/console/me").status_code == 401
    again = _login(net["app"], "sivan", "sivan-pass-123", monkeypatch, 23)
    assert again.get("/api/console/me").status_code == 200


def test_the_current_session_cannot_be_revoked_from_the_list(net):
    r = net["admin"].delete(f"{LIST}/{_sid(net['admin'])}")
    assert r.status_code == 409, r.text
    assert net["admin"].get("/api/console/me").status_code == 200


def test_bad_unknown_and_repeated_ids_are_visible_failures(net):
    assert net["admin"].delete(f"{LIST}/zzzzzz").status_code == 400
    assert net["admin"].delete(f"{LIST}/abc").status_code == 400
    assert net["admin"].delete(f"{LIST}/000000").status_code == 404
    sid = _sid(net["dana1"])
    assert net["admin"].delete(f"{LIST}/{sid}").status_code == 200
    assert net["admin"].delete(f"{LIST}/{sid}").status_code == 404, "ניתוק כפול נראה כהצלחה"


def test_an_ambiguous_short_id_revokes_nothing(net, monkeypatch):
    """שני סשנים שהמזהה הקצר שלהם זהה (24 ביט): 409 ולא "הראשון שנמצא"."""
    monkeypatch.setattr(presence, "SID_LEN", 1, raising=False)
    sid = _sid(net["dana1"])[:1]
    with _write_lock, writing(net["conn"]):                # מכריחים התנגשות בטבלה עצמה
        net["conn"].execute(
            "UPDATE console_sessions SET token_hash = ? || substr(token_hash, 2)"
            " WHERE username IN ('sivan', 'dana')", (sid,))
    assert len([s for s in _listed(net) if s["sid"] == sid]) >= 2
    assert net["admin"].delete(f"{LIST}/{sid}").status_code == 409
    assert net["dana1"].get("/api/console/me").status_code == 200


def test_revoking_writes_a_journal_row_with_who_and_whose(net):
    net["admin"].delete(f"{LIST}/{_sid(net['dana1'])}")
    rows = _journal(net["conn"], "session_revoked")
    assert len(rows) == 1
    assert rows[0]["user"] == "noc"
    assert rows[0]["detail"].split()[:2] == ["dana", _sid(net["dana1"])]
    assert _token(net["dana1"]) not in rows[0]["detail"]


def test_revoking_closes_an_open_monitor_bridge_of_that_session(net):
    admin, victim, other = net["admin"], net["dana1"], net["dana2"]

    async def scenario():
        loop = asyncio.get_running_loop()
        bridge = asyncio.ensure_future(asyncio.sleep(30))
        bystander = asyncio.ensure_future(asyncio.sleep(30))
        viewer_sessions.register(_token(victim), "dana", bridge)
        viewer_sessions.register(_token(other), "dana", bystander)
        try:
            listed = await loop.run_in_executor(None, lambda: admin.get(LIST).json())
            assert {s["sid"]: s["monitor"] for s in listed}[_sid(victim)] == 1
            assert {s["sid"]: s["monitor"] for s in listed}[_sid(other)] == 1
            r = await loop.run_in_executor(None, lambda: admin.delete(f"{LIST}/{_sid(victim)}"))
            assert r.status_code == 200, r.text
            await asyncio.sleep(0.05)
            assert bridge.cancelled() and viewer_sessions.was_revoked(bridge)
            assert not bystander.done(), "הגשר של סשן אחר נסגר"
        finally:
            for token, task in ((_token(victim), bridge), (_token(other), bystander)):
                task.cancel()
                viewer_sessions.forget(token, task)

    asyncio.run(scenario())


# --- צא מכל המכשירים -------------------------------------------------------------


def test_all_devices_of_a_user_are_rejected_at_once(net):
    """נקודת הקצה הקיימת (#1075) -- המסך משתמש בה. הסעיף השני בהגדרת "גמור"."""
    r = net["admin"].post(f"{LIST}/revoke", json={"username": "dana"})
    assert r.status_code == 200, r.text
    assert net["dana1"].get("/api/console/me").status_code == 401
    assert net["dana2"].get("/api/console/me").status_code == 401
    assert net["sivan"].get("/api/console/me").status_code == 200
    assert "dana" not in [s["user"] for s in _listed(net)]
    assert _journal(net["conn"], "sessions_revoked") == [{"user": "noc", "detail": "dana"}]


def test_all_devices_for_yourself_also_closes_this_session(net):
    assert net["admin"].post(f"{LIST}/revoke", json={"username": "noc"}).status_code == 200
    assert net["admin"].get("/api/console/me").status_code == 401
    assert net["dana1"].get("/api/console/me").status_code == 200


def test_all_devices_is_admin_only(net):
    assert net["deploy"].post(f"{LIST}/revoke", json={"username": "dana"}).status_code == 403
    assert net["dana1"].get("/api/console/me").status_code == 200
