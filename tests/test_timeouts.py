"""כל המתנה בסוכן — התקרה שלה, והדיווח כשהיא נחצית.

הרקע: קוד שקיבל מחיצת swap במגירה לא נכשל אלא *נתקע* — המגירה פתחה
fifo וחיכתה לזרם שלעולם לא הגיע. במעבדה זה נראה כמו קפיאה בלי סיבה,
ובלוג לא הייתה שורה אחת שמסבירה על מה חיכינו. קפיאה אילמת היא הפרה של
עיקרון 4 בדיוק כמו זריקת בלוק בשקט.

לכן כל בדיקה כאן מודדת **זמן אמיתי**: היא מריצה תת-תהליך עם timeout,
ונכשלת אם המסלול נתלה במקום לדווח. בדיקה שנתקעת במקום להיכשל היא בדיוק
הבעיה שהקובץ הזה נכתב נגדה — ב-CI היא הייתה תולה את הריצה עד התקרה של
GitHub במקום ליפול תוך דקה.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_agent import BASH, SH_FILES, posix

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"
WAITS = AGENT / "lib" / "waits.sh"
WATCHDOG = AGENT / "lib" / "watchdog.sh"
RESTORE = AGENT / "lib" / "restore.sh"

pytestmark = requires_native(("bash", BASH))

# הבדיקות שמריצות צינור אמיתי דורשות fifo, ולכן היה מתבקש לדלג עליהן
# בווינדוס כמו ב-test_fanout.py. לא: שם הסיבה היא gcc, כאן הכל sh, ו-mkfifo
# של Git Bash עובד. הבדיקה החשובה כאן היא בדיוק זו — היא לא תדולג בעמדה
# שבה נדב מריץ אותה.

#: התקרה של הבדיקה עצמה. כל מסלול כאן אמור להסתיים בשניות בודדות;
#: חצי דקה היא "משהו נתקע", לא "המכונה עמוסה".
TEST_TIMEOUT = 45


def run_sh(script: str, timeout: int = TEST_TIMEOUT) -> str:
    """מריץ סקריפט bash עם תקרת זמן אמיתית ומחזיר את פלטו. פקיעה = כישלון.

    הפלט הולך לקובץ ולא ל-PIPE, ו-stdout/stderr של התהליך הם DEVNULL.
    זה לא קישוט: ‏capture_output ממתין לסגירת הצינור, ו*כל* תהליך רקע
    שהסקריפט השאיר אחריו מחזיק עותק שלו — כולל היתומים שנוצרים בדיוק
    כשתקרה נאכפת. בלי זה הבדיקה הייתה מודדת את היתומים ולא את המסלול,
    ומכריזה על "תקיעה" גם כשהסוכן דיווח ויצא כשורה תוך שניות.

    ‏#1736: התהליך ראש session משלו וכל הקבוצה נהרגת בסוף (`hygiene.run_group`)
    -- stub ב-`&` או `sleep` שהסקריפט השאיר אחריו לא שורד את הטסט.

    ‏stdin=DEVNULL: בריצה רב-קבצית של pytest בווינדוס ה-handle של stdin
    נשבר תחת capture, וכל subprocess שיורש אותו נופל ב-WinError 50 (#14).

    הסקריפט נכתב **לקובץ** ולא עובר ב-`bash -c`: בווינדוס ארגומנט שעובר
    ~8191 תווים נקטע בשקט, ו-bash מדווח "unexpected end of file" על שורה
    שאינה סוף הסקריפט — כשל שנראה כמו באג בקוד שנבדק (נמדד ב-#926: נתיב
    ‏tmp_path של pytest ×20 זיופים + זיוף curl של 1.1KB עברו את הסף).
    """
    out_file = Path(tempfile.mkdtemp(prefix="imagectl-wait-")) / "out"
    wrapped = (
        'export PATH="/usr/bin:$PATH"\n'
        f"{{\n{script}\n}} > {posix(out_file)!r} 2>&1\n"
    )
    script_file = out_file.with_name("script.sh")
    script_file.write_text(wrapped, encoding="utf-8", newline="\n")
    try:
        hygiene.run_group(
            [BASH, posix(script_file)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        shutil.rmtree(out_file.parent, ignore_errors=True)
        pytest.fail(f"המסלול נתקע יותר מ-{timeout} שניות במקום לדווח ולהיכשל")
    try:
        return out_file.read_text(encoding="utf-8", errors="replace")
    finally:
        # ‏#1238: בלי זה כל קריאה השאירה תיקייה ב-/tmp — ~8,100 על ה-Testrunner,
        # ‏tmpfs של 2GB ב-91%, ו-507 מזויף בטסט לא קשור.
        shutil.rmtree(out_file.parent, ignore_errors=True)


def waits_prelude(box: Path, **env) -> str:
    """טוען common.sh + waits.sh עם תקרות שהבדיקה קובעת."""
    box.mkdir(parents=True, exist_ok=True)
    exports = " ".join(f"{k}={v!r}" for k, v in env.items())
    return (
        f"export RUN_DIR={posix(box)!r} {exports}; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
    )


def log_of(run_dir: Path) -> str:
    """‏common.sh קובע את LOG_FILE מתוך RUN_DIR — שם הלוג נמצא."""
    path = run_dir / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- wait_pid: תהליך שלא נגמר ------------------------------------------------


def test_a_process_that_finishes_in_time_is_simply_waited_for(tmp_path):
    box = tmp_path / "box"
    out = run_sh(waits_prelude(box) + 'sleep 1 & wait_pid $! 10 "quick-child"; echo "rc=$?"')
    assert out.strip().endswith("rc=0")
    assert "פג הזמן" not in log_of(box)


def test_a_process_that_never_ends_is_killed_reported_and_left_behind(tmp_path):
    """הדרישה במלואה: תקרה, שורת לוג שאומרת על מה חיכינו וכמה,
    והמשך — לא יציאה שקטה ולא קריסה."""
    box = tmp_path / "box"
    out = run_sh(
        waits_prelude(box)
        + 'sleep 120 & p=$!; s=$(date +%s); '
        'wait_pid "$p" 2 "stuck-child"; rc=$?; e=$(date +%s); '
        'kill -0 "$p" 2>/dev/null && alive=yes || alive=no; '
        'echo "rc=$rc elapsed=$((e - s)) alive=$alive"; '
        'echo "and-the-caller-carried-on"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 8, "התקרה לא נאכפה בזמן"
    assert fields["alive"] == "no", "התהליך התקוע נשאר חי"
    assert "and-the-caller-carried-on" in out

    line = log_of(box)
    assert "פג הזמן" in line
    assert "stuck-child" in line, "הלוג לא אומר על מה חיכינו"
    assert "2 שניות" in line, "הלוג לא אומר כמה חיכינו"


# --- wait_progress: המדד הוא חוסר התקדמות, לא משך -----------------------------


def test_a_slow_but_moving_transfer_is_never_cut_off(tmp_path):
    """הדרישה שאסור לשבור: שחזור מחיצה גדולה על כונן איטי לוקח דקות.
    התקרה היא נגד מונה שנעצר, לא נגד משך כולל — כאן היא נמוכה בהרבה
    מזמן הריצה, והריצה בכל זאת מסתיימת בהצלחה."""
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + f'( i=0; while [ $i -lt 30 ]; do i=$((i + 1)); '
        f'echo $((i * 4096)) >> {posix(counter)!r}; sleep 0.2; done ) & '
        's=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 20 2 "slow-drive"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "0", "העברה איטית-אך-מתקדמת נקטלה"
    # רצה הרבה מעבר לתקרת השקט (2 שניות) ובכל זאת שרדה.
    assert int(fields["elapsed"]) >= 4
    assert "פג הזמן" not in log_of(box)


def test_a_transfer_that_stops_moving_is_reported_with_where_it_stopped(tmp_path):
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + f'( echo 65536 >> {posix(counter)!r}; sleep 120 ) & '
        's=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 30 2 "stalled-stream"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 12

    line = log_of(box)
    assert "פג הזמן" in line and "stalled-stream" in line
    # איפה זה נעצר — זה ההבדל בין "נתקע" ל"נתקע אחרי 64KB".
    assert "65536" in line


def test_a_stream_that_never_starts_uses_the_start_ceiling(tmp_path):
    """שתי תקרות שונות ובכוונה: "השידור עוד לא התחיל" סבלני בהרבה
    מ"השידור נפסק באמצע". כאן אין בייט ראשון, ולכן התקרה הקצרה שנבחרה
    לתחילה היא זו שפוקעת — ולא זו שאחריה."""
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box)
        + 'sleep 120 & s=$(date +%s); '
        f'wait_progress $! {posix(counter)!r} 2 60 "silent-sender"; rc=$?; '
        'e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1"
    assert int(fields["elapsed"]) <= 10, "התקרה של ההתחלה לא נאכפה"
    assert "0 בייטים" in log_of(box)


def test_pv_zero_ticks_before_the_first_byte_keep_the_start_ceiling(tmp_path):
    """‏#1430: ‏`pv -n` מדפיס "0" בכל פעימה עוד לפני שהגיע בייט (גם הזיוף
    ב-STUBS מחקה את זה). השינוי ""→"0" נקרא כאן כ"הזרם התחיל", ותקרת
    השקט (300) החליפה את תקרת ההתחלה (900) — בעוד השולח רשאי להמתין
    עד 780. כאן: "0" במשך 5 שניות, תקרת שקט 2 ותקרת התחלה 20, ואז הזרם
    מגיע. **בקרה שלילית:** על הקוד הישן הפקיעה אחרי ~3 שניות, ‏rc=1."""
    box = tmp_path / "box"
    counter = box / "bytes.raw"
    box.mkdir(parents=True)
    counter.write_text("")
    out = run_sh(
        waits_prelude(box, WAIT_POLL_S=1)
        + f'( i=0; while [ $i -lt 10 ]; do i=$((i + 1)); '
        f'echo 0 >> {posix(counter)!r}; sleep 0.5; done; '
        f'echo 65536 >> {posix(counter)!r} ) & '
        f'wait_progress $! {posix(counter)!r} 20 2 "room-still-waiting"; '
        'echo "rc=$?"'
    )
    assert out.strip().endswith("rc=0"), out + log_of(box)
    assert "פג הזמן" not in log_of(box)


def test_stream_timeout_releases_a_fifo_reader_left_by_the_pipeline_wrapper(tmp_path):
    """#1130 negative control: killing the wrapper alone left sha/zstd children alive."""
    box = tmp_path / "box"; box.mkdir(parents=True)
    counter = box / "bytes.raw"; counter.write_text("")
    fifo = box / "hash.fifo"
    out = run_sh(
        waits_prelude(box, WAIT_POLL_S=1)
        + f'mkfifo {posix(fifo)!r}; '
        + f'( cat < {posix(fifo)!r}; echo released > {posix(box / "released")!r} ) & reader=$!; '
        + 'sleep 120 & wrapper=$!; '
        + f'wait_progress "$wrapper" {posix(counter)!r} 2 60 "stalled" {posix(fifo)!r}; rc=$?; '
        + 'wait_pid "$reader" 5 "fifo-reader"; reader_rc=$?; '
        + f'[ -f {posix(box / "released")!r} ] && released=yes || released=no; '
        + 'echo "rc=$rc reader_rc=$reader_rc released=$released"'
    )
    assert "rc=1" in out
    assert "reader_rc=0" in out and "released=yes" in out


