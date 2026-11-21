"""הפצה ישירה במנוע החתיכות (#3161): המקור הוא דיסק במחשב בנייה, והשולח הוא מחשב הבנייה.

עד כאן הפצה ישירה (`direct.py`, #715) רצה רק ב-udpcast: למנוע החתיכות (`chunksend.py`) נחוצים
קובץ ואינדקס בשרת, ובהפצה ישירה האימג' אינו נכתב בשרת (הכרעת נדב, 12/09). כאן השרת נשאר
המתזמר היחיד -- אותו `ChunkEngine`, אותה התייצבות ב-`chunk-pass`, אותו שלב השלמה, אותו
יומן -- ושלושה דברים באים מהמקור:

1. **האינדקס ופריים הראש.** מחשב הבנייה חותך את הדיסק לחתיכות כבר בקריאה הראשונה שלו
   (`chunkcut`, XXH64 לכל 4MiB), ומעלה לכאן, **לפני המניפסט**, את האינדקס של כל מחיצה ואת
   פריים הראש שלה. המניפסט מתקבל רק אם ה-XXH64 של כל אינדקס שהועלה הוא ה-`index_xxh64` שהמניפסט
   חותם עליו (`seal`); המקלטים בודקים את אותו דבר בעצמם, ואת הראש מול האינדקס. מה שנשמר כאן הוא
   מאות KB, לא אימג': `LiveStore`, בתיקייה ליד ה-DB ולעולם לא בספרייה.
2. **השידור.** במקום להריץ `chunkcast send`, המנוע מקבל מ-`runner` "תהליך" מרוחק (`RemoteSend`):
   הזמנה אחת למעבר אחד (מחיצה, מעבר, קצב, רשימת חתיכות). מחשב הבנייה רואה אותה ב-hello שהוא ממילא
   שולח, מריץ `chunkcast send --stdin` על קריאה נוספת של הדיסק, ומדווח את קוד היציאה. מקור
   שהפסיק לדווח הוא מעבר שנכשל, בשמו -- לעולם לא מעבר ש"כנראה הצליח" (עיקרון 5).
3. **ההשלמה.** אין קובץ בשרת. בזמן המעבר כל חתיכה חסרה, גם למחשב אחד, משודרת שוב מהמקור
   (`repair.plan(threshold=1)`), שקורא אותה ממקומה בדיסק ולא את המחיצה כולה. אחרי המעבר -- מגירה
   שחזרה, מחשב שדיווח באיחור -- `fetch` של חתיכות שלמות מועבר מכאן לשרת הפריימים של המקור
   (`file_response`, `_relay`; #3209). השרת מעביר ואינו מחזיק.

המתג "מנוע שידור" כבוי: דבר מכל זה אינו רץ, והפצה ישירה היא udpcast כמו תמיד.
"""

from __future__ import annotations

import http.client
import ipaddress
import json
import logging
import os
import re
import secrets
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, PlainTextResponse, Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from server.agent import readbackhash
from server.core.logtext import one_line
from server.library import chunkformat
from server.library.images import streamed_partitions
from server.rounds import chunklog, direct

log = logging.getLogger("imagesrv.directchunk")

#: במניפסט החי: המקור העלה אינדקס (ולמחיצת partclone גם ראש) לכל מחיצה בזרם, וכולם תאמו למניפסט.
#: מפתח שמתחיל בקו תחתון אינו מוגש למקלטים (`image_manifest`).
MARK = "_chunk_index"
#: תקרות ההעלאה. אינדקס: 36 בתים לחתיכה בערך, כלומר 64MiB הם מחיצה של כ-7TB. ראש: מפת הסיביות
#: של מחיצה בת 2TB עם בלוקים של 4KiB היא 61MB לפני דחיסה.
INDEX_MAX = 64 << 20
HEAD_MAX = 64 << 20
#: כמה העלאות (אינדקס או ראש) נקראות בו בזמן. מקור אחד מעלה אחת אחרי השנייה, וסבב חדר אחד פעיל בכל רגע;
#: בלי התקרה הזו 32 העלאות של 63MiB מחזיקות 2GiB בלי שאף אחת חורגת מהתקרה שלה (סקירת Codex על #3216).
#: הגוף עצמו נכתב לקובץ זמני בתיקיית האחסון, ולא לזיכרון: מה שמוחזק הוא MAX_UPLOADS קבצים של עד 64MiB.
MAX_UPLOADS = 2
SPOOL_PREFIX = ".upload-"
#: כמה מחכים למקור שהפסיק לדווח באמצע מעבר, מעבר ל"המתנה למחשב שהתנתק" (כמו `ENDED_GRACE_S`).
SILENCE_GRACE_S = 150
#: הקוד שהמנוע מקבל ממעבר מרוחק שנכשל. הקוד של `chunkcast` עצמו נמצא בסיבה.
REMOTE_FAILED = 1
#: #3209: כמה מחכים למחשב הבנייה כשהשרת מעביר אליו בקשה לחתיכות בודדות (חיבור, ושתיקה בין שני חלקים).
RELAY_TIMEOUT_S = 30
RELAY_PIECE = 1 << 16
#: סקירת האבטחה של #3216: שרת הפריימים עונה רק למי שמחזיק את האסימון של הסבב, לא למי ש"בא מהכתובת הנכונה".
#: האסימון נולד בשרת, נוסע למחשב הבנייה בתשובה ל-hello, וחוזר בכותרת הזו בכל בקשה שהשרת מעביר.
SERVE_TOKEN_HEADER = "X-Imagesrv-Serve-Token"
_RANGE = re.compile(r"bytes=(\d+)-(\d+)")

