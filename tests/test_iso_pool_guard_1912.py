"""#1912: שומר רגרסיה התנהגותי ל-#1162 — build-iso.sh מוכיח שה-pool מכסה את packages.txt.

#1162: ה-ISO נבנה גם כשחבילה מ-packages.txt חסרה מה-pool, וההתקנה offline
נכשלת על החבילה הראשונה שחסרה — מול שרת אמיתי בלי אינטרנט. הטסט הקיים
(`test_iso_build.py::test_build_iso_proves_every_requested_package_is_in_the_merged_pool`)
בודק מחרוזות בלבד, והבקרה השלילית שלו נכתבה "ידנית, טרם הורצה".

כאן **מורץ** קטע ה-pool של build-iso.sh עצמו (ממיזוג ה-pool ועד האינדוקס),
מול pool זמני ו-`dpkg-deb` מזויף שקורא את שדה Package מתוך הקובץ.

בקרה שלילית: מחיקת בלוק הבדיקה מ-build-iso.sh (הקוד שלפני #1162) — הטסט
של החבילה החסרה נופל (הבנייה ממשיכה עם קוד 0); שני האחרים עוברים בשני המצבים.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
BUILD_ISO = REPO / "tools" / "iso" / "build-iso.sh"


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = _find_bash()
pytestmark = requires_native(("bash", BASH), why="build-iso.sh הוא bash")


def _pool_block() -> str:
    """מהמיזוג ועד האינדוקס — שני עוגנים שאינם חלק מהבדיקה עצמה, ולכן שורדים את הבקרה."""
    text = BUILD_ISO.read_text(encoding="utf-8")
    start = text.index('cp -a "$POOL/pool/." "$ISO_TREE/pool/"')
    end = text.index('bash "$SCRIPT_DIR/make-pool.sh" --index-only "$ISO_TREE"')
    return text[start:end]


def _deb(root: Path, rel: str, package: str) -> None:
    path = root / "pool" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{package}\n", encoding="utf-8", newline="\n")


def _run(tmp_path: Path, packages: list[str], supplied: dict[str, str],
         netinst: dict[str, str]) -> subprocess.CompletedProcess:
    script_dir, pool, tree, work, bindir = (tmp_path / n for n in ("iso", "pool", "tree", "work", "bin"))
    for d in (script_dir, pool / "pool", tree / "pool", work, bindir):
        d.mkdir(parents=True, exist_ok=True)
    (script_dir / "packages.txt").write_text(
        "# comment\n" + "\n".join(packages) + "\n", encoding="utf-8", newline="\n")
    for rel, package in supplied.items():
        _deb(pool, rel, package)
    for rel, package in netinst.items():
        _deb(tree, rel, package)
    fake = bindir / "dpkg-deb"   # dpkg-deb -f <deb> Package → שדה Package, לא שם הקובץ
    fake.write_text('#!/bin/sh\n[ "$1" = -f ] && [ "$3" = Package ] || exit 2\nhead -n 1 "$2"\n',
                    encoding="utf-8", newline="\n")
    fake.chmod(0o755)
    def p(x: Path) -> str:
        return x.as_posix()
    script = ("set -euo pipefail\nexport PATH=\"$PATH:/usr/bin:/bin\"\n"
              "die() { printf 'build-iso: %s\\n' \"$*\" >&2; exit 1; }\n"
              "say() { printf 'build-iso: %s\\n' \"$*\"; }\n"
              f"SCRIPT_DIR='{p(script_dir)}'; POOL='{p(pool)}'; ISO_TREE='{p(tree)}'; WORK='{p(work)}'\n"
              + _pool_block() + "echo POOL_BLOCK_DONE\n")
    env = {**os.environ, "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}"}
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          env=env, stdin=subprocess.DEVNULL, timeout=120)


def test_a_package_missing_from_the_merged_pool_stops_the_build_by_name(tmp_path):
    proc = _run(tmp_path, ["nftables", "partclone"],
                supplied={"main/n/nftables/nftables_1.1_amd64.deb": "nftables"}, netinst={})
    assert proc.returncode != 0, f"הבנייה המשיכה בלי partclone ב-pool:\n{proc.stdout}"
    assert "POOL_BLOCK_DONE" not in proc.stdout
    assert "partclone" in proc.stderr and "nftables" not in proc.stderr.split(":")[-1]


def test_the_package_field_counts_not_the_file_name(tmp_path):
    """השומר מפני תיקון-יתר: שם הקובץ אינו ראיה — libfoo2_*.deb שבתוכו Package: libfoo מכסה את libfoo."""
    proc = _run(tmp_path, ["libfoo"],
                supplied={"main/libf/libfoo/libfoo2_2.0-1_amd64.deb": "libfoo"}, netinst={})
    assert proc.returncode == 0 and "POOL_BLOCK_DONE" in proc.stdout, proc.stderr


def test_a_package_from_the_netinst_tree_counts_after_the_merge(tmp_path):
    """ה-pool **הממוזג**: חבילה שמגיעה מה-netinst ולא מה-pool שסופק — מכוסה."""
    proc = _run(tmp_path, ["busybox", "grub-pc-bin"],
                supplied={"main/b/busybox/busybox_1.37_amd64.deb": "busybox"},
                netinst={"main/g/grub2/grub-pc-bin_2.12_amd64.deb": "grub-pc-bin"})
    assert proc.returncode == 0 and "POOL_BLOCK_DONE" in proc.stdout, proc.stderr
