"""#2084 (mockup 09, Nadav 24/10) -- the refusal on a smaller target never names a device.

``f239cb57`` / #2628 put the hibernated source in the mockup's words ("המקור (דיסק 3) במצב שינה
של Windows…"), and the screen's cut no longer breaks a letter. Two other sentences of the same
measure (``_lc_measure_fit``, lcinv.sh) still reached the TARGET's row with the device in them,
because they are the tools' own words, written for the capture:

* the hibernation check that did not answer -- ``capture_ntfs_hibernation_reason`` rc 2:
  "לא הצלחנו לבדוק מצב שינה ב-/dev/sdc3 (ntfs-3g.probe rc=…)";
* the shrink plan that failed -- ``SHRINK_ERROR`` (shrinkplan.sh): "… את מחיצה 3 בדיסק sdc — …".

The screen gets them without the device (``_lc_nodev``); a sentence that still names it after
that is replaced by "הבדיקה נכשלה — הפרטים ביומן", and the log keeps it whole.

**Negative control** (Testrunner): ``agent/lib/localclone/lcinv.sh`` at the base -> the three
refusal tests fail on "sda" in the reason; the guard (a reason with no device) passes on both.
"""

from __future__ import annotations

import pytest

from native import requires_native
from test_cloner_gui import BASH
from test_lc_faststartup_shrink_2208 import MEASURE
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

#: MEASURE with the real capture_ntfs_hibernation_reason (hibernation.sh): its stub goes, and
#: ntfs-3g.probe is a function that answers $PROBE_RC
REAL_PROBE = MEASURE.replace(
    'capture_ntfs_hibernation_reason() { case "$SRC_RC" in 0) return 0 ;; 14|15) echo "חלונות במחיצה sda1 נכבה ב-Fast Startup — x"; return 1 ;; esac; echo "לא הצלחנו לבדוק"; return 2; }\n',
    'ntfs-3g.probe() { return "$PROBE_RC"; }\n', 1) + 'echo "measure=$(cat "$LC/measure/sda")"\necho "log=$(tr "\\n" " " < "$LOG_FILE")"\n'


def _measure(tmp_path, pre: str) -> dict[str, str]:
    assert "ntfs-3g.probe() {" in REAL_PROBE, "the stub was not replaced"
    out = run(tmp_path, 'export LOG_FILE="$RUN_DIR/agent.log"; : > "$LOG_FILE"; ' + pre + REAL_PROBE)
    assert out.returncode == 0, out.stderr
    return dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)


def _reason(got: dict[str, str]) -> str:
    v = got["verdict"].split("|")
    assert v[0] == "unchecked", got
    assert got["measure"].split("|", 2)[2] == v[2], got   # the row's words are the measure's
    return v[2]


def test_a_hibernation_check_that_did_not_answer_names_no_device(tmp_path):
    got = _measure(tmp_path, "SRC_RC=5; PROBE_RC=5; ")
    why = _reason(got)
    assert "sda" not in why and "/dev" not in why, why
    assert why == "במקור (דיסק 3) — מחיצה 1: לא הצלחנו לבדוק מצב שינה (ntfs-3g.probe rc=5)", why


def test_a_shrink_plan_that_failed_names_no_device(tmp_path):
    plan = ('shrink_plan() { SHRINK_ERROR="לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה 1 בדיסק $1 — פרטי ntfsresize '
            'ביומן הסוכן. הדיסק לא שונה."; return 1; }; ')
    why = _reason(_measure(tmp_path, "SRC_RC=0; PROBE_RC=0; " + plan))
    assert "sda" not in why, why
    assert why == "במקור (דיסק 3) — לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה 1 — פרטי ntfsresize ביומן הסוכן. הדיסק לא שונה", why


def test_a_sentence_that_still_names_the_device_is_the_logs(tmp_path):
    """A tool's sentence in a shape the cut does not know: the screen does not get it at all --
    it says the check failed and where the words are; the log has them whole."""
    plan = 'shrink_plan() { SHRINK_ERROR="sda: partition 1 is busy"; return 1; }; '
    got = _measure(tmp_path, "SRC_RC=0; PROBE_RC=0; " + plan)
    why = _reason(got)
    assert why == "במקור (דיסק 3) — הבדיקה נכשלה — הפרטים ביומן", why
    assert "local clone: sda: partition 1 is busy" in got["log"], got["log"]


@pytest.mark.parametrize("plan_error", ["לא הצלחנו להכין את רשימת המחיצות שלא יכווצו — הדיסק לא שונה."])
def test_a_reason_with_no_device_is_kept_as_it_is(tmp_path, plan_error):
    """The guard: the cut takes only the device -- a sentence without one reaches the row whole."""
    plan = f'shrink_plan() {{ SHRINK_ERROR="{plan_error}"; return 1; }}; '
    why = _reason(_measure(tmp_path, "SRC_RC=0; PROBE_RC=0; " + plan))
    assert why == "במקור (דיסק 3) — " + plan_error.rstrip("."), why
