"""סימולציית קצה-לקצה: שרת אמיתי וארבע מכונות מדומות במקביל.

לא בדיקות יחידה — השרת רץ בתהליך נפרד, וכל מכונה היא תהליכון עם
לולאת hello משלה, שמדבר עם השרת אך ורק בממשקים 2, 3, 4 ו-7:

- מחשב בנייה   — קולט שני אימג'ים ומעלה לספרייה
- כיתה         — סבבים עם מספר משתתפים: שני תנאי ההתחלה, תחנה
                 שנכשלת, פותחת שנופלת, מאחר שנכנס לסבב הבא
- תחנת תלמיד   — עולה, בוחרת אימג', משוחזרת ומקבלת שם
- מחשב שיכפול  — שלוש מגירות, מגירה נכשלת, הגל הבא משלים
- הפצה ישירה   — מחשב הבנייה משדר מהדיסק שלו לשתי מגירות נבחרות (#715)

וסביבם הקצוות: ברירת המחדל של דיסק מקומי, סינון לפי גודל, והרשאות.

הרצה:  python tools/e2e_simulation.py
יציאה: 0 אם הלולאה נסגרה, 1 אם משהו בדרך נשבר.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
# הפלט בעברית; בלי זה ההרצה נופלת במסופים שאינם UTF-8 (ווינדוס, צינורות).
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from tools.e2e import (harness, phase_capture, phase_class, phase_direct,  # noqa: E402
                       phase_edges, phase_journal, phase_room, phase_setup)

#: כמה בדיקות חייבות לרוץ כדי שהריצה תיחשב ריצה.
#:
#: יציאה 0 נגזרה עד כה מכך ש-check() לא זרק — כלומר מהיעדר כישלון.
#: שלב שדולג, לולאה שהתרוקנה או פאזה שהוחזרה מוקדם היו מדפיסים "הלולאה
#: נסגרה" ויוצאים ירוק, כי בדיקה שלא רצה גם לא נכשלת. הרף הופך את זה
#: לכישלון גלוי. כשמוסיפים בדיקות — מעדכנים כלפי מעלה.
MIN_CHECKS = 126    # #855: +1 — MAC בלי חכירה מסורב בשם


def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="imagesrv-e2e-"))
    print(f"\nworkspace: {workdir}\nserver:    {harness.BASE}\n")
    server, data_dir, images_dir = harness.start_server(workdir)
    ctx = None
    try:
        if not harness.wait_for_server():
            print(harness.server_log_tail(workdir))
            return 1

        ctx = phase_setup.run(data_dir, images_dir)
        phase_capture.run(ctx)
        phase_edges.run(ctx)
        phase_class.run(ctx)
        phase_room.run(ctx)
        phase_direct.run(ctx)       # #715: אותו חדר, המקור הוא דיסק מחשב הבנייה
        phase_journal.run(ctx)

        for machine in [ctx.builder, ctx.cloner, *ctx.stations.values()]:
            if machine.error is not None:
                print(f"  ✗ תהליכון {machine.mac} נפל: {machine.error}")
                print(harness.server_log_tail(workdir))
                return 1

        if harness.passed < MIN_CHECKS:
            print(f"\n✗ רצו רק {harness.passed} בדיקות מתוך {MIN_CHECKS} לפחות —"
                  f"\n  שלב לא רץ. ריצה חלקית אינה ריצה ירוקה.")
            return 1

        print(f"\n{'=' * 52}\nהלולאה נסגרה. {harness.passed} בדיקות עברו."
              f"\n{'=' * 52}\n")
        return 0
    except SystemExit as exc:
        print(f"\n{exc}")
        if ctx is not None:
            for machine in [ctx.builder, ctx.cloner, *ctx.stations.values()]:
                if machine.error is not None:
                    print(f"  תהליכון {machine.mac} נפל: {machine.error}")
        print("\n--- סוף יומן השרת ---")
        print(harness.server_log_tail(workdir))
        return 1
    finally:
        if ctx is not None:
            for machine in [ctx.builder, ctx.cloner, *ctx.stations.values()]:
                machine.stop()
        server.terminate()
        try:
            server.wait(timeout=10)
        except Exception:
            server.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
