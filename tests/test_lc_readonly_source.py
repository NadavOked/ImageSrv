"""#1466 -- a local clone to a smaller target shrinks a RAM copy-on-write VIEW of
the source (dm-snapshot over the partition, brd as the store), never the
source disk (owner, 16/10, option B: NEVER). When the view cannot be had --
no device-mapper, not enough RAM, a table that does not read back -- the
smaller target is refused by name, with the numbers (``shrink_path=refused``);
the old in-place shrink does not run.

Without disks: dmsetup, modprobe, rmmod, ntfsresize, ntfsfix, sgdisk, blockdev
and curl are shell functions that record every call in ``order.log``, and a
fake /sys + /dev tree stands for the kernel. The checks read that log: a
writing tool that ever names the source's node is the bug this file exists
for. The real device-mapper on a real disk is metal's (the Issue says what).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, posix
from test_local_clone_agent import STREAM_STUBS, run

pytestmark = requires_native(("bash", BASH))

WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
PARTS = (f"1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|U1|2048|204800\n"
         f"3|{WIN}|U3|1085440|998244352\n"
         f"4|DE94BBA4-06D1-4D40-A16A-BFD50179D6AC|U4|999329792|2048000\n")
NEW_BYTES = 112_742_891_520                  # 107520 MiB
NEW_SEC = NEW_BYTES // 512
PLAN = f"3|{WIN}|U3|1085440|998244352|{998244352 * 512}|{NEW_BYTES}\n"
#: partition 4 moves back by what 3 gave up -- the manifest's layout, not the source's
COMPACT = PARTS.replace("|998244352\n", f"|{NEW_SEC}\n").replace(
    "|999329792|", f"|{999329792 - (998244352 - NEW_SEC)}|")

FAKES = r'''
export SYSROOT="$RUN_DIR/sysroot" DEVROOT="$RUN_DIR/devroot"
mkdir -p "$SYSROOT/proc" "$SYSROOT/sys/class/block/sdc3" "$SYSROOT/sys/module" "$SYSROOT/sys/block/sdc" "$DEVROOT" "$LC/run" "$LC/measure"
echo "MemAvailable:   ${MEM_KIB:-8388608} kB" > "$SYSROOT/proc/meminfo"
echo 998244352 > "$SYSROOT/sys/class/block/sdc3/size"; echo 8:35 > "$SYSROOT/sys/class/block/sdc3/dev"
echo 1000215216 > "$SYSROOT/sys/block/sdc/size"
: > "$DEVROOT/sdc3"
ord() { echo "$*" >> "$RUN_DIR/order.log"; }
disk_serial() { printf 'SER-%s' "$1"; }; disk_port() { printf 3; }
_fs_of() { case "$1" in *3) echo ntfs ;; *) echo vfat ;; esac; }
modprobe() {
    ord "modprobe $*"; [ "${FAIL_MODPROBE:-}" != "$1" ] || return 1
    [ "$1" = brd ] || return 0
    mkdir -p "$SYSROOT/sys/module/brd"; _n=${2#rd_nr=}; _s=${3#rd_size=}; _i=0
    while [ "$_i" -lt "$_n" ]; do
        mkdir -p "$SYSROOT/sys/block/ram$_i"; echo $((_s * 2 - ${BRD_SHORT:-0})) > "$SYSROOT/sys/block/ram$_i/size"
        echo "1:$_i" > "$SYSROOT/sys/block/ram$_i/dev"; : > "$DEVROOT/ram$_i"; _i=$((_i + 1))
    done
}
rmmod() { ord "rmmod $*"; [ -z "${FAIL_RMMOD:-}" ] || return 1; rm -rf "$SYSROOT/sys/module/$1"; }
dmsetup() {
    ord "dmsetup $* udev=${DM_DISABLE_UDEV:-}"
    case "$1" in
        create) [ -z "${FAIL_DM_CREATE:-}" ] || return 1
                _nm=$2; set -- $4
                mkdir -p "$DEVROOT/mapper"; : > "$DEVROOT/mapper/$_nm"
                echo "254:$(ls "$RUN_DIR" | grep -c '^mm\.')" > "$RUN_DIR/mm.$_nm"
                case "$3" in
                    snapshot) echo "$1 $2 $3 $(cat "$SYSROOT/sys/class/block/${4##*/}/dev") $(cat "$SYSROOT/sys/block/${5##*/}/dev") $6 ${DM_BAD_CHUNK:-$7}" ;;
                    linear) echo "$1 $2 $3 $(cat "$RUN_DIR/mm.${4##*/}") $5" ;;
                esac > "$RUN_DIR/dm.$_nm" ;;
        table) cat "$RUN_DIR/dm.$2" ;;
        info) eval "_nm=\${$#}"; cat "$RUN_DIR/mm.$_nm" ;;
        status) if [ -n "${DM_INVALID:-}" ]; then echo "0 998244352 snapshot Invalid"; else echo "0 998244352 snapshot 2048/14680064 16"; fi ;;
        remove) [ -z "${FAIL_DM_REMOVE:-}" ] || return 1; rm -f "$DEVROOT/mapper/$3" "$RUN_DIR/dm.$3" "$RUN_DIR/mm.$3" ;;
        ls) _l=$(cd "$RUN_DIR" && ls dm.* 2>/dev/null | sed 's/^dm\.//')
            if [ -n "$_l" ]; then printf '%s\t(254:0)\n' $_l; else echo "No devices found"; fi ;;
    esac
}
ntfsresize() {
    ord "ntfsresize $*"
    case "$*" in
        *--info*) echo "You might resize at 100000000000 bytes or 100000 MB (freeing 411000 MB)." ;;
        "-n "*) [ -n "${NO_RELOC_LINE:-}" ] || echo "Needed relocations : 25600 (${RELOC_MB:-105} MB)"
                echo "The read-only test run ended successfully." ;;
        "-s "*) cat > /dev/null; echo "Needed relocations : 25600 (105 MB)"; return "${NTFSRESIZE_RC:-0}" ;;
    esac
}
ntfsfix() { ord "ntfsfix $*"; return "${NTFSFIX_RC:-0}"; }
# #2208: ntfs-3g.probe --readwrite <node> -- the rc in $RUN_DIR/probe.<node's name>, 0 (clean) when none
ntfsprobe_fake() { ord "ntfs-3g.probe $*"; [ ! -f "$RUN_DIR/probe.${2##*/}" ] || return "$(cat "$RUN_DIR/probe.${2##*/}")"; }
NTFSPROBE=ntfsprobe_fake
sgdisk() { ord "sgdisk $*"; return 1; }
blockdev() {   # --getsize64 of a mapper node: its table's length, as the kernel would say it
    ord "blockdev $*"
    [ "$1" = --getsize64 ] || return 0
    _l=$(awk '{ print $2 }' "$RUN_DIR/dm.${2##*/}" 2>/dev/null) || return 1
    [ -n "$_l" ] && echo $((_l * 512 + ${CUT_SIZE_OFF:-0}))
}
curl() { ord "curl $*"; echo 000; return 7; }
'''


def setup(parts: str = PARTS, cache: bool = True, serial: str = "SER-sdc") -> str:
    """The source screen measured sdc (lc_measure's cache, #1466 P2) and lc_run read its table."""
    t = FAKES + f"printf '%b' {parts!r} > \"$LC/run/parts.txt\"; "
    if cache:
        t += (f"printf '%b' {PLAN!r} > \"$LC/measure/sdc.plan\"; printf '%b' {PARTS!r} > \"$LC/measure/sdc.parts\"; "
              f"printf '%s' {serial!r} > \"$LC/measure/sdc.serial\"; ")
    return t


#: the capture's in-place shrink: a stand-in that records itself -- the local
#: clone must never reach it (owner 16/10, option B)
IN_PLACE = ('shrink_before_capture() { ord "IN_PLACE $*"; shrink_plan "$1" "$2" "$3" || return 1; '
            'ord "IN_PLACE plan=$(cat "$RUN_DIR/shrink.plan")"; mkdir -p "$RUN_DIR/targets/$1"; echo 3 > "$RUN_DIR/targets/$1/shrunk"; return 0; }; ')

SHOW = ('echo "rc=$_rc"; echo "path=$(cat "$LC/run/shrink_path" 2>/dev/null)"; echo "error=$LC_ERROR"; '
        'echo "node3=$(lc_src_node sdc 3)"; echo "node4=$(lc_src_node sdc 4)"; '
        'for f in relocations_mb info_s resize_s snap_teardown; do echo "$f=$(cat "$LC/run/$f" 2>/dev/null)"; done; '
        'echo "brd=$(test -e "$SYSROOT/sys/module/brd" && echo loaded || echo gone)"; '
        'echo "views=$(ls "$DEVROOT/mapper" 2>/dev/null | tr "\\n" " ")"; echo "snapdir=$(test -d "$LC/run/snap" && echo yes || echo no)"; ')


def shrink(tmp_path: Path, pre: str = "", after: str = "", **env) -> tuple[dict[str, str], list[str], Path]:
    out = run(tmp_path, setup() + pre + 'lc_shrink_source sdc; _rc=$?; ' + SHOW + after, **env)
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and not line.startswith("imagectl:"))
    log = tmp_path / "run" / "order.log"
    return got, (log.read_text(encoding="utf-8").splitlines() if log.exists() else []), tmp_path / "run"


