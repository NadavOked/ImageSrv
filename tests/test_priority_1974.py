"""‏#1974: השידור והכתיבה של הקליטה בעדיפות נמוכה מהקונסולה.

המדידה (זמן התגובה של ‎/api/console/overview בזמן שידור) בגוף ה-PR; כאן —
שהעדיפות באמת מוחלת, בכל אחד משלושת המקומות, ונקראת בחזרה מהקרנל כשאפשר.
"""

from __future__ import annotations

import ctypes
import os
import platform
import sys
import threading

import pytest

from server.system import priority
from server.rounds import sender

LINUX = sys.platform.startswith("linux")
#: ‏ioprio_get(2) — לקריאה בחזרה של מה ש-`lower_this_thread` כתב.
IOPRIO_GET = {"x86_64": 252, "aarch64": 31}.get(platform.machine())


def test_udp_sender_runs_under_nice_and_ionice(monkeypatch, tmp_path):
    seen = {}

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        return object()

    monkeypatch.setattr(sender, "SENDER_LOG", tmp_path / "udp-sender.log")
    monkeypatch.setattr(sender.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(priority.shutil, "which", lambda name: f"/usr/bin/{name}")
    cmd = ["udp-sender", "--file", str(tmp_path / "img" / "p1.esp.pcl.zst"), "--portbase", "9000"]  # פורט-הפצה-מכוון: argv ל-Popen מזויף
    sender.run_process(cmd)
    assert seen["argv"] == [*priority.BACKGROUND_PREFIX, *cmd]
    # ‏best-effort 7 ולא idle: idle מרעיב את השידור לכיתה כשיש עבודת דיסק אחרת.
    assert seen["argv"][:8] == ["nice", "-n", "10", "ionice", "-c", "2", "-n", "7"]


def test_a_missing_tool_is_said_and_the_sender_still_runs(monkeypatch, caplog):
    monkeypatch.setattr(priority.shutil, "which",
                        lambda name: None if name == "ionice" else f"/usr/bin/{name}")
    with caplog.at_level("WARNING", logger="imagesrv.priority"):
        assert priority.background_cmd(["udp-sender", "--x"]) == ["udp-sender", "--x"]
    assert "normal priority" in caplog.text


def test_lowering_a_thread_targets_that_thread_only():
    calls = []
    tid = threading.get_native_id()
    problems = priority.lower_this_thread(
        setpriority=lambda which, who, value: calls.append(("cpu", who, value)),
        syscall=lambda *args: calls.append(("io", *args)) and 0 or 0,
        machine="x86_64")
    assert problems == []
    assert ("cpu", tid, 10) in calls
    assert ("io", 251, 1, tid, (2 << 13) | 7) in calls


def test_a_refused_lowering_is_returned_not_swallowed():
    def refuse(*args):
        raise PermissionError("nope")

    problems = priority.lower_this_thread(setpriority=refuse, syscall=lambda *a: -1,
                                          machine="x86_64")
    assert any(p.startswith("cpu:") for p in problems)
    assert any(p.startswith("io:") for p in problems)


@pytest.mark.skipif(not LINUX, reason="setpriority/ioprio per thread are Linux")
def test_the_capture_pool_threads_are_lowered_and_the_caller_is_not():
    def measure():
        tid = threading.get_native_id()
        io = None
        if IOPRIO_GET is not None:
            libc = ctypes.CDLL(None, use_errno=True)
            io = libc.syscall(IOPRIO_GET, 1, tid)
        return threading.current_thread().name, os.getpriority(os.PRIO_PROCESS, tid), io

    before = os.getpriority(os.PRIO_PROCESS, threading.get_native_id())
    name, nice, io = priority.CAPTURE_IO.submit(measure).result(timeout=10)
    assert name.startswith("capture-io")
    assert nice == 10, f"תהליכון הקליטה ב-nice {nice}, לא 10"
    if IOPRIO_GET is not None:
        assert io == (2 << 13) | 7, f"ioprio של תהליכון הקליטה: {io}"
    assert os.getpriority(os.PRIO_PROCESS, threading.get_native_id()) == before


def test_capture_bytes_are_written_on_the_lowered_pool(server, monkeypatch):
    from server.library import capture
    from test_capture import do_capture, make_task, setup_build_machine

    threads = []
    real = capture._write_and_hash

    def spy(handle, digest, data):
        threads.append(threading.current_thread().name)
        return real(handle, digest, data)

    monkeypatch.setattr(capture, "_write_and_hash", spy)
    created = make_task(server, setup_build_machine(server)).json()
    assert do_capture(server, created["id"]).status_code == 202
    assert threads, "לא נכתב אף בייט — הבדיקה לא בדקה דבר"
    assert all(name.startswith("capture-io") for name in threads), threads


def test_the_verification_thread_lowers_itself(monkeypatch, tmp_path):
    from server.library import capture

    lowered = []
    monkeypatch.setattr(capture.priority, "lower_this_thread",
                        lambda: lowered.append(threading.current_thread().name) or [])
    monkeypatch.setattr(capture, "_verify", lambda conn, root, task_id: "gone")
    thread = capture.start_verification(None, tmp_path, "t-1974")
    thread.join(timeout=10)
    assert lowered == ["verify-t-1974"]
