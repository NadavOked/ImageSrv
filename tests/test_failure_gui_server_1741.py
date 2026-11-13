"""#1741: the server's own failure screens on the GUI, display-only; #2517: "started" from evidence.

נדב, 17/10: "אין מסך שאינו גואי". שלושה מסכים שאדם עומד מולם נשארו טקסט
אחרי #1739: כישלון האתחול הראשון (S3, `tools/iso/firstboot.sh`), ה-hold
של המתקין החי (S9, `installer/imagesrv-installer`) וכיבוי/אתחול של השרת
(S7). עכשיו כולם כותבים את רשומות מסך הכישלון האחיד (#1569) עם role
`server`/`installer` (`installer/lib/failgui.sh`) ומעלים את הגואי
עם `--no-input`. הסיבה לא מוסתרת: העברית, ושורת היומן באנגלית מתחתיה.
בלי framebuffer או בינארי — הטקסט הוא המסך.

#2517: יחידת גואי ההתקנה נכשלה ב-203/EXEC (הבינארי לא הועתק למערכת
המותקנת) ו-firstboot כתב "started" כי `is-active` נבדק לפני ה-exec.
"הוצג" נקבע עכשיו רק מראיה: הגואי נגע ב-`IMAGESRV_GUI_BEAT` אחרי פריים.

הטסטים מריצים את הקוד האמיתי ב-bash עם גבול המערכת (systemctl,
systemd-run, הגואי) מזויף כפונקציות וסקריפטים, כדי שבקרה שלילית — הקוד
הישן — תיפול על ההתנהגות (אין גואי, "started" בלי פריים) ולא על קובץ חסר.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, REPO, posix
from test_cloner_gui import _build_gui, _pkgconfig

FIRSTBOOT = REPO / "tools" / "iso" / "firstboot.sh"
INSTALLER = REPO / "installer" / "imagesrv-installer"
FAILGUI = REPO / "installer" / "lib" / "failgui.sh"
SHUTDOWN = REPO / "deploy" / "imagesrv-shutdown-screen.sh"
SHUTDOWN_UNIT = REPO / "deploy" / "imagesrv-shutdown-screen@.service"
INSTALLER_GUI_UNIT = REPO / "deploy" / "imagesrv-installer-gui.service"
SETUP = REPO / "deploy" / "setup-boot-server.sh"

pytestmark = pytest.mark.skipif(BASH is None, reason="bash נדרש")

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _records(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        return []
    return [tuple(line.split("=", 1)) for line in path.read_text(encoding="utf-8").splitlines() if "=" in line]


def _values(recs, key: str) -> list[str]:
    return [v for k, v in recs if k == key]


def _fake_gui(path: Path, out: Path, *, beat: bool) -> Path:
    """The GUI at the system boundary: records its arguments and the state it
    was given, and -- like main.c gui_heartbeat after a frame -- touches the beat."""
    body = (
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" > "{posix(out)}/gui.args"\n'
        'while [ $# -gt 0 ]; do [ "$1" = --state ] && cp "$2" "' + posix(out) + '/gui.seen"; shift; done\n'
    )
    if beat:
        body += ': > "$IMAGESRV_GUI_BEAT"\n'
    path.write_text(body, encoding="utf-8", newline="\n")
    path.chmod(0o755)
    return path


# ---------------------------------------------------------------------------
# S3 -- firstboot's fail(), and #2517's installer GUI
# ---------------------------------------------------------------------------

def _firstboot_functions() -> str:
    code = FIRSTBOOT.read_text(encoding="utf-8")
    return code[code.index("log() {"):code.index("unexpected_failure() {")]


def _firstboot_harness(tmp_path: Path, tail: str, *, fb: bool = True, gui: str | None = "beat",
                       dcui: str = "inactive", start_rc: int = 0, start_beats: bool = False) -> dict:
    """firstboot's own functions (log .. fail) under bash -euo pipefail, with
    the tree under tmp_path and systemctl / systemd-run as recorded functions.
    gui: "beat" = a GUI that draws, "dead" = one that exits without a frame, None = no binary."""
    t = tmp_path
    src = t / "src"
    (src / "installer" / "lib").mkdir(parents=True)
    if FAILGUI.exists():
        shutil.copy(FAILGUI, src / "installer" / "lib" / "failgui.sh")
    if fb:
        (t / "fb0").write_text("", encoding="ascii")
    gui_bin = t / "gui"
    if gui is not None:
        _fake_gui(gui_bin, t, beat=gui == "beat")
    calls = t / "calls"
    harness = t / "harness.sh"
    harness.write_text(f"""set -euo pipefail
