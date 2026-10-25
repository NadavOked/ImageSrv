"""16/10 — "שחזור אימג' מהשרת" on the cloner, the agent's side (lcpull.sh,
localclone.sh, lcgui.sh), without disks and without a server.

The third boot choice opens a room round of ONE machine on its own drawers
(``POST /api/v1/agent/cloner-round``, ``test_cloner_round_server.py``) and
then goes on from the server: nothing here writes a disk. What is checked:
the choice is taken and "not chosen" is still the server; the screen's
``lc-pull`` is refused unless every port names exactly one attached drawer;
the controller checks again, opens on positive evidence only (200 + ok) and
says the server's refusal on the screen; the screen's records carry the
images and the drawers only -- never a removable boot stick.
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import GUI_UP, LIBS as LC_LIBS, LIB, REPO

pytestmark = requires_native(("bash", BASH))

LIBS = LC_LIBS + ("lcpull", "syspartgui")   # #1896: lc_pull_images carries the C: records

#: three drawers, disk numbers by the drawer mapping -- sdc is disk 1 (as on cloner 2)
DRAWERS = ('list_drawers() { echo sda; echo sdb; echo sdc; }; '
           'disk_port() { case "$1" in sda) echo 2;; sdb) echo 3;; sdc) echo 1;; *) echo 0;; esac; }; ')
BOOT = DRAWERS + 'D_ROLE=cloner; ' + GUI_UP


def run(tmp_path: Path, script: str, timeout: int = 60, **env):
    import subprocess
    run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
    pre = f'export RUN_DIR={posix(run_dir)!r} IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff SERVER=http://10.0.0.1:8080; '
    pre += "".join(f'export {k}={v!r}; ' for k, v in env.items())
    pre += "".join(f'. {posix(LIB)}/{name}.sh; ' for name in LIBS)
    # #1579: lc_pull_session starts the hello beat (attended_start: a background
    # `while :; sleep 1` subshell) and its caller in the agent stops it. The test
    # is that caller: without this trap the beat outlived every test that opens
    # the session, for good, holding the run dir. attended_stop kills its own pid.
    pre += "trap 'attended_stop' EXIT; "
    return hygiene.run_group([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             stdin=subprocess.DEVNULL, cwd=str(REPO))


# --- the boot choice -------------------------------------------------------------------

def test_the_third_choice_is_taken_and_goes_to_the_pull_screen(tmp_path):
    out = run(tmp_path, BOOT + 'local_clone_arm; ( sleep 1; echo pull > "$LC/boot.decision" ) & '
              'lc_pull_session() { echo PULL; LC_END=opened; }; local_clone_offer; '
              'echo "after=$(cat "$LC/screen" 2>/dev/null || echo none)"; kill $_gui_pid', LC_BOOT_S="20")
    assert out.returncode == 0, out.stderr
    assert "'restore an image from the server' chosen" in out.stdout and "PULL" in out.stdout, out.stdout
    assert "going on from the server" in out.stdout and "after=none" in out.stdout, out.stdout


def test_back_from_the_pull_screen_is_the_paused_choice_again(tmp_path):
    out = run(tmp_path, BOOT + 'local_clone_arm; n=0; '
              'lc_pull_session() { n=$((n+1)); echo "PULL$n"; LC_END=back; ( sleep 1; echo server > "$LC/boot.decision" ) & }; '
              '( sleep 1; echo pull > "$LC/boot.decision" ) & local_clone_offer; kill $_gui_pid', LC_BOOT_S="20")
    assert out.returncode == 0, out.stderr
    assert "PULL1" in out.stdout and "PULL2" not in out.stdout
    assert "'from the server' chosen" in out.stdout, out.stdout


def test_no_touch_is_still_the_server(tmp_path):
    """Principle 1 / #17: the third card changes nothing for an unattended machine."""
    t0 = time.monotonic()
    out = run(tmp_path, BOOT + 'local_clone_arm; lc_pull_session() { echo PULL; }; local_clone_offer; kill $_gui_pid',
              LC_BOOT_S="2")
    assert out.returncode == 0, out.stderr
    assert "PULL" not in out.stdout and "no touch for 2s -- from the server" in out.stdout
    assert time.monotonic() - t0 < 30


def test_with_one_disk_local_is_refused_and_pull_is_not(tmp_path):
    out = run(tmp_path, 'list_drawers() { echo sda; }; D_ROLE=cloner; ' + GUI_UP + 'local_clone_arm; '
              '( sleep 1; echo local > "$LC/boot.decision"; sleep 2; echo pull > "$LC/boot.decision" ) & '
              'lc_session() { echo LOCAL; }; lc_pull_session() { echo PULL; LC_END=opened; }; '
              'local_clone_offer; kill $_gui_pid', LC_BOOT_S="20")
    assert out.returncode == 0, out.stderr
    assert "'local clone' refused -- one disk" in out.stdout and "LOCAL" not in out.stdout, out.stdout
    assert "PULL" in out.stdout


