"""#2148 (rc4, ESXi and Hyper-V 24/10): one console fingerprint, the right one.

First boot ran setup-boot-server before the NIC had its address: the console
certificate was issued for loopback + name, its fingerprint went into the
build log ("compare with this"), and seconds later the restart issued it again
for the address. The DCUI read ``serving/console.crt`` -- the previous
process's copy, until the new one rebuilt its listener -- and showed the first
fingerprint while the browser saw the second (22:37:58 on ESXi).

The contract here:

* the DCUI shows the certificate the console *presents* (a TLS handshake on
  loopback), never a copy on disk that may have outlived a restart;
* a console that does not answer is "unavailable", not the file's fingerprint;
* while first boot still runs (its certificate is about to be replaced) the
  DCUI shows no fingerprint at all;
* first boot's log carries no fingerprint; a manual setup-boot-server run still
  prints it.
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import ssl
import subprocess
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _identity(host: str) -> tuple[bytes, bytes]:
    from server.interserver.auth import generate_self_signed
    return generate_self_signed("imagesrv-console", ip_sans=[host], dns_sans=["fp-2148"])


@contextmanager
def _console(cert_pem: bytes, key_pem: bytes, tmp_path: Path):
    """A TLS listener on loopback that presents ``cert_pem``, as the console does."""
    (tmp_path / "live.crt").write_bytes(cert_pem)
    (tmp_path / "live.key").write_bytes(key_pem)
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(tmp_path / "live.crt", tmp_path / "live.key")
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(8)
    stop = threading.Event()

    def serve():
        while not stop.is_set():
            try:
                conn, _ = sock.accept()
            except OSError:
                return
            try:
                with ctx.wrap_socket(conn, server_side=True):
                    pass
            except (OSError, ssl.SSLError):
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    try:
        yield sock.getsockname()
    finally:
        stop.set()
        sock.close()
        thread.join(timeout=5)


def _free_port() -> tuple[str, int]:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()


def _snapshot(tmp_path: Path, monkeypatch, address, state: str):
    """collect_snapshot on an installed server whose disk holds a stale console
    identity (canonical and serving/ copy), with the console at ``address``."""
    from server.console import tls as console_tls
    from server.dcui import data
    from server.dcui.data import CommandResult, collect_snapshot

    data_dir = tmp_path / "data"
    tls_dir = data_dir / console_tls.CONSOLE_TLS_DIRNAME
    (tls_dir / "serving").mkdir(parents=True)
    old_cert, old_key = _identity("127.0.0.1")
    for d in (tls_dir, tls_dir / "serving"):
        (d / "console.crt").write_bytes(old_cert)
        (d / "console.key").write_bytes(old_key)
    status = tmp_path / "firstboot.status"
    status.write_text(f"state={state}\nservers_if=ens33\n", encoding="utf-8")
    sys_net = tmp_path / "net"
    (sys_net / "ens33").mkdir(parents=True)
    for name, value in (("address", "00:50:56:01:02:01"), ("carrier", "1"),
                        ("operstate", "up"), ("speed", "1000")):
        (sys_net / "ens33" / name).write_text(value, encoding="ascii")

    def runner(argv):
        if argv[:4] == ["ip", "-j", "address", "show"]:
            return CommandResult(0, json.dumps([{"ifname": "ens33", "operstate": "UP", "addr_info": [
                {"family": "inet", "local": "10.68.0.84", "prefixlen": 24}]}]))
        return CommandResult(1, "", "not here")

    # raising=False: the same test runs against the code before #2148
    monkeypatch.setattr(data, "CONSOLE_LOCAL", address, raising=False)
    snap = collect_snapshot(data_dir, runner=runner, sys_net=sys_net, status_file=status,
                            override_file=tmp_path / "none.conf", repo_dir=tmp_path / "repo")
    return snap, console_tls.fingerprint_sha256(old_cert)


def test_the_dcui_shows_what_the_console_presents_not_a_stale_serving_copy(tmp_path, monkeypatch):
    from server.console import tls as console_tls
    from server.dcui import view
    live_cert, live_key = _identity("10.68.0.84")
    with _console(live_cert, live_key, tmp_path) as address:
        snap, stale = _snapshot(tmp_path, monkeypatch, address, state="done")
    assert snap.fingerprint == console_tls.fingerprint_sha256(live_cert), \
        "the DCUI showed a fingerprint the console does not present"
    assert snap.fingerprint != stale
    assert "10.68.0.84" in snap.certificate_sans
    assert view.parse_not_after(snap.certificate_not_after) is not None, snap.certificate_not_after


def _clock(monkeypatch, start: float = 1000.0) -> dict:
    """#2155: the DCUI's monotonic clock, moved by hand; the grace starts clean."""
    from server.dcui import data
    clock = {"now": start}
    monkeypatch.setattr(data, "_monotonic", lambda: clock["now"], raising=False)
    monkeypatch.setattr(data, "_cert_unread_since", [], raising=False)
    return clock


def test_a_console_that_does_not_answer_is_unavailable_not_the_file(tmp_path, monkeypatch):
    """The window between restart and the new listener (ESXi: 22:37:53 -> 22:38:04)
    -- once it has lasted past the start-up grace (#2155)."""
    from server.dcui import data, view
    from datetime import datetime
    clock = _clock(monkeypatch)
    port = _free_port()
    _snapshot(tmp_path / "a", monkeypatch, port, state="done")
    clock["now"] += getattr(data, "CONSOLE_START_GRACE_S", 0)
    snap, stale = _snapshot(tmp_path / "b", monkeypatch, port, state="done")
    assert snap.fingerprint != stale, "a copy on disk was shown as the console's fingerprint"
    assert snap.fingerprint.startswith("unavailable")
    assert view.cert_status(snap, datetime.now())[0] == "bad"


