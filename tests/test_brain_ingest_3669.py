"""#3669 (B6, B7, B8; אפיק #3653): קליטה והפצה ישירה -- רמת הדחיסה, מקום בשרת לקליטה, והמתנה לשרת ששותק, בצל.

הקוד הקיים מחליט וההתנהגות זהה להיום; המוח רושם שורה ב-`brain_decisions` (`mode=shadow`) עם מה שהיה מחליט ו-`agree=`.

1. **שקילות B7.** `ingest.space` מול העתק קפוא של הבלוק שהיה ב-`library/capture.py` (`_preflight_body`) לפני #3669, על רשת של קלטים: אותו
   סטטוס ואותה סיבה, מילה במילה. וביושם: `POST /preflight` מחזיר מה שהחזיר, ושורת המוח אומרת את אותו דבר, ב-`agree=1`.
2. **רישום B6.** קבלת מניפסט בקליטה ובהפצה ישירה רושמת את הרמה של המוח מול מה שהסוכן דיווח (`agree=1|0|unknown`), והקבלה עצמה
   לא משתנה (גם כשהרמה שונה).
3. **רישום B8.** פתיחת סבב ישיר רושמת שורה אחת עם 30 / 2 / 60.
4. **הסוכן מבצע אותם ערכים.** הקבועים של `capture.sh`, `directsend.sh` ו-`directchunk.sh` שווים לערכים של המוח: שינוי באחד בלי השני נופל כאן.
   "לא מצאנו את הקבוע" הוא כשל, לא מעבר (עיקרון 5).
5. **ברירת המחדל הבטוחה:** מה שלא נקרא אינו `ok`; מניפסט שלא דיווח רמה הוא `unknown`, לא `1`; `off` = לא נקרא ולא נרשם.

**בקרה שלילית:** ראו את גוף ה-PR.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.brain import memory, veto
from server.db import connect, set_setting
from server.storage import disk_reserve
from test_capture import make_task, manifest_for, setup_build_machine, wait_verified
from test_capture_preflight import ROOM, preflight
from test_capture_verify_1470 import _put_manifest as put_capture_manifest, _upload as upload_capture_files
from test_direct_server import _live_manifest, _put_manifest as put_direct_manifest, _setup as open_direct
from test_server_room import room_server  # noqa: F401 -- room_server הוא fixture

REPO = Path(__file__).resolve().parent.parent
LEVEL_RULE, SPACE_RULE, SILENCE_RULE = "B3669a", "B3669b", "B3669c"
#: #1737: ההגדרה של הרזרבה מאופסת בטסטים אחרים (`conftest`); כאן היא 10GiB, כמו ב-`test_capture_preflight`.
pytestmark = pytest.mark.system_reserve


def brain():
    """מודול המוח של הכללים. חסר = כשל בשמו, לא ImportError שמסתיר שהטסט לא רץ."""
    try:
        from server.brain import ingest
    except ImportError:
        pytest.fail("server/brain/ingest.py אינו קיים: אין כללי קליטה והפצה ישירה במוח")
    return ingest


def rows(conn, rule, **kw):
    return memory.decisions(conn, rule=rule, limit=50, **kw)


def evidence(row) -> dict[str, str]:
    return dict(item.split("=", 1) for item in row["evidence"])


# --- B7: מקום בשרת --------------------------------------------------------------------------------------------

def legacy_space(used, free, keep, missing):
    """הבלוק של `_preflight_body` כפי שהיה בדיוק לפני #3669 (העתק קפוא): האורקל של השקילות."""
    unchecked = []
    if missing:
        unchecked.append("נפח תפוס במחיצות " + ", ".join(missing))
    if free is None:
        unchecked.append("מקום פנוי באחסון השרת (loc_local)")
    if unchecked:
        return "unchecked", "לא נבדק: " + " · ".join(unchecked) + " — הקליטה נעצרה לפני הכיווץ"
    numbers = f"תפוס {used / 1e9:.1f} GB · פנוי בשרת {free / 1e9:.1f} GB"
    if keep:
        numbers += f" · נשמרים למערכת {disk_reserve.gb(keep)}"
    if used + keep > free:
        return "refused", "אין מקום בשרת לקליטה: " + numbers
    if used * 2 + keep > free:
        return "warning", "אזהרת מקום: " + numbers + " — יחס הדחיסה אינו ידוע; ניתן להמשיך"
    return "ok", numbers


KEEPS = (0, disk_reserve.SYSTEM_RESERVE_BYTES)
FREES = (None, 0, 1, 9_999_999_999, disk_reserve.SYSTEM_RESERVE_BYTES, 53_300_000_000)
USEDS = (0, 1, 5_000_000_000, 26_650_000_000, 26_650_000_001, 43_000_000_000, 53_300_000_000, 223_000_000_000)


@pytest.mark.parametrize("missing", [[], ["2"], ["1", "3"]], ids=["read", "one_missing", "two_missing"])
@pytest.mark.parametrize("keep", KEEPS)
def test_the_space_verdict_is_the_one_the_server_gave(keep, missing):
    ingest = brain()
    for free in FREES:
        for used in ((None,) if missing else USEDS):
            want = legacy_space(used, free, keep, missing)
            assert tuple(ingest.space(used=used, free=free, keep=keep, missing=missing)) == want, (used, free, keep, missing)


