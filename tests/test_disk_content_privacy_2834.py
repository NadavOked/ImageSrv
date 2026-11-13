"""#2834 (the rest of #1924's map; Nadav 20/10): nothing read from a disk's
content is kept outside the image -- not in the journal, a report, the DB or a
log. The agent's log leaves the machine: the server keeps its tail
(``datareport.sh``, ``watchdog.sh`` -> ``server/agent/logs.py``, which filters
GUIDs, labels and ``name='...'`` before it stores a byte) and the clone stick
copies it into its diag file (``usbdiag.sh``, filtered by ``usb_scrub``).

What those filters cannot find is a value without a shape. Found here:

1. a GPT partition name read from a disk -- a local clone's source names its
   partitions, and a person may have -- was written to the log in forms neither
   filter knows (``name 'X'``, ``named 'X'``, sgdisk's ``-c 1:X``):
   ``lc_table_check`` (lcplan.sh) and ``writemark_clear`` (writemark.sh). The
   log now says only what the name is: empty, the mark, or another one.
2. ``_lc_gpt_why`` (lccopy.sh) put the log's last lines into a target's error;
   the local clone's report takes that error to the server's journal as it is
   (``local_clone_report.journal_line``, no filter), and after a refused table
   those lines hold the source's disk and partition GUIDs (``-u``/``-U``). The
   tool's own words stay; a GUID becomes ``<guid>``.

lc_table_check also runs here behaviourally -- #1924 covered it by its source
text only, the engine's test being skipped on Windows.
"""

from __future__ import annotations

import re

from native import requires_native
from server.agent import logs
from test_agent import AGENT, BASH, posix, sh
from test_restore_evidence import log_of
from test_writemark_1798 import _box, _fail

pytestmark = requires_native(("bash", BASH))

#: a name a person gave a partition -- with a quote, which the server's `name='...'` stops at
PRIVATE = "Ofer's thesis"
GUID = re.compile(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}")


def _names_file(box) -> str:
    path = box / "names"
    path.write_text(f"1|{PRIVATE}|0\n2||0\n3|{PRIVATE}|0\n", encoding="utf-8", newline="\n")
    return posix(path)


# --- 1. writemark_clear: the source's names never reach the log -------------------


def test_a_name_that_does_not_read_back_is_not_logged(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + f'apply_gpt sda m.json || exit 9; : > {posix(box)}/sgdisk.lie; '
             f'writemark_clear sda {_names_file(box)!r}; echo "rc=$?"')
    assert out.strip().splitlines()[-1] == "rc=1", (out, log_of(run))
    log = log_of(run)
    assert "partition 1 is named another name (not logged) after sgdisk -c, not another name (not logged)" in log, log
    assert "Ofer" not in log and "thesis" not in log, log


def test_a_refused_clear_does_not_log_the_names_it_asked_for(tmp_path):
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + 'apply_gpt sda m.json || exit 9; '
             f'echo "-c *" > {posix(box)}/sgdisk.fail; writemark_clear sda {_names_file(box)!r}; echo "rc=$?"')
    assert out.strip().splitlines()[-1] == "rc=1", (out, log_of(run))
    log = log_of(run)
    assert "sgdisk refused to clear the incomplete-write mark (partitions 1 2 3)" in log, log
    assert "Ofer" not in log and "thesis" not in log, log


def test_the_names_still_come_back_on_success(tmp_path):
    """The guard: the fix is in the log line, not in what is written."""
    box, run, prelude = _box(tmp_path)
    out = sh(prelude + f'apply_gpt sda m.json || exit 9; writemark_clear sda {_names_file(box)!r}; echo "rc=$?"')
    assert out.strip().splitlines()[-1] == "rc=0", (out, log_of(run))
    assert (box / "name.1").read_text(encoding="utf-8").strip() == PRIVATE


# --- 1. lc_table_check, behaviourally ------------------------------------------------

DISK = "AAAAAAAA-1111-2222-3333-444444444444"
PART = "BBBBBBBB-1111-2222-3333-444444444444"
ESP = "C12A7328-F81F-11D2-BA4B-00A0C93EC93B"


