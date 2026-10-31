"""‏#874 — כשל כתיבה משאיר ראיה, לא סימון על הדיסק (הצד של הסוכן).

הכרעת נדב 16/09: הסימון ‏IMAGESRV-FAILED של #845 נפסל — הסוכן אינו משנה
דיסק מחוץ לשיכפול. ‏`agent/lib/restore/failmark.sh` עושה במקומו שני דברים:

* **בכשל** (‏`fail_written_target`): ‏`target_set failed`, ואיסוף שורות
  ה-ATA של הקרנל על הפורט של היעד — ‏`dmesg` מסונן ל-`ata<N>` /
  ‏`I/O error, dev <dev>`, מאז ‏`target_init` (‏`since`), עד 40 — יחד עם
  הסידורי והחריץ (‏`ident`). **אף קריאה ל-`sgdisk`**: ‏`sgdisk.calls` ריק.
  הדיווח (‏`build_progress`) מצרף אותם ליעד `failed` כמות שהם — הסיווג
  (כבל/דיסק) הוא של השרת, לא של הסוכן.
* **באתחול**: ‏`disk_failures_refresh` קורא את `disk_failures` מתשובת
  ה-hello (זיכרון השרת, לפי סידורי או חריץ), ו-`disk_failure_cause`
  עונה ממנו למסך הממתין ולשער — בלי `sgdisk -p`.

‏jq אינו קיים בתחנת הפיתוח, ולכן ‏`json_get_join` (המקום היחיד שנוגע
ב-jq) מזויף כאן ומדפיס מה ש-jq היה מדפיס; הביטוי עצמו רץ ב-`requires_jq`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_drawer_evidence import MIXED_FANOUT
from test_restore_evidence import build_box, restore_run
from test_stream_honesty import GOOD_FANOUT, GOOD_SHA, call, drawer_run, errors_of
from test_timeouts import log_of, make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))

JQ = shutil.which("jq")
requires_jq = pytest.mark.skipif(JQ is None, reason="jq אינו מותקן — הביטוי לא היה רץ בכלל")

#: ‏sgdisk מזויף שרק רושם: כל קריאה היא ראיה שהסוכן נגע בדיסק.
SGDISK_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "{box}/sgdisk.calls"
echo "Number  Start (sector)    End (sector)  Size       Code  Name"
exit 0
"""

#: ‏dmesg מזויף: שורות ata1 (הפורט של sda) ו-ata2 (השכן), לפני ואחרי
#: ה-`since` של היעד (uptime 300), ושורת I/O error לפי שם ההתקן.
DMESG = "\n".join([
    "[    5.100000] ata1: SATA link up 6.0 Gbps (SStatus 133 SControl 300)",
    "[    5.200000] ata2: SATA link up 6.0 Gbps (SStatus 133 SControl 300)",
    "[  120.000000] ata1.00: exception Emask 0x10 SAct 0x0 SErr 0x4050000 action 0xe frozen",
    "[  312.401172] ata1.00: exception Emask 0x50 SAct 0x1ff0 SErr 0x480900 action 0x6 frozen",
    "[  312.401183] ata1: SError: { UnrecovData HostInt 10B8B Handshk }",
    "[  312.401201]          res 40/00:00:00:00:00/00:00:00:00:00/00 Emask 0x50 (ATA bus error)",
    "[  312.500000] ata2.00: configured for UDMA/133",
    "[  318.101172] ata1: limiting SATA link speed to 1.5 Gbps",
    "[  320.000000] blk_update_request: I/O error, dev sda, sector 30720 op 0x1:(WRITE)",
    "[  320.000001] blk_update_request: I/O error, dev sdb, sector 1 op 0x1:(WRITE)",
    "[  321.000000] ata12: EH complete",
    '[  322.000000] ata1.00: quoted "text" and a back\\slash',
]) + "\n"


def stub_for(box: Path) -> str:
    return SGDISK_STUB.format(box=posix(box))


def sgdisk_calls(box: Path) -> list[str]:
    path = box / "sgdisk.calls"
    return path.read_text(encoding="utf-8").splitlines() if path.exists() else []


def zap_calls(box: Path) -> list[str]:
    return [c for c in sgdisk_calls(box) if c.startswith("--zap-all ")]


