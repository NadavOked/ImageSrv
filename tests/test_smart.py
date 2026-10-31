"""בדיקות שער ה-SMART בצד השחזור (#652) -- כמו Clonezilla.

הסוכן בודק בריאות דיסק יעד לפני כתיבה. הכלל שאסור לשבור (עיקרון 5):
**‏`unchecked` לעולם לא חוסם** -- דיסק בלי SMART / USB / RAID / probe
שנכשל לפתוח נכתב בלי שאלה, כי "לא הצלחנו לבדוק" איננו "בדקנו ונכשל".

‏smartctl אמיתי אינו זמין (ואין דיסקים פגומים במעבדה), ולכן `smartctl`
מזויף: סקריפט שפולט פלט קנוני וקוד יציאה נבחר -- בדיוק הדפוס של
"לזייף שלב אחד ולהריץ את הפונקציה האמיתית".
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"


def find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (
            r"C:\Program Files\Git\usr\bin\bash.exe",
            r"C:\Program Files\Git\bin\bash.exe",
        ):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = find_bash()
pytestmark = requires_native(("bash", BASH))


def posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def sh(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
        capture_output=True, text=True, cwd=str(REPO),
        stdin=subprocess.DEVNULL,
    )


# הרכיבים ש-smart.sh נשען עליהם, וחסימת ה-HTTP כך שהבדיקה אינה נוגעת ברשת.
PRELUDE = (
    f'. {posix(AGENT)}/lib/core/common.sh; '
    f'. {posix(AGENT)}/lib/core/waits.sh; '   # #1367: capped_run, the smartctl ceiling
    f'. {posix(AGENT)}/lib/hw/sysinfo.sh; '
    f'. {posix(AGENT)}/lib/core/jsonq.sh; '
    f'. {posix(AGENT)}/lib/gui/ui.sh; '
    f'. {posix(AGENT)}/lib/core/progress.sh; '
    f'. {posix(AGENT)}/lib/restore/failmark.sh; '   # disk_failure_cause — זיכרון השרת בשער (#872/#874)
    f'. {posix(AGENT)}/lib/smart/smart.sh; '
    f'. {posix(AGENT)}/lib/smart/smartgate.sh; '   # #1211: השער (smart_preflight/smart_gate) פוצל מ-smart.sh
    f'. {posix(AGENT)}/lib/gui/clonergui.sh; . {posix(AGENT)}/lib/restore/writemark.sh; '   # gui_smart_choice — כמו בסוכן האמיתי
    # ‏#906: hello בזמן ההמתנה לאדם. **רק אם הקובץ קיים**: הבקרה השלילית
    # מחזירה את main, ושם הטסט חייב ליפול על ההתנהגות ולא על `.` של קובץ חסר.
    + (f'. {posix(AGENT)}/lib/core/attended.sh; ' if (AGENT / "lib" / "core" / "attended.sh").exists() else "")
    # ‏#1859: every attribute and the orange -- the same "only if it exists", for the same reason.
    + (f'. {posix(AGENT)}/lib/smart/smartattrs.sh; ' if (AGENT / "lib" / "smart" / "smartattrs.sh").exists() else "")
    # #1921: the knowledge tables -- the same "only if it exists": without them smart_probe judges
    # nothing and says so (unchecked no_table), which is the negative control's failure on behaviour.
    + (f'export SMART_KB={posix(flat_lib())}/data; . {posix(AGENT)}/lib/smart/smartjudge.sh; . {posix(AGENT)}/lib/smart/hwissues.sh; '
       if (AGENT / "lib" / "smart" / "smartjudge.sh").exists() else "")
    + 'http_post_json() { return 0; }; '   # אין רשת בבדיקה
)


def env(run: Path, dev: Path, sysroot: Path | None = None, smartctl: str = "") -> str:
    out = (
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} '
        f'IMAGESRV_TEST=1 MAC=aa:bb:cc:dd:ee:ff SERVER=http://x; '
    )
    if sysroot is not None:
        out += f'export SYSROOT={posix(sysroot)!r}; '
    if smartctl:
        out += f'export SMARTCTL={smartctl!r}; '
    return out


def make_smartctl(tmp_path: Path, body: str, rc: int = 0) -> str:
    """‏smartctl מזויף: מדפיס `body`, יוצא ב-`rc`.

    ‏rc הוא מפת-סיביות אמיתית: bit 3 (8) = הדיסק נכשל, וזה קוד היציאה
    ש-smartctl אמיתי מחזיר על `-H` שראה FAILED. הפונקציה שנבדקת חייבת
    לקרוא את הפלט, לא להסיק מ-rc."""
    p = tmp_path / "smartctl"
    p.write_text("#!/bin/sh\ncat <<'EOF'\n" + body + "\nEOF\nexit " + str(rc) + "\n",
                 newline="\n")
    # chmod דרך bash: ‏os.chmod של פייתון על ווינדוס אינו קובע את סיבית
    # ה-exec ש-Git Bash קורא, ו-`command -v` לא היה מוצא את הקובץ.
    subprocess.run([BASH, "-c", f'chmod +x {posix(p)!r}'], check=True,
                   stdin=subprocess.DEVNULL)
    return posix(p)


HEALTH_OK = "SMART overall-health self-assessment test result: PASSED"
HEALTH_FAIL = "SMART overall-health self-assessment test result: FAILED"


def attrs(realloc=0, pending=0, uncorr=0, crc=0) -> str:
    return (
        "ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      "
        "UPDATED  WHEN_FAILED RAW_VALUE\n"
        f"  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  "
        f"Always       -       {realloc}\n"
        f"197 Current_Pending_Sector  0x0032   100   100   000    Old_age   "
        f"Always       -       {pending}\n"
        f"198 Offline_Uncorrectable   0x0030   100   100   000    Old_age   "
        f"Offline      -       {uncorr}\n"
        f"199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   "
        f"Always       -       {crc}\n"
    )


def full(health: str, **kw) -> str:
    return health + "\n\n" + attrs(**kw)


# --- smart_classify: פונקציה טהורה, שורה-שורה -------------------------------

CLASSIFY = [
    # health, realloc, pending, uncorr, crc -> "verdict reason"
    # ‏#872: שורת הבריאות (‏-H) לבדה מכריעה. המונים 5/197/198/199 נקראים
    # ונשמרים (מידע), ואינם צובעים: על 6 דיסקי הכיתה CRC הוא 137–55,242
    # (15/09) והם משכפלים מושלם. מונה מצטבר מספר על הכבלים, לא על הדיסק.
    ("PASSED", 0, 0, 0, 0, "ok ok"),
    ("FAILED", 0, 0, 0, 0, "fail health_failed"),
    # ‏#1921 (נדב 20/10, סופי): אדום = הדיסק עצמו מדווח FAILED. ‏197/198 מעל 0 הם כתום מטבלת
    # הידע (smartjudge.sh) -- לא כאן: שורת הבריאות לבדה מכריעה את הסיווג הזה.
    ("PASSED", 0, 0, 1, 0, "ok ok"),
    ("PASSED", 0, 4, 0, 0, "ok ok"),
    ("PASSED", 0, 4, 2, 0, "ok ok"),
    ("PASSED", 2, 0, 0, 0, "ok ok"),                # reallocated אינו צובע
    ("PASSED", 0, 0, 0, 55242, "ok ok"),            # CRC — מחשב 2 דיסק 2, 15/09
    ("unknown", 0, 0, 0, 0, "unchecked no_health"), # לא PASSED ולא FAILED
    ("unknown", 0, 0, 1, 0, "unchecked no_health"), # מונה רע אינו תחליף לשורת בריאות
    ("FAILED", 0, 0, 0, 55242, "fail health_failed"),
]


@pytest.mark.parametrize("health,re,pe,un,cr,expected", CLASSIFY)
def test_classify(health, re, pe, un, cr, expected):
    out = sh(PRELUDE + f'smart_classify {health} {re} {pe} {un} {cr}')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == expected


# --- smart_probe: מפלט smartctl אמיתי אל verdict ----------------------------

def probe(tmp_path, body, rc=0, dev="sda"):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev_dir = tmp_path / "dev"; dev_dir.mkdir(exist_ok=True)
    (dev_dir / dev).write_bytes(b"\x00")
    # ‏#1742: ‏/sys/block/<d>/ro נקרא לפני smartctl, ו-ro שלא נקרא אינו "תקין" --
    # הדיסק כאן כתיב (ro=0), כדי שהבדיקה תהיה על smartctl בלבד.
    blk = tmp_path / "sysro" / "sys" / "block" / dev; blk.mkdir(parents=True, exist_ok=True)
    (blk / "ro").write_text("0\n", newline="\n")
    sc = make_smartctl(tmp_path, body, rc)
    out = sh(env(run, dev_dir, sysroot=tmp_path / "sysro", smartctl=sc) + PRELUDE + f'smart_probe {dev}')
    assert out.returncode == 0, out.stderr
    return out.stdout.strip()


def test_probe_healthy_disk_is_ok(tmp_path):
    assert probe(tmp_path, full(HEALTH_OK)) == "ok"


def test_probe_failed_health_is_fail_even_with_nonzero_exit(tmp_path):
    # rc=8 הוא bit 3 -- הדיסק נכשל. זו תשובה, לא כשל פתיחה.
    assert probe(tmp_path, full(HEALTH_FAIL), rc=8) == "fail"


def test_smart_cache_is_invalidated_when_device_name_has_a_new_serial(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sdb").write_bytes(b"\x00")
    blk = tmp_path / "sysro/sys/block/sdb"; (blk / "device").mkdir(parents=True)
    (blk / "ro").write_text("0\n", newline="\n")
    serial = blk / "device/serial"; serial.write_text("SERIAL-A\n", newline="\n")
    sc = tmp_path / "smartctl"
    sc.write_text(
        "#!/bin/sh\n"
        f'case "$(cat {posix(serial)!r})" in *B*) h=FAILED; rc=8;; *) h=PASSED; rc=0;; esac\n'
        "printf 'SMART overall-health self-assessment test result: %s\\n" + attrs().replace("'", "'\\''") + "' \"$h\"\n"
        "exit \"$rc\"\n", newline="\n")
    subprocess.run([BASH, "-c", f'chmod +x {posix(sc)!r}'], check=True,
                   stdin=subprocess.DEVNULL)
    prefix = env(run, dev, sysroot=tmp_path / "sysro", smartctl=posix(sc)) + PRELUDE
    first = sh(prefix + "smart_probe sdb")
    assert first.stdout.strip() == "ok", first.stderr
    serial.write_text("SERIAL-B\n", newline="\n")
    second = sh(prefix + "smart_probe sdb")
    assert second.stdout.strip() == "fail", second.stderr
    assert (run / "smart/sdb.serial").read_text().strip() == "SERIAL-B"


def test_probe_pending_sectors_are_orange_and_recorded(tmp_path):
    # ‏#1921 (נדב 20/10): 197 מעל 0 = כתום (סימן מוקדם, הדיסק עובד), לא אדום; הערך נשמר במטמון.
    assert probe(tmp_path, full(HEALTH_OK, pending=4)) == "warn"
    assert (tmp_path / "run/smart/sda.v").read_text().split() == ["warn", "pending_sectors", "0", "4", "0", "0"]


def test_probe_reallocated_is_orange_and_recorded(tmp_path):
    """#1859 (Nadav 20/10) replaces #872 for 5: over 0 is orange (`warn reallocated`) -- asked,
    and written without an answer. Not red."""
    assert probe(tmp_path, full(HEALTH_OK, realloc=8)) == "warn"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[:3] == ["warn", "reallocated", "8"]


def test_probe_high_crc_is_ok_and_recorded(tmp_path):
    # 55,242 — מחשב 2 (.59) דיסק 2, המעבדה 15/09; שיכפל 20GB מושלם.
    assert probe(tmp_path, full(HEALTH_OK, crc=55242)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[5] == "55242"


def test_probe_crc_is_found_by_attribute_id_not_vendor_name(tmp_path):
    # #866: סמסונג מדפיסה 199 בשם CRC_Error_Count; לפי שם הוא נקרא 0 והדיסק
    # ירוק. השורה — מחשב 2 (.59) דיסק 2, המעבדה 15/09: 55,242 שגיאות CRC.
    samsung = attrs().replace(
        "199 UDMA_CRC_Error_Count    0x003e   200   200   000    Old_age   "
        "Always       -       0",
        "199 CRC_Error_Count         0x003e   045   045   000    Old_age   "
        "Always       -       55242")
    assert "UDMA" not in samsung
    # ‏#872: הערך עדיין חייב להיקרא נכון (55242 במטמון) — רק לא לצבוע.
    assert probe(tmp_path, full(HEALTH_OK).replace(attrs(), samsung)) == "ok"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[5] == "55242"


def test_probe_open_failure_is_unchecked_not_fail(tmp_path):
    # rc=2 = bit 1 = פתיחת ההתקן נכשלה (גם אחרי -d sat). unchecked, נכתב.
    assert probe(tmp_path, "Smartctl open device failed", rc=2) == "unchecked"


def test_probe_device_without_smart_is_unchecked(tmp_path):
    assert probe(tmp_path, "SMART support is: Unavailable") == "unchecked"


def test_probe_without_smartctl_is_unchecked(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent/smartctl") + PRELUDE
             + 'smart_probe sda')
    assert out.stdout.strip() == "unchecked"


# --- #858: ניסוחי בריאות שאינם PASSED/FAILED, קוד היציאה, ו-NVMe ---------------

#: ‏SCSI/SAS (וגשרי USB מסוימים): smartctl מדפיס `SMART Health Status:` ולא
#: `overall-health`, ובכשל — תיאור ה-asc/ascq של החיישן (5d/00), לא המילה
#: FAILED. הגוף — מבנה הפלט של smartctl 7.4 ל-`-H -A` על דיסק SAS; אין SAS
#: במעבדה, ולכן הטקסט מטבלת התיאורים של smartmontools ולא מצילום.
SCSI_FAILURE = (
    "=== START OF READ SMART DATA SECTION ===\n"
    "SMART Health Status: FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]\n"
    "\n"
    "Current Drive Temperature:     34 C\n"
    "Drive Trip Temperature:        65 C\n"
    "\n"
    "Accumulated power on time, hours:minutes 42154:23\n"
    "Elements in grown defect list: 12\n"
    "\n"
    "Error counter log:\n"
    "           Errors Corrected by           Total   Correction     Gigabytes    Total\n"
    "               ECC          rereads/    errors   algorithm      processed    uncorrected\n"
    "           fast | delayed   rewrites  corrected  invocations   [10^9 bytes]  errors\n"
    "read:          0        0         0         0          0     123456.789           0\n"
    "write:         0        0         0         0          0      98765.432           0\n"
)
SCSI_OK = SCSI_FAILURE.replace(
    "FAILURE PREDICTION THRESHOLD EXCEEDED [asc=5d, ascq=0]", "OK")


def nvme(critical: str, media_errors: int) -> str:
    """‏smartctl 7.x על NVMe: `Critical Warning != 0` הופך את שורת הבריאות
    ל-`FAILED!` (bit 3 = read-only → "media has been placed in read only
    mode"), והמונים הם שורות `שם:    ערך` עם רווחים ופסיקי-אלפים — לא
    הטבלה של ATA. אין NVMe במעבדה: המבנה לפי nvmeprint.cpp, לא מצילום."""
    failed = critical != "0x00"
    return (
        "=== START OF SMART DATA SECTION ===\n"
        "SMART overall-health self-assessment test result: "
        + ("FAILED!\n- media has been placed in read only mode\n" if failed else "PASSED\n")
        + "\n"
        "SMART/Health Information (NVMe Log 0x02)\n"
        f"Critical Warning:                   {critical}\n"
        "Temperature:                        38 Celsius\n"
        "Available Spare:                    100%\n"
        "Available Spare Threshold:          10%\n"
        "Percentage Used:                    3%\n"
        "Data Units Read:                    12,345,678 [6.32 TB]\n"
        "Data Units Written:                 9,876,543 [5.05 TB]\n"
        "Power Cycles:                       784\n"
        "Power On Hours:                     5,432\n"
        "Unsafe Shutdowns:                   61\n"
        f"Media and Data Integrity Errors:    {media_errors:,}\n"
        "Error Information Log Entries:      12\n"
    )


def test_scsi_health_failure_is_fail(tmp_path):
    """‏#858: `FAILURE` אינו `FAILED` — על main הדיסק היה `unchecked no_health`
    ונכתב בלי שאלה. rc=8 (bit 3) כמו ש-smartctl אמיתי מחזיר."""
    assert probe(tmp_path, SCSI_FAILURE, rc=8) == "fail"
    assert (tmp_path / "run/smart/sda.v").read_text().split()[:2] == ["fail", "health_failed"]


def test_scsi_health_ok_is_ok(tmp_path):
    assert probe(tmp_path, SCSI_OK) == "ok"


def test_exit_bit_3_alone_is_fail_when_the_health_line_is_not_recognised(tmp_path):
    """‏smartctl אמר "הדיסק נכשל" (bit 3) בניסוח ששורת הבריאות שלנו אינה
    מכירה — הסיבית היא ראיה חיובית (עיקרון 5), לא `unchecked`."""
    body = "SMART Health Status: UNKNOWN SENSE [asc=5d, ascq=ff]\n\n" + attrs()
    assert probe(tmp_path, body, rc=8) == "fail"


def test_exit_bit_3_does_not_override_a_clean_open_failure(tmp_path):
    # rc=10 = bit 1 + bit 3: ההתקן לא נפתח — אין מה לקרוא, unchecked.
    assert probe(tmp_path, "Smartctl open device failed", rc=10) == "unchecked"


def test_nvme_read_only_media_is_fail_and_integrity_errors_are_recorded(tmp_path):
    """‏#858: Critical Warning 0x08 (read-only) → FAILED! + rc=8 → fail; ומונה
    `Media and Data Integrity Errors` (המקבילה של 198) נקרא לאחסון — על main
    ‏`_smart_int` חיפש אותו כעמודה בטבלה ורשם 0 תמיד. ‏#872: המונה אינו מכריע."""
    assert probe(tmp_path, nvme("0x08", 3), rc=8, dev="nvme0n1") == "fail"
    assert (tmp_path / "run/smart/nvme0n1.v").read_text().split() == \
        ["fail", "media_readonly", "0", "0", "3", "0"]   # #1921 ס1: read-only is red, and says so


def test_nvme_healthy_with_integrity_errors_is_orange_and_the_counter_is_kept(tmp_path):
    # פסיקי-אלפים (1,234) — כך smartctl מדפיס מונים ב-NVMe. ‏#1921 (נדב 20/10, המלצה 10): מעל 0 = כתום.
    assert probe(tmp_path, nvme("0x00", 1234), dev="nvme0n1") == "warn"
    assert (tmp_path / "run/smart/nvme0n1.v").read_text().split()[1] == "media_errors"
    assert (tmp_path / "run/smart/nvme0n1.v").read_text().split()[4] == "1234"


# --- הכלל הקריטי: unchecked לעולם לא חוסם -----------------------------------

def test_unchecked_disk_is_written_without_asking(tmp_path):
    """דיסק שלא ניתן לבדוק חוזר ברשימת הכתיבה, ואף מקש לא נצרך.

    זו בקרה שלילית מובנית: ה-stdin ריק (`</dev/null`). אילו הפונקציה
    שאלה על `unchecked`, היא הייתה נחסמת/מקבלת EOF -- והדיסק לא היה
    ברשימת הכתיבה. הופעתו שם היא ההוכחה החיובית שלא נשאלה שאלה."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent") + PRELUDE
             + 'smart_preflight sess1 sda')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "sda"


# --- התפריט על fail/warn: החלטות בתור ---------------------------------------

#: ‏sgdisk מזויף לשער: רושם בלבד. עד #874 השער קרא ממנו את הסימון של #845
#: (‏`sgdisk -p`); מאז הזיכרון בשרת, ו-`sgdisk.calls` חייב להישאר ריק.
GATE_SGDISK = """#!/bin/sh
printf '%s\\n' "$*" >> "{box}/sgdisk.calls"
exit 0
"""


def fake_memory(failed: dict[str, str]) -> str:
    """זיכרון השרת (#874) כפי ש-jq היה מדפיס אותו מתשובת ה-hello: "ok" ואז
    serial|port|cause לכל דיסק ב-`failed` (dev → cause). הסידורי מוזרק
    (‏S-<dev>) — אין sysfs אמיתי בבדיקה. ‏jq עצמו: test_failmark (requires_jq)."""
    lines = " ".join(f"echo 'S-{dev}||{cause}';" for dev, cause in failed.items())
    return ('disk_serial() {{ printf "S-%s" "$1"; }}; '
            'json_get_join() {{ [ -s "$1" ] || return 1; echo ok; {lines} }}; ').format(lines=lines)


#: ‏11/10 (GUI בלבד): תפריט ה-SMART בטקסט הוסר, וההכרעה מגיעה **רק** מהמסך
#: הגרפי (‏gui_smart_choice). הזיוף כאן הוא הגבול הזה: תור תשובות בקובץ,
#: אחת לכל שאלה, וכל שאלה נרשמת ב-`asked` (הראיה החיובית שנשאלה). תור ריק
#: = אין גואי (return 1) — ואז ברירת "אין תשובה" של הצבע.
GUI_STUB = (
    'gui_smart_choice() { echo "$1" >> "$RUN_DIR/asked"; '
    '_q=$(sed -n 1p "$RUN_DIR/gui_answers" 2>/dev/null); '
    '[ -n "$_q" ] || return 1; '
    'sed -n \'2,$p\' "$RUN_DIR/gui_answers" > "$RUN_DIR/gui_answers.n"; '
    'mv "$RUN_DIR/gui_answers.n" "$RUN_DIR/gui_answers"; echo "$_q"; }; '
)


def preflight(tmp_path, body, gui=(), disks="sda", failed=(), extra="", keys="1\n"):
    """‏`gui` = התשובות שהמסך הגרפי נותן, לפי הסדר. ‏`keys` הם מקשים שמחכים
    ב-stdin — הם **אינם** נקראים יותר (בקוד הישן "1" היה "החלף")."""
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    dev = tmp_path / "dev"; dev.mkdir(exist_ok=True)
    box = tmp_path / "box"; (box / "bin").mkdir(parents=True, exist_ok=True)
    # #2158: a sysfs of our own with ro=0 for each disk. Without it smart_ro_check read the
    # HOST's /sys/block/<d>/ro: on the lab VM sda exists (ro=0, the gate saw "ok"); where it
    # cannot be read (Windows, measured; the public runner fails the same four tests) the
    # record was `unchecked ro_unread` and what the server learned (#1859) was never consulted.
    sysro = tmp_path / "sysro"
    for d in disks.split():
        (dev / d).write_bytes(b"\x00")
        blk = sysro / "sys" / "block" / d; blk.mkdir(parents=True, exist_ok=True)
        (blk / "ro").write_text("0\n", newline="\n")
    # ‏#874: `failed` הוא dev → cause (מחרוזת = cable); הרשומה מגיעה מהשרת.
    failed = {d: "cable" for d in failed} if not isinstance(failed, dict) else failed
    (run / "response.json").write_text('{"schema":1}', newline="\n")
    (run / "gui_answers").write_text("".join(f"{a}\n" for a in gui), newline="\n")
    extra = fake_memory(failed) + GUI_STUB + extra
    (box / "bin" / "sgdisk").write_text(GATE_SGDISK.format(box=posix(box)), newline="\n")
    sc = make_smartctl(tmp_path, body)
    keys_printf = keys.replace("\n", "\\n")
    out = sh(f'chmod +x {posix(box)}/bin/sgdisk; export PATH="$(cd {posix(box)}/bin && pwd):$PATH"; '
             + env(run, dev, sysroot=sysro, smartctl=sc) + PRELUDE + extra
             + f"printf '{keys_printf}' | smart_preflight sess1 {disks}")
    assert out.returncode == 0, out.stderr
    return out.stdout.strip(), run, out.stderr


def asked(run: Path) -> list[str]:
    f = run / "asked"
    return f.read_text().split() if f.exists() else []


def test_skip_from_the_screen_no_longer_removes_an_orange_disk(tmp_path):
    # ‏#2042 (נדב 23/10): כתום אינו נשאל -- "דלג" של המסך אינו נקרא, והדיסק נכתב.
    # (עד 23/10: נשאל, ו"דלג" הוריד אותו מהכתיבה.)
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), gui=["skip"], failed=("sda",))
    assert written == "sda"
    assert not (run / "targets/sda/state").exists()
    assert asked(run) == []


def test_rescue_keeps_the_disk(tmp_path):
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), gui=["rescue"], failed=("sda",))   # #1732: orange
    assert written == "sda"
    assert asked(run) == []   # #2042: written without asking


def test_a_disk_with_bad_cable_counters_but_passed_health_is_written_without_asking(tmp_path):
    """‏#872: 199 אינו עוצר. הראיה החיובית: לא נשאלה שאלה (‏`asked` ריק) והדיסק
    ברשימת הכתיבה. (‏5 — מאז #1859 כתום: נשאל, ובלי תשובה נכתב — test_smart_full_1859.)"""
    written, run, err = preflight(tmp_path, full(HEALTH_OK, crc=55242))
    assert written == "sda"
    assert asked(run) == [], err


@pytest.mark.parametrize("pending, uncorr", [(4, 0), (0, 2)])
def test_197_198_are_orange_and_written_without_a_question(tmp_path, pending, uncorr):
    """‏#1921 (נדב 20/10, סופי): אדום הוא רק FAILED של הדיסק עצמו (ה-F1). ‏197/198 מעל 0 בדיסק
    ש-PASSED הם **כתום** — #2042 (נדב 23/10): נכתב בלי שאלה (עד 23/10 נשאל ובלי תשובה נכתב;
    עד 20/10 -- #1366/#1732 -- אדום ומדולג)."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK, pending=pending, uncorr=uncorr))
    assert written == "sda", err
    assert asked(run) == [], err
    assert (run / "smart/sda.v").read_text().split()[:2] == ["warn", "uncorrectable" if uncorr else "pending_sectors"]


def test_replace_from_the_screen_is_never_read_on_orange(tmp_path):
    # #2042: orange is not asked -- the screen's "replace" is never read, nothing powers off.
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), gui=["replace"], failed=("sda",))   # #1732: orange
    assert written == "sda"
    assert not (run / "smart/awaiting_replace").exists()
    assert not (run / "targets/sda/state").exists()


def test_without_a_gui_the_keyboard_is_never_read(tmp_path):
    """‏11/10: אין תפריט טקסט. "1" שמחכה ב-stdin היה "החלף" בקוד הישן;
    עכשיו — ברירת הכתום (כותב; ‏#1732: אדום אינו נשאל בכלל), והמקש נשאר במקומו."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), keys="1\n", failed=("sda",))
    assert written == "sda"
    assert not (run / "smart/awaiting_replace").exists()
    assert "Choose [" not in err


def test_a_failing_disk_does_not_stop_a_healthy_sibling(tmp_path):
    # #2042: מגירה כתומה (נכשלה בשיכפול הקודם) נכתבת בלי שאלה, והשכנה איתה -- שום דבר לא עוצר.
    # (אדום שמדולג ליד שכן שנכתב: test_red_disk_never_target_1732.)
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), gui=["skip"],
                                disks="sda sdb", failed=("sda",))
    assert written == "sda sdb"
    assert asked(run) == []


# --- #872: דיסק אדום (נכשל בשיכפול הקודם) — החלף או המשך (=דלג), לא רק צבע ---

def test_a_disk_that_failed_the_previous_clone_is_written_with_its_note(tmp_path):
    """‏#872 (נדב 16/09): זיכרון השרת (#874: לפי סידורי) גובר על SMART נקי.
    #2042 (נדב 23/10): הדיסק הכתום נכתב בלי שאלה, וההערה עם הסיבה שהשרת סיווג
    (cable) נשמרת לסוף; השכן הבריא נכתב בלי הערה. ‏sgdisk אינו נקרא."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), gui=["skip"],
                                  disks="sda sdb", failed=("sda",))
    assert written == "sda sdb"
    assert not (run / "targets/sda/state").exists()
    assert (run / "targets/sda/smart_note").read_text(encoding="utf-8") == "נכשל בשיכפול הקודם — סיבה: כבל/חריץ"   # #1861: no raw word
    assert not (run / "targets/sdb/smart_note").exists()
    assert not (tmp_path / "box" / "sgdisk.calls").exists()
    assert asked(run) == [], err


def test_a_disk_the_server_blamed_is_written_without_a_swap(tmp_path):
    # #2042: failed_last (cause: disk) is orange -- not asked, so no "replace" and no power-off.
    written, run, _ = preflight(tmp_path, full(HEALTH_OK), gui=["replace"],
                                failed={"sda": "disk"})
    assert written == "sda"
    assert not (run / "smart/awaiting_replace").exists()
    assert (run / "targets/sda/smart_note").read_text(encoding="utf-8") == "נכשל בשיכפול הקודם — סיבה: הדיסק"


def test_a_red_disk_by_slot_is_red_even_with_a_new_serial(tmp_path):
    """‏#874: הרשומה לפי החריץ (מכונה+פורט) — הדיסק הוחלף, הכבל לא. הסידורי
    כאן אחר (S-sda מול הרשומה על X), והחריץ 1 תואם → אדום, כמו לפי סידורי."""
    written, run, err = preflight(
        tmp_path, full(HEALTH_OK), gui=["skip"],
        extra='disk_port() { printf 1; }; json_get_join() { echo ok; echo "X|1|cable"; }; ')
    assert written == "sda"   # #2042: orange, written without asking
    assert "נכשל בשיכפול הקודם — סיבה: כבל/חריץ" in (run / "targets/sda/smart_note").read_text(encoding="utf-8")   # #1861: no raw word
    assert asked(run) == [], err


def test_the_memory_is_refreshed_from_the_last_hello_before_the_gate(tmp_path):
    """השער קורא את זיכרון השרת מתשובת ה-hello **האחרונה**: רשומה שנוקתה
    בקונסולה (איננה בתשובה) אינה צובעת עוד. הקובץ הישן מהאתחול נדרס."""
    run = tmp_path / "run"; run.mkdir()
    (run / "disk_failures").write_text("ok\nS-sda||cable\n", newline="\n")
    written, run, err = preflight(tmp_path, full(HEALTH_OK))   # התשובה: בלי כשלים
    assert written == "sda"
    assert asked(run) == [], err


def event(run: Path) -> dict:
    """ה-disk_event האחרון שהשער שלח — ממנו השרת רושם `disk_decision`."""
    return json.loads((run / "disk_event.json").read_text(encoding="utf-8"))


def test_rescue_on_a_red_disk_writes_it(tmp_path):
    """ברזל 17/10 (מחשב שיכפול 2, מגירה 2 = sdb, failed_last): המפעיל ענה
    "rescue" (‏`smart_answered … rescue machine`), והשער הנמיך את התשובה
    בשקט ל-skip (‏`disk_decision … sdb (failed_last): skip`) — אף מגירה לא
    הצטרפה והשליחה נכשלה אחרי 180 ש'. תשובה מפורשת מבוצעת כמות שהיא."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), gui=["rescue"],
                                  disks="sda sdb", failed=("sdb",))
    assert written == "sda sdb", err
    assert asked(run) == [], err                   # #2042: orange is not asked
    assert not (run / "targets/sdb/state").exists()   # לא "skipped" ולא "replacing"
    ev = event(run)
    assert (ev["disk"], ev["smart"]["verdict"], ev["decision"], ev["write_state"]) == \
        ("sdb", "failed_last", None, "pending"), ev   # #2042: nobody decided


def test_no_answer_on_a_disk_that_failed_last_time_writes_it(tmp_path):
    """‏#1580 (נדב 17/10): נכשל בשיכפול הקודם ו-SMART תקין = **כתום** — נשאל
    ("נסה שוב"), ובלי תשובה כותב, כמו כל כתום. אותה צורה כמו בברזל — sda
    תקין, sdb נכשל בפעם הקודמת. עד 17/10 זה היה אדום שמדלג."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), disks="sda sdb",
                                  failed=("sdb",))
    assert asked(run) == [], err   # #2042 (נדב 23/10): not even asked
    assert written == "sda sdb", err
    ev = event(run)
    assert (ev["disk"], ev["smart"]["verdict"], ev["write_state"]) == ("sdb", "failed_last", "pending"), ev


def test_rescue_from_the_gui_never_writes_a_smart_failed_disk(tmp_path):
    """‏#1732 (נדב 18/10, מחליף את "כתוב בכל זאת" של #1580): אדום (SMART
    תקול) לעולם אינו יעד. מסך שהיה עונה "rescue" אינו נשאל — התשובה נשארת
    בתור, הדיסק מדולג בגלוי עם המשפט, וה-event אומר skip."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL), gui=["rescue"])
    assert written == "", err
    assert asked(run) == [], err
    assert (run / "gui_answers").read_text().split() == ["rescue"]        # never consumed
    assert "SMART נכשל — לא כותבים על הדיסק הזה" in (run / "targets/sda/error").read_text(encoding="utf-8")
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["decision"], ev["write_state"]) == ("fail", "skip", "skipped"), ev


