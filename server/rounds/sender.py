"""מנוע השידור — מפעיל udp-sender כשסבב עובר ל"משדר".

מה שעובר בקו מוגדר בסעיף 7 בממשקים: שידור אחד לכל קובץ מחיצה, בייט-בייט
כפי שהוא שמור, בסדר שבמניפסט. המנוע לא נוגע בתוכן — הוא רק מזרים קבצים.

udpcast עוצר בין שידורים עד שכל המקבלים מוכנים, ולכן המחיצות רצות
בזו אחר זו ולא במקביל.

הרצת התהליכים מוזרקת (`runner`) כדי שהבדיקות יריצו את כל הלוגיקה בלי
udpcast מותקן.

שני הפורטים (`portbase` ו-`portbase+1`) נבדקים **לפני** השידור: תהליך
יתום שמחזיק אותם מפיל את ההפצה הבאה, והאבחון נראה כמו תקלת רשת (#156).
הבדיקה המקדימה היא צילום רגע ולא ערובה — ולכן מי שנכנס בחלון שאחריה
נתפס בבדיקה שנייה **בנתיב הכישלון** (`_port_verdict`, ‏#202).
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from server.system import priority
from server.library.images import streamed_partitions

log = logging.getLogger("imagesrv.sender")

#: הפורטים שההפצה האמיתית מקצה בוילן ההפצה — `portbase` ו-`portbase+1`.
#: הערך הזה קבוע, והוא מה שחבילת הטסטים מוודאת שאינה נוגעת בו (#156).
PRODUCTION_PORTBASE = 9000
#: ברירת המחדל בפועל. חבילת הטסטים דורסת אותה ב-portbase גבוה ואקראי
#: (`hygiene.assign_test_portbase`), כדי שיתום של ריצה שנקטעה לא יוכל
#: להתנגש בהפצה אמיתית — גם אם איש לא ינקה אותו לעולם.
DEFAULT_PORTBASE = PRODUCTION_PORTBASE
#: כמה להמתין למקבלים שטרם התייצבו לפני שמשדרים בכל זאת. udpcast מחכה
#: לכל אחד בנפרד, ומכונה שנתקעה באתחול לא אמורה לעצור כיתה שלמה.
DEFAULT_MAX_WAIT = 120
#: כמה להמתין ל**מקבל הראשון** לפני שמוותרים. ‏`--max-wait` מתחיל לספור רק
#: מהמקבל הראשון, ולכן שידור שאיש לא הצטרף אליו אינו נכשל ואינו מסתיים —
#: הוא נתקע, והמסך מול הכיתה פשוט לא זז (#341). ‏`--start-timeout` הוא
#: התקרה על ההמתנה הזאת. שלוש דקות: מכונה שכבר אמרה hello צריכה פחות מזה
#: כדי להתייצב כמקבל, והמפעיל אינו נשאר מול מסך קפוא יותר מזה.
DEFAULT_START_TIMEOUT = 180
#: ההמתנה למקבלים **מהמחיצה השנייה והלאה** (#957, כמו FOG GH-536). למחיצה
#: הראשונה כל המכונות מגיעות יחד מהרשת, וההמתנה שם היא "המפעיל לא
#: התחיל". לבאות כל מכונה מגיעה כשסיימה **לכתוב** את הקודמת — ומגירה
#: איטית עם חוצץ fanout של עד 256MB מסיימת אימג' של 60GB יותר מ-120
#: שניות אחרי המהירה. עם `--max-wait 120` השולח היה מתחיל את ה-ESP
#: בלעדיה, וה-`udp-receiver` שלה נופל על מחיצה שאיש לא שידר לה.
#: הערך הוא `max(600, max_wait)` — לא פחות מעשר דקות, ולא פחות מהראשונה.
#:
#: ‏`--start-timeout` נע איתו: ב-udpcast הוא תקרה על **כל** שלב ההמתנה
#: (‏`udps-negotiate.c`: ‏`time(0) - loopStart >= startTimeout` → יציאה
#: בלי שידור), לא רק על המקבל הראשון — ‏max-wait של 600 מאחורי
#: ‏start-timeout של 180 היה מת. לכן למחיצות 2+ הוא `max_wait_later +
#: start_timeout`: עד 180 שניות למקבל הראשון, ואז עד 600 לשאר.
LATER_MAX_WAIT = 600
#: ‏ברזל 17/10: כל כמה זמן השולח שואל אם מחשב שיכפול בגל עוד ממתין לתשובת
#: אדם על דיסק (‏`smart_ask.open_in_wave`). קריאה אחת מה-DB, בלי תעבורה.
ASK_HOLD_POLL_S = 1.0


def later_max_wait(max_wait: int) -> int:
    return max(LATER_MAX_WAIT, max_wait)


def later_start_timeout(max_wait: int, start_timeout: float) -> int:
    return later_max_wait(max_wait) + int(start_timeout)
#: ‏`--rexmit-hello-interval` (מילישניות): כל כמה זמן udp-sender משדר שוב את
#: ה-hello שבו מקבל מתחבר. ברירת המחדל של udpcast היא 2000, וכל מחיצה היא
#: udp-sender משלה — ארבע מחיצות = ארבע המתנות של 2ש' בין "התחבר" ל"משדר",
#: על ברזל (‏17/10, ‏ses_fbd8f9ca: 08:24:46→48, 08:24:51→53, 08:24:56→58,
#: 08:29:43→45) ובמדידה על loopback (‏2.83ש' בברירת המחדל, ‏1.02ש' ב-200).
#: ה-hello הוא חבילה קטנה אחת; ב-5 לשנייה זו עלות אפסית גם במחכה של 600ש'.
HELLO_INTERVAL_MS = 200
#: אחרי כמה בקשות ACK ללא מענה udpcast מוותר על מקבל וממשיך בלעדיו.
#: **בלי הדגל הזה udpcast מגדיר 200**.
#:
#: ‏11/09/2026 (הכרעת נדב): מאמצים את הגישה של FOG/Clonezilla —
#: **"dynamic bitrate": להאט למקבל האיטי ולא להפיל אותו.** אימג' גדול
#: (win-build 67GB דחוס) נפל ב-~90% בשידור, ואימג' קטן (20GB) הצליח:
#: ‏back-pressure בזנב גרם ל-udp-sender להפיל מקבלים איטיים-אך-חיים אחרי
#: ‏15 בקשות ACK, המקבל שהופל המתין לנצח, ‏`--receive-timeout` שלו נורה
#: והזרם נקטע. הפתרון הוא לא להפיל את המקבל האיטי — כלומר תקרת נטישה
#: גבוהה, שמשאירה אותו בקבוצה ומאיטה את כל הזרם אליו.
#:
#: **מחיר מודע (#437):** מקבל שבאמת *מת* (שותק) מקפיא כעת את החדר בערך
#: ‏190 שניות לפני שהוא נזרק, במקום כ-15 קודם — כי אין דרך ל-udpcast
#: להבחין בין "איטי-אך-חי" ל"מת". ההכרעה של נדב היא שכשל *מלא* של
#: אימג' הדגל עדיף שלא יקרה, גם במחיר קפיאה של דקות כשמכונה קורסת.
#: ‏**הערך ניתן לכוונון דרך `IMAGESRV_RETRIES_UNTIL_DROP`** כדי שהמפעיל
#: יוכל להחזיר אותו אם הקפיאה הזו תתברר כגרועה מהתקלה שהיא מונעת.
#: **לא `--async`** — ויתור על ACK פירושו דאטגרם אבוד שהופך לדיסק פגום,
#: וזה בדיוק מה שהפרוטוקול הזה קיים כדי למנוע (עיקרון 4).
DEFAULT_RETRIES_UNTIL_DROP = 200


def _env_retries_until_drop() -> int:
    """התקרה מ-`IMAGESRV_RETRIES_UNTIL_DROP`, או ברירת המחדל.

    ערך env לא-מספרי נופל אחורה עם אזהרה ולא מפיל את הפעלת השרת: מספר
    שגוי בהגדרה אינו סיבה שהשידור כולו לא יעלה.
    """
    raw = os.environ.get("IMAGESRV_RETRIES_UNTIL_DROP")
    if raw is None:
        return DEFAULT_RETRIES_UNTIL_DROP
    try:
        value = int(raw)
    except ValueError:
        log.warning("IMAGESRV_RETRIES_UNTIL_DROP=%r אינו מספר — נופלים ל-%d",
                    raw, DEFAULT_RETRIES_UNTIL_DROP)
        return DEFAULT_RETRIES_UNTIL_DROP
    # ‏udp-sender סופר את הערך כ-int חתום: `0`/שלילי אינם "אל תזרוק" אלא
    # **זריקה כמעט מיידית** של המקבל הראשון שמפספס ACK — כלומר בדיוק כשל
    # ה-90% שהערך הזה קיים כדי למנוע, מוסווה כ"כוונון". מספר קטן מ-1 הוא
    # הגדרה שגויה, לא בקשה — נופלים לברירת המחדל עם אזהרה (עיקרון 5).
    if value < 1:
        log.warning("IMAGESRV_RETRIES_UNTIL_DROP=%r < 1 — זורק מקבל מיד; נופלים ל-%d",
                    value, DEFAULT_RETRIES_UNTIL_DROP)
        return DEFAULT_RETRIES_UNTIL_DROP
    return value
#: udpcast חוסם SIGTERM לכל אורך doTransfer — המתנה ארוכה ל-TERM אינה
#: ראיה שהוא ימות, היא זמן שמתבזבז לפני SIGKILL (#439).
STOP_TERM_WAIT = 0.5
STOP_KILL_WAIT = 1.0


#: הפלט של udp-sender — הבסיס לשם הלוג. ‏Starting transfer: בכל הקובץ
#: היא הראיה שהשידור באמת התחיל (#438); הזנב נכנס להודעת שגיאה אחרת.
#: **לוג נפרד לכל מחיצה** (`partition_log`) ולא קובץ יחיד: עד כאן
#: ‏run_process פתח את הקובץ הזה ב-"wb" לכל מחיצה, וכל מחיצה מחקה את לוג
#: קודמתה — כשל במחיצה השנייה בלע את ה-Starting transfer של הראשונה,
#: והראיה מדוע נפל השידור הגדול נמחקה במחיצה שאחריה (באג ראיות).
SENDER_LOG = Path(tempfile.gettempdir()) / "imagesrv-sender.log"
#: נמדד מול udpcast 20120424: בנתיב start-timeout השורה אינה נכתבת,
#: והתהליך יוצא 0 בכל זאת (#438).
TRANSFER_STARTED = "Starting transfer:"


def partition_log(part_file: str | Path) -> Path:
    """הלוג של מחיצה בודדת, נגזר מהדיסק ושם הקובץ, ליד `SENDER_LOG`.

    כל מחיצה כותבת לקובץ משלה, ולכן הראיה של מחיצה קודמת שורדת את
    השידור של הבאה. השם נגזר מ-`SENDER_LOG` כדי שבדיקה שמנתבת אותו
    מחדש (למשל ל-tmp) תנתב גם את הלוגים הפר-מחיצתיים איתו.
    """
    path = Path(part_file)
    name = "-".join(part for part in path.parts[-2:] if part not in ("/", "\\"))
    return SENDER_LOG.with_name(f"{SENDER_LOG.stem}-{name}{SENDER_LOG.suffix}")


def run_process(cmd: list[str]) -> subprocess.Popen:
    # לא stdout=PIPE: אף אחד לא קורא את הצינור, udp-sender מדפיס התקדמות
    # בלי הפסקה, וכשהחוצץ (~64KB) מתמלא — באמצע המחיצה הגדולה — התהליך
    # נחסם על הפלט של עצמו והשידור קופא, והמקבלים מתים בזה אחר זה (#22).
    # אותו לקח בדיוק כמו שרת הסימולציה ב-#12: פלט של תהליך ארוך → קובץ.
    # לוג פר-מחיצה נגזר מ---file, כדי שראיית מחיצה קודמת לא תימחק.
    # ‏#1974: בעדיפות הרקע (‏nice/ionice) — הקונסולה קודמת לשידור כשיש תחרות.
    with partition_log(cmd[cmd.index("--file") + 1]).open("wb") as handle:
        return subprocess.Popen(priority.background_cmd(cmd), stdout=handle,
                                stderr=subprocess.STDOUT)


#: טבלאות ה-UDP של הקרנל — מקור האמת למי מחזיק פורט בלינוקס.
UDP_TABLES = (Path("/proc/net/udp"), Path("/proc/net/udp6"))

#: פורט תפוס שלא הצלחנו לשייך לתהליך (סוקט של משתמש אחר). "לא ידוע מי"
#: אינו "אין אף אחד" — ולכן זה מחזיק, לא פנוי.
UNKNOWN_HOLDER = -1


def _inodes_on_port(port: int) -> set[str] | None:
    """ה-inodes של סוקטי UDP שקשורים ל-`port`; ‏None כשאין טבלה לקרוא."""
    inodes: set[str] = set()
    read_any = False
    for table in UDP_TABLES:
        try:
            rows = table.read_text().splitlines()[1:]
        except FileNotFoundError:        # אין /proc, או קרנל בלי IPv6
            continue
        except OSError as exc:
            # הטבלה קיימת ולא הצלחנו לקרוא אותה. דילוג עליה היה מחזיר
            # את התשובה החלקית של הטבלה השנייה — כלומר "פנוי" על סמך
            # חצי בדיקה (עיקרון 5).
            raise OSError(f"קריאת {table} נכשלה: {exc}") from exc
        read_any = True
        for row in rows:
            fields = row.split()
            if not fields:                   # שורה ריקה אינה שורת סוקט
                continue
            if len(fields) <= 9:
                # שורה קטועה היא שורה שלא בדקנו, בדיוק כמו שורה שלא
                # ידענו לפענח. "קצרה מכדי לעניין" הוא בדיוק הניחוש
                # שעיקרון 5 אוסר.
                raise OSError(f"שורה קטועה ב-{table}: {row!r}")
            try:
                local_port = int(fields[1].rsplit(":", 1)[-1], 16)
            except ValueError as exc:
                # שורה שאיננו יודעים לקרוא היא שורה שלא בדקנו, והיא
                # יכולה להיות בדיוק זו שמחזיקה את הפורט. "לא הבנו" אינו
                # "אין שם כלום" — ולכן חריגה, לא `continue` (עיקרון 5).
                raise OSError(f"שורה לא צפויה ב-{table}: {row!r}") from exc
            if local_port == port:
                inodes.add(fields[9])
    return inodes if read_any else None


def _pids_holding(inodes: set[str]) -> list[int]:
    """התהליכים שמחזיקים את ה-inodes האלה; ריק = לא הצלחנו לזהות אותם."""
    wanted = {f"socket:[{inode}]" for inode in inodes}
    pids: list[int] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            handles = list((entry / "fd").iterdir())
        except OSError:                  # תהליך של משתמש אחר, או שמת בינתיים
            continue
        for handle in handles:
            try:
                link = os.readlink(handle)
            except OSError:
                continue
            if link in wanted:
                pids.append(int(entry.name))
                break
    return sorted(pids)


def port_holders(port: int) -> list[int]:
    """מי מחזיק את `port`. רשימה ריקה = **נבדק ונמצא פנוי**.

    ‏`OSError` פירושו *לא הצלחנו לבדוק*, וזה אינו "פנוי" (עיקרון 5):
    מי שקורא חייב לטפל בשני המצבים בנפרד.

    שלוש תשובות ולא שתיים: פנוי, תפוס בידי PIDים ידועים, ותפוס בידי מי
    שלא הצלחנו לזהות (`UNKNOWN_HOLDER`) — סוקט של משתמש אחר, ש-`/proc`
    שלו סגור בפנינו. השלישית אינה "פנוי" ואינה שם של תהליך, והיא נאמרת
    ככזאת ולא מקופלת לאחת מהאחרות.

    **ובלי טבלת קרנל אין תשובה בכלל.** ‏bind מוצלח אינו תחליף: סוקט
    בדיקה חייב להיקשר לכתובת אחת, ולכן הוא מפספס מי שתפס את הפורט על
    כרטיס אחר — ו-bind לכל הכרטיסים הוא בדיוק מה שאסור לפתוח כאן. השרת
    רץ על דביאן, שם `/proc/net/udp` תמיד קיים; במקום שאין בו טבלה גם
    אין udpcast, ולכן "לא הצלחנו לבדוק" הוא התשובה הנכונה ולא מחיר.
    """
    inodes = _inodes_on_port(port)
    if inodes is None:
        raise OSError(
            f"אין טבלת UDP של הקרנל ({', '.join(str(t) for t in UDP_TABLES)}),"
            f" ואי אפשר לדעת מי מחזיק את פורט {port}"
        )
    if not inodes:
        return []
    return _pids_holding(inodes) or [UNKNOWN_HOLDER]


def holder_names(pids: list[int]) -> str:
    """"udp-sender (PID 1234)" — שם התהליך ולא רק מספרו.

    ‏PID לבדו אינו אומר למפעיל **מה לעשות**; השם אומר לו אם מולו סבב
    קודם שלא נסגר או כלי אחר לגמרי. ‏`UNKNOWN_HOLDER` אינו PID, ולכן
    אינו מקבל שם ואינו נספר כאן — מי שקורא אומר "לא זיהינו" בעצמו.
    """
    names = []
    for pid in pids:
        if pid == UNKNOWN_HOLDER:
            continue
        try:
            comm = Path(f"/proc/{pid}/comm").read_text().strip()
        except OSError:            # התהליך מת בינתיים, או /proc סגור בפנינו
            comm = ""
        names.append(f"{comm} (PID {pid})" if comm else f"PID {pid}")
    return ", ".join(names)


def sender_log_tail(log_path: Path, limit: int = 400) -> str:
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return ""
    return text[-limit:].strip()


def transfer_started_in_log(log_path: Path) -> bool | None:
    """True התחיל · False נקרא ואין · None לא ניתן לקרוא.

    כל הקובץ, לא הזנב: Progress דוחף את השורה מחוץ ל-400 תווים.
    קובץ חסר אינו "איש לא הצטרף" (#438, עיקרון 5).
    """
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    return TRANSFER_STARTED in text


#: ‏#989: שורת ההתקדמות ש-udp-sender כותב ל-stderr (= הלוג הפר-מחיצתי) לכל
#: היותר פעם ב-500ms. הצורה מקוד המקור של udpcast, לא ממדידה על ברזל
#: (‏`statistics.c` ‏`displaySenderStats`: ‏`"bytes=" printLongNum
#: " re-xmits=%07llu (%3u.%01u%%) slice=%04d ... - %3d\r"`). ‏`printLongNum`
#: (‏log.c) מדפיס שלוש קבוצות של שלוש ספרות מופרדות ברווח, ואחריהן סיומת:
#: רווח ביחידות בייט, ‏K (‏×1024) מ-10⁹ ומעלה, ‏M (‏×2²⁰) מעל 10¹².
#: ‏re-xmits = בלוקים ששודרו שוב כי מקבל דיווח שחסרו לו — אובדן ברשת או
#: במקבל שהפרוטוקול תיקן; לא בייט שאבד ליעד (עיקרון 4).
_PROGRESS = re.compile(r"bytes=([ \d]*)([KM]?) re-xmits=(\d+) \(")
_UNIT = {"": 1, "K": 1024, "M": 1_048_576}
#: כמה מסוף הלוג קוראים כדי למצוא את השורה האחרונה (‏~80 בייט לשורה).
PROGRESS_TAIL = 4096
#: ‏#989: בלי שורה חדשה זמן כזה — אין "קצב עכשיו" (udp-sender מדפיס כל 0.5ש').
PACE_STALE_S = 10.0
#: קצב אוטומטי (11/10): כל כמה זמן השולח דוגם את שורת ההתקדמות של
#: udp-sender בזמן שהוא ממתין לו — קריאה מקומית של לוג, בלי תעבורה. מזה
#: נגזר הקצב הממוצע של הזרם בגל (‏`wave_outcome`). ‏udp-sender כותב כל 0.5ש'.
PACE_POLL = 0.5


def sender_progress(log_path: Path) -> tuple[int, int] | None:
    """‏(בייטים שנשלחו, בלוקים ששודרו שוב) מהשורה השלמה האחרונה בלוג.

    ‏None = הקובץ לא נקרא, או שאין בו שורה בצורה שאנחנו מכירים — ‏"לא
    נמדד", לא אפס (עיקרון 5). שורה שנקטעה באמצע הכתיבה אינה תואמת
    (אין לה `(` אחרי המונה) ונופלת לשורה שלפניה.
    """
    try:
        with log_path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - PROGRESS_TAIL))
            text = handle.read().decode("ascii", errors="replace")
    except OSError:
        return None
    matches = _PROGRESS.findall(text)
    if not matches:
        return None
    digits, suffix, rexmits = matches[-1]
    return int(digits.replace(" ", "") or "0") * _UNIT[suffix], int(rexmits)


def wave_totals(logs: list[Path]) -> tuple[int, int] | None:
    """‏(בייטים, בלוקים ששודרו שוב) — סכום על כל המחיצות של גל.

    מחיצה שהלוג שלה נקרא ואין בו `Starting transfer` תורמת 0 (לא שודר
    כלום, אז לא שודר שוב); מחיצה שהשידור בה התחיל ואין בה שורה מוכרת,
    או לוג שלא נקרא — הסכום כולו None (עיקרון 5).
    """
    sent = loss = 0
    for path in logs:
        got = sender_progress(path)
        if got is not None:
            sent, loss = sent + got[0], loss + got[1]
            continue
        if transfer_started_in_log(path) is not False:
            return None
    return sent, loss


#: ‏#1761: שתי השורות ש-udp-sender כותב כשמקלט לא עונה (‏senddata.c:930-967).
#: כל פקיעה של המתנה ל-REQACK מדפיסה `Timeout notAnswered=...`; וכש-rxmitId
#: עובר את `--retries-until-drop` הוא מנתק את המקלט: `Dropping client #N
#: because of timeout`. ניתוק הוא האות הישיר שהקצב הפיל מקלט — לא rcvbuf.
SENDER_TIMEOUT = "Timeout notAnswered"
SENDER_DROP = "Dropping client"


def wave_events(logs: list[Path]) -> tuple[int, int] | None:
    """‏(שורות `Dropping client`, שורות `Timeout notAnswered`) — סכום על כל
    המחיצות של גל, מהקובץ כולו (השורות נדחקות מהזנב ע"י ההתקדמות).

    ‏None = לוג אחד לפחות לא נקרא: "לא נבדק", ולא "אפס ניתוקים" (עיקרון 5).
    """
    drops = timeouts = 0
    for path in logs:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            return None
        drops += text.count(SENDER_DROP)
        timeouts += text.count(SENDER_TIMEOUT)
    return drops, timeouts


#: ‏#1950: השורות שבהן udp-sender אומר מי מקבל ממנו. נמדד מול udpcast
#: 20120424 (testrunner, ‏22/10): ‏`New connection from 127.0.0.1  (#0) 00000009`,
#: ובסוף `Transfer complete.` ואחריה `Disconnecting #0 (127.0.0.1)`.
#: ‏`Dropping client #N because of timeout` — ‏senddata.c (ראו `SENDER_DROP`).
_RECEIVER_LINE = re.compile(
    r"New connection from (\S+)\s+\(#(\d+)\)"
    r"|Dropping client #(\d+)"
    r"|Disconnecting #(\d+)"
    r"|(Transfer complete)")


def finished_receivers(log_path: Path) -> set[str] | None:
    """‏#1950: הכתובות שקיבלו את המחיצה עד סופה — התחברו, ולא נזרקו ולא
    התנתקו לפני `Transfer complete`. זו ראיה חיובית שהמקלט חי **עכשיו**:
    הוא ענה ל-ACK האחרון של השולח לפני שניות.

    ‏None = הלוג לא נקרא — "לא נבדק", לא "אף אחד" (עיקרון 5). ‏udpcast
    ממחזר מספר `#N` אחרי ניתוק, ולכן השורות נקראות לפי הסדר."""
    try:
        text = log_path.read_text(errors="replace")
    except OSError:
        return None
    slots: dict[str, str] = {}
    for m in _RECEIVER_LINE.finditer(text):
        joined_ip, joined_slot, dropped, left, complete = m.groups()
        if complete:
            break
        if joined_ip:
            slots[joined_slot] = joined_ip
        else:
            slots.pop(dropped or left, None)
    return set(slots.values())


def _reaped(process, seconds: float) -> bool:
    """True רק כשיש ראיה שהילד מת. היעדר בדיקה אינו ראיה (#439)."""
    deadline = time.monotonic() + seconds
    while True:
        if process.poll() is not None:
            pid = getattr(process, "pid", None)
            root = Path("/proc")
            if pid is None or not root.is_dir():
                return True
            try:
                return not (root / str(pid)).is_dir()
            except OSError:
                return False
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


@dataclass
class SendState:
    session_id: str
    image_id: str
    total: int = 0
    index: int = 0                  # איזו מחיצה משודרת כרגע (1-based)
    file: str = ""
    state: str = "starting"         # starting / sending / done / failed / stopped
    error: str | None = None
    commands: list[list[str]] = field(default_factory=list)
    # קצב אוטומטי: בייטים ושניות שבהם המונה של udp-sender זז, על כל המחיצות.
    moved_bytes: int = 0
    moved_s: float = 0.0
    # ‏#1758: משך הזרם כולו — מהבייט הראשון של הגל (‏monotonic) ועד `send_done`.
    first_byte_at: float | None = None
    done_at: float | None = None


class SenderEngine:
    """מריץ שידור אחד בכל רגע — כמו שהאפיון מבטיח על סבבים."""

    def __init__(
        self,
        library,
        runner: Callable[[list[str]], subprocess.Popen] = run_process,
        portbase: int | None = None,
        interface: str | None = None,
        max_wait: int = DEFAULT_MAX_WAIT,
        start_timeout: float = DEFAULT_START_TIMEOUT,
        retries_until_drop: int | None = None,
        max_bitrate: str | None = None,
        on_event: Callable[[str, str], None] | None = None,
        simulate: bool = False,
        wave_bitrate: Callable[[dict], str | None] | None = None,
        live_receivers: Callable[[str], int] | None = None,
        link_speed: Callable[[str | None], dict] | None = None,
        pending_asks: Callable[[str], list[str]] | None = None,
        lost_receivers: Callable[[str], list[str | None]] | None = None,
        lost_wait: Callable[[], int] | None = None,
    ):
        self.library = library
        self.runner = runner
        # סימולציה (‏tools/e2e_simulation.py): אין udp-sender ואין ‎/proc/net/udp
        # בתחנת פיתוח, ומאז #854 כישלון השולח הוא כישלון הסבב — בלי הדגל
        # הסימולציה הייתה "עוברת" רק כל עוד הכישלון נבלע בשקט. עם הדגל
        # השולח אינו משדר ואינו בודק פורטים; הוא נאמר ביומן ואינו בייצור.
        self.simulate = simulate
        # ‏None ולא `DEFAULT_PORTBASE` כברירת מחדל בחתימה: ברירת מחדל
        # נקשרת בהגדרת הפונקציה, וחבילת הטסטים דורסת את המודול אחריה.
        self.portbase = DEFAULT_PORTBASE if portbase is None else portbase
        self.interface = interface
        self.max_wait = max_wait
        self.start_timeout = start_timeout
        # ‏None ולא הקבוע בחתימה: כך `IMAGESRV_RETRIES_UNTIL_DROP` נקרא
        # בזמן ההרצה (כמו max_bitrate), ומי שמעביר ערך מפורש עוקף אותו.
        self.retries_until_drop = (
            retries_until_drop if retries_until_drop is not None
            else _env_retries_until_drop()
        )
        # ‏#2056: ההמתנה למחשב שהתנתק (הגדרה בקונסולה, בשניות). ‏None = ללא הגדרה: התקרה כמות שהיא.
        self.lost_wait = lost_wait
        # ריסון קצב: מקבל חסום על כתיבה לדיסק גם שותק בפרוטוקול, וה-sender
        # זורק אותו ("Dropped by server"). ברשת 1G הכבל מרסן מעצמו; ברשת
        # מהירה (מעבדת VM, ‏10G) חייבים רסן מפורש בקצב שהדיסקים מעכלים (#24).
        self.max_bitrate = max_bitrate or os.environ.get("IMAGESRV_MAX_BITRATE")
        # ‏#447 + קצב אוטומטי (11/10): הקצב לגל הזה (`drive_rates.decide`).
        # מקבל את `max_bitrate` כגג ולעולם אינו מעליו (בלעדיו — 900m);
        # בלי `wave_bitrate` — `max_bitrate` כמות שהוא.
        self.wave_bitrate = wave_bitrate
        # ‏#1430: כמה חברים בגל עוד יכולים לקבל — נשאל לפני **כל** מחיצה.
        # חבר שדיווח `failed` לא יתחבר לעולם, ו-`--min-receivers` שסופר
        # אותו מבזבז את כל `--max-wait` (600ש' במחיצה 2+) על מכונה מתה —
        # עד שהמכונה התקינה פוקעת. בלעדיו — הצילום של `joined` מתחילת הגל.
        self.live_receivers = live_receivers
        # ‏#1950: הכתובות של חברי הגל שהשרת סימן `lost` (‏None = כתובת לא
        # ידועה). ‏`lost` הוא הסקה של השרת ("לא דיווח"), לא דיווח של המכונה:
        # מכונה שקיבלה את המחיצה הקודמת עד סופה עדיין מתחברת לשולח, ובלי
        # לספור אותה היא "ממלאת" את `--min-receivers` והמחיצה יוצאת בלי
        # החבר החי. ‏None = לא מחובר: ההתנהגות של #1430.
        self.lost_receivers = lost_receivers
        # הכרעת נדב 15/10: מהירות הקישור של `interface` (‏`drive_rates.read_link`)
        # — הגג והפתיחה של הקצב לפיה. ‏None = לא מחובר (בדיקות; ‏`server.main`
        # מחבר תמיד): "לא נקרא", וההתנהגות של היום — לא ניחוש של 1GbE.
        self.link_speed = link_speed
        # ברזל 17/10: מי בגל ממתין עכשיו לאדם (שאלת SMART פתוחה). כל עוד יש
        # כזה, ‏udp-sender של המחיצה הראשונה לא מופעל — ה-`--start-timeout`
        # שלו נספר מההפעלה, ושלוש דקות מול חלון SMART פתוח נגמרו בשולח שיצא
        # עשר שניות אחרי התשובה. ‏None = לא מחובר: ההתנהגות של לפני.
        self.pending_asks = pending_asks
        self.on_event = on_event or (lambda event, detail: None)
        #: ‏#705: ‏`gate(interface)` → סיבת סירוב או None — השידור כבוי בכרטיס
        #: (רשת ← חיבורים פיזיים ← שירותים). ‏`create_runtime` מתקין.
        self.gate: Callable[[str | None], str | None] | None = None
        self._lock = threading.Lock()
        self._state: SendState | None = None
        self._process: subprocess.Popen | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        # ‏#989: הדגימה הקודמת של הלוג הנוכחי — (נתיב, mtime, בייטים) —
        # והקצב האחרון שנגזר ממנה. נדגם רק כשמישהו קורא את `/room`.
        self._pace_sample: tuple[Path, float, int] | None = None
        self._pace_rate: int | None = None
        self._pace_lock = threading.Lock()

    # --- API ------------------------------------------------------------------

    def start(self, session: dict) -> None:
        """נקרא כשסבב עובר ל-running. חוזר מיד; השידור רץ ברקע."""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                log.warning("sender already running; ignoring start of %s", session["id"])
                return
            self._stop.clear()
            self._state = SendState(
                session_id=session["id"], image_id=session["image_id"]
            )
            self._thread = threading.Thread(
                target=self._run, args=(session,), daemon=True,
                name=f"sender-{session['id']}",
            )
            self._thread.start()

    def stop(self, session_id: str | None = None) -> int | None:
        """הורג את udp-sender וקורא בחזרה שהוא מת (#439).

        ‏None = יש ראיה שהתהליך איננו. מספר = עדיין חי, ואינו הצלחה
        — גם כשלא הצלחנו לבדוק. TERM לבד משאיר יתום: udpcast חוסם
        אותו ב-doTransfer. SIGKILL ואז קריאה בחזרה.

        ‏`session_id` מגן מפני קריאת-סגירה ישנה (#773): thread שסוגר
        את session A ומתעכב לפני ה-callback אינו רשאי להרוג את השידור
        של session B שכבר התחיל. הסגירה פועלת רק כש-`session_id` הוא
        `None` (סגירה מפורשת) או תואם את ה-session הרץ; אחרת no-op.
        """
        with self._lock:
            # השוואה תחת אותה נעילה שמגינה על `_state`: בין קריאת המצב
            # להדלקת ה-stop אסור ש-`start` של session אחר יחליף אותו.
            current = self._state.session_id if self._state is not None else None
            if session_id is not None and session_id != current:
                log.info("sender: ignoring stale stop of %s (current is %s)",
                         session_id, current)
                return None
            self._stop.set()
            process = self._process
            if self._state is not None and self._state.state in ("starting", "sending"):
                self._state.state = "stopped"
        if process is None:
            return None
        if process.poll() is not None:
            # poll() reports that the child exited, but wait() is the explicit
            # ownership boundary: every Popen we create is reaped here or by
            # the worker, including an exit racing with cancellation/shutdown.
            process.wait()
            with self._lock:
                if self._process is process:
                    self._process = None
            return None
        process.terminate()
        if _reaped(process, STOP_TERM_WAIT):
            process.wait()
            return None
        try:
            process.kill()
        except OSError:
            pass
        if _reaped(process, STOP_KILL_WAIT):
            process.wait()
            return None
        pid = getattr(process, "pid", None)
        log.error("sender: udp-sender still alive after SIGKILL (PID %s)", pid)
        return pid if pid is not None else -1

    def close(self) -> int | None:
        """Stop the current child and wait for the sender worker on shutdown."""
        left = self.stop()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(STOP_TERM_WAIT + STOP_KILL_WAIT + 1.0)
        return left

    def status(self) -> dict | None:
        with self._lock:
            if self._state is None:
                return None
            s = self._state
            return {
                "session_id": s.session_id, "image_id": s.image_id,
                "partition": s.index, "partitions": s.total,
                "file": s.file, "state": s.state, "error": s.error,
            }

    def stream_stats(self, session_id: str) -> dict:
        """‏#989: ‏`throughput_bps` ו-`loss_blocks` של הגל `session_id`.

        שניהם נקראים מהלוגים הפר-מחיצתיים ש-udp-sender כבר כותב — שום
        תעבורה חדשה, ושום דגימה ברקע: הדגימה היא הקריאה של `/room` עצמה.

        * ‏`throughput_bps` — בייטים לשנייה בין שתי השורות האחרונות שנדגמו
          מהמחיצה המשודרת, על פני ה-mtime שלהן (הרגע שבו udp-sender כתב
          אותן, לא הרגע שבו קראנו). ‏None: דגימה ראשונה, מחיצה חדשה, מונה
          שירד, או לוג שלא נכתב `PACE_STALE_S` שניות.
        * ‏`loss_blocks` — סכום ה-re-xmits על כל המחיצות שהגל הזה הריץ.
          מחיצה שהלוג שלה נקרא ואין בו `Starting transfer` תורמת 0 (לא
          שודר כלום, אז לא שודר שוב); מחיצה שהשידור בה התחיל ואין בה
          שורה מוכרת, או לוג שלא נקרא — הסכום כולו None.

        גל שהמנוע אינו משדר (מקור מחשב בנייה, סימולציה, גל אחר) — None.
        """
        logs, sending = self._wave_logs(session_id)
        if not logs:
            return {"throughput_bps": None, "loss_blocks": None}
        totals = wave_totals(logs)
        return {"throughput_bps": self._throughput(logs[-1]) if sending else None,
                "loss_blocks": totals[1] if totals is not None else None}

    def wave_outcome(self, session_id: str) -> dict:
        """מה שהקצב האוטומטי לומד מהגל בסופו (‏`drive_rates.record_stream`).

        * ‏`sent_bytes`, ‏`loss_blocks` — אותם כללים כמו `loss_blocks` של
          `stream_stats`; ‏None בשניהם = "לא נמדד".
        * ‏`stream_bps`, ‏`stream_s` — הקצב הממוצע של הזרם עצמו: הבייטים
          שהמונה של udp-sender זז חלקי השניות שבהן זז, על כל המחיצות
          (‏`_wait_sampling`). זה הקצב **בקו** — לפני פריסת הדחיסה, ולכן
          בלי ההטיה של `bytes_written` בכוננים. ‏None = לא זז בין שתי דגימות.
        * ‏`wall_s` (‏#1758) — משך הזרם של הגל כולו בשעון: מהדגימה הראשונה
          שבה המונה זז (הבייט הראשון של מחיצה 1) ועד `send_done`. כולל את
          הזנב של השידורים החוזרים, את ה-ACK האחרון ואת המעברים בין
          המחיצות — כל מה שהקצב יכול להאריך — ובלי ההמתנה למקבלים ולשאלות
          SMART לפני הבייט הראשון (אדם, לא קצב). ‏None — הגל לא הגיע ל-
          `send_done` (נכשל / נעצר) או שהמונה לא זז.
        * ‏`dropped_clients`, ‏`timeouts` (‏#1761) — כמה שורות `Dropping client`
          ו-`Timeout notAnswered` כתב udp-sender בכל המחיצות (`wave_events`).
          ‏None בשניהם = לוג שלא נקרא — "לא נבדק", לא "נקי".
        """
        none = {"sent_bytes": None, "loss_blocks": None,
                "stream_bps": None, "stream_s": None, "wall_s": None,
                "dropped_clients": None, "timeouts": None}
        logs, _ = self._wave_logs(session_id)
        if not logs:
            return none
        totals = wave_totals(logs)
        events = wave_events(logs)
        with self._lock:
            moved_bytes, moved_s = self._state.moved_bytes, self._state.moved_s
            began, ended = self._state.first_byte_at, self._state.done_at
        wall = ended - began if began is not None and ended is not None else None
        return {"sent_bytes": totals[0] if totals is not None else None,
                "loss_blocks": totals[1] if totals is not None else None,
                "stream_bps": int(moved_bytes / moved_s) if moved_s > 0 else None,
                "stream_s": round(moved_s, 1) if moved_s > 0 else None,
                "wall_s": round(wall, 1) if wall is not None and wall > 0 else None,
                "dropped_clients": events[0] if events is not None else None,
                "timeouts": events[1] if events is not None else None}

    def _wait_sampling(self, process, log_path: Path, asks_of: str | None = None) -> int | None:
        """ממתין ל-udp-sender, ובינתיים דוגם את המונה שלו כל `PACE_POLL`.

        נספר רק הזמן שבו המונה **זז**: מהדגימה הראשונה מעל אפס (ההמתנה
        למקבלים לפני הבייט הראשון אינה זרם) ועד הדגימה האחרונה שבה גדל
        (ההמתנה ל-ACK האחרון אחרי הבייט האחרון גם אינה). דגימה אחרונה גם
        אחרי היציאה — השורה הסופית. מחיצה שזזה בדגימה אחת בלבד — לא תורמת.

        ‏`asks_of` (ברזל rc2, 17/10): מזהה הגל — המחיצה הראשונה. שאלת SMART
        שהגיעה **אחרי** ש-udp-sender עלה (באותה שנייה כמו `send_start`) לא
        נתפסה בהמתנה שלפניו, ו-`--start-timeout` רץ מתחתיה עד הכשל. כל עוד
        יש ראיה חיובית שהשידור **לא** התחיל (הלוג נקרא ואין Starting
        transfer), שאלה פתוחה הורגת את udp-sender שעוד לא שידר בייט ומחזירה
        None — הקורא חוזר להמתנה ומפעיל אותו מחדש עם שעון מלא."""
        first = last = None
        next_ask = time.monotonic()
        while True:
            if asks_of is not None and time.monotonic() >= next_ask:
                next_ask = time.monotonic() + ASK_HOLD_POLL_S
                if (transfer_started_in_log(log_path) is False
                        and self._asks_waiting(asks_of) and process.poll() is None):
                    log.info("sender: a SMART question arrived before the first byte -- "
                             "rewinding partition 1 of %s", asks_of)
                    self._reap(process)
                    return None
            done = process.poll() is not None
            got = sender_progress(log_path)
            if got is not None and got[0] > 0 and (last is None or got[0] > last[1]):
                last = (time.monotonic(), got[0])
                if first is None:
                    first = last
                    with self._lock:      # ‏#1758: תחילת הזרם של הגל כולו
                        if self._state.first_byte_at is None:
                            self._state.first_byte_at = first[0]
            if done:
                break
            time.sleep(PACE_POLL)
        code = process.wait()
        if first is not None and last[0] > first[0]:
            with self._lock:
                self._state.moved_bytes += last[1] - first[1]
                self._state.moved_s += last[0] - first[0]
        return code

    def _wave_logs(self, session_id: str) -> tuple[list[Path], bool]:
        """הלוגים הפר-מחיצתיים של הגל `session_id`, והאם הוא עדיין משדר.
        רשימה ריקה: גל שהמנוע אינו משדר (מחשב בנייה, סימולציה, גל אחר)."""
        with self._lock:
            s = self._state
            if (self.simulate or s is None or s.session_id != session_id
                    or not s.commands):
                return [], False
            return ([partition_log(c[c.index("--file") + 1]) for c in s.commands],
                    s.state == "sending")

    def _throughput(self, path: Path) -> int | None:
        with self._pace_lock:        # שני דפדפנים פתוחים = שתי קריאות במקביל
            return self._throughput_locked(path)

    def _throughput_locked(self, path: Path) -> int | None:
        got = sender_progress(path)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            got = None
        if got is None:
            self._pace_sample, self._pace_rate = None, None
            return None
        prev = self._pace_sample
        if prev is None or prev[0] != path or got[0] < prev[2]:
            self._pace_sample, self._pace_rate = (path, mtime, got[0]), None
        elif mtime > prev[1]:
            self._pace_rate = int((got[0] - prev[2]) / (mtime - prev[1]))
            self._pace_sample = (path, mtime, got[0])
        if time.time() - mtime > PACE_STALE_S:
            return None
        return self._pace_rate

    # --- הלולאה ---------------------------------------------------------------

    @property
    def max_wait_later(self) -> int:
        """‏`--max-wait` למחיצות 2+ (#957): ההמתנה שם היא לכתיבה של הקודמת."""
        return later_max_wait(self.max_wait)

    @property
    def start_timeout_later(self) -> int:
        """‏`--start-timeout` למחיצות 2+ — חייב להכיל את `max_wait_later`."""
        return later_start_timeout(self.max_wait, self.start_timeout)

    def effective_retries_until_drop(self) -> int:
        """‏#2056: התקרה שמועברת ל-udp-sender -- לא פחות מההמתנה שהוגדרה לאובדן, כדי שהשולח
        לא יזרוק מקבל שהשרת עוד מחכה לו. ההמרה: 200 בקשות ≈ 190ש' (ראו למעלה)."""
        if self.lost_wait is None:
            return self.retries_until_drop
        from server.agent import agent_timing   # noqa: PLC0415 -- בלי מעגל ייבוא בעלייה
        return agent_timing.retries_until_drop(self.retries_until_drop, self.lost_wait())

    def command_for(self, path: Path, receivers: int, number: int = 1,
                    max_bitrate: str | None | bool = False) -> list[str]:
        # ‏number = מספר המחיצה בסדר השידור (1 = הראשונה). מהשנייה והלאה
        # ההמתנה ארוכה יותר — ראו LATER_MAX_WAIT. ‏`max_bitrate` — התקרה
        # שנבחרה לגל (#447); ‏False = לא נבחרה, ואז התקרה הקבועה.
        if max_bitrate is False:
            max_bitrate = self.max_bitrate
        later = number > 1
        cmd = [
            "udp-sender",
            "--portbase", str(self.portbase),
            "--min-receivers", str(max(1, receivers)),
            "--max-wait", str(self.max_wait_later if later else self.max_wait),
            # בלי זה udpcast ממתין למקבל הראשון בלי גבול (#341).
            "--start-timeout",
            str(self.start_timeout_later if later else self.start_timeout),
            # מקבל שמת אינו שולח CMD_DISCONNECT — הוא פשוט מפסיק לענות,
            # וכל שאר החדר ממתין לו. זו התקרה על ההמתנה הזאת (#437).
            "--retries-until-drop", str(self.effective_retries_until_drop()),
            "--rexmit-hello-interval", str(HELLO_INTERVAL_MS),
            "--nokbd",
            "--file", str(path),
        ]
        if self.interface:
            cmd[1:1] = ["--interface", self.interface]
        if max_bitrate:
            cmd[1:1] = ["--max-bitrate", max_bitrate]
        return cmd

    def link(self) -> dict:
        """כרטיס ההפצה ומהירותו (‏`drive_rates.read_link`), או למה לא נקראה."""
        if self.link_speed is None:
            return {"interface": self.interface, "speed_mbps": None, "why": "not_wired"}
        return self.link_speed(self.interface)

    def multicast_params(self) -> dict:
        """הפרמטרים שמחשב בנייה משדר בהם כשהוא המקור (‏#715, ממשק 3).

        אותם ערכים בדיוק שהמנוע הזה היה נותן ל-`udp-sender` — כדי שזרם
        מדיסק חי יתנהג כמו זרם מהספרייה (‏#437, ‏#438). ‏`min_receivers`
        אינו כאן: הוא נגזר מהגל ברגע התשובה (‏`direct.task_block`).
        """
        return {
            "portbase": self.portbase,
            "max_wait": self.max_wait,
            "start_timeout": int(self.start_timeout),
            # למחיצות 2+ (#957) — מחשב הבנייה בוחר לפי מספר המחיצה, כמונו.
            "max_wait_later": self.max_wait_later,
            "start_timeout_later": self.start_timeout_later,
            "retries_until_drop": self.effective_retries_until_drop(),
            "max_bitrate": self.max_bitrate or None,
        }

    def _run_simulated(self, session: dict) -> None:
        """שולח מדומה: "משדר" עד שהסבב נסגר. אף פורט לא נבדק, אף תהליך לא רץ."""
        with self._lock:
            self._state.total = 1
            self._state.index = 1
            self._state.state = "sending"
        self.on_event("send_simulated", f'{session["id"]} {session["image_id"]}')
        log.warning("sender: simulated -- no udp-sender, no port probe (%s)", session["id"])
        self._stop.wait()
        self.on_event("send_stopped", session["id"])

    def _fail(self, message: str) -> None:
        with self._lock:
            if self._state is not None:
                self._state.state = "failed"
                self._state.error = message
        log.error("sender: %s", message)
        self.on_event("send_failed", message)

    def _ports_are_free(self) -> bool:
        """שני הפורטים של udpcast פנויים — בראיה חיובית, לפני השידור.

        יתום שמחזיק את `portbase+1` מפיל את ההפצה הבאה, ו-udp-sender
        נכשל בהודעה אטומה שנראית כמו תקלת רשת (#156). כאן זה הופך
        לשורה אחת שנוקבת בפורט וב-PID. ובדיקה שלא הצליחה לרוץ אינה
        "הפורט פנוי" — היא כישלון בפני עצמה (עיקרון 5).

        **מה ש-udp-sender עושה בפועל (#342):** נמדד ב-`ss -ulnp`
        במעבדה — הוא נקשר רק ל-`portbase+1`, לא ל-`portbase`. מי
        שתופס את `portbase` כדי לשחזר התנגשות (למשל בעבודה על #202)
        לא יראה התנגשות בכלל; זה קרה בניסיון הראשון. **הבדיקה על
        שני הפורטים נשארת** — היא מגנה מפני שינוי עתידי ב-udpcast.
        לשחזור: לתפוס את `portbase+1`.

        **מה שהבדיקה אינה:** היא צילום רגע. הסוקט של הבדיקה נסגר לפני
        ש-`udp-sender` נקשר, ובחלון שביניהם תהליך אחר יכול לתפוס את
        הפורט — ולא ניתן לסגור את החלון בהחזקת סוקט, כי `udp-sender`
        צריך את הפורט לעצמו. היא מקטינה סיכון ומסבירה כישלון; היא אינה
        ערובה. וכשלא הצלחנו לזהות **מי** מחזיק — זה נאמר כך, ולא
        מוצג כשם של תהליך.
        """
        for port in (self.portbase, self.portbase + 1):
            try:
                holders = port_holders(port)
            except Exception as exc:              # noqa: BLE001 — ראו למטה
                # לא רק `OSError`: שורה לא צפויה ב-`/proc` היא `ValueError`,
                # ומניית `/proc` יכולה להפתיע אחרת. חריגה שתברח מכאן
                # תמות בתהליכון הרקע — והסבב יישאר תקוע ב-"starting",
                # כלומר "לא הצלחנו לבדוק" ייראה על המסך כמו "עדיין עובד".
                self._fail(f"לא הצלחנו לבדוק אם פורט {port} פנוי, ולכן"
                           f" לא משדרים: {exc}")
                return False
            if holders:
                self._fail(f"פורט {port} תפוס על ידי "
                           f"{holder_names(holders) or 'תהליך שאין הרשאה לזהות'}"
                           " — השידור לא יצא לדרך")
                return False
        return True

    def _port_verdict(self) -> str:
        """מי מחזיק את הפורטים **אחרי** ש-udp-sender נכשל (#202).

        הבדיקה המקדימה היא צילום רגע, והחלון שבינה לבין הקשירה של
        udp-sender אינו ניתן לסגירה — הוא צריך את הפורט לעצמו. מי
        שנכנס בחלון נשאר בלי אבחון, ודווקא הנדיר הוא מה שקורה מול
        כיתה. זו **הודעה בלבד**: לא ריפוי ולא ניסיון חוזר.

        שלוש תשובות ולא שתיים, ואף אחת אינה מקופלת לאחרת: נמצא מחזיק ·
        נבדק ונמצא פנוי, ולכן הסיבה אחרת ואינה ידועה · הבדיקה עצמה לא
        רצה (עיקרון 5). ‏udp-sender נכשל גם מסיבות אחרות, ולכן אסור
        להמציא מחזיק רק כדי שיהיה מה לכתוב.
        """
        busy, unchecked = [], []
        for port in (self.portbase, self.portbase + 1):
            try:
                holders = port_holders(port)
            except Exception as exc:          # noqa: BLE001 — כמו בבדיקה המקדימה
                unchecked.append(f"פורט {port}: {exc}")
                continue
            if holders:
                busy.append(f"פורט {port} תפוס על ידי "
                            f"{holder_names(holders) or 'תהליך שאין הרשאה לזהות'}")
        if busy:
            # מה שלא נבדק נאמר גם כאן: מחזיק שנמצא על פורט אחד אינו
            # תשובה על הפורט שאותו לא הצלחנו לבדוק.
            note = f" (ובנוסף: {'; '.join(unchecked)})" if unchecked else ""
            return "; ".join(busy) + note + " — יש לעצור אותו ולשדר שוב"
        if unchecked:
            return ("לא הצלחנו לבדוק מי מחזיק את הפורטים כעת ("
                    + "; ".join(unchecked) + ")")
        return ("הפורטים נבדקו כעת ושניהם פנויים — הכישלון אינו התנגשות"
                " על פורט, והסיבה אינה ידועה")

    def _asks_waiting(self, session_id: str) -> list[str]:
        """מי בגל ממתין עכשיו לאדם. קריאה שנכשלה = אין (נרשם), לא "יש" לנצח."""
        if self.pending_asks is None:
            return []
        try:
            return list(self.pending_asks(session_id))
        except Exception as exc:      # noqa: BLE001 — חריגה כאן הורגת את התהליכון
            log.warning("sender: open SMART questions of %s unreadable (%s);"
                        " sending without waiting", session_id, exc)
            return []

    @staticmethod
    def _reap(process) -> None:
        """udp-sender שעוד לא שידר: TERM, ואם לא מת — KILL, ותמיד wait."""
        process.terminate()
        if not _reaped(process, STOP_TERM_WAIT):
            try:
                process.kill()
            except OSError:
                pass
            if not _reaped(process, STOP_KILL_WAIT):
                # לא ממתינים לנצח על תהליך שלא מת: ההפעלה הבאה תיכשל על
                # הפורט התפוס, בשמה (`_port_verdict`), ולא תיתקע כאן.
                log.error("sender: udp-sender still alive after SIGKILL (PID %s)",
                          getattr(process, "pid", None))
                return
        process.wait()

    def _hold_for_asks(self, session_id: str) -> bool:
        """ממתין כל עוד מחשב שיכפול בגל עומד על שאלת SMART (ברזל 17/10).

        רק לפני המחיצה הראשונה: השאלה נשאלת לפני שהמכונה כותבת בייט, ומי
        שכבר קיבל את המחיצה הראשונה עבר אותה. ההמתנה נגמרת כשאין שאלה
        פתוחה של מכונה חיה (‏`open_in_wave` משחרר מכונה שנעלמה), או בעצירה
        — ‏False. קריאה שנכשלה אינה "יש שאלה" לנצח: יומן, ומשדרים כמו לפני.
        """
        if self.pending_asks is None:
            return True
        held_since = None
        while not self._stop.is_set():
            waiting = self._asks_waiting(session_id)
            if not waiting:
                if held_since is not None:
                    self.on_event("send_hold_released",
                                  f"{session_id} {int(time.monotonic() - held_since)}s")
                return True
            if held_since is None:
                held_since = time.monotonic()
                self.on_event("send_held_smart", f"{session_id} {' '.join(waiting)}")
            self._stop.wait(ASK_HOLD_POLL_S)
        return False

    def _receivers(self, session_id: str, joined: int,
                   previous_log: Path | None = None) -> int:
        """‏`--min-receivers` למחיצה שעומדת לצאת: החברים החיים, לא פחות מ-1.

        בדיקה שנכשלה אינה "אף אחד לא חי" (עיקרון 5): נשארים על הצילום
        של `joined` — ההתנהגות שלפני #1430, שממתינה יותר אך אינה מפילה
        מקבל חי — ואומרים את זה ביומן.

        ‏#1950: ועוד חבר `lost` שקיבל את המחיצה הקודמת (`previous_log`) עד
        סופה — הוא יתחבר גם לזו. בלי לספור אותו, הוא הגיע ראשון ל-p4 והשולח
        יצא לדרך בלי משכפל 2, שהצטרף 4 שניות אחריו וקיבל זרם ריק (R2,
        ‏22/10). חבר שנזרק או שלא התחבר — אינו נספר, כמו ב-#1430. לוג שלא
        נקרא או כתובת לא ידועה — נספר: המחיר הוא המתנה של `--max-wait`, לא
        מגירה חיה שנכשלת."""
        if self.live_receivers is None:
            return joined
        try:
            live = int(self.live_receivers(session_id))
            lost = (list(self.lost_receivers(session_id))
                    if self.lost_receivers is not None and previous_log is not None else [])
        except Exception as exc:          # noqa: BLE001 — חריגה כאן הורגת את התהליכון
            log.warning("sender: live receivers of %s unreadable (%s);"
                        " keeping the wave's %s", session_id, exc, joined)
            return joined
        if not lost:
            return max(1, live)
        finished = finished_receivers(previous_log)
        still = [ip for ip in lost if finished is None or ip is None or ip in finished]
        if still:
            self.on_event("send_counts_lost",
                          f"{session_id} {' '.join(ip or '?' for ip in still)}")
        return max(1, live + len(still))

    def _run(self, session: dict) -> None:
        if self.gate is not None:
            refused = self.gate(self.interface)
            if refused:
                self._fail(refused)
                return
        if self.simulate:
            self._run_simulated(session)
            return
        if not self._ports_are_free():
            return
        manifest = self.library.get(session["image_id"])
        if manifest is None:
            self._fail(f"אימג' {session['image_id']} לא נמצא בספרייה")
            return
        partitions = streamed_partitions(manifest)
        joined = max(1, int(session.get("joined") or 1))
        bitrate = (self.wave_bitrate(session) if self.wave_bitrate is not None
                   else self.max_bitrate)
        with self._lock:
            self._state.total = len(partitions)
        self.on_event(
            "send_start",
            f'{session["id"]} {session["image_id"]} partitions={len(partitions)}',
        )

        previous_log: Path | None = None        # ‏#1950: מי קיבל את הקודמת עד סופה
        for number, part in enumerate(partitions, start=1):
            if self._stop.is_set():
                self.on_event("send_stopped", session["id"])
                return
            path = self.library.file_path(session["image_id"], part["file"])
            if path is None:
                # ‏#1928: המפעיל צריך לדעת שהאימג' עצמו חסר — לא לנסות שוב ושוב
                self._fail(f"קובץ מחיצה חסר באימג' {session['image_id']}: {part['file']}"
                           " — האימג' חסר או פגום בשרת")
                return
            code = None
            while code is None:      # ברזל rc2: מחיצה 1 חוזרת להמתנה על שאלה מאוחרת
                if number == 1 and not self._hold_for_asks(session["id"]):
                    self.on_event("send_stopped", session["id"])
                    return
                with self._lock:
                    self._state.index = number
                    self._state.file = part["file"]
                    self._state.state = "sending"
                cmd = self.command_for(
                    path, self._receivers(session["id"], joined, previous_log),
                    number, bitrate)
                log_path = partition_log(path)
                log.info("sending partition %s/%s: %s", number, len(partitions), part["file"])
                try:
                    process = self.runner(cmd)
                except OSError as exc:
                    self._fail(f"udp-sender לא רץ: {exc}")
                    return
                with self._lock:
                    self._process = process
                    self._state.commands.append(cmd)
                code = self._wait_sampling(process, log_path,
                                           session["id"] if number == 1 else None)
                with self._lock:
                    self._process = None
                if self._stop.is_set():
                    break
            if self._stop.is_set():
                self.on_event("send_stopped", session["id"])
                return
            if code != 0:
                _tail = sender_log_tail(log_path)
                self._fail(f"udp-sender נכשל על {part['file']} (קוד {code})"
                           + (f": {_tail}" if _tail else "")
                           + f" · {self._port_verdict()}")
                return
            # קוד 0 אינו "השידור הצליח": udp-sender בנתיב start-timeout
            # יוצא 0 בלי Starting transfer ובלי מקבל אחד (#438). הראיה
            # החיובית היא השורה בלוג. בלי קובץ אין ראיה — וזה לא הצלחה.
            started = transfer_started_in_log(log_path)
            if started is None:
                self._fail(
                    f"לא הצלחנו לקרוא את יומן השידור אחרי {part['file']},"
                    " ולכן אין לדעת אם מישהו הצטרף — השידור לא נחשב תקין"
                )
                return
            if not started:
                self._fail(
                    f"אף מחשב לא הצטרף לשידור תוך {self.start_timeout}"
                    f" שניות — udp-sender סיים בלי לשדר את {part['file']}"
                    " (הלוג בלי Starting transfer). זה אינו אימג' פגום:"
                    " יש לבדוק כבל רשת, אתחול PXE ורישום MAC"
                )
                return
            previous_log = log_path

        with self._lock:
            self._state.state = "done"
            self._state.done_at = time.monotonic()
        self.on_event("send_done", session["id"])
