"""X16 / #3064 (Nadav 15/11) -- "HDD support" in the LOCAL clone, where there is no server.

The owner's decision: with the switch off (the default), an HDD is refused as a TARGET, with a
visible reason, and accepted as a SOURCE. The cloner has no manifest here, so the switch is the
last value the server sent it in a hello (``$RUN_DIR/hdd_support``, like ``min_free_gb``); none
stored = off. ``rotational`` that was not read is "not measured": judged as an SSD, with a log
line -- never silently (principle 5).

* the screen: the HDD target's verdict is ``short`` with the reason, which the native screen
  already draws red, "<reason>. לא ישמש כיעד." (``verdict_text``), and rules out of the targets;
* the run: ``lc_run`` refuses it by name before the source is even read, and ``lc_prepare_target``
  refuses it again right before the table is written.

**Negative control:** ``agent/lib/localclone/lcgui.sh``, ``localclone.sh``, ``lccopy.sh``,
``lcinv.sh``, ``agent/imagesrv-agent``, ``agent/lib/gui/guibridge.sh`` and
``lchdd.sh`` removed -> the verdict is the size's alone and an HDD target is written to. The hello's
``hdd_support`` is #3268's (``server/agent/hello.py``), not part of this change.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from agentlib import flat_lib
from native import requires_native
from test_cloner_gui import AGENT, BASH, env, posix, sh
from test_local_clone_agent import run
from test_room_precheck_432_65 import disk, drawers_hello
from test_server_room import CLONER1, room_server  # noqa: F401 -- room_server is a fixture

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")))

REFUSED = "HDD — תמיכה ב-HDD כבויה בהגדרות"
URL = "/api/console/settings"


def _stage(rot: dict[str, str | None], switch: str | None = None) -> str:
    """The inventory's evidence ($LC/inv/<dev>.rot, what lc_inv_disk reads from sysfs) and the
    stored switch, as shell: a value None writes no file at all."""
    out = 'LC="$RUN_DIR/lc"; mkdir -p "$LC/inv" "$LC/measure"; export LOG_FILE="$RUN_DIR/agent.log"; '
    for dev, value in rot.items():
        if value is not None:
            out += f"printf '%s\\n' {value!r} > \"$LC/inv/{dev}.rot\"; "
    if switch is not None:
        out += f'echo {switch} > "$RUN_DIR/hdd_support"; '
    return out


def _log(tmp_path: Path) -> str:
    path = tmp_path / "run" / "agent.log"
    return path.read_text(encoding="utf-8") if path.exists() else ""


# --- the switch: the last value the server sent --------------------------------------------


@pytest.mark.parametrize("answers,stored", [
    ([{"hdd_support": True}], "true"),
    ([{"hdd_support": False}], "false"),
    ([{"hdd_support": True}, {"min_free_gb": 20}], "true"),     # a short answer leaves the last value
    ([{"hdd_support": True}, {"hdd_support": None}], "true"),
    ([{"hdd_support": True}, {"hdd_support": False}], "false"),
    ([{"hdd_support": True}, {"hdd_support": "yes"}], ""),       # not true/false: off, and the file goes
    ([{"hdd_support": True}, {"hdd_support": 1}], ""),
    ([{"min_free_gb": 20}], ""),                                 # nothing ever sent: nothing stored
])
def test_the_switch_is_the_last_value_of_the_hello(tmp_path, answers, stored):
    script = _stage({})
    for i, answer in enumerate(answers):
        (tmp_path / f"a{i}.json").write_text(json.dumps(answer), encoding="utf-8")
        script += f'hdd_support_take {posix(tmp_path / f"a{i}.json")!r} > /dev/null; '   # log() echoes to stdout
    out = run(tmp_path, script + 'cat "$RUN_DIR/hdd_support" 2>/dev/null; true')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == stored, (out.stdout, out.stderr)
    if stored == "" and any("hdd_support" in a for a in answers):
        assert "hdd_support from the server is not true/false" in _log(tmp_path)


# --- the rule: an HDD target with the switch off ---------------------------------------------


@pytest.mark.parametrize("switch", [None, "false", "true", "garbage"])
@pytest.mark.parametrize("rot", ["1", "0", None, "", "bad"])
def test_only_an_hdd_with_the_switch_not_on_is_refused(tmp_path, rot, switch):
    out = run(tmp_path, _stage({"sdb": rot}, switch) + 'lc_hdd_refusal sdb; echo "|rc=$?"; lc_hdd_refusal sdb > /dev/null; true')   # the 2nd call's 1 = "not refused" is an answer, not the script's exit
    assert out.returncode == 0, out.stderr
    reason, rc = out.stdout.rsplit("|", 1)
    hdd_off = rot == "1" and switch != "true"           # no stored value = off, "garbage" = off
    assert (reason, rc.strip()) == ((REFUSED, "rc=0") if hdd_off else ("", "rc=1")), out.stdout
    unread = rot not in ("1", "0")
    # not read -> judged as an SSD and SAID, once per disk (the kiosk asks every second)
    assert _log(tmp_path).count("sdb: rotational not read") == (1 if unread else 0), _log(tmp_path)


def test_a_disk_nobody_inventoried_is_judged_as_an_ssd_with_a_line(tmp_path):
    """The positive control of the rule above: not read is not "an HDD", and not silent."""
    out = run(tmp_path, _stage({}, "false") + 'lc_hdd_refusal sdz; echo "rc=$?"')
    assert out.stdout.strip() == "rc=1", out.stdout
    assert "sdz: rotational not read (no file)" in _log(tmp_path)


# --- the screen and the run: the verdict -----------------------------------------------------


@pytest.mark.parametrize("rot,switch,want", [
    ("1", None, f"short||{REFUSED}"),            # the default: off
    ("1", "false", f"short||{REFUSED}"),
    ("1", "true", "fits|400|"),                  # on: the size's verdict, as before
    ("0", "false", "fits|400|"),
    (None, "false", "fits|400|"),                # not read: an SSD
])
def test_the_target_verdict_carries_the_reason(tmp_path, rot, switch, want):
    out = run(tmp_path, _stage({"sdb": rot}, switch) + 'lc_verdict_for() { echo "fits|400|"; }; lc_fit_verdict sda sdb')
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == want, out.stdout


def _kiosk_fit(tmp_path: Path, rot: str | None, switch: str | None) -> str:
    """The screen's line for target sdb, drawn by the kiosk's real load chain (gui_libs) -- the
    source sda measured, the target big enough, no free-space rule (min_free_gb 0)."""
    r = tmp_path / "run"; lc = r / "lc"; gui = tmp_path / "gui"
    for d in (lc / "inv", lc / "measure", gui):
        d.mkdir(parents=True, exist_ok=True)
    files = {
        lc / "screen": "local", lc / "phase": "idle", lc / "inv.state": "done", lc / "source": "sda",
        lc / "inv" / "sda.line": "1|sda|A|s1|480000000000|ok|ok|Windows|4|1|400000000000|1|",
        lc / "inv" / "sdb.line": "2|sdb|B|s2|500000000000|ok|ok||0|0|-1|0|",
        lc / "measure" / "sda": "none||", r / "min_free_gb": "0",
    }
    if rot is not None:
        files[lc / "inv" / "sdb.rot"] = rot
    if switch is not None:
        files[r / "hdd_support"] = switch
    for path, text in files.items():
        path.write_text(text + "\n", encoding="utf-8", newline="\n")
    sysroot = tmp_path / "sys"; sysroot.mkdir(exist_ok=True)
    out = sh(env(r, gui) + f'export SYSROOT={posix(sysroot)!r} LIB_DIR={posix(flat_lib())!r}; '
             f'. {posix(AGENT)}/lib/gui/guibridge.sh; gui_libs; lc_gui_state')
    assert out.returncode == 0, out.stderr
    assert "not found" not in out.stderr, out.stderr          # the kiosk's chain carries lchdd.sh
    return next(l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("lc_fit=sdb|"))


def test_the_screen_line_of_an_hdd_target_is_short_with_the_reason(tmp_path):
    assert _kiosk_fit(tmp_path, "1", None) == f"lc_fit=sdb|short||{REFUSED}"


def test_the_same_screen_with_the_switch_on_or_not_an_hdd_says_fits(tmp_path):
    """The positive controls: the line above is the rule's, not the harness's."""
    assert _kiosk_fit(tmp_path, "1", "true").startswith("lc_fit=sdb|fits|"), "on"
    assert _kiosk_fit(tmp_path, "0", "false").startswith("lc_fit=sdb|fits|"), "an SSD"
    assert _kiosk_fit(tmp_path, None, "false").startswith("lc_fit=sdb|fits|"), "not read"


# --- the run: refused by name, before any write; an HDD source is not asked -------------------


def _lc_run(tmp_path: Path, rot: dict[str, str | None], switch: str | None) -> dict[str, str]:
    """lc_run sdc -> "sda sdb", every verdict a fit and the run stopped at the source step."""
    script = (_stage(rot, switch)
              + 'for d in sda sdb sdc; do echo "x|$d" > "$LC/inv/$d.line"; done; echo sdc > "$LC/source"; : > "$LC/measure/sdc"; '
              'disk_serial() { printf "S-%s" "$1"; }; '
              'lc_inv_field() { case "$2" in 4) printf "S-%s" "$1" ;; 12) echo 1 ;; *) echo ;; esac; }; '
              'lc_verdict_for() { echo "fits||"; }; lc_measure() { :; }; '
              'smart_probe() { echo ok; }; '
              'lc_source_parts() { echo "PARTS" >> "$RUN_DIR/trace"; LC_ERROR=stopped-here; return 1; }; '
              'lc_end() { kill "$_lr_beat" 2>/dev/null; echo END >> "$RUN_DIR/trace"; }; ata_capture() { :; }; '
              'lc_run sdc "sda sdb" 0; '
              'for d in sda sdb; do echo "$d=$(cat "$RUN_DIR/targets/$d/state")|$(cat "$RUN_DIR/targets/$d/error")"; done')
    out = run(tmp_path, script)
    assert out.returncode == 0, out.stderr
    return dict(line.split("=", 1) for line in out.stdout.splitlines() if line[:3] in ("sda", "sdb"))


def test_the_run_fails_an_hdd_target_by_name_and_goes_on_with_the_rest(tmp_path):
    """sdb is an HDD, sdc (the source) is one too, sda is not. Off: sdb fails by its reason,
    sda goes on to the source step -- and the HDD source is read, never asked."""
    got = _lc_run(tmp_path, {"sda": "0", "sdb": "1", "sdc": "1"}, "false")
    assert got["sdb"] == f"failed|{REFUSED} — לא נכתב", got
    assert got["sda"].startswith("failed|stopped-here"), got
    assert "PARTS" in (tmp_path / "run" / "trace").read_text(encoding="utf-8")   # the source (an HDD) was read


def test_the_same_run_with_the_switch_on_writes_to_the_hdd(tmp_path):
    got = _lc_run(tmp_path, {"sda": "0", "sdb": "1", "sdc": "1"}, "true")
    assert got["sdb"].startswith("failed|stopped-here"), got


def test_prepare_refuses_an_hdd_before_the_table_and_a_fit_reaches_it(tmp_path):
    """lc_prepare_target is the last stop: nothing of the write path runs for the refused disk."""
    stubs = ('disk_fits() { echo DISK_FITS >> "$RUN_DIR/trace"; return 0; }; '
             'apply_gpt() { echo APPLY_GPT >> "$RUN_DIR/trace"; return 0; }; '
             'expand_last() { return 0; }; reread_table() { return 0; }; ata_capture() { :; }; ')
    for rot, switch, refused in (("1", "false", True), ("1", None, True), ("1", "true", False), ("0", "false", False)):
        run_dir = tmp_path / f"{rot}{switch}"
        run_dir.mkdir()
        out = run(run_dir, _stage({"sdb": rot}, switch) + stubs + 'mkdir -p "$RUN_DIR/targets/sdb"; '
                  + 'lc_prepare_target sdb m.json; echo "rc=$?"; cat "$RUN_DIR/targets/sdb/state" "$RUN_DIR/targets/sdb/error" 2>/dev/null; true')   # a fit writes no error file: cat's 1 is not the script's
        assert out.returncode == 0, out.stderr
        trace = (run_dir / "run" / "trace").read_text(encoding="utf-8") if (run_dir / "run" / "trace").exists() else ""
        if refused:
            assert out.stdout.splitlines()[-3:] == ["rc=1", "failed", f"{REFUSED} — לא נכתב"], (rot, switch, out.stdout)
            assert trace == "", trace                                  # not even the fit check
            assert "refused as a target" in _log(run_dir)
        else:
            assert "rc=0" in out.stdout and "APPLY_GPT" in trace, (rot, switch, out.stdout, trace)


# --- the server: the hello carries the switch ------------------------------------------------


def test_the_hello_carries_the_switch_off_until_the_owner_turns_it_on(room_server):
    admin = room_server["admin"]
    off = drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])
    assert off["hdd_support"] is False
    assert admin.post(URL, json={"hdd_support": True}).status_code == 200
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])["hdd_support"] is True
    assert admin.post(URL, json={"hdd_support": False}).status_code == 200
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])["hdd_support"] is False


# --- the wiring ------------------------------------------------------------------------------


def test_every_load_chain_has_it_and_the_two_callers_ask_the_wrapper():
    for rel in ("imagesrv-agent", "lib/gui/guibridge.sh"):
        assert '. "$LIB_DIR/lchdd.sh"' in (AGENT / rel).read_text(encoding="utf-8"), rel
    assert 'hdd_support_take "$RESP"' in (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    for rel in ("lib/localclone/lcgui.sh", "lib/localclone/localclone.sh"):
        text = (AGENT / rel).read_text(encoding="utf-8")
        assert "lc_fit_verdict " in text and "lc_verdict_for " not in text, rel
    assert 'lc_hdd_gate "$1" || return 1' in (AGENT / "lib/localclone/lccopy.sh").read_text(encoding="utf-8")
