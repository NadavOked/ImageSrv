"""#2017 -- "never worse than dd" (Nadav 22/10; MBR moved to v1.1 the same day).

A local clone of a GPT disk (the cloner and the USB stick) copies RAW what it
cannot copy smartly, and says so in orange:

* a filesystem partclone does not know (unknown, BitLocker, LUKS, ReFS...) --
  the manifest says ``copy: raw`` before the copy, and that partition never
  grows (when it was the one to grow, nothing grows: the source's layout);
* a filesystem partclone refused (an NTFS it cannot mount) -- streamed again,
  raw, and its filesystem is not grown either;
* the raw read failed (bad sectors) -- the operator is asked (askgui.sh kind
  ``badsect``), and Nadav decided (22/10) that the copy goes on: "continue",
  no answer within a minute, or no GUI at all copy through them with ``busybox
  dd conv=noerror,sync``; the sectors not read are counted, are zeros on the
  targets, and the targets say "not identical to the source" in orange. Only an
  explicit "stop" fails the copy, and the source is never touched;
* a source with an orange SMART is not a question of the copy at all;
* an MBR disk is still refused -- by its new name, "v1.1".

The engine on loop devices (root) is the Testrunner's; the rest runs anywhere bash does.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, PORTS, PRELUDE, env, posix, sh
from test_local_clone_agent import STREAM_STUBS, run

REPO = Path(__file__).resolve().parent.parent
AGENT = REPO / "agent"

pytestmark = requires_native(("bash", BASH))

BITLOCKER_WHY = "מוצפנת ב-BitLocker"


# --- which partition is raw, and why (lcraw.sh lc_raw_why) --------------------------

@pytest.mark.parametrize("fs,role,guid,want", [
    ("ntfs", "windows", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7", ""),
    ("vfat", "esp", "C12A7328-F81F-11D2-BA4B-00A0C93EC93B", ""),
    ("ext4", "linux", "0FC63DAF-8483-4772-8E79-3D69D8477DE4", ""),
    ("btrfs", "linux", "0FC63DAF-8483-4772-8E79-3D69D8477DE4", ""),
    ("swap", "swap", "0657FD6D-A4AB-43C4-84E5-0933C84B4F4F", ""),
    ("unknown", "msr", "E3C9E316-0B5C-4DB8-817D-F92DF00215AE", ""),           # raw by design: no note
    ("unknown", "data", "21686148-6449-6e6f-744e-656564454649", ""),          # BIOS boot: no note
    ("BitLocker", "data", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7", BITLOCKER_WHY),
    ("crypto_LUKS", "linux", "0FC63DAF-8483-4772-8E79-3D69D8477DE4", "מוצפנת ב-LUKS"),
    ("unknown", "linux", "0FC63DAF-8483-4772-8E79-3D69D8477DE4", "מערכת הקבצים לא מזוהה"),
    ("refs", "data", "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7", "מערכת הקבצים refs אינה נתמכת בהעתקה חכמה"),
])
def test_what_partclone_cannot_copy_is_raw_and_named(tmp_path, fs, role, guid, want):
    out = run(tmp_path, f'lc_raw_why {fs} {role} {guid}; echo "|$(lc_raw_json {fs} {role} {guid})"')
    assert out.returncode == 0, out.stderr
    why, js = out.stdout.rstrip("\n").split("|", 1) if "|" in out.stdout else ("", "")
    assert why.strip() == want, out.stdout
    assert (js == "") == (want == ""), js
    if want:
        assert js == f',"copy":"raw","raw_why":"{want}"', js


def _manifest(path: Path, parts: list[dict]) -> None:
    import json
    path.write_text(json.dumps({"schema": 1, "scheme": "gpt", "sector_size": 512, "partitions": parts}), newline="\n")


def test_a_raw_partition_is_never_the_one_that_grows(tmp_path):
    m = tmp_path / "m.json"
    _manifest(m, [{"index": 2, "fs": "ext4", "role": "linux", "expandable": False},
                  {"index": 4, "fs": "unknown", "role": "linux", "expandable": True, "copy": "raw", "raw_why": "x"}])
    out = run(tmp_path, f'lc_raw_fix {posix(m)!r} && jq -c "[.partitions[] | .expandable]" {posix(m)!r}')
    assert out.stdout.strip() == "[false,false]", out.stdout + out.stderr   # nothing grows: the source's layout


def test_a_partition_that_became_raw_is_marked_and_does_not_grow(tmp_path):
    m = tmp_path / "m.json"
    _manifest(m, [{"index": 3, "fs": "ntfs", "role": "recovery", "expandable": True}])
    out = run(tmp_path, f'lc_raw_mark {posix(m)!r} 3 "partclone סירב" && jq -c ".partitions[0] | [.copy, .raw_why, .expandable]" {posix(m)!r}')
    assert out.stdout.strip() == '["raw","partclone סירב",false]', out.stdout + out.stderr


@pytest.mark.parametrize("text,bs,want", [
    ("4095+1 records in\n4096+0 records out\n", 4096, "8"),     # measured: one 4KiB read lost = 8 sectors
    ("dd: /dev/x: Input/output error\n32744+24 records in\n32768+0 records out\n", 512, "24"),
    ("40960+0 records in\n40960+0 records out\n", 4096, "0"),
])
def test_the_sectors_not_read_are_counted_from_dd(tmp_path, text, bs, want):
    f = tmp_path / "err"; f.write_text(text, newline="\n")
    out = run(tmp_path, f'lc_rescue_count {posix(f)!r} {bs}; echo "rc=$?"')
    assert out.stdout.split() == [want, "rc=0"], out.stdout


def test_no_count_is_no_evidence(tmp_path):
    f = tmp_path / "err"; f.write_text("dd: /dev/x: Input/output error\n", newline="\n")
    out = run(tmp_path, f'lc_rescue_count {posix(f)!r} 4096; echo "rc=$?"')
    assert out.stdout.split() == ["rc=1"], out.stdout


# --- what happens when the source is not read (lcraw.sh lc_stream_any) --------------
#
# lc_stream is stubbed: it returns the codes listed in $LC/run/rcs one per call
# (3 = partclone refused, 4 = the raw read failed) and records how it was called.

STUB = r'''
mkdir -p "$LC/run"; _m="$LC/run/manifest.json"
printf '{"partitions":[{"index":4,"fs":"%s","role":"linux","size_bytes":%s,"expandable":false}]}\n' "${FS:-ntfs}" "${SIZE:-167772160}" > "$_m"
printf '%s\n' $RCS > "$LC/run/rcs"; : > "$LC/run/calls"
ata_capture() { :; }; target_init sda 100; target_init sdb 100
disk_port() { echo 3; }
lc_stream() {
    echo "$1|$2|${LC_RESCUE:-0}|${LC_RESCUE_BS:-}" >> "$LC/run/calls"
    _r=$(sed -n "$(wc -l < "$LC/run/calls")p" "$LC/run/rcs"); LC_SRC_RC=1
    if [ "$_r" = 0 ]; then
        for _t in "$@"; do case "$_t" in sd?) echo "$1" >> "$RUN_DIR/targets/$_t/lc_ok" ;; esac; done
        [ "${LC_RESCUE:-0}" != 1 ] || printf '40957+3 records in\n40960+0 records out\n' > "$LC/run/rescue.err"
    fi
    return "$_r"
}
'''


def _any(tmp_path: Path, rcs: str, answer: str | None, fs: str = "ntfs", size: int = 167772160):
    ask = ('gui_ask_choice() { echo "$*" > "$LC/run/asked"; return 1; }; ' if answer is None else
           f'gui_ask_choice() {{ echo "$*" > "$LC/run/asked"; echo {answer}; }}; ')
    out = run(tmp_path, f'RCS="{rcs}"; FS={fs}; SIZE={size}; ' + STUB + ask
              + 'lc_stream_any 4 "$FS" sdc sda sdb; echo "rc=$?"; '
              + 'for d in sda sdb; do echo "$d=$(cat $RUN_DIR/targets/$d/state)|$(cat $RUN_DIR/targets/$d/error 2>/dev/null)|$(cat $RUN_DIR/targets/$d/rescued 2>/dev/null)"; done; '
              + 'echo "calls=$(tr "\\n" " " < "$LC/run/calls")"; echo "asked=$(cat "$LC/run/asked" 2>/dev/null)"; '
              + 'echo "rescued=$(cat "$LC/run/rescued" 2>/dev/null)"; jq -c ".partitions[0] | [.copy, .raw_why]" "$LC/run/manifest.json"')
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_partclone_refused_the_filesystem_so_it_is_copied_raw(tmp_path):
    out = _any(tmp_path, "3 0", "stop")
    assert "rc=0" in out and "calls=4|ntfs|0| 4|raw|0| " in out, out
    assert '["raw","partclone סירב למערכת הקבצים (ntfs, rc=1)"]' in out, out
    assert "asked=\n" in out, out                      # a raw copy that read is not a question
    assert "sda=waiting||" in out, out


def test_stop_fails_the_copy_and_never_reads_the_source_again(tmp_path):
    out = _any(tmp_path, "3 4", "stop")
    assert "rc=1" in out, out
    assert "calls=4|ntfs|0| 4|raw|0| " in out, out      # never a third, rescued, read
    assert "asked=badsect sdc " in out, out             # the source, its partition in the rows
    for d in ("sda", "sdb"):
        assert f"{d}=failed|" in out and "המפעיל בחר לעצור; המקור לא שונה" in out, out
    assert "rescued=\n" in out, out


def test_no_answer_within_the_minute_continues_by_itself_and_counts(tmp_path):
    """Nadav 22/10: nobody answered (or there is no GUI to ask) -- the copy goes on, as dd would."""
    out = _any(tmp_path, "3 4 0", None)
    assert "rc=0" in out, out
    assert "calls=4|ntfs|0| 4|raw|0| 4|raw|1|4096 " in out, out   # the third read is the rescue
    assert "asked=badsect sdc " in out, out
    assert "rescued=4|24\n" in out, out
    assert "sda=waiting||4|24" in out and "sdb=waiting||4|24" in out, out
    assert '["raw","rescue"]' in out, out


def test_continue_reads_through_bad_sectors_and_counts_them(tmp_path):
    out = _any(tmp_path, "4 0", "continue", fs="unknown")
    assert "rc=0" in out, out
    assert "calls=4|unknown|0| 4|raw|1|4096 " in out, out   # a partition of whole 4KiB: bs=4096
    assert "rescued=4|24\n" in out, out                      # 3 reads of 4KiB not read = 24 sectors
    assert "sda=waiting||4|24" in out and "sdb=waiting||4|24" in out, out
    assert '["raw","rescue"]' in out, out


def test_a_partition_not_of_whole_4kib_is_read_by_sector(tmp_path):
    out = _any(tmp_path, "4 0", "continue", fs="unknown", size=167772160 + 512)
    assert "calls=4|unknown|0| 4|raw|1|512 " in out, out
    assert "rescued=4|3\n" in out, out


def test_the_question_carries_the_disk_the_partition_and_a_minute(tmp_path):
    import time
    before = int(time.time())
    out = run(tmp_path, 'mkdir -p "$LC/run"; disk_port() { echo 3; }; '
              'gui_ask_choice() { echo "$1|$2|$4|$(cat "$3")"; }; lc_rescue_ask sdc 4')
    got = out.stdout.strip().split("|")
    assert got[:5] == ["badsect", "sdc", "60", "3", "4"] and len(got) == 6, out.stdout + out.stderr
    assert before + 60 <= int(got[5]) <= int(time.time()) + 60, got   # the epoch the GUI counts down to


# --- the real lc_stream: which failure is the source's (lccopy.sh) ------------------

def _stream(tmp_path: Path, fs: str, extra: dict[str, str], rescue: bool = False, **envs) -> str:
    box = tmp_path / "box"; stubs = box / "stubs"; dev = box / "dev"
    dev.mkdir(parents=True)
    (dev / "sdc1").write_bytes(b"imagesrv" * 4096)
    make = [f"mkdir -p {posix(stubs)!r}"]
    for name, body in {**STREAM_STUBS, **extra}.items():
        make += [f"cat > {posix(stubs)}/{name} <<'STUB'\n{body}STUB", f"chmod 0755 {posix(stubs)}/{name}"]
    t = "\n".join(make) + "\n"
    t += f'export PATH="$(cd {posix(stubs)!r} && pwd):$PATH" STUB_BOX={posix(box)!r} DEVROOT={posix(dev)!r}; '
    t += 'mkdir -p "$LC/run"; ata_capture() { :; }; target_init sda 100; target_init sdb 100; '
    if rescue:
        t += 'LC_RESCUE=1; LC_RESCUE_BS=4096; '
    t += f'lc_stream 1 {fs} sdc sda sdb > {posix(box)}/stream.log 2>&1; echo "rc=$? src=$LC_SRC_RC"; '
    t += 'for d in sda sdb; do echo "$d=$(cat $RUN_DIR/targets/$d/state)|$(cat $RUN_DIR/targets/$d/error 2>/dev/null)"; done; '
    t += 'echo "err=$(cat "$LC/run/rescue.err" 2>/dev/null | tr "\\n" " ")"'
    out = run(tmp_path, "LC_SRC_RC=; " + t, timeout=120, WAIT_HELPER_S="20", WAIT_DRAWER_S="20", **envs)
    assert out.returncode == 0, out.stderr
    return out.stdout


PCL_FAILS = r'''#!/bin/sh
src=; out=
while [ $# -gt 0 ]; do case "$1" in -s) src=$2; shift 2 ;; -O) out=$2; shift 2 ;; *) shift ;; esac; done
if [ "$src" = - ]; then cat > "$out"; exit 0; fi
head -c 1000 "$src"; exit 1
'''


def test_partclone_that_fails_on_the_source_is_code_3_not_a_failed_target(tmp_path):
    out = _stream(tmp_path, "ntfs", {"partclone.ntfs": PCL_FAILS})
    assert "rc=3 src=1" in out, out
    assert "sda=waiting|" in out and "sdb=waiting|" in out, out   # nothing failed yet: lcraw.sh decides


def test_a_raw_read_that_fails_is_code_4(tmp_path):
    out = _stream(tmp_path, "unknown", {"partclone.dd": PCL_FAILS})
    assert "rc=4 src=1" in out, out


def test_a_reader_that_died_because_every_target_did_is_not_the_source(tmp_path):
    """fanout reports no target that took the stream: the targets failed, not the
    source -- the old failure, never a retry and never a bad-sector question."""
    fan = STREAM_STUBS["fanout"].replace('    echo "$f ok"\n', '    echo "$f failed write error"\n')
    out = _stream(tmp_path, "ntfs", {"partclone.ntfs": PCL_FAILS, "fanout": fan})
    assert "rc=1 src=" in out, out
    assert "sda=failed|" in out and "קריאת המקור נכשלה" in out, out


BUSYBOX = r'''#!/bin/sh
[ "$1" = dd ] || exit 99; shift
for a in "$@"; do case "$a" in if=*) src=${a#if=} ;; esac; done
cat "$src"; printf 'dd: %s: Input/output error\n7+1 records in\n8+0 records out\n' "$src" >&2
'''


def test_continue_reads_with_dd_noerror_and_keeps_its_count(tmp_path):
    out = _stream(tmp_path, "raw", {"busybox": BUSYBOX}, rescue=True)
    assert "rc=0" in out, out
    assert "err=dd: " in out and "7+1 records in" in out, out


def test_the_rescue_read_bypasses_the_page_cache(tmp_path):
    """#2158: through the page cache a failed read loses the kernel's whole read unit, not bs --
    2 bad sectors zeroed 4096 on kernel 6.18 (WSL, measured 25/10) and 256 on the public runner
    (run 40159245308), 8 on the lab's 6.12. iflag=direct makes it bs on every kernel. The
    behavioural test (test_bad_sectors_ask_...) only tells the two apart on a newer kernel."""
    stub = BUSYBOX.replace('cat "$src";', 'echo "$*" > "$STUB_BOX/dd.args"; cat "$src";')
    out = _stream(tmp_path, "raw", {"busybox": stub}, rescue=True)
    assert "rc=0" in out, out
    args = (tmp_path / "box" / "dd.args").read_text().split()
    assert "iflag=direct" in args and "bs=4096" in args and "conv=noerror,sync" in args, args


# --- after the copy: the note, and no grow (lcraw.sh) --------------------------------

def test_the_note_names_each_raw_partition_and_the_rescue(tmp_path):
    m = tmp_path / "m.json"
    _manifest(m, [{"index": 2, "fs": "BitLocker", "copy": "raw", "raw_why": BITLOCKER_WHY},
                  {"index": 3, "fs": "ntfs", "copy": "raw", "raw_why": "partclone סירב למערכת הקבצים (ntfs, rc=1)"},
                  {"index": 4, "fs": "unknown", "copy": "raw", "raw_why": "rescue"},
                  {"index": 5, "fs": "ext4"}])
    out = run(tmp_path, 'mkdir -p "$RUN_DIR/targets/sda"; echo "4|24" > "$RUN_DIR/targets/sda/rescued"; '
              f'lc_raw_note sda {posix(m)!r}; echo; lc_raw_flags sda {posix(m)!r}; echo; lc_raw_flags sdb {posix(m)!r}')
    note, flags, other = out.stdout.splitlines()
    assert note.startswith("הועתק במצב גולמי (כמו dd): מחיצה 2 — " + BITLOCKER_WHY), note
    assert "מחיצה 3 — partclone סירב" in note and "מחיצה 5" not in note, note
    assert "מחיצה 4 במצב הצלה: 24 סקטורים לא נקראו ונכתבו כאפסים — לא זהה למקור" in note, note
    assert len(note.encode()) < 400, len(note.encode())   # LcTgt.error[400] (localclone.h)
    assert flags == "3,24" and other == "3,-1", out.stdout


def test_a_raw_partition_that_the_table_grew_keeps_its_filesystem(tmp_path):
    m = tmp_path / "m.json"
    _manifest(m, [{"index": 3, "fs": "ntfs", "copy": "raw", "raw_why": "x"}, {"index": 5, "fs": "ext4"}])
    out = run(tmp_path, 'mkdir -p "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"; '
              'echo "3|ntfs" > "$RUN_DIR/targets/sda/expanded"; echo "5|ext4" > "$RUN_DIR/targets/sdb/expanded"; '
              f'lc_raw_nogrow sda {posix(m)!r}; lc_raw_nogrow sdb {posix(m)!r}; '
              'ls "$RUN_DIR/targets/sda" "$RUN_DIR/targets/sdb"')
    assert "expanded" not in out.stdout.split("sdb:")[0], out.stdout   # sda: the raw one -- no grow
    assert "expanded" in out.stdout.split("sdb:")[1], out.stdout      # sdb: ext4 grows as before


def test_an_mbr_disk_is_refused_by_its_v11_name():
    text = (AGENT / "lib" / "localclone" / "lcinv.sh").read_text(encoding="utf-8")
    assert 'mbr) _li_why="טבלת MBR — שיכפול מקומי של MBR יגיע ב-v1.1"' in text
    assert "מעתיק GPT בלבד" not in text


# --- the question (askgui.sh kind badsect) -------------------------------------------

def _dispatch(tmp_path, request: str, token: str, screen: str) -> bool:
    gui = tmp_path / "gui"; gui.mkdir(parents=True)
    (gui / "ask-request").write_text(request, newline="\n")
    sh(f"export GUI_DIR={posix(gui)!r}; . {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/gui/askgui.sh; "
       f"GUI_SCREEN={screen}; token='{token}'; gui_ask_dispatch")
    return (gui / "ask-decision").exists()


def test_the_cloner_answers_the_bad_sector_question_and_only_with_its_answers(tmp_path):
    q = "9-0|badsect|1|sdc\n3|4|2000000000\n"
    assert _dispatch(tmp_path / "a", q, "ask-continue|9-0", "cloner")
    assert _dispatch(tmp_path / "b", q, "ask-stop|9-0", "cloner")
    assert not _dispatch(tmp_path / "c", q, "ask-continue|9-0", "")         # not the build screen's
    assert not _dispatch(tmp_path / "d", q, "ask-wipe|9-0", "cloner")       # not an answer of badsect
    assert not _dispatch(tmp_path / "e", q, "ask-continue|8-0", "cloner")   # not the question on screen


def _ask(tmp_path: Path, rows: str, reply: str | None) -> tuple[subprocess.CompletedProcess, Path]:
    gui = tmp_path / "gui"; gui.mkdir(parents=True)
    (tmp_path / "rows").write_text(rows, newline="\n")
    answer = ("( for _ in $(seq 1 100); do [ -s \"$GUI_DIR/ask-request\" ] && break; sleep 0.1; done; "
              "cp \"$GUI_DIR/ask-request\" " + posix(tmp_path / "seen") + "; "
              "N=$(IFS='|' read -r n _ < \"$GUI_DIR/ask-request\"; echo \"$n\"); "
              f"printf '%s|{reply}\\n' \"$N\" > \"$GUI_DIR/ask-decision\" ) & ") if reply else ""
    out = sh(f"export GUI_DIR={posix(gui)!r} RUN_DIR={posix(tmp_path)!r} LOG_FILE={posix(tmp_path / 'log')!r}; "
             f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/gui/askgui.sh; attended_power_check() {{ :; }}; "
             f"sleep 20 & _gui_pid=$!; {answer}gui_ask_choice badsect sdc {posix(tmp_path / 'rows')!r} 3; echo \"rc=$?\"; "
             "kill $_gui_pid 2>/dev/null; wait", timeout=60)
    return out, tmp_path


def test_the_question_is_published_whole_and_answered(tmp_path):
    out, box = _ask(tmp_path, "3|4|2000000000\n", "continue")
    assert out.stdout.split() == ["continue", "rc=0"], out.stdout + out.stderr
    assert (box / "seen").read_text().splitlines() == ["1-0|badsect|1|sdc", "3|4|2000000000"]


def test_no_answer_is_no_answer(tmp_path):
    """gui_ask_choice never answers by itself; what no answer means is lcraw.sh's (continue)."""
    out, _ = _ask(tmp_path, "3|4|2000000000\n", None)
    assert out.stdout.split() == ["rc=1"], out.stdout + out.stderr


