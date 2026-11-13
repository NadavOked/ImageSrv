"""#107 (המשך): שם המחשב בכל שורש לינוקס שעל הדיסק — לא רק בראשון.

הקליטה מסמנת `"system": true` על **כל** מחיצת לינוקס שיש בה etc/os-release
או etc/fstab (#1899, `_linux_system` ב-manifest.sh) — דיסק עם שתי הפצות
מקבל שני סימונים. השחזור לקח את הראשון בלבד (`[...][0]` ב-`_mount_linux`),
והשורש השני עלה עם השם שבאימג' — אותה צורת כשל של #107, רק בין שתי מערכות
לינוקס במקום Windows ולינוקס. עכשיו כל שורש מסומן נכתב; שורש שנכשל נאמר
בשמו (מספר המחיצה), והאחרים נכתבים בכל זאת (עיקרון 5).

אותו קו ב-`_mount_linux` שבר את `linux_identity_reset` (#1979, grow.sh):
היא עוברת על כל מחיצת לינוקס עם תוכנית של שורה אחת, אבל `_mount_linux`
לקחה את הסימון מהמניפסט בלי להסתכל בתוכנית שקיבלה — מחיצה 2 (/boot)
עוגנה כ-`mount -t '' /dev/sda3`, ושורש מסומן שני לא אופס לעולם.

`mount`/`umount` מזויפים כמו ב-`test_linux_identity_1979`: העיגון מעתיק את
"המחיצה" (`disks/<צומת>`) לנקודת העיגון, והניתוק מחזיר את התוכן אליה — מה
שנכתב נוחת בדיוק במחיצה שעוגנה. `jq` מזויף מחזיר את `$MARKED` (`[0]` — את
הראשון, אחרת את כולם), כמו jq אמיתי על מניפסט שבו המחיצות האלה מסומנות.
הטסט האחרון מריץ jq אמיתי על מניפסט אמיתי (לינוקס; בווינדוס אין jq).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from agentlib import agent_sources

from native import requires_native
from test_agent import AGENT, BASH, CLEAN_HIVE, posix
from test_captured_stdout import HIVEWRITE_OK, sh, stub

pytestmark = requires_native(("bash", BASH))

LNX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
BOOT = f"2|{LNX}|linux|ext4|4096|1|p2|y|false|u"
ROOT_A = f"3|{LNX}|linux|ext4|8192|1|p3|y|false|u"
ROOT_B = f"4|{LNX}|linux|ext4|16384|1|p4|y|false|u"
WIN = "1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7|windows|ntfs|2048|1|p1|y|false|u"

MOUNT = """for a in "$@"; do dev=$mnt; mnt=$a; done
echo "mount $*" >> "$RUN_DIR/mounts"
d="$RUN_DIR/disks/${dev##*/}"
[ -d "$d" ] || exit 32
cp -R "$d/." "$mnt/" && echo "$d" > "$RUN_DIR/mounted\""""
UMOUNT = """case "$1" in */linux) ;; *) exit 0 ;; esac   # \u200fצד ה-Windows: ntfs-3g מזויף, אין מה להחזיר
d=$(cat "$RUN_DIR/mounted")
rm -rf "$d" && mkdir -p "$d" && cp -R "$1/." "$d/" && rm -rf "$1" && mkdir -p "$1\""""
JQ = """case "$*" in
  *'[0]'*) set -- $MARKED; [ -z "${1:-}" ] || echo "$1" ;;
  *) for i in $MARKED; do echo "$i"; done ;;
esac"""


def seed(run: Path, node: str) -> Path:
    """שורש לינוקס כפי שנקלט ממחשב הבנייה: השם שלו, ושורת 127.0.1.1."""
    etc = run / "disks" / node / "etc"
    etc.mkdir(parents=True)
    (etc / "hostname").write_text("BUILD-01\n", newline="\n")
    (etc / "hosts").write_text("127.0.0.1\tlocalhost\n127.0.1.1\tBUILD-01\n", newline="\n")
    (etc / "machine-id").write_text("4b1e5a0c9f7d4e2a8c3b6d1f0e9a7c55\n", newline="\n")
    return etc


