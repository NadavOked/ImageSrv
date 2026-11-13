"""`copy_libs`: ספרייה חסרה עוצרת את בניית ה-initramfs, wrapper לא (#507).

תיקון #12 היה אמיתי — `hivexget` הוא wrapper של shell, `ldd` יוצא
nonzero, ו-`pipefail` הרג את כל הבנייה. אבל `{ ldd || true; }` ו-
`cp ... || true` חלו על **כל** כישלון, כולל `libfoo.so => not found`.
הבנייה יצאה 0, ו-`partclone` נפל מול דיסק של מכונה.

הבדיקות מריצות את `copy_libs` מתוך `tools/build_initramfs.sh` עם
`ldd` מזויף, לא בודקות את הטקסט שלה: ניסוח אחר שיבלע ספרייה חסרה
צריך להיכשל כאן. הבקרה השלילית: החזרת הקובץ לבסיס חייבת להפיל את
`test_a_missing_shared_library_stops_the_build`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from native import requires_native

BUILDER = Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh"
BASH = shutil.which("bash")

pytestmark = requires_native("bash", why="copy_libs רץ ב-bash של הבנאי")


def bash_path(path: Path) -> str:
    """נתיב ש-Git bash מבין, בלי נקודתיים באמצע אחרי שרשור ל-ROOT."""
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def copy_libs_snippet() -> str:
    """הפונקציה כפי שהיא בסקריפט, בין העוגן לשורת ה-`}` הסוגרת."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("copy_libs() {"))
    end = next(i for i, l in enumerate(lines[start + 1:], start + 1) if l == "}")
    return "\n".join(lines[start:end + 1])


def run_copy_libs(tmp_path: Path, *, ldd_body: str, extra: str = ""):
    """מריץ את `copy_libs` האמיתית מול `ldd` (ואופציונלי `cp`) מזויפים."""
    root = tmp_path / "root"
    root.mkdir()
    binary = tmp_path / "partclone.ntfs"
    binary.write_bytes(b"\x7fELF")
    script = (
        "set -euo pipefail\n"
        f"ROOT={bash_path(root)!r}\n"
        f"ldd() {{\n{ldd_body}\n}}\n"
        f"{extra}\n"
        f"{copy_libs_snippet()}\n"
        f"copy_libs {bash_path(binary)!r}\n"
    )
    done = subprocess.run(
        [BASH, "-c", script],
        stdin=subprocess.DEVNULL,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )
    return done, root, binary


def test_a_missing_shared_library_stops_the_build(tmp_path: Path):
    """הבאג: `libfoo.so => not found` יצא 0, והספרייה לא נכנסה ל-initramfs."""
    done, _, binary = run_copy_libs(
        tmp_path,
        ldd_body=(
            "printf '%s\\n' "
            "'\\tlinux-vdso.so.1 (0x0000)' "
            "'\\tlibfoo.so => not found' "
            "'\\tlibc.so.6 => /lib/x86_64-linux-gnu/libc.so.6 (0x1234)'\n"
            "return 1"
        ),
    )
    assert done.returncode != 0, (
        "ספרייה חסרה עברה את copy_libs — הבנייה תצא 0 מול מכונה: "
        f"rc={done.returncode} stderr={done.stderr!r}"
    )
    assert "libfoo.so" in done.stderr, done.stderr
    assert bash_path(binary) in done.stderr, done.stderr


def test_a_shell_wrapper_does_not_stop_the_build(tmp_path: Path):
    """תיקון #12 נשאר: ldd על wrapper יוצא nonzero, והבנייה ממשיכה."""
    done, _, _ = run_copy_libs(
        tmp_path,
        ldd_body="echo 'not a dynamic executable' >&2; return 1",
    )
    assert done.returncode == 0, done.stderr


def test_a_resolved_library_is_copied_into_the_root(tmp_path: Path):
    """הצד החיובי: ספרייה ש-ldd מצא גם נכנסת תחת ROOT."""
    lib = tmp_path / "libz.so.1"
    lib.write_bytes(b"fake-lib")
    done, root, _ = run_copy_libs(
        tmp_path,
        ldd_body=(
            f"printf '%s\\n' 'libz.so.1 => {bash_path(lib)} (0x1234)'\n"
            "return 0"
        ),
    )
    assert done.returncode == 0, done.stderr
    packed = root / bash_path(lib).lstrip("/")
    assert packed.is_file(), f"לא נארז: {packed}"
    assert packed.read_bytes() == b"fake-lib"


def test_a_failed_copy_stops_the_build(tmp_path: Path):
    """`cp` שנכשל אינו `|| true` — אחרת initramfs בלי הספרייה יוצא 0."""
    lib = tmp_path / "libz.so.1"
    lib.write_bytes(b"fake-lib")
    done, _, _ = run_copy_libs(
        tmp_path,
        ldd_body=(
            f"printf '%s\\n' 'libz.so.1 => {bash_path(lib)} (0x1234)'\n"
            "return 0"
        ),
        extra='cp() { echo "cp: failed $*" >&2; return 1; }',
    )
    assert done.returncode != 0, (
        "cp שנכשל נבלע — הבנייה תצא 0 בלי הספרייה: "
        f"rc={done.returncode} stderr={done.stderr!r}"
    )