def source_writes(order: list[str], devroot: str) -> list[str]:
    """Every call that could write the source disk or its table."""
    src = f"{devroot}/sdc"
    bad = []
    for line in order:
        tool = line.split(" ", 1)[0]
        if tool in ("sgdisk", "IN_PLACE", "curl") or (tool == "blockdev" and "--getsize64" not in line):
            bad.append(line)
        elif tool in ("ntfsresize", "ntfsfix") and src in line and "--info" not in line:
            bad.append(line)
    return bad


# --- the view path: the source is only read -----------------------------------------


def test_the_view_path_never_writes_the_source_disk_or_its_table(tmp_path):
    got, order, run_dir = shrink(tmp_path)
    dev = posix(run_dir / "devroot")
    assert got["rc"] == "0" and got["path"] == "snapshot", (got, order)
    view = f"{dev}/mapper/lc-src-3"
    cut = f"{dev}/mapper/lc-cut-3"
    # the view over the real partition, its store a RAM disk sized from MemAvailable minus the reserve
    assert "modprobe dm-snapshot" in order
    assert f"modprobe brd rd_nr=1 rd_size={8388608 - 1024 * 1024}" in order, order
    assert f"dmsetup create lc-src-3 --table 0 998244352 snapshot {dev}/sdc3 {dev}/ram0 N 8 udev=1" in order, order
    # the budget on the view, then the shrink and the dirty flag on the view only
    assert order.index(f"ntfsresize -n -s {NEW_BYTES} --no-progress-bar {view}") \
        < order.index(f"ntfsresize -s {NEW_BYTES} --no-progress-bar {view}") < order.index(f"ntfsfix -d {view}")
    assert source_writes(order, dev) == [], source_writes(order, dev)
    assert not any("--info" in line for line in order), order          # P2: lc_measure's plan reused
    # the targets get the compacted layout from the plan; the copy reads the view
    assert (run_dir / "lc" / "run" / "parts.txt").read_text() == COMPACT
    assert (run_dir / "parts.orig.txt").read_text() == PARTS
    # the copy reads the cut: the view at exactly the new partition length (Testrunner 16/10)
    assert f"dmsetup create lc-cut-3 --table 0 {NEW_SEC} linear {view} 0 udev=1" in order, order
    assert f"blockdev --getsize64 {cut}" in order, order
    assert got["node3"] == cut and got["node4"] == f"{dev}/sdc4", got
    assert not (run_dir / "targets" / "sdc" / "shrunk").exists()       # nothing to put back
    assert not (run_dir / "lc" / "run" / "shrunk").exists()            # the screen: "read only, not changed"
    assert got["relocations_mb"] == "105" and got["info_s"].isdigit() and got["resize_s"].isdigit(), got


