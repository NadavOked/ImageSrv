"""#1912: שומרי רגרסיה ל-`disk_serial` (agent/lib/hw/diskinfo.sh) — #16 ו-#30.

#16: מגירה בלי serial נעלמה מסבב החדר — ספירת החדר היא לפי serial, ודיסק
וירטואלי (serial: null) הפך משכפל שלם לשקוף. התיקון: נפילה ל-`wwid`.

#30: `hdparm -I` על דיסק וירטואלי מייצר שגיאת קרנל בכל קריאה, וה-hello
הקבוע הציף את מסך הבנייה. התיקון: `wwid` **לפני** `hdparm`, ו-hdparm
נשאר מוצא אחרון.

hdparm רץ רק כש-`SYSROOT` ריק (מכונה אמיתית), ולכן הטסט של #30 מריץ את
`disk_serial` עם `SYSROOT=""` ומפנה את `/sys/...` לעץ מזויף דרך פונקציית
`cat` — ו-hdparm מזויף על PATH רושם כל הפעלה שלו.

בקרה שלילית (מוטציות על diskinfo.sh, ראו גוף ה-PR):
  A. בלוק ה-wwid הוסר (הקוד שלפני #16) — נופלים הטסט של #16 והטסט של #30.
  B. wwid אחרי hdparm (הקוד שבין #16 ל-#30) — נופל הטסט של #30 בלבד.
  serial-קודם ו-hdparm-כמוצא-אחרון עוברים בשני המצבים: שומרים מפני
  "תיקון" שמוחק את hdparm או מעדיף wwid על serial אמיתי.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = _find_bash()
pytestmark = requires_native(("bash", BASH), why="diskinfo.sh הוא POSIX sh")

WWID = "naa.60022480a1b2c3d4e5f60718293a4b5c"
HDPARM_SERIAL = "HD-FROM-HDPARM"


def _posix(p: Path) -> str:
    return str(p).replace("\\", "/")


def _machine(tmp_path: Path, *, serial: str | None, wwid: str | None) -> Path:
    """עץ /sys מזויף עם דיסק sda אחד."""
    device = tmp_path / "root" / "sys" / "block" / "sda" / "device"
    device.mkdir(parents=True)
    if serial is not None:
        (device / "serial").write_text(serial + "\n", encoding="utf-8")
    if wwid is not None:
        (device / "wwid").write_text(wwid + "\n", encoding="utf-8")
    return tmp_path / "root"


def _fake_hdparm(tmp_path: Path) -> tuple[Path, Path]:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    log = tmp_path / "hdparm.log"
    tool = bindir / "hdparm"
    tool.write_text('#!/bin/sh\nprintf "%s\\n" "$*" >> "$HDPARM_LOG"\n'
                    f'printf "\\tSerial Number:      {HDPARM_SERIAL}\\n"\n',
                    encoding="utf-8", newline="\n")
    tool.chmod(0o755)
    return bindir, log


def _disk_serial(tmp_path: Path, root: Path, *, real_machine: bool) -> tuple[str, list[str]]:
    """מריץ `disk_serial sda`. real_machine: SYSROOT ריק (hdparm מותר), ו-/sys מופנה ל-root."""
    bindir, log = _fake_hdparm(tmp_path)
    dev = tmp_path / "dev"
    dev.mkdir()
    if real_machine:
        setup = ('export SYSROOT=""; '
                 'cat() { case "$1" in /sys/*) command cat "$FAKE_ROOT$1" ;; *) command cat "$@" ;; esac; }; ')
    else:
        setup = f'export SYSROOT={_posix(root)!r}; '
    # PATH דרך הסביבה ולא בתוך הסקריפט: `C:/...` בווינדוס מכיל `:` — מפריד ה-PATH של bash.
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    script = ('export PATH="$PATH:/usr/bin:/bin"; '
              f'export DEVROOT={_posix(dev)!r} FAKE_ROOT={_posix(root)!r} '
              f'HDPARM_LOG={_posix(log)!r}; '
              f'. {_posix(LIB / "core" / "common.sh")}; . {_posix(LIB / "hw" / "sysinfo.sh")}; . {_posix(LIB / "hw" / "diskinfo.sh")}; '
              + setup + 'printf "[%s]" "$(disk_serial sda)"')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          env=env, stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == 0, proc.stderr
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    return proc.stdout, calls


def test_a_disk_without_a_serial_reports_its_wwid(tmp_path):
    """#16: דיסק וירטואלי בלי serial — ה-wwid הוא המזהה, לא ריק."""
    root = _machine(tmp_path, serial=None, wwid=WWID)
    out, _ = _disk_serial(tmp_path, root, real_machine=False)
    assert out == f"[{WWID}]"


