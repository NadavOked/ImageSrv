"""‏#87: כיווץ מחיצת ה-NTFS של דיסק המקור לקליטה -- ומאז #1466 (הכרעת נדב
16/10) **על עותק copy-on-write בזיכרון בלבד**: המקור אינו נכתב לעולם.

דיסק בנייה של 512GB עם 100GB בשימוש ייצר עד כאן אימג' של 512GB, ובדיקה
2.7 חסמה אותו בצדק על כל כונן 256. הכיווץ (‏agent/lib/shrink.sh) רץ
**על המקור**, אחרי שער המהובר (‏#651) ולפני שהזרם מתחיל, ורק אחרי
שאדם ליד המכונה אמר כן. המניפסט רושם את הפריסה המצומצמת —
‏`size_bytes` קטן, ‏`shrunk_from_bytes` לתיעוד, וכל מחיצה שיושבת אחרי
ווינדוס (‏recovery) זזה אחורה בהפרש, עם `source_start_sector` — ולכן
‏`min_target_bytes` ו-`required_bytes` בשחזור קטנים בלי שינוי שם.

כל צעד הרסני נמדד כאן על **הסדר** ועל **הארגומנטים** שהכלים קיבלו,
ולא על "רץ בלי שגיאה": ‏ntfsfix -b -d לפני ntfsresize, ‏-s עם המספר
המדויק, ‏sgdisk עם ה-GUID/השם/הדגלים, ‏rereadpt, קריאה חוזרת של הגודל,
ואז partclone — ובסוף ההחזרה בסדר ההפוך. סטאבים של ntfsresize כמו
ב-`test_stage_attribution.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, REPO, posix, sh
from test_capture_refusals import (
    CURL_SHRINK_SERVER,
    PIPE_INPUT,
    capture_body,
    capture_run,
    first_index,
    refusal_reason,
)

pytestmark = requires_native(("bash", BASH))

WIN_GUID = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC_GUID = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
WIN_UGUID = "4C7B1E00-0000-4000-8000-000000000003"
REC_UGUID = "4C7B1E00-0000-4000-8000-000000000004"

#: הפריסה של Windows 11 על דיסק 512GB: ‏ESP, ‏C: ענקית, ‏recovery אחריה.
WIN_START = 1085440
WIN_SECTORS = 998_244_352            # ‏476 GiB, כפולה של 2048
REC_START = WIN_START + WIN_SECTORS
REC_SECTORS = 2_048_000              # ‏~1GB
MIN_BYTES = 100_000_000_000          # מה ש-ntfsresize --info "מודד"
SOURCE_DISK = bytes(512) + b"EFI PART" + bytes(504)

#: ‏sgdisk עם מצב: אחרי `-d N -n N:start:end` הוא זוכר את הגודל החדש
#: וקורא אותו בחזרה ב-`-i` — כמו דיסק אמיתי. כל קריאה נרשמת ל-sgdisk.calls
#: (הארגומנטים כשורה אחת), וזה מה שהטסטים מודדים.
SGDISK_WIN_REC = f'''#!/bin/sh
echo "$*" >> "$RUN_DIR/sgdisk.calls"
sizes="$RUN_DIR/sgdisk.sizes"
[ -f "$sizes" ] || printf '3={WIN_SECTORS}\\n4={REC_SECTORS}\\n' > "$sizes"
[ "$1" = "-a" ] && shift 2
if [ "$1" = "-d" ]; then
  idx="$2"; spec="$4"
  start=${{spec#*:}}; start=${{start%%:*}}; end=${{spec##*:}}
  n=$((end - start + 1))
  [ "${{SGDISK_READBACK_LIES:-0}}" = 1 ] || sed -i "s/^$idx=.*/$idx=$n/" "$sizes"
  : > "$RUN_DIR/sgdisk.written"
  exit 0
fi
if [ "$1" = "-i" ]; then
  n=$(sed -n "s/^$2=//p" "$sizes")
  # אחרי כתיבה: התחלה שזזה ב-2048 (SGDISK_START_LIES) -- מה ש-Align() של
  # sgdisk עושה לתחילה לא-מיושרת, בלי לצעוק.
  drift=0; [ "${{SGDISK_START_LIES:-0}}" = 1 ] && [ -f "$RUN_DIR/sgdisk.written" ] && drift=2048
  case "$2" in
    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI system partition)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000001"
       echo "First sector: 2048 (at 1024.0 KiB)"
       echo "Partition size: 204800 sectors (100.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'EFI system partition'" ;;
    3) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {WIN_UGUID}"
       echo "First sector: $(({WIN_START} + drift)) (at 530.0 MiB)"
       echo "Partition size: $n sectors (huge)"
       [ "${{SGDISK_NO_ATTRS:-0}}" = 1 ] || echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Basic data partition'" ;;
    4) echo "Partition GUID code: {REC_GUID} (Windows RE)"
       echo "Partition unique GUID: {REC_UGUID}"
       echo "First sector: {REC_START} (at the end)"
       echo "Partition size: $n sectors (1000.0 MiB)"
       echo "Attribute flags: 8000000000000001"
       echo "Partition name: 'Recovery'" ;;
  esac
  exit 0
fi
echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
echo "   1            2048          206847   100.0 MiB   EF00  EFI system partition"
echo "   3         {WIN_START}      {WIN_START + WIN_SECTORS - 1}   476.0 GiB   0700  Basic data partition"
echo "   4       {REC_START}       {REC_START + REC_SECTORS - 1}   1000.0 MiB  2700  Recovery"
'''

#: ‏ntfsresize: ‏--info מדפיס את המינימום; ‏-s / ‏-f -f יוצאים 0. כל קריאה
#: נרשמת ל-order.log ליד השאר. הכיווץ (‏-s) רושם גם מה הגיע ב-stdin: השאלה
#: "proceed?" של ntfsresize רואה EOF כ**המשך** (proceed_question: fgets
#: שנכשל אינו עוצר), ולכן התשובה חייבת להיות "y" מפורש ולא היעדר תשובה.
NTFSRESIZE_OK = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "Current volume size: $((({WIN_SECTORS} * 512) - 512)) bytes"
            echo "You might resize at {MIN_BYTES} bytes or 100000 MB (freeing 411000 MB)."
            exit 0 ;;
  "-n "*) echo "Needed relocations : 2560 (${{RELOC_MB:-10}} MB)"; exit 0 ;;
  "-s "*|*" -s "*) IFS= read -r ans; echo "ntfsresize stdin=${{ans:-EOF}}" >> "$RUN_DIR/order.log" ;;
esac
exit "${{NTFSRESIZE_RC:-0}}"
'''

#: ‏#1466: צד הקרנל של העותק בזיכרון. ‏dmsetup/modprobe/rmmod נרשמים ב-order.log;
#: ‏modprobe brd מקים /sys/block/ramN ו-/dev/ramN, ‏dmsetup create מקים את
#: /dev/mapper/<שם> (512 אפסים -- שער ה-BitLocker קורא אותו) ושומר את הטבלה
#: כפי שהקרנל היה מחזיר אותה (major:minor).
DMSETUP_STUB = r'''#!/bin/sh
echo "dmsetup $*" >> "$RUN_DIR/order.log"
case "$1" in
  create) [ -z "${FAIL_DM_CREATE:-}" ] || exit 1
          nm=$2; set -- $4
          mkdir -p "$DEVROOT/mapper"; head -c 512 /dev/zero > "$DEVROOT/mapper/$nm"
          echo "254:$(ls "$RUN_DIR" | grep -c '^mm\.')" > "$RUN_DIR/mm.$nm"
          case "$3" in
            snapshot) echo "$1 $2 $3 $(cat "$SYSROOT/sys/class/block/${4##*/}/dev") $(cat "$SYSROOT/sys/block/${5##*/}/dev") $6 $7" ;;
            linear) echo "$1 $2 $3 $(cat "$RUN_DIR/mm.${4##*/}") $5" ;;
          esac > "$RUN_DIR/dm.$nm" ;;
  table) cat "$RUN_DIR/dm.$2" ;;
  info) eval "nm=\${$#}"; cat "$RUN_DIR/mm.$nm" ;;
  status) if [ -n "${DM_INVALID:-}" ]; then echo "0 1 snapshot Invalid"; else echo "0 1 snapshot 2048/4194304 16"; fi ;;
  remove) [ -z "${FAIL_DM_REMOVE:-}" ] || exit 1; rm -f "$DEVROOT/mapper/$3" "$RUN_DIR/dm.$3" "$RUN_DIR/mm.$3" ;;
  ls) l=$(cd "$RUN_DIR" && ls dm.* 2>/dev/null | sed 's/^dm\.//')
      if [ -n "$l" ]; then for n in $l; do printf '%s\t(254:0)\n' "$n"; done; else echo "No devices found"; fi ;;
esac
exit 0
'''
MODPROBE_STUB = r'''#!/bin/sh
echo "modprobe $*" >> "$RUN_DIR/order.log"
[ "$1" = brd ] || exit 0
mkdir -p "$SYSROOT/sys/module/brd"; n=${2#rd_nr=}; sz=${3#rd_size=}; i=0
while [ "$i" -lt "$n" ]; do
  mkdir -p "$SYSROOT/sys/block/ram$i"; echo $((sz * 2)) > "$SYSROOT/sys/block/ram$i/size"
  echo "1:$i" > "$SYSROOT/sys/block/ram$i/dev"; : > "$DEVROOT/ram$i"; i=$((i + 1))
done
'''
RMMOD_STUB = '#!/bin/sh\necho "rmmod $*" >> "$RUN_DIR/order.log"\nrm -rf "$SYSROOT/sys/module/$1"\n'


def view_sys(sizes: dict[int, int]) -> str:
    """/proc/meminfo ו-/sys/class/block/sdaN כפי שהקרנל היה מראה אותם (SYSROOT = הקופסה)."""
    t = 'mkdir -p "$SYSROOT/proc"; echo "MemAvailable:   ${MEM_KIB:-8388608} kB" > "$SYSROOT/proc/meminfo"; '
    for idx, sectors in sizes.items():
        t += (f'mkdir -p "$SYSROOT/sys/class/block/sda{idx}"; echo {sectors} > "$SYSROOT/sys/class/block/sda{idx}/size"; '
              f'echo 8:{idx} > "$SYSROOT/sys/class/block/sda{idx}/dev"; ')
    return t
NTFSFIX_OK = '#!/bin/sh\necho "ntfsfix $*" >> "$RUN_DIR/order.log"\nexit 0\n'
#: ‏--getsize64 של צומת mapper: האורך מהטבלה שלו, כמו שהקרנל היה אומר (#1466).
BLOCKDEV_OK = ('#!/bin/sh\necho "blockdev $*" >> "$RUN_DIR/order.log"\n'
               '[ "$1" = --getsize64 ] || exit 0\n'
               'l=$(awk \'{ print $2 }\' "$RUN_DIR/dm.${2##*/}" 2>/dev/null); [ -n "$l" ] || exit 1\n'
               'echo $((l * 512))\n')
PARTCLONE_LOGGED = ('#!/bin/sh\necho "partclone $*" >> "$RUN_DIR/order.log"\n'
                    'head -c 4096 /dev/zero\nexit 0\n')

#: ‏3 הוא NTFS (ווינדוס), 4 הוא NTFS (‏recovery), ‏1 vfat. השער של #651
#: מאושר — הוא נבדק ב-test_capture_hibernation.py; כאן הוא לא הנושא.
FS_MAP = ('_fs_of() { case "$1" in *3|*4) echo ntfs ;; *) echo vfat ;; esac; }; '
          'capture_ntfs_hibernation_reason() { return 0; }; '
          # ‏#1369: מחיצה 3 היא C: — יש בה Windows\System32 (נבדק ב-test_capture_measure_1368).
          '_system_role() { echo windows; }; '
          'disk_serial() { printf S926; }; disk_port() { printf 1; }; ')
FS_MAP += view_sys({3: WIN_SECTORS, 4: REC_SECTORS})
SAY_YES = 'shrink_ask() { echo continue; }; '
SAY_PLAIN = 'shrink_ask() { echo plain; }; '
SAY_CANCEL = 'shrink_ask() { echo cancel; }; '


def stubs(**extra: str) -> dict[str, str]:
    base = {
        # ‏#926: curl שעונה גם ל-shrink-open/close — בלי רשומה בשרת אין כיווץ.
        "sgdisk": SGDISK_WIN_REC, "curl": CURL_SHRINK_SERVER,
        "partclone.ntfs": PARTCLONE_LOGGED, "partclone.dd": PIPE_INPUT, "partclone.fat": PIPE_INPUT,
        "ntfsresize": NTFSRESIZE_OK, "ntfsfix": NTFSFIX_OK, "blockdev": BLOCKDEV_OK,
        "dmsetup": DMSETUP_STUB, "modprobe": MODPROBE_STUB, "rmmod": RMMOD_STUB,   # #1466
    }
    base.update(extra)
    return base


def order(run: Path) -> list[str]:
    path = run / "order.log"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def sgdisk_calls(run: Path) -> list[str]:
    path = run / "sgdisk.calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def source_writes(steps: list[str], box: Path) -> list[str]:
    """#1466: כל צעד שכותב לדיסק המקור או לטבלה שלו -- חייב להיות ריק."""
    src = f"{posix(box / 'dev')}/sda"
    return [st for st in steps
            if st.startswith(("blockdev --rereadpt", "shrink-open"))
            or (st.startswith(("ntfsresize", "ntfsfix")) and src in st and "--info" not in st)]


