"""‏#72: רמת הדחיסה בקליטה — החלטה שנמדדה, ונעולה למספרים שנמדדו.

‏`CAPTURE_LEVEL` היה 9, בלי `-T`. שתי סקירות חיצוניות סתרו זו את זו על
הערך הזה, ואף אחת מהן לא מדדה. המדידה — ‏4GiB מראש `p3.windows.pcl.zst`
של ‏tiny11, מפורק ונדחס מחדש על שרת המעבדה (‏2 ליבות, ‏zstd 1.5.7):

| opts     | bytes         | זמן    | שיא זיכרון |
|----------|---------------|--------|------------|
| `-1`     | 3,209,422,538 |  9.3s  |            |
| `-3`     | 3,170,554,752 | 13.7s  |    54MB    |
| `-9`     | 3,135,326,079 | 31.2s  |   112MB    |
| `-3 -T0` | 3,170,554,752 |  9.4s  |    71MB    |
| `-9 -T0` | 3,135,326,079 | 21.4s  |            |
| `-3 -T2` | 3,170,554,752 |  9.9s  |    72MB    |

רמה 9 עולה פי 3.1 בזמן ומחזירה 1.1% בגודל. ‏1.1% על אימג' של 7.3GB הם
‏~80MB — כארבע שניות בשידור שאורך 5:54. **וההסתייגות היחידה שהייתה
ל-`-T` התהפכה במדידה:** ‏`-3 -T2` הוא 72MB בשיא, פחות מ-112MB של רמה 9
שרצה כאן עד היום. הברירה החדשה מהירה יותר *וגם* קלה יותר בזיכרון.

עד 16/10 הברירה הייתה `-T2` ולא `-T0`, בגלל ה-OOM של #21 (מכונת 512MB).
המדידה שלמעלה היא של שרת בן **2 ליבות** — שם `-T2` ו-`-T0` זהים. על
HP-BUILD (‏i5-8400, ‏6 ליבות, קליטה של ~20GB, ‏top כל 15ש', ‏16/10) ‏`-T2`
היה התקרה: ‏zstd ‏15-32% מהמכונה ו-33-56% idle, העלאה ~70MB/s מול שיא
117. נדב הכריע (16/10): ‏`-T0`, רמה 3 נשארת.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh

CAPTURE = AGENT / "lib" / "capture" / "capture.sh"

pytestmark = requires_native(("bash", BASH))


def defaults() -> dict[str, str]:
    """הערכים כפי שהסוכן עצמו נטען איתם — לא כפי שהטסט מניח."""
    out = sh(f'. {posix(CAPTURE)}; echo "$CAPTURE_LEVEL $CAPTURE_THREADS"')
    level, threads = out.split()
    return {"level": level, "threads": threads}


def test_the_measured_level_is_the_default():
    """הערך שנמדד הוא הערך שרץ. רמה 9 עלתה פי 3.1 בזמן תמורת 1.1%."""
    assert defaults()["level"] == "3"


def test_capture_compresses_on_every_core():
    """הכרעת נדב 16/10: ‏`-T0`. ה-`-T2` נמדד "מהיר כמו ‎-T0" על שרת המעבדה
    בן 2 הליבות — שם אין הבדל. על HP-BUILD (‏i5-8400, ‏6 ליבות, קליטה של
    ‏~20GB, ‏top כל 15ש', ‏16/10) ‏zstd נתקע על 15-32% מהמכונה (‏~1.9 ליבות =
    התקרה של ‎-T2) בזמן ש-33-56% מה-CPU עמד בטל, וההעלאה ממוצעת ~70MB/s
    מול שיא 117 של קו ה-1GbE. בקרה שלילית: על `CAPTURE_THREADS=2` זה נופל."""
    assert defaults()["threads"] == "0"


def test_the_real_capture_pipeline_hands_zstd_t0(tmp_path):
    """The real `capture_disk` (test_capture_refusals harness), with a zstd
    that records its argv: what the pipeline actually ran with, not what the
    source text says. Negative control: with the old default (2) it is -T2."""
    from test_capture_refusals import ONE_PARTITION, capture_run
    from test_fanout_hash_1438 import CURL_KEEP, RAW

    argv = tmp_path / "zstd.argv"
    stubs = {
        "sgdisk": ONE_PARTITION,
        "curl": CURL_KEEP,
        "zstd": '#!/bin/sh\nprintf "%s\\n" "$@" >> "$ZARGV"\ncat\n',
        "partclone.dd": "#!/bin/sh\nprintf '%s' '" + RAW.decode() + "'\n",
    }
    _box, _run, out = capture_run(tmp_path, stubs=stubs, env={
        "ZARGV": posix(argv), "UPLOADED": posix(tmp_path / "uploaded.bin")})
    assert out.strip().endswith("rc=0"), out
    assert argv.read_text(encoding="utf-8").split() == ["-3", "-T0", "-c"]


def test_both_stay_overridable_from_the_environment():
    """שניהם נשארים ניתנים לדריסה כפי שהיו — מחשב בנייה עם 16 ליבות
    ואימג' שנשמר לארכיון הם מקרים אמיתיים, והם לא צריכים קוד חדש."""
    out = sh('CAPTURE_LEVEL=19 CAPTURE_THREADS=8; export CAPTURE_LEVEL CAPTURE_THREADS; '
             f'. {posix(CAPTURE)}; echo "$CAPTURE_LEVEL $CAPTURE_THREADS"')
    assert out.split() == ["19", "8"]


