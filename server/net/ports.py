"""מתג הדלקה/כיבוי לכל פורט של השרת — **בזמן ריצה** (‏#996).

הכרעת נדב (17/09): "לכל אחד אמור להיות דונגל הדלקה וכיבוי לפורט". עד
כאן היה מתג ל-DHCP/proxy/מוניטור, ולמאזינים של השרת עצמו — סוכן
‏8080, קונסולה 8081, בין-שרתים 8443 — לא היה כלום.

שני חלקים:

* **‏`Listeners`** — מנהל המאזינים שמחליף את `serve_all` הישן ב-`main.py`.
  מחזיק לכל פורט את היצרן של ה-`uvicorn.Server` שלו (או `start`/`stop`
  של מאזין threaded, כמו ה-mTLS הבין-שרתי), פותח בעלייה רק את מה
  שהמצב השמור מרשה, וסוגר/פותח בזמן ריצה. "כבוי" כאן הוא **סוקט
  סגור** — ‏`ss` לא מראה מאזין, ו-`bind` מבחוץ מצליח — ולא 403 על כל
  בקשה (עיקרון 5: "לא מאזין" נמדד, לא מוצהר).
* **המצב השמור** — ‏`settings` עם מפתח `port:<id>`, ‏JSON ‏`{"enabled": bool}`.
  שורד אתחול: פורט שכבוי ב-DB לא מופעל כלל בעלייה הבאה. חסר = דלוק,
  כי אחרת שדרוג לגרסה הזו היה מכבה את השרת בשקט.

**איך המאזין נסגר ונפתח.** ‏uvicorn 0.32 (דביאן 13) ו-0.52 (התחנה)
חולקים אותו מחזור: ‏`Server.serve()` קושר ב-`startup` ומסמן `started`,
מתקתק ב-`main_loop` כל 0.1ש' עד `should_exit`, ואז `shutdown()` סוגר
**קודם** את סוקטי ההאזנה ורק אחר כך ממתין לחיבורים חיים. לכן סגירה =
‏`should_exit = True` + המתנה למשימה (הפורט משוחרר כבר בתחילת ה-shutdown),
ופתיחה = `Server` **חדש** מאותו `Config` (Server אינו רב-פעמי — ‏`started`
ו-`servers` שלו הם מצב של ריצה אחת) והמתנה ל-`started` כראיה חיובית
שהקשירה הצליחה. ‏`serve()` של uvicorn תופס SIGINT/SIGTERM ומשחזר את
הקודם ביציאה — עם מאזינים שנפתחים ונסגרים הסדר הזה מתערבב, ולכן אחרי
כל מעבר המנהל מתקין את ה-handler **שלו** מחדש: אות = יציאה מתואמת של
כולם, כמו ב-`serve_all` הישן.

**מה נשאר במקום אחר, בכוונה.** ‏DHCP 67 ו-proxy 4011 (`PUT /net/interfaces/
{n}`) ומוניטור 5900 (`PUT /monitor/settings`)
כבר יש להם מתג עם הראיה שלהם — ‏`/ports` **מצביע** אליהם (`toggle_url`) ואינו
משכפל. ‏TFTP 69 (‏#1013) הוא dnsmasq: המתג נשמר כמו כל פורט (`port:tftp`)
ומוחל דרך ה-render של הקונסולה (`console_dhcp.apply_dnsmasq` — ‏`enable-tftp`
נכתב בקובץ שהשרת מרנדר, לא בקובץ המתקין), והראיה היא `ss -ulnp`. שרת
שקובץ המתקין שלו עדיין נושא `enable-tftp` (שודרג בלי להריץ את המתקין)
מקבל `toggle: none` עם הסבר — לא "כבוי" מזויף (`tftp_switch`). מולטיקאסט
9000–9001 הוא תהליך udp-sender של סבב, לא מאזין.
"""

from __future__ import annotations

import asyncio
import json
import re
import signal
import socket
import subprocess
import threading
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from server.console import auth
from server.net import dhcp
from server.db import get_setting, journal, set_setting

SETTING_PREFIX = "port:"

#: כמה זמן ממתינים ל-`started` בפתיחה, ולסיום המשימה בסגירה. הסגירה
#: משחררת את הפורט מיד; ההמתנה היא לחיבורים חיים (`timeout_graceful_shutdown`
#: של uvicorn הוא None = בלי תקרה), ולכן יש לה תקרה משלה כאן.
OPEN_TIMEOUT = 5.0
CLOSE_TIMEOUT = 10.0

#: ‏SIGBREAK קיים רק בווינדוס; ‏uvicorn מטפל באותה שלישייה.
_SIGNALS = tuple(s for s in (signal.SIGINT, signal.SIGTERM,
                             getattr(signal, "SIGBREAK", None)) if s is not None)


class ListenerError(RuntimeError):
    """פתיחה/סגירה **בזמן ריצה** שלא הסתיימה בראיה חיובית — bind שנכשל,
    ‏`started` שלא הגיע בזמן. שגיאה לבקשה, לא נפילה של התהליך."""


# --- המצב השמור ---------------------------------------------------------------


def enabled(conn, port_id: str) -> bool:
    """המתג השמור. **חסר = דלוק**: שרת ששודרג לגרסה הזו חייב לעלות עם
    כל המאזינים, ורק ערך שנכתב במפורש `false` מכבה. ערך שאי אפשר
    לקרוא נחשב דלוק מאותה סיבה — "לא הצלחנו לקרוא" אינו "המפעיל כיבה"."""
    try:
        raw = get_setting(conn, SETTING_PREFIX + port_id)
    except Exception:                                  # noqa: BLE001 — כוונה
        return True
    if not raw:
        return True
    try:
        return json.loads(raw).get("enabled") is not False
    except (ValueError, AttributeError):
        return True


