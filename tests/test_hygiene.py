"""בדיקות על מכשיר המדידה עצמו.

כל האימות של הפרויקט נשען על משפט אחד — "N עברו, אפס דילוגים" — ועל
הנחה שקטה: שכשהריצה נגמרה, היא נגמרה. שתיהן נמצאו לא נכונות. ‏#52:
שלוש חבילות שלמות יכלו לדלג ו-`pytest` יצא 0. ‏#79: טסט השאיר
`udp-sender` אמיתי רץ שעתיים וחצי על פורט השידור של השרת.

הקובץ הזה בודק את שני התיקונים בראיה חיובית: שהדגל באמת הופך דילוג
לכישלון, ושהסורק באמת מוצא תהליך חי ובאמת מוודא שהוא מת אחרי ההריגה.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import hygiene
import native
from native import requires_native

from conftest import hello_body, setup_classroom


# --- ‏#52: כלי חסר במקום שהוא אמור להיות -------------------------------------


def test_a_missing_tool_is_a_skip_here_and_a_failure_where_it_belongs(monkeypatch):
    """אותו כלי חסר, שתי תשובות — לפי הדגל, לא לפי ניחוש."""
    monkeypatch.delenv(native.ENV_FLAG, raising=False)
    lenient = requires_native("no-such-tool-ever")
    assert lenient.name == "skipif" and lenient.args[0] is True

    monkeypatch.setenv(native.ENV_FLAG, "1")
    strict = requires_native("no-such-tool-ever")
    assert strict.name == native.MISSING_MARK
    assert "no-such-tool-ever" in strict.args[0]


def test_a_tool_that_is_here_never_marks_anything(monkeypatch):
    """בלי דרישה חסרה אין סימון בכלל — לא דילוג ולא כישלון."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    mark = requires_native(("bash", "/usr/bin/bash"))
    assert mark.name == "skipif" and mark.args[0] is False


def test_a_missing_header_counts_as_a_missing_requirement(monkeypatch, tmp_path):
    """‏hivewrite צריך גם `/usr/include/hivex.h`, לא רק תוכניות."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    absent = requires_native(paths=(str(tmp_path / "nope.h"),))
    assert absent.name == native.MISSING_MARK
    present = requires_native(paths=(str(tmp_path),))
    assert present.args[0] is False


def test_the_flag_reads_the_environment_each_time(monkeypatch):
    """הדגל אינו נקבע ביבוא — אחרת CI שמדליק אותו לא היה משנה כלום."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    assert native.native_required()
    monkeypatch.setenv(native.ENV_FLAG, "0")
    assert not native.native_required()


class _Report:
    """דוח מדולג מינימלי, בצורה שבה pytest מוסר אותו."""

    def __init__(self, nodeid: str, reason: str):
        self.nodeid, self.skipped = nodeid, True
        self.longrepr = ("tests/x.py", 1, f"Skipped: {reason}")


def test_a_skipped_run_is_not_a_green_run_when_native_is_required(monkeypatch):
    """רשת הביטחון: כל דילוג, מאיזו סיבה שלא תהיה — כולל `importorskip`."""
    audit = native.SkipAudit()
    audit.record(_Report("tests/test_fanout.py::test_a", "gcc חסר"))
    audit.record(_Report("tests/test_fanout.py::test_a", "gcc חסר"))   # לא נכפל

    monkeypatch.delenv(native.ENV_FLAG, raising=False)
    assert audit.verdict() == []

    monkeypatch.setenv(native.ENV_FLAG, "1")
    verdict = audit.verdict()
    assert verdict and "1 טסטים דולגו" in verdict[0]
    assert any("test_fanout.py::test_a" in line for line in verdict)
    assert any("gcc חסר" in line for line in verdict)


