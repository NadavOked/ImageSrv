"""‏#1073 — מודל התפקידים ל-v1 (הכרעת נדב 18/09).

1. **למשתמש הפצה אין קונסולה.** ‏`POST /api/console/login` על אפליקציית
   הקונסולה (‎:8081) מסרב ל-`deploy` ב-403 עם ``deploy_no_console`` והודעה
   שמסך הכניסה מציג; והסירוב יושב על **כל** נתיב ניהול, לא רק על הדלת —
   עוגייה שפורט הסוכן הנפיק חתומה באותו סוד ותקפה גם כאן.
2. **מחשב הבנייה ממשיך לעבוד.** allowlist פורט הסוכן (‎:8080) וכניסת הסוכן
   (`/api/v1/agent/login`) מקבלים deploy — משם `buildmenu.sh` וה-GUI נכנסים.
3. **חיפה = ת"א.** שרת משני עונה לאדמין שלו בדיוק כמו standalone על כל
   נתיב, מלבד ההיררכיה (ניהול משניים — במשני אין צאצאים), והקונסולה
   מסתירה רק לפי דגלי ההיררכיה.

הבקרה השלילית של 1 ו-2 (אומתה 18/09, ווינדוס): בלי ``kiosk=`` ב-
`console_api.create_console_router` ובלי ``auth.console_only`` ב-`app.py`
— deploy מקבל 200 בכניסה לקונסולה ועל `/overview`, והטסטים נופלים על
קוד התשובה.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from server.console import auth, users
from server.interserver import storage_nodes

try:
    from fastapi.testclient import TestClient
    from test_kiosk_app import _api_routes
except Exception:                                             # noqa: BLE001
    TestClient = None

pytestmark = pytest.mark.skipif(TestClient is None, reason="fastapi is required")

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "server" / "static"

ADMIN = ("noc", "admin-pass-123")
DEPLOY = ("labtech", "deploy-pass-1")
MAC = "b4:2e:99:07:1a:c4"

#: הנתיבים שמותר להם להשתנות בין standalone למשני — **ההיררכיה בלבד**
#: (‏`require_standalone`, ‏#732), ופאנל ה-pairing המקומי שהוא הצד השני
#: של אותה היררכיה (#740). כל נתיב אחר חייב לענות אותו דבר.
HIERARCHY_ONLY = {
    "/api/console/storage-nodes",
    "/api/console/storage-node-groups",
    "/api/console/storage-transfers",
    "/api/console/storage-pairing-window",
}

#: דגלי `capabilities` שהקונסולה רשאית להסתיר לפיהם — היררכיה בלבד.
HIERARCHY_CAPS = {"interbranch_transfer", "enroll_secondary", "open_local_pairing"}
#: #1081: מהדורה (v1/v2), זהה בכל שרת. לא היררכיה — חיפה = ת"א.
EDITION_CAPS = {"classrooms", "tools"}   # ‏tools: v1 בלי ארגז הכלים (נדב 19/09)


def _login(client: TestClient, who: tuple[str, str]):
    return client.post("/api/console/login",
                       json={"username": who[0], "password": who[1]})


def _safe_hooks(tmp_path: Path) -> dict:
    """הסריקות כאן עוברות על **כל** GET של הקונסולה — גם בריאות, רשת ו-DHCP,
    שבלי הזרקה קוראים את המכונה שהטסט רץ עליה (#113, `hostguard`). מזייפים
    את המינימום שה-GET-ים צורכים; כתיבה נכשלת בשם."""
    from server.net import dhcp, netcfg_host
    from test_machine_identity import _health_hooks       # noqa: PLC0415

    fail = lambda *a, **k: pytest.fail("בדיקה נגעה במכונה אמיתית")   # noqa: E731
    return {
        "health_hooks": _health_hooks(tmp_path),
        "dhcp_hooks": {
            "interfaces": lambda: [], "read_active_conf": lambda: "",
            "service_active": lambda unit: False,
            "probe": lambda name: dhcp.ProbeResult(True, ()),
            "dnsmasq_version": lambda: "Dnsmasq version 2.91\n",
            "apply": fail, "apply_proxy": fail,
        },
        "netcfg_hooks": {
            "interfaces": lambda: [],
            "netcfg_state": lambda: netcfg_host.LiveState(True, {}, [], []),
            "netcfg_sourced": lambda *a: True, "netcfg_now": lambda: 1.0,
            "netcfg_timer_active": lambda *a: (True, "active"),
            "netcfg_read_conf": lambda name, root=None: None,
            "netcfg_local_address": lambda request: "10.10.10.8",
            "netcfg_boot_id": lambda *a: "boot-A",
            "netcfg_foreign_manager": lambda: None,   # #1558: ifupdown, לא המכונה המריצה
            "netcfg_write_conf": fail, "netcfg_write_resolv": fail, "netcfg_apply": fail,
        },
    }


@pytest.fixture()
def split_env(tmp_path: Path, images_root: Path):
    """‏console_app (‎:8081) + agent_app (‎:8080) על runtime אחד — כמו בייצור."""
    from server.app import create_agent_app, create_console_app, create_runtime
    from test_sender import Recorder                       # noqa: PLC0415

    rt = create_runtime(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                        sender_runner=Recorder(block=True), **_safe_hooks(tmp_path))
    users.create(rt.conn, *ADMIN, "admin", by="test",
                 is_builtin=True, check_policy=False)
    users.create(rt.conn, *DEPLOY, "deploy", by="test", check_policy=False)
    env = {"rt": rt, "console_app": create_console_app(rt),
           "agent_app": create_agent_app(rt)}
    yield env
    rt.ctx.sender.stop()


# --- 1: הפצה בלי וובי ---------------------------------------------------------


def test_console_login_refuses_deploy_with_the_contract_body(split_env):
    """403 — לא 401: הסיסמה נכונה, ו"בדקנו ואתה לא נכנס מכאן" הוא מצב
    משלו (עיקרון 5). הגוף הוא החוזה שמסך הכניסה מציג."""
    client = TestClient(split_env["console_app"])
    resp = _login(client, DEPLOY)
    assert resp.status_code == 403, resp.text
    assert resp.json() == {"error": "deploy_no_console",
                           "message_he": auth.DEPLOY_NO_CONSOLE_HE}
    assert auth.COOKIE_NAME not in resp.cookies, "סירוב שהנפיק עוגייה"
    rows = split_env["rt"].conn.execute(
        "SELECT user, detail FROM journal WHERE event = 'login_refused_console'"
    ).fetchall()
    assert [(r["user"], r["detail"]) for r in rows] == [(DEPLOY[0], "deploy")]


def test_console_login_still_admits_admin_and_still_says_401_for_a_wrong_password(split_env):
    """השומרים נגד תיקון-יתר: admin נכנס, וסיסמה שגויה נשארת 401."""
    client = TestClient(split_env["console_app"])
    assert _login(client, ADMIN).status_code == 200
    assert client.get("/api/console/overview").status_code == 200
    bad = client.post("/api/console/login",
                      json={"username": DEPLOY[0], "password": "nope"})
    assert bad.status_code == 401


def _kiosk_deploy_cookie(env) -> str:
    kiosk = TestClient(env["agent_app"])
    resp = _login(kiosk, DEPLOY)
    assert resp.status_code == 200, resp.text
    return resp.cookies[auth.COOKIE_NAME]


def _http_routes(app) -> list[tuple[str, str]]:
    """(method, path) לכל נתיב ‎/api/console ב-HTTP — פרמטרים בנתיב
    מוחלפים בערך דמה; הסירוב יושב לפני שהנתיב קורא אותם."""
    out = sorted((m, re.sub(r"\{[^}]+\}", "x", p)) for m, p in _api_routes(app)
                 if p.startswith("/api/console"))
    assert len(out) > 50, out
    return out


def test_a_kiosk_issued_deploy_cookie_is_refused_on_every_console_route(split_env):
    """הסירוב אינו רק בדלת: העוגייה מ-‎:8080 תקפה קריפטוגרפית גם ב-‎:8081,
    ולכן כל נתיב ניהול מחזיר 403 עם אותה הודעה — כולל מה שסעיף 11 הישן
    פתח ל-deploy (‏`/overview`, ‏`/images`, ‏`/me`, ‏`/room`)."""
    cookie = _kiosk_deploy_cookie(split_env)
    console = TestClient(split_env["console_app"], cookies={auth.COOKIE_NAME: cookie})
    leaks = []
    for method, path in _http_routes(split_env["console_app"]):
        if (method, path) == ("POST", "/api/console/login"):
            continue                       # הדלת עצמה נבדקת למעלה
        resp = console.request(method, path)
        # המוניטור (#690) נשאר מחוץ ל-dependency (ה-WebSocket שלו סוגר
        # אחרי accept) — ה-HTTP שלו admin_only, ולכן 403 עם ההודעה שלו.
        want = None if path.startswith("/api/console/monitor/") else auth.DEPLOY_NO_CONSOLE_HE
        if resp.status_code != 403 or (want and resp.json().get("detail") != want):
            leaks.append(f"{method} {path} -> {resp.status_code}")
    assert not leaks, "נתיבי קונסולה שנפתחו ל-deploy:\n" + "\n".join(leaks)


def test_the_refusal_is_deploy_only_admin_and_anonymous_are_untouched(split_env):
    """השומר נגד תיקון-יתר: admin לעולם אינו רואה את ההודעה על אף GET,
    ומי שאין לו עוגייה מקבל 401 (לא 403) — ה-dependency מעביר הלאה."""
    console = TestClient(split_env["console_app"])
    assert _login(console, ADMIN).status_code == 200
    hit = []
    for method, path in _http_routes(split_env["console_app"]):
        if method != "GET":
            continue
        resp = console.get(path)
        if resp.status_code == 403 and auth.DEPLOY_NO_CONSOLE_HE in resp.text:
            hit.append(path)
    assert not hit, hit
    anon = TestClient(split_env["console_app"])
    assert anon.get("/api/console/overview").status_code == 401


def test_an_admin_demoted_to_deploy_loses_the_console_at_once(split_env):
    """‏#91 באותו כיוון: ההרשאה נקראת מהטבלה בכל בקשה, לא מהעוגייה."""
    from conftest import complete_console_login
    console = TestClient(split_env["console_app"])
    # ‏#1262: לא is_builtin — ה-builtin אינו ניתן להורדה, וזה מנהל רגיל.
    users.create(split_env["rt"].conn, "second", "admin-pass-456", "admin",
                 by="test", check_policy=False)
    complete_console_login(console, split_env["rt"].conn, "second", "admin-pass-456")
    assert console.get("/api/console/overview").status_code == 200
    users.update(split_env["rt"].conn, "second", by="test", role="deploy")
    resp = console.get("/api/console/overview")
    assert resp.status_code == 403
    assert resp.json()["detail"] == auth.DEPLOY_NO_CONSOLE_HE


# --- 2: הקיוסק וכניסת הסוכן ממשיכים לקבל deploy -------------------------------


def test_kiosk_login_admits_deploy_with_a_session_cookie(split_env):
    kiosk = TestClient(split_env["agent_app"])
    resp = _login(kiosk, DEPLOY)
    assert resp.status_code == 200
    assert resp.json()["role"] == "deploy"
    assert resp.cookies.get(auth.COOKIE_NAME), "200 בלי עוגייה אינו session"
    # ומה שמחשב הבנייה צורך אחרי הכניסה עונה לו.
    assert kiosk.get("/api/console/images").status_code == 200
    assert kiosk.get("/api/console/room").status_code == 200


def test_agent_login_admits_deploy(server):
    resp = server["anon"].post("/api/v1/agent/login", json={
        "username": DEPLOY[0], "password": DEPLOY[1], "mac": MAC})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "role": "deploy"}


