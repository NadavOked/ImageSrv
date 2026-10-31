"""‏#106: סירוב מבני בקליטה מגיע לקונסולה **עם סיבה**, לא כ-failed ריק.

‏`capture_disk` ידע לצרף סיבה לכשל של מחיצה בלבד (שורות 211-218 לפני
התיקון). ארבעת הסירובים שקורים *לפני* הזרם — אין דיסק כזה, הדיסק אינו
GPT, אין מחיצות, לא הצלחנו לחשב את הפריסה — היו `log` ו-`return 1`
בלבד. ‏`log` כותב ל-`$LOG_FILE` שיושב ב-tmpfs ונמחק באתחול, ומהדהד
ל-tty שנמחק מיד אחר כך ב-`ui_clear`. הסיבה קיימת לרגע ואז נעלמת.

מה שהמפעיל ראה: משימת קליטה במצב `failed`, בלי שורת סיבה. מה שהוא היה
צריך לדעת — "הכונן שחיברת אינו GPT" — נכתב ונעלם. זו שיחת התמיכה
הגרועה ביותר: הטכנאי מחליף כבל, מחליף כונן, מנסה שוב.

לכן כל בדיקה כאן נמדדת ב**מצב שהקונסולה קוראת** — ‏`targets/<dev>/error`
ומה ש-`build_progress` מרכיב ממנו (ממשק 4) — ולא ביומן. בדיקה שמסתפקת
ביומן הייתה עוברת גם על הקוד השבור.

ובקרה שלילית משלה: הסיבות חייבות להיות **נבדלות זו מזו**. עיקרון 5
אוסר לקפל "אינו GPT" ו"אין מחיצות" ו"לא הצלחנו לחשב" למחרוזת אחת.
ל-#87 נוספו כאן שני שערים — פריסה שאינה נכנסת לכונן היעד, ורצפת יעד
שאינה מספר בייטים — והם נספרים באותה בקרה.
"""

from __future__ import annotations

import inspect
import json
import re
import shutil
from pathlib import Path

from agentlib import lib_files

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

MAC = "aa:bb:cc:00:00:10"
TASK = "tsk_106"

#: כותרת GPT אמיתית כפי ש-`disk_scheme` מחפש אותה: "EFI PART" ב-LBA1.
GPT_DISK = bytes(512) + b"EFI PART" + bytes(504)
#: ‏MBR ותו לא — החתימה ב-510, ואין "EFI PART" אחריה.
MBR_DISK = bytes(510) + b"\x55\xaa" + bytes(512)
#: לא זה ולא זה. דיסק ריק שזה עתה יצא מהאריזה.
BLANK_DISK = bytes(1024)

#: ‏sgdisk שמדפיס טבלה בלי אף מחיצה — דיסק עם כותרת GPT ותו לא.
EMPTY_TABLE = (
    '#!/bin/sh\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
)

#: ‏sgdisk עם מחיצה אחת — מספיק כדי לעבור את שער "אין מחיצות".
ONE_PARTITION = (
    '#!/bin/sh\n'
    'if [ "$1" = "-i" ]; then\n'
    '  echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI)"\n'
    '  echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002"\n'
    '  echo "First sector: 2048 (at 1024 KiB)"\n'
    '  echo "Partition size: 204800 sectors (100.0 MiB)"\n'
    '  echo "Attribute flags: 0000000000000000"\n'
    '  echo "Partition name: \'EFI system partition\'"\n'
    '  exit 0\n'
    'fi\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    'echo "   1            2048          206847   100.0 MiB   EF00  EFI system"\n'
)

#: ‏awk אמיתי בכל קריאה — חוץ מזו שמחשבת את הפריסה, שמזוהה לפי `-v disk=`
#: והיא היחידה בקובץ. השער הרביעי הוא "‏awk לא הניב ערך", וזה המצב היחיד
#: שמייצר אותו: כל שאר המסלולים מדפיסים מספר. הזיוף מדויק בכוונה — הוא
#: לא מפיל את פרסור ה-sgdisk שרץ באותו קובץ שורה קודם.
AWK_WITHOUT_AN_ANSWER = (
    '#!/bin/sh\n'
    'for a in "$@"; do\n'
    '  case "$a" in disk=*) exit 0 ;; esac\n'
    'done\n'
    'real=$(PATH=/usr/bin:/bin command -v awk)\n'
    'exec "$real" "$@"\n'
)

