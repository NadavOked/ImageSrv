"""‏#1598 — מגירה שמתה באמצע הזרם יוצאת ממנו תוך שניות, והשאר גומרות.

נמצא על ברזל ב-17/10 (מחשב שיכפול 2): ‏`echo 1 > /sys/block/sdb/device/delete`
באמצע מחיצה 3. ‏partclone של sdb המשיך "לכתוב" אל ה-page cache, נתקע, ו-fanout
פינה אותו רק אחרי ‏150 שניות (`STALL_EVICT_MS`) — כל אותו זמן הזרם של המכונה
כולה עמד (#660). השולח איבד את המכונה, ו-sda, הדיסק התקין, נכשל ב"פג הזמן --
הזרם לא הגיע" ונרשם כ-disk_failure.

הטסטים מריצים את ‏`restore_partition_drawers` האמיתי עם ‏**fanout האמיתי**
(מקומפל מ-`agent/fanout.c`), ‏zstd/partclone מזויפים, ‏sysfs ו-dmesg מזויפים
תחת ‏`SYSROOT`. המגירה המתה מפסיקה לקרוא בדיוק כמו partclone תקוע, ולכן
בלי התיקון fanout חוסם את הזרם, ‏`wait_progress` פוקע, ושתי המגירות נכשלות.

שלושה סימני מוות, כולם **ראיה חיובית** מהקרנל — שקט אינו מוות: מגירה
איטית-אך-חיה חייבת להמשיך להאט את הזרם ולא להיפסל (#660).
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, posix
from test_timeouts import log_of, make_stubs, run_sh
from agentlib import flat_lib

REPO = Path(__file__).resolve().parent.parent
pytestmark = requires_native("gcc", posix=True, why="fanout האמיתי צריך gcc ו-fifo")

#: ‏16MB: הרבה מעבר למה שהמגירה המתה בולעת לפני שהיא נחסמת (חוצץ fanout
#: של 2MB + ‏fifo של 1MB + צינור ה-zstd), כך שבלי התיקון הזרם באמת עומד.
PAYLOAD = hashlib.sha256(b"1598").digest() * (16 * 1024 * 1024 // 32)
GOOD_SHA = hashlib.sha256(PAYLOAD).hexdigest()

#: ‏partclone מזויף. ל-sda — כותב את כל הבייטים לצומת. ל-sdb — בולע 2MB,
#: ‏"מת" לפי ‏$DEATH, ומפסיק לקרוא בלי לצאת: כמו partclone שנתקע מול כונן
#: שנשלף, ולא כמו partclone שיוצא בשגיאה (שם fanout מקבל EPIPE בלאו הכי).
PARTCLONE = r"""#!/bin/sh
node=
while [ $# -gt 0 ]; do case "$1" in -O) node=$2; shift 2 ;; *) shift ;; esac; done
[ "$DEATH" = healthy ] && exec cat > "$node"
case "$node" in
  */sdb3)
    head -c 2097152 > /dev/null
    date +%s > "$BOX/died"
    case "$DEATH" in
      vanish)      rm -rf "$SYSROOT/sys/block/sdb" ;;
      offline)     echo offline > "$SYSROOT/sys/block/sdb/device/state" ;;
      write_error) echo '[  200.000001] sd 2:0:0:0: [sdb] tag#3 device offline error, dev sdb, sector 4096 op 0x1:(WRITE) flags 0x0 phys_seg 1 prio class 2' >> "$BOX/kmsg" ;;
      quiet)       : ;;
    esac
    echo $$ >> "$BOX/pids"
    exec sleep 40 ;;
  *) cat > "$node" ;;