def test_the_combined_test_app_keeps_deploy_on_the_kiosk_allowlist_only(server):
    """‏`create_app` (הבדיקות) מרכיב את שני הצדדים על אפליקציה אחת: allowlist
    הקיוסק קודם, הקונסולה אחריו. ולכן deploy עובד שם בדיוק כמו בייצור —
    מה שהקיוסק מגיש (אימג'ים, חדר) 200, וכל נתיב ניהול 403."""
    deploy = server["deploy"]
    assert deploy.get("/api/console/images").status_code == 200
    assert deploy.get("/api/console/room").status_code == 200
    for path in ("/api/console/overview", "/api/console/me", "/api/console/machines"):
        resp = deploy.get(path)
        assert resp.status_code == 403, (path, resp.status_code)
        assert resp.json()["detail"] == auth.DEPLOY_NO_CONSOLE_HE


# --- 3: חיפה = ת"א ---------------------------------------------------------------


def _combined_app(tmp_path: Path, images_root: Path, role: str):
    from server.app import create_app
    from test_sender import Recorder                       # noqa: PLC0415

    kwargs = {"storage_role": role, **_safe_hooks(tmp_path / role)}
    if role == "secondary":
        kwargs["primary_url"] = "http://parent:8080"
    app = create_app(tmp_path / f"data-{role}", images_root, "http://10.44.12.10:8080",
                     sender_runner=Recorder(block=True), **kwargs)
    users.create(app.state.ctx.conn, *ADMIN, "admin", by="test",
                 is_builtin=True, check_policy=False)
    # מ-loopback: חלון ה-pairing (#740) הוא loopback בלבד, ובלי זה שני
    # הצדדים עונים 403 על הכתובת ולא על התפקיד.
    client = TestClient(app, client=("127.0.0.1", 40000))
    assert _login(client, ADMIN).status_code == 200
    assert storage_nodes.role(app.state.ctx.conn) == role
    return app, client


