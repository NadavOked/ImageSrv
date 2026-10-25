"""F4 on a secondary's server screen: the pairing code by physical presence.

Nadav 16/10, variant A of the mockup (PR #1525, docs/design/native/
dcui-pairing): the DCUI of a **secondary** shows a one-time pairing code with
**no password** -- being at the server is the authorization, as for F9.  The
risk shown and accepted: the code alone is not enough (single use, 10 minutes,
5 wrong tries, and only an admin signed in to the primary's console can use
it).  The channel is the DCUI process itself (root on tty1, straight to the DB
through ``storage_nodes``) -- no route, no network address.  The loopback API
(``/api/console/storage-pairing-window``) stays exactly as it was.
"""

from __future__ import annotations

import re
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from server import db, interserver_auth, storage_nodes
from server.dcui import app as dcui_app
from server.dcui import gui, pairing, view
from server.dcui.data import demo_snapshot

REPO = Path(__file__).resolve().parent.parent
KEYS = gui.KEYS
SPKI = "a1b2c3d4e5f607189c3e5d27b04f8a61e2d7c93b5a0f14e8d6b27c39a5e01f84"
CODE_RE = re.compile(r"^[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}-[ABCDEFGHJKMNPQRSTUVWXYZ23456789]{4}$")


@pytest.fixture()
def conn(tmp_path):
    return db.connect(tmp_path / "imagectl.db")


def _secondary(conn, identity=True):
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    if identity:
        conn.execute("INSERT INTO storage_identity (singleton, node_id, server_spki,"
                     " server_cert_ref, server_key_ref) VALUES (1, ?, ?, 'c', 'k')",
                     ("sn_" + SPKI[:16], SPKI))
        conn.commit()
    return conn


def _parent(conn):
    conn.execute(
        "INSERT INTO parent_credentials (singleton, parent_id, token_hash,"
        " bound_cert_ref, pinned_parent_spki, protocol_version, enrolled_at)"
        " VALUES (1, 'sn_5e91c07a2b4d6f13', ?, 'cert:1', 'spki:1', '2.1', ?)",
        (interserver_auth.hash_token("t"), db.now_iso()))
    conn.commit()