def rename_calls(box: Path) -> list[str]:
    """קריאות שמשנות **שם** מחיצה (`-c`) — מה ש-#845 עשה. ‏`-n`/`-t`/`-e`
    הן של apply_gpt, לפני הזרם, ונשארות — ו-#1798 ‏(`-c i:imagesrv-incomplete`)
    רוכב על אותה קריאה של הטבלה (`-n`), לפני הזרם: זו אינה סימון של כשל."""
    return [c for c in sgdisk_calls(box)
            if (c.startswith("-c ") or " -c " in c) and " -n " not in f" {c}"]


def error_of(run: Path, dev: str) -> str:
    path = run / "targets" / dev / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


#: הזהות מוזרקת: ‏sysfs אמיתי דורש symlink ל-ataN (test_agent מדלג עליו
#: בווינדוס). ‏ata_port_of עצמו נבדק למטה עם readlink מזויף.
IDENT = ('disk_serial() { case "$1" in sda) printf S3TWNE0JB04745 ;; sdb) printf SB ;; esac; }; '
         'disk_port() { case "$1" in sda) printf 1 ;; sdb) printf 2 ;; esac; }; '
         'ata_port_of() { case "$1" in sda) printf 1 ;; sdb) printf 2 ;; esac; }; ')

LIBS = (f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/failmark.sh; ")


def fail_run(tmp_path, *, script, state="writing", error="", since="300", dmesg=DMESG,
             sysroot_uptime: str | None = None, write_started=True):
    box = tmp_path / "box"
    run = box / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text(state + "\n", newline="\n")
    if error:
        (run / "targets" / "sda" / "error").write_text(error + "\n", encoding="utf-8", newline="\n")
    if since is not None:
        (run / "targets" / "sda" / "since").write_text(since + "\n", newline="\n")
    if write_started:
        (run / "targets" / "sda" / "write_started").write_text("S3TWNE0JB04745\n", newline="\n")
    sysroot = box / "sysroot"
    (sysroot / "proc").mkdir(parents=True)
    if sysroot_uptime is not None:
        (sysroot / "proc" / "uptime").write_text(sysroot_uptime + "\n", newline="\n")
    # ה-dmesg בקובץ ולא ב-here-document בתוך הסקריפט: ‏MSYS bash חותך ארגומנט
    # של `-c` ב-8,186 תווים (נמדד, #890), וה-fixture מהברזל לבדו הוא 10K.
    (box / "dmesg.txt").write_text(dmesg, encoding="utf-8", newline="\n")
    out = run_sh(
        make_stubs(box / "stubs", {"sgdisk": stub_for(box),
                                   "dmesg": f"#!/bin/sh\ncat {posix(box)!r}/dmesg.txt\n"})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)}/dev SYSROOT={posix(sysroot)!r}; "
        + LIBS + IDENT + script
    )
    return box, run, out


# --- הכשל: ראיה בלי לגעת בדיסק ------------------------------------------------