# --- המסלולים האמיתיים -------------------------------------------------------

STUBS = {
    # מחקה pv -n -b: מעתיק, ומדווח מונה בייטים ל-stderr (הקורא מפנה אותו
    # ל-bytes.raw). שני ערכים שונים — אחרת wait_progress רואה מונה עומד.
    "pv": '#!/bin/sh\necho 0 >&2\ncat\necho 65536 >&2\n',
    "zstd": '#!/bin/sh\nexec cat\n',
    "partclone.dd": '#!/bin/sh\ncat > /dev/null\n',
    # ‏#1438: הקליטה והחדר מגבבים ומונים בתוך fanout (‏-S/‏-P), לא ב-tee.
    "fanout": (
        '#!/bin/sh\n'
        # #1805: the read-back is not modelled by this stand-in (test_readback_agent_1805.py
        # has its own) -- and the copy below would write its empty stdin over the "device".
        'if [ "$1" = --readback ]; then cat > /dev/null; echo "fanout stub: --readback is not modelled" >&2; exit 2; fi\n'
        'shift\n'
        'tmp="$(mktemp)"\n'
        'cat > "$tmp"\n'
        'fanout_eof "$tmp"\n'
        'for out; do cat "$tmp" > "$out" & done\n'
        'wait\n'
        'for out; do echo "$out ok"; done\n'
        'rm -f "$tmp"\n'
    ),
}

