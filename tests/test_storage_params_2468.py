"""#2468 + #2462 — מה שמגיע מהלקוח לא קובע התקן לפירמוט, שורת fstab או נתיב אסור.

#2468: ביצירת מיקום נשמר כל `params` שנשלח. מנהל קונסולה יכול היה לשלוח
`device_by_path=/dev/sda` (שמגיע לפירמוט כ-root ול-`/etc/fstab`) ו-`fs_type` עם
שורה חדשה בתוכו (שורת fstab מוזרקת). עכשיו: רק המפתחות של הסוג נשמרים, ההתקן
הוא רק קישור by-path של LUN מ-`iscsi_login`, `fs_type` מרשימה סגורה, ו-`fstab_write`
מסרב לשדה עם רווח או תו בקרה.

#2462: עצי האתחול נבדקו לפי ברירת המחדל בלבד, ונתיב בתוך `data_dir` אושר לפני כל
דחייה — גם ה-DB, הסודות והתעודות.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.storage import locations as sl
from server.storage.locations import OK
from test_storage_locations import DEVICE, IQN, PORTAL, env   # noqa: F401

NOT_MOUNTED = lambda path: False   # noqa: E731
SL = "/api/console/storage-locations"


def _params_of(env, loc_id):
    row = env["ctx"].conn.execute(
        "SELECT params_json FROM storage_locations WHERE id = ?", (loc_id,)).fetchone()
    return json.loads(row[0])


# ---------- #2468: יצירה — רק המפתחות של הסוג ----------

def test_create_iscsi_drops_server_owned_keys(env):
    r = env["admin"].post(SL, json={
        "name": "san", "type": "iscsi", "tested": True,
        "params": {"portal": PORTAL, "iqn": IQN, "auto": True,
                   "device_by_path": "/dev/sda", "fs_type": "ext4\n/dev/sda / ext4 defaults",
                   "fs_label": "x", "fs_size": 1, "uuid": "u"}})
    assert r.status_code == 200, r.text
    assert _params_of(env, r.json()["id"]) == {"portal": PORTAL, "iqn": IQN, "auto": True}


def test_client_device_never_reaches_format_or_fstab(env):
    """ה-login לא מצא התקן — מה שהלקוח שלח אינו נשאר "ההתקן"."""
    fake = env["fake"]
    loc_id = env["admin"].post(SL, json={
        "name": "san", "type": "iscsi", "tested": True,
        "params": {"portal": PORTAL, "iqn": IQN, "device_by_path": "/dev/sda",
                   "fs_type": "ext4\n/dev/sda /mnt ext4 defaults"}}).json()["id"]
    fake.device = ""
    login = env["admin"].post(f"{SL}/{loc_id}/iscsi-login")
    assert login.status_code == 200, login.text
    assert login.json()["device_by_path"] == ""
    fake.disk_status = "empty"
    fmt = env["admin"].post(f"{SL}/{loc_id}/format", json={"confirm": IQN})
    assert fmt.status_code == 409, fmt.text
    assert fake.make_fs_calls == [] and fake.probes == []
    for entries in fake.fstab:
        assert all("/dev/sda" not in e["source"] and "\n" not in e["fstype"] for e in entries)


def test_create_local_drops_uuid_and_fs_type(env):
    """`uuid` הופך מיקום local ל"כונן" שנכתב ל-fstab — גם הוא נקבע בשרת בלבד."""
    folder = env["data"] / "extra-images"
    folder.mkdir()
    r = env["admin"].post(SL, json={
        "name": "inside", "type": "local", "tested": True,
        "params": {"path": str(folder), "uuid": "x x", "fs_type": "ext4\nevil"}})
    assert r.status_code == 200, r.text
    assert _params_of(env, r.json()["id"]) == {"path": str(folder)}


def test_create_nfs_keeps_its_own_keys():
    assert sl.client_params("nfs", {"server": "s", "export": "/e", "version": "4.1",
                                    "readonly": True, "device_by_path": "/dev/sda"}) == {
        "server": "s", "export": "/e", "version": "4.1", "readonly": True}
    assert sl.client_params("iscsi", ["not", "a", "dict"]) == {}


# ---------- #2468: ההתקן — קישור by-path של LUN בלבד ----------

@pytest.mark.parametrize("bad", [
    "/dev/sda", "/dev/disk/by-path/pci-0000:00:17.0-ata-1",
    DEVICE + "-part1", DEVICE + "\n/dev/sda", "/dev/disk/by-path/ip-a b-iscsi-x-lun-0",
    "/dev/disk/by-path/../../sda", "", None, 7])
def test_a_non_iscsi_device_is_not_a_device(bad):
    assert not sl.valid_iscsi_device(bad)
    assert sl.iscsi_device({"device_by_path": bad}) == ""


def test_a_whole_lun_by_path_link_is_a_device():
    assert sl.iscsi_device({"device_by_path": DEVICE}) == DEVICE


def test_a_stored_legacy_device_is_refused_for_format_and_fstab(env):
    """שורה שנשמרה לפני התיקון עם `/dev/sda` — לא מגיעה לפירמוט ולא ל-fstab."""
    fake = env["fake"]
    loc_id = env["admin"].post(SL, json={
        "name": "san", "type": "iscsi", "tested": True,
        "params": {"portal": PORTAL, "iqn": IQN}}).json()["id"]
    conn = env["ctx"].conn
    sl.save_params(conn, loc_id, {"portal": PORTAL, "iqn": IQN, "device_by_path": "/dev/sda"})
    fake.disk_status = "empty"
    fmt = env["admin"].post(f"{SL}/{loc_id}/format", json={"confirm": IQN})
    assert fmt.status_code == 409
    assert fake.make_fs_calls == []
    sl.set_state(conn, loc_id, "connected")
    assert all(e["source"] != "/dev/sda" for e in sl.fstab_entries(conn))


def test_by_path_lookup_returns_the_whole_lun_link(tmp_path, monkeypatch):
    """`_iscsi_by_path` מחזיר את הקישור (לא `/dev/sdX`), ולא מחיצה שלו."""
    names = [f"ip-{PORTAL}-iscsi-{IQN}-lun-0-part1", f"ip-{PORTAL}-iscsi-{IQN}-lun-0"]

    class Root:
        def __init__(self, path):
            pass

        def is_dir(self):
            return True

        def iterdir(self):
            # `resolve` — מה שהקוד הישן החזיר: `/dev/sdX`, גם למחיצה.
            return [type("E", (), {"name": n, "resolve": lambda self: "/dev/sdb"})()
                    for n in names]

        def as_posix(self):
            return "/dev/disk/by-path"

    monkeypatch.setattr(sl, "Path", Root)
    assert sl._iscsi_by_path(PORTAL, IQN) == DEVICE
    names.pop()
    assert sl._iscsi_by_path(PORTAL, IQN) == ""


# ---------- #2468: fs_type מרשימה סגורה ----------

def test_mount_refuses_a_filesystem_outside_the_list(env):
    fake = env["fake"]
    loc_id = env["admin"].post(SL, json={
        "name": "san", "type": "iscsi", "tested": True,
        "params": {"portal": PORTAL, "iqn": IQN}}).json()["id"]
    assert env["admin"].post(f"{SL}/{loc_id}/iscsi-login").status_code == 200
    fake.disk_status, fake.fs_type = "fs", "ext4 defaults 0 0\n/dev/sda /x"
    r = env["admin"].post(f"{SL}/{loc_id}/mount", json={})
    assert r.status_code == 409, r.text
    assert fake.mounts == [] and fake.fstab == []
    assert "fs_type" not in _params_of(env, loc_id)


def test_apply_mount_refuses_a_stored_filesystem_outside_the_list(tmp_path):
    row = {"id": "loc_x", "type": "iscsi", "mount_point": str(tmp_path / "m"),
           "params_json": json.dumps({"device_by_path": DEVICE, "fs_type": "vfat"})}
    called = []
    result = sl.apply_mount(row, {"mount": lambda *a: called.append(a) or sl.HookResult(OK)})
    assert result.status == sl.FAILED and called == []


# ---------- #2468: fstab_write מסרב לשדה עם רווח או תו בקרה ----------

@pytest.mark.parametrize("field,value", [
    ("source", "/dev/sda /"), ("target", "/mnt/x\n/dev/sda"), ("fstype", "ext4\tdefaults"),
    ("opts", "defaults /dev/sdb"), ("fstype", "ext4\x00"), ("source", "")])
def test_fstab_write_refuses_an_unsafe_field(tmp_path: Path, monkeypatch, field, value):
    fstab = tmp_path / "fstab"
    original = "UUID=abc / ext4 defaults 0 1\n"
    fstab.write_text(original, encoding="utf-8")
    monkeypatch.setattr(sl, "FSTAB_PATH", fstab)
    monkeypatch.setattr(sl, "daemon_reload", lambda: sl.HookResult(OK))
    entry = {"source": DEVICE, "target": "/var/lib/imagesrv/storage/san",
             "fstype": "ext4", "opts": "defaults"}
    entry[field] = value
    result = sl.fstab_write([entry])
    assert result.status == sl.FAILED, result
    assert fstab.read_text(encoding="utf-8") == original


def test_fstab_write_accepts_a_clean_iscsi_line(tmp_path: Path, monkeypatch):
    fstab = tmp_path / "fstab"
    fstab.write_text("", encoding="utf-8")
    monkeypatch.setattr(sl, "FSTAB_PATH", fstab)
    monkeypatch.setattr(sl, "daemon_reload", lambda: sl.HookResult(OK))
    entry = {"source": DEVICE, "target": "/var/lib/imagesrv/storage/san",
             "fstype": "ext4", "opts": "defaults,_netdev,nofail"}
    assert sl.fstab_write([entry]).status == OK
    assert f"{DEVICE} /var/lib/imagesrv/storage/san ext4 defaults,_netdev,nofail 0 0" \
        in fstab.read_text(encoding="utf-8")


# ---------- #2462: עצי האתחול בפועל ----------

DATA = "/var/lib/imagesrv"


@pytest.mark.parametrize("bad", ["/srv/pxe", "/srv/pxe/grub", "/srv/boot2", "/srv/boot2/x"])
def test_the_actual_boot_and_tftp_trees_are_refused(bad):
    assert sl.local_path_problem(bad, DATA, NOT_MOUNTED, ("/srv/pxe", "/srv/boot2")), bad


def test_without_the_actual_trees_the_same_paths_stay_allowed():
    """גבול הבדיקה הקודמת — בלי הערכים בפועל, `/srv/pxe` מותר (תחת /srv)."""
    assert sl.local_path_problem("/srv/pxe", DATA, NOT_MOUNTED) is None


def test_a_boot_tree_inside_the_data_dir_is_refused():
    assert sl.local_path_problem(DATA + "/boot", DATA, NOT_MOUNTED, (DATA + "/boot",))


def test_the_router_passes_the_servers_trees(env, monkeypatch):
    seen = []
    real = sl.local_path_problem
    monkeypatch.setattr(sl, "local_path_problem",
                        lambda *a: seen.append(a) or real(*a))
    folder = env["data"] / "extra-images"
    folder.mkdir()
    env["admin"].post(f"{SL}/test", json={"type": "local", "params": {"path": str(folder)}})
    assert seen and len(seen[0]) == 4


def test_create_app_wires_boot_dir_and_tftp_root(tmp_path, images_root, clock):
    from server.app import create_app
    from server.net import deploy_net
    boot, tftp = tmp_path / "data" / "boot", tmp_path / "data" / "tftp"
    for d in (boot, tftp):
        d.mkdir(parents=True)
    deploy = deploy_net.DeployContext(state=deploy_net.DeployState("none", None, None), agent_port=8080, tftp_root=tftp,
                                      repo_dir=tmp_path)
    captured = {}
    import server.app as app_module
    real = app_module.create_storage_locations_router

    def spy(*args, **kwargs):
        captured.update(kwargs)
        return real(*args, **kwargs)

    mp = pytest.MonkeyPatch()
    mp.setattr(app_module, "create_storage_locations_router", spy)
    try:
        app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                         now_fn=clock, boot_dir=boot, deploy=deploy,
                         storage_check_interval=10**9)
        app.state.ctx.sender.stop()
    finally:
        mp.undo()
    assert set(captured.get("boot_trees") or ()) == {str(boot), str(tftp)}


# ---------- #2462: תת-נתיבים רגישים של data_dir ----------

@pytest.mark.parametrize("bad", [
    "imagesrv.db", "imagesrv.db-wal", "console-tls", "console-tls/serving", "interserver",
    "email-accounts", "secondaries", "netcfg", "wizard", "storage/san", "apt-repo"])
def test_sensitive_entries_of_the_data_dir_are_refused(bad):
    assert sl.local_path_problem(f"{DATA}/{bad}", DATA, NOT_MOUNTED), bad


@pytest.mark.parametrize("good", [DATA, DATA + "/extra-images", DATA + "/console-tlsx"])
def test_the_rest_of_the_data_dir_stays_allowed(good):
    assert sl.local_path_problem(good, DATA, NOT_MOUNTED) is None, good


def test_the_tls_dir_inside_the_data_dir_is_refused_over_the_api(env):
    r = env["admin"].post(f"{SL}/test", json={
        "type": "local", "params": {"path": str(env["data"] / "console-tls")}})
    assert r.status_code == 400, r.text
