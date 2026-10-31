"""‏#996: מנהל המאזינים — סגירה ופתיחה של פורט **בזמן ריצה**, על סוקט אמיתי.

זה הטסט שהעניין דורש במפורש: "כבוי" חייב להיות "לא מאזין" בפועל —
‏bind על loopback אחרי כיבוי מצליח, ‏connect נכשל — ולא 403 על כל בקשה.
‏uvicorn אמיתי על 127.0.0.1 עם פורט שהמערכת בוחרת; אף פורט ייצור אינו
נתפס כאן.

הבקרה השלילית (בגוף ה-PR): בלי `server/net/ports.py` הטסטים כאן לא רצים
כלל (ImportError), ולכן הבקרה נמדדת על `test_port_toggles.py` — שם
הכישלון התנהגותי. כאן הראיה היא הסוקט עצמו.
"""

from __future__ import annotations

import asyncio
import signal
import socket
import threading

import pytest

pytest.importorskip("fastapi")
uvicorn = pytest.importorskip("uvicorn")

from server.net import ports  # noqa: E402


async def _app(scope, receive, send):
    if scope["type"] != "http":
        return
    await send({"type": "http.response.start", "status": 200,
                "headers": [(b"content-type", b"text/plain")]})
    await send({"type": "http.response.body", "body": b"ok"})


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def can_bind(port: int) -> bool:
    """ראיה חיובית שהפורט שוחרר — bind מצליח. לא "connect נכשל"."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return False
        return True


def can_connect(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(1)
        try:
            s.connect(("127.0.0.1", port))
        except OSError:
            return False
        return True


def _factory(port: int):
    return lambda: uvicorn.Server(uvicorn.Config(
        _app, host="127.0.0.1", port=port, log_level="warning", lifespan="off"))


async def _wait(predicate, timeout: float = 15.0) -> None:
    # 15 ולא 5: על runner עמוס של GitHub (3.12, אחרי 4,000 טסטים) uvicorn עלה
    # ביותר מ-5 שניות ונפל "timed out waiting" — לא באג במאזין (v0.47.2).
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() > deadline:
            raise AssertionError("timed out waiting")
        await asyncio.sleep(0.02)


def test_close_releases_the_socket_and_open_rebinds_it():
    agent_port, kiosk_port = free_port(), free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_boot", [_factory(agent_port)], port=agent_port, hosts=["127.0.0.1"])
        mgr.add("kiosk", [_factory(kiosk_port)], port=kiosk_port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("kiosk") and mgr.is_open("http_boot"))
        assert can_connect(kiosk_port)
        assert ("127.0.0.1", kiosk_port) in mgr.bound("kiosk")

        await mgr.set_open("kiosk", False)
        assert not mgr.is_open("kiosk")
        assert mgr.bound("kiosk") == []
        # הראיה: הסוקט שוחרר באמת — אפשר לקשור אותו מחדש מבחוץ.
        assert can_bind(kiosk_port)
        assert not can_connect(kiosk_port)
        # והסוכן לא נפגע מכיבוי הקיוסק — אין "כיבוי מתואם" על כיבוי יזום.
        assert mgr.is_open("http_boot") and can_connect(agent_port)

        await mgr.set_open("kiosk", True)
        assert mgr.is_open("kiosk")
        assert can_connect(kiosk_port)
        assert not can_bind(kiosk_port)

        mgr.stop()
        await asyncio.wait_for(task, 10)
        assert can_bind(agent_port) and can_bind(kiosk_port)

    asyncio.run(scenario())


def test_a_port_disabled_in_the_saved_state_is_not_bound_at_boot():
    """‏#996: מצב שמור שורד אתחול — פורט שכבוי ב-DB לא מופעל כלל."""
    agent_port, kiosk_port = free_port(), free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_boot", [_factory(agent_port)], port=agent_port, hosts=["127.0.0.1"])
        mgr.add("kiosk", [_factory(kiosk_port)], port=kiosk_port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: pid != "kiosk"))
        await _wait(lambda: mgr.is_open("http_boot"))
        assert not mgr.is_open("kiosk")
        assert can_bind(kiosk_port)
        assert can_connect(agent_port)
        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


