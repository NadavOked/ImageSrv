"""‏#2786 (נדב 09/11) — שם המחשב של כל מגירה בסבב חדר השיכפול (הסוכן).

השרת שולח לכל משכפל את השמות של החריצים שלו (‏`session.hostnames`,
‏`{"<port>": name}`) ואת הצד של אימג' עם שתי מערכות (‏`session.hostname_os`).
‏`do_restore_drawers` כותב לכל מגירה שנכתבה עד הסוף את השם **שלה**, אחרי
השחזור ולפני הדרייברים, דרך אותו `write_hostname` של מסלול הכיתות (#62, #89,
‏#107) — לא עותק שני. שם שלא נכתב משאיר את המגירה `done` עם הסיבה (‏#856).

שני חלקים:
- המסלול האמיתי של `do_restore_drawers` (הרתמה של test_final_report והשרת
  המזויף שלה), עם `write_hostname` מרגל שרושם מה ביקשו ממנו.
- ‏`write_hostname` האמיתי על דיסק dual-boot (הרתמה של test_hostname_linux_dual)
  עם `HOSTNAME_OS` — רק הצד שנבחר נכתב.
"""

from __future__ import annotations

from agentlib import flat_lib, lib_path
from test_agent import posix
from test_final_report import ANSWER, IMAGE, SESSION, native_tools, reports, run_path, url_of  # noqa: F401
from test_hostname_linux_dual import LINUX_PLAN, WIN_PLAN, box, name, written

NAMES = {"1": "100-INS", "2": "100-01"}


def _lib(n: str) -> str:
    """‏`drawernames.sh` נטען רק אם הוא קיים — בבקרה השלילית (הקוד שלפני ה-Issue)
    המסלול האמיתי רץ ונופל על ההתנהגות (אף שם לא נכתב), לא על קובץ חסר."""
    try:
        lib_path(n)
    except FileNotFoundError:
        return ""
    return f". {posix(flat_lib())}/{n}; "


SPIES = (
    _lib("drawernames.sh")
    + 'assert_target() { return 0; }; '   # #2434 -- its own tests (test_target_identity_2434.py)
    + 'write_hostname() { echo "NAME $1 $3 os=${HOSTNAME_OS:-}" >> "$RUN_DIR/trace"; '
    '  case "$3" in *FAIL*) printf \'{"ok":false,"error":"כוורת SYSTEM לא נמצאה","code":"no_hive"}\\n\'; return 1;; esac; '
    '  printf \'{"ok":true,"hostname":"%s","method":"offline-registry"}\\n\' "$3"; }; '
    'stage_drawer_drivers() { echo "DRIVERS" >> "$RUN_DIR/trace"; }; '
)


def _answer(hostnames, hostname_os=None) -> dict:
    session = {**ANSWER["session"], "prefix": "ROOM", "hostnames": hostnames,
               "hostname_os": hostname_os}
    return {**ANSWER, "role": "cloner", "session": session}


def _run(tmp_path, reports, answer, stubs: str = ""):
    return run_path(tmp_path, url_of(reports), f"do_restore_drawers {IMAGE} {SESSION}",
                    answer=answer, stubs=SPIES + stubs)


def _names(result) -> list[str]:
    return [line for line in result["trace"] if line.startswith(("NAME", "DRIVERS"))]


def _final_targets(reports) -> dict:
    final = [r for r in reports.received if r.get("state") in ("done", "partial", "failed")][-1]
    return {t["dev"]: t for t in final["targets"]}


@native_tools
def test_each_drawer_gets_its_own_name_before_the_drivers(tmp_path, reports):
    result = _run(tmp_path, reports, _answer(NAMES))
    assert _names(result) == ["NAME sdb 100-INS os=", "NAME sdc 100-01 os=", "DRIVERS"], result["out"]
    targets = _final_targets(reports)
    assert targets["sdb"]["state"] == targets["sdc"]["state"] == "done"
    assert not targets["sdb"].get("error"), targets


@native_tools
def test_the_side_the_round_chose_reaches_write_hostname(tmp_path, reports):
    result = _run(tmp_path, reports, _answer(NAMES, ["linux"]))
    assert _names(result)[:2] == ["NAME sdb 100-INS os=linux", "NAME sdc 100-01 os=linux"]


@native_tools
def test_a_round_without_names_mounts_nothing(tmp_path, reports):
    result = _run(tmp_path, reports, _answer(None))
    assert _names(result) == ["DRIVERS"], result["trace"]


@native_tools
def test_a_name_that_was_not_written_is_an_orange_done_not_silence(tmp_path, reports):
    result = _run(tmp_path, reports, _answer({"1": "100-INS", "2": "FAIL-01"}))
    assert "NAME sdc FAIL-01 os=" in _names(result)
    targets = _final_targets(reports)
    assert targets["sdc"]["state"] == "done", "השחזור עצמו הושלם — השם אינו מפיל אותו"
    assert targets["sdc"]["error"] == "שם המחשב לא נכתב: כוורת SYSTEM לא נמצאה"
    assert not targets["sdb"].get("error"), "המגירה השנייה אינה נגררת"


@native_tools
def test_a_drawer_the_server_sent_no_name_for_says_so(tmp_path, reports):
    _run(tmp_path, reports, _answer({"1": "100-INS"}))
    assert _final_targets(reports)["sdc"]["error"] == "שם המחשב לא נכתב: השרת לא שלח שם לחריץ 2"


@native_tools
def test_a_drawer_that_failed_keeps_its_number_and_is_not_named(tmp_path, reports):
    """דיסק שנכשל אינו מקבל שם, והשם שלו אינו עובר לדיסק אחר (הכרעת נדב 1)."""
    one_failed = ('run_restore_drawers() { target_set sdb failed "I/O"; target_set sdc done; '
                  'echo partial > "$RUN_DIR/state"; return 0; }; ')
    result = _run(tmp_path, reports, _answer(NAMES), stubs=one_failed)
    assert [n for n in _names(result) if n.startswith("NAME")] == ["NAME sdc 100-01 os="]


# --- write_hostname: רק הצד שנבחר באימג' עם שתי מערכות ---------------------


def test_windows_only_on_a_dual_disk_leaves_linux_untouched(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True)
    rc, result = name("export HOSTNAME_OS=windows; " + script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"})
    assert "ComputerName LAB1-05" in written(run / "hivewrites")
    assert not (run / "linux/etc/hostname").exists(), "צד הלינוקס לא נבחר — לא נכתב"


def test_linux_only_on_a_dual_disk_leaves_windows_untouched(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True)
    rc, result = name("export HOSTNAME_OS=linux; " + script)
    assert (rc, result) == (0, {"ok": True, "hostname": "LAB1-05", "method": "etc-hostname"})
    assert written(run / "linux/etc/hostname") == "LAB1-05"
    assert written(run / "hivewrites") == "", "צד ה-Windows לא נבחר — הכוורת לא נגעה"


def test_no_choice_on_a_dual_disk_is_still_both_sides(tmp_path):
    script, run = box(tmp_path, [WIN_PLAN, LINUX_PLAN], default_etc=True)
    rc, result = name(script)
    assert (rc, result["method"]) == (0, "offline-registry+etc-hostname")
