"""מה שהריצה משאירה אחריה חי.

חבילת הטסטים הפעילה שולח **אמיתי** — על אותו portbase שהשרת מקצה
להפצה — והוא נמצא רץ שעתיים וחצי אחרי שהריצה הסתיימה, משדר קובץ
שכבר נמחק (#79). תקרת המתנה שלא נבדקה היא הבטחה ולא ראיה. תהליך כזה
על פורט ההפצה מפיל שידור אמיתי, והאבחון נראה כמו תקלת רשת. השולח אז
היה של המנוע הישן; מאז #3173 השולח היחיד הוא `chunkcast send` של מנוע
החתיכות, ושלוש השכבות שומרות עליו.

שלוש שכבות, וכולן על ראיה חיובית:

1. **חסימה** — בתוך הטסטים מנוע השידור לא *יכול* להפעיל את `chunkcast`
   המותקן. `block_real_processes` מחליף את `subprocess` בתוך
   `server.rounds.chunklog` בלבד (שם `run_process`, ה-runner של הייצור).
   כל ניסיון נרשם ב-`blocked_spawns`, והטסט שגרם לו נכשל בשמו — כי טסט
   שמגיע לשולח האמיתי אינו בודק את מה שהוא חושב שהוא בודק.
2. **portbase שאינו יכול להתנגש** — `assign_test_portbase` מקצה לריצה
   portbase גבוה ואקראי. שתי השכבות האחרות מניחות שמישהו מנקה, ושתיהן
   נשברות באותה נקודה: ריצה **שנקטעה** לא מריצה ניקוי, ויתום שלה אינו
   שייך לאף `basetemp` חי — ולכן אף ריצה עתידית לא תיגע בו לעולם.
   #156: יתום כזה שרד יום וחמש שעות והחזיק את פורטי ההפצה. יתום שאינו
   יכול להתנגש הוא יתום לא מזיק, וזו ההגנה היחידה שאינה תלויה באיש.
3. **סריקה בסוף הריצה** — קוראים ב-`/proc` מי חי ומזכיר את תיקיית
   ה-tmp של הריצה הזאת (ורק אותה — לא הורגים ריצה של מישהו אחר),
   הורגים, וקוראים שוב כדי לראות שבאמת מתו. "שלחנו SIGTERM" אינו
   "התהליך מת".
"""

from __future__ import annotations

import os
import random
import shutil
import signal
import subprocess
import threading
import time
from pathlib import Path

import mountleak

#: הכלים שמזרימים בייטים ברשת. אלה שיכולים לשרוד את הריצה ולהפריע
#: לשידור אמיתי; שאר תת-התהליכים בטסטים (gcc, sh) מסיימים מעצמם.
#: `chunkcast` הוא השולח וגם המקלט של מנוע החתיכות (`send` / `recv`).
STREAM_TOOLS = ("chunkcast",)

#: כל ניסיון להפעיל תהליך אמיתי ממנוע השידור, לפי הסדר.
blocked_spawns: list[list[str]] = []


class _NoRealProcess:
    """מחליף את המודול `subprocess` — בתוך `server.rounds.chunklog` בלבד.

    `chunkcast` **בשמו החשוף** — מה שהמנוע מריץ, ומה ש-PATH מפרש לבינארי
    המותקן — מסורב ונרשם. שני דברים רצים כרגיל, כי אינם השולח המותקן:
    פקודה אחרת (ילד-דמה שטסט מפעיל בנתיב מלא כדי לבדוק את `run_process`
    עצמו), ו-`chunkcast` ש-PATH מפרש לקובץ **תחת ה-basetemp של הריצה** — תחליף
    שטסט שם שם בכוונה כדי לבדוק את ה-runner של הייצור (#3340, #3125).
    בינארי מותקן אינו יושב ב-tmp; ו"אין `chunkcast` בכלל" מסורב ונרשם גם
    הוא — הטסט התכוון להריץ שולח, ולא הזריק אחד.

    לא `monkeypatch`: תהליכון השידור ממשיך לרוץ גם אחרי שהטסט נגמר,
    וכל החזרה של המקור בסוף טסט פותחת בדיוק את החלון שבו הוא מספיק
    להפעיל שולח אמיתי. החסימה מותקנת פעם אחת לריצה ואינה מוחזרת.
    """

    STDOUT = subprocess.STDOUT

    @staticmethod
    def Popen(cmd, *args, **kwargs):          # noqa: N802 — חתימה של subprocess
        argv = [str(part) for part in cmd]
        named = [part for part in argv if part in STREAM_TOOLS]
        if named and not _stand_in(named[0], kwargs.get("env")):
            blocked_spawns.append(argv)
            raise OSError(
                "הטסטים חוסמים הפעלת chunkcast אמיתי (#79) — הזרק `sender_runner`"
            )
        return subprocess.Popen(cmd, *args, **kwargs)


