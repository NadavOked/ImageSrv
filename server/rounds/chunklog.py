"""הלוג של `chunkcast send` (#3261): איפה הוא נכתב, ואיך קוראים ממנו את סיבת הכשל.

`chunkcast send` אומר בעצמו למה נפל (`ir_say`: "cannot send to ...: Operation not permitted", "... ended inside
chunk N", "the socket refused a packet: ..."), אבל הוא נופל עם קוד יציאה בלבד, והמפעיל ראה רק את הקוד.
כאן הפלט של התהליך נכתב לקובץ ליד מחיצה (אף אחד לא קורא את הצינור, וצינור
שלא נקרא נחסם כשהחוצץ מתמלא), וכאן קוראים את סופו.

**אין כאן תלות ב-`sender.py`.** הפונקציות האלה הן התאומים של `partition_log`, `run_process` ו-`sender_log_tail`
שבו, והן יושבות בקוד שנשאר כש-#3173 מוחק אותו.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from pathlib import Path

from server.system import priority

log = logging.getLogger("imagesrv.chunklog")

#: `chunkcast send` כותב `sent <chunks> <bytes>` כל שנייה (agent/chunkcast.c) ו-`done ...` בסוף: אלה התקדמות,
#: לא סיבה. כל שורה אחרת היא משפט של `ir_say`. #3125 (V4): `paced <wire_ms> <disk_ms>` אחרי כל `sent` ולפני `done` --
#: כמה זמן השולח המתין ל-`--kbps` ול-`--write-kbps`. גם היא התקדמות.
#: #3512: גם שורות משוב הקצב -- `rate <kbps> <reason>`, `feedback malformed=N`, `left-behind <id> <ip> <pass>` -- התקדמות, לא סיבה.
#: #3522: וגם `knobs ...` (הערכים שהשולח רץ איתם, בתחילת המחיצה) ו-`sendstats ...` (מונים, פעם אחת לפני ה-`paced` האחרון) -- ראיה
#: לניסוי, לא סיבת כישלון.
#: #3535: שורת `knobs` נושאת גם `pace=<legacy|smooth> batch=<n>`, ו-`sendstats` גם `over_ms=<n>` בסופה; שתי הצורות (עם ובלי) מוכרות.
#: #3535 (תהליכון קורא): `knobs` גם `reader=<inline|thread> readq=<n>` אחרי `batch`, ו-`sendstats` גם `readq_empty_ms=<n> readq_full_ms=<n>` אחרי `over_ms`.
#: #3124 (B-NET-18): `sndbuf <asked> <granted>` -- the send buffer the kernel gave (-1 = not read), once at the start; evidence, not a reason.
_PROGRESS = re.compile(r"(sent \d+ \d+|paced \d+ \d+|sndbuf \d+ -?\d+|done \d+ \d+ -?\d+|rate \d+ [a-z-]+|feedback malformed=\d+"
                       r"|left-behind [0-9a-f]{16} \S+ \d+"
                       r"|knobs ahead=\d+ readahead=(?:legacy|on) order=(?:seq|mixed) fb_low=\d+ fb_high=\d+ qcap=\d+"
                       r"(?: pace=(?:legacy|smooth) batch=\d+(?: reader=(?:inline|thread) readq=\d+)?)?"
                       r"|sendstats read_ms=\d+ lag_forgiven_wire=\d+ lag_forgiven_disk=\d+ forgiven_ms_wire=\d+"
                       r" forgiven_ms_disk=\d+ enobufs=\d+ eagain=\d+ eintr=\d+ refused=\d+(?: over_ms=\d+(?: readq_empty_ms=\d+ readq_full_ms=\d+)?)?)")

#: #3498: `paced <wire_ms> <disk_ms>` -- the sender's own count (cumulative) of how long it slept for `--kbps` and for `--write-kbps`.
_PACED = re.compile(r"paced (\d+) (\d+)")

#: #3553: `sendstats read_ms=<n> ... [readq_empty_ms=<n> readq_full_ms=<n>]` -- פעם אחת, בסוף הריצה (agent/chunkcast.c); `knobs ... reader=<inline|thread>`:
#: מי קורא את הקובץ. בקורא `inline` השולח עצמו נחסם ב-`read_ms`; בתהליכון קורא `read_ms` הוא זמן התהליכון (במקביל לשליחה), והשולח נחסם רק
#: ב-`readq_empty_ms` (התור ריק, ממתין לחתיכה).
_NUMBER = re.compile(r"(\w+)=(\d+)")
_READER = re.compile(r" reader=(inline|thread)(?: |$)")

#: כמה תווים מהשורה האחרונה מגיעים למסך.
TAIL_LIMIT = 400

#: נקרא בכל קריאה, לא נקבע ביבוא: בדיקה שמנתבת אותו מחדש (למשל ל-tmp) מנתבת גם את הלוגים שנגזרים ממנו.
LOG_DIR: Path | None = None


def log_path(part_file: str | Path) -> Path:
    """הלוג של מחיצה, נגזר מהתיקייה ושם הקובץ שלה: הראיה של מחיצה קודמת שורדת את השידור של הבאה."""
    parts = [p for p in Path(part_file).parts[-2:] if p not in ("/", "\\")]
    return (LOG_DIR or Path(tempfile.gettempdir())) / f"imagesrv-chunkcast-{'-'.join(parts)}.log"


#: #3535: מי קורא את קובץ המקור ב-`chunkcast send`, ומה אסור יחד אתו. ברירת המחדל של **הבינארי** נשארת `inline` (המשתנה אינו נקבע בו):
#: מחשב הבנייה שולח עם `--stdin` ותיקון עם `--device`, ו-`thread` יוצא שם עם 2. השרת מבקש אותו רק ל-`--file`. ברזל, 16/11 (LabRoot,
#: משכפל 2, `img_3fa17f`, מחיצה 3 בגודל 17.8GB; `lab/metal-1116-r16/results.md` ו-`r18`): `thread` 317, 318, 318, 321 ש'; `inline` 326, 328,
#: 328, 330 ש' (n=4 בכל צד, -9.5 ש', 2.9%, הטווחים אינם חופפים).
READER_ENV, AHEAD_ENV = "CHUNKCAST_READER", "CHUNKCAST_AHEAD"
READER_THREAD = "thread"


def sender_env(cmd: list[str], environ: dict[str, str] | None = None) -> dict[str, str] | None:
    """הסביבה של `chunkcast send` שהשרת מתחיל (#3535): `CHUNKCAST_READER=thread` על שליחה של `--file`; `None` = בלי שינוי, הסביבה של
    השרת עצמו.

    `None` גם כשהמפעיל קבע את `CHUNKCAST_READER` (כל ערך, גם ריק או שגוי: השולח מסרב לו בשמו, יציאה 2, והגל נכשל בגלוי -- הערך
    שלו גובר, וכך חוזרים ל-`inline`: `Environment=CHUNKCAST_READER=inline` ביחידה), וגם כשקבע `CHUNKCAST_AHEAD` (אין read-ahead
    ב-`thread`, והשולח מסרב ליחד). שילוב אסור שהמפעיל עשה בעצמו אינו מתוקן כאן ואינו נופל בשקט ל-`inline`: זה כישלון גלוי. `--stdin` ו-`--device`
    אינם נבנים בשרת, אבל נשללים כאן במפורש."""
    environ = os.environ if environ is None else environ
    if READER_ENV in environ or AHEAD_ENV in environ:
        return None
    if "--file" not in cmd or "--stdin" in cmd or "--device" in cmd:
        return None
    return {**environ, READER_ENV: READER_THREAD}


def run_process(cmd: list[str]) -> subprocess.Popen:
    """`chunkcast send` בעדיפות הרקע (#1974), פלט ושגיאות לקובץ המחיצה שנגזר מ-`--file`. לא `stdout=PIPE`:
    אף אחד לא קורא אותו, והתהליך נחסם על הפלט של עצמו כשהצינור מתמלא (#22). הסביבה: `sender_env` (#3535)."""
    with log_path(cmd[cmd.index("--file") + 1]).open("wb") as handle:
        return subprocess.Popen(priority.background_cmd(cmd), stdout=handle, stderr=subprocess.STDOUT, env=sender_env(cmd))


def paced_of(path: Path) -> tuple[int, int] | None:
    """#3498: (wire_ms, disk_ms) -- the last `paced` line of the sender's log: how long it slept to keep to `--kbps` and
    to `--write-kbps`. None = not read or no such line (a log that is missing, or a sender that is not `chunkcast`): "not
    measured" (principle 5), not zero -- zero is a sender that never slept."""
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        log.warning("chunkcast send log %s unreadable: %s", path, exc)
        return None
    return parse_paced(text)


def parse_paced(text: str, *, whole: bool = True) -> tuple[int, int] | None:
    """The one reader of the `paced` line (#3498 K8 reads the file through `paced_of`, #3505 V4 reads the tail of a live
    log): the last `paced <wire_ms> <disk_ms>` of `text`, or None when there is none. Only a line that ended with a
    newline counts (the last one may be half written), and the first line of a tail read from the middle is dropped
    (`whole=False`)."""
    lines = text.split("\n")[:-1]
    if not whole and lines:
        lines = lines[1:]
    got = None
    for line in lines:
        m = _PACED.fullmatch(line.strip())
        if m:
            got = (int(m[1]), int(m[2]))
    return got


def log_tail(path: Path, limit: int = TAIL_LIMIT) -> str:
    """השורה האחרונה שאינה התקדמות, או "" כשהלוג לא נקרא או שאין בו משפט.

    "" אינו "השולח לא אמר דבר" -- הוא "לא ידוע" (עיקרון 5): הקורא מוסיף את הסיבה רק כשיש, ולא ממציא אחת."""
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        log.warning("chunkcast send log %s unreadable: %s", path, exc)
        return ""
    # מסננים שורות שלמות לפני החיתוך: חיתוך קודם יכול להפוך שורת התקדמות לשבר שאי אפשר לזהות
    lines = [line.strip() for line in text.splitlines()]
    said = [line for line in lines if line and not _PROGRESS.fullmatch(line)]
    return said[-1][:limit].strip() if said else ""


def sendstats_of(path: Path) -> dict | None:
    """#3553: what the sender's log says of how long it waited for the library file: `{"read_ms", "readq_empty_ms", "reader"}` of the
    last `sendstats` line (`reader`: "inline" or "thread", from the last `knobs` line, None when it has none; `readq_empty_ms`: None
    for a sender that does not print it). None = the log is missing or has no whole `sendstats` line (a sender killed before it
    printed, or not `chunkcast`): "not measured" (principle 5), not zero -- a sender that never waited prints `read_ms=0`."""
    try:
        text = path.read_text(errors="replace")
    except OSError as exc:
        log.warning("chunkcast send log %s unreadable: %s", path, exc)
        return None
    return parse_sendstats(text)


def parse_sendstats(text: str) -> dict | None:
    """The reader of `sendstats_of`. Only a line that ended with a newline counts (the last one may be half written), and only one
    that is exactly what `chunkcast` prints (`_PROGRESS`)."""
    got, reader = None, None
    for line in text.split("\n")[:-1]:
        line = line.strip()
        if not _PROGRESS.fullmatch(line):
            continue
        if line.startswith("sendstats "):
            fields = {k: int(v) for k, v in _NUMBER.findall(line)}
            got = {"read_ms": fields["read_ms"], "readq_empty_ms": fields.get("readq_empty_ms")}
        elif line.startswith("knobs "):
            m = _READER.search(line)
            reader = m[1] if m else None
    return None if got is None else {**got, "reader": reader}


def add_sendstats(a: dict | None, b: dict | None) -> dict | None:
    """#3553: the `sendstats_of` of two runs, summed. None (not measured) in either is None in the sum -- never a partial count.
    A `reader` that differs between the runs is None: the sum would mix a wait that is the sender's with one that is not."""
    if a is None or b is None:
        return None
    both = a["readq_empty_ms"] is not None and b["readq_empty_ms"] is not None
    return {"read_ms": a["read_ms"] + b["read_ms"],
            "readq_empty_ms": a["readq_empty_ms"] + b["readq_empty_ms"] if both else None,
            "reader": a["reader"] if a["reader"] == b["reader"] else None}
