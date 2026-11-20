"""אימג' חתיכות — `image_format` 2 (#2533, #2528 שלב 2).

עד כאן קובץ מחיצה היה זרם zstd **אחד** של פלט partclone: כדי להגיע לבייט
ה-100GB היה צריך לפרוס 100GB, ולכן אי אפשר היה לבקש "את מה שפספסתי" (#2533),
להמשיך מאמצע כתיבה שנקטעה (#2528) או לבדוק חתיכה אחת לבדה. בפורמט 2 אותו
קובץ, באותו שם, בנוי מ**פריימים עצמאיים** של zstd — הראש (כותרת partclone
ומפת הסיביות; אין במחיצה גולמית) ואחריו פריים לכל 4MiB של זרם הנתונים —
ו-`zstd -dc` עדיין נותן את אותם בייטים, ולכן הצינור של היום קורא אותו כמו
שהוא. החוזה המלא: `docs/architecture/interfaces.md` סעיף 7 ("אימג' חתיכות").

ליד קובץ המחיצה יושב **האינדקס** (`<file>.idx`): שורת כותרת, ושורה לחתיכה —
היסט בקובץ, אורך הפריים, ו-XXH64 של תוכן החתיכה הפרוס. את האינדקס מייצר
הסוכן שקולט (`agent/chunkcut.c`), והמניפסט נושא את ה-XXH64 שלו
(`index_xxh64`). **השרת אינו מקבל את האינדקס — הוא גוזר אותו בעצמו מהקובץ**
(`derive_index`): גבולות הפריימים מקריאת כותרות ה-zstd, וה-hash מפריסה של
הקובץ. שתי הגזירות נפגשות רק אם כל היסט, כל אורך וכל hash זהים, ואז — ורק
אז — האינדקס נכתב ליד הקובץ. כך גם אימג' שהועתק בלי האינדקס שלו (העברה בין
אתרים, ייבוא) מקבל אותו בחזרה, מאומת מול המניפסט, בלי שכל נתיב העתקה יזכור
קובץ נוסף.

**ראיה חיובית בכל שלב** (עיקרון 5): כל פריים מצהיר על אורך התוכן שלו ונושא
checksum; אין בייט בקובץ שאינו שייך לפריים; אורכי הפריימים הם בדיוק הראש
ואז 4MiB לחתיכה (האחרונה קצרה); zstd יצא 0. כל אחד מהם שלא עבר —
`BadImage`: הבדיקה רצה והקובץ פגום, וזה נרשם (`index_error`) והאימג' אינו
מופץ. בדיקה ש**לא רצה** (הפורס לא עלה, נהרג, השרת פינה את הדרך) —
`Underived`, ואין הכרעה.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from server.agent.readbackhash import (B3Lister, B3Problem, BadImage, CHUNK_BYTES, Underived, b3_tool, digest_file,
                                       xxh64_factory)
from server.library.images import IMAGE_FORMAT
from server.library.usedbackfill import HEADER_SIZE, parse_header

INDEX_MAGIC = "IMAGESRV-IDX"
INDEX_SUFFIX = ".idx"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"           # 0xFD2FB528, little-endian
MAX_HEAD_BYTES = HEADER_SIZE + (1 << 31) + 4   # readbackhash.MAX_BITMAP
_HEX = set("0123456789abcdef")


class IndexProblem(Exception):
    """האינדקס אינו עקבי עם עצמו — הסיבה בהודעה."""


@dataclass(frozen=True)
class Index:
    kind: str                    # "partclone" | "raw"
    head_bytes: int
    head_zbytes: int
    head_xxh64: str | None       # None במחיצה גולמית
    data_bytes: int
    file_zbytes: int
    rows: tuple[tuple[int, int, str], ...]   # (היסט, אורך הפריים, XXH64) לכל חתיכה
    # #3332: רשימת ה-BLAKE3 אינה חלק מהאינדקס (אינה בקובץ ה-`.idx`) ולכן אינה נכנסת להשוואה: `derive_index(...) == parse_index(text)`
    b3: tuple[bytes, str] | None = field(default=None, compare=False)   # (רשימת BLAKE3, ה-BLAKE3 שלה) -- רק מ-derive_index
    b3_note: str | None = field(default=None, compare=False)            # למה אין רשימה, כשהכלי כאן והיא לא יצאה שלמה

    @property
    def chunks(self) -> int:
        return len(self.rows)

    def chunk_bytes(self, k: int) -> int:
        """אורך התוכן הפרוס של חתיכה k."""
        return min(CHUNK_BYTES, self.data_bytes - k * CHUNK_BYTES)

    def readback_list(self) -> str:
        """רשימת ה-readback של #1864 — העמודה השלישית, שורה לחתיכה."""
        return "".join(row[2] + "\n" for row in self.rows)

    def render(self) -> str:
        head = (f"{INDEX_MAGIC} {IMAGE_FORMAT} {self.kind} {CHUNK_BYTES} {self.head_bytes}"
                f" {self.head_zbytes} {self.head_xxh64 or '-'} {self.data_bytes} {self.chunks}"
                f" {self.file_zbytes}\n")
        return head + "".join(f"{o} {z} {x}\n" for o, z, x in self.rows)