_xxh64_new = None


def xxh64_hex(data: bytes) -> str:
    """ה-XXH64 של `data`, כמו `fanout --xxh64` בסוכן (16 ספרות hex)."""
    global _xxh64_new
    if _xxh64_new is None:
        _xxh64_new = readbackhash.xxh64_factory()
    digest = _xxh64_new()
    digest.update(data)
    return digest.hexdigest()


class LiveStore:
    """מה שמחשב הבנייה העלה לאימג' חי: לכל מחיצה אינדקס, ראש, ומציין-מקום בגודל קובץ המחיצה.

    המציין (`p<n>.part`) אינו מחזיק בייט של נתונים -- הוא קובץ דליל באורך `file_zbytes`, כדי
    ש-`chunkformat.read_index` (אותו קורא שבו המנוע בודק אימג' מהספרייה) ימצא אינדקס עקבי לידו.
    השמות נגזרים ממספר המחיצה בלבד: שם קובץ מהמניפסט אינו הופך לנתיב."""

    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, image_id: str) -> Path:
        if not direct.is_live_id(image_id):
            raise ValueError("not a live image id")
        return self.root / image_id

    def part_path(self, image_id: str, part: int) -> Path:
        return self._dir(image_id) / f"p{int(part)}.part"

    def sweep(self, ended: Callable[[str], bool]) -> None:
        """מוחק את התיקיות של אימג'ים חיים שהסבב שלהם **הסתיים במפורש** (`ended(image_id)`), ורק אותן.

        עד הסקירה של #3216 הניקוי היה "כל מה שאינו האימג' של ההעלאה הנוכחית": העלאה של משימה שבוטלה,
        שהגוף שלה הסתיים אחרי שנפתח הסבב הבא, מחקה את האינדקסים של הסבב החדש. מי שמעלה אינו ראיה למי חי."""
        if not self.root.is_dir():
            return
        for entry in self.root.iterdir():
            if direct.is_live_id(entry.name) and ended(entry.name):
                shutil.rmtree(entry, ignore_errors=True)

    def spool(self) -> Path:
        """קובץ זמני להעלאה אחת, בתיקיית האחסון (אותה מערכת קבצים: המעבר למקומו הוא rename)."""
        self.root.mkdir(parents=True, exist_ok=True)
        return self.root / f"{SPOOL_PREFIX}{secrets.token_hex(8)}"

    def clear_spool(self) -> None:
        """בעליית השרת: העלאה שנקטעה באמצע אינה משאירה קובץ. אף העלאה אינה רצה עדיין."""
        if self.root.is_dir():
            for left in self.root.glob(f"{SPOOL_PREFIX}*"):
                left.unlink(missing_ok=True)

    @staticmethod
    def _place(raw: bytes | Path, target: Path) -> None:
        if isinstance(raw, Path):
            os.replace(raw, target)
        else:
            target.write_bytes(raw)

    def put_index(self, image_id: str, part: int, raw: bytes | Path) -> str | None:
        """None = נשמר; אחרת למה לא. האינדקס נבדק כאן מול עצמו (`parse_index`), ומול המניפסט ב-`seal`.
        `raw` הוא הגוף עצמו, או הקובץ הזמני שהוא נכתב אליו (ואז הוא עובר למקומו ב-rename)."""
        try:
            text = raw.read_bytes() if isinstance(raw, Path) else raw
            index = chunkformat.parse_index(text.decode("ascii"))
        except (OSError, UnicodeDecodeError, chunkformat.IndexProblem) as exc:
            return f"partition {part}: not a format-2 chunk index ({exc})"
        del text
        folder = self._dir(image_id)
        placeholder = self.part_path(image_id, part)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            (folder / f"p{part}.head").unlink(missing_ok=True)   # an index again: its head again
            (folder / f"p{part}.b3").unlink(missing_ok=True)     # #3332: and its BLAKE3 list again
            with placeholder.open("wb") as f:
                f.truncate(index.file_zbytes)
            self._place(raw, chunkformat.index_path(placeholder))
        except OSError as exc:
            # a size no file system holds, a full disk: said, and the index is not there (`seal` then refuses)
            chunkformat.index_path(placeholder).unlink(missing_ok=True)
            return f"partition {part}: the server could not keep the chunk index ({type(exc).__name__}: {exc})"
        return None

    def put_b3(self, image_id: str, part: int, raw: bytes | Path) -> str | None:
        """#3332: the partition's BLAKE3 list (chunkcut's `<index>.b3`): 32 bytes for each chunk of its index,
        uploaded after it. Checked here against the index's length, and against the manifest's `chunk_b3` in `seal`."""
        index = self.index(image_id, part)
        if index is None:
            return f"partition {part}: the BLAKE3 list came before its chunk index"
        try:
            size = raw.stat().st_size if isinstance(raw, Path) else len(raw)
            if size != 32 * index.chunks:
                return f"partition {part}: {size} bytes are not the BLAKE3 list of {index.chunks} chunks"
            self._place(raw, self._dir(image_id) / f"p{int(part)}.b3")
        except OSError as exc:
            return f"partition {part}: the server could not keep the BLAKE3 list ({type(exc).__name__}: {exc})"
        return None

    def b3_bytes(self, image_id: str, part: int) -> bytes | None:
        try:
            return (self._dir(image_id) / f"p{int(part)}.b3").read_bytes()
        except (OSError, ValueError):
            return None

    def put_head(self, image_id: str, part: int, raw: bytes | Path) -> str | None:
        index = self.index(image_id, part)
        if index is None:
            return f"partition {part}: the head came before its chunk index"
        try:
            if isinstance(raw, Path):
                size = raw.stat().st_size
                with raw.open("rb") as f:
                    magic = f.read(4)
            else:
                size, magic = len(raw), raw[:4]
            if index.kind != "partclone" or size != index.head_zbytes or magic != chunkformat.ZSTD_MAGIC:
                return (f"partition {part}: {size} bytes are not the head frame its index describes"
                        f" ({index.kind}, {index.head_zbytes} bytes)")
            self._place(raw, self._dir(image_id) / f"p{part}.head")
        except OSError as exc:
            return f"partition {part}: the server could not keep the head frame ({type(exc).__name__}: {exc})"
        return None

    def index_bytes(self, image_id: str, part: int) -> bytes | None:
        try:
            return chunkformat.index_path(self.part_path(image_id, part)).read_bytes()
        except (OSError, ValueError):
            return None

    def index(self, image_id: str, part: int) -> chunkformat.Index | None:
        try:
            return chunkformat.read_index(self.part_path(image_id, part))
        except ValueError:
            return None

    def head(self, image_id: str, part: int) -> bytes | None:
        try:
            return (self._dir(image_id) / f"p{int(part)}.head").read_bytes()
        except (OSError, ValueError):
            return None

    def uploaded(self, image_id: str) -> bool:
        try:
            return any(self._dir(image_id).glob("p*.part.idx"))
        except (OSError, ValueError):
            return False


