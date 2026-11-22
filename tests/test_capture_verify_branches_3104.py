"""#3104 (capture._verify mutmut_28..36, 121..138): each failure branch of the background
verification ends the task as `failed` BY NAME, the image stays out of the library, and the
staging area is gone. `run_verification`'s catch-all would also end a task as `failed`, but with
"crashed" -- so every test below asserts the specific reason, not just `state == failed`."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from server.library import capture  # noqa: E402
from server.core.tasks import staging_dir  # noqa: E402

from test_capture import make_task, setup_build_machine  # noqa: E402
from test_capture_verify_1470 import _journal, _put_manifest, _task_row, _upload  # noqa: E402


@pytest.fixture
def verifying(server, images_root, monkeypatch):
    """A task the PUT accepted (202) and left `verifying`; nobody verifies it until the test does."""
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid, image_id = created["id"], created["image_id"]
    head = _upload(server, tid)
    monkeypatch.setattr(capture, "start_verification", lambda *a: None)
    assert _put_manifest(server, tid, head).status_code == 202
    assert _task_row(server, tid)["state"] == "verifying"
    return tid, image_id


def _assert_failed_by_name(server, images_root, tid, image_id, reason):
    row = _task_row(server, tid)
    assert row["state"] == "failed", row
    assert reason in row["error"], row
    assert "crashed" not in row["error"], row          # a named branch, not the catch-all
    assert not (images_root / image_id).exists()
    assert not staging_dir(images_root, tid).exists()
    assert any(tid in d and reason in d for d in _journal(server["ctx"], "capture_failed"))


def test_an_unreadable_staged_manifest_fails_the_task_by_name(server, images_root, verifying):
    tid, image_id = verifying
    (staging_dir(images_root, tid) / "manifest.json").write_text("{not json", encoding="utf-8")

    assert capture.run_verification(server["ctx"].conn, images_root, tid) == "failed"

    _assert_failed_by_name(server, images_root, tid, image_id, "the staged manifest could not be read")


def test_a_manifest_that_fails_at_verification_after_passing_the_put_fails_by_name(
        server, images_root, verifying):
    tid, image_id = verifying
    staged = sorted(p for p in staging_dir(images_root, tid).iterdir() if p.name.startswith("p1."))
    assert staged, "the ESP file should be staged"
    staged[0].unlink()                                    # the file vanished between the PUT and the verify

    assert capture.run_verification(server["ctx"].conn, images_root, tid) == "failed"

    _assert_failed_by_name(server, images_root, tid, image_id, "partition file was never uploaded")


def test_an_image_target_outside_the_library_root_fails_by_name(server, images_root, verifying):
    tid, image_id = verifying
    with capture._write_lock:
        server["ctx"].conn.execute("UPDATE tasks SET image_id = ? WHERE id = ?", ("../escape3104", tid))
        server["ctx"].conn.commit()

    assert capture.run_verification(server["ctx"].conn, images_root, tid) == "failed"

    _assert_failed_by_name(server, images_root, tid, "../escape3104", "image target falls outside the library root")
    assert not (images_root.parent / "escape3104").exists()


def test_an_image_with_the_same_id_already_in_the_library_is_not_overwritten(server, images_root, verifying):
    tid, image_id = verifying
    (images_root / image_id).mkdir()
    (images_root / image_id / "keep.txt").write_text("the earlier image", encoding="utf-8")

    assert capture.run_verification(server["ctx"].conn, images_root, tid) == "failed"

    row = _task_row(server, tid)
    assert row["state"] == "failed" and "an image with this id already exists" in row["error"], row
    assert (images_root / image_id / "keep.txt").read_text(encoding="utf-8") == "the earlier image"
    assert not staging_dir(images_root, tid).exists()
