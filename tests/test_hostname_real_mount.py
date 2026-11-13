"""#89 ו-#107: שם המחשב בצד הלינוקס — מול btrfs ו-ext4 **אמיתיים**.

`test_hostname_linux_dual.py` מכסה את הלוגיקה עם `mount` מזויף, והמזויף
הוא ההנחה שלנו על הקרנל: שעיגון רגיל נותן את ה-subvolume ברירת המחדל,
ש-`subvolid=5` חושף את ילדי ה-top level כתיקיות, ושה-snapshot של openSUSE
נראה דרך ה-default. כאן ההנחה נבדקת: קובץ דליל על התקן loop, `mkfs` אמיתי,
subvolumes אמיתיים, `write_hostname` של הסוכן בדיוק כמו שהוא — ורק
`manifest_plan`/`partition_node` מוחלפים, כי אין אימג' ואין דיסק.

הקריאה בחזרה אינה דרך הסוכן: עיגון עצמאי של ה-top level, וכל
`etc/hostname` שנמצא בו — כדי שסוכן שגוי לא יוכל לחתום על עצמו, וכדי
שכתיבה למקום הלא נכון (ה-subvolume השני, ה-top level) תיראה.

**מה זה לא מוכיח:** צד ה-Windows של dual-boot (כוורת SYSTEM אמיתית —
המזויף ב-`test_hostname_linux_dual.py` בלבד), ושחזור מאימג' על ברזל.

**הרצה:** לינוקס, `sudo -n`, `losetup`, `mkfs.btrfs`/`btrfs`
(btrfs-progs) ו-`mkfs.ext4`. בתחנת הווינדוס — דילוג; כש-
`IMAGESRV_REQUIRE_NATIVE=1` — כלי חסר הוא כישלון בשמו (`native.py`, #52).
המכונה היחידה שנוגעים בה: קובץ דליל ב-`tmp_path`. ה-`mkfs` רץ רק אחרי
שנבדק שה-loop מגובה **בקובץ שלנו**.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_smart import AGENT

SBIN_PATH = "/usr/local/sbin:/usr/sbin:/sbin:/usr/local/bin:/usr/bin:/bin"
TOOLS = ("losetup", "mount", "umount", "mkfs.btrfs", "btrfs", "mkfs.ext4", "truncate")


def _sudo_ok() -> bool:
    if os.name == "nt" or not shutil.which("sudo"):
        return False
    return subprocess.run(["sudo", "-n", "true"], capture_output=True,
                          stdin=subprocess.DEVNULL).returncode == 0


pytestmark = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, shutil.which(t, path=SBIN_PATH)) for t in TOOLS],
    posix=True, why="שורש, התקן loop ו-mkfs אמיתיים — ה-Testrunner",
)

#: מחיצה אחת בתוכנית, כמו ב-`test_captured_stdout.LINUX_PLAN`; ה-fs מוחלף.
PLAN = "2|0FC63DAF-8483-4772-8E79-3D69D8477DE4|linux|{fs}|4096|1|p2|y|false|u"

#: מערכת שהותקנה במחשב הבנייה: השם שלו, ושורת 127.0.1.1 שהמתקין כתב.
SEED = r"""seed() { mkdir -p "$1/etc" && echo BUILD-01 > "$1/etc/hostname" \
  && printf '127.0.0.1\tlocalhost\n127.0.1.1\tBUILD-01\n' > "$1/etc/hosts"; }
sv() { btrfs subvolume create "$1" > /dev/null; }
"""

#: איך כל הפצה מניחה את השורש על btrfs ($top = ה-top level, מעוגן).
LAYOUTS = {
    "fedora": 'sv "$top/root" && sv "$top/home" && seed "$top/root"',
    "ubuntu": 'sv "$top/@" && sv "$top/@home" && seed "$top/@"',
    "manual": 'sv "$top/@rootfs" && seed "$top/@rootfs"',
    # snapper: השורש הוא snapshot מקונן, וה-default מצביע עליו — ה-top level
    # ו-`@` עצמו אינם מחזיקים /etc.
    "opensuse": 'sv "$top/@" && sv "$top/@/.snapshots" && mkdir -p "$top/@/.snapshots/1" '
                '&& sv "$top/@/.snapshots/1/snapshot" && seed "$top/@/.snapshots/1/snapshot" '
                '&& btrfs subvolume set-default "$top/@/.snapshots/1/snapshot"',
    "ambiguous": 'sv "$top/root" && sv "$top/root-old" && seed "$top/root" && seed "$top/root-old"',
    "no_root": 'sv "$top/home" && sv "$top/var"',
    "ext4": 'seed "$top"',
    "ext4_no_etc": 'mkdir -p "$top/lost-and-found-only"',
}

SCRIPT = r"""set -u
img="$W/disk.img"; top="$W/top"; loop=""
mkdir -p "$top" "$W/run"
cleanup() {
    umount "$top" 2> /dev/null; umount "$W/run/linux" 2> /dev/null
    [ -z "$loop" ] || losetup -d "$loop"
    chown -R "$OWNER" "$W"
}
trap cleanup EXIT
truncate -s 320M "$img" || exit 90
loop=$(losetup -f --show "$img") || exit 91
[ "$(losetup -n -O BACK-FILE "$loop")" = "$img" ] || { echo "$loop is not backed by $img" >&2; exit 92; }
case "$FS" in
    btrfs) mkfs.btrfs -q -f "$loop" > /dev/null || exit 93 ;;
    ext4)  mkfs.ext4 -q -F "$loop" > /dev/null || exit 93 ;;
