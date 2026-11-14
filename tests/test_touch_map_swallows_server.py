"""מפת המגע: בליעות בשרת ובכלי הייצור שאיש לא החליט לפיהן — עכשיו שורה.

* A-DSK-17/18/22/23: `rmtree(..., ignore_errors=True)` על staging ותיקיות
  עבודה — דרך `server.core.cleanup.remove_tree`, שקורא בחזרה ואומר מה נשאר.
* A-NET-04: זנב יומן השולח שלא נקרא.
* A-NET-25: המאזין הבין-שרתי שהפסיק לקבל חיבורים (לא דרך `stop()`).
* A-NET-26: הגשר של המוניטור — קריאה מהמכונה שנכשלה, ומשימת ממסר שזרקה.
* C-FIL-05: שם ה-initiator של iSCSI כש-machine-id לא נקרא.
* C-FIL-06/07, C-CFG-08/10, C-FIL-03: כלי הבנייה וההתקנה.
"""

from __future__ import annotations

import asyncio
import errno
import importlib.util
import logging
import re
import sqlite3
import subprocess
import threading
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, posix

REPO = Path(__file__).resolve().parent.parent


# --- remove_tree ---------------------------------------------------------------


def test_a_tree_that_goes_is_true_and_says_nothing(tmp_path, caplog):
    from server.core.cleanup import remove_tree
    (tmp_path / "w" / "sub").mkdir(parents=True)
    (tmp_path / "w" / "sub" / "f").write_text("x")
    with caplog.at_level(logging.WARNING):
        assert remove_tree(tmp_path / "w", "test area") is True
    assert not (tmp_path / "w").exists()
    assert remove_tree(tmp_path / "never-was", "test area") is True   # אין = מה שרצינו
    assert not caplog.records


def test_a_tree_that_stays_is_false_and_said(tmp_path, caplog, monkeypatch):
    from server.core import cleanup
    (tmp_path / "w").mkdir()

    def refuse(path, onexc=None, **_kw):
        onexc(None, str(path), PermissionError(errno.EACCES, "Permission denied"))

    monkeypatch.setattr(cleanup.shutil, "rmtree", refuse)
    with caplog.at_level(logging.WARNING, logger="imagesrv.cleanup"):
        assert cleanup.remove_tree(tmp_path / "w", "capture staging of t1") is False
    text = caplog.text
    assert "capture staging of t1 left behind at" in text and "Permission denied" in text, text


def _keep_everything(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "rmtree", lambda path, *a, **k: None)


def test_a_failed_captures_staging_that_stays_is_said(tmp_path, caplog, monkeypatch):
    """A-DSK-22/23: `discard_failed_staging` (#1915) — האזור לא נמחק, ועכשיו יש שורה."""
    from server.core import tasks
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE tasks (id TEXT, type TEXT, state TEXT)")
    conn.execute("INSERT INTO tasks VALUES ('ab12', 'capture', 'failed')")
    staging = tasks.staging_dir(tmp_path, "ab12")
    staging.mkdir()
    _keep_everything(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="imagesrv.cleanup"):
        tasks.discard_failed_staging(conn, tmp_path, "ab12")
    assert staging.exists()
    assert "capture staging of ab12 left behind" in caplog.text, caplog.text


def test_a_vm_leftover_that_stays_is_said(tmp_path, caplog, monkeypatch):
    """A-DSK-17: `_clear_stale` — שאריות שמירה קודמת של משתמש ה-VM."""
    from server.vm import vmdisk
    work, run = tmp_path / "work", tmp_path / "run"
    (work / "unpriv").mkdir(parents=True)
    (run / "u-1").mkdir(parents=True)
    _keep_everything(monkeypatch)
    with caplog.at_level(logging.WARNING, logger="imagesrv.cleanup"):
        vmdisk._clear_stale(work, run)
    assert "VM save leftover (unpriv) left behind" in caplog.text, caplog.text
    assert "VM save leftover left behind" in caplog.text, caplog.text


# --- A-NET-04: the sender's log tail ----------------------------------------------


def test_an_unreadable_sender_log_is_said(tmp_path, caplog):
    from server.rounds.sender import sender_log_tail
    with caplog.at_level(logging.WARNING, logger="imagesrv.sender"):
        assert sender_log_tail(tmp_path / "no-such.log") == ""
    assert "udp-sender log" in caplog.text and "unreadable" in caplog.text, caplog.text


# --- A-NET-25: the interserver listener ----------------------------------------------


class _Sock:
    def __init__(self, exc: OSError):
        self.exc = exc

    def accept(self):
        raise self.exc


