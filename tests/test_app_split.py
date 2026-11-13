"""#731 (tracer 1 של #703): פיצול המאזינים — סוכן מול קונסולה.

מה שנבדק כאן, וזה **התנהגות** ולא מבנה:
- **מלאי-נתיבים**: כל נתיב עונה באפליקציה הנכונה, ומחזיר 404 באחרת.
- **זהות משותפת**: שתי האפליקציות חולקות `ctx`/`conn` **אחד** — לא שני
  DB (CLAUDE.md gotcha: שני חיבורי sqlite = "cannot commit").
- **אתחול חד-פעמי**: ה-sweep רץ **פעם אחת** לכל runtime, לא לכל אפליקציה.
- **כיבוי מתואם / כשל bind**: `serve_all` מוריד את כל השרתים כשאחד יצא,
  ומגלגל החוצה חריגת bind במקום להיראות כאילו עלה (עיקרון 5) — #996
  העביר את זה ל-`ports.Listeners`, ונבדק ב-`test_port_listeners.py`.

הבקרה השלילית (בגוף ה-PR): מוטציה שבה כל אפליקציה בונה runtime משלה —
זהות הופכת שקרית וה-sweep רץ פעמיים, ובדיוק שני הטסטים האלה נופלים.
"""

from __future__ import annotations

from pathlib import Path

import pytest

try:
    from fastapi.testclient import TestClient
    from starlette.routing import Mount
except ImportError:  # pragma: no cover
    TestClient = None

from server import app as app_module
from server.app import (create_agent_app, create_console_app,
                        create_runtime)


def _runtime(tmp_path: Path, images_root: Path):
    """runtime עם שולח מזויף — אף round לא נפתח כאן, אבל השולח נבנה
    בכל מקרה, וריאל אחד היה מגיע ל-udp-sender (conftest חוסם ומכשיל)."""
    from test_sender import Recorder                       # noqa: PLC0415
    return create_runtime(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        sender_runner=Recorder(block=True),
    )


def _mounts(app) -> set[str]:
    return {r.path for r in app.routes if isinstance(r, Mount)}


def _absent(client: TestClient, method: str, path: str) -> bool:
    """הנתיב אינו קיים באפליקציה הזו — 404 ברמת ה-app (ולא 4xx אחר
    שמעיד שהנתיב **כן** קיים אבל דחה את הבקשה)."""
    return client.request(method, path, json={}).status_code == 404


def _present(client: TestClient, method: str, path: str) -> bool:
    return client.request(method, path, json={}).status_code != 404


