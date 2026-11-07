"""‏#1633: שומר הקונסולה (‎:8081) — ‏X-Frame-Options, ‏Fetch Metadata, סוג גוף.

מה שנבדק כאן הוא ההתנהגות על ``create_console_app`` — האפליקציה שרצה על 8081
בייצור — ולא על המעטפת המשולבת: ‏``create_app`` מרכיב גם את נתיבי הסוכן
והקיוסק, והשומר במכוון אינו שם.

הפעולה הזדונית בכל טסט היא יצירת משתמש מנהל (``POST /users``): פעולה שמשנה
מצב, שיש לה ראיה חיובית בטבלה, ושאסור שתקרה מדף זר.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import complete_console_login
from server.app import create_agent_app, create_console_app


def _console(server):
    """לקוח מחובר כ-admin על אפליקציית הקונסולה עצמה (8081)."""
    console = TestClient(create_console_app(server["app"].state.runtime))
    complete_console_login(console, server["ctx"].conn, "noc", "admin-pass-123")
    return console


def _user_exists(server, name):
    return server["ctx"].conn.execute(
        "SELECT 1 FROM users WHERE username=?", (name,)).fetchone() is not None


def _new_user(name):
    return {"username": name, "password": "Aa12345!", "role": "admin"}


# --- (א) X-Frame-Options -----------------------------------------------------

@pytest.mark.parametrize("path", ["/console/", "/api/console/me", "/api/console/no-such"])
def test_every_console_response_allows_only_same_origin_framing(server, path):
    """דף הקונסולה, תשובת API ו-404 — כולם ``SAMEORIGIN``, פעם אחת. לא ``DENY``
    (#2642: חסם את ה-iframe של הקונסולה עצמה) ולא ``ALLOWALL``/``ALLOW-FROM``."""
    r = _console(server).get(path)
    values = r.headers.get_list("x-frame-options")
    assert values == ["SAMEORIGIN"], (path, r.status_code, values)


def _console_iframe_srcs():
    """כל ה-``<iframe ... src=...>`` שהקונסולה בונה ב-JS ובדפי ה-HTML שלה."""
    static = Path(__file__).resolve().parent.parent / "server" / "static"
    srcs = []
    for f in [*static.glob("*.js"), *static.glob("*.html")]:
        text = f.read_text(encoding="utf-8")
        srcs += [(f.name, m) for m in re.findall(r"<iframe[^>]*src=\"([^\"]*)\"", text)]
    return srcs


def test_console_iframe_is_same_origin_and_not_denied_2642(server):
    """#2642: "צפייה במסך" טוענת ``monitor.html`` ב-iframe. ‏``DENY`` (#1633) הציג
    שם קובץ שבור. מוכיח את שני הצדדים: (א) ה-iframe היחיד של הקונסולה הוא נתיב
    יחסי (אותו מקור), לא כתובת זרה; (ב) הדף שהוא טוען אינו ``DENY`` — ``SAMEORIGIN``
    בדיוק, כך שדף זר עדיין נחסם."""
    srcs = _console_iframe_srcs()
    assert srcs, "no <iframe> found in the console sources — the test lost its subject"
    js = (Path(__file__).resolve().parent.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    for name, src in srcs:
        if src == "${q}":   # console.js: the one viewer iframe, built from `const q = `monitor.html?...``
            assert re.search(r"const q = `monitor\.html\?", js), "the viewer src is no longer a relative monitor.html"
        else:
            assert not re.match(r"(?:[a-z][a-z0-9+.-]*:|//)", src, re.I), (name, src)
    r = _console(server).get("/console/monitor.html")
    assert r.status_code == 200, r.text
    framing = r.headers.get_list("x-frame-options")
    assert framing != ["DENY"], "#2642: DENY blocks the console's own monitor iframe"
    assert framing == ["SAMEORIGIN"], framing


def test_refusal_also_denies_framing(server):
    r = _console(server).post("/api/console/users", json=_new_user("framed"),
                              headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"


# --- (ב) Fetch Metadata -------------------------------------------------------

def _role(server, name):
    row = server["ctx"].conn.execute(
        "SELECT role FROM users WHERE username=?", (name,)).fetchone()
    return row[0] if row else None


@pytest.mark.parametrize("site", ["cross-site", "same-site", "Cross-Site"])
def test_foreign_page_cannot_change_state(server, site):
    """‏same-site נדחה גם הוא: על IP, דף בפורט אחר של אותו שרת הוא same-site.
    שלוש שיטות על נתיבים אמיתיים, וכל אחת עם ראיה בטבלה שלא קרה דבר."""
    console = _console(server)
    assert console.post("/api/console/users", json=_new_user("victim")).status_code == 200
    foreign = {"Sec-Fetch-Site": site}

    r = console.post("/api/console/users", json=_new_user("evil"), headers=foreign)
    assert (r.status_code, r.json().get("code")) == (403, "cross_site"), r.text
    assert not _user_exists(server, "evil")

    r = console.put("/api/console/users/victim", json={"role": "deploy"}, headers=foreign)
    assert (r.status_code, r.json().get("code")) == (403, "cross_site"), r.text
    assert _role(server, "victim") == "admin"

    r = console.delete("/api/console/users/victim", headers=foreign)
    assert (r.status_code, r.json().get("code")) == (403, "cross_site"), r.text
    assert _user_exists(server, "victim")


def test_patch_is_guarded_too(server):
    """אין היום נתיב PATCH בקונסולה; השומר עונה לפני הניתוב, כך שנתיב PATCH
    שיתווסף מחר מוגן מהיום הראשון (בלי השומר: 405)."""
    r = _console(server).patch("/api/console/users/noc", json={"role": "deploy"},
                               headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403, r.text


def test_two_fetch_site_values_one_foreign_is_foreign(server):
    console = _console(server)
    r = console.post("/api/console/users", json=_new_user("evil-dup"),
                     headers=[("Sec-Fetch-Site", "same-origin"),
                              ("Sec-Fetch-Site", "cross-site")])
    assert r.status_code == 403, r.text
    assert not _user_exists(server, "evil-dup")


@pytest.mark.parametrize("site", ["same-origin", "none", None])
def test_own_page_user_action_and_non_browser_pass(server, site):
    """הקונסולה עצמה, פעולת משתמש, ולקוח בלי הכותרת (סוכן/curl/TestClient)."""
    name = f"ok-{site or 'missing'}"
    headers = {"Sec-Fetch-Site": site} if site else {}
    r = _console(server).post("/api/console/users", json=_new_user(name), headers=headers)
    assert r.status_code == 200, (site, r.status_code, r.text)
    assert _user_exists(server, name)


# --- (ב2) דפדפן ישן: בלי Sec-Fetch-Site, עם Origin ----------------------------
#
# ‏TestClient מדבר עם ``http://testserver`` — זה ה-origin של "הקונסולה עצמה" כאן.

@pytest.mark.parametrize("origin", [
    "http://testserver:8082",        # אותו host, פורט אחר — הדף שהמיון תיאר
    "https://testserver",            # scheme אחר
    "http://evil.example",           # host אחר
    "null",                          # sandbox / file:// / הפניה
    "http://testserver/console/",    # Origin אינו נושא נתיב; ערך כזה אינו תקין
])
def test_old_browser_foreign_origin_is_refused(server, origin):
    name = "evil-origin-" + str(abs(hash(origin)))
    r = _console(server).post("/api/console/users", json=_new_user(name),
                              headers={"Origin": origin})
    assert "sec-fetch-site" not in r.request.headers
    assert (r.status_code, r.json().get("code")) == (403, "cross_origin"), (origin, r.text)
    assert not _user_exists(server, name)


@pytest.mark.parametrize("origin", ["http://testserver", "http://testserver:80",
                                    "HTTP://TestServer"])
def test_old_browser_own_origin_passes(server, origin):
    """הקונסולה עצמה בדפדפן ישן — פורט ברירת מחדל ורישיות אינם הבדל."""
    name = "ok-origin-" + str(abs(hash(origin)))
    r = _console(server).post("/api/console/users", json=_new_user(name),
                              headers={"Origin": origin})
    assert r.status_code == 200, (origin, r.text)
    assert _user_exists(server, name)


def test_cross_site_navigation_still_opens_the_console(server):
    """GET אינו משנה מצב — קישור מדף אחר אל הקונסולה ממשיך לעבוד."""
    r = _console(server).get("/api/console/me", headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 200, r.text


# --- (ג) סוג הגוף -------------------------------------------------------------

@pytest.mark.parametrize("ctype", [
    "text/plain",
    "text/plain;charset=UTF-8",
    "application/x-www-form-urlencoded",
    "multipart/form-data; boundary=x",
    "application/json, text/plain",
])
def test_json_body_under_a_simple_type_is_refused(server, ctype):
    """הגוף הוא JSON תקין — ``request.json()`` היה מקבל אותו. הכותרת מכריעה."""
    name = "evil-" + ctype.split("/")[1].split(";")[0].split(",")[0]
    r = _console(server).post("/api/console/users", content=json.dumps(_new_user(name)),
                              headers={"Content-Type": ctype})
    assert r.status_code == 415, (ctype, r.status_code, r.text)
    assert r.json()["code"] == "body_type"
    assert not _user_exists(server, name)


def test_body_without_content_type_is_refused(server):
    """‏Blob בלי type ב-fetch זר: גוף בלי ``Content-Type`` — גם זו בקשה פשוטה.
    ‏httpx עם ``content=`` אינו מוסיף ``Content-Type`` — אורך בלבד, או chunked."""
    console = _console(server)
    raw = json.dumps(_new_user("evil-blob")).encode()
    r = console.post("/api/console/users", content=raw)
    assert "content-type" not in r.request.headers
    assert r.status_code == 415, r.text
    r = console.post("/api/console/users", content=iter([raw]))
    assert r.request.headers.get("transfer-encoding") == "chunked"
    assert r.status_code == 415, r.text
    assert not _user_exists(server, "evil-blob")


def test_bodyless_post_without_content_type_passes(server):
    """‏POST בלי גוף ובלי כותרת (‏logout) — אין מה לפרש, עובר."""
    console = _console(server)
    r = console.post("/api/console/logout")
    assert "content-type" not in r.request.headers
    assert r.status_code == 200, r.text
    assert console.get("/api/console/me").status_code == 401


def test_node_console_write_refuses_text_plain(server):
    """המקום שהמיון נקב בו (``storage.py`` ‏``node_console_write``) — נעצר לפני ה-handler."""
    r = _console(server).post("/api/console/storage-nodes/nope/console/users",
                              content=json.dumps(_new_user("x")),
                              headers={"Content-Type": "text/plain"})
    assert r.status_code == 415, r.text


def test_json_and_bodyless_delete_pass(server):
    console = _console(server)
    assert console.post("/api/console/users", json=_new_user("gone")).status_code == 200
    # ‏``del()`` ב-console.js: ‏application/json בלי גוף.
    r = console.delete("/api/console/users/gone", headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    assert not _user_exists(server, "gone")


def test_tar_upload_type_reaches_the_handler(server):
    """העלאת אימג' (XHR, ‏application/x-tar) אינה נחסמת בשומר — ה-handler עונה."""
    r = _console(server).post("/api/console/images/upload", content=b"not a tar",
                              headers={"Content-Type": "application/x-tar"})
    assert r.status_code not in (403, 415), r.text
    assert r.headers.get("x-frame-options") == "SAMEORIGIN"


# --- מה שאסור לשבור ----------------------------------------------------------

def test_agent_port_is_untouched(server):
    """פורט הסוכן (8080, קיוסק וסוכן): בלי השומר ובלי הכותרת."""
    agent = TestClient(create_agent_app(server["app"].state.runtime))
    r = agent.post("/api/console/login", content=b"{}", headers={"Content-Type": "text/plain"})
    assert r.status_code != 415, r.text
    assert "x-frame-options" not in r.headers


def test_interserver_in_process_write_passes(server):
    """הערוץ הבין-שרתי מריץ כתיבה בתוך התהליך דרך כל ה-middleware של הקונסולה
    (``_run_console``): בלי ``Sec-Fetch-Site``, עם ``application/json``."""
    from server.interserver import api as interserver_api
    app = create_console_app(server["app"].state.runtime)
    status, _headers, body = interserver_api.run_console_write(
        app, "POST", "/users", "", "primary:noc",
        json.dumps(_new_user("via-peer")).encode())
    assert status == 200, (status, body)
    assert _user_exists(server, "via-peer")
