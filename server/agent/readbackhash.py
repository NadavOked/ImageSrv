"""הגיבוב לקריאה החוזרת מהדיסק (#1805, CLR-01).

עד #1805 מגירה הייתה `done` לפי ראיה מה**זרם** — ה-sha256 של הבייטים הדחוסים
לפני zstd ו-partclone — ואיש לא קרא את הדיסק. עכשיו הסוכן קורא בחזרה מכל
מחיצה שכתב את הבלוקים שבשימוש (O_DIRECT, `fanout --readback`) ומשווה
לגיבוב **שאינו הזרם**: זה, שהשרת לוקח מהאימג' עצמו כשהוא בספרייה.

**מה מגובב** — "זרם הנתונים": תוכן הבלוקים שבשימוש, לפי סדר הבלוקים, בדיוק
כפי ש-`partclone -r` כותב אותם לדיסק: הזרם הפרוס של המחיצה (`zstd -dc`)
בלי כותרת ה-`image_desc_v2` (110 בייט), בלי מפת הסיביות (וה-CRC-32 שלה) ובלי
סכומי הבדיקה שבין הבלוקים (`checksum_size` בייט אחרי כל `blocks_per_checksum`
בלוקים, ועוד אחד לשארית — `src/main.c` של partclone). מחיצה שנקלטה ב-
`partclone.dd` (אין כותרת) — הזרם הפרוס כולו.

**הכרעת נדב 20/10 (#1864, sha256 של הכול עלה ~8 דק' לסבב של 4 מגירות):** הזרם
נחתך ל-`CHUNK_BYTES` (4MiB), ולכל חתיכה XXH64 (זרע 0) — שורה של 16 ספרות hex.
הרשימה נשמרת **ליד** קובץ המחיצה (`<file>.readback`, `GET …/readback/<index>`),
והמניפסט נושא `readback_xxh64` (ה-XXH64 של קובץ הרשימה עצמו), `readback_chunks`,
`readback_bytes` ו-`readback_chunk_bytes`. הסוכן משווה חתיכה מול חתיכה: מדגם
(ברירת המחדל) או כולן (`readback_full` בהגדרות). XXH64 בקרנל (AF_ALG
`xxhash64`), ובלעדיו — מימוש Python מבוקר (איטי; בקול, ביומן). `readback_sha256`
של מניפסטים קודמים נשאר כמו שהוא (הסוכן עוד מכיר אותו), ואינו מחושב עוד.

**ראיה חיובית בכל שלב** (עיקרון 5): magic/גרסה/endianess/CRC של הכותרת,
CRC-32 של המפה, ספירת הסיביות שווה ל-`usedblocks`, אורך קטע הנתונים שווה
בדיוק למה שהכותרת מצהירה (EOF במקום הצפוי, לא לפניו ולא אחריו), ו-zstd
שיצא 0 (הוא בודק את סכום המסגרת). כל אחד מהם שלא עבר — **אין ערך** במניפסט,
והסוכן מדווח על המחיצה "לא אומת" בשמה; לעולם לא ערך מנוחש.

נכתב למניפסט עצמו (עיקרון 3: הסוכן קורא את המניפסט שהשרת מגיש), ליד
המחיצה: `readback_xxh64` ו-`readback_bytes`. `ImageLibrary.record_index`.

**מתי:** תהליכון רקע אחד (`run_forever`) — בעליית השרת על כל הספרייה (כולל
אימג'ים שנקלטו לפני #1805), ומיד כשאימג' נכנס לספרייה (`kick()` מסוף אימות
הקליטה), ולפחות פעם ב-`INTERVAL_S` (העלאה, ייבוא, סנכרון משני). אימג' ישן
שעוד לא גובב נפרס בינתיים בלי ערך — והסוכן אומר "לא אומת", לא "תקין".

**#2533 — אימג' חתיכות (`image_format` 2):** הרשימה אינה מיוצרת כאן עוד. הסוכן
שקולט חותך את הזרם לפריימים עצמאיים, בונה **אינדקס** (היסט, אורך ו-XXH64 לכל
חתיכה) וחותם עליו במניפסט (`index_xxh64`). התהליכון **גוזר את אותו אינדקס
מהקובץ שבספרייה** (`server/library/chunkformat.py`) ומשווה: תואם — האינדקס נכתב
ליד קובץ המחיצה (`<file>.idx`), ושדות ה-`readback_*` נרשמים מתוכו; אינו תואם —
`index_error` במניפסט, והאימג' אינו מופץ (`images.format_problem`). קובץ
`.readback` נפרד אינו קיים עוד: הרשימה שהסוכן מושך היא העמודה השלישית של
האינדקס. אימג' בפורמט ישן אינו מופץ, ולכן אינו מגובב.

**#3332 — רשימת BLAKE3:** XXH64 מוצא חתיכה פגומה ואינו עוצר חתיכה שנבנתה בכוונה
(XXH64 אינו קריפטוגרפי). באותה פריסה של הקובץ זרם הנתונים עובר גם ל-`chunkcast b3list`
(`B3Lister`), ונוצרת רשימת BLAKE3 -- 32 בתים לחתיכה. היא נכתבת ליד קובץ המחיצה
(`<file>.b3`, `GET …/b3/<index>`), וה-BLAKE3 של הרשימה עצמה נרשם במניפסט
(`chunk_b3`). הסוכן בודק בה כל חתיכה לפני כתיבה, ו**אינו כותב בלעדיה** (נדב 15/11): אימג' קיים
מקבל אותה בגזירה חוזרת ברקע, וגל של אימג' בלי רשימה ממתין לה (`server/rounds/indexprep.py`).
`chunkcast` שאינו מותקן בשרת (או ישן, בלי `b3list`) -- אין רשימה, היומן אומר זאת פעם אחת
(`b3_tool`), וגל של מנוע החתיכות אינו יוצא, בשמו.

הרצה יבשה לאימות במעבדה: `python -m server.agent.readbackhash <תיקיית-אימג'>`.
"""