#: ‏#1438: מה שכל fanout מזויף מקבל מעל לגוף שלו. הדגלים נקלפים מהחזית,
#: כך שהגוף רואה "<buffer> <out>..." כמו קודם. ‏`fanout_eof <file>` עושה
#: מה שהבינארי עושה בסוף זרם שנקרא כולו: מוסיף את סך הבייטים ל-‏-P
#: (פורמט pv -n) וכותב ל-‏-S ‏64 ספרות hex ושורה חדשה — בלי "  -" של
#: sha256sum. גוף שלא קורא לה מדמה fanout שלא הגיע ל-EOF. ‏-H (‏XXH64)
#: אינו ממודל ונכשל בקול, כדי שזיוף לא יעבור בשקט על דגל שאינו מבין.
#: כלל "זרם ריק אינו ok" אינו ממודל — הוא של test_fanout.py.
FANOUT_OPTIONS = (
    '_fo_S=; _fo_P=\n'
    'if [ "$1" = --sha256 ]; then sha256sum | cut -c1-64; exit 0; fi\n'
    'while :; do case "$1" in\n'
    '  -S) _fo_S=$2; shift 2 ;;\n'
    '  -P) _fo_P=$2; shift 2 ;;\n'
    '  -U) shift 2 ;;\n'                # drawerfeed.sh: the drawers' fanout, counter units
    '  -H) echo "fanout stub: -H is not modelled" >&2; exit 2 ;;\n'
    '  *) break ;;\n'
    'esac; done\n'
    'fanout_eof() {\n'
    '  [ -z "$_fo_P" ] || wc -c < "$1" | tr -d " " >> "$_fo_P"\n'
    '  [ -z "$_fo_S" ] || sha256sum < "$1" | cut -c1-64 > "$_fo_S"\n'
    '}\n'
)


