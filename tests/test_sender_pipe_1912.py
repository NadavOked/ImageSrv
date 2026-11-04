"""#1912: שומר רגרסיה ל-#22 — ‏`run_process` האמיתי אינו נחסם על הפלט של עצמו.

‏#22: ‏udp-sender הופעל עם `stdout=PIPE` שאיש לא קרא. הוא מדפיס התקדמות בלי
הפסקה, וכשחוצץ הצינור (~64KB) התמלא — באמצע המחיצה הגדולה — התהליך נחסם
על הכתיבה, השידור קפא, והמקבלים מתו בזה אחר זה. התיקון: הפלט לקובץ.

כל טסטי השולח מזריקים runner מזויף, ובתוך הטסטים `sender.subprocess` חסום
בכוונה (‏`hygiene.block_real_processes`, ‏#79) — ולכן `run_process` עצמו מעולם
לא רץ בבדיקות. כאן רץ **הקוד שלו** (`__code__`) עם `subprocess` אמיתי
בגלובלים פרטיים, בלי להסיר את החסימה מהמודול (תהליכון שידור של טסט אחר
היה מגיע דרכה לשולח אמיתי). במקום udp-sender רץ python שמדפיס 1MB.

בקרה שלילית: ‏`stdout=handle` → ‏`stdout=subprocess.PIPE` ב-`run_process`
(הקוד שלפני #22) — התהליך הבן נתקע ו-`wait` פג.
"""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.rounds import sender as sender_module

#: פי 16 מחוצץ צינור טיפוסי (64KB) — מעבר לכל חוצץ של לינוקס או ווינדוס.
OUTPUT_BYTES = 1 << 20
CHILD = ("import sys\n"
         f"for _ in range({OUTPUT_BYTES // 1024}):\n"
         "    sys.stdout.write('p' * 1023 + '\\n')\n"
         "sys.stdout.flush()\n")


def _real_run_process(log: Path):
    """‏`run_process` של הייצור, עם subprocess אמיתי ו-SENDER_LOG בתוך tmp."""
    def popen(*args, stdin=subprocess.DEVNULL, **kwargs):
        # ‏stdin לא נורש: בווינדוס ה-handle של pytest נשבר בריצה רב-קבצית (#14).
        return subprocess.Popen(*args, stdin=stdin, **kwargs)

    real = types.SimpleNamespace(Popen=popen, PIPE=subprocess.PIPE, STDOUT=subprocess.STDOUT)
    # ‏nice/ionice אינם הנושא כאן — משתנה אחד בבדיקה (עיקרון 5ג).
    priority = types.SimpleNamespace(background_cmd=lambda cmd: list(cmd))
    scope = {**sender_module.__dict__, "subprocess": real, "priority": priority,
             "SENDER_LOG": log}
    for name in ("partition_log", "run_process"):   # שתיהן קוראות את SENDER_LOG/subprocess מ-scope
        original = getattr(sender_module, name)
        scope[name] = types.FunctionType(original.__code__, scope, name,
                                         original.__defaults__, original.__closure__)
    return scope["run_process"], scope["partition_log"]


def test_run_process_does_not_stall_on_a_chatty_child(tmp_path):
    run_process, partition_log = _real_run_process(tmp_path / "imagesrv-sender.log")
    part = tmp_path / "img_test" / "p2.img"
    proc = run_process([sys.executable, "-c", CHILD, "--file", str(part)])
    try:
        rc = proc.wait(timeout=60)
    except subprocess.TimeoutExpired:
        pytest.fail(f"התהליך נתקע על הפלט של עצמו אחרי 60 שניות — #22 חזר "
                    f"(stdout={'PIPE' if proc.stdout else 'file'})")
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=10)
    assert rc == 0
    assert proc.stdout is None, "stdout הוא צינור — אף אחד לא קורא אותו"
    written = partition_log(part)
    assert written.parent == tmp_path
    assert written.stat().st_size >= OUTPUT_BYTES, "הפלט לא הגיע ללוג הפר-מחיצתי"
