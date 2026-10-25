"""‏#1955 — הפצה ישירה נקשרת ל-serial של הדיסק שנבחר, לא לשם ההתקן.

עד כאן `direct_send` שמר רק `disk` (‏`sda`), ו-`directsend.sh` קרא ל-`capture_disk`
בלי הארגומנט השלישי — כלומר שער הזהות של #1901 (`capture_identity_reason`)
**דולג** בהפצה ישירה. אם סדר הדיסקים במחשב הבנייה התחלף בין ההזמנה לביצוע,
המשכפלים קיבלו דיסק אחר, וה-sha256 עבר כי הוא מחושב על מה שנקרא.

מה שנבדק:
1. השרת שומר `tasks.disk_serial` מה-hello (אותו `_reported_serial` של הקליטה),
   ו-hello מוסר אותו למחשב הבנייה.
2. הסוכן מסרב **לפני הדאטגרם הראשון** כש-`/dev/sda` נושא serial אחר: ‏udp-sender
   לא רץ, והסיבה מגיעה לקונסולה בשמה.
3. אותו serial — השידור יוצא; בלי serial ובמחשב שני דיסקים — "לא ניתן לוודא".
"""

from __future__ import annotations

from test_agent import BASH
from native import requires_native
from test_directsend import _answer, _progress, direct_run
from test_direct_server import _build_hello, _open_direct, _register_build
from test_server_room import CLONER1, cloner_hello, room_server  # noqa: F401

#: מחליפים את מה שמסביב ל-`direct_send_task` — ה-UI, דיווח הסיום וההחזקה —
#: כדי להריץ את הפונקציה האמיתית שקוראת את המשימה מ-$RESP ומעבירה אותה הלאה.
TASK_SURROUND = (
    "ui_clear() { :; }; ui_header() { :; }; progress_loop() { :; }; "
    "report_final() { :; }; ui_error_hold() { :; }; build_end_ask() { :; }; "
    "shrink_restore_source() { :; }; "
)


def _serial(serial: str, extra: tuple[str, ...] = ()) -> str:
    pre = ('mkdir -p "$SYSROOT/sys/block/sda/device"; '
           f'printf "%s\\n" {serial!r} > "$SYSROOT/sys/block/sda/device/serial"; ')
    for name in extra:
        pre += f'mkdir -p "$SYSROOT/sys/block/{name}"; '
    return pre


def _task_answer(serial):
    answer = _answer()
    answer["task"]["disk_serial"] = serial
    return answer


def _run_task(tmp_path, ordered, on_disk, extra=()):
    """`direct_send_task` האמיתי: המשימה ב-$RESP, ה-serial בסיספס."""
    answer = _task_answer(ordered)
    return direct_run(
        tmp_path, answer=answer,
        pre=TASK_SURROUND + _serial(on_disk, extra) + 'send_hello; ',
        call="direct_send_task")


@requires_native(("bash", BASH))
def test_another_serial_on_the_named_device_is_refused_before_the_first_datagram(tmp_path):
    box, run, out = _run_task(tmp_path, "SERIAL-A", "SERIAL-B")
    assert not (box / "udp.argv").exists(), "udp-sender רץ — הדיסק הלא נכון שודר"
    report = _progress(box)
    reason = report["targets"][0]["error"]
    assert "אינו הדיסק שנבחר" in reason, reason
    assert "SERIAL-A" in reason and "SERIAL-B" in reason, reason


@requires_native(("bash", BASH))
def test_the_serial_it_was_ordered_for_is_sent(tmp_path):
    box, run, out = _run_task(tmp_path, "SERIAL-A", "SERIAL-A")
    assert (box / "udp.argv").exists(), out + (run / "agent.log").read_text("utf-8", "replace")
    assert _progress(box)["targets"][0]["state"] != "failed"


@requires_native(("bash", BASH))
def test_no_serial_at_ordering_with_two_disks_is_not_a_guess(tmp_path):
    box, run, out = _run_task(tmp_path, None, "SERIAL-A", extra=("sdb",))
    assert not (box / "udp.argv").exists()
    reason = _progress(box)["targets"][0]["error"]
    assert "לא ניתן לוודא" in reason and "2 דיסקים" in reason, reason


def test_the_direct_task_stores_the_serial_the_console_saw_and_hello_hands_it_over(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    _register_build(room_server)
    _build_hello(anon, disks=("sda", "sdb"))
    cloner_hello(anon, CLONER1, ["S1", "S2"])
    r = _open_direct(admin, [{"mac": CLONER1, "ports": [1, 2]}], disk="sdb")
    assert r.status_code == 200, r.json()
    task_id = r.json()["task_id"]
    # ‏SELECT * ו-.get: בבקרה השלילית הטסט נכשל על הערך, לא על SQL.
    row = dict(room_server["ctx"].conn.execute(
        "SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone())
    assert (row["disk"], row.get("disk_serial")) == ("sdb", "B-sdb")
    task = _build_hello(anon, disks=("sda", "sdb"))["task"]
    assert task["id"] == task_id
    assert (task["disk"], task.get("disk_serial")) == ("sdb", "B-sdb")
