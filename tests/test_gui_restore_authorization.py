"""השחזור מהגואי מוחק את הדיסק רק מאחורי סשן קונסולה חי (סקירת אבטחה, 11/10).

עד 11/10 ה-handoff של `restore-start` עבר ב-`recovery_gate` לפני
‏`single_restore_run`: שלושת המצבים של `.ui.require_login` וכניסה בטקסט.
כשמסכי הטקסט נמחקו (GUI בלבד) נמחק איתם גם השער — ו-handoff עם שם אימג'
הספיק כדי למחוק את הדיסק. סקירת האבטחה סימנה זאת MEDIUM. השער חזר בצד
הסוכן, fail-closed (‏`gui_restore_authorized`, ‏guiparent.sh):

* ‏`.ui.require_login` שאינו true/false — סירוב (עיקרון 5).
* הגשר מעביר עם ה-handoff את ה-cookie jar של הכניסה בגואי
  (‏`$RUN_DIR/gui/handoff.jar`), והסוכן בודק אותו מול השרת: ‏200 על נתיב
  קונסולה שדורש סשן — ורק אז. בלי jar, ‏401, בלי תשובה — סירוב.
* ה-jar חד-פעמי: סירוב מוחק אותו; ג'אר שאושר נוסע עם המשיכה (השרת מקבל
  אותו כהרשאה, 11/10) ונמחק מיד אחריה; ‏`console_login.json` (שממנו `gui_role`
  קורא תפקיד) נמחק בכל כשל כניסה, ביציאת הקיוסק וביציאה מהגואי.

בקרה שלילית: על הקומיט שלפני התיקון (bca54871) הטסטים של הסירוב נופלים —
‏`gui_restore_authorized` אינו קיים, וה-handoff מגיע ל-`single_restore_run`.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, REPO, posix

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")


def run(tmp_path: Path, body: str, require_login: str = "true",
        jar: bool = True, code: str = "200") -> tuple[str, int, Path]:
    """מריץ את guiparent.sh עם `curl` מזויף (רק הגבול: קוד ה-HTTP)."""
    rd = tmp_path / "run"
    (rd / "gui").mkdir(parents=True, exist_ok=True)
    resp = rd / "response.json"
    if require_login == "missing":
        resp.write_text('{"ui":{}}', encoding="utf-8")
    else:
        resp.write_text('{"ui":{"require_login":%s}}' % require_login, encoding="utf-8")
    if jar:
        (rd / "gui" / "handoff.jar").write_text(
            "localhost\tFALSE\t/\tFALSE\t0\timagectl_session\tticket\n", encoding="utf-8")
    script = (
        f"export RUN_DIR={shlex.quote(posix(rd))} RESP={shlex.quote(posix(resp))} "
        f"LOG_FILE={shlex.quote(posix(rd / 'agent.log'))} SERVER=http://x MAC=aa:bb "
        "IMAGECTL_TEST=1 HTTP_TIMEOUT=1 HTTP_RETRIES=0 SR_GUI_TICK_S=0.1 "
        "AFTER_TASK_FILE=/nonexistent/after-task D_ROLE=build; "   # #1561: the power-off after a success
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; "
        f". {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/ui.sh; "
        f". {posix(AGENT)}/lib/guiparent.sh; . {posix(AGENT)}/lib/srgui.sh; "   # #1561: sr_refused
        f"SR_GUI_BIN={shlex.quote(posix(rd / 'no-gui'))}; "   # no GUI here: the refusal ends at die_local
        'sleep() { case "$1" in 0.*) command sleep "$1" ;; esac; }; '   # whole seconds are skipped; the GUI's tick is not
        f'curl() {{ echo "CURL $*" >> "$RUN_DIR/curl.log"; printf {code}; }}; '
        + body
    )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60)
    return proc.stdout + proc.stderr, proc.returncode, rd


AUTH = 'gui_restore_authorized; echo "AUTH rc=$?"'


@pytest.mark.parametrize("require_login", ["true", "false"])
def test_a_live_session_is_authorized(tmp_path, require_login):
    out, _, rd = run(tmp_path, AUTH, require_login=require_login)
    assert "AUTH rc=0" in out, out
    assert "imagectl_session" not in out
    # The check went to the server with the GUI's jar; an accepted jar is
    # kept for the pull (11/10: the restore is linked to this session).
    assert "-b" in (rd / "curl.log").read_text() and "/api/console/folders" in (rd / "curl.log").read_text()
    assert (rd / "gui" / "handoff.jar").exists()


def test_a_handoff_without_a_session_is_refused(tmp_path):
    out, _, rd = run(tmp_path, AUTH, jar=False)
    assert "AUTH rc=1" in out, out
    assert "no console session from the GUI" in out
    assert not (rd / "curl.log").exists(), "בלי jar אין מה לבדוק מול השרת"


@pytest.mark.parametrize("code", ["401", "403", "000", "500"])
def test_an_expired_or_unverified_session_is_refused(tmp_path, code):
    out, _, rd = run(tmp_path, AUTH, code=code)
    assert "AUTH rc=1" in out, out
    assert f"http {code}" in out
    assert not (rd / "gui" / "handoff.jar").exists()


@pytest.mark.parametrize("require_login", ["null", "missing", '"maybe"'])
def test_an_unknown_require_login_is_refused(tmp_path, require_login):
    out, _, rd = run(tmp_path, AUTH, require_login=require_login)
    assert "AUTH rc=1" in out, out
    assert "did not say whether a login is needed" in out
    assert not (rd / "gui" / "handoff.jar").exists()
    assert not (rd / "curl.log").exists()


HANDOFF = (
    'printf "restore-start\\nimg_1\\n" > "$RUN_DIR/gui/handoff"; '
    '(exit 0) & _gui_pid=$!; '
    'http_get() { printf \'{"allowed_images":[{"id":"img_1"}]}\'; }; '
    'single_restore_run() { echo "ERASED $1"; return 0; }; '
    'gui_parent; echo "PARENT rc=$?"'
)


def test_the_handoff_without_a_session_erases_nothing(tmp_path):
    """הצורה שהסקירה סימנה: handoff עם שם אימג', בלי סשן — ‏die_local, ושום
    כתיבה. בקרה שלילית: על bca54871 הפלט מכיל ERASED img_1."""
    out, rc, _ = run(tmp_path, HANDOFF, jar=False)
    assert "ERASED" not in out, out
    assert "TEST-REBOOT: restore: not authorized -- nothing was written" in out
    assert rc == 86


def test_the_handoff_with_an_expired_session_erases_nothing(tmp_path):
    out, rc, _ = run(tmp_path, HANDOFF, code="401")
    assert "ERASED" not in out, out
    assert rc == 86


#: #1561: a GUI that lives like the real one -- it records how it was started
#: and every state it read (when the file changed), and leaves on a message.
LIVE_GUI = r'''#!/bin/sh
printf '%s\n' "$*" >> "$RUN_DIR/gui.calls"
st=; for a in "$@"; do [ "$p" = --state ] && st=$a; p=$a; done
n=0
while [ "$n" -lt 600 ]; do
    if [ -f "$st" ] && cat "$st" > "$RUN_DIR/gui.now" && ! cmp -s "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"; then
        { cat "$RUN_DIR/gui.now"; echo "--"; } >> "$RUN_DIR/gui.seen"; cp "$RUN_DIR/gui.now" "$RUN_DIR/gui.last"
        LC_ALL=C grep -q '^message=' "$RUN_DIR/gui.now" && exit 0
    fi
    sleep 0.05; n=$((n + 1))
done
'''


def live_gui(rd: Path) -> None:
    rd.mkdir(exist_ok=True)
    gui = rd / "no-gui"   # the harness's SR_GUI_BIN; here it exists
    gui.write_text(LIVE_GUI, encoding="utf-8", newline="\n")
    gui.chmod(0o755)


def cards(rd: Path) -> list[str]:
    seen = rd / "gui.seen"
    return [c for c in seen.read_text(encoding="utf-8").split("--\n") if c.strip()] if seen.exists() else []


def test_a_refused_handoff_says_why_in_hebrew_on_the_gui(tmp_path):
    """#1561: the operator pressed "שחזר" and the screen went -- an English FATAL
    line and a reboot. Now the GUI is back at the handoff, before the checks,
    and the refusal is its last card; the reboot (and nothing written) is
    unchanged. One GUI process from the handoff on. Negative control: on main
    no GUI starts."""
    rd = tmp_path / "run"
    live_gui(rd)
    out, rc, _ = run(tmp_path, f'. {posix(AGENT)}/lib/guibridge.sh; ' + HANDOFF, code="401")
    assert "ERASED" not in out and rc == 86, out
    assert "TEST-REBOOT: restore: not authorized -- nothing was written" in out
    calls = (rd / "gui.calls").read_text(encoding="utf-8").splitlines() if (rd / "gui.calls").exists() else []
    assert len(calls) == 1 and "--screen progress" in calls[0], (calls, out)
    seen = cards(rd)
    assert "בודק את האימג׳ ואת ההרשאה מול השרת — הדיסק עוד לא שונה" in seen[0], seen
    assert seen[-1] == ("message=השחזור לא התחיל — לא נכתב דבר|\u200f"
                        "הכניסה לקונסולה פגה או לא אומתה — היכנסו שוב ונסו שוב · המחשב יופעל מחדש\n"), seen
    # the GUI that owns the display, for log() (#1569 reads gui.pid)
    assert (rd / "gui.pid").read_text().strip().isdigit()


def test_an_image_no_longer_allowed_is_refused_on_the_same_gui(tmp_path):
    """guiparent.sh's third refusal (the fresh allowed_images), the same way."""
    rd = tmp_path / "run"
    live_gui(rd)
    body = HANDOFF.replace('{"allowed_images":[{"id":"img_1"}]}', '{"allowed_images":[]}')
    out, rc, _ = run(tmp_path, f'. {posix(AGENT)}/lib/guibridge.sh; ' + body)
    assert "ERASED" not in out and rc == 86, out
    seen = cards(rd)
    assert seen and seen[-1] == ("message=השחזור לא התחיל — לא נכתב דבר|\u200f"
                                 "האימג׳ כבר אינו מותר למחשב הזה · המחשב יופעל מחדש\n"), (seen, out)
    assert len((rd / "gui.calls").read_text(encoding="utf-8").splitlines()) == 1