# --- #2155: rc5 (25/10), every boot: red "could not read the certificate" for ~5 s

def test_2155_a_console_still_coming_up_is_neutral_not_a_red_problem(tmp_path, monkeypatch):
    """The listener answers seconds after systemd says active; until then the
    DCUI says "starting" -- not a red problem with "restart the services"."""
    from server.dcui import view
    from server.dcui.view import Extras
    from datetime import datetime
    clock = _clock(monkeypatch)
    port = _free_port()
    for _ in range(3):                      # 0 s, 5 s, 10 s: three DCUI refreshes
        snap, _stale = _snapshot(tmp_path / str(clock["now"]), monkeypatch, port, state="done")
        kind, pill = view.cert_status(snap, datetime.now())[:2]
        assert (kind, pill) == ("info", "עולה"), (snap.fingerprint, kind, pill)
        titles = [p.title for p in view.problems(snap, Extras(), datetime.now())]
        assert "לא הצלחנו לקרוא את תעודת האבטחה" not in titles, titles
        clock["now"] += 5


def test_2155_a_console_that_answers_resets_the_grace(tmp_path, monkeypatch):
    """A listener that answered and then stopped gets its own grace, measured
    from its own first failure -- not from a failure an hour ago."""
    from server.dcui import data, view
    from datetime import datetime
    clock = _clock(monkeypatch)
    port = _free_port()
    _snapshot(tmp_path / "a", monkeypatch, port, state="done")             # fails: grace starts
    clock["now"] += 3600
    live_cert, live_key = _identity("10.68.0.84")
    with _console(live_cert, live_key, tmp_path) as address:
        ok, _stale = _snapshot(tmp_path / "b", monkeypatch, address, state="done")
    assert ":" in ok.fingerprint
    clock["now"] += 5
    again, _stale = _snapshot(tmp_path / "c", monkeypatch, port, state="done")
    assert view.cert_status(again, datetime.now())[0] == "info", again.fingerprint
    clock["now"] += data.CONSOLE_START_GRACE_S
    late, _stale = _snapshot(tmp_path / "d", monkeypatch, port, state="done")
    assert view.cert_status(late, datetime.now())[0] == "bad", late.fingerprint


def test_while_first_boot_runs_no_fingerprint_is_shown(tmp_path, monkeypatch):
    """First boot replaces the certificate at its end: none, rather than one
    that is about to change -- even when a console already answers."""
    from server.console import tls as console_tls
    from server.dcui import view
    from datetime import datetime
    live_cert, live_key = _identity("127.0.0.1")
    with _console(live_cert, live_key, tmp_path) as address:
        snap, stale = _snapshot(tmp_path, monkeypatch, address, state="installing")
    assert snap.fingerprint not in (stale, console_tls.fingerprint_sha256(live_cert))
    assert ":" not in snap.fingerprint, snap.fingerprint
    assert view.cert_status(snap, datetime.now())[:2] == ("warn", "ממתינה")


# --- the build log ----------------------------------------------------------------

INSTALLER = REPO / "deploy" / "setup-boot-server.sh"
BASH = shutil.which("bash")
posix_bash = pytest.mark.skipif(BASH is None or os.name == "nt", reason="bash (Linux)")
FP = ":".join(["AB"] * 32)


def _summary(tmp_path: Path, **env: str) -> str:
    """setup-boot-server's summary section as it is: from CONSOLE_SHOWN= to the
    end of the heredoc it prints."""
    lines = INSTALLER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith("CONSOLE_SHOWN="))
    end = next(i for i in range(start, len(lines)) if lines[i] == "EOF")
    script = tmp_path / "summary.sh"
    script.write_text("\n".join(lines[start:end + 1]) + "\n", encoding="utf-8", newline="\n")
    base = {"CONSOLE_FP": FP, "CONSOLE_TLS_HOST": "", "ADMIN_PASS": "x", "ADMIN_USER": "admin",
            "SERVERS_IF": "ens33", "SERVERS_MODE": "static", "IFACE": "", "SERVER_URL": "",
            "TFTP_ROOT": "/srv/tftp", "DATA_DIR": "/var/lib/imagesrv", "IMAGES_DIR": "/srv/images"}
    got = subprocess.run([BASH, str(script)], capture_output=True, text=True, timeout=30,
                         env={**os.environ, **base, **env}, stdin=subprocess.DEVNULL)
    assert got.returncode == 0, got.stderr
    return got.stdout


@posix_bash
def test_first_boot_s_log_has_no_fingerprint_to_compare_with(tmp_path):
    out = _summary(tmp_path, IMAGESRV_CONSOLE_FP_ON_DCUI="1")
    assert FP not in out, "first boot's log still names the fingerprint it replaces"
    assert "DCUI" in out


@posix_bash
def test_a_manual_run_still_prints_the_fingerprint(tmp_path):
    """The guard: the rule is first boot's, not 'never print'."""
    assert f"SHA-256 {FP}" in _summary(tmp_path)


@posix_bash
def test_first_boot_runs_setup_boot_server_with_the_fingerprint_on_the_dcui(tmp_path):
    from test_firstboot_answers_network_2094 import _firstboot
    got, seen = _firstboot(tmp_path, lease_after=0)
    assert got.returncode == 0, got.stdout + got.stderr
    setup = [c for c in seen if c.startswith("setup-boot-server")]
    assert setup and all("fp_on_dcui=1" in c for c in setup), setup