def test_the_views_are_released_and_read_back(tmp_path):
    got, order, _ = shrink(tmp_path, after='lc_snap_teardown; echo "td=$?"; ' + SHOW.replace("_rc", "_rc2"))
    lines = order
    assert "dmsetup remove --retry lc-src-3 udev=1" in lines and "dmsetup ls udev=1" in lines and "rmmod brd" in lines
    # the cut first -- it holds the view open -- then the view, then the RAM disk
    assert lines.index("dmsetup remove --retry lc-cut-3 udev=1") < lines.index("dmsetup remove --retry lc-src-3 udev=1") \
        < lines.index("rmmod brd")
    assert got["td"] == "0" and got["snap_teardown"] == "ok" and got["brd"] == "gone", got
    assert got["views"].strip() == "" and got["snapdir"] == "no", got
    assert got["node3"].endswith("/sdc3"), got                            # the copy no longer points at a view


# --- no view: the smaller target is refused, by name --------------------------------

FALLBACKS = [
    ({"MEM_KIB": str(1024 * 1024)}, "", "פנויים 1024 MB זיכרון, ו-1024 MB שמורים להעתקה עצמה", False),
    ({"RELOC_MB": "8000"}, "", "להזזת 8000 MB נדרשים 9056 MB זיכרון, ויש 7168 MB", True),
    ({"NO_RELOC_LINE": "1"}, "", "לא אמר כמה צריך להזיז", True),
    ({"FAIL_DM_CREATE": "1"}, "", "dmsetup create lc-src-3 נכשל", True),
    ({"DM_BAD_CHUNK": "16"}, "", "לא נקרא בחזרה כפי שנוצר", True),
    ({"BRD_SHORT": "2"}, "", "לא עלה בגודל 7168 MB", True),
    ({"FAIL_MODPROBE": "dm-snapshot"}, "", "המודול dm-snapshot לא נטען", False),
    ({"FAIL_MODPROBE": "brd"}, "", "המודול brd לא נטען", False),   # not loaded: nothing to unload
    ({"DM_INVALID": "1"}, "", "אינו תקין אחרי הכיווץ", True),
    # #1519 gate: the CI runner has a real dmsetup on PATH -- hide every directory that holds one
    ({}, 'unset -f dmsetup; _p=; IFS=:; for _d in $PATH; do [ -x "$_d/dmsetup" ] || _p="$_p${_p:+:}$_d"; done; unset IFS; PATH=$_p; ',
     "dmsetup אינו ב-initramfs הזה", False),
    ({}, 'mkdir -p "$SYSROOT/sys/module/brd"; ', "כבר טעון", False),
    ({"CUT_SIZE_OFF": "512"}, "", "לא נקרא בחזרה בגודלו החדש", True),   # the cut is not the new length
]


