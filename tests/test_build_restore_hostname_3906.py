"""#3906 -- "שחזור לדיסק הזה" on a build machine writes this machine's name.

Metal, 20/11 (lab/metal-1120/truth.md, lnx1120): ext4 restored to bld1117
from its GUI; the disk came up with ``etc_hostname=lnxsrc-ext4`` -- the name
of the machine it was captured from -- and the agent's log had no hostname
line at all, not even "skipping". The root: ``single_restore_run`` (the
restore behind the GUI) went from the write straight to ``stage_drivers``;
the name was written only by ``name_this_machine`` (the class station's
round, imagesrv-agent) and ``name_drawers`` (the cloner's room). Windows on
the same path had the same hole: no hive was touched.

Now ``sr_name_machine`` runs before the drivers: the name is the one the
server holds for this machine (``name`` of the /agent/state the bridge read
just before the write), through the same ``write_hostname`` as every path --
Linux in /etc/hostname, Windows in the hive (not synced = written, with a
note, #3167). No name, an answer that did not read, or a write that failed
is said by name: in the log, in the target's error (the report and the
console), and on the success card -- the restore stays done.

Here: the real ``write_hostname`` in the boxes of test_captured_stdout.py
(fake mount / ntfs-3g / hivewrite), the real ``sr_name_machine``; the order
on the path is in test_writemark_postdeploy_1877.py; the card through the
real ``single_restore_run`` and ``srgui.sh`` (test_single_restore_progress_gui.py).

Negative control (in the PR): every production file this touched back to the
base -- ``sr_name_machine`` is not there (127), nothing is named, the path's
trace has no NAMED step, and the card says nothing.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from agentlib import agent_sources
from native import requires_native
from test_agent import AGENT, BASH
from test_captured_stdout import linux_box, posix, sh, windows_box
from test_hostname_dirty_hive_1821 import HIVEWRITE_LOGGED, NOTE, _hive
from test_hostname_failure_visible import HIVEWRITE_LIES
from test_single_restore_progress_gui import END, PROGRESS, RLM, body, cards, restore

pytestmark = requires_native(("bash", BASH), "jq")


def _named(box, tmp_path: Path, state: dict | None, pre: str = "", **override: str) -> dict:
    """The restore is written (target sda done), then sr_name_machine sda --
    with /agent/state's answer as the bridge left it (None = no file)."""
    script, run = box(tmp_path, **override)
    (run / "gui").mkdir(exist_ok=True)
    (run / "sr-manifest.json").write_text("{}", encoding="utf-8")
    if state is not None:
        (run / "gui" / "restore-state.json").write_text(json.dumps(state), encoding="utf-8")
    script += (
        f'{agent_sources("sysinfo", "diskinfo", "progress", "failmark")}'
        f'. {posix(AGENT)}/lib/core/jsonq.sh; . {posix(AGENT)}/lib/restore/single_restore.sh; '
        'assert_target() { return 0; }; '   # #2434: the disk's identity -- its own tests
        'target_init sda 4096; target_set sda done; ' + pre
        + 'sr_name_machine sda; echo "rc=$?"'
    )
    out = sh(script)
    read = lambda p: (run / p).read_text(encoding="utf-8").strip() if (run / p).exists() else None  # noqa: E731
    return {"out": out, "run": run, "state": read("targets/sda/state"), "error": read("targets/sda/error"),
            "note": read("targets/sda/note"), "log": read("agent.log") or "",
            "warn": (run / "gui/restore.warn").read_text(encoding="utf-8") if (run / "gui/restore.warn").exists() else None,
            "hostname": read("hostname.json")}


def _ok(r: dict) -> None:
    assert r["out"].returncode == 0, r["out"].stderr
    assert r["out"].stdout.strip().endswith("rc=0"), r["out"].stdout   # never fatal


# --- written --------------------------------------------------------------------------


def test_a_linux_restore_writes_this_machines_name_into_etc_hostname(tmp_path):
    """**The defect itself**: lnxsrc-ext4 stayed. Negative control: 127, no /etc/hostname."""
    r = _named(linux_box, tmp_path, {"name": "BLD1117", "role": "build"})
    _ok(r)
    assert (r["run"] / "linux/etc/hostname").read_text().strip() == "BLD1117"
    assert r["state"] == "done" and r["error"] is None, r
    assert json.loads(r["hostname"])["ok"] is True
    assert 'hostname: {"ok":true,"hostname":"BLD1117"' in r["log"], r["log"]
    assert r["warn"] is None


def test_a_windows_restore_writes_the_name_into_the_hive(tmp_path):
    r = _named(windows_box, tmp_path, {"name": "LAB1-05"}, hivewrite=HIVEWRITE_LOGGED)
    _ok(r)
    assert json.loads(r["hostname"]) == {"ok": True, "hostname": "LAB1-05", "method": "offline-registry"}
    writes = (r["run"] / "hivewrites").read_text(encoding="utf-8")
    assert "ComputerName LAB1-05" in writes and "NV Hostname LAB1-05" in writes, writes
    assert r["error"] is None and r["warn"] is None, r