def test_a_strict_xfail_that_failed_as_predicted_is_not_a_skipped_test(monkeypatch):
    """pytest מדווח xfail שנכשל כ-skipped + wasxfail. הוא רץ ונכשל כצפוי
    (‏strict הופך מעבר לכישלון), ולכן הוא אינו "טסט שלא רץ" -- אחרת כל xfail
    מפיל את ריצת המעבדה כש-IMAGECTL_REQUIRE_NATIVE=1. אבל ``run=False``
    נדלג ב-setup ולא רץ: הוא נשאר ברשימת הדילוגים."""
    monkeypatch.setenv(native.ENV_FLAG, "1")
    ran = _Report("tests/x.py::test_known_bug", "expected failure")
    ran.wasxfail, ran.when = "tracked in #1", "call"
    never_ran = _Report("tests/x.py::test_not_run", "[NOTRUN] expected failure")
    never_ran.wasxfail, never_ran.when = "tracked in #1", "setup"
    plain = _Report("tests/x.py::test_plain_skip", "gcc missing")
    plain.when = "call"

    audit = native.SkipAudit()
    audit.record(ran)
    assert audit.verdict() == [] and audit.skipped == {}

    audit.record(never_ran)
    audit.record(plain)
    assert set(audit.skipped) == {"tests/x.py::test_not_run", "tests/x.py::test_plain_skip"}
    assert audit.verdict()


# --- ‏#295: כלי תחנת-פיתוח — דילוג מוצהר, לא אדום קבוע ------------------------


def test_a_dev_workstation_tool_that_is_here_does_not_skip_anything():
    """הכיוון שבלעדיו זו מחיקה ולא דילוג.

    ‏`skipif(True)` שחל בכל סביבה מוחק את הטסט בלי שאיש יבחין. הראיה
    החיובית היא שכשהכלי **כן** כאן — הסימון אינו מדלג.
    """
    mark = native.requires_dev_workstation(("pwsh/powershell", "/usr/bin/pwsh"))
    assert mark.name == "skipif" and mark.args[0] is False


def test_a_missing_dev_workstation_tool_skips_with_its_name_in_the_reason():
    mark = native.requires_dev_workstation("no-such-shell-ever", why="הסבר")
    assert mark.name == "skipif" and mark.args[0] is True
    reason = mark.kwargs["reason"]
    assert native.DECLARED_PREFIX in reason
    assert "no-such-shell-ever" in reason and "הסבר" in reason


def test_a_requirement_without_a_tool_is_refused():
    """דרישה ריקה היתה מדלגת תמיד — זו הדרך שדילוג הופך למחיקה."""
    with pytest.raises(ValueError):
        native.requires_dev_workstation()


def test_with_sbin_appends_only_the_sbin_dirs_that_exist_and_are_missing():
    here = {"/usr/sbin", "/sbin"}.__contains__
    assert native.with_sbin("/usr/bin:/bin", here) == "/usr/bin:/bin:/usr/sbin:/sbin"
    assert native.with_sbin("/sbin:/usr/bin", here) == "/sbin:/usr/bin:/usr/sbin"   # not twice
    assert native.with_sbin("/usr/bin", lambda d: False) == "/usr/bin"


def test_a_tool_in_sbin_is_found_and_not_skipped():
    """#1594: on the Testrunner the user `test` had no /usr/sbin in PATH, so
    `which("sgdisk")` was None and every real-sgdisk test skipped -- a rc5
    regression hid there. The run itself (conftest) must put sbin on PATH."""
    path = os.environ.get("PATH", "").split(os.pathsep)
    for d in native.SBIN:
        if os.name != "nt" and os.path.isdir(d):
            assert d in path, f"{d} exists and is not on the run's PATH: {path}"
            for tool in ("sgdisk", "blockdev"):
                if os.path.exists(os.path.join(d, tool)):
                    assert shutil.which(tool), f"{d}/{tool} exists and which() does not find it"