def set_enabled(conn, port_id: str, want: bool) -> None:
    set_setting(conn, SETTING_PREFIX + port_id, json.dumps({"enabled": bool(want)}))


def server_name(conn) -> str:
    """שם השרת — **שם המארח** של המכונה. הכרעת נדב (17/10, סידור עמוד ההגדרות):
    שם אחד לתצוגה (העץ, הכותרת, מסכי התחנות, ‏`/me.server_name`) ולרשת, ומשנים
    אותו במקום אחד — ‏``POST /hostname``. ההגדרה הישנה ``server_name`` (#936) אינה
    נקראת עוד: שם תצוגה נפרד היה "שינוי שם" שני בעמוד. ‏``conn`` נשאר בחתימה
    בשביל הקוראים."""
    del conn
    return socket.gethostname()


# --- מנהל המאזינים -------------------------------------------------------------


def _server_host(server) -> str | None:
    """הכתובת שמאזין uvicorn נקשר אליה — מה-`Config` שלו (‏#705)."""
    return getattr(getattr(server, "config", None), "host", None)


async def _guarded(port_id: str, server) -> None:
    """‏uvicorn עושה `sys.exit(3)` על כשל bind. ‏SystemExit בתוך משימה של
    asyncio אינו נשאר בה — הלולאה כולה נופלת איתו (`Task.__step` מגלגל
    ‏BaseException החוצה). כאן הוא הופך לחריגה רגילה שהמנהל יודע לתפוס:
    בעלייה — יציאה מתואמת של כולם; בזמן ריצה — שגיאה לבקשה, והשאר חיים."""
    try:
        await server.serve()
    except SystemExit as exc:
        raise ListenerError(
            f"{port_id}: המאזין לא עלה (uvicorn יצא {exc.code} — כנראה הפורט "
            "תפוס; ראה יומן השרת)") from exc