def test_a_restore_that_succeeded_powers_off_and_never_reboots(tmp_path):
    """Nadav 17/10: after "שחזור לדיסק הזה" the machine powers off (20 s on
    the screen, or "כבה עכשיו") -- it rebooted until then. Negative control:
    on main the success returns before any power step (and reboots outside
    tests) -- no TEST-POWEROFF."""
    out, rc, rd = run(tmp_path, 'arm_wol() { return 0; }; ' + HANDOFF)
    assert "ERASED img_1" in out and "PARENT rc=0" in out, out
    assert "TEST-POWEROFF" in out and "TEST-REBOOT" not in out, out
    assert "restore: ended -- powering off in 20s" in (rd / "agent.log").read_text(encoding="utf-8")


def test_the_handoff_with_a_live_session_erases(tmp_path):
    out, _, _ = run(tmp_path, HANDOFF)
    assert "ERASED img_1" in out, out
    assert "PARENT rc=0" in out


# --- הגשר: ה-jar עובר עם ה-handoff, וסשן מת אינו משאיר תפקיד מאחור ----------


def bridge(tmp_path: Path, body: str) -> tuple[str, Path]:
    rd = tmp_path / "run"
    gui = rd / "gui"
    gui.mkdir(parents=True, exist_ok=True)
    script = (
        f"export RUN_DIR={shlex.quote(posix(rd))} GUI_DIR={shlex.quote(posix(gui))} "
        f"LOG_FILE={shlex.quote(posix(rd / 'agent.log'))} SERVER=http://x MAC=aa:bb "
        "HTTP_TIMEOUT=1 HTTP_RETRIES=0; "
        f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/jsonq.sh; "
        f". {posix(AGENT)}/lib/buildmenu.sh; . {posix(AGENT)}/lib/guibridge.sh; "
        'CONSOLE_JAR="$GUI_DIR/console.jar"; '
        + body
    )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60)
    return proc.stdout + proc.stderr, rd


