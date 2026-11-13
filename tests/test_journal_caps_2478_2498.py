"""#2478 + #2498 — two writes an attacker could inflate, now capped.

* **#2478** — `login_account_failures` (#2443) took a row per failed login with no
  cap on the row count: a random username per attempt added a row per request
  until the 30-minute window ran out. Same shape as #2459 (`login_attempts`):
  per (name, surface) only the last `ACCOUNT_LIMIT` rows are kept -- all that
  `_check_account` reads -- and the whole table holds at most `MAX_ACCOUNT_ROWS`;
  above it the names with the fewest rows go first, oldest first, so a locked
  account goes last. The account decision itself does not change.
* **#2498** — `GET /boot/menu?mac=` wrote an `unknown_mac` line on every request,
  with no identity gate and no throttle; a random MAC per request grew the
  journal (kept 365 days) linearly. Now: one line per MAC per window with a
  count (as the hello, #2487 -- same key), no line for a requester whose lease
  check was refused on the deploy vlan (the identity line already names it), and
  per event at most `JOURNAL_KEYS_PER_WINDOW` *different* keys per window -- the
  rest are counted, and a `journal_flood` line says so (principle 5). The
  identity refusals get the same per-event cap: a random MAC per request was a
  new key for #1949 every time. A machine proven by its DHCP lease is not
  counted in the cap. The menu answer is not touched (principle 1).

Negative control: with `server/console/login_guard.py`, `server/agent/api.py`,
`server/agent/hello.py` and `server/app.py` back at origin/main f8f62e26 every
test here fails except the ones marked "control".
"""

from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from server.console import login_guard
from test_machine_identity import (MAC1, STATION_IP, STRANGER_IP,  # noqa: F401
                                   identity_server, journal_details)


# --- #2478: login_account_failures -------------------------------------------------


@pytest.fixture()
def guard(server, monkeypatch):
    t = {"now": time.time()}
    monkeypatch.setattr(login_guard, "now", lambda: t["now"])
    return server["ctx"].conn, t


def _rows(conn, name: str | None = None) -> int:
    if name is None:
        return conn.execute("SELECT COUNT(*) FROM login_account_failures").fetchone()[0]
    return conn.execute("SELECT COUNT(*) FROM login_account_failures WHERE username = ?",
                        (name,)).fetchone()[0]


def _flood(conn, t, count: int, ip: str = "10.66.0.1") -> None:
    for n in range(count):
        t["now"] += 1
        login_guard.record_failure(conn, f"rnd-{n:05d}", ip)


def test_random_usernames_do_not_grow_the_account_table_without_bound(guard, monkeypatch):
    conn, t = guard
    # raising=False: against the old code the test fails on the assertion, not on monkeypatch.
    monkeypatch.setattr(login_guard, "MAX_ACCOUNT_ROWS", 20, raising=False)
    _flood(conn, t, 100)
    assert _rows(conn) <= 20
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'login_account_failures_trimmed'"
                        ).fetchone()[0] >= 1, "the trim leaves no trace in the journal"


def test_one_name_keeps_only_the_rows_the_account_check_reads(guard):
    """150 failures on one name (record_failure without check, as a path that skips it
    would): 50 rows stay, the account is still locked, and the lockout line is
    written once -- exactly as before the cap."""
    conn, t = guard
    for n in range(3 * login_guard.ACCOUNT_LIMIT):
        t["now"] += 1
        login_guard.record_failure(conn, "noc", f"10.77.{n // 200}.{n % 200 + 1}")
    assert _rows(conn, "noc") == login_guard.ACCOUNT_LIMIT
    with pytest.raises(login_guard.LoginBlocked) as exc:
        login_guard.check(conn, "noc", "10.88.0.1")
    assert exc.value.status == 429 and exc.value.code == login_guard.ACCOUNT_CAPPED
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'login_account_lockout'"
                        ).fetchone()[0] == 1


def test_a_locked_account_survives_a_random_name_flood(guard, monkeypatch):
    conn, t = guard
    monkeypatch.setattr(login_guard, "MAX_ACCOUNT_ROWS", 100, raising=False)
    for n in range(login_guard.ACCOUNT_LIMIT):
        t["now"] += 1
        login_guard.record_failure(conn, "noc", f"10.77.0.{n + 1}")
    _flood(conn, t, 300)
    assert _rows(conn) <= 100
    assert _rows(conn, "noc") == login_guard.ACCOUNT_LIMIT
    with pytest.raises(login_guard.LoginBlocked) as exc:
        login_guard.check(conn, "noc", "10.88.0.1")
    assert exc.value.code == login_guard.ACCOUNT_CAPPED


def test_below_the_caps_nothing_is_removed(guard, monkeypatch):
    """Control: under both caps every row stays, and nothing is journaled."""
    conn, t = guard
    monkeypatch.setattr(login_guard, "MAX_ACCOUNT_ROWS", 20, raising=False)
    _flood(conn, t, 15)
    for _ in range(5):
        t["now"] += 1
        login_guard.record_failure(conn, "noc", "10.77.0.1")
    assert _rows(conn) == 20 and _rows(conn, "noc") == 5
    assert conn.execute("SELECT COUNT(*) FROM journal WHERE event = 'login_account_failures_trimmed'"
                        ).fetchone()[0] == 0