def test_a_failed_target_wipes_the_gpt_after_recording_failure(tmp_path):
    """הבקרה המרכזית של #874: אחרי כשל **אין** קריאה ל-sgdisk כלל. בקרה
    שלילית: על main (#845) ‏sgdisk.calls מכיל `-p` ואז `-c 1:IMAGESRV-FAILED`."""
    box, run, out = fail_run(tmp_path, script='fail_written_target sda "partition 3: stream cut"; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert zap_calls(box) == [f"--zap-all {posix(box)}/dev/sda"]
    assert (run / "targets" / "sda" / "state").read_text().strip() == "failed"
    assert error_of(run, "sda") == "partition 3: stream cut"


def test_a_target_rejected_before_writing_is_failed_without_any_wipe(tmp_path):
    box, run, _ = fail_run(tmp_path, write_started=False,
                           script='fail_written_target sda "serial changed — not written"')
    assert sgdisk_calls(box) == []
    assert error_of(run, "sda") == "serial changed — not written"
    # ‏#1429: לא נוסתה כתיבה → אין ראיית כונן, והשרת אינו רושם disk_failure
    assert not (run / "targets" / "sda" / "ata_log.json").exists()


def test_write_start_is_recorded_on_the_vm_path_where_targets_does_not_exist(tmp_path):
    """‏#1427 (פתיחה מחדש): במסלול ה-VM אין `targets/loop0/` — ה-redirect נכשל."""
    _box, run, out = fail_run(tmp_path, write_started=False, script=(
        'disk_serial() { :; }; target_write_started loop0; echo "ws=$?"'))
    assert "ws=0" in out, out
    assert (run / "targets" / "loop0" / "write_started").exists()


def test_a_disk_without_a_serial_is_written_and_never_wiped_on_failure(tmp_path):
    """‏#1427: ל-loop של ה-VM (ולדיסקים וירטואליים אחרים) אין serial. השער של
    ‏#1409 סירב לכתוב אליהם בכלל — כל עדכון VM נכשל. הזהות נדרשת רק לניקוי
    שאחרי כשל, ולכן: הכתיבה ממשיכה, והניקוי לא קורה בלי זהות."""
    box, run, out = fail_run(tmp_path, write_started=False, script=(
        'disk_serial() { :; }; target_write_started sda; echo "ws=$?"; '
        'fail_written_target sda "stream cut"'))
    assert "ws=0" in out, out
    assert sgdisk_calls(box) == [], "GPT נמחק בלי זהות יעד"
    assert (run / "targets" / "sda" / "ata_log.json").exists(), "נכתב ונכשל — הראיה נאספת"
    assert error_of(run, "sda") == "stream cut"


def test_a_written_target_is_not_wiped_after_its_serial_changes(tmp_path):
    box, _, _ = fail_run(tmp_path,
                         script='disk_serial() { printf REPLACEMENT; }; fail_written_target sda "gone"')
    assert sgdisk_calls(box) == []


def test_the_kernel_lines_of_the_targets_port_since_target_init_are_kept(tmp_path):
    """‏dmesg מסונן: רק ata1 (הפורט של sda) ו-`I/O error, dev sda` — לא ata2,
    לא ata12, לא sdb, ולא שורת `res` בלי קידומת — ורק מאז `since` (300):
    השורה ב-120 נופלת."""
    box, run, out = fail_run(tmp_path, script='fail_written_target sda "partition 3: stream cut"')
    lines = (run / "targets" / "sda" / "ata_log").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "[  312.401172] ata1.00: exception Emask 0x50 SAct 0x1ff0 SErr 0x480900 action 0x6 frozen",
        "[  312.401183] ata1: SError: { UnrecovData HostInt 10B8B Handshk }",
        "[  318.101172] ata1: limiting SATA link speed to 1.5 Gbps",
        "[  320.000000] blk_update_request: I/O error, dev sda, sector 30720 op 0x1:(WRITE)",
        '[  322.000000] ata1.00: quoted "text" and a back\\slash',
    ], lines
    assert (run / "targets" / "sda" / "ident").read_text().strip() == "S3TWNE0JB04745|1|1"
    assert "5 kernel lines kept for the report (ata1)" in log_of(run)


def test_without_a_since_stamp_the_whole_log_is_scanned_and_capped_at_40(tmp_path):
    """בלי `since` ובלי `exception` — 40 **הראשונות** בחלון (#890). בקרה
    שלילית: על main ‏`tail -n 40` — ‏line 20..59."""
    long = "\n".join(f"[ {i:4d}.000000] ata1.00: line {i}" for i in range(60)) + "\n"
    box, run, _ = fail_run(tmp_path, since=None, dmesg=long,
                           script='fail_written_target sda "x"')
    lines = (run / "targets" / "sda" / "ata_log").read_text().splitlines()
    assert len(lines) == 40 and lines[0].endswith("line 0") and lines[-1].endswith("line 39")


#: ‏dmesg אמיתי מכשל הכתיבה על פורט 1 במחשב 2 (16/09 07:03, #890): 121 שורות
#: ‏ata1 — ‏exception → SError → 30× WRITE FPDMA QUEUED → hard reset → רעש
#: ACPI, ושוב. שורת `res ... (ATA bus error)` של הקרנל נכתבת **בלי** קידומת
#: ‏ata1 ולכן אינה כאן — הראיה לקו היא `SError: { ... 10B8B Handshk }`.
METAL_0703 = (Path(__file__).parent / "fixtures" / "ata1-cable-failure-0916.txt").read_text(encoding="utf-8")

#: 20 שורות רעש מפורט אחר לפני הכשל — נופלות בסינון הפורט, לא בסינון הרעש.
OTHER_PORT_NOISE = "\n".join(
    f"[20400.{i:06d}] ata2.00: ACPI cmd ef/03:0c:00:00:00:a0(SET FEATURES) filtered out" for i in range(20)
) + "\n"


