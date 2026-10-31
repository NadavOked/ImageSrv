"""הדופק של הראשי למשניים המצומדים — #1564.

(א) מקצה לקצה על loopback, אותם שני שרתים אמיתיים כמו ב-``test_storage_transfer``:
הדופק מגיע למשני בערוץ ה-mTLS ומרענן את ``last_contact_at`` שלו — ורק
כשהוא עבר את כל השומר. (ב) היעדים הם שורות ``storage_nodes`` פעילות בלבד.
(ג) משני שלא עונה אינו מחזיק את הדופק, והדופק אינו מחזיק את שעון החדר.
"""

from __future__ import annotations

import socket
import threading
import time

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from server import db
from server.interserver import interserver_auth, storage_heartbeat, storage_nodes
from server.rounds import room as room_mod
from server.dcui import view
from test_storage_transfer import paired, secondary          # noqa: F401 — fixtures

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)


def _primary_contact(server, nid) -> dict:
    return storage_nodes.contact_fields(server["ctx"].conn, nid)


# --- (א) מקצה לקצה ------------------------------------------------------------

def test_beat_reaches_the_paired_secondary_and_stamps_its_contact(
        server, secondary, paired, tmp_path):
    # ה-pairing לבדו אינו מגע (#1564) — זו נקודת המוצא.
    assert storage_nodes.parent_last_contact(secondary["conn"]) is None

    result = storage_heartbeat.beat(server["ctx"].conn, tmp_path / "data")

    assert result == {paired: None}
    assert storage_nodes.parent_last_contact(secondary["conn"]) is not None
    contact = _primary_contact(server, paired)
    assert contact["last_seen_at"] is not None and contact["last_error"] is None


def test_beat_with_a_wrong_token_never_stamps_the_secondary(
        server, secondary, paired, tmp_path):
    """הטוקן בקובץ ה-0600 של הראשי הוחלף — המשני דוחה, ולא נרשם מגע."""
    node = storage_nodes.node_row(server["ctx"].conn, paired)
    interserver_auth.write_credential_0600(node["credential_ref"],
                                           interserver_auth.generate_token())

    result = storage_heartbeat.beat(server["ctx"].conn, tmp_path / "data")

    assert result[paired]                       # כשל עם סיבה, לא None
    assert storage_nodes.parent_last_contact(secondary["conn"]) is None
    contact = _primary_contact(server, paired)
    assert contact["last_error"] and contact["last_error_at"]
    assert contact["last_seen_at"] is None


def test_a_secondary_that_accepts_tcp_and_never_answers_fails_fast(
        server, paired, tmp_path):
    """‏TCP נפתח ו-TLS לעולם אינו מתחיל: התקרה הקצרה של הדופק, לא 10+15ש'."""
    hang = socket.socket()
    hang.bind(("127.0.0.1", 0))
    hang.listen(1)                              # backlog: connect עובר, אין accept
    port = hang.getsockname()[1]
    try:
        with db.writing(server["ctx"].conn):
            server["ctx"].conn.execute(
                "UPDATE storage_nodes SET base_url = ? WHERE id = ?",
                (f"https://127.0.0.1:{port}/api/interserver/v1", paired))
        started = time.monotonic()
        result = storage_heartbeat.beat(server["ctx"].conn, tmp_path / "data",
                                        timeout=0.5)
        elapsed = time.monotonic() - started
    finally:
        hang.close()
    assert elapsed < 3.0, f"הדופק המתין {elapsed:.1f}ש' למשני שלא עונה"
    assert result[paired]
    assert _primary_contact(server, paired)["last_error"]


# --- (ב) היעדים: רק משניים מצומדים, מטבלת storage_nodes -------------------------

class _FakeClient:
    def __init__(self, url, fail):
        self.url, self.fail = url, fail

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def ping(self, token):
        if self.fail:
            raise ConnectionError("המשני לא עונה")
        return {"ok": True, "server_version": "v1.0.0"}


def _enroll(conn, label, i):
    return storage_nodes.enroll_node(
        conn, ("noc", "admin"), label=label,
        base_url=f"https://10.0.0.{i}:8443/api/interserver/v1",
        node_id=f"sn_{i:016x}",
        pinned_spki="00" * 32, client_cert_ref="ref", credential_ref="unused",
        protocol_version="2.1")


@pytest.fixture()
def fake_nodes(server, monkeypatch):
    conn = server["ctx"].conn
    ids = {name: _enroll(conn, name, i)
           for i, name in enumerate(("haifa", "dead", "off"), start=1)}
    storage_nodes.set_node_disabled(conn, ids["off"], True, ("noc", "admin"))
    called: list[str] = []
    timeouts: list[float] = []

    def node_client(conn, data_dir, node, *, timeout=None):
        called.append(node["base_url"])
        timeouts.append(timeout)
        return _FakeClient(node["base_url"], fail=node["id"] == ids["dead"]), "tok"

    monkeypatch.setattr(storage_nodes, "node_client", node_client)
    return {"conn": conn, "ids": ids, "called": called, "timeouts": timeouts}