def test_a_real_serial_still_wins_over_the_wwid(tmp_path):
    """#16, השומר מפני תיקון-יתר: serial אמיתי עדיין ראשון."""
    root = _machine(tmp_path, serial="  S4EWNX0R123456  ", wwid=WWID)
    out, _ = _disk_serial(tmp_path, root, real_machine=False)
    assert out == "[S4EWNX0R123456]"


def test_hdparm_is_not_run_when_the_wwid_answers(tmp_path):
    """#30: יש wwid → hdparm לא מופעל כלל (כל הפעלה על דיסק וירטואלי = שורת קרנל במסך)."""
    root = _machine(tmp_path, serial=None, wwid=WWID)
    out, calls = _disk_serial(tmp_path, root, real_machine=True)
    assert calls == [], f"hdparm הופעל למרות שיש wwid: {calls}"
    assert out == f"[{WWID}]"


def test_hdparm_is_still_the_last_resort(tmp_path):
    """#30, השומר מפני תיקון-יתר: בלי serial ובלי wwid — hdparm כן נשאל, פעם אחת."""
    root = _machine(tmp_path, serial=None, wwid=None)
    out, calls = _disk_serial(tmp_path, root, real_machine=True)
    assert len(calls) == 1 and calls[0].endswith("/dev/sda"), calls
    assert out == f"[{HDPARM_SERIAL}]"


def test_a_disk_with_no_identity_at_all_reports_nothing(tmp_path):
    """בלי serial, בלי wwid ובלי hdparm (SYSROOT מזויף) — ריק, וה-hello יכתוב null ("לא מזוהה")."""
    root = _machine(tmp_path, serial=None, wwid=None)
    out, calls = _disk_serial(tmp_path, root, real_machine=False)
    assert (out, calls) == ("[]", [])


# --- the same order, seen in the hello body (#1912, #16 and #30 only) -----------------------------------------------


def _disk_tree(tmp_path: Path, disks: dict[str, tuple[str | None, str | None]]) -> Path:
    """/sys fake with one entry per disk: name -> (serial, wwid). `None` = the file is not there."""
    root = tmp_path / "root"
    for name, (serial, wwid) in disks.items():
        device = root / "sys" / "block" / name / "device"
        device.mkdir(parents=True)
        for fname, value in (("serial", serial), ("wwid", wwid)):
            if value is not None:
                (device / fname).write_text(value + "\n", encoding="utf-8")
    return root


def _hello_disks(tmp_path: Path, root: Path) -> dict[str, dict]:
    """`build_disk_entry` of every disk in the tree (SYSROOT set: hdparm is never asked), parsed, by dev name."""
    dev = tmp_path / "dev"
    dev.mkdir(exist_ok=True)
    script = ('export PATH="$PATH:/usr/bin:/bin"; '
              f'export SYSROOT={_posix(root)!r} DEVROOT={_posix(dev)!r}; '
              f'. {_posix(LIB / "core" / "common.sh")}; . {_posix(LIB / "hw" / "sysinfo.sh")}; . {_posix(LIB / "hw" / "diskinfo.sh")}; '
              'for _d in $(list_disks); do build_disk_entry "$_d"; echo; done')
    proc = subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=60)
    assert proc.returncode == 0, proc.stderr
    entries = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return {e["dev"]: e for e in entries}


def test_the_hello_carries_serial_then_wwid_then_null(tmp_path):
    """#16 in the body the server reads: a real serial, else the wwid, else `null` ("not identified") -- never "" and
    never a disk dropped from the list."""
    root = _disk_tree(tmp_path, {"sda": ("S4EWNX0R123456", WWID), "sdb": (None, WWID), "sdc": (None, None)})
    disks = _hello_disks(tmp_path, root)
    assert sorted(disks) == ["sda", "sdb", "sdc"], disks
    assert disks["sda"]["serial"] == "S4EWNX0R123456"
    assert disks["sdb"]["serial"] == WWID
    assert disks["sdc"]["serial"] is None, "a disk with neither is `null`, not an empty string"


def test_two_disks_with_the_same_wwid_are_both_reported_with_it(tmp_path):
    """#16: the agent invents nothing and hides nothing -- twins that share a wwid both say it, and telling indistinguishable
    twins apart is the server's rule (ledger.identities, #2448), not an agent that drops one of the two."""
    root = _disk_tree(tmp_path, {"sda": (None, WWID), "sdb": (None, WWID)})
    disks = _hello_disks(tmp_path, root)
    assert [disks[d]["serial"] for d in ("sda", "sdb")] == [WWID, WWID], disks