#: ‏curl מזויף שמחקה `-T <קובץ>`: הוא **קורא** את ה-fifo שהקליטה פותחת.
#: בלעדיו `tee` נחסם ב-open() לנצח — בדיוק המצב שהסוכן עצמו נכווה בו,
#: וכאן הוא היה הופך בדיקה שאמורה לעבור לתקיעה של 45 שניות. הוא יושב
#: ליד ההרנס ולא בקובץ של #87: הוא מה שמניע את המסלול ה**מצליח**, וגם
#: השומר של #338 שבסוף הקובץ צריך אותו.
CURL_SINK = (
    '#!/bin/sh\n'
    'case "$*" in */preflight*) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;; esac\n'
    'f=""\n'
    'while [ $# -gt 0 ]; do\n'
    '  [ "$1" = "-T" ] && { f="$2"; shift; }\n'
    '  shift\n'
    'done\n'
    '[ -n "$f" ] && cat "$f" > /dev/null\n'
    'exit 0\n'
)


#: ‏#926: curl שמשחק גם את השרת של זיכרון הכיווץ — עונה ל-`shrink-open` /
#: ‏`shrink-close` כמו `-o <קובץ>` + `-w '%{http_code}'` של הסוכן, רושם כל
#: גוף שנשלח (`shrink_open.sent` / `shrink_close.sent`) ואת הצעד ב-order.log,
#: ומרוקן `-T` כמו CURL_SINK. ‏SHRINK_OPEN_HTTP / SHRINK_CLOSE_HTTP קובעים
#: את התשובה; ‏000 = השרת לא נענה כלל (curl יוצא 7, בלי קוד).
CURL_SHRINK_SERVER = (
    '#!/bin/sh\n'
    'f=""; o=""; d=""; url=""\n'
    'while [ $# -gt 0 ]; do\n'
    '  case "$1" in\n'
    '    -T) f="$2"; shift ;;\n'
    '    -o) o="$2"; shift ;;\n'
    '    --data-binary) d="${2#@}"; shift ;;\n'
    '    http://*|https://*) url="$1" ;;\n'
    '  esac\n'
    '  shift\n'
    'done\n'
    'case "$url" in\n'
    '  */preflight) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;;\n'
    '  */agent/shrink-open)\n'
    '    cat "$d" >> "$RUN_DIR/shrink_open.sent"; echo >> "$RUN_DIR/shrink_open.sent"\n'
    '    echo "shrink-open" >> "$RUN_DIR/order.log"\n'
    '    code="${SHRINK_OPEN_HTTP:-200}"\n'
    '    case "$code" in\n'
    "      200) printf '{\"ok\":true,\"id\":7}' > \"$o\" ;;\n"
    "      409) printf '{\"ok\":false,\"code\":\"already_open\",\"id\":3,\"opened_at\":\"2026-09-16T20:00:00+00:00\",\"error\":\"already open\"}' > \"$o\" ;;\n"
    '      000) exit 7 ;;\n'
    "      *) printf '{\"ok\":false,\"error\":\"database is locked\",\"code\":\"db\"}' > \"$o\" ;;\n"
    '    esac\n'
    '    printf "%s" "$code"; exit 0 ;;\n'
    '  */agent/shrink-close)\n'
    '    cat "$d" >> "$RUN_DIR/shrink_close.sent"; echo >> "$RUN_DIR/shrink_close.sent"\n'
    '    echo "shrink-close" >> "$RUN_DIR/order.log"\n'
    '    code="${SHRINK_CLOSE_HTTP:-200}"\n'
    "    if [ \"$code\" = 200 ]; then printf '{\"ok\":true}' > \"$o\"; else printf '{\"ok\":false,\"code\":\"not_open\"}' > \"$o\"; fi\n"
    '    printf "%s" "$code"; exit 0 ;;\n'
    '  */agent/shrink-note)\n'
    '    cat "$d" >> "$RUN_DIR/shrink_note.sent"; echo >> "$RUN_DIR/shrink_note.sent"\n'
    '    echo "shrink-note" >> "$RUN_DIR/order.log"\n'
    "    printf '{\"ok\":true}' > \"$o\"; printf 200; exit 0 ;;\n"
    'esac\n'
    '[ -n "$f" ] && cat "$f" > /dev/null\n'
    'exit 0\n'
)

#: ‏#1248: בדיקת המקום עוברת, וכל קריאה אחרת נכשלת כמו curl שלא התחבר.
CURL_NO_SERVER = (
    '#!/bin/sh\n'
    'case "$*" in */preflight*) echo \'{"status":"ok","reason":"space checked"}\'; exit 0 ;; esac\n'
    'exit 7\n'
)