#: ה-basetemp של הריצה (`sender_logs_under`): רק קובץ תחתיו הוא תחליף של טסט. None = עוד לא נקבע, ואין תחליפים.
_run_tmp: Path | None = None


def _stand_in(tool: str, env: dict | None) -> bool:
    """`tool` על ה-PATH **של הילד** הוא קובץ תחת ה-basetemp של הריצה: תחליף של טסט, לא הבינארי המותקן
    (ולא `chunkcast` שמישהו בנה במקום אחר ב-tmp). ילד שקיבל סביבה בלי PATH מחפש ב-`os.defpath`, לא ב-PATH שלנו."""
    if _run_tmp is None:
        return False
    path = os.environ.get("PATH") if env is None else env.get("PATH", os.defpath)
    found = shutil.which(tool, path=path)
    if found is None:
        return False
    root = os.path.realpath(_run_tmp)
    try:
        return os.path.commonpath([root, os.path.realpath(found)]) == root
    except ValueError:                         # כוננים שונים בווינדוס: אינו תחת ה-basetemp
        return False


def block_real_processes() -> bool:
    """מתקין את החסימה. False כשאין בכלל שרת לייבא (עמדה בלי fastapi)."""
    try:
        from server.rounds import chunklog
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return False
    chunklog.subprocess = _NoRealProcess
    return True


#: הטווח שממנו נבחר ה-portbase של הריצה: גבוה, רחוק מפורטי ההפצה,
#: ו**מתחת** לטווח הפורטים הארעיים של לינוקס (32768-60999) — סוקט יוצא
#: מקרי שיושב שם רגע היה נראה לבדיקת הפורטים כמו יתום. הצעד 4 שומר
#: שהפורטים של ריצה אחת (`portbase+2` לשידור ו-`+3` לבקרה, `chunksend.PORT_OFFSET`)
#: לא ייפלו על אלה של ריצה מקבילה.
TEST_PORTBASE_RANGE = (20000, 30000, 4)

#: ה-portbase שהוקצה לריצה הזאת; None כשלא הוקצה בכלל.
test_portbase: int | None = None


def assign_test_portbase() -> int | None:
    """נועל את מנוע השידור של **כל** אפליקציה בריצה על portbase גבוה ואקראי (#156).

    לא מספיק שכל טסט יעביר portbase משלו: `create_app` בונה מנוע משלו
    בלי לשאול, וזה המנוע שהשאיר יתום על פורטי ההפצה. לכן דורסים את
    ברירת המחדל של המודול (`chunkctx.DEFAULT_PORTBASE`, שהמנוע קורא) — פעם
    אחת לריצה ובלי החזרה, מאותה סיבה שהחסימה אינה מוחזרת.

    מחזיר את ה-portbase שהוקצה, או `None` כשאין בכלל שרת לייבא.
    """
    global test_portbase
    try:
        from server.rounds import chunkctx
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return None
    test_portbase = random.SystemRandom().randrange(*TEST_PORTBASE_RANGE)
    chunkctx.DEFAULT_PORTBASE = test_portbase
    return test_portbase


def sender_logs_under(basetemp: Path):
    """#2180, #2099: הלוגים של השולח (`chunklog.log_path`, לוג לכל מחיצה) תחת ה-basetemp של
    הריצה, לא ב-`$TMPDIR` עצמו — שם הם נשארו אחרי כל ריצה, ושם כל ה-workers של pytest-xdist
    כתבו **לאותו קובץ** (אותו אימג', אותה מחיצה): worker אחד קרא את הראיה של טסט ב-worker אחר.
    ה-basetemp הוא של ה-worker (`popen-gwN`), ולכן הלוג שלו שלו. מחזיר את התיקייה, או `None`."""
    try:
        from server.rounds import chunklog
    except ImportError:                        # pragma: no cover — עמדה חסרה
        return None
    global _run_tmp
    _run_tmp = Path(basetemp)                  # ומכאן גם: רק תחליף של `chunkcast` שיושב תחתיו רץ (`_stand_in`)
    chunklog.LOG_DIR = Path(basetemp)
    return chunklog.LOG_DIR


