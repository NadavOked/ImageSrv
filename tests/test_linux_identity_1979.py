"""‏#1979 (ו-#1978): זהות הלינוקס של כל משוכפל, על המגירה שלו.

‏#1979 — אימג' לינוקס שנקלט בלי ניקוי נושא את `/etc/machine-id` שלו: לכל
המשוכפלים אותו DUID ב-DHCP, ולכן אותה כתובת — ואותם מפתחות SSH של המארח.
‏`finish_grow` (כל מגירה, כל סבב, כל שיכפול מקומי) מרוקן עכשיו את
machine-id, מוחק את `ssh_host_*`, ומתקין יחידה חד-פעמית שמריצה
`ssh-keygen -A` לפני sshd — דביאן ואובונטו אינן יוצרות מפתח חסר בעצמן.

‏#1978 — אחרי שיכפול לכל המגירות אותו UUID; גישה לפי UUID הייתה עלולה לנחות
במגירה אחרת. הראיה כאן היא שלוש מגירות **זהות לחלוטין**, וכל אחת מעוגנת
דרך הצומת שלה (`/dev/sdX2`) ומשתנה לבדה. הסריקה הסטטית בסוף שומרת שאיש
לא יוסיף חיפוש לפי UUID/LABEL לנתיב הכתיבה.

‏`mount`/`umount` מזויפים: העיגון מעתיק את "הדיסק" (`disks/<צומת>`) לנקודת
העיגון ורושם את הצומת; הניתוק מחזיר את התוכן לאותו דיסק. כך מה שנכתב
לנקודת העיגון נוחת בדיוק בדיסק שעוגן — ושום דבר אחר.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_captured_stdout import LINUX_PLAN, WIN_PLAN, sh, stub

pytestmark = requires_native(("bash", BASH))

MOUNT = """for a in "$@"; do dev=$mnt; mnt=$a; done
echo "mount $dev" >> "$RUN_DIR/mounts"
d="$RUN_DIR/disks/${dev##*/}"
[ -d "$d" ] || exit 32
cp -R "$d/." "$mnt/" && echo "$d" > "$RUN_DIR/mounted\""""
UMOUNT = """d=$(cat "$RUN_DIR/mounted")
rm -rf "$d" && mkdir -p "$d" && cp -R "$1/." "$d/" && rm -rf "$1" && mkdir -p "$1\""""
UNIT = "imagesrv-ssh-hostkeys.service"
DRAWERS = ("sdb", "sdc", "sdd")


def disk(run: Path, node: str, *, systemd: bool = True, dbus_link: bool = False) -> Path:
    """מחיצת לינוקס כפי שהיא יוצאת מהשיכפול — זהה בכל המגירות."""
    root = run / "disks" / node
    (root / "etc/ssh").mkdir(parents=True)
    (root / "etc/machine-id").write_text("4b1e5a0c9f7d4e2a8c3b6d1f0e9a7c55\n", newline="\n")
    for key in ("ssh_host_ed25519_key", "ssh_host_ed25519_key.pub", "ssh_host_rsa_key"):
        (root / "etc/ssh" / key).write_text("same key on every clone\n", newline="\n")
    (root / "etc/ssh/sshd_config").write_text("PermitRootLogin no\n", newline="\n")
    if systemd:
        (root / "etc/systemd/system").mkdir(parents=True)
    dbus = root / "var/lib/dbus"
    dbus.mkdir(parents=True)
    if dbus_link:
        os.symlink("/etc/machine-id", dbus / "machine-id")
    else:
        (dbus / "machine-id").write_text("4b1e5a0c9f7d4e2a8c3b6d1f0e9a7c55\n", newline="\n")
    return root


