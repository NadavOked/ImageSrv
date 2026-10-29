"""#2208 -- a local clone to a SMALLER target from a Windows left in Fast Startup.

Metal, cloner 2, 26/10 (``C:\\ImageCtl-Lab\\metal-1026-shrink\\``): the source (a
Samsung 870 EVO, 75GB used) was hibernated, so the measure refused it
(``ntfs-3g.probe`` rc 14: "המקור במצב שינה של Windows"), and by hand the view
shrank but ``ntfsfix -d`` on it said "Windows is hibernated, refused to mount".

As #2009 does on a larger target's copy, the hibernation is dropped -- here on
the source's RAM VIEW (lcsnap.sh), before the shrink: ``ntfs_unhibernate``
(remove_hiberfile; once more after ntfsfix empties $LogFile), read back clean.
The source is only read. The budget holds the drop too (``LC_SNAP_UNHIB_MB``).
Every target's "done" is orange with #2009's words. A drop that fails refuses
the smaller target by name; a view whose state does not read is refused.

The tools are stubs (``test_lc_readonly_source``'s FAKES); the real ntfs-3g on a
real hibernated view is ``test_local_clone_engine.py`` (the Testrunner).

**Negative control** (Testrunner, 26/10): with ``agent/lib/lcsnap.sh``,
``agent/lib/lcinv.sh``, ``agent/lib/hibernation.sh`` and ``agent/lib/lcplan.sh``
at the base commit (#2207's a2523575), 10 fail: 9 here (the drop on the view,
the journal's second try, the drop that fails, the budget, the unread state,
the store that fills, both orange notes, the measure of rc 14) and the engine's
hibernated view (refused, as on metal). 6 here pass in both states, the guards
(a clean view, no note, the measure of rc 15/2/0, the capture that never probes),
and the engine's refused drop (the refusal by name stays).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_lc_ntfs_note_2009 import SETUP as NOTE_SETUP, UNHIB, UNHIB_LOGFILE, box, never_the_source, writes
from test_lc_readonly_source import COMPACT, IN_PLACE, NEW_BYTES, PARTS, SHOW, setup, source_writes
from test_local_clone_agent import run

pytestmark = requires_native(("bash", BASH))

#: ntfs-3g -o <opts> <node> <mnt>, as the view sees it: remove_hiberfile drops the
#: hibernation (the node's probe -> 0) unless $RUN_DIR/remove_fail (refused) or
#: $RUN_DIR/logfile (rc 14, "Metadata kept in Windows cache", until ntfsfix empties it)
TOOLS = r'''
ntfs-3g() {
    ord "ntfs-3g $*"
    case "$2" in remove_hiberfile)
        [ ! -f "$RUN_DIR/remove_fail" ] || return 1; [ ! -f "$RUN_DIR/logfile" ] || return 14
        echo 0 > "$RUN_DIR/probe.${3##*/}" ;; esac
}
umount() { ord "umount $*"; }
ntfsfix() { ord "ntfsfix $*"; case "$1" in -*) return "${NTFSFIX_RC:-0}" ;; esac; rm -f "$RUN_DIR/logfile"; return 1; }
echo 14 > "$RUN_DIR/probe.sdc3"
'''
HIB = TOOLS + 'echo 14 > "$RUN_DIR/probe.lc-src-3"; '


def shrink(tmp_path: Path, pre: str = "", flags: tuple[str, ...] = (), **env) -> tuple[dict[str, str], list[str], Path]:
    marks = "".join(f': > "$RUN_DIR/{f}"; ' for f in flags)
    out = run(tmp_path, setup() + IN_PLACE + pre + marks + 'lc_shrink_source sdc; _rc=$?; ' + SHOW
              + 'echo "unhib=$(cat "$LC/run/src_unhib" 2>/dev/null)"; ', **env)
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and not line.startswith("imagectl:"))
    log = tmp_path / "run" / "order.log"
    return got, (log.read_text(encoding="utf-8").splitlines() if log.exists() else []), tmp_path / "run"


def _mounts_of_the_source(order: list[str], devroot: str) -> list[str]:
    return [l for l in order if l.startswith(("ntfs-3g ", "umount ")) and f"{devroot}/sdc" in l]


def test_a_hibernated_view_is_unhibernated_on_the_view_then_shrunk(tmp_path):
    got, order, run_dir = shrink(tmp_path, HIB)
    dev = posix(run_dir / "devroot")
    view = f"{dev}/mapper/lc-src-3"
    assert got["rc"] == "0" and got["path"] == "snapshot", (got, order)
    # the view's own state, the budget, the drop on the view, then the shrink and the flag
    i_probe = order.index(f"ntfs-3g.probe --readwrite {view}")
    i_budget = order.index(f"ntfsresize -n -s {NEW_BYTES} --no-progress-bar {view}")
    i_drop = next(i for i, l in enumerate(order) if l.startswith(f"ntfs-3g -o remove_hiberfile {view} "))
    i_resize = order.index(f"ntfsresize -s {NEW_BYTES} --no-progress-bar {view}")
    assert i_probe < i_budget < i_drop < i_resize, order
    assert order.index(f"ntfsfix -d {view}") < i_resize, order               # the drop's own clean-up, read back
    assert f"ntfsfix -d {view}" in order[i_resize + 1:], order               # and the shrink's, after it
    assert got["unhib"] == "unhibernated", got
    assert source_writes(order, dev) == [] and _mounts_of_the_source(order, dev) == [], order
    assert (run_dir / "lc" / "run" / "parts.txt").read_text() == COMPACT    # the targets get the compacted layout
    assert got["node3"] == f"{dev}/mapper/lc-cut-3", got
    log = (run_dir / "agent.log").read_text(encoding="utf-8")
    assert "dropping it on the RAM view (#2208)" in log and "hibernation dropped on the copy" in log, log


def test_a_refused_drop_empties_the_views_journal_and_tries_once_more(tmp_path):
    """Metal 23/10 (#2009): remove_hiberfile rc 14 until ntfsfix empties $LogFile."""
    got, order, run_dir = shrink(tmp_path, HIB, flags=("logfile",))
    assert got["rc"] == "0" and got["unhib"] == "unhibernated_logfile", (got, order)
    assert sum(l.startswith("ntfs-3g -o remove_hiberfile") for l in order) == 2, order
    assert source_writes(order, posix(run_dir / "devroot")) == []


def test_a_drop_that_fails_refuses_the_smaller_target_by_name(tmp_path):
    """As today's refusal, by name -- and nothing shrunk, nothing left behind."""
    got, order, run_dir = shrink(tmp_path, HIB, flags=("remove_fail",))
    assert got["rc"] == "1" and got["path"] == "snapshot", (got, order)
    assert "במצב שינה של Windows" in got["error"] and "powercfg /h off" in got["error"], got
    assert "המקור לא נכתב" in got["error"], got
    assert not any(l.startswith("ntfsresize -s") for l in order), order
    assert got["unhib"] == "" and got["brd"] == "gone" and got["snapdir"] == "no", got
    assert got["node3"].endswith("/sdc3"), got
    assert (run_dir / "lc" / "run" / "parts.txt").read_text() == PARTS
    assert source_writes(order, posix(run_dir / "devroot")) == []


def test_the_budget_holds_the_drop_too(tmp_path):
    """8 GB available, 1 GB reserved -> 7168 MB for the view. 6200 MB to move: 6200 +
    620 + 256 fits a clean view; with the drop's 128 MB (7204) it does not."""
    got, order, _ = shrink(tmp_path, HIB, RELOC_MB="6200")
    assert got["rc"] == "1" and got["path"] == "refused", (got, order)
    assert "להזזת 6200 MB נדרשים 7204 MB זיכרון, ויש 7168 MB" in got["error"], got
    assert not any(l.startswith("ntfs-3g -o") for l in order), order       # refused before the first write
    assert got["brd"] == "gone" and got["snapdir"] == "no", got


def test_a_clean_view_is_neither_dropped_nor_charged(tmp_path):
    """The guard: a source shut down for real goes as before -- no mount, no note."""
    got, order, _ = shrink(tmp_path, TOOLS.replace('echo 14 > "$RUN_DIR/probe.sdc3"\n', ""), RELOC_MB="6200")
    assert got["rc"] == "0" and got["unhib"] == "", (got, order)
    assert not any(l.startswith(("ntfs-3g -o", "umount")) for l in order), order


def test_a_view_whose_state_does_not_read_is_refused_by_name(tmp_path):
    """Principle 5: a probe that did not answer is not "not hibernated"."""
    got, order, _ = shrink(tmp_path, TOOLS + 'echo 1 > "$RUN_DIR/probe.lc-src-3"; ')
    assert got["rc"] == "1" and got["path"] == "refused", (got, order)
    assert "לא הצלחנו לבדוק אם Windows בה כובה עד הסוף" in got["error"], got
    assert not any(l.startswith(("ntfs-3g -o", "ntfsresize -s")) for l in order), order


def test_a_store_that_fills_during_the_drop_is_refused_by_name(tmp_path):
    got, order, _ = shrink(tmp_path, HIB, DM_INVALID="1")
    assert got["rc"] == "1" and got["path"] == "refused", (got, order)
    assert "אינו תקין אחרי ביטול ההשהיה" in got["error"], got
    assert not any(l.startswith("ntfsresize -s") for l in order), order


def test_the_capture_never_probes_or_drops_on_its_views(tmp_path):
    """The capture calls lc_snap_shrink without ``unhib``: it refused a hibernated
    source before making any view (#651), and its path is not this Issue's."""
    out = run(tmp_path, setup() + HIB + 'lc_shrink_plan sdc; lc_snap_shrink sdc; echo "rc=$?"; '
              'echo "unhib=$(cat "$LC/run/src_unhib" 2>/dev/null)"')
    assert out.returncode == 0, out.stderr
    order = (tmp_path / "run" / "order.log").read_text(encoding="utf-8").splitlines()
    assert not any(l.startswith(("ntfs-3g.probe", "ntfs-3g -o")) for l in order), order
    assert "rc=0" in out.stdout.splitlines() and "unhib=" in out.stdout.splitlines(), out.stdout


# --- the targets: orange, with #2009's words ---------------------------------------------

UNHIBERNATE = NOTE_SETUP + (
    'mkdir -p "$RUN_DIR/targets/sda"; target_init sda 0; [ ! -s "$BOX/src_unhib" ] || cp "$BOX/src_unhib" "$LC/run/src_unhib"; '
    'lc_unhibernate_target sda "$RUN_DIR/m.json"; echo "rc=$?"; '
    'echo "action=$(cat "$RUN_DIR/targets/sda/ntfs.action" 2>/dev/null)"; '
    'lc_ntfs_note sda "$RUN_DIR/m.json"; echo "note=$(cat "$RUN_DIR/targets/sda/ntfs_note" 2>/dev/null)"'
)


@pytest.mark.parametrize("action,note", [("unhibernated", UNHIB), ("unhibernated_logfile", UNHIB_LOGFILE), ("", "")])
def test_a_copy_of_the_dropped_view_is_orange_with_what_was_done(tmp_path, action, note):
    """The target is clean -- the copy came from the dropped view -- and nothing more is
    written to it; its "done" says the source was not shut down all the way."""
    env = box(tmp_path, {"sdc3": 14})
    if action:
        (tmp_path / "box" / "src_unhib").write_text(action + "\n", newline="\n")
    out = run(tmp_path, UNHIBERNATE, **env)
    assert out.returncode == 0, out.stderr
    got = dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)
    assert got["rc"] == "0" and got["action"] == action and got["note"] == note, out.stdout
    assert not any("remove_hiberfile" in w for w in writes(tmp_path)), writes(tmp_path)
    never_the_source(tmp_path)