def test_a_hive_not_synced_is_written_with_a_note_on_the_card(tmp_path):
    """#3167 (Nadav 14/11): written all the same -- a note, not an error."""
    def dirty(tmp, **override):
        script, run = windows_box(tmp, **override)
        (run / "win/Windows/System32/config/SYSTEM").write_bytes(_hive(41, 40))
        return script, run
    r = _named(dirty, tmp_path, {"name": "LAB1-05"}, hivewrite=HIVEWRITE_LOGGED)
    _ok(r)
    assert json.loads(r["hostname"])["ok"] is True
    assert r["note"] == NOTE and r["error"] is None, r
    assert r["warn"] == NOTE + " · "


# --- not written: said by name ----------------------------------------------------------


def test_no_name_from_the_server_is_said_and_nothing_is_mounted(tmp_path):
    r = _named(linux_box, tmp_path, {"name": None, "role": "build"})
    _ok(r)
    assert r["state"] == "done", r                         # the restore itself is whole
    assert r["error"] == "שם המחשב לא נכתב: השרת לא שלח שם למחשב הזה", r
    assert "hostname: שם המחשב לא נכתב: השרת לא שלח שם למחשב הזה" in r["log"], r["log"]
    assert r["warn"] == "שם המחשב לא נכתב: השרת לא שלח שם למחשב הזה · "
    assert not (r["run"] / "linux/etc/hostname").exists()


def test_an_answer_that_did_not_read_is_not_no_name(tmp_path):
    """Principle 5: "could not read" and "the server sent none" are two states."""
    r = _named(linux_box, tmp_path, None)
    _ok(r)
    assert r["error"] == "שם המחשב לא נכתב: השם בתשובת השרת לא נקרא", r
    assert r["warn"] == "שם המחשב לא נכתב: השם בתשובת השרת לא נקרא · "


def test_a_write_that_failed_is_said_by_its_reason(tmp_path):
    """#33's write that did not write: the read-back is another name."""
    r = _named(windows_box, tmp_path, {"name": "LAB1-05"}, hivewrite=HIVEWRITE_LIES)
    _ok(r)
    assert r["state"] == "done", r
    assert r["error"] == "שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה", r
    assert json.loads(r["hostname"])["code"] == "hive_write_failed"
    assert r["warn"] == "שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה · "


def test_a_reason_too_long_for_the_card_is_said_short_there_and_whole_in_the_error(tmp_path):
    """sub= is 200 bytes in the GUI: a long reason would be cut mid-letter."""
    r = _named(linux_box, tmp_path, {"name": "עמדה 3"})
    _ok(r)
    assert r["error"].startswith("שם המחשב לא נכתב: שם המחשב 'עמדה 3' אינו חוקי"), r
    assert r["warn"] == "שם המחשב לא נכתב — הסיבה ביומן ובקונסולה · "
    assert len(r["warn"].encode()) <= 100


def test_an_earlier_warning_on_the_target_stays(tmp_path):
    r = _named(linux_box, tmp_path, {"name": None},
               pre='target_set sda done "הושלם, אך ההרחבה נדחתה: מחיצה 2 (ext4)"; ')
    _ok(r)
    assert r["error"] == "הושלם, אך ההרחבה נדחתה: מחיצה 2 (ext4) | שם המחשב לא נכתב: השרת לא שלח שם למחשב הזה", r


def test_a_disk_that_is_not_the_one_chosen_is_never_mounted(tmp_path):
    """#2434: assert_target fails it by name; sr_name_machine writes nothing."""
    r = _named(linux_box, tmp_path, {"name": "BLD1117"},
               pre='assert_target() { target_set "$1" failed "not the disk"; return 1; }; ')
    _ok(r)
    assert r["state"] == "failed" and r["error"] == "not the disk", r
    assert not (r["run"] / "linux/etc/hostname").exists()


# --- on the screen: the success card ---------------------------------------------------------


def test_the_success_card_says_the_name_was_not_written_before_the_count(tmp_path):
    """The real single_restore_run and srgui.sh: every done card carries it,
    and the line still fits the GUI's sub[200]. Negative control: no card says it."""
    said = 'sr_name_machine() { sr_name_said "$1" "עריכת הרג׳יסטרי נכשלה"; }; '
    r = restore(tmp_path, PROGRESS + said, then=END)
    assert "RC=0" in r["out"] and "END=0" in r["out"], r["out"]
    done = [body(c) for c in cards(r["seen"]) if "השחזור הושלם" in c]
    assert done, r["seen"]
    subs = [m for c in done for m in re.findall(r"^sub=(.*)$", c, re.M)]
    assert subs and all(s.startswith(f"{RLM}הדיסק של המחשב · שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה · ") for s in subs), subs
    assert any("המחשב יכבה בעוד" in s for s in subs) and subs[-1].endswith("מכבה את המחשב…"), subs
    assert all(len(s.encode()) < 200 for s in subs), subs
    assert r["error"].strip() == "שם המחשב לא נכתב: עריכת הרג׳יסטרי נכשלה", r["error"]
