"""#2009 -- a local clone from a Windows left in Fast Startup (hibernated).

Field, 22/10 (USB, HP 8000 Elite): the source had been shut down the normal
way, so it was in Fast Startup. The clone copied hiberfil.sys (13.6 GB) and
grew the Windows partition (ntfsresize -f -f over "Windows is hibernated");
the screen said a green "הושלם" and the copy did not boot (Automatic Repair).
Nadav, 22/10: "אם לא, dd יותר טוב מאיתנו" -- never worse than dd. Not asked,
not stopped; on the COPY only, never the source:

* the source is only probed (``ntfs-3g.probe --readwrite``, read-only), a
  line in the log for each NTFS partition (the USB stick's diag report);
* a target partition that is hibernated has its hibernation dropped
  (``ntfs-3g -o remove_hiberfile``, umount, ``ntfsfix -d``) with evidence --
  the probe says clean, hiberfil.sys is gone -- and then grows as always;
  when remove_hiberfile refuses ("Metadata kept in Windows cache", metal
  23/10), ``ntfsfix`` (no -d) empties $LogFile -- rc 1 while hibernated, and
  no rc of it is evidence -- and remove_hiberfile is tried once more;
* when that fails the target is made the source's own: its table, and every
  partition the grow had moved or the drop had written copied raw from the
  source and compared (like dd) -- no grow;
* ``grow.sh`` (every path, the server's restore included) grows a hibernated
  volume only after the drop worked;
* a done target is orange with what was done -- ``lc_tgt``'s 10th field and
  the report's ``ntfs_note`` (``clone-*.json``, the server's journal body).

The probe and the NTFS tools are stubs here; the real ``ntfs-3g`` on a real
hibernated NTFS is in ``test_local_clone_engine.py`` (the Testrunner). The
native screen's orange is ``test_lc_ntfs_note_gui_2009.py``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

UNHIB = "Windows במקור לא כובה עד הסוף (Fast Startup). בעותק בוטלה ההשהיה (עלייה מלאה)"
UNHIB_LOGFILE = ("Windows במקור לא כובה עד הסוף (Fast Startup). ההשהיה בוטלה בעותק (אחרי ריקון היומן) — "
                 "העותק יעלה כמו אחרי הפעלה מחדש")
SAME = ("Windows במקור לא כובה עד הסוף (Fast Startup), וביטול ההשהיה בעותק נכשל — הועתק בלי הגדלה, זהה למקור. "
        "כדי להגדיל: לכבות את המקור עם Shift+כיבוי ולשכפל שוב")
SAME_UNCHECKED = "לא הצלחנו לבדוק אם Windows במקור כובה עד הסוף — הועתק בלי הגדלה, זהה למקור"
DIRTY = "דגל ה-dirty של NTFS נשאר על הדיסק — בעלייה הראשונה Windows עשוי להריץ בדיקת דיסק"
TGT_UNCHECKED = "לא הצלחנו לבדוק את מצב ה-NTFS ביעד — בעלייה הראשונה Windows עשוי להריץ בדיקת דיסק"

#: ntfs-3g.probe --readwrite <node>: the exit code in $BOX/rc.<node's name>, 0 when none.
PROBE = '#!/bin/sh\nn=$(basename "$2"); [ -f "$BOX/rc.$n" ] && exit "$(cat "$BOX/rc.$n")"; exit 0\n'

#: ntfs-3g -o <opts> <node> <mnt>: every call in $BOX/writes. remove_hiberfile
#: drops the hibernation (rc.<node> -> 0) unless $BOX/remove_fail (refused,
#: nothing written), $BOX/logfile (refused -- "Metadata kept in Windows cache",
#: rc 14 -- until ntfsfix empties the journal) or $BOX/stay14 (mounted --
#: written -- and still hibernated). A read-only mount shows hiberfil.sys with
#: "hibr" when $BOX/hib_stays.
NTFS3G = (
    '#!/bin/sh\necho "ntfs-3g $*" >> "$BOX/writes"\nn=$(basename "$3")\n'
    'case "$2" in\n'
    '  remove_hiberfile) [ ! -f "$BOX/remove_fail" ] || exit 1; [ ! -f "$BOX/logfile" ] || exit 14\n'
    '    [ -f "$BOX/stay14" ] || echo 0 > "$BOX/rc.$n" ;;\n'
    '  ro) [ ! -f "$BOX/hib_stays" ] || printf hibr > "$4/hiberfil.sys" ;;\n'
    'esac\nexit 0\n'
)
#: ntfsfix <node> (no option): empties $LogFile ($BOX/logfile goes) and exits 1,
#: "Windows is hibernated" -- as on metal 23/10. With $BOX/logfile_stays it
#: says OK (rc 0) and the journal stays: no rc of it is evidence either way.
NTFSFIX = ('#!/bin/sh\necho "ntfsfix $*" >> "$BOX/writes"\ncase "$1" in -*) exit 0 ;; esac\n'
           '[ ! -f "$BOX/logfile_stays" ] || exit 0\nrm -f "$BOX/logfile"; exit 1\n')
NTFSRESIZE = '#!/bin/sh\necho "ntfsresize $*" >> "$BOX/writes"\nexit 0\n'
UMOUNT = '#!/bin/sh\nrm -f "$1/hiberfil.sys"\nexit 0\n'
BLOCKDEV = '#!/bin/sh\nexit 0\n'

#: The run's plan (idx|guid|role|fs|start|size|...): an ESP, Windows (3, NTFS --
#: the partition that grows) and Recovery (4, NTFS, after it: moved to the end).
SETUP = (
    'mkdir -p "$LC/run" "$DEVROOT"; LOG_FILE="$RUN_DIR/agent.log"; : > "$LOG_FILE"; : > "$BOX/writes"; '
    'PATH="$BOX/bin:$PATH"; echo sdc > "$LC/run/src"; '
    'manifest_plan() { [ ! -f "$BOX/plan_fail" ] || return 1; '
    'printf "1|g|esp|vfat|2048|104857600|f||false||u|\\n3|g|windows|ntfs|206848|224897344000|f||false||u|\\n'
    '4|g|recovery|ntfs|439455744|650000000|f||false||u|\\n"; }; '
    # the partitions as files: the source's bytes and the target's (different)
    'for i in 1 3 4; do printf "source-$i" > "$DEVROOT/sdc$i"; printf "target-$i" > "$DEVROOT/sda$i"; done; '
    'apply_gpt() { echo "GPT $1" >> "$BOX/writes"; [ ! -f "$BOX/gpt_fail" ]; }; '
)


def box(tmp_path: Path, rcs: dict[str, int] | None = None, *flags: str, probe: bool = True) -> dict:
    b = tmp_path / "box"; (b / "bin").mkdir(parents=True, exist_ok=True)
    for node, rc in (rcs or {}).items():
        (b / f"rc.{node}").write_text(str(rc), newline="\n")
    for f in flags:
        (b / f).write_text("", newline="\n")
    for name, body in (("ntfs-3g", NTFS3G), ("ntfsfix", NTFSFIX), ("ntfsresize", NTFSRESIZE), ("umount", UMOUNT),
                       ("blockdev", BLOCKDEV)):
        p = b / "bin" / name; p.write_text(body, newline="\n"); os.chmod(p, 0o755)
    env = {"BOX": posix(b), "DEVROOT": posix(tmp_path / "dev")}
    if probe:
        p = tmp_path / "ntfs-3g.probe"; p.write_text(PROBE, newline="\n"); os.chmod(p, 0o755)
        env["NTFSPROBE"] = posix(p)
    else:
        env["NTFSPROBE"] = "/nonexistent/ntfs-3g.probe"
    return env


def writes(tmp_path: Path) -> list[str]:
    return (tmp_path / "box" / "writes").read_text().splitlines()


def never_the_source(tmp_path: Path) -> None:
    """The source (sdc) is never handed to a tool that writes."""
    bad = [w for w in writes(tmp_path) if "/sdc" in w]
    assert bad == [], bad
    for i in (1, 3, 4):   # and its bytes are what they were (dd only ever reads it)
        assert (tmp_path / "dev" / f"sdc{i}").read_text() == f"source-{i}", i


# --- the probe ------------------------------------------------------------------------

@pytest.mark.parametrize("rc,want", [(0, "clean"), (14, "hibernated"), (15, "dirty"), (1, "unchecked:"), (12, "unchecked:")])
def test_the_probe_states_and_a_failed_probe_is_not_clean(tmp_path, rc, want):
    out = run(tmp_path, SETUP + 'ntfs_state "$DEVROOT/sda3"', **box(tmp_path, {"sda3": rc}))
    assert out.returncode == 0, out.stderr
    got = out.stdout.strip()
    assert got == want if want != "unchecked:" else got.startswith(want), got


def test_a_probe_that_is_not_there_is_unchecked(tmp_path):
    out = run(tmp_path, SETUP + 'ntfs_state "$DEVROOT/sda3"', **box(tmp_path, probe=False))
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().startswith("unchecked:") and "missing" in out.stdout, out.stdout


def test_the_hibernated_source_is_a_line_in_the_log_by_evidence(tmp_path):
    out = run(tmp_path, SETUP + 'lc_ntfs_source sdc "$RUN_DIR/m.json"; cat "$LOG_FILE"', **box(tmp_path, {"sdc3": 14}))
    assert out.returncode == 0, out.stderr
    assert "source sdc partition 3: Windows is hibernated (Fast Startup) -- ntfs-3g.probe rc 14" in out.stdout, out.stdout
    assert "source sdc partition 4: NTFS clean" in out.stdout
    never_the_source(tmp_path)


# --- the copy: unhibernated and grown, or the source's own ------------------------------

UNHIBERNATE = SETUP + (
    'mkdir -p "$RUN_DIR/targets/sda"; target_init sda 0; '
    '[ ! -f "$BOX/expanded" ] || echo "3|ntfs" > "$RUN_DIR/targets/sda/expanded"; '
    'lc_unhibernate_target sda "$RUN_DIR/m.json"; echo "rc=$?"; '
    'echo "action=$(cat "$RUN_DIR/targets/sda/ntfs.action" 2>/dev/null)"; '
    'echo "mark=$([ -f "$RUN_DIR/targets/sda/expanded" ] && echo yes || echo no)"; '
    'for i in 3 4; do echo "p$i=$(cat "$DEVROOT/sda$i")"; done; '
    'echo "state=$(cat "$RUN_DIR/targets/sda/state")|$(cat "$RUN_DIR/targets/sda/error" 2>/dev/null)"; cat "$LOG_FILE"'
)


def test_a_hibernated_copy_has_its_hibernation_dropped_on_the_target_only(tmp_path):
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "expanded"))
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout and "action=unhibernated" in out.stdout, out.stdout
    w = writes(tmp_path)
    assert any("remove_hiberfile" in x and "/sda3" in x for x in w), w
    assert any(x.startswith("ntfsfix -d") and x.endswith("/sda3") for x in w), w
    assert any("-o ro" in x and "/sda3" in x for x in w), w                         # hiberfil.sys read back
    assert "mark=yes" in out.stdout and "GPT sda" not in w, w                        # the grow goes on as planned
    assert "hibernation dropped on the copy -- probe clean, hiberfil.sys gone or blank" in out.stdout
    never_the_source(tmp_path)


@pytest.mark.parametrize("flags,raw3", [(("remove_fail",), True), (("stay14",), True), (("hib_stays",), True),
                                         (("logfile", "logfile_stays"), True), (("logfile", "stay14"), True)])
def test_a_drop_that_fails_makes_the_copy_the_sources_own(tmp_path, flags, raw3):
    """Refused twice (remove_fail; the journal that stays): ntfsfix had already
    written, so the Windows partition is raw from the source too, with the table
    and the moved Recovery. Written and still hibernated, or hiberfil.sys still
    "hibr": the same. No grow left."""
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "expanded", *flags))
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout and "action=same" in out.stdout, out.stdout
    assert "GPT sda" in writes(tmp_path) and "mark=no" in out.stdout, out.stdout     # the source's table, nothing to grow
    assert "p4=source-4" in out.stdout                                               # the moved one: the source's bytes
    assert ("p3=source-3" in out.stdout) == raw3, out.stdout                         # the written one, only when written
    assert "state=writing|" in out.stdout or "state=waiting|" in out.stdout, out.stdout
    never_the_source(tmp_path)


def _step(w: str) -> str:
    if "remove_hiberfile" in w:
        return "remove"
    if "-o ro" in w:
        return "ro"
    return {"ntfsfix -d": "fix-d", "ntfsfix -b": "fix-b"}.get(w[:10], "fix" if w.startswith("ntfsfix /") else w)


def test_a_refused_drop_empties_the_journal_and_tries_once_more(tmp_path):
    """Metal 23/10: remove_hiberfile rc 14 ("Metadata kept in Windows cache");
    ntfsfix (no -d) empties $LogFile and exits 1 -- expected, not a failure --
    and the second remove_hiberfile works. The read-back decides: unhibernated,
    and the grow goes on."""
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "expanded", "logfile"))
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout and "action=unhibernated_logfile" in out.stdout, out.stdout
    w = [x for x in writes(tmp_path) if "/sda3" in x]
    assert [_step(x) for x in w] == ["remove", "fix", "remove", "fix-d", "ro"], w   # once more, never a third time
    assert "mark=yes" in out.stdout and "p3=target-3" in out.stdout, out.stdout     # grown as planned, not the source's
    assert "ntfsfix (journal) rc=1 -- 1 is expected while hibernated" in out.stdout, out.stdout
    assert "hibernation dropped on the copy -- probe clean" in out.stdout
    never_the_source(tmp_path)


@pytest.mark.parametrize("flags", [("logfile", "logfile_stays"), ("remove_fail",)])
def test_a_second_refusal_is_the_last_try(tmp_path, flags):
    """ntfsfix says OK (rc 0) and the journal stays, or remove_hiberfile refuses
    whatever ntfsfix did: two tries, then the copy is the source's own."""
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "expanded", *flags))
    assert out.returncode == 0, out.stderr
    assert "action=same\n" in out.stdout and "mark=no" in out.stdout, out.stdout
    assert sum("remove_hiberfile" in x for x in writes(tmp_path)) == 2, writes(tmp_path)
    assert "refused again after ntfsfix" in out.stdout, out.stdout
    never_the_source(tmp_path)