def make_stubs(stub_dir: Path, extra: dict | None = None) -> str:
    """זיופים נכתבים מתוך bash דווקא — קובץ שנוצר מווינדוס אינו נחשב
    בר-הרצה ב-Git Bash. ה-chmod חובה: ‏cat > יוצר קובץ בלי סיבית הרצה,
    וזיוף כזה עובר בווינדוס ונופל ב-CI בלבד. ערך None = בלי זיוף (למשל
    fanout שקומפל לאותה תיקייה)."""
    # שורות אמיתיות ולא "; " — סוגר של here-document חייב להיות לבדו
    # בשורה, ואיחוד בנקודה-פסיק בולע אותו בשקט (הבנאי "עבר" ולא יצר כלום).
    lines = [f"mkdir -p {posix(stub_dir)!r}"]
    for name, body in {**STUBS, **(extra or {})}.items():
        if body is None:
            continue
        if name == "fanout":
            body = body.replace("#!/bin/sh\n", "#!/bin/sh\n" + FANOUT_OPTIONS, 1)
        lines.append(f"cat > {posix(stub_dir)}/{name} <<'STUB'\n{body}STUB")
        lines.append(f"chmod 0755 {posix(stub_dir)}/{name}")
    lines.append(f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"')
    return "\n".join(lines) + "\n"


#: fanout מזויף שמאכיל את המגירה הראשונה בלבד ולא פותח את השנייה כלל —
#: בדיוק מה שקרה כש-fanout מת לפני הבייט הראשון (#49). הקורא של ה-fifo
#: השני נחסם ב-open() ולעולם לא ישתחרר מעצמו.
FEED_ONLY_THE_FIRST = (
    '#!/bin/sh\n'
    'shift\n'                       # החוצץ
    'first="$1"\n'
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'cat "$tmp" > "$first"\n'
    'rm -f "$tmp"\n'
    'echo "$first ok"\n'
)


def test_a_drawer_that_is_never_fed_fails_by_name_and_its_neighbour_finishes(tmp_path):
    """הרגרסיה של #49, בזמן אמיתי.

    מגירה אחת מקבלת את הזרם ומסיימת; השנייה תקועה על fifo שאיש לא כתב
    אליו. הקוד הישן היה מחכה לה לנצח, וכל החדר היה נראה קפוא. עכשיו היא
    נכשלת בשמה, השכנה שלה מסיימת (תרחיש QA: כשל במגירה אחת לא עוצר את
    השאר), והפונקציה חוזרת בהצלחה כי מישהו כן שרד.
    """
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagectl" * 4096)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for dev in ("sda", "sdb"):
        target = run / "targets" / dev
        target.mkdir(parents=True)
        (target / "state").write_text("writing\n")
        (target / "base").write_text("0\n")
        (target / "bytes.raw").write_text("")

    out = run_sh(
        make_stubs(box / "stubs", {"fanout": FEED_ONLY_THE_FIRST})
        + f"export RUN_DIR={posix(run)!r} "
        f"DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=3 WAIT_HELPER_S=3 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/drawers.sh; . {posix(AGENT)}/lib/drawerfeed.sh; . {posix(AGENT)}/lib/verdict.sh; . {posix(AGENT)}/lib/failmark.sh; . {posix(AGENT)}/lib/ntfsboot.sh; "
        f'stream_source() {{ cat {posix(payload)!r}; }}; '
        "restore_partition_drawers unicast http://s img 3 dd part.zst "
        f"{sha} '' sda sdb > {posix(box)}/pipe.out 2>&1; echo \"rc=$?\""
    )

    assert out.strip().endswith("rc=0"), out
    states = {d: (run / "targets" / d / "state").read_text().strip()
              for d in ("sda", "sdb")}
    assert states["sda"] != "failed", "המגירה שקיבלה את הזרם נפסלה"
    assert states["sdb"] == "failed", "המגירה הרעבה לא סומנה ככישלון"

    error = (run / "targets" / "sdb" / "error").read_text(encoding="utf-8")
    assert "פג הזמן" in error
    log = log_of(run)
    assert "פג הזמן" in log and "sdb" in log


def test_a_station_whose_stream_never_arrives_fails_instead_of_freezing(tmp_path):
    """תחנה בודדת: המקור פתוח ושותק. הצינור כולו היה מחכה לו לנצח."""
    box = tmp_path / "box"
    run = box / "run"
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    (target / "state").write_text("writing\n")
    (target / "base").write_text("0\n")
    (target / "bytes.raw").write_text("")

    out = run_sh(
        make_stubs(box / "stubs")
        + f"export RUN_DIR={posix(run)!r} "
        f"DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_STREAM_START_S=3 WAIT_STREAM_STALL_S=3 "
        "WAIT_HELPER_S=3; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        "stream_source() { sleep 120; }; "
        # הבדיקה הזו על התקרה של הזרם. בדיקת ההתקן שלפניה (#51) מוחלפת
        # כי אין התקן בלוקים בקופסה, ונבדקת בנפרד ב-test_restore_evidence.py.
        "node_is_block() { true; }; "
        "s=$(date +%s); "
        # פלט הצינור לקובץ נפרד, כדי ששורות ה-log של הסוכן לא יתערבבו
        # בשורת ה-rc שהבדיקה מפרסרת.
        f"restore_partition unicast http://s img sda 2 dd p2.zst deadbeef '' "
        f"> {posix(box)}/pipe.out 2>&1; "
        'rc=$?; e=$(date +%s); echo "rc=$rc elapsed=$((e - s))"'
    )
    fields = dict(f.split("=") for f in out.split() if "=" in f)
    assert fields["rc"] == "1", out
    assert int(fields["elapsed"]) <= 25

    log = log_of(run)
    assert "פג הזמן" in log
    assert "מחיצה 2" in log, "הלוג לא אומר איזו מחיצה נתקעה"


# --- ‏#957: המחיצה הראשונה ממתינה למפעיל, הבאות ממתינות לחדר ----------------

#: ‏udp-receiver מזויף: רושם את ה-argv שלו ומוציא את התוכן — כמו האמיתי,
#: רק בלי רשת. הזרם עצמו כאן אינו הנבדק; הדגלים שהוא קיבל הם הנבדק.
RECORDING_RECEIVER = (
    '#!/bin/sh\n'
    'printf "%s\n" "$@" >> "$UDP_ARGV"\n'
    'echo "---" >> "$UDP_ARGV"\n'
    'cat "$UDP_PAYLOAD"\n'
)


#: ‏fanout מזויף למגירה אחת: מזין את ה-fifo שלה ומדווח עליה `ok`.
FEED_ONE = (
    '#!/bin/sh\n'
    'shift\n'
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'cat "$tmp" > "$1"\n'
    'rm -f "$tmp"\n'
    'echo "$1 ok"\n'
)


def _argv_blocks(path: Path) -> list[list[str]]:
    text = path.read_text(encoding="utf-8")
    return [b.splitlines() for b in text.split("---\n") if b.strip()]


def _flag(argv: list[str], flag: str) -> str:
    return argv[argv.index(flag) + 1]


def _two_streams(tmp_path, harness: str, call: str) -> tuple[list[list[str]], list[str]]:
    """מריץ שתי מחיצות מוזרמות בשחזור אחד, ומחזיר את ה-argv של כל
    ‏udp-receiver ואת התקרה ש-wait_progress קיבל לכל זרם."""
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagectl" * 512)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    for dev in ("sda",):
        target = run / "targets" / dev
        target.mkdir(parents=True)
        (target / "state").write_text("writing\n")
        (target / "base").write_text("0\n")
        (target / "bytes.raw").write_text("")
    argv, ceilings = box / "udp.argv", box / "ceilings"
    out = run_sh(
        make_stubs(box / "stubs", {"udp-receiver": RECORDING_RECEIVER, "fanout": FEED_ONE})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} "
        f"UDP_ARGV={posix(argv)!r} UDP_PAYLOAD={posix(payload)!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=10 WAIT_STREAM_STALL_S=20; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/drawers.sh; . {posix(AGENT)}/lib/drawerfeed.sh; . {posix(AGENT)}/lib/verdict.sh; . {posix(AGENT)}/lib/failmark.sh; . {posix(AGENT)}/lib/ntfsboot.sh; "
        # ‏wait_progress האמיתי — רק רושם את התקרה שקיבל לפני שהוא ממתין.
        "eval \"orig_$(declare -f wait_progress)\"; "
        f'wait_progress() {{ echo "$3" >> {posix(ceilings)!r}; orig_wait_progress "$@"; }}; '
        "node_is_block() { true; }; "
        + harness
        + f"{call} 2 dd p2.zst {sha} '' sda > {posix(box)}/pipe.out 2>&1; r1=$?; "
        f"{call} 3 dd p3.zst {sha} '' sda >> {posix(box)}/pipe.out 2>&1; r2=$?; "
        'echo "rc=$r1$r2"'
    )
    assert out.strip().endswith("rc=00"), out + (box / "pipe.out").read_text("utf-8", "replace")
    return _argv_blocks(argv), ceilings.read_text(encoding="utf-8").split()


