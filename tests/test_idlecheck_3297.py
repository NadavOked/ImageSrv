"""#3297 -- the write check holds an open fd on the disk's node for the whole check and put-back.

Every ``dd`` opens ``/dev/sdX`` again. A disk pulled and another one pushed in between two ``dd``s
could get the same ``sdX`` and take the pattern. An fd that stays open on the node makes the kernel
keep that name (measured on metal, 22/11: without the fd the letter comes back, with it it does not),
so ``idle_wc_disk`` opens fd 9 before ``_id_run`` and closes it after, on every way out.

Proved from outside the code: each ``dd`` that writes the node is a shim that records whether the
fd it inherited is the node; a probe at the start of ``_id_run`` and a look after ``idle_wc_disk``
returns say the fd is held while it works and closed when it is done -- on every way out of ``_id_run``.
``/proc/<pid>/fd`` is Linux: the Testrunner has it.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from test_idlecheck_3268 import DD_SHIM, _make, _res, _run, _writes, pytestmark  # noqa: F401

LINUX_ONLY = pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="fd 9 is read from /proc/<pid>/fd (Linux)")

#: Records, per write to the node, whether the fd 9 this dd inherited IS that node. Then the real shim.
HOLD_DD = r'''#!/bin/bash
out=""; for a in "$@"; do case "$a" in of=*) out=${a#of=} ;; esac; done
case "$out" in "$DEVROOT"/*)
    if [ /proc/$$/fd/9 -ef "$out" ]; then echo HELD >> "$SHIM_LOG/fd"; else echo "NOT $out" >> "$SHIM_LOG/fd"; fi ;;
esac
exec "$SHIM_LOG/bin/dd0" "$@"
'''

#: At the start of _id_run (via the first guard it calls): is fd 9 the node? Then the look after the return.
PROBE = '''eval "$(declare -f _ic_guard | sed '1s/^_ic_guard/_ic_guard0/')"
_ic_guard() { if [ /proc/self/fd/9 -ef "$DEVROOT/sdx" ]; then echo HELD >> "$SHIM_LOG/probe"; else echo NOT >> "$SHIM_LOG/probe"; fi; _ic_guard0 "$@"; }
after() { if { : <&9; } 2> /dev/null; then echo "9=OPEN"; else echo "9=CLOSED"; fi; }
'''


def _hold_shims(t: Path) -> None:
    for name, body in (("dd0", DD_SHIM), ("dd", HOLD_DD)):
        p = t / "bin" / name
        p.write_text(body, newline="\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)


def _lines(t: Path, name: str) -> list[str]:
    f = t / name
    return f.read_text().split("\n")[:-1] if f.exists() else []


@LINUX_ONLY
def test_every_write_of_the_check_and_the_putback_goes_out_with_the_node_held(tmp_path):
    _make(tmp_path)
    _hold_shims(tmp_path)
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert _res(tmp_path)["state"] == "ok", (_res(tmp_path), out.stderr)
    seen = _lines(tmp_path, "fd")
    assert len(seen) >= 4, f"too few writes to prove anything (the check's and the put-back's): {seen}"
    assert set(seen) == {"HELD"}, f"a dd wrote the node without fd 9 on it: {seen}"


@LINUX_ONLY
@pytest.mark.parametrize("way_out", [
    pytest.param("", id="a clean check"),
    pytest.param("touch $FAKE_SRV/refuse;", id="the server refuses the copy"),
    pytest.param("_ic_check() { _ic_st=failed; _ic_why=x; _ic_mb=; return 1; };", id="the check cannot finish"),
    pytest.param("_ic_putback() { return 1; };", id="the put-back fails"),
])
def test_the_fd_is_held_while_it_works_and_closed_on_every_way_out(tmp_path, way_out):
    _make(tmp_path)
    out = _run(tmp_path, f"{PROBE}{way_out} idle_wc_disk sdx; after")
    assert out.returncode == 0, out.stderr
    assert _lines(tmp_path, "probe") == ["HELD"], f"fd 9 was not the node while _id_run worked: {_lines(tmp_path, 'probe')}"
    assert "9=CLOSED" in out.stdout, f"fd 9 outlived idle_wc_disk: {out.stdout!r}"


@LINUX_ONLY
def test_a_node_that_cannot_be_opened_is_not_checked_and_does_not_end_the_shell(tmp_path):
    _make(tmp_path)
    (tmp_path / "dev" / "sdx").unlink()   # the drawer is gone between the list and the open
    out = _run(tmp_path, "set -o posix; idle_wc_disk sdx; echo ALIVE; ls \"$RUN_DIR/idlecheck\"")   # posix: a failed `exec` redirection ends the shell
    assert "ALIVE" in out.stdout, f"the shell ended on the failed open: {out.stdout!r} {out.stderr!r}"
    r = _res(tmp_path)
    assert r.get("state") == "unchecked" and "לא נפתח להחזקה" in r["reason"], r
    assert _writes(tmp_path) == []
    assert "busy.sdx" not in out.stdout, "the busy lock stayed: the disk would never be checked again"