@pytest.mark.parametrize("rows", ["3|4|2000000000\n3|5|2000000000\n", "3|0|2000000000\n", "3|x|2000000000\n",
                                  "3\n", "3|4\n", "3|4|x\n", "3|4|0\n"])
def test_a_malformed_bad_sector_question_is_never_published(tmp_path, rows):
    out, box = _ask(tmp_path, rows, None)
    assert out.stdout.split() == ["rc=1"], out.stdout
    assert not (box / "gui" / "ask-request").exists()


@requires_native("jq", why="lc_gui_state reads the run's manifest with jq")
def test_the_local_screen_state_carries_the_question_and_the_raw_flags(tmp_path):
    out = run(tmp_path, f'. {posix(AGENT)}/lib/gui/askgui.sh; '   # the kiosk loads it (gui_libs)
              'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC/inv" "$LC/run" "$RUN_DIR/targets/sda"; '
              'echo 0 > "$RUN_DIR/min_free_gb"; echo \'{"schema":1}\' > "$RUN_DIR/response.json"; '
              'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo copying > "$LC/phase"; '
              'echo \'{"partitions":[{"index":4,"role":"linux","fs":"unknown","used_bytes":1,"size_bytes":1,"copy":"raw","raw_why":"rescue"}]}\' > "$LC/run/manifest.json"; '
              'echo sda > "$LC/run/targets"; echo done > "$RUN_DIR/targets/sda/state"; echo "4|8" > "$RUN_DIR/targets/sda/rescued"; '
              'printf "9-0|badsect|1|sdc\\n3|4|2000000000\\n" > "$GUI_DIR/ask-request"; '
              'lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert "ask_prompt=9-0|badsect|1|sdc" in lines and "ask_row=3|4|2000000000" in lines, lines
    tgt = next(l for l in lines if l.startswith("lc_tgt=sda|"))
    assert tgt.split("|")[10] == "1,8", tgt   # field 11: #2009's note is field 10


def test_the_kiosk_loads_what_the_local_screen_state_calls():
    """lcgui.sh asks for the question and the raw flags only when they are loaded: the kiosk
    (gui_libs) must load both, or the question would never be drawn."""
    text = (AGENT / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")
    libs = text[text.index("gui_libs() {"):text.index("gui_main() {")]
    assert '. "$LIB_DIR/askgui.sh"' in libs and '. "$LIB_DIR/lcraw.sh"' in libs
    assert libs.index('"$LIB_DIR/lcraw.sh"') < libs.index('"$LIB_DIR/lcgui.sh"')
    agent = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '. "$LIB_DIR/lccopy.sh"; . "$LIB_DIR/lcraw.sh"' in agent and '. "$LIB_DIR/askgui.sh"' in agent


# --- the native dialog (Testrunner: cc + pango/cairo) --------------------------------

from test_shrink_gui import BASE_STATE, GUI_TOOLS, _render  # noqa: E402
from test_cloner_gui import _build_gui, _count_color, _png_rgb, _token  # noqa: E402


@GUI_TOOLS
def test_the_bad_sector_question_is_drawn_with_continue_as_the_default(tmp_path):
    """Nadav 22/10: going on is what happens anyway -- "המשך" is the filled primary
    button, not the red one; the frame stays orange."""
    import time
    binary = _build_gui(tmp_path)
    until = int(time.time()) + 45
    _render(binary, tmp_path / "r", BASE_STATE + f"ask_prompt=9-0|badsect|1|sdc\nask_row=1|3|{until}\n")
    _w, _h, ch, plain = _png_rgb(tmp_path / "r" / "card-cloner-light.png")
    _w, _h, ch2, asked = _png_rgb(tmp_path / "r" / "card-cloner-badsect-light.png")
    warn, danger, action = _token("--clr-warning"), _token("--clr-danger"), _token("--clr-action")
    assert _count_color(asked, ch2, warn, tol=12) - _count_color(plain, ch, warn, tol=12) > 1500
    assert _count_color(asked, ch2, action, tol=12) - _count_color(plain, ch, action, tol=12) > 1500
    assert _count_color(asked, ch2, danger, tol=12) - _count_color(plain, ch, danger, tol=12) < 300
    assert (tmp_path / "r" / "card-cloner-badsect-dark.png").exists()


#: ask_modal and the countdown are main.c's own (static): the driver includes main.c whole,
#: under another name for its main, and feeds the real handler one event at a time.
ASK_DRIVER = r'''
#define main imagesrv_gui_main
#include "main.c"
#undef main
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f);
    printf("@ pending=%d kind=%d counting=%d\n", a.st.ask_pending, a.st.ask_kind == ASK_BADSECT, counting(&a));
    fflush(stdout);
    for (int i = 2; i < argc; i++) {
        Event e; memset(&e, 0, sizeof e); const char *v = argv[i];
        if (!strncmp(v, "key:", 4)) { e.type = UIEV_KEY; e.key = !strcmp(v + 4, "ENTER") ? KEYSYM_ENTER : KEYSYM_ESC; }
        else if (!strncmp(v, "char:", 5)) { e.type = UIEV_CHAR; e.ch = v[5]; }
        a.ask_sent[0] = 0;                          /* each event on its own: one answer per nonce */
        ask_modal(&a, &e);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def ask_driver(tmp_path_factory) -> Path:
    from test_native_local_clone import GUI
    d = tmp_path_factory.mktemp("askdrv")
    (d / "driver.c").write_text(ASK_DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


@GUI_TOOLS
@pytest.mark.parametrize("event,want", [("key:ENTER", "ask-continue|9-0"), ("char:1", "ask-continue|9-0"),
                                        ("char:2", "ask-stop|9-0"), ("key:ESC", "ask-stop|9-0")])
def test_enter_is_continue_and_only_stop_or_esc_stops(tmp_path, ask_driver, event, want):
    state = tmp_path / "s.txt"
    state.write_text("ask_prompt=9-0|badsect|1|sdc\nask_row=1|3|2000000000\n", newline="\n")
    run = subprocess.run([str(ask_driver), posix(state), event], capture_output=True, text=True, timeout=60,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    lines = [l for l in run.stdout.splitlines() if l]
    assert lines[0] == "@ pending=1 kind=1 counting=1", lines   # the screen counts each second
    assert lines[1:] == [want], lines


@GUI_TOOLS
def test_a_broken_bad_sector_question_is_not_drawn(tmp_path):
    binary = _build_gui(tmp_path)
    for name, text in (("rows", "ask_prompt=9-0|badsect|2|sdc\nask_row=1|3|2000000000\nask_row=1|4|2000000000\n"),
                       ("idx", "ask_prompt=9-0|badsect|1|sdc\nask_row=1|0|2000000000\n"),
                       ("until", "ask_prompt=9-0|badsect|1|sdc\nask_row=1|3\n")):   # 22/10: no count, no question
        r = _render(binary, tmp_path / name, BASE_STATE + text)
        assert "question incomplete or malformed" in r.stderr, name


# --- the engine on loop devices (root, the Testrunner) --------------------------------

from test_local_clone_engine import ENGINE_LOCK, FIXTURE, SBIN, _engine, _root, _sudo_ok, _table, _tool  # noqa: E402,F401
from test_local_clone_engine import fanout_bin  # noqa: E402,F401  (the fixture)

ENGINE = requires_native(
    ("sudo -n", _sudo_ok()),
    *[(t, _tool(t)) for t in ("losetup", "sgdisk", "partclone.ntfs", "partclone.fat", "partclone.ext4", "partclone.dd",
                              "mkntfs", "mkfs.vfat", "mkfs.ext4", "ntfs-3g", "dmsetup", "busybox", "gcc", "jq", "flock")],
    posix=True, why="loop devices, device-mapper and partclone as root: the Testrunner")


@pytest.fixture
def raw_disks(tmp_path):
    fx = tmp_path / "fx"
    up = _root("sh", str(FIXTURE), "setup-raw", str(fx), timeout=300)
    try:
        assert up.returncode == 0, up.stdout + up.stderr
        yield fx, dict(line.split() for line in (fx / "devs").read_text().splitlines())
    finally:
        name = fx / "dmname"
        if name.exists():
            _root("dmsetup", "remove", name.read_text().strip())
        down = _root("sh", str(FIXTURE), "teardown", str(fx), timeout=120)
        assert down.returncode == 0, down.stdout + down.stderr


def _sha(node: str, skip_bytes: int = 0, zero: tuple[int, int] | None = None) -> str:
    data = subprocess.run(["sudo", "-n", "cat", node], capture_output=True, stdin=subprocess.DEVNULL, timeout=120).stdout
    assert data, node
    if zero:
        a, b = zero
        data = data[:a] + bytes(b - a) + data[b:]
    return hashlib.sha256(data).hexdigest()


def _state(fx: Path, dev: str, name: str) -> str:
    return _root("cat", str(fx / "run" / "targets" / dev.replace("/dev/", "") / name)).stdout.strip()


#: the bad sectors: two sectors of partition 4 behind a device-mapper error target,
#: in the middle of the page 100000-100007 (a buffered read loses the whole page).
BAD_HOOK = r'''
_bad=lcbad-$$; _p=/dev/${SRC}p4; _z=$(blockdev --getsz "$_p") || exit 3
printf '0 100003 linear %s 0\n100003 2 error\n100005 %s linear %s 100005\n' "$_p" "$((_z - 100005))" "$_p" \
    | dmsetup create "$_bad" || exit 3
echo "$_bad" > "$F/dmname"
trap 'kill "$_locker" 2>/dev/null; dmsetup remove "$_bad" 2>/dev/null' EXIT
ln -sf "/dev/mapper/$_bad" "$DEVROOT/${SRC}p4"
gui_ask_choice() { echo "$*" > "$F/asked"; [ -n "${ANSWER:-}" ] || return 1; echo "$ANSWER"; }
'''


@ENGINE
def test_what_partclone_cannot_copy_reaches_every_target_raw_and_nothing_grows(tmp_path, raw_disks, fanout_bin):
    fx, devs = raw_disks
    out = _engine(tmp_path, fx, fanout_bin, "same big", 0)
    src = devs["src"]
    for n in ("same", "big"):
        t = out["target"][n]
        assert t["state"] == "done", out["raw"][-3000:]
        assert "הועתק במצב גולמי (כמו dd)" in t["error"], t
        assert "מחיצה 4 — מערכת הקבצים לא מזוהה" in t["error"], t
        assert "מחיצה 3 — partclone סירב למערכת הקבצים (ntfs" in t["error"], t
        assert t["table"].split("|")[0] == "0", t                      # nothing grew
        for i in (3, 4):                                               # every byte, raw
            assert _sha(f"{devs[n]}p{i}") == _sha(f"{src}p{i}"), (n, i)
        assert _state(fx, devs[n], "readback") in ("sampled", "verified"), n
    want, big = _table(src)["parts"], _table(devs["big"])["parts"]
    for i in (1, 2, 3, 4):
        assert big[i]["First sector"] == want[i]["First sector"] and big[i]["Partition size"] == want[i]["Partition size"], i


@ENGINE
@pytest.mark.parametrize("answer", ["", "continue", "stop"])
def test_bad_sectors_ask_and_only_stop_stops_the_copy(tmp_path, raw_disks, fanout_bin, answer):
    """Nadav 22/10: "continue" and no answer (the minute passed, or no GUI) both copy
    through the bad sectors and say so in orange; only "stop" fails, the source untouched."""
    fx, devs = raw_disks
    src_before = _sha(devs["src"])
    hook = tmp_path / "hook.sh"; hook.write_text(BAD_HOOK, newline="\n")
    out = _engine(tmp_path, fx, fanout_bin, "same", 0, env=(f"HOOK={hook}", f"ANSWER={answer}"))
    asked = _root("cat", str(fx / "asked")).stdout.split()
    assert asked[:2] == ["badsect", devs["src"].replace("/dev/", "")], (asked, out["raw"][-3000:])
    assert _sha(devs["src"]) == src_before                       # the source is only ever read
    t = out["target"]["same"]
    if answer == "stop":
        assert t["state"] == "failed" and "המפעיל בחר לעצור; המקור לא שונה" in t["error"], t
        return
    assert t["state"] == "done", out["raw"][-3000:]
    assert "מחיצה 4 במצב הצלה: 8 סקטורים לא נקראו ונכתבו כאפסים — לא זהה למקור" in t["error"], t
    assert _sha(f"{devs['same']}p4") == _sha(f"{devs['src']}p4", zero=(100000 * 512, 100008 * 512))
    assert _state(fx, devs["same"], "readback") == "unverified", out["raw"][-3000:]


#: an orange SMART on the source (lcinv.sh smart_probe), and any question the engine
#: might ask recorded: SMART's (smart_choice, gui_smart_choice) and the dialog's.
SMART_HOOK = r'''
smart_probe() { if [ "$1" = "$SRC" ]; then echo warn; else echo ok; fi; }
gui_ask_choice() { echo "ask $*" >> "$F/asked"; return 1; }
gui_smart_choice() { echo "smart-gui $*" >> "$F/asked"; return 1; }
smart_choice() { echo "smart $*" >> "$F/asked"; return 1; }
'''


@ENGINE
def test_an_orange_smart_source_is_copied_without_a_question(tmp_path, raw_disks, fanout_bin):
    """Nadav 22/10: an orange SMART on the source is no question of the copy -- a guard of
    what was already so (it passes on the code before this change too)."""
    fx, devs = raw_disks
    hook = tmp_path / "hook.sh"; hook.write_text(SMART_HOOK, newline="\n")
    out = _engine(tmp_path, fx, fanout_bin, "same", 0, env=(f"HOOK={hook}",))
    assert out["inv"]["src"][5] == "warn", out["inv"]                # the stub took: the source is orange
    assert out["target"]["same"]["state"] == "done", out["raw"][-3000:]
    asked = _root("sh", "-c", f'cat {fx / "asked"} 2>/dev/null; true').stdout
    assert asked == "", asked


# --- Nadav 22/10: the source's SMART already counts sectors it could not read ------------
#
# An orange line on the choice before the copy -- no question, nothing blocked. From the
# record the inventory's smart_probe kept (smart.sh fields 4 = 197, 5 = 198 / NVMe media
# errors); SMART not read, or 0, says nothing -- never "0".

@pytest.mark.parametrize("record,want", [
    ("ok ok 0 12 0 0", "12"),                          # 197 = 12
    ("warn uncorrectable 0 0 7 0", "7"),               # 198 (or NVMe media errors) alone
    ("warn pending_sectors 0 12 5 0", "12"),           # both: the larger, never the sum
    ("ok ok 0 0 0 0", ""),                             # 0: no line
    ("unchecked no_smartctl 0 12 0 0", ""),            # SMART not read: no line, whatever is stored
    ("unchecked timeout 0 0 0 0", ""),
])
def test_what_smart_says_the_source_could_not_read(tmp_path, record, want):
    out = run(tmp_path, f'mkdir -p "$RUN_DIR/smart"; echo "{record}" > "$RUN_DIR/smart/sdc.v"; '
              'echo "[$(lc_smart_unread sdc)]"; echo "[$(lc_smart_unread sdz)]"')
    assert out.stdout.split() == [f"[{want}]", "[]"], out.stdout + out.stderr   # sdz: no record at all


@pytest.mark.parametrize("record,want", [("ok ok 0 12 0 0", "lc_unread=sdc|12"), ("ok ok 0 0 0 0", None),
                                         ("unchecked no_smartctl 0 12 0 0", None)])
def test_the_choice_screen_state_carries_the_source_unread_sectors(tmp_path, record, want):
    out = run(tmp_path, 'export GUI_DIR=$RUN_DIR/gui; mkdir -p "$GUI_DIR" "$LC/inv" "$LC/measure" "$RUN_DIR/smart"; '
              'echo 0 > "$RUN_DIR/min_free_gb"; echo \'{"schema":1}\' > "$RUN_DIR/response.json"; '
              'echo local > "$LC/screen"; echo done > "$LC/inv.state"; echo idle > "$LC/phase"; echo sdc > "$LC/source"; '
              f'echo "{record}" > "$RUN_DIR/smart/sdc.v"; echo "ok ok 0 40 0 0" > "$RUN_DIR/smart/sda.v"; '
              'lc_gui_state; cat "$GUI_DIR/state"')
    assert out.returncode == 0, out.stderr
    lines = [l for l in out.stdout.splitlines() if l.startswith("lc_unread=")]
    assert lines == ([want] if want else []), lines    # only the source's -- sda's 40 is not said


#: the source screen through the real lc_state.c / lc_text.c / screens_local.c: the line it says
#: (lc_unread_text) and a PNG of the screen.
UNREAD_DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include "lcui.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
/* weak: a GUI without the line links too, and says nothing (the negative control is behaviour) */
extern int lc_unread_text(const App *a, char *out, size_t n) __attribute__((weak));
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT; a.force_cloner = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) { perror(argv[1]); return 2; }
    state_parse(&a.st, f); fclose(f); lc_after_reload(&a); app_route(&a);
    char t[400]; printf("[%s]\n", lc_unread_text && lc_unread_text(&a, t, sizeof t) ? t : "");
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1280, 800);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1280, 800); cairo_destroy(cr);
    cairo_surface_write_to_png(s, argv[2]); cairo_surface_destroy(s);
    return 0;
}
'''


@pytest.fixture(scope="module")
def unread_driver(tmp_path_factory) -> Path:
    from test_native_local_clone import GUI
    d = tmp_path_factory.mktemp("unreaddrv")
    (d / "driver.c").write_text(UNREAD_DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _unread_screen(driver: Path, tmp_path: Path, extra: str) -> tuple[str, int]:
    from test_native_local_clone import DISKS, FITS, GUI
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "s.txt"; png = tmp_path / "s.png"
    state.write_text(DISKS + FITS + extra, encoding="utf-8", newline="\n")
    run_ = subprocess.run([str(driver), posix(state), posix(png)], capture_output=True, text=True, encoding="utf-8",
                          timeout=120, stdin=subprocess.DEVNULL, env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run_.returncode == 0, run_.stderr
    _w, _h, ch, px = _png_rgb(png)
    said = [l for l in run_.stdout.splitlines() if l.startswith("[")]   # the rest: lc-measure|sdc, the GUI's own record
    assert len(said) == 1, run_.stdout
    return said[0], _count_color(px, ch, _token("--clr-warning-soft"), tol=3)


@GUI_TOOLS
def test_the_source_screen_says_the_unread_sectors_in_orange(tmp_path, unread_driver):
    said, soft = _unread_screen(unread_driver, tmp_path / "a", "lc_unread=sdc|12\n")
    assert said == "[דיסק המקור כבר נתקל ב-12 נקודות שלא הצליח לקרוא — ייתכן שבזמן השיכפול תופיע השאלה 'להמשיך?']", said
    none, base = _unread_screen(unread_driver, tmp_path / "b", "")
    assert none == "[]", none
    assert soft - base > 5000, (soft, base)            # the orange banner (warning_soft fill), on the screen


@GUI_TOOLS
@pytest.mark.parametrize("extra", ["lc_unread=sda|12\n",    # not the chosen source (sdc)
                                   "lc_unread=sdc|0\n", "lc_unread=sdc|x\n", "lc_unread=sdc\n"])
def test_no_line_for_another_disk_zero_or_a_broken_value(tmp_path, unread_driver, extra):
    said, _ = _unread_screen(unread_driver, tmp_path, extra)
    assert said == "[]", (extra, said)