def seal(store: LiveStore, image_id: str, manifest: dict) -> str | None:
    """נקרא כשהמניפסט החי מגיע. None = מתקבל; אחרת הסיבה שהוא מסורב.

    מקור שלא העלה דבר (המתג כבוי, או סוכן שלפני #3161) אינו מסומן ואינו מסורב: הגל שלו הוא udpcast.
    מקור שהעלה -- **כל** מחיצה בזרם חייבת אינדקס שה-XXH64 שלו הוא ה-`index_xxh64` שבמניפסט, וראש
    כשיש לה מפה. העלאה חלקית אינה "אין העלאה": היא מניפסט שמסורב בשמו. `readback_chunks` -- מספר
    החתיכות שהמנוע והיומן נשענים עליו -- חייב להסכים עם האינדקס כשהמקור דיווח אותו, ונרשם ממנו כשלא."""
    if not store.uploaded(image_id):
        return None
    for part in streamed_partitions(manifest):
        n = part["index"]
        raw, index = store.index_bytes(image_id, n), store.index(image_id, n)
        if raw is None or index is None:
            return f"partition {n}: its chunk index was not uploaded before the manifest"
        if xxh64_hex(raw) != part.get("index_xxh64"):
            return f"partition {n}: the uploaded chunk index is not the one the manifest signs (index_xxh64)"
        if index.kind == "partclone" and store.head(image_id, n) is None:
            return f"partition {n}: its head frame was not uploaded before the manifest"
        # #3332: the receivers write nothing without the BLAKE3 list -- uploaded, and the one the manifest names
        listed = store.b3_bytes(image_id, n)
        if listed is None:
            return f"partition {n}: its BLAKE3 list was not uploaded before the manifest"
        tool = readbackhash.b3_tool()
        if tool is None:
            return f"partition {n}: the server has no chunkcast with b3list -- the BLAKE3 list cannot be checked"
        try:
            got = readbackhash.b3_hex(listed, tool)
        except readbackhash.B3Problem as exc:
            return f"partition {n}: the BLAKE3 list was not checked ({exc})"
        if got != part.get("chunk_b3"):
            return f"partition {n}: the uploaded BLAKE3 list is not the one the manifest names (chunk_b3)"
        told = part.get("readback_chunks")
        if told is not None and (type(told) is not int or told != index.chunks):
            return f"partition {n}: readback_chunks is {told!r}, and its chunk index holds {index.chunks}"
        part["readback_chunks"] = index.chunks
    manifest[MARK] = True
    return None