def rewrites(run: Path) -> list[str]:
    """הקריאות שכותבות טבלה: מחיקה+יצירה באותו אינדקס (‏-d N -n N:...)."""
    return [c for c in sgdisk_calls(run) if " -d " in f" {c} "]


def manifest(run: Path) -> dict:
    return json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))


def part(m: dict, idx: int) -> dict:
    return next(p for p in m["partitions"] if p["index"] == idx)


def shrink_target(min_bytes: int, current: int) -> int:
    """הפונקציה עצמה, מהקובץ: המרווח והעיגול נבדקים על הקוד ולא על עותק."""
    out = sh(f". {posix(AGENT)}/lib/shrinkplan.sh; _shrink_target {min_bytes} {current}")
    return int(out.strip())


MIB = 1 << 20
GIB = 1 << 30

# --- החשבון --------------------------------------------------------------------


def test_the_target_is_the_minimum_plus_ten_percent_rounded_to_a_mebibyte():
    """‏100GB בשימוש → יעד של ~110GB; ההפרש מהנוכחי כפולה של MiB."""
    current = WIN_SECTORS * 512
    new = shrink_target(MIN_BYTES, current)
    assert MIN_BYTES + MIN_BYTES // 10 <= new < MIN_BYTES + MIN_BYTES // 10 + 2 * MIB
    assert (current - new) % MIB == 0, "הזנב במניפסט חייב להישאר מיושר"
    assert new < current