def test_an_unchecked_copy_is_the_sources_own_without_a_drop(tmp_path):
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sda3": 2}, "expanded"))
    assert out.returncode == 0, out.stderr
    assert "action=same_unchecked" in out.stdout and "mark=no" in out.stdout, out.stdout
    assert not any("remove_hiberfile" in x for x in writes(tmp_path))


def test_a_clean_copy_is_left_alone(tmp_path):
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {}, "expanded"))
    assert out.returncode == 0, out.stderr
    assert "action=\n" in out.stdout and "mark=yes" in out.stdout and writes(tmp_path) == [], out.stdout


def test_when_the_copy_cannot_be_made_the_sources_own_it_fails_by_name(tmp_path):
    out = run(tmp_path, UNHIBERNATE, **box(tmp_path, {"sda3": 14}, "expanded", "remove_fail", "gpt_fail"))
    assert out.returncode == 0, out.stderr
    assert "rc=1" in out.stdout and "state=failed|" in out.stdout and "לא הוחזר להיות זהה למקור" in out.stdout, out.stdout


# --- grow.sh: every path, the server's restore included --------------------------------

GROW = SETUP + 'grow_filesystem ntfs "$DEVROOT/sda3"; echo "rc=$? err=$GROW_ERROR"; cat "$LOG_FILE"'


