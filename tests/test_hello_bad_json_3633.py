"""#3633 -- a hello the server answers 400 `bad_json` leaves a line in the journal, without the body.

18/11, cloner 2: every hello was refused for minutes and the server's log said only
`POST /api/v1/agent/hello 400 Bad Request` -- no reason, no source, nothing for the console (#3632
was found with tcpdump on the lab server). A machine that cannot report looks like a quiet machine
(principle 5). Now: one line per source and reason per window (the #1949/#2498 throttle), with the
source address, the reason, the length and, for a JSON syntax error, the position; the repeats are
counted. The body itself is never written: it can hold `monitor_secret`.

The answer (400, `bad_json`) does not change. Negative control: `server/agent/api.py`, `server/agent/body.py`,
`server/console/journal_he.py` and `server/console/journal_he_detail.py` back at the base commit.
"""

from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")

from conftest import hello_body
from server.agent import api as agent_api
from server.console.journal_he_detail import he_detail
from test_machine_identity import MAC1, STATION_IP, STRANGER_IP, identity_server, journal_details  # noqa: F401

HEADERS = {"Content-Type": "application/json"}
SECRET = "monitor-secret-do-not-journal-7f3a"


def torn_body() -> tuple[bytes, int]:
    """The field's body: a whole hello with a tail of a longer write after it. (bytes, JSON error position)."""
    whole = json.dumps({**hello_body(MAC1), "monitor_secret": SECRET}).encode()
    raw = whole + b']}}"netwatch":{"pending":true}'
    with pytest.raises(json.JSONDecodeError) as err:
        json.loads(raw)
    return raw, err.value.pos


def post(client, raw: bytes):
    return client.post("/api/v1/agent/hello", content=raw, headers=HEADERS)


def lines(server) -> list[str]:
    return [detail for _user, detail in journal_details(server, "hello_bad_json")]


def test_a_torn_hello_is_refused_as_before_and_journaled_with_source_length_and_position(identity_server):
    raw, pos = torn_body()
    answer = post(identity_server["station"], raw)
    assert (answer.status_code, answer.json()["code"]) == (400, "bad_json")
    assert lines(identity_server) == [f"{STATION_IP}: body is not JSON ({len(raw)} bytes, error at char {pos})"]


def test_the_body_is_not_written_anywhere_in_the_journal(identity_server):
    raw, _pos = torn_body()
    post(identity_server["station"], raw)
    everything = " ".join(row["detail"] for row in identity_server["ctx"].conn.execute("SELECT detail FROM journal"))
    assert lines(identity_server), "nothing journaled -- the check below would pass for nothing"
    assert SECRET not in everything and MAC1 not in everything and "netwatch" not in everything


def test_a_hello_every_two_seconds_is_one_line_then_a_count(identity_server, monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    raw, _pos = torn_body()
    for _ in range(5):
        post(identity_server["station"], raw)
        now[0] += 2
    assert len(lines(identity_server)) == 1
    now[0] += agent_api.IDENTITY_JOURNAL_WINDOW_S
    post(identity_server["station"], raw)
    second = lines(identity_server)
    assert len(second) == 2 and second[1].endswith("[+4 identical requests in the last 310s not journaled]"), second


def test_another_source_gets_its_own_line(identity_server):
    raw, _pos = torn_body()
    post(identity_server["station"], raw)
    post(identity_server["stranger"], raw)
    assert sorted(line.split(":")[0] for line in lines(identity_server)) == sorted([STATION_IP, STRANGER_IP])


def test_too_deep_and_not_an_object_say_their_own_reason(identity_server):
    post(identity_server["station"], b"[" * 40 + b"]" * 40)
    answer = post(identity_server["stranger"], b"[1, 2, 3]")
    assert (answer.status_code, answer.json()["code"]) == (400, "bad_json")
    assert {line.split(":")[0]: line for line in lines(identity_server)} == {
        STATION_IP: f"{STATION_IP}: body is nested too deeply (80 bytes)",
        STRANGER_IP: f"{STRANGER_IP}: body is not an object (9 bytes)"}


def test_the_console_reads_the_line_in_hebrew(identity_server):
    raw, pos = torn_body()
    post(identity_server["station"], raw)
    found = lines(identity_server)
    assert len(found) == 1, found
    detail = found[0]
    assert he_detail("hello_bad_json", detail) == f"{STATION_IP}: הגוף אינו JSON ({len(raw)} בייטים, השגיאה בתו {pos})"
    assert he_detail("hello_bad_json", detail + " [+4 identical requests in the last 10s not journaled]").endswith(
        " [ועוד 4 בקשות זהות ב-10 השניות האחרונות שלא נרשמו]")


def test_control_a_good_hello_and_an_oversized_one_leave_no_such_line(identity_server):
    """Passes with and without the fix: the line is for 400 `bad_json` only."""
    good = identity_server["station"].post("/api/v1/agent/hello", json=hello_body(MAC1))
    assert good.status_code == 200
    big = post(identity_server["station"], b" " * (1024 * 1024 + 1))
    assert big.status_code == 413
    assert lines(identity_server) == []
