"""הגדרת הלוגינג של השרת, וגשר האזהרות ליומן הקונסולה (#3157, אפשרות ג').

**שלוש שכבות לפי הצרכן** (הכרעת נדב, 14/11):

* **חקירה** -- `configure`: הגדרה **אחת** בעליית השרת. ה-root ב-INFO ל-stderr,
  ומשם ל-journald (היחידה שולחת אליו). עד כאן ה-root היה WARNING בלי handler,
  ו-``log.info`` של כל המודולים נשמט (#3157). **לוגר ``imagesrv.*`` אינו מקבל
  handler משלו**: השורה הייתה מופיעה פעמיים (`tests/test_serverlog_3157.py`).
* **המפעיל** -- `JournalBridge`: כל WARNING ומעלה של מודולי השרת נכנס ליומן
  הקונסולה כאירוע אחד, עם המקור: ``server_warning`` (כתום) או ``server_error``
  (אדום, ולא נמחק לעולם -- `housekeeping`). INFO והמדידות **לא** נכנסים למסך.
* **המוח** -- טבלה מובנית משלו (#3185). לא כאן.

**הגנה מהצפה.** אותה אזהרה (אותו לוגר, אותה רמה, אותה **תבנית** -- ``record.msg``
לפני הארגומנטים) שחוזרת בתוך `GROUP_QUIET_S` מהפעם הקודמת אינה שורה חדשה: היא
נספרת, והמונה נכתב לאותה שורה (``[+N identical in Ss]``) לכל היותר כל `FLUSH_S`.
קבוצה נסגרת אחרי `GROUP_MAX_S`, כדי שהצפה שנמשכת שעות לא תיקבר בשורה ישנה.
מעבר לכך, לכל היותר `NEW_ROWS_PER_HOUR` שורות חדשות בשעה; השאר נספרות, ושורת
``journal_flood`` אומרת כמה (עיקרון 5: מה שלא נרשם אחד-אחד נספר, לא נעלם).

**למה תהליכון ותור.** `emit` נקרא מכל מקום בשרת, גם מתוך טרנזאקציה פתוחה או
מתחת ל-`_write_lock`. כתיבה ישירה משם הייתה ננעלת או מערבבת טרנזאקציות; לכן
`emit` רק מכניס לתור, ותהליכון אחד כותב. מה שהתהליכון עצמו רושם (למשל מחיצה
מלאה ב-`db.journal`) אינו חוזר לתור -- אחרת לולאה.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass
from typing import Callable

from server import db

log = logging.getLogger("imagesrv.serverlog")

WARNING_EVENT = "server_warning"
ERROR_EVENT = "server_error"
FLOOD_EVENT = "journal_flood"   # #2498: אותו אירוע ואותו נוסח כמו ההצפה של הסוכן
#: חזרה בתוך החלון הזה מהפעם הקודמת = אותה שורה.
GROUP_QUIET_S = 300
#: שורה אחת מקבצת לכל היותר שעה; אחר כך שורה חדשה.
GROUP_MAX_S = 3600
#: המונה נכתב לשורה לכל היותר פעם בזמן הזה.
FLUSH_S = 30
#: תקרת השורות החדשות בשעה, לכל התבניות יחד.
NEW_ROWS_PER_HOUR = 120
BUDGET_WINDOW_S = 3600
DETAIL_MAX = 500
QUEUE_MAX = 1000
_FORMAT = "%(levelname)s %(name)s: %(message)s"


def ours(name: str) -> bool:
    """לוגר של מודול שרת: ``imagesrv.*`` או ``server.*`` (`getLogger(__name__)`)."""
    return name == "imagesrv" or name.startswith(("imagesrv.", "server."))


class _LiveStderr(logging.StreamHandler):
    """כותב ל-``sys.stderr`` **של הרגע** (כמו ``logging.lastResort``): בטסטים
    stderr מוחלף, ו-handler שאחז בישן כותב לקובץ סגור."""

    def __init__(self) -> None:
        logging.Handler.__init__(self)

    @property
    def stream(self):  # type: ignore[override]
        return sys.stderr


def configure() -> None:
    """ה-root ב-INFO, handler אחד ל-stderr. אידמפוטנטי."""
    root = logging.getLogger()
    if not any(isinstance(h, _LiveStderr) for h in root.handlers):
        handler = _LiveStderr()
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    root.setLevel(logging.INFO)


@dataclass
class _Group:
    row_id: int
    detail: str
    first: float
    last: float
    count: int = 0      # חזרות אחרי השורה הראשונה
    written: int = 0    # כמה מהן כבר במונה שבשורה
    flushed: float = 0.0


def _detail(record: logging.LogRecord) -> str:
    source = record.name.removeprefix("imagesrv.")
    text = record.getMessage()
    if record.exc_info and record.exc_info[1] is not None:
        exc = record.exc_info[1]
        text += f" -- {type(exc).__name__}: {exc}"
    detail = f"{source}: {text}"
    return detail if len(detail) <= DETAIL_MAX else detail[:DETAIL_MAX - 1] + "…"


class JournalBridge(logging.Handler):
    """WARNING ומעלה של מודולי השרת → `db.journal`. ``start=False`` = בלי
    תהליכון; הטסטים קוראים ל-`take`/`flush_counts` עם שעון משלהם."""

    def __init__(self, conn, *, clock: Callable[[], float] = time.monotonic,
                 start: bool = True) -> None:
        super().__init__(logging.WARNING)
        self._conn = conn
        self._clock = clock
        self._queue: queue.Queue = queue.Queue(QUEUE_MAX)
        self._groups: dict[tuple, _Group] = {}
        self._budget = [clock(), 0, 0]   # [תחילת החלון, שורות חדשות, נספרו בלבד]
        self._lost = 0                   # התור היה מלא
        self._lost_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run, daemon=True, name="journal-bridge")
            self._thread.start()

    # ---- הצד של הכותבים: רק תור ----
    def emit(self, record: logging.LogRecord) -> None:
        if not ours(record.name) or threading.current_thread() is self._thread:
            return
        try:
            key = (record.name, record.levelno >= logging.ERROR, str(record.msg))
            self._queue.put_nowait((key, _detail(record)))
        except queue.Full:
            with self._lost_lock:
                self._lost += 1
        except Exception:   # noqa: BLE001 -- handler אינו מפיל את מי שרשם
            self.handleError(record)

    def close(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            self._queue.put(None)
            self._thread.join(timeout=5)
        super().close()

    def _run(self) -> None:
        while True:
            try:
                item = self._queue.get(timeout=FLUSH_S / 3)
            except queue.Empty:
                item = ()
            try:
                if item is None:
                    self.flush_counts(final=True)
                    return
                if item:
                    self.take(*item)
                self.flush_counts()
            except Exception:   # noqa: BLE001 -- התהליכון חי; הכשל ב-stderr (journald)
                log.exception("journal bridge: write failed")

    # ---- הצד של התהליכון ----
    def take(self, key: tuple, detail: str) -> None:
        now = self._clock()
        group = self._groups.get(key)
        if group is not None and now - group.last < GROUP_QUIET_S and now - group.first < GROUP_MAX_S:
            group.count += 1
            group.last = now
            return
        if group is not None:
            self._write_count(group)
            del self._groups[key]
        self._roll_budget(now)
        if self._budget[1] >= NEW_ROWS_PER_HOUR:
            self._budget[2] += 1
            if self._budget[2] == 1:
                db.journal(self._conn, FLOOD_EVENT,
                           f"{WARNING_EVENT}: over {NEW_ROWS_PER_HOUR} different in {BUDGET_WINDOW_S}s"
                           " -- the rest are counted, not journaled one by one")
            return
        self._budget[1] += 1
        row_id = db.journal(self._conn, ERROR_EVENT if key[1] else WARNING_EVENT, detail)
        if row_id is not None:
            self._groups[key] = _Group(row_id, detail, first=now, last=now, flushed=now)

    def flush_counts(self, final: bool = False) -> None:
        """לא `flush` של `logging.Handler`: את זה `logging.shutdown` קורא מהתהליכון
        הראשי, במקביל לתהליכון הכותב."""
        now = self._clock()
        for key, group in list(self._groups.items()):
            closed = final or now - group.last >= GROUP_QUIET_S or now - group.first >= GROUP_MAX_S
            if closed or now - group.flushed >= FLUSH_S:
                self._write_count(group)
                group.flushed = now
            if closed:
                del self._groups[key]
        self._roll_budget(now, final=final)
        with self._lost_lock:
            lost, self._lost = self._lost, 0
        if lost:
            db.journal(self._conn, FLOOD_EVENT,
                       f"{WARNING_EVENT}: +{lost} lost, the queue was full")

    def _write_count(self, group: _Group) -> None:
        if group.count == group.written:
            return
        span = int(group.last - group.first)
        db.journal_detail(self._conn, group.row_id,
                          f"{group.detail} [+{group.count} identical in {span}s]")
        group.written = group.count

    def _roll_budget(self, now: float, final: bool = False) -> None:
        start, _used, dropped = self._budget
        if not final and now - start < BUDGET_WINDOW_S:
            return
        if dropped:
            db.journal(self._conn, FLOOD_EVENT,
                       f"{WARNING_EVENT}: +{dropped} more different not journaled one by one"
                       f" in the last {int(now - start)}s")
        self._budget = [now, 0, 0]


def bridge_to_journal(conn) -> JournalBridge:
    """מחבר את הגשר ל-root (פעם אחת). נקרא מ-`server.main` אחרי שה-DB קיים."""
    root = logging.getLogger()
    for handler in root.handlers:
        if isinstance(handler, JournalBridge):
            return handler
    bridge = JournalBridge(conn)
    root.addHandler(bridge)
    return bridge