def scan_supported() -> bool:
    """אפשר בכלל לבדוק? בלי `/proc` אין ראיה — ואומרים זאת, לא מניחים."""
    return Path("/proc").is_dir()


def live_stream_processes(marker: str) -> list[tuple[int, str]]:
    """תהליכי שידור חיים ששורת הפקודה שלהם מזכירה את `marker`.

    הסינון הוא לפי שם התוכנית **וגם** לפי הנתיב, כדי ששורת פקודה
    שסתם מכילה את הנתיב (מעטפת, pytest עצמו) לא תיספר כתהליך יתום.
    """
    found: list[tuple[int, str]] = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:                        # התהליך מת בינתיים
            continue
        argv = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if not argv or os.path.basename(argv[0]) not in STREAM_TOOLS:
            continue
        line = " ".join(argv)
        if marker in line:
            found.append((int(entry.name), line))
    return sorted(found)


def kill_and_confirm(marker: str, timeout: float = 5.0) -> list[tuple[int, str]]:
    """הורג את מי ששייך ל-`marker`, וקורא שוב. מחזיר את מי ששרד."""
    for pid, _ in live_stream_processes(marker):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:                        # כבר מת
            pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not live_stream_processes(marker):
            return []
        time.sleep(0.1)
    for pid, _ in live_stream_processes(marker):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    time.sleep(0.3)
    return live_stream_processes(marker)


#: #2702: כמה זמן לאפשר לפירוק אחרי SIGTERM לפני שהתהליך נהרג בכל זאת.
SIGTERM_GRACE_S = 60


def install_sigterm_as_interrupt() -> bool:
    """SIGTERM (`timeout 5400 pytest`, `kill`) הורג את pytest בלי להריץ אף
    finalizer -- וה-fixtures של טסטי השיכפול המקומי הם אלה שמנתקים loops
    ו-tmpfs, כך שריצה שנקטעה השאירה אותם מעוגנים (#2702). כאן ה-SIGTERM הופך
    ל-`KeyboardInterrupt`, ש-pytest מפרק לפיו כרגיל. SIGTERM שני הורג מיד,
    ובכל מקרה התהליך נהרג אחרי `SIGTERM_GRACE_S`: פירוק שנתקע לא יהפוך את
    `timeout` לתלוי. מחזיר אם הותקן."""
    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return False
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:     # מישהו כבר טיפל
        return False

    def _on_term(_signum, _frame) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        killer = threading.Timer(SIGTERM_GRACE_S, os.kill, (os.getpid(), signal.SIGKILL))
        killer.daemon = True
        killer.start()
        raise KeyboardInterrupt("SIGTERM")

    signal.signal(signal.SIGTERM, _on_term)
    return True


def leftover_mount_lines(basetemp: str) -> list[str]:
    """#2702: מעגן או loop שנשארו תחת ה-basetemp של הריצה אחרי שכל ה-fixtures
    פורקו. `rm -rf` על התיקייה דולג על עגינה, והמקום לא מתפנה. ננתק (רק מה
    שתחת הנתיב הזה) והריצה אדומה: הניקוי שלא נעשה הוא הכשל."""
    if not mountleak.supported():
        return []
    left = mountleak.wait_clean(basetemp, timeout=5.0)
    if not left:
        return []
    mountleak.sweep(basetemp)
    return [f"#2702: {len(left)} מעגנים/loops שרדו את הריצה תחת {basetemp} (נותקו עכשיו):"]         + [f"    {item}" for item in left]


def session_verdict(basetemp: str | None, native_required: bool) -> list[str]:
    """שורות לדוח בסוף הריצה — ריק כשהריצה באמת לא השאירה כלום."""
    if basetemp is None:
        return []
    if not scan_supported():
        # לא בדקנו אינו "נקי". בווינדוס אין `/proc` ואין שולח אמיתי, ולכן
        # זו לא תקלה; במקום שבו דורשים כלים מקומיים — היא כן.
        return ["אי אפשר לסרוק תהליכים יתומים בלי /proc — הריצה לא נבדקה"] \
            if native_required else []
    lines = leftover_mount_lines(basetemp)
    found = live_stream_processes(basetemp)
    if not found:
        return lines
    lines += [f"{len(found)} תהליכי שידור שרדו את הריצה (#79) — תחת {basetemp}:"]
    lines += [f"    pid {pid}: {line}" for pid, line in found]
    survivors = kill_and_confirm(basetemp)
    lines.append("נהרגו, ואימות חוזר ב-/proc מראה שמתו." if not survivors
                 else f"ו-{len(survivors)} מהם שרדו גם את SIGKILL: {survivors}")
    return lines