def test_the_head_of_the_failure_is_kept_and_the_reset_noise_is_dropped(tmp_path):
    """‏#890, נמדד על ברזל: הראיה יושבת **בראש** הרצף (exception → SError →
    failed commands), והרעש שאחרי כל איפוס (`ACPI cmd ... filtered out`,
    ‏`SATA link up`, ‏`configured for UDMA`, ‏`EH complete`) אינו ראיה. נשמרות
    40 השורות הראשונות מה-`exception Emask` הראשון, בלי הרעש. בקרה שלילית:
    על main ‏`tail -n 40` שומר את הזנב — 9 שורות ACPI, ואף שורת SError."""
    box, run, _ = fail_run(tmp_path, since=None, dmesg=OTHER_PORT_NOISE + METAL_0703,
                           script='fail_written_target sda "partition 3: stream cut"')
    lines = (run / "targets" / "sda" / "ata_log").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 40, lines
    assert lines[0].endswith("ata1.00: exception Emask 0x50 SAct 0x78c081ff SErr 0x4c0900 action 0x6 frozen")
    assert lines[1].endswith("ata1.00: irq_stat 0x08000000, interface fatal error")
    assert lines[2].endswith("ata1: SError: { UnrecovData HostInt CommWake 10B8B Handshk }")
    assert lines[3].endswith("ata1.00: failed command: WRITE FPDMA QUEUED")
    assert not any("ACPI cmd" in l or "ata2" in l for l in lines), lines
    assert "40 kernel lines kept for the report (ata1)" in log_of(run)


def test_the_noise_after_a_reset_is_dropped_even_when_the_head_is_short(tmp_path):
    """כשל קצר: exception, איפוס, ורעש — ואחרי ההשמטה נשארות רק שורות הראיה.
    שורה של הפורט **לפני** ה-exception (הקישור עלה) אינה נשמרת: ההתחלה
    היא ה-exception, לא תחילת החלון."""
    short = "\n".join([
        "[  400.000000] ata1: SATA link up 6.0 Gbps (SStatus 133 SControl 300)",
        "[  401.000000] ata1.00: configured for UDMA/133",
        "[  405.000000] ata1.00: ready before the failure",
        "[  410.000000] ata1.00: exception Emask 0x10 SAct 0x1 SErr 0x480100 action 0x6 frozen",
        "[  410.000001] ata1: SError: { UnrecovData 10B8B Handshk }",
        "[  410.000002] ata1: hard resetting link",
        "[  410.300000] ata1: SATA link up 3.0 Gbps (SStatus 123 SControl 300)",
        "[  410.300001] ata1.00: ACPI cmd f5/00:00:00:00:00:a0(SECURITY FREEZE LOCK) filtered out",
        "[  410.300002] ata1.00: configured for UDMA/133",
        "[  410.300003] ata1: EH complete",
        "[  411.000000] ata1.00: Read log 0x00 page 0x00 failed, Emask 0x40",
    ]) + "\n"
    box, run, _ = fail_run(tmp_path, dmesg=short, script='fail_written_target sda "x"')
    lines = (run / "targets" / "sda" / "ata_log").read_text(encoding="utf-8").splitlines()
    assert lines == [
        "[  410.000000] ata1.00: exception Emask 0x10 SAct 0x1 SErr 0x480100 action 0x6 frozen",
        "[  410.000001] ata1: SError: { UnrecovData 10B8B Handshk }",
        "[  410.000002] ata1: hard resetting link",
        "[  411.000000] ata1.00: Read log 0x00 page 0x00 failed, Emask 0x40",
    ], lines


def test_the_report_carries_serial_port_and_the_lines_as_json(tmp_path):
    """‏build_progress (ממשק 4): היעד ה-failed נושא serial/port/ata_port/ata_log
    — JSON תקין גם עם מרכאות ולוכסן אחורי בשורת קרנל. יעד שלא נכשל אינו
    נושא אותם. בקרה שלילית: על main אין שדות כאלה בדיווח."""
    box, run, out = fail_run(
        tmp_path,
        script='fail_written_target sda "partition 3: stream cut"; '
               'target_init sdb 100; build_progress ses1 aa:bb:cc:dd:ee:ff')
    report = json.loads(out.strip().splitlines()[-1])
    (sda, sdb) = sorted(report["targets"], key=lambda t: t["dev"])
    assert sda["state"] == "failed" and sda["serial"] == "S3TWNE0JB04745"
    assert sda["port"] == 1 and sda["ata_port"] == 1
    assert len(sda["ata_log"]) == 5
    assert sda["ata_log"][-1] == '[  322.000000] ata1.00: quoted "text" and a back\\slash'
    assert "ata_log" not in sdb and "serial" not in sdb


