"""#1322 — the local clone's agent side, without disks: the verdict rule,
the boot choice, the screen's clicks as the bridge validates them, the
evidence a target must show for one partition, and the wiring.

The engine on real (loop) disks is ``test_local_clone_engine.py`` (root, the
Testrunner); the native screen is ``test_native_local_clone.py``.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import pytest

import hygiene
from native import requires_native
from test_cloner_gui import BASH, posix

REPO = Path(__file__).resolve().parent.parent
LIB = REPO / "agent" / "lib"

pytestmark = requires_native(("bash", BASH))

LIBS = ("common", "jsonq", "waits", "sysinfo", "progress", "restore", "diskfit", "expand", "grow", "ntfsunhib", "expandshare", "hostname", "hostname_linux",
        "drawers", "verdict", "failmark", "ntfsboot", "manifest", "hibernation", "shrinkplan", "shrink",
        "shrinkmem", "smart", "guistate", "attended", "lcinv", "lcsize", "lcplan", "lcsnap", "lctune", "lccopy", "lcraw", "localclone", "lcwait", "idle", "lcgui")   # attended, lcwait, idle: #1367


def run(tmp_path: Path, script: str, timeout: int = 60, **env) -> subprocess.CompletedProcess:
    run_dir = tmp_path / "run"; run_dir.mkdir(exist_ok=True)
    pre = f'export RUN_DIR={posix(run_dir)!r} IMAGECTL_TEST=1 MAC=aa:bb:cc:dd:ee:ff; '
    pre += "".join(f'export {k}={v!r}; ' for k, v in env.items())
    pre += "".join(f'. {posix(LIB)}/{name}.sh; ' for name in LIBS)
    # #1736: the scripts leave background helpers behind -- a 20 s fallback that ends a
    # paused choice (test_lc_hello_1367), the kiosk's bridge (test_lc_poweroff_1483) --
    # and a subshell carries this bash -c's command line, tmp_path included. The whole
    # group goes at the end, as test_cloner_pull_agent's run does.
    return hygiene.run_group([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + pre + script],
                             capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                             stdin=subprocess.DEVNULL, cwd=str(REPO))


# --- the verdict (lcinv.sh lc_verdict) -------------------------------------------

G = 1_000_000_000


@pytest.mark.parametrize("target,need,state,need_min,reason,want", [
    (500 * G, 480 * G, "none", "", "", "fits"),            # larger: the last partition grows
    (480 * G, 480 * G, "none", "", "", "fits"),            # the source's own size
    (256 * G, 480 * G, "done", 200 * G, "", "shrink"),     # smaller, the data fits after the shrink
    (180 * G, 480 * G, "done", 200 * G, "", "short"),      # smaller than even the shrunk layout
    (256 * G, 480 * G, "measuring", "", "", "measuring"),
    (256 * G, 480 * G, "failed", "", "ntfsresize --info failed", "unchecked"),   # unable to check != fits
    (256 * G, 480 * G, "failed", 300 * G, "server down", "short"),             # a measured minimum still says short
    (256 * G, 480 * G, "failed", 200 * G, "server down", "unchecked"),         # it fits, but the shrink cannot happen
    (256 * G, -1, "none", "", "not GPT", "unchecked"),
    ("", 480 * G, "none", "", "", "unchecked"),             # a target size that was not read
])
def test_the_verdict_rule(tmp_path, target, need, state, need_min, reason, want):
    out = run(tmp_path, f'lc_verdict "{target}" "{need}" "{state}" "{need_min}" "{reason}"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.split("|")[0] == want, out.stdout


# --- the boot choice (localclone.sh) ----------------------------------------------

#: the GUI as the agent sees it: a live process that, once the boot choice is in
#: $LC/screen, stamps its first frame on the display (main.c, lcboot.shown --
#: the count starts there; test_boot_choice_first_frame.py)
GUI_UP = ('( while [ "$(cat "$LC/screen" 2>/dev/null)" != boot ]; do sleep 0.1; done; '
          'mkdir -p "$RUN_DIR/gui"; date +%s > "$RUN_DIR/gui/lcboot.shown"; exec sleep 300 ) & _gui_pid=$!; ')
BOOT = 'list_drawers() { echo sda; echo sdb; echo sdc; }; D_ROLE=cloner; ' + GUI_UP


def test_no_touch_is_the_server_and_nothing_else_changes(tmp_path):
    """#17 / principle 1: unattended, the machine goes on from the server --
    the choice expires by itself, and the screen is given back."""
    t0 = time.monotonic()
    out = run(tmp_path, BOOT + 'local_clone_arm; cat "$LC/screen"; lc_session() { echo SESSION; LC_END=; }; '
              'local_clone_offer; echo "after=$(cat "$LC/screen" 2>/dev/null || echo none)"; kill $_gui_pid',
              LC_BOOT_S="2")
    assert out.returncode == 0, out.stderr
    assert out.stdout.splitlines()[0] == "boot", out.stdout
    assert "SESSION" not in out.stdout, out.stdout                 # the local flow was not entered
    assert "after=none" in out.stdout and "no touch for 2s -- from the server" in out.stdout
    assert time.monotonic() - t0 < 30


def test_a_touch_stops_the_count_and_the_person_chooses(tmp_path):
    """boot-pause holds the screen past the deadline; "local" then opens the flow."""
    out = run(tmp_path, BOOT + 'local_clone_arm; '
              '( sleep 1; echo pause > "$LC/boot.decision"; sleep 5; echo local > "$LC/boot.decision" ) & '
              'lc_session() { echo SESSION; LC_END=; }; local_clone_offer; kill $_gui_pid',
              LC_BOOT_S="3")
    assert out.returncode == 0, out.stderr
    assert "touched -- the countdown stops" in out.stdout
    assert "SESSION" in out.stdout and "no touch for" not in out.stdout


def test_a_gui_that_dies_is_the_server(tmp_path):
    out = run(tmp_path, BOOT + 'local_clone_arm; kill $_gui_pid; wait $_gui_pid 2>/dev/null; '
              'lc_session() { echo SESSION; }; local_clone_offer', LC_BOOT_S="60")
    assert out.returncode == 0, out.stderr
    assert "SESSION" not in out.stdout and "from the server" in out.stdout


def test_one_disk_is_offered_without_the_local_clone_and_other_roles_none(tmp_path):
    """16/10: one disk is still a target for "restore from the server" -- the
    choice is shown, the local clone is not offered (boot.local=0); no disk at
    all is no choice."""
    for d in ("one", "two", "none"):
        (tmp_path / d).mkdir()
    one = run(tmp_path / "one", 'list_drawers() { echo sda; }; D_ROLE=cloner; local_clone_arm; '
              'test -f "$LC/screen" && echo SHOWN || echo HIDDEN; echo "local=$(cat "$LC/boot.local")"')
    assert "SHOWN" in one.stdout and "local=0" in one.stdout, one.stdout
    two = run(tmp_path / "two", 'list_drawers() { echo sda; echo sdb; }; D_ROLE=cloner; local_clone_arm; echo "local=$(cat "$LC/boot.local")"')
    assert "local=1" in two.stdout, two.stdout
    none = run(tmp_path / "none", 'list_drawers() { :; }; D_ROLE=cloner; local_clone_arm; '
               'test -f "$LC/screen" && echo SHOWN || echo HIDDEN')
    assert "HIDDEN" in none.stdout and "no disks" in none.stdout, none.stdout
    station = run(tmp_path, 'list_drawers() { echo sda; echo sdb; }; D_ROLE=classroom; local_clone_arm; '
                  'test -f "$LC/screen" && echo SHOWN || echo HIDDEN')
    assert "HIDDEN" in station.stdout


def test_the_choice_is_offered_once_a_boot(tmp_path):
    out = run(tmp_path, BOOT + 'local_clone_arm; ( sleep 1; echo server > "$LC/boot.decision" ) & local_clone_offer; '
              'local_clone_arm; test -f "$LC/screen" && echo AGAIN || echo ONCE; kill $_gui_pid', LC_BOOT_S="20")
    assert "'from the server' chosen" in out.stdout, out.stdout
    assert "ONCE" in out.stdout, out.stdout


# --- the clicks, as the bridge takes them (lcgui.sh lc_gui_dispatch) ------------

DISP = ('mkdir -p "$LC/inv"; for d in sda sdb sdc; do echo "1|$d|m|s|1|ok|ok|W|1|1|1|1|" > "$LC/inv/$d.line"; done; '
        'try() { token=$1; lc_gui_dispatch && echo "ok $1" || echo "refused $1"; }; ')


def test_boot_decisions_only_on_the_boot_screen(tmp_path):
    out = run(tmp_path, DISP + 'echo boot > "$LC/screen"; echo 1 > "$LC/boot.local"; try boot-local; cat "$LC/boot.decision"; '
              'echo local > "$LC/screen"; echo idle > "$LC/phase"; try boot-server')
    assert "ok boot-local" in out.stdout and "\nlocal\n" in "\n" + out.stdout
    assert "refused boot-server" in out.stdout


def test_a_start_names_inventoried_disks_only(tmp_path):
    out = run(tmp_path, DISP + 'echo local > "$LC/screen"; echo idle > "$LC/phase"; '
              'try "lc-start|sdc|sda,sdb|0"; cat "$LC/start.req"; rm -f "$LC/start.req"; '
              'try "lc-start|sdc|sdz|0"; try "lc-start|sdc|sdc|0"; try "lc-start|sdc|sda;rm|0"; '
              'try "lc-start|sdc||0"; try "lc-start|sdc|sda|2"; try "lc-measure|../x"; '
              'echo copying > "$LC/phase"; try "lc-start|sdc|sda|0"; try lc-stop; try lc-poweroff; '
              'echo done > "$LC/phase"; try lc-poweroff; try lc-back')
    lines = out.stdout.splitlines()
    assert "ok lc-start|sdc|sda,sdb|0" in lines and "sdc|sda,sdb|0" in lines
    for refused in ("lc-start|sdc|sdz|0", "lc-start|sdc|sdc|0", "lc-start|sdc|sda;rm|0", "lc-start|sdc||0",
                    "lc-start|sdc|sda|2", "lc-measure|../x"):
        assert f"refused {refused}" in lines, (refused, lines)
    assert lines.count("refused lc-start|sdc|sda|0") == 1          # not while copying
    assert "ok lc-stop" in lines and lines.count("refused lc-poweroff") == 1 and lines.count("ok lc-poweroff") == 1
    assert "refused lc-back" in lines                               # only from the source screen


# --- one partition's evidence (lccopy.sh lc_judge) ----------------------------------

SHA = "a" * 16   # xxh64, as fanout -H / --xxh64 write it (#1438)


def _judge(tmp_path, line: str, rc: str, tee: str, sha: str) -> str:
    t = 'd=$RUN_DIR/targets/sda; mkdir -p "$LC/run"; target_init sda 100; ata_capture() { :; }; '
    t += f'echo "$d/feed {line}" > "$LC/run/fanout.out"; echo {rc} > $d/pipeline.rc; echo {tee} > $d/tee.rc; '
    t += f'echo "{sha}  -" > $d/sha.out; lc_judge sda 3 {SHA}; echo "state=$(cat $d/state)"; cat $d/error 2>/dev/null; cat $d/lc_ok 2>/dev/null; true'
    out = run(tmp_path, t)
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_a_partition_counts_only_when_all_four_records_agree(tmp_path):
    good = _judge(tmp_path, "ok", 0, 0, SHA)
    assert "state=waiting" in good and good.strip().endswith("3"), good     # lc_ok gained partition 3


@pytest.mark.parametrize("line,rc,tee,sha,why", [
    ("ok", 0, 0, "b" * 16, "שונה מהמקור"),                 # what the target took is not what was read
    ("ok", 0, 0, "a" * 32, "לא חושב"),                    # an md5-shaped value is not an xxh64 (#1438)
    ("ok", 0, 0, "a" * 15, "לא חושב"),                    # one digit short: not a whole hash
    ("ok", 0, 0, "A" * 16, "לא חושב"),                    # fanout writes lowercase; anything else is not its output
    ("ok", 0, 0, "", "לא חושב"),                          # no hash = no evidence
    ("ok", 1, 0, SHA, "partclone rc=1"),
    ("ok", 0, 1, SHA, "ממסר rc=1"),
    ("failed write: Input/output error", 0, 0, SHA, "שגיאת כתיבה"),
    ("", 0, 0, SHA, "אין דיווח"),
])
def test_any_missing_or_wrong_record_fails_the_target(tmp_path, line, rc, tee, sha, why):
    out = _judge(tmp_path, line, rc, tee, sha)
    assert "state=failed" in out and why in out, out


# --- one partition through the pipeline (lccopy.sh lc_stream) --------------------
#
# fanout's own -H/-P/--xxh64 (#1438) are stubbed here with the same contract:
# the stand-in hash is the first 16 hex digits of md5 -- the shape of an
# XXH64, so the wiring is checked; the real XXH64 is tests/test_fanout.py's.

STREAM_STUBS = {
    "fanout": r'''#!/bin/sh
if [ "$1" = --xxh64 ]; then h=$(md5sum | cut -c1-16) || exit 2; echo "$h"; exit 0; fi
H=; P=
while :; do case "$1" in -H) H=$2; shift 2 ;; -P) P=$2; shift 2 ;; *) break ;; esac; done
shift
data="$STUB_BOX/stream.$$"; cat > "$data"
for f in "$@"; do
    case "$f" in *"/${STUB_CORRUPT:-none}/"*) { cat "$data"; echo x; } > "$f" ;; *) cat "$data" > "$f" ;; esac
    echo "$f ok"
done
[ -z "$P" ] || wc -c < "$data" | tr -d ' ' >> "$P"
[ -z "$H" ] || [ -n "$STUB_NO_HASH" ] || md5sum < "$data" | cut -c1-16 > "$H"
''',
    "partclone.dd": r'''#!/bin/sh
src=; out=
while [ $# -gt 0 ]; do case "$1" in -s) src=$2; shift 2 ;; -O) out=$2; shift 2 ;; *) shift ;; esac; done
if [ "$src" = - ]; then cat > "$out"; else cat "$src"; fi
''',
    # The pipeline before #1438 ran pv; present only so a negative control on
    # that code fails on the evidence and not on a missing tool.
    "pv": '#!/bin/sh\ncat\n',
}


def _stream(tmp_path: Path, **env) -> tuple[str, Path]:
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"
    dev.mkdir(parents=True)
    (dev / "sdc1").write_bytes(b"imagectl" * 4096)
    make = [f"mkdir -p {posix(stubs)!r}"]
    for name, body in STREAM_STUBS.items():   # from bash: a Windows-made file is not executable in Git Bash
        make += [f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB", f"chmod 0755 {posix(stubs)}/{name}"]
    t = "\n".join(make) + "\n"
    t += f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" STUB_BOX={posix(box)!r} DEVROOT={posix(dev)!r}; '
    t += 'mkdir -p "$LC/run"; ata_capture() { :; }; target_init sda 100; target_init sdb 100; '
    t += f'lc_stream 1 dd sdc sda sdb > {posix(box)}/stream.log 2>&1; echo "rc=$?"; '
    t += 'for d in sda sdb; do echo "$d=$(cat $RUN_DIR/targets/$d/state)|$(cat $RUN_DIR/targets/$d/lc_ok 2>/dev/null)|$(cat $RUN_DIR/targets/$d/error 2>/dev/null)"; done; '
    t += 'echo "sha=$(cat "$LC/run/source.sha" 2>/dev/null)"; echo "copied=$(cat "$LC/run/bytes.1" 2>/dev/null)"'
    out = run(tmp_path, t, timeout=120, WAIT_HELPER_S="20", WAIT_DRAWER_S="20", **env)
    assert out.returncode == 0, out.stderr
    return out.stdout, dev


def test_the_stream_takes_fanouts_hash_and_count_and_each_targets_own(tmp_path):
    out, dev = _stream(tmp_path)
    data = (dev / "sdc1").read_bytes()
    want = hashlib.md5(data).hexdigest()[:16]
    assert "rc=0" in out and "sda=waiting|1|" in out and "sdb=waiting|1|" in out, out
    assert f"\nsha=1|{want}\n" in out, out                       # the source hash is fanout -H's file, whole
    assert f"copied={len(data)}" in out, out                     # the counter is fanout -P's file, one per partition
    assert (dev / "sda1").read_bytes() == data == (dev / "sdb1").read_bytes()


def test_a_target_that_took_other_bytes_fails_alone(tmp_path):
    out, _ = _stream(tmp_path, STUB_CORRUPT="sdb")
    assert "rc=0" in out and "sda=waiting|1|" in out, out
    assert "sdb=failed||" in out and "שונה מהמקור" in out, out


def test_no_source_hash_is_no_evidence_for_any_target(tmp_path):
    """fanout -H writes nothing on a read error: "not computed", never a pass."""
    out, _ = _stream(tmp_path, STUB_NO_HASH="1")
    assert "rc=1" in out and "sha=\n" in out, out
    for d in ("sda", "sdb"):
        assert f"{d}=failed||" in out, out
    assert out.count("ה-xxh64 של המקור לא חושב") == 2, out


# --- the screen's records (lcgui.sh lc_gui_state) ------------------------------------

def test_the_state_file_carries_the_verdicts_against_the_measured_source(tmp_path):
    # #1354's rule needs the source's partitions, which this plumbing check has none of: 0 = off.
    out = run(tmp_path, 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC/inv" "$LC/measure"; echo 0 > "$RUN_DIR/min_free_gb"; '
              'echo \'{"name":"CLONER-02","server_name":"imagectl-ta","server_version":"v1.2.3"}\' > "$RUN_DIR/response.json"; '
              'export AGENT_VERSION=v1.2.2; '
              'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo idle > "$LC/phase"; '
              'echo "1|sda|A|s1|250000000000|ok|ok||0|0|-1|0|אין עליו טבלת מחיצות" > "$LC/inv/sda.line"; '
              'echo "3|sdc|C|s3|480000000000|ok|ok|Windows|4|1|480000000000|1|" > "$LC/inv/sdc.line"; '
              'echo "2|sdb|B|s2|500000000000|fail|health_failed||0|0|-1|0|" > "$LC/inv/sdb.line"; '
              'echo sdc > "$LC/source"; echo "done|200000000000|" > "$LC/measure/sdc"; '
              'lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    disks = [l for l in lines if l.startswith("lc_disk=")]
    assert [l.split("|")[0] for l in disks] == ["lc_disk=1", "lc_disk=2", "lc_disk=3"], disks   # by port
    assert {"machine_name=CLONER-02", "server_name=imagectl-ta", "server_version=v1.2.3",
            "agent_version=v1.2.2"} <= set(lines)
    assert "lc_src=sdc|done||" in lines          # #1443: the copy's size not measured yet = empty, not 0
    assert "lc_fit=sda|shrink|200000000000|" in lines and "lc_fit=sdb|fits|480000000000|" in lines


# --- the wiring ----------------------------------------------------------------------

def test_the_choice_wraps_the_idle_cloner_and_nothing_else():
    """Only wait_poll (an idle cloner) offers the choice; a round that is open
    or running goes on exactly as before (the server path is unchanged)."""
    agent = (REPO / "agent" / "imagectl-agent").read_text(encoding="utf-8")
    assert agent.count("local_clone_arm") == 1 and agent.count("local_clone_offer") == 1
    block = agent[agent.index("        wait_poll)"):agent.index("        build_console)")]
    assert block.index("local_clone_arm") < block.index("gui_cloner_parent") < block.index("local_clone_offer") \
        < block.index("poll_sleep"), block


def test_the_kernel_command_line_is_not_read():
    """Principle 2: nothing of the local clone travels on the command line."""
    for name in ("lcinv", "lcsize", "lcplan", "lcsnap", "lctune", "lccopy", "lcraw", "localclone", "lcwait", "lcgui"):
        text = (LIB / f"{name}.sh").read_text(encoding="utf-8")
        assert "/proc/cmdline" not in text and "imagectl.mode" not in text and "parse_cmdline" not in text, name


def test_nothing_is_written_before_lc_run():
    """The read-only layer (inventory, measurement) holds no writing tool."""
    text = (LIB / "lcinv.sh").read_text(encoding="utf-8")
    code = "\n".join(l.split("#")[0] for l in text.splitlines())
    for tool in ("ntfsresize -s", "sgdisk -n", "sgdisk -d", "sgdisk -c", "--" + "zap", "mkswap", " -O ", "of=/dev"):
        assert tool not in code, tool
    assert re.search(r"mount -t \"\$2\" -o \"\$_lo_o\"", text) and "ro,noload" in text
    # #1443: the copy's size is measured by partclone with no target at all -- no -o/-O, never -r
    size = "\n".join(l.split("#")[0] for l in (LIB / "lcsize.sh").read_text(encoding="utf-8").splitlines())
    for tool in (" -O ", " -o ", "of=/dev", "dd ", "ntfsresize", "sgdisk", "mount"):
        assert tool not in size, tool
    assert re.findall(r'"\$_lz_pcl" (-\w+)', size) == ["-c"], "partclone runs once, in clone mode (to /dev/null)"