def test_a_small_volume_gets_the_two_gibibyte_floor_not_ten_percent():
    """‏10% של 5GB הם 500MB — פחות מדי לווינדוס שמתעורר; הרצפה היא 2GiB."""
    new = shrink_target(5 * 10**9, 200 * 10**9)
    assert new >= 5 * 10**9 + 2 * GIB


def test_nothing_to_shrink_returns_the_current_size_itself():
    """מינימום קרוב לנוכחי — ההפרש קטן ממגה, והפונקציה מחזירה את הנוכחי
    ולא מספר שגדול ממנו (ואז הקורא היה 'מכווץ' כלפי מעלה)."""
    target = -(-(MIN_BYTES + MIN_BYTES // 10) // MIB) * MIB
    current = target + 500_000
    assert shrink_target(MIN_BYTES, current) == current
    assert shrink_target(current, current) == current
    assert shrink_target(MIN_BYTES, target + MIB) == target


# --- המסלול המלא: כיווץ, זרם, החזרה ---------------------------------------------


def test_a_windows_disk_is_shrunk_on_a_ram_view_and_the_source_is_never_written(tmp_path):
    """#1466: --info על המקור (קריאה), ואז עותק: ‏dm-snapshot מעל sda3 על ram0,
    ‏-n (התקציב), ‏-s ו-ntfsfix -d **על העותק**, ‏partclone קורא את העותק,
    והעותק משוחרר אחרי הזרם. אף צעד כותב אינו נוגע ב-sda ואין רשומה בשרת."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    new = shrink_target(MIN_BYTES, WIN_SECTORS * 512)
    dev = posix(box / "dev")
    view = f"{dev}/mapper/lc-src-3"
    cut = f"{dev}/mapper/lc-cut-3"
    steps = order(run)
    positions(steps,
              f"ntfsresize --info --no-progress-bar {dev}/sda3",
              "modprobe dm-snapshot", "modprobe brd rd_nr=1",
              f"dmsetup create lc-src-3 --table 0 {WIN_SECTORS} snapshot {dev}/sda3 {dev}/ram0 N 8",
              "dmsetup table lc-src-3",
              f"ntfsresize -n -s {new} --no-progress-bar {view}",
              f"ntfsresize -s {new} --no-progress-bar {view}", f"ntfsfix -d {view}",
              f"dmsetup create lc-cut-3 --table 0 {new // 512} linear {view} 0", f"blockdev --getsize64 {cut}",
              "partclone", "dmsetup remove --retry lc-cut-3", "dmsetup remove --retry lc-src-3", "dmsetup ls", "rmmod brd")
    assert [st for st in steps if st.startswith("partclone") and "sda3" in st] == [], "partclone קרא את המקור ולא את העותק"
    # the cut -- the view at exactly the new length -- not the view: partclone -c records the device size
    assert any(st.startswith("partclone") and cut in st for st in steps), steps
    assert source_writes(steps, box) == [], source_writes(steps, box)
    assert not rewrites(run), "טבלת המקור לא נכתבת"
    assert not (run / "targets" / "sda" / "shrunk").exists()
    assert not (box / "dev" / "mapper" / "lc-src-3").exists() and not (box / "sys" / "module" / "brd").exists()


def test_the_manifest_describes_the_compacted_layout(tmp_path):
    """‏size_bytes מכווץ + shrunk_from_bytes; ה-recovery זזה אחורה בדיוק
    בהפרש ונושאת source_start_sector; והרצפה נגזרת מהפריסה החדשה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    current = WIN_SECTORS * 512
    new = shrink_target(MIN_BYTES, current)
    delta_sectors = (current - new) // 512

    win = part(m, 3)
    assert win["size_bytes"] == new
    assert win["shrunk_from_bytes"] == current
    assert win["start_sector"] == WIN_START
    assert "source_start_sector" not in win, "המועמדת לא זזה — השדה חסר, לא 0"
    assert win["expandable"] is True

    rec = part(m, 4)
    assert rec["start_sector"] == REC_START - delta_sectors
    assert rec["source_start_sector"] == REC_START
    assert rec["size_bytes"] == REC_SECTORS * 512
    assert rec["attrs"] == "8000000000000001"
    assert "shrunk_from_bytes" not in rec
    assert rec["start_sector"] % 2048 == 0, "הזנב חייב להישאר מיושר ל-2048"

    esp = part(m, 1)
    assert "shrunk_from_bytes" not in esp and "source_start_sector" not in esp

    # הרצפה: סוף ה-recovery החדש + גיבוי ה-GPT (#1171), לא 512GB.
    end = (rec["start_sector"] * 512) + rec["size_bytes"]
    assert m["min_target_bytes"] == end + 256 * 1024  # #1171
    assert m["min_target_bytes"] < 130 * 10**9


def test_the_shrunk_manifest_is_what_restore_and_the_server_require(tmp_path):
    """הקצה השני: אותו מניפסט, דרך `required_bytes` של restore.sh ושל
    השרת — שניהם רואים ~110GB ולא 512, בלי שינוי בקוד שלהם."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    from server.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"]
    assert required_bytes(m) < 256_060_514_304, "האימג' עדיין לא נכנס ל-SSD של 256"


# --- ההכרעה של האדם ------------------------------------------------------------


def test_cancel_stops_the_capture_before_anything_is_written(tmp_path):
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_CANCEL)
    reason = refusal_reason(box, run, out)
    assert "הכיווץ לא אושר" in reason, reason
    assert "GB" in reason, "השאלה עצמה — המספרים — חייבת להגיע לקונסולה"
    steps = order(run)
    assert steps == [s for s in steps if s.startswith("ntfsresize --info")], steps
    assert not rewrites(run)
    assert not (run / "new-manifest.json").exists()


def test_no_answer_is_cancel_not_yes(tmp_path):
    """‏EOF על stdin (מסך שנעלם, run_sh עם DEVNULL) — שום דבר לא מכווץ."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP)
    reason = refusal_reason(box, run, out)
    assert "הכיווץ לא אושר" in reason, reason
    assert not [s for s in order(run) if s.startswith("ntfsresize -s")]


def test_capture_without_shrinking_is_still_offered(tmp_path):
    """‏[2]: ההתנהגות של עד היום — קריאה בלבד, האימג' בגודל המקור."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_PLAIN)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    assert part(m, 3)["size_bytes"] == WIN_SECTORS * 512
    assert "shrunk_from_bytes" not in part(m, 3)
    assert part(m, 4)["start_sector"] == REC_START
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsresize -f", "ntfsfix"))]
    assert not rewrites(run)


def test_without_a_gui_no_key_shrinks_anything(tmp_path):
    """‏shrink_ask עצמו, בלי גואי: מסך הטקסט שמיפה 1/2/3 הוסר (11/10, GUI
    בלבד). כל מקש שמחכה ב-stdin, וגם EOF — cancel; שום דבר לא מכווץ בלי
    אדם שאמר כן בדיאלוג הגרפי (#928)."""
    plan = tmp_path / "shrink.plan"
    plan.write_text(f"3|{WIN_GUID}|{WIN_UGUID}|{WIN_START}|{WIN_SECTORS}|512000000000|110000000000\n",
                    encoding="utf-8", newline="\n")

    def ask(keys: str) -> str:
        return sh(f"export IMAGECTL_TEST=1; . {posix(AGENT)}/lib/ui.sh; . {posix(AGENT)}/lib/shrinkplan.sh; "
                  f"printf '{keys}' | shrink_ask {posix(plan)!r} 2>/dev/null").strip()
    for keys in ("1\\n", "2\\n", "3\\n", ""):
        assert ask(keys) == "cancel", keys


# --- כשל בכל צעד = הקליטה נכשלת בגלוי, והמקור מוחזר -----------------------------


def test_a_failed_ntfsresize_on_the_view_fails_the_capture_and_the_source_is_untouched(tmp_path):
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"NTFSRESIZE_RC": "3"})
    reason = refusal_reason(box, run, out)
    assert "rc=3" in reason and "המקור לא נכתב" in reason, reason
    assert source_writes(order(run), box) == [] and not rewrites(run)
    assert "rmmod brd" in order(run), "העותק משוחרר גם בכשל"
    assert not (run / "new-manifest.json").exists()


@pytest.mark.parametrize("env,pre,why", [
    ({"MEM_KIB": str(1024 * 1024)}, "", "פנויים 1024 MB זיכרון, ו-1024 MB שמורים להעתקה עצמה"),
    ({"RELOC_MB": "8000"}, "", "להזזת 8000 MB נדרשים 9056 MB זיכרון, ויש 7168 MB"),
    ({"FAIL_DM_CREATE": "1"}, "", "dmsetup create lc-src-3 נכשל"),
    ({"DM_INVALID": "1"}, "", "אינו תקין אחרי הכיווץ"),
    ({}, 'mkdir -p "$SYSROOT/sys/module/brd"; ', "כבר טעון"),
])
def test_no_view_stops_the_capture_by_name_and_writes_nothing(tmp_path, env, pre, why):
    """#1466: אין עותק -- הקליטה נעצרת, הסיבה עם המספרים מגיעה לקונסולה, ונאמר
    איך מקבלים אימג' בגודל מלא ("בלי כיווץ", שאינו כותב דבר). אין נפילה לכיווץ
    במקום: אף צעד כותב על sda ואף שורה בטבלה שלו."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES + pre, env=env)
    reason = refusal_reason(box, run, out)
    assert why in reason and "המקור לא נכתב" in reason and "בלי כיווץ" in reason, reason
    assert source_writes(order(run), box) == [], source_writes(order(run), box)
    assert not rewrites(run) and not (run / "new-manifest.json").exists()
    assert not [st for st in order(run) if st.startswith("partclone")], "הזרם לא התחיל"


def test_a_view_that_was_not_released_after_the_capture_is_a_warning(tmp_path):
    """האימג' עבר והמקור לא נכתב; עותק שלא שוחרר מחזיק זיכרון -- אזהרה בשדה
    error, לא כשל קליטה."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES, env={"FAIL_DM_REMOVE": "1"})
    assert out.strip().endswith("rc=0"), out
    assert (run / "new-manifest.json").exists()
    warning = (run / "targets" / "sda" / "error").read_text(encoding="utf-8")
    assert "העותק בזיכרון לא שוחרר" in warning and "dmsetup remove lc-src-3 failed" in warning, warning


def test_an_entry_whose_flags_cannot_be_read_is_not_shrunk(tmp_path):
    """הדגלים והשם נקראים מ-sgdisk -i לפני הכיווץ כדי להיכתב מחדש; קריאה
    שלא הניבה דגלים אינה "אין דגלים" — היא סירוב, לפני שנכתב בייט."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"SGDISK_NO_ATTRS": "1"})
    reason = refusal_reason(box, run, out)
    assert "sgdisk -i" in reason, reason
    assert not [s for s in order(run) if s.startswith(("ntfsresize -s", "ntfsfix"))]
    assert not rewrites(run)


def test_the_source_is_never_treated_like_a_clone_target(tmp_path):
    """‏ntfsfix -b מוחק את רשימת הקלאסטרים הפגומים -- לעולם לא, גם לא על העותק;
    ‏ntfsfix רק -d ורק על העותק אחרי ה-resize; הכיווץ בלי -f ועם "y" מפורש
    (‏EOF בשאלה של ntfsresize הוא המשך)."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    steps = order(run)
    assert not [st for st in steps if st.startswith("ntfsfix -b")], steps
    dev = posix(box / "dev")
    assert f"ntfsresize --info --no-progress-bar {dev}/sda3" in steps, steps
    assert "ntfsresize stdin=y" in steps, steps
    fixes = [st for st in steps if st.startswith("ntfsfix")]
    assert fixes == [f"ntfsfix -d {dev}/mapper/lc-src-3"], fixes


def test_an_unreadable_minimum_is_a_refusal_not_a_skip(tmp_path):
    """‏ntfsresize --info שלא הדפיס מספר: "לא הצלחנו לבדוק" ≠ "אין מה לכווץ"."""
    # #1502: the tool's raw line is not in the headline -- it is in the log and the info file.
    silent = '#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\necho "ERROR: Volume is scheduled for check"\nexit 1\n'
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=silent), shell_pre=FS_MAP + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק" in reason and "בדיסק sda" in reason, reason
    assert "scheduled" not in reason and "ERROR" not in reason, reason
    assert "א" <= reason[0] <= "ת", reason
    assert "scheduled for check" in (run / "ntfsresize.info.3").read_text(encoding="utf-8")
    assert not (run / "new-manifest.json").exists()


