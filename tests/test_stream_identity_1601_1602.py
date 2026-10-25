"""‏#1601 ו-#1602 — שני ממצאים צדדיים של #1598 (מחשב שיכפול 2, 17/10).

‏**#1601** — בתחנה בודדת (`restore.sh`) כשל של **הזרם** נרשם כ-disk_failure
על הדיסק התקין: ‏`run_restore` קרא ‏`fail_written_target` בלי הקשר, ולכן
נאספה ראיית ATA, ‏`failure_json` צירף ‏`ata_log`, והשרת רשם את הדיסק
(‏`failed_last` אדום בסבב הבא). התיקון: ‏`restore_blame` (failmark.sh) מאשים
את הדיסק **רק על ראיה חיובית** — ‏fanout איבד את הקורא שלו תחת זרם זורם
(‏`write error` / ‏`stalled`), או שהזרם הגיע שלם (sha תואם) והכתיבה בכל זאת
נכשלה. כל השאר — ‏`stream`, בלי ‏ata_log, כמו המגירות ב-#1603.

‏**#1602** — מגירה שיצאה מ-sysfs איבדה פורט, סידורי ושורת קרנל: ‏`disk_port`
ו-`ata_port_of` קוראים את ה-sysfs **ברגע הכשל**, אחרי שהכונן כבר איננו.
על ברזל הגואי הראה ‏`drawer=0|sdb`. התיקון: ‏`target_init` שומר את הזהות
(‏`seen`), והיא משמשת כשה-sysfs ריק — בראיה (‏`ident`) ובגואי. ‏`ata_capture`
תופס גם ‏`device offline error, dev <d>,` ולא רק ‏`I/O error`.

הטסטים של #1601 מריצים את ‏`run_restore` האמיתי עם ‏**fanout האמיתי**
(מקומפל מ-`agent/fanout.c`); מה שלפני הזרם (טבלה, בדיקות מקדימות) מוחלף
בפונקציות ריקות, כי הוא אינו הנבדק.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, posix
from test_timeouts import log_of, make_stubs, run_sh

REPO = Path(__file__).resolve().parent.parent
pytestmark = requires_native("gcc", posix=True, why="fanout האמיתי צריך gcc, ו-sysfs מזויף צריך symlink")

#: ‏24MB: יותר מהחוצץ של fanout בתחנה (‏16MB, ‏restore.sh) ועוד ה-fifo
#: והצינור — כך ש-partclone שנתקע באמת עוצר את fanout עד הפינוי.
PAYLOAD = hashlib.sha256(b"1601").digest() * (24 * 1024 * 1024 // 32)
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()
PLAN = (f"3|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|data|dd|2048|{len(PAYLOAD)}"
        f"|p3.data.dd.zst|{GOOD_SHA}|false|||\n")

#: ‏partclone מזויף לפי ‏$PCL: ‏ok כותב הכול; ‏dies כותב 1MB ויוצא 1 (כונן
#: שדחה כתיבה); ‏hangs בולע 2MB ומפסיק לקרוא (כונן תקוע); ‏fails_at_end
#: כותב הכול ויוצא 1 (הזרם שלם, הכתיבה נכשלה).
PARTCLONE = r"""#!/bin/sh
node=
while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done
case "$PCL" in
  ok)           exec cat > "$node" ;;
  dies)         head -c 1048576 > "$node"; exit 1 ;;
  hangs)        head -c 2097152 > /dev/null; echo $$ >> "$BOX/pids"; exec sleep 40 ;;
  fails_at_end) cat > "$node"; exit 1 ;;
