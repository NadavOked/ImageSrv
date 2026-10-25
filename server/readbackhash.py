"""הגיבוב לקריאה החוזרת מהדיסק (‏#1805, ‏CLR-01).

עד #1805 מגירה הייתה `done` לפי ראיה מה**זרם** — ה-sha256 של הבייטים הדחוסים
לפני zstd ו-partclone — ואיש לא קרא את הדיסק. עכשיו הסוכן קורא בחזרה מכל
מחיצה שכתב את הבלוקים שבשימוש (‏O_DIRECT, ‏`fanout --readback`) ומשווה
לגיבוב **שאינו הזרם**: זה, שהשרת לוקח מהאימג' עצמו כשהוא בספרייה.

**מה מגובב** — "זרם הנתונים": תוכן הבלוקים שבשימוש, לפי סדר הבלוקים, בדיוק
כפי ש-`partclone -r` כותב אותם לדיסק: הזרם הפרוס של המחיצה (‏`zstd -dc`)
בלי כותרת ה-`image_desc_v2` (‏110 בייט), בלי מפת הסיביות (וה-CRC-32 שלה) ובלי
סכומי הבדיקה שבין הבלוקים (‏`checksum_size` בייט אחרי כל `blocks_per_checksum`
בלוקים, ועוד אחד לשארית — ‏`src/main.c` של partclone). מחיצה שנקלטה ב-
`partclone.dd` (אין כותרת) — הזרם הפרוס כולו.

**הכרעת נדב 20/10 (#1864, ‏sha256 של הכול עלה ~8 דק' לסבב של 4 מגירות):** הזרם
נחתך ל-`CHUNK_BYTES` (4MiB), ולכל חתיכה XXH64 (זרע 0) — שורה של 16 ספרות hex.
הרשימה נשמרת **ליד** קובץ המחיצה (`<file>.readback`, ‏`GET …/readback/<index>`),
והמניפסט נושא `readback_xxh64` (ה-XXH64 של קובץ הרשימה עצמו), ‏`readback_chunks`,
‏`readback_bytes` ו-`readback_chunk_bytes`. הסוכן משווה חתיכה מול חתיכה: מדגם
(ברירת המחדל) או כולן (`readback_full` בהגדרות). ‏XXH64 בקרנל (‏AF_ALG
‏`xxhash64`), ובלעדיו — מימוש Python מבוקר (איטי; בקול, ביומן). ‏`readback_sha256`
של מניפסטים קודמים נשאר כמו שהוא (הסוכן עוד מכיר אותו), ואינו מחושב עוד.

**ראיה חיובית בכל שלב** (עיקרון 5): magic/גרסה/endianess/CRC של הכותרת,
‏CRC-32 של המפה, ספירת הסיביות שווה ל-`usedblocks`, אורך קטע הנתונים שווה
בדיוק למה שהכותרת מצהירה (‏EOF במקום הצפוי, לא לפניו ולא אחריו), ו-zstd
שיצא 0 (הוא בודק את סכום המסגרת). כל אחד מהם שלא עבר — **אין ערך** במניפסט,
והסוכן מדווח על המחיצה "לא אומת" בשמה; לעולם לא ערך מנוחש.

נכתב למניפסט עצמו (עיקרון 3: הסוכן קורא את המניפסט שהשרת מגיש), ליד
המחיצה: `readback_sha256` ו-`readback_bytes`. ‏`ImageLibrary.record_readback`.

**מתי:** תהליכון רקע אחד (`run_forever`) — בעליית השרת על כל הספרייה (כולל
אימג'ים שנקלטו לפני #1805), ומיד כשאימג' נכנס לספרייה (`kick()` מסוף אימות
הקליטה), ולפחות פעם ב-`INTERVAL_S` (העלאה, ייבוא, סנכרון משני). אימג' ישן
שעוד לא גובב נפרס בינתיים בלי ערך — והסוכן אומר "לא אומת", לא "תקין".

הרצה יבשה לאימות במעבדה: `python -m server.readbackhash <תיקיית-אימג'>`.
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

from .db import get_setting
from .images import _whole, carries_a_stream_file, guarded_image_path
from .usedbackfill import HEADER_SIZE, captured_raw, parse_header

log = logging.getLogger("imagectl.readbackhash")

CHUNK = 4 * 1024 * 1024      # how much is read at a time
CHUNK_BYTES = 4 * 1024 * 1024  # a chunk of the data stream -- fanout.c RB_CHUNK, the same number
LIST_SUFFIX = ".readback"
STALL_S = 300.0          # zstd שלא נתן בייט כל הזמן הזה (מיקום רשת שנפל) — נהרג
INTERVAL_S = 600.0       # מעבר על הספרייה לפחות פעם בעשר דקות
_BM_BIT = 1              # partclone.h: BM_BIT = 0x01
MAX_BITMAP = 1 << 31     # bytes -- fanout.c's own ceiling (readback())
MAX_BLOCK = 64 << 20     # bytes -- likewise
DECOMPRESS = ("zstd", "-dc", "--")
#: ‏ברזל 20/10 (#1864): הגיבוב רץ עם הסבב והשידור הואט ~20%. עכשיו בעדיפות
#: הכי נמוכה של המעבד ושל הדיסק — ובנוסף נעצר כשיש עבודה (`busy`, למטה).
IDLE_PRIORITY = ("nice", "-n", "19", "ionice", "-c", "3")
PAUSE_RETRY_S = 30.0     # הושהה בגלל עבודה פעילה: בודקים שוב בעוד חצי דקה, לא בעוד עשר
STOP_EVERY_S = 2.0       # כל כמה זמן, תוך כדי קריאה, שואלים אם צריך לפנות את השרת


#: ‏#1864 (נדב 20/10): מתג בהגדרות. כבוי (ברירת המחדל) = מדגם; "true" = כל החתיכות.
SETTING = "readback_full"


def mode(conn) -> str:
    """‏"full" או "sample" — מה שהשרת מוסיף למניפסט שהסוכן מושך (`readback_mode`)."""
    return "full" if get_setting(conn, SETTING) == "true" else "sample"


def parse_setting(value) -> str | None:
    """ערך שהקונסולה שלחה → ‏"true"/"false", או None = נדחה (‏#1794: אימות בשרת)."""
    if isinstance(value, bool):
        return "true" if value else "false"
    return value if value in ("true", "false") else None


class Underived(Exception):
    """אין ראיה לגיבוב — הסיבה בהודעה. ערך לא נרשם."""


class Paused(Exception):
    """השרת עסוק (שידור, קליטה, העברה, סבב של אימג' אחר): הגיבוב פינה את
    הדרך. **אינו כישלון** — האימג' לא נרשם ב-`tried`, ויחזור כשהשרת שקט.
    ‏`values` = מה שכבר גובב לפני ההשהיה (נרשם, לא נזרק)."""

    def __init__(self, reason: str, values: dict | None = None) -> None:
        super().__init__(reason)
        self.reason, self.values = reason, values or {}


def decompress_cmd(which=shutil.which) -> tuple[str, ...]:
    """‏zstd בעדיפות הסרק של המעבד והדיסק (‏nice 19, ‏ionice idle) כשהכלים קיימים;
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
    """XXH64 של הקרנל (‏AF_ALG ‏`xxhash64`, כמו ‏sha256 של fanout). הקרנל מחזיר
    את המספר little-endian; ‏`hexdigest` כמו ‏`%016llx` של fanout."""

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
    """זרם הנתונים → רשימת XXH64, חתיכה של CHUNK_BYTES לשורה. ‏`update` כמו
    של hashlib, כך שהקוראים בהמשך הקובץ (`_hash_exact`) אינם משתנים."""

    def __init__(self, new) -> None:
        self.new, self.lines, self.in_chunk, self.total = new, [], 0, 0
        self.h = new()

    def update(self, data) -> None:
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
        raise Underived(f"the stream ended {n - len(data)} bytes early")
    return data


def _hash_exact(stream, n: int, sha) -> None:
    while n > 0:
        data = stream.read(min(n, CHUNK))
        if not data:
            raise Underived(f"the stream ended {n} bytes early")
        sha.update(data)
        n -= len(data)


def digest_partclone(stream, limit: int | None = None, new=None) -> tuple[str, int]:
    """(רשימת XXH64 לחתיכה, בייטים) של הבלוקים שבשימוש בזרם partclone v2 פרוס.

    ‏`limit` = גודל המחיצה במניפסט (`size_bytes`). הכותרת היא קלט: ערכים
    שאינם יכולים להיות של המחיצה הזו נדחים **לפני** שנקרא בייט אחריה —
    אחרת כותרת פגומה הייתה מקצה זיכרון כגודלה ומפילה את מעבר הרקע (סקירת
    אבטחה, #1805). המפה נקראת במנות של CHUNK: CRC ומניין מצטברים."""
    head = _exact(stream, HEADER_SIZE)
    header = parse_header(head)
    if header is None:
        raise Underived("no valid partclone v2 header")
    image_version = int.from_bytes(head[92:94], "little")
    checksum_mode = int.from_bytes(head[96:98], "little")
    checksum_size = int.from_bytes(head[98:100], "little")
    per_checksum = int.from_bytes(head[100:104], "little")
    if image_version != 2 or head[105] != _BM_BIT:
        raise Underived(f"image version {image_version}, bitmap mode {head[105]} -- not a v2 bit map")
    total, used, bs = header["total_blocks"], header["used_blocks"], header["block_size"]
    if bs > MAX_BLOCK or (total + 7) // 8 > MAX_BITMAP:
        raise Underived(f"implausible header: {total} blocks of {bs} bytes")
    if limit is not None and ((total - 1) * bs >= limit or used * bs > limit):
        raise Underived(f"the header describes {total} blocks of {bs} bytes ({used} used),"
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
        raise Underived("the bit map failed its CRC")
    if marked != used:
        raise Underived(f"the bit map marks {marked} blocks, the header {used}")
    cs = checksum_size if checksum_mode else 0
    k = per_checksum if cs else 0
    sha = ChunkLister(new or xxh64_factory())
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
        raise Underived("bytes after the last block")
    return sha.finish()


def digest_raw(stream, limit: int | None = None, new=None) -> tuple[str, int]:
    """(רשימה, בייטים) של זרם partclone.dd פרוס — המחיצה כולה. יותר מ-`limit` = לא של המחיצה."""
    sha, n = ChunkLister(new or xxh64_factory()), 0
    while True:
        data = stream.read(CHUNK)
        if not data:
            return sha.finish()
        sha.update(data)
        n += len(data)
        if limit is not None and n > limit:
            raise Underived(f"more than the partition ({limit} bytes) to read back")


class _Counting:
    """עוטף את stdout של zstd: מתי זז בייט אחרון (לשומר התקיעה), ופעם
    ב-STOP_EVERY_S שואל את `stop` אם השרת צריך את הדיסק — ואז `Paused`."""

    def __init__(self, raw, stop=None) -> None:
        self.raw, self.moved, self.stop = raw, time.monotonic(), stop
        self.asked = self.moved

    def read(self, n: int) -> bytes:
        now = time.monotonic()
        if self.stop is not None and now - self.asked >= STOP_EVERY_S:
            self.asked = now
            reason = self.stop()
            if reason:
                raise Paused(reason)
        data = self.raw.read(n)
        self.moved = time.monotonic()
        return data


def digest_file(path: Path, raw: bool, limit: int | None = None, stop=None, *,
                cmd: tuple[str, ...] | None = None, stall_s: float = STALL_S, new=None) -> tuple[str, int]:
    """מפעיל את הפורס על קובץ המחיצה ומגבב. ‏`Underived` על כל דבר שאינו ראיה;
    ‏`Paused` כש-`stop()` אומר שהשרת עסוק (ה-zstd נהרג מיד)."""
    cmd = decompress_cmd() if cmd is None else cmd
    try:
        proc = subprocess.Popen([*cmd, str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise Underived(f"cannot run {cmd[0]}: {exc}") from exc
    stream, done = _Counting(proc.stdout, stop), threading.Event()

    def guard() -> None:
        while not done.wait(5.0):
            if time.monotonic() - stream.moved > stall_s:
                proc.kill()
                return
    threading.Thread(target=guard, name="readback-hash-guard", daemon=True).start()
    try:
        result = (digest_raw if raw else digest_partclone)(stream, limit, new)
    except BaseException:
        proc.kill()                                # הכרענו לפני EOF: אין טעם לפרוס את השאר
        raise
    finally:
        done.set()
        proc.stdout.close()
        rc = proc.wait()
    if rc != 0:
        raise Underived(f"{cmd[0]} exited {rc} -- the file did not decompress whole")
    return result


def list_path(image_dir, part: dict) -> Path | None:
    """קובץ הרשימה של המחיצה: ליד קובץ הזרם (שעבר את `guarded_image_path`), ‏`<file>.readback`."""
    base = guarded_image_path(image_dir, part.get("file"))
    return base.with_name(base.name + LIST_SUFFIX) if base is not None else None


def has_list(image_dir, part: dict) -> bool:
    """יש למחיצה רשימה שלמה: השדות במניפסט, וקובץ באורך שהם מצהירים (17 בייט
    לשורה). מניפסט שהועתק בלי הקובץ (העברה, ייבוא) — אין רשימה, ומחשבים שוב."""
    xxh, chunks = part.get("readback_xxh64"), _whole(part.get("readback_chunks"))
    if not (isinstance(xxh, str) and len(xxh) == 16) or chunks is None:
        return False
    path = list_path(image_dir, part)
    try:
        return path is not None and not path.is_symlink() and path.is_file()             and path.stat().st_size == 17 * chunks
    except OSError:
        return False


def needs_list(manifest: dict) -> bool:
    return any(isinstance(p, dict) and carries_a_stream_file(p) and not has_list(manifest.get("_dir") or "", p)
               for p in manifest.get("partitions") or [])


def derive(manifest: dict, *, digest=digest_file, stop=None, new=None) -> tuple[dict, list[str]]:
    """‏({index: (רשימה, בייטים, XXH64 של הרשימה)}, בעיות) למחיצות עם קובץ זרם שאין
    להן עדיין רשימה. ‏`Paused` (עם מה שכבר גובב) עולה למעלה — אינו בעיה של המחיצה."""
    image_dir = manifest.get("_dir")
    if not image_dir or not manifest.get("_available", True):
        return {}, []
    values: dict = {}
    problems: list[str] = []
    for part in manifest.get("partitions") or []:
        if not isinstance(part, dict) or not carries_a_stream_file(part) or has_list(image_dir, part):
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
        try:
            sha, count = (digest(path, captured_raw(part), size) if stop is None
                          else digest(path, captured_raw(part), size, stop))
        except Paused as exc:
            exc.values = values
            raise
        except Underived as exc:
            problems.append(f"p{index}: {exc}")
            continue
        if count > size:
            problems.append(f"p{index}: {count} bytes to read back, more than the partition ({size})")
            continue
        new = new or xxh64_factory()
        values[index] = (sha, count, list_digest(sha, new))
    return values, problems


def _order(pending: dict[str, dict], rank: dict[str, str]) -> list[str]:
    """‏#1864: מי קודם. אימג' שנפתח לו סבב (`kick(image_id)`) — ראשון; אחריו לפי
    השימוש האחרון (סבב/הפצה, `rank` = ‏{image_id: זמן ISO}), מהחדש לישן; מה שלא
    שומש מעולם — בסוף; ובכל דרגה הקטן קודם (`total_compressed_bytes`). אימג'
    גדול שאיש לא פורס אינו עומד לפני האימג' שבסבב."""
    with _LOCK:
        first = [i for i in _PRIORITY if i in pending]
    def size(i: str) -> int:
        return _whole(pending[i].get("total_compressed_bytes")) or 0
    rest = sorted((i for i in pending if i not in first), key=size)
    rest.sort(key=lambda i: rank.get(i) or "", reverse=True)        # stable: size within a rank
    return first + rest


def fill(library, *, digest=digest_file, report=None, tried: dict | None = None,
         busy=None, rank=None, status: dict | None = None) -> list[dict]:
    """מעבר אחד על הספרייה. אימג' אחד שנכשל אינו עוצר את האחרים.

    ‏`tried` = ‏{image_id: חתימת הקבצים} של מה שכבר נוסה ונכשל — לא שוב בכל
    מעבר (פריסה של 100GB כל עשר דקות), אלא רק כשהקבצים השתנו.
    ‏`busy(image_id)` = סיבה (מחרוזת) כשהשרת צריך את הדיסק והמעבד, אחרת None —
    נשאל לפני כל אימג' ופעם ב-STOP_EVERY_S תוך כדי: אימג' שהושהה אינו נכשל.
    ‏`rank()` = ‏{image_id: שימוש אחרון}. ‏`status["paused"]` = הסיבה, כשהושהה."""
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
        stop = (lambda i=image_id: busy(i)) if busy is not None else None
        written: dict[int, tuple[str, int]] = {}
        paused = None
        try:
            try:
                values, problems = derive(manifest, digest=digest, stop=stop)
            except Paused as exc:
                paused = status["paused"] = exc.reason
                values, problems = exc.values, []
                log.info("readback digest of %s paused: %s", image_id, exc.reason)
            if values:
                done = library.record_readback(image_id, values)
                written = {i: values[i] for i in done}
                if not done:
                    problems.append("manifest not updated")
            if paused is None:
                with _LOCK:
                    _PRIORITY.pop(image_id, None)
        except Exception as exc:              # noqa: BLE001 — one image, by name; never "fine", never the end of the pass
            log.exception("readback digest of %s failed", image_id)
            values, problems = {}, [f"{type(exc).__name__}: {exc}"]
        if problems:
            tried[image_id] = stamp
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
_PRIORITY: dict[str, None] = {}      # סדר ההכנסה נשמר: הראשון שנפתח לו סבב — ראשון


def kick(image_id: str | None = None) -> None:
    """המעבר הבא מתחיל עכשיו, לא בעוד עשר דקות: אימג' חדש נכנס לספרייה, או
    (‏`image_id`, ‏#1864) נפתח לו סבב — והוא עובר לראש התור."""
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


def server_busy(conn, sender=None):
    """‏#1864 (ברזל 20/10): ‏`busy(image_id)` של התהליכון. סיבה = לפנות את הדרך:
    שידור שזורם (`sender` במצב sending), קליטה או שליחה ישירה שרצות או מאומתות
    (`tasks`), העברה בין אתרים (`storage_transfers`), וסבב פתוח של **אימג' אחר**.
    סבב פתוח של האימג' הזה עצמו, לפני שהשידור התחיל (המכונות עוד עולות), אינו
    עוצר: זה החלון שבו הגיבוב שלו עוד יכול להגיע למניפסט שהסוכן קורא — ואם לא
    הגיע, הסבב "לא אומת", כמו קודם. ‏None = שקט."""
    def busy(image_id: str) -> str | None:
        status = sender.status() if sender is not None else None
        if status and status.get("state") == "sending":
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
    """‏`rank()` של התהליכון: ‏{image_id: הסבב או ההפצה האחרונים שנפתחו לו}."""
    def rank() -> dict[str, str]:
        rows = conn.execute(
            "SELECT image_id, MAX(created_at) AS at FROM (SELECT image_id, created_at FROM room_rounds"
            " UNION ALL SELECT image_id, created_at FROM sessions) GROUP BY image_id").fetchall()
        return {row[0]: row[1] for row in rows}
    return rank


def main(argv: list[str] | None = None) -> int:
    """הרצה יבשה: מה הגיבוב של כל מחיצה. אינה כותבת דבר."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m server.readbackhash <image_dir>", file=sys.stderr)
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
            text, count = digest_file(path, captured_raw(part), _whole(part.get("size_bytes")))
        except Underived as exc:
            ok = False
            print(f"p{index} {name} UNDERIVED: {exc}")
            continue
        print(f"p{index} {name} readback_xxh64={list_digest(text, xxh64_factory())}"
              f" readback_chunks={text.count(chr(10))} readback_bytes={count}"
              f" manifest={part.get('readback_xxh64') or 'none'}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