def test_a_declared_skip_is_counted_and_named_but_never_fails(monkeypatch):
    """שתי האמירות של #295, ושתיהן נדרשות.

    מוצהר → אינו מפיל גם כשהדגל דלוק (‏PowerShell נעדר מהמעבדה במכוון);
    ולא-מוצהר → עדיין מפיל, כדי ש-#52 לא ייפתח מחדש דרך הפתח הזה.
    """
    monkeypatch.setenv(native.ENV_FLAG, "1")
    audit = native.SkipAudit()
    audit.record(_Report("tests/test_free_fleet.py::test_ps",
                         f"{native.DECLARED_PREFIX}: חסר pwsh/powershell"))
    assert audit.verdict() == [], "דילוג מוצהר הפיל ריצה — זה האדום שהוסר"

    notes = audit.notes()
    assert notes and "1 טסטים דולגו במוצהר" in notes[0]
    # התג האנגלי הוא מה ש-`tools/verify.py` קורא — הוא שורד גם
    # קונסולה שהחליפה את העברית ב-`\uXXXX`.
    assert f"[{native.DECLARED_TAG}=1]" in notes[0]
    assert any("test_free_fleet.py::test_ps" in line for line in notes)
    assert any("pwsh/powershell" in line for line in notes)

    audit.record(_Report("tests/test_fanout.py::test_a", "gcc חסר"))
    assert len(audit.notes()) == 2, "דילוג סתמי נספר כמוצהר"
    assert audit.verdict(), "דילוג שאינו מוצהר חדל להפיל — זה #52 חוזר"


def test_declared_skips_are_reported_even_without_the_flag(monkeypatch):
    """אדום קבוע מנרמל את עצמו — וגם ירוק שקט. סופרים בכל ריצה."""
    monkeypatch.delenv(native.ENV_FLAG, raising=False)
    audit = native.SkipAudit()
    audit.record(_Report("tests/test_x.py::t",
                         f"{native.DECLARED_PREFIX}: חסר pwsh/powershell"))
    assert audit.notes(), "הדילוג המוצהר לא דווח בלי הדגל — זה דילוג שקט"


def test_verify_reads_exactly_the_tag_that_the_run_prints():
    """שני הקבצים חייבים להסכים על התג — אחרת המעבדה אדומה שוב, בשקט.

    ‏`tools/verify.py` סופר דילוגים מתוך הפלט של pytest. אם `native.py`
    ישנה את השורה ו-verify לא, הדילוג המוצהר ייספר כדילוג רגיל והאימות
    ייפול — בדיוק האדום ש-#295 הסיר. הקישור הזה אינו נראה בשום diff,
    ולכן הבדיקה מריצה את הרג'קס של verify על השורה שהריצה באמת מדפיסה.
    """
    import importlib.util
    from pathlib import Path

    path = Path(__file__).resolve().parent.parent / "tools" / "verify.py"
    spec = importlib.util.spec_from_file_location("verify_under_test", path)
    verify = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verify)

    audit = native.SkipAudit()
    for i in range(3):
        audit.record(_Report(f"tests/t.py::t{i}",
                             f"{native.DECLARED_PREFIX}: חסר pwsh/powershell"))
    headline = audit.notes()[0]
    found = verify.DECLARED.search(headline)
    assert found, f"‏verify אינו מזהה את השורה שהריצה מדפיסה: {headline!r}"
    assert int(found.group(1)) == 3


# --- ‏#79: מה שהריצה משאירה אחריה --------------------------------------------


def test_the_server_fixture_never_hands_out_the_real_sender(server):
    """הראיה החיובית: מנוע השידור מחזיק שולח מוזרק, לא את זה של המערכת.

    לפני התיקון `server` בנה אפליקציה בלי `sender_runner`, ולכן כל סבב
    שהבשיל תחתיו הפעיל `udp-sender` אמיתי — וזה הטסט שנכשל על הקוד ההוא.
    """
    from server.sender import run_process

    assert server["ctx"].sender.runner is server["recorder"]
    assert server["ctx"].sender.runner is not run_process


