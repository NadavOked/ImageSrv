"""#2815: failed logins must still perform one password derivation."""

import pytest

from server.console import users
from server.db import connect


PASSWORD = "Aa12345!"


@pytest.fixture
def conn(tmp_path):
    c = connect(tmp_path / "users.db")
    users.create(c, "nadav", PASSWORD, "admin", by="test")
    yield c
    c.close()


@pytest.fixture
def derive_calls(conn, monkeypatch):
    real_derive = users._derive
    calls = []

    def counting_derive(password, salt, iterations):
        calls.append((password, salt, iterations))
        return real_derive(password, salt, iterations)

    monkeypatch.setattr(users, "_derive", counting_derive)
    return calls


@pytest.mark.parametrize("state, stored", [
    pytest.param("nonexistent", None, id="nonexistent"),
    pytest.param("disabled", None, id="disabled"),
    pytest.param("corrupt", "garbage", id="garbage"),
    pytest.param("corrupt", "pbkdf2$1$zzzz$ccdd", id="non_hex_salt"),
    pytest.param("corrupt", "unknown$1$aabb$ccdd", id="unknown_scheme"),
    pytest.param("corrupt", "pbkdf2$invalid$aabb$ccdd", id="invalid_iterations"),
    pytest.param("corrupt", "pbkdf2$0$aabb$ccdd", id="zero_iterations"),
    pytest.param("corrupt", "pbkdf2$-1$aabb$ccdd", id="negative_iterations"),
    pytest.param("corrupt", "pbkdf2$1$aabb$", id="empty_digest"),
])
def test_rejected_user_derives_once_like_wrong_password(conn, derive_calls, state, stored):
    password = "Wrong-pass-1!"
    assert users.verify(conn, "nadav", password) is None
    assert len(derive_calls) == 1
    derive_calls.clear()

    username = "nadav"
    if state == "nonexistent":
        username = "missing"
    elif state == "disabled":
        conn.execute("UPDATE users SET disabled_at = '2026-01-01T00:00:00'")
    else:
        conn.execute("UPDATE users SET pw_hash = ?", (stored,))
    conn.commit()

    assert users.verify(conn, username, password) is None
    assert len(derive_calls) == 1
    assert derive_calls[0][0] == password
    assert derive_calls[0][2] == users._ITERATIONS


def test_right_password_returns_role_with_one_derivation(conn, derive_calls):
    assert users.verify(conn, "nadav", PASSWORD) == "admin"
    assert len(derive_calls) == 1