class LiveLibrary:
    """הספרייה כפי שמנוע החתיכות והיומן שלו קוראים אותה, עם האימג' החי של הפצה ישירה לצידה.

    אימג' חי "נמצא" רק אחרי `seal`: מניפסט בלי הסימן הוא גל של udpcast, ולמנוע אין מה לחפש בו."""

    def __init__(self, library, conn, store: LiveStore):
        self.library, self.conn, self.store = library, conn, store

    def __getattr__(self, name):
        return getattr(self.library, name)

    def get(self, image_id: str) -> dict | None:
        if not direct.is_live_id(image_id):
            return self.library.get(image_id)
        manifest = direct.live_manifest(self.conn, image_id)
        return manifest if manifest is not None and manifest.get(MARK) is True else None

    def file_path(self, image_id: str, filename: str) -> Path | None:
        if not direct.is_live_id(image_id):
            return self.library.file_path(image_id, filename)
        manifest = self.get(image_id)
        part = next((p for p in streamed_partitions(manifest) if p["file"] == filename), None) if manifest else None
        return self.store.part_path(image_id, part["index"]) if part is not None else None


@dataclass
class Order:
    """מעבר אחד שהמנוע ביקש מהמקור."""
    seq: int
    image_id: str
    part: int
    round: int
    kbps: int
    write_kbps: int
    to: str
    key: str
    chunks: str | None              # "A-B,C-D" למעבר השלמה; None = כל המחיצה
    beat: float                     # מתי המקור נשמע לאחרונה (שעון מונוטוני)
    state: str = "pending"          # pending / sending / done / failed / cancelled
    code: int | None = None
    failure: str | None = None
    finished: threading.Event = field(default_factory=threading.Event)

    def public(self) -> dict:
        group, _, port = self.to.rpartition(":")
        return {"seq": self.seq, "part": self.part, "pass": self.round, "kbps": self.kbps,
                "write_kbps": self.write_kbps, "group": group, "port": int(port), "key": self.key,
                "chunks": self.chunks is not None}


class RemoteSend:
    """מה ש-`ChunkEngine` מחזיק במקום `Popen` כשהשולח הוא מחשב הבנייה. אותן ארבע פעולות."""

    pid = None

    def __init__(self, owner: "DirectChunks", order: Order):
        self.owner, self.order = owner, order

    @property
    def failure(self) -> str | None:
        return self.order.failure

    def poll(self) -> int | None:
        return self.order.code if self.order.finished.is_set() else None

    def wait(self, timeout: float | None = None) -> int:
        """קוד היציאה של המעבר. מקור ששתק מעבר לתקרה הוא מעבר שנכשל, ולא המתנה בלי סוף."""
        order = self.order
        deadline = None if timeout is None else self.owner.clock() + timeout
        while not order.finished.wait(self.owner.poll_s):
            now = self.owner.clock()
            ceiling = self.owner.silence()
            if now - order.beat > ceiling:
                self.owner.finish(order, "failed", REMOTE_FAILED, (
                    f"מחשב הבנייה הפסיק לדווח באמצע השידור של מחיצה {order.part} (מעבר {order.round}):"
                    f" {int(ceiling)} שניות בלי דיווח"))
            elif deadline is not None and now > deadline:
                raise TimeoutError
        return order.code if order.code is not None else REMOTE_FAILED

    def terminate(self) -> None:
        self.owner.finish(self.order, "cancelled", -15, None)

    kill = terminate


