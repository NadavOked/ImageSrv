"""‏#2122 / ‏#2123 — עיגון במרחב של PID 1, ומצב שאינו "מחובר" מהזיכרון.

‏#2122: ‏imagesrv-server רץ עם `ProtectSystem=strict` — במרחב עיגון משלו. ‏`mount`
שרץ משם נשאר שם, ונעלם ב-restart של השירות; ‏`storage/<שם>` חוזר להיות תיקייה
על דיסק המערכת (נמדד על rc2/ESXi, ‏24/10). כאן: mount/umount רצים דרך `nsenter`
למרחב של PID 1, וההצלחה נקבעת לפי שני קובצי mountinfo, לא לפי קוד היציאה.

‏#2123: אחרי restart הרשימה הראתה "מחובר" מהזיכרון. כאן: מיקום-עיגון הוא
"לא נבדק" מעליית התהליך ועד הבדיקה הראשונה שלו, שרצה כבר בפעימה הראשונה.

אף טסט כאן אינו מריץ mount/nsenter/systemctl אמיתי — `_run` מזויף.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from server.storage import storage_locations as sl
from server.storage.storage_locations import FAILED, OK, UNCHECKED, HookResult

REPO = Path(__file__).resolve().parents[1]
TARGET = "/var/lib/imagesrv/storage/extra20"
LINE = "{n} 1 8:17 / {point} rw,relatime shared:{n} - ext4 /dev/sdb1 rw\n"


class Namespaces:
    """שני קובצי mountinfo — של PID 1 ושל השירות — ו-`_run` מזויף שמעגן באחד מהם."""

    def __init__(self, tmp_path: Path, monkeypatch, *, propagate: bool = True):
        self.host = tmp_path / "pid1-mountinfo"
        self.own = tmp_path / "self-mountinfo"
        for path in (self.host, self.own):
            path.write_text(LINE.format(n=1, point="/"), encoding="utf-8")
        self.propagate = propagate     # False = כמו לפני #2122: רק המרחב של השירות
        self.calls: list[list[str]] = []
        # ‏raising=False: על הקוד שלפני #2122 אין את השמות — הבקרה השלילית נכשלת בהתנהגות
        monkeypatch.setattr(sl, "HOST_MOUNTINFO", str(self.host), raising=False)
        monkeypatch.setattr(sl, "OWN_MOUNTINFO", str(self.own), raising=False)
        monkeypatch.setattr(sl, "_run", self.run)

    def _add(self, path: Path, point: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(LINE.format(n=len(path.read_text().splitlines()) + 1,
                                 point=point.replace(" ", "\\040")))

    def _drop(self, path: Path, point: str) -> None:
        escaped = point.replace(" ", "\\040")
        keep = [ln for ln in path.read_text().splitlines(keepends=True)
                if ln.split(" ")[4] != escaped]
        path.write_text("".join(keep), encoding="utf-8")

    def where(self, point: str) -> list[str]:
        """באילו מרחבים הנקודה מעוגנת — נקרא מהקבצים, לא מהקוד שנבדק."""
        escaped = point.replace(" ", "\\040")
        return [name for name, path in (("host", self.host), ("own", self.own))
                if escaped in [ln.split(" ")[4] for ln in path.read_text().splitlines()]]

    def mount_both(self, point: str) -> None:
        self._add(self.host, point)
        self._add(self.own, point)

    def run(self, cmd, timeout=30, input_text=None):
        self.calls.append(list(cmd))
        on_host = cmd[:3] == ["nsenter", "--mount=/proc/1/ns/mnt", "--"]
        inner = cmd[3:] if on_host else list(cmd)
        if inner and inner[0] == "mount":
            point = inner[-1]
            if on_host:
                self._add(self.host, point)
                if self.propagate:
                    self._add(self.own, point)
            else:
                self._add(self.own, point)
        elif inner and inner[0] == "umount":
            if on_host:
                self._drop(self.host, inner[-1])
                if self.propagate:
                    self._drop(self.own, inner[-1])
            else:
                self._drop(self.own, inner[-1])
        return HookResult(OK, payload={"stdout": "", "stderr": ""})


# --- #2122: mount / umount ---------------------------------------------------

def test_mount_runs_in_pid1_mount_namespace_and_is_seen_by_both(tmp_path, monkeypatch):
    ns = Namespaces(tmp_path, monkeypatch)
    target = str(tmp_path / "storage" / "extra20")
    result = sl.mount("ext4", "UUID=2b687e73", target, "")
    assert result.status == OK, result.reason
    assert ns.calls[0] == ["nsenter", "--mount=/proc/1/ns/mnt", "--",
                           "mount", "-t", "ext4", "UUID=2b687e73", target]
    assert ns.where(target) == ["host", "own"]


def test_a_mount_that_pid1_does_not_see_is_a_failure_and_is_undone(tmp_path, monkeypatch):
    """בדיוק מה שנמדד ב-rc2: ‏sdb1 ב-`/proc/<השירות>/mountinfo` ו-0 פעמים אצל PID 1."""
    ns = Namespaces(tmp_path, monkeypatch)
    target = str(tmp_path / "storage" / "extra20")

    def private_only(cmd, timeout=30, input_text=None):
        ns.calls.append(list(cmd))
        if "mount" in cmd:
            ns._add(ns.own, cmd[-1])
        return HookResult(OK, payload={"stdout": "", "stderr": ""})

    monkeypatch.setattr(sl, "_run", private_only)
    result = sl.mount("ext4", "UUID=2b687e73", target, "")
    assert result.status == FAILED
    assert "PID 1" in result.reason and result.payload["missing"] == ["PID 1"]
    assert ns.calls[-1][-2:] == ["umount", target], "העיגון החלקי פורק"


def test_a_mount_the_service_does_not_see_is_a_failure(tmp_path, monkeypatch):
    """עיגון אצל PID 1 שלא התפשט לשירות — השירות היה כותב לתיקייה על דיסק המערכת."""
    Namespaces(tmp_path, monkeypatch, propagate=False)
    target = str(tmp_path / "storage" / "extra20")
    result = sl.mount("ext4", "UUID=2b687e73", target, "")
    assert result.status == FAILED and result.payload["missing"] == ["השירות"]


def test_umount_runs_in_pid1_namespace_and_needs_both_to_lose_it(tmp_path, monkeypatch):
    ns = Namespaces(tmp_path, monkeypatch)
    ns.mount_both(TARGET)
    result = sl.umount(TARGET)
    assert result.status == OK, result.reason
    assert ns.calls == [["nsenter", "--mount=/proc/1/ns/mnt", "--", "umount", TARGET]]
    assert ns.where(TARGET) == []


def test_umount_that_left_the_mount_at_pid1_is_a_failure(tmp_path, monkeypatch):
    """‏umount מתוך מרחב פרטי מסיר רק את העותק של השירות — אצל המארח העיגון נשאר."""
    ns = Namespaces(tmp_path, monkeypatch)
    ns.mount_both(TARGET)

    def private_only(cmd, timeout=30, input_text=None):
        ns._drop(ns.own, cmd[-1])
        return HookResult(OK, payload={"stdout": "", "stderr": ""})

    monkeypatch.setattr(sl, "_run", private_only)
    result = sl.umount(TARGET)
    assert result.status == FAILED and "עדיין מעוגן" in result.reason


def test_default_is_mount_needs_pid1_and_the_service(tmp_path, monkeypatch):
    ns = Namespaces(tmp_path, monkeypatch)
    is_mount = sl.default_hooks()["is_mount"]
    ns._add(ns.own, TARGET)
    assert is_mount(TARGET) is False, "רק אצל השירות — זה הבאג של #2122"
    ns._add(ns.host, TARGET)
    assert is_mount(TARGET) is True
    assert is_mount(TARGET + "/") is True


def test_a_mount_that_could_not_be_verified_is_unchecked_not_ok(tmp_path, monkeypatch):
    """‏mountinfo של PID 1 שלא נקרא אינו "מעוגן" — וגם אינו "לא מעוגן"."""
    Namespaces(tmp_path, monkeypatch)
    monkeypatch.setattr(sl, "HOST_MOUNTINFO", str(tmp_path / "missing"), raising=False)
    target = str(tmp_path / "storage" / "extra20")
    result = sl.mount("ext4", "UUID=2b687e73", target, "")
    assert result.status == UNCHECKED and "missing" in result.reason
    assert sl.default_hooks()["is_mount"](target) is False


def test_mount_point_with_a_space_is_read_from_its_mountinfo_escape(tmp_path, monkeypatch):
    ns = Namespaces(tmp_path, monkeypatch)
    ns.mount_both("/srv/images 2")
    assert "\\040" in ns.host.read_text()
    assert sl.default_hooks()["is_mount"]("/srv/images 2") is True


def test_fstab_write_reloads_systemd_after_the_file_is_written(tmp_path, monkeypatch):
    """‏fstab שהשתנה בלי daemon-reload: ל-PID 1 נשארת יחידת mount ישנה לאותה נקודה."""
    fstab = tmp_path / "fstab"
    fstab.write_text("UUID=root / ext4 defaults 0 1\n", encoding="utf-8")
    monkeypatch.setattr(sl, "FSTAB_PATH", fstab)
    seen = []

    def run(cmd, timeout=30, input_text=None):
        seen.append((list(cmd), sl.FSTAB_BEGIN in fstab.read_text(encoding="utf-8")))
        return HookResult(OK, payload={"stdout": "", "stderr": ""})

    monkeypatch.setattr(sl, "_run", run)
    entry = {"source": "UUID=2b687e73", "target": TARGET, "fstype": "ext4", "local": True}
    assert sl.fstab_write([entry]).status == OK
    assert seen == [(["systemctl", "daemon-reload"], True)], "אחרי הכתיבה, לא לפניה"

    monkeypatch.setattr(sl, "_run", lambda cmd, timeout=30, input_text=None:
                        HookResult(FAILED, "Access denied"))
    result = sl.fstab_write([entry])
    assert result.status == FAILED and "daemon-reload" in result.reason


# --- היחידות מאפשרות את מה שהקוד צריך ----------------------------------------

def _unit(name: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in (REPO / "deploy" / name).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and "=" in line and not line.startswith("["):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


@pytest.mark.parametrize("unit", ["imagesrv-server.service", "imagesrv-dcui.service"])
def test_units_that_mount_storage_can_enter_pid1_namespace(unit):
    """‏`nsenter --mount=/proc/1/ns/mnt` דורש root עם CAP_SYS_ADMIN+CAP_SYS_CHROOT,
    setns שאינו חסום, ו-/proc/1 גלוי; ‏`systemctl daemon-reload` דורש AF_UNIX; והעיגון
    של PID 1 מגיע לשירות רק כשההתפשטות אינה private. כל אחת מאלה, אם תתווסף ליחידה,
    מחזירה את #2122 — בשקט, כי ה-mount יכשל רק במכונה אמיתית."""
    values = _unit(unit)
    assert values.get("ProtectSystem") == "strict", "זו הסיבה שה-mount צריך את PID 1"
    assert values.get("User", "root") == "root"
    for banned in ("CapabilityBoundingSet", "AmbientCapabilities", "SystemCallFilter",
                   "RestrictNamespaces", "ProtectProc", "ProcSubset", "PrivatePIDs",
                   "PrivateMounts"):
        assert banned not in values, f"{unit}: {banned}= חוסם עיגון דרך PID 1"
    assert values.get("MountFlags", "shared") in ("shared", "slave")
    families = values.get("RestrictAddressFamilies")
    assert families is None or "AF_UNIX" in families.split()
    rw = values["ReadWritePaths"].split()
    assert "/etc" in rw and "/var/lib/imagesrv" in rw