def _number(text: str, what: str) -> int:
    if not text.isascii() or not text.isdigit() or (len(text) > 1 and text[0] == "0"):
        raise IndexProblem(f"{what} is not a plain decimal number: {text!r}")
    return int(text)


def _hash(text: str, what: str) -> str:
    if len(text) != 16 or not set(text) <= _HEX:
        raise IndexProblem(f"{what} is not 16 lowercase hex digits: {text!r}")
    return text


def parse_index(text: str) -> Index:
    """האינדקס כמבנה, אחרי כל כללי העקביות הפנימיים — או `IndexProblem`.

    מה שאינו נבדק כאן, כי הוא דורש את הקובץ: ש-`file_zbytes` הוא גודל קובץ
    המחיצה בפועל (`size_problem`), ושהפריימים אכן יושבים בהיסטים (`derive_index`)."""
    if not isinstance(text, str) or not text.endswith("\n"):
        raise IndexProblem("the index does not end with a newline")
    lines = text[:-1].split("\n")
    head = lines[0].split(" ")
    if len(head) != 10 or head[0] != INDEX_MAGIC:
        raise IndexProblem("the first line is not an index header")
    if head[1] != str(IMAGE_FORMAT):
        raise IndexProblem(f"index version {head[1]!r}, not {IMAGE_FORMAT}")
    kind = head[2]
    if kind not in ("partclone", "raw"):
        raise IndexProblem(f"unknown kind {kind!r}")
    if _number(head[3], "chunk_bytes") != CHUNK_BYTES:
        raise IndexProblem(f"chunk_bytes {head[3]}, not {CHUNK_BYTES}")
    head_bytes, head_zbytes = _number(head[4], "head_bytes"), _number(head[5], "head_zbytes")
    data_bytes, chunks = _number(head[7], "data_bytes"), _number(head[8], "chunks")
    file_zbytes = _number(head[9], "file_zbytes")
    if kind == "raw":
        if head_bytes or head_zbytes or head[6] != "-":
            raise IndexProblem("a raw partition has no head")
        head_xxh = None
    else:
        if head_bytes < HEADER_SIZE + 4 or head_bytes > MAX_HEAD_BYTES or not head_zbytes:
            raise IndexProblem(f"a partclone head of {head_bytes} bytes in {head_zbytes}")
        head_xxh = _hash(head[6], "head_xxh64")
    if chunks != (data_bytes + CHUNK_BYTES - 1) // CHUNK_BYTES:
        raise IndexProblem(f"{chunks} chunks for {data_bytes} data bytes")
    if len(lines) - 1 != chunks:
        raise IndexProblem(f"{len(lines) - 1} chunk lines, the header says {chunks}")
    rows, at = [], head_zbytes
    for k, line in enumerate(lines[1:]):
        fields = line.split(" ")
        if len(fields) != 3:
            raise IndexProblem(f"chunk {k}: not three fields")
        offset, zbytes = _number(fields[0], f"chunk {k} offset"), _number(fields[1], f"chunk {k} length")
        if offset != at:
            raise IndexProblem(f"chunk {k} starts at {offset}, the frame before it ends at {at}")
        if not zbytes:
            raise IndexProblem(f"chunk {k} is an empty frame")
        rows.append((offset, zbytes, _hash(fields[2], f"chunk {k} xxh64")))
        at += zbytes
    if at != file_zbytes:
        raise IndexProblem(f"the frames end at {at}, the header says the file is {file_zbytes}")
    return Index(kind, head_bytes, head_zbytes, head_xxh, data_bytes, file_zbytes, tuple(rows))


