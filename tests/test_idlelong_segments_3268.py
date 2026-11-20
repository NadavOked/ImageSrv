"""#3268 (Nadav 18/11, metal of the same day) -- the sustained write check continues past the first segment.

Cloner 2, 18/11, LabRoot 49d64a9f6: the APS-SL3N-512 passed the 256MiB sustained check at 213 MB/s (first half) and 216 MB/s (last
half), floor 74 -- and the same drive writes 43 MB/s in the wave. 256MiB was still inside its cache. "Write until the rate is
stable" cannot be the stop rule: a cache is a stable plateau (213 against 216 is 1.4% apart), and it would stop where the single
segment stopped. What continues the check is a SEGMENT after a segment -- each its own copy, write, verified put-back and
release -- while the LAST window is not under the floor, nothing holds the drawers, there is room, and the next segment would
still end inside `IDLE_WC_LONG_BUDGET_S` (Nadav's 30 s, the copy and the put-back included).

The drive is the model of test_idlelong_3268.py (`cache MiB, MB/s inside it, MB/s after it`; the clock the agent reads moves by every
write to the image, the put-back's too). Proved from outside the code under test: the image's bytes, the server's folder, the number
of copies the server was asked to keep (`longposts`).

**Negative control:** `agent/lib/restore/idlelong.sh` back at the base (`9ab7a1a4a`): one segment, so the drive whose cache is
longer than one segment comes out ~200 MB/s, green.
"""

from __future__ import annotations

import re
import stat
from pathlib import Path

from test_idlecheck_3268 import MIB, _res, _sha
from test_idlelong_3268 import DISK, _field, _long, _run, _written  # noqa: F401 -- _long builds the disk, the shims and the drive model

SEG = 16   # MiB a segment writes here (the default is 256: the disks are small). A multiple of 16, the disk takes five of them: 80, 96, 112, 128, 144


def _posts(t: Path) -> int:
    f = t / "longposts"
    return len(f.read_text().split()) if f.exists() else 0


def _wrap_curl(t: Path) -> None:
    """The file-based server of test_idlelong_3268, behind a wrapper that can act after a call: raise the hold at the second
    release (the first segment's), or refuse every copy after the first long one (the marker files say which)."""
    real = t / "bin" / "curl"
    real.rename(t / "bin" / "curl.real")
    real.write_text(
        '#!/bin/bash\n"$(dirname "$0")/curl.real" "$@"; rc=$?\n'
        'case "$*" in *"-X DELETE"*) echo x >> "$SHIM_LOG/deletes"; [ ! -e "$SHIM_LOG/hold_on_second_delete" ] || [ "$(wc -l < "$SHIM_LOG/deletes")" -lt 2 ] || touch "$RUN_DIR/idlecheck/hold" ;; esac\n'
        'case "$*" in *"-X POST"*"/write-backup/long"*) [ ! -e "$SHIM_LOG/refuse_after_first" ] || touch "$FAKE_SRV/refuse_long" ;; esac\n'
        'exit $rc\n', newline="\n")
    real.chmod(real.stat().st_mode | stat.S_IEXEC)


def _check(t: Path, budget: str = "30", extra: str = "") -> object:
    return _run(t, f"export IDLE_WC_LONG_BUDGET_S={budget}; {extra} idle_wc_disk sdx", long_mib=SEG)


def test_a_drive_whose_cache_is_longer_than_one_segment_is_found_slow_by_the_next_segment(tmp_path):
    # 48MiB of cache at 200 MB/s, then 20: the first 16MiB (and its put-back) are inside it -- today's single segment says ~200, green
    img = _long(tmp_path, model="48 200 20")
    before = _sha(img)
    out = _check(tmp_path)
    assert out.returncode == 0, out.stderr
    r = _res(tmp_path)
    assert r["state"] == "ok" and 15 <= int(r["mbps"]) <= 25, f"the rate is the LAST window of the last segment, not the cache's: {r}"
    assert "ב-2 מקטעים" in r["reason"] and f"{2 * SEG}MiB" in r["reason"], r["reason"]
    first = int(r["reason"].split("בחלון האחרון (")[1].split(" ")[0])
    assert first > 150, f"the first window is the first segment's, still in the cache, and the words say so: {r['reason']}"
    assert re.fullmatch(r"slow\|איטי — (1[5-9]|2[0-5]) MB/s, מתחת לסף 74 MB/s", _field(tmp_path)), _field(tmp_path)
    assert _posts(tmp_path) == 2, "it stopped at the first segment, or went on past a slow window"
    assert _sha(img) == before, "the original bytes are not back"
    assert not list((tmp_path / "srv").iterdir()), "a copy is still on the server after a verified restore"
    assert not (tmp_path / "run/idlecheck/long.sdx").exists(), "the work dir (the pattern) is left in RAM"
    assert "stopped by: the last window is under the floor" in (tmp_path / "run/agent.log").read_text()