def test_a_ripened_round_records_the_command_instead_of_broadcasting(server):
    """המסלול המדויק מ-#79: סבב של 1/1 מבשיל תוך כדי hello.

    ‏`test_the_session_starts_once_when_two_threads_ripen_it` עשה בדיוק
    את זה, והשאיר מאחוריו שולח חי על ה-portbase של ההפצה.
    """
    ids = setup_classroom(server)
    assert server["deploy"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": "LAB1", "expected_clients": 1},
    ).status_code == 200

    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))
    server["anon"].post("/api/v1/agent/hello", json=hello_body(ids["mac1"]))

    recorder = server["recorder"]
    assert recorder.spawned.wait(timeout=5), "השידור לא יצא לדרך בכלל"
    assert recorder.commands[0][0] == "udp-sender"


def test_windows_allowed_failures_stay_empty():
    """#312: כשל בווינדוס הוא skipif מוצהר או תיקון. הרשימה הריקה היא השער.

    הוספה ל-`WINDOWS_ALLOWED_FAILURES` הייתה ממירה רעש לרשימה מתוחזקת
    ביד — בדיוק מה שה-Issue אוסר ("29 שנקרא 28"). ‏28 המקוריים, הכרעה:

    רצים (תיקון בטסט, לא בקוד הייצור):
    * ‏9 ב-`test_sender` — `free_ports` מחליף את `port_holders`
    * ‏5 ב-`test_agent` (wizard) — `input` בבייטים, לא `\\r\\n` (#479)
    * ‏5 ב-`test_restore_evidence` — `posix()` ב-`build_box`
    * ‏1 ב-`test_hygiene` + 1 ב-`test_server_streams` — אותה הזרקה ב-fixture
    * ‏1 ב-`test_server_netcfg` — `Path.as_posix()` מול מפריד ווינדוס

    דילוג מוצהר (התלות אינה קיימת כאן; במעבדה הם רצים):
    * ‏`test_a_busy_udpcast_port_refuses_the_broadcast` — אין `/proc/net/udp`
    * שלושה טסטי wol שכופים ממשק — אין `SO_BINDTODEVICE` (כמו האח שכבר מדלג)
    * ‏`test_a_symlink_named_like_a_work_area_is_not_followed` — WinError 1314
    """
    from conftest import WINDOWS_ALLOWED_FAILURES

    assert WINDOWS_ALLOWED_FAILURES == frozenset(), (
        "#312: נוספו כשלים מותרים בווינדוס. skipif עם נימוק או תיקון, "
        f"לא allowlist: {sorted(WINDOWS_ALLOWED_FAILURES)}"
    )