def index_path(partition_file: Path) -> Path:
    return partition_file.with_name(partition_file.name + INDEX_SUFFIX)


def read_index(partition_file: Path) -> Index | None:
    """האינדקס שליד קובץ המחיצה, אם הוא שם ועקבי עם עצמו ועם גודל הקובץ.
    `None` = אין אינדקס שאפשר להישען עליו (חסר, קישור סמלי, לא נקרא, לא עקבי)."""
    path = index_path(partition_file)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        index = parse_index(path.read_text(encoding="ascii"))
        return index if index.file_zbytes == partition_file.stat().st_size else None
    except (OSError, ValueError, IndexProblem):
        return None


# --- פריימים של zstd (RFC 8878) ------------------------------------------------


def frame_header(buf: bytes) -> tuple[int, int | None, bool]:
    """(אורך הכותרת, Frame_Content_Size או None, יש checksum) של פריים שמתחיל ב-`buf`."""
    if len(buf) < 6 or buf[:4] != ZSTD_MAGIC:
        raise BadImage("no zstd frame starts here")
    fhd = buf[4]
    fcs_flag, single, checksum, dict_flag = fhd >> 6, bool(fhd & 0x20), bool(fhd & 0x04), fhd & 0x03
    if fhd & 0x08:
        raise BadImage("a reserved bit is set in the frame header")
    at = 5 + (0 if single else 1) + (0, 1, 2, 4)[dict_flag]
    size = (1 if single else 0, 2, 4, 8)[fcs_flag]
    if len(buf) < at + size:
        raise BadImage("the frame header is cut short")
    content = int.from_bytes(buf[at:at + size], "little") + (256 if fcs_flag == 1 else 0) if size else None
    return at + size, content, checksum


def walk_frames(path: Path):
    """כל פריים בקובץ, לפי הסדר: (היסט, אורך, אורך התוכן, יש checksum). קורא רק
    כותרות (פריים ובלוקים) — לא פורס. בייט שאינו שייך לפריים — `Underived`."""
    size = path.stat().st_size
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        at = 0
        while at < size:
            start = at
            header, content, checksum = frame_header(_pread(fd, 18, at))
            at += header
            while True:
                block = _pread(fd, 3, at)
                if len(block) != 3:
                    raise BadImage(f"the frame at {start} is cut inside a block header")
                value = int.from_bytes(block, "little")
                last, kind, length = value & 1, (value >> 1) & 3, value >> 3
                if kind == 3:
                    raise BadImage(f"a reserved block type in the frame at {start}")
                at += 3 + (1 if kind == 1 else length)      # RLE: בייט אחד, כל אורך
                if last:
                    break
            at += 4 if checksum else 0
            if at > size:
                raise BadImage(f"the frame at {start} runs past the end of the file")
            yield start, at - start, content, checksum
    finally:
        os.close(fd)


def _pread(fd: int, n: int, at: int) -> bytes:
    if hasattr(os, "pread"):
        return os.pread(fd, n, at)
    os.lseek(fd, at, os.SEEK_SET)                           # תחנת הפיתוח (ווינדוס)
    return os.read(fd, n)