# --- the clicks, as the bridge takes them (lcgui.sh lc_gui_dispatch) -------------------

TRY = 'try() { token=$1; lc_gui_dispatch && echo "ok $1" || echo "refused $1"; }; '


def test_the_bridge_takes_boot_pull_and_boot_local_only_with_two_disks(tmp_path):
    out = run(tmp_path, TRY + 'mkdir -p "$LC"; echo boot > "$LC/screen"; echo 0 > "$LC/boot.local"; '
              'try boot-local; try boot-pull; cat "$LC/boot.decision"; echo 1 > "$LC/boot.local"; try boot-local; '
              'echo pull > "$LC/screen"; try boot-pull')
    lines = out.stdout.splitlines()
    assert lines[:3] == ["refused boot-local", "ok boot-pull", "pull"], lines
    assert "ok boot-local" in lines and lines[-1] == "refused boot-pull", lines


@pytest.mark.parametrize("token", [
    "lc-pull|img_7f3a91|4",           # no drawer with disk number 4
    "lc-pull|img_7f3a91|1,1",         # twice
    "lc-pull|img_7f3a91|0",           # no slot
    "lc-pull|img_7f3a91|01",
    "lc-pull|img_7f3a91|",            # none ticked is never "all"
    "lc-pull|img_7f3a91|1,",
    "lc-pull|img_7f3a91|1|x",
    "lc-pull|img_7f3a91|a",
    "lc-pull|img;rm|1",
    "lc-pull||1",
])
def test_a_pull_that_does_not_name_attached_drawers_is_refused(tmp_path, token):
    out = run(tmp_path, DRAWERS + TRY + f'mkdir -p "$LC"; echo pull > "$LC/screen"; try {token!r}; '
              'test -f "$LC/pull.req" && echo WRITTEN || echo NONE')
    assert out.stdout.splitlines() == [f"refused {token}", "NONE"], out.stdout


def test_a_pull_of_attached_drawers_is_handed_to_the_controller(tmp_path):
    out = run(tmp_path, DRAWERS + TRY + 'mkdir -p "$LC"; echo boot > "$LC/screen"; try "lc-pull|img_7f3a91|1,3"; '
              'echo pull > "$LC/screen"; try "lc-pull|img_7f3a91|1,3"; cat "$LC/pull.req"; try lc-back; cat "$LC/end.req"')
    assert out.stdout.splitlines() == ["refused lc-pull|img_7f3a91|1,3", "ok lc-pull|img_7f3a91|1,3",
                                       "img_7f3a91|1,3", "ok lc-back", "back"], out.stdout


def test_a_removable_disk_is_never_a_drawer(tmp_path):
    """list_drawers is the only source: a USB stick in list_disks is not offered."""
    out = run(tmp_path, 'list_disks() { echo sda; echo sdb; }; mkdir -p "$SYSROOT/sys/block/sda/device" "$SYSROOT/sys/block/sdb/device"; '
              'echo 0 > "$SYSROOT/sys/block/sda/removable"; echo 1 > "$SYSROOT/sys/block/sdb/removable"; '
              'disk_port() { case "$1" in sda) echo 1;; sdb) echo 2;; esac; }; '
              'lc_pull_ports 1 && echo OK1; lc_pull_ports 2 || echo NO2', SYSROOT=posix(tmp_path / "root"))
    assert out.stdout.splitlines() == ["1", "OK1", "NO2"], (out.stdout, out.stderr)


# --- the controller (lcpull.sh lc_pull_open / lc_pull_session) ---------------------------

def said(out) -> list[str]:
    """stdout without the agent's console log lines: log() writes
    "imagectl: ..." to stdout as well as to the log file (common.sh) -- the
    console of the machine is where a technician reads it. The functions'
    answer is their exit code, echoed by the test."""
    return [l for l in out.stdout.splitlines() if not l.startswith("imagectl: ")]


#: a curl that records its arguments and the body, and answers $CODE with $BODY
CURL = ('curl() { printf "%s\\n" "$@" > "$RUN_DIR/curl.args"; o=; d=; '
        'while [ $# -gt 0 ]; do case "$1" in -o) o=$2; shift;; --data-binary) d=${2#@}; shift;; esac; shift; done; '
        'cp "$d" "$RUN_DIR/curl.body"; printf "%s" "$BODY" > "$o"; printf "%s" "$CODE"; }; ')