def test_opening_on_a_taken_port_fails_loudly_and_leaves_the_rest_serving():
    """כשל bind בפתיחה **בזמן ריצה** הוא שגיאה לבקשה — לא נפילת התהליך
    (בניגוד לכשל bind בעלייה, שמפיל את הכול, כמו serve_all הישן)."""
    agent_port, kiosk_port = free_port(), free_port()
    squatter = socket.socket()
    squatter.bind(("127.0.0.1", kiosk_port))
    squatter.listen(1)

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("http_boot", [_factory(agent_port)], port=agent_port, hosts=["127.0.0.1"])
        mgr.add("kiosk", [_factory(kiosk_port)], port=kiosk_port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: pid != "kiosk"))
        await _wait(lambda: mgr.is_open("http_boot"))
        with pytest.raises(ports.ListenerError):
            await mgr.set_open("kiosk", True)
        assert not mgr.is_open("kiosk")
        # ולא רק "עכשיו": כשל bind שנרשם כ"יציאה לא צפויה" היה מפיל את
        # כולם כמה מאות אלפיות אחר כך — ממתינים לפני שמכריזים "השאר חיים".
        await asyncio.sleep(0.5)
        assert not task.done()
        assert mgr.is_open("http_boot") and can_connect(agent_port)
        mgr.stop()
        await asyncio.wait_for(task, 10)

    try:
        asyncio.run(scenario())
    finally:
        squatter.close()


def test_request_is_usable_from_a_worker_thread():
    """ה-endpoint הוא `def` רגיל שרץ בתהליכון מהמאגר של uvicorn; המתג
    חייב לעבוד משם, מול לולאת האירועים שבתהליכון הראשי."""
    kiosk_port = free_port()

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("kiosk", [_factory(kiosk_port)], port=kiosk_port, hosts=["127.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("kiosk"))
        result: dict = {}

        def worker():
            try:
                mgr.request("kiosk", False, timeout=10)
                result["released"] = can_bind(kiosk_port)
            except Exception as exc:                     # noqa: BLE001 — כוונה
                result["error"] = exc

        t = threading.Thread(target=worker)
        t.start()
        await _wait(lambda: not t.is_alive(), timeout=10)
        assert result == {"released": True}
        mgr.stop()
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())


# --- החוזה של serve_all הישן, שנשמר: כיבוי מתואם וכשל bind בעלייה --------

class _FakeServer:
    """‏uvicorn.Server מזויף: `serve()` קורוטינה, `should_exit`, ‏`started`
    ו-`servers` — החוזה המינימלי שהמנהל נשען עליו."""

    def __init__(self, mode: str):
        self.mode = mode
        self.should_exit = False
        self.started = False
        self.servers: list = []

    async def serve(self):
        if self.mode == "raise":
            raise OSError("address already in use")   # כשל bind
        self.started = True
        if self.mode == "quick":
            return                       # יצא מיד (Ctrl-C על שרת אחד)
        while not self.should_exit:      # "wait": חי עד שמבקשים ממנו לצאת
            await asyncio.sleep(0.005)


def test_an_unexpected_exit_of_one_listener_stops_all():
    quick, waiter = _FakeServer("quick"), _FakeServer("wait")
    mgr = ports.Listeners()
    mgr.add("a", [lambda: quick], port=1, hosts=[])
    mgr.add("b", [lambda: waiter], port=2, hosts=[])
    asyncio.run(mgr.serve(lambda pid: True))
    # השרת שיצא מעצמו הוריד גם את זה שהיה ממתין.
    assert waiter.started and waiter.should_exit


def test_bind_failure_at_boot_propagates_and_stops_others():
    failing, waiter = _FakeServer("raise"), _FakeServer("wait")
    mgr = ports.Listeners()
    mgr.add("a", [lambda: waiter], port=1, hosts=[])
    mgr.add("b", [lambda: failing], port=2, hosts=[])
    with pytest.raises(OSError):
        asyncio.run(mgr.serve(lambda pid: True))
    # כשל bind מגלגל חריגה **וגם** מוריד את השרת השני — אין חצי-שרת.
    assert waiter.should_exit


def test_threaded_listener_is_opened_and_stopped_by_the_manager():
    """המאזין הבין-שרתי (8443) הוא thread, לא uvicorn — אותו מתג."""
    log: list[str] = []

    class Threaded:
        port = 8443

    async def scenario():
        mgr = ports.Listeners()
        mgr.add_threaded("interserver", start=lambda: (log.append("start"), Threaded())[1],
                         stop=lambda obj: log.append("stop"), port=8443, hosts=["10.0.0.1"])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("interserver"))
        assert log == ["start"]
        await mgr.set_open("interserver", False)
        assert log == ["start", "stop"] and not mgr.is_open("interserver")
        await mgr.set_open("interserver", True)
        assert log == ["start", "stop", "start"]
        mgr.stop()
        await asyncio.wait_for(task, 10)
        assert log == ["start", "stop", "start", "stop"]

    asyncio.run(scenario())