def test_zstd_is_invoked_with_both_of_them():
    """הדגלים חייבים להגיע לכלי. ‏`-T` שנשאר בהערה ולא בשורת הפקודה הוא
    בדיוק "החלטנו" בלי "ביצענו"."""
    source = CAPTURE.read_text(encoding="utf-8")
    assert 'zstd -"$CAPTURE_LEVEL" -T"$CAPTURE_THREADS" -c' in source


def test_the_manifest_records_the_level_that_was_actually_used():
    """השדה `compression` נגזר מאותו משתנה שנכנס לשורת הפקודה של zstd,
    ולא ממספר שנכתב בנפרד. שני מקורות אמת לערך אחד נגמרים בכך שהמניפסט
    מצהיר `zstd-9` על אימג' שנדחס ב-3, וזה שקר שאי אפשר לגלות מהקובץ."""
    source = CAPTURE.read_text(encoding="utf-8")
    assert '"compression":"zstd-%s"' in source
    # הארגומנט האחרון ל-printf של המניפסט הוא המשתנה עצמו.
    printf_args = source[source.index('"compression":"zstd-%s"'):]
    assert '"$CAPTURE_LEVEL"' in printf_args, "המניפסט אינו נגזר מהמשתנה"
    # ואין בקובץ אף מופע של רמה קשיחה בתוך המחרוזת.
    assert not re.search(r"zstd-[0-9]", source), "רמה קשיחה במניפסט"


def test_the_agent_says_which_options_it_asked_for():
    """‏zstd שהודר בלי ריבוי תהליכונים מזהיר ומתעלם מ-`-T`. האזהרה שלו
    כבר הולכת ליומן; בלי הצהרה לצידה אי אפשר לדעת *מה ביקשנו*, ולכן אי
    אפשר לדעת ש-`-T2` לא רץ בכלל (עיקרון 5)."""
    source = CAPTURE.read_text(encoding="utf-8")
    assert 'log "compressing with zstd -$CAPTURE_LEVEL -T$CAPTURE_THREADS"' in source


@requires_native("zstd")
def test_real_zstd_accepts_exactly_what_the_agent_will_hand_it(tmp_path):
    """הבדיקה היחידה כאן שמריצה את הכלי עצמו, ועם הערכים של הסוכן ולא עם
    ערכים שהטסט המציא: דגל שגוי (`-T 2` עם רווח, רמה מחוץ לטווח) עובר כל
    בדיקת מקור ונופל רק מול מחיצה אמיתית במעבדה. גם הלוך-חזור, כי דחיסה
    שאי אפשר לפרוח היא אימג' שאבד."""
    values = defaults()
    payload = b"imagesrv" * 4096
    source = tmp_path / "part.raw"
    source.write_bytes(payload)

    packed = subprocess.run(
        ["zstd", f"-{values['level']}", f"-T{values['threads']}", "-c", str(source)],
        capture_output=True, stdin=subprocess.DEVNULL,
    )
    assert packed.returncode == 0, packed.stderr.decode("utf-8", "replace")
    assert packed.stdout, "zstd לא הוציא בייטים"

    back = subprocess.run(["zstd", "-dc"], input=packed.stdout,
                          capture_output=True)
    assert back.returncode == 0, back.stderr.decode("utf-8", "replace")
    assert back.stdout == payload, "הלוך-חזור לא החזיר את אותם בייטים"


def test_the_interfaces_document_shows_the_level_that_ships():
    """‏`docs/interfaces.md` הוא מקור האמת למה שעובר בין רכיבים. דוגמה
    שנשארה על `zstd-9` היא בדיוק ההפרש בין מה שכתוב למה שרץ."""
    doc = (Path(__file__).resolve().parent.parent / "docs" / "interfaces.md")
    text = doc.read_text(encoding="utf-8")
    assert '"compression": "zstd-3"' in text
