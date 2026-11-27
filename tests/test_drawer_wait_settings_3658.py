"""#3658, #3675 (Nadav 18/11) -- "גם דיסק וגם רשת אחרי 2 דקות מוותר", ו"משהו שאני יכול לשנות בהגדרות".

A drawer that waits for its DISK and one that waits for the NETWORK both give up after 120 s (the
agent used to say 150 and 300), and the two numbers are console settings: ``drawer_disk_wait_seconds``
and ``drawer_net_wait_seconds`` (30-900), carried to the agent in the hello answer like the other
agent timings and echoed back in ``applied_timing``.

Server side: range, default, hello, validation, the "applied on N of M" count.
Agent side (``timing.sh`` + ``stall.sh``): what the agent holds when it was told nothing, when it
was told 200/90, and what the screens are told is the limit (``stall_limit_s``).
"""

from __future__ import annotations

import re

import pytest

from console_src import console_js
from conftest import hello_body
from native import requires_native
from test_agent import BASH, fake_machine, posix, sh  # noqa: F401
from agentlib import agent_sources
from test_agent_timing_2056 import (   # noqa: F401 -- room_server is a fixture
    CLONER1, CLONER2, URL, _applied, _hello_applied, disk, drawers_hello, room_server)

DISK, NET = "drawer_disk_wait_seconds", "drawer_net_wait_seconds"


# --- the server ---------------------------------------------------------------------------


@pytest.mark.parametrize("key", [DISK, NET])
@pytest.mark.parametrize("raw, want", [
    ("30", 30), (900, 900), ("120", 120), (450, 450),
    ("29", None), (901, None), ("0", None), ("0120", None), (True, None), (120.5, None),
    ("", None), (None, None), ("abc", None), ("-120", None), ("123456", None),
])
def test_parse_takes_a_whole_number_between_30_and_900_only(key, raw, want):
    from server.agent import timing as agent_timing
    assert agent_timing.parse(key, raw) == want


def test_both_default_to_two_minutes_and_are_served_when_nothing_is_set(room_server):
    admin = room_server["admin"]
    got = admin.get(URL).json()
    assert (got[DISK], got[NET]) == ("120", "120")           # in force, not "unset"
    answer = drawers_hello(room_server["anon"], CLONER1, [disk("S1", 1)])
    assert (answer[DISK], answer[NET]) == (120, 120)


def test_a_saved_value_reaches_hello_for_a_cloner_and_a_build_machine(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post(URL, json={DISK: 90, NET: "200"}).status_code == 200
    got = drawers_hello(anon, CLONER2, [disk("S3", 1)])
    assert (got[DISK], got[NET]) == (90, 200)
    assert admin.post("/api/console/machines", json={
        "mac": "aa:bb:cc:00:00:99", "name": "build-1", "group_id": "grp_BUILD"}).status_code == 200
    built = anon.post("/api/v1/agent/hello", json=hello_body("aa:bb:cc:00:00:99")).json()
    assert built["role"] == "build" and (built[DISK], built[NET]) == (90, 200)
    stranger = anon.post("/api/v1/agent/hello", json=hello_body("de:ad:be:ef:00:01")).json()
    assert stranger["known"] is False and DISK not in stranger and NET not in stranger   # principle 1


@pytest.mark.parametrize("bad", [{DISK: 29}, {DISK: 901}, {NET: 29}, {NET: 901}, {NET: True}, {DISK: "abc"}])
def test_a_bad_value_is_refused_before_anything_is_written(room_server, bad):
    admin, ctx = room_server["admin"], room_server["ctx"]
    r = admin.post(URL, json={"session_wait_seconds": "77", **bad})
    assert r.status_code == 400 and "מספר שלם של שניות, 30–900" in r.text, r.text
    stored = {k: v for k, v in ctx.conn.execute("SELECT key, value FROM settings")}
    assert stored.get("session_wait_seconds") != "77" and not set(bad) & set(stored)


def test_a_corrupt_stored_value_is_the_two_minutes_not_zero_or_forever(room_server):
    from server.agent import timing as agent_timing
    ctx = room_server["ctx"]
    ctx.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, '5')", (DISK,))
    ctx.conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, 'x')", (NET,))
    ctx.conn.commit()
    assert agent_timing.seconds(ctx.conn, DISK) == 120 and agent_timing.seconds(ctx.conn, NET) == 120


def test_only_an_admin_sets_them_and_the_change_is_journaled_by_its_name(room_server):
    admin, deploy, ctx = room_server["admin"], room_server["deploy"], room_server["ctx"]
    assert deploy.post(URL, json={NET: 300}).status_code == 403
    assert admin.post(URL, json={NET: 300}).status_code == 200
    detail = [r["detail"] for r in ctx.conn.execute("SELECT detail FROM journal WHERE event = 'setting_change'")]
    assert f"{NET}=300" in detail
    from server.console.journal_he import SETTINGS_HE
    assert {DISK, NET} <= set(SETTINGS_HE)


def test_applied_counts_only_machines_that_read_the_new_values_back(room_server):
    admin, anon = room_server["admin"], room_server["anon"]
    assert admin.post(URL, json={DISK: 90}).status_code == 200
    _hello_applied(anon, CLONER1, None)                                   # an old agent: no echo
    _hello_applied(anon, CLONER2, {DISK: 120})                            # echoes the OLD value
    assert _applied(admin)[DISK] == {"applied": 0, "connected": 2}, "stored on the server is not applied"
    _hello_applied(anon, CLONER1, {DISK: 90, NET: 120})
    assert _applied(admin)[DISK] == {"applied": 1, "connected": 2}
    assert _applied(admin)[NET] == {"applied": 1, "connected": 2}          # CLONER2 sent no net value


