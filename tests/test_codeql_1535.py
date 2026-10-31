"""‏#1535 — התראות CodeQL בשרת מהשער הציבורי של v0.75.0.

1. **הרשאות** (`py/overly-permissive-file`): אזור העבודה של עדכון ה-VM היה
   ‏0o755/0o644 — כל משתמש מקומי עבר בו וקרא את קובצי האתחול שהורדו מה-ESP.
   מי שצריך אותם בפועל הוא משתמש ה-VM בלבד. ‏#1535 צמצם ל-0o710/0o640 דרך
   הקבוצה שלו, אבל ‏CodeQL מסמן **כל** ביט שאינו של הבעלים (גם 0o710), והערת
   `codeql[...]` אינה סוגרת התראה בשער הציבורי (#1610). ‏לכן הגישה ניתנת
   **ב-ACL על ה-uid של משתמש ה-VM**: ‏root בעלים, ערך אחד בשמו (תיקייה —
   מעבר בלבד, ‏`boot_ca_probe` בודק `[ -f ]` על נתיבים ידועים ואינו מונה;
   קובץ — קריאה), והקבוצה ו"אחרים" — אפס. ‏QEMU פותח את הדיסק כ-root
   לפני `-run-with`, והשמירה רצה כשהוא כבוי.
2. **ניקוי תגיות** (`js/incomplete-multi-character-sanitization`):
   ‏vmupdate.js ו-nicsvc.js — ‏`stripTags` של console.js (‏#1523), לא ה-regex.
3. **log-injection**: שבירת שורה מהבקשה אינה מגיעה ליומן.
   (‏`stack-trace-exposure` — ב-`test_vmupdate_api.py`.)
"""

from __future__ import annotations

import ast
import errno
import json
import logging
import os
import re
import sqlite3
import struct
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from server.vm import agent_shell, vmdisk
from server.agent import gui_failure

STATIC = Path(__file__).resolve().parents[1] / "server" / "static"
posix_only = requires_native(posix=True, why="ACL/xattr של POSIX")

#: ה-uid של משתמש ה-VM בטסטים — שונה מהבעלים, כדי שהערך בשמו יהיה נראה לעין.
VM_UID = 54321
_ACL_NAMES = {0x01: "owner", 0x04: "group_obj", 0x10: "mask", 0x20: "other"}


def _mode(path: Path) -> int:
    return os.lstat(path).st_mode & 0o777


def _acl(path: Path) -> dict:
    """ה-ACL של `path` כמות שהקרנל מחזיר אותו: ‏{שם: הרשאה}, ו-`("user", uid)`
    לערך בשם. ‏{} כשאין ACL כלל."""
    try:
        raw = os.getxattr(path, "system.posix_acl_access", follow_symlinks=False)
    except OSError as exc:
        if exc.errno == errno.ENODATA:
            return {}
        raise
    assert struct.unpack_from("<I", raw)[0] == 2, raw
    out: dict = {}
    for tag, perm, ident in struct.iter_unpack("<HHI", raw[4:]):
        out[("user", ident) if tag == 0x02 else _ACL_NAMES.get(tag, (tag, ident))] = perm
    return out


def _only_the_vm_user(path: Path, perm: int, uid: int = VM_UID) -> None:
    """הבעלים נשאר, ערך אחד בשם משתמש ה-VM, והקבוצה ו"אחרים" — אפס."""
    owner = (_mode(path) >> 6) & 7
    assert _acl(path) == {"owner": owner, ("user", uid): perm, "group_obj": 0,
                          "mask": perm, "other": 0}, (path, _acl(path))
    assert _mode(path) & 0o007 == 0, (path, oct(_mode(path)))
    assert os.lstat(path).st_uid == os.geteuid()


# --- 1. הרשאות -----------------------------------------------------------------


class FakeBox:
    def fetch(self, _index, paths, dest):
        for path in paths:
            target = dest / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"MZ")