esac
mount -t "$FS" "$loop" "$top" || exit 94
""" + SEED + r"""
{ LAYOUT; } || { echo "layout failed" >&2; exit 95; }
umount "$top" || exit 96

# The agent as it runs: its own shell and RUN_DIR; only the plan and the node are given.
RUN_DIR="$W/run" LOOP="$loop" sh -c '
    . "$AGENT/lib/core/common.sh"; . "$AGENT/lib/restore/restore.sh"
    . "$AGENT/lib/postdeploy/hostname.sh"; . "$AGENT/lib/postdeploy/unattend.sh"
    . "$AGENT/lib/postdeploy/hostname_linux.sh"
    manifest_plan() { printf "%s\n" "$PLAN"; }
    partition_node() { echo "$LOOP"; }
    write_hostname sda /dev/null LAB1-05' > "$W/result" 2> "$W/agent.stderr"
echo $? > "$W/rc"
awk -v m="$W/run/linux" 'index($2, m) == 1 { n++ } END { print n + 0 }' /proc/mounts > "$W/left_mounted"

# Read back on our own: the btrfs top level shows every subvolume as a directory.
if [ "$FS" = btrfs ]; then mount -t btrfs -o subvolid=5 "$loop" "$top" || exit 97
else mount -t "$FS" "$loop" "$top" || exit 97; fi
( cd "$top" && find . -path '*/etc/hostname' | sort | while read -r f; do
      d=${f%/hostname}
      printf '%s|%s|%s\n' "${d#./}" "$(cat "$f")" "$(awk '$1 == "127.0.1.1" { print $2 }' "$d/hosts")"
  done ) > "$W/names" || exit 98
umount "$top" || exit 99
"""


def run(tmp_path: Path, layout: str) -> tuple[int, dict, dict[str, tuple[str, str]], str]:
    """(rc, התוצאה של סעיף 5, {תיקיית etc: (hostname, שם ב-127.0.1.1)}, כמה נשאר מעוגן)."""
    fs = "ext4" if layout.startswith("ext4") else "btrfs"
    w = tmp_path / "w"
    w.mkdir()
    env = {"W": str(w), "FS": fs, "PLAN": PLAN.format(fs=fs), "AGENT": str(AGENT),
           "OWNER": f"{os.getuid()}:{os.getgid()}", "PATH": SBIN_PATH}
    proc = subprocess.run(
        ["sudo", "-n", "env", *[f"{k}={v}" for k, v in env.items()],
         "sh", "-c", SCRIPT.replace("LAYOUT", LAYOUTS[layout])],
        capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=180)
    assert proc.returncode == 0, (proc.returncode, proc.stderr)  # הסביבה עצמה, לא הסוכן
    agent_err = (w / "agent.stderr").read_text(encoding="utf-8")
    lines = (w / "result").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1, (lines, agent_err)  # רק אובייקט התוצאה ב-stdout
    names = {}
    for line in (w / "names").read_text(encoding="utf-8").splitlines():
        etc, hostname, hosts = line.split("|")
        names[etc] = (hostname, hosts)
    rc = int((w / "rc").read_text().strip())
    return rc, json.loads(lines[0]), names, (w / "left_mounted").read_text().strip()


NAMED = ("LAB1-05", "LAB1-05")
UNTOUCHED = ("BUILD-01", "BUILD-01")


# --- #89: השורש נמצא, לא מנוחש -------------------------------------------------

@pytest.mark.parametrize("layout, root", [
    ("fedora", "root/etc"),
    ("ubuntu", "@/etc"),
    ("manual", "@rootfs/etc"),
    ("opensuse", "@/.snapshots/1/snapshot/etc"),
])
def test_a_real_btrfs_root_gets_the_name_wherever_it_lives(tmp_path, layout, root):
    """הגדרת "גמור" של #89: subvolume שאינו `@` מקבל את השם — ורק הוא."""
    rc, result, names, left = run(tmp_path, layout)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "etc-hostname"})
    assert names == {root: NAMED}
    assert left == "0"


def test_two_real_roots_are_a_named_failure_and_neither_is_written(tmp_path):
    rc, result, names, left = run(tmp_path, "ambiguous")
    assert (rc, result["ok"], result["code"]) == (1, False, "ambiguous_root_subvolume"), result
    assert names == {"root/etc": UNTOUCHED, "root-old/etc": UNTOUCHED}
    assert left == "0"


def test_no_real_subvolume_holding_etc_is_named_not_a_mount_failure(tmp_path):
    """עיקרון 5: "לא נמצא subvolume" ≠ "לא הצלחנו לעגן"."""
    rc, result, names, left = run(tmp_path, "no_root")
    assert (rc, result["code"]) == (1, "no_root_subvolume"), result
    assert names == {}
    assert left == "0"


# --- ext4: המסלול שלא השתנה ------------------------------------------------------

def test_a_real_ext4_root_gets_the_name(tmp_path):
    rc, result, names, left = run(tmp_path, "ext4")
    assert (rc, result["ok"]) == (0, True), result
    assert names == {"etc": NAMED}
    assert left == "0"


def test_a_real_ext4_without_etc_is_named(tmp_path):
    rc, result, names, left = run(tmp_path, "ext4_no_etc")
    assert (rc, result["code"]) == (1, "no_etc"), result
    assert names == {}
    assert left == "0"