# --- the measure: a hibernated source is measured, not refused ----------------------------

MEASURE = r'''
mkdir -p "$LC/inv" "$LC/measure"
printf '1|sda|A|s1|480000000000|ok|ok|Windows|1|1|400000000000|1|\n' > "$LC/inv/sda.line"
printf '2|sdb|B|s2|250000000000|ok|ok|Windows|1|1|240000000000|1|\n' > "$LC/inv/sdb.line"
printf '1|EBD0A0A2-B9E5-4433-87C0-68B6B72699C7||2048|781250000\n' > "$LC/inv/sda.parts"
list_drawers() { echo sda; echo sdb; }
partition_node() { echo "/dev/$1$2"; }
_fs_of() { echo ntfs; }
probe_fake() { case "$2" in /dev/sda1) return "$SRC_RC" ;; esac; return 0; }; NTFSPROBE=probe_fake
capture_ntfs_hibernation_reason() { case "$SRC_RC" in 0) return 0 ;; 14|15) echo "חלונות במחיצה sda1 נכבה ב-Fast Startup — x"; return 1 ;; esac; echo "לא הצלחנו לבדוק"; return 2; }
disk_port() { echo 2; }; disk_drawer() { echo "3|SATA 2"; }
ntfsresize() { echo "$*" >> "$RUN_DIR/ntfsresize.log"; echo "You might resize at 100000000000 bytes or 100000 MB (freeing 300000 MB)."; }
lc_copy_size() { :; }
disk_serial() { echo "S-$1"; }
echo 0 > "$RUN_DIR/min_free_gb"
lc_measure sda; echo "rc=$?"
echo "verdict=$(lc_verdict_for sda sdb)"
echo "read=$(cat "$RUN_DIR/ntfsresize.log" 2>/dev/null)"
'''


@pytest.mark.parametrize("rc,want", [(14, "shrink"), (15, "unchecked"), (2, "unchecked"), (0, "shrink")])
def test_the_measure_takes_a_hibernated_source_and_still_refuses_a_dirty_or_unchecked_one(tmp_path, rc, want):
    out = run(tmp_path, f'SRC_RC={rc}; ' + MEASURE)
    assert out.returncode == 0, out.stderr
    got = dict(l.split("=", 1) for l in out.stdout.splitlines() if "=" in l)
    assert got["verdict"].split("|")[0] == want, out.stdout
    assert got["read"] == ("--info --no-progress-bar /dev/sda1" if want == "shrink" else ""), out.stdout   # read-only
