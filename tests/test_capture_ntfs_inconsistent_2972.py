"""#2972 (Nadav 12/11, a build machine at the college, v0.92.0): a capture failed
because the shrink step refused an NTFS that ntfsresize found inconsistent --
"בחלונות: chkdsk /f ... קלטו מחדש". Nadav's rule (10/09, said many times): "it
clones whether I shut down properly or not -- like Clonezilla". So:

* the partition is **not shrunk** and is read at its full size (partclone -I,
  #643) -- the capture, the direct send and the local clone go on;
* it is **said**, never hidden (principle 5: "not shrunk" is neither "failed"
  nor "shrunk"): ``"ntfs_inconsistent": true`` on the partition in the
  manifest, an orange note in the target's ``error`` field (the GUI's done
  screen and the console), a warn journal line on the server, a tag in the
  console;
* any other ``ntfsresize --info`` failure still refuses by name.

The kernel and NTFS tools are stubs (test_capture_fast_startup_2213 /
test_shrink_on_capture): no privileged device, every write-capable tool logs
its arguments, and the source is checked byte for byte afterwards.
"""

from __future__ import annotations

import copy
import json

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_fast_startup_2213 import fast_run, no_source_writes
from test_capture_refusals import capture_run, refusal_reason
from test_shrink_on_capture import (
    DATA_SECTORS, DATA_START, FS_MAP_C_D, INCONSISTENT, MIN_BYTES, SAY_YES, WIN_SECTORS,
    order, part, shrink_target, source_writes, two_stubs,
)

NOTE = "NTFS לא נסגר כראוי (לא עקבי) — לא כווץ; Windows יריץ chkdsk באתחול הראשון"

#: ntfsresize --info as the build machine printed it (#1502), for every node.
RESIZE_INCONSISTENT = ('#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\ncat << \'EOF\'\n'
                       + INCONSISTENT + 'EOF\nexit 1\n')
#: Another refusal of --info: not a diagnosis, still "we could not check".
RESIZE_SCHEDULED = ('#!/bin/sh\necho "ntfsresize $*" >> "$RUN_DIR/order.log"\n'
                    'echo "ERROR: Volume is scheduled for check."\nexit 1\n')

bash = requires_native(("bash", BASH))


def _report(box):
    return json.loads((box / "progress.json").read_text(encoding="utf-8"))


def _manifest(run):
    return json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))


# --- capture and direct send: one Windows partition, not hibernated -------------------


@bash
@pytest.mark.parametrize("direct", [False, True])
def test_an_inconsistent_ntfs_is_read_whole_labelled_and_never_refused(tmp_path, direct):
    """The bug itself: the same disk that was refused at the shrink step is now
    read at its full size from the source (no RAM view, no resize, nobody asked),
    the manifest says so on the partition, and the target ends orange with the note."""
    box, run, out = fast_run(tmp_path, direct=direct, env={"SOURCE_RC": "0"},
                             pre='shrink_ask() { echo asked >> "$RUN_DIR/order.log"; echo cancel; }; ',
                             extra={"ntfsresize": RESIZE_INCONSISTENT})
    details = box / ("direct.out" if direct else "capture.out")
    assert out.strip().endswith("rc=0"), out + details.read_text(encoding="utf-8")
    p2 = _manifest(run)["partitions"][1]
    assert p2["ntfs_inconsistent"] is True, p2
    assert p2["size_bytes"] == 204800 * 512 and "shrunk_from_bytes" not in p2, p2
    assert "ntfs_inconsistent" not in _manifest(run)["partitions"][0]   # the ESP: absent, not false
    steps = order(run)
    assert "asked" not in steps, "nothing is shrunk, so nobody is asked"
    assert not any(s.startswith(("ntfsresize -s", "ntfsresize -n", "dmsetup create", "modprobe")) for s in steps), steps
    reads = [s for s in steps if s.startswith("partclone ") and f'-s {posix(box / "dev")}/sda2 ' in s]
    assert len(reads) == (2 if direct else 1), steps
    assert all(" -I " in f" {s} " for s in reads), reads   # #643: a dirty source is read
    report = _report(box)
    assert report["targets"][0]["error"] == f"מחיצה 2: {NOTE}", report
    no_source_writes(box, run)


