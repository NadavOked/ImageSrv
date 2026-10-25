"""מיקומי אחסון (#1066 שלב א') — כל hook מזויף, אף כלי מערכת לא רץ."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from conftest import MANIFEST_256, setup_classroom, write_image
from server import storage_locations as sl
from server.storage_locations import FAILED, OK, UNCHECKED, HookResult

IQN = "iqn.2005-10.org.freenas.ctl:images"
PORTAL = "10.44.3.75:3260"


class FakeHooks:
    """כל כלי המערכת — רשימות, מונים, ושלושה מצבים. לא subprocess."""

    def __init__(self):
        self.nfs_items = [{"export": "/mnt/pool/images", "clients": "10.44.10.0/24 · rw"}]
        self.smb_items = [{"share": "images", "type": "Disk"}]
        self.iscsi_items = [{"portal": PORTAL, "iqn": IQN}]
        self.scan_status = OK
        self.scan_reason = "showmount: command not found"
        self.mount_status = OK
        self.mount_reason = "access denied"
        self.mount_fail_substr = ""      # #1123: כשל רק בעיגון שהיעד מכיל את זה
        self.fstab_status = OK           # #1123: כשל בכתיבת fstab
        self.login_status = OK
        self.login_reason = "login failed"
        self.make_fs_status = OK
        self.disk_status = "empty"
        self.disk_reason = "Input/output error"
        self.device = "/dev/sdb"
        self.fs_type = "ext4"
        self.fs_label = "images"
        self.fs_size = 800_000_000_000
        self.free = 1_800_000_000_000
        self.total = 4_000_000_000_000
        self.fail_targets: set[str] = set()
        self.interfaces = [
            {"name": "ens19", "state": "up", "addresses": ["10.44.12.10/24"]},
        ]
        self.mounts: list[tuple] = []
        self.umounts: list[str] = []
        self.mounted: set[str] = set()   # #1994: מה שבאמת מעוגן — לא "התיקייה קיימת"
        self.fstab: list[list] = []
        self.make_fs_calls: list[tuple] = []
        self.logins: list[tuple] = []
        self.logouts: list[tuple] = []
        self.probes: list[str] = []
        self.creds_writes: list[tuple] = []
        self.creds_removes: list[str] = []
        self.calls: list[str] = []       # #1123: סדר הקריאות (fstab לפני mount)

    def nfs_scan(self, server):
        if self.scan_status != OK:
            return HookResult(self.scan_status, self.scan_reason)
        return HookResult(OK, payload={"items": list(self.nfs_items)})

    def smb_scan(self, server, creds=None):
        del creds
        if self.scan_status != OK:
            return HookResult(self.scan_status, self.scan_reason)
        return HookResult(OK, payload={"items": list(self.smb_items)})

    def iscsi_discover(self, portal, chap=None):
        del chap
        if self.scan_status != OK:
            return HookResult(self.scan_status, self.scan_reason)
        return HookResult(OK, payload={"items": list(self.iscsi_items)})

    def iscsi_login(self, iqn, portal, chap=None, auto=True):
        self.logins.append((iqn, portal, chap, auto))
        if self.login_status != OK:
            return HookResult(self.login_status, self.login_reason)
        return HookResult(OK, payload={"device_by_path": self.device, "iqn": iqn,
                                       "portal": portal})

    def iscsi_logout(self, iqn, portal, chap=None, auto=False):
        self.logouts.append((iqn, portal))
        return HookResult(OK)

    def disk_probe(self, device):
        self.probes.append(device)
        if self.disk_status == "unknown":
            return HookResult(UNCHECKED, self.disk_reason,
                              {"device": device, "disk_status": "unknown",
                               "fs_size": self.fs_size})
        if self.disk_status == "fs":
            return HookResult(OK, payload={
                "device": device, "disk_status": "fs",
                "fs_type": self.fs_type, "fs_label": self.fs_label,
                "fs_size": self.fs_size,
            })
        return HookResult(OK, payload={
            "device": device, "disk_status": "empty",
            "fs_type": None, "fs_label": None, "fs_size": self.fs_size,
        })

    def make_fs(self, device, label=""):
        self.make_fs_calls.append((device, label))
        if self.make_fs_status != OK:
            return HookResult(self.make_fs_status, "mkfs failed")
        return HookResult(OK)

    def mount(self, fstype, source, target, opts=""):
        self.mounts.append((fstype, source, target, opts))
        self.calls.append("mount:" + target)
        if self.mount_status != OK:
            return HookResult(self.mount_status, self.mount_reason)
        if self.mount_fail_substr and self.mount_fail_substr in target:
            return HookResult(FAILED, self.mount_reason)
        Path(target).mkdir(parents=True, exist_ok=True)
        self.mounted.add(str(Path(target)))
        return HookResult(OK)

    def umount(self, target):
        self.umounts.append(target)
        self.mounted.discard(str(Path(target)))
        return HookResult(OK)

    def is_mount(self, target):
        return str(Path(target)) in self.mounted

    def fstab_write(self, entries):
        self.fstab.append(list(entries))
        self.calls.append("fstab")
        if self.fstab_status != OK:
            return HookResult(self.fstab_status, "Read-only file system")
        return HookResult(OK, payload={"entries": len(entries)})

    def creds_write(self, path, creds):
        self.creds_writes.append((path, dict(creds)))
        return HookResult(OK, payload={"path": path})

    def creds_remove(self, path):
        self.creds_removes.append(path)
        return HookResult(OK)

    def df(self, target):
        if target in self.fail_targets:
            return HookResult(FAILED, "no route to 10.44.3.75:3260")
        return HookResult(OK, payload={"free_bytes": self.free, "total_bytes": self.total})

    def as_dict(self) -> dict:
        return {
            "nfs_scan": self.nfs_scan,
            "smb_scan": self.smb_scan,
            "iscsi_discover": self.iscsi_discover,
            "iscsi_login": self.iscsi_login,
            "iscsi_logout": self.iscsi_logout,
            "disk_probe": self.disk_probe,
            "make_fs": self.make_fs,
            "mount": self.mount,
            "umount": self.umount,
            "is_mount": self.is_mount,
            "fstab_write": self.fstab_write,
            "creds_write": self.creds_write,
            "creds_remove": self.creds_remove,
            "df": self.df,
            "interfaces": lambda: list(self.interfaces),
        }


@pytest.fixture()
def env(tmp_path: Path, images_root: Path, clock, monkeypatch):
    from server import sender as sender_module
    from server import users
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    fake = FakeHooks()
    tftp = tmp_path / "tftp"
    (tftp / "grub").mkdir(parents=True)
    for name in ("bootx64.efi", "grubx64.efi", "grub/grub.cfg"):
        (tftp / name).write_bytes(b"x")
    health_hooks = {
        "ss": lambda: "",
        "unit_active": lambda name: "",
        "http_get": lambda url: 200,
        "http_size": lambda url: (200, 9_000_000),
        "interfaces": lambda: list(fake.interfaces),
        "tftp_root": lambda: tftp,
        "udp_sender_pids": lambda: [],
    }
    app = create_app(
        tmp_path / "data", images_root, "http://10.44.12.10:8080",
        now_fn=clock, sender_runner=Recorder(block=True),
        storage_hooks=fake.as_dict(),
        storage_check_interval=10**9,
        health_hooks=health_hooks,
    )
    ctx = app.state.ctx
    users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                 is_builtin=True, check_policy=False)
    users.create(ctx.conn, "labtech", "deploy-pass-1", "deploy", by="test",
                 check_policy=False)
    admin, deploy = TestClient(app), TestClient(app)
    assert admin.post("/api/console/login",
                      json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    assert deploy.post("/api/console/login",
                       json={"username": "labtech", "password": "deploy-pass-1"}).status_code == 200
    yield {"app": app, "ctx": ctx, "admin": admin, "deploy": deploy,
           "fake": fake, "images_root": images_root, "data": tmp_path / "data"}
    ctx.sender.stop()


def _nfs_params():
    return {"server": "10.44.10.20", "export": "/mnt/pool/images",
            "version": "4.1", "readonly": False}


def _iscsi_params():
    return {"portal": PORTAL, "iqn": IQN, "auto": True}


def _create_nfs(env, name="nas-images"):
    return env["admin"].post("/api/console/storage-locations", json={
        "name": name, "type": "nfs", "params": _nfs_params(), "tested": True,
    })


def _create_iscsi(env, name="san-lun1"):
    return env["admin"].post("/api/console/storage-locations", json={
        "name": name, "type": "iscsi", "params": _iscsi_params(), "tested": True,
    })


# --- רשימה מובנית -----------------------------------------------------------

def test_local_location_exists_and_is_not_removable(env):
    body = env["admin"].get("/api/console/storage-locations").json()
    assert body["locations"][0]["id"] == "loc_local"
    assert body["locations"][0]["name"] == "מקומי"
    assert body["locations"][0]["type"] == "local"
    assert body["locations"][0]["removable"] is False
    assert body["locations"][0]["state"] in ("connected", "unchecked")
    assert body["summary"]["locations"] >= 1


def test_deploy_gets_403(env):
    assert env["deploy"].get("/api/console/storage-locations").status_code == 403
    assert env["deploy"].post("/api/console/storage-locations/test", json={
        "type": "nfs", "params": _nfs_params(),
    }).status_code == 403
    assert env["deploy"].post("/api/console/storage-locations", json={
        "name": "x", "type": "nfs", "params": _nfs_params(), "tested": True,
    }).status_code == 403


def test_deploy_gets_403_on_every_endpoint_the_console_page_calls(env):
    """‏#1066 שלב ב': הדף הוא admin בלבד (`data-admin` בעץ), וכל נתיב שהוא
    קורא — לא רק list/test/create — מסרב ל-deploy ב-403 (לא 404/409: ההרשאה
    נבדקת לפני קיום השורה)."""
    base = "/api/console/storage-locations"
    calls = [
        ("POST", base + "/scan", {"type": "nfs", "server": "10.44.10.20"}),
        ("POST", base + "/loc_local/check", {}),
        ("POST", base + "/loc_local/connect", {}),
        ("POST", base + "/loc_local/disconnect", {}),
        ("POST", base + "/loc_local/iscsi-login", {}),
        ("GET", base + "/loc_local/disk", None),
        ("POST", base + "/loc_local/mount", {"format": False}),
        ("POST", base + "/loc_local/format", {"confirm": "x"}),
        ("DELETE", base + "/loc_local", {"confirm": "מקומי"}),
        ("POST", base + "/loc_missing/check", {}),
    ]
    for method, url, body in calls:
        r = env["deploy"].request(method, url, json=body)
        assert r.status_code == 403, (method, url, r.status_code)
    assert env["admin"].post(base + "/loc_missing/check").status_code == 404, "admin רואה 404 — ההבדל הוא ההרשאה"


def test_create_without_test_is_422(env):
    r = env["admin"].post("/api/console/storage-locations", json={
        "name": "nas-images", "type": "nfs", "params": _nfs_params(),
    })
    assert r.status_code == 422
    assert "בדיקת חיבור" in r.json()["detail"]


def test_failed_test_rejects_create(env):
    env["fake"].mount_status = FAILED
    r = env["admin"].post("/api/console/storage-locations", json={
        "name": "nas-images", "type": "nfs", "params": _nfs_params(), "tested": True,
    })
    assert r.status_code == 409
    listed = env["admin"].get("/api/console/storage-locations").json()["locations"]
    assert all(loc["name"] != "nas-images" for loc in listed)


def test_nfs_scan_and_successful_create_mounts_and_writes_fstab(env):
    scanned = env["admin"].post("/api/console/storage-locations/scan", json={
        "type": "nfs", "server": "10.44.10.20",
    }).json()
    assert scanned["ok"] is True
    assert scanned["items"][0]["export"] == "/mnt/pool/images"

    tested = env["admin"].post("/api/console/storage-locations/test", json={
        "type": "nfs", "params": _nfs_params(),
    }).json()
    assert tested["ok"] is True
    assert tested["free_bytes"] == env["fake"].free
    assert tested["warnings"] == []

    created = _create_nfs(env)
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["type"] == "nfs" and body["state"] == "connected"
    assert body["params"].get("secret") is None
    assert any(m[0] == "nfs" for m in env["fake"].mounts)
    assert env["fake"].fstab
    assert any("_netdev" in (e.get("opts") or "") and "nofail" in (e.get("opts") or "")
               for e in env["fake"].fstab[-1])


def test_test_warns_when_storage_shares_the_deploy_nic_subnet(env):
    env["fake"].interfaces = [
        {"name": "ens19", "state": "up", "addresses": ["10.44.12.10/24"]},
    ]
    params = {"server": "10.44.12.50", "export": "/mnt/pool/images", "version": "4.1"}
    tested = env["admin"].post("/api/console/storage-locations/test", json={
        "type": "nfs", "params": params,
    }).json()
    assert tested["ok"] is True
    assert tested["warnings"]
    assert "הפצה" in tested["warnings"][0]


def test_smb_secret_is_not_returned_on_get(env):
    params = {"server": "fs01", "share": "images", "username": "imagectl",
              "secret": "s3cret", "domain": "COLLEGE"}
    created = env["admin"].post("/api/console/storage-locations", json={
        "name": "fileserver-images", "type": "smb", "params": params, "tested": True,
    })
    assert created.status_code == 200, created.text
    assert "s3cret" not in created.text
    assert created.json()["params"].get("secret") is None
    listed = env["admin"].get("/api/console/storage-locations").json()
    row = next(r for r in listed["locations"] if r["name"] == "fileserver-images")
    assert row["params"].get("secret") is None
    assert row["params"]["username"] == "imagectl"


# --- iSCSI: גילוי → login → דיסק → פירמוט/עיגון -----------------------------

def test_iscsi_discover_login_unknown_disk_refuses_format(env):
    created = _create_iscsi(env)
    assert created.status_code == 200, created.text
    loc_id = created.json()["id"]
    login = env["admin"].post(f"/api/console/storage-locations/{loc_id}/iscsi-login")
    assert login.status_code == 200
    assert login.json()["device_by_path"] == "/dev/sdb"
    env["fake"].disk_status = "unknown"
    disk = env["admin"].get(f"/api/console/storage-locations/{loc_id}/disk").json()
    assert disk["disk_status"] == "unknown"
    fmt = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={
        "confirm": IQN,
    })
    assert fmt.status_code == 409
    assert "לא הצלחנו לקרוא" in fmt.json()["detail"]
    assert env["fake"].make_fs_calls == []


def test_empty_disk_format_requires_exact_iqn_then_makes_fs_mounts_fstab(env):
    loc_id = _create_iscsi(env).json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/iscsi-login").status_code == 200
    env["fake"].disk_status = "empty"

    missing = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={})
    assert missing.status_code == 422

    wrong = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={
        "confirm": "iqn.wrong",
    })
    assert wrong.status_code == 403
    assert env["fake"].make_fs_calls == []

    ok = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={
        "confirm": IQN,
    })
    assert ok.status_code == 200, ok.text
    assert env["fake"].make_fs_calls == [("/dev/sdb", "san-lun1")]
    assert any(m[1] == "/dev/sdb" for m in env["fake"].mounts)
    assert env["fake"].fstab
    assert ok.json()["state"] == "connected"


def test_disk_with_fs_mounts_without_format(env):
    loc_id = _create_iscsi(env).json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/iscsi-login").status_code == 200
    env["fake"].disk_status = "fs"
    mounted = env["admin"].post(f"/api/console/storage-locations/{loc_id}/mount", json={
        "format": False,
    })
    assert mounted.status_code == 200, mounted.text
    assert env["fake"].make_fs_calls == []
    assert any(m[1] == "/dev/sdb" for m in env["fake"].mounts)
    assert mounted.json()["state"] == "connected"


def test_format_with_fs_without_wipe_is_409(env):
    loc_id = _create_iscsi(env).json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/iscsi-login").status_code == 200
    env["fake"].disk_status = "fs"
    fmt = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={
        "confirm": IQN,
    })
    assert fmt.status_code == 409
    assert "wipe" in fmt.json()["detail"]
    assert env["fake"].make_fs_calls == []


def test_format_with_fs_and_wipe_and_confirm_formats(env):
    loc_id = _create_iscsi(env).json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/iscsi-login").status_code == 200
    env["fake"].disk_status = "fs"
    fmt = env["admin"].post(f"/api/console/storage-locations/{loc_id}/format", json={
        "confirm": IQN, "wipe": True,
    })
    assert fmt.status_code == 200, fmt.text
    assert env["fake"].make_fs_calls


# --- מנטר 60ש' --------------------------------------------------------------

def test_monitor_connected_to_unreachable_to_connected(env):
    created = _create_nfs(env)
    loc_id = created.json()["id"]
    mount = created.json()["mount_point"]
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] == "connected"

    env["fake"].fail_targets.add(mount)
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] == "unreachable"
    assert "no route" in row["state_detail"]
    since = row["state_since"]

    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] == "unreachable"
    assert row["state_since"] == since

    env["fake"].fail_targets.clear()
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] == "connected"


def test_disconnected_is_not_polled(env):
    created = _create_nfs(env)
    loc_id = created.json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/disconnect").status_code == 200
    env["fake"].fail_targets.add(created.json()["mount_point"])
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    assert sl.get(env["ctx"].conn, loc_id)["state"] == "disconnected"


# --- #1994: ‏df על נקודת עיגון ריקה מודד את דיסק המערכת ----------------------

def _create_smb(env, name="fileserver-images"):
    return env["admin"].post("/api/console/storage-locations", json={
        "name": name, "type": "smb", "params": _smb_params(), "tested": True,
    })


def _connected_iscsi(env):
    created = _create_iscsi(env)
    loc_id = created.json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/iscsi-login").status_code == 200
    env["fake"].disk_status = "fs"
    return env["admin"].post(f"/api/console/storage-locations/{loc_id}/mount",
                             json={"format": False})


@pytest.mark.parametrize("make", [_create_nfs, _create_smb, _connected_iscsi],
                         ids=["nfs", "smb", "iscsi"])
def test_a_network_location_that_is_not_mounted_is_unreachable_not_connected(env, make):
    """שרת שעלה כשה-NAS כבוי (‏`nofail`): התיקייה הריקה קיימת, ‏df עליה
    "מצליח" ומודד את דיסק המערכת. נקודת עיגון אמיתית היא הראיה — במנטר
    וב"בדוק" כאחד."""
    made = make(env)
    assert made.status_code == 200 and made.json()["state"] == "connected", made.text
    loc_id, mount = made.json()["id"], made.json()["mount_point"]
    fake = env["fake"]

    fake.mounted.discard(str(Path(mount)))               # האתחול עבר בלי ה-NAS
    sl.poll(env["ctx"].conn, env["ctx"].library, fake.as_dict())
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] == "unreachable" and "אינו מעוגן" in row["state_detail"], dict(row)

    checked = env["admin"].post(f"/api/console/storage-locations/{loc_id}/check").json()
    assert checked["state"] == "unreachable"

    fake.mounted.add(str(Path(mount)))                   # ה-NAS חזר ועוגן
    sl.poll(env["ctx"].conn, env["ctx"].library, fake.as_dict())
    assert sl.get(env["ctx"].conn, loc_id)["state"] == "connected"


def test_the_local_location_is_a_folder_and_needs_no_mountpoint(env):
    """‏`loc_local` הוא תיקייה בתוך דיסק המערכת — לא נקודת עיגון, ו-is_mount
    עליו שקרי. הוא נשאר מחובר."""
    assert not env["fake"].is_mount(sl.get(env["ctx"].conn, sl.LOCAL_ID)["mount_point"])
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    assert sl.get(env["ctx"].conn, sl.LOCAL_ID)["state"] == "connected"


# --- אימג' על מיקום לא נגיש -------------------------------------------------

def test_image_on_unreachable_is_unavailable_and_round_is_409(env):
    setup_classroom(env)
    created = _create_nfs(env)
    loc_id = created.json()["id"]
    mount = Path(created.json()["mount_point"])
    images_dir = mount / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    remote = {**MANIFEST_256, "id": "img_aa11bb", "name": "win-build v4"}
    write_image(images_dir, remote)

    listed = env["admin"].get("/api/console/images").json()
    found = next(i for i in listed if i["id"] == "img_aa11bb")
    assert found["location_id"] == loc_id
    assert found["available"] is True

    sl.refresh_catalog(env["ctx"].conn, env["ctx"].library)
    env["fake"].fail_targets.add(str(mount))
    sl.poll(env["ctx"].conn, env["ctx"].library, env["fake"].as_dict())
    assert sl.get(env["ctx"].conn, loc_id)["state"] == "unreachable"

    listed = env["admin"].get("/api/console/images").json()
    found = next(i for i in listed if i["id"] == "img_aa11bb")
    assert found["available"] is False
    local = next(i for i in listed if i["id"] == "img_7f3a91")
    assert local["available"] is True

    opened = env["admin"].post("/api/console/sessions", json={
        "group_id": "grp_LAB1", "image_id": "img_aa11bb",
    })
    assert opened.status_code == 409
    assert "לא זמין" in opened.json()["detail"]


def test_capture_on_unavailable_local_is_409(env):
    setup_classroom(env)
    env["fake"].fail_targets.add(str(env["images_root"]))
    env["admin"].post("/api/console/storage-locations/loc_local/check")
    assert sl.get(env["ctx"].conn, "loc_local")["state"] == "unreachable"
    r = env["admin"].post("/api/console/tasks/capture", json={
        "mac": "b4:2e:99:07:1a:c4", "disk": "sda", "name": "שחזור", "folder": "כיתות",
    })
    assert r.status_code == 409
    assert "זמין" in r.json()["detail"]


# --- הסרה -------------------------------------------------------------------

def test_delete_only_when_disconnected_and_no_images(env):
    created = _create_nfs(env)
    loc_id = created.json()["id"]
    name = created.json()["name"]

    still_up = env["admin"].request("DELETE", f"/api/console/storage-locations/{loc_id}",
                                    json={"confirm": name})
    assert still_up.status_code == 409

    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/disconnect").status_code == 200

    wrong = env["admin"].request("DELETE", f"/api/console/storage-locations/{loc_id}",
                                 json={"confirm": "wrong"})
    assert wrong.status_code == 403

    images_dir = Path(created.json()["mount_point"]) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    write_image(images_dir, {**MANIFEST_256, "id": "img_cc22dd", "name": "keep"})
    # מנותק — הסריקה חיה לא רואה, אבל המטמון מהיצירה ריק. נזין מטמון.
    sl.save_catalog(env["ctx"].conn, loc_id, [{"id": "img_cc22dd", "name": "keep"}])
    has_images = env["admin"].request(
        "DELETE", f"/api/console/storage-locations/{loc_id}", json={"confirm": name})
    assert has_images.status_code == 409

    sl.save_catalog(env["ctx"].conn, loc_id, [])
    gone = env["admin"].request(
        "DELETE", f"/api/console/storage-locations/{loc_id}", json={"confirm": name})
    assert gone.status_code == 200, gone.text

    local = env["admin"].request(
        "DELETE", "/api/console/storage-locations/loc_local", json={"confirm": "מקומי"})
    assert local.status_code == 409


def test_health_has_a_row_per_location(env):
    _create_nfs(env)
    rows = {r["id"]: r for r in env["admin"].get("/api/console/health").json()}
    assert "storage_loc_local" in rows
    nas = next(r for r in rows.values() if r["label"] == "nas-images")
    assert nas["state"] in ("ok", "bad", "off", "unknown")


def test_installer_has_storage_packages_and_unique_initiator():
    text = Path("install/setup-boot-server.sh").read_text(encoding="utf-8")
    for pkg in ("open-iscsi", "nfs-common", "cifs-utils"):
        assert pkg in text, pkg
    assert "initiatorname.iscsi" in text
    assert "iqn.2026-09.imagectl." in text
    assert "machine-id" in text


def test_shared_nic_warning_needs_positive_evidence():
    """אין אזהרה בלי ראיה שה-IP ב-subnet של כרטיס ההפצה."""
    assert sl.shared_nic_warning("10.44.10.20", [], "http://10.44.12.10:8080") is None
    nics = [{"name": "ens19", "addresses": ["10.44.12.10/24"]}]
    msg = sl.shared_nic_warning("10.44.12.50", nics, "http://10.44.12.10:8080")
    assert msg and "10.44.12.50" in msg
    assert sl.shared_nic_warning("10.44.10.20", nics, "http://10.44.12.10:8080") is None


# --- #1123 (1): סיסמת SMB לא ב-argv ולא ב-fstab ------------------------------

def _smb_params():
    return {"server": "fs01", "share": "images", "username": "imagectl",
            "secret": "s3cret", "domain": "COLLEGE"}


def test_smb_secret_goes_to_credentials_file_not_argv_or_fstab(env):
    """הסיסמה נכתבת לקובץ credentials (hook), ו-`mount`/fstab מקבלים
    `credentials=<path>` — לא `password=`. גם בבדיקת החיבור, וגם הקובץ הזמני
    של הבדיקה מוסר. הסרת המיקום מוחקת את הקובץ."""
    created = env["admin"].post("/api/console/storage-locations", json={
        "name": "fileserver-images", "type": "smb", "params": _smb_params(),
        "tested": True,
    })
    assert created.status_code == 200, created.text
    loc_id = created.json()["id"]
    fake = env["fake"]
    cifs_mounts = [m for m in fake.mounts if m[0] == "cifs"]
    assert len(cifs_mounts) == 2, fake.mounts        # בדיקה + קבוע
    for _, _, _, opts in cifs_mounts:
        assert "password=" not in opts and "s3cret" not in opts, opts
        assert "credentials=" in opts, opts
    assert sl.creds_path(loc_id) == f"/etc/imagectl/creds/{loc_id}"
    assert f"credentials={sl.creds_path(loc_id)}" in cifs_mounts[-1][3]
    fstab_text = str(fake.fstab[-1])
    assert "s3cret" not in fstab_text and "password=" not in fstab_text
    assert f"credentials={sl.creds_path(loc_id)}" in fstab_text
    path, creds = fake.creds_writes[-1]
    assert path == sl.creds_path(loc_id)
    assert creds == {"username": "imagectl", "password": "s3cret", "domain": "COLLEGE"}
    test_path = fake.creds_writes[0][0]
    assert test_path != path and test_path in fake.creds_removes
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/disconnect").status_code == 200
    gone = env["admin"].request("DELETE", f"/api/console/storage-locations/{loc_id}",
                                json={"confirm": "fileserver-images"})
    assert gone.status_code == 200, gone.text
    assert path in fake.creds_removes


def test_creds_write_is_0600_in_mount_cifs_format(tmp_path: Path):
    """ה-hook האמיתי: `username=`/`password=`/`domain=`, ‏0600, בלי קובץ זמני."""
    target = tmp_path / "creds" / "loc_ab12cd34"
    result = sl.creds_write(str(target), {"username": "imagectl", "password": "s3cret",
                                          "domain": "COLLEGE"})
    assert result.status == OK, result.reason
    assert target.read_text(encoding="utf-8") == (
        "username=imagectl\npassword=s3cret\ndomain=COLLEGE\n")
    assert not list(target.parent.glob("*.tmp"))
    if os.name == "posix":
        assert (target.stat().st_mode & 0o777) == 0o600
    else:
        assert target.is_file()  # ווינדוס: אין מצב POSIX; ההרשאות נבדקות במעבדה
    assert sl.creds_remove(str(target)).status == OK and not target.exists()
    assert sl.creds_remove(str(target)).status == OK  # כבר איננו — לא כשל


def test_smb_scan_uses_auth_file_not_argv(monkeypatch, tmp_path: Path):
    """‏`smbclient -L` מקבל `-A <קובץ>` ולא `-U user%password`, והקובץ מוסר."""
    seen: list[list[str]] = []

    def fake_run(cmd, timeout=30, input_text=None):
        seen.append(list(cmd))
        text = ("\n\tSharename       Type      Comment\n"
                "\t---------       ----      -------\n\timages          Disk\n")
        return HookResult(OK, payload={"stdout": text, "stderr": ""})

    monkeypatch.setattr(sl, "_run", fake_run)
    monkeypatch.setattr(sl, "CREDS_DIR", tmp_path / "creds")
    result = sl.smb_scan("fs01", {"username": "imagectl", "secret": "s3cret"})
    assert result.status == OK, result.reason
    assert result.payload["items"] == [{"share": "images", "type": "Disk"}]
    cmd = seen[0]
    assert "s3cret" not in " ".join(cmd), cmd
    assert "-A" in cmd
    assert not list((tmp_path / "creds").glob("scan-*"))


# --- #1123 (2): fstab אטומי + mount_point ייחודי ------------------------------

def test_fstab_write_is_atomic(tmp_path: Path, monkeypatch):
    """כתיבה ל-tmp + `os.replace`: כשל ב-replace משאיר את fstab המקורי בשלמותו."""
    fstab = tmp_path / "fstab"
    original = "UUID=abc / ext4 defaults 0 1\n"
    fstab.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sl, "FSTAB_PATH", fstab)
    monkeypatch.setattr(sl, "daemon_reload", lambda: HookResult(OK), raising=False)   # ‏#2122: לא PID 1 של המריץ
    entry = {"source": "10.44.10.20:/mnt/pool/images",
             "target": "/var/lib/imagectl/storage/nas", "fstype": "nfs",
             "opts": "vers=4.1,soft"}
    assert sl.fstab_write([entry]).status == OK
    text = fstab.read_text(encoding="utf-8")
    assert text.startswith(original) and sl.FSTAB_BEGIN in text
    assert "_netdev,nofail" in text
    assert not list(tmp_path.glob("fstab.*")), "לא נשאר קובץ זמני"

    def broken_replace(src, dst):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(sl.os, "replace", broken_replace)
    result = sl.fstab_write([])
    assert result.status == FAILED and "fstab" in result.reason
    assert fstab.read_text(encoding="utf-8") == text, "fstab לא השתנה כשההחלפה נכשלה"


def test_duplicate_mount_point_is_409_and_enforced_in_db(env, tmp_path: Path):
    shared = tmp_path / "shared-images"
    shared.mkdir()
    first = env["admin"].post("/api/console/storage-locations", json={
        "name": "usb-a", "type": "local", "params": {"path": str(shared)},
        "tested": True,
    })
    assert first.status_code == 200, first.text
    second = env["admin"].post("/api/console/storage-locations", json={
        "name": "usb-b", "type": "local", "params": {"path": str(shared)},
        "tested": True,
    })
    assert second.status_code == 409
    assert "usb-a" in second.json()["detail"]
    with pytest.raises(sqlite3.IntegrityError):
        env["ctx"].conn.execute(
            "INSERT INTO storage_locations (id, name, type, mount_point, state,"
            " state_since, created_at)"
            " VALUES ('loc_dup', 'dup', 'local', ?, 'unchecked', 'x', 'x')",
            (str(shared),))
    env["ctx"].conn.rollback()


# --- #1123 (3): validation לשמות רשת ----------------------------------------

@pytest.mark.parametrize("kind,params,word", [
    ("nfs", {"server": "10.44.10.20 -o rw", "export": "/mnt/pool/images"}, "שרת"),
    ("nfs", {"server": "nas..college", "export": "/mnt/pool/images"}, "שרת"),
    ("nfs", {"server": "10.44.10.20", "export": "mnt/pool"}, "ייצוא"),
    ("nfs", {"server": "10.44.10.20", "export": "/mnt/pool,rw"}, "ייצוא"),
    ("nfs", {"server": "10.44.10.20", "export": "/mnt/pool images"}, "ייצוא"),
    ("nfs", {"server": "10.44.10.20", "export": "/mnt/pool", "version": "4.1,rw"}, "גרסת"),
    ("smb", {"server": "fs01", "share": "images rw"}, "שיתוף"),
    ("smb", {"server": "fs01", "share": "images", "username": "a\nb", "secret": "x"}, "משתמש"),
    ("smb", {"server": "fs01", "share": "images", "username": "a", "secret": "x\ny"}, "סיסמה"),
    ("iscsi", {"portal": "10.44.3.75:abc", "iqn": IQN}, "פורטל"),
    ("iscsi", {"portal": "10.44.3.75:3260 x", "iqn": IQN}, "פורטל"),
    ("iscsi", {"portal": PORTAL, "iqn": "iqn.bad"}, "IQN"),
    ("iscsi", {"portal": PORTAL, "iqn": IQN + " x"}, "IQN"),
])
def test_invalid_network_names_are_400_before_any_tool_runs(env, kind, params, word):
    r = env["admin"].post("/api/console/storage-locations/test", json={
        "type": kind, "params": params,
    })
    assert r.status_code == 400, r.text
    assert word in r.json()["detail"], r.json()["detail"]
    c = env["admin"].post("/api/console/storage-locations", json={
        "name": "bad", "type": kind, "params": params, "tested": True,
    })
    assert c.status_code == 400, c.text
    fake = env["fake"]
    assert fake.mounts == [] and fake.logins == [] and fake.creds_writes == []


def test_scan_validates_server_and_portal(env):
    bad = env["admin"].post("/api/console/storage-locations/scan", json={
        "type": "nfs", "server": "10.44.10.20 -e",
    })
    assert bad.status_code == 400 and "שרת" in bad.json()["detail"]
    bad = env["admin"].post("/api/console/storage-locations/scan", json={
        "type": "iscsi", "portal": "10.44.3.75:99999",
    })
    assert bad.status_code == 400 and "פורטל" in bad.json()["detail"]


@pytest.mark.parametrize("kind,params", [
    ("nfs", {"server": "nas01.college.local", "export": "/mnt/pool/images",
             "version": "4.1"}),
    ("nfs", {"server": "fe80::1", "export": "/export"}),
    ("smb", {"server": "10.44.10.21", "share": "images$", "username": "svc.imagectl",
             "secret": "p=ss w0rd!", "domain": "COLLEGE"}),
    ("iscsi", {"portal": "10.44.3.75", "iqn": IQN}),
    ("iscsi", {"portal": "[fd00::75]:3260", "iqn": IQN}),
])
def test_valid_network_names_pass_validation(env, kind, params):
    r = env["admin"].post("/api/console/storage-locations/test", json={
        "type": kind, "params": params,
    })
    assert r.status_code == 200, r.text
    assert r.json()["ok"] is True


def test_validate_params_accepts_eui_and_naa_targets():
    assert sl.validate_params("iscsi", {"portal": PORTAL, "iqn": "eui.02004567A425678D"}) is None
    assert sl.validate_params("iscsi", {"portal": PORTAL,
                                        "iqn": "naa.52004567BA64678D"}) is None
    assert sl.validate_params("iscsi", {"portal": PORTAL, "iqn": "eui.zz"}) is not None


# --- #1123 (4): סדר ביצירה — fstab → mount → connected ------------------------

def test_create_writes_fstab_before_mount_and_is_connected_only_with_both(env):
    created = _create_nfs(env)
    assert created.status_code == 200, created.text
    mount = created.json()["mount_point"]
    calls = env["fake"].calls
    assert calls.index("fstab") < calls.index("mount:" + mount), calls
    assert created.json()["state"] == "connected"


def test_create_network_location_is_unchecked_until_its_mount_succeeds(env):
    """#1123 (4): השורה נולדת `unchecked`, ו-`connected` רק אחרי fstab **וגם**
    mount. הטסט שלמעלה רואה רק את המצב הסופי — שורה שנולדה `connected` עוברת
    אותו, וזה בדיוק מה שמי שקורא את הטבלה בזמן העיגון (סריקה, הקונסולה,
    קריסה באמצע) היה רואה. המרגל קורא את המצב **בתוך** העיגון הקבוע:
    ‏`FakeHooks.mount` שואל `if self.mount_fail_substr and ...` לפני כל עיגון."""
    conn = env["ctx"].conn

    class StateAtMount:
        def __init__(self):
            self.seen = []

        def __bool__(self):
            self.seen.extend(r["state"] for r in sl.list_rows(conn)
                             if r["name"] == "nas-images")
            return False                    # העיגון עצמו מצליח

    spy = StateAtMount()
    env["fake"].mount_fail_substr = spy
    created = _create_nfs(env)
    assert created.status_code == 200, created.text
    assert spy.seen == ["unchecked"], spy.seen
    assert created.json()["state"] == "connected"


def test_create_fstab_failure_leaves_no_row_and_no_permanent_mount(env):
    env["fake"].fstab_status = FAILED
    r = _create_nfs(env)
    assert r.status_code == 409 and "fstab" in r.json()["detail"]
    rows = [x for x in sl.list_rows(env["ctx"].conn) if x["name"] == "nas-images"]
    assert rows == []
    assert all("/storage/" not in m[2].replace("\\", "/") for m in env["fake"].mounts), \
        env["fake"].mounts


def test_create_mount_failure_removes_fstab_line_and_row(env):
    env["fake"].mount_fail_substr = "nas-images"   # רק העיגון הקבוע, לא הבדיקה
    r = _create_nfs(env)
    assert r.status_code == 409
    rows = [x for x in sl.list_rows(env["ctx"].conn) if x["name"] == "nas-images"]
    assert rows == []
    assert len(env["fake"].fstab) >= 2
    assert env["fake"].fstab[-1] == [], "שורת ה-fstab הוסרה אחרי כשל העיגון"


def test_connect_with_fstab_failure_is_not_connected(env):
    loc_id = _create_nfs(env).json()["id"]
    assert env["admin"].post(
        f"/api/console/storage-locations/{loc_id}/disconnect").status_code == 200
    env["fake"].fstab_status = FAILED
    r = env["admin"].post(f"/api/console/storage-locations/{loc_id}/connect")
    assert r.status_code == 409
    row = sl.get(env["ctx"].conn, loc_id)
    assert row["state"] != "connected"
    assert env["fake"].umounts[-1] == row["mount_point"]


def test_connect_after_disconnect_puts_the_network_fstab_line_back(env):
    """‏#1993: ‏connect כתב את fstab כשהמצב עוד היה `disconnected` — השורה
    דולגה, והמיקום "מחובר" לא שרד את האתחול הבא."""
    loc_id = _create_nfs(env).json()["id"]
    base = f"/api/console/storage-locations/{loc_id}"
    assert env["admin"].post(base + "/disconnect").status_code == 200
    assert not any(e["fstype"] == "nfs" for e in env["fake"].fstab[-1])
    again = env["admin"].post(base + "/connect")
    assert again.status_code == 200 and again.json()["state"] == "connected"
    assert [e["source"] for e in env["fake"].fstab[-1] if e["fstype"] == "nfs"] == [
        "10.44.10.20:/mnt/pool/images"], env["fake"].fstab[-1]


# --- #1552: ארגז החול של השירות חייב לתת ל-iscsiadm לכתוב ----------------------

#: מה ש-iscsiadm כותב: תיקיית הנעילה (נוצרת ב-mkdir אם אינה קיימת — ומכאן
#: "Could not open /run/lock/iscsi" בשרת חיפה, 17/10), ורשומות discovery/node.
ISCSIADM_WRITES = ("/run/lock/iscsi", "/var/lib/iscsi", "/etc/iscsi")


def test_the_server_unit_lets_iscsiadm_write_its_lock_and_node_records():
    """‏#1552: ‏ProtectSystem=strict הופך הכול לקריאה-בלבד מלבד ReadWritePaths.
    נתיב שאינו מכוסה = "גלה יעדים" שנכשל ב-Read-only file system בכל שרת מותקן,
    וכל טסט עם hooks מזויפים עובר. ‏/run/lock/iscsi עצמו לא קיים בעליית
    היחידה, ולכן מה שנפתח הוא /run/lock שבו iscsiadm יוצר אותו."""
    unit = (Path(__file__).resolve().parents[1] / "install" / "imagectl-server.service").read_text(
        encoding="utf-8")
    assert "ProtectSystem=strict" in unit
    rw = [p.lstrip("-") for p in next(
        line for line in unit.splitlines()
        if line.startswith("ReadWritePaths=")).split("=", 1)[1].split()]
    for need in ISCSIADM_WRITES:
        assert any(need == p or need.startswith(p.rstrip("/") + "/") for p in rw), \
            f"{need} is read-only for iscsiadm under ProtectSystem=strict"
    assert "/run" not in rw and "/var" not in rw, "the sandbox is opened wider than iscsiadm needs"
