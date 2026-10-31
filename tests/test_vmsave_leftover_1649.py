"""‏#1649 — אתחול השרת באמצע כיווץ של שמירת VM משאיר את העותק `imagesrv-vmsave`
ושני loop (על `disk.raw` ועל `run/shrink.cow`); לפני התיקון ה-`detach_all` של
השמירה הבאה (או של הביטול) נכשל על "התקן loop נשאר מחובר", כי העותק עוד מחזיק
אותם — ואיש לא ניקה.

כאן, בזיופים (בלי root): הקרנל מחזיק את ה-loop-ים כל עוד העותק קיים
(`HeldHost`), וההוכחה היא ש-(1) שמירה שמתחילה מעל שאריות מצליחה ומשאירה הכול
נקי, (2) עליית השרת מנקה, ו-(3) ניקוי שנכשל — או כלי שלא רץ — אינו נבלע.
המקבילה מול כלים אמיתיים: `test_vmupdate_integration.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from server.vm import vmdisk
from test_vmdisk_readonly_1491 import SIZE, ViewHost, done
from test_vmupdate_isolation import DISK, FakeGuest

try:
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None

LINUX = "0FC63DAF-8483-4772-8E79-3D69D8477DE4"
UGUID = "6C2E9F71-1A3B-4F0A-9C1D-2E7B85A0C3D4"
USER = vmdisk.VmUser(999, os.getgid() if hasattr(os, "getgid") else 989)


class HeldHost(ViewHost):
    """הקרנל: loop שהעותק מחזיק אינו משתחרר ב-`losetup -d` כל עוד העותק קיים.
    `stuck=True` — גם `dmsetup remove` לא מסיר (משהו פתח אותו)."""

    def __init__(self, *, stuck=False, ls_rc=0, missing=None, **kw):
        super().__init__(**kw)
        self.held: set[str] = set()
        self.stuck = stuck
        self.ls_rc = ls_rc
        self.missing = missing           # שם כלי שאינו מותקן — OSError

    def leftover(self, disk: Path, cow: Path) -> None:
        """מה שנשאר ממכונה שהשרת שלה אותחל באמצע כיווץ."""
        self.views.add(vmdisk.VIEW_NAME)
        disk.parent.mkdir(parents=True, exist_ok=True)
        if not disk.exists():
            with disk.open("wb") as fh:
                fh.truncate(SIZE)
        self.loops["/dev/loop5"] = str(disk)
        self.loops["/dev/loop6"] = str(cow)
        self.held = {"/dev/loop5", "/dev/loop6"}
        cow.parent.mkdir(parents=True, exist_ok=True)
        with cow.open("wb") as fh:
            fh.truncate(1 << 20)

    def clean(self) -> bool:
        return not self.views and not self.loops

    def run(self, argv, **kw):
        if self.missing and argv[0] == self.missing:
            raise FileNotFoundError(argv[0])
        if argv[:2] == ["dmsetup", "ls"] and self.ls_rc:
            return done(argv, rc=self.ls_rc, err="ls failed")
        if argv[:2] == ["losetup", "-d"] and argv[2] in self.held and self.views:
            self.calls.append(list(argv))
            return done(argv)            # busy: ה-loop נשאר
        if argv[:2] == ["dmsetup", "remove"] and self.stuck:
            self.calls.append(list(argv))
            return done(argv, rc=1, err="device busy")
        return super().run(argv, **kw)

    def agent(self, func, *args, **_kw):
        if func == "_lc_relocs":
            return super().agent(func, *args)
        return type("Call", (), {"out": "", "ok": True})()


class ExtGuest(FakeGuest):
    """דיסק ext4 בודד — אין מה לכווץ, ולכן השמירה אינה עוברת ב-`shrink_view`."""

    def __init__(self):
        super().__init__(parts=[{"part_num": 1, "part_start": 1 << 20, "part_size": 32 << 20}],
                         guids={1: UGUID}, fs={"/dev/sda1": "ext4"})
        self.types = {1: LINUX}

    def pread_device(self, *_a):
        return b"\x00" * 8

    def mount_ro(self, *_a):
        raise RuntimeError("not mounted in the fake")


@pytest.fixture()
def work(tmp_path):
    folder = tmp_path / "work"
    (folder / "run").mkdir(parents=True)
    with (folder / "disk.raw").open("wb") as fh:
        fh.truncate(SIZE)
    (folder / "source-manifest.json").write_text(json.dumps({
        "id": "img_aaaaaa", "disk_guid": DISK,
        "partitions": [{"index": 1, "type_guid": LINUX, "fs": "ext4"}]}), encoding="utf-8")
    return folder


def save(host, folder, monkeypatch):
    monkeypatch.setattr(vmdisk, "capture_partition",
                        lambda *_a, **_k: ("ab" * 32, 123))
    return vmdisk.capture_disk(
        host, folder, folder / "out", lambda n: None, user=USER,
        appliance=lambda disk, **_k: vmdisk.Appliance(disk, factory=ExtGuest))


# --- 1. שמירה מעל שאריות ----------------------------------------------------------


def test_a_save_over_leftovers_of_a_cut_save_succeeds_and_leaves_nothing(work, monkeypatch):
    host = HeldHost()
    host.leftover(work / "disk.raw", work / "run" / "shrink.cow")
    captured = save(host, work, monkeypatch)
    assert captured["partitions"][0]["sha256"] == "ab" * 32
    assert host.clean(), (host.views, host.loops)
    assert not (work / "run" / "shrink.cow").exists()


def test_the_leftovers_are_removed_before_the_vm_disk_is_used_again(work, monkeypatch):
    host = HeldHost()
    host.leftover(work / "disk.raw", work / "run" / "shrink.cow")
    save(host, work, monkeypatch)
    removed = host.calls.index(["dmsetup", "remove", "--retry", vmdisk.VIEW_NAME])
    first_use = next(i for i, c in enumerate(host.calls) if c[:3] == ["losetup", "--find", "--show"])
    assert removed < first_use


def test_a_save_with_no_leftovers_touches_nothing_it_does_not_own(work, monkeypatch):
    host = HeldHost()
    other = "/dev/loop99"
    host.loops[other] = "/somewhere/else.raw"
    save(host, work, monkeypatch)
    assert host.loops == {other: "/somewhere/else.raw"}
    assert not any(c[:2] == ["dmsetup", "remove"] for c in host.calls)


# --- 2. ניקוי שנכשל אינו נבלע ---------------------------------------------------------


def test_a_view_that_will_not_go_fails_the_save_by_name(work, monkeypatch):
    host = HeldHost(stuck=True)
    host.leftover(work / "disk.raw", work / "run" / "shrink.cow")
    with pytest.raises(vmdisk.DiskError) as caught:
        save(host, work, monkeypatch)
    assert caught.value.reason == "loop"
    assert "שאריות משמירה קודמת לא נוקו" in caught.value.message
    assert vmdisk.VIEW_NAME in caught.value.message
    assert (work / "run" / "shrink.cow").exists()       # לא נמחק מתחת לעותק חי
    assert not any(c[:3] == ["losetup", "--find", "--show"] for c in host.calls)


def test_a_cleanup_that_cannot_be_checked_is_a_failure_not_clean(work, monkeypatch):
    host = HeldHost(ls_rc=1)
    with pytest.raises(vmdisk.DiskError) as caught:
        save(host, work, monkeypatch)
    assert "dmsetup ls" in caught.value.message


def test_a_missing_tool_is_a_named_failure_not_a_crash(work, monkeypatch):
    host = HeldHost(missing="dmsetup")
    with pytest.raises(vmdisk.DiskError) as caught:
        save(host, work, monkeypatch)
    assert caught.value.reason == "loop" and "dmsetup" in caught.value.message


# --- 3. עליית השרת ----------------------------------------------------------------------


@pytest.fixture()
def server(tmp_path, images_root):
    if TestClient is None:
        pytest.skip("fastapi is required")
    from test_app_split import _runtime                      # noqa: PLC0415
    from server.app import create_console_app                # noqa: PLC0415
    rt = _runtime(tmp_path, images_root)
    yield rt, create_console_app(rt)
    rt.ctx.sender.stop()


def test_server_startup_clears_the_leftovers_of_a_save_that_was_cut(server):
    rt, console = server
    engine = rt.vmupdate
    engine.host = HeldHost()
    engine.host.leftover(engine.workdir / "disk.raw", engine.workdir / "run" / "shrink.cow")
    with TestClient(console):
        pass
    assert engine.host.clean(), (engine.host.views, engine.host.loops)
    assert not (engine.workdir / "run" / "shrink.cow").exists()
    assert not engine.status().get("warnings")


def test_server_startup_does_not_hide_a_cleanup_that_failed(server):
    rt, console = server
    engine = rt.vmupdate
    engine.host = HeldHost(stuck=True)
    engine.host.leftover(engine.workdir / "disk.raw", engine.workdir / "run" / "shrink.cow")
    with TestClient(console):            # השרת עולה — אבל הכשל גלוי
        pass
    warnings = engine.status().get("warnings") or []
    assert any(vmdisk.VIEW_NAME in w for w in warnings), warnings
    events = [row[0] for row in engine.conn.execute("SELECT event FROM journal")]
    assert "vmupdate_leftover_failed" in events


def test_server_startup_leaves_a_live_save_alone(server):
    rt, console = server
    engine = rt.vmupdate
    engine.host = HeldHost()
    engine.host.leftover(engine.workdir / "disk.raw", engine.workdir / "run" / "shrink.cow")
    engine._state["state"] = "saving"          # תהליך זה שומר עכשיו: העותק חי
    with TestClient(console):
        pass
    assert engine.host.views == {vmdisk.VIEW_NAME}
    assert not any(c[:2] == ["dmsetup", "remove"] for c in engine.host.calls)


def test_server_startup_without_a_work_area_runs_no_command(server):
    rt, console = server
    engine = rt.vmupdate
    engine.host = HeldHost()
    with TestClient(console):
        pass
    assert engine.host.calls == []