class Listeners:
    """המאזינים של התהליך, לפי מזהה פורט (`http_boot`/`http_console`/
    ‏`interserver`). ‏`serve()` מחליף את `serve_all`: פותח מה שמותר, ממתין,
    ומוריד את כולם כשאחד יצא **שלא מרצונו** (Ctrl-C, קריסה) — יציאה שהמנהל
    ביקש אינה מפילה את השאר."""

    def __init__(self, want_open: Callable[[str], bool] | None = None) -> None:
        #: המצב השמור — `want_open(port_id)`; ‏None = הכול פתוח (בדיקות).
        #: ‏main.py קובע אותו אחרי שה-DB קיים (`ports.enabled`).
        self.want_open = want_open
        self._factories: dict[str, list[Callable[[], object]]] = {}
        self._threaded: dict[str, tuple[Callable[[], object], Callable[[object], None]]] = {}
        self._specs: dict[str, dict] = {}
        self._servers: dict[str, list] = {}
        self._tasks: dict[str, list[asyncio.Task]] = {}
        self._threads: dict[str, object] = {}
        self._wanted: set[str] = set()
        #: פורטים באמצע פתיחה: משימה שנגמרת בשלב הזה היא כשל bind
        #: ש-`_open` מטפל בו — לא "יציאה לא צפויה" שמפילה את כולם.
        self._opening: set[str] = set()
        self._failures: list[BaseException] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._exit: asyncio.Event | None = None
        self._lock: asyncio.Lock | None = None
        self._rebind_gen: dict[str, int] = {}
        #: ‏#705: משימות של מאזין שה-retarget פותח או סוגר בכוונה — סיומן
        #: אינו "יציאה לא צפויה" שמפילה את כל השאר.
        self._retiring: set[asyncio.Task] = set()

    # -- רישום (לפני serve) --

    def add(self, port_id: str, factories: list[Callable[[], object]], *,
            port: int, hosts: list[str]) -> None:
        """מאזין uvicorn: יצרן לכל `Server` (הקונסולה יכולה להיות שניים —
        כרטיס הניהול ו-loopback, ‏#904), והפורט/כתובות **לתצוגה** בלבד —
        מה שבאמת מאזין נקרא מהסוקטים (`bound`)."""
        self._factories[port_id] = list(factories)
        self._specs[port_id] = {"port": port, "hosts": list(hosts)}

    def add_threaded(self, port_id: str, *, start: Callable[[], object],
                     stop: Callable[[object], None], port: int, hosts: list[str]) -> None:
        """מאזין שאינו uvicorn (ה-mTLS הבין-שרתי): ‏`start()` קושר ומחזיר
        אובייקט, ‏`stop(obj)` סוגר. ‏OSError מ-`start` הוא כשל bind."""
        self._threaded[port_id] = (start, stop)
        self._specs[port_id] = {"port": port, "hosts": list(hosts)}

    # -- קריאה --

    def ids(self) -> list[str]:
        return list(self._specs)

    def spec(self, port_id: str) -> dict:
        return dict(self._specs[port_id])

    def is_open(self, port_id: str) -> bool:
        if port_id not in self._wanted:
            return False
        if port_id in self._threaded:
            return port_id in self._threads
        return all(s.started for s in self._servers.get(port_id, ())) \
            and not any(t.done() for t in self._tasks.get(port_id, ()))

    def bound(self, port_id: str) -> list[tuple[str, int]]:
        """(כתובת, פורט) של כל סוקט האזנה חי — מהסוקטים עצמם."""
        if port_id in self._threaded:
            obj = self._threads.get(port_id)
            if obj is None:
                return []
            spec = self._specs[port_id]
            return [(h, getattr(obj, "port", spec["port"])) for h in spec["hosts"]]
        found = []
        for server in self._servers.get(port_id, ()):
            for srv in getattr(server, "servers", None) or ():
                for sock in getattr(srv, "sockets", None) or ():
                    name = sock.getsockname()
                    found.append((name[0], name[1]))
        return found

    # -- הרצה --

    async def serve(self, want_open: Callable[[str], bool] | None = None) -> None:
        """מה שהיה `serve_all`: פותח את המותר, ממתין, ומוריד את כולם.

        כשל bind **בעלייה** מגלגל חריגה החוצה אחרי שכולם ירדו — שרת שלא
        תפס את הפורט אינו "עלה", ואסור שיֵרָאה כך בזמן שהסוכן ממשיך לבדו
        (עיקרון 5; החוזה של serve_all נשמר, ונבדק)."""
        want_open = want_open or self.want_open or (lambda port_id: True)
        self._loop = asyncio.get_running_loop()
        self._exit = asyncio.Event()
        self._lock = asyncio.Lock()
        originals = self._install_signal_handlers()
        try:
            for port_id in self._specs:
                if want_open(port_id):
                    try:
                        await self._open(port_id)
                    except ListenerError as exc:
                        # ‏_task_done אולי כבר רשם את החריגה של המשימה עצמה.
                        if not self._failures:
                            self._failures.append(exc.__cause__ or exc)
                        self._exit.set()
                        break
            await self._exit.wait()
        finally:
            await asyncio.gather(*(self._close(p) for p in list(self._wanted)),
                                 return_exceptions=True)
            self._restore_signal_handlers(originals)
        if self._failures:
            raise self._failures[0]

    def stop(self) -> None:
        """בקשת יציאה מתואמת (מה שאות SIGINT/SIGTERM עושה)."""
        if self._loop is not None and self._exit is not None:
            self._loop.call_soon_threadsafe(self._exit.set)

    async def set_open(self, port_id: str, want: bool) -> None:
        """המתג, מתוך לולאת האירועים. מעלה `ListenerError` על פתיחה שנכשלה."""
        if port_id not in self._specs:
            raise KeyError(port_id)
        assert self._lock is not None, "serve() לא רץ"
        async with self._lock:
            if want and port_id not in self._wanted:
                await self._open(port_id)
            elif not want and port_id in self._wanted:
                await self._close(port_id)

    def request(self, port_id: str, want: bool, timeout: float | None = None) -> None:
        """המתג מתהליכון אחר — ה-endpoint הוא `def` שרץ במאגר של uvicorn,
        והלולאה בתהליכון הראשי. חוסם עד שהמעבר הסתיים (או נכשל)."""
        if self._loop is None:
            raise ListenerError("מנהל המאזינים אינו רץ")
        future = asyncio.run_coroutine_threadsafe(self.set_open(port_id, want), self._loop)
        future.result(timeout or (OPEN_TIMEOUT + CLOSE_TIMEOUT))

    async def rebind(self, port_id: str, *, hosts: list[str],
                     factories: list[Callable[[], object]] | None = None,
                     start: Callable[[], object] | None = None) -> None:
        """‏#1088: אותו פורט, כתובת אחרת — הכרטיס קיבל lease חדש.

        סוגר את המאזין הקיים (אם היה פתוח), מחליף את היצרנים/`start` ואת
        הכתובות לתצוגה, ופותח מחדש **רק אם היה פתוח** — מתג שהמפעיל כיבה
        אינו נדלק בגלל שינוי כתובת. הפתיחה מחכה ל-`started` כמו כל פתיחה
        (ראיה חיובית), וכשל bind מגיע לקורא כ-`ListenerError` — לא נבלע.
        """
        if port_id not in self._specs:
            raise KeyError(port_id)
        assert self._lock is not None, "serve() לא רץ"
        # ‏#1121 ס' 6: אסימון דור — כמה rebinds שהצטברו (הדוגם המשיך אחרי
        # timeout) מתבצעים לפי הסדר, ורק **האחרון** נכנס לתוקף: כתובת ישנה
        # שהגיעה לתור מאוחר לא תדרוס את החדשה.
        generation = self._rebind_gen[port_id] = self._rebind_gen.get(port_id, 0) + 1
        async with self._lock:
            if self._rebind_gen.get(port_id) != generation:
                return
            was_open = port_id in self._wanted
            if was_open:
                await self._close(port_id)
            if port_id in self._threaded:
                if start is None:
                    raise ValueError(f"{port_id}: מאזין threaded דורש start חדש")
                self._threaded[port_id] = (start, self._threaded[port_id][1])
            else:
                if factories is None:
                    raise ValueError(f"{port_id}: מאזין uvicorn דורש factories חדשים")
                self._factories[port_id] = list(factories)
            self._specs[port_id]["hosts"] = list(hosts)
            if was_open:
                await self._open(port_id)

    def request_rebind(self, port_id: str, *, hosts: list[str],
                       factories: list[Callable[[], object]] | None = None,
                       start: Callable[[], object] | None = None,
                       timeout: float | None = None) -> None:
        """‏`rebind` מתהליכון אחר (הדוגם של `ifaddr.AddressWatcher`)."""
        if self._loop is None:
            raise ListenerError("מנהל המאזינים אינו רץ")
        future = asyncio.run_coroutine_threadsafe(
            self.rebind(port_id, hosts=hosts, factories=factories, start=start),
            self._loop)
        future.result(timeout or (OPEN_TIMEOUT + CLOSE_TIMEOUT))

    async def retarget(self, port_id: str, *, hosts: list[str],
                       factory_for: Callable[[str], Callable[[], object]]) -> None:
        """‏#705: אותו פורט, **קבוצת כתובות** אחרת — לפי השירותים של כל כרטיס.

        בניגוד ל-`rebind` זה מצטבר: שרת שהכתובת שלו נשארת **אינו נסגר**
        (חיבור הקונסולה שדרכו המפעיל שומר את השינוי חי עליו); כתובת חדשה
        נפתחת ומחכה ל-`started` (ראיה חיובית), ורק אחר כך נסגרות הכתובות
        שהוסרו. פתיחה שנכשלה סוגרת את מה שנפתח בניסיון הזה ומעלה
        `ListenerError` — הכתובות הקיימות נשארות כמו שהיו (עיקרון 5).
        פורט שהמפעיל כיבה בדף הפורטים אינו נפתח: רק היצרנים מתעדכנים.
        """
        if port_id not in self._specs or port_id in self._threaded:
            raise KeyError(port_id)
        assert self._lock is not None, "serve() לא רץ"
        hosts = list(dict.fromkeys(hosts))
        async with self._lock:
            factories = [factory_for(h) for h in hosts]
            if port_id not in self._wanted:
                self._factories[port_id] = factories
                self._specs[port_id]["hosts"] = hosts
                return
            pairs = list(zip(self._servers.get(port_id, []), self._tasks.get(port_id, [])))
            have = {_server_host(s) for s, _t in pairs}
            keep = [(s, t) for s, t in pairs if _server_host(s) in hosts]
            drop = [(s, t) for s, t in pairs if _server_host(s) not in hosts]
            new = [factory_for(h)() for h in hosts if h not in have]
            new_tasks = [asyncio.ensure_future(_guarded(port_id, s)) for s in new]
            self._retiring.update(new_tasks)        # עד started — כשל bind, לא קריסה
            for task in new_tasks:
                task.add_done_callback(lambda t, p=port_id: self._task_done(p, t))
            try:
                deadline = self._loop.time() + OPEN_TIMEOUT
                while not all(s.started for s in new):
                    if any(t.done() for t in new_tasks) or self._loop.time() > deadline:
                        for s in new:
                            s.should_exit = True
                        await asyncio.wait(new_tasks, timeout=CLOSE_TIMEOUT)
                        cause = next((t.exception() for t in new_tasks
                                      if t.done() and not t.cancelled() and t.exception()), None)
                        raise ListenerError(
                            f"{port_id}: כתובת חדשה לא עלתה ({cause or 'לא דיווח started בזמן'})"
                        ) from cause
                    await asyncio.sleep(0.01)
            finally:
                self._retiring.difference_update(new_tasks)
                self._install_signal_handlers()
            dropping = [t for _s, t in drop]
            self._retiring.update(dropping)
            for s, _t in drop:
                s.should_exit = True
            if dropping:
                _done, pending = await asyncio.wait(dropping, timeout=CLOSE_TIMEOUT)
                for task in pending:
                    task.cancel()
                if pending:
                    await asyncio.wait(pending, timeout=1)
            self._retiring.difference_update(dropping)
            self._servers[port_id] = [s for s, _t in keep] + new
            self._tasks[port_id] = [t for _s, t in keep] + new_tasks
            self._factories[port_id] = factories
            self._specs[port_id]["hosts"] = hosts
            self._install_signal_handlers()

    def request_retarget(self, port_id: str, *, hosts: list[str],
                         factory_for: Callable[[str], Callable[[], object]],
                         timeout: float | None = None) -> None:
        """‏`retarget` מתהליכון אחר (ה-endpoint של #705 רץ במאגר של uvicorn)."""
        if self._loop is None:
            raise ListenerError("מנהל המאזינים אינו רץ")
        future = asyncio.run_coroutine_threadsafe(
            self.retarget(port_id, hosts=hosts, factory_for=factory_for), self._loop)
        future.result(timeout or (OPEN_TIMEOUT + CLOSE_TIMEOUT + 2))

    # -- פנימי --

    async def _open(self, port_id: str) -> None:
        if port_id in self._threaded:
            start, _stop = self._threaded[port_id]
            try:
                self._threads[port_id] = start()
            except OSError as exc:
                raise ListenerError(f"{port_id}: {exc}") from exc
            self._wanted.add(port_id)
            return
        servers = [factory() for factory in self._factories[port_id]]
        tasks = [asyncio.ensure_future(_guarded(port_id, s)) for s in servers]
        self._servers[port_id], self._tasks[port_id] = servers, tasks
        self._wanted.add(port_id)
        self._opening.add(port_id)
        for task in tasks:
            task.add_done_callback(lambda t, p=port_id: self._task_done(p, t))
        # הראיה החיובית: `started` — ‏uvicorn מסמן אותו רק אחרי create_server.
        # עד אז המשימה שנגמרה היא כשל bind (uvicorn עושה sys.exit).
        try:
            deadline = self._loop.time() + OPEN_TIMEOUT
            while not all(s.started for s in servers):
                if any(t.done() for t in tasks) or self._loop.time() > deadline:
                    self._wanted.discard(port_id)
                    for s in servers:
                        s.should_exit = True
                    await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT)
                    self._servers.pop(port_id, None)
                    self._tasks.pop(port_id, None)
                    cause = next((t.exception() for t in tasks
                                  if t.done() and not t.cancelled() and t.exception()), None)
                    raise ListenerError(
                        f"{port_id}: המאזין לא עלה ({cause or 'לא דיווח started בזמן'})"
                    ) from cause
                await asyncio.sleep(0.01)
        finally:
            self._opening.discard(port_id)
            self._install_signal_handlers()
        # מאזין שעלה ונגמר בין `started` לכאן (Ctrl-C באמצע פתיחה) — יציאה
        # לא צפויה כמו כל אחרת; ‏_task_done כבר לא ראה אותו כ"נפתח".
        if any(t.done() for t in tasks):
            self._task_done(port_id, next(t for t in tasks if t.done()))

    async def _close(self, port_id: str) -> None:
        self._wanted.discard(port_id)
        if port_id in self._threaded:
            obj = self._threads.pop(port_id, None)
            if obj is not None:
                self._threaded[port_id][1](obj)
            return
        servers = self._servers.pop(port_id, [])
        tasks = self._tasks.pop(port_id, [])
        for s in servers:
            s.should_exit = True
        if tasks:
            _done, pending = await asyncio.wait(tasks, timeout=CLOSE_TIMEOUT)
            # סוקט ההאזנה כבר סגור (תחילת shutdown); מה שנשאר הוא חיבור
            # חי שלא נגמר — מבטלים במקום להחזיק את המתג לנצח.
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.wait(pending, timeout=1)
        self._install_signal_handlers()

    def _task_done(self, port_id: str, task: asyncio.Task) -> None:
        """משימה שנגמרה בזמן שהפורט עדיין "רצוי" = יציאה שלא ביקשנו.
        באמצע פתיחה זה כשל bind, ו-`_open` מדווח אותו למי שביקש."""
        if port_id not in self._wanted or port_id in self._opening or task in self._retiring:
            return
        self._wanted.discard(port_id)
        if not task.cancelled() and task.exception() is not None:
            self._failures.append(task.exception())
        if self._exit is not None:
            self._exit.set()

    def _on_signal(self, signum, frame) -> None:
        del signum, frame
        for servers in self._servers.values():
            for s in servers:
                s.should_exit = True
        self.stop()

    def _install_signal_handlers(self) -> dict:
        """‏`signal.signal` עובד רק בתהליכון הראשי — שם רצה הלולאה בייצור;
        בבדיקות שמריצות את המנהל מתהליכון אחר פשוט אין אותות לתפוס."""
        if threading.current_thread() is not threading.main_thread():
            return {}
        originals = {}
        for sig in _SIGNALS:
            try:
                originals[sig] = signal.signal(sig, self._on_signal)
            except (ValueError, OSError):            # pragma: no cover
                pass
        return originals

    @staticmethod
    def _restore_signal_handlers(originals: dict) -> None:
        for sig, handler in originals.items():
            try:
                signal.signal(sig, handler)
            except (ValueError, OSError):            # pragma: no cover
                pass


