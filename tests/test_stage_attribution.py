"""ייחוס שלב: מזייפים שלב אחד, מריצים את הפונקציה האמיתית, בודקים
את המחרוזת שהטכנאי רואה ואת מצב היעד — לא קוד יציאה.

למה הקובץ הזה קיים. ‏#440 ו-#444 (וגם צד השחזור של #443) שרדו לא כי
לא נכתבו טסטים, אלא כי הטסטים כיסו את הטופולוגיות שהמחבר דמיין:

- ``test_a_stream_cut_at_the_source`` מזייף מקור שנופל *אחרי* ש-fanout
  צרך את כל המטען (``GOOD_FANOUT``) — לא מגירה אחרונה שמתה באמצע.
- ``test_sigpipe_does_not_kill_the_whole_machine`` יש בו אח ששרד.
- טסט ההרחבה מחליף את ``grow_filesystem`` ב-``echo`` וטוען ``rc=0``.
- ``test_expansion_knows_every_filesystem_family`` הוא חיפוש מחרוזת
  בקוד, לא הרצה.

הדפוס (העתק אותו למסלול הבא, ~30 דקות):

1. זיוף של **שלב אחד** שנופל (fanout / ntfsresize / e2fsck / ...).
2. הרצת הפונקציה האמיתית (``restore_partition_drawers`` /
   ``run_restore`` / ``run_restore_drawers``).
3. ``assert`` על ``targets/<dev>/error`` ו-``targets/<dev>/state``.

בקרה שלילית — להחזיר **את כל** קבצי הסוכן, לא אחד::

    git checkout origin/main -- agent/lib/restore/drawers.sh \\
        agent/lib/restore/expand.sh agent/lib/restore/grow.sh agent/lib/restore/restore.sh
    python -m pytest tests/test_stage_attribution.py -q
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_stream_honesty import GOOD_SHA, call, drawer_run, errors_of
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH))


def state_of(run: Path, dev: str) -> str:
    return (run / "targets" / dev / "state").read_text(encoding="utf-8").strip()


def error_of(run: Path, dev: str) -> str:
    path = run / "targets" / dev / "error"
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- זיופי שלב ---------------------------------------------------------------

#: fanout של המגירה האחרונה שמתה: בולע 64KB (לא את כולו — כדי שהמקור
#: יקבל SIGPIPE), פותח את ה-fifo כדי שהצינור ייגמר ולא ייתקע על open,
#: מדווח ``failed write error`` ויוצא 1. זו הטופולוגיה ש-GOOD_FANOUT
#: (צורך הכול, יוצא 0) ו-DYING_FANOUT (יוצא 137 בלי שורת דוח) לא מכסות.
LAST_DRAWER_DIES = (
    "#!/bin/sh\n"
    "shift\n"
    "for out; do ( : > \"$out\" ) & done\n"
    "head -c 65536 > /dev/null\n"
    "wait\n"
    "for out; do echo \"$out failed write error\"; done\n"
    "exit 1\n"
)

#: כמו למעלה, בלי שורת דוח — הדרך לבדוק שחסר fanout.out אינו "רשת"
#: ואינו "הדיסק תקין".
LAST_DRAWER_DIES_SILENT = (
    "#!/bin/sh\n"
    "shift\n"
    "for out; do ( : > \"$out\" ) & done\n"
    "head -c 65536 > /dev/null\n"
    "wait\n"
    "exit 1\n"
)

#: partclone שכותב את שורת ה-EIO ללוג שכבר קיים ולא נקרא, ויוצא 1.
DYING_PARTCLONE = (
    "#!/bin/sh\n"
    "log=/dev/null\n"
    "while [ $# -gt 0 ]; do\n"
    "  case \"$1\" in -L) log=\"$2\"; shift 2 ;; *) shift ;; esac\n"
    "done\n"
    "cat > /dev/null\n"
    "echo \"ERROR: Input/output error\" > \"$log\"\n"
    "exit 1\n"
)


# --- #440: דיסק מת במגירה היחידה --------------------------------------------


def test_the_last_drawer_dying_is_a_disk_fault_not_a_cut_stream(tmp_path):
    """מגירה יחידה, fanout.rc=1, source.rc=141.

    לפני התיקון ההודעה היא ``הזרם נקטע (מקור rc=141)`` והטכנאי נשלח
    לרשת. אחריו — ההתקן והכתיבה, עם מה ש-fanout ו-partclone כתבו.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": LAST_DRAWER_DIES, "partclone.dd": DYING_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=1"), out
    assert state_of(run, "sda") == "failed"
    err = errors_of(run, disks=("sda",))["sda"]
    assert "הכתיבה לדיסק נכשלה" in err, err
    assert "write error" in err, err
    assert "Input/output error" in err, err
    assert "מקור rc=" not in err, err
    assert "הזרם נקטע" not in err, err
    assert "sha256" not in err, err