# --- #2498: GET /boot/menu?mac= -------------------------------------------------------


def _cap(agent_api) -> int:
    """32 when the constant is missing: against the old code the test fails on the
    journal, not on an AttributeError (a failure that proves nothing)."""
    return getattr(agent_api, "JOURNAL_KEYS_PER_WINDOW", 32)


def _random_macs(count: int) -> list[str]:
    return [f"02:66:00:00:{n // 256:02x}:{n % 256:02x}" for n in range(count)]


def _journal_rows(server) -> int:
    return server["ctx"].conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0]


def _menus(client: TestClient, macs: list[str]) -> list[str]:
    out = []
    for mac in macs:
        r = client.get(f"/boot/menu?mac={mac}")
        assert r.status_code == 200, r.text
        out.append(r.text.replace(mac, "<MAC>"))
    return out


def test_random_macs_from_a_stranger_do_not_grow_the_journal_linearly(identity_server):
    from server.agent import api as agent_api
    cap = _cap(agent_api)
    before = _journal_rows(identity_server)
    texts = _menus(identity_server["stranger"], _random_macs(cap * 6))
    grown = _journal_rows(identity_server) - before
    assert grown <= cap + 2, f"{grown} journal rows for {cap * 6} menu requests"
    # principle 5: the flood is not silent.
    assert journal_details(identity_server, "journal_flood")
    # principle 1: the answer is the same before and after the cap.
    assert len(set(texts)) == 1
    assert "set default=local" in texts[0]


def test_random_macs_off_the_deploy_vlan_are_capped_but_the_first_is_seen(identity_server):
    """#141: an unregistered machine on the college network is still journaled --
    there is no lease to check there -- but a random MAC per request is capped."""
    from server.agent import api as agent_api
    cap = _cap(agent_api)
    off = TestClient(identity_server["app"], base_url="http://10.99.0.1:8080",
                     client=(STRANGER_IP, 40010))
    macs = _random_macs(cap * 4)
    texts = _menus(off, macs)
    lines = journal_details(identity_server, "unknown_mac")
    assert lines[0][1].startswith(macs[0])
    assert len(lines) == cap
    assert len(set(texts)) == 1


def test_a_real_unregistered_machine_is_journaled_during_the_flood(identity_server):
    """Control (the issue's): the same MAC from its own lease address still writes
    `unknown_mac` -- also after the flood has used the cap."""
    from server.agent import api as agent_api
    off = TestClient(identity_server["app"], base_url="http://10.99.0.1:8080",
                     client=(STRANGER_IP, 40011))
    _menus(off, _random_macs(_cap(agent_api) * 2))
    identity_server["station"].get(f"/boot/menu?mac={MAC1}")
    assert any(detail.startswith(f"{MAC1} from {STATION_IP}")
               for _u, detail in journal_details(identity_server, "unknown_mac"))


def test_a_repeated_menu_is_one_line_then_a_count(identity_server, monkeypatch):
    from server.agent import api as agent_api
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    for _ in range(4):
        identity_server["station"].get(f"/boot/menu?mac={MAC1}")
    assert len(journal_details(identity_server, "unknown_mac")) == 1
    now[0] += agent_api.IDENTITY_JOURNAL_WINDOW_S + 1
    identity_server["station"].get(f"/boot/menu?mac={MAC1}")
    lines = [d for _u, d in journal_details(identity_server, "unknown_mac")]
    assert len(lines) == 2 and "[+3 identical requests" in lines[1], lines


def test_the_flood_count_comes_in_the_next_window(identity_server, monkeypatch):
    from server.agent import api as agent_api
    from server.console.journal_he_detail import he_detail
    cap = _cap(agent_api)
    now = [1000.0]
    monkeypatch.setattr(agent_api, "_clock", lambda: now[0])
    _menus(identity_server["stranger"], _random_macs(cap + 10))
    now[0] += agent_api.IDENTITY_JOURNAL_WINDOW_S + 1
    _menus(identity_server["stranger"], ["02:77:00:00:00:01"])
    lines = [d for _u, d in journal_details(identity_server, "journal_flood")]
    # #2489: all of them come from one stranger, so its own cap (JOURNAL_KEYS_PER_SOURCE)
    # binds before the event's: every one past it is counted, not only the 10 past `cap`.
    counted = cap + 10 - agent_api.JOURNAL_KEYS_PER_SOURCE
    assert any(f"+{counted} more different not journaled" in d for d in lines), lines
    for line in lines:
        assert he_detail("journal_flood", line) != line, line


def test_a_stranger_on_the_vlan_writes_no_unknown_mac_line(identity_server):
    """The refused requester is journaled once, as `identity_*` -- not twice."""
    identity_server["stranger"].get("/boot/menu?mac=02:66:00:00:99:01")
    assert journal_details(identity_server, "unknown_mac") == []
    assert journal_details(identity_server, "identity_unverifiable")