# --- הטבלה: מה יש לכל שורה ב-/ports ------------------------------------------

#: המאזינים של השרת עצמו: ‏(toggle, מה קורה אם מכבים). ‏"confirm" = כיבוי
#: מאחורי הקלדת שם השרת (עיקרון 7), כי הוא שובר את המערכת; ‏"api" = מתג
#: רגיל. ‏8081 מסורב בנוסף כשהבקשה מגיעה דרכו (הדלת האחרונה).
OWN = {
    "http_boot": ("confirm",
                  "אין תפריט אתחול, קרנל ו-hello — תחנות לא יעלו מהרשת ויפלו "
                  "לדיסק המקומי, וסבבים לא יתחילו"),
    "http_console": ("confirm",
                     "הקונסולה נסגרת, כולל הדף הזה — פתיחה מחדש רק מהמקלדת של "
                     "השרת (systemctl restart) או מהמסוף"),
    "interserver": ("api",
                    "שרתי משנה לא יוכלו להירשם או לסנכרן מול השרת הזה"),
}

#: ‏#1013 — TFTP 69. האזהרה מוצגת בקונסולה **לפני** שדה הקלדת שם השרת
#: (הכרעת נדב 19/09); ‏v2 יוסיף את מחשבי הכיתה לניסוח.
TFTP_WARNING_HE = ("כיבוי 69 (TFTP) עוצר את ה-PXE: מחשבי בנייה ושיכפול לא יעלו "
                   "מהשרת.")