# --- ‏#742 (אודיט אסטרה על serve_all): אותות, SystemExit, תקרת המתנה ----------

class _SignalGrabbingServer(_FakeServer):
    """כמו uvicorn: ‏`serve()` מתקין handler משלו ל-SIGINT ומשחזר ביציאה."""

    def _handle(self, signum, frame):
        self.should_exit = True

    async def serve(self):
        old = signal.signal(signal.SIGINT, self._handle)
        try:
            await super().serve()
        finally:
            signal.signal(signal.SIGINT, old)


@pytest.fixture()
def saved_signal_handlers():
    """טסט שנופל באמצע לא משאיר handler של מאזין מזויף לטסטים שאחריו."""
    saved = {sig: signal.getsignal(sig) for sig in ports._SIGNALS}
    yield
    for sig, handler in saved.items():
        signal.signal(sig, handler)


def test_the_managers_signal_handler_wins_over_every_listener(saved_signal_handlers):
    """שלושה `serve()` שכל אחד מתקין handler דורסים זה את זה; אחרי כל
    פתיחה/סגירה ה-handler המותקן חייב להיות של המנהל — אחרת Ctrl-C/SIGTERM
    מגיע לשרת אחד בלבד — ובסוף ה-handler המקורי חוזר.

    הבדיקה הראשונה ממתינה ל-`_opening` ריק ולא רק ל-`is_open`: ‏`is_open`
    נדלק ברגע ש-`serve()` של "b" סימן `started` — אחרי שהתקין את ה-handler
    שלו ולפני ש-`_open` התעורר והחזיר את של המנהל. מי שהתעורר ראשון בחלון
    הזה קבע את התוצאה (טיימרים של asyncio בהפרש של שבריר מילישנייה), וב-
    ריצה מלאה ועמוסה על ה-Testrunner זה היה הטסט. ‏`set_open` ממתין
    לסיום המעבר בעצמו, ולכן שתי הבדיקות שאחריו אינן צריכות זאת."""
    original = signal.getsignal(signal.SIGINT)
    a = _SignalGrabbingServer("wait")

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("a", [lambda: a], port=1, hosts=[])
        # ‏Server אינו רב-פעמי — כל פתיחה של "b" מקבלת אחד חדש.
        mgr.add("b", [lambda: _SignalGrabbingServer("wait")], port=2, hosts=[])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("a") and mgr.is_open("b") and not mgr._opening)
        assert signal.getsignal(signal.SIGINT) == mgr._on_signal
        await mgr.set_open("b", False)
        assert signal.getsignal(signal.SIGINT) == mgr._on_signal
        await mgr.set_open("b", True)
        assert signal.getsignal(signal.SIGINT) == mgr._on_signal
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        await asyncio.wait_for(task, 10)

    asyncio.run(scenario())
    assert a.should_exit
    assert signal.getsignal(signal.SIGINT) == original


def test_uvicorn_sys_exit_on_bind_failure_at_boot_is_a_listener_error():
    """‏uvicorn עושה `sys.exit(3)` על כשל bind — לא OSError כמו ב-_FakeServer
    למעלה. ‏SystemExit שלא נתפס מפיל את הלולאה כולה בלי לסגור את השאר."""

    class _Exits(_FakeServer):
        async def serve(self):
            raise SystemExit(3)

    waiter = _FakeServer("wait")
    mgr = ports.Listeners()
    mgr.add("a", [lambda: waiter], port=1, hosts=[])
    mgr.add("b", [lambda: _Exits("x")], port=2, hosts=[])
    with pytest.raises(ports.ListenerError):
        asyncio.run(mgr.serve(lambda pid: True))
    assert waiter.should_exit


def test_shutdown_does_not_wait_forever_on_a_listener_that_never_exits(monkeypatch):
    """מאזין שמתעלם מ-`should_exit` (חיבור חי שלא נגמר) — הכיבוי מבטל
    אותו אחרי CLOSE_TIMEOUT ולא נתקע לנצח."""
    monkeypatch.setattr(ports, "CLOSE_TIMEOUT", 0.2)

    class _Stubborn(_FakeServer):
        async def serve(self):
            self.started = True
            await asyncio.sleep(3600)

    async def scenario():
        mgr = ports.Listeners()
        mgr.add("a", [lambda: _Stubborn("x")], port=1, hosts=[])
        task = asyncio.ensure_future(mgr.serve(lambda pid: True))
        await _wait(lambda: mgr.is_open("a"))
        mgr.stop()
        await asyncio.wait_for(task, 5)

    asyncio.run(scenario())
