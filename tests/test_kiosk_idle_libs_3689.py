"""#3689 -- the kiosk writes cdisk= fields 18-19 (the waiting-screen write check), so the kiosk's own load chain must carry it.

`imagesrv-kiosk` is a separate process from `imagesrv-agent` (guibridge.sh `gui_libs`: "nothing imagesrv-agent sourced is
here unless listed"). The write check runs in the agent; the screen's state file is written by the kiosk. On the metal
(cloner 2, 18/11, LabRoot 49d64a9f6) `gui_libs` did not load idlecheck.sh, idlebrain.sh or writecheck.sh, so
`command -v idle_wc_field ... || _cg_wc="|"` (clonergui.sh) wrote an empty field 18 for every disk: the red "slow", the
"checking" and the brain's history line never reached the screen, whatever the disk did.

Here the kiosk's own chain (`gui_libs`, not the test's PRELUDE) runs `cloner_gui_state` over disks whose check results
are on disk the way the agent leaves them (`$RUN_DIR/idlecheck/res.<dev>/`). The positive evidence is the value read:
`ok|`, `slow|<reason>`, `history|<the brain's line>`, `unchecked|<why>`, `checking|` -- never the default `|`.
The second test is the half that hid the finding: a chain WITHOUT the libs says so, once, on stderr.

**Negative control:** `agent/lib/gui/guibridge.sh` and `agent/lib/gui/clonergui.sh` back at the base (`9ab7a1a4a`).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from native import requires_native
from agentlib import flat_lib

from test_cloner_gui import BASH, IDLE_SGDISK, PORTS, PRELUDE, AGENT, env, posix, sh

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

#: dev -> (check state, MB/s of the last half, MB/s of the first half, reason). None = no result yet (the scan has not reached it)
RESULTS = {
    "sda": ("ok", "216", "213", "כתיבה מתמשכת של 256MiB: 216 MB/s"),   # APS on the metal: passes the 74 floor
    "sdb": ("ok", "43", "43", "כתיבה מתמשכת של 256MiB: 43 MB/s"),      # slow: under the floor
    "sdc": ("unchecked", "", "", "יש מחיצה מחוברת בדיסק"),
    "sdd": None,
}


def _kiosk_state(tmp_path: Path, chain: str) -> tuple[list[str], str, Path]:
    """The waiting cloner screen over four fake disks, in the chain `chain` ("gui_libs" or the PRELUDE). cdisk= lines, stderr, run dir."""
    run = tmp_path / "run"; run.mkdir()
    gui = tmp_path / "gui"; gui.mkdir()
    box = tmp_path / "box"; box.mkdir()
    sysroot = tmp_path / "sysroot"
    (run / "smart").mkdir()
    for dev, res in RESULTS.items():
        base = sysroot / "sys" / "block" / dev
        (base / "device").mkdir(parents=True)
        (base / "queue").mkdir()
        for name, body in (("size", "1000"), ("ro", "0"), ("queue/rotational", "0"), ("device/model", f"Model {dev}"),
                           ("device/serial", f"S-{dev}")):
            (base / name).write_text(body + "\n", newline="\n")
        (run / "smart" / f"{dev}.v").write_text("ok ok 0 0 0 0\n", newline="\n")
        if res is None:
            continue
        state, mbps, first, reason = res
        d = run / "idlecheck" / f"res.{dev}"
        d.mkdir(parents=True)
        for name, body in (("key", f"S-{dev}|1000"), ("reason", reason), ("mbps", mbps), ("first", first), ("state", state)):
            (d / name).write_text(body, newline="\n")
    # the hello answer: the floor, and the brain's decision on exactly sda's result (history: it fell behind last wave, passes now)
    (run / "response.json").write_text(json.dumps({"schema": 1, "writecheck_min_mbps": 74, "write_decisions": [
        {"serial": "S-sda", "verdict": "INFO", "state": "ok", "mbps": 216, "first_mbps": 213,
         "reason": "פיגר בגל הקודם (55MB/s, 17/11); הבדיקה עכשיו תקינה"}]}, ensure_ascii=False), encoding="utf-8")
    script = (
        f"mkdir -p {posix(box)}/bin\n"
        f"cat > {posix(box)}/bin/sgdisk <<'STUB'\n{IDLE_SGDISK.format(box=posix(box))}STUB\n"
        f"chmod 0755 {posix(box)}/bin/sgdisk\n"
        f'export PATH="$(cd {posix(box)}/bin && pwd):$PATH"\n'
        + env(run, gui)
        + f"export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; "
        + chain
        + 'disk_port() { echo 1; }; '
        + "cloner_gui_state && cloner_gui_state"   # twice: a warning is once per shell, not once a second
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("cdisk=")], out.stderr, run


def test_the_kiosk_chain_carries_the_waiting_screen_write_check(tmp_path):
    """The five words of fields 18-19, read through the kiosk's own chain; and the brain's decision file, which only the kiosk's refresh writes."""
    lines, err, run = _kiosk_state(tmp_path, f". {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; ")
    assert "not found" not in err and "not in the kiosk load chain" not in err, err
    by_dev = {l.split("|")[1]: l.split("|")[-2:] for l in lines}
    assert by_dev["sda"] == ["history", "פיגר בגל הקודם (55MB/s, 17/11); הבדיקה עכשיו תקינה"], by_dev   # the brain's line on exactly this result (idlebrain.sh)
    assert by_dev["sdb"] == ["slow", "איטי — 43 MB/s, מתחת לסף 74 MB/s"], by_dev                       # the check's own word, the floor of the hello
    assert by_dev["sdc"] == ["unchecked", "יש מחיצה מחוברת בדיסק"], by_dev                              # not checked is its own state, with its reason
    assert by_dev["sdd"] == ["checking", ""], by_dev                                                   # no result yet: checking, never an empty field
    assert (run / "write_decisions").read_text(encoding="utf-8").splitlines()[0] == "ok"                # the kiosk refreshed the decisions itself


def test_a_chain_without_the_libs_says_so_once(tmp_path):
    """The silence was the bug: `|| _cg_wc="|"` wrote an empty field and the log said nothing. Now the field is still empty
    (nothing to read, nothing invented -- principle 5) but stderr names the missing chain, exactly once for four disks run twice."""
    lines, err, _ = _kiosk_state(tmp_path, PRELUDE + PORTS)
    assert all(l.split("|")[-2:] == ["", ""] for l in lines) and len(lines) == 4, lines
    assert err.count("idle_wc_field is not in the kiosk load chain") == 1, err