#: ‏jq אינו מותקן בווינדוס; ‏`json_get` על תשובת `shrink-open` (ok/id) עובר
#: דרך התחליף הצר של test_directsend — במעבדה וב-CI רץ jq אמיתי.
JQ_STUB = '#!/bin/sh\nexec python "' + posix(Path(__file__).parent / "jq_shim.py") + '" "$@"\n'


#: ‏#1466: קליטה עם כיווץ מריצה עכשיו גם את צד העותק בזיכרון (dmsetup, modprobe,
#: ‏rmmod -- כעשרה תהליכים מזויפים). בווינדוס (Git Bash) זה 34-45 שניות לקליטה,
#: נמדד 16/10 -- ממש על התקרה הכללית של 45, ולכן לקליטה תקרה משלה. היא עדיין
#: תקרה: מסלול שנתקע נכשל כאן, לא תלוי לנצח.
CAPTURE_TIMEOUT = 90


def capture_run(tmp_path, *, present=True, image=GPT_DISK, stubs=None,
                disk="sda", env=None, shell_pre="", serial=None,
                raid=False) -> tuple[Path, Path, str]:
    """מריץ את `capture_disk` האמיתי מול דיסק מזויף, ומחזיר (box, run, out).

    ‏`node_is_block` מוחלף כמו בכל שאר בדיקות הסוכן: אין דרך ליצור התקן
    בלוקים בלי root, ובדיקה שמדולגת בחצי מהסביבות היא ירוק בלי ראיה.
    ‏`disk_scheme` לעומת זאת רץ באמת — `dd` על קובץ רגיל קורא בדיוק כמו
    על צומת, ולכן המחרוזת שמגיעה להודעה היא זו שהכלי באמת החזיר.

    שרשרת הטעינה היא זו של `agent/imagesrv-agent`, באותו סדר. ‏`manifest.sh`
    נעדר ממנה עד #338, ובסביבת בדיקה זה כשל **שקט**: ב-POSIX sh פונקציה
    שאינה מוגדרת היא "command not found" ל-stderr, הריצה ממשיכה ומחזירה
    ‏`rc=0`, והמניפסט יוצא עם `os` ריק ו-`partitions` ריק. שני השומרים
    בסוף הקובץ אוכפים את שני הצדדים — השרשרת עצמה והתוצר שלה.
    """
    box = tmp_path / "box"
    dev = box / "dev"
    run = box / "run"
    dev.mkdir(parents=True)
    run.mkdir(parents=True)
    # ‏#1808: שער ה-RAID קורא את 16 הסקטורים האחרונים (ראיה חיובית שהדיסק נקרא)
    # לפי גודלו ב-sysfs — הקופסה מרופדת לפחות לזה, והגודל נחשף כמו בכונן אמיתי.
    image = image + bytes(max(0, 8192 - len(image)))
    (dev / disk).write_bytes(image)
    # ‏#1130: שער ה-BitLocker קורא את הסקטור הראשון של **צומת המחיצה**, וקריאה
    # שנכשלה היא "לא הצלחנו לבדוק" — סירוב, לא ירוק. בקופסה יש קובץ לדיסק
    # בלבד; המחיצות מקבלות כותרת נקייה (512 אפסים), וטסט שרוצה חתימה
    # (‏FVE_SECTOR) דורס אותה ב-shell_pre.
    for idx in range(1, 5):
        (dev / f"{disk}{idx}").write_bytes(bytes(512))
    # ‏`disk_scheme` גוזר את מיקום כותרת ה-GPT מגודל הסקטור הלוגי (#126),
    # ולכן הקופסה חייבת לחשוף אותו כמו כל כונן אמיתי. בלעדיו התשובה היא
    # `unknown` — לא ידענו — וזה מצב אחר מכל ארבעת הסירובים שנבדקים כאן.
    queue = box / "sys/block" / disk / "queue"
    queue.mkdir(parents=True)
    (queue / "logical_block_size").write_text("512\n", encoding="utf-8")
    (box / "sys/block" / disk / "size").write_text(f"{len(image) // 512}\n", encoding="utf-8")
    nodes = box / "nodes"
    nodes.write_text(f"{posix(dev)}/{disk}\n" if present else "",
                     encoding="utf-8", newline="\n")

    # ‏env נכתב **לפני** ה-`.` של capture.sh: כל ה-`${X:-ברירת מחדל}` שבו
    # נגזרים בטעינה, וייצוא שמגיע אחריה אינו משנה דבר.
    extra_env = "".join(f"export {name}={value!r}; "
                        for name, value in (env or {}).items())
    stubs = dict(stubs or {})
    # ‏#1248: בדיקת המקום רצה לפני הכיווץ; טסט בלי curl משלו בודק שער אחר.
    stubs.setdefault("curl", CURL_NO_SERVER)
    if shutil.which("jq") is None and "jq" not in stubs:
        stubs["jq"] = JQ_STUB
    # #1905: a missing blkid is "unreadable" now (the capture refuses), not "unknown".
    # Where it is missing (Windows), the stub answers what the real one says of the
    # box's blank partition files: no TYPE, rc 2.
    if shutil.which("blkid") is None and "blkid" not in stubs:
        stubs["blkid"] = "#!/bin/sh\nexit 2\n"
    out = run_sh(
        make_stubs(box / "stubs", stubs)
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} "
        f"SYSROOT={posix(box)!r} SERVER=http://s; " + extra_env
        # ‏#926: jsonq.sh — shrink_open_record קורא את תשובת השרת (ok/id) ב-json_get.
        + f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; . {posix(AGENT)}/lib/core/jsonq.sh; "
        f". {posix(AGENT)}/lib/core/waits.sh; . {posix(AGENT)}/lib/core/progress.sh; "
        # ‏manifest.sh (#338) ואז bootca.sh, בדיוק כסדר של
        # `imagesrv-agent`: ‏`capture_disk` קורא לשניהם בשורת המניפסט,
        # ופונקציה שאינה טעונה היא מניפסט פגום — לא שגיאה.
        f". {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/capture/manifest.sh; "
        f". {posix(AGENT)}/lib/postdeploy/bootca.sh; . {posix(AGENT)}/lib/capture/hibernation.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; "
        # ‏#87: shrink.sh לפני capture.sh כמו בסוכן; הוא עצמו קורא ל-attended
        # (‏#906) ול-ui.sh, ולכן גם הם כאן — פונקציה שאינה טעונה היא כשל שקט.
        f". {posix(AGENT)}/lib/gui/ui.sh; . {posix(AGENT)}/lib/core/attended.sh; "
        # ‏#926: shrinkmem.sh — הרשומה בשרת לפני הכיווץ; נטען אחרי shrink.sh כמו בסוכן.
        # ‏#929: shrinkplan.sh (המדידה והשאלה) לפניו, shrinkoffer.sh (ההצעה באתחול) אחריו.
        f". {posix(AGENT)}/lib/shrink/shrinkplan.sh; . {posix(AGENT)}/lib/shrink/shrink.sh; . {posix(AGENT)}/lib/localclone/lcsnap.sh; "   # #1466: the RAM view
        f". {posix(AGENT)}/lib/shrink/shrinkmem.sh; . {posix(AGENT)}/lib/shrink/shrinkoffer.sh; "
        f". {posix(AGENT)}/lib/capture/capturedetail.sh; "   # #1505: the console's partition table, loaded before capture.sh like the agent does
        f". {posix(AGENT)}/lib/restore/diskfit.sh; . {posix(AGENT)}/lib/capture/capturegate.sh; "   # #1901/#1900 the source gates; #1808 the RAID check (diskfit.sh)
        f"if [ -f {posix(AGENT)}/lib/capture/captureview.sh ]; then . {posix(AGENT)}/lib/capture/captureview.sh; fi; "   # #2213: negative control can remove the new library
        f". {posix(AGENT)}/lib/capture/capture.sh; . {posix(AGENT)}/lib/restore/writemark.sh; . {posix(AGENT)}/lib/capture/capturesink.sh; "   # #1217
        f". {posix(AGENT)}/lib/restore/readback.sh; . {posix(AGENT)}/lib/localclone/readbacklc.sh; "   # #1805: rb_capture_json (a direct send's digest; nothing here)
        f'node_is_block() {{ grep -qxF "$1" {posix(nodes)!r} 2>/dev/null; }}; '
        # ‏#1808: שער ה-RAID נבדק ב-test_capture_gates.py (‏raid=True); כאן הוא
        # מוחלף כמו node_is_block — קופסאות רבות מזייפות גודל דיסק ב-sysfs.
        + ("" if raid else "raid_member_reason() { :; }; ")
        # ‏shell_pre רץ **אחרי** שרשרת הטעינה, ולכן הוא יכול להחליף
        # פונקציה שנטענה — זה מה שמאפשר לבדוק מה קורה כשהיא מתנהגת רע.
        + shell_pre
        + f"capture_disk {TASK} {disk}{'' if serial is None else ' ' + repr(serial)} > {posix(box)}/capture.out 2>&1; "
        'echo "rc=$?"; '
        f"build_progress '' {MAC} {TASK} > {posix(box)}/progress.json",
        timeout=CAPTURE_TIMEOUT,
    )
    return box, run, out