@pytest.mark.parametrize("rc,flags,grows", [(0, (), True), (15, (), True), (14, (), True),
                                            (14, ("remove_fail",), False), (14, ("stay14",), False), (2, (), False),
                                            (14, ("logfile",), True),                     # metal 23/10: journal, then the drop
                                            (14, ("logfile", "logfile_stays"), False),    # refused twice
                                            (14, ("logfile", "stay14"), False)])          # dropped, the probe not clean
def test_grow_grows_a_hibernated_ntfs_only_after_the_drop(tmp_path, rc, flags, grows):
    out = run(tmp_path, GROW, **box(tmp_path, {"sda3": rc}, *flags))
    assert out.returncode == 0, out.stderr
    w = writes(tmp_path)
    resized = any(x.startswith("ntfsresize") for x in w)
    assert resized == grows, (w, out.stdout)
    if rc == 14 and grows:
        assert w.index(next(x for x in w if "remove_hiberfile" in x)) < w.index(next(x for x in w if x.startswith("ntfsresize")))
    if not grows:
        assert not any(x.startswith("ntfsfix -b") for x in w) and "rc=1" in out.stdout and "לא הוגדלה" in out.stdout, (w, out.stdout)


def test_grow_with_no_probe_does_not_grow(tmp_path):
    out = run(tmp_path, GROW, **box(tmp_path, probe=False))
    assert out.returncode == 0, out.stderr
    assert not any(x.startswith("ntfsresize") for x in writes(tmp_path)) and "rc=1" in out.stdout, out.stdout