#: #1502, metal 16/10: what ntfsresize --info printed on the build machine (shortened).
INCONSISTENT = (
    "Cluster accounting failed at 12345 (0x3039): extra cluster in $Bitmap\n"
    "Cluster accounting failed at 12346 (0x303a): extra cluster in $Bitmap\n"
    "Cluster accounting failed at 12347 (0x303b): extra cluster in $Bitmap\n"
    "ERROR: NTFS is inconsistent. Run chkdsk /f on Windows then reboot it TWICE!\n"
    "The usage of the /f parameter is very IMPORTANT! No modification was\n"
    "and will be made to NTFS by this software until it gets repaired.\n"
)


def test_an_inconsistent_ntfs_is_named_in_hebrew_with_the_fix(tmp_path):
    """#1502 (metal 16/10): the headline was "sda: 3 לא הצלחנו לבדוק ...
    (ntfsresize --info): and will be made to NTFS by this software until it
    gets repaired." -- the tool's last line, in English, inside a Hebrew line.
    Now: Hebrew first, what the state is and what to do; the raw output stays
    in the info file."""
    stub = ('#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\ncat << \'EOF\'\n'
            + INCONSISTENT + 'EOF\nexit 1\n')
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=stub), shell_pre=FS_MAP + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert reason.startswith("מחיצה 3 בדיסק sda לא עקבית (NTFS)"), reason
    assert "chkdsk /f" in reason and "shutdown /s /t 0" in reason and "הדיסק לא שונה" in reason, reason
    assert "will be made" not in reason and "Cluster accounting" not in reason, reason
    assert "NTFS is inconsistent" in (run / "ntfsresize.info.3").read_text(encoding="utf-8")
    assert not (run / "new-manifest.json").exists()


