"""#3331 (I14, #3064): `(boot_id, seq)` בדיווחי ההתקדמות -- דיווח ישן לא מחזיר מצב ו-ledger אחורה.

דיווח שהתעכב ברשת (או נשלח שוב אחרי אתחול) היה מגיע אחרי דיווח חדש ממנו, מחזיר את המצב והיומן אחורה, ומרענן את
`session_members.updated_at` כאילו המשכפל חי עכשיו (K1 קורא אותו כראיית חיים). הטסטים כאן:

- **הסוכן** (`agent/lib/core/reportseq.sh`): כל דיווח התקדמות נושא `boot_id` ו-`seq`; ה-`seq` עולה גם כשהספרייה
  של הסוכן נמחקת או שהוא מופעל מחדש באותו אתחול; ובלי מזהה אתחול בצורה של הקרנל או בלי uptime -- בלי השדות,
  לא שדות מומצאים.
- **השרת** (`server/agent/reportorder.py`): ישן נזרק, שווה מתקבל, אתחול חדש מתקבל והקודם יוצא משימוש, בלי שדות
  (סוכן ישן) מתקבל כמו היום, ודיווח שנדחה אינו מזיז את הבסיס.
- **מקצה לקצה** (`POST /api/v1/agent/progress`): דיווח ישן לא משנה את `session_members` ולא מרענן את `updated_at`.

**בקרה שלילית:** הסרת הבדיקה מ-`agent_progress` (או החזרת `check` ל-`ORDERED` תמיד) מפילה את הטסטים של השרת
והמקצה-לקצה; הסרת הקריאה ל-`report_seq_json` מ-`machine_extra_json` מפילה את טסטי הסוכן.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from agentlib import agent_sources, flat_lib
from native import requires_native
from server.agent import reportorder
from server.agent.reportorder import INVALID, ORDERED, STALE, UNORDERED, ReportOrder
from test_agent import BASH, posix, sh

MAC = "b4:2e:99:07:1a:c4"
BOOT_A = "5b1f6f0e-3c0a-4b52-9d4e-0d6a2f1c7e88"
BOOT_B = "0d6a2f1c-7e88-4b52-9d4e-5b1f6f0e3c0a"
LIB = flat_lib()


# --- הסוכן ------------------------------------------------------------------------------------------------------


def _root(tmp_path: Path, *, boot: str | None = BOOT_A, uptime: str | None = "100.00 50.00") -> Path:
    root = tmp_path / "sys"
    (root / "proc" / "sys" / "kernel" / "random").mkdir(parents=True, exist_ok=True)
    boot_file, up_file = root / "proc" / "sys" / "kernel" / "random" / "boot_id", root / "proc" / "uptime"
    for path, text in ((boot_file, boot), (up_file, uptime)):
        path.unlink(missing_ok=True)
        if text is not None:
            path.write_text(text + "\n", encoding="ascii", newline="\n")
    return root


def _report(tmp_path: Path, root: Path, task: str = "") -> dict:
    run = tmp_path / "run"
    (run / "targets").mkdir(parents=True, exist_ok=True)
    (run / "state").write_text("writing\n", encoding="ascii", newline="\n")
    return json.loads(sh(f'export RUN_DIR={posix(run)!r} SYSROOT={posix(root)!r}; . {posix(LIB)}/common.sh;'
                         f' {agent_sources("sysinfo", "diskinfo", "progress", "failmark")}'
                         f'. {posix(LIB)}/datareport.sh; . {posix(LIB)}/reportseq.sh;'
                         f' build_progress ses_1 {MAC} {task}'))


@requires_native(("bash", BASH))
def test_every_progress_report_carries_the_boot_and_how_far_into_it(tmp_path):
    got = _report(tmp_path, _root(tmp_path))
    assert got["boot_id"] == BOOT_A and got["seq"] == 10000        # 100.00 s of uptime, in hundredths


@requires_native(("bash", BASH))
def test_a_capture_report_carries_it_too(tmp_path):
    got = _report(tmp_path, _root(tmp_path), task="tsk_1")
    assert got["boot_id"] == BOOT_A and got["seq"] == 10000 and got["task_id"] == "tsk_1"


@requires_native(("bash", BASH))
def test_seq_rises_with_every_report_and_survives_a_cleared_run_dir(tmp_path):
    first = _report(tmp_path, _root(tmp_path, uptime="100.00 0.00"))["seq"]
    second = _report(tmp_path, _root(tmp_path, uptime="102.01 0.00"))["seq"]
    assert (first, second) == (10000, 10201)
    run = tmp_path / "run"
    for leftover in run.rglob("*"):                                 # the agent restarted inside this boot: $RUN_DIR starts empty
        if leftover.is_file():
            leftover.unlink()
    assert _report(tmp_path, _root(tmp_path, uptime="130.00 0.00"))["seq"] == 13000     # not 1: that would look OLD to the server


@requires_native(("bash", BASH))
def test_no_leading_zero_it_would_not_be_json(tmp_path):
    assert _report(tmp_path, _root(tmp_path, uptime="0.07 0.00"))["seq"] == 7


@requires_native(("bash", BASH))
@pytest.mark.parametrize("boot, uptime", [
    (None, "100.00 0.00"), ("not-a-boot-id", "100.00 0.00"), (BOOT_A.upper(), "100.00 0.00"),
    (BOOT_A, None), (BOOT_A, "garbage"), (BOOT_A, "100 0.00"), (BOOT_A, ".5 0")])
def test_nothing_readable_means_no_fields_never_invented_ones(tmp_path, boot, uptime):
    got = _report(tmp_path, _root(tmp_path, boot=boot, uptime=uptime))
    assert "boot_id" not in got and "seq" not in got and got["session_id"] == "ses_1"      # and the report is still whole


# --- השרת: ReportOrder ------------------------------------------------------------------------------------------


def body(boot: str | None = BOOT_A, seq: int | None = 10, **more) -> dict:
    return {"mac": MAC, **({} if boot is None else {"boot_id": boot}), **({} if seq is None else {"seq": seq}), **more}


def taken(order: ReportOrder, **kw) -> str:
    """בדיקה, ואם עברה: הדיווח התקבל (כמו `agent_progress`)."""
    verdict = order.check(MAC, body(**kw))
    if verdict != STALE:
        order.note(MAC, body(**kw))
    return verdict


def test_an_older_seq_of_the_same_boot_is_stale_and_a_newer_one_is_not():
    order = ReportOrder()
    assert taken(order, seq=10) == ORDERED and taken(order, seq=11) == ORDERED
    assert taken(order, seq=10) == STALE and taken(order, seq=1) == STALE
    assert taken(order, seq=12) == ORDERED
    assert order.dropped == {MAC: 2}


def test_the_same_seq_again_is_a_resend_and_is_taken():
    """תשובה שאבדה: הסוכן שולח שוב אותו דיווח. אינו מחזיר דבר אחורה, והיומן הוא איחוד."""
    order = ReportOrder()
    assert taken(order, seq=10) == ORDERED and taken(order, seq=10) == ORDERED


def test_a_new_boot_is_taken_and_the_one_before_it_is_stale_whatever_its_seq():
    order = ReportOrder()
    assert taken(order, boot=BOOT_A, seq=90_000) == ORDERED
    assert taken(order, boot=BOOT_B, seq=5) == ORDERED                      # the machine rebooted: its seq starts again
    assert taken(order, boot=BOOT_A, seq=99_999) == STALE                   # a late report of the boot that ended
    assert taken(order, boot=BOOT_B, seq=6) == ORDERED and taken(order, boot=BOOT_B, seq=5) == STALE


def test_only_the_last_few_boots_are_remembered_and_the_memory_is_bounded():
    order = ReportOrder()
    boots = [f"{n:08x}-0000-4000-8000-000000000000" for n in range(reportorder.RETIRED + 3)]
    for boot in boots:
        assert taken(order, boot=boot, seq=1) == ORDERED
    assert taken(order, boot=boots[-2], seq=9) == STALE                     # recent: remembered
    assert len(order._by_mac[MAC].retired) == reportorder.RETIRED


def test_a_report_without_the_fields_is_taken_as_before_and_said_once(caplog):
    """סוכן שלפני #3331: אין מה לסדר, ואין סיבה לזרוק."""
    order = ReportOrder()
    caplog.set_level(logging.INFO, logger="imagesrv.reportorder")
    assert taken(order, boot=None, seq=None) == UNORDERED and taken(order, boot=None, seq=None) == UNORDERED
    assert [r.getMessage() for r in caplog.records].count(
        f"progress report of {MAC} has no (boot_id, seq): an agent from before #3331, accepted as before") == 1