def test_windows_env_skip_tags_have_not_grown():
    """#312: תקרת הדילוגים המוצהרים. גידול בלי עדכון כאן הוא הכשל.

    הסורק מחפש את התג `issue-312-windows-env` ליד הסימון. skipif גורף
    על הקובץ אינו נושא תג, ולכן גם לא נספר כאן — והוא אסור ממילא.
    """
    root = Path(__file__).resolve().parent
    found: list[str] = []
    for path in sorted(root.glob("test_*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.startswith("def test_"):
                window = "\n".join(lines[max(0, i - 8):i])
                if "issue-312-windows-env" in window:
                    found.append(f"{path.name}::{line[4:].split('(')[0]}")
    expected = [
        "test_sender_ports.py::test_a_busy_udpcast_port_refuses_the_broadcast",
        "test_wol.py::test_no_sysfs_at_all_is_unknown_and_still_sends",
        "test_wol.py::test_an_unreadable_link_state_is_unknown_and_still_sends",
        "test_wol.py::test_the_journal_carries_the_reason_and_not_only_the_count",
    ]
    assert found == expected, (
        f"דילוגי #312 השתנו ({len(expected)} → {len(found)}). "
        "גידול דורש הכרעה לכל אחד, לא skipif גורף.\n"
        f"נוספו: {sorted(set(found) - set(expected))}\n"
        f"נעלמו: {sorted(set(expected) - set(found))}"
    )


def test_the_guard_blocks_a_real_spawn_and_records_it(tmp_path, monkeypatch):
    """גם כשטסט שוכח להזריק שולח — תהליך אמיתי לא יוצא מכאן.

    הרישום הוא מה שהופך את זה לראיה: החסימה לבדה הייתה "אין תהליך",
    ואי אפשר להבדיל בינה לבין "אין באג".
    """
    from server import sender

    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "sender.log")
    with pytest.raises(OSError):
        sender.run_process(["udp-sender", "--portbase", "21000",
                            "--file", str(tmp_path)])
    # ‏`_no_real_sender` מפיל טסט שהשאיר רישום — הרישום הזה מכוון.
    # ‏#1974: השולח רץ תחת `nice … ionice …` כשהכלים קיימים (‏priority.background_cmd) —
    # מה שנחסם הוא אותה שורת פקודה, עם הקידומת או בלעדיה.
    from server import priority
    spawned = hygiene.blocked_spawns.pop()
    prefix = list(priority.BACKGROUND_PREFIX)
    if spawned[:len(prefix)] == prefix:
        spawned = spawned[len(prefix):]
    assert spawned[0] == "udp-sender", spawned


@requires_native("cat", paths=("/proc",), why="הסריקה קוראת ב-/proc")
def test_the_scan_finds_a_live_sender_and_proves_it_died(tmp_path):
    """סורקים, הורגים, וקוראים שוב — "שלחנו SIGTERM" אינו "התהליך מת".

    התהליך כאן הוא עותק של `cat` בשם `udp-sender`, חסום על fifo שאיש
    אינו כותב אליו: אותה צורה שהסורק מחפש (שם התוכנית **וגם** הנתיב),
    בלי לשדר בית אחד לרשת ובלי להשאיר אחריו תת-תהליך משלו. סקריפט עם
    ‏shebang לא היה עובד — הקרנל מחליף את argv[0] בשם המפרש.
    """
    fake = tmp_path / "udp-sender"
    shutil.copy(shutil.which("cat"), fake)
    pipe = tmp_path / "stream"
    os.mkfifo(pipe)
    marker = str(tmp_path)

    child = subprocess.Popen([str(fake), str(pipe)], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not hygiene.live_stream_processes(marker):
            time.sleep(0.05)
        found = hygiene.live_stream_processes(marker)
        assert [pid for pid, _ in found] == [child.pid], f"הסורק לא מצא: {found}"

        assert hygiene.kill_and_confirm(marker) == []
        assert hygiene.live_stream_processes(marker) == []
    finally:
        if child.poll() is None:                      # pragma: no cover
            child.kill()
        child.wait(timeout=10)


@requires_native("cat", paths=("/proc",), why="הסריקה קוראת ב-/proc")
def test_the_scan_ignores_processes_that_are_not_ours(tmp_path):
    """סינון לפי נתיב הריצה **וגם** לפי שם התוכנית.

    בלי שם התוכנית, המעטפת שבה נכתב `--basetemp=...` — ו-pytest עצמו —
    היו נספרים כתהליכים יתומים ונהרגים. ובלי הנתיב, ריצה מקבילה של
    מישהו אחר על אותו שרת הייתה נהרגת יחד איתנו.
    """
    assert hygiene.live_stream_processes(str(tmp_path / "someone-else")) == []

    pipe = tmp_path / "stream"
    os.mkfifo(pipe)
    child = subprocess.Popen([shutil.which("cat"), str(pipe)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    try:
        assert hygiene.live_stream_processes(str(tmp_path)) == []
    finally:
        child.kill()
        child.wait(timeout=10)


def test_the_session_verdict_says_not_checked_instead_of_clean(monkeypatch):
    """בלי `/proc` לא בדקנו — וזה לא אותו דבר כמו "נקי" (עיקרון 5)."""
    monkeypatch.setattr(hygiene, "scan_supported", lambda: False)
    assert hygiene.session_verdict("/tmp/whatever", native_required=False) == []
    strict = hygiene.session_verdict("/tmp/whatever", native_required=True)
    assert strict and "לא נבדקה" in strict[0]


def test_the_block_is_installed_for_the_whole_run():
    """החסימה מותקנת פעם אחת ואינה מוחזרת — אחרת תהליכון השידור שממשיך
    לרוץ אחרי סוף הטסט מוצא את `subprocess` האמיתי בדיוק בחלון הזה."""
    from server import sender

    assert sender.subprocess is not subprocess
    assert getattr(sender.subprocess, "STDOUT", None) == subprocess.STDOUT


# --- #1940: תקרת run_group כשתהליך יוצא מהקבוצה ---------------------------

#: התקרה של הקורא בבדיקות האלה, והשינה של הילד. 8ש' מכסות timeout=2
#: ועוד ניקוז קצר; שינה של 20ש' היא מה שנמדד בלי התקרה (חזר אחרי 20ש').
_GROUP_TIMEOUT_S = 2
_GROUP_SLEEP_S = 20
_GROUP_BOUND_S = 8


def _kill_pidfile(pidfile: Path) -> None:
    """SIGKILL ל-PID שהילד עצמו כתב. אין קובץ או שהתהליך כבר מת = לא שגיאה."""
    if not pidfile.is_file():
        return
    try:
        pid = int(pidfile.read_text(encoding="ascii").strip())
    except ValueError:
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        return


def test_run_group_returns_a_child_that_finished():
    """ילד שנגמר מוחזר כמו subprocess.run — לא חריגת פקיעה."""
    out = hygiene.run_group(
        [sys.executable, "-c", "print('ok')"],
        capture_output=True, text=True, timeout=10,
        stdin=subprocess.DEVNULL,
    )
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_a_child_in_the_group_raises_before_its_sleep_ends():
    """ילד שנשאר בקבוצה: הריגה, ואז TimeoutExpired בתוך התקרה.
    עובר גם בלי תקרת הניקוז — השומר שלא שברנו את המסלול שכבר עבד."""
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        hygiene.run_group(
            [sys.executable, "-c", f"import time; time.sleep({_GROUP_SLEEP_S})"],
            capture_output=True, timeout=_GROUP_TIMEOUT_S,
            stdin=subprocess.DEVNULL,
        )
    elapsed = time.monotonic() - started
    assert elapsed < _GROUP_BOUND_S, f"TimeoutExpired אחרי {elapsed:.1f}s"


@requires_native(posix=True, why="fork ו-setsid; בווינדוס run_group הוא subprocess.run")
def test_an_escaped_child_raises_before_its_sleep_ends(tmp_path):
    """#1940: נכד שקרא setsid ומחזיק את הצינור אחרי שהקבוצה נהרגה.
    בלי תקרת הניקוז TimeoutExpired מגיע כשהנכד נגמר (sleep 20 → כ-20ש'),
    לא בתוך התקרה של הקורא. הכישלון נשאר חריגה — פלט חלקי אינו הצלחה."""
    pidfile = tmp_path / "escaped.pid"
    code = "\n".join([
        "import os, sys, time",
        "pid = os.fork()",
        "if pid == 0:",
        "    os.setsid()",
        "    with open(sys.argv[1], 'w', encoding='ascii') as handle:",
        "        handle.write(str(os.getpid()))",
        f"    time.sleep({_GROUP_SLEEP_S})",
        "    os._exit(0)",
        "os._exit(0)",
    ])
    started = time.monotonic()
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            hygiene.run_group(
                [sys.executable, "-c", code, str(pidfile)],
                capture_output=True, timeout=_GROUP_TIMEOUT_S,
                stdin=subprocess.DEVNULL,
            )
        elapsed = time.monotonic() - started
        assert elapsed < _GROUP_BOUND_S, (
            f"TimeoutExpired אחרי {elapsed:.1f}s — התהליך שברח "
            f"(sleep {_GROUP_SLEEP_S}) החזיק את הצינור, והתקרה לא נאכפה"
        )
        assert pidfile.is_file(), "הנכד לא כתב pid — אין ראיה שברח מהקבוצה"
    finally:
        _kill_pidfile(pidfile)
