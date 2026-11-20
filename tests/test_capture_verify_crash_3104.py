"""#3104 (run_verification mutmut_7..22): a verification that CRASHES ends as `failed`
by name, not as a silent pass and not as a stuck `verifying` task (principle 5)."""

from __future__ import annotations

import logging
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from server.library import capture  # noqa: E402
from server.core.tasks import staging_dir  # noqa: E402

from test_capture import make_task, setup_build_machine  # noqa: E402
from test_capture_verify_1470 import _journal, _put_manifest, _task_row, _upload  # noqa: E402


def test_a_crashing_verification_fails_the_task_by_name_and_clears_its_registration(
        server, images_root, monkeypatch, caplog):
    # The crash is logged at ERROR on purpose; a failing run would print that line in the summary, and the collector reads a
    # line that starts with ERROR as a collection error (not a control that failed on the value). The assertions below are the evidence.
    caplog.set_level(logging.CRITICAL, logger="imagesrv.capture")
    mac = setup_build_machine(server)
    created = make_task(server, mac).json()
    tid, image_id = created["id"], created["image_id"]
    head = _upload(server, tid)
    # The PUT answers 202 and leaves the task `verifying`; nobody verifies it here.
    monkeypatch.setattr(capture, "start_verification", lambda *a: None)
    assert _put_manifest(server, tid, head).status_code == 202

    def boom(*args, **kwargs):
        raise RuntimeError("disk fell off the bus")

    monkeypatch.setattr(capture, "_verify", boom)
    with capture._VERIFYING_LOCK:                          # as start_verification registers it
        capture._VERIFYING[tid] = threading.current_thread()

    result = capture.run_verification(server["ctx"].conn, images_root, tid)

    assert result == "failed"
    row = _task_row(server, tid)
    assert row["state"] == "failed", row
    assert "the verification on the server crashed" in row["error"], row
    assert "disk fell off the bus" in row["error"], row
    assert not (images_root / image_id).exists()          # the image never entered the library
    assert not staging_dir(images_root, tid).exists()
    assert any(tid in d and "crashed" in d for d in _journal(server["ctx"], "capture_failed"))
    assert tid not in capture._VERIFYING                  # the registration is cleaned