@posix_only
def test_boot_files_are_open_to_the_vm_user_only(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    paths = ["/EFI/BOOT/bootx64.efi", "/EFI/Microsoft/Boot/bootmgfw.efi"]
    target = vmdisk.boot_files(FakeBox(), 1, paths, run_dir, VM_UID)
    dirs = [target, target / "EFI", target / "EFI" / "BOOT", target / "EFI" / "Microsoft",
            target / "EFI" / "Microsoft" / "Boot"]
    files = [target / "EFI" / "BOOT" / "bootx64.efi",
             target / "EFI" / "Microsoft" / "Boot" / "bootmgfw.efi"]
    for path in dirs:
        _only_the_vm_user(path, 1)       # מעבר בלבד
    for path in files:
        _only_the_vm_user(path, 4)       # קריאה בלבד


@posix_only
def test_capture_opens_the_work_area_to_the_vm_user_for_passage_only(tmp_path):
    """הבדיקה רצה עד הדחייה הראשונה (מערכת קבצים שהשתנתה) — ההרשאות כבר נקבעו."""
    from test_vmupdate_isolation import SOURCE, FakeGuest, NoHost
    work = tmp_path / "work"
    work.mkdir()
    # הנקודה: התחלה **פתוחה לכולם** (כמו לפני #1535) והוכחה שנסגרת. ‏`chmod`
    # חיצוני ולא `os.chmod`: ‏CodeQL (‏#1610) מסמן 0o777 גם כשזה מתקן בטסט.
    subprocess.run(["chmod", "777", str(work)], check=True, stdin=subprocess.DEVNULL)
    assert _mode(work) & 0o007 == 0o007, oct(_mode(work))
    (work / "disk.raw").write_bytes(b"")
    (work / "source-manifest.json").write_text(json.dumps(SOURCE), encoding="utf-8")
    guest = FakeGuest(fs={"/dev/sda1": "vfat", "/dev/sda2": "ext4"})
    with pytest.raises(vmdisk.DiskError):
        vmdisk.capture_disk(NoHost(), work, work / "out", lambda n: None,
                            user=vmdisk.VmUser(VM_UID, os.getgid()),
                            appliance=lambda disk: vmdisk.Appliance(disk, factory=lambda: guest))
    for folder in (work, work / "run"):
        _only_the_vm_user(folder, 1)


@posix_only
def test_a_new_parent_for_the_vm_users_dirs_is_passage_only_for_its_user(tmp_path):
    parent = tmp_path / "work" / "run"
    # ה-uid האמיתי: ‏`fresh_user_dir` עושה `fchown` לילד, וזה דורש root לכל uid אחר.
    child = agent_shell.fresh_user_dir(parent, os.getuid(), os.getgid())
    assert child.parent == parent
    _only_the_vm_user(parent, 1, os.getuid())


@posix_only
def test_a_filesystem_without_acl_fails_loudly_and_does_not_widen(tmp_path, monkeypatch):
    """עיקרון 5: אי אפשר לתת גישה בשם — נכשלים, לא נופלים להרשאה רחבה."""
    def refuse(*_a, **_kw):
        raise OSError(errno.EOPNOTSUPP, "Operation not supported")

    target = tmp_path / "dir"
    target.mkdir(mode=0o700)
    before = _mode(target)
    monkeypatch.setattr(os, "setxattr", refuse)
    with pytest.raises(OSError) as caught:
        agent_shell.grant_user(target, VM_UID, agent_shell.ACL_X)
    assert str(target) in str(caught.value) and caught.value.errno == errno.EOPNOTSUPP
    assert _mode(target) == before


# --- 1ב. הכלל של CodeQL, מקומית ----------------------------------------------
# הפרטי אינו מריץ CI (ראו CLAUDE.md), ולכן ההתראה הייתה מגיעה רק בשער הציבורי.

ROOT = Path(__file__).resolve().parents[1]


def _permissive_chmods(source: str) -> list[tuple[int, int]]:
    """‏(שורה, מצב) לכל `chmod` עם מצב קבוע שיש בו ביט שאינו של הבעלים — מה
    ש-`py/overly-permissive-file` מסמן. ‏מצב נקרא מליטרל, או מקבוע שלם ברמת
    המודול (כמו שהיה `VM_PASS_MODE`)."""
    tree = ast.parse(source)
    consts = {n.targets[0].id: n.value.value for n in tree.body
              if isinstance(n, ast.Assign) and len(n.targets) == 1
              and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Constant)
              and isinstance(n.value.value, int)}
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("chmod", "fchmod", "lchmod")):
            continue
        # רק `os.chmod` — ‏`Path.chmod` אינו מודל ב-CodeQL (ואלפי מופעים בטסטים).
        if not (isinstance(node.func.value, ast.Name) and node.func.value.id == "os"
                and len(node.args) >= 2):
            continue
        arg = node.args[1]
        mode = (arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, int)
                else consts.get(arg.id) if isinstance(arg, ast.Name) else None)
        if mode is not None and mode & 0o077:
            found.append((node.lineno, mode))
    return found