class DirectChunks:
    """המצב של הפצה ישירה במנוע החתיכות: מה שהמקור העלה, וההזמנה הפתוחה אליו."""

    def __init__(self, conn, root: Path, *, engine_on: Callable[[], bool], silence: Callable[[], float],
                 clock: Callable[[], float] = time.monotonic, poll_s: float = 0.5):
        self.conn, self.store = conn, LiveStore(root)
        self.engine_on, self.silence, self.clock, self.poll_s = engine_on, silence, clock, poll_s
        self.engine = None                      # ה-`ChunkEngine`; `server.app` מחבר אותו אחרי שנבנה
        self.index_max, self.head_max = INDEX_MAX, HEAD_MAX     # תקרות ההעלאה, בבתים
        self.max_uploads = MAX_UPLOADS
        self._uploads = 0                       # העלאות שהגוף שלהן נקרא עכשיו
        #: האימות מחדש של המשימה, הכתיבה והניקוי הם פעולה אחת: סבב שהתחלף ביניהם אינו נמחק ואינו נכתב
        self.store_lock = threading.Lock()
        self._lock = threading.Lock()
        self._order: Order | None = None
        self._seq = 0
        #: אסימון אחד לכל סבב חי (לפי `image_id`): מה ששרתי הפריימים של מחשב הבנייה דורשים. בזיכרון בלבד --
        #: אתחול של השרת מכשיל את הסבב ממילא (#3094), ואסימון ישן אינו חוזר לאף סבב.
        self._serve_tokens: dict[str, str] = {}
        self.store.clear_spool()
        self.store.sweep(self.round_ended)

    # --- ההעלאות של המקור -------------------------------------------------------

    def round_ended(self, image_id: str) -> bool:
        """הסבב של האימג' החי הזה הסתיים במפורש: יש לו שורה, והיא אינה `active` -- או שאין לו שורה כלל.
        בלי DB (בדיקות שבונות את המצב ביד) אין ראיה שמשהו הסתיים, ולכן דבר אינו נמחק."""
        if self.conn is None:
            return False
        row = self.conn.execute("SELECT state FROM room_rounds WHERE image_id = ? ORDER BY created_at DESC LIMIT 1",
                                (image_id,)).fetchone()
        return row is None or row["state"] != "active"

    def still_open(self, task_id: str) -> bool:
        """המשימה עדיין פתוחה והסבב שלה עדיין פעיל -- **עכשיו**, לא כשהבקשה התחילה. גוף של העלאה יכול
        להיקרא דקות, ובינתיים המפעיל ביטל את הסבב ופתח אחר."""
        row = self.conn.execute(
            "SELECT t.state AS task, r.state AS round FROM tasks t LEFT JOIN room_rounds r"
            " ON r.source_task_id = t.id WHERE t.id = ?", (task_id,)).fetchone()
        return row is not None and row["task"] in ("pending", "running") and row["round"] == "active"

    def begin_upload(self) -> bool:
        """False = כבר נקראות `max_uploads` העלאות, והגוף של זו אינו נקרא."""
        with self._lock:
            if self._uploads >= self.max_uploads:
                return False
            self._uploads += 1
            return True

    def end_upload(self) -> None:
        with self._lock:
            self._uploads -= 1

    # --- מי משדר --------------------------------------------------------------

    def wants(self, session_id: str) -> bool:
        """הגל הזה הוא הפצה ישירה שיוצאת במנוע החתיכות: המתג דלוק, והמקור העלה אינדקס לכל מחיצה."""
        if not self.engine_on() or not direct.is_direct_wave(self.conn, session_id):
            return False
        row = self.conn.execute("SELECT image_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        manifest = direct.live_manifest(self.conn, row["image_id"]) if row is not None else None
        return manifest is not None and manifest.get(MARK) is True

    def refusal(self, session_id: str) -> str | None:
        """למה גל ישיר אינו יוצא כשהמתג דלוק והמקור לא העלה אינדקסים. אין נפילה שקטה ל-udpcast."""
        if not self.engine_on() or self.wants(session_id):
            return None
        return ("המתג 'מנוע שידור' דלוק, ומחשב הבנייה לא העלה אינדקס חתיכות למחיצות שלו — הסוכן שלו ישן,"
                " או שהמתג הודלק אחרי שהקריאה התחילה. יש לעדכן את אימג' הסוכן או לכבות את המתג,"
                " ולהפיץ שוב. דבר לא שודר")

    def runner(self, inner: Callable[[list[str]], object] | None) -> Callable[[list[str]], object]:
        """ה-`runner` של המנוע: פקודה על קובץ של אימג' חי הופכת להזמנה למקור; כל פקודה אחרת רצה כאן.

        **ברירת המחדל של "כאן" היא `chunklog.run_process`**, התהליך שמנוע החתיכות כותב בו את הלוג שהוא עצמו קורא
        (`chunklog.log_path`). בייצור `inner` הוא `None`, ובעבר זו נפלה ל-`udpcast.run_process` (השולח הישן):
        הפלט של `chunkcast send` נכתב ל-`partition_log` והמנוע קרא קובץ אחר (#3340)."""
        local = inner or chunklog.run_process

        def run(cmd: list[str]):
            path = Path(cmd[cmd.index("--file") + 1])
            if path.parent.parent != self.store.root:
                return local(cmd)
            return RemoteSend(self, self._issue(path.parent.name, cmd))

        return run

    def _issue(self, image_id: str, cmd: list[str]) -> Order:
        def arg(name: str) -> str:
            return cmd[cmd.index(name) + 1]

        chunks = None
        if "--chunks-file" in cmd:              # the engine removes the file after the pass: read now
            chunks = Path(arg("--chunks-file")).read_text(encoding="ascii").strip()
        with self._lock:
            self._seq += 1
            order = self._order = Order(self._seq, image_id, int(arg("--part")), int(arg("--pass")),
                                        int(arg("--kbps")), int(arg("--write-kbps")), arg("--to"),
                                        arg("--session"), chunks, self.clock())
        return order

    def finish(self, order: Order, state: str, code: int, failure: str | None) -> None:
        with self._lock:
            if order.finished.is_set():
                return
            order.state, order.code, order.failure = state, code, failure
            order.finished.set()

    # --- מה שהמקור רואה ומדווח ---------------------------------------------------

    def task_fields(self, task: dict, block: dict) -> dict:
        """מה שנוסף ל-`task.direct` של מחשב הבנייה (ממשק 3).

        לפני המניפסט: `engine` לבדו -- "העלה אינדקסים". אחריו, לגל של המנוע: גם `chunk` עם
        ההזמנה הפתוחה (`order`) ועם `done` כשהמנוע סיים את כל המחיצות."""
        image_id, session_id = task.get("image_id"), block.get("session_id")
        if not direct.is_live_id(image_id) or self.engine is None:
            return {}
        # a wave the engine already runs stays its wave: a switch turned off in the middle changes the NEXT one
        status = self.engine.status()
        ours = session_id is not None and status is not None and status["session_id"] == session_id
        if not ours:
            if not self.engine_on():
                return {}
            if direct.live_manifest(self.conn, image_id) is None:
                return {"engine": "chunk"}
            if session_id is None or not self.wants(session_id):
                return {}
        return {"engine": "chunk", "chunk": {
            # #3209: the source's frame servers listen on this port + the partition's index (TCP)
            "serve_port": self.serve_port(),
            # the frame servers answer only a request that carries it (the review of #3216). Not a log line, not an argv
            "serve_token": self.serve_token(image_id),
            "order": self.order_for(image_id),
            "done": bool(ours and status["state"] == "done"),
            "error": status["error"] if ours else None}}

    def order_for(self, image_id: str) -> dict | None:
        """ההזמנה הפתוחה של האימג' החי, כפי שהמקור רואה אותה -- או None. מי ששואל חי: השאלה היא דופק."""
        with self._lock:
            order = self._order
            if order is None or order.image_id != image_id or order.finished.is_set():
                return None
            order.beat = self.clock()
            return order.public()

    def serve_port(self) -> int:
        """הבסיס של שרתי הפריימים במחשב הבנייה (`chunkcast serve`, פורט לכל מחיצה): אחרי הפורט של המנוע."""
        return self.engine.port + 1

    def serve_token(self, image_id: str) -> str:
        """האסימון של הסבב של האימג' החי הזה: אקראי, נוצר בבקשה הראשונה, ואותו אסימון לכל ה-hello של הסבב.
        אסימונים של סבבים שהסתיימו נזרקים כאן, כדי שהמילון אינו גדל."""
        stale = [k for k in list(self._serve_tokens) if k != image_id and self.round_ended(k)]
        with self._lock:
            for k in stale:
                self._serve_tokens.pop(k, None)
            return self._serve_tokens.setdefault(image_id, secrets.token_hex(16))

    def source_addr(self, image_id: str, part: int) -> tuple[str, int] | None:
        """(כתובת, פורט) של שרת הפריימים של המחיצה במחשב הבנייה -- רק כשהסבב של האימג' עדיין פעיל,
        והכתובת היא זו שה-hello האחרון של המקור הגיע ממנה. None = אין ממי לבקש."""
        row = self.conn.execute(
            "SELECT n.ip FROM room_rounds r JOIN net_devices n ON n.mac = r.source_mac"
            " WHERE r.image_id = ? AND r.state = 'active' ORDER BY r.created_at DESC LIMIT 1", (image_id,)).fetchone()
        try:
            return (str(ipaddress.ip_address(row["ip"])), self.serve_port() + int(part)) if row is not None else None
        except (TypeError, ValueError):
            return None

    def chunks_of(self, image_id: str, seq: int) -> str | None:
        with self._lock:
            order = self._order
            return (order.chunks if order is not None and order.image_id == image_id
                    and order.seq == seq and not order.finished.is_set() else None)

    def sent(self, image_id: str, body: dict) -> dict:
        """דיווח המקור על מעבר: `sending` (דופק), `done` או `failed`. הזמנה שאינה הפתוחה: `stale`."""
        seq, state = body.get("seq"), body.get("state")
        if type(seq) is not int or state not in ("sending", "done", "failed"):
            raise ValueError("seq and state (sending, done, failed) are required")
        with self._lock:
            order = self._order
            if order is None or order.image_id != image_id or order.seq != seq or order.finished.is_set():
                return {"ok": True, "stale": True}
            order.beat = self.clock()
            if state == "sending":
                order.state = "sending"
                return {"ok": True}
        code = body.get("rc")
        code = code if type(code) is int else REMOTE_FAILED
        if state == "done" and code == 0:
            self.finish(order, "done", 0, None)
        else:
            why = body.get("error")
            why = why.strip()[:300] if isinstance(why, str) and why.strip() else "מחשב הבנייה לא נתן סיבה"
            self.finish(order, "failed", REMOTE_FAILED,
                        f"השידור ממחשב הבנייה נכשל במחיצה {order.part} (מעבר {order.round}, קוד {code}): {why}")
        return {"ok": True}

    # --- מה שהמקלטים מורידים -----------------------------------------------------

    def _sealed(self, image_id: str) -> dict | None:
        manifest = direct.live_manifest(self.conn, image_id)
        return manifest if manifest is not None and manifest.get(MARK) is True else None

    def b3_response(self, image_id: str, part: int) -> Response:
        """#3332: the BLAKE3 list the source uploaded, once its manifest named it (`seal`)."""
        raw = self.store.b3_bytes(image_id, part) if self._sealed(image_id) is not None else None
        if raw is None:
            return _error(404, "no BLAKE3 list for this partition", "no_b3")
        return Response(content=raw, media_type="application/octet-stream")

    def index_response(self, image_id: str, part: int) -> Response:
        raw = self.store.index_bytes(image_id, part) if self._sealed(image_id) is not None else None
        if raw is None:
            return _error(404, "no chunk index for this partition", "no_index")
        return PlainTextResponse(raw)

    def file_response(self, image_id: str, filename: str, wanted: str | None) -> Response:
        """הקובץ של מחיצה חיה אינו בשרת. פריים הראש כן (המקור העלה אותו), ומוגש מכאן. טווח של **חתיכות
        שלמות** מועבר למחשב הבנייה, שקורא אותן מהדיסק שלו (#3209): כך `fetch` של מגירה שחזרה, או של מחשב
        שדיווח אחרי שהמחיצה נסגרה, מושלם גם בהפצה ישירה. כל בקשה אחרת נענית 404 בשמה, ולעולם לא באפסים."""
        manifest = self._sealed(image_id)
        part = (next((p for p in streamed_partitions(manifest) if p["file"] == filename), None)
                if manifest is not None else None)
        found = _RANGE.fullmatch(wanted.strip()) if isinstance(wanted, str) else None
        index = self.store.index(image_id, part["index"]) if part is not None else None
        if index is None or found is None or int(found[1]) > int(found[2]):
            return _error(404, "a direct send keeps no partition file on the server -- only its head frame, and"
                               " whole chunks by an exact Range, which the build machine reads", "no_file")
        first, last = int(found[1]), int(found[2])
        if last < index.head_zbytes:
            head = self.store.head(image_id, part["index"])
            if head is None:
                return _error(404, "the head frame of this partition is not on the server", "no_file")
            return Response(head[first:last + 1], status_code=206, media_type="application/octet-stream",
                            headers={"Content-Range": f"bytes {first}-{last}/{index.file_zbytes}"})
        starts = {offset for offset, _zbytes, _xxh in index.rows}
        ends = {offset + zbytes - 1 for offset, zbytes, _xxh in index.rows}
        if first not in starts or last not in ends:
            return _error(404, "this Range is not whole chunks of the partition's index", "no_file")
        return self._relay(image_id, part["index"], first, last, index.file_zbytes)

    def _relay(self, image_id: str, part: int, first: int, last: int, whole: int) -> Response:
        """הבקשה כמו שהיא, אל שרת הפריימים של המקור, והתשובה כמו שהיא חזרה -- בחלקים, לא בזיכרון. המקלט בודק כל
        חתיכה מול האינדקס בעצמו, ולכן מה שעובר כאן אינו מאומן ואינו צריך להיות. מקור שאינו עונה: 502 בשמו."""
        where = self.source_addr(image_id, part)
        if where is None:
            return _error(502, "the build machine of this direct send is not there to read the chunks from",
                          "source_unreachable")
        link = http.client.HTTPConnection(where[0], where[1], timeout=RELAY_TIMEOUT_S)
        try:
            link.request("GET", "/", headers={"Range": f"bytes={first}-{last}",
                                              SERVE_TOKEN_HEADER: self.serve_token(image_id)})
            answer = link.getresponse()
            length = int(answer.getheader("Content-Length") or -1)
        except (OSError, ValueError, http.client.HTTPException) as exc:
            link.close()
            # #3891: the exception's text goes to the server log, the answer is our sentence (CodeQL py/stack-trace-exposure)
            log.warning("direct chunks of %s: the build machine did not give them: %s", one_line(image_id),
                        one_line(f"{type(exc).__name__}: {exc}"))
            return _error(502, "the build machine did not give the chunks -- the details are in the server log",
                          "source_unreachable")
        if answer.status != 206 or length != last - first + 1:
            link.close()
            return _error(502, f"the build machine answered {answer.status} with {length} bytes for"
                               f" {last - first + 1}", "source_refused")

        def pieces():
            try:
                while piece := answer.read(RELAY_PIECE):
                    yield piece
            finally:
                link.close()

        return StreamingResponse(pieces(), status_code=206, media_type="application/octet-stream",
                                 headers={"Content-Range": f"bytes {first}-{last}/{whole}",
                                          "Content-Length": str(length)})


def _error(status: int, message: str, code: str) -> JSONResponse:
    return JSONResponse({"ok": False, "error": message, "code": code}, status_code=status)


async def _capped(request: Request, limit: int) -> bytes | None:
    """גוף קטן (דיווח של מעבר), בזיכרון -- או None כשהוא ארוך מהתקרה (נעצר שם, ואינו נקרא עד סופו)."""
    got = bytearray()
    async for piece in request.stream():
        got += piece
        if len(got) > limit:
            return None
    return bytes(got)


async def _spooled(request: Request, limit: int, path: Path) -> bool:
    """גוף של העלאה, לקובץ `path` ולא לזיכרון. False = ארוך מהתקרה: נעצר שם, והקורא מוחק את הקובץ."""
    size = 0
    with path.open("wb") as out:
        async for piece in request.stream():
            size += len(piece)
            if size > limit:
                return False
            out.write(piece)
    return True


def add_routes(router, ctx, task_for, fail) -> None:
    """הנתיבים של המקור, על ה-router של `direct.py`: אותו אסימון משימה ואותו שער זהות כמו המניפסט."""

    def gate(task_id: str, request: Request, where: str):
        row = task_for(task_id, request)
        from server.agent.api import identity_gate   # noqa: PLC0415 — api imports direct
        refused = identity_gate(ctx, row["mac"], request, where)
        live = getattr(ctx, "direct_chunk", None)
        if refused is None and live is None:
            refused = _error(404, "this server has no chunk engine for a direct send", "no_engine")
        return row, live, refused

    async def upload(task_id: str, part: int, request: Request, what: str):
        row, live, refused = await run_in_threadpool(gate, task_id, request, f"direct-{what}")
        if refused is not None:
            return refused
        # the body is read only by a holder of a slot, and into a file: neither one upload nor many hold memory
        if not live.begin_upload():
            return _error(429, f"{live.max_uploads} uploads are being read already -- try again", "busy")
        spooled = live.store.spool()
        try:
            limit = live.index_max if what in ("index", "b3") else live.head_max
            if not await _spooled(request, limit, spooled):
                return _error(413, f"the {what} is larger than {limit} bytes", "too_large")
            return await run_in_threadpool(_store, task_id, row, live, part, spooled, what)
        finally:
            live.end_upload()
            spooled.unlink(missing_ok=True)     # kept = moved to its place already; anything else is not left behind

    def _store(task_id: str, row: dict, live: "DirectChunks", part: int, spooled: Path, what: str):
        with live.store_lock:
            # the task was open when the request began; its body may have taken minutes, and the round may be another's now
            if not live.still_open(task_id):
                return _error(409, "the task of this upload is no longer open -- nothing was kept", "task_closed")
            if direct.live_manifest(ctx.conn, row["image_id"]) is not None:
                return _error(409, "the manifest of this task was already accepted: it signs what was uploaded"
                                   " before it", "sealed")
            live.store.sweep(live.round_ended)  # only rounds that ended -- never "whatever is not this upload's"
            put = {"index": live.store.put_index, "b3": live.store.put_b3}.get(what, live.store.put_head)
            problem = put(row["image_id"], part, spooled)
        if problem:
            fail(task_id, problem, row["mac"])
            raise HTTPException(400, problem)
        return {"ok": True}

    @router.put("/{task_id}/index/{part}")
    async def put_index(task_id: str, part: int, request: Request):
        return await upload(task_id, part, request, "index")

    @router.put("/{task_id}/b3/{part}")
    async def put_b3(task_id: str, part: int, request: Request):
        return await upload(task_id, part, request, "b3")      # #3332

    @router.put("/{task_id}/head/{part}")
    async def put_head(task_id: str, part: int, request: Request):
        return await upload(task_id, part, request, "head")

    @router.get("/{task_id}/chunks/{seq}")
    def get_chunks(task_id: str, seq: int, request: Request):
        row, live, refused = gate(task_id, request, "direct-chunks")
        if refused is not None:
            return refused
        text = live.chunks_of(row["image_id"], seq)
        if text is None:
            return _error(404, "no open repair pass with this number", "no_order")
        return PlainTextResponse(text + "\n")

    @router.post("/{task_id}/chunk-sent")
    async def chunk_sent(task_id: str, request: Request):
        row, live, refused = await run_in_threadpool(gate, task_id, request, "direct-chunk-sent")
        if refused is not None:
            return refused
        raw = await _capped(request, 65536)
        try:
            body = json.loads(raw.decode("utf-8")) if raw is not None else None
            if not isinstance(body, dict):
                raise ValueError("the body is not a JSON object")
            # the engine's thread takes the verdict from here and fails the wave by name (`send_failed`)
            return live.sent(row["image_id"], body)
        except (ValueError, UnicodeDecodeError, RecursionError) as exc:
            # #3891: json's text (or ours) to the server log; the answer says what was expected (CodeQL py/stack-trace-exposure)
            log.warning("direct chunk-sent refused: %s", one_line(exc))
            return _error(400, "the body is not a chunk-sent report (an object with seq and state) -- the details are in"
                               " the server log", "bad_chunk_sent")