def box(tmp_path: Path, plan: str = LINUX_PLAN, **disk_kw) -> tuple[str, Path]:
    run = tmp_path / "run"
    run.mkdir()
    for node in DRAWERS:
        disk(run, f"{node}2", **disk_kw)
        (run / "targets" / node).mkdir(parents=True)
    bindir = tmp_path / "bin"; bindir.mkdir()
    stubs = {"mount": MOUNT, "umount": UMOUNT, "sgdisk": "exit 0"}
    setup = "".join(stub(bindir / n, b) for n, b in stubs.items())
    setup += (
        f'export PATH="$(cd {posix(bindir)!r} && pwd):/usr/bin:/bin" '
        f'RUN_DIR={posix(run)!r} LOG_FILE={posix(run / "agent.log")!r}; '
        f'. {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/progress.sh; '
        f'. {posix(AGENT)}/lib/grow.sh; . {posix(AGENT)}/lib/ntfsunhib.sh; . {posix(AGENT)}/lib/writemark.sh; '
        f'. {posix(AGENT)}/lib/hostname.sh; . {posix(AGENT)}/lib/hostname_linux.sh; '
        f"manifest_plan() {{ printf '%s\\n' {' '.join(repr(ln) for ln in plan.splitlines())}; }}; "
        'partition_node() { printf "/dev/%s%s\\n" "$1" "$2"; }; '
        'boot_hidden_disk() { return 0; }; '   # #2015: the hidden sectors have their own file
    )
    return setup, run


def finish_all(setup: str) -> None:
    out = sh(setup + "for d in sdb sdc sdd; do finish_grow $d '' m.json || exit 7; done")
    assert out.returncode == 0, (out.stdout, out.stderr)


def mounted(run: Path) -> list[str]:
    """מה ש-mount המזויף רשם; כלום לא עוגן = רשימה ריקה, לא קובץ חסר."""
    f = run / "mounts"
    return [m for m in f.read_text().splitlines() if m] if f.exists() else []


def error_of(run: Path, node: str) -> str:
    f = run / "targets" / node / "error"
    return f.read_text(encoding="utf-8").strip() if f.exists() else ""


def test_three_identical_drawers_each_get_their_own_identity(tmp_path):
    setup, run = box(tmp_path)
    finish_all(setup)
    # ‏#1978: כל מגירה עוגנה דרך הצומת שלה — אחת, פעם אחת, בסדר.
    assert mounted(run) == [f"mount /dev/{n}2" for n in DRAWERS]
    for node in DRAWERS:
        etc = run / "disks" / f"{node}2" / "etc"
        assert (etc / "machine-id").exists() and (etc / "machine-id").read_bytes() == b""
        assert not list((etc / "ssh").glob("ssh_host_*")), node
        assert (etc / "ssh/sshd_config").read_text() == "PermitRootLogin no\n"
        unit = (etc / "systemd/system" / UNIT).read_text()
        assert "ExecStart=/usr/bin/ssh-keygen -A" in unit and "Before=ssh.service sshd.service" in unit
        wants = etc / "systemd/system/multi-user.target.wants" / UNIT
        assert wants.is_symlink() and os.readlink(wants) == f"../{UNIT}"
        assert not (run / "disks" / f"{node}2/var/lib/dbus/machine-id").exists()
        assert (run / "targets" / node / "state").read_text().strip() == "done"
        assert error_of(run, node) == "", node


def test_a_dbus_link_to_etc_machine_id_is_kept(tmp_path):
    setup, run = box(tmp_path, dbus_link=True)
    finish_all(setup)
    assert (run / "disks/sdb2/var/lib/dbus/machine-id").is_symlink()


def test_a_windows_disk_is_not_mounted_at_all(tmp_path):
    setup, run = box(tmp_path, plan=WIN_PLAN)
    finish_all(setup)
    assert mounted(run) == []
    assert (run / "disks/sdb2/etc/machine-id").read_text() != ""


def test_without_systemd_the_keys_stay_and_that_is_said(tmp_path):
    """אין מי שייצור מפתחות חדשים — מחיקה הייתה משאירה מכונה בלי SSH."""
    setup, run = box(tmp_path, systemd=False)
    finish_all(setup)
    etc = run / "disks/sdb2/etc"
    assert len(list((etc / "ssh").glob("ssh_host_*"))) == 3
    assert (etc / "machine-id").read_bytes() == b""
    assert (run / "targets/sdb/state").read_text().strip() == "done"
    assert "SSH" in error_of(run, "sdb") and "systemd" in error_of(run, "sdb")