def refusal_reason(box: Path, run: Path, out: str, disk="sda") -> str:
    """הסיבה **כפי שהקונסולה מקבלת אותה**: משדה `error` של היעד בדיווח
    ההתקדמות, ולא מהיומן. זו כל הנקודה של #106 — היומן נמחק באתחול."""
    assert out.strip().endswith("rc=1"), out
    report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
    assert report["state"] == "failed", report
    targets = report["targets"]
    assert len(targets) == 1, f"היעד היחיד — דיסק המקור — חייב להיות בדיווח: {report}"
    assert targets[0]["dev"] == disk, report
    assert targets[0]["state"] == "failed", report
    reason = targets[0].get("error", "")
    assert reason, (
        "היעד נכשל בלי סיבה — בדיוק ה-failed הריק של #106. "
        f"קובץ השגיאה: {(run / 'targets' / disk / 'error').exists()}"
    )
    return reason


# --- ארבעת הסירובים המבניים ---------------------------------------------------


def test_a_disk_that_is_not_there_says_so(tmp_path):
    """השער הראשון, והיחיד שרץ עוד לפני שנגענו בדיסק."""
    box, run, out = capture_run(tmp_path, present=False)
    reason = refusal_reason(box, run, out)
    assert "sda" in reason, reason
    assert "אין דיסק" in reason, reason


