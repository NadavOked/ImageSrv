"""#2213: real capture/direct-send shell flow, fake kernel and NTFS tools.

No privileged devices. Every write-capable tool logs its arguments. New
regression cases reach behavior the old early refusal could never reach;
unchanged gates are paired with a successful hibernated control so each
test also fails on the old code. Runtime/negative control must run on Linux.
"""

import json

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import CURL_SINK, capture_run, refusal_reason
from test_directsend import PARTCLONE, TWO_PARTITIONS, direct_run
from test_lc_ntfs_note_2009 import UNHIB, UNHIB_LOGFILE
from test_shrink_on_capture import (
    BLOCKDEV_OK, DMSETUP_STUB, MODPROBE_STUB, NTFSRESIZE_OK,
    RMMOD_STUB, order, view_sys,
)

pytestmark = requires_native(("bash", BASH))

PROBE = r'''#!/bin/sh
echo "probe $*" >> "$RUN_DIR/order.log"
case "$2" in
    */sda2) exit "${SOURCE_RC:-14}" ;;
    */mapper/*)
        [ ! -f "$RUN_DIR/dropped" ] || exit "${READBACK_RC:-0}"
        exit "${VIEW_RC:-14}" ;;
esac
exit 0
'''
MOUNT = r'''#!/bin/sh
echo "ntfs-3g $*" >> "$RUN_DIR/order.log"
if [ "$2" = remove_hiberfile ]; then
    [ "${DROP_FAIL:-0}" = 0 ] || exit 1
    [ "${JOURNAL:-0}" = 0 ] || [ -f "$RUN_DIR/journal_reset" ] || exit 14
    : > "$RUN_DIR/dropped"
fi
exit 0
'''
FIX = r'''#!/bin/sh
echo "ntfsfix $*" >> "$RUN_DIR/order.log"
[ "$1" != -d ] || exit 0
: > "$RUN_DIR/journal_reset"
exit 1
'''
BLKID = r'''#!/bin/sh
for node; do :; done
case "$node" in
    */sda2|*/mapper/*) [ "${BLKID_FAIL:-0}" = 0 ] || exit 7; echo "${SOURCE_FS:-ntfs}" ;;
    *) echo vfat ;;
esac
'''
PRE = (view_sys({2: 204800})
       + '_system_role() { echo windows; }; '
       + 'export PCL_ARGV="$RUN_DIR/order.log" PCL_COUNT="$RUN_DIR/pcl.count" PCL_BYTE=A PCL_SECOND=""; '
       + 'NTFSPROBE=ntfs-3g.probe; ')


def fast_run(tmp_path, *, direct=False, env=None, pre="", extra=None, call="direct_send_run"):
    stubs = {
        "sgdisk": TWO_PARTITIONS, "blkid": BLKID,
        "ntfs-3g.probe": PROBE, "ntfs-3g": MOUNT, "ntfsfix": FIX,
        "umount": '#!/bin/sh\necho "umount $*" >> "$RUN_DIR/order.log"\n',
        "sync": "#!/bin/sh\nexit 0\n",
        "ntfsresize": NTFSRESIZE_OK, "dmsetup": DMSETUP_STUB,
        "modprobe": MODPROBE_STUB, "rmmod": RMMOD_STUB,
        "blockdev": BLOCKDEV_OK, "partclone.ntfs": PARTCLONE,
        "partclone.fat": PARTCLONE, **(extra or {}),
    }
    stubs["sgdisk"] = stubs["sgdisk"].replace("#!/bin/sh\n", '#!/bin/sh\necho "sgdisk $*" >> "$RUN_DIR/order.log"\n', 1)
    if direct:
        return direct_run(tmp_path, partitions=2, extra_stubs=stubs,
                          pre=PRE + pre, env=env, call=call)
    return capture_run(tmp_path, stubs={"curl": CURL_SINK, **stubs},
                       shell_pre=PRE + pre, env=env)


def _writes_source(line, source):
    """A logged step that could write the source disk or its table (#1466, #2213)."""
    if source not in line:
        return False
    tool, *args = line.split()
    if tool == "ntfsfix":
        return True                                   # every ntfsfix writes
    if tool == "ntfs-3g":
        return args[:2] != ["-o", "ro"]               # remove_hiberfile, or any rw mount
    if tool == "ntfsresize":
        return not ({"--info", "-n"} & set(args))     # only the two read-only forms
    if tool == "sgdisk":
        return not ({"-p", "-i"} & set(args))         # anything but a read of the table
    if tool == "blockdev":
        return "--getsize64" not in args              # --rereadpt, --setrw, ...
    return False