# --- lc_finish: read back, then the drop, then the grow; the note ----------------------

FINISH = SETUP + (
    'lc_table_check() { :; }; readback_local() { :; }; '
    'finish_grow() { echo "FINISH $1 mark=$([ -f "$RUN_DIR/targets/$1/expanded" ] && echo yes || echo no) '
    'rc3=$(cat "$BOX/rc.sda3" 2>/dev/null)"; target_set "$1" done; return 0; }; '   # 0 = done, as the real one (#2017's "|| continue")
    'lc_ntfs_source sdc "$RUN_DIR/m.json"; '
    'for t in sda sdb; do target_init "$t" 0; echo "$t" >> "$LC/run/targets"; done; target_set sdb failed "x"; '
    'echo "3|ntfs" > "$RUN_DIR/targets/sda/expanded"; '
    'lc_finish "$RUN_DIR/m.json" sda sdb; '
    'for t in sda sdb; do echo "state $t=$(cat "$RUN_DIR/targets/$t/state")|$(cat "$RUN_DIR/targets/$t/ntfs_note" 2>/dev/null)"; done; '
    'lc_gui_run | grep "^lc_tgt="'
)


def test_the_drop_comes_before_the_grow_and_the_done_is_orange(tmp_path):
    out = run(tmp_path, FINISH, **box(tmp_path, {"sdc3": 14, "sda3": 14}))
    assert out.returncode == 0, out.stderr
    assert "FINISH sda mark=yes rc3=0" in out.stdout and "FINISH sdb" not in out.stdout, out.stdout   # unhibernated, then grown
    assert f"state sda=done|{UNHIB}\n" in out.stdout, out.stdout
    assert "state sdb=failed|\n" in out.stdout, out.stdout                                         # no note on a failed one
    tgt = [l for l in out.stdout.splitlines() if l.startswith("lc_tgt=sda|")][0]
    fields = tgt[len("lc_tgt="):].split("|")
    assert len(fields) == 12 and fields[1] == "done" and fields[9] == UNHIB, fields   # field 11: #2017's raw,rescued; 12: #2045's pulled
    never_the_source(tmp_path)


