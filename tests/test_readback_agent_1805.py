"""#1805/#1864 -- the agent's read-back after the write (agent/lib/readback.sh,
readbacklc.sh).

What the shell does with what ``fanout --readback -x`` says (the C reader itself
is tests/test_readback_1805.py): flush first and check every exit code
(CLR-03), read every chunk (full) or a seeded sample (the default, Nadav 20/10),
judge against the server's list -- itself checked against the manifest -- and
turn the result into the drawer's state, every drawer in parallel.

The stand-ins: ``blockdev`` (a flush that can be made to fail per node),
``partclone.<fs> -c`` (records the call), ``fanout`` (``--readback`` prints the
lines a test put in ``$BOX/rbx.<node>`` -- or ``rbs.<node>`` for a sample --
``--xxh64`` is the first 16 digits of sha256: the same stand-in on both sides of
the comparison) and ``http_get`` (serves ``$BOX/served.<index>``).

**Negative control** (on origin/work/rc7-cand, before #1805): there is no
readback.sh -- every behaviour test fails on a drawer that is neither failed nor
verified/sampled; the wiring tests fail on the missing call before finish_grow.
Mutations of #1864: in test_readback_1805.py and in the PR.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import re
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, posix
from test_timeouts import make_stubs, run_sh

pytestmark = requires_native(("bash", BASH), "jq", why="the manifest is read with jq, as on the machine")

FAIL = "האימות אחרי הכתיבה נכשל — הדיסק לא שמר את מה שנכתב"
NOT_RUN = "האימות אחרי הכתיבה לא רץ — אין ראיה שהדיסק שמר את מה שנכתב"
CH = 4194304

#: per partition: chunks and bytes of the data stream (p2 is a dd partition: read whole)
SHAPE = {1: (1, 40960), 2: (4, 16 * 1024 * 1024), 3: (5, 4 * CH + 4096)}


def chunk_hex(i: int, k: int, salt: str = "") -> str:
    return hashlib.sha256(f"{salt}{i}:{k}".encode()).hexdigest()[:16]


def list_text(i: int, salt: str = "") -> str:
    return "".join(chunk_hex(i, k, salt) + "\n" for k in range(SHAPE[i][0]))


def xxh(text: str) -> str:
    """The stand-in fanout --xxh64: the first 16 digits of sha256 -- on both sides."""
    return hashlib.sha256(text.encode("ascii")).hexdigest()[:16]


BLOCKDEV = (
    '#!/bin/sh\n'
    'echo "blockdev $1 $(basename "$2")" >> "$BOX/calls"\n'
    'case "$1" in\n'
    '  --flushbufs) [ ! -f "$BOX/flush_fail.$(basename "$2")" ] ;;\n'
    '  --getsize64) wc -c < "$2" | tr -d " " ;;\n'
    '  *) exit 1 ;;\n'
    'esac\n'
)

PARTCLONE = (
    '#!/bin/sh\n'
    'src=; mode=; flags=\n'
    'while [ $# -gt 0 ]; do case "$1" in -s) src=$2; shift ;; -c) mode=c ;; -I) flags=I ;; esac; shift; done\n'
    'echo "partclone $(basename "$0") -$mode${flags:+ -$flags} $(basename "$src")" >> "$BOX/calls"\n'
    'printf "header-and-map"\n'
)

FANOUT_RB = (
    '#!/bin/sh\n'
    'if [ "$1" = --xxh64 ]; then sha256sum | cut -c1-16; exit 0; fi\n'
    '[ "$1" = --readback ] || exit 2\n'
    'shift; x=; s=\n'
    'if [ "$1" = -x ]; then x=1; shift; fi\n'
    'if [ "$1" = -s ]; then s="$2 $3"; shift 3; fi\n'
    'n=$(basename "$1")\n'
    'echo "readback $n${x:+ -x}${s:+ -s $s}${2:+ $2}" >> "$BOX/calls"\n'
    'cat > /dev/null\n'
    'if [ -f "$BOX/rb.$n.rc" ]; then echo "fanout: readback: the stand-in failed" >&2; exit "$(cat "$BOX/rb.$n.rc")"; fi\n'
    'if [ -z "$x" ]; then cat "$BOX/rb.$n"; elif [ -n "$s" ]; then cat "$BOX/rbs.$n"; else cat "$BOX/rbx.$n"; fi\n'
)


def manifest(mode: str | None = "sample", *, kinds: dict | None = None) -> dict:
    parts = [
        {"index": 1, "role": "esp", "fs": "vfat", "file": "p1.esp.pcl.zst", "size_bytes": 100 << 20},
        {"index": 2, "role": "msr", "fs": "", "file": "p2.msr.pcl.zst", "size_bytes": 16 << 20},
        {"index": 3, "role": "windows", "fs": "ntfs", "file": "p3.windows.pcl.zst", "size_bytes": 50 << 30},
        {"index": 4, "role": "swap", "fs": "swap", "file": None, "size_bytes": 1 << 30},
    ]
    kinds = {1: "x", 2: "x", 3: "x"} if kinds is None else kinds
    for p in parts:
        i, kind = p["index"], kinds.get(p["index"])
        if kind == "x":
            p.update({"readback_xxh64": xxh(list_text(i)), "readback_chunks": SHAPE[i][0],
                      "readback_bytes": SHAPE[i][1], "readback_chunk_bytes": CH})
        elif kind == "sha":
            p.update({"readback_sha256": f"{i}" * 64, "readback_bytes": SHAPE[i][1]})
    m = {"schema": 1, "partitions": parts}
    if mode:
        m["readback_mode"] = mode
    return m


def disk_lines(i: int, *, salt: str = "", picks=None, n_bytes=None) -> str:
    chunks, nbytes = SHAPE[i]
    ks = range(chunks) if picks is None else picks
    return "".join(f"c {k} {chunk_hex(i, k, salt)}\n" for k in ks) + f"n {chunks} {n_bytes or nbytes}\n"


def box_for(tmp_path: Path, disks=("sdb", "sdc"), extra=()) -> Path:
    box = tmp_path / "box"
    (box / "dev").mkdir(parents=True)
    for d in (*disks, *extra):
        (box / "run" / "targets" / d).mkdir(parents=True)
        (box / "run" / "targets" / d / "state").write_text("writing\n", newline="\n")
        (box / "dev" / d).write_bytes(b"")
        for i in (1, 2, 3, 4):
            (box / "dev" / f"{d}{i}").write_bytes(b"")
            if i in SHAPE:
                (box / f"rbx.{d}{i}").write_text(disk_lines(i), newline="\n")
                (box / f"rbs.{d}{i}").write_text(disk_lines(i, picks=sorted({0, SHAPE[i][0] // 2, SHAPE[i][0] - 1})), newline="\n")
                (box / f"rb.{d}{i}").write_text(f"{str(i) * 64} {SHAPE[i][1]}\n", newline="\n")
    for i in SHAPE:
        (box / f"served.{i}").write_text(list_text(i), newline="\n")
    (box / "run" / "response.json").write_text('{"readback_mode": "sample"}', newline="\n")
    (box / "calls").write_text("", newline="\n")
    return box


def prelude(box: Path) -> str:
    stubs = {"blockdev": BLOCKDEV, "partclone.ntfs": PARTCLONE, "partclone.fat": PARTCLONE, "fanout": FANOUT_RB}
    return (
        f'export BOX={posix(box)!r}\n'
        + make_stubs(box / "stubs", stubs)
        + f"export TMPDIR={posix(box)!r} RUN_DIR={posix(box / 'run')!r} DEVROOT={posix(box / 'dev')!r} "
        f"WAIT_POLL_S=1 WAIT_READBACK_S=60 RB_LIST_PAUSE_S=0\n"   # #1907: the list's tries, no pause in tests
        + "".join(f". {posix(AGENT)}/lib/{n}.sh\n" for n in
                  ("common", "jsonq", "waits", "progress", "restore", "verdict", "failmark", "readback", "readbacklc"))
        + "ata_capture() { :; }\n"
        "lc_src_node() { partition_node \"$@\"; }\n"
        'http_get() { echo "get ${1##*/api/v1/images/}" >> "$BOX/calls"; cat "$BOX/served.${1##*/}"; }\n'
    )


def run(box: Path, body: str) -> dict:
    script = prelude(box) + body + (
        '\nfor d in sda sdb sdc; do t=$RUN_DIR/targets/$d; [ -d "$t" ] || continue\n'
        '  echo "T $d|$(cat "$t/state")|$(cat "$t/error" 2>/dev/null)|$(cat "$t/readback" 2>/dev/null)'
        '|$(cat "$t/readback_reason" 2>/dev/null)"; done\n'
    )
    out = run_sh(script, timeout=150)   # every stand-in is a process: slow on a loaded Windows box
    res: dict = {"raw": out, "t": {}, "calls": (box / "calls").read_text(encoding="utf-8").splitlines()}
    for line in out.splitlines():
        if line.startswith("T "):
            d, state, error, rb, why = line[2:].split("|", 4)
            res["t"][d] = {"state": state, "error": error, "readback": rb, "why": why}
        elif line.startswith("RC "):
            res["rc"] = int(line[3:])
    return res


def drawers(box: Path, m: dict | str) -> dict:
    (box / "m.json").write_text(m if isinstance(m, str) else json.dumps(m), encoding="utf-8", newline="\n")
    return run(box, f'RB_SERVER=http://s RB_IMAGE=img_x readback_drawers {posix(box / "m.json")!r} sdb sdc; echo "RC $?"')


def log_of(box: Path) -> str:
    return (box / "run" / "agent.log").read_text(encoding="utf-8")


# --- full: every chunk ----------------------------------------------------------


def test_full_reads_every_chunk_and_matches_the_manifest(tmp_path):
    box = box_for(tmp_path)
    res = drawers(box, manifest("full"))
    assert res["rc"] == 0, res["raw"]
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["error"], t["readback"]) == ("verifying", "", "verified"), res["raw"]
        for n in (d, f"{d}1", f"{d}2", f"{d}3"):
            assert f"blockdev --flushbufs {n}" in res["calls"]          # CLR-03: every node, every exit code
        assert f"blockdev --flushbufs {d}4" not in res["calls"]           # swap: mkswap, nothing to read
        assert f"partclone partclone.fat -c {d}1" in res["calls"] and f"partclone partclone.ntfs -c -I {d}3" in res["calls"]
        assert f"readback {d}1 -x" in res["calls"] and f"readback {d}2 -x {SHAPE[2][1]}" in res["calls"]
    assert not any(c.startswith("get ") for c in res["calls"])            # full needs no list: the manifest's XXH64
    assert "mode full" in log_of(box)


def test_full_one_chunk_the_disk_did_not_keep_fails_the_drawer_alone(tmp_path):
    box = box_for(tmp_path)
    lines = disk_lines(3).splitlines()
    lines[2] = "c 2 deadbeefdeadbeef"
    (box / "rbx.sdc3").write_text("\n".join(lines) + "\n", newline="\n")
    res = drawers(box, manifest("full"))
    assert res["t"]["sdc"]["state"] == "failed" and res["t"]["sdc"]["error"] == f"מחיצה 3: {FAIL}", res["raw"]
    assert res["t"]["sdc"]["readback"] == ""                                # never "verified"
    assert res["t"]["sdb"]["readback"] == "verified" and res["rc"] == 0


def test_a_different_map_says_so(tmp_path):
    box = box_for(tmp_path)
    (box / "rbx.sdb1").write_text(disk_lines(1, n_bytes=8192), newline="\n")
    res = drawers(box, manifest("full"))
    assert res["t"]["sdb"]["error"] == f"מחיצה 1: {FAIL} (מפת הבלוקים בדיסק: 8192 בייט, באימג': {SHAPE[1][1]})"


# --- sample: the default -----------------------------------------------------------


def test_sample_is_the_default_and_says_sampled_never_verified(tmp_path):
    """Nadav 20/10: ~5% of the chunks, the server's list fetched and checked against
    the manifest, the drawn chunks compared -- "נבדק במדגם", not "אומת"."""
    box = box_for(tmp_path)
    res = drawers(box, manifest(None))                                     # no mode in the manifest: the default
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["error"], t["readback"]) == ("verifying", "", "sampled"), res["raw"]
        assert t["why"].startswith("נבדק במדגם") and "3 מתוך 5" in t["why"], t["why"]
        assert re.search(rf"^readback {d}3 -x -s \d+ 50$", "\n".join(res["calls"]), re.M)
    assert {f"get img_x/readback/{i}" for i in (1, 2, 3)} <= set(res["calls"])
    seed = re.search(r"sample seed (\d+)", log_of(box)).group(1)
    assert f"readback sdb3 -x -s {seed} 50" in res["calls"]                 # the logged seed is the one used
    out = run_sh(prelude(box) + 'build_progress ses_1 aa:bb')
    entry = next(t for t in json.loads(out.strip().splitlines()[-1])["targets"] if t["dev"] == "sdb")
    assert entry["readback"] == "sampled" and entry["readback_reason"].startswith("נבדק במדגם"), entry


def test_a_drawn_chunk_that_differs_fails_with_its_number(tmp_path):
    box = box_for(tmp_path)
    (box / "rbs.sdc3").write_text(disk_lines(3, picks=[0, 2, 4], salt="x")
                                  .replace(f"c 0 {chunk_hex(3, 0, 'x')}", f"c 0 {chunk_hex(3, 0)}")
                                  .replace(f"c 2 {chunk_hex(3, 2, 'x')}", f"c 2 {chunk_hex(3, 2)}"), newline="\n")
    res = drawers(box, manifest("sample"))
    assert res["t"]["sdc"]["state"] == "failed", res["raw"]
    assert res["t"]["sdc"]["error"] == f"מחיצה 3: {FAIL} (חתיכה 4 של 4MiB מתוך 5)"
    assert res["t"]["sdb"]["readback"] == "sampled"


@pytest.mark.parametrize("mode", ["sample", "full"])
def test_a_hash_that_looks_like_a_number_is_compared_as_text(tmp_path, mode):
    """#1890 (cloner 2, 20/10): chunk 8060's hash was 954480869382e837 on the disk and on the
    server alike. awk read both as a number (~inf) and busybox called them different: two healthy
    drawers failed. The same chunk, the same digits, on both sides -- must be a match."""
    box = box_for(tmp_path)
    num = "954480869382e837"
    served = list_text(3).splitlines()
    old = served[2]
    served[2] = num
    (box / "served.3").write_text("".join(h + "\n" for h in served), newline="\n")
    for d in ("sdb", "sdc"):
        for f in (f"rbx.{d}3", f"rbs.{d}3"):
            (box / f).write_text((box / f).read_text().replace(f"c 2 {old}", f"c 2 {num}"), newline="\n")
    m = manifest(mode)
    m["partitions"][2]["readback_xxh64"] = xxh("".join(h + "\n" for h in served))
    # The bug lives in busybox awk (the agent's awk); gawk/mawk compare two infs as equal and
    # would hide it. Without busybox the test cannot see the bug at all -- skipped, by name.
    bb = shutil.which("busybox")
    if not bb:
        pytest.skip("busybox not installed: only busybox awk compares 954480869382e837 as a number")
    shim = tmp_path / "bbawk"
    shim.mkdir()
    (shim / "awk").write_text(f'#!/bin/sh\nexec {posix(Path(bb))} awk "$@"\n', newline="\n")
    (shim / "awk").chmod(0o755)
    (box / "m.json").write_text(json.dumps(m), encoding="utf-8", newline="\n")
    res = run(box, f'export PATH={posix(shim)!r}:"$PATH"; '
                   f'RB_SERVER=http://s RB_IMAGE=img_x readback_drawers {posix(box / "m.json")!r} sdb sdc; echo "RC $?"')
    for d in ("sdb", "sdc"):
        assert res["t"][d]["state"] != "failed" and res["t"][d]["error"] == "", res["raw"]
        assert res["t"][d]["readback"] in ("sampled", "verified"), res["raw"]


@pytest.mark.parametrize("picks", [[], [4], [0, 4]])
def test_a_sample_that_read_fewer_than_the_fixed_three_is_failed_never_sampled(tmp_path, picks):
    """Security review 20/10: a fanout whose sample hashed nothing (mutation C1:
    every chunk "not drawn") -- or fewer than the start, the middle and the end --
    vouches for nothing. Failed by name, never "נבדק במדגם" (principle 5)."""
    box = box_for(tmp_path)
    (box / "rbs.sdc3").write_text(disk_lines(3, picks=picks), newline="\n")
    res = drawers(box, manifest("sample"))
    t = res["t"]["sdc"]
    assert t["state"] == "failed" and t["readback"] == "", res["raw"]
    assert t["error"] == f"מחיצה 3: המדגם לא נקרא מהדיסק ({len(picks)} חתיכות, לפחות 3 נדרשות)"
    assert res["t"]["sdb"]["readback"] == "sampled"


def test_a_local_clone_sample_that_read_nothing_is_failed(tmp_path):
    box = box_for(tmp_path, extra=("sda",))
    for d in ("sda", "sdb"):                       # the source and a target alike: nothing drawn, "identical"
        (box / f"rbs.{d}3").write_text(disk_lines(3, picks=[]), newline="\n")
    res = local(box, "sample")
    assert res["t"]["sdb"]["state"] == "failed", res["raw"]
    assert res["t"]["sdb"]["error"] == "מחיצה 3: המדגם לא נקרא מהדיסק"


@pytest.mark.parametrize("serve, why", [
    (None, "רשימת הבדיקה לא הורדה מהשרת או אינה של המניפסט"),            # nothing served (404, a direct send)
    ("0000000000000000\n" * 5, "רשימת הבדיקה לא הורדה מהשרת או אינה של המניפסט"),   # not the manifest's list
])
def test_a_sample_without_the_servers_own_list_is_not_verified(tmp_path, serve, why):
    box = box_for(tmp_path)
    if serve is None:
        (box / "served.3").unlink()
    else:
        (box / "served.3").write_text(serve, newline="\n")
    res = drawers(box, manifest("sample"))
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["readback"]) == ("verifying", "unverified"), res["raw"]
        assert t["why"].startswith(why) and "מחיצות: 3)" in t["why"]


def test_an_image_without_lists_is_written_and_says_not_verified(tmp_path):
    box = box_for(tmp_path)
    res = drawers(box, manifest("sample", kinds={}))
    for d in ("sdb", "sdc"):
        t = res["t"][d]
        assert (t["state"], t["error"], t["readback"]) == ("verifying", "", "unverified"), res["raw"]
        assert t["why"] == "לאימג' אין עדיין רשימת בדיקה (מחיצות: 1 2 3) — מה שנכתב לא נבדק מול הדיסק"
        assert f"blockdev --flushbufs {d}3" in res["calls"]                 # flushed all the same
    assert not any(c.startswith(("readback", "partclone")) for c in res["calls"])


@pytest.mark.parametrize("mode, want", [("full", "verified"), ("sample", "unverified")])
def test_a_manifest_from_before_1864_is_read_whole_in_full_mode_only(tmp_path, mode, want):
    """Compatibility: `readback_sha256` alone -- sha256 of everything in full mode;
    in sample mode there is nothing to draw from until the server has made its list."""
    box = box_for(tmp_path)
    res = drawers(box, manifest(mode, kinds={1: "sha", 2: "sha", 3: "sha"}))
    assert res["t"]["sdb"]["readback"] == want, res["raw"]
    assert ("readback sdb3" in res["calls"]) == (mode == "full")


@pytest.mark.parametrize("rc, why", [
    (4, f"מחיצה 3: {FAIL} (הקריאה מהדיסק נכשלה: the stand-in failed)"),
    (3, f"{NOT_RUN} — מחיצה 3: מפת הבלוקים לא נקראה מהדיסק (the stand-in failed)"),
    (2, f"{NOT_RUN} — מחיצה 3: fanout --readback rc=2: the stand-in failed"),
])
def test_a_read_that_did_not_answer_fails_the_drawer(tmp_path, rc, why):
    """Principle 5: "could not check" is a failed drawer too -- never folded into done."""
    box = box_for(tmp_path)
    (box / "rb.sdb3.rc").write_text(str(rc), newline="\n")
    res = drawers(box, manifest("full"))
    assert res["t"]["sdb"]["state"] == "failed" and res["t"]["sdb"]["error"] == why, res["raw"]
    assert res["t"]["sdc"]["readback"] == "verified"


def test_a_flush_that_failed_is_not_a_read_back(tmp_path):
    box = box_for(tmp_path)
    (box / "flush_fail.sdc3").write_text("", newline="\n")
    res = drawers(box, manifest("sample"))
    assert res["t"]["sdc"]["state"] == "failed", res["raw"]
    assert res["t"]["sdc"]["error"].startswith(f"{NOT_RUN} — ריקון המטמון של מחיצה 3 נכשל (blockdev --flushbufs rc=1)")
    assert not any(c.startswith("readback sdc") for c in res["calls"])
    assert res["t"]["sdb"]["readback"] == "sampled"


def test_a_manifest_jq_cannot_read_fails_every_drawer(tmp_path):
    box = box_for(tmp_path)
    res = drawers(box, "{not json")
    assert res["rc"] == 1
    for d in ("sdb", "sdc"):
        assert res["t"][d]["state"] == "failed" and res["t"][d]["error"] == f"{NOT_RUN} — המניפסט לא נקרא"


def test_a_failed_or_cancelled_drawer_is_not_read(tmp_path):
    box = box_for(tmp_path)
    (box / "run" / "targets" / "sdc" / "state").write_text("cancelled\n", newline="\n")
    res = drawers(box, manifest("full"))
    assert res["t"]["sdc"]["state"] == "cancelled" and not any("sdc" in c for c in res["calls"]), res["raw"]
    assert res["t"]["sdb"]["readback"] == "verified"


# --- the local clone: against the source --------------------------------------------


def local(box: Path, mode: str) -> dict:
    (box / "run" / "response.json").write_text(json.dumps({"readback_mode": mode}), newline="\n")
    (box / "m.json").write_text(json.dumps(manifest(None, kinds={})), encoding="utf-8", newline="\n")
    return run(box, f'readback_local {posix(box / "m.json")!r} sda sdb sdc; echo "RC $?"')


@pytest.mark.parametrize("mode, want", [("full", "verified"), ("sample", "sampled")])
def test_a_local_clone_judges_every_target_against_the_source(tmp_path, mode, want):
    box = box_for(tmp_path, extra=("sda",))
    flavour = "rbx" if mode == "full" else "rbs"
    (box / f"{flavour}.sdc2").write_text(disk_lines(2, salt="y", picks=None if mode == "full" else [0, 2, 3]),
                                          newline="\n")
    res = local(box, mode)
    assert res["t"]["sdb"]["readback"] == want, res["raw"]
    assert res["t"]["sdc"]["state"] == "failed" and res["t"]["sdc"]["error"] == f"מחיצה 2: {FAIL}"
    assert f"readback sda2 -x{'' if mode == 'full' else ' -s'}" in " ".join(res["calls"])
    assert f"{SHAPE[2][1]}" in next(c for c in res["calls"] if c.startswith("readback sda2"))   # dd: size_bytes
    # the source was never written: not flushed, never opened for writing (#1466, a read-only source)
    assert not any(c.startswith("blockdev") and " sda" in c for c in res["calls"]), res["calls"]


def test_a_source_that_could_not_be_read_back_fails_every_target(tmp_path):
    box = box_for(tmp_path, extra=("sda",))
    (box / "rb.sda3.rc").write_text("4", newline="\n")
    res = local(box, "full")
    for d in ("sdb", "sdc"):
        assert res["t"][d]["state"] == "failed", res["raw"]
        assert res["t"][d]["error"].startswith(f"{NOT_RUN} — המקור לא נקרא לאימות: מחיצה 3: {FAIL}")


# --- a direct send: pass 1 carries the list's XXH64 --------------------------------


@pytest.mark.parametrize("sink, fail, want", [
    ("", False, ""),
    ("discard", False, f',"readback_xxh64":"{xxh(list_text(3))}","readback_chunks":5,'
                       f'"readback_bytes":{SHAPE[3][1]},"readback_chunk_bytes":{CH}'),
    ("discard", True, ""),
])
def test_a_direct_send_puts_the_lists_digest_in_its_manifest(tmp_path, sink, fail, want):
    box = box_for(tmp_path)
    if fail:
        (box / "rb.sdb3.rc").write_text("3", newline="\n")
    node = posix(box / "dev" / "sdb3")
    out = run_sh(prelude(box) + f'mkdir -p "$RUN_DIR"; printf "<%s>\\n" "$(CAPTURE_SINK={sink} rb_capture_json {node!r} ntfs 123)"')
    assert out.strip().splitlines()[-1] == f"<{want}>", out
    if fail:
        assert "its targets will say NOT VERIFIED" in log_of(box)


# --- the wiring: before the filesystem grows, on every path -------------------------


def _text(rel: str) -> str:
    return (AGENT / rel).read_text(encoding="utf-8")


def test_every_write_path_reads_back_before_it_grows():
    drawers_sh = _text("lib/drawers.sh")
    rd = drawers_sh.index('RB_SERVER=$_server RB_IMAGE=$_image readback_drawers "$_manifest" $_disks')
    assert drawers_sh.index('_written=$((_written + 1))') < rd < drawers_sh.index('finish_grow "$_d"')
    assert '[ "$?" -ne 127 ] || _fail_alive "$_disks"' in drawers_sh
    assert '{ RB_SERVER=$3 RB_IMAGE=$4 readback_drawers "$5" "$2" && finish_grow "$2" "" "$5"; } || { echo "failed"' \
        in _text("lib/restore.sh")
    lc = _text("lib/lccopy.sh")
    body = lc[lc.index("lc_finish() {"):]
    assert body.index("readback_local") < body.index('finish_grow "$_lf_t"')
    assert '[ "$?" -ne 127 ] || _fail_alive "$*"' in body
    agent = _text("imagectl-agent")
    assert re.search(r'\. "\$LIB_DIR/readback\.sh"; \. "\$LIB_DIR/readbacklc\.sh"', agent)
    assert '$(rb_capture_json "$_node" "$_fs" "$((_sizesec * _sector_size))")' in _text("lib/capture.sh")


def test_the_console_says_sampled_and_has_the_switch():
    js = (AGENT.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert "נבדק במדגם" in js and "לא אומת אחרי הכתיבה" in js and "readbackLine(d)" in js
    assert 'key: "readback_full"' in js and "אימות מלא מוסיף כ-5 דק' לסבב של 4 מגירות" in js
    assert re.search(r"[A-Za-z]{2,}", FAIL) is None and re.search(r"[A-Za-z]{2,}", NOT_RUN) is None
