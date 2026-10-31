"""#1432: direct-send receiver counts follow the live wave membership."""

from test_direct_server import (BUILD, _build_hello, _open_direct,
                                _put_manifest, _register_build)
from test_server_room import CLONER1, CLONER2, cloner_hello, report, room_server  # noqa: F401


CLONER3 = "aa:bb:cc:00:00:23"
CLONERS = (CLONER1, CLONER2, CLONER3)


def _running_three(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post("/api/console/machines", json={
        "mac": CLONER3, "name": "shich-3", "group_id": "grp_CLONERS",
    }).status_code == 200
    _register_build(room_server)
    for number, mac in enumerate(CLONERS, 1):
        cloner_hello(anon, mac, [f"S{number}"])
    opened = _open_direct(admin, [
        {"mac": mac, "ports": [1]} for mac in CLONERS
    ]).json()
    for number, mac in enumerate(CLONERS, 1):
        cloner_hello(anon, mac, [f"S{number}"])
    assert _put_manifest(room_server, opened["task_id"]).status_code == 200
    assert admin.post("/api/console/room/start").status_code == 200
    first = _build_hello(anon)["task"]["direct"]
    assert first["session_state"] == "running"
    assert first["multicast"]["min_receivers"] == 3
    return opened


def _fail(room_server, opened, mac):
    report(room_server["anon"], opened["wave_session_id"], mac,
           {"sda": "failed"}, top="failed")


def test_partition_two_waits_only_for_the_two_targets_still_alive(room_server):
    opened = _running_three(room_server)
    _fail(room_server, opened, CLONER3)

    second = _build_hello(room_server["anon"])["task"]["direct"]
    assert second["receivers"] == 2
    assert second["multicast"]["min_receivers"] == 2


def test_direct_send_with_no_live_targets_fails_with_a_reason(room_server):
    opened = _running_three(room_server)
    for mac in CLONERS:
        _fail(room_server, opened, mac)

    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "failed"
    conn = room_server["ctx"].conn
    session = conn.execute(
        "SELECT state, failed_reason FROM sessions WHERE id = ?",
        (opened["wave_session_id"],),
    ).fetchone()
    round_row = conn.execute(
        "SELECT state, failed_reason FROM room_rounds WHERE id = ?", (opened["id"],)
    ).fetchone()
    task = conn.execute(
        "SELECT state, error FROM tasks WHERE id = ?", (opened["task_id"],)
    ).fetchone()
    assert session["state"] == round_row["state"] == task["state"] == "failed"
    assert "לא נשארו מחשבי יעד חיים" in session["failed_reason"]
    assert session["failed_reason"] == round_row["failed_reason"] == task["error"]


def test_direct_send_without_failures_keeps_all_three_receivers(room_server):
    _running_three(room_server)
    next_partition = _build_hello(room_server["anon"])["task"]["direct"]
    assert next_partition["receivers"] == 3
    assert next_partition["multicast"]["min_receivers"] == 3


def _source_done(room_server, opened):
    """הדיווח הסופי של המקור — מה ש-`report_final` של directsend.sh שולח."""
    from test_direct_server import _token
    from server.core.tasks import TOKEN_HEADER
    return room_server["anon"].post("/api/v1/agent/progress", json={
        "task_id": opened["task_id"], "mac": BUILD, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": 4096, "bytes_total": 4096,
                     "state": "done"}]},
        headers={TOKEN_HEADER: _token(room_server, opened["task_id"])})


def _states(room_server, opened):
    conn = room_server["ctx"].conn
    return (
        conn.execute("SELECT state FROM sessions WHERE id = ?",
                     (opened["wave_session_id"],)).fetchone()["state"],
        conn.execute("SELECT state FROM tasks WHERE id = ?",
                     (opened["task_id"],)).fetchone()["state"],
        conn.execute("SELECT COUNT(*) AS n FROM journal WHERE event = 'direct_failed'"
                     ).fetchone()["n"],
    )


def test_hello_after_every_target_finished_does_not_fail_a_written_wave(room_server):
    """#1613: ‏`live_count == 0` כי **כולם סיימו** אינו "לא נשאר מי שיקבל".

    בין הדיווח `done` של המקבלים לבין הדופק שסוגר את הגל, hello של המקור
    (‏hold_beat אחרי `report_final` שלא נשמע, או סתם תזמון) ראה גל `running`
    בלי חברים חיים — והשרת הכריז כישלון על גל שכל המגירות שלו נכתבו."""
    opened = _running_three(room_server)
    for mac in CLONERS:
        report(room_server["anon"], opened["wave_session_id"], mac, {"sda": "done"})

    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "running"
    assert _states(room_server, opened) == ("running", "running", 0)

    assert _source_done(room_server, opened).status_code == 200
    assert _states(room_server, opened)[1] == "done"


def test_one_target_written_and_the_rest_failed_is_not_a_failed_direct_send(room_server):
    """מכונה אחת שסיימה (‏`done`/`partial`) קיבלה את הזרם כולו — המקור
    כבר אחרי המחיצה האחרונה, והכרעת הסבב שייכת לספירת המגירות."""
    opened = _running_three(room_server)
    report(room_server["anon"], opened["wave_session_id"], CLONER1, {"sda": "done"})
    _fail(room_server, opened, CLONER2)
    _fail(room_server, opened, CLONER3)

    direct = _build_hello(room_server["anon"])["task"]["direct"]
    assert direct["session_state"] == "running"
    assert _states(room_server, opened) == ("running", "running", 0)
