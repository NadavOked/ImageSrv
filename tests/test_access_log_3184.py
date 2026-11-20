"""#3184: שורות הגישה של uvicorn -- חריגים בלבד, לא polling שגרתי.

שלושה סוגי בדיקה:

* **טבלת סיווג** על הפילטר עצמו, עם רשומה בצורה ש-uvicorn כותב.
* **uvicorn אמיתי** (שרת בתהליכון, בקשות HTTP אמיתיות): מה שיוצא ל-
  ``uvicorn.access`` אחרי ש-``uvicorn.Config`` רץ ו-``dictConfig`` שלו עבר.
  רק כך נבדק ש-(א) הפילטר שורד את ההגדרה של uvicorn, ו-(ב) הזמן שה-
  middleware מודד מגיע לפילטר דרך ה-``ContextVar`` -- גם מנתיב ``async`` וגם
  מ-``def`` שרץ ב-threadpool.
* **חיווט:** שלוש מפעלות האפליקציה מתקינות את המדידה.
"""

import asyncio
import http.client
import logging
import socket
import threading
import time
from pathlib import Path

import pytest

from server.system import accesslog

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    FastAPI = None


def _record(method: str, target: str, status, *, args=None) -> logging.LogRecord:
    """כמו ש-uvicorn כותב: ``(client_addr, method, path_with_query, http_version, status)``."""
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        args if args is not None else ("10.55.0.7:41234", method, target, "1.1", status), None)


@pytest.fixture(autouse=True)
def _clean_slow():
    token = accesslog._slow.set(None)
    yield
    accesslog._slow.reset(token)


# ---------- טבלת סיווג ----------

KEPT = [
    # כל 4xx/5xx, גם על polling
    ("GET", "/api/console/machines", 404),
    ("GET", "/api/console/machines", 500),
    ("POST", "/api/v1/agent/hello", 403),
    ("POST", "/api/v1/agent/progress", 500),
    ("GET", "/api/v1/images/x/manifest", 400),
    # כתיבה של מפעיל
    ("POST", "/api/console/images/delete", 200),
    ("PUT", "/api/console/storage-locations/a", 200),
    ("PATCH", "/api/console/users/u", 200),
    ("DELETE", "/api/console/users/u", 204),
    ("POST", "/api/console/login?next=/", 200),
    # כתיבה נדירה של סוכן -- אירוע, לא polling
    ("POST", "/api/v1/agent/disk-event", 200),
    ("POST", "/api/v1/agent/log", 200),
    ("PUT", "/api/v1/capture/t1/manifest", 202),
]
DROPPED = [
    ("GET", "/api/console/machines", 200),
    ("GET", "/api/console/machines?since=5", 200),
    ("GET", "/api/console/room", 200),
    ("GET", "/api/v1/agent/state", 200),
    ("GET", "/console/index.html", 304),
    ("GET", "/boot/grub.cfg", 200),
    ("GET", "/", 307),
    ("HEAD", "/api/console/machines", 200),
    ("OPTIONS", "/api/console/machines", 200),
    ("POST", "/api/v1/agent/hello", 200),
    ("POST", "/api/v1/agent/progress", 200),
    ("POST", "/api/v1/agent/pulls", 200),
    ("POST", "/api/v1/agent/hello?x=1", 200),
]


@pytest.mark.parametrize("method,target,status", KEPT)
def test_exceptions_are_logged(method, target, status):
    assert accesslog.AccessFilter().filter(_record(method, target, status)) is True


@pytest.mark.parametrize("method,target,status", DROPPED)
def test_routine_polling_that_succeeded_is_dropped(method, target, status):
    assert accesslog.AccessFilter().filter(_record(method, target, status)) is False


@pytest.mark.parametrize("args", [
    ("10.0.0.1:1", "GET", "/x", "1.1"),                 # פחות מחמישה ערכים
    ("10.0.0.1:1", "GET", "/x", "1.1", "200"),          # סטטוס שאינו מספר
    ("10.0.0.1:1", None, "/x", "1.1", 200),             # שיטה חסרה
    (),
])
def test_a_record_it_cannot_classify_is_logged_not_dropped(args):
    """עיקרון 5: "לא הצלחתי לסווג" אינו "שגרתי"."""
    assert accesslog.AccessFilter().filter(_record("", "", 0, args=args)) is True


def test_slow_polling_is_logged_with_its_time():
    accesslog._slow.set(3.456)
    rec = _record("GET", "/api/console/machines", 200)
    assert accesslog.AccessFilter().filter(rec) is True
    assert rec.getMessage().endswith('"GET /api/console/machines [slow 3.5s] HTTP/1.1" 200')


# ---------- uvicorn אמיתי ----------

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _app():
    if FastAPI is None:
        pytest.skip("fastapi is required")
    app = FastAPI(docs_url=None, redoc_url=None)

    @app.get("/api/console/status")
    def status():
        return {"ok": True}

    @app.get("/api/console/boom")
    def boom():
        return JSONResponse({"err": 1}, status_code=500)

    @app.get("/api/console/slow-async")
    async def slow_async():
        await asyncio.sleep(1.4)
        return {"ok": True}

    @app.get("/api/console/slow-sync")
    def slow_sync():
        time.sleep(1.4)     # def: רץ ב-threadpool
        return {"ok": True}

    @app.post("/api/console/images/delete")
    def delete():
        return {"ok": True}

    @app.post("/api/v1/agent/{step}")
    def agent(step: str, deny: int = 0):
        return JSONResponse({"ok": not deny}, status_code=403 if deny else 200)

    accesslog.install(app)
    return app