def _windows(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM pairing_windows").fetchone()[0]


# ---- the service: what F4 may do, and where it is refused ------------------------

def test_the_code_is_eight_unambiguous_characters_shown_once_for_ten_minutes(conn):
    _secondary(conn)
    before = time.time()
    result, code = pairing.open_code(conn)
    assert result.ok and code is not None
    assert CODE_RE.match(code.code), code.code          # no 0/O, 1/I (nor L)
    assert 595 <= code.expires - before <= 605          # 10 minutes, not the API's 3
    assert (code.node_id, code.spki) == ("sn_" + SPKI[:16], SPKI)
    # once: the DB keeps the salted hash, and no status returns the code
    status = interserver_auth.pairing_window_status(conn)
    assert status["open"] and code.code not in repr(status)
    row = conn.execute("SELECT * FROM pairing_windows").fetchone()
    assert code.code.encode() not in b"".join(
        v if isinstance(v, bytes) else str(v).encode() for v in tuple(row))
    # and it is the real thing: the primary's verify consumes it, once
    assert interserver_auth.verify_and_consume_code(conn, code.code) is True
    assert interserver_auth.verify_and_consume_code(conn, code.code) is False
    events = [r["event"] for r in conn.execute("SELECT event, user FROM journal WHERE user='dcui'")]
    assert "storage_pairing_open" in events


def test_the_api_keeps_its_three_minute_window_and_now_eight_character_codes(conn):
    _secondary(conn)
    opened = storage_nodes.open_local_pairing(conn, ("noc", "admin"))
    expires = datetime.fromisoformat(opened["expires_at"])
    left = (expires - datetime.now(timezone.utc)).total_seconds()
    assert 170 <= left <= 181
    assert CODE_RE.match(opened["code"])
    assert "opened_at" not in opened                     # the API's answer is unchanged


@pytest.mark.parametrize("role", [None, "standalone"])
def test_f4_is_refused_on_a_primary_or_standalone_like_the_api(conn, role):
    if role:
        db.set_setting(conn, storage_nodes.ROLE_KEY, role)
    result, code = pairing.open_code(conn)
    assert (result.ok, code) == (False, None)
    assert result.message == "Pairing codes are shown only on a secondary server."
    assert _windows(conn) == 0
    with pytest.raises(storage_nodes.NotSecondaryError):
        storage_nodes.open_physical_pairing(conn)
    with pytest.raises(storage_nodes.NotSecondaryError):
        storage_nodes.unbind_parent_physical(conn)
    assert pairing.disconnect(conn).ok is False


def test_f4_is_refused_while_a_primary_is_recorded(conn):
    _parent(_secondary(conn))
    result, code = pairing.open_code(conn)
    assert code is None and "already paired" in result.message
    assert _windows(conn) == 0


def test_without_a_db_nothing_is_opened():
    result, code = pairing.open_code(None)
    assert code is None and result.ok is False


def test_leaving_the_code_screen_voids_the_code(conn):
    _secondary(conn)
    _result, code = pairing.open_code(conn)
    # a newer window (the loopback API) is not the screen's to close
    storage_nodes.open_local_pairing(conn, ("noc", "admin"))
    pairing.close_code(conn, code)
    assert _windows(conn) == 1
    _result, code = pairing.open_code(conn)
    pairing.close_code(conn, code)
    assert _windows(conn) == 0
    assert interserver_auth.verify_and_consume_code(conn, code.code) is False


def test_the_screen_follows_the_window(conn):
    _secondary(conn)
    _result, code = pairing.open_code(conn)
    now = time.time()
    assert pairing.state(conn, code, now) == "open"
    assert pairing.state(conn, code, code.expires + 1) == "expired"
    # replaced by a newer window (the loopback API): the code shown is void
    storage_nodes.open_local_pairing(conn, ("noc", "admin"))
    assert pairing.state(conn, code, now) == "closed"
    # five wrong tries on the primary close it before its time
    _result, code = pairing.open_code(conn)
    for _ in range(interserver_auth.MAX_ATTEMPTS):
        interserver_auth.verify_and_consume_code(conn, "ZZZZ-ZZZZ")
    assert pairing.state(conn, code, time.time()) == "closed"
    # the primary used it: a parent is recorded
    _parent(conn)
    assert pairing.state(conn, code, time.time()) == "paired"
    assert pairing.paired_details(conn)["parent_id"] == "sn_5e91c07a2b4d6f13"


def test_an_unreadable_db_is_unchecked_not_open(conn, monkeypatch):
    import sqlite3
    _secondary(conn)
    _result, code = pairing.open_code(conn)

    def broken(*a, **k):
        raise sqlite3.OperationalError("disk I/O error")
    monkeypatch.setattr(storage_nodes, "has_parent", broken)
    assert pairing.state(conn, code, time.time()) == "unchecked"


def test_disconnect_removes_the_primary_and_reads_it_back(conn):
    _parent(_secondary(conn))
    result = pairing.disconnect(conn)
    assert result.ok, result.message
    assert storage_nodes.has_parent(conn) is False
    assert conn.execute("SELECT user FROM journal WHERE event='storage_parent_unbind'"
                        ).fetchone()["user"] == "dcui"


@pytest.mark.parametrize("typed,stored", [
    ("203.0.113.40:8443", "https://203.0.113.40:8443"),
    ("https://haifa.example.org:8443", "https://haifa.example.org:8443"),
    ("  10.30.0.8:8443/ ", "https://10.30.0.8:8443"),
    ("[2001:db8::5]:8443", "https://[2001:db8::5]:8443"),
])
def test_the_address_the_primary_sees_is_normalized_saved_and_read_back(conn, typed, stored):
    result = pairing.save_advertised(conn, typed)
    assert result.ok, result.message
    assert storage_nodes.advertised_url(conn) == stored
    assert pairing.advertised(conn) == stored


@pytest.mark.parametrize("typed", ["203.0.113.40", "http://203.0.113.40:8443",
                                   "203.0.113.40:8443/api", "u:p@203.0.113.40:8443"])
def test_a_bad_address_is_refused_and_nothing_is_saved(conn, typed):
    result = pairing.save_advertised(conn, typed)
    assert result.ok is False and "Invalid address" in result.message
    assert storage_nodes.advertised_url(conn) == ""


def test_an_empty_address_clears_it(conn):
    pairing.save_advertised(conn, "203.0.113.40:8443")
    result = pairing.save_advertised(conn, "")
    assert result.ok and result.message == "Address cleared."
    assert storage_nodes.advertised_url(conn) == ""


def test_the_eight_character_code_is_redacted_from_logs():
    code = interserver_auth.generate_pairing_code()
    assert CODE_RE.match(code)
    out = interserver_auth.redact_secrets(f"pairing failed for {code} at 10.0.0.5")
    assert code not in out and "10.0.0.5" in out


# ---- no network path reaches it ---------------------------------------------------

def _walk(routes):
    from fastapi.routing import APIRoute
    from starlette.routing import Mount
    for r in routes:
        if isinstance(r, APIRoute):
            yield r
        elif hasattr(r, "original_router"):
            yield from _walk(r.original_router.routes)
        elif hasattr(r, "routes") and not isinstance(r, Mount):
            yield from _walk(r.routes)


def test_no_route_opens_a_window_without_the_admin_session_and_loopback(server):
    """The only routes that touch the window are the two that were there."""
    routes = {(r.path, m) for r in _walk(server["app"].router.routes)
              for m in (r.methods or ()) if re.search(r"pairing|storage-parent", r.path)}
    assert routes == {("/api/console/storage-pairing-window", "POST"),
                      ("/api/console/storage-pairing-window", "GET"),
                      ("/api/console/storage-parent", "DELETE")}, routes


@pytest.mark.parametrize("host", ["10.0.0.5", "192.168.68.56"])
def test_a_non_local_peer_still_gets_403_even_as_admin(server, host):
    from conftest import complete_console_login
    from fastapi.testclient import TestClient
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    client = TestClient(server["app"], client=(host, 40000))
    complete_console_login(client, server["ctx"].conn, "noc", "admin-pass-123")
    assert client.post("/api/console/storage-pairing-window").status_code == 403
    assert client.delete("/api/console/storage-parent").status_code == 403
    assert _windows(server["ctx"].conn) == 0


def test_the_api_on_a_standalone_is_still_409(server):
    from conftest import complete_console_login
    from fastapi.testclient import TestClient
    client = TestClient(server["app"], client=("127.0.0.1", 40000))
    complete_console_login(client, server["ctx"].conn, "noc", "admin-pass-123")
    assert client.post("/api/console/storage-pairing-window").status_code == 409


def test_only_the_server_screen_calls_the_physical_functions():
    """No web module may reach the no-password path: its callers are
    storage_nodes itself and the DCUI process (server/dcui/)."""
    names = re.compile(r"\b(open_physical_pairing|close_physical_pairing|unbind_parent_physical)\b")
    callers = sorted(str(p.relative_to(REPO)).replace("\\", "/")
                     for p in (REPO / "server").rglob("*.py") if names.search(p.read_text(encoding="utf-8")))
    assert callers == ["server/dcui/pairing.py", "server/storage_nodes.py"], callers


# ---- the server screen: keys, stages and frames -----------------------------------

def _runtime(tmp_path, conn, role="secondary", **state):
    runtime = dcui_app.Runtime(tmp_path, conn, replace(demo_snapshot(), storage_role=role),
                               last_key_at=time.monotonic())
    for key, value in state.items():
        setattr(runtime, key, value)
    return runtime


def _press(runtime, *keys):
    for key in keys:
        dcui_app._handle_key(runtime, key, KEYS)


def _frame(runtime, wall=None):
    wall = time.time() if wall is None else wall
    return view.render(runtime, time.monotonic(), wall, "16:32", datetime(2026, 10, 16))


def _records(frame):
    return [line.split("\t") for line in frame.rstrip("\n").split("\n")]


def _kind(frame, kind):
    return [rec for rec in _records(frame) if rec[0] == kind]


def test_f4_on_an_unpaired_secondary_shows_the_code_and_the_identity(tmp_path, conn):
    _secondary(conn)
    pairing.save_advertised(conn, "203.0.113.40:8443")
    runtime = _runtime(tmp_path, conn)
    runtime.extras = view.collect_extras(conn)
    assert ["key", "F4", "צימוד לשרת ראשי", "0"] in _kind(_frame(runtime), "key")
    _press(runtime, KEYS.KEY_F4)
    assert runtime.screen == "pair" and runtime.pair_stage == "code"
    frame = _frame(runtime)
    code = _kind(frame, "d.code")[0]
    assert code[1] == runtime.pair.code and CODE_RE.match(code[1])
    assert code[2] == "זמן שנותר" and re.match(r"^(09:[0-5]\d|10:00)$", code[3])
    kv = {rec[1]: rec[2] for rec in _kind(frame, "d.kv")}
    assert kv["זהות השרת"] == "A1B2 C3D4 E5F6 0718"
    assert kv["מזהה"] == "sn_a1b2c3d4e5f60718"
    assert kv["הכתובת שהראשי מקליד"] == "https://203.0.113.40:8443"
    assert not [rec for rec in _kind(frame, "d.alert") if rec[2:] == ["warn"]]
    # the text screen shows the same code and is 80x25 ASCII
    lines = dcui_app._current_lines(runtime, time.monotonic())
    assert len(lines) == 25 and all(len(line) <= 80 for line in lines)
    "\n".join(lines).encode("ascii")
    assert runtime.pair.code in "\n".join(lines)


def test_the_code_screen_is_not_logged_out_after_90_seconds(tmp_path, conn):
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4)
    runtime.last_key_at = time.monotonic() - 200
    dcui_app.tick(runtime, time.monotonic())
    assert runtime.screen == "pair"