TFTP_OFF_MEANS = ("אין shim/GRUB ותפריט — אף מחשב לא עולה ב-PXE מהשרת הזה, "
                  "וסבבים לא יתחילו")

#: אזהרה לפני כיבוי, לכל פורט, בניסוח של 69 (הכרעת נדב 19/09: "על כל פורט
#: אזהרה"). הקונסולה מציגה אותה **לפני** ההקלדה/האישור; ‏`off_means` נשאר
#: הטקסט הקצר בשורה. ‏v1 — בלי כיתות; ב-v2 מוסיפים "ומחשבי כיתה".
WARNINGS_HE = {
    "tftp": TFTP_WARNING_HE,
    "dhcp": ("כיבוי 67 (DHCP) עוצר את ה-PXE: מחשבי בנייה ושיכפול לא יקבלו כתובת "
             "ולא יעלו מהשרת."),
    "pxe_proxy": ("כיבוי 4011 (PXE proxy) עוצר את ההפניה ל-PXE ברשת עם DHCP זר: "
                  "מחשבי בנייה ושיכפול לא יעלו מהשרת."),
    "http_boot": ("כיבוי 8080 (סוכן/boot) עוצר הכול: אין תפריט אתחול, קרנל ו-hello — "
                  "מחשבי בנייה ושיכפול יעלו מהדיסק המקומי, וסבבים לא יתחילו."),
    "http_console": ("כיבוי 8081 (הקונסולה) סוגר את הדף הזה: פתיחה מחדש רק מהמסך של "
                     "השרת (systemctl restart imagesrv-server)."),
    "interserver": ("כיבוי 8443 (בין-שרתי) מנתק את שרת המשנה: אין רישום, push או pull "
                    "מול השרת הזה."),
    "monitor": ("כיבוי המוניטור עוצר צפייה ושליטה מרחוק במחשבי בנייה ושיכפול; "
                "אף תחנה לא מקבלת חיבור 5900."),
    "ntp": "כיבוי 123 (NTP) מפסיק לתת שעה למחשבים ברשת; השעון של השרת עצמו ממשיך להתעדכן.",
}