def test_the_rule_flags_group_execute_and_passes_owner_only():
    """בקרה על הבודק עצמו: אותו דפוס שהשער סימן (0o710, גם דרך קבוע) נתפס."""
    assert _permissive_chmods("import os\nos.chmod(p, 0o710)\n") == [(2, 0o710)]
    assert _permissive_chmods("import os\nM = 0o640\nos.chmod(p, M)\n") == [(3, 0o640)]
    assert _permissive_chmods("import os\nos.chmod(p, 0o777)\n") == [(2, 0o777)]
    assert _permissive_chmods("import os\nos.chmod(p, 0o700)\nos.chmod(p, 0o600)\n") == []


def test_no_os_chmod_in_the_server_grants_non_owner_bits():
    """‏`tests/` לא נסרק כאן: ‏`test_interserver_enrollment.py:293` (התראה 147,
    קיימת מלפני #1610) פותח הרשאה בכוונה כדי לבדוק שנדחית."""
    bad = {}
    for path in sorted((ROOT / "server").rglob("*.py")):
        found = _permissive_chmods(path.read_text(encoding="utf-8"))
        if found:
            bad[str(path.relative_to(ROOT))] = [(line, oct(mode)) for line, mode in found]
    assert not bad, bad


# --- 2. ניקוי תגיות ב-JS -------------------------------------------------------


@pytest.mark.parametrize("name", ["vmupdate.js", "nicsvc.js"])
def test_tag_stripping_goes_through_strip_tags_not_the_regex(name):
    """הדפוס `/<[^>]*>/g` → "" משאיר `<script>` מ-`<<b>script>` (‏#1523).
    ‏stripTags עצמו נבדק ב-`strip_tags.test.cjs`."""
    source = (STATIC / name).read_text(encoding="utf-8")
    assert not re.search(r"""\.replace\(/<\[\^>\]\*>/g,\s*(""|'')\)""", source), name
    assert "stripTags(" in source, name


# --- 3. log-injection -----------------------------------------------------------


def test_a_gui_failure_that_was_not_journaled_logs_the_mac_on_one_line(monkeypatch, caplog):
    def broken(*_a, **_kw):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(gui_failure, "journal", broken)
    with caplog.at_level(logging.ERROR, logger="imagesrv.gui_failure"):
        noted = gui_failure.note(None, "aa:bb\r\nFORGED: admin logged in",
                                 {"screen": "build", "boot_next": "set"}, known=True)
    assert noted is False
    messages = [r.getMessage() for r in caplog.records if r.name == "imagesrv.gui_failure"]
    assert len(messages) == 1 and "FORGED" in messages[0], messages
    assert "\n" not in messages[0] and "\r" not in messages[0], messages