def test_a_smart_failed_disk_that_also_failed_last_time_is_red(tmp_path):
    """SMART תקול גובר על זיכרון השרת: האדום הוא `fail` (לא failed_last), ובלי
    תשובה מדלג — גם כשהכשל הקודם הופך אותו לכתום אילו SMART היה תקין."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL), failed=("sda",))
    assert written == "", err
    ev = event(run)
    assert (ev["smart"]["verdict"], ev["decision"], ev["write_state"]) == ("fail", "skip", "skipped"), ev


# --- #872: בלי תשובה — "המשך" של אותו צבע: כתום כותב, אדום מדלג ---------------

def test_no_answer_on_an_orange_disk_writes_it(tmp_path):
    """בלי גואי / בלי תשובה על כתום (‏#1580: נכשל בשיכפול הקודם) = המשך = כותב."""
    written, run, err = preflight(tmp_path, full(HEALTH_OK), failed=("sda",))
    assert asked(run) == [], err                  # #2042 (נדב 23/10): not even asked
    assert "written without asking" in err, err
    assert written == "sda"


def test_no_answer_on_a_red_disk_skips_it(tmp_path):
    """אדום = SMART תקול; ‏#1732: לא נשאל בכלל, ומדולג."""
    written, run, err = preflight(tmp_path, full(HEALTH_FAIL))
    assert asked(run) == [], err                   # #1732: not even asked
    assert written == ""
    assert (run / "targets/sda/state").read_text().strip() == "skipped"


# --- #875: log() בתוך הלכידה -- disk_event שלא נמסר אינו "דיסק" ------------------

def test_an_undelivered_disk_event_does_not_leak_into_the_write_list(tmp_path):
    """‏#875: ‏`smart_gate` לוכד את `smart_preflight` ב-`$( )`, ובתוכו
    ‏`smart_send_event` מדווח על disk_event שלא נמסר דרך `log` -- שכותב גם
    ל-stdout. עם curl כושל (שרת בעדכון, 5xx) הרשימה חייבת להיות **בדיוק**
    שמות הדיסקים, וההודעה בלוג. בקרה שלילית: על main הרשימה מכילה
    `imagesrv: disk_event for sda was not delivered`."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    for d in ("sda", "sdb"):
        (dev / d).write_bytes(b"\x00")
    out = sh(env(run, dev, smartctl="/nonexistent") + PRELUDE
             + 'http_post_json() { return 22; }; '   # curl -f על 5xx
             + 'smart_gate sess1 sda sdb; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[:2] == ["sda sdb", "rc=0"], out.stdout
    assert "imagesrv:" not in out.stdout, out.stdout
    assert "disk_event for sda was not delivered" in (run / "agent.log").read_text()


# --- #1216: smart_gate כשלא נשאר מה לכתוב -----------------------------------

def test_smart_gate_returns_1_when_all_disks_are_skipped(tmp_path):
    """‏smart_gate הוא השער שמונע התחלת כתיבה (#652, cloning-always-attended)
    כשהמפעיל דילג על **כל** הדיסקים -- לא רק חלקם. #1216: זה נמדד כפער
    כיסוי, לא כבאג -- ההתנהגות היום נכונה, ונמצא במוטציה בפיצול #1211
    (24/09) שעברה בשקט. בקרה שלילית: `return 0` בסוף smart_gate מפיל
    את הטסט הזה."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    for d in ("sda", "sdb"):
        (dev / d).write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_FAIL))
    (run / "gui_answers").write_text("skip\nskip\n", newline="\n")
    out = sh(env(run, dev, smartctl=sc) + PRELUDE + GUI_STUB
             + 'smart_gate sess1 sda sdb; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "rc=1", out.stdout   # לא נכתב דבר, השער עצר
    assert (run / "targets/sda/state").read_text().strip() == "skipped"
    assert (run / "targets/sdb/state").read_text().strip() == "skipped"


# --- תווית הדיסק: לפי חריץ, לא לפי שם ההתקן ----------------------------------

def test_disk_label_uses_the_slot_number(tmp_path):
    # ‏disk_port מוזרק (מקורו נבדק ב-test_agent) -- כאן נבדקת התווית עצמו.
    out = sh(PRELUDE + 'disk_port() { echo 3; }; smart_disk_label sda')
    assert out.stdout.strip() == "Disk 3"


def test_disk_label_falls_back_to_device_name_without_a_slot(tmp_path):
    out = sh(PRELUDE + 'disk_port() { return 0; }; smart_disk_label nvme0n1')
    assert out.stdout.strip() == "Disk nvme0n1"


# --- hello: הבריאות זמינה לקונסולה לפני start (מטמון בלבד) -------------------

def test_hello_field_reads_the_cache_and_never_probes(tmp_path):
    # אחרי probe אחד, שדה ה-hello מחזיר את ה-verdict מהמטמון; בלי probe
    # הוא `unchecked`. זה מה שמאפשר לקונסולה להראות "דיסק N: אזהרה"
    # *לפני* שסבב נפתח, בלי לשלם smartctl בכל hello.
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_FAIL, realloc=4))
    base = env(run, dev, smartctl=sc) + PRELUDE
    assert sh(base + 'smart_hello_field sdb').stdout.strip() == "unchecked"
    sh(base + 'smart_probe sda >/dev/null')
    assert sh(base + 'smart_hello_field sda').stdout.strip() == "fail"


