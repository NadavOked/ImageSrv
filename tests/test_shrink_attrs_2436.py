"""#2436 -- the attrs of a shrink record reach sgdisk only as 16 hex digits.

`_shrink_gpt` (agent/lib/shrink/shrink.sh) builds `-A <idx>:=:0x<attrs>` and
splits it on purpose (SC2086), so a record whose `attrs` carried a space --
`0 --delete=3` -- handed sgdisk a second option: the restore at boot deleted
another partition of the build machine's disk. The record comes from the
server (hello `shrink_open`), and shrinkoffer.sh compared the start, the
unique GUID and the size with the disk -- never the attrs.

Two halves:

* **the agent**: a record whose attrs are not 16 hex digits is not offered --
  nothing is asked, nothing is written, nothing is closed, and the log says
  why; `_shrink_gpt` itself refuses such a value before sgdisk runs (the
  last guard, for every caller).
* **the server** (`server/disks/shrink_records.py`): `shrink-open` refuses
  such attrs as `bad_layout`, so the value is never remembered at all.

Negative control: shrink.sh, shrinkoffer.sh and shrink_records.py back to
main -- the offer test sees sgdisk called with `--delete=3`, the direct one
sees sgdisk run, and the server tests see the record accepted.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH
from test_shrink_memory import fake_jq, offer_run, record, record_line, sent
from test_shrink_on_capture import order, rewrites, sgdisk_calls
from test_timeouts import log_of

BAD_ATTRS = ["0 --delete=3", "0000000000000000 -z", "00000000000000000", "000000000000000g", "0;reboot"]


@requires_native(("bash", BASH))
@pytest.mark.parametrize("attrs", BAD_ATTRS)
def test_a_record_with_attrs_that_are_not_16_hex_digits_is_not_offered(tmp_path, attrs):
    rec = record(attrs=attrs)
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", prelude=fake_jq(rec))
    assert "rc=0" in out, out                              # the offer never stops the machine
    assert "ASK growback" not in out, out                  # nobody is asked to write it
    assert rewrites(run) == [], sgdisk_calls(run)
    assert not any("--delete" in c or " -z" in f" {c}" for c in sgdisk_calls(run)), sgdisk_calls(run)
    assert not [s for s in order(run) if s.startswith(("ntfsresize", "ntfsfix", "blockdev"))]
    assert sent(run, "shrink_close") == []                 # the record stays for the console
    log = log_of(run)
    assert "attribute flags" in log and "nothing changed" in log, log


@requires_native(("bash", BASH))
def test_the_attrs_of_a_good_record_still_reach_sgdisk(tmp_path):
    """The guard is not a wall: 16 hex digits, either case, are written back."""
    rec = record(attrs="80000000000000a1")
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", prelude=fake_jq(rec))
    assert "rc=0" in out and "ASK growback sda" in out, out
    (call,) = rewrites(run)
    assert call.endswith("-A 3:=:0x80000000000000a1 " + str(box / "dev" / "sda").replace("\\", "/")), call


@requires_native(("bash", BASH))
@pytest.mark.parametrize("attrs", BAD_ATTRS)
def test_shrink_gpt_refuses_bad_attrs_before_sgdisk_runs(tmp_path, attrs):
    """Every caller of `_shrink_gpt`, not only the offer: rc 1 (the table step), no sgdisk."""
    q = attrs.replace("'", "'\\''")
    box, run, out = offer_run(
        tmp_path, answer_records=None,
        prelude=f"_shrink_gpt sda 3 2048 1000 EBD0A0A2-B9E5-4433-87C0-68B6B72699C7 "
                f"4C7B1E00-0000-4000-8000-000000000003 'Basic data partition' '{q}'; echo \"gpt=$?\"; ")
    assert "gpt=1" in out, out
    assert sgdisk_calls(run) == [], sgdisk_calls(run)


# --- the server -----------------------------------------------------------------------


def test_the_server_refuses_attrs_that_are_not_16_hex_digits(tmp_path: Path):
    from server import db
    from server.disks import shrink_records
    from test_shrink_records import layout

    conn = db.connect(tmp_path / "t.db")
    for bad in BAD_ATTRS:
        with pytest.raises(shrink_records.BadRecord) as exc:
            shrink_records.open_record(conn, layout(attrs=bad))
        assert exc.value.code == "bad_layout", bad
        with pytest.raises(shrink_records.BadRecord):
            shrink_records.open_record(conn, layout(partitions=[{**layout(), "attrs": bad}]))
    assert conn.execute("SELECT COUNT(*) FROM shrink_records").fetchone()[0] == 0
    # 16 hex digits in either case, and no attrs at all, are still remembered
    assert shrink_records.open_record(conn, layout(attrs="80000000000000a1"))["attrs"] == "80000000000000a1"
    assert shrink_records.open_record(conn, layout(serial="S2", attrs=None))["attrs"] is None


# --- review 04/11: a field that carries the separator moves the others ------------------
#
# shrink_records_refresh joins the record with `|` (jq -r) and shrink_offer_disk splits it
# with IFS='|': a name `X|8000000000000000` was read as attrs=8000000000000000 -- 16 hex
# digits, past shrink_args_ok and past the server's own check -- and a newline in the name
# was a whole line of its own. jq now turns `|`, CR and LF into \037, the offer refuses a
# line with \037 (or a `|` left in its last field), and the server refuses them on open.

SHIFT = "X|8000000000000000"


def raw_jq(*lines: str) -> str:
    """json_get_join as jq would print it, the lines as given (no repr: \037 stays a byte)."""
    body = " ".join(f"echo '{line}';" for line in ("ok", *lines))
    return 'json_get_join() { [ -s "$1" ] || return 1; ' + body + ' }; '


@requires_native(("bash", BASH))
@pytest.mark.parametrize("name", [SHIFT, "Basic\x1fdata"])
def test_a_name_that_moved_the_fields_is_not_offered(tmp_path, name):
    rec = record(name=name)
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n", prelude=raw_jq(record_line(rec)))
    assert "rc=0" in out, out
    assert "ASK growback" not in out, out
    assert rewrites(run) == [], sgdisk_calls(run)
    assert "nothing changed" in log_of(run), log_of(run)


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq is required -- the real expression")
@pytest.mark.parametrize("name", [SHIFT, "a\n5|S2436|3|2048|9|T|U|N|0000000000000000||x", "a\rb"])
def test_the_real_jq_turns_separators_into_one_refused_line(tmp_path, name):
    rec = record(name=name)
    box, run, out = offer_run(tmp_path, answer_records=[rec], keys="1\n")
    lines = (run / "shrink_open").read_text(encoding="utf-8").split("\n")
    lines = [ln for ln in lines if ln]
    assert len(lines) == 2 and "\x1f" in lines[1] and lines[1].count("|") == 10, lines
    assert "ASK growback" not in out and rewrites(run) == [], out


def test_the_server_refuses_a_separator_in_the_text_fields(tmp_path: Path):
    from server import db
    from server.disks import shrink_records
    from test_shrink_records import layout

    conn = db.connect(tmp_path / "t.db")
    for key, bad in (("name", SHIFT), ("name", "a\nb"), ("name", "a\rb"),
                     ("unique_guid", "4C7B1E00|0"), ("type_guid", "EBD0A0A2\nX")):
        with pytest.raises(shrink_records.BadRecord) as exc:
            shrink_records.open_record(conn, layout(**{key: bad}))
        assert exc.value.code == "bad_layout", (key, bad)
    assert conn.execute("SELECT COUNT(*) FROM shrink_records").fetchone()[0] == 0
    assert shrink_records.open_record(conn, layout(name="Basic data partition"))["name"] == "Basic data partition"