def warning_for(port_id: str) -> str | None:
    """האזהרה לשורה, או None."""
    return WARNINGS_HE.get(port_id)


def tftp_switch(conn, installer_conf: str | None) -> dict:
    """שדות המתג של שורת `tftp` ב-`/ports` (‏#1013), לפי **קובץ המתקין**:

    * ‏`installer_conf` הוא הטקסט של `/etc/dnsmasq.d/imagesrv.conf` (‏`""`
      = אין קובץ), או `None` = קיים ולא נקרא (`dhcp.read_installer_conf`).
    * הקובץ עדיין נושא `enable-tftp` → **אין מתג** (`none`): dnsmasq קורא את
      /etc/dnsmasq.d במצטבר, והקובץ שהשרת מרנדר אינו יכול לבטל אותו.
      זה מצב שדרוג (המתקין לא הורץ מחדש), ו-`off_means` אומר מה לעשות.
    * לא נקרא → גם `none`, בשם: "לא ידוע אם המתג יכול לכבות" אינו "יכול".
    * אחרת → `confirm` מאחורי הקלדת שם השרת (עיקרון 7), כמו 8080/8081.
    """
    if installer_conf is None:
        return {"toggle": "none", "enabled": None,
                "off_means": (f"אין מתג: קובץ המתקין ({dhcp.INSTALLER_CONF}) לא נקרא, "
                              "ולכן לא ידוע אם enable-tftp עדיין יושב בו — "
                              "כיבוי מכאן היה יכול להיראות ככבוי בזמן ש-69 מאזין. "
                              f"כיבוי = {TFTP_OFF_MEANS}")}
    if dhcp.installer_serves_tftp(installer_conf):
        return {"toggle": "none", "enabled": None,
                "off_means": (f"אין מתג: enable-tftp עדיין יושב בקובץ המתקין "
                              f"({dhcp.INSTALLER_CONF}) ו-dnsmasq אינו יודע לבטלו "
                              "מקובץ אחר. הריצו את המתקין מחדש (מגרסה זו הוא אינו "
                              "כותב את השורה, והשרת כותב אותה בקובץ שלו) — ואז "
                              f"המתג יעבוד. כיבוי = {TFTP_OFF_MEANS}")}
    return {"toggle": "confirm", "enabled": enabled(conn, "tftp"),
            "toggle_url": "/api/console/ports/tftp",
            "confirm_word": server_name(conn), "confirm_when": "off",
            "off_means": TFTP_OFF_MEANS, "warning_he": TFTP_WARNING_HE}


#: שורות שהמתג שלהן חי במקום אחר — ‏`/ports` מצביע לשם.
#: #1829: proxy toggles also regenerate the interface-scoped UDP 4011 rule;
#: port_firewall reads that explicit accept rule instead of assuming it is open.
ELSEWHERE = {
    "dhcp": "/api/console/net/interfaces/{name}",
    "pxe_proxy": "/api/console/net/interfaces/{name}",
    "monitor": "/api/console/monitor/settings",
}


