"""#3262 (Nadav 14/11, option A) -- a target smaller than the image by up to 64 MiB takes it
only when the LAST partition is a small NTFS that is not C: (the recovery partition).

The way is "way 2" of the lab experiment: the partition is restored into RAM (brd) at its full
size, the filesystem is shrunk THERE, the shrunk part is written to the short partition,
ntfsfix -d, and the result is CHECKED (bytes read back, filesystem size, ntfsfix -n,
ntfsresize -c, and for a recovery partition the sha256 of its .wim files against the source).
"Way 1" (cut the tail, fix at the end) corrupts silently and is not here: the tests pin that
the table is cut and the stream goes to the RAM node, never to the short partition.

The switch (`tail_shrink_enabled`) is OFF by default until a clone boots (#2526).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from test_room_precheck_432_65 import disk, drawers_hello
from test_server_room import CLONER1, room_server  # noqa: F401 -- room_server is a fixture
from test_agent import BASH, posix, sh
from agentlib import flat_lib

needs_shell = pytest.mark.skipif(
    BASH is None or shutil.which("jq") is None, reason="bash and jq are required")

GIB = 1 << 30
MIB = 1 << 20
LIMIT = 64 * MIB
WIN = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
REC = "DE94BBA4-06D1-4D40-A16A-BFD50179D6AC"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"
C_START = 239616
C_SIZE = 100 * GIB
REC_START = C_START + C_SIZE // 512
REC_SIZE = 40 * MIB
GAP = 1_744_896     # the 1.7 MB of the bench (20/09)


def image(rec_used: int = 4 * MIB, rec_fs: str = "ntfs", c_last: bool = False, rec_size: int = REC_SIZE) -> dict:
    parts = [(1, ESP, "esp", "vfat", 2048, 100 * MIB, 30 * MIB),
             (3, WIN, "windows", "ntfs", C_START, C_SIZE, 60 * GIB)]
    if not c_last:
        parts.append((4, REC, "recovery", rec_fs, REC_START, rec_size, rec_used))
    return {"schema": 1, "id": "img_3262", "name": "t", "family": 256, "scheme": "gpt",
            "sector_size": 512, "disk_guid": None, "source_disk_bytes": None, "min_target_bytes": None,
            "partitions": [{"index": i, "type_guid": g, "role": r, "fs": f, "start_sector": st, "size_bytes": sz,
                            "used_bytes": u, "file": f"p{i}.x.pcl.zst", "sha256": "aa" * 32, "expandable": False}
                           for i, g, r, f, st, sz, u in parts]}


def need(m: dict) -> int:
    return max(p["start_sector"] * 512 + p["size_bytes"] for p in m["partitions"]) + 262144


STUBS = {
    # ntfsresize: -n budget, -s shrink (moves the size read back unless NR_NOSHRINK), -c check, --info read-back.
    "ntfsresize": '''#!/bin/sh
echo "ntfsresize $*" >> "$RUN_DIR/calls"
n=0; c=0; sz=""; info=0; node=""
while [ $# -gt 0 ]; do case "$1" in -n) n=1 ;; -c) c=1 ;; -s) sz=$2; shift ;; --info) info=1 ;; --no-progress-bar) ;; *) node=$1 ;; esac; shift; done
b=$(basename "$node")
if [ "$info" = 1 ]; then
    [ "${NR_INFO_RC:-0}" = 0 ] || exit 1
    f="$RUN_DIR/vol.$b"; [ -f "$f" ] || f="$RUN_DIR/vol.ram0"
    echo "Current volume size: $(cat "$f") bytes (x MB)"; exit 0
fi
[ "$c" = 0 ] || exit "${NR_C_RC:-0}"
[ "$n" = 0 ] || exit "${NR_N_RC:-0}"
[ "${NR_S_RC:-0}" = 0 ] || exit "$NR_S_RC"
[ "${NR_NOSHRINK:-0}" = 1 ] || echo $((sz - 512)) > "$RUN_DIR/vol.$b"
exit 0
''',
    "ntfsfix": '#!/bin/sh\necho "ntfsfix $*" >> "$RUN_DIR/calls"\ncase "$1" in -n) exit "${NFN_RC:-0}" ;; *) exit "${NFD_RC:-0}" ;; esac\n',
    "blockdev": '#!/bin/sh\ncase "$1" in --getsize64) cat "$RUN_DIR/bsize.$(basename "$2")" 2>/dev/null || echo "$DISK_BYTES" ;; *) exit "${BD_RC:-0}" ;; esac\n',
    "modprobe": '#!/bin/sh\necho "modprobe $*" >> "$RUN_DIR/calls"\nexit "${MP_RC:-0}"\n',
    "rmmod": '#!/bin/sh\necho "rmmod $*" >> "$RUN_DIR/calls"\n',
    "ntfs-3g": '#!/bin/sh\n[ "$1" = -o ] && shift 2; d="$RUN_DIR/fix/$(basename "$1")"; [ -d "$d" ] || exit 1; cp -r "$d/." "$2/"\n',
    "umount": '#!/bin/sh\nrm -rf "$1"/*\n',
}
MEM_OK = "MemAvailable:    4000000 kB\n"


def box(tmp_path: Path, m: dict, disk_bytes: int, on: bool = True, mem: str = MEM_OK):
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True)
    (run / "targets" / "sda" / "state").write_text("writing\n")
    (run / "dev").mkdir()
    (run / "sysroot" / "proc").mkdir(parents=True)
    (run / "sysroot" / "proc" / "meminfo").write_text(mem)
    (run / "vol.ram0").write_text(f"{REC_SIZE - 512}\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    for name, body in STUBS.items():
        (stubs / name).write_text(body, encoding="utf-8")
    if on:
        (run / "tail_shrink_on").write_text("1\n")
    (run / "min_free_gb").write_text("0\n")
    path = tmp_path / "m.json"
    path.write_text(json.dumps(m), encoding="utf-8")
    libs = ("common", "jsonq", "sysinfo", "diskinfo", "progress", "failmark", "restore", "expand",
            "diskfit", "tailshrink", "shrinkoffer")
    prelude = (
        f"chmod 0755 {posix(stubs)}/*; "
        f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH"; '
        f"export RUN_DIR={posix(run)!r} DEVROOT={posix(run)!r}/dev SYSROOT={posix(run)!r}/sysroot "
        f"LOG_FILE={posix(run)!r}/log DISK_BYTES={disk_bytes}; "
        + "".join(f". {posix(flat_lib())}/{lib}.sh; " for lib in libs)
        + 'raid_member_reason() { :; }; node_is_block() { [ -e "$1" ]; }; '
    )
    return run, path, prelude


def fits(tmp_path: Path, m: dict, disk_bytes: int, on: bool = True, mem: str = MEM_OK):
    run, path, prelude = box(tmp_path, m, disk_bytes, on, mem)
    out = sh(prelude + f'disk_fits sda {posix(path)!r}; echo "rc=$?"')
    t = run / "targets" / "sda"
    read = lambda n: (t / n).read_text(encoding="utf-8").strip() if (t / n).exists() else ""   # noqa: E731
    return out.strip().splitlines()[-1], read("error"), read("tail_shrink"), read("notice")


# --- disk_fits: who is admitted ---------------------------------------------------------------


@needs_shell
def test_a_small_gap_on_a_small_non_c_last_partition_is_admitted_and_the_notice_says_so(tmp_path):
    m = image()
    rc, err, plan, notice = fits(tmp_path, m, need(m) - GAP)
    assert (rc, err) == ("rc=0", "")
    assert plan == f"4|{2 * MIB}|{REC_SIZE}|recovery"
    assert "מחיצה 4" in notice and "תוקטן" in notice and "זיכרון" in notice, notice


@needs_shell
def test_exactly_64_mib_is_admitted_and_one_byte_more_is_refused_by_name(tmp_path):
    big = image(rec_size=200 * MIB)
    assert fits(tmp_path / "a", big, need(big) - LIMIT)[0] == "rc=0"
    rc, err, plan, _ = fits(tmp_path / "b", big, need(big) - LIMIT - 1)
    assert rc == "rc=1" and plan == "" and err.startswith("הדיסק קטן מדי ב-65 MiB"), err


@needs_shell
def test_no_free_room_in_the_last_partition_is_refused_by_name(tmp_path):
    ok = image(rec_used=REC_SIZE - 2 * MIB - 32 * MIB)                    # exactly cut + 32 MiB free
    assert fits(tmp_path / "a", ok, need(ok) - GAP)[0] == "rc=0"
    full = image(rec_used=REC_SIZE - 2 * MIB - 32 * MIB + 1)
    rc, err, plan, _ = fits(tmp_path / "b", full, need(full) - GAP)
    assert rc == "rc=1" and plan == "" and err.startswith("הדיסק קטן מדי ב-"), err


@needs_shell
def test_c_as_the_last_partition_is_refused_by_name(tmp_path):
    """The experiment: 20 GB in use at the tail of C: -- no shrink in RAM for 98 GB."""
    m = image(c_last=True)
    rc, err, plan, _ = fits(tmp_path, m, need(m) - GAP)
    assert rc == "rc=1" and plan == "" and err.startswith("הדיסק קטן מדי"), err
    assert "היא C:" in (tmp_path / "run" / "agent.log").read_text(encoding="utf-8")


@needs_shell
def test_not_ntfs_is_refused(tmp_path):
    m = image(rec_fs="ext4")
    assert fits(tmp_path, m, need(m) - GAP)[0] == "rc=1"


@needs_shell
def test_a_partition_that_does_not_fit_in_ram_is_refused_by_name(tmp_path):
    m = image()
    # 40 MiB partition + 512 MiB reserve = 552 MiB = 565248 KiB
    rc, err, plan, _ = fits(tmp_path / "a", m, need(m) - GAP, mem="MemAvailable: 565247 kB\n")
    assert rc == "rc=1" and plan == "" and err.startswith("הדיסק קטן מדי"), err
    assert "does not fit in RAM" in (tmp_path / "a" / "run" / "agent.log").read_text(encoding="utf-8")
    assert fits(tmp_path / "b", m, need(m) - GAP, mem="MemAvailable: 565248 kB\n")[0] == "rc=0"
    assert fits(tmp_path / "c", m, need(m) - GAP, mem="")[0] == "rc=1"      # unreadable = not checked = refused


@needs_shell
def test_the_switch_off_is_the_behaviour_of_today(tmp_path):
    m = image()
    disk_bytes = need(m) - GAP
    rc, err, plan, notice = fits(tmp_path, m, disk_bytes, on=False)
    assert rc == "rc=1" and plan == "" and notice == ""
    assert err == f"הדיסק קטן מדי ב-2 MiB (נדרשים {need(m)} בייט, בדיסק {disk_bytes}; חסרים {need(m) - disk_bytes} בייט)"


@needs_shell
def test_a_library_that_was_not_loaded_refuses_like_today(tmp_path):
    m = image()
    run, path, prelude = box(tmp_path, m, need(m) - GAP)
    out = sh(prelude + f'unset -f tail_shrink_admit; disk_fits sda {posix(path)!r}; echo "rc=$?"')
    assert out.strip().splitlines()[-1] == "rc=1"


@needs_shell
@pytest.mark.parametrize("answer,expected", [
    ('{"tail_shrink_enabled":true}', "1"), ('{"tail_shrink_enabled":false}', ""),
    ('{"tail_shrink_enabled":1}', ""), ('{"min_free_gb":20}', ""),
])
def test_only_a_literal_true_in_the_hello_turns_it_on(tmp_path, answer, expected):
    run, _path, prelude = box(tmp_path, image(), 1, on=True)
    (run / "response.json").write_text(answer, encoding="utf-8")      # json_get reads a FILE: the hello's $RESP
    out = sh(prelude + 'tail_shrink_take "$RUN_DIR/response.json"; cat "$RUN_DIR/tail_shrink_on" 2>/dev/null || true')
    assert out.strip() == expected


# --- the way: RAM node, cut table, shrink in RAM, write, fix, CHECK ---------------------------


def admitted(tmp_path: Path, env: str = "", fixtures: bool = True, wim_dst: bytes = b"WINRE"):
    """Admit, prepare (apply_gpt's hook) and return what the finish needs."""
    m = image()
    run, path, prelude = box(tmp_path, m, need(m) - GAP)
    with open(run / "dev" / "ram0", "wb") as f:
        f.truncate(REC_SIZE)
        f.seek(0)
        f.write(b"IMAGE-BYTES" * 100)
    with open(run / "dev" / "sda4", "wb") as f:
        f.truncate(REC_SIZE - 2 * MIB)
    (run / "bsize.sda4").write_text(f"{REC_SIZE - 2 * MIB}\n")
    if fixtures:
        for node, data in (("ram0", b"WINRE"), ("sda4", wim_dst)):
            d = run / "fix" / node / "Recovery" / "WindowsRE"
            d.mkdir(parents=True)
            (d / "Winre.wim").write_bytes(data)
    pre = (prelude + f"{env} disk_fits sda {posix(path)!r} || exit 9; "
           f'tail_shrink_prepare sda {posix(path)!r}; echo "prep=$?"; ')
    return run, path, pre


def calls_of(run: Path) -> list[str]:
    return (run / "calls").read_text().splitlines() if (run / "calls").exists() else []


@needs_shell
def test_prepare_opens_the_ram_node_cuts_the_table_and_points_the_stream_at_ram(tmp_path):
    run, path, pre = admitted(tmp_path)
    out = sh(pre + 'echo "n4=$(partition_node sda 4)"; echo "n3=$(partition_node sda 3)"')
    assert "prep=0" in out
    cut = json.loads((run / "tail_shrink.sda.json").read_text())
    assert [p for p in cut["partitions"] if p["index"] == 4][0]["size_bytes"] == REC_SIZE - 2 * MIB
    assert [p for p in cut["partitions"] if p["index"] == 3][0]["size_bytes"] == C_SIZE      # only the last is cut
    assert f"n4={posix(run)}/dev/ram0" in out and f"n3={posix(run)}/dev/sda3" in out          # the stream goes to RAM
    assert any(c.startswith("modprobe brd rd_nr=16 rd_size=40960") for c in calls_of(run))


@needs_shell
def test_a_brd_that_is_loaded_and_not_ours_is_refused_and_the_table_is_not_cut(tmp_path):
    run, path, pre = admitted(tmp_path)
    (run / "sysroot" / "sys" / "module" / "brd").mkdir(parents=True)
    out = sh(pre)
    assert "prep=1" in out and not (run / "tail_shrink.sda.json").exists()


@needs_shell
def test_a_brd_that_does_not_load_is_refused(tmp_path):
    run, path, pre = admitted(tmp_path, env="export MP_RC=1;")
    assert "prep=1" in sh(pre)


def finish(tmp_path: Path, env: str = "", **kw):
    run, path, pre = admitted(tmp_path, **kw)
    out = sh(pre + f'{env} tail_shrink_finish sda; echo "fin=$?"; echo "node=$(partition_node sda 4)"')
    t = run / "targets" / "sda"
    err = (t / "error").read_text(encoding="utf-8").strip() if (t / "error").exists() else ""
    return run, out, (t / "state").read_text().strip(), err


@needs_shell
def test_the_filesystem_is_shrunk_in_ram_written_fixed_and_checked(tmp_path):
    run, out, state, err = finish(tmp_path)
    assert "fin=0" in out and state == "writing", (out, err)
    kinds = [" ".join(c.split()[:2]) for c in calls_of(run) if not c.startswith("modprobe")]
    assert kinds == ["ntfsresize -n", "ntfsresize -s", "ntfsresize --info", "ntfsfix -d", "ntfsfix -n",
                     "ntfsresize -c", "ntfsresize --info", "rmmod brd"], kinds        # the last slot frees brd
    assert f"-s {REC_SIZE - 2 * MIB} " in [c for c in calls_of(run) if c.startswith("ntfsresize -s")][0]
    assert (run / "dev" / "sda4").read_bytes()[:1100] == b"IMAGE-BYTES" * 100         # what RAM held went to the partition
    assert f"node={posix(run)}/dev/sda4" in out and not (run / "targets" / "sda" / "tail_ram").exists()


@needs_shell
@pytest.mark.parametrize("env,why", [
    ("export NR_N_RC=1;", "ntfsresize -n"),                 # the budget did not approve
    ("export NR_S_RC=1;", "לא הקטין"),                      # the shrink failed
    ("export NR_NOSHRINK=1;", "עדיין"),                      # rc 0 and the size did NOT move: the exit code is not the evidence
    ("export NR_INFO_RC=1;", "לא הצלחנו לקרוא"),            # could not read it back
    ("export NFD_RC=1;", "ntfsfix -d"),
    ("export NFN_RC=1;", "ntfsfix -n"),
    ("export NR_C_RC=1;", "ntfsresize -c"),
    ("export BD_RC=1;", "flush"),
])
def test_any_failed_step_fails_the_target_by_name_and_says_the_disk_is_not_ok(tmp_path, env, why):
    run, out, state, err = finish(tmp_path, env=env)
    assert "fin=1" in out and state == "failed", (out, err)
    assert why in err and "הקטנת המחיצה נכשלה" in err and "מחיצה 4" in err, err


@needs_shell
def test_a_recovery_whose_wim_differs_from_the_source_fails_the_target(tmp_path):
    """The experiment's trap: ntfsfix -d and ntfsresize -c say 0 on a broken partition. The sha is the evidence."""
    run, out, state, err = finish(tmp_path, wim_dst=b"BROKEN")
    assert "fin=1" in out and state == "failed" and "sha" in err and "שונה מהמקור" in err, err


@needs_shell
def test_a_recovery_whose_wim_cannot_be_read_fails_the_target(tmp_path):
    run, out, state, err = finish(tmp_path, fixtures=False)
    assert "fin=1" in out and state == "failed" and "sha" in err, err


@needs_shell
def test_a_failure_in_the_shrink_never_touches_the_short_partition(tmp_path):
    run, out, state, err = finish(tmp_path, env="export NR_S_RC=1;")
    assert (run / "dev" / "sda4").read_bytes().strip(b"\0") == b""                   # nothing was written to it


@needs_shell
def test_a_disk_that_was_not_shrunk_has_nothing_to_finish(tmp_path):
    m = image()
    run, path, prelude = box(tmp_path, m, need(m))
    out = sh(prelude + 'tail_shrink_prepare sda "$RUN_DIR/x"; echo "ts=[$TS_MANIFEST]"; '
             'tail_shrink_finish sda; echo "fin=$?"; echo "n=$(partition_node sda 4)"')
    assert "ts=[]" in out and "fin=0" in out and f"n={posix(run)}/dev/sda4" in out and calls_of(run) == []


# --- the server: the setting, the hello field, the validation ---------------------------------


def test_the_server_switch_is_off_by_default_and_only_true_turns_it_on():
    import sqlite3

    from server.agent import timing

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)")
    assert timing.tail_shrink_enabled(conn) is False                  # not set = off
    for raw, want in (("true", True), ("false", False), ("yes", False), ("1", False), ("", False)):
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (timing.TAIL_SHRINK, raw))
        assert timing.tail_shrink_enabled(conn) is want, raw
    assert timing.TAIL_SHRINK not in timing.KEYS          # a switch: no range, no "applied", no console row