#: #3124 (A-DSK-28): פריים אחד הוא עד 4MiB ונפרס במילי-שניות; פורס שלא חזר תוך כך תקוע, וה-derive
#: ברקע היה ממתין לו לנצח.
DECODE_TIMEOUT_S = 60.0


def decode_frame(frame: bytes, *, cmd: tuple[str, ...] = ("zstd", "-dc")) -> bytes:
    """פריים אחד, נפרס לבדו. `Underived` כשהפורס לא עלה, לא יצא 0, או לא חזר תוך `DECODE_TIMEOUT_S`."""
    timeout_s = DECODE_TIMEOUT_S
    try:
        done = subprocess.run(list(cmd), input=frame, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, check=False, timeout=timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise Underived(f"{cmd[0]} did not finish in {timeout_s:g} s -- stopped; the frame was not judged") from exc
    except OSError as exc:
        raise Underived(f"cannot run {cmd[0]}: {exc}") from exc
    if done.returncode != 0:
        kind = BadImage if done.returncode > 0 else Underived       # refused by the tool, or the tool was killed
        raise kind(f"{cmd[0]} exited {done.returncode} -- the frame did not decompress")
    return done.stdout


def chunk_problem(index: Index, k: int, frame: bytes, *, cmd: tuple[str, ...] = ("zstd", "-dc"),
                  new=None) -> str | None:
    """חתיכה `k` שנשלפה לבדה (Range לפי האינדקס): הסיבה שאסור לכתוב אותה, או
    `None` כשהיא נפרסה לאורך הנכון וה-XXH64 שלה הוא זה שבאינדקס."""
    if not 0 <= k < index.chunks:
        return f"chunk {k} is not one of the {index.chunks}"
    offset, zbytes, want = index.rows[k]
    if len(frame) != zbytes:
        return f"chunk {k}: {len(frame)} bytes, the index says the frame is {zbytes}"
    try:
        header, content, checksum = frame_header(frame)
        data = decode_frame(frame, cmd=cmd)
    except Underived as exc:
        return f"chunk {k}: {exc}"
    if content != index.chunk_bytes(k) or len(data) != content:
        return f"chunk {k}: {len(data)} bytes decompressed, {index.chunk_bytes(k)} expected"
    digest = (new or xxh64_factory())()
    digest.update(data)
    got = digest.hexdigest()
    return None if got == want else f"chunk {k}: xxh64 {got}, the index says {want}"


def _read_at(path: Path, offset: int, n: int) -> bytes:
    with path.open("rb") as handle:
        handle.seek(offset)
        return handle.read(n)


def derive_index(path: Path, raw: bool, limit: int | None = None, stop=None, *,
                 cmd: tuple[str, ...] | None = None, frame_cmd: tuple[str, ...] = ("zstd", "-dc"),
                 new=None, progress=None, b3=b3_tool) -> Index:
    """האינדקס של קובץ מחיצה בפורמט 2, מהקובץ עצמו — או `Underived`.

    גבולות הפריימים מהכותרות (`walk_frames`); ה-hash של כל חתיכה מפריסה אחת
    של הקובץ כולו (`digest_file`: כותרת partclone, CRC של המפה, מניין הסיביות,
    EOF במקום, zstd יצא 0); והראש — מפריסה של הפריים הראשון לבדו. ואז
    ההצלבה: הפריים הראשון הוא בדיוק הראש, וכל פריים אחריו בדיוק חתיכה אחת.

    #3332: `b3()` = ה-`chunkcast` שמייצר את רשימת ה-BLAKE3 באותה פריסה, או None (אין, ובלי רשימה)."""
    new = new or xxh64_factory()
    frames = []
    for offset, zbytes, content, checksum in walk_frames(path):
        if content is None or not checksum:
            raise BadImage(f"the frame at {offset} does not declare its content size and checksum"
                            " -- not an image_format 2 file")
        frames.append((offset, zbytes, content))
    if not frames:
        raise BadImage("no frame in the file")
    head_bytes = head_zbytes = 0
    head_xxh = None
    chunk_frames = frames
    if not raw:
        if frames[0][2] > MAX_HEAD_BYTES:
            raise BadImage(f"the first frame declares {frames[0][2]} bytes -- not a head")
        head = decode_frame(_read_at(path, 0, frames[0][1]), cmd=frame_cmd)
        if len(head) < HEADER_SIZE + 4:
            raise BadImage("the first frame is shorter than a partclone header")
        if int.from_bytes(head[96:98], "little") != 0:
            raise BadImage("the stream carries partclone checksums -- image_format 2 is read with -a0")
        head_bytes = HEADER_SIZE + (int.from_bytes(head[60:68], "little") + 7) // 8 + 4
        if len(head) != head_bytes or frames[0][2] != head_bytes:
            raise BadImage(f"the first frame holds {len(head)} bytes, the head is {head_bytes}")
        digest = new()
        digest.update(head)
        head_zbytes, head_xxh, chunk_frames = frames[0][1], digest.hexdigest(), frames[1:]
    # #3137: `progress(read, total)` -- the decompressed bytes read of all that the frames declare
    whole = sum(content for _, _, content in frames)
    count = (lambda n: progress(n, whole)) if progress is not None else None
    tool = b3() if b3 is not None else None
    lister = B3Lister(tool) if tool else None
    try:
        text, data_bytes = digest_file(path, raw, limit, stop, cmd=cmd, new=new, count=count, b3=lister)
    except BaseException:
        if lister is not None:
            lister.kill()
        raise
    hashes = text.splitlines()
    b3_list, b3_note = None, None
    if lister is not None:
        try:
            b3_list = lister.finish(len(hashes))
        except B3Problem as exc:
            b3_note = str(exc)
    if len(chunk_frames) != len(hashes):
        raise BadImage(f"{len(chunk_frames)} chunk frames for {len(hashes)} chunks of data")
    rows = []
    for k, ((offset, zbytes, content), xxh) in enumerate(zip(chunk_frames, hashes)):
        want = min(CHUNK_BYTES, data_bytes - k * CHUNK_BYTES)
        if content != want:
            raise BadImage(f"the frame of chunk {k} holds {content} bytes, not {want}")
        rows.append((offset, zbytes, xxh))
    return Index("raw" if raw else "partclone", head_bytes, head_zbytes, head_xxh, data_bytes,
                 path.stat().st_size, tuple(rows), b3_list, b3_note)


# --- לחתוך בשרת: שמירת גרסה מ-VM (#1334) ---------------------------------------


class CutProblem(Exception):
    """הזרם אינו מה שהוא חייב להיות, או שהדוחס נכשל — הסיבה בהודעה. אין אינדקס."""


def _take(stream, n: int) -> bytes:
    """עד `n` בייטים; פחות — רק בסוף הזרם."""
    parts, left = [], n
    while left:
        data = stream.read(left)
        if not data:
            break
        parts.append(data)
        left -= len(data)
    return b"".join(parts)


def frame_argv(data: bytes, level: int) -> list[str]:
    """הפקודה שדוחסת `data` לפריים אחד: `--stream-size` כותב את אורך התוכן בכותרת,
    וה-checksum הוא ברירת המחדל של הכלי — שתי הדרישות של הפורמט מכל פריים."""
    return ["zstd", "-q", f"-{level}", f"--stream-size={len(data)}", "-c"]


def _frame(data: bytes, level: int) -> bytes:
    try:
        done = subprocess.run(frame_argv(data, level), input=data, stdout=subprocess.PIPE,
                              stderr=subprocess.DEVNULL, check=False)
    except OSError as exc:
        raise CutProblem(f"cannot run zstd: {exc}") from exc
    if done.returncode != 0 or not done.stdout:
        raise CutProblem(f"zstd exited {done.returncode} on a chunk of {len(data)} bytes")
    return done.stdout


def cut(stream, out, raw: bool, *, level: int = 3, limit: int | None = None, on_bytes=None,
        compress=None, new=None) -> tuple[str, str, int]:
    """חותך זרם partclone (`-a0`) לקובץ מחיצה בפורמט 2, בשרת — מה ש-`agent/chunkcut.c`
    עושה במחשב הבנייה. (טקסט האינדקס, sha256 של הקובץ, בייטים שנכתבו).

    לשמירת גרסה מ-VM בלבד (`server/vm/vmdisk.py`): כל חתיכה נדחסת בקריאה ל-`zstd`
    של המערכת, אחת אחרי השנייה — איטי מהכלי ב-C, ומספיק לדיסק אחד בשרת. אותן
    בדיקות על הזרם: כותרת v2 שלמה, בלי סכומי בדיקה בתוכו, מפה שמסכימה עם הכותרת,
    וסוף במקום — לא לפניו ולא אחריו. `limit` — קובץ שגדל מעבר לזה נעצר.
    `compress(data) -> פריים` — מי שמריץ את הדוחס (vmdisk: דרך ה-`Host` שלו)."""
    new = new or xxh64_factory()
    compress = compress or (lambda data: _frame(data, level))
    sha, total, rows = hashlib.sha256(), 0, []

    def emit(frame: bytes) -> None:
        nonlocal total
        sha.update(frame)
        out.write(frame)
        total += len(frame)
        if on_bytes is not None:
            on_bytes(len(frame))
        if limit is not None and total > limit:
            raise CutProblem(f"the file grew past {limit} bytes")

    def digest(data: bytes) -> str:
        h = new()
        h.update(data)
        return h.hexdigest()

    head_bytes = head_zbytes = 0
    head_xxh, want = None, None
    if not raw:
        first = _take(stream, HEADER_SIZE)
        header = parse_header(first)
        if header is None:
            raise CutProblem("no valid partclone v2 header")
        if int.from_bytes(first[92:94], "little") != 2 or first[105] != 1:
            raise CutProblem("not a v2 bit map image")
        if int.from_bytes(first[96:98], "little") != 0:
            raise CutProblem("the stream carries partclone checksums -- it must be read with -a0")
        map_bytes = (header["total_blocks"] + 7) // 8
        if HEADER_SIZE + map_bytes + 4 > MAX_HEAD_BYTES:
            raise CutProblem(f"implausible header: {header['total_blocks']} blocks")
        head = first + _take(stream, map_bytes + 4)
        if len(head) != HEADER_SIZE + map_bytes + 4:
            raise CutProblem("the stream ended inside the bit map")
        bits = bytearray(head[HEADER_SIZE:HEADER_SIZE + map_bytes])
        if header["total_blocks"] % 8:
            bits[-1] &= (1 << (header["total_blocks"] % 8)) - 1
        if int.from_bytes(bits, "little").bit_count() != header["used_blocks"]:
            raise CutProblem("the bit map does not mark the blocks its header counts")
        head_bytes, head_xxh = len(head), digest(head)
        frame = compress(head)
        head_zbytes = len(frame)
        emit(frame)
        want = header["used_blocks"] * header["block_size"]
    data_bytes = 0
    while want is None or data_bytes < want:
        chunk = _take(stream, CHUNK_BYTES if want is None else min(CHUNK_BYTES, want - data_bytes))
        if not chunk:
            break
        frame = compress(chunk)
        rows.append((total, len(frame), digest(chunk)))
        emit(frame)
        data_bytes += len(chunk)
    if want is not None and data_bytes != want:
        raise CutProblem(f"the stream ended {want - data_bytes} bytes early")
    if want is not None and stream.read(1):
        raise CutProblem("bytes after the last block")
    if raw and not data_bytes:
        raise CutProblem("an empty stream")
    index = Index("raw" if raw else "partclone", head_bytes, head_zbytes, head_xxh, data_bytes, total, tuple(rows))
    return index.render(), sha.hexdigest(), total