def no_source_writes(box, run):
    source = posix(box / "dev" / "sda")
    steps = order(run)
    assert any(source in s for s in steps), steps     # the log saw the source at all
    bad = [s for s in steps if _writes_source(s, source)]
    assert not bad, bad
    assert not (run / "targets/sda/shrunk").exists()
    for n in (1, 2):                                  # and the bytes themselves
        assert (box / "dev" / f"sda{n}").read_bytes() == bytes(512), n


def succeeded(result, *, direct=False, action="unhibernated"):
    box, run, out = result
    details = box / ("direct.out" if direct else "capture.out")
    assert out.strip().endswith("rc=0"), out + details.read_text(encoding="utf-8")
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert manifest["src_unhib"] == action
    assert manifest["partitions"][1]["size_bytes"] == 204800 * 512
    steps = order(run)
    reads = [s for s in steps if s.startswith("partclone ") and "/mapper/" in s]
    assert len(reads) == (2 if direct else 1), steps
    assert all(f'-s {posix(box / "dev")}/mapper/lc-src-2 ' in s for s in reads), reads
    assert not any(s.startswith("partclone ") and f'-s {posix(box / "dev")}/sda2 ' in s for s in steps)
    assert not any(s.startswith("ntfsresize -s") for s in steps)  # full-size, no shrink requested
    note = UNHIB_LOGFILE if action.endswith("_logfile") else UNHIB
    report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
    assert report["targets"][0]["error"] == note, report
    assert (run / "src_unhib").read_text().strip() == action
    no_source_writes(box, run)
    if direct:
        # Pass 2 uses the very same object, not a recreated snapshot.
        assert sum(s.startswith("dmsetup create lc-src-2 ") for s in steps) == 1
        assert not any(s.startswith("dmsetup remove") for s in steps)
        assert (run / "dsha.2").read_text().split()[0] == manifest["partitions"][1]["sha256"]
    else:
        assert any(s.startswith("dmsetup remove --retry lc-src-2") for s in steps)
        assert not (box / "sys/module/brd").exists()
    return box, run


def shrink_hibernated(tmp_path, *, plain=False, direct=False):
    """One large Windows partition: choose shrink or plain, both use a view."""
    sectors = 998244352
    table = TWO_PARTITIONS.replace("204800 sectors", f"{sectors} sectors")
    # Keep the ESP at its original size; only partition 2 is enlarged.
    table = table.replace(f"{sectors} sectors", "204800 sectors", 1)
    choice = "plain" if plain else "continue"
    pre = view_sys({2: sectors}) + f'shrink_ask() {{ echo {choice}; }}; '
    pre += 'attended() { shift; "$@"; }; '  # operator answer only; production shrink logic remains real
    if direct:
        # Tail planning uses the disk's measured size; leave no slack.
        pre += f'echo {206848 + sectors + 512} > "$SYSROOT/sys/block/sda/size"; '
    box, run, out = fast_run(tmp_path, direct=direct, pre=pre, extra={"sgdisk": table})
    assert out.strip().endswith("rc=0"), out + (box / ("direct.out" if direct else "capture.out")).read_text(encoding="utf-8")
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert manifest["src_unhib"] == "unhibernated"
    part = manifest["partitions"][1]
    steps = order(run)
    node = posix(box / "dev/mapper/lc-src-2")
    if plain:
        assert part["size_bytes"] == sectors * 512
        assert not any(s.startswith("ntfsresize -s") for s in steps)
        assert any(s.startswith("partclone ") and f"-s {node} " in s for s in steps)
    else:
        assert part["size_bytes"] < sectors * 512
        assert part["shrunk_from_bytes"] == sectors * 512
        drop = next(i for i, s in enumerate(steps) if s.startswith(f"ntfs-3g -o remove_hiberfile {node} "))
        resize = next(i for i, s in enumerate(steps) if s.startswith(f'ntfsresize -s {part["size_bytes"]} '))
        assert drop < resize
        reads = [s for s in steps if s.startswith("partclone ") and "lc-cut-2" in s]
        assert len(reads) == (2 if direct else 1), steps
    no_source_writes(box, run)