@pytest.mark.parametrize(
    ("harness", "call"),
    [
        pytest.param("STREAMED_PARTITIONS=0; ",
                     "restore_partition multicast http://s img sda", id="station"),
        pytest.param("target_init sda 4096; STREAMED_PARTITIONS=0; ",
                     "restore_partition_drawers multicast http://s img", id="drawers"),
    ],
)
def test_the_second_stream_waits_longer_for_its_first_byte_than_the_first(tmp_path, harness, call):
    """‏#957 (FOG GH-536): למחיצה הראשונה ההמתנה היא "המפעיל לא התחיל"
    — 600ש'. מהשנייה השולח ממתין עד 600ש' למגירה האיטית שעוד כותבת
    את הקודמת, ו-udp-receiver מודד את ההמתנה הזאת ב-`--start-timeout`
    שלו: תקרה של 600 שם הייתה מפילה דווקא את המקבל שהגיע ראשון, בדיוק
    כשהשולח מתחיל. לכן הזרם השני מקבל תקרה גדולה יותר (900), ואותה
    תקרה בדיוק מקבל wait_progress שמסתכל על המונה — בשני מסלולי
    השחזור (תחנה בודדת, חדר שיכפולים).

    **בקרה שלילית:** על הקוד הישן (‏`--start-timeout $UDPCAST_START_TIMEOUT`
    אחיד) שני הזרמים מקבלים 600, וההשוואה של הזרם השני נופלת."""
    blocks, ceilings = _two_streams(tmp_path, harness, call)
    assert len(blocks) == 2, blocks
    # ההתנהגות קודם, בלי להישען על הקבוע החדש: הזרם השני ממתין יותר.
    got = [int(_flag(b, "--start-timeout")) for b in blocks]
    assert got[1] > got[0], f"--start-timeout לכל זרם: {got}"
    assert [int(c) for c in ceilings] == got, (ceilings, got)
    first = _sh_default(WAITS, "WAIT_STREAM_START_S")
    later = _sh_default(WAITS, "WAIT_STREAM_START_LATER_S")
    assert _flag(blocks[0], "--start-timeout") == str(first)
    assert _flag(blocks[1], "--start-timeout") == str(later)
    assert ceilings == [str(first), str(later)], ceilings
    # תקרת השקט אינה חלק מזה — היא על זרם שכבר התחיל, ונשארת אחידה.
    assert {_flag(b, "--receive-timeout") for b in blocks} == {"20"}