@requires_native(("jq", shutil.which("jq")))   # json_get reads the answer
@pytest.mark.parametrize("code,body,ok,error", [
    ("200", '{"ok":true,"id":"room_ab12","wave_session_id":"s1"}', True, ""),
    ("200", '{"ok":false,"error":"x"}', False, "x"),               # 200 without ok:true is not an open round
    ("409", '{"ok":false,"error":"כבר יש סבב חדר פעיל","code":"round_conflict"}', False, "כבר יש סבב חדר פעיל"),
    ("000", "", False, "השרת לא פתח את הסבב (http 000)"),
])
def test_the_round_is_open_only_on_positive_evidence(tmp_path, code, body, ok, error):
    out = run(tmp_path, CURL + 'mkdir -p "$LC"; HTTP_TIMEOUT=5; echo "ab12" > "$LC/pull.nonce"; '
              'lc_pull_open img_7f3a91 "1 3" && echo OPEN || echo REFUSED; cat "$LC/pull.error" 2>/dev/null; '
              'echo; cat "$RUN_DIR/curl.body"; echo; cat "$RUN_DIR/curl.args"', CODE=code, BODY=body)
    lines = said(out)
    assert lines[0] == ("OPEN" if ok else "REFUSED"), (out.stdout, out.stderr)
    if error:
        assert error in out.stdout
    body_line = next(l for l in lines if l.startswith("{"))
    assert json.loads(body_line) == {"mac": "aa:bb:cc:dd:ee:ff", "nonce": "ab12", "image_id": "img_7f3a91", "ports": [1, 3]}
    assert "--retry" not in lines and "http://10.0.0.1:8080/api/v1/agent/cloner-round" in lines


@requires_native(("jq", shutil.which("jq")))
@pytest.mark.parametrize("code,body,kept", [
    ("200", '{"ok":true,"nonce":"0f3a","ttl_s":900}', "0f3a"),
    ("200", '{"ok":true}', ""),                           # no token in a 200 is no token
    ("200", '{"ok":true,"nonce":"x;rm"}', ""),
    ("403", '{"ok":false,"code":"identity_unverifiable"}', ""),
    ("000", "", ""),
])
def test_the_screen_token_is_kept_only_on_positive_evidence(tmp_path, code, body, kept):
    out = run(tmp_path, CURL + 'mkdir -p "$LC"; HTTP_TIMEOUT=5; echo old > "$LC/pull.nonce"; '
              'lc_pull_nonce && echo GOT || echo NONE; cat "$LC/pull.nonce" 2>/dev/null; echo; cat "$RUN_DIR/curl.args"',
              CODE=code, BODY=body)
    lines = said(out)
    assert lines[0] == ("GOT" if kept else "NONE"), (out.stdout, out.stderr)
    if not kept:   # a refusal is said on the console (log), never silent
        assert "imagectl: pull: the server gave no screen token" in out.stdout, out.stdout
    assert (kept in lines) if kept else "old" not in lines           # a stale token never survives
    assert "http://10.0.0.1:8080/api/v1/agent/cloner-round/nonce" in lines and "--retry" not in lines


def test_the_session_checks_again_then_opens_and_leaves(tmp_path):
    out = run(tmp_path, DRAWERS + 'sleep 300 & _gui_pid=$!; mkdir -p "$LC"; '
              'lc_pull_images() { echo "image=img_7f3a91|Office|Office" > "$LC/pull.images"; }; '
              'lc_pull_nonce() { echo NONCE; }; '
              'lc_pull_open() { echo "OPEN $1 [$2]"; }; '
              '( sleep 1; echo "img_7f3a91|4" > "$LC/pull.req"; sleep 2; echo "img_7f3a91|1,3" > "$LC/pull.req" ) & '
              'lc_pull_session; echo "end=$LC_END"; cat "$LC/screen"; kill $_gui_pid')
    assert out.returncode == 0, out.stderr
    assert "the choice is not what is attached now" in out.stdout, out.stdout   # disk 4 is not there
    assert "OPEN img_7f3a91 [1 3]" in out.stdout and "end=opened" in out.stdout, out.stdout
    assert out.stdout.count("NONCE") == 2, out.stdout     # on the way in, and after the refused try
    assert out.stdout.splitlines()[-1] == "pull"


def test_a_refused_open_stays_on_the_screen_and_back_leaves(tmp_path):
    out = run(tmp_path, DRAWERS + 'sleep 300 & _gui_pid=$!; mkdir -p "$LC"; lc_pull_images() { :; }; lc_pull_nonce() { echo NONCE; }; '
              'lc_pull_open() { _lc_req pull.error "כבר יש סבב חדר פעיל"; return 1; }; '
              '( sleep 1; echo "img_7f3a91|1" > "$LC/pull.req"; sleep 2; cat "$LC/pull.error"; echo back > "$LC/end.req" ) & '
              'lc_pull_session; echo "end=$LC_END"; kill $_gui_pid')
    assert out.returncode == 0, out.stderr
    assert "כבר יש סבב חדר פעיל" in out.stdout and "end=back" in out.stdout, out.stdout