# --- #1736: תהליך שהטסט הפעיל אינו נשאר אחרי שהטסט נגמר --------------------------
#
# `subprocess.run([bash, "-c", ...])` מחכה לתהליך הישיר בלבד. כל מה שהסקריפט
# הפעיל ברקע (`( ... ) &`, `sleep 300 &`, stub של fanout) נשאר חי אחריו --
# בלי `/proc` הם נראו כ-`bash -c` יתומים בני 23 שעות על ה-Testrunner. כאן
# התהליך הישיר הוא ראש session משלו, ובסוף -- גם כשהטסט עבר, גם בפקיעה --
# כל הקבוצה נהרגת.

def _sudo_kill(target: str) -> None:
    """#2702: תהליך של root אינו ניתן לסיגנל ממשתמש הטסטים. `os.kill` נכשל שם
    ב-`PermissionError`, וקוד שקרא לזה "כבר מת" השאיר ילדי `sudo` של ריצה
    שנקטעה חיים כ-root, מחזיקים את נעילת המנוע. ההורג הוא אותו `sudo -n` שהפעיל
    אותם, וה-PID (או הקבוצה) הם מה שנשמר בעת ההפעלה -- לעולם לא שם."""
    if shutil.which("sudo"):
        subprocess.run(["sudo", "-n", "kill", "-KILL", "--", target], capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=30)


def kill_pid(pid: int) -> None:
    """SIGKILL לפי PID; של root -- דרך `sudo -n`. כבר מת = לא שגיאה."""
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        _sudo_kill(str(pid))


def kill_group(pgid: int) -> None:
    """SIGKILL לכל הקבוצה. אין קבוצה (כולם כבר מתו) = לא שגיאה.

    #2702: `killpg` הורג את ה-`sudo` (שלנו) ומשאיר את ילדיו של root באותה
    קבוצה. אות 0 אחריו אומר אם נשאר חבר כלשהו (`ProcessLookupError` = הקבוצה ריקה);
    נשאר -- הקבוצה כולה נהרגת דרך `sudo -n kill -- -PGID`."""
    if not hasattr(os, "killpg"):              # ווינדוס: אין קבוצות
        return
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    try:
        os.killpg(pgid, 0)
    except ProcessLookupError:
        return
    except PermissionError:
        pass
    # חבר שנשאר: root's child (PermissionError), או ה-sudo עצמו כזומבי שטרם נאסף (הצלחה) --
    # בשני המקרים root's children עלולים להיות עוד חיים, ו-`kill -- -PGID` הורג את כולם
    _sudo_kill(f"-{pgid}")


#: #1940: אחרי SIGKILL לקבוצה, נכד שברח (setsid) עדיין מחזיק את הצינור.
#: communicate() בלי תקרה ממתין לו, ו-TimeoutExpired של הקורא לא מגיע
#: (נמדד: timeout=2 עם setsid sleep 20 חזר אחרי 20ש'). זו תקרת ניקוז,
#: לא המתנה שנייה לעבודה — חצייתה נזרקת, לא נבלעת.
RUN_GROUP_DRAIN_S = 1


def run_group(argv, *, timeout: float | None = None, **kwargs) -> subprocess.CompletedProcess:
    """כמו `subprocess.run`, אבל התהליך ראש session משלו וכל הקבוצה נהרגת
    בסוף. בווינדוס (אין `start_new_session`/`killpg`) זה `subprocess.run`
    רגיל -- ושם הסריקה של ה-guard מדולגת במוצהר."""
    if not hasattr(os, "killpg"):
        return subprocess.run(argv, timeout=timeout, **kwargs)
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    with subprocess.Popen(argv, start_new_session=True, **kwargs) as proc:
        try:
            out, err = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_group(proc.pid)
            # #1940: נכד מחוץ לקבוצה מחזיק את הצינור. בלי תקרה communicate()
            # ממתין לו וה-TimeoutExpired לא מגיע לקורא. פקיעת הניקוז נזרקת
            # כמו שהיא; ניקוז שהסתיים עדיין כישלון — לא פלט חלקי כהצלחה.
            proc.communicate(timeout=RUN_GROUP_DRAIN_S)
            raise
        finally:
            kill_group(proc.pid)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)
