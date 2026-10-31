"""סוף משימה לא מאתחל ולא מכבה את המכונה שמריצה את הטסט — #2077.

`run_path` הוא המסלול של סוף משימה (גם #1916). ברירת המחדל שלו
`D_ROLE=classroom`. אחרי 'כבה' (`end_countdown` → `finish_and_stop`)
תחנת כיתה מגיעה ל-`reboot -f`, ובלי שומר `IMAGESRV_TEST`. הפקודה
האמיתית נכשלת רק כשהמריץ אינו root; תחת sudo (#2066) המכונה עולה מחדש.

הזיוף ב-PATH נכשל אם נקרא (127, ו-126 כשאין לאן לרשום). הראיה היא
השורה שהוא כותב, וקוד היציאה ש-`safe_poweroff` קורא בחזרה.
"""

from __future__ import annotations

from native import requires_native
from test_agent import BASH
from test_final_report import run_path

pytestmark = requires_native(("bash", BASH))

#: לפני כל פקודת כיבוי: מה ש-`type -P` מצא חייב להיות הזיוף. אחרת זו
#: הפקודה האמיתית, והטסט יוצא לפני שהיא רצה.
GUARD = r"""
_fail=0
for _c in reboot poweroff safe_poweroff; do
    _p=$(type -P "$_c" 2>/dev/null || true)
    case "$_p" in
        "$IMAGESRV_POWER_STUB_DIR"/$_c) ;;
        *) printf 'REAL-ON-PATH %s %s\n' "$_c" "${_p:-missing}"; _fail=1 ;;
    esac
done
[ "$_fail" = 0 ] || exit 97
"""

NO_AFTER = "AFTER_TASK_FILE=/nonexistent/after-task"


def _calls(tmp_path) -> str:
    log = tmp_path / "power.calls"
    assert log.is_file(), "הזיוף לא נקרא — אין power.calls"
    return log.read_text(encoding="utf-8")


def test_a_classroom_task_end_hits_the_reboot_stub_and_it_fails(tmp_path):
    """**לב #2077.** סוף משימה בכיתה: `reboot -f` דרך הזיוף, והזיוף נכשל.

    בלי הזיוף השומר רואה את הפקודה האמיתית ויוצא (`REAL-ON-PATH`) לפני
    שהיא רצה. קריאה בלי `IMAGESRV_POWER_LOG` נכשלת גם היא (126) — אין
    הצלחה שקטה כשהראיה לא נכתבה.
    """
    result = run_path(
        tmp_path, "http://127.0.0.1:9",
        GUARD + 'finish_and_stop; echo "AFTER rc=$?"; '
        'IMAGESRV_POWER_LOG= "$IMAGESRV_POWER_STUB_DIR/reboot" -f; '
        'echo "UNSET rc=$?"',
        env=NO_AFTER,
    )
    assert "REAL-ON-PATH" not in result["out"], result["out"]
    assert "POWER-STUB-" not in result["out"], result["out"]
    assert "AFTER rc=127" in result["out"], result["out"]
    assert "UNSET rc=126" in result["out"], result["out"]
    assert "IMAGESRV_POWER_LOG is unset" in result["out"], result["out"]
    assert "task complete -- rebooting (after-task=reboot)" in result["log"], result["log"]
    assert _calls(tmp_path).splitlines() == ["STUB reboot -f"]


def test_poweroff_and_safe_poweroff_fail_closed(tmp_path):
    """כיבוי (`safe_poweroff` האמיתי, ואז הפקודה עצמה) פוגע בזיוף ונכשל.

    ‏`IMAGESRV_TEST=0` כדי שהפונקציה לא תחזור ב-`TEST-POWEROFF` לפני
    ‏`poweroff -f`. ‏`arm_wol` מצליח — אחרת הפונקציה חוזרת בלי לכבות,
    והבדיקה לא הייתה רואה את הפקודה.
    """
    result = run_path(
        tmp_path, "http://127.0.0.1:9",
        GUARD + 'finish_and_stop; echo "AFTER rc=$?"; '
        'command safe_poweroff from-test; echo "CMD rc=$?"',
        stubs='arm_wol() { return 0; }; ',
        env=f"IMAGESRV_TEST=0 D_ROLE=build {NO_AFTER}",
    )
    assert "REAL-ON-PATH" not in result["out"], result["out"]
    assert "POWER-STUB-" not in result["out"], result["out"]
    assert "AFTER rc=1" in result["out"], result["out"]
    assert "CMD rc=127" in result["out"], result["out"]
    assert "TEST-POWEROFF" not in result["out"], result["out"]
    assert "poweroff -f returned rc=127" in result["log"], result["log"]
    assert _calls(tmp_path).splitlines() == [
        "STUB poweroff -f",
        "STUB safe_poweroff from-test",
    ]