from __future__ import annotations

import json
import logging
import shutil
import socket
import struct
import subprocess
import sys
import threading
import time
import zlib
from pathlib import Path

from server.db import get_setting
from server.library.images import (_whole, carries_a_stream_file, format_problem, guarded_image_path,
                                   has_index_xxh64)
from server.library.usedbackfill import HEADER_SIZE, captured_raw, parse_header

log = logging.getLogger("imagesrv.readbackhash")

CHUNK = 4 * 1024 * 1024      # how much is read at a time
CHUNK_BYTES = 4 * 1024 * 1024  # a chunk of the data stream -- fanout.c RB_CHUNK, the same number
STALL_S = 300.0          # zstd שלא נתן בייט כל הזמן הזה (מיקום רשת שנפל) — נהרג
INTERVAL_S = 600.0       # מעבר על הספרייה לפחות פעם בעשר דקות
_BM_BIT = 1              # partclone.h: BM_BIT = 0x01
MAX_BITMAP = 1 << 31     # bytes -- fanout.c's own ceiling (readback())
MAX_BLOCK = 64 << 20     # bytes -- likewise
DECOMPRESS = ("zstd", "-dc", "--")
#: ברזל 20/10 (#1864): הגיבוב רץ עם הסבב והשידור הואט ~20%. עכשיו בעדיפות
#: הכי נמוכה של המעבד ושל הדיסק — ובנוסף נעצר כשיש עבודה (`busy`, למטה).
IDLE_PRIORITY = ("nice", "-n", "19", "ionice", "-c", "3")
PAUSE_RETRY_S = 30.0     # הושהה בגלל עבודה פעילה: בודקים שוב בעוד חצי דקה, לא בעוד עשר
STOP_EVERY_S = 2.0       # כל כמה זמן, תוך כדי קריאה, שואלים אם צריך לפנות את השרת


#: #1864 (נדב 20/10): מתג בהגדרות. כבוי (ברירת המחדל) = מדגם; "true" = כל החתיכות.
SETTING = "readback_full"


def mode(conn) -> str:
    """"full" או "sample" — מה שהשרת מוסיף למניפסט שהסוכן מושך (`readback_mode`)."""
    return "full" if get_setting(conn, SETTING) == "true" else "sample"