@pytest.mark.parametrize("env,pre,why,made", FALLBACKS)
def test_no_view_refuses_the_smaller_target_by_name(tmp_path, env, pre, why, made):
    """Could not check / does not fit -> refused, with the reason and its numbers in
    Hebrew; never a silent snapshot, never the source shrunk in place (option B)."""
    got, order, run_dir = shrink(tmp_path, pre=pre + IN_PLACE, **env)
    assert got["rc"] == "1" and got["path"] == "refused", (got, order)
    assert why in got["error"] and "המקור לא נכתב" in got["error"], got          # the screen's words
    said = [l for l in (run_dir / "agent.log").read_text(encoding="utf-8").splitlines() if "refused" in l]
    assert len(said) == 1 and why in said[0], said                          # and the log's
    assert source_writes(order, posix(run_dir / "devroot")) == [], source_writes(order, posix(run_dir / "devroot"))
    assert not any("--info" in line for line in order), order
    # nothing of the view is left: no node, no module, no pointer for the copy
    assert got["views"].strip() == "" and got["snapdir"] == "no", got
    assert got["brd"] == ("loaded" if "כבר טעון" in why else "gone"), got   # a brd we did not load is not ours to unload
    assert got["node3"].endswith("/sdc3"), got
    assert ("rmmod brd" in order) == made, order
    assert not (run_dir / "lc" / "run" / "shrunk").exists()
    assert (run_dir / "lc" / "run" / "parts.txt").read_text() == PARTS     # no compacted layout for any target


def test_a_shrink_the_view_refuses_fails_by_name_and_the_source_is_untouched(tmp_path):
    """ntfsresize said no on a valid view: failed by name, the source untouched."""
    got, order, run_dir = shrink(tmp_path, pre=IN_PLACE, NTFSRESIZE_RC="1")
    assert got["rc"] == "1" and got["path"] == "snapshot", got
    assert "המקור לא נכתב" in got["error"] and "rc=1" in got["error"], got
    # the headline is Hebrew; the tool's own last line goes to the log, not the screen
    assert "Needed relocations" not in got["error"], got
    assert "failed (rc=1): Needed relocations" in (run_dir / "agent.log").read_text(encoding="utf-8")
    assert not any(l.startswith("IN_PLACE") for l in order), order
    assert source_writes(order, posix(run_dir / "devroot")) == []
    assert got["brd"] == "gone" and got["snapdir"] == "no" and got["node3"].endswith("/sdc3"), got