@pytest.mark.parametrize("plain", [False, True])
@pytest.mark.parametrize("direct", [False, True])
def test_shrink_choice_preserves_the_required_unhibernated_view(tmp_path, plain, direct):
    shrink_hibernated(tmp_path, plain=plain, direct=direct)


def test_hibernated_recovery_gets_a_full_view_alongside_a_clean_windows_shrink(tmp_path):
    from test_shrink_on_capture import REC_SECTORS, SGDISK_WIN_REC, WIN_SECTORS

    probe = PROBE.replace("*/sda2)", "*/sda4)").replace(
        "*/mapper/*)", "*/mapper/lc-src-4)")
    blkid = BLKID.replace("*/sda2|*/mapper/*)", "*/sda3|*/sda4|*/mapper/*)")
    pre = view_sys({3: WIN_SECTORS, 4: REC_SECTORS}) + 'shrink_ask() { echo continue; }; '
    box, run, out = fast_run(tmp_path, pre=pre, extra={
        "sgdisk": SGDISK_WIN_REC, "blkid": blkid, "ntfs-3g.probe": probe,
    })
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8")
    manifest = json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))
    assert manifest["src_unhib"] == "unhibernated"
    win, recovery = manifest["partitions"][1:]
    assert win["size_bytes"] < WIN_SECTORS * 512
    assert recovery["size_bytes"] == REC_SECTORS * 512
    assert "source_start_sector" in recovery
    steps = order(run)
    assert any(s.startswith("partclone ") and "/mapper/lc-cut-3 " in s for s in steps)
    assert any(s.startswith("partclone ") and "/mapper/lc-src-4 " in s for s in steps)
    drops = [s for s in steps if s.startswith("ntfs-3g -o remove_hiberfile")]
    assert len(drops) == 1 and "/mapper/lc-src-4 " in drops[0]
    no_source_writes(box, run)


@pytest.mark.parametrize("direct", [False, True])
def test_inconsistent_measure_is_not_bypassed_for_a_required_view(tmp_path, direct):
    succeeded(fast_run(tmp_path / "control", direct=direct), direct=direct)
    resize = '#!/bin/sh\necho "NTFS is inconsistent"\nexit 1\n'
    box, run, out = fast_run(tmp_path / "bad", direct=direct, extra={"ntfsresize": resize})
    assert "לא עקבית (NTFS)" in refusal_reason(box, run, out)
    assert not any(s.startswith(("modprobe ", "partclone ", "ntfsfix ")) for s in order(run))
    no_source_writes(box, run)


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("journal", [False, True])
def test_hibernated_capture_reads_the_view_reports_orange_and_never_writes_source(tmp_path, direct, journal):
    succeeded(fast_run(tmp_path, direct=direct, env={"JOURNAL": str(int(journal))}),
              direct=direct, action="unhibernated_logfile" if journal else "unhibernated")


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("env,reason,attempted", [
    ({"DROP_FAIL": "1"}, "ביטול ההשהיה בעותק של מחיצה 2 נכשל — לא נקלט. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי — המקור לא נכתב", True),
    ({"READBACK_RC": "14"}, "ביטול ההשהיה בעותק של מחיצה 2 נכשל — לא נקלט. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי — המקור לא נכתב", True),
    ({"READBACK_RC": "18"}, "ביטול ההשהיה בעותק של מחיצה 2 נכשל — לא נקלט. ב-Windows של המקור: ⁦powercfg /h off⁩, ואז כיבוי — המקור לא נכתב", True),
    ({"MEM_KIB": str(1280 * 1024)}, "מחיצה 2: לביטול מצב השינה בעותק נדרשים 384 MB זיכרון, ויש 256 MB — לא נקלט. המקור לא נכתב", False),
    ({"VIEW_RC": "18"}, "לא הצלחנו לבדוק אם Windows בה כובה עד הסוף", False),
    ({"DM_INVALID": "1"}, "אינו תקין אחרי ביטול ההשהיה", True),
])
def test_failed_drop_readback_or_budget_refuses_by_name(tmp_path, direct, env, reason, attempted):
    box, run, out = fast_run(tmp_path, direct=direct, env=env)
    assert reason in refusal_reason(box, run, out)
    steps = order(run)
    assert any(s.startswith("dmsetup create lc-src-2 ") for s in steps), steps
    assert any(s.startswith("ntfs-3g -o remove_hiberfile") for s in steps) == attempted
    assert not any(s.startswith("partclone ") for s in steps)
    assert not (run / "new-manifest.json").exists()
    assert not (box / "sys/module/brd").exists()
    no_source_writes(box, run)