class _Collect(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record):
        self.lines.append(record.getMessage())


@pytest.fixture()
def live(monkeypatch):
    uvicorn = pytest.importorskip("uvicorn")
    monkeypatch.setattr(accesslog, "SLOW_S", 1.0)   # הרחק מבקשה רגילה גם תחת עומס
    port = _free_port()
    # בדיוק כמו ``server.main``: log_level="info", וההגדרה של uvicorn רצה אחרי ההתקנה.
    server = uvicorn.Server(uvicorn.Config(_app(), host="127.0.0.1", port=port,
                                           log_level="info", lifespan="off"))
    logger = logging.getLogger(accesslog.ACCESS_LOGGER)
    collect = _Collect()
    logger.addHandler(collect)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        thread.join(0.05)
    assert server.started, "uvicorn did not start"
    yield port, collect
    server.should_exit = True
    thread.join(5)
    logger.removeHandler(collect)
    for f in [f for f in logger.filters if isinstance(f, accesslog.AccessFilter)]:
        logger.removeFilter(f)


def _call(port: int, method: str, path: str) -> int:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        resp.read()
        return resp.status
    finally:
        conn.close()


def _line_for(lines: list[str], request: str):
    return [ln for ln in lines if f'"{request}' in ln]


def test_through_real_uvicorn_only_exceptions_reach_the_access_log(live):
    port, collect = live
    assert _call(port, "GET", "/api/console/status") == 200
    assert _call(port, "GET", "/api/console/status?since=1") == 200
    assert _call(port, "POST", "/api/v1/agent/hello") == 200
    assert _call(port, "POST", "/api/v1/agent/progress") == 200
    assert _call(port, "GET", "/api/console/boom") == 500
    assert _call(port, "GET", "/api/console/nope") == 404
    assert _call(port, "POST", "/api/v1/agent/hello?deny=1") == 403
    assert _call(port, "POST", "/api/console/images/delete") == 200
    assert _call(port, "POST", "/api/v1/agent/disk-event") == 200

    lines = collect.lines
    # ראיה חיובית שהערוץ חי: החריגים הגיעו, ולא רק שהשגרתי נעלם
    assert len(_line_for(lines, "GET /api/console/boom")) == 1 and lines[0].endswith("500")
    assert len(_line_for(lines, "GET /api/console/nope")) == 1
    assert len(_line_for(lines, "POST /api/v1/agent/hello?deny=1")) == 1
    assert len(_line_for(lines, "POST /api/console/images/delete")) == 1
    assert len(_line_for(lines, "POST /api/v1/agent/disk-event")) == 1
    # השגרתי לא
    assert _line_for(lines, "GET /api/console/status") == []
    assert _line_for(lines, "POST /api/v1/agent/hello HTTP") == []
    assert _line_for(lines, "POST /api/v1/agent/progress") == []
    assert len(lines) == 5, lines


@pytest.mark.parametrize("path", ["/api/console/slow-async", "/api/console/slow-sync"])
def test_through_real_uvicorn_a_slow_request_is_logged_with_its_time(live, path):
    port, collect = live
    assert _call(port, "GET", "/api/console/status") == 200      # מהיר: לא נרשם
    assert _call(port, "GET", path) == 200
    assert _call(port, "GET", "/api/console/status") == 200      # והסימון לא דולף לבקשה הבאה
    assert len(collect.lines) == 1, collect.lines
    assert f"GET {path} [slow 1." in collect.lines[0] and collect.lines[0].endswith("200")   # ~1.4s


def test_the_filter_survives_uvicorn_logging_setup():
    """``uvicorn.Config`` מריץ ``dictConfig`` על ``uvicorn.access`` -- ובין כמה
    מאזינים (סוכן, קונסולה, 8443) הוא רץ שוב ושוב. הפילטר נשאר, ופעם אחת."""
    uvicorn = pytest.importorskip("uvicorn")
    logger = logging.getLogger(accesslog.ACCESS_LOGGER)
    try:
        app = _app()
        for _ in range(3):
            uvicorn.Config(app, log_level="info", lifespan="off")
            accesslog.install(app)
        assert [type(f) for f in logger.filters].count(accesslog.AccessFilter) == 1
    finally:
        for f in [f for f in logger.filters if isinstance(f, accesslog.AccessFilter)]:
            logger.removeFilter(f)


# ---------- חיווט ----------

def _has_stamp(app) -> bool:
    return any(m.cls is accesslog.SlowStamp for m in app.user_middleware)


def test_the_production_apps_install_it(tmp_path: Path, images_root: Path):
    """סוכן (8080) וקונסולה (8081) -- שתי האפליקציות שרצות בייצור."""
    from test_app_split import _runtime
    from server.app import create_agent_app, create_console_app
    rt = _runtime(tmp_path, images_root)
    try:
        assert _has_stamp(create_agent_app(rt))
        assert _has_stamp(create_console_app(rt))
    finally:
        rt.ctx.sender.stop()


def test_the_combined_app_installs_it(server):
    """המעטפת המשולבת (בדיקות/e2e), כדי שתתנהג כמו הייצור."""
    assert _has_stamp(server["app"])
