"""#1798 -- a target whose write was cut says so ON the disk, and is never a source.

On metal (19/10, rc7nl2, cloner 2) a local clone cut by ``reboot -f`` after
18.4GB left the target with a full GPT and half its data: "SMART תקין" on the
standby screen, "Windows · 4 מחיצות" on the local clone screen, and it could be
picked as the SOURCE. /run (tmpfs) with every record of the failure was gone.

``agent/lib/restore/writemark.sh``: every partition of the table is NAMED
``imagesrv-incomplete`` in the same sgdisk call that writes the table
(``apply_gpt``, and ``expand_last`` when it rebuilds partitions); the original
names come back only after the whole image and the grow (``finish_grow``, the
local clone too), each read back, and only then is the target done. Reading it is three answers: incomplete / clean / unchecked.

Here: a sgdisk stub that keeps a tiny GPT in files (a name per partition), so
the disk "survives" the shell that wrote it -- a fresh process with a fresh
RUN_DIR is the reboot. Real sgdisk on a sparse file: test_writemark_real_1798.py.
"""

from __future__ import annotations

import json
from pathlib import Path

from native import requires_native
from test_agent import AGENT, BASH, posix, sh
from test_capture_refusals import capture_run, refusal_reason
from test_restore_evidence import build_box, log_of, rc_of

pytestmark = requires_native(("bash", BASH))

MARK = "imagesrv-incomplete"
MARK_HE = "כתיבה לא הושלמה — לא שמיש"

#: sgdisk that keeps the table's names in $B/name.<i> -- -n makes an unnamed
#: partition, -c names it, -d and --zap-all remove; -p lists them as the real
#: one does (the name last), -i says the name as the real one does.
#: $B/sgdisk.fail holds globs that exit 2; with $B/sgdisk.lie, -c exits 0 and
#: writes another name.
GPT_STUB = r'''#!/bin/sh
B="@BOX@"
printf '%s\n' "$*" >> "$B/sgdisk.calls"
case " $* " in *" --pretend "*) exit 0 ;; esac
if [ -f "$B/sgdisk.fail" ]; then
    while read -r pat; do
        # shellcheck disable=SC2254
        case "$*" in $pat) exit 2 ;; esac
    done < "$B/sgdisk.fail"
fi
prev=""
for a in "$@"; do
    case "$prev" in
        -n) : > "$B/name.${a%%:*}" ;;
        -d) rm -f "$B/name.$a" "$B/uguid.$a" ;;
        -c) if [ -f "$B/sgdisk.lie" ]; then echo garbled; else printf '%s\n' "${a#*:}"; fi > "$B/name.${a%%:*}" ;;
        -u) printf '%s\n' "${a#*:}" > "$B/uguid.${a%%:*}" ;;
        -U) printf '%s\n' "$a" > "$B/disk_guid.requested" ;;
    esac
    [ "$a" = "--zap-all" ] && rm -f "$B"/name.[0-9]* "$B"/uguid.[0-9]*
    prev="$a"
done
case "$1" in
    -i) [ -f "$B/uguid.$2" ] && echo "Partition unique GUID: $(cat "$B/uguid.$2")"
        [ -f "$B/name.$2" ] && echo "Partition name: '$(cat "$B/name.$2")'" ;;
    -p) [ -f "$B/disk_guid.requested" ] && echo "Disk identifier (GUID): $(cat "$B/disk_guid.requested")"
        echo "Number  Start (sector)    End (sector)  Size       Code  Name"
        for f in "$B"/name.[0-9]*; do
            [ -f "$f" ] || continue
            echo "   ${f##*.}            2048          206847   100.0 MiB   0700  $(cat "$f")"
        done ;;
esac
exit 0
'''


def _stub(box: Path) -> str:
    return (f"\ncat > {posix(box)}/stubs/sgdisk <<'STUB'\n{GPT_STUB.replace('@BOX@', posix(box))}STUB\n"
            f"chmod 0755 {posix(box)}/stubs/sgdisk\n")


def _box(tmp_path, **kw):
    box, run, prelude = build_box(tmp_path, **kw)
    return box, run, prelude + _stub(box)


