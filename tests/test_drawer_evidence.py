"""#520 — מגירה שנפלה אינה פוטרת את השאר מבדיקת ה-sha.

השאלה "את מי להאשים" והשאלה "האם הבייטים תקינים" הן שתי שאלות
שונות, ועד #520 הן ענו במשתנה אחד. `fanout` שיוצא 1 -- כלומר
**מגירה אחת נפלה והשאר המשיכו**, שהוא בדיוק התרחיש שהמערכת בנויה
סביבו -- איפס את ``_why`` **לפני** הענף שאומר "הבייטים היו שגויים",
וכל מגירה ששרדה נחתמה ``done`` על זרם שלא אומת.

הטסטים כאן מריצים את ``restore_partition_drawers`` **האמיתי** דרך
אותו ``drawer_run`` של #73/#440. הם אינם קוראים את שרשרת ה-elif
כטקסט ואינם משכפלים אותה: טסט שמשכפל את המימוש עובר גם כשהמימוש
נסחף, וזו בדיוק המחלה של #523.

**`test_one_dead_drawer_with_a_matching_sha_is_still_a_disk_fault`
חייב לעבור גם לפני התיקון וגם אחריו.** הוא השומר על #440: בלעדיו
התיקון של #520 היה מחליף שקר בשקר, והופך כל ``rc=1`` לכישלון
גלובלי שמאשים את הרשת במקום את הדיסק.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_agent import BASH
from test_stream_honesty import (
    GOOD_FANOUT,
    GOOD_SHA,
    WRONG_SHA,
    call,
    drawer_run,
    errors_of,
)

pytestmark = requires_native(("bash", BASH))


def state_of(run: Path, dev: str) -> str:
    return (run / "targets" / dev / "state").read_text(encoding="utf-8").strip()


#: fanout שיוצא 1: מגירה אחת ``ok`` והשנייה ``failed`` -- הצורה
#: המדויקת של "כשל במגירה אחת לא עוצר את השאר".
#:
#: ⚠️ שני ה-fifo מוזנים **במלואם**. fanout שלא מזין את השורדת משאיר
#: אותה תקועה על ``open()``, היא מקבלת "פג הזמן" -- ענף שנבדק *לפני*
#: ``rc=1`` -- ושתי המגירות נכשלות על הסיבה הלא נכונה. הטסט היה עובר
#: מבלי לגעת בבאג.
MIXED_FANOUT = (
    "#!/bin/sh\n"
    "shift\n"
    'tmp="$(mktemp)"\n'
    'cat > "$tmp"\n'
    'fanout_eof "$tmp"\n'
    'for out; do cat "$tmp" > "$out" & done\n'
    "wait\n"
    "first=1\n"
    "for out; do\n"
    '  if [ "$first" = 1 ]; then echo "$out ok"; first=0\n'
    '  else echo "$out failed write error"\n'
    "  fi\n"
    "done\n"
    "exit 1\n"
)


def test_a_wrong_sha_is_not_waived_just_because_one_drawer_died(tmp_path):
    """הטופולוגיה של #520: בייטים שגויים **וגם** מגירה שנפלה.

    לפני התיקון ענף ``fanout_rc=1`` איפס את ``_why`` לפני ענף
    אי-ההתאמה, השורדת עברה ל-done, והפונקציה חזרה 0.
    """
    run, out = drawer_run(
        tmp_path, call(WRONG_SHA, disks=("sda", "sdb")),
        {"fanout": MIXED_FANOUT},
        disks=("sda", "sdb"),
    )
    assert out.strip().endswith("rc=1"), out
    errors = errors_of(run, disks=("sda", "sdb"))
    for dev in ("sda", "sdb"):
        assert state_of(run, dev) == "failed", (
            f"{dev} נחתמה בלי ראיית sha: state={state_of(run, dev)!r} "
            f"error={errors[dev]!r}")
        assert "אי-התאמת sha256" in errors[dev], errors[dev]


def test_one_dead_drawer_with_a_matching_sha_is_still_a_disk_fault(tmp_path):
    """השומר על #440 -- `fanout_rc` קובע **נוסח**, לא שלמות.

    אותה טופולוגיה בדיוק, sha תואם. השורדת ממשיכה, והמתה נכשלת על
    **הכתיבה לדיסק** ולא על הרשת. הטסט הזה עובר בשני המצבים, וזו
    כל מטרתו.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda", "sdb")),
        {"fanout": MIXED_FANOUT},
        disks=("sda", "sdb"),
    )
    assert out.strip().endswith("rc=0"), out
    errors = errors_of(run, disks=("sda", "sdb"))
    assert state_of(run, "sda") != "failed", errors["sda"]
    assert errors["sda"] == "", errors["sda"]

    assert state_of(run, "sdb") == "failed"
    err = errors["sdb"]
    assert "הכתיבה לדיסק נכשלה" in err, err
    assert "sha256" not in err, err
    assert "הזרם נקטע" not in err, err