#: כמו NTFSRESIZE_OK, אבל המתיחה בחזרה (‏-f -f) נכשלת.
NTFSRESIZE_GROW_FAILS = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*) echo "You might resize at {MIN_BYTES} bytes"; exit 0 ;;
  *"-f -f"*) exit 5 ;;
esac
exit 0
'''


# --- מתי לא מכווצים ------------------------------------------------------------


def test_nothing_to_shrink_asks_nobody_and_writes_nothing(tmp_path):
    close = NTFSRESIZE_OK.replace(f"at {MIN_BYTES} bytes", f"at {WIN_SECTORS * 512 - MIB} bytes")
    box, run, out = capture_run(tmp_path, stubs=stubs(ntfsresize=close),
                                shell_pre=FS_MAP + 'shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo cancel; }; ')
    assert out.strip().endswith("rc=0"), out
    assert "asked" not in order(run)
    assert part(manifest(run), 3)["size_bytes"] == WIN_SECTORS * 512


def test_direct_send_takes_only_the_tail_and_never_writes_the_source(tmp_path):
    """‏#715: הקריאה השנייה משווה sha256 לראשונה, והדיסק נשאר קריאה-בלבד, כמו
    שהמסך מבטיח. ‏#1171: שידור ישיר לוקח רק את הזנב של #1202 (לא את המרווח
    הרגיל), אחרי "כווץ ושלח" (SAY_YES), על עותק **אחד** בזיכרון ששתי הקריאות קוראות --
    tests/test_direct_tail_1171.py. גודל הדיסק לא ידוע כאן, ולכן הזנב נלקח."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_YES,
                                env={"CAPTURE_SINK": "discard"})
    assert out.strip().endswith("rc=0"), out
    steps, tail = order(run), 64 << 20
    assert source_writes(steps, box) == [], source_writes(steps, box)
    assert [s for s in steps if s.startswith("ntfsresize -s")] == \
        [f"ntfsresize -s {WIN_SECTORS * 512 - tail} --no-progress-bar {posix(box / 'dev')}/mapper/lc-src-3"], steps
    assert part(manifest(run), 3)["size_bytes"] == WIN_SECTORS * 512 - tail


def test_a_hibernated_disk_drops_on_the_view_before_the_shrink(tmp_path):
    """#2213 replaces the early refusal; the source remains read-only."""
    from test_capture_fast_startup_2213 import shrink_hibernated

    shrink_hibernated(tmp_path)


# --- ‏#929: כל מחיצות ה-NTFS מכווצות, כל אחת במקומה ---------------------------

DATA_START = WIN_START + WIN_SECTORS
DATA_SECTORS = 104_857_600            # ‏D: של 50 GiB אחרי C:
DATA_UGUID = "4C7B1E00-0000-4000-8000-000000000005"
MIN_D = 20_000_000_000                # מה ש-ntfsresize --info "מודד" ב-D:

#: דיסק בנייה עם D: **אחרי** C: (‏ESP, ‏MSR, ‏C: ענקית, ‏D:). עם מצב כמו
#: SGDISK_WIN_REC: הגדלים של 3 ו-4 נזכרים אחרי `-d N -n N:start:end` ונקראים
#: בחזרה ב-`-i`; ה**תחילות** קבועות — D: אינה זזה על המקור (הכרעת נדב 17/09).
SGDISK_C_THEN_D = f'''#!/bin/sh
echo "$*" >> "$RUN_DIR/sgdisk.calls"
sizes="$RUN_DIR/sgdisk.sizes"
[ -f "$sizes" ] || printf '3={WIN_SECTORS}\\n4={DATA_SECTORS}\\n' > "$sizes"
[ "$1" = "-a" ] && shift 2
if [ "$1" = "-d" ]; then
  idx="$2"; spec="$4"
  start=${{spec#*:}}; start=${{start%%:*}}; end=${{spec##*:}}
  n=$((end - start + 1))
  sed -i "s/^$idx=.*/$idx=$n/" "$sizes"
  exit 0
fi
if [ "$1" = "-i" ]; then
  n=$(sed -n "s/^$2=//p" "$sizes")
  case "$2" in
    1) echo "Partition GUID code: C12A7328-F81F-11D2-BA4B-00A0C93EC93B (EFI system partition)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000001"
       echo "First sector: 2048 (at 1024.0 KiB)"
       echo "Partition size: 204800 sectors (100.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'EFI system partition'" ;;
    2) echo "Partition GUID code: E3C9E316-0B5C-4DB8-817D-F92DF00215AE (Microsoft reserved)"
       echo "Partition unique GUID: 4C7B1E00-0000-4000-8000-000000000002"
       echo "First sector: 206848 (at 101.0 MiB)"
       echo "Partition size: 32768 sectors (16.0 MiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Microsoft reserved partition'" ;;
    3) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {WIN_UGUID}"
       echo "First sector: {WIN_START} (at 530.0 MiB)"
       echo "Partition size: $n sectors (huge)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Basic data partition'" ;;
    4) echo "Partition GUID code: {WIN_GUID} (Microsoft basic data)"
       echo "Partition unique GUID: {DATA_UGUID}"
       echo "First sector: {DATA_START} (at the end)"
       echo "Partition size: $n sectors (50.0 GiB)"
       echo "Attribute flags: 0000000000000000"
       echo "Partition name: 'Data'" ;;
  esac
  exit 0