def bind_addresses(ss_output: str, port: int) -> list[str] | None:
    """כתובות ההאזנה על פורט, מפלט `ss -ltnp`/`-ulnp`. ‏None = הטבלה לא
    נקראה (פלט ריק/בלי כותרת) — "לא נקרא" ואינו "אף אחד לא מאזין"."""
    lines = ss_output.splitlines()
    if not lines or "Local Address" not in lines[0]:
        return None
    found = []
    for line in lines[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        if local.endswith(f":{port}") and local.rsplit(":", 1)[1] == str(port):
            found.append(local)
    return found


def measured_state(enabled_flag: bool | None, listening: bool | None) -> tuple[str, str]:
    """שלושה מצבים, שלושה צבעים (עיקרון 5): ‏(state, detail).

    ‏"כבוי" הוא המתג; "לא מאזין" נמדד; "לא נקרא" הוא ss שלא רץ — והוא
    אדום (`unknown`), כי דלת שאי אפשר לראות גרועה מדלת פתוחה שיודעים עליה.
    """
    if listening is None:
        return "unknown", "טבלת הסוקטים לא נקראה (ss לא זמין) — לא ידוע אם מאזין"
    if enabled_flag is None:
        return ("ok", "מאזין") if listening else ("off", "אף אחד לא מאזין")
    if enabled_flag and listening:
        return "ok", "המתג דלוק ומאזין"
    if not enabled_flag and not listening:
        return "off", "כבוי על ידי המפעיל — לא מאזין"
    if enabled_flag:
        return "bad", "המתג דלוק אבל אף אחד לא מאזין — המאזין לא עלה"
    return "bad", "המתג כבוי אבל עדיין מאזין — הכיבוי לא תפס"


# --- חומת אש (nftables, #1074) ------------------------------------------------

NFT_TIMEOUT = 5.0


def read_nft_ruleset() -> str | None:
    """פלט `nft list ruleset`. ‏None = לא הצלחנו לבדוק (חסר/נכשל/timeout).

    יציאה 0 עם פלט ריק היא בדיקה שהצליחה — הטבלה פשוט אינה שם. יציאה
    שאינה 0, או שאין בינארי, אינה "לא נטענה"."""
    try:
        proc = subprocess.run(
            ["nft", "list", "ruleset"],
            capture_output=True, text=True, timeout=NFT_TIMEOUT,
            check=False, stdin=subprocess.DEVNULL,
        )
    except FileNotFoundError:
        return None
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout


def _imagesrv_table_body(ruleset: str) -> str | None:
    marker = "table inet imagesrv"
    start = ruleset.find(marker)
    if start < 0:
        return None
    brace = ruleset.find("{", start)
    if brace < 0:
        return None
    depth = 0
    for i, ch in enumerate(ruleset[brace:], brace):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return ruleset[brace + 1:i]
    return None


def count_imagesrv_rules(ruleset: str) -> int | None:
    """מספר כללי הטבלה, או None כשהטבלה אינה שם."""
    body = _imagesrv_table_body(ruleset)
    if body is None:
        return None
    n = 0
    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line in "{}":
            continue
        if line.startswith("chain ") or line.startswith("type "):
            continue
        n += 1
    return n


def rule_interfaces(ruleset: str) -> set[str]:
    """‏#1810: שמות הכרטיסים שכללי טבלת imagesrv מפנים אליהם (‏`iifname`)."""
    body = _imagesrv_table_body(ruleset) or ""
    names = set(re.findall(r'iifname\s+"([^"]+)"', body))
    for group in re.findall(r"iifname\s+\{([^}]*)\}", body):
        names |= set(re.findall(r'"([^"]+)"', group))
    return names


def firewall_status(ruleset: str | None,
                    interfaces: list[str] | None = None) -> tuple[str, str]:
    """שלושה מצבים, שלושה משפטים (עיקרון 5 / #1074).

    None → לא הצלחנו לבדוק; אין טבלת imagesrv → לא נטענה;
    טבלה קיימת → פעילה, N כללים.

    ‏#1810: ‏`interfaces` — הכרטיסים שקיימים במערכת. כלל `iifname` שמפנה לשם
    שאינו ביניהם נטען בלי שגיאה ואינו תופס אף חבילה — אדום, לא "פעילה".
    ‏None = לא נבדק (כמו היום); רשימה ריקה = לא נקראה, ונאמר.
    """
    if ruleset is None:
        return "unknown", "לא הצלחנו לבדוק"
    n = count_imagesrv_rules(ruleset)
    if n is None:
        return "off", "לא נטענה"
    detail = f"פעילה, {n} כללים לטבלת imagesrv"
    if interfaces is None:
        return "ok", detail
    if not interfaces:
        return "ok", detail + " · רשימת הכרטיסים לא נקראה — לא נבדק שהכללים מפנים לכרטיס קיים"
    missing = sorted(name for name in rule_interfaces(ruleset)
                     if name != "lo" and "*" not in name and name not in interfaces)
    if missing:
        return "bad", (f"{detail} — אבל הכללים מפנים לכרטיס שאינו קיים במערכת: "
                       f"{', '.join(missing)}; חבילות בכרטיס האמיתי אינן נתפסות")
    return "ok", detail


#: ‏#1267: לאיזו רשת שייכת כל שורה ב-`/ports`. ברשת ההפצה השרת מנהל את
#: הפורטים ואין חומת אש של המכללה; ‏`mgmt`/`interserver` חוצים רשתות.
#: ‏`firewall` (מצב ה-nft כולו) אינו פורט — ‏None.
NETWORK = {"dhcp": "deploy", "tftp": "deploy", "http_boot": "deploy",
           "pxe_proxy": "deploy", "multicast": "deploy", "monitor": "deploy",
           "http_console": "mgmt", "interserver": "interserver", "ntp": "per_nic"}


def network_of(port_id: str) -> str | None:
    return NETWORK.get(port_id)


def port_firewall(ruleset: str | None, proto: str, port: str) -> tuple[str | None, str]:
    """‏#1267: האם טבלת `inet imagesrv` של השרת מכניסה `proto`/`port`.

    ‏"open" רק על ראיה: כלל `accept` עם `<proto> dport <port>` (או policy
    accept); ‏"blocked" רק כש-policy drop ואין כלל כזה. כל השאר — nft שלא
    נקרא, טבלה שלא נטענה, policy שלא זוהה — ‏None ("לא נבדק", עיקרון 5).
    ‏`nft list ruleset` מדפיס פורטים כמספרים (שמות שירות רק עם ‎-S)."""
    if ruleset is None:
        return None, "nft לא נקרא — לא נבדק"
    body = _imagesrv_table_body(ruleset)
    if body is None:
        return None, "טבלת inet imagesrv לא נטענה — לא נבדק"
    pattern = re.compile(rf"\b{re.escape(proto)} dport {re.escape(port)}(?![\d-])")
    # טווח (`8000-9000`) או קבוצה (`{ 8081, 8443 }`) — המחולל אינו כותב כאלה
    # ל-tcp; כלל כזה שלא פוענח אינו ראיה ל"חסום".
    opaque = re.compile(rf"\b{re.escape(proto)} dport (\{{|\d+-\d+)")
    policy, chain, unparsed = None, None, False
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        if line.startswith("chain "):
            chain = line.split()[1]
            continue
        if chain != "input":
            continue
        if "hook input" in line:
            found = re.search(r"policy (accept|drop)", line)
            policy = found.group(1) if found else policy
        words = line.split()
        accepts = "accept" in words and not {"drop", "reject"} & set(words)
        if pattern.search(line) and accepts:
            return "open", f"כלל פתיחה בטבלת imagesrv: {line}"
        unparsed = unparsed or (accepts and bool(opaque.search(line)))
    if unparsed:
        return None, "יש בטבלת imagesrv כלל טווח/קבוצה שלא פוענח — לא נבדק"
    if policy == "drop":
        return "blocked", "אין כלל פתיחה בטבלת imagesrv, ו-policy drop חוסם חיבור מבחוץ"
    if policy == "accept":
        return "open", "policy accept בטבלת imagesrv — אינה חוסמת"
    return None, "אין כלל פתיחה וה-policy של input לא זוהה — לא ידוע"


# --- ה-endpoint ---------------------------------------------------------------


def create_ports_router(ctx, snapshot: Callable[[], list[dict]],
                        listeners, dnsmasq_apply: Callable[[str, object], str | None] | None = None,
                        ) -> APIRouter:
    """‏`PUT /api/console/ports/{id}` — ‏admin בלבד. נתלה בראוטר הבריאות
    (`/api/console`), כי המתג והחיווי הם אותם hooks.

    ‏`def` ולא `async def`, בכוונה: המעבר עצמו
    רץ על לולאת האירועים דרך `Listeners.request`, ו-endpoint אסינכרוני
    היה חוסם את הלולאה שהוא ממתין לה.

    ‏`dnsmasq_apply(what, user_id)` (‏#1013): מרנדר ומחיל את קובצי ה-dnsmasq
    מה-DB — זה מה שמכבה/מדליק את TFTP 69 בפועל (`console_dhcp.apply_dnsmasq`).
    ‏None = אין חיבור ל-dnsmasq בתהליך הזה, והמתג אומר זאת.
    """
    router = APIRouter(prefix="/ports")
    _current_user, admin_only = auth.dependencies(ctx.conn)

    def row_of(port_id: str) -> dict:
        return next(r for r in snapshot() if r["id"] == port_id)

    @router.put("/{port_id}")
    def set_port(port_id: str, body: dict, request: Request, user=Depends(admin_only)):
        if port_id in ELSEWHERE:
            raise HTTPException(
                409, f"המתג של השורה הזו חי ב-PUT {ELSEWHERE[port_id]} — לא כאן")
        row = next((r for r in snapshot() if r["id"] == port_id), None)
        if row is None:
            raise HTTPException(404, "אין פורט כזה")
        if row["toggle"] == "none" or (port_id not in OWN and port_id != "tftp"):
            raise HTTPException(409, f"אין מתג לשורה הזו — {row['off_means']}")
        want = bool(body.get("enabled", False))
        toggle, off_means = OWN.get(port_id, ("confirm", row["off_means"]))
        if not want:
            # הדלת האחרונה: בקשה שמגיעה דרך הפורט שהיא מבקשת לסגור.
            via = (request.scope.get("server") or ("", 0))[1]
            if via == int(row["port"]):
                raise HTTPException(
                    409, f"הבקשה הגיעה דרך פורט {via} עצמו — סגירתו מכאן תנעל "
                         f"אותך בחוץ. {off_means}")
            if toggle == "confirm":
                expected = server_name(ctx.conn)
                if body.get("confirm") != expected:
                    raise HTTPException(
                        400, f"כיבוי הפורט הזה שובר את המערכת ({off_means}) — "
                             f"יש להקליד בדיוק: {expected}")
        set_enabled(ctx.conn, port_id, want)
        journal(ctx.conn, "port_toggle", f"{port_id} {'on' if want else 'off'}", user[0])
        applied, detail = False, ""
        if port_id == "tftp":
            # ‏#1013: dnsmasq, לא מאזין של התהליך — הקובץ נכתב מחדש והשירות
            # מופעל מחדש; "מאזין" נמדד אחר כך ב-ss כמו בכל שורה.
            if dnsmasq_apply is None:
                detail = ("אין חיבור ל-dnsmasq בתהליך הזה — ההגדרה נשמרה ותיכנס "
                          "לתוקף בכתיבה הבאה של קובץ ה-DHCP")
            else:
                error = dnsmasq_apply(f"tftp {'on' if want else 'off'}", user[0])
                applied, detail = error is None, error or ""
        elif listeners is None:
            detail = ("אין מנהל מאזינים בתהליך הזה — ההגדרה נשמרה ותיכנס "
                      "לתוקף בעלייה הבאה")
        else:
            try:
                listeners.request(port_id, want)
                applied = True
            except (ListenerError, Exception) as exc:         # noqa: BLE001
                detail = str(exc)
        row = row_of(port_id)
        verified = row["listening"] is want
        if not (applied and verified):
            journal(ctx.conn, "port_unverified",
                    f"{port_id} want={'on' if want else 'off'} "
                    f"applied={applied} listening={row['listening']} {detail}".strip(),
                    user[0])
        return {"ok": applied and verified, "enabled": want, "applied": applied,
                "verified": verified, "detail": detail, "port": row}

    return router