#: ‏#1438: המונה של החדר נכתב עכשיו ב-fanout (‏-P), לא ב-pv. ‏FEED_ONE
#: שותק עד EOF — כך הקובץ מכיל רק את מה שנשאר מהמחיצה הקודמת, וזה מה
#: ש-wait_progress קורא בסקירה הראשונה. ‏FEED_ONE_TICKS_ZERO כותב "0"
#: לפני הבייט הראשון, כמו pv -n — הצורה ש-waits.sh חייב לסנן.
FEED_ONE_TICKS_ZERO = FEED_ONE.replace(
    'tmp="$(mktemp)"\n', '[ -z "$_fo_P" ] || echo 0 >> "$_fo_P"\ntmp="$(mktemp)"\n', 1)


@pytest.mark.parametrize(
    "feed", [pytest.param(FEED_ONE_TICKS_ZERO, id="counter-ticks-zero"),
             pytest.param(FEED_ONE, id="previous-partition-count")])
def test_a_drawer_room_waits_out_a_late_second_partition_instead_of_the_stall_ceiling(
        tmp_path, feed):
    """‏#1430 (מעבדת ה-VM, 15/10): ‏p1 נכתב, והשולח המתין למכונה שכבר
    נכשלה לפני ששידר את p14. המכונה התקינה פקעה אחרי ~361ש' — 300 של
    תקרת השקט ועוד ההמתנות למגירות — ולא אחרי 900 של תקרת ההתחלה.
    השורש: ‏`$RUN_DIR/bytes.raw` של החדר אינו מתאפס בין מחיצות, והמונה
    הסופי של p1 נקרא ב-p14 כ"בייט ראשון" (ו-"0" של pv — באותה דרך).

    כאן: הזרם השני שותק 5 שניות — יותר מתקרת השקט (2), פחות מתקרת
    ההתחלה של מחיצה 2+ (20) — ואז מגיע. **בקרה שלילית:** על הקוד הישן
    המחיצה השנייה נכשלת "פג הזמן -- הזרם לא הגיע". שני הזיופים של המונה
    מכסים את שני המנגנונים: המונה של המחיצה הקודמת (drawers.sh) וה-"0"
    שלפני הבייט הראשון (waits.sh) — כל אחד נופל כשרק התיקון שלו חסר."""
    box = tmp_path / "box"
    run = box / "run"
    payload = box / "part.bin"
    box.mkdir(parents=True)
    payload.write_bytes(b"imagectl" * 512)
    sha = hashlib.sha256(payload.read_bytes()).hexdigest()
    target = run / "targets" / "sda"
    target.mkdir(parents=True)
    (target / "state").write_text("writing\n")
    out = run_sh(
        make_stubs(box / "stubs", {"fanout": feed})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)!r} "
        "WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=10 WAIT_STREAM_STALL_S=2 "
        "WAIT_STREAM_START_S=20 WAIT_STREAM_START_LATER_S=20; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(WAITS)}; "
        f". {posix(AGENT)}/lib/progress.sh; . {posix(AGENT)}/lib/restore.sh; "
        f". {posix(AGENT)}/lib/drawers.sh; . {posix(AGENT)}/lib/drawerfeed.sh; . {posix(AGENT)}/lib/verdict.sh; . {posix(AGENT)}/lib/failmark.sh; . {posix(AGENT)}/lib/ntfsboot.sh; "
        # השולח ממתין לחדר לפני המחיצה השנייה: שקט ארוך מתקרת השקט.
        f'stream_source() {{ [ "$4" = p3.zst ] && sleep 5; cat {posix(payload)!r}; }}; '
        "target_init sda 4096; STREAMED_PARTITIONS=0; "
        f"restore_partition_drawers multicast http://s img 2 dd p2.zst {sha} '' sda "
        f"> {posix(box)}/pipe.out 2>&1; r1=$?; "
        f"restore_partition_drawers multicast http://s img 3 dd p3.zst {sha} '' sda "
        f">> {posix(box)}/pipe.out 2>&1; r2=$?; "
        'echo "rc=$r1$r2"'
    )
    log = log_of(run)
    assert out.strip().endswith("rc=00"), out + log
    assert (target / "state").read_text().strip() != "failed", log
    assert "פג הזמן" not in log