@pytest.mark.parametrize("direct", [False, True])
@pytest.mark.parametrize("env,text,code", [
    ({"SOURCE_FS": "BitLocker"}, "מוצפנת ב-BitLocker", "source_bitlocker"),
    ({"SOURCE_RC": "15"}, "Fast Startup", "source_ntfs"),
    ({"SOURCE_RC": "18"}, "ntfs-3g.probe rc=18", "source_disk"),
    ({"BLKID_FAIL": "1"}, "לא הצלחנו לקרוא את מערכת הקבצים שלה (blkid)", "source_disk"),
])
def test_old_refusals_remain_distinct_from_the_hibernated_success(tmp_path, direct, env, text, code):
    succeeded(fast_run(tmp_path / "hibernated", direct=direct), direct=direct)
    box, run, out = fast_run(tmp_path / "refused", direct=direct, env=env)
    assert text in refusal_reason(box, run, out)
    assert (run / "capture.reason").read_text().strip() == code
    assert not any(s.startswith(("modprobe ", "partclone ", "ntfsfix ")) for s in order(run))
    no_source_writes(box, run)


def test_success_warning_is_sent_before_manifest_can_close_task(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "src_unhib").write_text("unhibernated\n", newline="\n")
    script = (f'RUN_DIR={posix(run)!r}; LOG_FILE="$RUN_DIR/log"; MAC=x; SERVER=http://s; '
              f'. {posix(AGENT)}/lib/capturesink.sh; '
              'report_final() { echo report >> "$RUN_DIR/order"; return "$REPORT_RC"; }; '
              'curl() { echo manifest >> "$RUN_DIR/order"; }; ')
    assert sh(script + 'REPORT_RC=0; upload_manifest t m; echo "rc=$?"').strip() == "rc=0"
    assert (run / "order").read_text().splitlines() == ["report", "manifest"]
    (run / "order").write_text("")
    assert sh(script + 'REPORT_RC=1; upload_manifest t m; echo "rc=$?"').strip() == "rc=1"
    assert (run / "order").read_text().splitlines() == ["report"]


def test_teardown_warning_does_not_erase_the_hibernation_provenance(tmp_path):
    box, run, out = fast_run(tmp_path, env={"FAIL_DM_REMOVE": "1"})
    assert out.strip().endswith("rc=0"), out + (box / "capture.out").read_text(encoding="utf-8")
    report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
    note = report["targets"][0]["error"]
    assert "העותק בזיכרון לא שוחרר" in note and UNHIB in note, note
    assert json.loads((run / "new-manifest.json").read_text(encoding="utf-8"))["src_unhib"] == "unhibernated"
    no_source_writes(box, run)


def test_done_screen_keeps_warning_and_uses_orange_instead_of_silent_green():
    src = AGENT.parent / "native-gui/src"
    screens = (src / "screens.c").read_text(encoding="utf-8")
    done = (src / "screens_capture.c").read_text(encoding="utf-8").split("void screen_done(", 1)[1]
    assert 's->task==TASK_DONE&&s->task_error[0]' in screens
    assert 'RLM "%s",s->task_error)' in screens
    assert 'warning = !failed && s->task_error[0]' in done
    assert 'failed || warning ? ICON_WARNING : ICON_SUCCESS' in done
    assert 'warning ? t->warn' in done


def test_the_source_write_guard_itself_flags_every_write_form(tmp_path):
    """The guard above is the proof of "the source is never written": it must
    see each write form, and pass the reads, or its silence proves nothing."""
    src = "/b/dev/sda"
    writes = [f"ntfsfix -d {src}2", f"ntfs-3g -o remove_hiberfile {src}2 /m",
              f"ntfs-3g {src}2 /m", f"ntfsresize -s 100 {src}2", f"ntfsresize -f -f {src}2",
              f"sgdisk -a 1 -d 2 -n 2:1:9 {src}", f"blockdev --rereadpt {src}"]
    reads = [f"probe --readwrite {src}2", f"ntfs-3g -o ro {src}2 /m",
             f"ntfsresize --info --no-progress-bar {src}2", f"ntfsresize -n -s 1 {src}2",
             f"sgdisk -p {src}", f"sgdisk -i 2 {src}", f"blockdev --getsize64 {src}2",
             "ntfsfix -d /b/dev/mapper/lc-src-2", "ntfs-3g -o remove_hiberfile /b/dev/mapper/lc-src-2 /m"]
    assert [w for w in writes if not _writes_source(w, src)] == []
    assert [r for r in reads if _writes_source(r, src)] == []


