"""24/11 (mypy): `hello.build_answer` -- מכונה שנמחקה בין שתי הקריאות ל-`registry.lookup` (אחרי `model_slots.complete_ordinals`)
היא מכונה שאינה רשומה: תשובת `known: false` (עיקרון 1), לא 500.

**בקרה שלילית:** על `server/agent/hello.py` של הבסיס הטסט נופל ב-TypeError (`None["role"]`).
"""

from __future__ import annotations

import pytest
from conftest import Clock, MANIFEST_256, write_image

from server.agent import hello
from test_server_room import CLONER1

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None


@pytest.fixture()
def env(tmp_path):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from fakesend import Recorder
    from server.app import create_app
    from server.console import users

    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    app = create_app(tmp_path / "data", images, "http://10.44.12.10:7712", now_fn=Clock(),
                     sender_runner=Recorder(), wol_send=lambda _p: None)
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    admin = TestClient(app)
    admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"})
    assert admin.post("/api/console/machines", json={
        "mac": CLONER1, "name": "shich-1", "group_id": "grp_CLONERS"}).status_code == 200
    yield ctx
    ctx.chunk.stop()


def test_a_machine_deleted_between_the_two_lookups_is_an_unregistered_mac_not_a_500(env, monkeypatch):
    ctx = env
    real, calls = hello.registry.lookup, []

    def lookup(conn, mac, all_macs=None):
        calls.append(mac)
        return real(conn, mac, all_macs) if len(calls) == 1 else None      # gone when asked again

    monkeypatch.setattr(hello.registry, "lookup", lookup)
    monkeypatch.setattr(hello.model_slots, "complete_ordinals", lambda conn, mac, disks: True)
    monkeypatch.setattr(hello, "_record_seen", lambda *args, **kwargs: None)
    answer = hello.build_answer(ctx.conn, ctx.library, ctx.store, CLONER1,
                                disks=[{"serial": "S1"}], record_journal=False)
    assert len(calls) == 2, "the second lookup (after `complete_ordinals`) did not happen: the test proves nothing"
    assert answer["known"] is False and answer["role"] == "unknown" and answer["name"] is None