def test_the_later_ceiling_outlasts_the_senders_later_wait():
    """המספרים משני צידי הרשת חייבים להיות מסודרים, וזה נבדק כאן ולא
    מונח: השולח (‏`server/sender.py`) ממתין למחיצות 2+ עד
    ‏`max_wait_later` מהמקבל הראשון, ומוגבל ב-`start_timeout_later`
    שמכיל אותו; המקבל שהצטרף ראשון ממתין את כל ה-`max_wait_later`
    בעצמו, ולכן תקרת הבייט הראשון שלו חייבת להיות **גדולה** ממנו —
    ומחשב הבנייה בהפצה ישירה ממתין ל-udp-sender שלו עד
    ‏`start_timeout_later`, ולכן היא חייבת להיות גדולה גם ממנו.
    והמחיצה הראשונה — לא השתנתה: הראשונה עדיין 600, ואינה ארוכה
    יותר מהשנייה."""
    from server import sender as sender_module

    first = _sh_default(WAITS, "WAIT_STREAM_START_S")
    later = _sh_default(WAITS, "WAIT_STREAM_START_LATER_S")
    max_wait_later = sender_module.later_max_wait(sender_module.DEFAULT_MAX_WAIT)
    start_later = sender_module.later_start_timeout(
        sender_module.DEFAULT_MAX_WAIT, sender_module.DEFAULT_START_TIMEOUT)
    assert max_wait_later == 600 and start_later == 780
    assert later > start_later > max_wait_later, (later, start_later, max_wait_later)
    assert first == 600 and first < later
    # השולח למחיצה הראשונה — כפי שהיה: 120 מהמקבל הראשון, 180 בסך הכול.
    assert sender_module.DEFAULT_MAX_WAIT == 120
    assert sender_module.DEFAULT_START_TIMEOUT == 180


# --- שמירה על הכלל: אין המתנה בלי תקרה ---------------------------------------