def test_a_drop_after_the_journal_is_grown_and_says_a_restart(tmp_path):
    out = run(tmp_path, FINISH, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "logfile"))
    assert out.returncode == 0, out.stderr
    assert "FINISH sda mark=yes rc3=0" in out.stdout, out.stdout
    assert f"state sda=done|{UNHIB_LOGFILE}\n" in out.stdout, out.stdout
    never_the_source(tmp_path)


def test_a_failed_drop_is_done_with_no_grow_and_says_so(tmp_path):
    out = run(tmp_path, FINISH, **box(tmp_path, {"sdc3": 14, "sda3": 14}, "remove_fail"))
    assert out.returncode == 0, out.stderr
    assert "FINISH sda mark=no" in out.stdout and f"state sda=done|{SAME}\n" in out.stdout, out.stdout
    never_the_source(tmp_path)


def test_a_clean_source_and_target_stay_green_and_grow(tmp_path):
    out = run(tmp_path, FINISH, **box(tmp_path))
    assert out.returncode == 0, out.stderr
    assert "FINISH sda mark=yes" in out.stdout and "state sda=done|\n" in out.stdout, out.stdout
    tgt = [l for l in out.stdout.splitlines() if l.startswith("lc_tgt=sda|")][0]
    assert tgt.split("|")[9] == "", tgt


