"""Storage Nodes — משיכת אימג' משני→ראשי (#1071).

שני שרתים אמיתיים על loopback כמו ``test_storage_transfer``: הראשי הוא
``create_app``, המשני הוא ``InterserverTLSServer``. היוזם תמיד הראשי;
המשני רק עונה GET. sha256 מאומת **לפני** שהאימג' נכנס לספריית הראשי.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש ל-mTLS הבין-שרתי (#740)")

from conftest import MANIFEST_256, PARTITION_BYTES, write_image
from server import (db, disk_reserve, interserver_api, interserver_auth, storage_client,
                    storage_nodes, storage_transfer)
from server.images import ImageLibrary

try:
    from fastapi.testclient import TestClient
except Exception:                                             # noqa: BLE001
    TestClient = None


pytestmark = pytest.mark.skipif(
    not interserver_auth.can_export_keying_material(),
    reason="אין יכולת tls-exporter (pyOpenSSL) — channel-binding לא ניתן לאימות",
)

IMAGE = MANIFEST_256["id"]
PULL = {
    **MANIFEST_256,
    "id": "img_c0ffee",
    "name": "Haifa Local",
    "folder": "Haifa",
}
PULL_ID = PULL["id"]


class _Ctx:
    def __init__(self, conn, library, data_dir):
        self.conn = conn
        self.library = library
        self.data_dir = data_dir


@pytest.fixture()
def secondary(tmp_path):
    conn = db.connect(tmp_path / "sec.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    root = tmp_path / "sec_images"
    root.mkdir()
    ctx = _Ctx(conn, ImageLibrary(root), str(tmp_path / "sec"))
    ident = storage_nodes.ensure_identity(conn, tmp_path / "sec",
                                          host_sans=["127.0.0.1"])
    server = interserver_api.InterserverTLSServer(
        ctx, "127.0.0.1", 0, ident["cert_pem"], ident["key_pem"],
        data_dir=str(tmp_path / "sec")).start()
    yield {"conn": conn, "ctx": ctx, "ident": ident, "server": server,
           "port": server.port, "root": root}
    server.stop()


@pytest.fixture()
def paired(server, secondary, tmp_path):
    data_dir = tmp_path / "data"
    ident = storage_nodes.ensure_identity(server["ctx"].conn, data_dir,
                                          host_sans=["127.0.0.1"])
    code = interserver_auth.open_pairing_window(secondary["conn"])
    url = f"https://127.0.0.1:{secondary['port']}/api/interserver/v1"
    result = storage_client.pair_secondary(
        url, code=code, expected_secondary_spki=secondary["ident"]["server_spki"],
        cert_pem=ident["cert_pem"], key_pem=ident["key_pem"],
        primary_id=ident["node_id"])
    cred_dir = data_dir / "secondaries"
    cred_dir.mkdir(parents=True, exist_ok=True)
    cred_path = cred_dir / f"{result['secondary_id']}.token"
    interserver_auth.write_credential_0600(cred_path, result["token"])
    nid = storage_nodes.enroll_node(
        server["ctx"].conn, ("noc", "admin"), label="HAIFA", base_url=url,
        node_id=result["secondary_id"], pinned_spki=secondary["ident"]["server_spki"],
        client_cert_ref=interserver_auth.certificate_ref(ident["cert_pem"]),
        credential_ref=str(cred_path), protocol_version="2.1")
    return nid


def _wait(admin, nid, tid, timeout=30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        rows = admin.get("/api/console/storage-transfers").json()
        row = next(r for r in rows if r["id"] == tid and r["node_id"] == nid)
        if row["state"] in ("done", "failed"):
            return row
        time.sleep(0.1)
    raise AssertionError(f"ההעברה לא הסתיימה תוך {timeout}s: {row}")


def _incoming(server) -> Path:
    return Path(server["ctx"].library.root) / storage_transfer.INCOMING


def _peer(cert_pem, exporter=b"E" * 32, ver="TLSv1.3"):
    return interserver_api.TlsPeer(tls_version=ver,
                                   cert_der=interserver_auth._to_der(cert_pem),
                                   exporter=exporter)


@pytest.fixture()
def paired_ctx(tmp_path):
    conn = db.connect(tmp_path / "s.db")
    db.set_setting(conn, storage_nodes.ROLE_KEY, "secondary")
    root = tmp_path / "root"
    root.mkdir()
    ctx = _Ctx(conn, ImageLibrary(root), str(tmp_path / "s"))
    ident = storage_nodes.ensure_identity(conn, tmp_path / "s", host_sans=["127.0.0.1"])
    primary, _ = interserver_auth.generate_self_signed("p", ip_sans=["127.0.0.1"])
    peer = _peer(primary)
    handles = {}
    begin = interserver_api.pair_begin(conn, handles, peer, {
        "protocol_version": "2.1", "expected_secondary_spki": ident["server_spki"],
        "primary_id": "p1"}, data_dir=str(tmp_path / "s"))
    code = interserver_auth.open_pairing_window(conn)
    comp = interserver_api.pair_complete(conn, handles, peer, {
        "handle": begin["handle"], "code": code, "protocol_version": "2.1"})
    return ctx, peer, comp["token"]


def _creds(token):
    return {"token": token, "protocol_version": "2.1"}


# --- קצה-לקצה: האימג' נוחת בראשי אחרי אימות sha256 --------------------------

def test_pull_end_to_end_lands_verified_copy(server, secondary, paired):
    write_image(secondary["root"], PULL)
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID})
    assert resp.status_code == 200, resp.text
    tid = resp.json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "done", row
    assert row["direction"] == "pull"
    assert row["bytes_sent"] == row["bytes_total"] > 0
    assert row["error"] is None

    got = server["ctx"].library.scan()
    assert PULL_ID in got
    src = secondary["root"] / PULL_ID
    dst = Path(got[PULL_ID]["_dir"])
    assert (dst / "p1.esp.pcl.zst").read_bytes() == (src / "p1.esp.pcl.zst").read_bytes()
    assert (dst / "p3.win.pcl.zst").read_bytes() == (src / "p3.win.pcl.zst").read_bytes()
    assert json.loads((dst / "manifest.json").read_text(encoding="utf-8")) == \
        json.loads((src / "manifest.json").read_text(encoding="utf-8"))
    leftover = _incoming(server) / PULL_ID
    assert not leftover.exists()
    events = [r["event"] for r in server["ctx"].conn.execute(
        "SELECT event FROM journal WHERE event LIKE 'storage_transfer_%'")]
    assert events == ["storage_transfer_start", "storage_transfer_done"]
    listed = admin.get(f"/api/console/storage-nodes/{paired}/images").json()
    assert listed["connected"] is True
    assert any(i["id"] == PULL_ID for i in listed["images"])


def test_forged_sha_on_secondary_is_refused_and_leaves_no_incoming(
        server, secondary, paired):
    write_image(secondary["root"], PULL)
    (secondary["root"] / PULL_ID / "p1.esp.pcl.zst").write_bytes(b"X" * 20)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL_ID}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed", row
    assert "sha256" in row["error"]
    assert PULL_ID not in server["ctx"].library.scan()
    leftover = _incoming(server) / PULL_ID
    assert not leftover.exists()
    parent = _incoming(server)
    assert (not parent.exists()) or PULL_ID not in {p.name for p in parent.iterdir()}


def test_existing_id_on_primary_is_409_not_overwritten(server, secondary, paired):
    write_image(secondary["root"], MANIFEST_256)
    marker = Path(server["ctx"].library.get(IMAGE)["_dir"]) / "p1.esp.pcl.zst"
    marker.write_bytes(b"DO-NOT-CLOBBER")
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": IMAGE})
    assert resp.status_code == 409, resp.text
    assert "כבר קיים" in resp.text
    assert marker.read_bytes() == b"DO-NOT-CLOBBER"


def test_file_range_resume_completes_after_disconnect(server, secondary, paired,
                                                      tmp_path, monkeypatch):
    write_image(secondary["root"], PULL)
    real = storage_client.PinnedMTLSClient.get_stream
    calls = {"n": 0}

    def flaky(self, path, token, dest, **kwargs):
        dest = Path(dest)
        if "/files/" in path and calls["n"] == 0:
            calls["n"] += 1
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(PARTITION_BYTES[:8])
            raise storage_client.InterserverClientError("נותק אחרי 8 בייטים")
        return real(self, path, token, dest, **kwargs)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", flaky)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL_ID}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed"
    leftover = _incoming(server) / PULL_ID
    assert leftover.exists()
    first = leftover / "p1.esp.pcl.zst"
    assert first.read_bytes() == PARTITION_BYTES[:8]

    sizes = []

    def tracking(self, path, token, dest, **kwargs):
        dest = Path(dest)
        sizes.append((path, dest.stat().st_size if dest.is_file() else 0))
        return real(self, path, token, dest, **kwargs)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", tracking)
    tid2 = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID}).json()["id"]
    row2 = _wait(admin, paired, tid2)
    assert row2["state"] == "done", row2
    assert any(sz == 8 for _path, sz in sizes), sizes
    got = Path(server["ctx"].library.get(PULL_ID)["_dir"])
    assert (got / "p1.esp.pcl.zst").read_bytes() == PARTITION_BYTES
    assert hashlib.sha256(PARTITION_BYTES).hexdigest() == \
        server["ctx"].library.get(PULL_ID)["partitions"][0]["sha256"]


def test_get_stream_honors_range_against_the_terminator(server, secondary, paired,
                                                        tmp_path):
    write_image(secondary["root"], PULL)
    node = storage_nodes.node_row(server["ctx"].conn, paired)
    client, token = storage_nodes.node_client(
        server["ctx"].conn, tmp_path / "data", node)
    dest = tmp_path / "partial"
    dest.write_bytes(PARTITION_BYTES[:5])
    with client:
        n = client.get_stream(f"/images/{PULL_ID}/files/p1.esp.pcl.zst", token, dest)
    assert dest.read_bytes() == PARTITION_BYTES
    assert n == len(PARTITION_BYTES)


def test_files_name_outside_manifest_is_404(paired_ctx):
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_file_plan(
            ctx, peer, image_id=PULL_ID, filename="not-in-manifest.bin",
            **_creds(token))
    assert e.value.status == 404
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_file_plan(
            ctx, peer, image_id=PULL_ID, filename="../evil",
            **_creds(token))
    assert e.value.status == 404


def test_secondary_exposes_no_new_post_or_put(paired_ctx):
    if TestClient is None:
        pytest.skip("fastapi required")
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    app = interserver_api.create_interserver_app(ctx, lambda _req: peer)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {token}", "ImageCtl-Protocol-Version": "2.1"}
    listed = client.get("/api/interserver/v1/images", headers=headers)
    assert listed.status_code == 200, listed.text
    assert any(i["id"] == PULL_ID for i in listed.json())
    man = client.get(f"/api/interserver/v1/images/{PULL_ID}/manifest", headers=headers)
    assert man.status_code == 200 and man.json()["id"] == PULL_ID
    body = client.get(f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst",
                      headers=headers)
    assert body.status_code == 200 and body.content == PARTITION_BYTES
    ranged = client.get(
        f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst",
        headers={**headers, "Range": "bytes=5-"})
    assert ranged.status_code == 206
    assert ranged.content == PARTITION_BYTES[5:]
    missing = client.get(
        f"/api/interserver/v1/images/{PULL_ID}/files/nope.bin", headers=headers)
    assert missing.status_code == 404
    for method, path in (
        ("post", "/api/interserver/v1/images"),
        ("put", "/api/interserver/v1/images"),
        ("post", f"/api/interserver/v1/images/{PULL_ID}/manifest"),
        ("put", f"/api/interserver/v1/images/{PULL_ID}/manifest"),
        ("post", f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst"),
        ("put", f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst"),
    ):
        resp = getattr(client, method)(path, headers=headers)
        assert resp.status_code in (404, 405), (method, path, resp.status_code, resp.text)


def test_pull_route_guards(server, paired):
    admin, deploy = server["admin"], server["deploy"]
    path = f"/api/console/storage-nodes/{paired}/pull"
    assert deploy.post(path, json={"image_id": PULL_ID}).status_code == 403
    assert deploy.get(f"/api/console/storage-nodes/{paired}/images").status_code == 403
    assert admin.post("/api/console/storage-nodes/nope/pull",
                      json={"image_id": PULL_ID}).status_code == 404
    assert admin.post(path, json={"image_id": "not-an-id"}).status_code == 400


def test_pull_refused_on_a_secondary_server(server, paired):
    db.set_setting(server["ctx"].conn, storage_nodes.ROLE_KEY, "secondary")
    resp = server["admin"].post(f"/api/console/storage-nodes/{paired}/pull",
                                json={"image_id": PULL_ID})
    assert resp.status_code == 409
    with pytest.raises(storage_nodes.NodeManagementForbidden):
        storage_transfer.start_pull(server["ctx"], "x", paired, PULL_ID,
                                    ("noc", "admin"), run_in_thread=False)


def test_cancel_push_is_refused(server, paired, tmp_path):
    tid = storage_transfer.start_transfer(
        server["ctx"], tmp_path / "data", paired, IMAGE, ("noc", "admin"),
        run_in_thread=False)
    with pytest.raises(storage_transfer.TransferError) as e:
        storage_transfer.cancel_transfer(server["ctx"].conn, tid, ("noc", "admin"))
    assert e.value.status == 409


def test_list_images_and_manifest_from_disk(paired_ctx):
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    rows = interserver_api.list_images(ctx, peer, **_creds(token))
    assert len(rows) == 1
    assert rows[0]["id"] == PULL_ID
    assert rows[0]["name"] == "Haifa Local"
    assert rows[0]["size_bytes"] == len(PARTITION_BYTES) * 2
    assert "p1.esp.pcl.zst" in rows[0]["sha256"]
    man = interserver_api.image_manifest(
        ctx, peer, image_id=PULL_ID, **_creds(token))
    assert man["id"] == PULL_ID and "_dir" not in man


# --- #1456: חידוש אחרי ניתוק בקובץ השלישי ------------------------------------
#
# קובץ שכבר שלם שולח ``Range: bytes=<size>-``; המשני עונה 416 וסוגר את
# החיבור. הלקוח המשיך על החיבור הסגור, והקובץ הבא נכשל בכל "נסה שוב".
# הטסט למעלה מנתק בקובץ הראשון — ולכן לא ראה את זה.

PULL3 = {
    **PULL,
    "id": "img_c0ffe3",
    "partitions": [
        MANIFEST_256["partitions"][0],
        {
            "index": 2, "type_guid": "E3C9E316-0B5C-4DB8-817D-F92DF00215AE",
            "role": "msr", "fs": "", "start_sector": 206848,
            "size_bytes": 16777216, "used_bytes": 16777216,
            "file": "p2.msr.pcl.zst", "sha256": "dd" * 32, "expandable": False,
        },
        MANIFEST_256["partitions"][1],
    ],
}
PULL3_ID = PULL3["id"]
PULL3_FILES = ["p1.esp.pcl.zst", "p2.msr.pcl.zst", "p3.win.pcl.zst"]


def test_resume_after_drop_in_third_file_completes_and_verifies(
        server, secondary, paired, monkeypatch):
    write_image(secondary["root"], PULL3)
    real = storage_client.PinnedMTLSClient.get_stream

    def drop_in_third(self, path, token, dest, **kwargs):
        dest = Path(dest)
        if path.endswith("/files/p3.win.pcl.zst"):
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(PARTITION_BYTES[:8])
            raise storage_client.InterserverClientError("נותק אחרי 8 בייטים")
        return real(self, path, token, dest, **kwargs)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", drop_in_third)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL3_ID}).json()["id"]
    row = _wait(admin, paired, tid)
    assert row["state"] == "failed", row
    left = _incoming(server) / PULL3_ID
    assert (left / "p1.esp.pcl.zst").read_bytes() == PARTITION_BYTES
    assert (left / "p2.msr.pcl.zst").read_bytes() == PARTITION_BYTES
    assert (left / "p3.win.pcl.zst").read_bytes() == PARTITION_BYTES[:8]

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", real)
    tid2 = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL3_ID}).json()["id"]
    row2 = _wait(admin, paired, tid2)
    assert row2["state"] == "done", row2
    assert row2["bytes_sent"] == row2["bytes_total"] == 3 * len(PARTITION_BYTES)
    landed = server["ctx"].library.get(PULL3_ID)
    got = Path(landed["_dir"])
    digest = hashlib.sha256(PARTITION_BYTES).hexdigest()
    for name in PULL3_FILES:
        assert (got / name).read_bytes() == PARTITION_BYTES, name
    assert [p["sha256"] for p in landed["partitions"]] == [digest] * 3
    assert not left.exists()


def _client(server, paired, tmp_path):
    node = storage_nodes.node_row(server["ctx"].conn, paired)
    return storage_nodes.node_client(server["ctx"].conn, tmp_path / "data", node)


def test_416_on_complete_file_reconnects_for_the_next_request(
        server, secondary, paired, tmp_path):
    write_image(secondary["root"], PULL3)
    client, token = _client(server, paired, tmp_path)
    done = tmp_path / "done"
    done.write_bytes(PARTITION_BYTES)
    fresh = tmp_path / "fresh"
    with client:
        n = client.get_stream(f"/images/{PULL3_ID}/files/p1.esp.pcl.zst", token, done)
        m = client.get_stream(f"/images/{PULL3_ID}/files/p2.msr.pcl.zst", token, fresh)
    assert n == m == len(PARTITION_BYTES)
    assert done.read_bytes() == PARTITION_BYTES
    assert fresh.read_bytes() == PARTITION_BYTES


def test_416_on_a_file_that_is_not_the_secondary_size_is_a_failure(
        server, secondary, paired, tmp_path):
    """עיקרון 5: 416 לבדו אינו ראיה ש"הקובץ שלם". יעד גדול מהקובץ במשני
    — כשל עם שני המספרים, לא "כבר הורד"."""
    write_image(secondary["root"], PULL3)
    client, token = _client(server, paired, tmp_path)
    longer = tmp_path / "longer"
    longer.write_bytes(PARTITION_BYTES + b"EXTRA")
    with client:
        with pytest.raises(storage_client.InterserverClientError) as e:
            client.get_stream(f"/images/{PULL3_ID}/files/p1.esp.pcl.zst", token, longer)
    msg = str(e.value)
    assert str(len(PARTITION_BYTES) + 5) in msg and str(len(PARTITION_BYTES)) in msg
    assert longer.read_bytes() == PARTITION_BYTES + b"EXTRA"


def test_416_without_a_reported_size_is_a_failure(
        server, secondary, paired, tmp_path, monkeypatch):
    """משני שלא אמר מה גודל הקובץ (‏416 בלי ``Content-Range``, משני ישן) —
    לא בדקנו, ולכן לא "שלם"."""
    write_image(secondary["root"], PULL3)

    def bare_416(header, size):
        raise interserver_api.PairError(416, "טווח לא ניתן לסיפוק")

    monkeypatch.setattr(interserver_api, "parse_byte_range", bare_416)
    client, token = _client(server, paired, tmp_path)
    same = tmp_path / "same"
    same.write_bytes(PARTITION_BYTES)
    with client:
        with pytest.raises(storage_client.InterserverClientError) as e:
            client.get_stream(f"/images/{PULL3_ID}/files/p1.esp.pcl.zst", token, same)
    assert "Content-Range" in str(e.value)


@pytest.mark.parametrize("via", ["tls", "fastapi"])
def test_secondary_416_reports_the_file_size(paired_ctx, via):
    """RFC 9110 §15.5.17: ‏416 נושא ``Content-Range: bytes */<size>``."""
    ctx, peer, token = paired_ctx
    write_image(Path(ctx.library.root), PULL)
    headers = {"Authorization": f"Bearer {token}",
               "ImageCtl-Protocol-Version": "2.1",
               "Range": f"bytes={len(PARTITION_BYTES)}-"}
    path = f"/api/interserver/v1/images/{PULL_ID}/files/p1.esp.pcl.zst"
    if via == "fastapi":
        if TestClient is None:
            pytest.skip("fastapi required")
        app = interserver_api.create_interserver_app(ctx, lambda _req: peer)
        resp = TestClient(app).get(path, headers=headers)
        assert resp.status_code == 416
        assert resp.headers["content-range"] == f"bytes */{len(PARTITION_BYTES)}"
        return
    with pytest.raises(interserver_api.PairError) as e:
        interserver_api.image_file_plan(
            ctx, peer, image_id=PULL_ID, filename="p1.esp.pcl.zst",
            range_header=headers["Range"], **_creds(token))
    assert e.value.status == 416
    raw = interserver_api._http_response(e.value.status, e.value.payload(),
                                         close=True, headers=e.value.headers)
    assert f"\r\nContent-Range: bytes */{len(PARTITION_BYTES)}\r\n".encode() in raw


# --- #1458: מקום פנוי בראשי לפני pull ----------------------------------------

def _pull_with_free(server, paired, monkeypatch, free):
    def fake_free(path):
        if isinstance(free, Exception):
            raise free
        return free() if callable(free) else free

    monkeypatch.setattr(storage_transfer, "_free_bytes", fake_free)
    admin = server["admin"]
    tid = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                     json={"image_id": PULL3_ID}).json()["id"]
    return _wait(admin, paired, tid)


def _pull_need(already=0):
    # ‏#1737: ועוד הרזרבה למערכת (`disk_reserve`).
    return (3 * len(PARTITION_BYTES) - already + storage_transfer.PULL_FREE_SPACE_MARGIN
            + disk_reserve.SYSTEM_RESERVE_BYTES)


@pytest.mark.system_reserve   # #1737: the real 10GiB (conftest _host_free_space)
def test_pull_refused_up_front_when_free_space_is_one_byte_short(
        server, secondary, paired, monkeypatch):
    write_image(secondary["root"], PULL3)
    need = _pull_need()
    row = _pull_with_free(server, paired, monkeypatch, need - 1)
    assert row["state"] == "failed", row
    assert "מקום" in row["error"]
    assert str(need) in row["error"] and str(need - 1) in row["error"]
    assert row["bytes_sent"] == 0
    assert not (_incoming(server) / PULL3_ID).exists()
    assert PULL3_ID not in server["ctx"].library.scan()


@pytest.mark.system_reserve   # #1737: the real 10GiB (conftest _host_free_space)
def test_pull_proceeds_when_free_space_is_exactly_enough(
        server, secondary, paired, monkeypatch):
    write_image(secondary["root"], PULL3)
    row = _pull_with_free(server, paired, monkeypatch, _pull_need())
    assert row["state"] == "done", row
    assert PULL3_ID in server["ctx"].library.scan()


@pytest.mark.system_reserve   # #1737: the real 10GiB (conftest _host_free_space)
def test_pull_resume_needs_only_the_remainder(server, secondary, paired, monkeypatch):
    """בחידוש, מה שכבר ב-``.incoming`` כבר תפוס — דרישת המקום המלאה הייתה
    חוסמת בדיוק את החידוש שחוסך את ההורדה מחדש."""
    write_image(secondary["root"], PULL3)
    left = _incoming(server) / PULL3_ID
    left.mkdir(parents=True)
    (left / "p1.esp.pcl.zst").write_bytes(PARTITION_BYTES)
    (left / "p2.msr.pcl.zst").write_bytes(PARTITION_BYTES[:8])
    already = len(PARTITION_BYTES) + 8
    short = _pull_with_free(server, paired, monkeypatch, _pull_need(already) - 1)
    assert short["state"] == "failed", short
    assert str(_pull_need(already)) in short["error"]
    row = _pull_with_free(server, paired, monkeypatch, _pull_need(already))
    assert row["state"] == "done", row


def test_pull_fails_when_free_space_cannot_be_read(
        server, secondary, paired, monkeypatch):
    write_image(secondary["root"], PULL3)
    row = _pull_with_free(server, paired, monkeypatch, OSError("EIO"))
    assert row["state"] == "failed", row
    assert "מקום" in row["error"] and "EIO" in row["error"]
    assert not (_incoming(server) / PULL3_ID).exists()


@pytest.mark.system_reserve   # #1737: the real 10GiB (conftest _host_free_space)
def test_pull_stops_at_the_system_reserve_when_the_disk_fills_mid_transfer(
        server, secondary, paired, monkeypatch):
    """‏#1737: הבדיקה המוקדמת עברה, ואז הדיסק התמלא (קליטה במקביל). ההורדה נעצרת
    ברזרבה, בשמה — לא ממשיכה עד ENOSPC, והאימג' לא נכנס לספרייה."""
    write_image(secondary["root"], PULL3)
    calls = {"n": 0}

    def shrinking():
        calls["n"] += 1
        return _pull_need() if calls["n"] == 1 else disk_reserve.SYSTEM_RESERVE_BYTES - 1

    monkeypatch.setattr(disk_reserve, "STREAM_CHECK_EVERY", 1)
    row = _pull_with_free(server, paired, monkeypatch, shrinking)
    assert row["state"] == "failed", row
    assert "נעצרה" in row["error"] and "נשמרים למערכת" in row["error"], row["error"]
    assert calls["n"] >= 2
    assert PULL3_ID not in server["ctx"].library.scan()