fi
echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
echo "   1            2048          206847   100.0 MiB   EF00  EFI system partition"
echo "   2          206848          239615   16.0 MiB    0C01  Microsoft reserved partition"
echo "   3         {WIN_START}      {WIN_START + WIN_SECTORS - 1}   476.0 GiB   0700  Basic data partition"
echo "   4       {DATA_START}       {DATA_START + DATA_SECTORS - 1}   50.0 GiB    0700  Data"
'''

#: ‏ntfsresize שמודד לפי המחיצה: ‏sda3 → MIN_BYTES, ‏sda4 → MIN_D. ‏NTFSRESIZE_FAIL_ON
#: מפיל את `-s` על מחיצה אחת בלבד; ‏NTFSRESIZE_INFO_FAIL_ON — את `--info` שלה;
#: ‏NTFSRESIZE_GROW_FAIL_ON — את המתיחה בחזרה (`-f -f`) שלה.
NTFSRESIZE_TWO = f'''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/order.log"
case "$*" in
  *--info*)
    [ -n "${{NTFSRESIZE_INFO_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_INFO_FAIL_ON") echo "ERROR: NTFS is inconsistent. Run chkdsk /f"; exit 1 ;; esac
    case "$*" in *3) min={MIN_BYTES}; cur=$(({WIN_SECTORS} * 512 - 512)) ;; *) min={MIN_D}; cur=$(({DATA_SECTORS} * 512 - 512)) ;; esac
    echo "Cluster size       : 4096 bytes"
    echo "Current volume size: $cur bytes"
    echo "You might resize at $min bytes or $((min / 1000000)) MB (freeing some MB)."
    exit 0 ;;
  *"-f -f"*)
    [ -n "${{NTFSRESIZE_GROW_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_GROW_FAIL_ON") exit 5 ;; esac ;;
  "-n "*) echo "Needed relocations : 2560 (10 MB)"; exit 0 ;;
  "-s "*|*" -s "*)
    IFS= read -r ans; echo "ntfsresize stdin=${{ans:-EOF}}" >> "$RUN_DIR/order.log"
    [ -n "${{NTFSRESIZE_FAIL_ON:-}}" ] && case "$*" in *"$NTFSRESIZE_FAIL_ON") echo "ERROR: resize failed"; exit 3 ;; esac ;;
