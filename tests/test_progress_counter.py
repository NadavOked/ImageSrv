"""מונה ההתקדמות נקרא מ-stderr של `pv` — ולכן הוא מכיל גם שגיאות (#100).

`pv -n -b` כותב מספרים ל-stderr, **ושם גם הודעות השגיאה שלו**. כשה-curl
של ההעלאה נופל, ה-fifo נסגר ו-`pv` מקבל EPIPE ומדפיס
``pv: write failed: Broken pipe`` לאותו קובץ בדיוק. `tail -n 1` הרים
את השורה הזאת, והיא נכנסה ל-``$((...))``.

**הבדיקה חייבת לרוץ תחת מעטפת POSIX, ובזה כל הערך שלה.** אותו קלט:

    bash          →  מדפיס שגיאה, ממשיך, יוצא 0   ← הבאג בלתי נראה
    busybox ash   →  malformed ?: operator, יוצא 2
    dash          →  Illegal number, יוצא 2

טסט שנכתב ב-bash היה נצבע ירוק בזמן שהסוכן על המכונה נשבר. זה בדיוק
הכשל של #145, באותו פרויקט, שלושה שבועות קודם.

**ומה שנמדד כאן שונה ממה שה-Issue שיער.** שני אתרי הקריאה
(`progress.sh:72` ו-`:87`) עוטפים ב-``$( )``, ולכן מה שמת הוא
**תת-המעטפת** ולא הלולאה — והערך חוזר **ריק**. משם שתי תוצאות, שתיהן
נמדדו תחת busybox ash:

* ``{"dev":"sda","bytes_written":,"state":"running"}`` — JSON פגום.
  לא שדה אחד שגוי: **כל הדוח** נפסל בשרת.
* קובץ `base` נכתב ריק, ובקריאה הבאה ``$(("" + 8192))`` מחזיר 8192 —
  כלומר הבייטים של כל המחיצות שכבר הסתיימו **נמחקים בשקט**,
  וההתקדמות קופצת אחורה.
"""

from __future__ import annotations

from pathlib import Path

from test_json_escape import bb, source_line          # noqa: F401 — pytestmark משם
from test_json_escape import pytestmark               # noqa: F401

#: מה ש-pv באמת כותב לאותו קובץ כשהצינור נסגר מתחתיו.
PV_ERROR = "pv: write failed: Broken pipe"


def _run(tmp_path: Path, counter_lines: list[str], finished: str = "1000") -> str:
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "bytes.0").write_text(finished + "\n", encoding="utf-8")
    (run / "targets" / "sda" / "bytes.1").write_text(
        "".join(line + "\n" for line in counter_lines), encoding="utf-8")
    body = (
        f'export RUN_DIR="{run.as_posix()}"\n'
        + source_line("common.sh", "progress.sh")
        + 'echo "bytes=$(target_bytes sda)"\n'
        'echo alive\n'                       # ← לא יודפס אם המעטפת מתה
    )
    return bb(tmp_path, body)


def test_a_pv_error_line_does_not_kill_the_shell(tmp_path):
    """הבקרה השלילית של #100.

    לפני התיקון ``$((1000 + pv: write failed: Broken pipe))`` הרג את
    `busybox ash` עם קוד 2, ו-`bb` נכשל על returncode לפני שהגיע
    לבדיקת הערך.
    """
    out = _run(tmp_path, ["4096", "8192", PV_ERROR])
    assert "alive" in out, f"המעטפת מתה על שורת השגיאה:\n{out}"
    assert "bytes=9192" in out, out          # 1000 + 8192, המספר האחרון


def test_the_last_number_wins_and_not_the_last_line(tmp_path):
    """שורת שגיאה אינה מאפסת התקדמות — היא פשוט אינה מדידה."""
    out = _run(tmp_path, ["4096", PV_ERROR, "8192", PV_ERROR])
    assert "bytes=9192" in out, out


def test_a_counter_with_no_numbers_at_all_is_zero(tmp_path):
    """קובץ שכולו שגיאות: 0 התקדמות, ולא קריסה ולא מספר שהומצא."""
    out = _run(tmp_path, [PV_ERROR, "pv: unknown option"])
    assert "alive" in out
    assert "bytes=1000" in out, out          # הבסיס בלבד


def test_an_empty_counter_is_still_zero(tmp_path):
    """המקרה שכן עבד קודם — כדי שהתיקון לא ישבור אותו."""
    out = _run(tmp_path, [])
    assert "bytes=1000" in out, out


def test_carriage_returns_are_still_stripped(tmp_path):
    """`pv` כותב \r בין דגימות. זה היה נכון קודם וחייב להישאר."""
    out = _run(tmp_path, ["4096\r", "8192\r"])
    assert "bytes=9192" in out, out