# --- #2123: מצב אחרי עליית השרת ----------------------------------------------

def _env(tmp_path, images_root, clock, monkeypatch, fake, **kwargs):
    from fastapi.testclient import TestClient

    from server.rounds import sender as sender_module
    from server.console import users
    from server.app import create_app
    from test_sender import Recorder

    monkeypatch.setattr(sender_module, "port_holders", lambda port: [])
    app = create_app(tmp_path / "data", images_root, "http://10.44.12.10:8080",
                     now_fn=clock, sender_runner=Recorder(block=True),
                     storage_hooks=fake.as_dict(), **kwargs)
    ctx = app.state.ctx
    if users.lookup(ctx.conn, "noc") is None:
        users.create(ctx.conn, "noc", "admin-pass-123", "admin", by="test",
                     is_builtin=True, check_policy=False)
    client = TestClient(app)
    assert client.post("/api/console/login",
                       json={"username": "noc", "password": "admin-pass-123"}).status_code == 200
    return app, client


def _disk_row(client) -> dict:
    rows = client.get("/api/console/storage-locations").json()["locations"]
    return next(r for r in rows if r["id"] != "loc_local")


@pytest.fixture()
def restart(tmp_path, images_root, clock, monkeypatch):
    """הוסף כונן בשרת אחד, ואז "restart": אפליקציה חדשה על אותו data-dir."""
    pytest.importorskip("fastapi")
    from test_add_disk_1988 import BLANK, DiskFake

    first = DiskFake()
    app, client = _env(tmp_path, images_root, clock, monkeypatch, first,
                       storage_check_interval=10**9)
    added = client.post("/api/console/storage-locations/add-disk", json={
        "device": BLANK["device"], "serial": BLANK["serial"],
        "size_bytes": BLANK["size_bytes"], "name": "extra20", "confirm_erase": True})
    assert added.status_code == 200 and added.json()["state"] == "connected"
    app.state.ctx.sender.stop()
    apps = []

    def again(fake, **kwargs):
        kwargs.setdefault("storage_check_interval", 10**9)
        app2, client2 = _env(tmp_path, images_root, clock, monkeypatch, fake, **kwargs)
        apps.append(app2)
        return app2, client2

    yield again, added.json()
    for item in apps:
        item.state.ctx.sender.stop()