def test_the_setting_is_writable_validated_and_labelled():
    from server.agent import timing
    from server.console import journal_he
    from server.console.api import BOOLEAN_SETTINGS, WRITE_SETTINGS

    assert timing.TAIL_SHRINK in WRITE_SETTINGS and timing.TAIL_SHRINK in BOOLEAN_SETTINGS
    assert journal_he.SETTINGS_HE[timing.TAIL_SHRINK]


URL = "/api/console/settings"


def test_the_switch_travels_console_to_hello_off_until_the_owner_turns_it_on(room_server):
    admin = room_server["admin"]
    assert admin.get(URL).json()["tail_shrink_enabled"] == "false"          # default: off
    off = drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])
    assert off["tail_shrink_enabled"] is False
    assert admin.post(URL, json={"tail_shrink_enabled": True}).status_code == 200
    assert admin.get(URL).json()["tail_shrink_enabled"] == "true"
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])["tail_shrink_enabled"] is True
    assert admin.post(URL, json={"tail_shrink_enabled": "false"}).status_code == 200
    assert drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])["tail_shrink_enabled"] is False


@pytest.mark.parametrize("bad", ["yes", 1, "TRUE", "", None])
def test_a_value_that_is_not_true_or_false_is_refused_and_nothing_is_written(room_server, bad):
    admin = room_server["admin"]
    r = admin.post(URL, json={"session_wait_seconds": "88", "tail_shrink_enabled": bad})
    assert r.status_code == 400 and "נדרש true או false" in r.json()["detail"]
    got = admin.get(URL).json()
    assert got["tail_shrink_enabled"] == "false" and got["session_wait_seconds"] != "88"
