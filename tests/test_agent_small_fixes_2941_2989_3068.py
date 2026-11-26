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
import pytest
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

#: a `read` whose input redirection comes BEFORE `2>/dev/null`: a missing file prints "can't open" first.
#: No exceptions (#2989): the reads guarded by `[ -r f ]` / `[ -f f ]` put the `2>` first too -- the guard
#: and the open are two steps, and the file can go between them.
LATE_REDIRECT = re.compile(r'\bread\b[^\n]*?<\s*"?[^\s"]+"?\s+2>/dev/null')


def _late_redirects(pattern, skip=()):
    bad = []
    for path in sorted(AGENT.rglob("*")):
        if not path.is_file() or path.suffix not in ("", ".sh") or path.name == "fanout":
            continue
        rel = path.relative_to(AGENT).as_posix()
        if rel in skip:
            continue
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.lstrip().startswith("#") and pattern.search(line):
                bad.append(f"{rel}:{n}: {line.strip()[:110]}")
    return bad


def test_no_read_puts_its_2_redirect_after_the_input_file():
    bad = _late_redirects(LATE_REDIRECT)
    assert not bad, "a missing file prints 'can't open' on the screen:\n" + "\n".join(bad)


#: #4134: the same shape for ANY command (`gzip -c < f > o 2>/dev/null`, `fanout --xxh64 < f 2>/dev/null`).
#: `read` (guarded above), `wc` and `tr` (#4131) have their own tests, so the command word is skipped for them.
#: A command word starts after a line start, `| ; & ( { ` or a keyword; its arguments may hold quoted text.
LATE_REDIRECT_ANY = re.compile(
    r"""(?:^|[|;&({`]|\b(?:if|until|while|then|do|else|elif)\s|!\s)\s*"""
    r"""(?!(?:if|until|while|then|do|else|elif)\s)(?P<cmd>[A-Za-z_./][^\s|;&<>()'"`=$]*)(?=\s)"""
    r"""(?:'[^']*'|"[^"]*"|[^|;&<()'"\n`])*?"""
    r"""<\s*(?:"[^"]*"|[^\s"|;&<>]+)(?:\s+>>?\s*(?:"[^"]*"|[^\s"|;&<>]+))*\s+2>/dev/null""")
OWN_TESTS = {"read", "wc", "tr"}


def _late_redirect_any(line: str) -> list[str]:
    return [m.group("cmd") for m in LATE_REDIRECT_ANY.finditer(line) if m.group("cmd") not in OWN_TESTS]


def test_no_command_puts_its_2_redirect_after_the_input_file():
    bad = []
    for path in sorted(AGENT.rglob("*")):
        if not path.is_file() or path.suffix not in ("", ".sh") or path.name == "fanout":
            continue
        rel = path.relative_to(AGENT).as_posix()
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if not line.lstrip().startswith("#") and _late_redirect_any(line):
                bad.append(f"{rel}:{n}: {line.strip()[:110]}")
    assert not bad, "a missing file prints 'cannot open' on the screen:\n" + "\n".join(bad)


@pytest.mark.parametrize("line,cmds", [
    ('''if gzip -c < "$a" > "$b" 2>/dev/null \\''', ["gzip"]),
    ('''x="$(fanout --xxh64 < "$2" 2>/dev/null)" = y''', ["fanout"]),
    ('''    sha256sum < "$f" 2>/dev/null | cut -d' ' -f1''', ["sha256sum"]),
    ('''r=$(head -c 4 < "$f" 2>/dev/null)''', ["head"]),
    ('''gzip -c 2>/dev/null < "$a" > "$b"''', []),
    ('''x=$(fanout --xxh64 2>/dev/null < "$2")''', []),
    ('''gzip -c < "$a" > "$b" | tail -n 1 2>/dev/null''', []),
    ('''n=$(wc -c < "$f" 2>/dev/null)''', []),
    ('''read -r x < "$f" 2>/dev/null''', []),
])
def test_the_any_command_pattern_sees_the_late_redirect_and_only_it(line, cmds):
    assert _late_redirect_any(line) == cmds


#: #4131: the same shape for `wc` and `tr` (`wc -c < f 2>/dev/null`). Nothing is exempt: the 2> goes first.
#: The arguments may hold quoted text (a `|` or `<` inside a quote does not end the command).
LATE_REDIRECT_WC_TR = re.compile(
    r"""\b(?:wc|tr)\b(?:'[^']*'|"[^"]*"|[^|;&<\n'"])*?<\s*(?:"[^"]*"|[^\s"|;&]+)\s+2>/dev/null""")


def test_no_wc_or_tr_puts_its_2_redirect_after_the_input_file():
    bad = _late_redirects(LATE_REDIRECT_WC_TR)
    assert not bad, "a missing file prints 'cannot open' on the screen:\n" + "\n".join(bad)


@pytest.mark.parametrize("line,hit", [
    ("""n=$(wc -c < "$f" 2>/dev/null)""", True),
    ("""x=$(tr '\\n' ' ' < "$f" 2>/dev/null)""", True),
    ("""tr -d '\\r\\n|' < "$f" 2>/dev/null""", True),
    ("""[ "$(wc -c < "$a/b.$i" 2>/dev/null | tr -d ' ')" = 4 ]""", True),
    ("""n=$(wc -c 2>/dev/null < "$f")""", False),
    ("""tr '\\r' '\\n' 2>/dev/null < "$LOG" > "$out\"""", False),
    ("""wc -l < "$f" | tr -d " " 2>/dev/null""", False),
])
def test_the_wc_tr_pattern_sees_the_late_redirect_and_only_it(line, hit):
    assert bool(LATE_REDIRECT_WC_TR.search(line)) is hit


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