# --- disk_event: מבנה החוזה --------------------------------------------------

def test_event_json_is_valid_and_carries_the_contract(tmp_path):
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(b"\x00")
    sc = make_smartctl(tmp_path, full(HEALTH_OK, realloc=3))
    out = sh(env(run, dev, smartctl=sc) + PRELUDE
             + 'smart_probe sda >/dev/null; '
             + 'smart_event_json sess1 sda ok ok rescue rescue')
    assert out.returncode == 0, out.stderr
    ev = json.loads(out.stdout)
    assert ev["session_id"] == "sess1"
    assert ev["disk"] == "sda"
    assert ev["mac"] == "aa:bb:cc:dd:ee:ff"
    assert ev["smart"]["verdict"] == "ok"
    assert ev["smart"]["reason"] == "ok"
    assert ev["smart"]["realloc"] == 3
    assert ev["write_state"] == "rescue"
    assert ev["decision"] == "rescue"



# --- #906: hello ממשיך בזמן שהשאלה ממתינה למפעיל -------------------------------

#: ‏build_hello מזויף: הבדיקה היא על *מה שנוסף* לגוף ועל הקצב, לא על sysfs.
#: ‏joining מועבר כפי ש-attended_hello מבקש (false), כדי שייבדק בגוף.
FAKE_HELLO = (
    'build_hello() { printf %s "{\\"schema\\":2,\\"mac\\":\\"$MAC\\",\\"joining\\":$1}"; }; '
)