BOOT_PLAN = LINUX_PLAN.replace("2|", "1|", 1).replace("|p2|", "|p1|") + "\n" + LINUX_PLAN


def test_a_boot_partition_before_the_root_is_mounted_and_passed_over(tmp_path):
    """פדורה: ‏/boot (ext4, אותו type GUID) לפני השורש. הראשונה אינה השורש."""
    setup, run = box(tmp_path, plan=BOOT_PLAN)
    for node in DRAWERS:
        (run / "disks" / f"{node}1" / "grub").mkdir(parents=True)
    finish_all(setup)
    assert mounted(run) == [f"mount /dev/{n}{i}" for n in DRAWERS for i in (1, 2)]
    for node in DRAWERS:
        assert (run / "disks" / f"{node}2/etc/machine-id").read_bytes() == b""
        assert not (run / "disks" / f"{node}1/etc").exists()
        assert error_of(run, node) == "", node


def test_a_linux_data_disk_without_etc_is_not_a_failure(tmp_path):
    """עוגן, נבדק, אין /etc — אין זהות לאפס. זו תשובה, לא "לא בדקנו"."""
    setup, run = box(tmp_path)
    import shutil
    for node in DRAWERS:
        shutil.rmtree(run / "disks" / f"{node}2/etc")
    finish_all(setup)
    assert len(mounted(run)) == 3
    assert all(error_of(run, n) == "" for n in DRAWERS)


def test_a_linux_partition_that_does_not_mount_is_said(tmp_path):
    setup, run = box(tmp_path)
    import shutil
    shutil.rmtree(run / "disks/sdc2")          # ‏mount המזויף נכשל על צומת בלי דיסק
    finish_all(setup)
    assert "לא הצלחנו לעגן" in error_of(run, "sdc")
    assert error_of(run, "sdb") == "" and error_of(run, "sdd") == ""


# --- החיווט: כל מסלול כתיבה מעביר את המניפסט --------------------------------

def test_every_finish_grow_call_in_the_agent_passes_the_manifest():
    """‏`finish_grow` בלי מניפסט מדלג על האיפוס — ולכן כל קורא מעביר אותו."""
    calls = []
    for f in sorted((AGENT / "lib").glob("*.sh")) + [AGENT / "imagectl-agent"]:
        for line in f.read_text(encoding="utf-8").splitlines():
            code = line.split("#", 1)[0]
            calls += [(f.name, m) for m in re.findall(r'finish_grow ("[^"]*" "[^"]*" "[^"]*"|[^;}|]*)', code)
                      if not code.lstrip().startswith("finish_grow()")]
    assert len(calls) == 3, calls
    for name, args in calls:
        assert len(re.findall(r'"[^"]*"', args)) == 3, (name, args)


# --- ‏#1978: אין חיפוש לפי UUID/LABEL בסוכן ----------------------------------

#: חיפוש התקן לפי זהות שהשיכפול משכפל. ‏`blkid -s UUID <צומת>` **קורא** ערך
#: מצומת ידוע — זה מותר; אלה **מוצאים** צומת לפי ערך, וזה מה שאסור.
LOOKUP = re.compile(r"blkid\s+(-[a-zA-Z]+\s+)*-[UL]\b|\bfindfs\b|/dev/disk/by-|"
                    r"\bmount\b[^#\n]*\b(PART)?(UUID|LABEL)=")
#: מדיית ההתקנה נמצאת לפי התווית שלה (#1190) — היא אינה דיסק משוכפל.
ALLOWED = {("installer_boot.sh", "blkid -L")}


def test_no_disk_is_found_by_uuid_or_label_in_the_agent():
    found = []
    for f in sorted((AGENT / "lib").glob("*.sh")) + [AGENT / "imagectl-agent", AGENT / "init"]:
        for no, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0] if not line.lstrip().startswith("#") else ""
            m = LOOKUP.search(code)
            if m and not any(f.name == n and s in code for n, s in ALLOWED):
                found.append(f"{f.name}:{no}: {line.strip()}")
    assert found == []
