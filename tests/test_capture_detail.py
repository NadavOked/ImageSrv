"""#1505 — פירוט קליטה חי בקונסולה: טבלת מחיצות, קצב בחלון, ETA רק כשנמדד.

שלוש שכבות:
* ‏`server/library/capture_detail.py` — חישוב טהור עם שעון מוזרק: ‏done_bytes מ-source_progress,
  קצב מחלון (לא ממוצע), `None` עד שנמדד, ‏`None` אחרי שתיקה, ETA רק כשכל המחיצות נמדדו.
* ה-API — דיווח עם `partitions` מגיע ל-`/api/console/tasks` כ-`capture_detail`; טבלה
  פגומה נזרקת בשלמותה; סוכן ישן (בלי טבלה) = `null`, לא טבלה ריקה.
* הסוכן — `capturedetail.sh` בונה את הקובץ ו-`build_progress` נושא אותו (תחת bash;
  רק בדיווח של משימה).
"""

from __future__ import annotations

import json
import types

import pytest

from server.library import capture_detail as cd

GB = 1024 ** 3


def part(index, state, used, role="windows", fs="ntfs", size=None):
    return {"index": index, "role": role, "fs": fs, "state": state,
            "size_bytes": size if size is not None else (used or 0) * 2 + GB, "used_bytes": used}


def targets(parts, blocks=None, **extra):
    t = {"dev": "sda", "bytes_written": 0, "bytes_total": 0, "state": "capturing", "partitions": parts, **extra}
    if blocks:
        t["source_progress"] = {"partition": blocks[0], "blocks_read": blocks[1], "blocks_total": blocks[2]}
    return [t]


@pytest.fixture(autouse=True)
def _clean_samples():
    cd._samples.clear(); cd._first.clear()
    yield
    cd._samples.clear(); cd._first.clear()


def row(task_id, tg, state="running"):
    return {"id": task_id, "state": state, "targets_json": json.dumps(tg)}


# --- bytes done ---------------------------------------------------------------


def test_done_bytes_follow_the_state_and_the_block_fraction_of_the_reading_partition():
    tg = targets([part(1, "done", 100), part(2, "reading", 1000), part(3, "waiting", 500), part(4, "shrinking", 7)],
                 blocks=(2, 250, 1000))
    view = cd.view(row("t", tg), now=1000.0)
    assert [p["done_bytes"] for p in view["partitions"]] == [100, 250, 0, 0]
    assert view["read_bytes"] == 350 and view["total_used_bytes"] == 1607


def test_an_unmeasured_partition_makes_the_total_unknown_not_partial():
    tg = targets([part(1, "done", 100), part(2, "waiting", None)])
    view = cd.view(row("t", tg), now=1000.0)
    assert view["total_used_bytes"] is None and view["read_bytes"] is None, "סכום חלקי הוצג כמכנה"
    assert view["partitions"][1]["used_bytes"] is None


def test_a_reading_partition_without_a_block_count_has_no_done_bytes():
    tg = targets([part(1, "reading", 100)])
    view = cd.view(row("t", tg), now=1000.0)
    assert view["partitions"][0]["done_bytes"] is None and view["read_bytes"] is None


# --- rate and ETA: measured in a window, null until then ---------------------


def feed(task_id, samples, parts_fn):
    """samples = [(t, compressed, partition-1 blocks_read)]"""
    for t, comp, blocks in samples:
        cd.record(task_id, targets(parts_fn(), blocks=(1, blocks, 1000)), comp, now=t)


def one_part():
    return [part(1, "reading", 1000)]


def test_no_rate_and_no_eta_before_a_window_is_measured():
    feed("t", [(100.0, 10, 100)], one_part)
    view = cd.view(row("t", targets(one_part(), blocks=(1, 100, 1000))), now=100.0)
    assert view["rate_bps"] is None and view["eta_s"] is None, "קצב מדגימה אחת"
    feed("t", [(103.0, 400, 200)], one_part)      # 3 שניות — מתחת ל-MIN_SPAN_S
    view = cd.view(row("t", targets(one_part(), blocks=(1, 200, 1000))), now=103.0)
    assert view["rate_bps"] is None and view["eta_s"] is None, "קצב מחלון קצר מדי"