def _serve_once(monkeypatch, stopped: bool):
    from server.interserver import api
    monkeypatch.setattr(api.interserver_auth, "build_server_tls_context", lambda *_a: None)
    server = api.InterserverTLSServer.__new__(api.InterserverTLSServer)
    server._cert_pem = server._key_pem = b""
    server._sock = _Sock(OSError(errno.EMFILE, "Too many open files"))
    server._stop = threading.Event()
    if stopped:   # הלולאה נכנסת, ו-stop() נקרא בזמן ה-accept — ה-except רואה אותו
        flags = iter([False, True])
        server._stop.is_set = lambda: next(flags, True)
    server._serve()


def test_a_listener_that_stops_accepting_is_said(monkeypatch, caplog):
    with caplog.at_level(logging.ERROR):
        _serve_once(monkeypatch, stopped=False)
    assert "listener stopped accepting" in caplog.text and "Too many open files" in caplog.text


def test_a_listener_stopped_on_purpose_says_nothing(monkeypatch, caplog):
    """שומר: `stop()` סוגר את השקע וה-accept נכשל — זה הסוף המתוכנן, בלי שורה."""
    with caplog.at_level(logging.ERROR):
        _serve_once(monkeypatch, stopped=True)
    assert "listener stopped accepting" not in caplog.text


# --- A-NET-26: the monitor's bridge ---------------------------------------------------


class _Ws:
    def __init__(self, fail_send_after: int | None = None):
        self.sent: list[bytes] = []
        self.inbox = [b"RFB 003.008\n", b"\x01", b"\x01"]
        self.fail_after = fail_send_after
        self.closed = None

    async def send_bytes(self, data):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise RuntimeError("send after close")
        self.sent.append(data)

    async def receive(self):
        if self.inbox:
            return {"type": "websocket.receive", "bytes": self.inbox.pop(0)}
        await asyncio.sleep(3600)

    async def close(self, code=1000, reason=""):
        self.closed = (code, reason)


class _Writer:
    def write(self, _data):
        pass

    async def drain(self):
        pass


class _Reader:
    def __init__(self, exc: Exception | None):
        self.exc = exc

    async def read(self, _n):
        if self.exc:
            raise self.exc
        return b"frame"


def _bridge(ws, reader) -> str:
    pytest.importorskip("fastapi")
    from server.console.monitor import bridge_browser
    return asyncio.run(bridge_browser(ws, reader, _Writer(), idle_seconds=30))


def test_a_failed_read_from_the_machine_is_said(caplog):
    with caplog.at_level(logging.INFO, logger="imagesrv.monitor"):
        assert _bridge(_Ws(), _Reader(ConnectionResetError("reset by peer"))) == "machine"
    assert "read from the machine failed: reset by peer" in caplog.text, caplog.text


def test_a_relay_task_that_raised_is_said(caplog):
    """שלוש שליחות לחיצת-היד עוברות, והראשונה של המכונה זורקת. היום
    `gather(return_exceptions=True)` זרק את החריגה בשקט."""
    with caplog.at_level(logging.INFO, logger="imagesrv.monitor"):
        _bridge(_Ws(fail_send_after=3), _Reader(None))
    assert "relay task ended with RuntimeError: send after close" in caplog.text, caplog.text


# --- C-FIL-06: boot-payload's version ------------------------------------------------


def test_a_version_stamped_unknown_is_said(tmp_path, capsys, monkeypatch):
    spec = importlib.util.spec_from_file_location("boot_payload_cfil06", REPO / "deploy" / "boot-payload.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("IMAGESRV_ISO_MANIFEST", str(tmp_path / "no-manifest.json"))
    assert module.stamped_version(tmp_path) == "unknown"
    err = capsys.readouterr().err
    assert "version stamped as unknown" in err and "no-manifest.json unreadable" in err, err
    assert module.stamped_version(tmp_path, "v0.94.3") == "v0.94.3"
    assert capsys.readouterr().err == ""


# --- shell blocks of the build and install tools, run as they are --------------------


def _bash(script: str) -> subprocess.CompletedProcess:
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, timeout=60)


def _block(path: str, start: str, end: str) -> str:
    text = (REPO / path).read_text(encoding="utf-8")
    m = re.search(re.escape(start) + r".*?" + re.escape(end), text, re.S)
    assert m, f"{path}: the block moved -- update the extractor"
    return m.group(0)


INITIATOR = ("deploy/setup-boot-server.sh", "write_initiator_name() {", '    chmod 0644 "$1"\n}\n')


