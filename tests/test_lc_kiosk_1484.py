"""#1484 -- metal, cloner 2, 16/10: on the local clone screen the agent log
filled every second with

    /usr/bin/imagesrv-kiosk: line 244: min_free_gb: not found
    sh: bad number
    /usr/bin/imagesrv-kiosk: line 88: can't open .../lc/run/src.base: no such file
    /usr/bin/imagesrv-kiosk: line 88: can't open .../lc/run/bytes.raw: no such file

1. The kiosk's load chain (``gui_libs``) did not carry ``diskfit.sh``, so
   ``lc_min_free`` (lcinv.sh) ran ``[ "$(min_free_gb)" -gt 0 ]`` on nothing --
   ``bad number`` -- and fell through to ``ok``: the screen drew "fits"
   without the #1354 free-space rule ever being checked (principle 5).
   And a value that is not a number is "not checked", whoever produced it.
2. ``lc_gui_run`` read ``src.base`` (written when the first partition ends)
   and ``bytes.raw`` (when one begins) before either existed. Not started is
   a state, said explicitly -- 0 bytes copied -- not a failed read.

The kiosk chain here is the real one (``gui_libs``), not a test prelude.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_cloner_gui import AGENT, BASH, env, posix, sh
from test_local_clone_agent import run
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH))


def _kiosk_state(tmp_path: Path, min_free: str) -> tuple[list[str], str]:
    """A run that failed before any partition was copied, its result on the
    screen, one source with a fitting target -- drawn by the kiosk chain."""
    r = tmp_path / "run"; lc = r / "lc"; gui = tmp_path / "gui"
    for d in (lc / "inv", lc / "measure", lc / "run", r / "targets" / "sdb", gui):
        d.mkdir(parents=True, exist_ok=True)
    files = {
        lc / "screen": "local", lc / "phase": "done", lc / "inv.state": "done", lc / "source": "sda",
        lc / "inv" / "sda.line": "1|sda|A|s1|480000000000|ok|ok|Windows|4|1|400000000000|1|",
        lc / "inv" / "sdb.line": "2|sdb|B|s2|500000000000|ok|ok||0|0|-1|0|",
        lc / "measure" / "sda": "done|200000000000|",
        lc / "run" / "src": "sda", lc / "run" / "targets": "sdb",
        lc / "run" / "started": "1000", lc / "run" / "ended": "1060",
        r / "targets" / "sdb" / "state": "failed", r / "targets" / "sdb" / "error": "x",
        r / "targets" / "sdb" / "base": "0", r / "targets" / "sdb" / "bytes.raw": "",   # target_init's own
        r / "min_free_gb": min_free,
    }
    for path, text in files.items():
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    sysroot = tmp_path / "sys"; sysroot.mkdir()
    out = sh(env(r, gui) + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; lc_gui_state')
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text(encoding="utf-8").splitlines(), out.stderr


def test_the_kiosk_chain_draws_a_run_not_started_without_errors(tmp_path):
    lines, err = _kiosk_state(tmp_path, "0")
    for noise in ("not found", "bad number", "src.base", "bytes.raw"):
        assert noise not in err, err
    run_line = next(l for l in lines if l.startswith("lc_run="))
    assert run_line.split("|")[2] == "0", run_line          # copied: none yet, said as 0


def test_the_kiosk_checks_the_free_space_rule_and_never_folds_it_to_fits(tmp_path):
    # 20 GB is the rule; the source's layout was not read -- "not checked", never "fits"
    lines, err = _kiosk_state(tmp_path, "20")
    assert "not found" not in err and "bad number" not in err, err
    fit = next(l for l in lines if l.startswith("lc_fit=sdb|"))
    assert fit.startswith("lc_fit=sdb|unchecked|"), fit


def test_a_minimum_that_is_not_a_number_is_not_checked_not_ok(tmp_path):
    out = run(tmp_path, 'mkdir -p "$LC/inv"; min_free_gb() { echo; }; '
              'lc_min_free sda 500000000000 "$LC/inv/sda.parts"')
    assert out.returncode == 0, out.stderr
    assert out.stdout.startswith("unchecked|"), out.stdout