def test_no_bare_wait_is_left_anywhere_in_the_agent():
    """‏`wait <pid>` חשוף הוא המתנה בלי תקרה. כל אחת כזו עוברת דרך
    wait_pid/wait_progress, ולכן ההופעות היחידות של ה-builtin הן בתוך
    waits.sh עצמו."""
    offenders = []
    for path in SH_FILES:
        if path.name == "waits.sh":
            continue
        for number, line in enumerate(
                path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.match(r"\s*wait\b", line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], "המתנות בלי תקרה: " + "; ".join(offenders)


@pytest.mark.parametrize("name", ["restore.sh", "drawers.sh", "capture.sh"])
def test_every_streaming_library_waits_through_the_helpers(name):
    """שלושת הקבצים שמזרימים בייטים — שחזור תחנה, חדר שיכפולים, קליטה —
    עוברים כולם דרך אותם שני עוזרים. אין קובץ שממציא המתנה משלו."""
    source = (AGENT / "lib" / name).read_text(encoding="utf-8")
    assert "wait_progress " in source, f"{name} מזרים בלי שעון על ההתקדמות"
    assert "wait_pid " in source, f"{name} ממתין לתהליך עזר בלי תקרה"


def test_every_ceiling_is_defined_in_one_place():
    """הערכים לא מפוזרים כמספרי קסם: כל WAIT_* שנקרא באיזשהו קובץ
    מוגדר — עם נימוק — בראש waits.sh."""
    defined = set(re.findall(r"^(WAIT_[A-Z_]+)=", WAITS.read_text(encoding="utf-8"),
                             flags=re.M))
    assert defined, "waits.sh לא מגדיר תקרות בכלל"
    used = set()
    for path in SH_FILES:
        if path.name == "waits.sh":
            continue
        used |= set(re.findall(r"\$\{?(WAIT_[A-Z_]+)", path.read_text(encoding="utf-8")))
    assert used - defined == set(), f"תקרות שאין להן הגדרה: {used - defined}"


@pytest.mark.parametrize(
    ("path", "needles"),
    [
        # ‏curl: בלי תקרת משך (מחיצה גדולה לוקחת דקות) אבל עם תקרת
        # חוסר-התקדמות — חיבור שנפל יוצא במקום להיתלות.
        ("lib/common.sh", ["--speed-limit", "--speed-time"]),
        ("lib/capturesink.sh", ["--speed-limit", "--speed-time"]),   # #1217: capture_sink
        # ‏udp-receiver ממתין לשדר לנצח כברירת מחדל. אלה התקרות שלו עצמו.
        ("lib/restore.sh", ["--start-timeout", "--receive-timeout"]),
    ],
    ids=lambda v: str(v),
)
def test_the_long_transfers_carry_their_own_ceiling(path, needles):
    source = (AGENT / path).read_text(encoding="utf-8")
    for needle in needles:
        assert needle in source, f"{path} מזרים בלי {needle}"


def _sh_default(path: Path, var: str) -> int:
    """הערך שאחרי `:-` בהשמה `VAR="${VAR:-N}"` — ברירת המחדל כשה-env ריק."""
    m = re.search(rf'{var}="\$\{{{var}:-(\d+)\}}"', path.read_text(encoding="utf-8"))
    assert m, f"{var} אינו מוגדר עם ברירת מחדל מספרית ב-{path.name}"
    return int(m.group(1))


def test_the_stream_stall_ceiling_is_300_and_stays_under_the_watchdog_lease():
    """‏11/09/2026 (הכרעת נדב): תקרת השקט באמצע הזרם הועלתה מ-120 ל-300ש'
    — השולח מאט למקבל איטי בזנב אימג' גדול (גישת FOG/Clonezilla) במקום
    להפילו, ותקרת 120 חתכה אותו דווקא אז (כשל ~90%). ‏udp-receiver
    (‏`--receive-timeout` ב-restore.sh) נגזר מאותו ערך, ולכן הם נעים יחד.

    **הקישור ל-watchdog:** התקרה חייבת להישאר מתחת ל-lease של ה-watchdog
    (watchdog.sh, 600ש'), שמוזן בכל דגימה של wait_progress/wait_pid — אחרת
    ה-watchdog יאתחל את המכונה לפני שהשקט מדווח ככישלון גלוי. ‏300 < 600.

    **בקרה שלילית:** החזרת ברירת המחדל ל-120 מפילה את `== 300`; העלאתה
    מעל ה-lease מפילה את בדיקת `< lease`."""
    stall = _sh_default(WAITS, "WAIT_STREAM_STALL_S")
    assert stall == 300
    # הנפילה-אחורה של udp-receiver ב-restore.sh נעה יחד עם waits.sh.
    m = re.search(r'WAIT_STREAM_STALL_S:-(\d+)', RESTORE.read_text(encoding="utf-8"))
    assert m and int(m.group(1)) == 300, "ה---receive-timeout ב-restore.sh לא תואם"
    lease = int(re.search(r'-ge (\d+)', WATCHDOG.read_text(encoding="utf-8")).group(1))
    assert stall < lease, f"תקרת השקט {stall} חייבת להיות מתחת ל-lease {lease}"


def test_fanout_bounds_the_fifo_open_itself():
    """‏open() על fifo לכתיבה נחסם עד שיש קורא. ‏fanout פותח ב-O_NONBLOCK
    ומוותר אחרי OPEN_RETRY_MS — ומדווח על כך כפקיעה, לא כ"לא ניתן לפתוח":
    ‏fifo שאין לו קורא ונתיב שאינו קיים הם שתי תקלות שונות לטכנאי."""
    source = (AGENT / "fanout.c").read_text(encoding="utf-8")
    assert "O_WRONLY | O_NONBLOCK" in source
    assert "OPEN_NO_READER" in source
    assert "timed out waiting for the writer pipeline" in source
    for ceiling in ("OPEN_RETRY_MS", "ROOM_POLL_MS"):
        assert re.search(rf"#define {ceiling}\s+\d+", source), f"{ceiling} אינו קבוע"
    # #427: EOF בלי אורך אינו ok. הסימנים רצים גם בלי gcc (ווינדוס).
    assert "FANOUT_EXPECTED_BYTES" in source
    assert "empty stream" in source
    assert "fsync" in source


def test_run_sh_leaves_no_temp_dir_behind():
    """#1238: run_sh מנקה את התיקייה הזמנית שלו — גם אחרי ריצה תקינה."""
    before = set(Path(tempfile.gettempdir()).glob("imagectl-wait-*"))
    assert run_sh("echo hi").strip() == "hi"
    after = set(Path(tempfile.gettempdir()).glob("imagectl-wait-*"))
    assert after - before == set(), f"left behind: {sorted(after - before)}"