@pytest.mark.parametrize("scheme", ["mbr", "none"])
def test_a_source_disk_that_is_not_gpt_names_what_was_found(tmp_path, scheme):
    """הגדרת ה"גמור" של #106 מילה במילה: לא "capture failed" אלא
    "הכונן אינו GPT (נמצא: mbr)". המגבלה עצמה נשארת — ‏GPT בלבד היא
    החלטת אפיון (‏`docs/imagesrv-spec.md:52`) — הבאג הוא שהיא הגיעה
    ליעד בלי סיבה."""
    box, run, out = capture_run(
        tmp_path, image={"mbr": MBR_DISK, "none": BLANK_DISK}[scheme])
    reason = refusal_reason(box, run, out)
    assert "GPT" in reason, reason
    assert scheme in reason, f"מה שנמצא בפועל אינו בהודעה: {reason}"


def test_a_gpt_disk_without_partitions_says_that_and_not_something_else(tmp_path):
    """כותרת GPT תקינה וטבלה ריקה. זה מצב אחר לגמרי מ"אינו GPT",
    והטכנאי שמקבל את שתי ההודעות עושה שני דברים שונים."""
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": EMPTY_TABLE})
    reason = refusal_reason(box, run, out)
    assert "מחיצות" in reason, reason
    assert "GPT" not in reason, f"הודעת המחיצות מאשימה את הסכימה: {reason}"


def test_a_layout_that_could_not_be_sized_says_it_could_not_be_sized(tmp_path):
    """השער הרביעי: יש מחיצות, והחישוב לא הניב ערך.

    ‏"לא הצלחנו לחשב" אינו "אין מחיצות" ואינו "המחיצה נכשלה" — עיקרון 5
    בדיוק. הסירוב עצמו נכון (בלי רצפת גודל אי אפשר לדעת לאיזה כונן
    האימג' נכנס), ומה שחסר לו היה רק המילה.
    """
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION, "awk": AWK_WITHOUT_AN_ANSWER})
    reason = refusal_reason(box, run, out)
    assert "פריסת" in reason or "לחשב" in reason, reason
    assert "מחיצות" not in reason or "לחשב" in reason, reason


# --- הבקרה השלילית: ארבע סיבות, לא אחת ---------------------------------------


