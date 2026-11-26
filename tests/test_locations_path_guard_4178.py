"""#4178 (CodeQL py/path-injection, alert 1204; the same alert 789 was dismissed and came back):
the path of a storage location reaches `Path`/`mkdir`/`is_dir` only through `_inside_allowed`.

A path outside the allowed roots (data_dir, /srv, a real mount point that is not under a system
tree) -- `..` that leaves a root, or a symlink that points out of one -- is refused by name: FAILED,
a log line, no directory created and no mount run. A good path still mounts and gets `images/`.
Every target that the refusal tests create lives under `tmp_path`; nothing here writes to /etc or /srv.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from server.storage import locations as sl
from server.storage.locations import FAILED, OK, HookResult

# the fixture and the helper of the API tests: a real app, every system tool faked.
from test_storage_locations import _create_nfs, env  # noqa: F401  (env is a fixture)


def _nfs_row(mount_point):
    return {"id": "loc_t", "type": "nfs", "mount_point": str(mount_point),
            "params_json": json.dumps({"server": "10.0.0.5", "export": "/images"})}


def _hooks(mounts: list) -> dict:
    return {"mount": lambda *args: mounts.append(args) or HookResult(OK),
            "is_mount": lambda path: False}


def _records(caplog):
    return [r for r in caplog.records if r.name == "imagesrv.storage_locations"]


def _symlink(link: Path, target: Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):   # windows without the privilege
        pytest.skip("symlinks are not available here")


# --- apply_mount: the sink of alert 1204 ------------------------------------------------------

def test_apply_mount_refuses_a_mount_point_outside_the_roots(tmp_path):
    target = tmp_path / "elsewhere" / "nas"
    mounts: list = []
    result = sl.apply_mount(_nfs_row(target), _hooks(mounts))
    assert result.status == FAILED
    assert mounts == [], "a refused path is never mounted"
    assert not (tmp_path / "elsewhere").exists(), "and no directory is created for it"
    assert result.reason == sl.PATH_NOT_ALLOWED


def test_apply_mount_with_a_control_character_is_refused_and_logged_on_one_line(tmp_path, caplog):
    caplog.set_level(logging.WARNING, logger="imagesrv.storage_locations")
    target = f"{tmp_path}/storage/x\nWARNING forged: admin logged in"
    result = sl.apply_mount(_nfs_row(target), _hooks([]), data_dir=tmp_path)
    assert result.status == FAILED
    assert result.reason == sl.PATH_NOT_ALLOWED
    messages = [r.getMessage() for r in _records(caplog)]
    assert messages and all("\n" not in m for m in messages)
    assert any("forged" in m for m in messages), "the text is kept, only the break is gone"


def test_apply_mount_creates_images_under_a_mount_point_inside_data_dir(tmp_path):
    target = tmp_path / "storage" / "nas"
    mounts: list = []
    result = sl.apply_mount(_nfs_row(target), _hooks(mounts), data_dir=tmp_path)
    assert result.status == OK, result.reason
    assert mounts and mounts[0][2] == str(target), "the hook gets the stored name, not the realpath"
    assert (target / "images").is_dir()


def test_apply_mount_refuses_a_traversal_that_leaves_data_dir(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    target = f"{data}/storage/../../escape/nas"
    result = sl.apply_mount(_nfs_row(target), _hooks([]), data_dir=data)
    assert result.status == FAILED
    assert not (tmp_path / "escape").exists()


def test_apply_mount_refuses_a_symlink_that_points_out_of_data_dir(tmp_path):
    data, elsewhere = tmp_path / "data", tmp_path / "elsewhere"
    (data / "storage").mkdir(parents=True)
    elsewhere.mkdir()
    _symlink(data / "storage" / "nas", elsewhere)
    mounts: list = []
    result = sl.apply_mount(_nfs_row(data / "storage" / "nas"), _hooks(mounts), data_dir=data)
    assert result.status == FAILED
    assert mounts == []
    assert not (elsewhere / "images").exists(), "the directory was not created through the link"


def test_apply_mount_local_directory_is_checked_before_is_dir(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    row = {"id": "loc_l", "type": "local", "mount_point": str(plain), "params_json": "{}"}
    refused = sl.apply_mount(row, {"is_mount": lambda path: False})
    assert refused.status == FAILED, "an existing directory outside the roots is not applied"
    allowed = sl.apply_mount(row, {"is_mount": lambda path: False}, data_dir=tmp_path)
    assert allowed.status == OK, allowed.reason


# --- through the console: the real router, a fake mount hook ----------------------------------

def test_creating_a_location_whose_mount_point_is_a_symlink_out_is_refused(env):
    data = env["data"]
    elsewhere = data.parent / "elsewhere"
    elsewhere.mkdir()
    (data / "storage").mkdir(parents=True, exist_ok=True)
    _symlink(data / "storage" / "sneaky", elsewhere)
    reply = _create_nfs(env, "sneaky")
    assert reply.status_code == 409, reply.text
    assert [m for m in env["fake"].mounts if "sneaky" in m[2]] == [], "no mount ran on the link"
    assert not (elsewhere / "images").exists(), "no directory was created through the link"
    assert reply.json()["detail"] == sl.PATH_NOT_ALLOWED
    listed = env["admin"].get("/api/console/storage-locations").json()["locations"]
    assert [loc["name"] for loc in listed if loc["name"] == "sneaky"] == []


def test_creating_a_network_location_still_mounts_and_creates_images(env):
    reply = _create_nfs(env, "plain-nas")
    assert reply.status_code in (200, 201), reply.text
    assert (env["data"] / "storage" / "plain-nas" / "images").is_dir()


# --- test_connection: a local path ------------------------------------------------------------

def test_connection_refuses_a_local_path_outside_the_roots_before_df(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    reads: list = []
    hooks = {"df": lambda path: reads.append(path) or HookResult(OK, payload={"free_bytes": 1, "total_bytes": 2}),
             "is_mount": lambda path: False}
    outcome = sl.test_connection("local", {"path": str(plain)}, hooks)
    assert outcome["ok"] is False
    assert reads == [], "df did not run on a path that was refused"
    assert outcome["reason"] == sl.PATH_NOT_ALLOWED


def test_connection_accepts_a_local_path_inside_data_dir(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    hooks = {"df": lambda path: HookResult(OK, payload={"free_bytes": 1, "total_bytes": 2}),
             "is_mount": lambda path: False}
    outcome = sl.test_connection("local", {"path": str(plain)}, hooks, data_dir=tmp_path)
    assert outcome["ok"] is True, outcome


# --- _inside_allowed itself -------------------------------------------------------------------

def test_inside_allowed_returns_the_realpath_only_inside_a_root(tmp_path):
    data = tmp_path / "data"
    (data / "a").mkdir(parents=True)
    no = lambda path: False   # noqa: E731
    assert sl._inside_allowed(data / "a", data, no) == os.path.realpath(data / "a")
    assert sl._inside_allowed(data, data, no) == os.path.realpath(data)
    assert sl._inside_allowed(data / "a" / "new" / "deeper", data, no) == os.path.realpath(
        data / "a" / "new" / "deeper"), "a directory that does not exist yet is fine"
    assert sl._inside_allowed(tmp_path / "data2" / "x", data, no) is None, "a shared prefix is not a root"
    assert sl._inside_allowed(f"{data}/../data2", data, no) is None
    assert sl._inside_allowed("/etc/passwd", data, no) is None
    assert sl._inside_allowed("/srv/../etc", data, no) is None


@pytest.mark.parametrize("bad", ["", None, "relative/x", "x"])
def test_inside_allowed_refuses_an_empty_or_a_relative_path(tmp_path, monkeypatch, bad):
    monkeypatch.chdir(tmp_path)   # a relative path would resolve to a directory that is inside the root
    assert sl._inside_allowed(bad, tmp_path, lambda path: False) is None


def test_inside_allowed_refuses_a_control_character_even_inside_a_root(tmp_path):
    assert sl._inside_allowed(f"{tmp_path}/a\nb", tmp_path, lambda path: False) is None
    assert sl._inside_allowed(f"{tmp_path}/a\x00b", tmp_path, lambda path: False) is None


def test_inside_allowed_follows_a_symlink_in_both_directions(tmp_path):
    data, elsewhere = tmp_path / "data", tmp_path / "elsewhere"
    (data / "real").mkdir(parents=True)
    elsewhere.mkdir()
    _symlink(data / "out", elsewhere)
    _symlink(data / "in", data / "real")
    no = lambda path: False   # noqa: E731
    assert sl._inside_allowed(data / "out" / "x", data, no) is None
    assert sl._inside_allowed(data / "in" / "x", data, no) == os.path.realpath(data / "real" / "x")


def test_inside_allowed_never_treats_the_filesystem_root_as_a_root(tmp_path):
    assert sl._inside_allowed(tmp_path, os.path.abspath(os.sep), lambda path: False) is None


def test_inside_allowed_accepts_a_mounted_drive_like_local_path_problem(tmp_path, monkeypatch):
    """The third root `local_path_problem` permits: an existing mount point, unless it is under a system tree."""
    monkeypatch.setattr(sl, "LOCAL_SYSTEM_TREES", ())
    drive = tmp_path / "drive"
    drive.mkdir()
    mounted = lambda path: path == os.path.realpath(drive)   # noqa: E731
    assert sl._inside_allowed(drive / "images", None, mounted) == os.path.realpath(drive / "images")
    assert sl._inside_allowed(drive, None, mounted) == os.path.realpath(drive)
    assert sl._inside_allowed(drive, None, lambda path: False) is None, "no mount point, no root"
    assert sl._inside_allowed(tmp_path / "other", None, mounted) is None


def test_inside_allowed_does_not_let_a_mount_under_a_system_tree_open_it(tmp_path, monkeypatch):
    """A separate /boot partition is a mount point, and `local_path_problem` still refuses it."""
    monkeypatch.setattr(sl, "LOCAL_SYSTEM_TREES", (str(tmp_path / "sys"),))
    (tmp_path / "sys" / "part").mkdir(parents=True)
    assert sl._inside_allowed(tmp_path / "sys" / "part", None, lambda path: True) is None