def _param_free_gets(app) -> list[str]:
    paths = sorted({p for m, p in _api_routes(app)
                    if m == "GET" and p.startswith("/api/console") and "{" not in p})
    assert len(paths) > 30, paths
    return paths


def test_a_secondary_answers_its_admin_exactly_like_a_standalone(tmp_path, images_root, monkeypatch):
    """כל GET של הקונסולה, כפי שהאדמין המקומי רואה אותו: אותו קוד תשובה על
    משני ועל standalone — מלבד ההיררכיה, ששם ההבדל הוא הפיצ'ר (409/200
    הפוכים). כל נתיב אחר שסטה = משהו שהמשני מסתיר/מגביל, וזה באג."""
    from server.rounds import sender as sender_module             # noqa: PLC0415

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    sa_app, sa = _combined_app(tmp_path, images_root, "standalone")
    se_app, se = _combined_app(tmp_path, images_root, "secondary")
    try:
        assert _param_free_gets(sa_app) == _param_free_gets(se_app)
        drift = []
        for path in _param_free_gets(sa_app):
            a, b = sa.get(path).status_code, se.get(path).status_code
            if path in HIERARCHY_ONLY:
                assert a != b, f"{path}: ההיררכיה אמורה להיות שונה בין התפקידים"
                continue
            if a != b:
                drift.append(f"{path}: standalone {a}, secondary {b}")
        assert not drift, "המשני עונה אחרת מ-standalone:\n" + "\n".join(drift)
        me = se.get("/api/console/me").json()["capabilities"]
        assert me == {"interbranch_transfer": False, "enroll_secondary": False,
                      "open_local_pairing": True, "classrooms": False, "tools": False}
    finally:
        sa_app.state.ctx.sender.stop()
        se_app.state.ctx.sender.stop()


def test_the_console_hides_nothing_on_a_secondary_but_the_hierarchy():
    """‏index.html/console.js מסתירים לפי `data-cap`/`capabilities` — ורק
    לפי דגלי ההיררכיה **או** דגל המהדורה (`classrooms`, זהה בכל שרת).
    דף שייתלה בדגל אחר יסתיר משהו בחיפה שרואים בת"א."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    # #1689: data-cap="a|b" = any of them (console.js splits on "|") -- every
    # one of the alternatives is held to the same rule.
    caps = {cap for value in re.findall(r'data-cap="([^"]+)"', page) for cap in value.split("|")}
    allowed = HIERARCHY_CAPS | EDITION_CAPS
    assert caps and caps <= allowed, caps
    assert "classrooms" in caps
    js = (STATIC / "console.js").read_text(encoding="utf-8")
    used = set(re.findall(r"capabilities(?:\?\.|\.)(\w+)", js))
    used |= set(re.findall(r"\bcaps\.(\w+)", js))
    assert used and used <= allowed, used
    assert "classrooms" in used