def test_hello_keeps_going_while_the_operator_is_asked(tmp_path):
    """נמדד 16/09 על מחשב 2: בזמן `smart_choice` אף hello לא יצא, ‏`last_seen`
    קפא והמוניטור נחסם (403) בדיוק כשהמפעיל צריך לראות את השאלה. כאן
    ‏`http_post_json` מזויף רושם כל גוף שנשלח; התשובה מגיעה אחרי ~2.5 פעימות
    (‏ATTENDED_BEAT_S=1 — שניות שלמות, #1333), ולכן חייבים להיספר ≥2 hello — כל
    אחד עם `waiting_for: operator`, השאלה ב-`prompt`, ו-`joining: false`
    (ממתין אינו מצטרף לגל). בקרה שלילית: על main נספרים 0."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(bytes(1))
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + 'export ATTENDED_BEAT_S=1; '
             + 'http_post_json() { printf "%s\\n" "$(cat "$2")" >> "$RUN_DIR/hellos"; }; '
             + 'gui_smart_choice() { sleep 2.5; echo skip; }; '
             + "smart_choice 'Disk 1' 'SMART fail (x)' sda fail x rescue")
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "skip", out.stdout          # ההכרעה עצמה לא נפגעה
    lines = (run / "hellos").read_text().splitlines() if (run / "hellos").exists() else []
    hellos = [json.loads(line) for line in lines if line]
    assert len(hellos) >= 2, hellos
    for h in hellos:
        assert h["waiting_for"] == "operator"
        assert h["prompt"] == "Disk 1: SMART fail (x)"
        assert h["joining"] is False
        assert h["mac"] == "aa:bb:cc:dd:ee:ff"


def test_the_beat_stops_with_the_answer(tmp_path):
    """אחרי שהמפעיל ענה, הפעימה מפסיקה: ‏`$( )` של הקורא (‏smart_preflight)
    אינו נתקע על תת-מעטפת שעוד מחזיקה את stdout, ואף hello נוסף אינו
    נשלח אחרי ההכרעה."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir(); (dev / "sda").write_bytes(bytes(1))
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + 'export ATTENDED_BEAT_S=1; '
             + 'http_post_json() { echo hello >> "$RUN_DIR/hellos"; }; '
             + 'gui_smart_choice() { echo replace; }; '
             + "_dec=$(smart_choice 'Disk 1' 'ctx' sda fail x rescue); "
             + 'sleep 0.8; echo "dec=$_dec"; _n=$(wc -l < "$RUN_DIR/hellos"); '
             + 'sleep 0.8; echo "grew=$(( $(wc -l < "$RUN_DIR/hellos") - _n ))"')
    assert out.returncode == 0, out.stderr
    assert "dec=replace" in out.stdout, out.stdout
    assert "grew=0" in out.stdout, out.stdout