@pytest.mark.parametrize("kw", [dict(boot=BOOT_A, seq=None), dict(boot=None, seq=7), dict(boot="nope", seq=7),
                                dict(boot=BOOT_A.upper(), seq=7), dict(boot=BOOT_A, seq=0), dict(boot=BOOT_A, seq=-3),
                                dict(boot=BOOT_A, seq=True), dict(boot=BOOT_A, seq="7"), dict(boot=BOOT_A, seq=7.5),
                                dict(boot=BOOT_A, seq=reportorder.MAX_SEQ + 1)])
def test_fields_that_cannot_be_read_are_unordered_and_never_move_the_base(kw):
    """דיווח שאי אפשר לסדר אינו "חדש" (עיקרון 5): מתקבל, ואינו משנה מה שהשרת זוכר."""
    order = ReportOrder()
    assert taken(order, seq=100) == ORDERED
    assert taken(order, **kw) == INVALID
    assert taken(order, seq=99) == STALE and taken(order, seq=101) == ORDERED


def test_a_report_that_was_refused_does_not_move_the_base():
    """בדיקה בלי `note`: מי ששולח `seq` ענק בדיווח שנדחה לא גורם לדיווחים אמיתיים להיזרק."""
    order = ReportOrder()
    assert taken(order, seq=100) == ORDERED
    assert order.check(MAC, body(seq=10**9)) == ORDERED                     # refused later (bad token, closed session): never noted
    assert taken(order, seq=101) == ORDERED


