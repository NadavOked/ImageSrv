"""‏#555: מטריצה מלאה נתיב × תפקיד deploy — על **כל** נתיב קונסולה, לא על רשימה.

הכלל (CLAUDE.md): "משתמש deploy: אימג'ים וסבבים בלבד, כל השאר 403". ומאז
‏#1073 הוא חד יותר: בקונסולה (‏8081) deploy מסורב **בכל** נתיב
(‏`deploy_no_console`), ובפורט הסוכן (‏8080) הוא מקבל רק את ה-allowlist של
`kiosk.KIOSK_CONSOLE_ROUTES`. הנתיבים נמנים כאן **מהאפליקציה עצמה**, ולכן
נתיב חדש נכנס למטריצה בלי שאיש יזכור לרשום אותו — וזה השער: נתיב קונסולה
שמשיב ל-deploy משהו שאינו 403 מפיל את הבדיקה בשם."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from starlette.routing import Mount

from server.app import create_agent_app, create_console_app
from server.core import capabilities
from server.agent.kiosk import KIOSK_CLASS_ONLY_ROUTES, KIOSK_CONSOLE_ROUTES

#: נתיבים ציבוריים במכוון — בלי כניסה בכלל, ולכן גם deploy רואה אותם:
#: הכניסה עצמה (שמשיבה ל-deploy 403 ממילא). לוגו מסך הכניסה (#1168) הוסר ב-#1302.
PUBLIC = {("POST", "/api/console/login")}
#: על הקיוסק deploy מקבל 403 בדיוק כאן — קליטה ותיקיות הן של המנהל.
DEPLOY_KIOSK_DENIED = {("POST", "/api/console/folders"), ("POST", "/api/console/tasks/capture")}
DUMMY = {"username": "noc", "gid": "grp_x", "name": "x", "image_id": "img_x", "session_id": "ses_x",
         "task_id": "tsk_x", "filename": "f.bin", "mac": "b4:2e:99:07:1a:c4", "node_id": "n_x",
         "port_id": "p", "record_id": "1", "tag": "v0.0.1", "location_id": "loc_x", "id": "x"}


def _url(path: str) -> str:
    out = path
    for key, val in DUMMY.items():
        out = out.replace("{" + key + "}", val)
    return re.sub(r"\{[^}]+\}", "x", out)     # a parameter nobody listed still gets a value


def _walk(routes):
    # FastAPI keeps included routers as wrapper objects that carry their own
    # `.routes`; the API routes live one level down. Recurse, do not assume.
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):           # FastAPI >= 0.115: _IncludedRouter
            yield from _walk(r.original_router.routes)
        elif hasattr(r, "routes") and not isinstance(r, Mount):
            yield from _walk(r.routes)


def _routes(app):
    rows = []
    for r in _walk(app.router.routes):
        if r.path.startswith("/api/console"):
            for m in sorted(r.methods or ()):
                if m in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                    rows.append((m, r.path))
    return sorted(set(rows))


def _deploy_cookie(rt) -> dict:
    kiosk = TestClient(create_agent_app(rt))
    r = kiosk.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"})
    assert r.status_code == 200, r.text
    return dict(kiosk.cookies)


def test_deploy_gets_403_on_every_console_route(server):
    rt = server["app"].state.runtime
    console = TestClient(create_console_app(rt))
    console.cookies.update(_deploy_cookie(rt))
    rows = _routes(console.app)
    assert len(rows) >= 60, f"הסריקה ראתה רק {len(rows)} נתיבים — היא לא רצה על הקונסולה"
    leaks = []
    for method, path in rows:
        if (method, path) in PUBLIC:
            continue
        body = {} if method in ("POST", "PUT", "PATCH") else None
        resp = console.request(method, _url(path), json=body)
        if resp.status_code != 403:
            leaks.append(f"{method} {path} -> {resp.status_code}")
    assert not leaks, "נתיבי קונסולה שמשיבים ל-deploy משהו שאינו 403:\n" + "\n".join(leaks)


@pytest.mark.parametrize("classrooms", [False, True], ids=["v1", "v1.2"])
def test_the_kiosk_allowlist_is_exactly_what_deploy_can_reach(server, monkeypatch, classrooms):
    # הכרעת נדב 11/10: ב-v1 (כיתות כבויות) רשימת הכיתות אינה מוגשת בקיוסק.
    monkeypatch.setattr(capabilities, "CLASSROOMS", classrooms)
    allow = set(KIOSK_CONSOLE_ROUTES) - (set() if classrooms else set(KIOSK_CLASS_ONLY_ROUTES))
    rt = server["app"].state.runtime
    kiosk = TestClient(create_agent_app(rt))
    kiosk.cookies.update(_deploy_cookie(rt))
    served = set(_routes(kiosk.app))
    # the allowlisted console routes, plus the kiosk's own room routes (#738) -- nothing else
    assert allow <= served, allow - served
    extra = served - allow
    assert all(path.startswith("/api/console/room") for _, path in extra), extra
    # the row-level matrix (#555): what deploy may do from the build machine.
    # The allowlist also serves the admin there -- capture and new folders
    # stay admin-only ("deploy: images and rounds only", CLAUDE.md).
    for method, path in sorted(allow):
        if (method, path) == ("POST", "/api/console/login"):
            continue
        body = {} if method in ("POST", "PUT") else None
        resp = kiosk.request(method, _url(path), json=body)
        if (method, path) in DEPLOY_KIOSK_DENIED:
            assert resp.status_code == 403, f"{method} {path}: admin בלבד, ו-deploy קיבל {resp.status_code}"
        else:
            assert resp.status_code != 403, f"{method} {path}: ברשימת ההיתר של הקיוסק ובכל זאת 403 ל-deploy"


def test_an_anonymous_client_gets_401_not_403_on_the_console(server):
    """‏401 ו-403 הם שני מצבים: בלי עוגייה = לא מחובר; deploy = מחובר ומסורב."""
    rt = server["app"].state.runtime
    console = TestClient(create_console_app(rt))
    for method, path in _routes(console.app):
        if (method, path) in PUBLIC or path.startswith("/api/console/health/live"):
            continue
        body = {} if method in ("POST", "PUT", "PATCH") else None
        resp = console.request(method, _url(path), json=body)
        # v1 ships without the toolbox: /tools/* answers 404 to everyone (v1.1 turns it on);
        # #1303: the same for the PXE proxy read path while classrooms are off (v1.2)
        edition_gated = (path.startswith("/api/console/tools")
                         or path == "/api/console/net/proxy-support")
        allowed = {401, 404} if edition_gated else {401}
        assert resp.status_code in allowed, f"{method} {path} -> {resp.status_code} בלי כניסה"