esac
"""

#: מקור הזרם לפי תרחיש. ‏timeout: ‏4MB ואז שקט; ‏cut: ‏4MB ויציאה (EOF
#: באמצע); ‏empty: שום בייט; ‏whole: כל הקובץ.
SOURCES = {
    "timeout": "head -c 4194304 {part!r}; sleep 40 & echo $! >> {pids!r}; wait",
    "cut": "head -c 4194304 {part!r}",
    "empty": "return 1",
    "whole": "cat {part!r}",
}

STUBS = {
    "zstd": "#!/bin/sh\nexec cat\n",
    "partclone.dd": PARTCLONE,
    "dmesg": '#!/bin/sh\ncat "$BOX/kmsg"\n',
    "sgdisk": '#!/bin/sh\necho "$*" >> "$BOX/sgdisk.calls"\n',
    "blockdev": "#!/bin/sh\nexit 0\n",
    "fanout": None,   # the real one, compiled below
}

#: מה שלפני הזרם אינו הנבדק: הטבלה "נכתבה", ‏write_started נרשם כמו ב-apply_gpt.
PRE_STREAM = (
    "disk_fits() { :; }; boot_ca_preflight() { :; }; write_check() { :; }; "
    "write_speed_verdict() { :; }; readback_drawers() { :; }; expand_last() { :; }; finish_grow() { :; }; "
    "apply_gpt() { target_write_started \"$1\"; }; node_is_block() { :; }; "
    "ntfs_boot_target() { :; }; "
    'manifest_plan() { cat "$BOX/plan"; }; '
)


@pytest.fixture(scope="module")
def fanout_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("fanout-bin")
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out / "fanout"),
         str(REPO / "agent" / "fanout.c")],
        check=True, stdin=subprocess.DEVNULL,
    )
    return out


def sysfs_disk(box: Path, dev: str, serial: str, ata: int) -> None:
    """כונן SATA ב-sysfs מזויף: ‏`device` הוא symlink לנתיב שעובר דרך ‏ataN,
    בדיוק כמו בקרנל — משם נגזרים ‏disk_port ו-ata_port_of."""
    real = (box / "sys" / "devices" / "pci0000:00" / "0000:00:17.0" / f"ata{ata}"
            / f"host{ata - 1}" / f"target{ata - 1}:0:0" / f"{ata - 1}:0:0:0")
    real.mkdir(parents=True)
    (real / "serial").write_text(serial + "\n")
    (real / "state").write_text("running\n")
    block = box / "sys" / "block" / dev
    block.mkdir(parents=True)
    (block / "device").symlink_to(real)


def target(run: Path, dev: str, name: str) -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- #1601: כשל של הזרם בתחנה אינו כשל של הכונן ---------------------------------


def station(tmp_path: Path, fanout_dir: Path, source: str, pcl: str) -> tuple[Path, str]:
    box = tmp_path / "box"
    run = box / "run"
    (run / "targets").mkdir(parents=True)
    (box / "dev").mkdir()
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    sysfs_disk(box, "sda", "SER-A", 1)
    (box / "kmsg").write_text("[  150.000000] ata1.00: configured for UDMA/133\n")
    (box / "part.bin").write_bytes(PAYLOAD)
    (box / "plan").write_text(PLAN)
    (box / "pids").write_text("")
    src = SOURCES[source].format(part=posix(box / "part.bin"), pids=posix(box / "pids"))
    env = (f"export BOX={posix(box)!r} PCL={pcl} SYSROOT={posix(box)!r} "
           f"RUN_DIR={posix(run)!r} DEVROOT={posix(box / 'dev')!r} TMPDIR={posix(tmp_path)!r} "
           "FANOUT_STALL_EVICT_MS=2000 WAIT_POLL_S=1 WAIT_DRAWER_S=4 WAIT_HELPER_S=4 "
           "WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S=5; ")
    libs = "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in (
        "common", "jsonq", "sysinfo", "waits", "progress", "restore", "verdict", "failmark",
        "drawers"))   # #1906: restore_partition's pipe is restore_pipe (drawers.sh), as in the agent
    out = run_sh(
        make_stubs(box / "stubs", STUBS)
        + f'export PATH="{posix(fanout_dir)}:$PATH"\n'
        + env + libs + PRE_STREAM
        + f"stream_source() {{ {src}; }}; "
        + "target_init sda 1; "
        + "run_restore unicast sda http://s img m.json > /dev/null 2>&1; " 'echo "rc=$?"; '
        f'for p in $(cat {posix(box / "pids")!r}); do kill -9 "$p" 2>/dev/null; done; '
        f'pkill -9 -f {posix(box)!r} 2>/dev/null; :',
        timeout=90,
    )
    return run, out


#: תרחישים שבהם הזרם אשם. בלי התיקון כל אחד מהם משאיר ‏ata_log.json — כלומר
#: ‏disk_failure בשרת על דיסק תקין.
STREAM_FAULTS = {
    "timeout": "ok",      # ‏rc=124: ‏4MB ואז שקט, ‏wait_progress פוקע
    "cut": "ok",          # המקור יצא באמצע: ‏fanout ראה EOF, ה-sha אינו תואם
    "empty": "ok",        # שום בייט: ‏fanout "empty stream", ‏rc=1
}


@pytest.mark.parametrize("source", sorted(STREAM_FAULTS))
def test_a_stream_fault_on_a_station_is_not_recorded_against_its_disk(
        tmp_path, fanout_dir, source):
    run, out = station(tmp_path, fanout_dir, source, STREAM_FAULTS[source])
    log = log_of(run)
    assert out.strip().endswith("rc=1"), out + log
    assert target(run, "sda", "state") == "failed", log
    assert "נכתבו 0 מתוך 1 מחיצות" in target(run, "sda", "error")
    assert not (run / "targets" / "sda" / "ata_log.json").exists(), log


#: תרחישים שבהם הכונן אשם — **השומרים**: עוברים לפני התיקון ואחריו. בלעדיהם
#: "תיקון" שמוותר על הראיה תמיד היה עובר.
DISK_FAULTS = {
    "dies": "whole",          # ‏partclone נכשל בכתיבה: ‏fanout "write error"
    "hangs": "whole",         # הכונן נתקע: ‏fanout מפנה אותו, "stalled"
    "fails_at_end": "whole",  # הזרם הגיע שלם (sha תואם), הכתיבה נכשלה
}


@pytest.mark.parametrize("pcl", sorted(DISK_FAULTS))
def test_a_disk_fault_on_a_station_still_keeps_its_evidence(tmp_path, fanout_dir, pcl):
    run, out = station(tmp_path, fanout_dir, DISK_FAULTS[pcl], pcl)
    log = log_of(run)
    assert out.strip().endswith("rc=1"), out + log
    assert target(run, "sda", "state") == "failed", log
    assert (run / "targets" / "sda" / "ata_log.json").exists(), log
    assert target(run, "sda", "ident") == "SER-A|1|1"


# --- #1602: מגירה שיצאה מ-sysfs שומרת את זהותה ----------------------------------

#: מה שהקרנל כתב על ברזל ב-17/10 אחרי ‏`echo 1 > /sys/block/sdb/device/delete`,
#: ושורת ata של הפורט אחריה. הראיה נחתכת מהשורה הראשונה שמעידה על כשל (#890),
#: ושורת ה-ata נשמרת רק אם הפורט שנשמר ב-target_init מזוהה. השורה על sda
#: (של השכנה) אסור שתיכנס.
KMSG = "\n".join([
    "[  200.000002] sd 2:0:0:0: [sdb] tag#3 device offline error, dev sdb, sector 4096 "
    "op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2",
    "[  200.000004] ata3.00: disable device",
    "[  200.000003] I/O error, dev sda, sector 1 op 0x1:(WRITE) flags 0x0",
]) + "\n"


def pulled(tmp_path: Path, then: str) -> tuple[Path, str]:
    """‏target_init על sdb כשהוא ב-sysfs, ואז הוא נשלף — ‏`then` רץ אחרי."""
    box = tmp_path / "box"
    run = box / "run"
    (run / "targets").mkdir(parents=True)
    (box / "proc").mkdir(parents=True)
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    sysfs_disk(box, "sdb", "SER-B", 3)
    (box / "kmsg").write_text(KMSG)
    env = (f"export BOX={posix(box)!r} SYSROOT={posix(box)!r} RUN_DIR={posix(run)!r} "
           f"DEVROOT={posix(box / 'dev')!r}; ")
    libs = "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in (
        "common", "jsonq", "sysinfo", "progress", "failmark"))
    out = run_sh(
        make_stubs(box / "stubs", {"dmesg": '#!/bin/sh\ncat "$BOX/kmsg"\n',
                                   "sgdisk": "#!/bin/sh\nexit 0\n",
                                   "blockdev": "#!/bin/sh\nexit 0\n"})
        + env + libs
        + "target_init sdb 100; target_write_started sdb; "
        + f"rm -rf {posix(box / 'sys' / 'block' / 'sdb')!r}; "
        + then,
    )
    return run, out


def test_a_pulled_drawer_reports_the_serial_port_and_kernel_line_it_had(tmp_path):
    """הראיה על המגירה שנשלפה נושאת את הזהות שנקלטה ב-target_init, ואת שורת
    ה-offline של הקרנל. בלי התיקון: ‏`ident` הוא ‏`||` ו-ata_log ריק."""
    run, out = pulled(tmp_path, 'fail_written_target sdb "מחיצה 3: נשלפה"; echo "rc=$?"')
    log = log_of(run)
    assert out.strip().endswith("rc=0"), out + log
    assert target(run, "sdb", "ident") == "SER-B|3|3", log
    kept = target(run, "sdb", "ata_log")
    assert "device offline error, dev sdb," in kept, kept
    assert "ata3.00: disable device" in kept, kept
    assert "dev sda," not in kept, kept


def test_a_pulled_drawer_keeps_its_slot_on_the_gui(tmp_path):
    """‏`drawer=0|sdb` של 17/10: הגואי מציג את הפורט שנשמר, לא ‏0."""
    gui = tmp_path / "gui"
    gui.mkdir()
    libs = "".join(f". {posix(AGENT)}/lib/{n}.sh; " for n in (
        "disklink", "guistate", "askgui", "slotnames", "clonergui"))
    run, out = pulled(
        tmp_path,
        f"export GUI_DIR={posix(gui)!r} IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff; "
        + libs + 'echo "{}" > "$RUN_DIR/response.json"; '
        'target_set sdb failed "נשלפה"; cloner_gui_state; echo "rc=$?"',
    )
    assert out.strip().endswith("rc=0"), out + log_of(run)
    lines = [ln for ln in (gui / "state").read_text().splitlines() if ln.startswith("drawer=")]
    assert len(lines) == 1, lines
    assert lines[0].startswith("drawer=3|sdb|failed|"), lines
