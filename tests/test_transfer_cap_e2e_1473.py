"""#1473 end to end -- the inter-site rate cap on a real push and a real pull.

A real primary and a real secondary on loopback (the fixtures of
``test_storage_transfer``); only the pacer's clock and sleep are swapped for a
virtual pair, so the pacing is measured exactly and the test does not wait. The
rate source is the real one: the setting, read on the transfer's own thread.
"""

from __future__ import annotations

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL is needed for the inter-server mTLS (#740)")

from conftest import write_image  # noqa: E402
from server.interserver import interserver_auth, transfer_cap  # noqa: E402
from test_storage_pull import PULL, PULL_ID  # noqa: E402
from test_storage_transfer import IMAGE, _wait, paired, secondary  # noqa: E402,F401
from test_transfer_cap_1473 import MBPS, _Virtual  # noqa: E402

pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="no tls-exporter (pyOpenSSL) -- channel binding cannot be checked")


@pytest.fixture()
def virtual(monkeypatch):
    """Every transfer's pacer keeps its real rate source (the setting, read on the
    transfer's own thread) but runs on a virtual clock."""
    real, v = transfer_cap.pacer, _Virtual()

    def fake(conn, **_kw):
        pace = real(conn, clock=v.clock, sleep=v.sleep)

        def record(sent):
            v.sent.append(sent)
            pace(sent)
        return record
    monkeypatch.setattr(transfer_cap, "pacer", fake)
    return v


def _run(admin, nid, kind, image_id):
    r = admin.post(f"/api/console/storage-nodes/{nid}/{kind}", json={"image_id": image_id})
    assert r.status_code == 200, r.text
    row = _wait(admin, nid, r.json()["id"])
    assert row["state"] == "done", row
    return row


@pytest.mark.parametrize("kind", ["transfer", "pull"])
def test_without_a_cap_a_transfer_is_not_slowed(server, secondary, paired, virtual, kind):
    if kind == "pull":
        write_image(secondary["root"], PULL)
    _run(server["admin"], paired, kind, PULL_ID if kind == "pull" else IMAGE)
    assert virtual.sent, "the transfer never consulted the pacer"
    assert virtual.slept == []


@pytest.mark.parametrize("kind", ["transfer", "pull"])
def test_a_cap_holds_push_and_pull_to_it(server, secondary, paired, virtual, kind):
    """1 Mb/s = 125,000 B/s: the time the pacer waits is exactly the bytes it saw
    pass (after the first piece, which anchors) at that rate."""
    admin = server["admin"]
    assert admin.post("/api/console/settings",
                      json={transfer_cap.SETTING: 1}).status_code == 200
    if kind == "pull":
        write_image(secondary["root"], PULL)
    row = _run(admin, paired, kind, PULL_ID if kind == "pull" else IMAGE)
    assert virtual.sent[-1] == row["bytes_total"]
    moved = virtual.sent[-1] - virtual.sent[0]
    assert moved > 0
    assert sum(virtual.slept) == pytest.approx(moved / (1 * MBPS))