def test_machines_do_not_share_an_order():
    order = ReportOrder()
    assert taken(order, seq=50) == ORDERED
    other = "b4:2e:99:07:1a:c5"
    assert order.check(other, body(seq=1) | {"mac": other}) == ORDERED


# --- מקצה לקצה: POST /api/v1/agent/progress ---------------------------------------------------------------------

pytest.importorskip("fastapi")

from test_closed_session_report import _session           # noqa: E402

SENTINEL = "2026-09-08T00:00:00+00:00"


def _post(server, sid: str, mac: str, written: int, **order) -> tuple[int, dict]:
    payload = {"session_id": sid, "mac": mac, "state": "writing",
               "targets": [{"dev": "sda", "bytes_written": written, "bytes_total": 1000, "state": "writing"}], **order}
    response = server["anon"].post("/api/v1/agent/progress", json=payload)
    return response.status_code, response.json()


def _taken(reply: dict) -> bool:
    """הדיווח הוחל: ``ok`` ובלי ``stale`` (בשאר השדות התשובה משתנה לפי הסבב)."""
    return reply.get("ok") is True and "stale" not in reply


def _member(server, sid: str, mac: str):
    return server["ctx"].conn.execute("SELECT bytes_written, updated_at FROM session_members"
                                      " WHERE session_id = ? AND mac = ?", (sid, mac)).fetchone()


def test_a_stale_report_moves_nothing_and_does_not_refresh_the_clock(server):
    mac = _session(server, "ses_ord")
    code, reply = _post(server, "ses_ord", mac, 500, boot_id=BOOT_A, seq=200)
    assert code == 200 and _taken(reply)
    fresh = _member(server, "ses_ord", mac)
    assert fresh["bytes_written"] == 500 and fresh["updated_at"] != SENTINEL
    server["ctx"].conn.execute("UPDATE session_members SET updated_at = ? WHERE session_id = 'ses_ord'", (SENTINEL,))
    server["ctx"].conn.commit()

    code, reply = _post(server, "ses_ord", mac, 100, boot_id=BOOT_A, seq=150)       # held up on the network
    assert (code, reply) == (200, {"ok": True, "stale": True})                      # taken off the agent's hands, not applied
    held_up = _member(server, "ses_ord", mac)
    assert held_up["bytes_written"] == 500 and held_up["updated_at"] == SENTINEL    # not moved back, not "alive now"

    assert _taken(_post(server, "ses_ord", mac, 600, boot_id=BOOT_A, seq=201)[1])
    assert _member(server, "ses_ord", mac)["bytes_written"] == 600


def test_a_late_report_of_the_boot_before_is_stale_too(server):
    mac = _session(server, "ses_boot")
    assert _taken(_post(server, "ses_boot", mac, 100, boot_id=BOOT_A, seq=90_000)[1])
    assert _taken(_post(server, "ses_boot", mac, 300, boot_id=BOOT_B, seq=40)[1])
    assert _post(server, "ses_boot", mac, 100, boot_id=BOOT_A, seq=99_999)[1] == {"ok": True, "stale": True}
    assert _member(server, "ses_boot", mac)["bytes_written"] == 300


def test_an_agent_from_before_the_fields_is_taken_as_it_was(server):
    mac = _session(server, "ses_old")
    assert _taken(_post(server, "ses_old", mac, 10)[1])
    assert _taken(_post(server, "ses_old", mac, 20, boot_id=BOOT_A, seq=5)[1])
    assert _taken(_post(server, "ses_old", mac, 5)[1])                      # no seq: nothing to compare, as before
    assert _member(server, "ses_old", mac)["bytes_written"] == 5


def test_a_refused_report_cannot_poison_the_order(server):
    mac = _session(server, "ses_poison")
    code, reply = _post(server, "ses_nobody", mac, 1, boot_id=BOOT_A, seq=10**9)    # a session that does not exist
    assert reply["ok"] is False
    assert _taken(_post(server, "ses_poison", mac, 30, boot_id=BOOT_A, seq=7)[1])
    assert _member(server, "ses_poison", mac)["bytes_written"] == 30