NOTE = SETUP + ('mkdir -p "$RUN_DIR/targets/sda"; [ ! -s "$BOX/action" ] || cp "$BOX/action" "$RUN_DIR/targets/sda/ntfs.action"; '
                'lc_ntfs_note sda "$RUN_DIR/m.json"; echo "note=$(cat "$RUN_DIR/targets/sda/ntfs_note" 2>/dev/null)"')


@pytest.mark.parametrize("action,rcs,want", [
    ("unhibernated", {}, UNHIB),
    ("unhibernated_logfile", {}, UNHIB_LOGFILE),                # metal 23/10: after the journal -- orange, not green
    ("unhibernated_logfile", {"sda3": 15}, f"{UNHIB_LOGFILE} · {DIRTY}"),
    ("unhibernated", {"sda3": 15}, f"{UNHIB} · {DIRTY}"),     # dropped, grown, and ntfsfix -d did not clear the flag
    ("same", {"sda3": 14}, SAME),                               # the copy resumes as the source would
    ("same_unchecked", {"sda3": 2}, SAME_UNCHECKED),
    ("", {"sda3": 15}, DIRTY),                                  # a clean source, a target left dirty
    ("", {"sda4": 2}, TGT_UNCHECKED),
    ("", {}, ""),                                               # nothing to say: green
])
def test_the_note_of_a_done_target(tmp_path, action, rcs, want):
    env = box(tmp_path, rcs)
    (tmp_path / "box" / "action").write_text(f"{action}\n" if action else "", newline="\n")
    out = run(tmp_path, NOTE, **env)
    assert out.returncode == 0, out.stderr
    assert f"note={want}\n" in out.stdout, out.stdout


def test_the_report_carries_the_note(tmp_path):
    """The body of clone-*.json (USB) and of the server's journal POST."""
    out = run(tmp_path, FINISH + '; _lr_src=sdc; date +%s > "$LC/run/started"; date +%s > "$LC/run/ended"; '
              'disk_port() { echo 1; }; disk_serial() { echo S; }; '
              'curl() { echo 200; }; lc_report; cat "$LC/run/report.json"', **box(tmp_path, {"sdc3": 14, "sda3": 14}))
    assert out.returncode == 0, out.stderr
    body = json.loads([l for l in out.stdout.splitlines() if l.startswith('{"mac"')][-1])
    by = {t["dev"]: t for t in body["targets"]}
    assert by["sda"]["state"] == "done" and by["sda"]["ntfs_note"] == UNHIB, body
    assert by["sdb"]["ntfs_note"] == "", body
