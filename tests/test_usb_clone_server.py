"""#1914: the clone stick's download from the console -- admin only.

deploy -> 403, anonymous -> 401, on all three paths. A file without a matching
sha256 line and manifest is "not available" with the reason, never a download
of bytes nothing vouches for (principles 5 and 6).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

PATHS = ("/api/console/usb-clone", "/api/console/usb-clone/download", "/api/console/usb-clone/sha256")


@pytest.fixture()
def usb_server(tmp_path: Path, images_root: Path, clock):
    from server.console import users
    from server.app import create_app

    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080", now_fn=clock)
    users.create(app.state.ctx.conn, "noc", "admin-pass-123", "admin", by="test", is_builtin=True, check_policy=False)
    users.create(app.state.ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test", check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login", json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login", json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    return {"admin": admin, "deploy": deploy, "anon": TestClient(app), "data_dir": tmp_path / "data"}


def publish(data_dir: Path, body: bytes = b"\x00" * 4096 + b"IMGSRV_USB", *, sha: str | None = None,
            size: int | None = None) -> str:
    """What tools/build_usb_clone.sh --publish leaves in <data_dir>/usb/."""
    d = data_dir / "usb"
    d.mkdir(parents=True, exist_ok=True)
    (d / "imagesrv-usb.img").write_bytes(body)
    real = hashlib.sha256(body).hexdigest()
    name = "imagesrv-usb-v0.77.3.img"
    (d / "imagesrv-usb.img.sha256").write_text(f"{sha or real}  {name}\n", encoding="utf-8", newline="\n")
    (d / "imagesrv-usb.json").write_text(json.dumps({"version": "v0.77.3", "sha256": sha or real,
                                                     "size": len(body) if size is None else size,
                                                     "built_at": "2026-10-20T12:00:00Z", "bios": True}),
                                         encoding="utf-8")
    return real


@pytest.mark.parametrize("path", PATHS)
def test_deploy_gets_403_and_anonymous_401(usb_server, path):
    publish(usb_server["data_dir"])
    assert usb_server["deploy"].get(path).status_code == 403
    assert usb_server["anon"].get(path).status_code == 401


def test_admin_downloads_the_file_and_its_sha256(usb_server):
    real = publish(usb_server["data_dir"])
    st = usb_server["admin"].get("/api/console/usb-clone").json()
    assert st["available"] is True and st["sha256"] == real and st["version"] == "v0.77.3"
    r = usb_server["admin"].get("/api/console/usb-clone/download")
    assert r.status_code == 200
    assert hashlib.sha256(r.content).hexdigest() == real
    assert "imagesrv-usb-v0.77.3.img" in r.headers["content-disposition"]
    s = usb_server["admin"].get("/api/console/usb-clone/sha256")
    assert s.status_code == 200 and s.text == f"{real}  imagesrv-usb-v0.77.3.img\n"


def test_not_built_is_said_and_nothing_is_served(usb_server):
    st = usb_server["admin"].get("/api/console/usb-clone").json()
    assert st == {"available": False, "reason": "לא נבנה בשרת הזה", "build": st["build"]}
    assert usb_server["admin"].get("/api/console/usb-clone/download").status_code == 404


@pytest.mark.parametrize("kw", [{"sha": "f" * 64}, {"size": 1}, {"sha": "not-a-sha"}])
def test_a_file_its_manifest_does_not_vouch_for_is_not_served(usb_server, kw):
    publish(usb_server["data_dir"], **kw)
    st = usb_server["admin"].get("/api/console/usb-clone").json()
    assert st["available"] is False and st["reason"]
    r = usb_server["admin"].get("/api/console/usb-clone/download")
    assert r.status_code == 404 and r.json()["code"] == "usb_unavailable"