def test_a_target_that_was_already_failed_keeps_its_evidence(tmp_path):
    """‏#73 דורס את הסיבה בכוונה; הראיה שנאספה בכשל הראשון נשארת, ו-dmesg
    אינו נקרא שוב (השורה שנוספה אחרי הכשל הראשון אינה מופיעה)."""
    box, run, out = fail_run(
        tmp_path,
        script='fail_written_target sda "partition 3: פג הזמן"; '
               'cp "$RUN_DIR/targets/sda/ata_log" "$RUN_DIR/first"; '
               'dmesg() { echo "[  900.0] ata1: later"; }; '
               'fail_written_target sda "partition 3: הפצת הזרם נכשלה"; echo "rc=$?"')
    assert out.strip().endswith("rc=0"), out
    assert error_of(run, "sda") == "partition 3: הפצת הזרם נכשלה"
    assert (run / "targets" / "sda" / "ata_log").read_text() == (run / "first").read_text()


def test_target_init_stamps_since_and_clears_old_evidence(tmp_path):
    box, run, out = fail_run(
        tmp_path, sysroot_uptime="4321.55 8000.00",
        script='fail_written_target sda "x"; target_init sda 100; '
               'cat "$RUN_DIR/targets/sda/since"; ls "$RUN_DIR/targets/sda"')
    listing = out.strip().splitlines()
    assert "4321.55" in listing, listing
    assert not any(n in listing for n in ("ata_log", "ata_log.json", "ident", "error")), listing


def test_ata_port_of_reads_the_ata_component_of_the_device_link(tmp_path):
    box, run, out = fail_run(
        tmp_path,
        script='unset -f ata_port_of; . ' + posix(AGENT) + '/lib/restore/failmark.sh; '
               'readlink() { echo "/sys/devices/pci0000:00/00:1f.2/ata3/host2/target2:0:0/2:0:0:0"; }; '
               'echo "p=$(ata_port_of sda)"; '
               'readlink() { echo "/sys/devices/pci0000:00/vmbus/host1/target1:0:0/1:0:0:0"; }; '
               'echo "v=[$(ata_port_of sda)]"')
    assert "p=3" in out and "v=[]" in out, out


# --- חדר השיכפולים: המגירה שנכשלה בלבד, ובלי sgdisk ----------------------------


#: ‏apply_gpt (שהטסטים כאן עוקפים) רושם write_started לפני הזרם — בלעדיו אין
#: ראיה ואין ניקוי, כי לא נוסתה כתיבה (#1429).
WRITTEN = "target_write_started sda; target_write_started sdb; "


def test_only_the_drawer_that_failed_carries_evidence_and_no_disk_is_renamed(tmp_path):
    """הטופולוגיה של #440/#520: ‏sda שרדה, ‏sdb נפלה בכתיבה. הראיה על sdb
    בלבד, ואף מחיצה — של אף דיסק — אינה משנה שם (#845 הוסר)."""
    # חותמת מעבר ל-uptime של תחנת הבדיקה: ‏target_init רושם `since` מ-/proc/uptime
    # (קיים גם ב-MSYS), ושורה "ישנה" ממנו נופלת בצדק.
    box = tmp_path / "box"
    run, out = drawer_run(
        tmp_path, IDENT + 'dmesg() { echo "[ 99999999.0] ata2.00: error: { UNC }"; }; '
        + WRITTEN + call(GOOD_SHA, disks=("sda", "sdb")),
        {"fanout": MIXED_FANOUT, "sgdisk": stub_for(box)},
        disks=("sda", "sdb"),
    )
    assert out.strip().endswith("rc=0"), out
    assert rename_calls(box) == [], sgdisk_calls(box)
    errors = errors_of(run, disks=("sda", "sdb"))
    assert errors["sda"] == "" and "הכתיבה לדיסק נכשלה" in errors["sdb"], errors
    assert "סומן" not in errors["sdb"]
    assert not (run / "targets" / "sda" / "ata_log").exists()
    assert (run / "targets" / "sdb" / "ata_log").read_text().strip() == "[ 99999999.0] ata2.00: error: { UNC }"