esac
"""

STUBS = {
    "zstd": "#!/bin/sh\nexec cat\n",
    "partclone.dd": PARTCLONE,
    "dmesg": '#!/bin/sh\ncat "$BOX/kmsg"\n',
    "sgdisk": '#!/bin/sh\necho "$*" >> "$BOX/sgdisk.calls"\n',
    "blockdev": "#!/bin/sh\nexit 0\n",
    "fanout": None,   # the real one, compiled below
}

DEATHS = {
    "vanish": "נעלם מהמערכת",
    "offline": "offline",
    "write_error": "הקרנל דחה כתיבה",
}


@pytest.fixture(scope="module")
def fanout_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("fanout-bin")
    subprocess.run(
        ["gcc", "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(out / "fanout"),
         str(REPO / "agent" / "fanout.c")],
        check=True, stdin=subprocess.DEVNULL,
    )
    return out


def room(tmp_path: Path, fanout_dir: Path, death: str, source: str,
         stall_s: int = 20) -> tuple[Path, Path, str, float]:
    box = tmp_path / "box"
    run, dev = box / "run", box / "dev"
    (run / "targets").mkdir(parents=True)
    dev.mkdir()
    (box / "proc").mkdir()
    (box / "proc" / "uptime").write_text("100.00 50.00\n")
    for d, serial in (("sda", "SER-A"), ("sdb", "SER-B")):
        sysd = box / "sys" / "block" / d / "device"
        sysd.mkdir(parents=True)
        (sysd / "state").write_text("running\n")
        (sysd / "serial").write_text(serial + "\n")
    # שגיאת כתיבה ישנה על sda, **לפני** תחילת הסבב (since=100): אסור שתפיל אותה.
    (box / "kmsg").write_text(
        "[    5.000000] I/O error, dev sda, sector 1 op 0x1:(WRITE) flags 0x0\n")
    (box / "part.bin").write_bytes(PAYLOAD)
    (box / "pids").write_text("")
    env = (f"export BOX={posix(box)!r} DEATH={death} SYSROOT={posix(box)!r} "
           f"RUN_DIR={posix(run)!r} DEVROOT={posix(dev)!r} TMPDIR={posix(tmp_path)!r} "
           f"FANOUT_BUFFER=2097152 WAIT_POLL_S=1 WAIT_DRAWER_S=10 WAIT_HELPER_S=5 "
           f"WAIT_STREAM_START_S=20 WAIT_STREAM_STALL_S={stall_s}; ")
    libs = "".join(f". {posix(flat_lib())}/{n}.sh; " for n in (
        "common", "sysinfo", "waits", "progress", "restore", "drawers", "drawerfeed", "verdict",
        "failmark", "ntfsboot"))
    started = time.monotonic()
    out = run_sh(
        make_stubs(box / "stubs", STUBS)
        + f'export PATH="{posix(fanout_dir)}:$PATH"\n'
        + env + libs
        + "target_init sda 1; target_init sdb 1; "
        'echo SER-A > "$RUN_DIR/targets/sda/write_started"; '
        'echo SER-B > "$RUN_DIR/targets/sdb/write_started"; '
        + source
        + f"restore_partition_drawers unicast http://s img 3 dd part.zst {GOOD_SHA} '' sda sdb"
        " > /dev/null 2>&1; " 'echo "rc=$?"; '
        # ניקיון: ה-partclone התקוע, וכל מה שעוד מחזיק נתיב מתחת לקופסה.
        f'for p in $(cat {posix(box / "pids")!r}); do kill -9 "$p" 2>/dev/null; done; '
        f'pkill -9 -f {posix(box)!r} 2>/dev/null; :',
        timeout=90,
    )
    return box, run, out, time.monotonic() - started


def target(run: Path, dev: str, name: str) -> str:
    path = run / "targets" / dev / name
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


@pytest.mark.parametrize("death", sorted(DEATHS))
def test_a_drawer_that_dies_mid_stream_is_out_in_seconds_and_the_other_finishes(
        tmp_path, fanout_dir, death):
    """התרחיש של 17/10, בכל אחד משלושת סימני המוות.

    בלי התיקון: fanout חוסם את הזרם על sdb (פינוי רק אחרי ‏150ש'),
    ‏`wait_progress` פוקע אחרי ‏20ש' שקט, ושתי המגירות נכשלות ב"הזרם לא הגיע".
    """
    source = f"stream_source() {{ cat {posix(tmp_path / 'box' / 'part.bin')!r}; }}; "
    box, run, out, elapsed = room(tmp_path, fanout_dir, death, source)
    log = log_of(run)

    assert out.strip().endswith("rc=0"), out + log
    # sda — התקינה — גמרה, וקיבלה כל בייט (עיקרון 4: לא "כמעט").
    assert target(run, "sda", "state") != "failed", target(run, "sda", "error") + log
    assert target(run, "sda", "error") == ""
    assert hashlib.sha256((box / "dev" / "sda3").read_bytes()).hexdigest() == GOOD_SHA
    # sdb — המתה — failed בגלוי, עם הסיבה, ותוך שניות מרגע המוות.
    assert target(run, "sdb", "state") == "failed", log
    assert DEATHS[death] in target(run, "sdb", "error"), target(run, "sdb", "error")
    assert target(run, "sdb", "dead"), "the watcher did not mark the dead drawer"
    died = int((box / "died").read_text().strip())
    marked = int(os.stat(run / "targets" / "sdb" / "error").st_mtime)
    assert marked - died <= 6, (died, marked)
    # הראיה של sdb נאספה — הכונן הזה באמת אשם. של sda לא.
    assert (run / "targets" / "sdb" / "ata_log.json").exists()
    assert not (run / "targets" / "sda" / "ata_log.json").exists()
    assert elapsed < 18, elapsed


def test_a_drawer_that_only_goes_quiet_is_not_declared_dead(tmp_path, fanout_dir):
    """השומר על #660: שקט בלי ראיה מהקרנל אינו מוות. ‏sdb מפסיקה לקרוא בלי
    סימן — הזרם ממתין לה (back-pressure), ואיש לא מסמן אותה dead. עובר
    לפני התיקון ואחריו; בלעדיו "תיקון" שמפנה כל מגירה שקטה היה עובר."""
    source = f"stream_source() {{ cat {posix(tmp_path / 'box' / 'part.bin')!r}; }}; "
    box, run, out, _ = room(tmp_path, fanout_dir, "quiet", source, stall_s=6)
    assert not target(run, "sdb", "dead"), target(run, "sdb", "dead")
    assert "out of the stream now" not in log_of(run)
    # הזרם עמד בגללה, ולכן הוא פג — לשתיהן, בשם "הזרם", לא בשם כונן.
    assert "הזרם לא הגיע" in target(run, "sda", "error"), target(run, "sda", "error")


def test_a_stream_that_never_arrives_is_not_recorded_against_the_disks(tmp_path, fanout_dir):
    """באג 3 של #1598: ‏"פג הזמן -- הזרם לא הגיע" אינו כשל של הכונן.

    עד התיקון לולאת ההמתנה למגירות קראה ‏`fail_written_target` בלי ההקשר,
    נאספה ראיית ATA, ‏`failure_json` צירף ‏`ata_log`, והשרת רשם disk_failure
    (‏unclassified) על דיסק תקין — ‏`failed_last` בסבב הבא. הניגוב של ה-GPT
    (#1388) **נשאר**: הדיסק כתוב חלקית, ולכן אסור שיאתחל.
    """
    part = posix(tmp_path / "box" / "part.bin")
    pids = posix(tmp_path / "box" / "pids")
    source = (f"stream_source() {{ head -c 4194304 {part!r}; "
              f"sleep 40 & echo $! >> {pids!r}; wait; }}; ")
    box, run, out, _ = room(tmp_path, fanout_dir, "healthy", source, stall_s=4)
    assert out.strip().endswith("rc=1"), out
    for d in ("sda", "sdb"):
        assert target(run, d, "state") == "failed"
    assert "הזרם לא הגיע" in target(run, "sda", "error"), target(run, "sda", "error")
    for d in ("sda", "sdb"):
        assert not (run / "targets" / d / "ata_log.json").exists(), (d, log_of(run))
    calls = (box / "sgdisk.calls").read_text() if (box / "sgdisk.calls").exists() else ""
    assert calls.count("--zap-all") == 2, calls