def _table_check(tmp_path, *, disk=DISK, part=PART, name="imagesrv-incomplete") -> tuple[str, str]:
    """lc_table_check over an sgdisk that reads back one ESP: ``disk``, ``part``
    and ``name`` are what the target says. Returns (stdout, the agent's log)."""
    run = tmp_path / "run"; (run / "targets" / "sda").mkdir(parents=True)
    lc = tmp_path / "lc"; (lc / "run").mkdir(parents=True)
    (lc / "run" / "entries").write_text("1|EFI system partition|0000000000000000\n", encoding="utf-8", newline="\n")
    blk = tmp_path / "sys" / "block" / "sda"; blk.mkdir(parents=True)
    (blk / "size").write_text("1000000\n")
    log = run / "agent.log"   # common.sh's own, from RUN_DIR
    plan = f"1|{ESP}|esp|vfat|2048|104857600|local:p1|-|false|{PART}||0000000000000000"
    out = sh(
        f'export RUN_DIR={posix(run)!r} LC={posix(lc)!r} SYSROOT={posix(tmp_path)!r} '
        f'DEVROOT={posix(tmp_path)!r}/dev; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/jsonq.sh; '
        f'. {posix(AGENT)}/lib/restore/writemark.sh; . {posix(AGENT)}/lib/capture/manifest.sh; '
        f'. {posix(AGENT)}/lib/localclone/lcinv.sh; . {posix(AGENT)}/lib/localclone/lcplan.sh; '
        f'json_get() {{ echo {DISK}; }}; manifest_plan() {{ echo {plan!r}; }}; '
        'sgdisk() { case "$1" in '
        f'-p) echo "Disk identifier (GUID): {disk}"; echo "   1            2048          206847   100.0 MiB   EF00  x" ;; '
        f'-i) echo "Partition GUID code: {ESP} (EFI system partition)"; echo "Partition unique GUID: {part}"; '
        'echo "First sector: 2048 (at 1024.0 KiB)"; echo "Partition size: 204800 sectors (100.0 MiB)"; '
        'echo "Attribute flags: 0000000000000000"; '
        f'printf "Partition name: \'%s\'\\n" {name!r} ;; esac; }}; '
        'lc_table_check sda /m.json; echo "rc=$?"; printf "error=%s\\n" "$LC_ERROR"')
    return out, log.read_text(encoding="utf-8") if log.exists() else ""


def test_the_table_check_passes_a_table_that_came_back(tmp_path):
    out, log = _table_check(tmp_path)
    assert "rc=0" in out and "error=\n" in out + "\n", (out, log)


def test_the_table_check_does_not_log_a_name_read_from_the_target(tmp_path):
    out, log = _table_check(tmp_path, name=PRIVATE)
    assert "rc=1" in out, (out, log)
    assert "error=מחיצה 1: שם המחיצה אינו הסימן שנכתב" in out, out
    assert "partition 1 is named another name (not logged), not the mark" in log, log
    assert "Ofer" not in out + log and "thesis" not in out + log, (out, log)


def test_the_table_check_says_a_guid_only_in_the_log_and_the_server_drops_it(tmp_path):
    """#1924's rule, now run: the error carries no GUID; the log does, and the
    server's filter (logs.redact) takes it out before the log is stored."""
    for kw, want in (({"disk": "CCCCCCCC-1111-2222-3333-444444444444"}, "ה-GUID של הדיסק ביעד אינו של המקור"),
                     ({"part": "DDDDDDDD-1111-2222-3333-444444444444"}, "ה-GUID הייחודי אינו של המקור")):
        out, log = _table_check(tmp_path / next(iter(kw)), **kw)
        error = out.split("error=", 1)[1]
        assert "rc=1" in out and want in error, (out, log)
        assert not GUID.search(error), error
        assert next(iter(kw.values())) in log, log
        assert not GUID.search(logs.redact(log)), logs.redact(log)


# --- 2. _lc_gpt_why: a refused table's error carries no GUID -------------------------


def test_a_refused_table_names_its_cause_without_the_sources_guids(tmp_path):
    """The real path: apply_gpt's one table call is refused, its log line holds
    every -u/-U, and _lc_gpt_why makes the target's error from the log's tail --
    the error the local clone's report takes to the server's journal."""
    disk = "5E5E5E5E-1234-5678-9ABC-DEF012345678"
    box, run, prelude = _box(tmp_path, disk_guid=disk)
    _fail(box, "-a 1 *")
    out = sh(prelude + f'. {posix(AGENT)}/lib/localclone/lccopy.sh; '
             'apply_gpt sda m.json; echo "rc=$?"; printf "why=%s\\n" "$(_lc_gpt_why)"')
    assert "rc=1" in out, (out, log_of(run))
    log = log_of(run)
    assert disk in log and "11111111-1111-1111-1111-111111111111" in log, log   # the values stay on the machine
    why = out.split("why=", 1)[1]
    assert why.startswith("טבלת המחיצות לא נכתבה: "), why
    assert "sgdisk refused the table" in why and "<guid>" in why, why   # #1455: the cause is still named
    assert not GUID.search(why), why