def test_after_restart_a_disk_that_is_not_mounted_is_not_shown_connected(restart):
    """מה שנמדד ב-rc2: אחרי restart, בלי שום עיגון — הרשימה הראתה "מחובר" 19.8/20.9GB."""
    from test_add_disk_1988 import DiskFake

    again, added = restart
    fake = DiskFake()                                  # restart: שום דבר לא מעוגן
    app, client = again(fake)
    row = _disk_row(client)
    assert row["state"] == "unchecked" and row["state_label"] == "לא נבדק", row
    assert row["state_detail"] == sl.AFTER_START_DETAIL
    roots = sl.library_roots(app.state.ctx.conn, app.state.ctx.library.root)
    assert not next(r for r in roots if r["id"] == added["id"])["available"], \
        "הספרייה אינה כותבת למיקום שלא נבדק"
    sl.poll(app.state.ctx.conn, app.state.ctx.library, fake.as_dict())
    row = _disk_row(client)
    assert row["state"] == "unreachable" and row["state_detail"] == "הכונן אינו מעוגן"


def test_after_reboot_a_disk_that_is_mounted_again_is_not_shown_unreachable(restart):
    """הצד השני של אותו באג: אחרי אתחול ה-fstab עיגן, והרשימה הראתה "לא נגיש"."""
    from test_add_disk_1988 import DiskFake

    again, added = restart
    down = DiskFake()
    app, client = again(down)
    sl.poll(app.state.ctx.conn, app.state.ctx.library, down.as_dict())
    assert _disk_row(client)["state"] == "unreachable"
    app.state.ctx.sender.stop()
    up = DiskFake()
    up.mounted.add(str(Path(added["mount_point"])))    # ה-fstab עיגן באתחול
    app, client = again(up)
    assert _disk_row(client)["state"] == "unchecked"
    sl.poll(app.state.ctx.conn, app.state.ctx.library, up.as_dict())
    assert _disk_row(client)["state"] == "connected"