def box(tmp_path: Path, plan: list[str], marked: str, *, roots=("sda3", "sda4"),
        boot: bool = True, jq: bool = True) -> tuple[str, Path]:
    run = tmp_path / "run"
    (run / "linux").mkdir(parents=True)
    for node in roots:
        seed(run, node)
    if boot:
        (run / "disks/sda2/grub").mkdir(parents=True)   # /boot נפרד: אין etc
    bindir = tmp_path / "bin"; bindir.mkdir()
    stubs = {"mount": MOUNT, "umount": UMOUNT, **({"jq": JQ} if jq else {})}
    script = "".join(stub(bindir / n, b) for n, b in stubs.items())
    script += (
        f'export PATH="$(cd {posix(bindir)!r} && pwd):$PATH" MARKED={marked!r} '
        f'RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; {agent_sources("sysinfo", "diskinfo", "progress", "failmark")}'
        f'. {posix(AGENT)}/lib/restore/restore.sh; . {posix(AGENT)}/lib/restore/grow.sh; '
        f'. {posix(AGENT)}/lib/postdeploy/hostname.sh; . {posix(AGENT)}/lib/postdeploy/unattend.sh; '
        f'. {posix(AGENT)}/lib/postdeploy/hostname_linux.sh; '
        f"manifest_plan() {{ printf '%s\\n' {' '.join(repr(p) for p in plan)}; }}; "
        'partition_node() { printf "/dev/%s%s\\n" "$1" "$2"; }; '
    )
    return script, run


def name(script: str, manifest: str = "/dev/null") -> tuple[int, dict]:
    out = sh(script + f'write_hostname sda {manifest} LAB1-05')
    lines = out.stdout.strip().splitlines()
    assert len(lines) == 1, (out.stdout, out.stderr)   # רק אובייקט התוצאה
    return out.returncode, json.loads(lines[0])


def names(run: Path) -> dict[str, tuple[str, str]]:
    """{מחיצה: (etc/hostname, השם בשורת 127.0.1.1)} — מה שנמצא בכל "מחיצה"."""
    found = {}
    for etc in sorted((run / "disks").glob("*/etc")):
        hosts = (etc / "hosts").read_text(encoding="utf-8").splitlines()
        found[etc.parent.name] = ((etc / "hostname").read_text(encoding="utf-8").strip(),
                                  next(ln.split()[1] for ln in hosts if ln.startswith("127.0.1.1")))
    return found


NAMED = ("LAB1-05", "LAB1-05")
UNTOUCHED = ("BUILD-01", "BUILD-01")


# --- שם המחשב: כל שורש מסומן ------------------------------------------------------

def test_every_marked_linux_root_gets_the_name(tmp_path):
    """שתי הפצות על דיסק אחד — שתיהן נקראות בשם. /boot (2) אינו נגע."""
    script, run = box(tmp_path, [BOOT, ROOT_A, ROOT_B], "3 4")
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "etc-hostname"})
    assert names(run) == {"sda3": NAMED, "sda4": NAMED}
    assert "/dev/sda2" not in (run / "mounts").read_text()


def test_a_root_that_failed_is_named_by_partition_and_the_others_are_written(tmp_path):
    """שורש שלא עוגן — כישלון בשם המחיצה; השורש השני נכתב בכל זאת."""
    script, run = box(tmp_path, [BOOT, ROOT_A, ROOT_B], "3 4", roots=("sda3",))
    rc, result = name(script)
    assert (rc, result["ok"], result["code"]) == (1, False, "mount_failed"), result
    assert result["error"].startswith("מחיצה 4: לא הצלחנו לעגן את מחיצת הלינוקס"), result
    assert names(run) == {"sda3": NAMED}