def test_a_wave_that_succeeds_leaves_no_evidence(tmp_path):
    box = tmp_path / "box"
    run, out = drawer_run(
        tmp_path, IDENT + call(GOOD_SHA, disks=("sda", "sdb")),
        {"fanout": GOOD_FANOUT, "sgdisk": stub_for(box)},
        disks=("sda", "sdb"),
    )
    assert out.strip().endswith("rc=0"), out
    assert rename_calls(box) == []
    for dev in ("sda", "sdb"):
        assert not (run / "targets" / dev / "ata_log").exists()
        assert errors_of(run)[dev] == ""


# --- תחנה בודדת: run_restore -----------------------------------------------------


def test_a_station_whose_second_partition_failed_carries_evidence_and_is_not_renamed(tmp_path):
    box, run, prelude = build_box(tmp_path)
    prelude += make_stubs(box / "failmark-stubs", {"sgdisk": stub_for(box)})
    # הזיוף כאן רק רושם, ואינו עונה ל-`sgdisk -i`. הקריאה החוזרת של GUID
    # המחיצות (#1212) נבדקת ב-test_partition_guid.py; כאן נבדק הכשל במחיצה 2.
    prelude += "verify_unique_guids() { true; }; "
    out = run_sh(restore_run(prelude, IDENT + 'dmesg() { echo "[ 99999999.0] ata1: SError: { ICRC }"; }; '
                             + '[ "$5" = 2 ] && return 1; return 0'))
    assert out.strip().endswith("rc=1"), out
    assert rename_calls(box) == [], sgdisk_calls(box)
    assert zap_calls(box) == [f"--zap-all {posix(box)}/dev/sda"]
    assert error_of(run, "sda") == "נכתבו 1 מתוך 3 מחיצות"
    assert (run / "targets" / "sda" / "ata_log").read_text().strip() == "[ 99999999.0] ata1: SError: { ICRC }"


def test_only_the_failed_drawer_has_its_gpt_wiped(tmp_path):
    box = tmp_path / "box"
    run, out = drawer_run(
        tmp_path, IDENT + WRITTEN + call(GOOD_SHA, disks=("sda", "sdb")),
        {"fanout": MIXED_FANOUT, "sgdisk": stub_for(box)}, disks=("sda", "sdb"))
    assert out.strip().endswith("rc=0"), out
    zaps = zap_calls(box)
    # ‏apply_gpt אינו רץ בטסט הזה (הזרם בלבד) — המחיקה היחידה היא של הכשל
    assert zaps == [f"--zap-all {posix(box)}/dev/sdb"], zaps
    assert (run / "targets/sdb/ata_log").exists()


# --- באתחול: אדום מזיכרון השרת, לא מהדיסק ------------------------------------------


def fake_jq(*lines: str) -> str:
    """‏json_get_join מזויף: מה ש-jq היה מדפיס על התשובה — "ok" ואז שורה
    לרשומה — ורק אם קובץ התשובה קיים ואינו ריק (הביטוי האמיתי: requires_jq)."""
    body = " ".join(f"echo {line!r};" for line in ("ok", *lines))
    return 'json_get_join() { [ -s "$1" ] || return 1; ' + body + ' }; '


def hello_answer(*records: dict) -> str:
    return json.dumps({"schema": 1, "known": True, "disk_failures": list(records)}, separators=(",", ":"))


def memory_run(tmp_path, response: str | None, script: str, jq_fn: str):
    box = tmp_path / "box"
    run = box / "run"
    run.mkdir(parents=True)
    if response is not None:
        (run / "response.json").write_text(response, encoding="utf-8", newline="\n")
    out = run_sh(
        make_stubs(box / "stubs", {"sgdisk": stub_for(box)})
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(box)}/dev SYSROOT={posix(box)}/sysroot; "
        + LIBS + IDENT + jq_fn + script
    )
    return run, out, box


