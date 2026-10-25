"""‏#89 ו-#107: שם המחשב בצד הלינוקס.

‏#89 — שורש btrfs ב-subvolume ששמו אינו `@` (פדורה: `root`, התקנה ידנית:
כל שם). הקוד הישן ניחש `subvol=@` בלבד, נכשל, והמכונה עלתה עם השם
שבאימג'. עכשיו ה-top level מעוגן והילד היחיד שיש בו `etc/` הוא השורש;
אפס או כמה — כישלון בשם, לא בחירה.

‏#107 — דיסק dual-boot. הקוד הישן כתב רק לצד ה-Windows. עכשיו שני הצדדים
נכתבים, והתוצאה אומרת בנפרד: שניהם / רק Windows / רק לינוקס / אף אחד.

‏`mount` מזויף: עיגון רגיל (ה-subvolume ברירת המחדל) יוצר `etc/` רק כש-
`DEFAULT_ETC` דלוק; ‏`subvol=X` יוצר `etc/` כש-X ב-`SUBVOLS`; ‏`subvolid=5`
(ה-top level) חושף `X/etc` לכל X ב-`SUBVOLS` ו-`X` בלי etc לכל X
ב-`OTHER_SUBVOLS` (‏`home` כברירת מחדל). כך `subvol=@` של הקוד הישן
עובד באמת כשיש `@` — והבקרה השלילית נופלת רק על מה שהקוד הישן באמת לא ידע.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_captured_stdout import HIVEWRITE_OK, LINUX_PLAN, WIN_PLAN, sh, stub
from test_smart import AGENT, BASH, posix

pytestmark = requires_native(("bash", BASH))

BTRFS_PLAN = LINUX_PLAN.replace("|ext4|", "|btrfs|")

MOUNT = """echo "mount $*" >> "$RUN_DIR/mounts"
s=""; for a in "$@"; do case "$a" in subvol=*) s=${a#subvol=};; esac; done
case "$*" in
  *subvolid=5*) for v in $SUBVOLS; do mkdir -p "$RUN_DIR/linux/$v/etc"; done
                for v in $OTHER_SUBVOLS; do mkdir -p "$RUN_DIR/linux/$v"; done ;;
  *subvol=*) case " $SUBVOLS " in *" $s "*) mkdir -p "$RUN_DIR/linux/etc";; esac ;;
  *) if [ -n "$DEFAULT_ETC" ]; then mkdir -p "$RUN_DIR/linux/etc"; fi ;;