esac
exit 0
'''

#: ‏partclone שנופל על מחיצה 4 בלבד — כשל באמצע הזרם, אחרי ששתיהן כווצו.
PARTCLONE_FAILS_ON_4 = ('#!/bin/sh\necho "partclone $*" >> "$RUN_DIR/order.log"\n'
                        'case " $* " in *"/lc-cut-4 "*) echo "read error on the view of sda4" >&2; exit 1 ;; esac\n'
                        'head -c 4096 /dev/zero\nexit 0\n')

FS_MAP_C_D = ('_fs_of() { case "$1" in *3|*4) echo ntfs ;; *) echo vfat ;; esac; }; '
              'capture_ntfs_hibernation_reason() { return 0; }; '
              # ‏#1369: ‏C: היא המערכת; ‏D: — אותו GUID, בלי Windows\System32 — היא data.
              '_system_role() { case "$2" in *3) echo windows ;; *) echo data ;; esac; }; '
              'disk_serial() { printf S926; }; disk_port() { printf 1; }; ')
FS_MAP_C_D += view_sys({3: WIN_SECTORS, 4: DATA_SECTORS})
FS_MAP_C_EXT4 = FS_MAP_C_D.replace("*3|*4) echo ntfs", "*3) echo ntfs ;; *4) echo ext4")

SSD_256 = "256060514304"


def two_stubs(**extra: str) -> dict[str, str]:
    return stubs(sgdisk=SGDISK_C_THEN_D, ntfsresize=NTFSRESIZE_TWO, **extra)


def two_targets() -> tuple[int, int, int, int]:
    """(C: חדש, D: חדש, ההפרש של C: בסקטורים, ההפרש של D: בסקטורים)."""
    new_c = shrink_target(MIN_BYTES, WIN_SECTORS * 512)
    new_d = shrink_target(MIN_D, DATA_SECTORS * 512)
    return new_c, new_d, (WIN_SECTORS * 512 - new_c) // 512, (DATA_SECTORS * 512 - new_d) // 512


def positions(steps: list[str], *prefixes: str) -> list[int]:
    """המקום של כל קידומת ב-order.log, בסדר שנתבקש — נופל בשם על מה שחסר."""
    found = []
    after = 0
    for prefix in prefixes:
        for i, line in enumerate(steps):
            if i >= after and line.startswith(prefix):
                found.append(i)
                after = i + 1
                break
        else:
            raise AssertionError(f"{prefix!r} לא נמצא אחרי {after} ב-{steps}")
    return found


def rewritten_indexes(run: Path) -> list[str]:
    return [c.split(" -d ")[1].split(" ")[0] for c in rewrites(run)]


def test_c_and_d_are_both_shrunk_on_their_views_and_the_source_is_never_written(tmp_path):
    """#929 + #1466: **כל** מחיצת NTFS-data מכווצת -- כל אחת על עותק משלה
    (ram0, ‏ram1), שתי המדידות לפני העותק הראשון, ‏C: ואז D:, הזרם מהעותקים,
    ואז שחרור. אין רשומה בשרת ואין שורה בטבלת המקור."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    new_c, new_d, _, _ = two_targets()
    dev = posix(box / "dev")
    steps = order(run)
    positions(steps,
              f"ntfsresize --info --no-progress-bar {dev}/sda3",
              f"ntfsresize --info --no-progress-bar {dev}/sda4",
              "modprobe brd rd_nr=2",
              f"dmsetup create lc-src-3 --table 0 {WIN_SECTORS} snapshot {dev}/sda3 {dev}/ram0 N 8",
              f"dmsetup create lc-src-4 --table 0 {DATA_SECTORS} snapshot {dev}/sda4 {dev}/ram1 N 8",
              f"ntfsresize -s {new_c} --no-progress-bar {dev}/mapper/lc-src-3", f"ntfsfix -d {dev}/mapper/lc-src-3",
              f"ntfsresize -s {new_d} --no-progress-bar {dev}/mapper/lc-src-4", f"ntfsfix -d {dev}/mapper/lc-src-4",
              "partclone", "dmsetup remove --retry lc-cut-4", "dmsetup remove --retry lc-cut-3",
              "dmsetup remove --retry lc-src-4", "dmsetup remove --retry lc-src-3", "rmmod brd")
    assert source_writes(steps, box) == [] and not rewrites(run), source_writes(steps, box)
    assert "shrink-open" not in steps and "shrink-close" not in steps


def test_the_source_gpt_is_never_written(tmp_path):
    """הטבלה על המקור לא נכתבה כלל: אף `sgdisk -d/-n`, והזיוף לא זכר גודל חדש."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    assert not rewrites(run)
    assert not (run / "sgdisk.sizes").exists() or \
        (run / "sgdisk.sizes").read_text(encoding="utf-8") == f"3={WIN_SECTORS}\n4={DATA_SECTORS}\n"


def test_the_manifest_compacts_both_and_marks_the_system_expandable(tmp_path):
    """היעד מקבל את הפריסה הצפופה: ‏C: בגודלה החדש במקומה; ‏D: זזה אחורה
    בהפרש של C: (‏source_start_sector זוכר את מקומה האמיתי) ובגודלה החדש;
    והרצפה היא סכום המינימומים ומחיצות המערכת.

    ‏expandable על **C:** — מאז #1369 ‏D: היא `data` (אותו GUID, בלי
    Windows/System32), ו-expand_last בוחר את מחיצת המערכת האחרונה. עד
    #1369 ‏D: הייתה "windows" מתוך ה-GUID, ולכן היא זו שנמתחה."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    new_c, new_d, delta_c, _ = two_targets()

    win = part(m, 3)
    assert win["size_bytes"] == new_c and win["shrunk_from_bytes"] == WIN_SECTORS * 512
    assert win["start_sector"] == WIN_START and "source_start_sector" not in win
    assert win["expandable"] is True, "מחיצת המערכת היא שנמתחת על היעד (#1369)"

    data = part(m, 4)
    assert data["size_bytes"] == new_d and data["shrunk_from_bytes"] == DATA_SECTORS * 512
    assert data["start_sector"] == DATA_START - delta_c
    assert data["source_start_sector"] == DATA_START
    assert data["start_sector"] % 2048 == 0
    assert data["role"] == "data" and data["expandable"] is False

    for idx in (1, 2):
        assert "shrunk_from_bytes" not in part(m, idx) and "source_start_sector" not in part(m, idx)

    end = data["start_sector"] * 512 + data["size_bytes"]
    assert m["min_target_bytes"] == end + 256 * 1024  # #1171
    assert m["min_target_bytes"] < 140 * 10**9
    from server.images import required_bytes
    assert required_bytes(m) == m["min_target_bytes"]


def test_c_before_d_now_fits_a_256_drive(tmp_path):
    """הסירוב הישן של #929 ("‏C: אינה האחרונה — מחקו את D:") נעלם: שתיהן
    מכווצות, והאימג' נכנס ל-SSD של 256 — ‏target_floor_bytes נרשם."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"CAPTURE_TARGET_BYTES": SSD_256})
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8", errors="replace")
    m = manifest(run)
    assert m["target_floor_bytes"] == int(SSD_256)
    assert m["min_target_bytes"] < int(SSD_256)


def test_one_question_lists_every_partition(tmp_path):
    """שאלה **אחת** למפעיל, עם הטבלה של כולן — לא שאלה למחיצה. ובביטול
    המספרים של שתיהן מגיעים לקונסולה (שדה error)."""
    asked = ('shrink_ask() { echo asked >> "$RUN_DIR/order.log"; cat "$1" > "$RUN_DIR/asked.plan"; echo cancel; }; ')
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + asked)
    reason = refusal_reason(box, run, out)
    assert order(run).count("asked") == 1
    plan = (run / "asked.plan").read_text(encoding="utf-8").splitlines()
    assert [ln.split("|")[0] for ln in plan] == ["3", "4"], plan
    new_c, new_d, _, _ = two_targets()
    assert "מחיצה 3" in reason and "מחיצה 4" in reason, reason
    assert f"{new_c / 1e9:.1f} GB" in reason and f"{new_d / 1e9:.1f} GB" in reason, reason
    assert not rewrites(run) and not [s for s in order(run) if s.startswith("ntfsresize -s")]


def test_a_failure_in_the_second_stream_releases_both_views(tmp_path):
    """‏partclone נופל על העותק של D: אחרי ששתיהן כווצו: הקליטה נכשלת בשם,
    ושני העותקים משוחררים -- המקור לא נכתב, ואין מה להחזיר."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(**{"partclone.ntfs": PARTCLONE_FAILS_ON_4}),
                                shell_pre=FS_MAP_C_D + SAY_YES)
    reason = refusal_reason(box, run, out)
    assert "מחיצה 4" in reason, reason   # #1503: the capture's refusal is Hebrew on the screen
    steps = order(run)
    positions(steps, "dmsetup remove --retry lc-src-4", "dmsetup remove --retry lc-src-3", "rmmod brd")
    assert source_writes(steps, box) == [] and not rewrites(run)
    assert not (run / "new-manifest.json").exists()


def test_a_failed_shrink_of_the_second_view_fails_by_name_and_writes_nothing(tmp_path):
    """‏ntfsresize -s נופל על העותק של D: אחרי ש-C: כבר כווצה (בעותק שלה):
    הקליטה נכשלת בשם (מחיצה 4, ‏rc=3), שני העותקים משוחררים, המקור לא נכתב."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_FAIL_ON": "lc-src-4"})
    reason = refusal_reason(box, run, out)
    assert "מחיצה 4" in reason and "rc=3" in reason and "המקור לא נכתב" in reason, reason
    assert source_writes(order(run), box) == [] and not rewrites(run)
    assert "rmmod brd" in order(run)


def test_a_partition_that_cannot_be_measured_refuses_the_whole_capture(tmp_path):
    """‏--info נופל על D: (מלוכלכת): לא "מכווצים חלקית" — **כל** הקליטה
    מסורבת בשם, לפני שאלה, לפני רשומה, לפני בייט."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_INFO_FAIL_ON": "4"})
    reason = refusal_reason(box, run, out)
    # #1502: the stub's "NTFS is inconsistent" is named as such, with the fix
    assert "מחיצה 4" in reason and "לא עקבית" in reason and "chkdsk" in reason, reason
    steps = order(run)
    assert steps == [s for s in steps if s.startswith("ntfsresize --info")], steps
    assert not rewrites(run) and not (run / "new-manifest.json").exists()


def test_a_non_ntfs_data_partition_is_named_when_the_image_still_does_not_fit(tmp_path):
    """‏C: (NTFS) כווצה, ‏D: היא ext4 — לא מכווצת כאן (מסלול e2fsck נפרד) —
    והאימג' עדיין גדול מהיעד: הסירוב אומר איזו מחיצה, למה, ומה לעשות."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_EXT4 + SAY_YES,
                                env={"CAPTURE_TARGET_BYTES": "128000000000"})
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason, reason
    assert "מחיצה 4" in reason and "ext4" in reason and "NTFS בלבד" in reason, reason
    assert f"{DATA_SECTORS * 512 / 1e9:.1f} GB" in reason, reason
    assert "אינה האחרונה" not in reason, "ההודעה הישנה של #929 — הכיווץ כבר אינו מוגבל לאחרונה"
    # ‏C: כווצה על העותק שלה (המקור לא נכתב); ‏D: לא נמדדה אפילו.
    assert not rewrites(run) and source_writes(order(run), box) == []
    assert [st for st in order(run) if st.startswith("dmsetup create lc-src-3")]
    assert not [s for s in order(run) if s.startswith("ntfsresize --info") and s.endswith("sda4")]


def test_the_old_refusal_is_unchanged_when_the_last_partition_is_the_big_one(tmp_path):
    """‏ESP/C:/recovery, בלי כיווץ: אין מחיצת data שאינה NTFS, וההודעה היא זו
    של #87 — בלי תוספת."""
    box, run, out = capture_run(tmp_path, stubs=stubs(), shell_pre=FS_MAP + SAY_PLAIN,
                                env={"CAPTURE_TARGET_BYTES": SSD_256})
    reason = refusal_reason(box, run, out)
    assert "הפריסה גדולה מכונן היעד" in reason, reason
    assert "אינה האחרונה" not in reason and "NTFS בלבד" not in reason, reason
    assert reason.endswith("בייט לפני הקליטה"), reason


def test_on_the_target_c_is_stretched_and_d_moves_to_the_tail(tmp_path):
    """הצד השני, על היעד: ‏apply_gpt/expand_last על המניפסט המצומצם —
    ‏D: (‏data מאז #1369) עוברת לסוף הכונן בגודלה המכווץ, ו-C: נמתחת
    במקומה עד תחילתה. עד #1369 ‏D: נמתחה ו-C: נשארה מכווצת."""
    from test_agent import run_expand
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES)
    assert out.strip().endswith("rc=0"), out
    m = manifest(run)
    plan = ["|".join(str(p.get(k, "")) for k in ("index", "type_guid", "role", "fs", "start_sector", "size_bytes",
                                                   "file", "sha256")) + f"|{str(p['expandable']).lower()}|{p['unique_guid']}"
            for p in m["partitions"]]
    rc, calls, marker = run_expand(tmp_path / "target", plan, disk_sectors=976773168)
    assert rc == "rc=0", (rc, calls)
    assert marker == "3|ntfs", marker
    d_sectors = part(m, 4)["size_bytes"] // 512
    call = __import__("test_agent").one_call(calls)   # #1714: one sgdisk call
    assert call.startswith("-a 1 -d 4 " + __import__("test_agent").tail_n(4, d_sectors)[0] + " "), \
        "‏D: נבראת בזנב, בגודל שהמניפסט מצהיר"
    assert f" -d 3 -n 3:{WIN_START}:0 " in call, "‏C: נשארת בתחילתה ונמתחת"


# --- הנעילות המבניות ------------------------------------------------------------


def test_the_shrink_runs_after_the_hibernation_gate_and_before_the_layout_math():
    lines = capture_body()
    gate = first_index(lines, "capture_ntfs_hibernation_reason")
    shrink = first_index(lines, "capture_prepare_views")
    layout = first_index(lines, "_min_target=$(awk")
    assert gate < shrink < layout, (gate, shrink, layout)
    assert shrink < first_index(lines, "partclone_for_fs")


def test_the_agent_loads_shrink_before_capture():
    src = (AGENT / "imagectl-agent").read_text(encoding="utf-8")
    assert src.index('"$LIB_DIR/shrink.sh"') < src.index('"$LIB_DIR/capture.sh"')


def test_every_shrink_step_checks_its_exit_code():
    """אין `|| true` ואין `2>/dev/null` על צעד הרסני (עיקרון 5) -- בהחזרה
    (shrink.sh, לדיסקים שכווצו לפני #1466) ובעותק (lcsnap.sh)."""
    src = (AGENT / "lib" / "shrink.sh").read_text(encoding="utf-8")
    snap = (AGENT / "lib" / "lcsnap.sh").read_text(encoding="utf-8")
    assert "|| true" not in src and "|| true" not in snap
    assert "ntfsfix -b" not in src + snap, "‏-b מוחק את רשימת הקלאסטרים הפגומים"
    for text, tools in ((src, ("ntfsfix -d", "ntfsresize -f -f", "sgdisk -a 1 -d", "blockdev --rereadpt")),
                        (snap, ("ntfsfix -d", "ntfsresize -s", "dmsetup", "rmmod brd"))):
        for tool in tools:
            assert tool in text, tool
            for line in text.splitlines():
                if tool in line and not line.strip().startswith("#"):
                    assert "2>/dev/null" not in line, line


def test_the_capture_never_shrinks_the_source_in_place():
    """#1466: הכיווץ במקום הוסר מהקליטה -- אין `ntfsresize -s` ב-shrink.sh,
    ואין רשומת shrink-open בסוכן."""
    src = (AGENT / "lib" / "shrink.sh").read_text(encoding="utf-8")
    code = "\n".join(line.split("#")[0] for line in src.splitlines())
    assert "ntfsresize -s" not in code and "shrink_open_record" not in code
    mem = (AGENT / "lib" / "shrinkmem.sh").read_text(encoding="utf-8")
    assert "shrink_open_record" not in "\n".join(line.split("#")[0] for line in mem.splitlines())


def test_the_interfaces_document_carries_the_new_fields():
    text = (REPO / "docs" / "interfaces.md").read_text(encoding="utf-8")
    for field in ("shrunk_from_bytes", "source_start_sector"):
        assert f"`{field}`" in text, field
    assert "הכיווץ עצמו עדיין אינו ממומש" not in text