def test_the_refusals_do_not_collapse_into_one_message(tmp_path):
    """מה שאסור לפי ה-Issue במפורש: לקפל אותם למחרוזת אחת.

    טסט שבודק כל שער לחוד היה עובר גם אם כולם היו מחזירים "הקליטה
    נדחתה" — ואז הקונסולה הייתה מציגה סיבה, והמפעיל עדיין לא היה יודע
    מה לעשות. כאן הם נמדדים זה מול זה. שני השערים של #87 נספרים כאן
    מאותה סיבה בדיוק: "הפריסה גדולה מהיעד" ו"הרצפה שנמסרה אינה מספר"
    הם שני מצבים, והשני הוא בדיוק זה שאסור לו להיראות כמו "לא נבדק".
    """
    cases = {
        "missing": dict(present=False),
        "not_gpt": dict(image=MBR_DISK),
        "no_parts": dict(stubs={"sgdisk": EMPTY_TABLE}),
        "no_size": dict(stubs={"sgdisk": ONE_PARTITION,
                               "awk": AWK_WITHOUT_AN_ANSWER}),
        "too_big": dict(stubs={"sgdisk": ONE_PARTITION},
                        env={"CAPTURE_TARGET_BYTES": "1048576"}),
        "bad_floor": dict(stubs={"sgdisk": ONE_PARTITION},
                          env={"CAPTURE_TARGET_BYTES": "256GB"}),
    }
    reasons = {}
    for name, kwargs in cases.items():
        box, run, out = capture_run(tmp_path / name, **kwargs)
        reasons[name] = refusal_reason(box, run, out)

    assert len(set(reasons.values())) == len(cases), reasons


def test_the_reason_also_stays_in_the_log(tmp_path):
    """הדיווח הוא היעד, והיומן נשאר — הוא מה שיש לטכנאי שיושב מול
    המכונה בזמן אמת. ‏#106 אינו "להעביר את הסיבה", הוא "גם לשם"."""
    box, run, out = capture_run(tmp_path, image=MBR_DISK)
    refusal_reason(box, run, out)
    assert "mbr" in log_of(run), log_of(run)


# --- הנעילה: אין `return 1` מבני שאינו עובר דרך היעד --------------------------