esac
exit 0"""
UMOUNT = 'echo "umount $*" >> "$RUN_DIR/mounts"; exit 0'
#: ‏hivewrite שמתעד כל כתיבה — הראיה שצד ה-Windows באמת נכתב.
HIVEWRITE_LOGGED = 'echo "$*" >> "$RUN_DIR/hivewrites"\n' + HIVEWRITE_OK


def box(tmp_path: Path, plans: list[str], *, subvols: str = "", others: str = "home",
        default_etc: bool = False, hive: bool = True, **override: str) -> tuple[str, Path]:
    stub_dir = tmp_path / "stubs"; stub_dir.mkdir(parents=True)
    run = tmp_path / "run"; run.mkdir()
    if hive:
        (run / "win/Windows/System32/config").mkdir(parents=True)
        (run / "win/Windows/System32/config/SYSTEM").write_text("hive", encoding="utf-8")
    stubs = {"mount": MOUNT, "umount": UMOUNT, "ntfs-3g": "exit 0",
             "hivewrite": HIVEWRITE_LOGGED, **override}
    script = "".join(stub(stub_dir / n, b) for n, b in stubs.items())
    lines = " ".join(repr(p) for p in plans)
    script += (
        f'export PATH="$(cd {posix(stub_dir)!r} && pwd):$PATH"; '
        f'export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev '
        f'LOG_FILE={posix(run / "agent.log")!r} SUBVOLS={subvols!r} OTHER_SUBVOLS={others!r} '
        f'DEFAULT_ETC={("1" if default_etc else "")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/restore.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; . {posix(AGENT)}/lib/unattend.sh; . {posix(AGENT)}/lib/hostname_linux.sh; '
        f'manifest_plan() {{ printf \'%s\\n\' {lines}; }}; '
        f'partition_node() {{ echo /dev/fake; }}; '
    )
    return script, run


def name(script: str) -> tuple[int, dict]:
    out = sh(script + 'write_hostname sda /dev/null LAB1-05')
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, (out.stdout, out.stderr)  # רק אובייקט התוצאה
    return out.returncode, json.loads(lines[0])


def written(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip() if path.exists() else ""


# --- #89: ה-subvolume שמחזיק את /etc ------------------------------------------

@pytest.mark.parametrize("subvol", ["root", "@rootfs", "@"])
def test_a_btrfs_root_in_any_subvolume_gets_the_name(tmp_path, subvol):
    """פדורה (`root`), התקנה ידנית (`@rootfs`) ואובונטו (`@`) — כולם נכתבים."""
    script, run = box(tmp_path, [BTRFS_PLAN], subvols=subvol)
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "etc-hostname"})
    assert written(run / "linux" / subvol / "etc/hostname") == "LAB1-05"
    assert "subvolid=5" in (run / "mounts").read_text()


def test_the_default_subvolume_is_used_when_it_holds_etc(tmp_path):
    """‏openSUSE: ה-default subvolume הוא ה-snapshot, והעיגון הרגיל כבר רואה
    ‏/etc — אין סיבה לחפש, ואין עיגון שני."""
    script, run = box(tmp_path, [BTRFS_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True)
    assert written(run / "linux/etc/hostname") == "LAB1-05"
    assert (run / "mounts").read_text().count("mount -t") == 1


def test_no_subvolume_holding_etc_is_a_named_failure(tmp_path):
    """עיקרון 5: "לא נמצא subvolume" הוא מצב בשם — לא mount_failed ולא הצלחה."""
    script, run = box(tmp_path, [BTRFS_PLAN], others="home var")
    rc, result = name(script)
    assert rc == 1
    assert result["ok"] is False
    assert result["code"] == "no_root_subvolume", result
    assert "תת-כרך" in result["error"]
    assert "0 top-level subvolumes hold /etc" in (run / "agent.log").read_text()


def test_two_subvolumes_holding_etc_are_not_guessed_between(tmp_path):
    """שני מועמדים — אין ניחוש: כישלון בשם, ולא נכתב לאף אחד מהם."""
    script, run = box(tmp_path, [BTRFS_PLAN], subvols="root rootfs-old")
    rc, result = name(script)
    assert rc == 1
    assert result["code"] == "ambiguous_root_subvolume", result
    assert written(run / "linux/root/etc/hostname") == ""
    assert written(run / "linux/rootfs-old/etc/hostname") == ""


def test_an_ext4_without_etc_is_named_not_a_mount_failure(tmp_path):
    """עוגן, אבל אין /etc — שונה מ"לא עוגן"."""
    script, _ = box(tmp_path, [LINUX_PLAN])
    rc, result = name(script)
    assert (rc, result["code"]) == (1, "no_etc")


# --- #107: dual-boot — שני הצדדים, ארבעה מצבים -----------------------------------

def test_a_dual_boot_disk_is_named_on_both_sides(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05",
                                "method": "offline-registry+etc-hostname"})
    assert written(run / "linux/etc/hostname") == "LAB1-05"
    assert "ComputerName LAB1-05" in written(run / "hivewrites")


def test_a_dual_boot_with_a_btrfs_subvolume_is_named_on_both_sides(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, BTRFS_PLAN], subvols="root")
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True)
    assert written(run / "linux/root/etc/hostname") == "LAB1-05"


def test_dual_boot_linux_failed_says_only_windows(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], mount="exit 1")
    rc, result = name(script)
    assert rc == 1 and result["ok"] is False
    assert result["code"] == "named_windows_only", result
    assert "לא הצלחנו לעגן את מחיצת הלינוקס" in result["error"]
    assert "ComputerName LAB1-05" in written(run / "hivewrites")  # הצד השני כן נכתב


def test_dual_boot_windows_failed_says_only_linux(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True, hive=False)
    rc, result = name(script)
    assert rc == 1
    assert result["code"] == "named_linux_only", result
    assert "כוורת SYSTEM לא נמצאה" in result["error"]
    assert written(run / "linux/etc/hostname") == "LAB1-05"


def test_dual_boot_both_failed_says_neither_with_both_reasons(tmp_path):
    script, _ = box(tmp_path, [WIN_PLAN, LINUX_PLAN], hive=False, mount="exit 1")
    rc, result = name(script)
    assert rc == 1
    assert result["code"] == "named_neither", result
    assert "כוורת SYSTEM לא נמצאה" in result["error"]
    assert "לא הצלחנו לעגן את מחיצת הלינוקס" in result["error"]


def test_a_partial_dual_boot_name_reaches_the_report_as_a_warning(tmp_path):
    """הדרך לשרת (סעיף 4, ‏#856): ‏done + ‏error. הצד שנכשל נקרא בשמו."""
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], mount="exit 1")
    (run / "response.json").write_text("{}", encoding="utf-8")
    out = sh(script
             + f'. {posix(AGENT)}/lib/progress.sh; RESP={posix(run / "response.json")!r}; '
             'json_get() { case "$2" in *prefix) echo lab1;; *suffix) echo 05;; '
             '  .error) sed -n \'s/.*"error":"\\([^"]*\\)".*/\\1/p\' "$1";; *) echo null;; esac; }; '
             'target_init sda 4096; target_set sda done; name_this_machine sda sess1')
    assert out.returncode == 0, out.stderr
    assert written(run / "targets/sda/state") == "done"
    err = written(run / "targets/sda/error")
    assert err.startswith("שם המחשב לא נכתב: צד הלינוקס לא קיבל שם"), err