def test_the_budget_decides_how_many_segments_and_zero_is_one(tmp_path):
    # a drive with no cache: a segment (write + put-back) is ~0.17 s of the model's clock. 0.6 s allows three, 0 allows one, 1000 is the room: five
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    for budget, segments in (("0.6", 3), ("0", 1), ("1000", 5)):
        (tmp_path / "longposts").unlink(missing_ok=True)
        assert _check(tmp_path, budget).returncode == 0
        assert _res(tmp_path)["state"] == "ok" and int(_res(tmp_path)["mbps"]) >= 150, (budget, _res(tmp_path))
        assert _posts(tmp_path) == segments, f"budget {budget}: {_posts(tmp_path)} segments, wanted {segments}"
        assert _sha(img) == before, f"budget {budget}: the original bytes are not back"
    assert "stopped by: no room" in (tmp_path / "run/agent.log").read_text()
    assert "stopped by: the time budget" in (tmp_path / "run/agent.log").read_text()


def test_a_task_that_takes_the_drawers_ends_the_check_after_the_segment_it_is_in(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    _wrap_curl(tmp_path)
    (tmp_path / "hold_on_second_delete").write_text("")   # the first release is the short check's, the second the first segment's: a person presses a button then
    before = _sha(img)
    assert _check(tmp_path).returncode == 0
    assert _res(tmp_path)["state"] == "ok" and _posts(tmp_path) == 1
    assert "stopped by: a task holds the drawers" in (tmp_path / "run/agent.log").read_text()
    assert _sha(img) == before and not list((tmp_path / "srv").iterdir())


def test_a_segment_the_server_will_not_keep_is_not_written_and_the_ones_before_it_stand(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    _wrap_curl(tmp_path)
    (tmp_path / "refuse_after_first").write_text("")
    before = _sha(img)
    assert _check(tmp_path).returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "ok" and "מקטעים" not in r["reason"] and f"כתיבה מתמשכת של {SEG}MiB" in r["reason"], r   # one segment, worded as one
    assert _posts(tmp_path) == 2, "the second segment was never asked for"
    assert _written(tmp_path) < 70 * MIB, "it wrote the second segment without a stored copy"   # the short check ~24MiB + one segment ~32MiB; a second one would make ~88
    assert "segment 2 not started" in (tmp_path / "run/agent.log").read_text()
    assert _sha(img) == before


def test_a_write_that_fails_in_a_later_segment_fails_the_disk_by_name_and_every_segment_is_back(tmp_path):
    img = _long(tmp_path, model="1000 200 200")
    before = _sha(img)
    (tmp_path / "failafter").write_text("3")   # 8MiB chunks: two in the first segment, one in the second, then I/O error
    assert _check(tmp_path).returncode == 0
    r = _res(tmp_path)
    assert r["state"] == "failed" and "כתיבה מתמשכת" in r["reason"] and "Input/output error" in r["reason"], r
    assert _posts(tmp_path) == 2
    assert _sha(img) == before, "the half-written second segment, or the first, was not put back"
    assert not list((tmp_path / "srv").iterdir())


def test_a_drive_that_is_slow_from_the_start_is_judged_by_its_first_segment(tmp_path):
    # guard, passes before and after: slow is slow -- nothing is gained by writing more of it
    _long(tmp_path, model="1 40 40")
    assert _check(tmp_path).returncode == 0
    assert _res(tmp_path)["state"] == "ok" and 35 <= int(_res(tmp_path)["mbps"]) <= 41
    assert _posts(tmp_path) == 1