def test_a_missing_fanout_report_is_neither_ok_nor_a_network_cut(tmp_path):
    """עיקרון 5 על המסווג עצמו: אין fanout.out — אין דיווח, לא רשת."""
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": LAST_DRAWER_DIES_SILENT, "partclone.dd": DYING_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=1"), out
    err = errors_of(run, disks=("sda",))["sda"]
    assert "אין דיווח" in err, err
    assert "הכתיבה לדיסק נכשלה" in err, err
    assert "מקור rc=" not in err, err
    assert "הזרם נקטע" not in err, err
    assert state_of(run, "sda") == "failed"


# --- הרחבה best-effort אחרי שחזור מלא (שיטת קרונזילה, #648) ----------------

FAILING = "#!/bin/sh\nexit 1\n"
OK = "#!/bin/sh\nexit 0\n"
E2FSCK_CORRECTED = "#!/bin/sh\nexit 1\n"
E2FSCK_UNFIXED = "#!/bin/sh\nexit 4\n"


def grow_drawers(tmp_path, *, fs="ntfs", disks=("sda",), stubs=None,
                 mark=True) -> tuple[Path, str]:
    """``run_restore_drawers`` האמיתי, עם הכתיבה מוחלפת — מה שנבדק
    הוא רק מה קורה ליעד אחרי ``finish_grow``."""
    run = tmp_path / "run"
    run.mkdir()
    # #1798: finish_grow reads the table for the incomplete-write mark -- an empty table here
    extra = {"ntfsfix": OK, "ntfsresize": OK, "e2fsck": OK, "resize2fs": OK, "sgdisk": OK,
             "ntfs-3g.probe": OK}   # #2009: the grow reads the volume's hibernation first -- clean here
    extra.update(stubs or {})
    marks = ""
    if mark:
        marks = "".join(
            f"echo '2|{fs}' > \"$RUN_DIR/targets/{d}/expanded\"; "
            for d in disks)
    out = run_sh(
        make_stubs(tmp_path / "stubs", extra)
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/restore/expand.sh; . {posix(AGENT)}/lib/restore/grow.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; . {posix(AGENT)}/lib/restore/expandshare.sh; . {posix(AGENT)}/lib/restore/writemark.sh; . {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; . {posix(AGENT)}/lib/restore/verdict.sh; . {posix(AGENT)}/lib/restore/failmark.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh; "
        f". {posix(AGENT)}/lib/restore/writecheck.sh; "
        "disk_fits() { return 0; }; apply_gpt() { return 0; }; "
        "write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; "
        "boot_hidden_disk() { return 0; }; "   # #2015: the hidden sectors have their own file
        "expand_last() { return 0; }; "
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        "restore_partition_drawers() { return 0; }; "
        + "".join(f"target_init {d} 100; " for d in disks)
        + marks
        + "run_restore_drawers multicast http://s img m.json "
        + " ".join(disks) + '; echo "rc=$?"'
    )
    return run, out


def grow_station(tmp_path, *, fs="ntfs", stubs=None, mark=True) -> tuple[Path, str]:
    """``run_restore`` האמיתי של תחנת הכיתה — אותו שלב, מסך אחר."""
    run = tmp_path / "run"
    run.mkdir()
    # #1798: finish_grow reads the table for the incomplete-write mark -- an empty table here
    extra = {"ntfsfix": OK, "ntfsresize": OK, "e2fsck": OK, "resize2fs": OK, "sgdisk": OK,
             "ntfs-3g.probe": OK}   # #2009: the grow reads the volume's hibernation first -- clean here
    extra.update(stubs or {})
    mark_cmd = f"echo '2|{fs}' > \"$RUN_DIR/targets/sda/expanded\"; " if mark else ""
    out = run_sh(
        make_stubs(tmp_path / "stubs", extra)
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/restore/expand.sh; . {posix(AGENT)}/lib/restore/grow.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; . {posix(AGENT)}/lib/restore/expandshare.sh; . {posix(AGENT)}/lib/restore/writemark.sh; "
        "disk_fits() { return 0; }; boot_ca_preflight() { return 0; }; apply_gpt() { return 0; }; "
        "write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; "
        "boot_hidden_disk() { return 0; }; "   # #2015: the hidden sectors have their own file
        "expand_last() { return 0; }; "
        "manifest_plan() { echo '1|G|win|ntfs|2048|1024|p1.zst|sha|false|UG|'; }; "
        "restore_partition() { return 0; }; "
        "target_init sda 100; "
        + mark_cmd
        + 'run_restore multicast sda http://s img m.json; echo "rc=$?"'
    )
    return run, out


@pytest.mark.parametrize("runner", [grow_drawers, grow_station])
@pytest.mark.parametrize(("fs", "stubs"), [
    ("ntfs", {"ntfsresize": FAILING}),
    ("ntfs", {"ntfsfix": FAILING, "ntfsresize": FAILING}),
    ("ext4", {"e2fsck": OK, "resize2fs": FAILING}),
    ("ext4", {"e2fsck": E2FSCK_UNFIXED, "resize2fs": OK}),
    ("xfs", {"mount": OK, "umount": OK, "xfs_growfs": FAILING}),
    ("f2fs", {}),
])
def test_grow_failure_defers_growth_without_failing_clone(tmp_path, runner, fs, stubs):
    """שיטת קרונזילה: דיסק שקיבל תמונה מלאה הוא done גם אם ההרחבה נכשלה.
    הכישלון הופך ל-warning ‏\"done (grow deferred)\" ולא מפיל את המגירה."""
    run, out = runner(tmp_path, fs=fs, stubs=stubs)
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    err = error_of(run, "sda")
    assert "ההרחבה נדחתה" in err, err
    assert fs in err, err
    assert (run / "state").read_text(encoding="utf-8").strip() == "verifying"   # #1935: done is the caller's, last


def test_ntfs_grow_calls_the_three_steps_in_order_dirty_last(tmp_path):
    """שיטת FOG resetFlag: ‏ntfsfix -b -d לפני ה-resize, ntfsresize עם
    double force, ואז ntfsfix -d **אחרון** — כי ה-resize עלול לסמן dirty
    מחדש, וניקוי הדגל חייב לבוא אחריו כדי שלא ירוץ chkdsk בבוט הראשון."""
    run, out = grow_drawers(tmp_path, stubs={
        "ntfsfix": '#!/bin/sh\necho "ntfsfix $*" >> "$RUN_DIR/order.log"\nexit 0\n',
        "ntfsresize": '#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\nexit 0\n',
    })
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert error_of(run, "sda") == ""
    dev = f"{posix(run)}/dev/sda2"
    seq = (run / "order.log").read_text(encoding="utf-8").strip().splitlines()
    assert seq == [
        f"ntfsfix -b -d {dev}",
        f"ntfsresize -f -f --no-progress-bar {dev}",
        f"ntfsfix -d {dev}",
    ], seq


def test_ntfsfix_failure_still_attempts_double_force_resize(tmp_path):
    """ntfsfix -b -d נכשל — ntfsresize עדיין רץ עם double force (-f -f).
    כישלון ntfsfix לא עוצר את הרצף (best-effort), וה-clone נשאר done —
    ומאז #1124 ה-`ntfsfix -d` האחרון שנכשל **נאמר**: הדגל המלוכלך נשאר,
    וזה done עם אזהרה, לא done נקי."""
    run, out = grow_drawers(tmp_path, stubs={
        "ntfsfix": '#!/bin/sh\necho "$*" >> "$RUN_DIR/ntfsfix.called"\nexit 1\n',
        "ntfsresize": '#!/bin/sh\n[ -f "$RUN_DIR/ntfsfix.called" ] || exit 2\n'
                      '[ "$#" -eq 4 ] && [ "$1" = -f ] && [ "$2" = -f ] && '
                      '[ "$3" = --no-progress-bar ] && [ "$4" = "$RUN_DIR/dev/sda2" ]\n',
    })
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert "דגל ה-dirty" in error_of(run, "sda"), error_of(run, "sda")
    # ‏ntfsfix -b -d לפני ה-resize, וגם ntfsfix -d האחרון — שניהם רצו.
    calls = (run / "ntfsfix.called").read_text(encoding="utf-8").strip().splitlines()
    dev = f"{posix(run)}/dev/sda2"
    assert f"-b -d {dev}" in calls, calls
    assert f"-d {dev}" in calls, calls


NTFSFIX_LAST_FAILS = '#!/bin/sh\ncase "$1" in -b) exit 0 ;; *) exit 1 ;; esac\n'
UMOUNT_STUCK = "#!/bin/sh\nexit 32\n"


@pytest.mark.parametrize("runner", [grow_drawers, grow_station])
@pytest.mark.parametrize(("fs", "stubs", "said"), [
    ("ntfs", {"ntfsfix": NTFSFIX_LAST_FAILS}, "מערכת הקבצים הורחבה, אך דגל ה-dirty של NTFS נשאר (ntfsfix -d rc=1)"),
    ("xfs", {"mount": OK, "xfs_growfs": OK, "umount": UMOUNT_STUCK}, "xfs הורחב, אך נשאר מעוגן (umount rc=32)"),
])
def test_a_grow_that_succeeded_is_never_said_deferred(tmp_path, runner, fs, stubs, said):
    """‏#2011 (שטח 22/10): ‏ntfsresize הצליח ורק ה-`ntfsfix -d` האחרון נכשל — ההודעה אמרה
    "ההרחבה נדחתה" ומיד אחריה "מערכת הקבצים הורחבה". הורחב = "הורחב"; הדגל שנשאר הוא
    אזהרה (#2009 — לא ירוק), ו"נדחתה" נשאר רק כשההרחבה עצמה לא קרתה."""
    run, out = runner(tmp_path, fs=fs, stubs=stubs)
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    err = error_of(run, "sda")
    assert said in err, err                                   # still a warning, never green
    assert "נדחתה" not in err, err
    assert f"מחיצה 2 ({fs})" in err, err


def test_one_drawer_deferring_grow_leaves_the_machine_done(tmp_path):
    """האזהרה שייכת רק למגירה שההרחבה שלה נדחתה; השאר done נקי,
    והמכונה כולה done (‏best-effort — לא partial)."""
    run, out = grow_drawers(
        tmp_path, fs="ntfs", disks=("sda", "sdb"),
        stubs={"ntfsresize": (
            "#!/bin/sh\n"
            "case \"$*\" in *sdb*) exit 1 ;; *) exit 0 ;; esac\n"
        )})
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert error_of(run, "sda") == ""
    assert state_of(run, "sdb") == "done"
    assert "ההרחבה נדחתה" in error_of(run, "sdb")
    assert (run / "state").read_text(encoding="utf-8").strip() == "verifying"   # #1935: done is the caller's, last


# --- רדיוס הפגיעה: אזהרת הרחבה אינה נכשלת את העבודה כולה -----------------


def test_e2fsck_that_corrected_errors_still_reaches_done(tmp_path):
    """e2fsck יוצא 1 כשהוא תיקן — הצלחה, לא כישלון. בלי זה התיקון
    הופך כל כרך עם inode שתוקן לעבודה שנכשלה."""
    run, out = grow_drawers(
        tmp_path, fs="ext4",
        stubs={"e2fsck": E2FSCK_CORRECTED, "resize2fs": OK})
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert error_of(run, "sda") == ""


def test_nothing_to_grow_is_still_done(tmp_path):
    """בלי סימון expanded אין מה להגדיל — זה לא כשל."""
    run, out = grow_drawers(tmp_path, fs="ntfs", mark=False)
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"


# --- #667: XFS (RHEL/Fedora/Rocky/Alma) --------------------------------------

def _xfs_stubs(grow="0", mount="0") -> dict[str, str]:
    grow_body = (
        "#!/bin/sh\n"
        'echo "xfs_growfs $*" >> "$RUN_DIR/order.log"\n'
        f"exit {grow}\n"
    )
    mount_body = (
        "#!/bin/sh\n"
        'echo "mount $*" >> "$RUN_DIR/order.log"\n'
        f"exit {mount}\n"
    )
    umount_body = (
        "#!/bin/sh\n"
        'echo "umount $*" >> "$RUN_DIR/order.log"\n'
        "exit 0\n"
    )
    return {"mount": mount_body, "umount": umount_body, "xfs_growfs": grow_body}


def test_xfs_grow_mounts_then_grows_the_mountpoint_not_the_device(tmp_path):
    """#667: XFS גדל רק כשהוא mounted — xfs_growfs על נקודת העגינה,
    לא על ההתקן (בניגוד ל-resize2fs). לפני התיקון הענף ``*)`` מחזיר 1
    בלי לקרוא לכלי, ו-golden image של RHEL נכשל ב-grow תמיד."""
    run, out = grow_drawers(tmp_path, fs="xfs", stubs=_xfs_stubs())
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert error_of(run, "sda") == ""
    mnt = posix(run / "grow")
    seq = (run / "order.log").read_text(encoding="utf-8").strip().splitlines()
    assert seq == [
        f"mount -t xfs {posix(run)}/dev/sda2 {mnt}",
        f"xfs_growfs {mnt}",
        f"umount {mnt}",
    ], seq
    assert "/dev/sda2" not in seq[1]


def test_xfs_grow_does_not_run_the_tool_if_mount_failed(tmp_path):
    """fail-closed: mount שנכשל אינו הצלחה שקטה, ו-xfs_growfs לא רץ."""
    run, out = grow_drawers(tmp_path, fs="xfs", stubs=_xfs_stubs(mount="1"))
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") == "done"
    assert "ההרחבה נדחתה" in error_of(run, "sda")
    log = run / "order.log"
    lines = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert any(line.startswith("mount ") for line in lines), lines
    assert not any("xfs_growfs" in line for line in lines), lines


# --- #426: לולאת המגירות סופרת מחיצות, לא היעדר כישלון ---------------------


def _plan_drawers(tmp_path, *, plan_cmd, restore_cmd="return 0",
                  disks=("sda", "sdb")) -> tuple[Path, str]:
    """``run_restore_drawers`` האמיתי אחרי apply_gpt — מה שנבדק הוא
    ההכרעה על done כשהתוכנית נכשלת או ריקה או קצרה."""
    run = tmp_path / "run"
    run.mkdir()
    out = run_sh(
        make_stubs(tmp_path / "stubs", {"ntfsfix": OK, "ntfsresize": OK, "sgdisk": OK})   # #1798: the mark's table read
        + f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/core/progress.sh; . {posix(AGENT)}/lib/restore/restore.sh; "
        f". {posix(AGENT)}/lib/restore/expand.sh; . {posix(AGENT)}/lib/restore/grow.sh; . {posix(AGENT)}/lib/restore/ntfsunhib.sh; . {posix(AGENT)}/lib/restore/expandshare.sh; . {posix(AGENT)}/lib/restore/writemark.sh; "
        f". {posix(AGENT)}/lib/restore/drawers.sh; . {posix(AGENT)}/lib/restore/drawerfeed.sh; . {posix(AGENT)}/lib/restore/verdict.sh; . {posix(AGENT)}/lib/restore/failmark.sh; . {posix(AGENT)}/lib/restore/ntfsboot.sh; "
        f". {posix(AGENT)}/lib/restore/writecheck.sh; "
        "disk_fits() { return 0; }; apply_gpt() { return 0; }; "
        "write_check() { return 0; }; write_speed_verdict() { return 0; }; readback_drawers() { :; }; "
        "boot_hidden_disk() { return 0; }; "   # #2015: the hidden sectors have their own file
        "expand_last() { return 0; }; grow_expanded() { return 0; }; "
        f"manifest_plan() {{ {plan_cmd}; }}; "
        "restore_partition_drawers() { "
        'echo ran >> "$RUN_DIR/wrote"; '
        f"{restore_cmd}; }}; "
        + "".join(f"target_init {d} 100; " for d in disks)
        + "run_restore_drawers multicast http://s img m.json "
        + " ".join(disks) + '; echo "rc=$?"'
    )
    return run, out


def test_manifest_plan_failure_after_gpt_fails_every_drawer(tmp_path):
    """#426 א: apply_gpt כתב, manifest_plan פלט שורה ונכשל. בצינור
    השורה נצרכת, הכשל נבלע, והמגירות → done. עכשיו כולן failed."""
    run, out = _plan_drawers(
        tmp_path,
        plan_cmd="echo '1|G|win|ntfs|2048|1|p1.zst|aa|false|U|'; return 1",
    )
    assert out.strip().endswith("rc=1"), out
    assert (run / "state").read_text(encoding="utf-8").strip() == "failed"
    for dev in ("sda", "sdb"):
        assert state_of(run, dev) == "failed", error_of(run, dev)
        assert "לא הצלחנו לקרוא את תוכנית המחיצות" in error_of(run, dev)
    assert not (run / "wrote").exists()


def test_an_empty_plan_is_not_a_finished_loop(tmp_path):
    """לולאה שרצה אפס פעמים על תוכנית ריקה (jq יצא 0 בלי שורות) אינה
    הצלחה — אין ספירה להשוות אליה, וזה בדיוק מה שנראה כמו סיום."""
    run, out = _plan_drawers(tmp_path, plan_cmd=":")
    assert out.strip().endswith("rc=1"), out
    assert (run / "state").read_text(encoding="utf-8").strip() == "failed"
    for dev in ("sda", "sdb"):
        assert state_of(run, dev) == "failed", error_of(run, dev)
        assert "נכתבו 0 מתוך 0 מחיצות" in error_of(run, dev)
    assert not (run / "wrote").exists()


def test_a_plan_that_stops_midway_fails_the_surviving_drawers(tmp_path):
    """שתי מחיצות, הראשונה נכתבה והשנייה נכשלה: השורדות אינן done על
    חצי תוכנית. הספירה היא הראיה, לא היעדר סימן כישלון על המגירה."""
    run, out = _plan_drawers(
        tmp_path,
        plan_cmd=(
            "echo '1|G|win|ntfs|2048|1|p1.zst|aa|false|U|'; "
            "echo '2|G|win|ntfs|4096|1|p2.zst|bb|false|U|'"
        ),
        restore_cmd=(
            'if [ -f "$RUN_DIR/once" ]; then return 1; fi; '
            ': > "$RUN_DIR/once"; return 0'
        ),
    )
    assert out.strip().endswith("rc=1"), out
    assert (run / "state").read_text(encoding="utf-8").strip() == "failed"
    for dev in ("sda", "sdb"):
        assert state_of(run, dev) == "failed", error_of(run, dev)
        assert "נכתבו 1 מתוך 2 מחיצות" in error_of(run, dev)
    assert (run / "wrote").read_text(encoding="utf-8").count("ran") == 2


def test_a_whole_plan_still_reaches_done_on_every_drawer(tmp_path):
    """בקרה חיובית: תוכנית של מחיצה אחת שנכתבה — done, כמו לפני התיקון."""
    run, out = _plan_drawers(
        tmp_path,
        plan_cmd="echo '1|G|win|ntfs|2048|1|p1.zst|aa|false|U|'",
    )
    assert out.strip().endswith("rc=0"), out
    assert (run / "state").read_text(encoding="utf-8").strip() == "verifying"   # #1935: done is the caller's, last
    for dev in ("sda", "sdb"):
        assert state_of(run, dev) == "done", error_of(run, dev)
        assert error_of(run, dev) == ""
    assert (run / "wrote").read_text(encoding="utf-8").count("ran") == 1