def test_a_disk_with_an_open_record_is_red_by_serial_or_by_slot(tmp_path):
    """‏sda תואם לפי סידורי (הרשומה ממכונה אחרת, port ריק); ‏sdb תואם לפי
    החריץ 2 במכונה הזאת (סידורי אחר — הדיסק הוחלף, הכבל לא); ‏sdc לא תואם.
    ‏sgdisk אינו נקרא. בקרה שלילית: על main `disk_failure_cause` אינו קיים,
    ו-last_clone_failed קורא `sgdisk -p` ומוצא דיסק "נקי"."""
    run, out, box = memory_run(
        tmp_path,
        hello_answer({"serial": "S3TWNE0JB04745", "port": None, "cause": "cable", "at": "t"},
                     {"serial": "OTHER", "port": 2, "cause": "disk", "at": "t"}),
        'disk_failures_refresh; echo "refresh=$?"; '
        'disk_port() { case "$1" in sda) printf 1 ;; sdb) printf 2 ;; sdc) printf 3 ;; esac; }; '
        'echo "a=$(disk_failure_cause sda)/$?"; echo "b=$(disk_failure_cause sdb)/$?"; '
        'echo "c=$(disk_failure_cause sdc)/$?"; disk_failure_cause sda >/dev/null; echo "red=$?"; '
        'disk_failure_cause sdc >/dev/null; echo "clean=$?"',
        fake_jq("S3TWNE0JB04745||cable", "OTHER|2|disk"))
    assert "refresh=0" in out, out
    assert "a=cable/0" in out and "b=disk/0" in out and "c=/1" in out, out
    assert "red=0" in out and "clean=1" in out, out
    assert sgdisk_calls(box) == []


def test_an_unreadable_answer_keeps_the_previous_memory_not_an_empty_one(tmp_path):
    """עיקרון 5: תשובה שלא נקראה (jq נפל, קובץ באמצע כתיבה) אינה "אין
    כשלים" — ‏disk_failures הקודם נשאר, refresh מחזיר 1, ואין קובץ .next."""
    run, out, _ = memory_run(
        tmp_path, hello_answer({"serial": "S3TWNE0JB04745", "port": None, "cause": "cable", "at": "t"}),
        'disk_failures_refresh; echo "first=$?"; '
        'json_get_join() { echo "{\\"schema\\":1,\\"disk_fail"; return 5; }; '
        'disk_failures_refresh; echo "second=$?"; '
        'echo "a=$(disk_failure_cause sda)"; [ -f "$RUN_DIR/disk_failures.next" ] && echo LEFTOVER; true',
        fake_jq("S3TWNE0JB04745||cable"))
    assert "first=0" in out and "second=1" in out and "a=cable" in out, out
    assert "LEFTOVER" not in out


def test_no_memory_at_all_is_not_red(tmp_path):
    run, out, _ = memory_run(
        tmp_path, None,
        'disk_failures_refresh; echo "refresh=$?"; disk_failure_cause sda >/dev/null; echo "red=$?"',
        fake_jq())
    assert "refresh=1" in out and "red=1" in out, out


def test_an_answer_without_failures_is_read_as_clean(tmp_path):
    run, out, _ = memory_run(
        tmp_path, hello_answer(),
        'disk_failures_refresh; echo "refresh=$?"; disk_failure_cause sda >/dev/null; echo "red=$?"',
        fake_jq())
    assert "refresh=0" in out and "red=1" in out, out


@requires_jq
def test_the_jq_expression_renders_the_hello_answer(tmp_path):
    """הביטוי האמיתי, על jq אמיתי (מעבדת ה-VM): "ok" ואז שורה לרשומה, ‏null → ריק;
    תשובה בלי השדה (שרת ישן) = "ok" בלבד; JSON קטוע = כשל, לא "נקי"."""
    run, out, _ = memory_run(
        tmp_path,
        hello_answer({"serial": "S3TWNE0JB04745", "port": None, "cause": "cable", "at": "t"},
                     {"serial": None, "port": 2, "cause": "unclassified", "at": "t"}),
        'disk_failures_refresh; echo "refresh=$?"; cat "$RUN_DIR/disk_failures"', "")
    assert out.strip().splitlines()[-4:] == ["refresh=0", "ok", "S3TWNE0JB04745||cable", "|2|unclassified"], out
    _, out2, _ = memory_run(tmp_path / "e", '{"schema":1,"known":true}',
                            'disk_failures_refresh; echo "refresh=$?"; cat "$RUN_DIR/disk_failures"', "")
    assert out2.strip().splitlines()[-2:] == ["refresh=0", "ok"], out2
    _, out3, _ = memory_run(tmp_path / "t", '{"schema":1,"disk_failures":[{"serial":"S',
                            'disk_failures_refresh; echo "refresh=$?"', "")
    assert out3.strip().endswith("refresh=1"), out3