def test_pull_fails_when_secondary_reports_no_size(
        server, secondary, paired, monkeypatch):
    write_image(secondary["root"], PULL3)
    real = interserver_api.list_images

    def sizeless(*args, **kwargs):
        return [{**row, "size_bytes": None} for row in real(*args, **kwargs)]

    monkeypatch.setattr(interserver_api, "list_images", sizeless)
    row = _pull_with_free(server, paired, monkeypatch, 10 ** 15)
    assert row["state"] == "failed", row
    assert "size_bytes" in row["error"]
    assert not (_incoming(server) / PULL3_ID).exists()


# --- #1340: מניפסט של משני שנפרץ אינו קובע איפה הראשי כותב ---------------------

@pytest.mark.parametrize("evil", ["../../escape.bin", "../escape.bin", "sub/p1.bin"])
def test_secondary_manifest_cannot_write_outside_incoming(
        server, secondary, paired, monkeypatch, evil):
    """משני שנפרץ (סניף) מחזיר מניפסט ש-``file`` בו יוצא מ-``.incoming``.
    הראשי רץ כ-root ו-``/etc`` ב-``ReadWritePaths`` — כתיבה כזו היא הרצת קוד
    בראשי. ה-sha256 נבדק רק **אחרי** הכתיבה ומנקה רק את ``.incoming``, ולכן
    השומר חייב לשבת לפני ``get_stream``: אף קובץ אינו נפתח מחוץ ל-``.incoming``,
    וההעברה נכשלת בגלוי."""
    write_image(secondary["root"], PULL)
    real_get_json = storage_client.PinnedMTLSClient.get_json

    def tampered(self, path, token, *a, **kw):
        answer = real_get_json(self, path, token, *a, **kw)
        if path.endswith("/manifest") and isinstance(answer, dict):
            answer = json.loads(json.dumps(answer))
            answer["partitions"][0]["file"] = evil
        return answer

    dests: list[Path] = []
    real_stream = storage_client.PinnedMTLSClient.get_stream

    def recording(self, path, token, dest, **kw):
        dests.append(Path(dest))
        return real_stream(self, path, token, dest, **kw)

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_json", tampered)
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", recording)
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID})
    assert resp.status_code == 200, resp.text
    row = _wait(admin, paired, resp.json()["id"])
    assert row["state"] == "failed", row
    incoming = (_incoming(server) / PULL_ID).resolve()
    outside = [d for d in dests if d.resolve().parent != incoming]
    assert not outside, f"נכתב מחוץ ל-.incoming: {outside}"
    root = Path(server["ctx"].library.root)
    assert not (root / "escape.bin").exists()
    assert not (root.parent / "escape.bin").exists()
    assert PULL_ID not in server["ctx"].library.scan()