def test_a_view_that_cannot_be_released_is_said_with_the_refusal(tmp_path):
    """Principle 5: a view still held is said, next to why there was none to use."""
    got, order, _ = shrink(tmp_path, pre=IN_PLACE, RELOC_MB="8000", FAIL_DM_REMOVE="1")
    assert got["rc"] == "1", got
    assert not any(l.startswith("IN_PLACE") for l in order), order
    assert "לא שוחרר" in got["error"] and "dmsetup remove lc-src-3 failed" in got["error"], got
    assert "להזזת 8000 MB" in got["error"], got
    assert "lc-src-3 is still listed" in got["error"], got
    assert got["node3"].endswith("/sdc3"), got                            # no copy reads a half-made view


# --- the end of the run: release, the screen's line, the report ----------------------

END = ('_lr_r=$LC/run; _lr_src=sdc; sleep 30 & _lr_beat=$!; date +%s > "$LC/run/started"; echo sda > "$LC/run/targets"; '
       'target_init sda 0; target_set sda done; echo 7 > "$LC/run/copy_s"; echo 2 > "$LC/run/verify_s"; '
       'lc_end; echo "restore=$(cat "$LC/run/source_restore" 2>/dev/null)"; ')


def _report(run_dir: Path) -> dict:
    return json.loads((run_dir / "lc" / "run" / "report.json").read_text(encoding="utf-8"))


def test_the_report_names_the_path_and_the_stage_times(tmp_path):
    got, order, run_dir = shrink(tmp_path, after=END)
    rep = _report(run_dir)
    assert rep["shrink_path"] == "snapshot" and rep["snapshot_teardown"] == "ok" and rep["shrunk"] is False, rep
    assert rep["relocations_mb"] == 105 and rep["copy_s"] == 7 and rep["verify_s"] == 2, rep
    for k in ("info_s", "resize_s", "restore_s"):
        assert isinstance(rep[k], int) and rep[k] >= 0, (k, rep)
    assert got["restore"] == "", got                                        # the screen: "read only, not changed"
    assert "rmmod brd" in order


def test_a_release_that_failed_is_on_the_screen_and_in_the_report(tmp_path):
    got, _, run_dir = shrink(tmp_path, after=END, FAIL_DM_REMOVE="1")
    assert got["rc"] == "0" and got["path"] == "snapshot", got
    assert "העותק בזיכרון לא שוחרר" in got["restore"] and "dmsetup remove lc-src-3 failed" in got["restore"], got
    rep = _report(run_dir)
    assert "dmsetup remove lc-src-3 failed" in rep["snapshot_teardown"], rep
    assert rep["source_restore"] == got["restore"], rep


def test_a_run_without_a_shrink_reports_nulls_not_zeros(tmp_path):
    out = run(tmp_path, FAKES + END.replace('echo 7 > "$LC/run/copy_s"; echo 2 > "$LC/run/verify_s"; ', ""))
    assert out.returncode == 0, out.stderr
    rep = _report(tmp_path / "run")
    assert rep["shrink_path"] is None and rep["snapshot_teardown"] == "", rep
    for k in ("info_s", "resize_s", "relocations_mb", "copy_s", "verify_s", "restore_s"):
        assert rep[k] is None, (k, rep)


# --- P2: the plan is reused only on the same disk and table ---------------------------


@pytest.mark.parametrize("parts,serial", [
    (PARTS.replace("|2048000\n", "|2049024\n"), "SER-sdc"),   # the table changed since the screen measured it
    (PARTS, "SER-other"),                                       # another disk in the drawer
])
def test_a_stale_measurement_is_measured_again(tmp_path, parts, serial):
    out = run(tmp_path, setup(parts=parts, serial=serial) + 'lc_shrink_plan sdc; echo "rc=$?"')
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout, out.stdout
    log = tmp_path / "run" / "order.log"
    order = log.read_text(encoding="utf-8") if log.exists() else ""   # no file = no tool ran at all
    assert "--info" in order, f"the stale plan was reused, not measured again: {order!r}"