def test_beat_targets_only_enabled_rows_and_one_failure_does_not_stop_the_rest(
        fake_nodes, tmp_path):
    ids = fake_nodes["ids"]
    result = storage_heartbeat.beat(fake_nodes["conn"], tmp_path)

    assert set(result) == {ids["haifa"], ids["dead"]}          # המושבת לא נשאל
    assert sorted(fake_nodes["called"]) == ["https://10.0.0.1:8443/api/interserver/v1",
                                            "https://10.0.0.2:8443/api/interserver/v1"]
    assert result[ids["haifa"]] is None and "לא עונה" in result[ids["dead"]]
    assert fake_nodes["timeouts"] == [storage_heartbeat.TIMEOUT_SECONDS] * 2
    conn = fake_nodes["conn"]
    assert storage_nodes.contact_fields(conn, ids["haifa"])["last_seen_at"]
    assert storage_nodes.contact_fields(conn, ids["dead"])["last_error"]
    assert storage_nodes.contact_fields(conn, ids["off"])["last_seen_at"] is None


def test_a_secondary_server_sends_no_heartbeat(fake_nodes, tmp_path):
    db.set_setting(fake_nodes["conn"], storage_nodes.ROLE_KEY, "secondary")
    assert storage_heartbeat.beat(fake_nodes["conn"], tmp_path) == {}
    assert fake_nodes["called"] == []


# --- (ג) השעון: קצב, ואי-חסימה ------------------------------------------------

def test_the_default_cadence_keeps_a_quiet_secondary_green():
    """150ש' (הכרעה 17/10), ושלושה דופקים שהוחמצו עדיין בתוך סף 10 הדקות."""
    assert storage_heartbeat.HEARTBEAT_SECONDS == 150
    assert 3 * storage_heartbeat.HEARTBEAT_SECONDS < view.PARENT_CONTACT_FRESH_SECONDS
    assert storage_heartbeat.TIMEOUT_SECONDS < storage_heartbeat.HEARTBEAT_SECONDS


def _app(tmp_path, **kwargs):
    from conftest import Clock, write_image, MANIFEST_256
    from server.app import create_app
    images = tmp_path / "images"
    write_image(images, MANIFEST_256)
    return create_app(tmp_path / "data", images, "http://10.44.12.10:8080",
                      now_fn=Clock(), **kwargs)


def test_the_runtime_defaults_to_the_150s_heartbeat(tmp_path):
    app = _app(tmp_path)
    try:
        assert app.state.runtime.heartbeat_interval == storage_heartbeat.HEARTBEAT_SECONDS
    finally:
        app.state.ctx.sender.stop()


def test_the_heartbeat_fires_from_the_lifespan_at_its_interval(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    stamps: list[float] = []
    seen_args: list = []
    three = threading.Event()

    def counting_beat(conn, data_dir):
        stamps.append(time.monotonic())
        seen_args.append(data_dir)
        if len(stamps) == 1:
            raise RuntimeError("DB נפל באמצע דופק")         # הלולאה חייבת לשרוד
        if len(stamps) >= 3:
            three.set()
        return {}

    monkeypatch.setattr(storage_heartbeat, "beat", counting_beat)
    app = _app(tmp_path, heartbeat_interval=0.2, room_clock_interval=10**6)
    try:
        with TestClient(app):
            assert three.wait(timeout=10.0), "הדופק לא ירה שלוש פעמים — הלולאה אינה מחוברת"
    finally:
        app.state.ctx.sender.stop()
    # לא לולאה צפופה: בין הדופק הראשון לשלישי עברו לפחות שני מרווחים.
    assert stamps[2] - stamps[0] >= 2 * 0.2 * 0.9
    assert seen_args[0] == app.state.runtime.data_dir


def test_a_stuck_heartbeat_does_not_hold_the_room_clock(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    entered, release = threading.Event(), threading.Event()
    sweeps = {"n": 0}
    real_sweep = room_mod.sweep

    def stuck_beat(conn, data_dir):
        entered.set()
        release.wait(timeout=30)
        return {}

    def counting_sweep(conn, store):
        sweeps["n"] += 1
        return real_sweep(conn, store)

    monkeypatch.setattr(storage_heartbeat, "beat", stuck_beat)
    monkeypatch.setattr(room_mod, "sweep", counting_sweep)
    app = _app(tmp_path, heartbeat_interval=0.01, room_clock_interval=0.02)
    try:
        with TestClient(app):
            assert entered.wait(timeout=5.0), "הדופק לא התחיל"
            before = sweeps["n"]
            deadline = time.monotonic() + 5.0
            while sweeps["n"] < before + 3 and time.monotonic() < deadline:
                time.sleep(0.1)
            moved = sweeps["n"] - before
            release.set()
    finally:
        release.set()
        app.state.ctx.sender.stop()
    assert moved >= 3, f"שעון החדר עמד בזמן שהדופק תקוע ({moved} דגימות)"