T={posix(t)}
ETC=$T/etc; STATUS=$ETC/firstboot.status; FIRSTBOOT_TTY=$T/tty1; TTY_PROGRESS=0
SRC=$T/src; FAILGUI_LIB=$SRC/installer/lib/failgui.sh; FAILGUI_WAIT_S=2
FB_DEV=$T/fb0; GUI_BINS="$T/gui $T/no-such-gui"
FAIL_GUI_STATE=$T/fail.state; FAIL_GUI_BEAT=$T/fail.beat; INSTALLER_GUI_BEAT=$T/inst.beat
has_keyboard=1
systemctl() {{
    printf 'systemctl %s\\n' "$*" >> {posix(calls)}
    case "$*" in
        "is-active imagesrv-dcui.service") echo {dcui}; [ {dcui} = active ]; return ;;
        "is-active imagesrv-installer-gui.service") echo inactive; return 3 ;;
        "start imagesrv-installer-gui.service") {': > "$INSTALLER_GUI_BEAT"; ' if start_beats else ''}return {start_rc} ;;
        "is-active --quiet imagesrv-installer-gui.service") return 0 ;;   # 'active': is-active before the exec (#2517)
        "is-active --quiet imagesrv-firstboot-failure.service") [ -f $T/gui.ran ] && [ -f $T/fail.beat ]; return ;;
    esac
    return 0
}}
systemd-run() {{
    printf 'systemd-run %s\\n' "$*" >> {posix(calls)}
    local beat=""
    while [ $# -gt 0 ] && [ "$1" != -- ]; do
        case "$1" in --setenv=IMAGESRV_GUI_BEAT=*) beat=${{1#--setenv=IMAGESRV_GUI_BEAT=}} ;; esac
        shift
    done
    shift
    : > $T/gui.ran
    IMAGESRV_GUI_BEAT=$beat "$@"
}}
{_firstboot_functions()}
{tail}
""", encoding="utf-8", newline="\n")
    got = subprocess.run([BASH, posix(harness)], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    return {"got": got, "calls": calls.read_text(encoding="utf-8") if calls.exists() else "",
            "state": _records(t / "fail.state"), "tty": (t / "tty1").read_text(encoding="utf-8")
            if (t / "tty1").exists() else "", "status": _records(t / "etc" / "firstboot.status"),
            "args": (t / "gui.args").read_text(encoding="utf-8") if (t / "gui.args").exists() else ""}


FAIL_PAYLOAD = 'fail payload-failed "Boot payload decision failed: kernel 6.12 has no modules"'


def test_s3_a_first_boot_failure_is_drawn_on_the_gui_with_its_reason(tmp_path):
    r = _firstboot_harness(tmp_path, FAIL_PAYLOAD)
    assert r["got"].returncode == 1, r["got"].stderr
    assert "systemd-run --unit=imagesrv-firstboot-failure" in r["calls"], r["calls"]
    for word in ("--backend fbdev", "--no-input", f"--state {posix(tmp_path)}/fail.state"):
        assert word in r["args"], r["args"]
    fail = _values(r["state"], "fail")
    assert fail == ["bad|1|ההכנה הראשונה של השרת נכשלה|first boot failed · payload-failed|server"], r["state"]
    assert "בניית קובצי האתחול של השרת" in _values(r["state"], "fail_why")[0]
    # the reason is not hidden: the log's own line is on the screen
    assert any("Boot payload decision failed: kernel 6.12 has no modules" in s
               for s in _values(r["state"], "fail_step")), r["state"]
    assert "failure screen: on the GUI" in r["got"].stdout
    # the text stays under it (a GUI that dies later leaves the line), and the status is written
    assert "first boot failed [payload-failed]" in r["tty"]
    assert _values(r["status"], "state") == ["payload-failed"]


@pytest.mark.parametrize("case,why", [
    ({"fb": False}, "no framebuffer"),
    ({"gui": None}, "no GUI binary"),
    ({"dcui": "active"}, "tty1 belongs to the DCUI"),
])
def test_s3_without_framebuffer_binary_or_a_free_tty1_the_text_is_the_screen(tmp_path, case, why):
    r = _firstboot_harness(tmp_path, FAIL_PAYLOAD, **case)
    assert r["got"].returncode == 1
    assert "systemd-run" not in r["calls"], "never two GUIs on one framebuffer, never a GUI with nothing to draw on"
    assert "first boot failed [payload-failed]" in r["tty"]
    assert "on the GUI" not in r["got"].stdout


def test_s3_a_gui_that_drew_no_frame_is_not_called_shown(tmp_path):
    r = _firstboot_harness(tmp_path, FAIL_PAYLOAD, gui="dead")
    assert r["got"].returncode == 1
    assert "systemd-run --unit=imagesrv-firstboot-failure" in r["calls"]
    assert "the GUI drew no frame" in r["got"].stderr, r["got"].stderr
    assert "on the GUI" not in r["got"].stdout, "a started process is not a shown screen (principle 5)"
    assert "first boot failed [payload-failed]" in r["tty"]


def _installer_gui_block() -> str:
    code = FIRSTBOOT.read_text(encoding="utf-8")
    start = code.index('if [[ -e /dev/fb0 && "$has_keyboard" == 1 ]]; then')
    end = code.index("\nfi\n", start) + 4
    return code[start:end].replace("/dev/fb0", "$T/fb0")


def test_2517_an_installer_gui_that_never_drew_is_a_failure_not_started(tmp_path):
    """203/EXEC with Type=simple: start returned 0, is-active said active, and
    nothing was on tty1 -- firstboot wrote "started on tty1"."""
    r = _firstboot_harness(tmp_path, _installer_gui_block(), gui=None)
    assert r["got"].returncode == 1, (r["got"].stdout, r["got"].stderr)
    assert "started on tty1" not in r["got"].stdout and "on tty1 (it drew" not in r["got"].stdout
    assert _values(r["status"], "state") == ["installer-gui-failed"], r["status"]


def test_2517_an_installer_gui_that_cannot_exec_fails_by_name(tmp_path):
    r = _firstboot_harness(tmp_path, _installer_gui_block(), gui=None, start_rc=1)
    assert r["got"].returncode == 1
    assert _values(r["status"], "state") == ["installer-gui-failed"]


def test_2517_an_installer_gui_that_drew_a_frame_is_on_tty1(tmp_path):
    r = _firstboot_harness(tmp_path, _installer_gui_block(), start_beats=True)
    assert r["got"].returncode == 0, r["got"].stderr
    assert "installer gui: on tty1 (it drew a frame)" in r["got"].stdout
    assert r["status"] == []


def test_2517_the_unit_execs_the_installed_binary_and_names_its_beat():
    unit = INSTALLER_GUI_UNIT.read_text(encoding="utf-8")
    assert "Type=exec" in unit, "203/EXEC must be a failed start, not an active unit"
    assert "Environment=IMAGESRV_GUI_BEAT=/run/imagesrv-installer-gui.beat" in unit
    assert "ExecStart=/usr/bin/imagesrv-station-gui --screen install" in unit
    code = FIRSTBOOT.read_text(encoding="utf-8")
    assert "INSTALLER_GUI_BEAT=/run/imagesrv-installer-gui.beat" in code


# ---------------------------------------------------------------------------
# S9 -- the live installer's hold
# ---------------------------------------------------------------------------

def _hold(tmp_path: Path, *, fb: bool = True, gui: str | None = "beat") -> dict:
    """The real imagesrv-installer, sourced by bash so that the hold's
    `sleep 60` ends the run instead of waiting for a person."""
    t = tmp_path
    inst = t / "installer"
    (inst / "lib").mkdir(parents=True)
    if FAILGUI.exists():
        shutil.copy(FAILGUI, inst / "lib" / "failgui.sh")
    bridge = inst / "gui-bridge.sh"
    bridge.write_text("#!/bin/sh\n", encoding="ascii", newline="\n")
    bridge.chmod(0o755)
    cdrom = t / "cdrom"   # no imagesrv-iso.json: the first hold, before the keyboard is looked at
    cdrom.mkdir()
    gui_bin = t / "gui"
    if gui is not None:
        _fake_gui(gui_bin, t, beat=gui == "beat")
    harness = t / "harness.sh"
    harness.write_text(f"""
sleep() {{ if [ "$1" = 60 ]; then echo HOLD-LOOP; exit 0; fi; command sleep "$@"; }}
INSTALLER_DIR={posix(inst)} GUI_BIN={posix(gui_bin)} RUN_DIR={posix(t)}/run CDROM={posix(cdrom)}
STATE_FILE={posix(t)}/run/install.state FB_DEV={'/dev/null' if fb else posix(t) + '/no-fb'} FAILGUI_WAIT_S=2
export INSTALLER_DIR GUI_BIN RUN_DIR CDROM STATE_FILE FB_DEV FAILGUI_WAIT_S
. {posix(INSTALLER)}
""", encoding="utf-8", newline="\n")
    got = subprocess.run([BASH, posix(harness)], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    return {"got": got, "state": _records(t / "gui.seen"),
            "args": (t / "gui.args").read_text(encoding="utf-8") if (t / "gui.args").exists() else ""}


def test_s9_no_media_with_a_framebuffer_is_drawn_on_the_gui(tmp_path):
    r = _hold(tmp_path)
    assert "HOLD-LOOP" in r["got"].stdout, r["got"].stdout + r["got"].stderr
    assert "--no-input" in r["args"] and "--backend fbdev" in r["args"], r["args"]
    assert _values(r["state"], "fail") == ["bad|1|מדיית ההתקנה לא נמצאה|install media not found|installer"], r["state"]
    assert any("install media is not mounted" in s for s in _values(r["state"], "fail_step"))
    assert "the reason is on the screen (GUI)" in r["got"].stdout
    # the text stays as it was: the notice and what to do, every minute
    assert "install media is not mounted" in r["got"].stdout


def test_s9_no_media_names_the_common_cause_in_hebrew_and_english_2761(tmp_path):
    """#2761: the screen says what a person most often did wrong -- an unfinished write with Rufus --
    as a common cause (not a promise), in both languages, and keeps the plain reconnect advice."""
    r = _hold(tmp_path)
    steps = " ".join(_values(r["state"], "fail_step"))
    assert "סיבה נפוצה" in steps and "Rufus" in steps and "sha256" in steps, steps
    assert "DD Image" in steps and "העתקת קבצים" in steps, steps
    assert "Common cause" in steps and "file-copy mode" in steps, steps
    assert "חברו מחדש את הדיסק-און-קי" in steps, steps


@pytest.mark.parametrize("line,title", [
    ("no keyboard found", "לא נמצאה מקלדת"),
    ("GUI bridge /usr/lib/imagesrv/installer/gui-bridge.sh is missing from this image", "רכיב של ההתקנה חסר"),
    ("the install GUI exited with code 139 -- last state: state=ready", "מסך ההתקנה נסגר"),
])
def test_s9_every_hold_with_a_framebuffer_has_its_hebrew_title_and_its_line(tmp_path, line, title):
    """The other holds go through the same hold_screen; their words, here."""
    out = subprocess.run([BASH, "-c", '. "$1"; failgui_installer "$2"', "_", posix(FAILGUI), line],
                         capture_output=True, text=True, encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)
    recs = [tuple(x.split("=", 1)) for x in out.stdout.splitlines()]
    assert _values(recs, "fail")[0].startswith(f"bad|1|{title}|"), out.stdout + out.stderr
    assert _values(recs, "fail")[0].endswith("|installer")
    assert any(line in s for s in _values(recs, "fail_step")), "the reason is not hidden"


@pytest.mark.parametrize("case", [{"fb": False}, {"gui": None}])
def test_s9_without_framebuffer_or_binary_the_text_is_the_screen(tmp_path, case):
    r = _hold(tmp_path, **case)
    assert "HOLD-LOOP" in r["got"].stdout
    assert r["args"] == "" and "on the screen (GUI)" not in r["got"].stdout
    assert "imagesrv installer:" in r["got"].stdout


def test_s9_a_gui_that_drew_no_frame_leaves_the_text(tmp_path):
    r = _hold(tmp_path, gui="dead")
    assert "the GUI drew no frame" in r["got"].stdout, r["got"].stdout
    assert "on the screen (GUI)" not in r["got"].stdout


# ---------------------------------------------------------------------------
# S7 -- the server going down
# ---------------------------------------------------------------------------

def _shutdown(tmp_path: Path, word: str, *, gui: bool = True):
    app = tmp_path / "app"
    (app / "installer" / "lib").mkdir(parents=True)
    shutil.copy(FAILGUI, app / "installer" / "lib" / "failgui.sh")
    (app / "native-gui").mkdir()
    if gui:
        _fake_gui(app / "native-gui" / "imagesrv-station-gui", tmp_path, beat=False)
    env = {**os.environ, "APP_DIR": posix(app), "SHUTDOWN_SCREEN_STATE": posix(tmp_path / "s7.state")}
    got = subprocess.run([BASH, posix(SHUTDOWN), word], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, env=env, stdin=subprocess.DEVNULL)
    args = (tmp_path / "gui.args").read_text(encoding="utf-8") if (tmp_path / "gui.args").exists() else ""
    return got, _records(tmp_path / "gui.seen"), args


@pytest.mark.parametrize("word,title,action", [("reboot", "השרת מאתחל", "אתחול"), ("poweroff", "השרת נכבה", "כיבוי")])
def test_s7_the_server_going_down_is_drawn_on_the_gui(tmp_path, word, title, action):
    got, recs, args = _shutdown(tmp_path, word)
    assert got.returncode == 0, got.stderr
    assert "--no-input" in args and "--backend fbdev" in args
    assert _values(recs, "fail")[0].startswith(f"inf|0|{title}|"), recs
    assert _values(recs, "fail")[0].endswith("|server")
    assert f"פעולה|{action}" in _values(recs, "fail_ctx")


def test_s7_without_a_binary_says_so_and_is_red(tmp_path):
    got, recs, _ = _shutdown(tmp_path, "reboot", gui=False)
    assert got.returncode == 1 and "no GUI binary" in got.stderr
    assert recs == []


def test_s7_the_unit_runs_in_the_shutdown_transaction_after_the_dcui():
    unit = SHUTDOWN_UNIT.read_text(encoding="utf-8")
    assert "DefaultDependencies=no" in unit
    assert re.search(r"^After=.*imagesrv-dcui\.service", unit, re.M), "the DCUI's GUI leaves the framebuffer first"
    assert "Before=systemd-%i.service" in unit and "WantedBy=%i.target" in unit
    assert "ExecStart=/bin/sh /opt/imagesrv/deploy/imagesrv-shutdown-screen.sh %i" in unit
    assert "Conflicts=" not in unit, "the shutdown must not stop its own screen"
    setup = SETUP.read_text(encoding="utf-8")
    assert '"$APP_DIR/deploy/imagesrv-shutdown-screen@.service"' in setup
    assert "systemctl enable imagesrv-shutdown-screen@reboot.service imagesrv-shutdown-screen@poweroff.service" in setup
    assert setup.index("imagesrv-shutdown-screen@.service") < setup.index("run systemctl daemon-reload\nrun systemctl enable --now imagesrv-netrollback")


# ---------------------------------------------------------------------------
# #2517 -- the live installer leaves its GUI binary on the target
# ---------------------------------------------------------------------------

@pytest.mark.skipif(os.name == "nt", reason="executable shims and block-device flow are Linux-only")
def test_2517_the_installed_system_gets_the_live_images_gui_binary(tmp_path):
    from test_installer_engine import _answers, _full_install_box, _run
    box, _calls = _full_install_box(tmp_path)
    got = _run(box, _answers(tmp_path / "answers"))
    assert got.returncode == 0, got.stderr[-3000:]
    copied = box["target"] / "usr" / "bin" / "imagesrv-station-gui"
    assert copied.is_file(), "the installer GUI unit and the failure screens exec this path"
    assert copied.read_bytes() == Path(box["env"]["GUI_BIN_SRC"]).read_bytes()
    assert os.access(copied, os.X_OK)


# ---------------------------------------------------------------------------
# The GUI: role server|installer is the server's own header and status line
# ---------------------------------------------------------------------------

S3_STATE = (
    "fail=bad|1|ההכנה הראשונה של השרת נכשלה|first boot failed · payload-failed|server\n"
    "fail_why=בניית קובצי האתחול של השרת (שלב א׳) נכשלה.\n"
    "fail_step=השרת לא הושלם, והוא נשאר דלוק.\n"
    "fail_step=השורה ביומן: \u2066Boot payload decision failed\u2069\n"
    "fail_ctx=שלב|payload-failed\n"
)


def _card_words(log: Path, card: str) -> list[str]:
    words, on = [], False
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.startswith("@@ "):
            on = line == f"@@ {card}"
            continue
        if on:
            words.append(line)
    return words


def _failure_card_words(tmp_path: Path, records: str) -> list[str]:
    binary = _build_gui(tmp_path)
    state = tmp_path / "s.state"
    # message=: --png walks every card and proves its route, and the "message" card routes
    # only on a state's own message= -- the same line as every other --state render here.
    state.write_text(records + "message=x|y\n", encoding="utf-8", newline="\n")
    log = tmp_path / "text.log"
    env = {**os.environ, "IMAGESRV_GUI_TEXTLOG": posix(log)}
    fonts = REPO / "native-gui" / "fonts.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    run = subprocess.run([str(binary), "--png", posix(tmp_path / "card"), "--state", posix(state)],
                         capture_output=True, text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return _card_words(log, "failure-light")


@GUI_BUILD
@pytest.mark.parametrize("role,pill", [("server", "שרת ImageSrv"), ("installer", "התקנת שרת")])
def test_the_server_role_draws_the_servers_header_not_a_stations(tmp_path, role, pill):
    words = _failure_card_words(tmp_path, S3_STATE.replace("|server\n", f"|{role}\n"))
    assert pill in words, words
    assert "Boot payload decision failed" in " ".join(words), "the reason is on the screen"
    assert "נכשל · המחשב נשאר דלוק" in words, "the status word is the server's, not a station's"
    for station in ("מחשב בנייה", "מכונה", "טרם הסתיים hello ראשון", "MAC"):
        assert station not in words, f"{station!r}: a station's header/status on the server's own screen"
    assert not any(w.startswith("סוכן ") for w in words), words


@GUI_BUILD
def test_the_server_going_down_is_not_a_station_that_is_not_registered(tmp_path):
    """Tone inf on a station is "לא רשום בשרת" (#1802). S7 is that tone: the word is its title."""
    words = _failure_card_words(tmp_path, "fail=inf|0|השרת מאתחל|server restart|server\nfail_why=השירותים נעצרים.\n")
    assert "לא רשום בשרת" not in words, words
    assert words.count("השרת מאתחל") == 2, words   # the title, and the status word


@GUI_BUILD
@pytest.mark.parametrize("role,told", [("server", False), ("installer", False), ("cloner", True)])
def test_a_screen_written_once_is_not_an_agent_that_stopped_updating(tmp_path, role, told):
    """failgui writes the records once and no agent refreshes them, so 10 seconds in every
    real S3/S9 screen is "stale" (state.c). Live, the way failgui runs it, on a file that old:
    the first frame is what the screen says from then on. cloner is the control -- the same
    file does say it on a station, where an agent should be writing."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "s.state"
    state.write_text(S3_STATE.replace("|server\n", f"|{role}\n"), encoding="utf-8", newline="\n")
    old = time.time() - 3600
    os.utime(state, (old, old))
    log, beat = tmp_path / "text.log", tmp_path / "beat"
    env = {**os.environ, "IMAGESRV_GUI_TEXTLOG": posix(log), "IMAGESRV_GUI_BEAT": posix(beat)}
    fonts = REPO / "native-gui" / "fonts.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    proc = subprocess.Popen([str(binary), "--backend", "mem", "--fb-file", posix(tmp_path / "fb.mem"),
                             "--no-input", "--state", posix(state)], stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
    try:
        deadline = time.monotonic() + 30
        while not beat.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert beat.exists(), "no frame was presented: the words below would prove nothing"
    words = log.read_text(encoding="utf-8").splitlines()
    assert ("הסוכן לא מעדכן מצב" in words) is told, words
