"""#2493 (retro negative control for #3385): the monitor poll does not renew the cookie.

#3385 changed two places for the poll: ``presence`` (no ``touch``) and
``session_renewal.propose`` (no new cookie). The first had a test, the second none: putting
``session_renewal.py`` back to the old code left every test green. A monitor window that is open
polls every few seconds; if each poll renewed the cookie, an open window would keep the session
alive for ever (the very thing #2493 forbids), only through the renewal path instead of through
``touch``.

Same clock as ``test_console_session_sliding``: the cookie is 7 hours old, so a *real* request
renews it. The control proves the setup does renew (without it a "no Set-Cookie" would be vacuous).
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from test_console_session_sliding import (SEVEN_HOURS, _client, _session_cookies,  # noqa: F401
                                          skew)

POLL = "/api/console/monitor/aa:bb:cc:dd:ee:01/control"


def test_control_a_real_request_renews_this_cookie(server, skew):
    client, _ = _client(server)
    skew["s"] = SEVEN_HOURS
    r = client.get("/api/console/me")
    assert r.status_code == 200, r.text
    assert len(_session_cookies(r)) == 1, r.headers.get_list("set-cookie")


def test_a_monitor_poll_does_not_renew_the_cookie(server, skew):
    client, _ = _client(server)
    skew["s"] = SEVEN_HOURS
    r = client.get(POLL)
    assert r.status_code == 200, r.text
    assert _session_cookies(r) == [], \
        "the monitor poll renewed the session cookie: an open window keeps the console alive"


def test_a_poll_does_not_use_up_the_renewal_of_the_next_real_request(server, skew):
    client, _ = _client(server)
    skew["s"] = SEVEN_HOURS
    assert _session_cookies(client.get(POLL)) == []
    r = client.get("/api/console/me")
    assert r.status_code == 200, r.text
    assert len(_session_cookies(r)) == 1, r.headers.get_list("set-cookie")