def _names(box: Path) -> dict[int, str]:
    return {int(p.name.split(".")[1]): p.read_text(encoding="utf-8").rstrip("\n")
            for p in box.glob("name.[0-9]*")}


def _writes(box: Path) -> list[str]:
    path = box / "sgdisk.calls"
    calls = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return [c for c in calls if not c.startswith(("-p ", "-i ")) and " --pretend " not in f" {c} "]


def _fail(box: Path, *globs: str) -> None:
    (box / "sgdisk.fail").write_text("\n".join(globs) + "\n", newline="\n")


def _state(run: Path) -> tuple[str, str]:
    t = run / "targets" / "sda"
    err = t / "error"
    return ((t / "state").read_text(encoding="utf-8").strip(),
            err.read_text(encoding="utf-8").strip() if err.exists() else "")


# --- the mark is written with the table ------------------------------------------


def test_the_table_and_its_mark_are_one_write(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"; writemark_state sda')
    lines = out.strip().splitlines()
    assert lines[-2] == "rc=0", (out, log_of(run))
    assert lines[-1] == "incomplete", out
    writes = _writes(box)
    assert len(writes) == 2 and writes[0].startswith("--zap-all "), writes   # #1714: still one table call
    one = writes[1]
    assert f" -c 1:{MARK} -c 2:{MARK} -c 3:{MARK} " in one, one
    assert one.index(" -n 3:") < one.index(f" -c 1:{MARK}") < one.index(" -e "), one
    assert _names(box) == {1: MARK, 2: MARK, 3: MARK}


def test_the_expansion_marks_what_it_rebuilt(tmp_path):
    """-d/-n make the candidate and the tail unnamed: without the mark again a
    single-partition image (the grown root) would carry no mark at all."""
    box, run, prelude = _box(tmp_path)
    (box / "sys" / "block" / "sda").mkdir(parents=True)
    (box / "sys" / "block" / "sda" / "size").write_text("976773168\n")
    out = sh(prelude + 'apply_gpt sda m.json || { echo "rc=apply"; exit; }; '
             + ': > "$RUN_DIR/../sgdisk.calls"; expand_last sda m.json; echo "rc=$?"')
    assert rc_of(out) == "rc=0", (out, log_of(run))
    (one,) = _writes(box)
    assert one.startswith("-a 1 -d 3 -n 3:") and f" -c 2:{MARK} -c 3:{MARK}" in one, one
    assert _names(box) == {1: MARK, 2: MARK, 3: MARK}


# --- cut: the mark outlives the run --------------------------------------------------


def test_a_cut_write_is_incomplete_after_the_reboot(tmp_path):
    """The write stops after the table (reboot -f): a new process, a new RUN_DIR
    (tmpfs is gone), reads the disk -- and the disk says so."""
    box, run, prelude = _box(tmp_path)
    assert rc_of(sh(prelude + 'apply_gpt sda m.json; echo "rc=$?"')) == "rc=0"
    fresh = tmp_path / "after-reboot"
    fresh.mkdir()
    out = sh(f'export PATH="$(cd {posix(box)!r}/stubs && pwd):$PATH" RUN_DIR={posix(fresh)!r} DEVROOT={posix(box)}/dev '
             f'LOG_FILE={posix(fresh)}/log; log() {{ :; }}; . {posix(AGENT)}/lib/restore/writemark.sh; '
             'writemark_state sda; writemark_json sda gpt')
    assert out.strip().splitlines() == ["incomplete", ',"write_mark":"incomplete"'], out


# --- success: names back, read back, then done ------------------------------------------


def test_success_puts_the_names_back_and_only_then_done(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; : > "$RUN_DIR/../sgdisk.calls"; '
             'finish_grow sda; echo "rc=$?"; writemark_state sda')
    lines = out.strip().splitlines()
    assert lines[-2:] == ["rc=0", "clean"], (out, log_of(run))
    assert _state(run) == ("done", "")
    # an image's manifest has no names: the disk gets exactly that -- empty
    assert _names(box) == {1: "", 2: "", 3: ""}
    assert _writes(box) == [f"-c 1: -c 2: -c 3: {posix(box)}/dev/sda"], _writes(box)   # one call
    assert "the incomplete-write mark is cleared (partitions 1 2 3)" in log_of(run)


def test_a_marked_disk_is_still_a_target(tmp_path):
    """Allowed as a target: a disk that carries the mark from a cut write is
    zapped and written like any other, and ends clean."""
    box, run, prelude = _box(tmp_path)
    for i in (1, 2, 5):
        (box / f"name.{i}").write_text(MARK + "\n", newline="\n")
    out = sh(prelude + 'writemark_state sda; apply_gpt sda m.json || exit 9; finish_grow sda; '
             'echo "rc=$?"; writemark_state sda')
    lines = out.strip().splitlines()
    assert lines[0] == "incomplete" and lines[-2:] == ["rc=0", "clean"], (out, log_of(run))
    assert _state(run)[0] == "done"


def test_a_mark_that_does_not_come_off_fails_the_target(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; '
             f'echo "-c *" > {posix(box)}/sgdisk.fail; '
             'finish_grow sda; echo "rc=$?"; writemark_state sda')
    lines = out.strip().splitlines()
    assert lines[-2:] == ["rc=1", "incomplete"], (out, log_of(run))
    state, error = _state(run)
    assert state == "failed", (state, error)
    assert "הסימון 'כתיבה לא הושלמה' לא הוסר מהדיסק" in error, error


def test_a_name_that_does_not_read_back_fails_the_target(tmp_path):
    """sgdisk -c exits 0 and the disk carries another name: the exit code is not
    evidence (principle 5) -- each name is read back with sgdisk -i."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; '
             f': > {posix(box)}/sgdisk.lie; finish_grow sda; echo "rc=$?"')
    assert rc_of(out) == "rc=1", (out, log_of(run))
    assert _names(box) == {1: "garbled", 2: "garbled", 3: "garbled"}
    state, error = _state(run)
    assert state == "failed" and "לא הוסר מהדיסק" in error, (state, error)
    assert "partition 1 is named 'garbled' after sgdisk -c, not ''" in log_of(run)


def test_a_table_that_cannot_be_read_is_unchecked_never_clean(tmp_path):
    """Principle 5: sgdisk -p failing is not "no mark"."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; '
             f'echo "-p *" > {posix(box)}/sgdisk.fail; '
             'writemark_state sda; writemark_json sda gpt; echo; finish_grow sda; echo "rc=$?"')
    # log() echoes to stdout too: only the answers
    lines = [l for l in out.strip().splitlines() if not l.startswith(("imagesrv:", "["))]
    assert lines[-3:] == ["unchecked", ',"write_mark":"unchecked"', "rc=1"], (out, log_of(run))
    assert _state(run)[0] == "failed"
    assert not _writes(box)[2:], "nothing is written over a table that was not read"