def test_measure_leaves_the_plan_for_the_run(tmp_path):
    """lc_measure's side of P2: the plan and the serial it was taken on."""
    out = run(tmp_path, FAKES + 'shrink_plan() { printf "%s" "PLAN" > "$RUN_DIR/shrink.plan"; }; '
              'lc_inv_field() { case "$2" in 11) echo 600000000000 ;; 5) echo 256000000000 ;; esac; }; '
              'list_drawers() { echo sda; echo sdc; }; capture_ntfs_hibernation_reason() { return 0; }; '
              f'mkdir -p "$LC/inv"; printf "%b" {PARTS!r} > "$LC/inv/sdc.parts"; '
              'lc_measure sdc; echo "plan=$(cat "$LC/measure/sdc.plan")"; echo "serial=$(cat "$LC/measure/sdc.serial")"')
    assert out.returncode == 0, out.stderr
    assert "plan=PLAN" in out.stdout and "serial=SER-sdc" in out.stdout, out.stdout


def test_a_smaller_target_is_measured_with_no_server(tmp_path):
    """#1478: the source screen offers a smaller target with the server down.
    The run shrinks a RAM view and never writes the source, so there is no
    original layout for the server to record (#926 is the capture's).

    **Negative control:** on lcinv.sh before #1478, lc_measure asked the server
    (FAKES' curl answers 000) and wrote ``failed|<min>|...השרת לא עונה`` --
    the target was ``unchecked`` and could not be chosen."""
    out = run(tmp_path, FAKES + f'shrink_plan() {{ printf "%b" {PLAN!r} > "$RUN_DIR/shrink.plan"; }}; '
              'lc_inv_field() { case "$2" in 11) echo 512110190592 ;; 5) echo 256060514304 ;; esac; }; '
              'list_drawers() { echo sda; echo sdc; }; capture_ntfs_hibernation_reason() { return 0; }; '
              f'mkdir -p "$LC/inv"; printf "%b" {PARTS!r} > "$LC/inv/sdc.parts"; '
              'lc_measure sdc; echo "rc=$?"; echo "m=$(cat "$LC/measure/sdc")"; '
              'echo "v=$(lc_verdict 256060514304 512110190592 "$(cut -d"|" -f1 "$LC/measure/sdc")" "$(cut -d"|" -f2 "$LC/measure/sdc")" "")"')
    assert out.returncode == 0, out.stderr
    got = dict(line.split("=", 1) for line in out.stdout.splitlines() if "=" in line and not line.startswith("imagectl:"))
    state, need, why = got["m"].split("|")
    assert (got["rc"], state, why) == ("0", "done", ""), out.stdout
    assert need.isdigit() and int(need) < 256060514304, out.stdout        # the compacted layout fits the target
    assert got["v"] == f"shrink|{need}|", out.stdout                       # offered, not "unchecked"
    log = tmp_path / "run" / "order.log"
    order = log.read_text(encoding="utf-8") if log.exists() else ""
    assert "curl" not in order, f"the measurement asked the server: {order!r}"


# --- the copy reads the view ------------------------------------------------------------


def test_the_copy_reads_the_shrunk_view_not_the_partition(tmp_path):
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"
    (dev / "mapper").mkdir(parents=True)
    (dev / "sdc1").write_bytes(b"source-partition" * 1024)
    (dev / "mapper" / "lc-src-1").write_bytes(b"shrunk-view" * 1024)
    make = [f"mkdir -p {posix(stubs)!r}"]
    for name, body in STREAM_STUBS.items():
        make += [f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB", f"chmod 0755 {posix(stubs)}/{name}"]
    t = "\n".join(make) + "\n"
    t += f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" STUB_BOX={posix(box)!r} DEVROOT={posix(dev)!r}; '
    t += 'mkdir -p "$LC/run/snap"; echo "$DEVROOT/mapper/lc-src-1" > "$LC/run/snap/1.node"; ata_capture() { :; }; '
    t += f'target_init sda 100; lc_stream 1 dd sdc sda > {posix(box)}/stream.log 2>&1; echo "rc=$?"'
    out = run(tmp_path, t, timeout=120, WAIT_HELPER_S="20", WAIT_DRAWER_S="20")
    assert out.returncode == 0, out.stderr
    assert "rc=0" in out.stdout, out.stdout
    assert (dev / "sda1").read_bytes() == (dev / "mapper" / "lc-src-1").read_bytes()
    assert hashlib.md5((dev / "sda1").read_bytes()).digest() != hashlib.md5((dev / "sdc1").read_bytes()).digest()