def test_images_that_could_not_be_read_are_said_not_hidden(tmp_path):
    out = run(tmp_path, 'sleep 300 & _gui_pid=$!; mkdir -p "$LC"; lc_pull_images() { return 1; }; lc_pull_nonce() { :; }; '
              '( sleep 1; echo back > "$LC/end.req" ) & lc_pull_session; cat "$LC/pull.error"; kill $_gui_pid')
    assert "לא ניתן לקרוא מהשרת את רשימת האימג׳ים" in out.stdout, out.stdout


# --- the screen's records (lcpull.sh lc_pull_state, lcgui.sh lc_gui_state) ---------------

def test_the_pull_screen_records_are_the_images_the_drawers_and_the_refusal(tmp_path):
    out = run(tmp_path, DRAWERS + 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC"; '
              'cloner_cdisks() { for d in $1; do echo "cdisk=$(disk_port $d)|$d"; done; }; '
              'slot_names_refresh() { :; }; drawer_slots_refresh() { :; }; slot_names_records() { echo "cslot_name=1|שמאל"; }; '
              'gui_header_state() { echo machine_name=CLONER-02; }; gui_hello_state() { :; }; '
              'echo pull > "$LC/screen"; echo "image=img_7f3a91|Office|Office" > "$LC/pull.images"; '
              'echo "השרת סירב" > "$LC/pull.error"; lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert "lc_screen=pull" in lines and "image=img_7f3a91|Office|Office" in lines, lines
    assert [l for l in lines if l.startswith("cdisk=")] == ["cdisk=2|sda", "cdisk=3|sdb", "cdisk=1|sdc"], lines
    assert "cslot_name=1|שמאל" in lines and "form_error=השרת סירב" in lines
    assert not any(l.startswith(("lc_boot=", "lc_disk=")) for l in lines), lines


def test_the_boot_screen_says_whether_the_local_card_is_offered(tmp_path):
    out = run(tmp_path, 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC"; gui_header_state() { :; }; gui_hello_state() { :; }; '
              'echo boot > "$LC/screen"; echo "0|0" > "$LC/boot"; echo 0 > "$LC/boot.local"; lc_gui_state; grep lc_local "$GUI_DIR/state"')
    assert out.stdout.splitlines() == ["lc_local=0"], (out.stdout, out.stderr)


# --- the wiring ----------------------------------------------------------------------

def test_nothing_is_written_and_the_command_line_is_not_read():
    """lcpull.sh opens a round on the server; the round's own path writes."""
    text = (LIB / "lcpull.sh").read_text(encoding="utf-8")
    code = "\n".join(l.split("#")[0] for l in text.splitlines())
    for tool in ("apply_gpt", "run_restore", "partclone", "sgdisk", "dd ", "mkfs", "/proc/cmdline", "parse_cmdline"):
        assert tool not in code, tool


def test_the_kiosk_and_the_agent_both_load_lcpull():
    bridge = (LIB / "guibridge.sh").read_text(encoding="utf-8")
    libs = bridge[bridge.index("gui_libs() {"):bridge.index("gui_main() {")]
    assert '"$LIB_DIR/lcpull.sh"' in libs and '"$LIB_DIR/drawers.sh"' in libs   # list_drawers in the kiosk
    assert '"$LIB_DIR/lcpull.sh"' in (REPO / "agent" / "imagectl-agent").read_text(encoding="utf-8")


@requires_native(("/proc", Path("/proc/self").is_dir()), posix=True, why="the check reads /proc")
def test_a_session_leaves_no_process_behind(tmp_path):
    """#1579: the session starts a background beat; what run() starts is gone after it.
    A process is "left" when /proc holds a command line naming this test's run dir."""
    out = run(tmp_path, 'sleep 300 & _gui_pid=$!; mkdir -p "$LC"; lc_pull_images() { :; }; lc_pull_nonce() { :; }; '
              '( sleep 1; echo back > "$LC/end.req" ) & lc_pull_session; echo "end=$LC_END"; kill $_gui_pid')
    assert out.returncode == 0 and "end=back" in out.stdout, out.stdout + out.stderr
    mine = str(tmp_path).encode()
    deadline = time.monotonic() + 5
    while True:   # a killed process leaves /proc a moment after the kill returns
        left = []
        for cmd in Path("/proc").glob("[0-9]*/cmdline"):
            try:
                if mine in cmd.read_bytes():
                    left.append(f"{cmd.parent.name}: {cmd.read_bytes()[-200:]!r}")
            except OSError:   # the process ended between the glob and the read
                pass
        if not left or time.monotonic() > deadline:
            break
        time.sleep(0.2)
    assert not left, left
