"""השלמת `used_bytes` לאימג' ישן מכותרת partclone (‏#1494).

אימג' שנקלט לפני #1368 נושא `used_bytes: null` על מחיצות ה-NTFS, ולכן
כלל המקום הפנוי (‏#1354) "לא נבדק" — והסוכן מסרב לו בכל מגירה, בצדק
(עיקרון 5). אבל המדידה **קיימת על הדיסק**: כל קובץ מחיצה הוא
`partclone.<fs> -c | zstd`, ובראש הזרם הפרוס יושבת כותרת `image_desc_v2`
של partclone (‏110 בייט) עם `usedblocks` ו-`block_size`. כאן קוראים אותה —
פורסים רק את תחילת הזרם — וגוזרים `used_bytes = usedblocks × block_size`.

**נגזר, לא מומצא:** כותרת שלא עברה magic, גרסה `0002`, ‏endianess ו-CRC,
או שהערך שלה גדול מהמחיצה — המחיצה נשארת `null`. הערך נכתב ל-manifest.json
עצמו (`ImageLibrary.record_used_bytes`) ולצידו `used_bytes_source` — עיקרון 3:
הדיסק הוא מקור האמת, והסוכן קורא את `used_bytes` מהמניפסט שהשרת מגיש;
קובץ צד היה דורש שכל קורא יזכור למזג אותו.

הפורמט — מ-`src/partclone.h` ו-`src/checksum.c` של partclone (‏`pragma pack(1)`,
little-endian): magic[16] · ptc_version[14] · version[4] · endianess u16 ·
fs[16] · device_size u64 · totalblock u64 · superBlockUsedBlocks u64 ·
usedblocks u64 · block_size u32 · image_options_v2[18] · crc u32. ‏CRC-32
מוחזר (0xEDB88320), זרע 0xFFFFFFFF, **בלי** XOR סופי, על 106 הבייטים
הראשונים — כלומר `zlib.crc32(x) ^ 0xFFFFFFFF`.

`usedblocks` (ולא `superBlockUsedBlocks`) — מספר הבלוקים שבמפת הסיביות,
כלומר מה ש-partclone העתיק בפועל לזרם הזה.

הרצה יבשה לאימות במעבדה: `python -m server.library.usedbackfill <תיקיית-אימג'>`.
"""

from __future__ import annotations

import json
import logging
import struct
import subprocess
import sys
import threading
import zlib
from pathlib import Path

from server.library.images import _whole, carries_a_stream_file, guarded_image_path
from server.vm.vmdisk import partclone_for_fs

log = logging.getLogger("imagesrv.usedbackfill")

HEADER_SIZE = 110
SOURCE = "partclone_header"
_LAYOUT = struct.Struct("<15sx14s4sH16sQQQQI18xI")
assert _LAYOUT.size == HEADER_SIZE
_MAGIC = b"partclone-image"
_VERSION = b"0002"
_ENDIAN = 0xC0DE


def captured_raw(part: dict) -> bool:
    """האם הסוכן קלט את המחיצה ב-`partclone.dd` — ולכן אין בזרם כותרת (‏#1641).

    ‏`partclone.dd` מעתיק בייטים וזהו; כותרת `image_desc_v2` יש רק לשאר
    הכלים. הכלל הוא `partclone_for_fs` של הסוכן על `fs` שבמניפסט (‏MSR:
    ‏`fs: ""`) — **לא** שם הקובץ, שהוא `.pcl.zst` בשני המקרים, ולא "לא
    נמצאה כותרת": מחיצת NTFS/FAT שאין לה כותרת ממשיכה להיכשל בקול.
    """
    return partclone_for_fs(part.get("fs") or "") == "partclone.dd"