def test_a_question_inside_a_question_beats_once_and_hands_back(tmp_path):
    """‏#912: ‏`attended` בתוך `attended` — למשל שאלה שנשאלת בזמן שמסך אחר
    כבר ממתין. נמדד 16/09 על הקוד של #906: שתי פעימות במקביל (ה-prompt
    מתחלף על השרת בכל פעימה), והחיצונית נשארת יתומה אחרי שתי העצירות.
    הנדרש: בזמן הפנימית נשלחת **רק** השאלה הפנימית; כשהיא נענתה, פעימת
    החיצונית חוזרת; ובסוף — אף hello נוסף. בקרה שלילית: `attended.sh` של
    main → `outer` בין הפעימות הפנימיות, ו-`grew` > 0."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + 'export ATTENDED_BEAT_S=1; '
             + 'http_post_json() { printf "%s\\n" "$(cat "$2")" >> "$RUN_DIR/hellos"; }; '
             + 'wait_for_hellos() { _p=$1; _n=$2; _i=0; '
             + 'while :; do _seen=$(grep -c "\\\"prompt\\\":\\\"$_p\\\"" '
             + '"$RUN_DIR/hellos" 2>/dev/null) || _seen=${_seen:-0}; '
             + '[ "$_seen" -lt "$_n" ] || break; [ "$_i" -lt 50 ] || return 1; '
             + 'sleep 0.1; _i=$((_i + 1)); done; }; '
             + 'inner() { wait_for_hellos inner 2 || return 1; : > "$RUN_DIR/inner-end"; }; '
             + 'outer() { attended inner inner || return 1; wait_for_hellos outer 2 || return 1; '
             + ': > "$RUN_DIR/outer-end"; }; '
             + 'attended outer outer; _rc=$?; _n=$(wc -l < "$RUN_DIR/hellos"); _i=0; '
             + 'while [ "$_i" -lt 5 ]; do sleep 0.1; '
             + '[ "$(wc -l < "$RUN_DIR/hellos")" -eq "$_n" ] || break; _i=$((_i + 1)); done; '
             + 'echo "grew=$(( $(wc -l < "$RUN_DIR/hellos") - _n ))"; exit "$_rc"')
    assert out.returncode == 0, out.stderr
    assert "grew=0" in out.stdout, out.stdout
    assert (run / "inner-end").exists() and (run / "outer-end").exists()
    raw = (run / "hellos").read_bytes()
    lines = [ln for ln in raw.split(b"\n")[:-1] if ln]  # ignore a partial final append
    prompts = [json.loads(line)["prompt"] for line in lines]
    inner_count = prompts.count("inner")
    outer_count = prompts.count("outer")
    assert inner_count >= 2 and outer_count >= 2, prompts
    assert prompts == ["inner"] * inner_count + ["outer"] * outer_count, prompts


@pytest.mark.parametrize("beat", ["0.2", "0", "abc", "-1", ""])
def test_a_beat_that_is_not_whole_seconds_is_named_not_a_hello_storm(tmp_path, beat):
    """#1333: הלולאה של #989 סופרת שניות שלמות ב-`[ -lt ]`. על "0.2" זה אינו
    false אלא שגיאה (rc=2), הלולאה לא ישנה אף פעם, והפעימה הפכה ל-hello רציף
    — בדיוק מה ש-server-must-not-congest-network אוסר. ערך שאינו מספר שלם
    חיובי נאמר ב-stderr, וברירת המחדל (10 ש') נשארת: ב-2 ש' יוצא hello אחד.
    בקרה שלילית: `attended.sh` של הבסיס → עשרות hello, ושום מילה ב-stderr."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + f"export ATTENDED_BEAT_S='{beat}'; "
             + 'http_post_json() { echo hello >> "$RUN_DIR/hellos"; }; '
             + 'attended q sleep 2')
    assert out.returncode == 0, out.stderr
    hellos = (run / "hellos").read_text().splitlines()
    assert len(hellos) == 1, len(hellos)
    assert f"ATTENDED_BEAT_S='{beat}' is not a whole number of seconds" in out.stderr, out.stderr
    assert out.stdout == "", out.stdout          # stdout של העוטף נשאר נקי