def test_without_the_address_the_code_screen_warns_in_yellow(tmp_path, conn):
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4)
    frame = _frame(runtime)
    warn = [rec for rec in _kind(frame, "d.alert") if rec[2:] == ["warn"]]
    assert len(warn) == 1 and "הכתובת שהראשי רואה לא הוגדרה" in warn[0][1]
    assert "הכתובת שהראשי מקליד" not in {rec[1] for rec in _kind(frame, "d.kv")}
    assert "WARNING: The address the primary sees is not set." in "\n".join(
        dcui_app._current_lines(runtime, time.monotonic()))


def test_f4_does_nothing_on_a_primary_and_offers_no_key(tmp_path, conn):
    runtime = _runtime(tmp_path, conn, role="standalone")
    assert not [rec for rec in _kind(_frame(runtime), "key") if rec[1] == "F4"]
    assert "<F4>" not in "\n".join(dcui_app._current_lines(runtime, time.monotonic()))
    _press(runtime, KEYS.KEY_F4)
    assert runtime.screen == "main" and _windows(conn) == 0
    # F4 is not a key there at all -- not a key that is tried and refused
    assert runtime.message == ""
    assert not conn.execute("SELECT 1 FROM journal WHERE event LIKE 'storage_pairing%'").fetchone()


def test_esc_closes_the_code_and_f5_replaces_it(tmp_path, conn):
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4)
    first = runtime.pair.code
    _press(runtime, KEYS.KEY_F5)
    assert runtime.screen == "pair" and runtime.pair.code != first
    assert interserver_auth.verify_and_consume_code(conn, first) is False
    _press(runtime, "\x1b")
    assert runtime.screen == "main" and _windows(conn) == 0
    assert view.he(runtime.message) == "קוד הצימוד בוטל — אי אפשר להשתמש בו עוד."