def test_the_first_tick_after_start_checks_what_was_marked_unchecked(restart):
    """לא אחרי `storage_check_interval` שלם — בפעימה הראשונה של שעון החדר."""
    from test_add_disk_1988 import DiskFake

    again, added = restart
    fake = DiskFake()
    fake.mounted.add(str(Path(added["mount_point"])))
    app, client = again(fake, room_clock_interval=0.05)
    assert _disk_row(client)["state"] == "unchecked"
    with client:                                      # lifespan → שעון החדר
        deadline = time.monotonic() + 10
        while _disk_row(client)["state"] == "unchecked" and time.monotonic() < deadline:
            time.sleep(0.1)
        assert _disk_row(client)["state"] == "connected"


def test_the_local_folder_and_disconnected_locations_are_not_marked(restart):
    """רק מיקום שהוא עיגון. ‏`loc_local` הוא תיקייה; מנותק נשאר מנותק."""
    from test_add_disk_1988 import DiskFake

    again, added = restart
    app, client = again(DiskFake())
    client.post(f"/api/console/storage-locations/{added['id']}/disconnect")
    app.state.ctx.sender.stop()
    app, client = again(DiskFake())
    rows = {r["id"]: r for r in client.get("/api/console/storage-locations").json()["locations"]}
    assert rows["loc_local"]["state"] == "connected"
    assert rows[added["id"]]["state"] == "disconnected"