@pytest.fixture()
def split(tmp_path: Path, images_root: Path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    rt = _runtime(tmp_path, images_root)
    agent_app = create_agent_app(rt)
    console_app = create_console_app(rt)
    yield rt, agent_app, console_app
    rt.ctx.sender.stop()


# --- מלאי נתיבים -----------------------------------------------------------

def test_agent_routes_only_on_agent_app(split):
    rt, agent_app, console_app = split
    agent, console = TestClient(agent_app), TestClient(console_app)

    # /api/v1 של הסוכן + capture + מאזין /boot — כאן, לא בקונסולה.
    assert _present(agent, "POST", "/api/v1/agent/hello")
    assert _absent(console, "POST", "/api/v1/agent/hello")
    # capture-manifest מחזיר 404 גם כשהנתיב קיים (המשימה לא נמצאה), ולכן
    # מבדילים דרך המתודה: GET על נתיב PUT → 405 אם הנתיב רשום, 404 אם לא.
    assert agent.get("/api/v1/capture/t1/manifest").status_code == 405
    assert console.get("/api/v1/capture/t1/manifest").status_code == 404
    assert "/boot" in _mounts(agent_app)
    assert "/boot" not in _mounts(console_app)


def test_console_routes_only_on_console_app(split):
    rt, agent_app, console_app = split
    agent, console = TestClient(agent_app), TestClient(console_app)

    # קונסולה + סטטי /console — כאן, לא בסוכן. login עצמו הוא allowlist
    # הקיוסק ולכן חי גם על הסוכן (ראו test_build_flow_console_calls_on_agent_app);
    # מה שאסור על הסוכן הוא נתיב ניהול.
    assert _present(console, "POST", "/api/console/login")
    assert _absent(agent, "GET", "/api/console/users")
    assert _absent(agent, "GET", "/api/console/machines")
    assert _absent(agent, "GET", "/api/console/net")
    assert TestClient(console_app).get("/console/").status_code == 200
    assert _absent(agent, "GET", "/console/")
    assert "/console" in _mounts(console_app)
    assert "/console" not in _mounts(agent_app)


def test_station_router_on_agent_not_console(split):
    # #738 (tracer 2 של #703): `create_station_router` (prefix
    # `/api/v1/agent/{groups,state,sessions}`) חולץ מהקונסולה — ואפליקציית
    # הניהול אינה מאזינה בוילן ההפצה. #1247: אפליקציית הקיוסק (:8082) הוסרה.
    # #824: אותם נתיבים הם גם ה-API שהסוכן על המכונה צורך (guistate.sh,
    # guibridge.sh, guiparent.sh) דרך `$SERVER` = פורט
    # הסוכן — ולכן הם חיים גם על הסוכן. על הקונסולה — לא.
    rt, agent_app, console_app = split
    agent, console = TestClient(agent_app), TestClient(console_app)
    assert _present(agent, "POST", "/api/v1/agent/sessions")
    assert _present(agent, "GET", "/api/v1/agent/state?mac=aa:bb:cc:dd:ee:ff")
    assert _absent(console, "POST", "/api/v1/agent/sessions")
    assert _absent(console, "GET", "/api/v1/agent/state?mac=aa:bb:cc:dd:ee:ff")


def test_agent_app_alone_serves_station_state(split):
    """#824: הסוכן הגרפי קורא `$SERVER/api/v1/agent/state?mac=…` על פורט
    הסוכן, ומאז #738 קיבל 404 בכל דגימה (53,770 × 404 על השרת החי).
    המעטפת המשולבת `create_app` הסתירה זאת — שם הכול על אפליקציה אחת.
    כאן `agent_app` **לבד**, כמו בייצור: הקריאה המדויקת של guistate.sh:8
    ושל guibridge.sh:38, ומה שהמכונה צריכה לראות — 200 עם המידע שלה."""
    rt, agent_app, _console_app = split
    agent = TestClient(agent_app)
    mac = "aa:bb:cc:dd:ee:ff"
    r = agent.get(f"/api/v1/agent/state?mac={mac}")
    assert r.status_code == 200, r.text
    state = r.json()
    assert state["mac"] == mac and state["known"] is False
    assert state["disks"] == [] and state["task"] is None
    # ומה שהגואי צורך לרשימת הכיתות/ההפצה (guistate.sh:76-77) — גם כאן.
    # ב-v1 (כיתות כבויות, הכרעת נדב 11/10) הנתיב מוגש ומסרב בשם — 409, לא
    # ה-404 של #824.
    groups = agent.get("/api/v1/agent/groups")
    assert groups.status_code == 409 and groups.json()["code"] == "classrooms_off", groups.text
    assert agent.get("/api/v1/agent/sessions/active").json() == {"session": None}


# --- זהות משותפת -----------------------------------------------------------

def test_build_flow_console_calls_on_agent_app(split):
    """17/09 01:06, מדוד על השרת החי: מחשב הבנייה (10.44.0.88) שלח
    `POST /api/console/login` ל-`$SERVER` = פורט הסוכן וקיבל **404** — הכניסה
    במסך הבנייה נכשלה עם סיסמה נכונה. #738 העביר את allowlist מחשב הבנייה ל-:8082
    בלבד (הוסר ב-#1247), והסוכן (`buildmenu.sh:59,95,105`, `roomflow.sh`) קורא את אותם
    נתיבים דרך `$SERVER` — אותה משפחה כמו #824. ה-allowlist של הקיוסק (login,
    folders, images, tasks/capture, room) חי גם על הסוכן; נתיבי ניהול — לא."""
    rt, agent_app, _console_app = split
    agent = TestClient(agent_app)
    assert _present(agent, "POST", "/api/console/login")
    r = agent.post("/api/console/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 401, r.text          # הנתיב חי ועונה, לא 404
    assert _present(agent, "GET", "/api/console/folders")
    assert _present(agent, "GET", "/api/console/images")
    assert _present(agent, "GET", "/api/console/room")
    assert _absent(agent, "GET", "/api/console/users")
    assert _absent(agent, "POST", "/api/console/users")
    assert _absent(agent, "GET", "/api/console/settings")
    assert _absent(agent, "GET", "/api/console/storage-nodes")


def test_both_apps_share_one_runtime(split):
    rt, agent_app, console_app = split
    # אותו ctx בדיוק — לא שתי מופעים.
    assert agent_app.state.ctx is console_app.state.ctx
    assert agent_app.state.ctx is rt.ctx
    # ובעיקר אותו חיבור DB (זה מה שמונע את "cannot commit").
    assert agent_app.state.ctx.conn is console_app.state.ctx.conn
    assert rt.conn is agent_app.state.ctx.conn
    assert agent_app.state.runtime is console_app.state.runtime


# --- אתחול חד-פעמי ---------------------------------------------------------

def test_startup_sweep_runs_once_per_runtime(tmp_path, images_root, monkeypatch):
    if TestClient is None:
        pytest.skip("fastapi is required")
    calls: list[Path] = []
    real = app_module.sweep_work_areas

    def counting_sweep(conn, root):
        calls.append(root)
        return real(conn, root)

    monkeypatch.setattr(app_module, "sweep_work_areas", counting_sweep)

    rt = _runtime(tmp_path, images_root)
    try:
        create_agent_app(rt)
        create_console_app(rt)
        # runtime אחד → sweep אחד, גם אחרי בניית שתי האפליקציות.
        assert len(calls) == 1
    finally:
        rt.ctx.sender.stop()


def test_the_real_lifespan_shutdown_closes_the_sender(split, monkeypatch):
    """#1426: ה-lifespan קרא ל-`rt.sender.close`, ול-ServerRuntime אין
    `sender` — כל עצירה נגמרה ב-"Application shutdown failed" והשולח לא
    נסגר. ה-runtime כאן הוא האמיתי (`create_runtime`), לא מדומה."""
    rt, _agent_app, console_app = split
    closed = []
    real_close = rt.ctx.sender.close
    monkeypatch.setattr(rt.ctx.sender, "close", lambda: closed.append(1) or real_close())
    with TestClient(console_app):
        pass
    assert closed == [1]


def test_two_listeners_on_one_console_app_run_one_clock_and_close_once(split, monkeypatch):
    """הקונסולה מוגשת בשרת uvicorn לכל כתובת; כל אחד מריץ lifespan. היו שני
    מתזמני ריסטרט (דחייה נרשמה פעמיים באותה שנייה) ושתי סגירות שולח."""
    import asyncio                                                 # noqa: PLC0415
    rt, _agent_app, console_app = split
    started, closed = [], []
    monkeypatch.setattr(app_module.auto_reboot, "run",
                        lambda *_a: started.append(1) or asyncio.sleep(3600))
    real_close = rt.ctx.sender.close
    monkeypatch.setattr(rt.ctx.sender, "close", lambda: closed.append(1) or real_close())
    life = console_app.router.lifespan_context

    async def two_listeners():
        async with life(console_app):
            async with life(console_app):
                await asyncio.sleep(0)
            assert closed == [], "מאזין אחד נסגר — השני עדיין חי"
        assert started == [1] and closed == [1]

    asyncio.run(two_listeners())