@requires_native(("bash", BASH))
@pytest.mark.parametrize("machine_id, existing, want, warned", [
    ("0123abcd\n", None, ":0123abcd", False),
    (None, "InitiatorName=iqn.keep:me\n", "iqn.keep:me", True),
    (None, None, "iqn.2026-09.imagesrv.srv1:", True),
])
def test_the_initiator_name_does_not_drift_when_machine_id_is_unreadable(tmp_path, machine_id,
                                                                         existing, want, warned):
    """C-FIL-05: היום — uuid אקראי חדש בכל הרצה, וה-ACL ב-NAS מפסיק להתאים בשקט."""
    target, mid = tmp_path / "initiatorname.iscsi", tmp_path / "machine-id"
    if machine_id is not None:
        mid.write_text(machine_id, newline="\n")
    if existing is not None:
        target.write_text(existing, newline="\n")
    proc = _bash("warn() { echo \"WARN $*\" >&2; }; hostname() { echo srv1; }; "
                 + _block(*INITIATOR) + f"write_initiator_name {posix(target)!r} {posix(mid)!r}")
    assert proc.returncode == 0, proc.stderr
    assert want in target.read_text(encoding="utf-8")
    assert ("WARN" in proc.stderr) is warned, proc.stderr
    if existing is not None:
        assert target.read_text(encoding="utf-8") == existing


@requires_native(("bash", BASH))
@pytest.mark.parametrize("rc, out, code, said", [
    (1, "", 0, ""),                                     # 1 = no package owns it: an answer
    (2, "", 1, "failed (rc=2) -- cannot tell its owner"),
    (0, "no-such-pkg-x: /usr/bin/env", 1, "package no-such-pkg-x owns packed file"),
])
def test_a_broken_dpkg_query_stops_the_license_pass(tmp_path, rc, out, code, said):
    """C-CFG-08: היום `|| true` — כשל של dpkg-query נראה כמו "אין בעלים",
    והודעת הרישיון נעלמה מהאימג' בשקט."""
    root = tmp_path / "root"
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "usr" / "bin" / "env").write_text("x")
    block = _block("tools/build_initramfs.sh", 'mapfile -d \'\' -t PACKED_FILES', "\ndone\n")
    proc = _bash(f"set -e; ROOT={posix(root)!r}; dpkg-query() {{ printf '%s\\n' {out!r}; return {rc}; }}; "
                 f"readlink() {{ echo /usr/bin/env; }}; {block}")
    assert proc.returncode == code, proc.stderr
    assert said in proc.stderr, proc.stderr


@requires_native(("bash", BASH))
def test_a_source_commit_that_falls_back_is_said(tmp_path):
    """C-FIL-07: אין checkout (התקנה מ-ISO) — הנפילה צפויה, ועכשיו גם נאמרת."""
    block = _block("tools/boot-payload-build.sh", "    SOURCE_COMMIT=$(git", 'recorded as $SOURCE_COMMIT"; }\n')
    proc = _bash(f"set -euo pipefail; say() {{ echo \"SAY $*\" >&2; }}; APP_DIR={posix(tmp_path)!r}; "
                 f"export GIT_CEILING_DIRECTORIES={posix(tmp_path.parent)!r}; "
                 f"EPOCH=1700000000; {block}\necho \"commit=$SOURCE_COMMIT\"")
    assert proc.returncode == 0, proc.stderr
    assert "commit=iso:1700000000" in proc.stdout
    assert "SAY no git commit in" in proc.stderr, proc.stderr


@requires_native(("bash", BASH))
def test_a_missing_pool_date_is_said(tmp_path):
    """C-CFG-10: שדה ריק במניפסט — עם שורה שאומרת למה."""
    line = next(ln for ln in (REPO / "tools/iso/build-iso.sh").read_text(encoding="utf-8").splitlines()
                if ln.startswith('[[ -s "$POOL/pool-date" ]]'))
    proc = _bash(f"say() {{ echo \"SAY $*\"; }}; POOL={posix(tmp_path)!r}; {line}")
    assert "pool-date is missing" in proc.stdout
    (tmp_path / "pool-date").write_text("2026-11-13\n")
    assert _bash(f"say() {{ echo \"SAY $*\"; }}; POOL={posix(tmp_path)!r}; {line}").stdout == ""


@requires_native(("bash", BASH))
def test_a_secret_that_was_not_removed_is_said(tmp_path):
    """C-FIL-03: `shred` נכשל ו-`rm` לא מחק — הסיסמה נשארה בדיסק, בלי שורה."""
    block = _block("tools/iso/firstboot-answers.sh", '    shred -u "$ANSWERS_SECRET"', "delete it by hand\"\n")
    secret = tmp_path / "answers.secret"
    secret.write_text("admin_pass=x\n")
    proc = _bash(f"log() {{ echo \"LOG $*\"; }}; shred() {{ return 1; }}; rm() {{ return 0; }}; "
                 f"ANSWERS_SECRET={posix(secret)!r}; {block}")
    assert "LOG WARNING:" in proc.stdout and "was not removed" in proc.stdout, proc.stdout + proc.stderr
    proc = _bash(f"log() {{ echo \"LOG $*\"; }}; ANSWERS_SECRET={posix(secret)!r}; {block}")
    assert not secret.exists() and proc.stdout == "", proc.stdout