def capture_body() -> list[str]:
    """גוף `capture_disk` כ**קוד**: בלי הערות, והמשכי שורה מאוחדים.

    הנעילות שלמטה נשענות על סדר וצמידות, ושתיהן היו נופלות או עוברות
    בטעות על טקסט בהערה או על `\\` בסוף שורה. בדיקת נעילה ששוברת אותה
    הערה חדשה אינה נעילה, היא רעש."""
    source = (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8")
    body = source[source.index("capture_disk() {"):]
    body = body[:body.index("\n}\n")]
    lines, buf = [], ""
    for raw in body.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            continue
        buf = f"{buf} {line}".strip()
        if line.endswith("\\"):
            continue
        cleaned = buf.replace("\\", " ").strip()
        if cleaned:
            lines.append(cleaned)
        buf = ""
    return lines


def first_index(lines: list[str], needle: str) -> int:
    for i, line in enumerate(lines):
        if needle in line:
            return i
    raise AssertionError(f"לא נמצא בקוד: {needle}")


def test_no_structural_refusal_returns_without_writing_a_reason():
    """הדפוס עצמו, ולא רק ארבעת המופעים: כל יציאה מ-`capture_disk` לפני
    הזרם חייבת לעבור דרך פונקציית הכשל המשותפת. בלי הנעילה הזו, השער
    החמישי שייכתב מחר יחזור בדיוק לבאג הזה — וכל ארבעת הטסטים למעלה
    ימשיכו לעבור."""
    lines = capture_body()
    bare = [line for i, line in enumerate(lines)
            if "return 1" in line and "_capture_failed" not in line
            and "_capture_failed" not in (lines[i - 1] if i else "")]
    assert not bare, f"סירוב שלא כותב סיבה ליעד: {bare}"


def test_the_target_is_registered_before_the_first_check():
    """‏`target_set` על יעד שלא נרשם אינו כותב לשום מקום שהקונסולה
    קוראת. ‏`target_init` חייב להיות השורה הראשונה בפועל — שני השערים
    הראשונים רצים לפני שהיה בכלל מה לדווח עליו."""
    lines = capture_body()
    init = first_index(lines, 'target_init "$_disk"')
    assert init < first_index(lines, "node_is_block"), \
        "היעד נרשם אחרי הבדיקה הראשונה — הסיבה של השער הזה תיפול לרצפה"
    assert init < first_index(lines, "disk_scheme"), "היעד נרשם אחרי בדיקת הסכימה"


def test_the_interfaces_document_says_when_the_capture_target_appears():
    """‏`docs/interfaces.md` הוא מקור האמת למה שעובר בין רכיבים. השדה
    `error` לא השתנה — אבל הוא מתמלא עכשיו במצבים חדשים, לפני שנשלח
    בייט אחד, וזה בדיוק מה שהמסמך צריך לומר."""
    doc = Path(__file__).resolve().parent.parent / "docs" / "interfaces.md"
    text = doc.read_text(encoding="utf-8")
    assert "#106" in text, "השינוי בהתנהגות השדה אינו מתועד"


# --- ההרנס עצמו: ספרייה שאינה נטענת היא ירוק בלי בדיקה (#338) -----------------


def test_the_harness_loads_every_lib_that_capture_calls():
    """‏`213dc1f` הוסיף את `test_every_lib_file_is_loaded_and_packed` בדיוק
    בשביל הכשל הזה — אבל הוא בודק את `agent/imagesrv-agent` בלבד. הבקרה
    השלילית שלו מדווחת שכשהוסרה משם שורת הטעינה **כל שאר 1365 הטסטים
    עברו**. אותה עיוורון בדיוק השאירה את `manifest.sh` מחוץ להרנס הזה:
    חמש פונקציות החזירו "command not found" ל-stderr, והטסטים ספרו
    הצלחה (#338).

    לכן הבדיקה **נגזרת** ואינה רשימה: כל ספריית `agent/lib` שמגדירה
    פונקציה ש-`capture.sh` קורא לה בקוד חייבת להיות בשרשרת. ה-lib הבא
    שייחתך מ-`capture.sh` (‏#211) ייתפס כאן ביום שהוא נוצר.
    """
    chain = set(re.findall(r"lib/(?:\w+/)?(\w+\.sh)", inspect.getsource(capture_run)))
    # ההערות יורדות: `ui_clear` מוזכר בהערה ב-capture.sh ואינו נקרא ממנה,
    # והשומר היה דורש את `ui.sh` בשרשרת בלי סיבה.
    body = re.sub(r"(?m)(?<!\S)#.*$", "",
                  (AGENT / "lib" / "capture" / "capture.sh").read_text(encoding="utf-8"))
    missing = {}
    for lib in lib_files(AGENT / "lib"):
        if lib.name in chain:
            continue
        defined = re.findall(r"(?m)^([A-Za-z_][A-Za-z0-9_]*)\(\)",
                             lib.read_text(encoding="utf-8"))
        used = [fn for fn in defined
                if re.search(rf"(?<![\w.]){re.escape(fn)}\b", body)]
        if used:
            missing[lib.name] = used
    assert not missing, \
        f"‏capture.sh קורא לפונקציות מספריות שההרנס אינו טוען: {missing}"


def test_a_successful_capture_produces_a_whole_manifest(tmp_path):
    """הצד השני של אותו שער, על ה**תוצר**: ריצה שנספרת כהצלחה חייבת
    להשאיר מניפסט מלא.

    זה מה שהיה שבור בפועל — ‏`rc=0`, ובמניפסט `"os":""` ו-`"partitions":[]`:
    ‏`os` ריק כי `_image_os` לא היה מוגדר, ו-`partitions` ריק כי
    ‏`$(_mark_expandable ...)` החזיר כלום ובלע את הרשומה שנבנתה לפניו.
    שדה ריק שנראה כמו תשובה הוא עיקרון 5, וכאן הוא ישב בתוך כלי הבדיקה:
    כל טענה עתידית על `role`, ‏`fs`, ‏`used_bytes` או `expandable` הייתה
    ירוקה בלי לבדוק דבר.
    """
    box, run, out = capture_run(
        tmp_path, stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK})
    assert out.strip().endswith("rc=0"), \
        out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    written = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert written["os"], f"‏os ריק על ריצה שנספרה כהצלחה: {written}"
    assert written["partitions"], f"מניפסט בלי מחיצות על ריצה מוצלחת: {written}"
    # התפקיד מגיע מ-`_partition_role` על ה-GUID של `ONE_PARTITION`. מחרוזת
    # ריקה הייתה עוברת כל טענה שמסתפקת בקיום המפתח.
    assert written["partitions"][0]["role"] == "esp", written["partitions"]


# --- #776: כשל אמצע-צנרת אינו נבלע ------------------------------------------

PIPE_INPUT = "#!/bin/sh\nhead -c 4096 /dev/zero\nexit 0\n"