@bash
@pytest.mark.parametrize("direct", [False, True])
def test_another_info_failure_still_refuses_by_name(tmp_path, direct):
    """Only "NTFS is inconsistent" passes. "Volume is scheduled for check" is not
    a diagnosis -- the capture stops, says it could not check, and reads nothing.
    (A direct send without a required view sends at full size, #1171 -- that is
    the existing rule and is not this test; here the source is hibernated, so the
    view is required.)"""
    env = {} if direct else {"SOURCE_RC": "0"}
    box, run, out = fast_run(tmp_path, direct=direct, env=env, extra={"ntfsresize": RESIZE_SCHEDULED})
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק כמה אפשר לכווץ את מחיצה 2" in reason, reason
    assert not any(s.startswith("partclone ") for s in order(run))
    dirty = run / "shrink.dirty"   # a guard: true before #2972 too (no such file then)
    assert not dirty.exists() or not dirty.read_text(encoding="utf-8").strip()
    no_source_writes(box, run)


@bash
@pytest.mark.parametrize("direct", [False, True])
def test_a_hibernated_and_inconsistent_partition_gets_its_view_and_the_label(tmp_path, direct):
    """#2213 x #2972: Fast Startup is still dropped on the full-size RAM view, and
    the inconsistent volume is read from that view, not shrunk. Both notes."""
    box, run, out = fast_run(tmp_path, direct=direct, extra={"ntfsresize": RESIZE_INCONSISTENT})
    details = box / ("direct.out" if direct else "capture.out")
    assert out.strip().endswith("rc=0"), out + details.read_text(encoding="utf-8")
    m = _manifest(run)
    assert m["src_unhib"] == "unhibernated" and m["partitions"][1]["ntfs_inconsistent"] is True, m
    assert m["partitions"][1]["size_bytes"] == 204800 * 512
    steps = order(run)
    assert any(s.startswith("partclone ") and "/mapper/lc-src-2 " in s for s in steps), steps
    assert not any(s.startswith("ntfsresize -s") for s in steps), steps
    error = _report(box)["targets"][0]["error"]
    assert f"מחיצה 2: {NOTE}" in error and "Fast Startup" in error, error
    no_source_writes(box, run)


# --- capture: C: shrinks, an inconsistent D: is read whole ------------------------------


@bash
def test_c_is_shrunk_and_an_inconsistent_d_is_read_whole_after_it(tmp_path):
    """#929's two partitions: C: (3) is measured and shrunk on its RAM view as
    always; D: (4) is inconsistent -- out of the plan, read from the source at its
    full size, moved back in the manifest by C:'s shrink like any partition after it."""
    box, run, out = capture_run(tmp_path, stubs=two_stubs(), shell_pre=FS_MAP_C_D + SAY_YES,
                                env={"NTFSRESIZE_INFO_FAIL_ON": "4"})
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8")
    m = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    new_c = shrink_target(MIN_BYTES, WIN_SECTORS * 512)
    c, d = part(m, 3), part(m, 4)
    assert c["size_bytes"] == new_c and c["shrunk_from_bytes"] == WIN_SECTORS * 512, c
    assert "ntfs_inconsistent" not in c, c
    assert d["ntfs_inconsistent"] is True and d["size_bytes"] == DATA_SECTORS * 512, d
    assert "shrunk_from_bytes" not in d and d["source_start_sector"] == DATA_START, d
    steps = order(run)
    dev = posix(box / "dev")
    assert [s for s in steps if s.startswith("ntfsresize -s")] == \
        [f"ntfsresize -s {new_c} --no-progress-bar {dev}/mapper/lc-src-3"], steps
    assert any(s.startswith("partclone") and f"-s {dev}/sda4 " in s for s in steps), steps
    assert source_writes(steps, box) == [], source_writes(steps, box)
    assert _report(box)["targets"][0]["error"] == f"מחיצה 4: {NOTE}"