RESTORE_RECORD = (
    'gui_role() { GUI_ROLE=admin; }; '
    'http_get() { printf \'{"name":"BUILD-01"}\'; }; '
    'jq() { printf "BUILD-01\\n"; }; '
    'printf "restore-start\\nimage=img_1\\nconfirm=BUILD-01\\n\\n" | gui_records; '
)


def test_the_bridge_hands_the_session_over_with_the_restore(tmp_path):
    out, rd = bridge(tmp_path, 'printf "jar\\n" > "$CONSOLE_JAR"; ' + RESTORE_RECORD)
    assert (rd / "gui" / "handoff").exists(), out
    assert (rd / "gui" / "handoff.jar").read_text() == "jar\n"


def test_the_handoff_clears_the_tty_before_the_gui_lets_go(tmp_path):
    """#1561: the GUI's exit gives the VT back, with everything written to it
    since boot. While the GUI still holds it, the bridge resets it (RIS, the
    cursor off) -- so nothing English flashes before the restore's GUI.
    Negative control: on main the tty is not written."""
    tty = tmp_path / "tty0"
    out, rd = bridge(tmp_path, f'IMAGECTL_TTY={shlex.quote(posix(tty))}; '
                     'printf "jar\\n" > "$CONSOLE_JAR"; ' + RESTORE_RECORD)
    assert (rd / "gui" / "handoff").exists(), out
    assert tty.exists() and tty.read_bytes() == b"\x1bc\x1b[?25l", out