# --- the local clone: the source's names, after the grow -------------------------------


def test_the_local_clone_gets_the_sources_names_back_only_after_the_grow(tmp_path):
    """The grow (ntfsresize) writes too: a cut there leaves the mark. The table
    check runs before it and sees the mark; the source's names come after."""
    box, run, prelude = _box(tmp_path)
    lc = tmp_path / "lc"
    (lc / "run").mkdir(parents=True)
    (lc / "run" / "entries").write_text(
        "1|EFI system partition|8000000000000000\n2||0\n3|Basic data partition|0\n",
        encoding="utf-8", newline="\n")
    seen = box / "names_seen"
    names = f'for i in 1 2 3; do printf "%s|" "$(cat {posix(box)}/name.$i)"; done >> {posix(seen)}; echo >> {posix(seen)}'
    out = sh(prelude + f'. {posix(AGENT)}/lib/localclone/lccopy.sh; . {posix(AGENT)}/lib/localclone/lcraw.sh; export LC={posix(lc)!r}; '
             'apply_gpt sda m.json || exit 9; '
             # the names the target carries at the table check and at the grow
             f'lc_table_check() {{ {names}; LC_ERROR=""; }}; grow_expanded() {{ {names}; }}; boot_hidden_disk() {{ return 0; }}; '
             'lc_unhibernate_target() { :; }; '   # #2009 (hibernation.sh): nothing hibernated here
             'lc_finish m.json sda; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-1] == "clean", (out, log_of(run))
    assert seen.read_text(encoding="utf-8").splitlines() == [f"{MARK}|{MARK}|{MARK}|"] * 2
    assert _names(box) == {1: "EFI system partition", 2: "", 3: "Basic data partition"}
    assert _state(run)[0] == "done"


def test_the_local_clone_prepare_leaves_the_mark_on(tmp_path):
    """lc_prepare_target used to write the source's names right after the
    table -- the copy then ran on a disk that looked finished."""
    box, run, prelude = _box(tmp_path)
    lc = tmp_path / "lc"
    (lc / "run").mkdir(parents=True)
    (lc / "run" / "entries").write_text("1|EFI system partition|0\n2|x|0\n3|y|0\n",
                                        encoding="utf-8", newline="\n")
    out = sh(prelude + f'. {posix(AGENT)}/lib/localclone/lcplan.sh; . {posix(AGENT)}/lib/localclone/lccopy.sh; export LC={posix(lc)!r}; '
             'disk_fits() { return 0; }; expand_last() { return 0; }; '
             'lc_prepare_target sda m.json; echo "rc=$?"; writemark_state sda')
    assert out.strip().splitlines()[-2:] == ["rc=0", "incomplete"], (out, log_of(run))
    assert _names(box) == {1: MARK, 2: MARK, 3: MARK}


# --- reading it: the local clone's inventory, the capture, the hello, the standby screen ----


LCINV = r'''
disk_port() { echo 1; }; smart_probe() { echo ok; }; smart_field() { :; }; disk_failure_cause() { return 1; }
logical_block_size() { echo 512; }; disk_scheme() { echo gpt; }
lc_parts() { printf '1|C12A7328-F81F-11D2-BA4B-00A0C93EC93B|11111111-1111-1111-1111-111111111111|2048|204800\n' > "$2"; : > "$2.fail"; }
partition_node() { echo "$DEVROOT/$1$2"; }; _fs_of() { echo vfat; }; _partition_role() { echo esp; }
partclone_for_fs() { echo partclone.fat; }; _used_bytes() { echo 1000; }; unique_guid_gate() { :; }
lc_min_free() { echo ok; }
'''


def _inventory(tmp_path, names: dict[str, str | None], fail_read=False) -> dict[str, list[str]]:
    """lc_inv_disk on each disk; names[dev] = the name of its partition 1
    (None = no table read: sgdisk -p fails on it)."""
    out_lines = {}
    for dev, name in names.items():
        box = tmp_path / dev
        (box / "stubs").mkdir(parents=True)
        (box / "lc" / "inv").mkdir(parents=True)
        (box / f"name.1").write_text((name or "") + "\n", encoding="utf-8", newline="\n")
        if name is None:
            _fail(box, "-p *")
        out = sh(_stub(box) + f'export PATH="$(cd {posix(box)!r}/stubs && pwd):$PATH" RUN_DIR={posix(box)!r} '
                 f'DEVROOT={posix(box)}/dev SYSROOT={posix(box)} LOG_FILE={posix(box)}/log LC={posix(tmp_path)}/lc; '
                 f'mkdir -p "$LC/inv"; log() {{ :; }}; . {posix(AGENT)}/lib/core/common.sh; '
                 f'. {posix(AGENT)}/lib/localclone/lcinv.sh; . {posix(AGENT)}/lib/restore/writemark.sh; ' + LCINV
                 + f'lc_inv_disk {dev} > "$LC/inv/{dev}.line"; cat "$LC/inv/{dev}.line"; cat "$LC/inv/{dev}.mark"')
        line, mark = out.strip().splitlines()[-2:]
        out_lines[dev] = line.split("|") + [mark]
    return out_lines


def test_the_local_clone_never_offers_a_cut_disk_as_its_source(tmp_path):
    inv = _inventory(tmp_path, {"sda": "EFI system partition", "sdb": MARK, "sdc": None})
    # fields: 12 usable, 13 why; then the mark file (lc_disk field 16)
    assert (inv["sda"][11], inv["sda"][12], inv["sda"][13]) == ("1", "", "clean"), inv["sda"]
    assert (inv["sdb"][11], inv["sdb"][12], inv["sdb"][13]) == ("0", MARK_HE, "incomplete"), inv["sdb"]
    assert (inv["sdc"][11], inv["sdc"][13]) == ("0", "unchecked"), inv["sdc"]
    assert inv["sdc"][12] == "לא הצלחנו לבדוק אם הכתיבה האחרונה אליו הושלמה", inv["sdc"]


def test_the_kiosk_carries_the_mark_as_lc_disk_field_16(tmp_path):
    lc = tmp_path / "lc"
    (lc / "inv").mkdir(parents=True)
    (lc / "inv" / "sdb.line").write_text(f"2|sdb|M|S|1000|ok||Windows|4|10|900|0|{MARK_HE}\n",
                                         encoding="utf-8", newline="\n")
    (lc / "inv" / "sdb.mark").write_text("incomplete\n", newline="\n")
    (lc / "screen").write_text("local\n", newline="\n")
    gui = tmp_path / "gui"
    gui.mkdir()
    out = sh(f'export RUN_DIR={posix(tmp_path)!r} GUI_DIR={posix(gui)!r} LC={posix(lc)!r}; log() {{ :; }}; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/localclone/lcgui.sh; '
             'gui_header_state() { :; }; gui_hello_state() { :; }; drawer_slots_refresh() { :; }; '
             'disk_drawer() { echo "2|SATA 1"; }; lc_gui_state; echo "rc=$?"')
    assert rc_of(out) == "rc=0", out
    (line,) = [l for l in (gui / "state").read_text(encoding="utf-8").splitlines() if l.startswith("lc_disk=")]
    fields = line[len("lc_disk="):].split("|")
    # field 17 is #1921's hardware note, 18 #2001's stick connection name (both empty
    # here); the mark stays field 16
    assert len(fields) == 18 and fields[13:16] == ["2", "SATA 1", "incomplete"], fields


#: sgdisk -p of a disk whose write was cut: the marked partitions.
MARKED_TABLE = (
    '#!/bin/sh\n'
    'echo "Disk identifier (GUID): 4C7B1E00-0000-4000-8000-000000000001"\n'
    'echo "Number  Start (sector)    End (sector)  Size       Code  Name"\n'
    f'echo "   1            2048          206847   100.0 MiB   EF00  {MARK}"\n'
    f'echo "   2          206848       976773134   465.7 GiB   0700  {MARK}"\n'
)


def test_a_capture_refuses_a_cut_disk_by_name(tmp_path):
    """Also the direct send: its pass 1 is capture_disk (directsend.sh)."""
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": MARKED_TABLE})
    reason = refusal_reason(box, run, out)
    assert MARK_HE in reason and "לא נקלט" in reason, reason


def test_a_capture_refuses_a_disk_whose_mark_it_could_not_read(tmp_path):
    box, run, out = capture_run(tmp_path, stubs={"sgdisk": "#!/bin/sh\nexit 1\n"})
    reason = refusal_reason(box, run, out)
    assert "לא הצלחנו לבדוק אם הכתיבה האחרונה לדיסק הושלמה" in reason, reason


def _hello_disk(tmp_path, image: bytes, sgdisk: str | None) -> dict:
    box = tmp_path / "hello"
    (box / "dev").mkdir(parents=True)
    (box / "dev" / "sda").write_bytes(image)
    q = box / "sys" / "block" / "sda" / "queue"
    q.mkdir(parents=True)
    (q / "logical_block_size").write_text("512\n", newline="\n")
    (box / "sys" / "block" / "sda" / "size").write_text("1000\n", newline="\n")
    stubs = box / "stubs"
    stubs.mkdir()
    calls = box / "calls"
    body = (sgdisk or "#!/bin/sh\nexit 0\n").replace("#!/bin/sh\n", f"#!/bin/sh\necho x >> {posix(calls)}\n", 1)
    (stubs / "sgdisk").write_text(body, encoding="utf-8", newline="\n")
    out = sh(f'chmod 0755 {posix(stubs)}/sgdisk; export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" DEVROOT={posix(box)}/dev '
             f'SYSROOT={posix(box)}; '
             f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; . {posix(AGENT)}/lib/restore/writemark.sh; '
             f'LOG_FILE={posix(box)}/log; build_disk_entry sda')   # after common.sh: it sets its own default
    entry = json.loads(out.strip().splitlines()[-1])
    entry["_sgdisk_calls"] = len(calls.read_text().splitlines()) if calls.exists() else 0
    return entry


GPT = bytes(512) + b"EFI PART" + bytes(504)
MBR = bytes(510) + b"\x55\xaa" + bytes(512)


def test_the_hello_reports_the_mark_in_three_words(tmp_path):
    assert _hello_disk(tmp_path / "a", GPT, MARKED_TABLE)["write_mark"] == "incomplete"
    assert _hello_disk(tmp_path / "b", GPT, "#!/bin/sh\nexit 1\n")["write_mark"] == "unchecked"
    clean = _hello_disk(tmp_path / "c", GPT, "#!/bin/sh\necho 'Number  Start'\n")
    assert clean["write_mark"] == "clean"
    mbr = _hello_disk(tmp_path / "d", MBR, None)
    assert (mbr["write_mark"], mbr["_sgdisk_calls"]) == ("clean", 0), "an MBR disk cannot carry it"


def test_the_standby_screen_record_carries_the_mark_as_field_12(tmp_path):
    from test_cloner_gui import PRELUDE, env
    run = tmp_path / "run"
    run.mkdir()
    gui = tmp_path / "gui"
    gui.mkdir()
    (run / "response.json").write_text("{}", newline="\n")
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    (stubs / "sgdisk").write_text(MARKED_TABLE, encoding="utf-8", newline="\n")
    out = sh(f'chmod 0755 {posix(stubs)}/sgdisk; export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" DEVROOT={posix(tmp_path)}/dev; '
             + env(run, gui) + PRELUDE
             + 'disk_port() { echo 1; }; smart_hello_field() { echo ok; }; disk_failure_cause() { return 1; }; '
             'disk_failures_refresh() { :; }; disk_link() { echo "6.0 6.0"; }; disk_link_json() { :; }; '
             'disk_drawer() { echo "1|SATA 0"; }; cloner_cdisks sda; echo "rc=$?"')
    (line,) = [l for l in out.splitlines() if l.startswith("cdisk=")]
    assert line.split("|")[9:12] == ["1", "SATA 0", "incomplete"], line   # field 13 after it: #1921's hw note


# --- the server passes it to the room's drawer list; the console says it in orange ----


def test_the_room_drawer_list_carries_the_mark_and_an_old_agent_is_not_clean():
    import pytest
    pytest.importorskip("fastapi")
    from server.rounds import room

    class _Conn:
        def __init__(self, disks):
            self._disks = disks

        def execute(self, *_a, **_k):
            disks = self._disks

            class _R:
                def fetchone(self):
                    return {"disks_json": json.dumps(disks)}
            return _R()

    def mark_of(**extra):
        disk = {"dev": "sda", "port": 1, "serial": "S1", "model": "M", "size_bytes": 1, **extra}
        return room.drawer_list(_Conn([disk]), "m", set())[0]["write_mark"]

    assert mark_of(write_mark="incomplete") == "incomplete"
    assert mark_of(write_mark="unchecked") == "unchecked"
    assert mark_of() is None, "an agent before #1798 did not look -- not 'clean'"
    assert mark_of(write_mark="bogus") is None


def test_the_console_says_it_in_orange_and_still_offers_the_disk_as_a_target():
    js = (AGENT.parent / "server" / "static" / "console.js").read_text(encoding="utf-8")
    assert f'const WRITE_MARK_HE = "{MARK_HE}";' in js
    assert 'if (d.write_mark === "incomplete") why.push(WRITE_MARK_HE);' in js   # a warning in the pick grid
    assert 'if (d.write_mark === "incomplete") return chip("w", WRITE_MARK_HE);' in js   # "w" = orange