def test_the_screen_turns_to_paired_when_the_primary_uses_the_code(tmp_path, conn):
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4)
    assert interserver_auth.verify_and_consume_code(conn, runtime.pair.code)
    _parent(conn)
    runtime.pair_checked = 0.0
    dcui_app.tick(runtime, time.monotonic())
    assert runtime.pair_stage == "paired"
    frame = _frame(runtime)
    assert _kind(frame, "dlg")[0][3] == "✓ השרת מצומד"
    assert {"שרת ראשי", "מזהה ראשי", "זמן"} <= {rec[1] for rec in _kind(frame, "d.kv")}
    _press(runtime, "\n")
    assert runtime.screen == "main"


def test_an_expired_code_offers_a_new_one_on_enter(tmp_path, conn):
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4)
    runtime.pair = replace(runtime.pair, expires=time.time() - 1)
    conn.execute("DELETE FROM pairing_windows")
    conn.commit()
    runtime.pair_checked = 0.0
    dcui_app.tick(runtime, time.monotonic())
    assert runtime.pair_stage == "expired"
    assert _kind(_frame(runtime), "dlg")[0][3] == "הקוד פג תוקף"
    _press(runtime, "\n")
    assert runtime.pair_stage == "code" and _windows(conn) == 1


def test_f4_on_a_paired_secondary_asks_with_the_red_button_and_enter_disconnects(tmp_path, conn):
    _parent(_secondary(conn))
    runtime = _runtime(tmp_path, conn)
    runtime.extras = view.collect_extras(conn)
    assert ["key", "F4", "ניתוק מהשרת הראשי", "0"] in _kind(_frame(runtime), "key")
    _press(runtime, KEYS.KEY_F4)
    assert runtime.screen == "unpair" and _windows(conn) == 0
    frame = _frame(runtime)
    assert _kind(frame, "dlg")[0][1] == "danger"
    buttons = _kind(frame, "d.btn")
    assert ["d.btn", "נתק מהשרת הראשי", "Enter", "danger", "1"] in buttons
    assert not _kind(frame, "d.field"), "#1270: nothing is typed to confirm"
    _press(runtime, "\n")
    assert runtime.screen == "main" and storage_nodes.has_parent(conn) is False
    assert view.he(runtime.message) == "השרת נותק מהשרת הראשי."