def test_rate_is_the_window_slope_and_eta_uses_the_uncompressed_axis():
    feed("t", [(100.0, 0, 0), (110.0, 5000, 100), (120.0, 10000, 200)], one_part)
    view = cd.view(row("t", targets(one_part(), blocks=(1, 200, 1000))), now=120.0)
    assert view["rate_bps"] == 500                       # 10000 דחוסים ב-20 שניות
    # נקרא 200 מתוך 1000 (בשימוש) ב-20 שניות -> 10 ליחידה; נשארו 800 -> 80 שניות
    assert view["eta_s"] == 80
    assert view["elapsed_s"] == 20


def test_a_window_is_not_the_average_since_the_start():
    """40 שניות ראשונות מהירות, ואז האטה: הקצב הוא של החלון (30 ש'), לא הממוצע."""
    fast = [(100.0 + i, i * 1000, i * 10) for i in range(0, 41, 10)]          # עד t=140
    slow = [(150.0, 40000 + 100, 410), (160.0, 40000 + 200, 420), (170.0, 40000 + 300, 430)]
    feed("t", fast + slow, one_part)
    view = cd.view(row("t", targets(one_part(), blocks=(1, 430, 1000))), now=170.0)
    assert view["rate_bps"] == 10, view          # 100 בייט ב-10ש' בחלון, לא ~240 מהממוצע


def test_a_silent_agent_has_no_rate():
    feed("t", [(100.0, 0, 0), (110.0, 5000, 100), (120.0, 10000, 200)], one_part)
    stale = cd.view(row("t", targets(one_part(), blocks=(1, 200, 1000))), now=120.0 + cd.STALE_S + 1)
    assert stale["rate_bps"] is None and stale["eta_s"] is None


def test_a_stalled_counter_is_not_reported_as_a_zero_rate():
    feed("t", [(100.0, 700, 100), (110.0, 700, 100), (120.0, 700, 100)], one_part)
    view = cd.view(row("t", targets(one_part(), blocks=(1, 100, 1000))), now=120.0)
    assert view["rate_bps"] is None and view["eta_s"] is None


def test_eta_needs_the_total_even_when_the_rate_is_known():
    parts = lambda: [part(1, "reading", 1000), part(2, "waiting", None)]   # noqa: E731
    feed("t", [(100.0, 0, 0), (110.0, 5000, 100), (120.0, 10000, 200)], parts)
    view = cd.view(row("t", targets(parts(), blocks=(1, 200, 1000))), now=120.0)
    assert view["rate_bps"] == 500 and view["eta_s"] is None


def test_a_finished_task_reports_no_live_rate():
    feed("t", [(100.0, 0, 0), (110.0, 5000, 100), (120.0, 10000, 200)], one_part)
    view = cd.view(row("t", targets(one_part(), blocks=(1, 200, 1000)), state="done"), now=120.0)
    assert view["rate_bps"] is None and view["eta_s"] is None


def test_the_sample_store_is_bounded():
    for i in range(cd.MAX_TASKS + 20):
        cd.record(f"t{i}", targets(one_part()), 1, now=float(i))
    assert len(cd._samples) <= cd.MAX_TASKS and len(cd._first) <= cd.MAX_TASKS


# --- validation -----------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    None, [], "x", [1], [{"index": 0, "role": "esp", "fs": "vfat", "state": "done"}],
    [{"index": 1, "role": "esp", "fs": "vfat", "state": "flying"}],
    [{"index": 1, "role": "esp<", "fs": "vfat", "state": "done"}],
    [{"index": 1, "role": "esp", "fs": "vfat", "state": "done", "used_bytes": -1}],
    [{"index": 1, "role": "esp", "fs": "vfat", "state": "done", "used_bytes": 1.5}],
    [{"index": 1, "role": "esp", "fs": "vfat", "state": "done"}, {"index": 1, "role": "esp", "fs": "vfat", "state": "done"}],
    [{"index": 1, "role": "esp", "fs": "vfat", "state": "done", "used_bytes": 1}, "junk"],
])
def test_a_malformed_table_is_dropped_whole(bad):
    assert cd.clean_partitions(bad) is None