MIDPIPE_FAILURES = [
    ("zstd", "#!/bin/sh\ncat\nexit 7\n", 7),
    # ‏#1438: fanout במקום pv|tee|sha256sum — מעביר הכול, מגבב, ויוצא 9.
    (
        "fanout",
        "#!/bin/sh\n"
        "shift\n"
        'tmp="$(mktemp)"\n'
        'cat > "$tmp"\n'
        'fanout_eof "$tmp"\n'
        'cat "$tmp" > "$1"\n'
        'rm -f "$tmp"\n'
        "exit 9\n",
        9,
    ),
]


@pytest.mark.parametrize(("stage", "stub", "expected_rc"), MIDPIPE_FAILURES)
def test_a_mid_pipeline_capture_failure_is_not_ingested(
        tmp_path, stage, stub, expected_rc):
    """zstd/fanout (לפני #1438: pv/tee) שנפל אחרי שהזרים את כל הקלט היה נבלע: ב-ash אין
    pipefail, ו-sha256sum יוצא 0 על קלט חלקי. האימג' הקטוע נרשם תקין."""
    box, run, out = capture_run(
        tmp_path,
        stubs={
            "sgdisk": ONE_PARTITION,
            "curl": CURL_SINK,
            "partclone.dd": PIPE_INPUT,
            stage: stub,
        },
    )

    reason = refusal_reason(box, run, out)
    assert f"שלב={stage}" in reason, reason
    assert f"rc={expected_rc}" in reason, reason
    assert (run / f"{stage}.1.rc").read_text().strip() == str(expected_rc)
    assert not (run / "new-manifest.json").exists()


def test_capture_manifest_uses_source_logical_sector_size(tmp_path):
    sector_size = 4096
    source = bytes(sector_size) + b"EFI PART" + bytes(sector_size - 8)
    logical_size = (
        tmp_path / "box/sys/block/sda/queue/logical_block_size"
    )

    box, run, out = capture_run(
        tmp_path,
        image=source,
        stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK},
        shell_pre=(
            f"printf '4096\\n' > {posix(logical_size)!r}; "
            # ‏#1900: מקור 4Kn נדחה עכשיו לפני הבייט הראשון (test_capture_gates).
            # הטסט הזה שומר על חישוב המניפסט לפי גודל הסקטור, ולכן עוקף את השער.
            "capture_source_reason() { :; }; "
        ),
    )

    assert out.strip().endswith("rc=0"), (
        out + (box / "capture.out").read_text(
            encoding="utf-8", errors="replace"
        )
    )
    written = json.loads(
        (run / "new-manifest.json").read_text(encoding="utf-8")
    )
    part = written["partitions"][0]
    expected_end = (2048 + 204800) * sector_size
    expected_need = expected_end + 262144  # #1171: גיבוי ה-GPT, בלי עיגול

    assert written["sector_size"] == sector_size
    assert part["size_bytes"] == 204800 * sector_size
    assert written["min_target_bytes"] == expected_need


# --- #671: כרך BitLocker אינו נקלט כ-ciphertext ------------------------------

#: util-linux probe_bitlocker.c / FOG isBitlockedPartition: ‎-FVE-FS-‎
#: ב-offset 3 של הסקטור הראשון. בלי המגן, `_fs_of` מחזיר unknown/BitLocker
#: ו-`partclone_for_fs` נופל ל-`partclone.dd` — קליטת ההצפנה בשקט.
FVE_SECTOR = b"\xeb\x52\x90-FVE-FS-" + bytes(501)


def test_a_bitlocker_signature_is_refused_not_captured_as_ciphertext(tmp_path):
    """חתימת BitLocker על צומת המחיצה — סירוב עם סיבה, לא partclone.dd.

    הבקרה השלילית: החזרת `capture.sh` לייצור בלי `_bitlocker_reason`
    מחזירה rc=0 ומניפסט, כי CURL_SINK + partclone.dd בולעים את הכרך.
    """
    fve = tmp_path / "fve.bin"
    fve.write_bytes(FVE_SECTOR)
    part = tmp_path / "box" / "dev" / "sda1"
    box, run, out = capture_run(
        tmp_path,
        stubs={"sgdisk": ONE_PARTITION, "curl": CURL_SINK},
        shell_pre=f"cp {posix(fve)!r} {posix(part)!r}; ",
    )
    reason = refusal_reason(box, run, out)
    assert "BitLocker" in reason, reason
    assert "1" in reason, reason
    assert not (run / "new-manifest.json").exists()