def test_what_was_not_read_is_never_ok():
    ingest = brain()
    assert ingest.space(used=None, free=100, keep=0, missing=["1"]).status == "unchecked"
    assert ingest.space(used=10, free=None, keep=0, missing=[]).status == "unchecked"
    assert ingest.space(used=None, free=100, keep=0, missing=[]).status == "unchecked", "used חסר בלי שם מחיצה: עדיין לא ok"
    assert ingest.SPACE_VERDICT["unchecked"] == "UNDECIDED" and ingest.SPACE_VERDICT["ok"] == "PROCEED"
    assert ingest.SPACE_VERDICT["warning"] == "WARN" and ingest.SPACE_VERDICT["refused"] == "BLOCK"


@pytest.mark.parametrize("used,status,verdict", [
    (ROOM + 1, "refused", "BLOCK"), (ROOM, "warning", "WARN"), (ROOM // 2 + 1, "warning", "WARN"),
    (ROOM // 2, "ok", "PROCEED"), (0, "ok", "PROCEED")])
def test_the_preflight_answer_is_unchanged_and_the_brain_row_says_the_same(server, monkeypatch, used, status, verdict):
    task, response = preflight(server, monkeypatch, [{"index": 2, "used_bytes": used}])
    assert response.status_code == 200 and response.json()["status"] == status
    [row] = rows(server["ctx"].conn, SPACE_RULE)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "CAPTURE_SPACE", "preflight", verdict), row
    assert row["reason"] == response.json()["reason"], "הסיבה היא של הקוד הקיים, מילה במילה"
    got = evidence(row)
    assert got["task"] == task and got["status"] == status and got["used"] == str(used) and got["agree"] == "1" and got["legacy"] == status
    assert got["free"] == "53300000000" and got["keep"] == str(disk_reserve.SYSTEM_RESERVE_BYTES)


def test_a_free_space_that_was_not_read_is_undecided_not_ok(server, monkeypatch):
    task, response = preflight(server, monkeypatch, [{"index": 1, "used_bytes": 1}], OSError("unavailable"))
    assert response.json()["status"] == "unchecked"
    [row] = rows(server["ctx"].conn, SPACE_RULE)
    assert row["verdict"] == "UNDECIDED" and "loc_local" in row["reason"]
    assert evidence(row)["free"] == "unread" and evidence(row)["agree"] == "1"


def test_a_partition_whose_used_bytes_were_not_read_is_named(server, monkeypatch):
    _, response = preflight(server, monkeypatch, [{"index": 2, "used_bytes": None}])
    assert response.json()["status"] == "unchecked"
    [row] = rows(server["ctx"].conn, SPACE_RULE)
    assert row["verdict"] == "UNDECIDED" and "מחיצות 2" in row["reason"] and evidence(row)["used"] == "unread"


# --- B6: רמת הדחיסה, בקליטה ובהפצה ישירה ---------------------------------------------------------------------

def capture_with(server, compression):
    mac = setup_build_machine(server)
    task = make_task(server, mac).json()["id"]
    head = upload_capture_files(server, task)
    manifest = manifest_for()
    if compression is None:
        manifest.pop("compression")
    else:
        manifest["compression"] = compression
    reply = put_capture_manifest(server, task, head, manifest)
    assert reply.status_code == 202, reply.text
    wait_verified(server, task)
    [row] = rows(server["ctx"].conn, LEVEL_RULE)
    return task, row


@pytest.mark.parametrize("compression,agree", [("zstd-3", "1"), ("zstd-9", "0"), (None, "unknown"), ("lz4", "unknown")])
def test_a_captured_manifest_is_compared_to_the_level_of_the_brain_and_accepted_as_before(server, compression, agree):
    task, row = capture_with(server, compression)
    assert (row["mode"], row["action"], row["point"], row["verdict"]) == ("shadow", "CAPTURE_COMPRESSION", "capture", "PROCEED"), row
    got = evidence(row)
    assert got["level"] == "3" and got["threads"] == "0" and got["task"] == task and got["agree"] == agree, got
    assert got["applied"] == (compression or "unreported")


def test_a_direct_manifest_is_compared_too_and_the_open_round_records_the_silence_ceiling(room_server):
    opened = open_direct(room_server)
    conn = room_server["ctx"].conn
    [silence] = rows(conn, SILENCE_RULE, kind="round", key=opened["id"])
    assert (silence["mode"], silence["action"], silence["point"], silence["verdict"]) == ("shadow", "DIRECT_SILENCE", "direct_open", "WAIT")
    assert evidence(silence) == {"task": opened["task_id"], "misses": "30", "poll_s": "2", "silence_s": "60", "evict_margin_s": "60"}
    assert silence["round_id"] == opened["id"]
    reply = put_direct_manifest(room_server, opened["task_id"])
    assert reply.status_code == 200, reply.text
    [level] = rows(conn, LEVEL_RULE)
    assert level["point"] == "direct" and evidence(level)["agree"] == "1" and evidence(level)["applied"] == _live_manifest()["compression"]


def test_a_direct_manifest_with_another_level_is_still_accepted_and_the_difference_is_named(room_server):
    opened = open_direct(room_server)
    manifest = _live_manifest()
    manifest["compression"] = "zstd-19"
    assert put_direct_manifest(room_server, opened["task_id"], manifest).status_code == 200
    [level] = rows(room_server["ctx"].conn, LEVEL_RULE)
    assert evidence(level)["agree"] == "0" and evidence(level)["applied"] == "zstd-19"


@pytest.mark.parametrize("raw,level", [("zstd-3", 3), ("zstd-19", 19), ("zstd", None), ("ZSTD-3", None), ("zstd-3 ", None),
                                       ("lz4", None), (3, None), (None, None)])
def test_only_zstd_n_is_a_level(raw, level):
    assert brain().applied_level({"compression": raw}) == level
    assert brain().applied_level({}) is None and brain().applied_level("zstd-3") is None


def test_the_off_switch_reads_nothing_and_records_nothing(tmp_path):
    ingest = brain()
    conn = connect(tmp_path / "off.db")
    set_setting(conn, veto.SETTING, "off")
    ingest.compression_shadow(conn, "tsk_off", {"compression": "zstd-3"}, "capture")
    assert ingest.space_shadow(conn, "tsk_off", ("ok", "x"), used=1, free=100, keep=0, missing=[]) is None
    ingest.silence_shadow(conn, "room_off", "tsk_off")
    assert memory.decisions(conn, limit=50) == []


# --- הסוכן מבצע בדיוק את הערכים של המוח ---------------------------------------------------------------------

def constant(path: str, pattern: str) -> int:
    """הקבוע בקובץ של הסוכן. לא נמצא, או נמצא יותר מפעם אחת = כשל: 'לא מצאנו' אינו 'שווה' (עיקרון 5)."""
    found = re.findall(pattern, (REPO / path).read_text(encoding="utf-8"))
    assert len(found) == 1, f"{path}: {pattern!r} נמצא {len(found)} פעמים"
    return int(found[0])


def test_the_agent_applies_the_level_and_the_threads_of_the_brain():
    ingest = brain()
    assert constant("agent/lib/capture/capture.sh", r'CAPTURE_LEVEL="\$\{CAPTURE_LEVEL:-(\d+)\}"') == ingest.LEVEL == 3
    assert constant("agent/lib/capture/capture.sh", r'CAPTURE_THREADS="\$\{CAPTURE_THREADS:-(\d+)\}"') == ingest.THREADS == 0
    assert ingest.compression() == (3, 0)


def test_the_agent_gives_up_on_a_silent_server_after_the_misses_and_the_poll_of_the_brain():
    ingest = brain()
    assert constant("agent/lib/capture/directsend.sh", r'\[ "\$_dw_miss" -ge (\d+) \]') == ingest.MISS_LIMIT == 30
    assert constant("agent/lib/capture/directchunk.sh", r'\[ "\$_cr_miss" -lt (\d+) \]') == ingest.MISS_LIMIT
    assert re.findall(r'DIRECT_POLL_S:-(\d+)', (REPO / "agent/lib/capture/directsend.sh").read_text(encoding="utf-8")) == [str(ingest.POLL_S)]
    assert re.findall(r'DIRECT_POLL_S:-(\d+)', (REPO / "agent/lib/capture/directchunk.sh").read_text(encoding="utf-8")) == [str(ingest.POLL_S)]
    assert ingest.POLL_S == 2 and ingest.silence_s() == 60


def test_the_agent_evicts_after_the_start_and_stall_ceilings_plus_the_margin_of_the_brain():
    ingest = brain()
    # #3173: directsend.sh's own eviction (the old sender's pass 2) left with it; the chunk pass's is the one below
    assert constant("agent/lib/capture/directchunk.sh", r'\(WAIT_STREAM_START_S \+ WAIT_STREAM_STALL_S \+ (\d+)\) \* 1000') == ingest.EVICT_MARGIN_S
    assert ingest.evict_ms(600, 300) == 960_000


# --- הקטלוג ---------------------------------------------------------------------------------------------------

def test_the_catalog_has_the_three_points_in_shadow_and_pointing_at_the_code():
    with (REPO / "server" / "brain" / "catalog.toml").open("rb") as f:
        items = {it["id"]: it for it in tomllib.load(f)["item"]}
    want = {LEVEL_RULE: "compression", SPACE_RULE: "space", SILENCE_RULE: "silence_s"}
    for rule, symbol in want.items():
        item = items[rule]
        assert (item["mode"], item["layer"], item["file"], item["symbol"]) == ("shadow", "decisions", "server/brain/ingest.py", symbol), item
    ingest = brain()
    assert (ingest.RULE_LEVEL, ingest.RULE_SPACE, ingest.RULE_SILENCE) == (LEVEL_RULE, SPACE_RULE, SILENCE_RULE)