#: A partclone that streams, then fails -- the read broke halfway (#2213 teardown).
PARTCLONE_FAILS = PARTCLONE.replace("exit 0\n", '[ -z "${PCL_FAIL:-}" ] || exit 1\nexit 0\n')


def views_released(box, run):
    """No view is left behind: the dmsetup stub keeps dm.<name> per live
    device and removes it on `remove`; brd is gone from /sys/module."""
    steps = order(run)
    assert any(s.startswith("dmsetup create lc-src-2 ") for s in steps), steps
    assert sorted(p.name for p in run.glob("dm.*")) == [], steps
    assert not (box / "sys/module/brd").exists(), steps
    assert "rmmod brd" in steps, steps


@pytest.mark.parametrize("direct", [False, True])
def test_a_read_that_fails_midway_still_releases_the_views(tmp_path, direct):
    """Point 3: a failure after the views exist (partclone of pass 1 / of the
    capture) tears them down -- no /dev/mapper/lc-src-*, no brd held."""
    box, run, out = fast_run(tmp_path, direct=direct, env={"PCL_FAIL": "1"},
                             extra={"partclone.ntfs": PARTCLONE_FAILS})
    assert not out.strip().endswith("rc=0"), out
    steps = order(run)
    assert any(s.startswith("partclone ") and "/mapper/lc-src-2 " in s for s in steps), steps
    assert not (run / "new-manifest.json").exists()
    views_released(box, run)
    no_source_writes(box, run)


#: Pass 2 of the view reads other bytes than pass 1 did -- direct send fails
#: on that partition, after both reads of the view (ESP reads stay equal).
PARTCLONE_VIEW_DIFFERS = PARTCLONE.replace(
    'b="$PCL_BYTE"; [ "$n" -ge 2 ] && [ -n "$PCL_SECOND" ] && b="$PCL_SECOND"\n',
    'b="$PCL_BYTE"; case "$*" in *mapper*) echo x >> "$RUN_DIR/view.reads"\n'
    '[ "$(wc -l < "$RUN_DIR/view.reads")" -lt 2 ] || [ -z "$VIEW_SECOND" ] || b="$VIEW_SECOND" ;; esac\n')


@pytest.mark.parametrize("second", ["", "B"])
def test_direct_send_task_keeps_the_view_through_pass_two_then_releases_it(tmp_path, second):
    """Point 3, through direct_send_task: the one view is read by both passes,
    and only then removed -- on success, and when pass 2 fails (its bytes
    differ from pass 1 on the view)."""
    from test_direct_tail_1171 import TASK_QUIET
    assert PARTCLONE_VIEW_DIFFERS != PARTCLONE
    pre = TASK_QUIET + f'cp {posix(tmp_path / "box" / "answer.json")!r} "$RESP"; export VIEW_SECOND={second!r}; '
    box, run, out = fast_run(tmp_path, direct=True, pre=pre, call="direct_send_task",
                             extra={"partclone.ntfs": PARTCLONE_VIEW_DIFFERS})
    assert out.strip().endswith("rc=0") == (second == ""), out + (box / "direct.out").read_text(encoding="utf-8")
    steps = order(run)
    reads = [i for i, s in enumerate(steps) if s.startswith("partclone ") and "/mapper/lc-src-2 " in s]
    removes = [i for i, s in enumerate(steps) if s.startswith("dmsetup remove --retry lc-src-2")]
    assert len(reads) == 2 and len(removes) == 1, steps
    assert reads[-1] < removes[0], steps            # the view outlives pass 2
    views_released(box, run)
    no_source_writes(box, run)
    if second == "":
        report = json.loads((box / "progress.json").read_text(encoding="utf-8"))
        assert report["targets"][0]["error"] == UNHIB, report   # orange survives the release