@pytest.mark.parametrize("ask", ["0.5", "0"])
def test_an_ask_beat_that_is_not_whole_seconds_does_not_kill_the_beat(tmp_path, ask):
    """#1333: אותה משפחה ב-ATTENDED_ASK_BEAT_S — בזמן שאלת SMART על הגואי
    הלולאה מחשבת `$((t % ask))`, ועל "0.5" או "0" זו שגיאה אריתמטית שהורגת
    את תת-המעטפת בשקט (stderr שלה הוא /dev/null): הפעימה מתה, וזה #906
    מחדש. נדרש: נאמר ב-stderr, וברירת המחדל (3) — כאן ATTENDED_BEAT_S=2
    קצר ממנה, ולכן יוצאים hello ב-0 וב-2. בבסיס: hello אחד ומוות שקט."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    (gui / "smart-request").write_text("3-2|2|fail|x\n")
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + f"export GUI_DIR='{posix(gui)}' ATTENDED_BEAT_S=2 ATTENDED_ASK_BEAT_S='{ask}'; "
             + 'http_post_json() { echo hello >> "$RUN_DIR/hellos"; }; '
             + 'attended q sleep 3.2')
    assert out.returncode == 0, out.stderr
    hellos = (run / "hellos").read_text().splitlines()
    assert len(hellos) == 2, len(hellos)
    assert f"ATTENDED_ASK_BEAT_S='{ask}' is not a whole number of seconds" in out.stderr, out.stderr


def test_a_plain_beat_carries_no_prompt(tmp_path):
    """‏attended_hello בלי שאלה = דופק רגיל (hold_beat בלי HOLD_PROMPT): בלי
    `waiting_for` ובלי `prompt`, כך שהשרת מנקה שאלה קודמת. עם שאלה —
    שני השדות, והשאלה עוברת json_escape."""
    run = tmp_path / "run"; run.mkdir()
    dev = tmp_path / "dev"; dev.mkdir()
    out = sh(env(run, dev) + PRELUDE + FAKE_HELLO
             + 'http_post_json() { cat "$2" >> "$RUN_DIR/hellos"; echo >> "$RUN_DIR/hellos"; }; '
             + "attended_hello ''; attended_hello 'FAILED: \"restore\" did not complete'")
    assert out.returncode == 0, out.stderr
    plain, with_prompt = (json.loads(line) for line in (run / "hellos").read_text().splitlines())
    assert "waiting_for" not in plain and "prompt" not in plain
    assert with_prompt == {"schema": 2, "mac": "aa:bb:cc:dd:ee:ff", "joining": False,
                           "waiting_for": "operator",
                           "prompt": 'FAILED: "restore" did not complete'}