def parse_setting(value) -> str | None:
    """ערך שהקונסולה שלחה → "true"/"false", או None = נדחה (#1794: אימות בשרת)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if value in ("true", "false") else None


class Underived(Exception):
    """אין ראיה לגיבוב — הסיבה בהודעה. ערך לא נרשם."""


class BadImage(Underived):
    """הבדיקה **רצה**, והקובץ אינו מה שהוא חייב להיות: zstd סירב לו, כותרת partclone או
    ה-CRC של המפה אינם תקינים, הזרם נגמר לא במקום, הפריימים אינם פריימי חתיכות. זו ראיה
    חיובית לקובץ פגום — לא "לא הצלחנו לבדוק" (עיקרון 5; סקירת 12/11, 2-ד). מי שגוזר
    אינדקס רושם אותה כ-`index_error`, והאימג' אינו מופץ. `Underived` לבדו נשאר למה
    שלא רץ: הפורס לא עלה, נהרג על תקיעה, או שהשרת פינה את הדרך."""


class Paused(Exception):
    """השרת עסוק (שידור, קליטה, העברה, סבב של אימג' אחר): הגיבוב פינה את
    הדרך. **אינו כישלון** — האימג' לא נרשם ב-`tried`, ויחזור כשהשרת שקט.
    `values` = מה שכבר גובב לפני ההשהיה (נרשם, לא נזרק)."""

    def __init__(self, reason: str, values: dict | None = None) -> None:
        super().__init__(reason)
        self.reason, self.values = reason, values or {}


def decompress_cmd(which=shutil.which) -> tuple[str, ...]:
    """zstd בעדיפות הסרק של המעבד והדיסק (nice 19, ionice idle) כשהכלים קיימים;
    בלעדיהם — zstd לבדו, ובקול (ביומן): הגיבוב עדיין נעצר כשיש עבודה."""
    if which("nice") and which("ionice"):
        return (*IDLE_PRIORITY, *DECOMPRESS)
    log.warning("readback digest: nice/ionice missing -- zstd runs at normal priority")
    return DECOMPRESS


_M = (1 << 64) - 1
_P1, _P2, _P3 = 11400714785074694791, 14029467366897019727, 1609587929392839161
_P4, _P5 = 9650029242287828579, 2870177450012600261


class PyXxh64:
    """XXH64, זרע 0, מהתיאור הציבורי — המימוש המבוקר כשאין `xxhash64` בקרנל.
    איטי (Python): לבדיקות ולשרת בלי המודול; הקרנל הוא הדרך הרגילה."""

    def __init__(self) -> None:
        self.v = [(_P1 + _P2) & _M, _P2, 0, (-_P1) & _M]
        self.tail, self.total = b"", 0

    @staticmethod
    def _round(acc: int, lane: int) -> int:
        acc = (acc + lane * _P2) & _M
        return (((acc << 31) | (acc >> 33)) & _M) * _P1 & _M

    def update(self, data) -> None:
        data = self.tail + bytes(data)
        self.total += len(data) - len(self.tail)
        v1, v2, v3, v4 = self.v
        r = self._round
        end = len(data) - len(data) % 32
        for off in range(0, end, 32):
            a, b, c, d = struct.unpack_from("<QQQQ", data, off)
            v1, v2, v3, v4 = r(v1, a), r(v2, b), r(v3, c), r(v4, d)
        self.v = [v1, v2, v3, v4]
        self.tail = data[end:]

    def intdigest(self) -> int:
        def rol(x, n):
            return ((x << n) | (x >> (64 - n))) & _M
        v1, v2, v3, v4 = self.v
        if self.total >= 32:
            h = (rol(v1, 1) + rol(v2, 7) + rol(v3, 12) + rol(v4, 18)) & _M
            for v in (v1, v2, v3, v4):
                h = ((h ^ self._round(0, v)) * _P1 + _P4) & _M
        else:
            h = (_P5) & _M
        h = (h + self.total) & _M
        t, i = self.tail, 0
        while i + 8 <= len(t):
            h ^= self._round(0, struct.unpack_from("<Q", t, i)[0])
            h = (rol(h, 27) * _P1 + _P4) & _M
            i += 8
        if i + 4 <= len(t):
            h ^= (struct.unpack_from("<I", t, i)[0] * _P1) & _M
            h = (rol(h, 23) * _P2 + _P3) & _M
            i += 4
        while i < len(t):
            h ^= (t[i] * _P5) & _M
            h = (rol(h, 11) * _P1) & _M
            i += 1
        h ^= h >> 33
        h = (h * _P2) & _M
        h ^= h >> 29
        h = (h * _P3) & _M
        h ^= h >> 32
        return h

    def hexdigest(self) -> str:
        return f"{self.intdigest():016x}"


class KernelXxh64:
    """XXH64 של הקרנל (AF_ALG `xxhash64`, כמו sha256 של fanout). הקרנל מחזיר
    את המספר little-endian; `hexdigest` כמו `%016llx` של fanout."""

    def __init__(self, tfm) -> None:
        self.op, _ = tfm.accept()

    def update(self, data) -> None:
        self.op.sendall(data, socket.MSG_MORE)

    def hexdigest(self) -> str:
        self.op.send(b"")
        digest = self.op.recv(8)
        self.op.close()
        if len(digest) != 8:
            raise Underived(f"the kernel gave {len(digest)} of 8 xxhash64 bytes")
        return f"{int.from_bytes(digest, 'little'):016x}"


B3_EMPTY = "af1349b9f5f9a1a6a0404dea36dcc9499bcb25c9adc112b7cc9a93cae41f3262"   # BLAKE3 of nothing (the spec's vector)
#: the answer of `b3_tool`: `[path]` for good, `[None]` = "no tool" -- which is believed only for `B3_RETRY_S`.
_B3_TOOL: list = []
#: [when `b3_tool` itself said "no tool"]. Empty beside a `[None]` = the answer was put there, not found (tests): final.
_B3_MISSED: list = []
#: how long "no tool" stands before it is asked again. A server whose chunkcast is built after the process started
#: (a deploy that builds it last, `tools/server-tools-build.sh` run by hand) must not need a restart to see it.
B3_RETRY_S = 30.0


def b3_tool(which=shutil.which, now=time.monotonic) -> str | None:
    """#3332: the `chunkcast` that makes BLAKE3 lists -- or None, said once. It is the one the server sends
    with (tools/server-tools-build.sh); it is believed only when it gives the spec's BLAKE3 of nothing.
    A tool is remembered for good; "none" only for `B3_RETRY_S`: metal-1115-compare, a chunkcast built after the
    server started was not seen until the server was restarted, and every wave failed "no chunkcast" meanwhile."""
    asked_before = bool(_B3_MISSED)
    if _B3_TOOL:
        if _B3_TOOL[0] is not None or not _B3_MISSED or now() - _B3_MISSED[0] < B3_RETRY_S:
            return _B3_TOOL[0]
        _B3_TOOL.clear()
        _B3_MISSED.clear()
    path = which("chunkcast")
    found = None
    if path:
        try:
            done = subprocess.run([path, "b3sum"], input=b"", capture_output=True, timeout=10, check=False)
            found = path if done.returncode == 0 and done.stdout.strip() == B3_EMPTY.encode() else None
        except (OSError, subprocess.SubprocessError):
            found = None
    if found is None:
        (log.debug if asked_before else log.warning)(
            "readback digest: no chunkcast with b3list (%s) -- no BLAKE3 list is made, and no chunk"
            " wave goes out without one", path or "not on PATH")
        _B3_MISSED[:] = [now()]
    _B3_TOOL.append(found)
    return found


class B3Problem(Exception):
    """הרשימה לא נוצרה שלמה -- הסיבה בהודעה. האינדקס עצמו אינו נפגע ממנה."""


def b3_hex(data: bytes, tool: str) -> str:
    """ה-BLAKE3 של `data` -- של רשימה, למניפסט (`chunkcast b3sum`)."""
    try:
        done = subprocess.run([tool, "b3sum"], input=data, capture_output=True, timeout=120, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        raise B3Problem(f"chunkcast b3sum did not run: {exc}") from exc
    text = done.stdout.decode("ascii", "replace").strip()
    if done.returncode != 0 or len(text) != 64 or not set(text) <= set("0123456789abcdef"):
        raise B3Problem(f"chunkcast b3sum exited {done.returncode}")
    return text


class B3Lister:
    """#3332: זרם הנתונים → `chunkcast b3list` → 32 בתים לכל CHUNK_BYTES. מה ש-`ChunkLister` מגבב
    ב-XXH64 עובר גם לכאן, באותם גבולות. הפלט לקובץ זמני ולא לצינור (צינור שאיש אינו קורא נחסם)."""

    def __init__(self, tool: str) -> None:
        import tempfile
        self.tool, self.out = tool, tempfile.TemporaryFile()
        self.failed: str | None = None
        try:
            self.proc = subprocess.Popen([tool, "b3list"], stdin=subprocess.PIPE, stdout=self.out,
                                         stderr=subprocess.DEVNULL)
        except OSError as exc:
            self.proc, self.failed = None, f"chunkcast b3list did not start: {exc}"

    def write(self, data) -> None:
        if self.failed is None:
            try:
                self.proc.stdin.write(data)
            except (BrokenPipeError, OSError) as exc:
                self.failed = f"chunkcast b3list stopped taking the stream: {exc}"

    def kill(self) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()
        self.out.close()

    def finish(self, chunks: int) -> tuple[bytes, str]:
        """(הרשימה, ה-BLAKE3 שלה) -- או `B3Problem`."""
        try:
            if self.failed is None:
                try:
                    self.proc.stdin.close()
                except OSError as exc:
                    self.failed = f"chunkcast b3list: {exc}"
                rc = self.proc.wait(timeout=600)
                if rc != 0 and self.failed is None:
                    self.failed = f"chunkcast b3list exited {rc}"
            if self.failed is not None:
                raise B3Problem(self.failed)
            self.out.seek(0)
            data = self.out.read()
            if len(data) != chunks * 32:
                raise B3Problem(f"the BLAKE3 list holds {len(data)} bytes, not {chunks} chunks of 32")
            return data, b3_hex(data, self.tool)
        finally:
            self.kill()


def xxh64_factory():
    """מה שמייצר מגבב XXH64: הקרנל כשאפשר; אחרת המימוש המבוקר — ובקול."""
    try:
        tfm = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
        tfm.bind(("hash", "xxhash64"))
    except (AttributeError, OSError) as exc:
        log.warning("readback digest: no kernel xxhash64 (%s) -- the slow Python XXH64 is used", exc)
        return PyXxh64
    return lambda: KernelXxh64(tfm)


class ChunkLister:
    """זרם הנתונים → רשימת XXH64, חתיכה של CHUNK_BYTES לשורה. `update` כמו
    של hashlib, כך שהקוראים בהמשך הקובץ (`_hash_exact`) אינם משתנים."""

    def __init__(self, new, b3=None) -> None:
        self.new, self.lines, self.in_chunk, self.total = new, [], 0, 0
        self.h = new()
        self.b3 = b3                                    # #3332: a B3Lister, or None

    def update(self, data) -> None:
        if self.b3 is not None:
            self.b3.write(data)
        mv = memoryview(data)
        while len(mv):
            take = min(len(mv), CHUNK_BYTES - self.in_chunk)
            self.h.update(mv[:take])
            mv, self.in_chunk, self.total = mv[take:], self.in_chunk + take, self.total + take
            if self.in_chunk == CHUNK_BYTES:
                self.lines.append(self.h.hexdigest())
                self.h, self.in_chunk = self.new(), 0

    def finish(self) -> tuple[str, int]:
        if self.in_chunk:
            self.lines.append(self.h.hexdigest())
        return "".join(line + "\n" for line in self.lines), self.total


def list_digest(text: str, new) -> str:
    """ה-XXH64 של קובץ הרשימה כמו שהוא — מה ש-`fanout --xxh64 < רשימה` נותן בסוכן."""
    h = new()
    h.update(text.encode("ascii"))
    return h.hexdigest()


def _exact(stream, n: int) -> bytes:
    data = stream.read(n)
    if len(data) != n:
        raise BadImage(f"the stream ended {n - len(data)} bytes early")
    return data


def _hash_exact(stream, n: int, sha) -> None:
    while n > 0:
        data = stream.read(min(n, CHUNK))
        if not data:
            raise BadImage(f"the stream ended {n} bytes early")
        sha.update(data)
        n -= len(data)


def digest_partclone(stream, limit: int | None = None, new=None, b3=None) -> tuple[str, int]:
    """(רשימת XXH64 לחתיכה, בייטים) של הבלוקים שבשימוש בזרם partclone v2 פרוס.

    `limit` = גודל המחיצה במניפסט (`size_bytes`). הכותרת היא קלט: ערכים
    שאינם יכולים להיות של המחיצה הזו נדחים **לפני** שנקרא בייט אחריה —
    אחרת כותרת פגומה הייתה מקצה זיכרון כגודלה ומפילה את מעבר הרקע (סקירת
    אבטחה, #1805). המפה נקראת במנות של CHUNK: CRC ומניין מצטברים."""
    head = _exact(stream, HEADER_SIZE)
    header = parse_header(head)
    if header is None:
        raise BadImage("no valid partclone v2 header")
    image_version = int.from_bytes(head[92:94], "little")
    checksum_mode = int.from_bytes(head[96:98], "little")
    checksum_size = int.from_bytes(head[98:100], "little")
    per_checksum = int.from_bytes(head[100:104], "little")
    if image_version != 2 or head[105] != _BM_BIT:
        raise BadImage(f"image version {image_version}, bitmap mode {head[105]} -- not a v2 bit map")
    total, used, bs = header["total_blocks"], header["used_blocks"], header["block_size"]
    if bs > MAX_BLOCK or (total + 7) // 8 > MAX_BITMAP:
        raise BadImage(f"implausible header: {total} blocks of {bs} bytes")
    if limit is not None and ((total - 1) * bs >= limit or used * bs > limit):
        raise BadImage(f"the header describes {total} blocks of {bs} bytes ({used} used),"
                        f" more than the partition ({limit})")
    left, crc, marked = (total + 7) // 8, 0, 0
    while left:
        chunk = _exact(stream, min(left, CHUNK))
        left -= len(chunk)
        crc = zlib.crc32(chunk, crc)
        if not left and total % 8:            # the bits past the last block are not blocks
            chunk = chunk[:-1] + bytes([chunk[-1] & ((1 << (total % 8)) - 1)])
        marked += int.from_bytes(chunk, "little").bit_count()
    if int.from_bytes(_exact(stream, 4), "little") != crc ^ 0xFFFFFFFF:
        raise BadImage("the bit map failed its CRC")
    if marked != used:
        raise BadImage(f"the bit map marks {marked} blocks, the header {used}")
    cs = checksum_size if checksum_mode else 0
    k = per_checksum if cs else 0
    sha = ChunkLister(new or xxh64_factory(), b3)
    if k:
        left = used
        while left:
            group = min(k, left)
            _hash_exact(stream, group * bs, sha)
            _exact(stream, cs)
            left -= group
    else:
        _hash_exact(stream, used * bs, sha)
    if stream.read(1):
        raise BadImage("bytes after the last block")
    return sha.finish()


def digest_raw(stream, limit: int | None = None, new=None, b3=None) -> tuple[str, int]:
    """(רשימה, בייטים) של זרם partclone.dd פרוס — המחיצה כולה. יותר מ-`limit` = לא של המחיצה."""
    sha, n = ChunkLister(new or xxh64_factory(), b3), 0
    while True:
        data = stream.read(CHUNK)
        if not data:
            return sha.finish()
        sha.update(data)
        n += len(data)
        if limit is not None and n > limit:
            raise BadImage(f"more than the partition ({limit} bytes) to read back")


class _Counting:
    """עוטף את stdout של zstd: מתי זז בייט אחרון (לשומר התקיעה), ופעם
    ב-STOP_EVERY_S שואל את `stop` אם השרת צריך את הדיסק — ואז `Paused`."""

    def __init__(self, raw, stop=None, count=None) -> None:
        # #3137: `count(n)` hears, after every read, how many decompressed bytes were read so far
        self.raw, self.moved, self.stop, self.count = raw, time.monotonic(), stop, count
        self.asked, self.read_bytes = self.moved, 0

    def read(self, n: int) -> bytes:
        now = time.monotonic()
        if self.stop is not None and now - self.asked >= STOP_EVERY_S:
            self.asked = now
            reason = self.stop()
            if reason:
                raise Paused(reason)
        data = self.raw.read(n)
        self.moved = time.monotonic()
        if self.count is not None:
            self.read_bytes += len(data)
            self.count(self.read_bytes)
        return data


def digest_file(path: Path, raw: bool, limit: int | None = None, stop=None, *,
                cmd: tuple[str, ...] | None = None, stall_s: float = STALL_S, new=None,
                count=None, b3=None) -> tuple[str, int]:
    """מפעיל את הפורס על קובץ המחיצה ומגבב. `Underived` על כל דבר שאינו ראיה;
    `Paused` כש-`stop()` אומר שהשרת עסוק (ה-zstd נהרג מיד)."""
    cmd = decompress_cmd() if cmd is None else cmd
    try:
        proc = subprocess.Popen([*cmd, str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("cannot run %s: %s", cmd[0], exc)       # #3021
        raise Underived(f"לא ניתן להריץ {Path(cmd[0]).name} — הפרטים ביומן השרת") from exc
    stream, done = _Counting(proc.stdout, stop, count), threading.Event()

    stalled = threading.Event()

    def guard() -> None:
        while not done.wait(5.0):
            if time.monotonic() - stream.moved > stall_s:
                stalled.set()
                proc.kill()
                return
    threading.Thread(target=guard, name="readback-hash-guard", daemon=True).start()
    try:
        result = (digest_raw if raw else digest_partclone)(stream, limit, new, b3)
    except BaseException:
        proc.kill()                                # הכרענו לפני EOF: אין טעם לפרוס את השאר
        raise
    finally:
        done.set()
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0:
        # a decompressor that RAN and refused the file is evidence about the file; one that was
        # killed (a stalled read on a network location, a signal) is a check that did not run
        kind = BadImage if rc > 0 and not stalled.is_set() else Underived
        raise kind(f"{cmd[0]} exited {rc} -- the file did not decompress whole")
    return result


def part_index(image_dir, part: dict):
    """האינדקס שליד קובץ המחיצה (`<file>.idx`, #2533) — או None כשאין אחד שלם
    ועקבי. קובץ המחיצה עבר את `guarded_image_path`."""
    from server.library import chunkformat           # chunkformat ← readbackhash: מעגל במעלה הקובץ
    base = guarded_image_path(image_dir, part.get("file"))
    return chunkformat.read_index(base) if base is not None else None


def has_list(image_dir, part: dict) -> bool:
    """יש למחיצה אינדקס שאומת: שדות ה-`readback_*` במניפסט (נרשמים רק כשהאינדקס
    שנגזר תאם ל-`index_xxh64`), ואינדקס ליד הקובץ שמסכים איתם. מניפסט שהועתק
    בלי האינדקס (העברה, ייבוא) — אין, וגוזרים שוב."""
    xxh, chunks = part.get("readback_xxh64"), _whole(part.get("readback_chunks"))
    if not (isinstance(xxh, str) and len(xxh) == 16) or chunks is None:
        return False
    index = part_index(image_dir, part)
    return index is not None and index.chunks == chunks and index.data_bytes == _whole(part.get("readback_bytes"))


def b3_path(image_dir, part: dict):
    """`<file>.b3` -- רשימת ה-BLAKE3 של המחיצה (#3332). None = קובץ המחיצה אינו בטוח."""
    base = guarded_image_path(image_dir, part.get("file"))
    return base.with_name(base.name + ".b3") if base is not None else None


def has_b3(image_dir, part: dict) -> bool:
    """#3332: למחיצה יש רשימת BLAKE3: `chunk_b3` במניפסט, וליד הקובץ רשימה באורך האינדקס.
    ה-BLAKE3 שלה אינו נבדק כאן -- הסוכן בודק אותו בעצמו (chunkcast), ורשימה שאינה היא נכשלת שם בשמה."""
    want, chunks = part.get("chunk_b3"), _whole(part.get("readback_chunks"))
    if not (isinstance(want, str) and len(want) == 64 and set(want) <= set("0123456789abcdef")) or chunks is None:
        return False
    path = b3_path(image_dir, part)
    try:
        return path is not None and not path.is_symlink() and path.is_file() and path.stat().st_size == chunks * 32
    except OSError:
        return False


def b3_bytes(image_dir, part: dict) -> bytes | None:
    """הרשימה, למי שמבקש אותה (`GET …/b3/<index>`). None = אין רשימה מאומתת."""
    if not has_list(image_dir, part) or not has_b3(image_dir, part):
        return None
    try:
        return b3_path(image_dir, part).read_bytes()
    except OSError:
        return None


def list_text(image_dir, part: dict) -> str | None:
    """רשימת ה-readback של המחיצה (שורה לחתיכה) — העמודה השלישית של האינדקס.
    None = אין אינדקס מאומת."""
    if not has_list(image_dir, part):
        return None
    index = part_index(image_dir, part)
    return index.readback_list() if index is not None else None


def _awaits_index(image_dir, part: dict) -> bool:
    """מחיצה של אימג' בפורמט 2 שהאינדקס שלה עוד לא נגזר (ולא נמצא שאינו תואם)."""
    return (isinstance(part, dict) and carries_a_stream_file(part) and has_index_xxh64(part)
            and not part.get("index_error")
            and (not has_list(image_dir, part) or (b3_tool() is not None and not has_b3(image_dir, part))))


def lacks_list(image_dir, part: dict) -> bool:
    """#3502: למחיצה של אימג' בפורמט 2 אין עוד אינדקס או רשימת BLAKE3 -- **בלי קשר לכלי**: `_awaits_index` אינו
    אומר "חסר" כשאין `chunkcast` (אין מה לגזור), וזה בדיוק מה שהמפעיל צריך לראות."""
    return (isinstance(part, dict) and carries_a_stream_file(part) and has_index_xxh64(part)
            and not part.get("index_error")
            and (not has_list(image_dir, part) or not has_b3(image_dir, part)))


def needs_list(manifest: dict) -> bool:
    """רק אימג' שמופץ: פורמט ישן (או אינדקס שכבר נמצא שאינו תואם) אינו מגובב —
    פריסה של 100GB בשביל אימג' שאיש לא יפיץ."""
    if format_problem(manifest) is not None:
        return False
    return any(_awaits_index(manifest.get("_dir") or "", p) for p in manifest.get("partitions") or [])


def derive(manifest: dict, *, digest=None, stop=None, new=None, progress=None) -> tuple[dict, list[str]]:
    """({index: ערך ל-`ImageLibrary.record_index`}, בעיות) למחיצות של אימג' בפורמט 2
    שהאינדקס שלהן עוד לא נגזר. `digest` = `chunkformat.derive_index`. אינדקס שנגזר
    ואינו ה-`index_xxh64` שבמניפסט הוא ערך (`{"error": …}`) **וגם** בעיה.
    `Paused` (עם מה שכבר נגזר) עולה למעלה — אינו בעיה של המחיצה.
    `progress(index, נקראו, מתוך)` (#3137, `tracker`) עובר ל-`digest` כמילת מפתח: רק מי
    שגוזר ב-`chunkformat.derive_index` עצמו מעביר אותו."""
    from server.library import chunkformat           # chunkformat ← readbackhash: מעגל במעלה הקובץ
    digest = digest or chunkformat.derive_index
    image_dir = manifest.get("_dir")
    if not image_dir or not manifest.get("_available", True) or format_problem(manifest) is not None:
        return {}, []
    values: dict = {}
    problems: list[str] = []
    for part in manifest.get("partitions") or []:
        if not _awaits_index(image_dir, part):
            continue
        index = part.get("index")
        path = guarded_image_path(image_dir, part.get("file"))
        if path is None:
            problems.append(f"p{index}: file missing or unsafe")
            continue
        size = _whole(part.get("size_bytes"))
        if size is None:
            problems.append(f"p{index}: no size_bytes -- nothing bounds what is read")
            continue
        kw = {} if progress is None else {"progress": lambda done, total, i=index: progress(i, done, total)}
        try:
            found = (digest(path, captured_raw(part), size, **kw) if stop is None
                     else digest(path, captured_raw(part), size, stop, **kw))
        except Paused as exc:
            exc.values = values
            raise
        except BadImage as exc:                   # the check ran: the file is not what was captured
            problems.append(f"p{index}: {exc}")
            values[index] = {"error": str(exc)}
            continue
        except Underived as exc:                  # the check did not run: no verdict, tried again later
            problems.append(f"p{index}: {exc}")
            continue
        if found.data_bytes > size:
            problems.append(f"p{index}: {found.data_bytes} bytes to read back, more than the partition ({size})")
            continue
        new = new or xxh64_factory()
        text = found.render()
        got = list_digest(text, new)
        if got != part["index_xxh64"]:
            why = (f"the index derived from the file (xxh64 {got}) is not the one the capture"
                   f" signed (index_xxh64 {part['index_xxh64']})")
            problems.append(f"p{index}: {why}")
            values[index] = {"error": why}
            continue
        listing = found.readback_list()
        values[index] = {"index": text, "list": listing, "list_xxh64": list_digest(listing, new),
                         "bytes": found.data_bytes}
        b3 = getattr(found, "b3", None)
        if b3 is not None:                        # #3332: (the list, its BLAKE3)
            values[index]["b3"], values[index]["b3_hash"] = b3
        elif getattr(found, "b3_note", None):     # the tool is here and the list did not come out whole: the index
            values[index]["b3_note"] = found.b3_note  # stands (a wave waits for it, #3137), the image stays unverified
            log.warning("readback digest: p%s: no BLAKE3 list -- %s", index, found.b3_note)
    return values, problems


def _order(pending: dict[str, dict], rank: dict[str, str]) -> list[str]:
    """#1864: מי קודם. אימג' שנפתח לו סבב (`kick(image_id)`) — ראשון; אחריו לפי
    השימוש האחרון (סבב/הפצה, `rank` = {image_id: זמן ISO}), מהחדש לישן; מה שלא
    שומש מעולם — בסוף; ובכל דרגה הקטן קודם (`total_compressed_bytes`). אימג'
    גדול שאיש לא פורס אינו עומד לפני האימג' שבסבב."""
    with _LOCK:
        first = [i for i in _PRIORITY if i in pending]
    def size(i: str) -> int:
        return _whole(pending[i].get("total_compressed_bytes")) or 0
    rest = sorted((i for i in pending if i not in first), key=size)
    rest.sort(key=lambda i: rank.get(i) or "", reverse=True)        # stable: size within a rank
    return first + rest


def fill(library, *, digest=None, report=None, tried: dict | None = None,
         busy=None, rank=None, status: dict | None = None) -> list[dict]:
    """מעבר אחד על הספרייה. אימג' אחד שנכשל אינו עוצר את האחרים.

    `tried` = {image_id: חתימת הקבצים} של מה שכבר נוסה ונכשל — לא שוב בכל
    מעבר (פריסה של 100GB כל עשר דקות), אלא רק כשהקבצים השתנו.
    `busy(image_id)` = סיבה (מחרוזת) כשהשרת צריך את הדיסק והמעבד, אחרת None —
    נשאל לפני כל אימג' ופעם ב-STOP_EVERY_S תוך כדי: אימג' שהושהה אינו נכשל.
    `rank()` = {image_id: שימוש אחרון}. `status["paused"]` = הסיבה, כשהושהה."""
    tried = {} if tried is None else tried
    status = {} if status is None else status
    status.pop("paused", None)
    results = []
    pending = {}
    for image_id, manifest in library.scan().items():
        if not needs_list(manifest):
            continue
        if tried.get(image_id) == _stamp(manifest):
            continue
        pending[image_id] = manifest
    try:
        ranks = rank() if rank is not None else {}
    except Exception:                         # noqa: BLE001 — a wrong order is not a reason to stop
        log.exception("readback digest: the last-use order could not be read")
        ranks = {}
    for image_id in _order(pending, ranks):
        manifest = pending[image_id]
        stamp = _stamp(manifest)
        reason = busy(image_id) if busy is not None else None
        if reason:
            status["paused"] = reason
            continue
        if not claim(image_id, manifest):
            continue            # #3137: a wave that waits for this image is deriving it right now
        stop = (lambda i=image_id: busy(i)) if busy is not None else None
        written: dict[int, dict] = {}
        paused = None
        try:
            try:
                values, problems = derive(manifest, digest=digest, stop=stop,
                                          progress=tracker(image_id) if digest is None else None)
            except Paused as exc:
                paused = status["paused"] = exc.reason
                values, problems = exc.values, []
                log.info("readback digest of %s paused: %s", image_id, exc.reason)
            if values:
                done = library.record_index(image_id, values)
                written = {i: values[i] for i in done}
                if not done:
                    problems.append("manifest not updated")
            if paused is None:
                with _LOCK:
                    _PRIORITY.pop(image_id, None)
        except Exception as exc:              # noqa: BLE001 — one image, by name; never "fine", never the end of the pass
            log.exception("readback digest of %s failed: %s", image_id, exc)
            values, problems = {}, ["הגיבוב נכשל — הפרטים ביומן השרת"]       # #3021: הגולמי ב-log.exception
        finally:
            release(image_id)
        notes = [f"p{i}: {v['b3_note']}" for i, v in sorted(values.items()) if isinstance(v, dict) and v.get("b3_note")]
        if problems or notes:
            tried[image_id] = stamp           # #3332: a list that did not come out is not made again every pass
            with _LOCK:
                _FAILED[image_id] = "; ".join(problems + notes)
        elif paused is None:
            with _LOCK:
                _FAILED.pop(image_id, None)
        if not written and not problems:
            continue
        result = {"image_id": image_id, "written": written, "problems": problems}
        results.append(result)
        if report is not None:
            report(result)
    return results


def _stamp(manifest: dict) -> tuple:
    out = []
    for part in manifest.get("partitions") or []:
        path = guarded_image_path(manifest.get("_dir") or "", part.get("file")) \
            if isinstance(part, dict) and carries_a_stream_file(part) else None
        try:
            st = path.stat() if path is not None else None
        except OSError:
            st = None
        out.append((part.get("file") if isinstance(part, dict) else None,
                    st.st_size if st else None, st.st_mtime_ns if st else None))
    return tuple(out)


_KICK = threading.Event()
_LOCK = threading.Lock()
#: #3502: {image_id: הסיבה} לאימג' שהמעבר ברקע ניסה ולא הצליח -- לא נבלע, והבריאות אומרת אותה
#: (`failed()`). נמחק כשהגזירה הצליחה או כשהקבצים השתנו והניסיון נעשה שוב.
_FAILED: dict[str, str] = {}
_PRIORITY: dict[str, None] = {}      # סדר ההכנסה נשמר: הראשון שנפתח לו סבב — ראשון
#: #3137: מי גוזר עכשיו את האינדקס של אימג' — המעבר ברקע (`fill`) או גל שממתין לו
#: (`server.library.indexprep`). אחד בכל פעם: אותו אימג' אינו נפרס פעמיים במקביל.
#: הערך: {מחיצה: [משקל = הבייטים הדחוסים של הקובץ, נקראו, מתוך]}, בשביל `percent`.
_CLAIMED: dict[str, dict[int, list[int]]] = {}


def claim(image_id: str, manifest: dict | None = None) -> bool:
    """True = הקורא גוזר עכשיו את האינדקס של האימג', עד `release`. False = מישהו אחר גוזר אותו."""
    weights: dict[int, list[int]] = {}
    image_dir = (manifest or {}).get("_dir") or ""
    for part in (manifest or {}).get("partitions") or []:
        if not _awaits_index(image_dir, part):
            continue
        path = guarded_image_path(image_dir, part.get("file"))
        try:
            size = path.stat().st_size if path is not None else 0
        except OSError:
            size = 0
        weights[part["index"]] = [max(size, 1), 0, 0]
    with _LOCK:
        if image_id in _CLAIMED:
            return False
        _CLAIMED[image_id] = weights
    return True


def release(image_id: str) -> None:
    with _LOCK:
        _CLAIMED.pop(image_id, None)


def tracker(image_id: str):
    """`progress` של `derive` שכותב ל-`_CLAIMED`, וממנו `percent` קורא."""
    def update(index: int, done: int, total: int) -> None:
        with _LOCK:
            row = _CLAIMED.get(image_id, {}).get(index)
            if row is not None:
                row[1], row[2] = done, total
    return update


def failed() -> dict[str, str]:
    """#3502: {image_id: סיבה} -- מה שהמעבר ברקע ניסה לגזור ולא הצליח, עד שיצליח."""
    with _LOCK:
        return dict(_FAILED)


def percent(image_id: str) -> int | None:
    """כמה מהגזירה של האימג' נעשה (0 עד 99, משוקלל בבייטים הדחוסים של כל מחיצה).
    None = איש אינו גוזר אותו עכשיו."""
    with _LOCK:
        rows = _CLAIMED.get(image_id)
        if rows is None:
            return None
        whole = sum(w for w, _, _ in rows.values())
        got = sum(w * min(1.0, done / total) for w, done, total in rows.values() if total > 0)
    return min(99, int(100 * got / whole)) if whole else 0


def kick(image_id: str | None = None) -> None:
    """המעבר הבא מתחיל עכשיו, לא בעוד עשר דקות: אימג' חדש נכנס לספרייה, או
    (`image_id`, #1864) נפתח לו סבב — והוא עובר לראש התור."""
    if image_id:
        with _LOCK:
            _PRIORITY[image_id] = None
    _KICK.set()


def run_forever(library, *, report=None, interval: float = INTERVAL_S,
                stop: threading.Event | None = None, busy=None, rank=None) -> None:
    """התהליכון: מעבר, ואז המתנה ל-`kick()` או ל-`interval` — או ל-PAUSE_RETRY_S
    כשהמעבר פינה את הדרך לעבודה. חריגה נרשמת ולא עוצרת."""
    tried: dict = {}
    while stop is None or not stop.is_set():
        status: dict = {}
        try:
            fill(library, report=report, tried=tried, busy=busy, rank=rank, status=status)
        except Exception:                       # noqa: BLE001 — תהליכון רקע: נרשם, לא נבלע
            log.exception("readback digest pass failed")
        _KICK.wait(PAUSE_RETRY_S if status.get("paused") else interval)
        _KICK.clear()


def server_busy(conn, engine=None):
    """#1864 (ברזל 20/10): `busy(image_id)` של התהליכון. סיבה = לפנות את הדרך:
    שידור שזורם (`engine` במצב sending או repairing), קליטה או שליחה ישירה שרצות או מאומתות
    (`tasks`), העברה בין אתרים (`storage_transfers`), וסבב פתוח של **אימג' אחר**.
    סבב פתוח של האימג' הזה עצמו, לפני שהשידור התחיל (המכונות עוד עולות), אינו
    עוצר: זה החלון שבו הגיבוב שלו עוד יכול להגיע למניפסט שהסוכן קורא — ואם לא
    הגיע, הסבב "לא אומת", כמו קודם. None = שקט."""
    def busy(image_id: str) -> str | None:
        status = engine.status() if engine is not None else None
        if status and status.get("state") in ("sending", "repairing"):
            return "שידור פעיל"
        if conn.execute("SELECT 1 FROM tasks WHERE state IN ('running', 'verifying') LIMIT 1").fetchone():
            return "קליטה או שליחה ישירה פעילה"
        if conn.execute("SELECT 1 FROM storage_transfers WHERE state IN ('queued', 'sending', 'verifying')"
                        " LIMIT 1").fetchone():
            return "העברה בין אתרים פעילה"
        for table, states in (("sessions", "('open', 'running')"), ("room_rounds", "('active', 'closing')")):
            if conn.execute(f"SELECT 1 FROM {table} WHERE state IN {states} AND image_id != ? LIMIT 1",
                            (image_id,)).fetchone():
                return "סבב פתוח לאימג' אחר"
        return None
    return busy


def last_use(conn):
    """`rank()` של התהליכון: {image_id: הסבב או ההפצה האחרונים שנפתחו לו}."""
    def rank() -> dict[str, str]:
        rows = conn.execute(
            "SELECT image_id, MAX(created_at) AS at FROM (SELECT image_id, created_at FROM room_rounds"
            " UNION ALL SELECT image_id, created_at FROM sessions) GROUP BY image_id").fetchall()
        return {row[0]: row[1] for row in rows}
    return rank


def main(argv: list[str] | None = None) -> int:
    """הרצה יבשה: האינדקס שנגזר מכל מחיצה, מול המניפסט. אינה כותבת דבר."""
    from server.library import chunkformat           # chunkformat ← readbackhash: מעגל במעלה הקובץ
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m server.agent.readbackhash <image_dir>", file=sys.stderr)
        return 2
    image_dir = Path(args[0])
    try:
        manifest = json.loads((image_dir / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"cannot read manifest: {exc}", file=sys.stderr)
        return 2
    ok = True
    for part in manifest.get("partitions") or []:
        if not isinstance(part, dict) or not carries_a_stream_file(part):
            continue
        index, name = part.get("index"), part.get("file")
        path = guarded_image_path(image_dir, name)
        try:
            if path is None:
                raise Underived("file missing or unsafe")
            found = chunkformat.derive_index(path, captured_raw(part), _whole(part.get("size_bytes")))
        except Underived as exc:
            ok = False
            print(f"p{index} {name} UNDERIVED: {exc}")
            continue
        got = list_digest(found.render(), xxh64_factory())
        ok = ok and got == part.get("index_xxh64")
        print(f"p{index} {name} index_xxh64={got} chunks={found.chunks} data_bytes={found.data_bytes}"
              f" manifest={part.get('index_xxh64') or 'none'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