# --- the report reaches the server before the manifest can close the task ---------------


@bash
def test_the_note_is_reported_before_the_manifest(tmp_path):
    """#2213's race: accepting the manifest may close the task, and a report after
    it is refused -- the orange note must go first, like src_unhib's."""
    run = tmp_path / "run"
    run.mkdir()
    (run / "shrink.dirty").write_text("4\n", newline="\n")
    script = (f'RUN_DIR={posix(run)!r}; LOG_FILE="$RUN_DIR/log"; MAC=x; SERVER=http://s; '
              f'. {posix(AGENT)}/lib/capture/capturesink.sh; '
              'report_final() { echo report >> "$RUN_DIR/order"; }; '
              'curl() { echo manifest >> "$RUN_DIR/order"; }; ')
    assert sh(script + 'upload_manifest t m; echo "rc=$?"').strip() == "rc=0"
    assert (run / "order").read_text().splitlines() == ["report", "manifest"]


# --- the server: the console list and the journal ---------------------------------------


def test_the_image_list_names_the_inconsistent_partitions(server):
    from conftest import MANIFEST_256, write_image

    m = copy.deepcopy(MANIFEST_256)
    m.update(id="img_297201", name="Unclean C")
    m["partitions"][1]["ntfs_inconsistent"] = True
    write_image(server["ctx"].library.root, m)
    rows = {i["id"]: i for i in server["admin"].get("/api/console/images").json()}
    assert rows["img_297201"]["ntfs_inconsistent"] == [m["partitions"][1]["index"]]
    assert rows[MANIFEST_256["id"]]["ntfs_inconsistent"] == []


def test_the_journal_line_is_hebrew_orange_and_only_when_there_is_something_to_say(tmp_path):
    from server import db as dbmod
    from server.console import journal_he
    from server.library.images import journal_ntfs_inconsistent

    conn = dbmod.connect(tmp_path / "imagesrv.db")
    lines = "SELECT detail, user FROM journal WHERE event = 'ntfs_not_shrunk'"
    journal_ntfs_inconsistent(conn, {"partitions": [{"index": 1}, {"index": 3, "ntfs_inconsistent": False}]}, "img_x")
    journal_ntfs_inconsistent(conn, {}, "img_x")   # a manifest with no partitions at all
    assert conn.execute(lines).fetchall() == []
    journal_ntfs_inconsistent(conn, {"partitions": [{"index": 3, "ntfs_inconsistent": True},
                                                    {"index": 4, "ntfs_inconsistent": True}]}, 'img_x "C"', "admin")
    assert [tuple(r) for r in conn.execute(lines).fetchall()] == [(f'img_x "C" מחיצה 3, 4: {NOTE}', "admin")]
    assert journal_he.severity("ntfs_not_shrunk") == "warn"
    assert "בלי כיווץ" in journal_he.EVENTS_HE["ntfs_not_shrunk"]


def test_the_server_journals_it_when_the_image_enters_the_library_and_on_a_direct_manifest():
    """The two places a manifest is accepted call the one helper (the capture's
    background verify, and the direct send's manifest PUT)."""
    from pathlib import Path

    repo = Path(__file__).resolve().parent.parent
    capture = (repo / "server/library/capture.py").read_text(encoding="utf-8")
    verify = capture[capture.index("def _verify("):capture.index("def _verify_progress(")]
    assert verify.index('"capture_done"') < verify.index("journal_ntfs_inconsistent(conn, manifest,") < verify.index('return "done"')
    direct = (repo / "server/rounds/direct.py").read_text(encoding="utf-8")
    body = direct[direct.index("def _put_manifest_body("):direct.index("def _fail(")]
    assert body.index("store_live_manifest(") < body.index("journal_ntfs_inconsistent(ctx.conn, manifest,")
