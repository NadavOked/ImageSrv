"""#1966 -- "איזו מגירה זו?": the drawer's LED blinks from READS of its disk.

Nadav 22/10: read only, ~15 s, works without a port mapping, never while that
disk is written. Verified 22/10: the request is taken from the GUI's file and
read with dd to /dev/null only; every chunk is at a new offset (the same 16 MiB
again comes from the page cache and the LED stays dark -- the first version did
exactly that); a disk that is not a drawer is refused; and the round's stop
WAITS for the reader, so no read is running when the first write starts.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_cloner_gui import GUI, _build_gui, _pkgconfig, _png
from test_timeouts import run_sh

pytestmark = requires_native(("bash", BASH))


def _env(tmp_path: Path, dd_body: str = "") -> str:
    root, run, bindir = tmp_path / "root", tmp_path / "run", tmp_path / "bin"
    for d in ("sda", "sdb"):
        (root / "sys/block" / d).mkdir(parents=True, exist_ok=True)
        (root / "sys/block" / d / "size").write_text("204800\n", encoding="utf-8")   # 100 MiB
    (run / "gui").mkdir(parents=True, exist_ok=True)
    bindir.mkdir(exist_ok=True)
    dd = bindir / "dd"
    dd.write_text(f'#!/bin/sh\necho "$*" >> {posix(tmp_path / "dd.calls")!r}\n{dd_body}\n',
                  encoding="utf-8", newline="\n")
    run_sh(f"chmod +x {posix(dd)!r}")
    return (f"export PATH={posix(bindir)!r}:$PATH SYSROOT={posix(root)!r} DEVROOT=/fake-dev "
            f"RUN_DIR={posix(run)!r} GUI_DIR={posix(run / 'gui')!r} LOG_FILE={posix(tmp_path / 'log')!r} D_ROLE=cloner; "
            f". {posix(AGENT)}/lib/common.sh; . {posix(AGENT)}/lib/waits.sh; . {posix(AGENT)}/lib/identify.sh; "
            "list_drawers() { echo sda; echo sdb; }; ")


def _calls(tmp_path: Path) -> list[str]:
    p = tmp_path / "dd.calls"
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


def test_identify_only_reads_and_each_chunk_is_a_new_offset(tmp_path):
    run_sh(_env(tmp_path) + 'IDENTIFY_SECONDS=2; identify_disk sda; echo "rc=$?"')
    calls = _calls(tmp_path)
    assert len(calls) >= 3, calls
    for c in calls:
        assert re.fullmatch(r"if=/fake-dev/sda of=/dev/null bs=1048576 count=16 skip=\d+", c), c
    skips = [int(c.rsplit("=", 1)[1]) for c in calls]
    assert skips[:3] == [0, 16, 32], skips          # never the same cached chunk twice in a row
    assert max(skips) + 16 <= 100, skips             # wraps inside the disk


def test_a_disk_that_is_not_a_drawer_is_refused_unread(tmp_path):
    out = run_sh(_env(tmp_path) + 'identify_disk sdz; echo "rc=$?"')
    assert out.strip().endswith("rc=1") and _calls(tmp_path) == []


def test_identify_stops_when_its_disk_is_being_written(tmp_path):
    state = tmp_path / "run/targets/sda/state"
    body = f'mkdir -p {posix(state.parent)!r}; echo writing > {posix(state)!r}'
    out = run_sh(_env(tmp_path, body) + 'IDENTIFY_SECONDS=30; identify_disk sda; echo "rc=$?"', timeout=20)
    assert out.strip().endswith("rc=1") and len(_calls(tmp_path)) == 1, _calls(tmp_path)


def test_round_stop_waits_for_the_reader_before_returning(tmp_path):
    """The watcher reads sda (each dd takes 1 s). identify_watch_stop -- what
    do_restore_drawers calls before anything else -- must return only when no
    dd is left: the marker written after the stop sees no dd after it."""
    env = _env(tmp_path, f'sleep 1; echo end >> {posix(tmp_path / "dd.calls")!r}')
    out = run_sh(env + f"""
echo sda > "$GUI_DIR/identify-request"
IDENTIFY_SECONDS=60; identify_watch_start
i=0; while [ ! -s {posix(tmp_path / 'dd.calls')!r} ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
identify_watch_stop; echo STOPPED >> {posix(tmp_path / 'dd.calls')!r}
sleep 2; [ -e "$GUI_DIR/identify-request" ] && echo LEFT; echo "rc=$?"
""", timeout=40)
    calls = _calls(tmp_path)
    assert "STOPPED" in calls, calls
    after = calls[calls.index("STOPPED") + 1:]
    assert after == [], f"reads after the stop returned: {after}"
    assert calls[calls.index("STOPPED") - 1] == "end", calls    # the last read had ENDED
    assert "LEFT" not in out


def test_the_request_is_taken_once_and_only_the_named_disk_is_read(tmp_path):
    env = _env(tmp_path)
    run_sh(env + """
echo sdb > "$GUI_DIR/identify-request"
IDENTIFY_SECONDS=1; identify_watch_start
i=0; while [ -e "$GUI_DIR/identify-request" ] && [ $i -lt 50 ]; do sleep 0.1; i=$((i+1)); done
sleep 1.5; identify_watch_stop
""", timeout=30)
    calls = _calls(tmp_path)
    assert calls and all(c.startswith("if=/fake-dev/sdb ") for c in calls), calls


@requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui is built on the lab only",
)
def test_the_standby_screen_with_an_unmapped_disk_builds_and_renders(tmp_path):
    """The button's branch is drawn for idle disks, one of them on port 0 (no
    mapping). STRUCTURAL, not a click: the GUI has no event replay. The hit is
    the idle index -- BASE + port with `id > BASE` dropped the port-0 disk."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    state.write_text("cloner=\ndrawer_count=2\n"
                     "cdisk=0|sda|Samsung SSD 860 EVO|500107862016|passed||6|6|0|0||clean|\n"
                     "cdisk=2|sdb|WDC WD5000|500107862016|passed||3|6|0|2||clean|\n"
                     "message=x|y\n", newline="\n")   # --png draws every card: message needs its title
    _png(binary, tmp_path / "card", state)
    assert (tmp_path / "card-cloner-light.png").exists()
    src = (GUI / "src/screens_cloner.c").read_text(encoding="utf-8")
    main = (GUI / "src/main.c").read_text(encoding="utf-8")
    assert "HIT_IDENTIFY_BASE + (int)(entries[i].idle - s->idisks)" in src
    assert "id >= HIT_IDENTIFY_BASE && id < HIT_IDENTIFY_BASE + MAX_DRAWERS" in main