def test_a_secondary_manifest_that_fails_validate_is_refused_before_any_byte(
        server, secondary, paired, monkeypatch):
    """‏#1627, החצי השני של "גמור": מניפסט המשני עובר `ImageLibrary._validate`
    **לפני** ההורדה. שמות הקבצים כאן תקינים — רק `family` פסול — ולכן השומר
    על `file` לבדו אינו עוצר אותו; בלי `_validate` ההורדה מתחילה."""
    write_image(secondary["root"], PULL)
    real_get_json = storage_client.PinnedMTLSClient.get_json

    def tampered(self, path, token, *a, **kw):
        answer = real_get_json(self, path, token, *a, **kw)
        if path.endswith("/manifest") and isinstance(answer, dict):
            answer = {**answer, "family": 999}
        return answer

    dests: list[Path] = []

    def recording(self, path, token, dest, **kw):
        dests.append(Path(dest))
        raise AssertionError("get_stream before the manifest was validated")

    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_json", tampered)
    monkeypatch.setattr(storage_client.PinnedMTLSClient, "get_stream", recording)
    admin = server["admin"]
    resp = admin.post(f"/api/console/storage-nodes/{paired}/pull",
                      json={"image_id": PULL_ID})
    assert resp.status_code == 200, resp.text
    row = _wait(admin, paired, resp.json()["id"])
    assert row["state"] == "failed", row
    assert "מניפסט המשני נפסל" in row["error"] and "family" in row["error"], row["error"]
    assert dests == []
    assert PULL_ID not in server["ctx"].library.scan()
