"""#3268 (Nadav 14/11) -- the write check on the waiting screen, with a copy.

The disks are sparse image files under ``DEVROOT``; two PATH shims stand in for what the
Testrunner's loop devices and the server would give: ``dd`` (strips ``iflag/oflag=direct``, which a
tmpfs refuses, and logs every write to a disk image) and ``curl`` (a file-based server with the
contract of ``server/disks/write_backup.py``, which test_write_backup_3268.py runs for real).
What is proved from outside the code under test: the image's own bytes, before and after.

The second half is the security review of #3271 (H1-H4, M1-M3): a disk swapped under the name,
a task that touches a drawer while the check is on it, a region cut in the middle and a twin
serial, a dd that outlives the check's ceiling -- each with a test that fails on the code before.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest
from test_smart import BASH, posix  # noqa: F401 -- the same bash, declared skip without it

from native import requires_native
from agentlib import flat_lib

pytestmark = requires_native(("bash", BASH), ("jq", shutil.which("jq")), ("sha256sum", shutil.which("sha256sum")))

MIB = 1048576
LIBS = ("common", "jsonq", "waits", "sysinfo", "diskinfo", "progress", "failmark", "hibernation", "writeback", "drawers",
        "writecheck", "idlecheck", "idlerecover", "idlegate", "capturegate")

DD_SHIM = r'''#!/bin/bash
args=(); out=""
for a in "$@"; do
    case "$a" in iflag=direct|oflag=direct) continue ;; of=*) out=${a#of=} ;; conv=fsync) a=conv=fsync,notrunc ;; esac   # a block device is never truncated; a file is
    args+=("$a")
done
case "$out" in "$DEVROOT"/*)
    echo "$out" >> "$SHIM_LOG/writes"
    if [ -e "$SHIM_LOG/hang" ]; then   # a write that does not come back: the first one hangs (and, with late/, leaves a dd behind)
        rm -f "$SHIM_LOG/hang"
        [ ! -d "$SHIM_LOG/late" ] || setsid "$SHIM_LOG/late/dd" "${args[@]}" < /dev/null > /dev/null 2>&1 &
        sleep 12 < /dev/null > /dev/null 2>&1; exit 1
    fi ;;
esac
exec "$REAL_DD" "${args[@]}"
'''

LATE_DD = r'''#!/bin/bash
# a dd of the check that outlives it: it holds its pattern and writes it a while later
src=""; for a in "$@"; do case "$a" in if=*) src=${a#if=} ;; esac; done
cp "$src" "$0.in"
sleep 8
args=(); for a in "$@"; do case "$a" in if=*) a="if=$0.in" ;; esac; args+=("$a"); done
"$REAL_DD" "${args[@]}" 2> /dev/null; touch "$0.done"
'''

CURL_SHIM = r'''#!/bin/bash
M=GET; BODY=""; OUT=""; URL=""
while [ $# -gt 0 ]; do
    case "$1" in -X) M=$2; shift ;; --data-binary) BODY=${2#@}; shift ;; -o) OUT=$2; shift ;; --max-time|-H) shift ;; -*) ;; *) URL=$1 ;; esac
    shift
done
q=${URL#*\?}; path=${URL%%\?*}
param() { printf '%s' "$q" | tr '&' '\n' | sed -n "s/^$1=//p"; }
S=$(param serial); D=$FAKE_SRV
ID=0123456789abcdef0123456789abcdef
if [ "$M" = POST ] && [ "${path%/stale}" != "$path" ]; then
    [ -e "$D/$S.meta" ] || exit 22
    param blocks > "$D/$S.stale"; printf '{"ok":true}'
elif [ "$M" = POST ]; then
    echo "$S" >> "$SHIM_LOG/posts"   # a copy was asked to be kept: the disk was read for it. The fake server's own log,
    # so it is kept OUT of srv/: srv/ is what the server holds, and a verified restore must leave it empty (#3271)
    [ ! -e "$D/refuse" ] || exit 22
    [ ! -e "$D/$S.bin" ] || exit 22
    sha=$(sha256sum "$BODY" | cut -d' ' -f1)
    [ "$sha" = "$(param sha256)" ] || exit 22
    sum=$(param regions | tr ',' '\n' | awk -F: '{ s += $2 } END { print s }'); got=$(stat -c %s "$BODY"); x=$(param expect)
    # the originals, then what the check writes there (#3271). The body before it (the originals + expect) is
    # taken too: a negative control on the code before fails on the finding, not on this fake server
    [ "$got" = $((2 * sum)) ] || { [ "$got" = "$sum" ] && [ -n "$x" ]; } || exit 22
    cp "$BODY" "$D/$S.bin"; echo "$(param size) $(param regions) $sha ${x:--}" > "$D/$S.meta"
    if [ -e "$D/swap_on_post" ]; then   # the operator swaps the drawer while the copy is on its way
        rm -f "$D/swap_on_post"; cp "$D/b.img" "$DEVROOT/sdx"; printf SER2 > "$SYSROOT/sys/block/sdx/device/serial"
    fi
    printf '{"ok":true,"id":"%s","sha256":"%s"}' "$ID" "$sha"
elif [ "$M" = DELETE ]; then
    [ -e "$D/nodelete" ] && exit 22
    rm -f "$D/$S.bin" "$D/$S.meta" "$D/$S.stale"; printf '{"ok":true}'
elif [ "${path%/blob}" != "$path" ]; then
    cp "$D/$S.bin" "$OUT"
elif [ -e "$D/$S.meta" ]; then
    read -r size regions sha x < "$D/$S.meta"; e=""; [ "$x" = - ] || e=",\"expect\":\"$x\""
    printf '{"ok":true,"backups":[{"id":"%s","size":%s,"regions":"%s","sha256":"%s"%s}]}' "$ID" "$size" "$regions" "$sha" "$e"
else
    printf '{"ok":true,"backups":[]}'
fi
'''

DRIVER = r'''
set -u
T=$1; shift
REAL_DD=$(command -v dd)
export REAL_DD PATH=$T/bin:$PATH RUN_DIR=$T/run SYSROOT=$T DEVROOT=$T/dev SERVER=http://srv MAC=aa:bb:cc:dd:ee:ff LOG_FILE=$T/run/agent.log
export D_ROLE=cloner SHIM_LOG=$T FAKE_SRV=$T/srv WAIT_WRITECHECK_S=60 IDLE_WC_BUSY_WAIT_S=2 IDLE_WC_KILL_S=3 HTTP_TIMEOUT=10
mkdir -p "$RUN_DIR"; touch "$LOG_FILE"
for n in %(libs)s; do [ ! -f %(lib)s/$n.sh ] || . %(lib)s/$n.sh; done
smart_probe() { cat "$SHIM_LOG/smart" 2>/dev/null || echo ok; }   # smart.sh is not loaded: what smartctl would have said
set +e
eval "$@"
'''


def _make(tmp_path: Path, serial: str = "SER1", size_mib: int = 64) -> Path:
    t = tmp_path
    for d in ("bin", "dev", "srv", "run", "sys/block/sdx/device", "sys/block/sdx/queue", "proc"):
        (t / d).mkdir(parents=True, exist_ok=True)
    (t / "proc/mounts").write_text("")
    (t / "proc/uptime").write_text("100.00 100.00\n")
    img = t / "dev" / "sdx"
    img.write_bytes(os.urandom(size_mib * MIB))
    (t / "sys/block/sdx/size").write_text(str(size_mib * MIB // 512))
    (t / "sys/block/sdx/removable").write_text("0")   # an internal drawer (list_drawers, #1832)
    (t / "sys/block/sdx/device/serial").write_text(serial)
    (t / "sys/block/sdx/queue/rotational").write_text("1")
    for name, body in (("dd", DD_SHIM), ("curl", CURL_SHIM),
                       ("blockdev", '#!/bin/bash\nstat -c %s "$2"\n')):
        p = t / "bin" / name
        p.write_text(body, newline="\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    (t / "run" / "response.json").write_text('{"writecheck_min_mbps": 74}')
    return img


def _run(t: Path, script: str) -> subprocess.CompletedProcess:
    driver = DRIVER % {"libs": " ".join(LIBS), "lib": posix(flat_lib())}
    return subprocess.run([BASH, "-c", driver, "x", posix(t), script], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=240)


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _writes(t: Path) -> list[str]:
    f = t / "writes"
    return f.read_text().split() if f.exists() else []


def _res(t: Path, name: str = "sdx") -> dict[str, str]:
    d = t / "run" / "idlecheck" / f"res.{name}"
    return {k: (d / k).read_text() for k in ("state", "reason", "key", "mbps")} if d.is_dir() else {}


def _copies(t: Path) -> list[Path]:
    return list((t / "srv").glob("*.bin"))


def test_the_check_runs_and_the_disk_comes_back_byte_for_byte(tmp_path):
    img = _make(tmp_path)
    before = _sha(img)
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert _res(tmp_path)["state"] == "ok", (_res(tmp_path), out.stderr)
    assert _writes(tmp_path), "the check wrote nothing: the proof below would prove nothing"
    assert _sha(img) == before, "the original bytes are not back"
    assert not list((tmp_path / "srv").iterdir()), "the copy is still on the server after a verified restore"


def test_no_confirmation_from_the_server_means_no_write(tmp_path):
    img = _make(tmp_path)
    (tmp_path / "srv" / "refuse").write_text("")
    before = _sha(img)
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert _res(tmp_path)["state"] == "unchecked" and "לא אישר" in _res(tmp_path)["reason"]
    assert _writes(tmp_path) == [], "it wrote without a stored copy"
    assert _sha(img) == before


def test_a_cut_in_the_middle_is_put_back_at_the_next_boot(tmp_path):
    img = _make(tmp_path)
    before = _sha(img)
    # the first boot: the check wrote, and the restore never happened (the power went)
    out = _run(tmp_path, "_ic_putback() { return 1; }; idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert _sha(img) != before, "the fixture did not leave the test patterns on the disk"
    assert _copies(tmp_path), "the copy was dropped although the disk was not restored"
    # the next boot: a fresh shell, nothing remembered but the server's copy
    out = _run(tmp_path, "idle_wc_recover sdx; echo RC=$?")
    assert "RC=0" in out.stdout, out.stderr
    assert _sha(img) == before, "the recovery did not bring the original bytes back"
    assert not list((tmp_path / "srv").iterdir())
    assert "WRITE CHECK RECOVERY" in (tmp_path / "run" / "agent.log").read_text()


def test_a_copy_the_server_cannot_drop_is_not_called_a_clean_check(tmp_path):
    img = _make(tmp_path)
    before = _sha(img)
    (tmp_path / "srv" / "nodelete").write_text("")
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    r = _res(tmp_path)
    assert r["state"] == "unchecked" and "לא נמחק" in r["reason"], r
    assert _sha(img) == before, "the disk is restored even when the server cannot drop the copy"
    assert _copies(tmp_path), "the copy stays: the next boot may need it"


def test_bitlocker_is_not_checked_and_not_written(tmp_path):
    img = _make(tmp_path)
    (tmp_path / "sys/block/sdx/sdx1").mkdir()
    (tmp_path / "dev" / "sdx1").write_bytes(b"\xeb\x58\x90-FVE-FS-" + bytes(500))
    before = _sha(img)
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    r = _res(tmp_path)
    assert r["state"] == "unchecked" and "BitLocker" in r["reason"], r
    assert _writes(tmp_path) == [] and _sha(img) == before


def test_a_mounted_disk_is_not_written(tmp_path):
    img = _make(tmp_path)
    (tmp_path / "proc/mounts").write_text(f"{tmp_path}/dev/sdx1 /mnt ext4 rw 0 0\n")
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert _res(tmp_path)["state"] == "unchecked", out.stderr
    assert _writes(tmp_path) == []
    assert _sha(img)


def _seed(tmp_path: Path, name: str, state: str, mbps: str = "", reason: str = "", key: str = "SER1|131072") -> None:
    d = tmp_path / "run" / "idlecheck" / f"res.{name}"
    d.mkdir(parents=True, exist_ok=True)
    for k, v in (("key", key), ("state", state), ("reason", reason), ("mbps", mbps)):
        (d / k).write_text(v)


def test_slow_is_red_with_its_numbers_before_any_round(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "ok", mbps="59")
    out = _run(tmp_path, "idle_wc_field sdx")
    assert out.stdout.strip() == "slow|איטי — 59 MB/s, מתחת לסף 74 MB/s", (out.stdout, out.stderr)


def test_fast_is_ok_and_not_yet_measured_is_grey_checking(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "ok", mbps="120")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "ok|"
    assert _run(tmp_path, "idle_wc_field sdy").stdout.strip() == "checking|"   # no result: never ok
    (tmp_path / "run" / "idlecheck" / "busy.sdx").write_text("")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "checking|"


def test_a_check_that_failed_or_did_not_run_is_said_never_ok(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "unchecked", reason="אין מספר סידורי")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "unchecked|אין מספר סידורי"
    _seed(tmp_path, "sdx", "failed", reason="הדיסק לא מקבל כתיבה — x")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "failed|הדיסק לא מקבל כתיבה — x"
    _seed(tmp_path, "sdx", "ok", mbps="")   # ok without a number: no verdict of slow -- and no number to show
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "ok|"


STUB = "idle_wc_disk() { echo \"$1\" >> %s/called; }; list_disks() { %s; }"


def test_a_replaced_or_reinserted_disk_is_checked_again_but_a_standing_one_is_not(tmp_path):
    _make(tmp_path)
    stub = STUB % (posix(tmp_path), "echo sdx")
    _seed(tmp_path, "sdx", "ok", mbps="120", key="SER1|131072")
    _run(tmp_path, f"{stub}; idle_wc_scan; wait")
    assert not (tmp_path / "called").exists(), "a disk that stood there was checked again"
    (tmp_path / "sys/block/sdx/device/serial").write_text("OTHER")   # the swap: same name, another disk
    _run(tmp_path, f"{stub}; idle_wc_scan; wait")
    assert (tmp_path / "called").read_text().split() == ["sdx"], "a replaced disk kept the old result"
    (tmp_path / "called").unlink()
    _seed(tmp_path, "sdx", "ok", mbps="120", key="OTHER|131072")
    _run(tmp_path, STUB % (posix(tmp_path), ":") + "; idle_wc_scan; wait")   # pulled out: the result goes
    assert not (tmp_path / "run/idlecheck/res.sdx").exists()
    _run(tmp_path, f"{stub}; idle_wc_scan; wait")   # the same serial put back in = a new insertion
    assert (tmp_path / "called").read_text().split() == ["sdx"], "a disk put back was not checked again"


def test_a_round_takes_the_result_of_its_disk_and_a_missing_or_foreign_one_is_written(tmp_path):
    _make(tmp_path)
    _seed(tmp_path, "sdx", "ok", mbps="120", reason="נכתבו")
    out = _run(tmp_path, "idle_wc_adopt sdx; echo RC=$?")
    assert "RC=0" in out.stdout, out.stderr
    t = tmp_path / "run" / "targets" / "sdx"
    assert (t / "write_check_state").read_text() == "ok" and (t / "write_check_mbps").read_text() == "120"
    assert "RC=1" in _run(tmp_path, "idle_wc_adopt sdy; echo RC=$?").stdout
    _seed(tmp_path, "sdx", "ok", mbps="120", key="OLD|131072")
    assert "RC=1" in _run(tmp_path, "idle_wc_adopt sdx; echo RC=$?").stdout
    _seed(tmp_path, "sdx", "failed", reason="x")
    assert "RC=1" in _run(tmp_path, "idle_wc_adopt sdx; echo RC=$?").stdout, "a failed waiting-screen check must be written again by the round"


def _kind(tmp_path: Path, name: str, rotational: str, mbps: str) -> None:
    q = tmp_path / "sys/block" / name / "queue"
    q.mkdir(parents=True, exist_ok=True)
    (q / "rotational").write_text(rotational)
    _seed(tmp_path, name, "ok", mbps=mbps, key=f"{name}|1")


def _answer(tmp_path: Path, **extra) -> None:
    (tmp_path / "run" / "response.json").write_text(json.dumps({"writecheck_min_mbps": 74, **extra}))


def test_hdd_support_off_a_mechanical_disk_is_red_as_a_target_even_when_fast(tmp_path):
    _make(tmp_path)
    _answer(tmp_path, hdd_support=False)
    _kind(tmp_path, "sdx", "1", "150")      # fast, and mechanical
    _kind(tmp_path, "sdy", "0", "150")      # an SSD at the same speed
    hdd = _run(tmp_path, "idle_wc_field sdx").stdout.strip()
    assert hdd.startswith("slow|") and "מכני" in hdd and "150 MB/s" in hdd and "כמקור בלבד" in hdd, hdd
    assert _run(tmp_path, "idle_wc_field sdy").stdout.strip() == "ok|", "the switch must not touch an SSD"


def test_hdd_support_on_a_mechanical_disk_is_judged_against_mechanical_disks_only(tmp_path):
    _make(tmp_path)
    _answer(tmp_path, hdd_support=True)
    _kind(tmp_path, "sdx", "1", "59")       # under the floor of 74 an SSD must meet
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "ok|", "an HDD alone is not 'slow' because it is an HDD"
    _kind(tmp_path, "sdy", "1", "200")
    _kind(tmp_path, "sdz", "1", "210")
    why = _run(tmp_path, "idle_wc_field sdx").stdout.strip()
    assert why.startswith("slow|") and "פחות מחצי" in why and "(מכני)" in why, why
    _kind(tmp_path, "sdw", "0", "40")       # an SSD under the floor stays red: the floor is the SSD's
    assert _run(tmp_path, "idle_wc_field sdw").stdout.strip().startswith("slow|איטי — 40 MB/s, מתחת לסף 74")


def test_an_older_server_that_says_nothing_keeps_the_rule_as_it_was(tmp_path):
    _make(tmp_path)
    _answer(tmp_path)
    _kind(tmp_path, "sdx", "1", "150")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip() == "ok|"
    _kind(tmp_path, "sdx", "1", "59")
    assert _run(tmp_path, "idle_wc_field sdx").stdout.strip().startswith("slow|איטי — 59 MB/s, מתחת לסף 74")


# --- the recovery goes block by block: the original -> nothing, the check's pattern -> put back, anything else -> not touched ---

PLACE = 4 * MIB
MID = 32 * MIB


def _cut(tmp_path: Path) -> tuple[Path, bytes]:
    """The first boot: the check wrote its patterns and the power went before the restore."""
    img = _make(tmp_path)
    original = img.read_bytes()
    out = _run(tmp_path, "_ic_putback() { return 1; }; idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert img.read_bytes() != original and _copies(tmp_path), "the fixture did not leave a cut check behind"
    return img, original


def _put(img: Path, offset: int, data: bytes) -> None:
    with img.open("r+b") as f:
        f.seek(offset)
        f.write(data)


def _recover(tmp_path: Path) -> str:
    out = _run(tmp_path, "idle_wc_recover sdx; echo RC=$?")
    assert "RC=0" in out.stdout, (out.stdout, out.stderr)
    return (tmp_path / "run" / "agent.log").read_text()


def test_a_copy_over_a_disk_that_was_written_since_touches_nothing_and_is_kept(tmp_path):
    img, _original = _cut(tmp_path)
    _put(img, 0, os.urandom(64 * MIB))           # an image was written over the whole disk
    written = img.read_bytes()
    log = _recover(tmp_path)
    assert img.read_bytes() == written, "the recovery overwrote an image"
    assert "not touched" in log
    assert _copies(tmp_path), "#3271 H3: the agent never deletes a copy it could not put back whole"


def test_one_region_that_is_neither_original_nor_pattern_is_left_the_rest_put_back_and_the_copy_kept_stale(tmp_path):
    img, original = _cut(tmp_path)
    other = os.urandom(PLACE)
    _put(img, MID, other)                         # something else was written over the middle place
    log = _recover(tmp_path)
    got = img.read_bytes()
    assert got[:MID] == original[:MID], "the first place and the markers before the middle were not put back"
    assert got[MID:MID + PLACE] == other, "the region that held something else was touched"
    assert got[MID + PLACE:] == original[MID + PLACE:], "the last place was not put back"
    assert "STALE" in log
    assert _copies(tmp_path), "#3271 H3: a stale copy is kept, not dropped"
    assert int((tmp_path / "srv" / "SER1.stale").read_text()) >= PLACE // 4096, "the copy was not marked stale with its blocks"


def test_a_restore_that_was_cut_is_finished_only_where_the_pattern_still_is(tmp_path):
    img, original = _cut(tmp_path)
    _put(img, 0, original[:PLACE])                # the first place was already put back before the next cut
    log = _recover(tmp_path)
    assert img.read_bytes() == original, "the rest of the check's pattern was not put back"
    assert "STALE" not in log
    assert not list((tmp_path / "srv").iterdir())


def test_a_copy_over_an_untouched_disk_is_just_dropped(tmp_path):
    img = _make(tmp_path)
    original = img.read_bytes()
    out = _run(tmp_path, "_ic_check() { _ic_st=failed; _ic_why=x; _ic_mb=; }; _ic_putback() { return 1; }; idle_wc_disk sdx")
    assert out.returncode == 0, out.stderr
    assert _copies(tmp_path) and _writes(tmp_path) == [], "the fixture wrote, or left no copy"
    _recover(tmp_path)
    assert img.read_bytes() == original and not list((tmp_path / "srv").iterdir())


# --- #3271 H3: a 4MiB write cut in the middle, and a twin serial ---

def test_h3_a_place_cut_in_the_middle_of_its_write_comes_back_whole_and_the_copy_goes(tmp_path):
    img, original = _cut(tmp_path)
    _put(img, PLACE // 2, original[PLACE // 2:PLACE])   # the first place's 4MiB write stopped half way: half pattern, half original
    log = _recover(tmp_path)
    assert img.read_bytes() == original, "a region that was half the check's pattern was not put back"
    assert "STALE" not in log
    assert not list((tmp_path / "srv").iterdir()), "every block is the original again: the copy goes"


def test_h3_a_twin_with_the_same_serial_and_size_does_not_take_the_copy_of_the_other(tmp_path):
    img, original = _cut(tmp_path)               # disk A: cut in the middle, its copy open
    a_cut = img.read_bytes()
    twin = os.urandom(64 * MIB)
    img.write_bytes(twin)                          # disk B in the drawer: "To be filled by O.E.M.", the same size
    log = _recover(tmp_path)
    assert img.read_bytes() == twin, "the twin was written with the other disk's copy"
    assert _copies(tmp_path) and not (tmp_path / "srv" / "SER1.stale").exists(), "the twin dropped (or marked) A's copy"
    assert "another disk" in log
    img.write_bytes(a_cut)                         # A comes back
    _recover(tmp_path)
    assert img.read_bytes() == original, "A lost its copy to its twin"
    assert not _copies(tmp_path)


# --- #3271 H1: the disk under the name is asked again before every write ---

def test_h1_a_drawer_swapped_while_the_copy_is_sent_is_never_written(tmp_path):
    _make(tmp_path)
    twin = os.urandom(64 * MIB)
    (tmp_path / "srv" / "b.img").write_bytes(twin)
    (tmp_path / "srv" / "swap_on_post").write_text("")
    out = _run(tmp_path, "idle_wc_disk sdx")
    img = tmp_path / "dev" / "sdx"
    assert img.read_bytes() == twin, "the disk swapped in got the check's pattern and the other disk's regions"
    assert _writes(tmp_path) == [], out.stderr
    assert _copies(tmp_path), "the copy of the disk that left must stay"
    assert _res(tmp_path)["state"] != "ok" and "הוחלף" in _res(tmp_path)["reason"], _res(tmp_path)


def test_h1_a_drawer_swapped_during_the_check_does_not_get_the_put_back(tmp_path):
    _make(tmp_path)
    twin = os.urandom(64 * MIB)
    (tmp_path / "srv" / "b.img").write_bytes(twin)
    swap = (f'_ic_check() {{ cp "{posix(tmp_path)}/srv/b.img" "$DEVROOT/sdx"; printf SER2 > "$SYSROOT/sys/block/sdx/device/serial"; '
            '_ic_st=ok; _ic_why=x; _ic_mb=; }; idle_wc_disk sdx')
    out = _run(tmp_path, swap)
    assert (tmp_path / "dev" / "sdx").read_bytes() == twin, "the put-back of A landed on B"
    assert _writes(tmp_path) == [], out.stderr
    assert _copies(tmp_path)
    assert _res(tmp_path)["state"] == "failed" and "הוחלף" in _res(tmp_path)["reason"], _res(tmp_path)


# --- #3271 H2: a task never touches a drawer the check is on, nor one that still carries its pattern ---

def test_h2_a_task_hold_stops_the_scanner_and_a_check_that_sees_it_writes_nothing(tmp_path):
    img = _make(tmp_path)
    before = _sha(img)
    (tmp_path / "run" / "idlecheck").mkdir(parents=True)
    (tmp_path / "run" / "idlecheck" / "hold").write_text("")
    out = _run(tmp_path, "idle_wc_disk sdx; idle_wc_scan; wait")
    assert _writes(tmp_path) == [] and _sha(img) == before, out.stderr
    assert not (tmp_path / "posts").exists(), "the disk was read for a copy while a task holds the drawers"
    assert not (tmp_path / "run" / "idlecheck" / "busy.sdx").exists(), "the lock was left behind"
    assert _run(tmp_path, "idle_wc_field sdx").stdout.startswith("unchecked|"), "#3271 M3: 'checking' while nothing runs"


def test_h2_a_capture_does_not_read_a_disk_the_check_is_still_on(tmp_path):
    _make(tmp_path)
    (tmp_path / "run" / "idlecheck" / "busy.sdx").mkdir(parents=True)
    out = _run(tmp_path, "capture_source_reason sdx")
    assert "עוד רצה" in out.stdout and "לא נקלט" in out.stdout, (out.stdout, out.stderr)
    assert (tmp_path / "run" / "idlecheck" / "hold").exists(), "the scanner was not told to stop"


def test_h2_a_capture_puts_an_open_copy_back_before_it_reads_the_source(tmp_path):
    img, original = _cut(tmp_path)
    _run(tmp_path, "capture_source_reason sdx")   # a fresh boot: the task comes before the scanner reached the disk
    assert img.read_bytes() == original, "the capture would read the check's pattern as the source"
    assert not _copies(tmp_path)


def test_h2_a_local_copy_target_is_refused_while_the_check_is_on_it(tmp_path):
    _make(tmp_path)
    (tmp_path / "run" / "idlecheck" / "busy.sdx").mkdir(parents=True)
    out = _run(tmp_path, "idle_wc_ready sdx dst; echo RC=$?")
    assert "RC=1" in out.stdout and "עוד רצה" in out.stdout, (out.stdout, out.stderr)
    (tmp_path / "run" / "idlecheck" / "busy.sdx").rmdir()
    assert "RC=0" in _run(tmp_path, "idle_wc_ready sdx dst; echo RC=$?").stdout


# --- #3271 H4: a dd that outlives the check's ceiling ---

def _late_alive(t: Path) -> bool:
    late = str(t / "late" / "dd").encode()
    for p in Path("/proc").iterdir():
        if p.name.isdigit():
            try:
                if late in (p / "cmdline").read_bytes():
                    return True
            except OSError:
                continue
    return False


@requires_native("setsid", posix=True)
def test_h4_a_dd_left_behind_by_the_timeout_is_killed_before_the_put_back(tmp_path):
    img = _make(tmp_path)
    before = _sha(img)
    (tmp_path / "late").mkdir()
    late = tmp_path / "late" / "dd"
    late.write_text(LATE_DD, newline="\n")
    late.chmod(late.stat().st_mode | stat.S_IEXEC)
    (tmp_path / "hang").write_text("")
    out = _run(tmp_path, "WAIT_WRITECHECK_S=2; idle_wc_disk sdx")
    deadline = time.monotonic() + 20
    while _late_alive(tmp_path) and time.monotonic() < deadline:
        time.sleep(0.2)
    assert not _late_alive(tmp_path), "the dd left behind is still alive"
    assert _sha(img) == before or _copies(tmp_path), "a dd of the check wrote after the put-back, and the copy is gone"
    assert _sha(img) == before, (out.stderr, (tmp_path / "run" / "agent.log").read_text()[-2000:])
    assert _res(tmp_path)["state"] == "failed", _res(tmp_path)


def test_h4_no_put_back_while_a_dd_may_still_write_and_the_copy_stays(tmp_path):
    _make(tmp_path)
    (tmp_path / "hang").write_text("")
    out = _run(tmp_path, "WAIT_WRITECHECK_S=1; _ic_stop_writers() { return 1; }; idle_wc_disk sdx; "
                         'for p in $(_ic_writers "$DEVROOT/sdx"); do kill -9 "$p"; done')   # the test's own leftover, after the verdict
    assert len(_writes(tmp_path)) == 1, ("it wrote the put-back while a dd may still write the disk", _writes(tmp_path), out.stderr)
    assert _copies(tmp_path), "the copy was dropped although the disk was not put back"
    r = _res(tmp_path)
    assert r["state"] == "failed" and "dd" in r["reason"], r


# --- #3271 M1-M3 ---

def test_m1_a_usb_disk_is_not_a_drawer_and_is_not_checked(tmp_path):
    _make(tmp_path)
    usb = tmp_path / "devices" / "pci0000:00" / "usb1" / "1-1" / "host0" / "target0:0:0" / "0:0:0:0"
    usb.mkdir(parents=True)
    (tmp_path / "sys/block/sdu").mkdir(parents=True)
    (tmp_path / "sys/block/sdu/removable").write_text("0")
    (tmp_path / "sys/block/sdu/device").symlink_to(usb)
    _run(tmp_path, f"idle_wc_disk() {{ echo \"$1\" >> {posix(tmp_path)}/called; }}; idle_wc_scan; wait")
    assert (tmp_path / "called").read_text().split() == ["sdx"], "a USB disk was written by the waiting-screen check"


@pytest.mark.parametrize("why", ["smart", "disk_failures"])
def test_m2_a_red_disk_or_one_with_an_open_failure_is_not_written(tmp_path, why):
    img = _make(tmp_path)
    before = _sha(img)
    if why == "smart":
        (tmp_path / "smart").write_text("fail")
    else:
        _answer(tmp_path, disk_failures=[{"serial": "SER1", "port": "", "cause": "disk"}])
    out = _run(tmp_path, "idle_wc_disk sdx")
    assert _writes(tmp_path) == [] and _sha(img) == before, out.stderr
    assert not (tmp_path / "posts").exists(), "a red disk was read and sent for a copy"
    assert _res(tmp_path)["state"] == "unchecked", _res(tmp_path)


def test_m3_a_finished_round_left_on_the_screen_does_not_stop_the_check_for_good(tmp_path):
    _make(tmp_path)
    (tmp_path / "run" / "targets" / "sdx").mkdir(parents=True)
    (tmp_path / "run" / "targets" / "sdx" / "state").write_text("done")   # the card of the last round (#2229)
    _run(tmp_path, f"idle_wc_disk() {{ echo \"$1\" >> {posix(tmp_path)}/called; }}; idle_wc_scan; wait")
    assert (tmp_path / "called").read_text().split() == ["sdx"], "a new disk is never checked after the first round"
