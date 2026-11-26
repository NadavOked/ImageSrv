"""#2941, #2989, #3068 -- three small agent fixes, one test file.

* **#2941**: a machine that is not a cloner, with one internal disk the SMART gate refuses
  (red), reported `waiting` and not `failed`: `job_reset` writes `waiting`, and the gate's
  early return did not write `failed` (the same shape as #2910 on the drawers' path).
* **#2989**: `read -r x < "$f" 2>/dev/null` -- the redirections run left to right, so a missing
  `$f` fails BEFORE `2>/dev/null` is in place and busybox ash prints "can't open ..." on the
  boot screen. The `2>` goes first.
* **#3068**: the text screen's "no contact with the server since HH:MM:SS" was UTC while the GUI
  shows local time. It now takes the zone the server sent (`response.json` `.tz`); with no zone
  it says `UTC` next to the time.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import datetime, timedelta, timezone

import hygiene
from native import requires_native
from test_agent import AGENT, REPO, posix
from test_final_report import native_tools
from test_hold_and_capture import DEAD_BEAT, hold_probe

BUSYBOX = os.environ.get("IMAGESRV_BUSYBOX") or shutil.which("busybox")

# --- #2941 ---------------------------------------------------------------------------------

# The #2941 fix (a single red disk is reported `failed`, not `waiting`, in do_restore) was in the station
# multicast restore that #3173 removed: do_restore now fails by name before it touches the disk. Its test went
# with it (merge of main into this branch).


# --- #2989 ----------------------------------------------------------------------------------

#: a `read` whose input redirection comes BEFORE `2>/dev/null` and whose file may be missing.
#: The ones below are guarded by `[ -r f ]` / `[ -f f ]` on the line above, or the file was just
#: written by the step before; neighborfill.sh is a chunk-engine file another writer owns.
LATE_REDIRECT = re.compile(r'\bread\b[^\n]*?<\s*"?[^\s"]+"?\s+2>/dev/null')
GUARDED = {
    "lib/core/datareport.sh", "lib/gui/clonergui.sh", "lib/hw/diskused.sh",
    "lib/restore/readback.sh", "lib/restore/neighborfill.sh",
}


def test_no_unguarded_read_puts_its_2_redirect_after_the_input_file():
    bad = []
    for path in sorted(AGENT.rglob("*")):
        if not path.is_file() or path.suffix not in ("", ".sh") or path.name == "fanout":
            continue
        rel = path.relative_to(AGENT).as_posix()
        if rel in GUARDED:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            if LATE_REDIRECT.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:110]}")
    assert not bad, "a missing file prints 'can't open' on the screen:\n" + "\n".join(bad)


@requires_native(("busybox", BUSYBOX), posix=True, why="the agent runs under busybox ash")
def test_monitor_probes_say_nothing_on_stderr_when_the_marker_files_are_missing(tmp_path):
    """Under busybox ash, with no monitor.pid and no monitor.waiter: both probes are quiet."""
    run = tmp_path / "run"
    run.mkdir()
    script = tmp_path / "s.sh"
    out = tmp_path / "out"
    script.write_text(
        f'RUN_DIR={posix(run)!r}; log() {{ :; }}\n'
        f'. {posix(AGENT)}/lib/core/monitor.sh\n'
        f'_monitor_alive; echo "ALIVE rc=$?"\n'
        f'IMAGESRV_MONITOR=1; D_ROLE=classroom; monitor_start >/dev/null; echo "START rc=$?"\n'
        f'read -r _x 2>/dev/null < {posix(run)}/nothing; echo "PLAIN rc=$?"\n',
        encoding="utf-8", newline="\n")
    with out.open("w", encoding="utf-8") as sink:
        hygiene.run_group([BUSYBOX, "ash", posix(script)], stdout=sink, stderr=subprocess.STDOUT,
                          stdin=subprocess.DEVNULL, cwd=str(REPO), timeout=45)
    text = out.read_text(encoding="utf-8", errors="replace")
    assert "can't open" not in text and "No such file" not in text, text
    assert "ALIVE rc=1" in text, text   # no supervisor known: the answer is "no", said quietly


# --- #3068 ----------------------------------------------------------------------------------

def _printed_time(out: str) -> str:
    m = re.search(r"no contact with the server since (\d\d:\d\d:\d\d)( UTC)?\.", out)
    assert m, out
    return m.group(1) + (m.group(2) or "")


@native_tools
def test_the_no_contact_time_is_in_the_zone_the_server_sent(tmp_path):
    """A fixed POSIX zone five hours ahead of UTC: the printed hour is UTC + 5, with no label."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "response.json").write_text('{"tz": "XYZ-5"}', encoding="utf-8")
    before = datetime.now(timezone.utc)
    shown = _printed_time(hold_probe(tmp_path, DEAD_BEAT)["out"])
    after = datetime.now(timezone.utc)
    assert not shown.endswith("UTC"), shown
    hours = {(t + timedelta(hours=5)).strftime("%H") for t in (before, after)}
    assert shown[:2] in hours, (shown, hours)


@native_tools
def test_with_no_zone_known_the_time_says_utc(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "response.json").write_text("{}", encoding="utf-8")
    shown = _printed_time(hold_probe(tmp_path, DEAD_BEAT)["out"])
    assert shown.endswith(" UTC"), shown
