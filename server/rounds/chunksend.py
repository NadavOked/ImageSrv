"""מנוע החתיכות בצד השרת (#2533, שלב 3) — מנוע השידור היחיד מ-#3173.

המנוע מפעיל `chunkcast send`
(`agent/chunkcast.c`), שאינו מחכה לאף אחד: מעבר אחד על כל החתיכות של המחיצה,
בקצב קבוע, ואחריו **שלב השלמה** — רק מה שחסר, למי שחסר לו (`repair.py`).

**ההתייצבות וההשלמה עוברות ב-HTTP, לא ב-UDP** (התכנון, סעיף 5.3). כל משכפל שולח
`POST /api/v1/agent/chunk-pass` בשני רגעים:

- ``listening`` — המקלט שלו מאזין למעבר N של המחיצה. השרת משדר רק כשכל מי שחי
  מאזין (או כשפקעה ההמתנה — מי שלא הגיע פשוט יחסר לו, וההשלמה תמלא).
- ``ended`` — המעבר נגמר אצלו, עם **כל** מה שעמיד על כל דיסק שלו (טווחים
  מצטברים, חוזה הפורמט 9.1). התשובה היא מה עושים עכשיו: ``wait`` (עוד לא כולם
  דיווחו), ``receive`` (מעבר השלמה במולטיקאסט), ``fetch`` (להוריד ב-HTTP את מה
  שחסר רק לו), ``done``, או ``closed``. דיסק שנכשל בתקרה חוזר ב-``fail``, בשמו.

**היומן.** מה שעמיד על כל דיסק נרשם ב-`chunk_ledger` (שלב 2, `chunkledger.py`) ונקרא ממנו:
ההכרעה של שלב ההשלמה אינה נשענת על זיכרון התהליך (`DbLedger`). האינדקס של כל מחיצה נקרא
ב-`chunkformat.read_index` של שלב 2 — אותו קורא, ואותו תנאי, שבהם השרת מגיש אותו למקלטים.

**מה לא כאן, בכוונה:** **הפצה ישירה** (מחשב בנייה כמקור) מתוזמרת כאן ומשודרת משם (`directchunk.py`, #3161). **הלמידה של הקצב** (`drive_rates`) אינה ניזונה מגלים של המנוע הזה: המספרים שלו
(חסרים, מעברים) הם אות אחר. גל שרץ בזמן אתחול של השרת נכשל (`reconcile_on_start`); היומן שלו
נשאר, והמשך ממנו הוא שלב 4.

**#3163 -- the rate does learn from these waves, by its own module:** `rate_outcome` tells `chunk_rate.record` at the
end of a wave what the first pass showed (rate pushed, chunks lost in the network, cloners), and `chunk_rate.adjust`
starts the next wave from the rate the last teaching wave proved, inside the network budget. #3296: the drive memory (`drive_rates.record_wave`) learns from
them what each drive took from the first passes (`rate_outcome.drives`), not the window of its counter.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import logging
import os
import secrets
import tempfile
import subprocess
import threading
import time
from bisect import bisect_right
from collections import deque
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from server.core import shutdown
from server.core import ports as product_ports   # #3871: the group and Trident's port
from server.brain import observe as brain_observe
from server.brain import drawerhold as brain_drawerhold   # #2023: CONTINUE / DROP, the words of its verdicts
from server.brain import fecrule
from server.brain import repairrule   # #3659 (S10, S11, S12): the multicast threshold, the repair cap and "slow, not failed" are decided there
from server.brain import resume as brain_resume   # #3670 (S16): the first pass of a partition that resumes
from server.brain import planes as brain_planes   # #3571: three ceilings, recorded only; the command below is not changed
from server.brain import writerate as brain_writerate   # #3655 (S3, S4): `--write-kbps` and the feedback's bounds are decided there
from server.db import get_setting, SqlConnection
from server.library import chunkformat
from server.library.images import streamed_partitions
from server.rounds import chunkctx, chunklog, chunkledger, chunkpass, repair

log = logging.getLogger("imagesrv.chunksend")

#: #3173: ההגדרה שבחרה פעם בין שני מנועים. המנוע הישן הוסר, ואיש אינו קורא אותה להחלטה; היא נקראת רק כדי לומר
#: ביומן, פעם אחת בעלייה, ששרת משודרג שמר בה בחירה שכבר אינה קיימת (`note_retired`).
SETTING = "stream_engine"
CHUNK = "chunk"
#: מה שעונים למי שעוד שולח את ההגדרה (`POST /api/console/settings`), ומה שנרשם ביומן על ערך שמור.
RETIRED_HE = "מנוע השידור יצא מההגדרות: מנוע החתיכות הוא המנוע היחיד (#3173)"
RETIRED_EVENT = "stream_engine_retired"

#: קבוצת המולטיקאסט של המנוע (239/8 — תחום מנהלי, אינו יוצא מהאתר). server/core/ports.py (#3871).
GROUP = product_ports.TRIDENT_GROUP
#: הנתונים ב-`portbase + PORT_OFFSET`, והבקרה (משוב הקצב מהמקבלים) בפורט שאחריו. בייצור: `TRIDENT_DATA` ו-`TRIDENT_CONTROL`
#: (`chunkctx.PRODUCTION_PORTBASE`, #3871). ההיסט נשאר 2 מאז השולח שהוסר (#3173), ו-`--sender-portbase` נשען עליו.
PORT_OFFSET = 2


def feedback_grow_cap(kbps: int, learned_max: int | None, file_bytes: int | None, chunks: int | None) -> int | None:
    """`--rate-cap` of `chunkcast send` (#3599). The decision is the brain's (`writerate.grow_cap`, S4, #3655); this only passes the chunk size."""
    return brain_writerate.grow_cap(kbps, learned_max, file_bytes, chunks, CHUNK_BYTES)


def feedback_bounds(kbps: int, write_kbps: int, floor_mbs: int, learned_max: int | None = None) -> tuple[int, int]:
    """(`--rate-min`, `--rate-max`) of `chunkcast send` (#3512). The decision is the brain's (`writerate.bounds`, S4, #3655); `brain.waveeta`
    (K3, #3599) reads the same numbers through this name."""
    return brain_writerate.bounds(kbps, write_kbps, floor_mbs, learned_max)


def probe_write_kbps(probes: list[int | None], floor_mbs: int) -> int:
    """`--write-kbps` from the write check alone. The decision is the brain's (`writerate.probe_kbps`, S3, #3655); `brain.waveeta`
    (K3) reads the same number through this name."""
    return brain_writerate.probe_kbps(probes, floor_mbs)


def _size_of(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


POLL_S = 0.2
#: #3034/#3260: נעילה למדידה בלבד -- משתנה הסביבה של השרת (לא הגדרה בקונסולה, ולא סף שהמפעיל רואה). כשהוא קיים וחיובי,
#: `--write-kbps` הוא בדיוק הערך הזה (kbit/s, כמו הפקודה) ובדיקת הכתיבה של המגירות אינה משנה אותו: בדיקת הכתיבה זזה
#: ב-±20% בין ריצות, ובלי נעילה אי אפשר להשוות בין המנוע הישן לחדש בהגינות. אינו קיים = כמו תמיד.
MEASURE_WRITE_KBPS_ENV = "IMAGESRV_MEASURE_WRITE_KBPS"
#: #3522: סדר השידור של `chunkcast send` (`--order`), בכל מעבר, גם בהשלמה. **`mixed` היא ברירת המחדל** (הכרעה 16/11 על סמך הברזל,
#: r14: סדר הקובץ 464/469 שניות, `mixed` 388/390, השולח הישן 429 -- ראו `docs/architecture/interfaces.md`): חתיכות כבדות (`zlen` גדול)
#: וקלות לסירוגין, כדי שכונן איטי לא יקבל רצף כבד. **איפה משנים:** משתנה הסביבה של השרת (`systemctl edit imagesrv-server`) גובר ועובר
#: **כמו שהוא** ל-`--order` -- ערך אחר מ-`seq` ו-`mixed` אינו "לא קיים" בשקט: השולח מסרב (יציאה 2) והגל נכשל בשם הערך. בלעדיו ההגדרה
#: `chunk_order` (טבלת `settings`, `mixed` או `seq`, **אינה במסך ההגדרות** -- הכרעת נדב 14/11: ספים טכניים לא נכנסים למסך; ערך אחר =
#: ברירת המחדל ושורת יומן), נקראת בכל מעבר. בלעדיה `mixed`.
CHUNK_ORDER_ENV = "IMAGESRV_CHUNK_ORDER"
CHUNK_ORDER_SETTING = "chunk_order"
CHUNK_ORDERS = ("mixed", "seq")
DEFAULT_CHUNK_ORDER = "mixed"
#: #3532: כמה זמן `chunkcast send` חוזר על ה-ANNOUNCE לפני הנתונים (`--lead-ms`) ועל ה-END אחריהם (`--tail-ms`). הוא ישן רווח
#: (200ms / 100ms) **אחרי כל שליחה**, ולכן המחיר במעבר הוא `(lead/200 + 1) x 200 + (tail/100 + 1) x 100` ms. ברירות המחדל של הבינארי
#: (1000/2000) עלו 1.2 + 2.1 = 3.3 שניות לכל מעבר, והשרת לא העביר דבר. הערכים כאן עולים 0.6 + 0.4 = 1.0 שנייה: חיסכון 2.3ש' למעבר.
#: **למה אלה:** ה-ANNOUNCE הוא מה שמלמד את המקלט את כתובת השולח למשוב, אבל השרת משדר רק אחרי שכל המקלטים אמרו `listening`, ומקלט
#: שפספס את כולם לומד מה-keepalive (ANNOUNCE כל שנייה, `cc_keepalive`) תוך שנייה אחת לכל היותר, כלומר המחיר בטל וחסום: 400ms = שלוש
#: שליחות (0, 200, 400). ה-END שונה: מקלט שחסרה לו חתיכה מסיים רק בו (או אחרי `--stall-s`, שעון הקיר של "מחשב שהתנתק", 120ש' ויותר),
#: ולכן 300ms = ארבע שליחות (0, 100, 200, 300), פי 2.2 מ-134ms, הזמן שחוצץ קבלה של 16MiB (`CC_RCVBUF`) צריך להתרוקן בקצב הכבל
#: (1Gbit/s) -- לכל היותר, הזמן שבו END יכול למצוא את החוצץ מלא. הערכים הם מודל על קבועי הקוד, לא מדידה על ברזל (#3532 דורש ריצה כזו).
#: **איפה משנים:** משתנה הסביבה של השרת (`systemctl edit imagesrv-server`, כמו `IMAGESRV_CHUNK_ORDER`) עובר **כמו שהוא**, גם ערך שגוי --
#: השולח מסרב (יציאה 2) והגל נכשל בשם הערך; ובלעדיו ההגדרה `chunk_lead_ms` / `chunk_tail_ms` (טבלת `settings`, שלם 0..10000, אינה במסך
#: ההגדרות; פגומה = ברירת המחדל ושורת יומן); ובלעדיה הקבועים. 0 מותר לניסוי בלבד: ANNOUNCE אחד בלי רווח / END אחד.
LEAD_MS_ENV, TAIL_MS_ENV = "IMAGESRV_CHUNK_LEAD_MS", "IMAGESRV_CHUNK_TAIL_MS"
LEAD_MS_SETTING, TAIL_MS_SETTING = "chunk_lead_ms", "chunk_tail_ms"
DEFAULT_LEAD_MS, DEFAULT_TAIL_MS = 400, 300
#: #3821: מנגנון, לא החלטה של המוח -- נרשם בקטלוג (`server/brain/catalog.toml`, `T-MAX-SAY-MS`), עם הסיווג והמקור.
MAX_SAY_MS = 10000
#: כמה לחכות לדיווחי ``ended`` מעבר ל"המתנה למחשב שהתנתק" לפני שממשיכים בלעדיו. הסוכן מחזיק
#: מקלט שהזרם שלו השתתק ``lost + 120`` שניות (``WAIT_STREAM_STALL_S``, ``timing.sh``) ורק אז מדווח:
#: תקרה קצרה מזו סוגרת מחיצה לפני שמקלט שרק איבד את חבילת הסוף הספיק לומר מה יש לו.
ENDED_GRACE_S = 150
#: הורדה (`fetch`) שהקצב המצטבר שלה נמוך מהמהירות המינימלית לכונן (`writecheck_min_mbps`) במשך הזמן הזה ברצף נרשמת
#: כ"איטית" (`_note_slow_fetches`) -- **ואינה נכשלת** (#3170 מול #3295, נדב 15/11, אפשרות ב': ההפצה בקצב של המהירים,
#: והאיטי משלים). הזמן הוא חלון המדידה של הקצב, לא ניתוק לפי זמן (#2537). #3189: הקצב נמדד ממה שהדיסק **כותב**
#: (`received`, החתיכות ש-`apply` לקח ומסר לכותבים), ולא רק מ-flush נדיר. `_stall_window` משתמש בו גם כתקרת שתיקה.
FETCH_SLOW_S = 600
#: #3064 (K2): כשהווטו מחזיק פעולה הרסנית (רק ב-``enforce``), כל כמה שניות ממתינים עוד ושואלים אותו שוב (KT1).
#: #3821: מנגנון, לא החלטה של המוח -- נרשם בקטלוג (`server/brain/catalog.toml`, `T-VETO-RECHECK`), עם הסיווג והמקור.
VETO_RECHECK_S = 10
CHUNK_MB = 4194304 / 1_000_000          # חתיכה של זרם הנתונים, ב-MB
#: #2388: השולח שמחזיק את הגל לשאלה של מחשב אחר (#2202) רושם זאת בכל סבב המתנה (`POLL_S`). רישום ישן מזה אינו
#: ראיה שהוא עוד מחזיק -- למשל תהליכון שמת -- ולכן מקלט שלא קיבל דבר אינו מקבל `held`, ונכשל בתקרה שלו.
#: #3821: מנגנון, לא החלטה של המוח -- נרשם בקטלוג (`server/brain/catalog.toml`, `T-HELD-FRESH`), עם הסיווג והמקור.
HELD_FRESH_S = 5
CHUNK_BYTES = 4 * 1024 * 1024


def source_of(server_base: str | None) -> str | None:
    """כתובת המקור של השידור: כתובת ה-IPv4 שב-`--server-url` (כתובת וילן ההפצה, #498).

    הכרעת נדב 14/11 (#1079/#2485): המקלט מקבל חבילות רק מכתובת המקור של השרת, שמגיעה
    בהוראת הגל (`session.chunk.source`), והשולח נקשר אליה (`chunkcast send --from`) --
    כך שתי הכתובות הן אותה כתובת בהגדרה ולא בניחוש של הקרנל. None = אין כתובת שתחנה
    רואה (שם מארח, או loopback כשרשת ההפצה טרם הוגדרה, #1088); אז השדה אינו נשלח, והסוכן
    מקבל מכל מקור ואומר זאת באזהרה (`chunkgate.sh`)."""
    try:
        host = urlsplit(server_base or "").hostname
        ip = ipaddress.IPv4Address(host) if host else None
    except ValueError:
        return None
    return None if ip is None or ip.is_loopback or ip.is_unspecified else str(ip)


def engine_of(conn) -> str:
    """המנוע שהשרת משדר בו: מנוע החתיכות, תמיד (#3173, נדב 21/11). ההגדרה השמורה אינה נקראת."""
    return CHUNK


def note_retired(conn, journal: Callable[..., object]) -> bool:
    """#3173: שרת משודרג ששמר `stream_engine` בערך שאינו ``chunk`` -- שורת יומן אחת, בעלייה, בשמו. הערך אינו נמחק
    (הכי פחות הרסני) ואינו משנה דבר: השרת משדר במנוע החתיכות. True = נרשמה שורה. עלייה נוספת עם אותו ערך אינה
    רושמת שוב (כמו `brain.veto.note_retired`): השורה שכבר ביומן היא הראיה."""
    stored = get_setting(conn, SETTING)
    if stored in (None, "", CHUNK):
        return False
    detail = f"{SETTING}={stored}: הערך השמור אינו נקרא יותר. {RETIRED_HE}. הערך לא נמחק"
    if conn.execute("SELECT 1 FROM journal WHERE event = ? AND detail = ? LIMIT 1",
                    (RETIRED_EVENT, detail)).fetchone() is not None:
        return False
    journal(conn, RETIRED_EVENT, detail)
    return True


def _say_value(get: Callable[[str], str | None], env: str, setting: str, default: int) -> str:
    """The value of one of `--lead-ms` / `--tail-ms` (#3532): the environment as it is, else the setting, else the default."""
    raw = os.environ.get(env)
    if raw is not None:
        return raw
    stored = get(setting)
    if stored is None:
        return str(default)
    if stored.isascii() and stored.isdigit() and (stored == "0" or not stored.startswith("0")) and int(stored) <= MAX_SAY_MS:
        return stored
    log.warning("chunksend: setting %s=%r is not a whole number 0-%d -- using %d", setting, stored, MAX_SAY_MS, default)
    return str(default)


def say_ms(conn=None) -> tuple[str, str]:
    """(`--lead-ms`, `--tail-ms`) of `chunkcast send` (#3532). `conn` = the database (None: the environment and the defaults).
    A failure to read it is the defaults: the sender's own flags are never a reason to fail a wave."""
    try:
        read = (lambda key: get_setting(conn, key)) if conn is not None else (lambda key: None)
        return (_say_value(read, LEAD_MS_ENV, LEAD_MS_SETTING, DEFAULT_LEAD_MS),
                _say_value(read, TAIL_MS_ENV, TAIL_MS_SETTING, DEFAULT_TAIL_MS))
    except Exception:                           # noqa: BLE001 -- the flags never stop a wave
        log.exception("chunksend: the lead/tail settings could not be read; using %d/%d", DEFAULT_LEAD_MS, DEFAULT_TAIL_MS)
        return str(DEFAULT_LEAD_MS), str(DEFAULT_TAIL_MS)


def chunk_order(conn=None) -> str:
    """The `--order` of every `chunkcast send` (#3522): the environment as it is, else the setting `chunk_order`, else `mixed`.
    `conn` = the database (None: the environment and the default). A setting that is neither `mixed` nor `seq`, or one that cannot
    be read, is the default and a line in the log: the sender's own flags are never a reason to fail a wave."""
    raw = os.environ.get(CHUNK_ORDER_ENV)
    if raw is not None:
        return raw
    try:
        stored = get_setting(conn, CHUNK_ORDER_SETTING) if conn is not None else None
    except Exception:                           # noqa: BLE001 -- the flag never stops a wave
        log.exception("chunksend: the setting %s could not be read; using %s", CHUNK_ORDER_SETTING, DEFAULT_CHUNK_ORDER)
        return DEFAULT_CHUNK_ORDER
    if stored is None:
        return DEFAULT_CHUNK_ORDER
    if stored in CHUNK_ORDERS:
        return stored
    log.warning("chunksend: setting %s=%r is not one of %s -- using %s", CHUNK_ORDER_SETTING, stored, "/".join(CHUNK_ORDERS), DEFAULT_CHUNK_ORDER)
    return DEFAULT_CHUNK_ORDER


#: קודי היציאה של shell/`nice` כשהתוכנית עצמה לא הופעלה: 127 = לא נמצאה, 126 = נמצאה ואינה ניתנת להרצה.
#: השולח רץ דרך `nice`/`ionice` (עדיפות הרקע, #1974), ולכן בינארי חסר אינו `OSError` אלא הקוד הזה.
NOT_RUN_CODES = (126, 127)
#: מה ש-`chunkcast send` יוצא איתו כשעצרו אותו באות (chunkcast.c: "stopped by a signal"). לא כישלון של
#: השולח כשהאות הגיע בגלל שהשרת עצמו יורד (#3094) — ורק אז; בלי האות של השרת הוא כישלון כמו כל קוד.
STOPPED_BY_SIGNAL = 6
#: כמה ממתינים לאות של השרת עצמו אחרי שהילד כבר מת על קוד 6: systemd שולח את האות לכל ה-cgroup יחד, והילד
#: עשוי לצאת לפני שהמטפל של השרת רץ. קוד 6 בלי אות — מי שעצר את הילד לבדו — נכשל אחרי ההמתנה הזו.
SHUTDOWN_GRACE_S = 2.0
#: הסיבה של גל שהשרת ירד באמצעו — באותו נוסח של `session_orphaned` (reconcile.py): הסבב נכשל בשמו, לא נסגר בשקט.
SERVER_STOPPED = "השרת הופעל מחדש או נעצר — המשדר נעצר באמצע השידור של {file} (מעבר {round})"
NOT_INSTALLED = ("הבינארי של מנוע החתיכות (chunkcast) אינו מותקן בשרת, או שאינו ניתן להרצה —"
                 " tools/server-tools-build.sh בונה אותו בהתקנה ובכל עדכון, וסיבת הכישלון שלו ביומן ההתקנה או העדכון")


def binary_problem(run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> str | None:
    """None = `chunkcast` מותקן **ורץ** (`chunkcast --version` ענה בשמו). אחרת הסיבה, למפעיל.

    ראיה חיובית (עיקרון 5): קובץ שקיים ב-PATH אינו שולח שעובד — ספרייה חסרה או בינארי של מעבד אחר
    מתגלים רק בהרצה. נשאל לפני שהגל מתחיל, ולפני שהמתג בקונסולה נדלק."""
    try:
        done = run(["chunkcast", "--version"], capture_output=True, text=True, timeout=10,
                   stdin=subprocess.DEVNULL, check=False)
    except FileNotFoundError:
        return NOT_INSTALLED
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"{NOT_INSTALLED} ({type(exc).__name__}: {exc})"
    if done.returncode != 0 or not (done.stdout or "").startswith("chunkcast "):
        said = ((done.stderr or "") + (done.stdout or "")).strip().splitlines()
        return f"{NOT_INSTALLED} (chunkcast --version: קוד {done.returncode}{': ' + said[-1][:200] if said else ''})"
    return None


class DbLedger:
    """היומן הקבוע (`chunk_ledger`, שלב 2) כפי שמנוע החתיכות כותב אליו וקורא ממנו.

    ההכרעה של שלב ההשלמה נשענת עליו ולא על זיכרון התהליך: מה שעמיד על דיסק הוא מה שהיומן
    אומר, בין שהגיע בהודעת הבקרה (`chunk-pass`) ובין שבדיווח ההתקדמות (`chunkledger.note_report`).
    הזהות של דיסק היא המספר הסידורי והגודל מה-hello האחרון של המכונה; דיסק שאין לו זהות כזו,
    או שיש לו תאום, אין לו יומן — והמנוע אינו משלים דיסק שאינו יודע מה כתוב עליו."""

    def __init__(self, conn, library):
        self.conn, self.library = conn, library

    def _disk(self, mac: str, serial: str) -> tuple[str, int] | None:
        found = [who for who in chunkledger.disks_of(self.conn, mac).values() if who[0] == serial]
        return found[0] if len(found) == 1 else None

    def record(self, session_id: str, image_id: str, mac: str, serial: str, part: int,
               ranges: repair.Ranges) -> str | None:
        """None = נרשם (איחוד בלבד); אחרת הסיבה שהיומן סירב."""
        who = self._disk(mac, serial)
        if who is None:
            return "the server does not know this disk's serial and size from the machine's hello, or two disks carry them"
        manifest = self.library.get(image_id)
        if manifest is None:
            return "the round's image is not in the library"
        try:
            chunkledger.record(self.conn, round_id=session_id, mac=mac, serial=who[0], size_bytes=who[1],
                               image_id=image_id, manifest=manifest,
                               durable=[{"part": part, "ranges": [list(r) for r in ranges]}])
        except chunkledger.LedgerProblem as exc:
            return str(exc)
        return None

    def durable(self, session_id: str, mac: str, serial: str, part: int) -> repair.Ranges:
        """מה שעמיד על הדיסק במחיצה הזו, בסבב הזה. אין שורה = אין ראיה = כלום.

        **שורה של אימג' אחר אינה ראיה (#3117):** הכתיבה בצד היומן קשורה לאימג' (`record`), ובצד
        הקריאה השורה נבחרה לפי הסבב בלבד — ו-`adopt` מעביר שורה לסבב אחר. היומן עמיד גם בפני
        קורא עתידי שיעביר שורה בלי לבדוק: האימג' של השורה הוא האימג' של הסבב, או שאין ראיה."""
        who = self._disk(mac, serial)
        row = chunkledger.of_disk(self.conn, *who).get(part) if who is not None else None
        if row is None or row["round_id"] != session_id:
            return []
        wave = self.conn.execute("SELECT image_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        return list(row["ranges"]) if wave is not None and row["image_id"] == wave["image_id"] else []


def _measured_write_kbps() -> int | None:
    """הערך הנעול של `--write-kbps` למדידה (`MEASURE_WRITE_KBPS_ENV`), או None: לא קיים. ערך שאינו מספר חיובי אינו
    "לא קיים" בשקט -- נרשמת אזהרה, והחישוב הרגיל רץ (נעילה שגויה אינה סיבה לעצור גל)."""
    raw = os.environ.get(MEASURE_WRITE_KBPS_ENV)
    if raw is None:
        return None
    try:
        value = int(raw)
    except ValueError:
        value = 0
    if value < 1:
        log.warning("chunksend: %s=%r אינו מספר חיובי -- הנעילה לא פעלה, הקצב מחושב כרגיל", MEASURE_WRITE_KBPS_ENV, raw)
        return None
    return value


@dataclass
class _Mark:
    """הדיווח האחרון של מחשב אחד על מחיצה אחת."""
    round: int
    state: str                                      # listening / ended
    at: float
    serials: set[str] = field(default_factory=set)  # הדיסקים שהמחשב דיווח עליהם; הטווחים — ביומן
    failed: set[str] = field(default_factory=set)   # serials שהסוכן עצמו הכשיל
    write_mbps: dict[str, int | None] = field(default_factory=dict)
    #: #3141/#3170: כמה חתיכות המקלט ויתר עליהן לכל דיסק במעבר הזה, כי הדיסק פיגר (`skipped` של הסוכן)
    skipped: dict[str, int] = field(default_factory=dict)
    bad_chunks: frozenset[int] | None = None


@dataclass
class _Wave:
    session_id: str
    image_id: str
    key: str                                        # מספר הסשן שבכל חבילה: 16 ספרות hex
    state: str = "starting"                         # starting / sending / repairing / done / failed / stopped
    error: str | None = None
    part: int | None = None                         # אינדקס המחיצה (כמו במניפסט)
    chunks: int = 0
    round: int = 0                                  # 1 = המעבר הראשון
    plan: repair.Plan | None = None                 # מה שהוכרע לסבב `round`
    marks: dict[tuple[int, str], _Mark] = field(default_factory=dict)
    done_parts: set[int] = field(default_factory=set)
    part_chunks: dict[int, int] = field(default_factory=dict)      # אינדקס מחיצה -> מספר החתיכות שלה
    #: #3296: the compressed size of each chunk (the index's frame length), by partition index -- what a drive took
    #: from the first pass, in the units of its own counter (`bytes_written`)
    part_zbytes: dict[int, list[int]] = field(default_factory=dict)
    late_first: dict[tuple[int, str], int] = field(default_factory=dict)   # מי דיווח אחרי שהמחיצה נסגרה, וממתי
    commands: list[list[str]] = field(default_factory=list)
    failed_disks: dict[tuple[str, str], str] = field(default_factory=dict)
    #: מתי דיווח `fetching` של מחשב הוסיף משהו ליומן: הורדה שמתקדמת אינה שתיקה (`_wait_marks`).
    progress: dict[tuple[int, str], float] = field(default_factory=dict)
    #: ההורדה של מחשב בסבב הנוכחי: (מתי התחילה, כמה חתיכות היו לו אז, כמה יש עכשיו, ממתי היא איטית או None).
    fetching: dict[tuple[int, str], list] = field(default_factory=dict)
    #: #3112: כמה חתיכות עמידות על הדיסקים של המחשב לפי דיווח ה-`fetching` האחרון שלו (`fetched`): מספר שגדל = ההורדה חיה.
    fetched: dict[tuple[int, str], int] = field(default_factory=dict)
    #: #3149: כמה חתיכות `chunkcast apply` של המחשב לקח מהזרם, עמידות או לא (`received`), לפי (מחיצה, מחשב, מעבר). flush איטי
    #: על דיסק איטי מעכב את `fetched` דקות; `received` לא מחכה לו. הוא ראיית חיים בלבד: אינו נכנס ליומן ואינו נספר כעמיד.
    received: dict[tuple[int, str, int], int] = field(default_factory=dict)
    #: #3149: הפער הארוך ביותר בין שתי התקדמויות של ההורדה (שנייה), שנמדד בה: התקרה אינה קטנה ממנו שלוש פעמים (`_stall_window`).
    gap: dict[tuple[int, str], float] = field(default_factory=dict)
    #: #1079/#2485: כמה חבילות מכתובת זרה המקלט דיווח לכל (מחיצה, מחשב, מעבר) -- כדי שהודעה חוזרת לא תרשום אירוע שוב.
    stray: dict[tuple[int, str, int], int] = field(default_factory=dict)
    #: מי שנכשל בשמו בזמן ההמתנה (הורדה איטית): השרת כבר אינו מחכה לו בסבב הזה.
    dropped: set[tuple[int, str]] = field(default_factory=set)
    #: #2388: מתי השולח החזיק לאחרונה את (מחיצה, מעבר) לשאלה פתוחה למפעיל (`_wait_marks`, #2202).
    held: dict[tuple[int, int], float] = field(default_factory=dict)
    #: #3170 (נדב 15/11, אפשרות ב'): הורדה איטית שכבר נרשמה כ"איטית" (מחיצה, מחשב) -- פעם אחת. היא אינה נכשלת ואינה נושרת.
    slow_noted: set[tuple[int, str]] = field(default_factory=set)
    #: #3141: מי שהשרת לא ספר בין החיים בזמן שחיכה לסוף המעבר הראשון -- ראיה מצד השרת לניתוק.
    away: set[tuple[int, str]] = field(default_factory=set)
    #: #3064 (K7): הקצב, בביטים לשנייה, שהגל הזה ביקש מהזרם (`_send`). תקציב ה-HTTP שומר אותו לשידור (`sending_bps`).
    bits: int = 0
    #: network-rate learning (`rate_outcome`, `chunk_rate.record`): what the FIRST pass of each partition showed,
    #: by partition index -- {"chunks", "lost" (the worst machine's chunks lost in the network), "measured", "away",
    #: "zbytes" (#3296: the compressed bytes the pass multicast), "drives" ({mac: {serial: the compressed bytes durable
    #: on it when the pass ended}}; None = the sizes are not known)}
    pass1: dict[int, dict] = field(default_factory=dict)
    #: seconds `chunkcast send` ran in first passes, all partitions (the engine's clock); repair passes are not in it
    loss_reports: dict[tuple[int, str], dict] = field(default_factory=dict)
    fec_parts: dict[int, dict] = field(default_factory=dict)
    pass1_s: float = 0.0
    #: #3295: the same seconds by partition index -- what a drive that was behind in one partition wrote, over that pass
    part_pass1_s: dict[int, float] = field(default_factory=dict)
    #: #3498: by partition index, how long `chunkcast send` slept in its first passes for `--kbps` and `--write-kbps`, in ms
    #: (`chunklog.paced_of`, summed over the runs of the pass); None = not measured
    part_paced: dict[int, tuple[int, int] | None] = field(default_factory=dict)
    #: #3553: by partition index, what `chunkcast send` said of how long it waited for the library file in its first passes
    #: (`chunklog.sendstats_of`, summed over the runs of the pass); None = not measured (no `sendstats` line)
    part_sendstats: dict[int, dict | None] = field(default_factory=dict)
    #: #3506: by partition index, (the `--write-kbps` its first pass went out at, the serials of the drives whose check set it,
    #: what the loop did to the check's rate -- a label, or None). No serials = no drive's check set it (the floor, the lock):
    #: the loop has no evidence about that partition
    part_write: dict[int, tuple[int, tuple[str, ...], str | None]] = field(default_factory=dict)
    #: #3295: the drives already journaled as left out of `--write-kbps` in this wave (`chunk_write_behind`, once each)
    write_noted: set[str] = field(default_factory=set)
    #: #3064 (B7): (partition, pass) of the full pass the brain's carousel put in place of the plan, and whether that
    #: pass was cut (B12/B47: one live listener or none, `carousel_cut`) -- a cut pass is not the sender failing
    carousel: tuple[int, int] | None = None
    carousel_cut: bool = False
    #: #3064 (K9): מה שהמעבר הראשון של כל מחיצה הראה, לסיווג הסיבה כשדיסק נכשל אחר כך: (mac ← serial ← `skipped`,
    #: mac ← חלקים שלא הגיעו למחשב). `_Mark.skipped` נדרס בכל מעבר, וזה נשאר.
    told1: dict[int, tuple[dict, dict]] = field(default_factory=dict)
    #: #2023: כמה חתיכות עמידות על כל דיסק של המחשב כשההורדה שלו התחילה -- הבסיס של קצב ההשלמה של הדיסק (`_judge_drawers`)
    fetch_base: dict[tuple[int, str], dict[str, int]] = field(default_factory=dict)
    #: #2023 (`brain_drawer_hold` = on): the drawers the brain held in a partition: (part, mac, serial). Out of `_lacks`, the plan,
    #: `left` and `_unreported` -- like `failed_disks`, but NOT a failure: the agent completes the drawer in the background
    #: (`agent/lib/restore/chunkhold.sh`) while the others go on to the next partition. Per partition: a drawer that catches up
    #: and joins a later one is a drawer like the others there.
    held_disks: set[tuple[int, str, str]] = field(default_factory=set)
    #: #2023: מתי נגמרה המחיצה הקודמת (שעון המנוע): ממנה עד תחילת השידור של הבאה = התקורה לכל מחיצה (`part_gap`)
    part_end: float | None = None
    stats: dict = field(default_factory=lambda: {"repair_rounds": 0, "multicast_chunks": 0, "fetched_chunks": 0})


class RosterReadError(RuntimeError):
    """The live members could not be read for a chunk wave."""


class ChunkEngine:
    """שידור אחד בכל רגע. ההרצה, השעון וההמתנה מוזרקים לבדיקות."""

    def __init__(
        self,
        library,
        *,
        live_members: Callable[[str], list[str]],
        lost_wait: Callable[[], int],
        write_floor: Callable[[], int],
        runner: Callable[[list[str]], chunklog.SendProcess] = chunklog.run_process,
        portbase: int | None = None,
        interface: str | None = None,
        source: str | None = None,
        max_wait: int = chunkctx.DEFAULT_MAX_WAIT,
        start_timeout: float = chunkctx.DEFAULT_START_TIMEOUT,
        wave_bitrate: Callable[[dict], str | None] | None = None,
        max_bitrate: str | None = None,
        link_speed: Callable[[str | None], dict] | None = None,
        pending_prompts: Callable[[str], list[str]] | None = None,
        ledger,
        binary_check: Callable[[], str | None] | None = None,
        on_event: Callable[[str, str, str], None] | None = None,
        wants: Callable[[str], bool] | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        going_down: threading.Event | None = None,
        shutdown_grace: float = SHUTDOWN_GRACE_S,
        feedback: bool = True,
    ):
        #: האות שהשרת עצמו קיבל (#3094); בייצור `server.core.shutdown.GOING_DOWN`, שהמטפל של `ports` קובע.
        self._going_down = shutdown.GOING_DOWN if going_down is None else going_down
        self.shutdown_grace = shutdown_grace
        self.library = library
        self.live_members = live_members
        self.lost_wait = lost_wait
        self.write_floor = write_floor
        self.runner = runner
        self.port = (chunkctx.DEFAULT_PORTBASE if portbase is None else portbase) + PORT_OFFSET
        self.interface = interface
        #: כתובת המקור של כל חבילה (`source_of`): השולח נקשר אליה, והמקלטים מקבלים רק ממנה.
        self.source = source
        self.feedback = feedback
        self.max_wait = max_wait
        self.start_timeout = start_timeout
        self.wave_bitrate = wave_bitrate
        #: #3173: גג הקצב שהמפעיל קבע (``"700m"``), מהבנאי או מ-`IMAGESRV_MAX_BITRATE`; None = לא נקבע.
        #: `drive_rates.decide` מקבל אותו כגג ולעולם אינו בוחר מעליו.
        self.max_bitrate = max_bitrate or chunkctx.env_max_bitrate()
        #: #3173: קורא מהירות הקישור (`drive_rates.read_link`). None = לא מחובר: `link()` עונה "לא נקרא".
        self.link_speed = link_speed
        self.pending_prompts = pending_prompts
        self.on_event = on_event or (lambda event, detail, session_id: None)
        #: "הגל הזה ייצא במנוע החתיכות" -- אותה הכרעה ש-`on_running` מקבל (`server.app`). hello שואל
        #: אותה כשהמנוע עוד לא שמע על הגל: ה-commit ל-`running` קודם ל-`on_running` (#2427), ובלי זה
        #: hello שנכנס ביניהם היה משאיר משכפל בלי השדות של הגל מול שולח chunkcast.
        self.wants = wants
        #: היומן: `record(...)` ו-`durable(...)` — בייצור `DbLedger` (טבלת `chunk_ledger`).
        self.ledger = ledger
        #: "השולח מותקן ורץ" (`binary_problem`). None = מי שהזריק `runner` משלו (בדיקות, סימולציה).
        self.binary_check = binary_check
        #: #705/#2668: הכרטיס של כל גל (`select_interface`, ValueError = אין כרטיס ודאי)
        #: והשער "שידור כבוי בכרטיס" (`gate`). מחוברים ב-`server.app`; None = בדיקות שאינן מחברות.
        self.select_interface: Callable[[dict], str | None] | None = None
        self.gate: Callable[[str | None], str | None] | None = None
        #: #3064 (K2): הווטו של המוח, `server.brain.veto.check(...).proceed`. נשאל לפני כל פעולה הרסנית שנשענת על
        #: שתיקה (D15, D16, D18, D19). None = לא מחובר (בדיקות): הקוד של לפני #3064, בדיוק.
        self.veto: Callable[..., bool] | None = None
        #: #3064: `server.brain.veto.settle` -- הנקודה יצאה מההחזקה, והרשם של הווטו שוכח אותה. None = לא מחובר.
        self.veto_settle: Callable[..., None] | None = None
        #: #3064 (K9): `server.brain.cause.judge` -- הסיבה של דיסק שהמנוע מכשיל בשמו, לפי הראיות של הגל. רושם בלבד:
        #: הכשל עצמו אינו משתנה. None = לא מחובר (בדיקות): הקוד של לפני K9, בדיוק.
        self.cause: Callable[..., object] | None = None
        #: #3064 (K9): (session_id, mac, serial) -> the kernel's ATA lines about that drawer, as the machine reported them in
        #: the wave (`wavesense.kernel_lines`): () = dmesg read and clean, None = not known. None here = not wired (tests):
        #: `kernel` is not passed to K9, as before.
        self.kernel_of: Callable[[str, str, str], tuple[str, ...] | None] | None = None
        #: #3161: "לגל הזה אין קובץ בשרת" -- הפצה ישירה, שהשולח שלה הוא מחשב הבנייה. אין `fetch`: מה
        #: שחסר, גם למחשב אחד, משודר שוב (`repair.plan(threshold=1)`). מחובר ב-`server.app`; None = יש קובץ.
        self.no_file: Callable[[str], bool] | None = None
        #: #3532: () -> (`--lead-ms`, `--tail-ms`) of every `chunkcast send`. `server.app` reads the settings; here the environment
        #: and the defaults (`say_ms`), so a test that wires nothing still sends the flags.
        self.say: Callable[[], tuple[str, str]] = say_ms
        #: #3522: () -> the `--order` of every `chunkcast send` (`chunk_order`). `server.app` reads the setting; here the environment
        #: and the default (`mixed`), so a test that wires nothing sends the same order as a server does.
        self.order: Callable[[], str] = chunk_order
        #: #3269: (session_id, הקצב שנבחר בביטים, {(mac, serial): MB/s של בדיקת הכתיבה או None}) -> הקצב שהגל יוצא בו.
        #: נשאל פעם אחת לגל, אחרי שהמגירות של המחיצה הראשונה התייצבו -- רק אז בדיקת הכתיבה של הגל ידועה.
        #: `chunk_rate.start` ב-`server.app`; None = לא מחובר (בדיקות): הקצב של `wave_bitrate`, בדיוק.
        self.wave_start: Callable[[str, int, dict], object] | None = None
        #: #3295: serial -> (the MB/s it wrote in a past chunk wave it was behind in, that wave), or None -- no such wave
        #: (`chunk_rate.write_history`). Nadav 15/11: the stream goes at the pace of the fast drives; a drive that was
        #: behind is left out of `--write-kbps` and completes what it misses afterwards. None = not wired (tests):
        #: every drive counts, as before.
        self.write_history: Callable[[str], tuple[int, str] | None] | None = None
        #: #3506 (K8): (session_id, partition, the rate `_write_kbps` computed, the serials of the drives it counted) -> the
        #: `--write-kbps` the partition goes out at: the brain's closed loop over the waves (`chunk_rate.write_rate`) --
        #: up when the drives kept up and the cap held the sender, down when one fell behind. The answer is never below the
        #: floor. It answers (the rate, a label of what moved it or None). A failure, or an answer of another shape, is the
        #: rate as computed. None = not wired (tests).
        self.write_rate: Callable[[str, int, int, list[str]], tuple[int, str | None]] | None = None
        #: #3511/#3512: (the serials `write_rate` counted) -> the most `--write-kbps` a drive of them was proven to write
        #: (`chunk_rate.write_ceiling`), the in-wave feedback's `--rate-max`; None, or an answer that is not a positive
        #: number = the feedback keeps the brain's own room (`writerate.RATE_MAX_FACTOR`). None = not wired (tests).
        self.write_ceiling: Callable[[list[str]], int | None] | None = None
        #: #3655: the connection the brain records its decisions through (`writerate.decide_base/decide_bounds`, S3/S4) -- `server.app` wires it;
        #: None (tests that wire nothing) = the same decisions, not recorded.
        self.brain_conn: SqlConnection | None = None
        #: #3064 (B7, B12, B47): `server.brain.carousel.Carousel.consider` -- after each pass of a partition, the brain
        #: weighs one more full multicast pass against the plan, and records it. A plan comes back only when its own
        #: switch is on (`brain_carousel`, off by default); None = the plan runs. `carousel_cut` -- during such a pass,
        #: the reason to cut it (X9). None = not wired (tests): the code before the carousel, exactly.
        self.carousel: Callable[..., repair.Plan | None] | None = None
        self.carousel_cut: Callable[[str, int, int, list[str]], str | None] | None = None
        #: #2023 (K3, shadow): `server.brain.drawerhold.DrawerHold.evaluate` -- בכל דיווח `fetching`: האם ההשלמה של דיסק איטי
        #: ממשיכה או שהכתיבה מחדש בגל הבא קצרה ממנה. רושם בלבד, ואינו משנה דבר. None = לא מחובר (בדיקות).
        self.drawer_hold: Callable[..., object] | None = None
        #: #2023: `DrawerHold.gap(session_id, seconds)` -- כמה עבר מסוף מחיצה עד שהשולח התחיל את הבאה (התקורה לכל מחיצה).
        self.part_gap: Callable[[str, float], None] | None = None
        #: #2023: `DrawerHold.executed(session_id, part, mac, serial, kind)` -- the engine did what the brain decided (hold / drop):
        #: the decision is closed with that, not with "the drive is whole". None = not wired (tests).
        self.drawer_held: Callable[..., None] | None = None
        #: #3125: מה שכל מעבר ראה, כשדות וביומן (`chunkpass.PassLog`). `.conn` מחובר ב-`server.app`; בלעדיו -- ביומן בלבד.
        self.passlog = chunkpass.PassLog(lambda *a: self.on_event(*a), CHUNK_MB)
        self._nonce = secrets.token_hex(8)      # לכל עלייה של השרת: מספר הסשן אינו חוזר אחרי אתחול
        self._capable: set[str] = set()         # מחשבים שה-hello האחרון שלהם הצהיר על המנוע
        self._ended: set[str] = set()           # גלים שלנו שנכשלו או נעצרו
        self.clock = clock
        self.sleep = sleep
        self._lock = threading.Lock()
        self._wave: _Wave | None = None
        self._process: chunklog.SendProcess | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        #: #2427 (ported from the old sender, #3173): waves closed (`stop(id)`) before their `start` arrived. The commit to
        #: `running` comes before `on_running`, and a close between them was a no-op here -- the engine then sent a closed
        #: wave and held the next one off until "nobody listened". Bounded: an old id that never starts falls out.
        self._closed_early: deque[str] = deque(maxlen=64)

    # --- API ------------------------------------------------------------------

    def start(self, session: dict) -> str | None:
        """None = הופעל, אחרת הסיבה שלא — והקורא מכשיל את הסבב בשמו.

        #3173: a wave that was told to stop (`stop`) and is still ending is waited for, with a ceiling, before the next
        one is refused: `stop` does not wait for the thread, which notices within `POLL_S` and then writes its end. The
        old sender waited the same way; without it the next wave of the room failed by "the engine is still sending"
        when it went running inside that moment. A wave nobody stopped is refused at once, as before."""
        sid = session["id"]
        with self._lock:
            refused = self._closed_before_start(sid)
            thread, stopping = self._thread, self._stop.is_set()
        if refused:
            return refused
        if thread is not None and thread.is_alive() and stopping:
            thread.join(chunkctx.STOP_TERM_WAIT + chunkctx.STOP_KILL_WAIT + 1.0)   # outside the lock: the thread takes it to end
        with self._lock:
            refused = self._closed_before_start(sid)
            if refused:
                return refused
            if self._thread is not None and self._thread.is_alive():
                busy = self._wave.session_id if self._wave is not None else "?"
                return f"השידור של {sid} לא הופעל: המנוע עדיין משדר את הסבב {busy}"
            self._stop.clear()
            self._wave = _Wave(session["id"], session["image_id"], self.key_for(session["id"]))
            self._thread = threading.Thread(target=self._run, args=(session,), daemon=True,
                                            name=f"chunksend-{session['id']}")
            self._thread.start()
        return None

    def _closed_before_start(self, session_id: str) -> str | None:
        """Under `_lock`. A wave closed before its `start` came: the reason, and it leaves the list (#2427)."""
        if session_id not in self._closed_early:
            return None
        self._closed_early.remove(session_id)
        log.warning("chunksend: %s was closed before its start arrived; not starting", session_id)
        return f"הסבב {session_id} נסגר לפני שהשידור שלו התחיל — מנוע החתיכות לא הופעל"

    def stop(self, session_id: str | None = None) -> int | None:
        """#439: None = יש ראיה שאין `chunkcast send` חי של הגל
        (או שהגל אינו שלנו); מספר = הוא עדיין חי — ה-PID, או -1 כשאין לו."""
        with self._lock:
            wave, process = self._wave, self._process
            if wave is None or (session_id is not None and wave.session_id != session_id):
                # #2427: a wave that is not ours yet -- if its `start` is still on the way, it sends nothing
                if session_id is not None and session_id not in self._closed_early:
                    self._closed_early.append(session_id)
                return None
            self._stop.set()
        if process is not None and process.poll() is None:
            process.terminate()
            if not chunkctx.reaped(process, chunkctx.STOP_TERM_WAIT):   # ראיה שהילד מת, לא היעדר בדיקה (#439)
                process.kill()
                if not chunkctx.reaped(process, chunkctx.STOP_KILL_WAIT):
                    pid = getattr(process, "pid", None)
                    log.error("chunksend: chunkcast send still alive after SIGKILL (PID %s)", pid)
                    return pid if pid is not None else -1
        return None

    def close(self) -> None:
        self.stop()
        thread = self._thread
        if thread is not None:
            thread.join(chunkctx.STOP_TERM_WAIT + chunkctx.STOP_KILL_WAIT + 1.0)

    def link(self) -> dict:
        """כרטיס ההפצה של המנוע ומהירותו (`drive_rates.read_link`), או למה לא נקראה."""
        return self.link_of(self.interface)

    def link_of(self, interface: str | None) -> dict:
        """אותו קורא, לכרטיס אחר (#3073: הכרטיס שהגל בחר)."""
        return chunkctx.link_of(self.link_speed, interface)

    def status(self) -> dict | None:
        with self._lock:
            w = self._wave
            if w is None:
                return None
            # #3978: and what the first passes that ended measured (`_live_view`), while the wave runs -- not only at its end
            live = self._live_view(w)
            return {"session_id": w.session_id, "image_id": w.image_id, "engine": CHUNK,
                    "partition": w.part, "round": w.round, "state": w.state, "error": w.error,
                    **w.stats, **{k: live[k] for k in ("first_pass_s", "stream_zbytes", "lost_chunks", "away",
                                                       "paced_ms", "write_kbps")}}

    def sending_bps(self) -> int:
        """#3064 (K7): כמה ביטים לשנייה הגל הפעיל ביקש מהזרם, כל עוד הוא משדר או משלים (גם בהמתנה בין מעברים:
        המעבר הבא בדרך). 0 = אין גל פעיל. תקציב ה-HTTP נשאר רק עם מה שאחרי הקצב הזה."""
        with self._lock:
            w = self._wave
            if w is None or self._thread is None or not self._thread.is_alive():
                return 0
            return w.bits if w.state in ("sending", "repairing") else 0

    def rate_outcome(self, session_id: str) -> dict:
        """What the network-rate learning takes from the wave at its end (`chunk_rate.record`).

        ``chunks`` / ``bytes``: what the first passes of all partitions multicast (`CHUNK_BYTES` a chunk).
        ``first_pass_s``: how long they took by the engine's clock -- the repair rounds are not in it.
        ``lost_chunks``: per partition the worst machine's chunks that did not arrive in the first pass and
        that the drive's own `skipped` does not explain (`repair.Plan.lost_in_net`, #3141), summed;
        ``None`` = not measured (a machine whose agent reported no `skipped`): not the same as "none lost".
        ``away``: machines the server stopped counting as alive while it waited for a first pass to end --
        a disconnect, which is not the rate's fault; ``away_macs``: who they are (#3977). ``cloners``: machines in the wave.
        #3296: ``stream_zbytes`` -- the compressed bytes the first passes multicast (the index's frames); ``drives`` --
        ``{mac: {serial: compressed bytes}}``, what each drive had durable when each first pass ended, summed over the
        partitions; ``None`` for a drive missing from one partition's first pass (not measured, not zero). Both
        ``None`` when the sizes are not known. The drive memory learns from these, not from its counter's window,
        which also holds the HTTP completion -- and the wait for a neighbour's (`drive_rates.record_wave`).
        #3295: ``behind`` -- `_behind`: what each drive the receiver skipped chunks for wrote in those first passes
        (`chunk_rate.write_history`: the next wave leaves it out of `--write-kbps`, and the prediction can read it).
        Empty = this is not the wave (or none is). The hook for #3164's metric: its number goes in
        ``lost_chunks``. #3978: ``multicast_chunks`` / ``fetched_chunks`` -- what the repair rounds sent again, kept with
        the wave so that `/room` still has them after the engine moved on to the next wave."""
        with self._lock:
            w = self._wave
            if w is None or w.session_id != session_id:
                return {}
            return self._outcome(w)

    def _outcome(self, w: _Wave) -> dict:
        """`rate_outcome` of the wave `w`; the caller holds `_lock`. #3978: also what `status` and `stream_view` show
        while the wave runs."""
        parts = list(w.pass1.values())
        paced = [w.part_paced.get(i) for i in w.pass1]
        sends = [w.part_sendstats.get(i) for i in w.pass1]
        written = [w.part_write.get(i) for i in w.pass1]
        proven = [x for x in written if x is not None]       # (the set is complete when `len(proven) == len(written)`)
        read = [p for p in paced if p is not None]
        # #3506: what `--write-kbps` was in the first passes and whose measurement set it -- only when every partition's
        # was set by a drive's check (not the floor, not the lock): the next wave's loop stands on it
        wrote = bool(written) and len(proven) == len(written) and all(x[1] for x in proven)
        chunks = sum(p["chunks"] for p in parts)
        sized = bool(parts) and all(p.get("drives") is not None for p in parts)
        return {"chunks": chunks, "bytes": chunks * CHUNK_BYTES, "first_pass_s": w.pass1_s,
                "stream_zbytes": sum(p["zbytes"] for p in parts) if sized else None,
                "drives": _first_pass_drives(parts) if sized else None,
                "behind": _behind(parts),
                # #3498: the time the sender slept for `--kbps` / `--write-kbps` in the first passes; None unless every
                # partition's was read -- a sender held by its own pacing is not held by the library disk
                "paced_ms": ({"wire": sum(p[0] for p in read), "disk": sum(p[1] for p in read)}
                             if paced and len(read) == len(paced) else None),
                # #3553: how long the sender waited for the library file in the first passes (`chunklog.sendstats_of`, summed);
                # None unless every partition's was read -- it is what says whether the library disk held the stream
                "sendstats": _sum_sendstats(sends),
                "write_kbps": min(x[0] for x in proven) if wrote else None,
                "write_serials": sorted({s for x in proven for s in x[1]}) if wrote else None,
                "lost_chunks": (sum(p["lost"] for p in parts)
                                if parts and all(p["measured"] for p in parts) else None),
                "away": sum(p["away"] for p in parts),
                # #3977: who they are -- the rate a cloner fell at is the machine's memory (`rateinputs.remember_drops`)
                "away_macs": sorted({mac for _part, mac in w.away}),
                "cloners": len({mac for _part, mac in w.marks}),
                "repair_rounds": w.stats["repair_rounds"],
                # #3978: what the repair rounds sent again -- by multicast and over HTTP, as the engine planned them
                "multicast_chunks": w.stats["multicast_chunks"], "fetched_chunks": w.stats["fetched_chunks"]}

    def _live_view(self, w: _Wave) -> dict:
        """#3978: what the wave `w` has measured so far, for the console; the caller holds `_lock`. Until a first pass
        ended, what only a first pass measures is None -- not measured, not 0 (`pass1_s` starts at 0.0, `away` at an
        empty sum). The repair counters are the engine's own counts: 0 before any repair is a count."""
        got, ended = self._outcome(w), bool(w.pass1)
        return {"first_pass": ended, "chunks": got["chunks"] if ended else None,
                "first_pass_s": got["first_pass_s"] if ended else None, "stream_zbytes": got["stream_zbytes"],
                "lost_chunks": got["lost_chunks"], "away": got["away"] if ended else None,
                "paced_ms": got["paced_ms"], "write_kbps": got["write_kbps"],
                "repair_rounds": got["repair_rounds"], "multicast_chunks": got["multicast_chunks"],
                "fetched_chunks": got["fetched_chunks"]}

    def stream_view(self, session_id: str) -> dict | None:
        """#3173/#3978: what `/room.round` shows of the wave `session_id` while the engine holds it; None = it does not.

        ``throughput_bps`` -- bytes a second on the wire: the compressed bytes the first passes that ENDED multicast (the
        index's frames, `pass1[i]["zbytes"]`), over the engine's seconds of the same passes (`pass1[i]["seconds"]`) --
        the measurement of `chunk_rate.sample_of`'s ``sent_bps``, over the partitions done so far. None until a first
        pass ended, or when one's size or time is not known: not measured, never 0. The rest is `_live`."""
        with self._lock:
            w = self._wave
            if w is None or w.session_id != session_id:
                return None
            parts = list(w.pass1.values())
            timed = bool(parts) and all(isinstance(p.get("zbytes"), int) and isinstance(p.get("seconds"), (int, float))
                                        for p in parts)
            seconds = sum(p["seconds"] for p in parts) if timed else 0
            return {"throughput_bps": int(sum(p["zbytes"] for p in parts) / seconds) if timed and seconds > 0 else None,
                    **self._live_view(w)}

    def binary_problem(self) -> str | None:
        """None = יש שולח. אחרת למה אין — לגל שעומד לצאת, ולמתג בקונסולה."""
        return self.binary_check() if self.binary_check is not None else None

    def key_for(self, session_id: str) -> str:
        """מספר הסשן שבכל חבילה של הגל: נגזר מהמזהה שלו, כדי ש-hello יוכל לתת אותו גם ברגע
        שלפני `start` (ראו `wants`). אינו סוד -- הוא מבחין בין זרמים, לא מאמת אותם."""
        return hashlib.sha256(f"{self._nonce}:{session_id}".encode()).hexdigest()[:16]

    def note_agent(self, mac: str, engines: object) -> None:
        """מה שה-hello של המכונה הצהיר (`engines`, ממשק 2). סוכן שאינו מצהיר על המנוע -- אימג'
        מלפני השינוי -- אינו יודע לקבל ממנו, וגל שהוא חבר בו מסורב בשמו ולא נכשל ב"הזרם לא הגיע"."""
        with self._lock:
            if isinstance(engines, list) and CHUNK in engines:
                self._capable.add(mac)
            else:
                self._capable.discard(mac)

    def session_fields(self, session_id: str) -> dict:
        """מה ש-hello מוסיף ל-`session` של גל שהמנוע הזה משדר (ממשק 3). גל אחר — כלום."""
        with self._lock:
            w = self._wave
            if session_id in self._ended:
                return {}
            known = w is not None and w.session_id == session_id
        if not known and not (self.wants is not None and self.wants(session_id)):
            return {}
        chunk = {"group": GROUP, "port": self.port, "key": self.key_for(session_id)}
        if self.source:
            chunk["source"] = self.source       # הכרעת נדב 14/11: המקלט מקבל רק ממנה (`chunkcast recv --from`)
        return {"engine": CHUNK, "chunk": chunk}

    def report(self, session_id: str, mac: str, body: dict) -> dict:
        """הודעת הבקרה של משכפל (``listening`` / ``ended`` / ``fetching``) → מה הוא עושה עכשיו.
        #2023: אחרי התשובה, הדיווח של מי שמוריד נשפט (`_judge_drawers`). ההגדרה `brain_drawer_hold` כבויה (ברירת המחדל) = המוח
        מחזיר דבר ריק והתשובה אינה משתנה; דולקת = `_execute` מוסיף לתשובה `hold` / `fail` (רק ל-`fetching`)."""
        answer = self._report(session_id, mac, body)
        if answer.get("ok") and body.get("state") in ("fetching", "ended"):
            verdicts = self._judge_drawers(session_id, mac, body)
            if body.get("state") == "fetching" and answer.get("action") == "wait":
                try:
                    answer = self._execute(session_id, mac, body.get("part"), verdicts, answer)
                except RosterReadError as exc:
                    self._fail(str(exc), session_id=session_id)
                    raise
        return answer

    def _judge_drawers(self, session_id: str, mac: str, body: dict) -> dict:
        """#2023 (K3): לכל דיסק של `mac` שעוד חסר לו משהו -- מה נמדד בהשלמה שלו, ושליחתו למוח. בלי נעילה בזמן הקריאה
        (המוח כותב ל-DB), ולעולם אינו זורק: המוח אינו מפיל את בקשת הדיווח. מחזיר מה שהמוח מבקש לבצע ({serial: (CONTINUE|DROP, למה)}),
        ו-{} כשההגדרה כבויה, או שאין מוח, או שהוא נכשל."""
        if self.drawer_hold is None:
            return {}
        part, rnd, ended = body.get("part"), body.get("pass"), body.get("state") == "ended"
        try:
            with self._lock:
                w = self._wave
                if w is None or w.session_id != session_id or not isinstance(part, int):
                    return {}
                track = w.fetching.get((part, mac))
                if track is None or w.state in ("failed", "stopped"):
                    return {}                    # אין הורדה של המחשב הזה במחיצה הזו: אין מה לשפוט
                order = list(w.part_chunks)
                base = w.fetch_base.get((part, mac), {})
                drawers = []
                for serial, gap in self._lacks(w, part, mac).items():
                    have = repair.count(self.ledger.durable(session_id, mac, serial, part))
                    drawers.append({"serial": serial, "remaining": repair.count(gap),
                                    "grown": have - base[serial] if serial in base else None})
                args = {
                    "session_id": session_id, "part": part, "mac": mac, "drawers": drawers, "ended": ended,
                    "received": w.received.get((part, mac, rnd)) if isinstance(rnd, int) else None,
                    "elapsed_s": self.clock() - track[0],
                    # מגירה שהשאר עברו הלאה בלעדיה משלימה גם את המחיצות שאחרי (אינה מקבלת את הזרם שלהן)
                    "later_chunks": sum(w.part_chunks[p] for p in order[order.index(part) + 1:]) if part in order else None,
                    "chunks_total": sum(w.part_chunks.values()) or None,
                    "zbytes_total": sum(sum(z) for z in w.part_zbytes.values()) or None,
                    "bits": w.bits, "parts": len(order)}
            got = self.drawer_hold(**args)
            return got if isinstance(got, dict) else {}
        except Exception:                        # noqa: BLE001 -- המוח אינו מפיל בקשה
            log.exception("chunksend: the drawer hold of %s %s part %s was not judged", session_id, mac, part)
            return {}

    def _execute(self, session_id: str, mac: str, part: object, verdicts: dict, answer: dict) -> dict:
        """#2023 (`brain_drawer_hold` = on): what the brain decided, for the drawers of `mac` it judged in a fetch.

        CONTINUE -> the drawer is HELD: out of the partition (`held_disks`) -- the agent completes it in the background and the
        others go on to the next partition. DROP (the brain confirmed it twice) -> the drawer fails by name and is written in
        the next wave (`failed_disks`, `chunk_disk_failed`). **Only when it frees someone** (`_frees_others`): a decision nobody
        waits for changes nothing, and the drawer goes on completing as it does today. Silence is not here (`_fail_silent`).
        The answer to the agent: `hold` -- the serials held in this partition, on every report of the machine's fetch (an agent that asks
        again hears it again); `fail` -- the machine's failed drawers (as in `_answer`), when this report dropped one.
        With nothing held and nothing decided -- every report with the setting off -- the answer is the one it was."""
        if not isinstance(verdicts, dict):         # #3764: not an answer -- the agent's report is answered as it was, not with a 500
            log.warning("chunksend: _execute got verdicts of %s (not a dict) for %s part %s; the answer is unchanged",
                        type(verdicts).__name__, mac, part)
            return answer
        live = set(self._live_for(session_id)) if verdicts else set()     # outside the lock: it asks the database
        held, dropped = [], []
        with self._lock:
            w = self._wave
            if w is None or w.session_id != session_id or w.state in ("failed", "stopped") or not isinstance(part, int):
                return answer
            lacking = self._lacks(w, part, mac)           # only a drawer that is still completing here; held and failed are not in it
            for serial, got in sorted((k, v) for k, v in verdicts.items() if isinstance(k, str)):
                if serial not in lacking or not isinstance(got, (tuple, list)) or len(got) != 2:
                    continue
                verdict, why = got
                if verdict not in (brain_drawerhold.DROP, brain_drawerhold.CONTINUE) or not self._frees_others(
                        w, part, mac, serial, live, keeps=verdict == brain_drawerhold.CONTINUE):
                    continue
                if verdict == brain_drawerhold.DROP:
                    w.failed_disks[(mac, serial)] = why
                    dropped.append((serial, why))
                else:
                    w.held_disks.add((part, mac, serial))
                    held.append((serial, why))
            hold = sorted(s for p, m, s in w.held_disks if (p, m) == (part, mac))
            fail = {serial: why for (m, serial), why in w.failed_disks.items() if m == mac}
        for serial, why in dropped:
            self.on_event("chunk_disk_failed", f"{mac} {serial}: {why}", session_id)
        for serial, why in held:
            self.on_event("chunk_disk_held", f"{mac} {serial}: {why} -- היא משלימה ברקע, והמגירות האחרות עוברות למחיצה הבאה",
                          session_id)
        for kind, items in ((brain_drawerhold.CONTINUE, held), (brain_drawerhold.DROP, dropped)):
            for serial, _why in items:
                self._tell_executed(session_id, part, mac, serial, kind)
        return {**answer, **({"hold": hold} if hold else {}), **({"fail": fail} if fail and dropped else {})}

    def _tell_executed(self, session_id: str, part: int, mac: str, serial: str, kind: str) -> None:
        if self.drawer_held is None:
            return
        try:
            self.drawer_held(session_id, part, mac, serial, kind)
        except Exception:                        # noqa: BLE001 -- the brain's record does not stop a wave
            log.exception("chunksend: the brain was not told that %s %s was %s", mac, serial, kind)

    def _live_for(self, session_id: str) -> list[str]:
        try:
            return list(self.live_members(session_id))
        except Exception as exc:
            raise RosterReadError(f"לא ניתן לקרוא את המכונות הפעילות של הסבב {session_id}: {exc}") from exc


    def _frees_others(self, w: _Wave, part: int, mac: str, serial: str, live: set[str], *, keeps: bool) -> bool:
        """Under the lock. Does taking `serial` out of the partition let someone go on? (1) someone is waiting: a drawer of the
        partition, another one, whole (not lacking, failed or held) on a live machine that has ended. (2) only for a drawer that is
        HELD (`keeps`): its machine keeps a drawer in the stream -- an agent whose every drawer is completing in the background
        has nothing to feed the next partition (`chunk_partition_drawers`: "no drawers left"). A dropped drawer fails like any other."""
        def out(m: str, s: str, mk: _Mark) -> bool:
            return s in mk.failed or (m, s) in w.failed_disks or (part, m, s) in w.held_disks
        mine = w.marks.get((part, mac))
        if mine is None or mac not in live:
            return False
        if keeps and not any(s != serial and not out(mac, s, mine) for s in mine.serials):
            return False
        return any((m, s) != (mac, serial) and not out(m, s, mk) and s not in self._lacks(w, part, m)
                   for (p, m), mk in w.marks.items() if p == part and m in live and mk.state == "ended" for s in mk.serials)

    def _report(self, session_id: str, mac: str, body: dict) -> dict:
        part, rnd, state = body.get("part"), body.get("pass"), body.get("state")
        if (isinstance(part, bool) or not isinstance(part, int) or isinstance(rnd, bool)
                or not isinstance(rnd, int) or rnd < 1 or state not in ("listening", "ended", "fetching")):
            return {"ok": False, "code": "bad_chunk_pass", "error": "part, pass and state are required"}
        with self._lock:
            w = self._wave
            if w is None or w.session_id != session_id or w.state in ("failed", "stopped"):
                return {"ok": True, "action": "closed"}
            image_id, chunks = w.image_id, w.part_chunks.get(part)
        try:
            noted = self._read_targets(part, chunks, body.get("targets"))
        except ValueError as exc:
            return {"ok": False, "code": "bad_durable", "error": str(exc)}
        # היומן נכתב מחוץ לנעילה של המנוע: סגירת סבב מחזיקה את נעילת ה-DB וקוראת ל-`stop`.
        held = self._held(session_id, mac, part, noted) if state == "fetching" else 0
        refused = {serial: problem for serial, _failed, _mbps, ranges, _skipped in noted if ranges is not None
                   and (problem := self.ledger.record(session_id, image_id, mac, serial, part, ranges))}
        now_held = self._held(session_id, mac, part, noted) if state == "fetching" else 0
        grew = state == "fetching" and now_held > held
        fetched = body.get("fetched")
        fetched = fetched if isinstance(fetched, int) and not isinstance(fetched, bool) and fetched >= 0 else None
        received = body.get("received")
        received = received if isinstance(received, int) and not isinstance(received, bool) and received >= 0 else None
        with self._lock:
            if self._wave is not w or w.state in ("failed", "stopped"):
                return {"ok": True, "action": "closed"}
            if state == "fetching" and fetched is not None:
                # #3112 (מעבדת ה-VM 13/11: הורדה של 2,751 טווחים, והגל נסגר כ"שתק" אחרי 6.7 דקות): המספר שהסוכן
                # קורא בקובץ המצב של `chunkcast apply` -- חתיכות עמידות על הדיסקים שלו -- הוא ראיה בפני עצמה. גדל
                # מאז הדיווח הקודם (או מאז שההורדה התחילה) = ההורדה חיה, גם כשהיומן לא זז.
                track = w.fetching.get((part, mac))
                prev = w.fetched.get((part, mac), track[1] if track else None)
                grew = grew or (prev is not None and fetched > prev)
                w.fetched[(part, mac)] = max(fetched, prev or 0)
                if track is not None and fetched > track[2]:
                    now_held = max(now_held, fetched)
            if state == "fetching" and received is not None:
                # #3149 (מעבדת ה-VM 14/11: `apply` לקח 2,569 חתיכות מהזרם בתוך 342 שניות, ובקובץ המצב שלו רק 4 כתיבות --
                # ה-flush של דיסק thin איטי): מספר שגדל מאז הדיווח הקודם של אותו מעבר ההורדה הוא ראיה שהיא חיה, לפני ש-flush
                # אחד חזר. הוא אינו עמיד: `now_held` והיומן אינם נוגעים בו. #3189: הקצב של `_note_slow_fetches` כן קורא אותו -- כתיבה, לא עמידות.
                before = w.received.get((part, mac, rnd), 0)
                grew = grew or received > before
                w.received[(part, mac, rnd)] = max(received, before)
            for serial, problem in refused.items():
                log.warning("chunksend: no ledger for %s of %s: %s", serial, mac, problem)
                w.failed_disks.setdefault((mac, serial), (
                    "לדיסק אין יומן כתיבה בשרת (המספר הסידורי והגודל שלו אינם ידועים, או שיש לו תאום)"
                    " — מנוע החתיכות אינו משלים דיסק בלי יומן"))
            if grew:
                now = self.clock()
                began = w.fetching[(part, mac)][0] if (part, mac) in w.fetching else now
                w.gap[(part, mac)] = max(w.gap.get((part, mac), 0.0), now - max(w.progress.get((part, mac), 0.0), began))
                w.progress[(part, mac)] = now
                if (part, mac) in w.fetching:
                    w.fetching[(part, mac)][2] = max(w.fetching[(part, mac)][2], now_held)
            if state == "fetching":
                # ההורדה של המחשב (HTTP) עוד רצה. זה אינו מצב חדש שלו — הוא ידווח `ended` כשתסתיים —
                # אלא ראיה שהיא מתקדמת: רק דיווח שהוסיף חתיכות ליומן מאפס את ההמתנה של השרת.
                return {"ok": True, "action": "wait"}
            if body.get("durable_more") is True:
                # חתיכה של דיווח ארוך (הסוכן מפצל מעל 30,000 טווחים): הטווחים נרשמו, ולא יותר. המצב
                # של המכונה וההכרעה מחכים לחתיכה האחרונה — היומן עוד אינו מה שהסוכן יודע.
                return {"ok": True, "action": "wait"}
            mark = w.marks.get((part, mac))
            if mark is None or rnd >= mark.round:
                mark = w.marks[(part, mac)] = _Mark(rnd, state, self.clock(),
                                                    set(mark.serials) if mark else set(),
                                                    set(mark.failed) if mark else set(),
                                                    dict(mark.write_mbps) if mark else {},
                                                    dict(mark.skipped) if mark and mark.round == rnd else {})
            for serial, failed, mbps, _ranges, skipped in noted:
                mark.serials.add(serial)
                if failed:
                    mark.failed.add(serial)
                if mbps is not None or serial not in mark.write_mbps:
                    mark.write_mbps[serial] = mbps
                if state == "ended" and skipped is not None:
                    mark.skipped[serial] = skipped
            bad = body.get("bad_chunks")
            if state == "ended" and rnd == mark.round and isinstance(bad, list) and chunks is not None:
                if all(type(k) is int and 0 <= k < chunks for k in bad):
                    mark.bad_chunks = frozenset(bad)
                    total = body.get("bad_chunks_total")
                    if type(total) is int and total > len(bad):
                        # #4155: the agent cut the list (its body would pass 1 MiB). What is here is a subset of what it rejected: the
                        # shared ids of cut lists are shared by the full ones too, so a verdict is never invented -- at most late
                        log.warning("chunksend: %s rejected %d chunks of p%s; only the first %d reached the server",
                                    mac, total, part, len(bad))
                else:
                    # #4156: a list that is not trusted is no evidence (a source verdict aborts the wave) -- and it is said
                    log.warning("chunksend: bad_chunks of %s for p%s dropped whole: %d ids, not all integers in 0..%d",
                                mac, part, len(bad), chunks - 1)
            # #1079/#2485: חבילות שהגיעו למקלט מכתובת שאינה של השרת ונזרקו. אירוע רק כשהמספר של אותו מעבר גדל
            # (ההודעה `ended` חוזרת אחרי הורדה); חסר, לא מספר או 0 = לא נאמר, ואין אירוע.
            stray = body.get("stray") if state == "ended" else None
            if isinstance(stray, int) and not isinstance(stray, bool) and stray > w.stray.get((part, mac, rnd), 0):
                w.stray[(part, mac, rnd)] = stray
            else:
                stray = None
            if state == "listening":
                # #2388: מקלט שהקשיב ולא קיבל דבר שואל שוב. `held` = השולח מחזיק עכשיו את המעבר הזה לשאלה של מחשב
                # אחר, והמקלט ממתין שוב במקום להיכשל. בלי `held` הוא נכשל בתקרה שלו, כמו תמיד.
                held = self.clock() - w.held.get((part, rnd), float("-inf")) <= HELD_FRESH_S
                return {"ok": True, "action": "receive", "pass": rnd, **({"held": True} if held else {})}
            if rnd == 1 and state == "ended" and isinstance(body.get("loss_shape"), dict):
                w.loss_reports[(part, mac)] = body["loss_shape"]
            answer = self._answer(w, part, mac, rnd)
            seen = self._pass_seen(w, part, mac, rnd)           # #3125
        self._note_pass(w, part, mac, rnd, noted, body, seen)    # #3125 (R6, U1, U8): after the lock -- it writes
        if stray:
            self.on_event("chunk_stray_source", f"{mac}: {stray} חבילות בשידור של מחיצה {part} (מעבר {rnd}) הגיעו מכתובת"
                          " שאינה של השרת, והמקלט זרק אותן — לא נכתב מהן דבר", session_id)
        return answer

    # --- פנים ------------------------------------------------------------------

    def _pass_seen(self, w: _Wave, part: int, mac: str, rnd: int) -> tuple[str | None, tuple[float, int] | None]:
        """#3125, תחת הנעילה: מה היה המעבר של המחשב (`receive`/`fetch`, לפי התוכנית של הסבב; None = לא ידוע, למשל
        דיווח באיחור על סבב ישן), ואם הורדה -- מתי התחילה וכמה חתיכות עמידות היו לו אז."""
        if rnd == 1:
            return "receive", None
        if w.round != rnd or w.plan is None:
            return None, None
        if mac in w.plan.fetch:
            track = w.fetching.get((part, mac))
            return "fetch", (track[0], track[1]) if track else None
        return ("receive", None) if mac in w.plan.receive else (None, None)

    def _note_pass(self, w: _Wave, part: int, mac: str, rnd: int, noted: list[tuple], body: dict,
                   seen: tuple[str | None, tuple[float, int] | None]) -> None:
        """#3125: שדות המעבר של המחשב (`chunkpass`). מחוץ לנעילה: הרישום כותב ל-DB. לעולם אינו זורק."""
        kind, began = seen
        try:
            held = self._held(w.session_id, mac, part, noted) if kind == "fetch" else None
            self.passlog.reported(w.session_id, part, rnd, mac, kind, {serial: skipped for serial, *_r, skipped in noted},
                                  body.get("stray"), body.get("received"), body.get("fetched"), self.clock(), held, began)
        except Exception:                      # noqa: BLE001 -- הרישום אינו מפיל דיווח
            log.exception("chunksend: the counters of %s from %s were not recorded", w.session_id, mac)

    def _held(self, session_id: str, mac: str, part: int, noted: list[tuple]) -> int:
        """כמה חתיכות היומן מחזיק עכשיו לדיסקים שבדיווח."""
        return sum(repair.count(self.ledger.durable(session_id, mac, serial, part)) for serial, *_rest in noted)

    @staticmethod
    def _read_targets(part: int, chunks: int | None, targets: object) -> list[tuple]:
        """מה שהסוכן אמר על הדיסקים שלו: [(serial, נכשל, MB/s או None, הטווחים של המחיצה או None,
        `skipped` או None)]. ``skipped`` שאינו מספר שלם אי-שלילי הוא "לא נאמר" (None), לא 0.
        דיווח שאינו בצורה של החוזה נדחה כולו (`ValueError`), ואינו "מתוקן"."""
        if not isinstance(targets, list):
            raise ValueError("targets must be a list")
        out = []
        for t in targets:
            serial = t.get("serial") if isinstance(t, dict) else None
            if not isinstance(serial, str) or not serial:
                raise ValueError("a target without a serial has no ledger")
            mbps = t.get("write_mbps")
            mbps = mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None
            ranges = None
            for entry in t.get("durable") or []:
                if not isinstance(entry, dict) or entry.get("part") != part:
                    continue                    # מחיצה אחרת: לא של ההודעה הזו
                if chunks is None:
                    if entry.get("ranges") == []:
                        continue                # מתייצב למחיצה שהשרת עוד לא פתח: אין לו מה לרשום
                    raise ValueError("durable ranges for a partition the server is not sending")
                ranges = repair.parse_ranges(entry.get("ranges"), chunks)
                if ranges is None:
                    raise ValueError(f"durable ranges of {serial} are not sorted closed ranges inside 0..{chunks - 1}")
            skipped = t.get("skipped")
            skipped = skipped if isinstance(skipped, int) and not isinstance(skipped, bool) and skipped >= 0 else None
            out.append((serial, bool(t.get("failed")), mbps, ranges, skipped))
        return out

    def _lacks(self, w: _Wave, part: int, mac: str) -> dict[str, repair.Ranges]:
        """תחת הנעילה. מה חסר לכל דיסק חי של המחשב במחיצה, לפי מה שהוא עצמו דיווח."""
        mark, chunks = w.marks.get((part, mac)), w.part_chunks.get(part)
        if mark is None or chunks is None:
            return {}
        gaps = {s: repair.missing(chunks, self.ledger.durable(w.session_id, mac, s, part))
                for s in mark.serials if s not in mark.failed and (mac, s) not in w.failed_disks
                and (part, mac, s) not in w.held_disks}
        return {s: gap for s, gap in gaps.items() if gap}

    def _answer(self, w: _Wave, part: int, mac: str, rnd: int) -> dict:
        """תחת הנעילה. מה עושה המחשב אחרי ש-`rnd` נגמר אצלו. **המוח מחליט** (`repairrule.decide_answer`, S14, #3659): receive / fetch / done / wait. כאן נאספות
        העובדות (מה חסר לו, מי תוכנית הסבב) ונבנית התשובה; ומתבצעת הכשלת מאחר שהמכסה נגמרה (S11): דיסק כזה נכשל בשמו."""
        lacks = self._lacks(w, part, mac)
        if part in w.done_parts and lacks:
            # המחיצה נסגרה בלעדיו (דיווח אחרי התקרה: מקלט שאיבד את חבילת הסוף). אין צורך בשולח
            # כדי להשלים מחשב אחד: הוא מוריד את שלו. גם לזה יש תקרה, והיא כישלון בשם הדיסק.
            # #3209: גם בהפצה ישירה. לשרת אין שם קובץ, והוא מעביר את הבקשה למחשב הבנייה (`directchunk.file_response`).
            first = w.late_first.setdefault((part, mac), rnd)
            if repairrule.exhausted(rnd - first):
                for serial, gap in lacks.items():
                    w.failed_disks[(mac, serial)] = repairrule.exhausted_text(repair.count(gap))
                lacks = {}
        fail = {serial: why for (m, serial), why in w.failed_disks.items() if m == mac}
        out: dict = {"ok": True, **({"fail": fail} if fail else {})}
        late: repair.Ranges | None = None
        if part in w.done_parts and lacks:
            late = []
            for gap in lacks.values():
                late = repair.union(late, gap)
        # `whole` / `others`: ההמתנה אינה שלו והסוכן אינו סופר אותה מול התקרה שלו (השרת עונה לו כל הזמן)
        mark = w.marks.get((part, mac))
        current = w.plan
        planned = w.part == part and current is not None and w.round > rnd
        call = repairrule.decide_answer(
            getattr(self, "brain_conn", None), round_id=w.session_id, mac=mac, part_done=part in w.done_parts, late_ranges=late,
            no_lacks=not lacks, has_mark=mark is not None, ended_now=mark is not None and mark.state == "ended" and mark.round >= w.round,
            planned=planned, in_receive=current is not None and planned and mac in current.receive,
            fetch=current.fetch.get(mac) if current is not None and planned else None,
            rnd=rnd, wave_round=w.round)
        if call is None or call.verdict == repairrule.ANSWER_WAIT:
            return {**out, "action": "wait", "poll": 2,
                    **({"whole": True} if call is not None and call.whole else {}), **({"others": True} if call is not None and call.others else {})}
        if call.verdict == repairrule.ANSWER_DONE:
            return {**out, "action": "done"}
        if call.verdict == repairrule.ANSWER_RECEIVE:
            return {**out, "action": "receive", "pass": call.pass_}
        return {**out, "action": "fetch", "pass": call.pass_, "ranges": [list(r) for r in call.ranges]}

    def _note_part_gap(self, w: _Wave) -> None:
        """#2023: the overhead of one partition -- from the end of the previous one to the moment this one is about to send
        (the listening wait of the machines included): measured, for the brain's rewrite-from-scratch estimate. The first
        partition has no previous one. Never raises: the measurement does not stop a wave."""
        if self.part_gap is None or w.part_end is None:
            return
        try:
            self.part_gap(w.session_id, self.clock() - w.part_end)
        except Exception:                        # noqa: BLE001
            log.exception("chunksend: the partition gap of %s was not noted", w.session_id)

    def _wave_now(self) -> _Wave:
        """The wave the engine runs, or ran last. `_wave` is None only before the first `start`, and what calls this (the
        sender thread, `_fail`) runs only after it."""
        w = self._wave
        if w is None:
            raise RuntimeError("chunksend: no wave was started")
        return w

    def _fail(self, message: str, *, session_id: str | None = None) -> None:
        with self._lock:
            w = self._wave_now()
            if w.state == "failed":
                return
            if session_id is not None and (w.session_id != session_id or w.state in ("done", "stopped")):
                return
            w.state, w.error = "failed", message
            self._ended.add(w.session_id)
            if session_id is not None:
                self._stop.set()
        if session_id is not None:
            self.stop(session_id)
        log.error("chunksend: %s", message)
        self.on_event("send_failed", message, w.session_id)

    def _live(self, w: _Wave) -> list[str]:
        return self._live_for(w.session_id)

    def _live_noting_away(self, w: _Wave, part: int, rnd: int, involved: list[str]) -> list[str]:
        """מי מ-`involved` חי עכשיו. במעבר הראשון מי שאינו חי נרשם (#3141): השרת עצמו ראה אותו נעלם
        (`lost`, אחרי "המתנה למחשב שהתנתק"), וזו ראיה שהחוסר שלו הוא ניתוק ולא דיסק."""
        live = set(self._live(w))
        if rnd == 1:
            with self._lock:
                w.away.update((part, m) for m in involved if m not in live)
        return [m for m in involved if m in live]

    def _wait_marks(self, w: _Wave, part: int, rnd: int, state: str, who: Callable[[], list[str]],
                    ceiling: float, first: float | None = None) -> list[str] | None:
        """`_wait_marks_inner`, ועוד #3470: ההחזקה על שאלה למפעיל נרשמת ביומן באותם אירועים כמו בשולח הישן
        (`send_held_prompt` כשהיא מתחילה, `send_prompt_released` עם `<N>s` כשהיא נגמרת), גם כשההמתנה יוצאת
        בעצירה או בחריגה -- בלי השחרור `learn/waits.py` לא יכול לדעת כמה זמן הגל עמד."""
        hold: list[float] = []
        try:
            return self._wait_marks_inner(w, part, rnd, state, who, ceiling, first, hold)
        finally:
            self._prompt_released(w, hold)

    def _prompt_released(self, w: _Wave, hold: list[float]) -> None:
        if hold:
            since = hold.pop()
            self.on_event("send_prompt_released", f"{w.session_id} {max(int(self.clock() - since), 0)}s", w.session_id)

    def _wait_marks_inner(self, w: _Wave, part: int, rnd: int, state: str, who: Callable[[], list[str]],
                          ceiling: float, first: float | None, hold: list[float]) -> list[str] | None:
        """ממתין שכל מי ש-`who()` מחזיר ידווח `state` על (מחיצה, סבב). מחזיר את מי
        שדיווח; None = נעצר. `ceiling` נספר מהדיווח הראשון; `first` (אם ניתן) היא
        התקרה עד הדיווח הראשון — בלעדיה מחזירים רשימה ריקה. שאלה פתוחה למפעיל עוצרת
        את שני השעונים (#2202)."""
        started = self.clock()
        since_first: float | None = None
        base = ceiling
        while not self._stop.is_set():
            members = who()
            with self._lock:
                # מי שכבר סיים את הסבב האזין לו (מחיצה ריקה: המקלט יוצא מיד)
                got = [m for m in members
                       if (mk := w.marks.get((part, m))) is not None
                       and (mk.round > rnd or (mk.round == rnd and (mk.state == state or state == "listening")))]
                # הורדה שמתקדמת אינה שתיקה: דיווח `fetching` שהוסיף ליומן מזיז את שני השעונים. בלי זה
                # הורדה ארוכה מהתקרה הפילה את הגל (מוריד יחיד), או שרפה סבבי השלמה (עם מאזינים).
                moved = max((w.progress.get((part, m), 0.0) for m in members), default=0.0)
            now = self.clock()
            if state == "ended":
                # רק מי שעוד מוריד: מי שכבר דיווח `ended` סיים, והמספרים שלו אינם מתעדכנים עוד
                self._note_slow_fetches(w, part, [m for m in members if m not in got], now)
                members = [m for m in members if (part, m) not in w.dropped]
                got = [m for m in got if m in members]
                if not members:
                    return []                     # אין למי לחכות (אבדו, או נכשלו בשמם): לא יושבים עד התקרה
                ceiling = self._stall_window(w, part, members, base)
            elif rnd > 1 and not members:
                return []                         # מעבר השלמה שכל המאזינים שלו כבר אינם חיים: לא מחכים להתייצבות
            if members and len(got) == len(members):
                return got
            if state == "ended" and moved > started:
                started = moved
                since_first = moved if since_first is not None else None
            asked = self.pending_prompts(w.session_id) if self.pending_prompts is not None and state == "listening" else None
            if not asked:
                self._prompt_released(w, hold)
            if asked:
                if not hold:
                    hold.append(now)
                    self.on_event("send_held_prompt", f"{w.session_id} {' '.join(map(str, asked))}", w.session_id)
                started, since_first = now, None if not got else now
                with self._lock:
                    w.held[(part, rnd)] = now     # #2388: מה שנאמר למקלט שהקשיב ולא קיבל דבר (`report`)
            elif got:
                since_first = since_first if since_first is not None else now
                if now - since_first > ceiling:
                    return got
            elif first is not None and now - started > first:
                return []
            elif first is None and now - started > ceiling:
                return []
            self.sleep(POLL_S)
        return None

    def _stall_window(self, w: _Wave, part: int, members: list[str], ceiling: float) -> float:
        """#3149: כמה זמן בלי התקדמות מחכים להורדה: התקרה הקבועה, או פי שלושה מהפער הארוך ביותר שנמדד בה -- הגדול מהם, ולא
        יותר מ-`FETCH_SLOW_S`. הורדה שמתקדמת בקפיצות (flush איטי שמעכב את הכתיבה ואחריו מתפנה) אינה "תקועה" רק
        כי הקפיצה הבאה ארוכה מהתקרה; התקדמות שאינה באה כלל -- לא `received` ולא `fetched` -- נכשלת בתקרה כמו תמיד."""
        with self._lock:
            gap = max((w.gap.get((part, m), 0.0) for m in members), default=0.0)
        return max(ceiling, min(3 * gap, FETCH_SLOW_S))

    def _note_slow_fetches(self, w: _Wave, part: int, members: list[str], now: float) -> None:
        """הורדה שמטפטפת: הקצב המצטבר שלה (חתיכות שנעשו עמידות מאז שהתחילה, חלקי הזמן) נמוך מהסף של
        בדיקת הכתיבה במשך `FETCH_SLOW_S` ברצף. **אינה נכשלת** (#3170 מול #3295, הכרעת נדב 15/11, אפשרות ב': "ההפצה בקצב של
        המהירים. האיטי מפספס חתיכות ומשלים אותן אחר כך"): כונן איטי שאין לו כשל משלו משלים, והגל ממשיך לחכות לו כל עוד
        הוא מתקדם (שתיקה נכשלת, כמו תמיד: `_fail_silent`). נרשם פעם אחת, כדי שהמפעיל ידע למה ההשלמה ארוכה. אם ההשלמה
        תארך הרבה יותר מהגל, המוח רושם תחזית בצל (K3, WAVE_ETA): הוא אינו מכשיל."""
        with self._lock:
            tracked = [m for m in members if (part, m) in w.fetching and (part, m) not in w.dropped
                       and (part, m) not in w.slow_noted]
        if not tracked:
            return                              # אין הורדה בסבב הזה: לא שואלים את ההגדרה חמש פעמים בשנייה
        floor, named = self.write_floor(), []
        with self._lock:
            for mac in tracked:
                track = w.fetching.get((part, mac))
                if track is None or (part, mac) in w.dropped:
                    continue
                began, base, held, slow_since = track
                # #3189: מה שהדיסק כותב, לא רק מה שכבר עבר flush. `received` הוא מה ש-`apply` של המעבר הזה לקח
                # מהזרם ומסר לכותבים (מתאפס בכל הורדה); כותב שחוסם על דיסק מלא אינו לוקח עוד, ולכן הקצב שלו הוא
                # קצב הדיסק. על הברזל: 3.9MB/s לפי flush, 40MiB/s לפי `/sys/block/sda/stat` ולפי `received`.
                done = max(held - base, w.received.get((part, mac, w.round), 0))
                rate = done * CHUNK_MB / max(now - began, 1e-6)
                if rate >= floor:
                    track[3] = None
                    continue
                if slow_since is None:
                    track[3] = slow_since = now
                if now - slow_since < FETCH_SLOW_S:
                    continue
                w.slow_noted.add((part, mac))
                for serial, gap in self._lacks(w, part, mac).items():
                    named.append((mac, serial, f"ההורדה של החלקים החסרים איטית: {rate:.1f}MB/s בממוצע, מתחת לסף {floor}MB/s"
                                  f" במשך {FETCH_SLOW_S // 60 or 1} דקות ({repair.count(gap)} חלקים עדיין חסרים)"
                                  " -- הדיסק אינו נכשל, והגל ממשיך לחכות לו כל עוד הוא מתקדם"))
        for mac, serial, why in named:
            self.on_event("chunk_disk_slow", f"{mac} {serial}: {why}", w.session_id)

    def _fail_silent(self, w: _Wave, part: int, involved: list[str], reported: list[str],
                     fetchers: set[str], ceiling: float) -> None:
        """סבב השלמה נגמר, ומי שהתבקש להאזין או להוריד לא דיווח: שתק עד התקרה, או שכבר אינו חי.
        הדיסקים שעוד חסר להם נכשלים **בשמם**, והוא לבדו -- הגל אינו נכשל בגללו (כשל בתחנה אחת אינו
        עוצר את השאר). מי שיחזור וידווח יקבל את הסיבה, ולא סבב נוסף."""
        named = []
        with self._lock:
            for mac in involved:
                if mac in reported or (part, mac) in w.dropped:
                    continue
                for serial, gap in self._lacks(w, part, mac).items():
                    what = ("ההורדה של החלקים החסרים לא הסתיימה ולא התקדמה" if mac in fetchers
                            else "המחשב לא דיווח על סיום מעבר ההשלמה")
                    why = f"{what} במשך {int(ceiling)} שניות ({repair.count(gap)} חלקים עדיין חסרים)"
                    w.failed_disks[(mac, serial)] = why
                    named.append((mac, serial, why))
        for mac, serial, why in named:
            self.on_event("chunk_disk_failed", f"{mac} {serial}: {why}", w.session_id)
        self._tell_cause(w, part, "D15", named)

    def _tell_cause(self, w: _Wave, part: int, point: str, named: list[tuple[str, str, str]]) -> None:
        """#3064 (K9): לכל דיסק שהמנוע הכשיל זה עתה בשמו -- הסיבה לפי מה שהגל יודע: `skipped` של המעבר הראשון,
        החלקים שלא הגיעו למחשב, והאם השרת איבד אותו באמצע. את הקרנל המנוע אינו רואה: הוא מגיע מהמשכפל עצמו בדיווח הגל
        (`kernel_of`, ``wave_senses.kernel``), ובלעדיו (``None``) הוא לא נקרא. מחוץ
        לנעילה: הרישום כותב ל-DB. לעולם אינו זורק לתוך תהליכון השידור."""
        if self.cause is None or not named:
            return
        with self._lock:
            told, lost = w.told1.get(part, ({}, {}))
            away = {m for p, m in w.away if p == part}
        for mac, serial, why in named:
            try:
                kernel = None
                if self.kernel_of is not None:
                    try:
                        kernel = self.kernel_of(w.session_id, mac, serial)
                    except Exception:            # noqa: BLE001 — בלי שורות הקרנל הסיבה נרשמת כמו לפני כן, ולא נעלמת
                        log.exception("chunksend: the kernel lines of %s %s were not read", mac, serial)
                self.cause(point=point, round_id=w.session_id, mac=mac, serial=serial, existing=f"נכשל: {why}",
                           error=why, skipped=told.get(mac, {}).get(serial), lost_in_net=lost.get(mac), kernel=kernel,
                           client_lost=True if mac in away else None)
            except Exception:                    # noqa: BLE001 — המוח אינו מפיל את תהליכון השידור
                log.exception("chunksend: K9 of %s %s failed", mac, serial)

    def _hold(self, w: _Wave, action: str, rule: str, part: int, quiet: Callable[[], list[str]], why: str,
              rewait: Callable[[list[str]], list[str] | None], *, until_any: bool = False,
              can_hold: bool = True) -> list[str] | None:
        """#3064 (K2): לפני פעולה הרסנית שנשענת על השתיקה של `quiet()`, שואלים את הווטו. "בצע" (תמיד ב-shadow,
        ותמיד בלי וטו מחובר) → מי שנשמע בזמן ההחזקה, ו-[] כשלא הוחזקה כלל: הפעולה מתבצעת כמו היום. HOLD (רק
        ב-enforce) → ממתינים עוד (`rewait`, `VETO_RECHECK_S`) ושואלים שוב, עד שהשתיקה נגמרת (`until_any`: אחד
        שנשמע מספיק) או שהווטו משחרר (תקרה). None = המנוע נעצר."""
        heard: list[str] = []
        if self.veto is None:
            return heard
        asked = False
        try:
            while not self._stop.is_set():
                silent = [m for m in quiet() if m not in heard]
                if not silent or (until_any and heard):
                    return heard
                with self._lock:
                    seen = brain_observe.wave_evidence(w, self.clock())
                    drawers = [(m, s) for m in silent for s in sorted(self._lacks(w, part, m))]
                asked = True
                try:
                    go = self.veto(action=action, rule=rule, round_id=w.session_id, part=part, targets=silent,
                                   reason=why, drawers=drawers, engine=seen, can_hold=can_hold)
                except Exception:                    # noqa: BLE001 — המוח אינו מפיל את תהליכון השידור
                    log.exception("chunksend: the veto of %s %s failed; the action goes on as before", rule, action)
                    go = True
                if go:
                    return heard
                got = rewait(silent)
                if got is None:
                    return None
                heard += [m for m in got if m not in heard]
            return None
        finally:
            # ההחזקה נגמרה (הדיווח הגיע, הווטו שחרר, או שהמנוע נעצר): הרשם של הווטו שוכח אותה, שההחזקה הבאה על אותו
            # גל, פעולה ומחיצה לא תיספר מזו
            if asked and self.veto_settle is not None:
                try:
                    self.veto_settle(round_id=w.session_id, action=action, part=part)
                except Exception:                    # noqa: BLE001 — הרשם אינו מפיל את תהליכון השידור
                    log.exception("chunksend: the veto's hold of %s %s was not settled", rule, action)

    def _first_pass(self, w: _Wave, part: int, listening: list[str], chunks: int) -> repair.Ranges | None:
        """#2528 שלב 4: המעבר הראשון של מחיצה שולח רק את מה שחסר, כשלכל דיסק של כל מי שהתייצב יש כבר ב-ledger של
        הגל הזה חתיכות שאומתו (משכפל שחזר וממשיך: הקריאה החוזרת שלו, `agent/lib/restore/resume.sh`, נשלחה עם
        `listening`). #3670 (S16): **ההחלטה היא של המוח** (`brain.resume.first_pass`, ורשומה ב-`brain_decisions`); כאן רק
        העובדות -- מי התייצב, ואילו דיסקים נושאים -- וביצוע התשובה. None = הכול משודר כמו היום. [] = לאף אחד לא חסר דבר."""
        with self._lock:
            disks = [(mac, s) for mac in listening if (mk := w.marks.get((part, mac))) is not None
                     for s in mk.serials if s not in mk.failed]
        got = brain_resume.first_pass(getattr(self.ledger, "conn", None), session_id=w.session_id, part=part, chunks=chunks,
                                      listening=listening, disks=disks,
                                      held=lambda mac, serial: self.ledger.durable(w.session_id, mac, serial, part))
        if got.need is not None:
            self.on_event("chunk_first_pass_from_ledger", f"{w.session_id} part={part} send={repair.count(got.need)}"
                          f" of {chunks} (every disk listening holds the rest, read back)", w.session_id)
        return got.need

    def _alive_among(self, w: _Wave, among: list[str] | set[str]) -> list[str]:
        """מי מ-`among` חי עכשיו."""
        return [m for m in self._live(w) if m in among]

    def _silent(self, w: _Wave, part: int, involved: list[str], reported: list[str]) -> list[str]:
        """מי ש-`_fail_silent` עומד להכשיל את הדיסקים שלו: לא דיווח, לא נשמט כאיטי, ועוד חסר לו."""
        with self._lock:
            return [m for m in involved
                    if m not in reported and (part, m) not in w.dropped and self._lacks(w, part, m)]

    def _unreported(self, w: _Wave, part: int, involved: list[str], rnd: int) -> list[str]:
        """#3064 (D19): מי שחי, היה בגל, ועוד לא דיווח `ended` על המעבר `rnd` -- ואינו כבר מושמט כאיטי, ועוד נשאר לו דיסק
        שלא נכשל. מי שכל דיסקיו כבר נכשלו (למשל `_fail_silent` זה עתה, אחרי ההחזקה של D15) אין מה לחכות לו: דיווח שלו
        לא ישנה את התוכנית, והחזקה נוספת עליו הייתה תקרה שנייה על אותה שתיקה."""
        live = set(self._live(w))
        with self._lock:
            quiet = []
            for m in involved:
                mk = w.marks.get((part, m))
                if (m not in live or (part, m) in w.dropped or mk is None
                        or (mk.round > rnd or (mk.round == rnd and mk.state == "ended"))
                        or all(s in mk.failed or (m, s) in w.failed_disks or (part, m, s) in w.held_disks for s in mk.serials)):
                    continue
                quiet.append(m)
            return quiet

    def _write_kbps(self, w: _Wave, part: int, members: list[str]) -> int:
        """`--write-kbps` של מחיצה: `_write_base` (בדיקת הכתיבה), ואחריו הלולאה הסגורה של K8 (`write_rate`, #3506). מה שיצא,
        ועל אילו דיסקים הוא נשען, נשמר לסוף הגל (`rate_outcome`) -- הגל הבא קורא אותו."""
        base, counted = self._write_base(w, part, members)
        kbps, ruled = self._ruled(w, part, base, counted) if counted and self.write_rate is not None else (base, None)
        with self._lock:
            w.part_write[part] = (kbps, tuple(counted), ruled)
        return kbps

    def _ruled(self, w: _Wave, part: int, base: int, counted: list[str]) -> tuple[int, str | None]:
        """#3506: `write_rate` -- never raises, never under the floor; an answer that is not (a positive number, a label or
        None) is `base`."""
        write_rate = self.write_rate
        if write_rate is None:                  # the caller asks only when it is set
            return base, None
        try:
            got = write_rate(w.session_id, part, base, list(counted))
        except Exception:                       # noqa: BLE001 -- the learning never stops a wave
            log.exception("chunksend: the write rate of %s part %d failed; keeping %d", w.session_id, part, base)
            return base, None
        if (isinstance(got, tuple) and len(got) == 2 and isinstance(got[0], int) and not isinstance(got[0], bool)
                and got[0] > 0 and (got[1] is None or isinstance(got[1], str))):
            return max(self.write_floor() * 8000, got[0]), got[1]
        return base, None

    def _write_base(self, w: _Wave, part: int, members: list[str]) -> tuple[int, list[str]]:
        """הקצב שהדיסקים נדרשים לכתוב (`--write-kbps`). **המוח מחליט** (`brain.writerate`, K-WRITE, S3, #3655): האיטי מבין מה שנמדד בבדיקת
        הכתיבה שלפני הזרם (#1366) × המרווח, לא מתחת לרצפה; דיסק שפיגר בגל חתיכות קודם אינו נספר (#3295, נדב 15/11). כאן נאספות העובדות
        (מה כל דיסק מדד, ההיסטוריה שלו) ומבוצעת התוצאה: שורת היומן על מי שהוחרג, והקצב. אין החלטה (המוח נכשל) = הרצפה."""
        locked = _measured_write_kbps()
        if locked is not None:
            log.info("chunksend: write_kbps_locked %s part %d: %d (%s)", w.session_id, part, locked, MEASURE_WRITE_KBPS_ENV)
            return locked, []
        with self._lock:
            measured = [(s, v) for m in members if (mk := w.marks.get((part, m))) is not None
                        for s, v in mk.write_mbps.items() if s not in mk.failed]
        floor = self.write_floor()
        # the history is read only when there is a complete set of checks to weigh it against (as before: no check, no history read)
        facts = self._histories(w, measured) if measured and all(v is not None for _s, v in measured) else {}
        call = brain_writerate.decide_base(self.brain_conn, measured, facts, floor, round_id=w.session_id, part=part)
        if call is None:
            return floor * 8000, []
        for serial, probe, mbps, source in call.left_out:
            if serial not in w.write_noted:
                w.write_noted.add(serial)
                # #3574: the serial is a quoted JSON string, not a bare word: `t10.ATA     APS-SL3N-512 ...` has spaces,
                # and the brain (`waveeta._BEHIND`) reads this line back -- a serial must never decide where a field ends.
                self.on_event("chunk_write_behind",
                              f"{w.session_id} {json.dumps(serial, ensure_ascii=False)} check={probe}MB/s wrote={mbps}MB/s in={source}"
                              f" {'counted:all_behind' if call.all_behind else 'left_out'}", w.session_id)
        return call.kbps, list(call.counted)

    def _histories(self, w: _Wave, measured: list[tuple[str, int | None]]) -> dict[str, tuple[int, str]]:
        """#3295: the `write_history` of each drive of `measured` that has one -- facts for the brain's `writerate.left_out`."""
        return {s: h for s, _v in measured if (h := self._history(w, s)) is not None}

    def _history(self, w: _Wave, serial: str) -> tuple[int, str] | None:
        """#3295: `write_history` of one drive, or None -- no history, not wired, an answer that is not (a positive
        number, a wave), or a failure: the drive counts, as before (the learning never stops a wave)."""
        if self.write_history is None:
            return None
        try:
            got = self.write_history(serial)
        except Exception:                       # noqa: BLE001 -- the learning never stops a wave
            log.exception("chunksend: the write history of %s in %s failed; it counts", serial, w.session_id)
            return None
        if (isinstance(got, tuple) and len(got) == 2 and isinstance(got[0], int) and not isinstance(got[0], bool)
                and got[0] > 0 and isinstance(got[1], str)):
            return got
        return None

    def _start_rate(self, w: _Wave, part: int, members: list[str], bits: int) -> int:
        """#3269: הקצב שהגל יוצא בו, אחרי בדיקת הכתיבה של המגירות שלו (`wave_start`). תשובה שאינה מספר חיובי, או
        כשל -- הקצב שנבחר, כמות שהוא: הלמידה אינה עוצרת גל (כמו `decide`).
        #3269/#3295, הכרעת נדב 15/11 ("האיטי לא מאט את הגל"): דיסק שפיגר בגל חתיכות קודם (`write_history`) אינו
        נספר גם כאן, כמו ב-`_write_kbps`: בדיקת הכתיבה שלו אינה קובעת את `--kbps`. בלי היסטוריה -- כמו היום. כולם
        פיגרו -- כולם נספרים, כמו היום."""
        if self.wave_start is None:
            return bits
        with self._lock:
            probes = {(m, s): v for m in members if (mk := w.marks.get((part, m))) is not None
                      for s, v in mk.write_mbps.items() if s not in mk.failed}
        pairs = [(s, v) for (_m, s), v in probes.items()]
        slow = brain_writerate.left_out(pairs, self._histories(w, pairs), self.write_floor())
        fast = {key: v for key, v in probes.items() if key[1] not in slow}
        probes = fast or probes
        try:
            got = self.wave_start(w.session_id, bits, probes)
        except Exception:                       # noqa: BLE001 -- the learning never stops a wave
            log.exception("chunksend: the start rate of %s failed; keeping %s", w.session_id, bits)
            return bits
        if not isinstance(got, int) or isinstance(got, bool) or got <= 0:
            return bits
        with self._lock:
            w.bits = got
        return got

    def _command(self, w: _Wave, part: dict, path: Path, kbps: int, write_kbps: int,
                 chunks: repair.Ranges | None, learned_max: int | None = None,
                 say: tuple[str, str] | None = None, order: str | None = None) -> list[str]:
        cmd = ["chunkcast", "send", "--to", f"{GROUP}:{self.port}", "--session", w.key,
               "--part", str(part["index"]), "--pass", str(w.round),
               "--index", f"{path}.idx", "--file", str(path),
               "--kbps", str(kbps), "--write-kbps", str(write_kbps)]
        cmd += ["--feedback", "on" if self.feedback else "off", "--control-port", str(self.port + 1)]
        if write_kbps or kbps:
            # #3655 (S4): the brain decides the feedback's room (`--rate-min/max/cap`); #3599: only a full pass of a partition may grow the ceiling
            # to the wire's capacity, a repair pass (a list of chunks) is short and does not. No decision: the rate is pinned (min = max = what was asked)
            got = brain_writerate.decide_bounds(
                getattr(self, "brain_conn", None), kbps, write_kbps, self.write_floor(), learned_max,
                round_id=getattr(w, "session_id", ""), part=part["index"],
                file_bytes=_size_of(path), chunks=getattr(w, "part_chunks", {}).get(part["index"]), full_pass=chunks is None,
                chunk_bytes=CHUNK_BYTES)
            if got is None:
                cmd += ["--rate-min", str(write_kbps or kbps), "--rate-max", str(write_kbps or kbps)]
            else:
                cmd += ["--rate-min", str(got.low), "--rate-max", str(got.high)]
                if got.cap is not None:
                    cmd += ["--rate-cap", str(got.cap)]
        if say is not None:                     # #3532: None = the sender's own 1000/2000 (a caller that does not ask)
            cmd += ["--lead-ms", say[0], "--tail-ms", say[1]]
        if self.interface:
            cmd += ["--iface", self.interface]
        if self.source:
            cmd += ["--from", self.source]      # אותה כתובת שהמקלטים קיבלו בהוראת הגל
        if order is not None:                   # #3522: None = the sender's own `seq` (a caller that does not ask)
            cmd += ["--order", order]
        if chunks is not None:
            # בקובץ ולא כארגומנט: 20% חוסר מפוזר במחיצה גדולה הם אלפי טווחים, מעל מה שארגומנט אחד מחזיק (E2BIG)
            fd, name = tempfile.mkstemp(prefix="imagesrv-chunks-", suffix=".txt")
            with os.fdopen(fd, "w", encoding="ascii") as f:
                f.write(repair.as_text(chunks) + "\n")
            cmd += ["--chunks-file", name]
        # #3571: the command is already complete. This only records that per-disk durable, RTT and inventory are not on this path.
        brain_planes.note_send(getattr(self, "brain_conn", None), round_id=getattr(w, "session_id", "") or "")
        return cmd

    def _learned_max(self, w: _Wave, part: int) -> int | None:
        """#3511: K8's maximum for the feedback of this partition (`write_ceiling`, over the serials `write_rate` counted), or
        None -- not wired, nothing was counted, an answer that is not a positive number, or a failure: the brain's own room (`writerate.RATE_MAX_FACTOR`)."""
        if self.write_ceiling is None:
            return None
        with self._lock:
            counted = list((w.part_write.get(part) or (0, (), None))[1])
        if not counted:
            return None
        try:
            got = self.write_ceiling(counted)
        except Exception:                       # noqa: BLE001 -- the learning never stops a wave
            log.exception("chunksend: the write ceiling of %s part %d failed; the feedback keeps its fallback", w.session_id, part)
            return None
        return got if isinstance(got, int) and not isinstance(got, bool) and got > 0 else None

    def _send_pass(self, w: _Wave, part: dict, path: Path, kbps: int, write_kbps: int,
                   chunks: repair.Ranges | None) -> bool:
        cmd = self._command(w, part, path, kbps, write_kbps, chunks, self._learned_max(w, part["index"]), self.say(),
                            self.order())
        with self._lock:                        # #3125 (U2, R7): what this pass asks of the drives, and who set it
            probes = {(m, s): v for (p, m), mk in w.marks.items() if p == part["index"]
                      for s, v in mk.write_mbps.items() if s not in mk.failed}
            rnd_now = w.round
            ruled = (w.part_write.get(part["index"]) or (0, (), None))[2]
        self.passlog.rates(w.session_id, part["index"], rnd_now, kbps, write_kbps, probes, self.write_floor(), part["file"],
                           ruled=ruled)
        process, error = None, None
        with self._lock:
            if self._stop.is_set():
                return False
            try:
                process = self.runner(cmd)
            except OSError as exc:
                error = exc
            else:
                self._process = process
                w.commands.append(cmd)
        if process is None:
            self._fail(f"chunkcast send לא רץ: {error} — {NOT_INSTALLED}")
            return False
        began = self.clock()
        code = self._wait_pass(w, part["index"], process)
        paced = chunklog.paced_of(chunklog.log_path(path))       # #3498: what held the sender back, by its own count
        stats = chunklog.sendstats_of(chunklog.log_path(path))   # #3553: how long it waited for the library file, by its own count
        with self._lock:
            self._process = None
            if w.round == 1:
                took = self.clock() - began
                w.pass1_s += took
                w.part_pass1_s[part["index"]] = w.part_pass1_s.get(part["index"], 0.0) + took
                before = w.part_paced.get(part["index"], (0, 0))
                w.part_paced[part["index"]] = (None if paced is None or before is None
                                               else (before[0] + paced[0], before[1] + paced[1]))
                w.part_sendstats[part["index"]] = (stats if part["index"] not in w.part_sendstats
                                                   else chunklog.add_sendstats(w.part_sendstats[part["index"]], stats))
        if "--chunks-file" in cmd:
            listed = cmd[cmd.index("--chunks-file") + 1]
            try:
                os.unlink(listed)
            except FileNotFoundError:
                pass
            except OSError as exc:              # #3124 (A-NET-08): הרשימה כבר נקראה, וקובץ זמני שנשאר אינו כישלון של הגל -- אבל נאמר בשם
                log.warning("chunksend: the chunk list %s was not removed (%s); the pass is unaffected", listed, exc)
        if self._stop.is_set():
            return False
        if w.carousel_cut:
            w.carousel_cut = False              # #3064 (B12/B47): the brain cut its own full pass -- the round goes on
            return True
        if code in NOT_RUN_CODES:
            # `nice` הופעל, `chunkcast` לא: הקוד הוא של המעטפת, לא של השולח
            self._fail(f"chunkcast send לא רץ על {part['file']} (קוד {code}) — {NOT_INSTALLED}")
            return False
        if code == STOPPED_BY_SIGNAL and self._going_down.wait(self.shutdown_grace):
            # #3094: הילד נעצר באות שהגיע גם לשרת (`systemctl restart`) -- הסיבה היא השרת, לא השולח
            self._fail(SERVER_STOPPED.format(file=part["file"], round=w.round))
            return False
        if code != 0:
            # #3161: a remote sender (the build machine) gives its own reason; a local `chunkcast` gives its code
            # #3261: and then the sender's own sentence (its log). Not read = not said: no reason is made up
            failure = getattr(process, "failure", None)
            if failure:
                self._fail(failure)
                return False
            said = chunklog.log_tail(chunklog.log_path(path))
            self._fail(f"chunkcast send נכשל על {part['file']} (קוד {code}, מעבר {w.round})" + (f": {said}" if said else ""))
            return False
        return True

    def _wait_pass(self, w: _Wave, part: int, process: chunklog.SendProcess) -> int:
        """`process.wait()` -- and during a full pass the carousel put in place of the plan (B7), `carousel_cut` is asked
        every `POLL_S` with the pass's live listeners: a reason = the pass is cut (B12 lone handoff, B47 no listener)."""
        if self.carousel_cut is None or w.carousel != (part, w.round):
            return process.wait()
        while True:
            try:
                return process.wait(timeout=POLL_S)
            except subprocess.TimeoutExpired:
                pass
            plan = w.plan
            listening = [m for m in self._live(w) if plan is not None and m in plan.receive]
            if self.carousel_cut(w.session_id, part, w.round, listening):
                with self._lock:
                    w.carousel_cut = True
                process.terminate()
                return process.wait()

    def _run(self, session: dict) -> None:
        w = self._wave_now()
        try:
            self._send(w, session)
        except RosterReadError as exc:
            self._fail(str(exc), session_id=w.session_id)
        except Exception as exc:                 # noqa: BLE001 — כאן נגמר התהליכון (#2427)
            log.exception("chunksend: the thread of %s crashed", w.session_id)
            self.stop(w.session_id)
            self._fail(f"תהליכון השידור נפל ({type(exc).__name__}: {exc}) — השידור הופסק")

        finally:
            fecrule.record(self.brain_conn, w.session_id, {p: w.fec_parts.get(p, {}) for p in w.part_chunks})

    def _send(self, w: _Wave, session: dict) -> None:
        # #705/#2668: לפני הכול — הכרטיס של הגל הזה, והשידור דלוק בו.
        if self.select_interface is not None:
            try:
                self.interface = self.select_interface(session)
            except ValueError as exc:
                self._fail(str(exc))
                return
        if self.gate is not None:
            refused = self.gate(self.interface)
            if refused:
                self._fail(refused)
                return
        # לפני שמשכפל אחד מתייצב ולפני שמחיצה אחת נבדקת: יש שולח, והוא רץ.
        problem = self.binary_problem()
        if problem:
            self._fail(f"{problem}. דבר לא שודר")
            return
        manifest = self.library.get(session["image_id"])
        if manifest is None:
            self._fail(f"אימג' {session['image_id']} לא נמצא בספרייה")
            return
        raw_rate = self.wave_bitrate(session) if self.wave_bitrate is not None else None
        bits = rate_bits(raw_rate)
        if bits is None:
            self._fail(f"קצב הגל ({raw_rate!r}) אינו מספר — המנוע אינו משדר בלי קצב")
            return
        with self._lock:
            w.bits = bits
        partitions = streamed_partitions(manifest)
        # לפני הבייט הראשון, לא באמצע הגל: לכל מחיצה יש קובץ ואינדקס בפורמט 2 (חוזה הפורמט, סעיף 6),
        # וכל מי שבגל הצהיר ב-hello שהוא מכיר את המנוע.
        chunks_of: dict[int, int] = {}
        zbytes_of: dict[int, list[int]] = {}
        for part in partitions:
            path = self.library.file_path(session["image_id"], part["file"])
            if path is None or not Path(path).is_file():
                # #1928: the image itself is missing -- said so, and not read as an index still being derived (principle 5).
                # The operator must not try again and again.
                self._fail(f"קובץ מחיצה חסר באימג' {session['image_id']}: {part['file']}"
                           " — האימג' חסר או פגום בשרת. דבר לא שודר")
                return
            # אותו תנאי שבו `GET …/index/<n>` מגיש את האינדקס למקלטים (`readbackhash.has_list`): אינדקס
            # שלם ועקבי ליד הקובץ (`chunkformat.read_index`), שמסכים עם מה שהשרת רשם במניפסט כשאימת אותו.
            found = chunkformat.read_index(Path(path)) if path is not None else None
            if (found is None or not isinstance(part.get("index_xxh64"), str)
                    or part.get("readback_chunks") != found.chunks):
                # האינדקס נגזר בשרת ברקע אחרי הקליטה (שלב 2): עד אז הקובץ אינו קיים
                self._fail(f"למחיצה {part['file']} באימג' {session['image_id']} אין אינדקס מאומת ליד הקובץ"
                           " — הוא עדיין נבנה אחרי הקליטה, או שהאימג' בפורמט ישן. דבר לא שודר")
                return
            chunks_of[part["index"]] = found.chunks
            zbytes_of[part["index"]] = [zbytes for _offset, zbytes, _xxh in found.rows]
        with self._lock:
            w.part_chunks, w.part_zbytes = chunks_of, zbytes_of
            old = sorted(m for m in self._live(w) if m not in self._capable)
        if old:
            self._fail(f"מחשב בגל ({', '.join(old)}) מריץ סוכן שאינו מכיר את מנוע החתיכות —"
                       " יש לעדכן את אימג' הסוכן שלו, או לכבות את המתג 'מנוע שידור'. דבר לא שודר")
            return
        self.on_event("send_start", f"{w.session_id} {w.image_id} partitions={len(partitions)} engine=chunk",
                      w.session_id)
        for number, part in enumerate(partitions, start=1):
            path = self.library.file_path(session["image_id"], part["file"])
            index = {"chunks": chunks_of[part["index"]]}
            with self._lock:
                if w.state == "failed":
                    break
                w.part, w.chunks, w.round, w.plan, w.state = part["index"], index["chunks"], 1, None, "sending"
            later = number > 1
            # #3112 (G3): the wait the message names is the one waited -- a later partition's is longer (metal 13/11:
            # 780 s, and the message said 180)
            settle = chunkctx.later_start_timeout(self.max_wait, self.start_timeout) if later else self.start_timeout
            asked_at = self.clock()
            listening = self._wait_marks(
                w, part["index"], 1, "listening", lambda: self._live(w),
                chunkctx.later_max_wait(self.max_wait) if later else self.max_wait, settle)
            if listening == []:
                # #3064 (K2, D16): "אף מחשב לא התייצב" נשען על שתיקה -- הווטו נשאל לפני שהגל כולו נכשל
                listening = self._hold(
                    w, "FAIL_WAVE", "D16", part["index"], lambda: self._live(w),
                    f"{'later_start_timeout' if later else 'start_timeout'} {settle:g}s, {part['file']}",
                    lambda _silent: self._wait_marks(
                        w, part["index"], 1, "listening", lambda: self._live(w),
                        chunkctx.later_max_wait(self.max_wait) if later else self.max_wait, VETO_RECHECK_S),
                    until_any=True)
            if listening is None:
                break
            # #3125 (U3, R10): who did not show up, and how long the server really waited (a veto hold adds to `settle`)
            absent = [m for m in self._live(w) if m not in (listening or [])]
            waited = self.clock() - asked_at
            if not listening:
                self._fail(f"אף מחשב לא התייצב לשידור של {part['file']} תוך {waited:.0f} שניות{chunkpass.who_he(absent)}."
                           " זה אינו אימג' פגום: יש לבדוק כבל רשת, אתחול PXE ורישום MAC")
                return
            self.passlog.no_show(w.session_id, part["file"], absent, waited)
            write_kbps = self._write_kbps(w, part["index"], listening)
            if number == 1:
                bits = self._start_rate(w, part["index"], listening, bits)
            floor = self.write_floor()          # המהירות המינימלית לכונן (#3170): פעם אחת למחיצה, מחוץ לנעילה
            self._note_part_gap(w)
            chunks = self._first_pass(w, part["index"], listening, index["chunks"])   # #2528: None = הכול
            fetchers: set[str] = set()
            involved: list[str] = listening
            while True:
                if chunks != [] and (chunks is not None or w.round == 1) and index["chunks"]:
                    if not self._send_pass(w, part, path, bits // 1000, write_kbps, chunks):
                        break
                rnd = w.round
                ceiling = self.lost_wait() + ENDED_GRACE_S
                reported = self._wait_marks(
                    w, part["index"], rnd, "ended",
                    partial(self._live_noting_away, w, part["index"], rnd, involved), ceiling)
                if reported == [] and rnd == 1:
                    # #3064 (K2, D18): "אף מחשב לא דיווח על סיום המעבר" -- הווטו נשאל לפני שהגל כולו נכשל
                    reported = self._hold(
                        w, "FAIL_WAVE", "D18", part["index"],
                        partial(self._alive_among, w, involved),
                        f"no ended for pass 1 in {ceiling:g}s, {part['file']}",
                        lambda silent: self._wait_marks(w, part["index"], rnd, "ended",
                                                        lambda: [m for m in self._live(w) if m in silent],
                                                        VETO_RECHECK_S),
                        until_any=True)
                elif reported is not None and rnd > 1:
                    # #3064 (K2, D15): הדיסקים של מי ששתק נכשלים בשמם -- הווטו נשאל לפני כן
                    heard = self._hold(
                        w, "FAIL_DISKS", "D15", part["index"],
                        partial(self._silent, w, part["index"], involved, reported),
                        f"silent in repair pass {rnd} for {ceiling:g}s, {part['file']}",
                        lambda silent: self._wait_marks(w, part["index"], rnd, "ended",
                                                        lambda: [m for m in self._live(w) if m in silent],
                                                        VETO_RECHECK_S))
                    reported = None if heard is None else [*reported, *heard]
                if reported is None:
                    break
                # #4154: "every receiver rejected it" is said only when every live receiver of the wave has said so. One that
                # has not reported `ended` (late, or still downloading) may hold the chunk durable: the plan decides, not an abort
                live = set(self._live(w))
                unheard = [m for m in involved if m in live and m not in reported]
                with self._lock:
                    marks = {mac: w.marks.get((part["index"], mac)) for mac in reported}
                    shared = set()
                    if len(marks) >= 2 and not unheard and all(m and m.round == rnd and m.state == "ended" and m.bad_chunks and m.serials
                                               for m in marks.values()):
                        serials = {s for m in marks.values() for s in m.serials}
                        if len(serials) >= 2:
                            shared = set.intersection(*(set(m.bad_chunks) for m in marks.values()))
                    for mac, mark in marks.items():
                        if not shared:
                            break
                        for serial in mark.serials:
                            have = self.ledger.durable(w.session_id, mac, serial, part["index"])
                            for k in tuple(shared):
                                pos = bisect_right(have, (k, index["chunks"]))
                                if pos and k <= have[pos - 1][1]:
                                    shared.remove(k)
                if shared:
                    self._fail(f"{chunkpass.SOURCE_FAILURE_PREFIX}{part['index']}/chunk{min(shared)}: "
                               f"כשל באימג' {w.image_id} — כל המקלטים פסלו את אותה חתיכה מול האינדקס")
                    return
                if rnd == 1 and not reported:
                    # אין ראיה שמישהו קיבל משהו: זה אינו "אין מה להשלים" (עיקרון 5)
                    self._fail(f"אף מחשב לא דיווח על סיום המעבר של {part['file']} (מעבר {rnd})"
                               " — אין לדעת מה נכתב")
                    break
                if rnd > 1:
                    # סבב השלמה: מי שלא דיווח נכשל בשמו ולבדו. הגל ממשיך עם מי שנשאר.
                    self._fail_silent(w, part["index"], involved, reported, fetchers,
                                      self._stall_window(w, part["index"], involved, ceiling))
                held = False
                while True:
                    live = set(self._live(w))
                    with self._lock:
                        if w.state == "failed":
                            break
                        # כל מי שחי וכבר סיים מעבר כלשהו של המחיצה — גם מי שדיווח באיחור
                        durable = {mac: {s: self.ledger.durable(w.session_id, mac, s, part["index"])
                                         for s in mk.serials if s not in mk.failed and (mac, s) not in w.failed_disks
                                         and (part["index"], mac, s) not in w.held_disks}
                                   for (p, mac), mk in w.marks.items()
                                   if p == part["index"] and mac in live and mk.state == "ended"}
                        told = {mac: dict(mk.skipped) for (p, mac), mk in w.marks.items()
                                if p == part["index"] and mk.round == 1 and mac in durable}
                        direct = self.no_file is not None and bool(self.no_file(w.session_id))
                        plan = repair.plan(index["chunks"], durable, repairs_done=rnd - 1, skipped=told,
                                           asked_mbps=write_kbps / 8000, floor_mbps=floor, threshold=repairrule.threshold(direct))
                        w.failed_disks.update(plan.failed)
                        away = {mac for p, mac in w.away if p == part["index"]}
                        if rnd == 1:
                            loss_reports = [w.loss_reports.get((part["index"], m)) for m in involved]
                            w.told1[part["index"]] = (told, dict(plan.lost_in_net))
                            # measured = every machine that finished the pass told us what its drives skipped
                            sizes = w.part_zbytes.get(part["index"])
                            w.pass1[part["index"]] = {
                                "chunks": index["chunks"], "lost": max(plan.lost_in_net.values(), default=0),
                                "measured": bool(durable) and all(told.get(mac) for mac in durable), "away": len(away),
                                # #3296: what each drive took from THIS pass -- the HTTP that completes it comes later
                                "zbytes": None if sizes is None else sum(sizes),
                                "drives": None if sizes is None else {
                                    mac: {s: _zbytes_in(sizes, r) for s, r in got.items()} for mac, got in durable.items()},
                                # #3295: a drive the receiver skipped chunks for ("the drive was behind") wrote as fast as
                                # it could: the chunks durable on it at the end of this pass, over the pass's seconds
                                "behind": {s: repair.count(r) for mac, got in durable.items() for s, r in got.items()
                                           if (told.get(mac) or {}).get(s)},
                                "seconds": w.part_pass1_s.get(part["index"])}
                        # מי נשאר: מחשב חי עם דיסק שלא נכשל. בלי אף אחד כזה אין למי לשדר את המחיצה הבאה.
                        left = any(s not in mk.failed and (mac, s) not in w.failed_disks and (p, mac, s) not in w.held_disks
                                   for (p, mac), mk in w.marks.items()
                                   if p == part["index"] and mac in live and mk.state == "ended" for s in mk.serials)
                        if left or held or self.veto is None:
                            if left and not plan.done and self.carousel is not None and not (
                                    self.no_file is not None and self.no_file(w.session_id)):
                                # #3064 (B7): one more full pass, or the plan -- recorded always; in place of the plan
                                # only when the carousel's own switch is on. Before the plan is told to anyone
                                full = self.carousel(w.session_id, part["index"], rnd, index["chunks"], durable, plan,
                                                     w.part_zbytes.get(part["index"]), w.bits)
                                if full is not None:
                                    plan, w.carousel = full, (part["index"], rnd + 1)
                            if plan.done:
                                w.done_parts.add(part["index"])
                                w.plan = None
                            else:
                                w.round, w.plan, w.state = rnd + 1, plan, "repairing"
                                w.stats["repair_rounds"] += 1
                                w.stats["multicast_chunks"] += repair.count(plan.multicast)
                                w.stats["fetched_chunks"] += sum(repair.count(r) for r in plan.fetch.values())
                            break
                    # #3064 (K2, D19): אין מחשב עם דיסק שלא נכשל, אבל מחשב חי עוד לא סיים את המעבר (למשל מגירה
                    # שחזרה ומשלימה ברקע). ב-enforce ממתינים ל-`ended` שלו ומתכננים מחדש, במקום להכשיל את הגל; ב-shadow
                    # נרשם "היה חוסם" והגל נכשל כמו היום. `held` אחרי שהווטו כבר נשאל ולא הגיע דבר: מתכננים, ונכשלים.
                    heard = self._hold(
                        w, "FAIL_WAVE", "D19", part["index"],
                        partial(self._unreported, w, part["index"], involved, rnd),
                        f"no live machine with a disk left, pass {rnd}, {part['file']}",
                        lambda silent: self._wait_marks(
                            w, part["index"], rnd, "ended",
                            lambda: [m for m in self._live(w) if m in silent], VETO_RECHECK_S),
                        until_any=True)
                    if heard is None:
                        break
                    held = not heard
                if self._stop.is_set():
                    break
                # #3659 (S10, S11, S12): the decision of this round -- the threshold, the cap, who failed and who is slow -- is a `brain_decisions` row
                if rnd == 1:
                    w.fec_parts[part["index"]] = fecrule.partition(loss_reports, zbytes_of[part["index"]])
                repairrule.record_plan(getattr(self, "brain_conn", None), plan, repairs_done=rnd - 1, direct=direct,
                                       machines=len(durable), round_id=w.session_id, part=part["index"])
                for (mac, serial), why in plan.failed.items():
                    self.on_event("chunk_disk_failed", f"{mac} {serial}: {why}", w.session_id)
                for (mac, serial), why in plan.slow.items():
                    # #3170 (נדב 15/11, אפשרות ב'): הדיסק שפיגר אינו נכשל -- הוא משלים מהשכן או מהשרת. רק שורה ביומן.
                    self.on_event("chunk_disk_slow", f"{mac} {serial}: {why}", w.session_id)
                self._tell_cause(w, part["index"], "D11" if rnd == 1 else "D12",       # איטי / אחרי MAX_REPAIRS
                                 [(m, s, why) for (m, s), why in plan.failed.items()])
                for mac, lost in plan.lost_in_net.items():
                    # #3141 (א): הסיבה האמיתית ביומן, והדיסק משלים מהשרת -- אינו נכשל
                    why = ("ניתוק רשת: השרת איבד את המחשב באמצע המעבר" if mac in away
                           else "חבילות שאבדו ברשת, לא דיסק איטי")
                    self.on_event("chunk_net_gap", f"{mac}: {lost} חלקים לא הגיעו למחשב בשידור הראשון"
                                  f" ({why}) — הם מושלמים מהשרת", w.session_id)
                if not left:
                    self._fail(f"לא נשאר אף מחשב חי עם דיסק שלא נכשל במחיצה {part['file']} (מעבר {rnd})"
                               " — כל מי שהיה בגל אבד, שתק או נכשל; הסיבה לכל דיסק ביומן")
                    break
                if plan.done:
                    break
                self.on_event("chunk_repair",
                              f"{w.session_id} {part['file']} round={rnd + 1}"
                              f" multicast={repair.count(plan.multicast)} to {len(plan.receive)}"
                              f" fetch={ {m: repair.count(r) for m, r in plan.fetch.items()} }", w.session_id)
                involved, fetchers = [*plan.receive, *plan.fetch], set(plan.fetch)
                chunks = plan.multicast or None
                with self._lock:                # מכאן נמדד הקצב של כל הורדה: מתי התחילה, וכמה היה לו אז
                    began = self.clock()
                    for mac in plan.fetch:
                        serials = w.marks[(part["index"], mac)].serials if (part["index"], mac) in w.marks else ()
                        base = {s: repair.count(self.ledger.durable(w.session_id, mac, s, part["index"])) for s in serials}
                        have = sum(base.values())
                        w.fetching[(part["index"], mac)] = [began, have, have, None]
                        w.fetch_base[(part["index"], mac)] = base     # #2023: the base of each drive's own completion rate
                        w.gap.pop((part["index"], mac), None)
                if chunks is not None:
                    ready = self._wait_marks(w, part["index"], w.round, "listening",
                                             partial(self._alive_among, w, plan.receive),
                                             self.max_wait, self.lost_wait())
                    if ready is None:
                        break
                    if not ready:
                        chunks = None           # איש אינו מאזין: אין משדרים מעבר לאף אחד. מי שלא התייצב ייכשל בשמו למטה
            if self._stop.is_set() or w.state == "failed":
                break
            w.part_end = self.clock()           # #2023: where the next partition's overhead begins
        with self._lock:
            if w.state == "failed":
                return
            stopped = self._stop.is_set()
            if stopped:
                self._ended.add(w.session_id)
                w.state = "stopped"
            else:
                w.state = "done"
        if stopped:
            self.on_event("send_stopped", w.session_id, w.session_id)
            return
        self.on_event("send_done", f"{w.session_id} {w.stats}", w.session_id)


def _zbytes_in(sizes: list[int], ranges: repair.Ranges) -> int:
    """#3296: the compressed bytes of the chunks in `ranges` (closed, `repair.Ranges`)."""
    return sum(sum(sizes[lo:hi + 1]) for lo, hi in ranges)


def _behind(parts: list[dict]) -> dict[str, dict[str, float]]:
    """#3295: {serial: {"bytes", "seconds"}} -- for each drive the receiver skipped chunks for in a first pass, the
    bytes it made durable in the passes it was behind in (`CHUNK_BYTES` a chunk: what it WROTE, the unit of
    `--write-kbps`), and those passes' seconds. A pass with no seconds is left out; no such drive = empty."""
    out: dict[str, dict[str, float]] = {}
    for p in parts:
        seconds = p.get("seconds")
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            continue
        for serial, chunks in (p.get("behind") or {}).items():
            got = out.setdefault(serial, {"bytes": 0, "seconds": 0.0})
            got["bytes"] += chunks * CHUNK_BYTES
            got["seconds"] += seconds
    return out


def _first_pass_drives(parts: list[dict]) -> dict[str, dict[str, int | None]]:
    """#3296: {mac: {serial: compressed bytes}} over the first passes of all partitions; a drive that is not in one
    of them is None -- what it took from that pass was not measured."""
    keys = {(mac, serial) for p in parts for mac, got in p["drives"].items() for serial in got}
    out: dict[str, dict[str, int | None]] = {}
    for mac, serial in sorted(keys):
        each = [p["drives"].get(mac, {}).get(serial) for p in parts]
        out.setdefault(mac, {})[serial] = None if None in each else sum(each)
    return out


def rate_bits(raw: object) -> int | None:
    """קצב הגל כפי ש-`drive_rates` נותן אותו (``"700m"``) → ביטים לשנייה."""
    from server.rounds import drive_rates       # noqa: PLC0415 — בלי מעגל ייבוא בעלייה
    return drive_rates.parse_bitrate(raw)


def _sum_sendstats(each: list[dict | None]) -> dict | None:
    """#3553: the `sendstats` of every partition's first pass, summed (`chunklog.add_sendstats`); None unless all of them were read."""
    if not each or any(x is None for x in each):
        return None
    total = each[0]
    for x in each[1:]:
        total = chunklog.add_sendstats(total, x)
    return total
