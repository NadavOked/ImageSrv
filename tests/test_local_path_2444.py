"""#2444 — מיקום אחסון `local` אינו מקבל כל נתיב.

עד כאן `validate_params` לא בדק את `local`: מנהל יכול היה להצביע על `/etc`, והשרת
(root) יוצר שם `images/` וכותב קובץ בדיקה. עכשיו `path` חייב להיות (אחרי `realpath`,
כלומר אחרי `..` וקישורים) בתוך תיקיית הנתונים של השרת, תחת `/srv`, או על כונן
מעוגן נפרד שאינו מערכת הקבצים של השורש. ההגבלה חלה על יצירה ובדיקה בלבד —
מיקום שכבר שמור ב-DB ממשיך לעבוד.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.storage import locations as sl
from test_storage_locations import env   # noqa: F401

NOT_MOUNTED = lambda path: False   # noqa: E731


def _mounted(*points):
    """`is_mount` מזויף לפי נתיב פוסיקסי — עובד גם בתחנת הפיתוח (ווינדוס)."""
    return lambda path: sl._posix(str(path)) in points


# ---------- הכלל עצמו ----------


def test_etc_and_root_are_refused(tmp_path):
    data = tmp_path / "data"
    for bad in ("/etc", "/etc/ssh", "/", "/usr/lib", "/root", "/var/lib/dpkg"):
        assert sl.local_path_problem(bad, data, NOT_MOUNTED), bad


def test_srv_is_allowed_but_a_prefix_lookalike_is_not(tmp_path):
    data = tmp_path / "data"
    assert sl.local_path_problem("/srv", data, NOT_MOUNTED) is None
    assert sl.local_path_problem("/srv/images2", data, NOT_MOUNTED) is None
    assert sl.local_path_problem("/srvx/images", data, NOT_MOUNTED)


def test_the_data_dir_is_allowed_and_dotdot_cannot_leave_it(tmp_path):
    data = tmp_path / "data"
    assert sl.local_path_problem(str(data / "extra"), data, NOT_MOUNTED) is None
    assert sl.local_path_problem(str(data / ".." / "elsewhere"), data, NOT_MOUNTED)
    assert sl.local_path_problem(str(data) + "x/images", data, NOT_MOUNTED)


def test_an_existing_mount_point_other_than_root_is_allowed():
    is_mount = _mounted("/mnt/usb")
    assert sl.local_path_problem("/mnt/usb", "/data", is_mount) is None
    assert sl.local_path_problem("/mnt/usb/images", "/data", is_mount) is None
    assert sl.local_path_problem("/mnt/other", "/data", is_mount)
    assert sl.local_path_problem("/mnt", "/data", is_mount)


def test_the_root_filesystem_is_never_a_mount_that_counts():
    """גם אם `/` מדווח כמעוגן (הוא תמיד), נתיב בתוכו אינו מותר בגללו."""
    assert sl.local_path_problem("/var/lib/dpkg", "/data", _mounted("/"))
    assert sl.local_path_problem("/home/x", "/data", _mounted("/"))


@pytest.mark.parametrize("pseudo", ["/proc", "/sys/fs", "/dev/shm", "/run/lock", "/boot/efi"])
def test_system_trees_are_refused_even_when_mounted(pseudo):
    assert sl.local_path_problem(pseudo, "/data", lambda p: True)


# ---------- Fable על #2444: דחייה לפי prefix אחרי realpath, לא לפי נקודת עיגון ----------

DATA = "/var/lib/imagesrv"          # ברירת המחדל של --data-dir


@pytest.mark.parametrize("bad", ["/var", "/var/lib/dpkg", "/var/spool/cron", "/var/log",
                                 "/var/cache/apt", "/var/lib/imagesrv/../../lib/dpkg",
                                 "/var/lib/imagesrvx/images"])
def test_var_is_refused_even_when_it_is_a_separate_mount(bad):
    assert sl.local_path_problem(bad, DATA, _mounted("/var")), bad


@pytest.mark.parametrize("good", [DATA, DATA + "/extra-images", DATA + "/mnt/disk2"])
def test_the_data_dir_under_a_mounted_var_stays_allowed(good):
    assert sl.local_path_problem(good, DATA, _mounted("/var")) is None, good


@pytest.mark.parametrize("bad", ["/srv/imagesrv/boot", "/srv/imagesrv/boot/grub",
                                 "/srv/tftp", "/srv/tftp/grub"])
def test_the_servers_boot_and_tftp_trees_are_refused_even_when_mounted(bad):
    assert sl.local_path_problem(bad, DATA, _mounted("/srv/tftp", "/srv/imagesrv/boot")), bad


@pytest.mark.parametrize("good", ["/srv/imagesrv/images", "/srv/images2", "/srv/imagesrv/bootx"])
def test_the_rest_of_srv_stays_allowed(good):
    assert sl.local_path_problem(good, DATA, NOT_MOUNTED) is None, good


@pytest.mark.parametrize("bad", ["/tmp", "/tmp/images", "/opt", "/opt/imagesrv/server"])
def test_tmp_and_opt_are_refused_even_when_mounted(bad):
    assert sl.local_path_problem(bad, DATA, lambda p: True), bad


def test_a_separately_mounted_data_disk_elsewhere_stays_allowed():
    assert sl.local_path_problem("/mnt/usb/images", DATA, _mounted("/mnt/usb")) is None
    assert sl.local_path_problem("/data/images", DATA, _mounted("/data")) is None


def test_a_relative_or_empty_path_is_refused():
    assert sl.local_path_problem("images", "/data", lambda p: True)
    assert sl.local_path_problem("", "/data", lambda p: True)
    assert sl.local_path_problem("/srv/x\x00y", "/data", lambda p: True)


def test_a_symlink_cannot_smuggle_an_outside_path(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    try:
        os.symlink(target, data / "link", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("אין הרשאה ליצור symlink בסביבה הזו")
    assert sl.local_path_problem(str(data / "link"), data, NOT_MOUNTED)


# ---------- ה-API ----------


def _post(env, name, path, route=""):
    body = {"name": name, "type": "local", "params": {"path": str(path)}, "tested": True}
    if route:
        body = {"type": "local", "params": {"path": str(path)}}
    return env["admin"].post(f"/api/console/storage-locations{route}", json=body)


def test_create_with_etc_is_refused_and_nothing_is_written(env):
    r = _post(env, "bad", "/etc")
    assert r.status_code == 400, r.text
    assert "נתיב" in r.json()["detail"]
    assert env["ctx"].conn.execute(
        "SELECT COUNT(*) FROM storage_locations WHERE name = 'bad'").fetchone()[0] == 0


def test_the_connection_test_refuses_it_too(env):
    r = _post(env, "bad", "/etc", route="/test")
    assert r.status_code == 400, r.text


def test_dotdot_out_of_the_data_dir_is_refused_over_the_api(env):
    outside = env["data"] / ".." / "outside"
    outside.mkdir(exist_ok=True)
    assert _post(env, "bad", outside).status_code == 400


def test_a_folder_inside_the_data_dir_is_created(env):
    folder = env["data"] / "extra-images"
    folder.mkdir()
    r = _post(env, "inside", folder)
    assert r.status_code == 200, r.text


def test_a_mounted_disk_is_created(env, tmp_path, monkeypatch):
    # tmp_path של pytest יושב ב-CI תחת /tmp, שאסור גם כשהוא מעוגן (#2467). הטסט בודק
    # את כלל העיגון, לא איפה pytest שם קבצים — לכן הענף שמכיל את tmp_path מוצא מהרשימה.
    where = sl._posix(os.path.realpath(str(tmp_path)))
    monkeypatch.setattr(sl, "LOCAL_SYSTEM_TREES",
                        tuple(t for t in sl.LOCAL_SYSTEM_TREES if not sl._under(where, t)))
    usb = tmp_path / "usb"
    (usb / "images").mkdir(parents=True)
    env["fake"].mounted.add(str(usb))
    r = _post(env, "usb", usb / "images")
    assert r.status_code == 200, r.text


def test_an_existing_location_outside_the_rule_keeps_working(env, tmp_path):
    """שרת שכבר יש לו מיקום כזה: ההגבלה חלה על יצירה ועריכה, לא על מה ששמור."""
    legacy = tmp_path / "legacy-disk"
    legacy.mkdir()
    conn = env["ctx"].conn
    conn.execute(
        "INSERT INTO storage_locations (id, name, type, params_json, mount_point, state,"
        " state_since, created_at, last_images_json)"
        " VALUES ('loc_legacy', 'ישן', 'local', ?, ?, 'connected', 'x', 'x', '[]')",
        ('{"path": %s}' % __import__("json").dumps(str(legacy)), str(legacy)))
    conn.commit()
    listed = env["admin"].get("/api/console/storage-locations").json()["locations"]
    assert "loc_legacy" in [row["id"] for row in listed]
    r = env["admin"].post("/api/console/storage-locations/loc_legacy/check")
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "connected"