def parse_header(raw: bytes | None) -> dict | None:
    """הכותרת כמילון, או `None` כשאין ראיה שזו כותרת v2 שלמה ותקינה."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) < HEADER_SIZE:
        return None
    (magic, _ptc, version, endian, fs, device_size, total, _sb_used, used,
     block_size, crc) = _LAYOUT.unpack_from(raw)
    if magic != _MAGIC or version != _VERSION or endian != _ENDIAN:
        return None
    if crc != zlib.crc32(bytes(raw[:HEADER_SIZE - 4])) ^ 0xFFFFFFFF:
        return None
    if block_size == 0 or used > total:
        return None
    return {"fs": fs.split(b"\0", 1)[0].decode("ascii", "replace"),
            "device_size": device_size, "total_blocks": total,
            "used_blocks": used, "block_size": block_size}


def read_head(path: Path, *, cmd: tuple[str, ...] = ("zstd", "-dc", "--"),
              timeout: float = 30.0) -> bytes | None:
    """110 הבייטים הראשונים של הזרם הפרוס. ‏`None` = הפורס לא עלה (‏zstd חסר).

    התהליך נהרג אחרי הקריאה בכוונה — אין טעם לפרוס עשרות GB — ולכן קוד
    היציאה שלו אינו הראיה; ה-CRC של הכותרת הוא. הטיימר תוחם קריאה תקועה
    (קובץ על מיקום רשת שנפל), כדי שהשלמה ברקע לא תחכה לנצח.
    """
    try:
        proc = subprocess.Popen([*cmd, str(path)], stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError as exc:
        log.warning("cannot run %s: %s", cmd[0], exc)
        return None
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    data = b""
    try:
        while len(data) < HEADER_SIZE:
            chunk = proc.stdout.read(HEADER_SIZE - len(data))
            if not chunk:
                break
            data += chunk
    finally:
        timer.cancel()
        proc.kill()
        proc.stdout.close()
        proc.wait()
    return data


def derive(manifest: dict, *, read=read_head) -> tuple[dict[int, int], list[str]]:
    """‏({index: used_bytes}, בעיות) למחיצות שלא נמדדו ויש להן קובץ זרם."""
    image_dir = manifest.get("_dir")
    if not image_dir or not manifest.get("_available", True):
        return {}, []
    values: dict[int, int] = {}
    problems: list[str] = []
    for part in manifest.get("partitions") or []:
        if not isinstance(part, dict) or part.get("used_bytes") is not None \
                or not carries_a_stream_file(part) or captured_raw(part):
            continue
        index = part.get("index")
        path = guarded_image_path(image_dir, part.get("file"))
        if path is None:
            problems.append(f"p{index}: file missing or unsafe")
            continue
        raw = read(path)
        if raw is None:
            problems.append(f"p{index}: could not run zstd")
            continue
        header = parse_header(raw)
        if header is None:
            problems.append(f"p{index}: no valid partclone v2 header")
            continue
        used = header["used_blocks"] * header["block_size"]
        size = _whole(part.get("size_bytes"))
        if size is None or used > size:
            problems.append(f"p{index}: header says {used} bytes used,"
                            f" more than the partition ({size})")
            continue
        values[index] = used
    return values, problems


def backfill(library, *, read=read_head, report=None) -> list[dict]:
    """מעבר אחד על הספרייה. אימג' אחד שנכשל אינו עוצר את האחרים."""
    results = []
    for image_id, manifest in library.scan().items():
        written: dict[int, int] = {}
        try:
            values, problems = derive(manifest, read=read)
            if values:
                done = library.record_used_bytes(image_id, values, SOURCE)
                written = {index: values[index] for index in done}
                if not done:
                    problems.append("manifest not updated")
        except (OSError, ValueError) as exc:
            values, problems = {}, [f"{type(exc).__name__}: {exc}"]
        if not values and not problems:
            continue
        result = {"image_id": image_id, "written": written, "problems": problems}
        results.append(result)
        if report is not None:
            report(result)
    return results


def main(argv: list[str] | None = None) -> int:
    """הרצה יבשה: מה הכותרת אומרת על כל מחיצה. אינה כותבת דבר."""
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: python -m server.library.usedbackfill <image_dir>", file=sys.stderr)
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
        if captured_raw(part):
            print(f"p{index} {name} RAW DD CAPTURE (no partclone header)")
            continue
        path = guarded_image_path(image_dir, name)
        header = parse_header(read_head(path)) if path is not None else None
        if header is None:
            ok = False
            print(f"p{index} {name} NO VALID HEADER")
            continue
        used = header["used_blocks"] * header["block_size"]
        current = part.get("used_bytes")
        print(f"p{index} {name} fs={header['fs']} block_size={header['block_size']}"
              f" used_blocks={header['used_blocks']} total_blocks={header['total_blocks']}"
              f" used_bytes={used}"
              f" manifest_used_bytes={'null' if current is None else current}"
              f" size_bytes={part.get('size_bytes')}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