def test_no_session_in_the_bridge_means_no_handoff(tmp_path):
    out, rd = bridge(tmp_path, RESTORE_RECORD)
    assert not (rd / "gui" / "handoff").exists(), out
    assert not (rd / "gui" / "handoff.jar").exists()


def test_logout_drops_the_session_and_the_role(tmp_path):
    out, rd = bridge(tmp_path,
                     'printf "jar\\n" > "$CONSOLE_JAR"; '
                     'printf \'{"role":"admin"}\' > "$RUN_DIR/console_login.json"; '
                     'printf "logout\\n\\n" | gui_records; echo "rc=$?"')
    assert "rc=0" in out, out
    assert not (rd / "gui" / "console.jar").exists()
    assert not (rd / "console_login.json").exists()


@pytest.mark.parametrize("code", ["401", "503"])
def test_a_refused_sign_in_leaves_no_role_behind(tmp_path, code):
    """curl כותב את גוף הסירוב ל-console_login.json; ‏gui_role קורא משם תפקיד.
    סירוב חייב למחוק אותו — אחרת תפקיד ישן מכניסה קודמת עובר את gui_role."""
    out, rd = bridge(tmp_path,
                     'printf \'{"role":"admin"}\' > "$RUN_DIR/console_login.json"; '
                     f'curl() {{ cat > /dev/null; printf {code}; }}; '
                     'RECOVERY_USER=u RECOVERY_PASS=p; console_signin; echo "rc=$?"; '
                     'gui_role && echo ROLE-PASSED')
    assert "rc=1" in out, out
    assert not (rd / "console_login.json").exists()
    assert "ROLE-PASSED" not in out


# --- 11/10: the session goes with the pull, once -----------------------------


def test_the_pull_carries_the_gui_session_as_its_cookie(tmp_path):
    out, _, rd = run(tmp_path,
                     f'. {posix(AGENT)}/lib/pull.sh; '
                     'PULL_JAR="$RUN_DIR/gui/handoff.jar"; echo "{}" > "$RUN_DIR/body"; '
                     'pull_post http://x "$RUN_DIR/body" "$RUN_DIR/resp"; echo')
    log = (rd / "curl.log").read_text()
    assert f"-b {posix(rd / 'gui' / 'handoff.jar')}" in log, log
    assert "/api/v1/agent/pulls" in log


def test_without_a_session_the_pull_carries_no_cookie(tmp_path):
    out, _, rd = run(tmp_path,
                     f'. {posix(AGENT)}/lib/pull.sh; PULL_JAR=; echo "{{}}" > "$RUN_DIR/body"; '
                     'pull_post http://x "$RUN_DIR/body" "$RUN_DIR/resp"; echo', jar=False)
    assert " -b " not in (rd / "curl.log").read_text()


def test_the_restore_uses_the_session_once_and_deletes_it(tmp_path):
    out, _, rd = run(tmp_path,
                     f'. {posix(AGENT)}/lib/single_restore.sh; '
                     'PULL_JAR="$RUN_DIR/gui/handoff.jar"; '
                     'http_get() { echo "{}"; }; pick_internal_disk() { echo sda; }; target_init() { :; }; '
                     'sr_share_gate() { return 0; }; '   # #1898: the split question -- its own tests
                     'pull_open() { [ -s "$PULL_JAR" ] && echo "PULL-WITH-SESSION"; PULL_SESSION=ses_1; }; '
                     'progress_loop() { :; }; run_restore() { return 0; }; stage_drivers() { :; }; writemark_release_done() { :; }; '
                     'pull_close() { :; }; '
                     'single_restore_run img_1; echo "rc=$?"')
    assert "PULL-WITH-SESSION" in out and "rc=0" in out, out
    assert not (rd / "gui" / "handoff.jar").exists()