def test_sanitize_removes_a_bad_table_and_a_bad_compression_but_keeps_the_target():
    tg = targets([part(1, "done", 1)], compression="rm -rf")
    tg[0]["partitions"][0]["state"] = "weird"
    cd.sanitize(tg)
    assert "partitions" not in tg[0] and "compression" not in tg[0] and tg[0]["dev"] == "sda"


# --- through the API ---------------------------------------------------------------

pytest.importorskip("fastapi")
from server.core.tasks import TOKEN_HEADER  # noqa: E402
from test_capture import make_task, setup_build_machine, task_token  # noqa: E402


def _post(server, task_id, mac, tgt):
    report = {"task_id": task_id, "mac": mac, "state": "capturing", "targets": [tgt]}
    return server["anon"].post("/api/v1/agent/progress", json=report,
                               headers={TOKEN_HEADER: task_token(server, task_id)})


def _detail(server):
    return server["admin"].get("/api/console/tasks").json()[0]["capture_detail"]


def _running(server):
    mac = setup_build_machine(server)
    return mac, make_task(server, mac).json()["id"]


def test_the_partition_table_reaches_the_console(server):
    mac, tid = _running(server)
    tgt = {"dev": "sda", "bytes_written": 40 * GB, "bytes_total": 0, "state": "capturing",
           "partitions": [part(1, "done", 90 * 1024 ** 2, "esp", "vfat"), part(3, "reading", 100 * GB)],
           "source_progress": {"partition": 3, "blocks_read": 30, "blocks_total": 100},
           "compression": "zstd-3"}
    assert _post(server, tid, mac, tgt).json()["ok"]
    d = _detail(server)
    assert d["compression"] == "zstd-3"
    assert [(p["index"], p["role"], p["fs"], p["state"]) for p in d["partitions"]] == [
        (1, "esp", "vfat", "done"), (3, "windows", "ntfs", "reading")]
    assert d["partitions"][1]["done_bytes"] == 30 * GB
    assert d["rate_bps"] is None and d["eta_s"] is None, "קצב מדיווח בודד"


def test_an_old_agent_without_a_table_reports_null_not_an_empty_table(server):
    mac, tid = _running(server)
    assert _post(server, tid, mac, {"dev": "sda", "bytes_written": 5, "bytes_total": 0, "state": "capturing"}).json()["ok"]
    assert _detail(server) is None


def test_a_malformed_table_never_reaches_the_console_or_the_database(server):
    mac, tid = _running(server)
    bad = [part(1, "done", 5)]
    bad[0]["state"] = "flying"
    assert _post(server, tid, mac, {"dev": "sda", "bytes_written": 5, "bytes_total": 0, "state": "capturing",
                                    "partitions": bad}).json()["ok"], "דיווח עם טבלה פגומה אסור שיידחה"
    assert _detail(server) is None
    stored = server["ctx"].conn.execute("SELECT targets_json FROM tasks WHERE id = ?", (tid,)).fetchone()[0]
    assert "flying" not in stored


def test_rate_appears_after_a_measured_window_through_the_api(server, monkeypatch):
    mac, tid = _running(server)
    clock = iter([1000.0, 1010.0, 1020.0])
    monkeypatch.setattr(cd, "time", types.SimpleNamespace(time=lambda: next(clock, 1020.0)))   # לא time.time הגלובלי
    for i in range(3):
        tgt = {"dev": "sda", "bytes_written": i * 10_000, "bytes_total": 0, "state": "capturing",
               "partitions": [part(1, "reading", 1000)],
               "source_progress": {"partition": 1, "blocks_read": i * 100, "blocks_total": 1000}}
        assert _post(server, tid, mac, tgt).json()["ok"]
    monkeypatch.setattr(cd, "time", types.SimpleNamespace(time=lambda: 1020.0))
    d = _detail(server)
    assert d["rate_bps"] == 1000 and d["eta_s"] == 80 and d["elapsed_s"] == 20, d