def test_the_console_has_both_rows_in_the_cloning_card_with_the_servers_range():
    from server.agent import timing
    js = console_js()
    for key in (DISK, NET):
        row = re.search(rf'\{{ key: "{key}", card: "deploy", type: "agenttime".*?\n(?:    .*\n)*?    sheetNote', js)
        assert row, f"console.js has no agenttime row for {key} in the deploy card"
        default, low, high = timing.RANGES[key]
        assert f'unit: "sec", min: {low}, max: {high}, def: {default}' in row.group(0), key


# --- the agent ----------------------------------------------------------------------------

native = requires_native(("bash", BASH))


def _run(tmp_path, body: str) -> str:
    run = tmp_path / "run"
    (run / "targets" / "sda").mkdir(parents=True, exist_ok=True)
    (run / "targets" / "sdb").mkdir(parents=True, exist_ok=True)
    return sh(
        "unset WAIT_STREAM_STALL_S STREAM_STALL_TIMEOUT DISK_STALL_S FANOUT_STALL_EVICT_MS "
        "CHUNKCAST_WRITE_STALL_MS STALL_NET_LIMIT_S STALL_DISK_LIMIT_S; "
        f"export RUN_DIR={posix(run)!r} LOG_FILE={posix(run)!r}/agent.log; "
        + agent_sources("common", "timing", "waits", "stall", "chunkstall")
        + "log() { :; }; " + body)


@native
def test_what_the_screens_are_told_the_limit_is_two_minutes_for_the_disk_and_for_the_network(tmp_path):
    """stall_limit_s (stall.sh, the wire, the console's and the cloner's line) -- no answer from the server yet."""
    out = _run(tmp_path, '_st_now=200; _stall_set sda waiting_disk 100; _stall_set sdb waiting_net 100; '
                         'cat "$RUN_DIR/targets/sda/stall" "$RUN_DIR/targets/sdb/stall"')
    assert out.split() == ["waiting_disk", "100", "120", "waiting_net", "100", "120"], out


@native
def test_the_chunk_engine_is_told_the_same_two_limits(tmp_path):
    out = _run(tmp_path, "_chunk_live() { :; }; drawer_stall_tick() { echo \"disk=$STALL_DISK_LIMIT_S net=$STALL_NET_LIMIT_S\"; }; "
                         "chunk_stall_tick 1 /dev/null sda")
    assert "disk=120 net=120" in out, out


@native
def test_the_agent_hands_fanout_and_chunkcast_the_disk_wait_in_their_environment(tmp_path):
    """The ceiling is enforced by fanout (STALL_EVICT_MS) and chunkcast (CC_WRITE_STALL_MS): they read it from
    the environment, so it must be exported -- a child process sees what the agent holds."""
    out = _run(tmp_path, 'sh -c \'echo "child fanout=$FANOUT_STALL_EVICT_MS chunkcast=$CHUNKCAST_WRITE_STALL_MS"\'')
    assert "child fanout=120000 chunkcast=120000" in out, out


# --- the local clone's stall ceiling (Nadav 18/11: "כן בבקשה", 5 minutes down to 2) -------------------------------------
# One rule: the console's drawer_disk_wait_seconds if an answer brought it, else 120 (a local clone may have no server).

from test_agent_timing_2056_2059 import _with_answer, answer   # noqa: E402,F401


def _lc_wait(machine, timing: dict, env: str = "") -> str:
    """_lc_wait_stream on a stream that never moves, its naps stubbed to nothing: the loop counts seconds in no time
    and ends the source reader at the ceiling the agent holds. Prints the log line that names that ceiling."""
    body = (agent_sources("sysinfo", "failmark", "progress", "lccopy") + env
            + 'LC="$RUN_DIR/lc"; mkdir -p "$LC/run"; _nap() { :; }; wait_pid() { :; }; '
              'sleep 60 & echo $! > "$LC/run/src.pid"; echo 5 > "$RUN_DIR/ctr"; '
              '_lc_wait_stream "$(cat "$LC/run/src.pid")" "$RUN_DIR/ctr"; echo "rc=$?"; kill "$(cat "$LC/run/src.pid")" 2>/dev/null; true')
    return _with_answer(machine, timing, body)


@native
def test_the_local_clone_waits_120_when_no_setting_came(fake_machine):
    out = _lc_wait(fake_machine, {})
    assert "local clone: no progress for 120 s" in out and "rc=1" in out, out


@native
def test_the_local_clone_waits_the_setting_when_one_came(fake_machine):
    out = _lc_wait(fake_machine, {"drawer_disk_wait_seconds": 45})
    assert "local clone: no progress for 45 s" in out and "rc=1" in out, out


@native
def test_a_bad_setting_leaves_the_local_clone_on_120(fake_machine):
    out = _lc_wait(fake_machine, {"drawer_disk_wait_seconds": 29})
    assert "local clone: no progress for 120 s" in out, out


@native
def test_a_tests_own_ceiling_still_wins_for_the_local_clone(fake_machine):
    out = _lc_wait(fake_machine, {"drawer_disk_wait_seconds": 45}, env="export LC_STALL_S=7; ")
    assert "local clone: no progress for 7 s" in out, out