def test_dual_boot_with_two_linux_roots_names_all_three(tmp_path):
    """#107 בצורתו המלאה: Windows ושתי מערכות לינוקס."""
    script, run = box(tmp_path, [WIN, ROOT_A, ROOT_B], "3 4", boot=False)
    (run / "win/Windows/System32/config").mkdir(parents=True)
    (run / "win/Windows/System32/config/SYSTEM").write_bytes(CLEAN_HIVE)
    script = (stub(tmp_path / "bin/ntfs-3g", "exit 0") + stub(tmp_path / "bin/hivewrite", HIVEWRITE_OK)
              + script + "system_partition() { echo 1; }; ")
    rc, result = name(script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05",
                                "method": "offline-registry+etc-hostname"}), result
    assert names(run) == {"sda3": NAMED, "sda4": NAMED}


# --- שומרים: מה שעבד קודם עובד גם עכשיו -------------------------------------------

def test_one_marked_root_after_boot_is_the_one_written(tmp_path):
    """#1899: /boot ראשונה, השורש מסומן — נכתב השורש בלבד."""
    script, run = box(tmp_path, [BOOT, ROOT_A], "3", roots=("sda3",))
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    assert names(run) == {"sda3": NAMED}


def test_an_old_manifest_without_marks_names_the_first_linux_partition(tmp_path):
    """מניפסט מלפני #1899: הראשונה, כמו קודם — לא כל מחיצה (/home אינה שורש)."""
    script, run = box(tmp_path, [ROOT_A, ROOT_B], "", boot=False)
    rc, result = name(script)
    assert (rc, result["ok"]) == (0, True), result
    assert names(run) == {"sda3": NAMED, "sda4": UNTOUCHED}


# --- #1979: איפוס הזהות עובר על כל מחיצה בתוכנית של שורה אחת -----------------------

def identity(script: str) -> tuple[int, str]:
    out = sh(script + 'linux_identity_reset sda /dev/null; echo "rc=$? why=$LINUX_ID_ERROR"')
    return out.returncode, out.stdout.strip().splitlines()[-1]


def machine_ids(run: Path) -> dict[str, str]:
    return {etc.parent.name: (etc / "machine-id").read_text(encoding="utf-8").strip()
            for etc in sorted((run / "disks").glob("*/etc"))}


def test_identity_reset_mounts_each_partition_it_was_given(tmp_path):
    """/boot נפרד ושורש מסומן: כל מחיצה מעוגנת דרך הצומת שלה, והשורש מתאפס."""
    script, run = box(tmp_path, [BOOT, ROOT_A], "3", roots=("sda3",))
    _, last = identity(script)
    assert last == "rc=0 why=", (last, (run / "mounts").read_text())
    mounts = (run / "mounts").read_text()
    assert "/dev/sda2 " in mounts and "/dev/sda3 " in mounts, mounts
    assert machine_ids(run) == {"sda3": ""}


def test_identity_reset_reaches_the_second_marked_root(tmp_path):
    script, run = box(tmp_path, [BOOT, ROOT_A, ROOT_B], "3 4")
    _, last = identity(script)
    assert last == "rc=0 why=", last
    assert machine_ids(run) == {"sda3": "", "sda4": ""}


# --- jq אמיתי על מניפסט אמיתי --------------------------------------------------------

@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required (the testrunner)")
def test_real_jq_finds_every_marked_root_in_a_real_manifest(tmp_path):
    parts = [dict(index=i, role="linux", fs="ext4") for i in (2, 3, 4)]
    parts[1]["system"] = parts[2]["system"] = True
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"partitions": parts}), encoding="utf-8")
    script, run = box(tmp_path, [BOOT, ROOT_A, ROOT_B], "", jq=False)
    rc, result = name(script, posix(manifest))
    assert (rc, result["ok"]) == (0, True), result
    assert names(run) == {"sda3": NAMED, "sda4": NAMED}