def test_esc_on_the_disconnect_question_changes_nothing(tmp_path, conn):
    _parent(_secondary(conn))
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F4, "\x1b")
    assert runtime.screen == "main" and storage_nodes.has_parent(conn) is True


def test_f2_sets_the_address_the_primary_sees_behind_the_password(tmp_path, conn):
    from server import users
    users.create(conn, "admin", "before", "admin", by="test", is_builtin=True, check_policy=False)
    _secondary(conn)
    runtime = _runtime(tmp_path, conn)
    _press(runtime, KEYS.KEY_F2, *"before", "\n")
    assert runtime.screen == "menu"
    runtime.menu_index = dcui_app.MENU_ITEMS.index("Primary-Visible Address")   # #1988: not the last
    assert dcui_app.MENU_ITEMS[runtime.menu_index] == "Primary-Visible Address"
    _press(runtime, "\n")
    assert runtime.screen == "advertised"
    frame = _frame(runtime)
    assert _first_field(frame)[:2] == ["כתובת ופורט", ""]
    _press(runtime, *"203.0.113.40:8443", "\n")
    assert runtime.screen == "result" and runtime.result.ok
    assert storage_nodes.advertised_url(conn) == "https://203.0.113.40:8443"
    _press(runtime, "\x1b", "\x1b", KEYS.KEY_F4)
    assert runtime.pair.advertised == "https://203.0.113.40:8443"


def _first_field(frame):
    return _kind(frame, "d.field")[0][1:]


def test_every_new_screen_draws_80x25_ascii_text(tmp_path, conn):
    code = pairing.Code("K7M4-X9QD", "x", time.time() + 300, "sn_" + SPKI[:16], SPKI, "")
    screens = [
        dcui_app.render_pair(code, "code", time.time()),
        dcui_app.render_pair(replace(code, spki="", node_id=""), "code", time.time(), "err"),
        dcui_app.render_pair(code, "expired", time.time()),
        dcui_app.render_pair(code, "closed", time.time()),
        dcui_app.render_pair(code, "paired", time.time(), paired={"parent_id": "p"}),
        dcui_app.render_unpair("https://10.20.10.5:8443"),
        dcui_app.render_advertised("203.0.113.40:8443", 60, "Invalid address"),
        dcui_app.render_main(replace(demo_snapshot(), storage_role="secondary")),
    ]
    for lines in screens:
        assert len(lines) == 25 and all(len(line) <= 80 for line in lines)
        "\n".join(lines).encode("ascii")
    assert "<F4> Pairing" in screens[-1][23]


def test_the_gui_sends_f4_as_the_curses_key():
    assert gui.parse_token("KEY F4") == ("key", KEYS.KEY_F4)
    assert KEYS.KEY_F4 == 268                           # curses.KEY_F4 (KEY_F0 + 4)


def test_every_new_message_reads_in_hebrew():
    for message in ("Pairing codes are shown only on a secondary server.",
                    "This server is already paired; disconnect it first.",
                    "Disconnect cancelled; nothing was changed.",
                    "The code's state could not be read from the DB.",
                    "Paired with the primary server."):
        assert view.he(message) != message


def test_samples_render_every_pairing_state():
    frames = __import__("server.dcui.samples", fromlist=["frames"]).frames()
    for name in ("13-pair-code", "13b-pair-code-no-address", "13c-pair-expired",
                 "13d-paired", "14-unpair", "15-advertised"):
        assert frames[name].endswith("\n\n"), name
    assert "d.code\tK7M4-X9QD\tזמן שנותר\t09:42\t" in frames["13-pair-code"]
    assert "\t00:41\twarn" in frames["13b-pair-code-no-address"]


def test_the_code_window_is_ten_minutes_and_the_api_range_still_refuses_beyond(conn):
    assert interserver_auth.PHYSICAL_WINDOW_TTL_SECONDS == 600
    with pytest.raises(ValueError):
        interserver_auth.open_pairing_window(conn, ttl_seconds=601)
    now = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
    code = interserver_auth.open_pairing_window(conn, ttl_seconds=600, now=now)
    assert interserver_auth.verify_and_consume_code(
        conn, code, now=now + timedelta(seconds=599)) is True