#: zstd שבולע את ה-fifo במלואו ואז נופל.
#:
#: ⚠️ **חייב לרוקן.** יציאה מיד בלי לקרוא שולחת EPIPE ל-`fanout`, הוא
#: מדווח ``failed``, והמגירה נכשלת על fanout ולא על הצינור -- הטסט
#: "עובר" בלי לגעת בבאג.
DYING_ZSTD = (
    "#!/bin/sh\n"
    "cat >/dev/null\n"
    "exit 7\n"
)

#: partclone שמקבל EOF מוקדם ויוצא 0 -- הצורה של ``partclone.dd``
#: על קלט חלקי.
OK_PARTCLONE = (
    "#!/bin/sh\n"
    "cat >/dev/null\n"
    "exit 0\n"
)


def test_a_zstd_failure_is_not_done_just_because_partclone_exited_zero(tmp_path):
    """#527 -- אין ``pipefail`` ב-ash, ו-``$?`` של צינור הוא של האחרון.

    הבייטים הדחוסים הגיעו **שלמים**: ה-sha תואם ו-``fanout`` דיווח
    ``ok``. zstd נפל *אחרי* שבלע אותם, ו-partclone קיבל EOF ויצא 0.
    לפני התיקון ``pipeline.rc=0`` והמגירה נחתמה ``done`` על כתיבה קצרה.

    ⚠️ **התיקון אינו ``set -o pipefail``.** הוא אינו קיים ב-busybox ash,
    והטסט הזה היה עובר תחת Git Bash בזמן שהבאג נשאר על המכונה.
    """
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": GOOD_FANOUT, "zstd": DYING_ZSTD, "partclone.dd": OK_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=1"), out
    err = errors_of(run, disks=("sda",))["sda"]
    assert state_of(run, "sda") == "failed", (
        f"כתיבה קצרה נחתמה כהצלחה: state={state_of(run, 'sda')!r} err={err!r}")
    assert err, "מגירה שנכשלה בלי סיבה כתובה"
    # הזרם **כן** הגיע שלם. האשמה על הצינור, לא על הרשת ולא על הבייטים.
    assert "אי-התאמת sha256" not in err, err
    assert "לא חושב" not in err, err


def test_the_drawer_still_succeeds_when_zstd_and_partclone_both_exit_zero(tmp_path):
    """רדיוס הפגיעה: אותם זיופים, zstd תקין. בלי זה התיקון דוחה הכול."""
    run, out = drawer_run(
        tmp_path, call(GOOD_SHA, disks=("sda",)),
        {"fanout": GOOD_FANOUT, "zstd": "#!/bin/sh\nexec cat\n",
         "partclone.dd": OK_PARTCLONE},
        disks=("sda",),
    )
    assert out.strip().endswith("rc=0"), out
    assert state_of(run, "sda") != "failed"
    assert errors_of(run, disks=("sda",))["sda"] == ""
