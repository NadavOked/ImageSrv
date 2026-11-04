"""#2155 section 3 (rc5, 25/10): first boot issues one console certificate, not two.

firstboot-answers runs setup-boot-server before it restarts networking.service
(setup-boot-server does no ifup: the web wizard is connected through that NIC).
A static NIC therefore had no address when setup-boot-server issued the
certificate: SAN 127.0.0.1 + name. The restart after the address arrived
(#2095) found the address missing from the SANs and issued a second one --
journal ``console_tls_regenerated`` with another fingerprint, the first kept as
``console.crt.prev``. Nobody had seen the first one (#2148 keeps it off the log
and the DCUI), so it was pure noise in the journal and on disk.

The fix: for a static servers NIC that has no address yet, the certificate is
issued for the address it is configured to get. A live address still wins, a
DHCP NIC without a lease is still loopback (its address is not known), and an
``ip`` that failed still stops the installer (principle 5).

The block is run as it stands in the installer (lines from
``CONSOLE_TLS_HOST=`` to ``CONSOLE_FP="(dry-run)"``), so the same test runs on
the code before the fix.
"""

from __future__ import annotations

from test_installer_1131 import BASH, INSTALLER, bindir, fake_tool, installer_function, run_bash  # noqa: F401
from native import requires_native

pytestmark = requires_native(("bash", BASH), why="the installer block is bash")


def _tls_host_block() -> str:
    lines = INSTALLER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, line in enumerate(lines) if line.startswith('CONSOLE_TLS_HOST="$CONSOLE_HOST"'))
    end = next(i for i in range(start, len(lines)) if lines[i].startswith('CONSOLE_FP="(dry-run)"'))
    return "\n".join(lines[start:end]) + "\n"


def _tls_host(tmp_path, bindir, *, mode: str, address_now: str = "", ip_fails: bool = False,
              console_host: str = "ens33"):
    if ip_fails:
        fake_tool(bindir, "ip", 'echo "Device \\"ens33\\" does not exist." >&2; exit 1')
    elif address_now:
        fake_tool(bindir, "ip", f"echo '2: ens33    inet {address_now}/24 brd 10.68.0.255 scope global ens33'")
    else:
        fake_tool(bindir, "ip", "true")
    script = ("set -euo pipefail\n"
              f'CONSOLE_HOST={console_host}; SERVERS_IF=ens33; SERVERS_MODE={mode}\n'
              'SERVERS_ADDR=10.68.0.84\n'
              + installer_function("iface_ip") + _tls_host_block()
              + 'echo "got=[$CONSOLE_TLS_HOST]"\n')
    return run_bash(script, tmp_path, bindir)


def test_a_static_nic_without_its_address_yet_gets_the_certificate_for_its_address(tmp_path, bindir):
    """rc5's first boot: static ens33, networking not restarted yet."""
    proc = _tls_host(tmp_path, bindir, mode="static")
    assert proc.returncode == 0, proc.stderr
    assert "got=[10.68.0.84]" in proc.stdout, proc.stdout


def test_a_live_address_still_wins(tmp_path, bindir):
    proc = _tls_host(tmp_path, bindir, mode="static", address_now="10.68.0.99")
    assert "got=[10.68.0.99]" in proc.stdout, proc.stdout + proc.stderr


def test_a_dhcp_nic_without_a_lease_is_still_loopback(tmp_path, bindir):
    """Its address is not known before the lease: no guess."""
    proc = _tls_host(tmp_path, bindir, mode="dhcp")
    assert proc.returncode == 0, proc.stderr
    assert "got=[]" in proc.stdout, proc.stdout


def test_a_console_on_another_nic_does_not_take_the_servers_address(tmp_path, bindir):
    proc = _tls_host(tmp_path, bindir, mode="static", console_host="ens34")
    assert "got=[]" in proc.stdout, proc.stdout + proc.stderr


def test_ip_that_failed_still_stops_the_installer(tmp_path, bindir):
    """Not "no address, so the configured one" (principle 5)."""
    proc = _tls_host(tmp_path, bindir, mode="static", ip_fails=True)
    assert proc.returncode != 0
    assert "got=" not in proc.stdout, proc.stdout


# --- the rest of the chain: why one certificate is enough --------------------------

def test_a_certificate_for_the_coming_address_survives_both_server_starts(tmp_path):
    """setup-boot-server issues it for 10.68.0.84; the server starts while the
    NIC has no address (loopback, #2095) and again after it has it -- neither
    replaces it, so nothing goes to the journal or to console.crt.prev."""
    from server.console import tls as console_tls
    first = console_tls.ensure_console_cert(tmp_path, "10.68.0.84", hostname="rc5-hv")
    before = console_tls.ensure_console_cert(tmp_path, "127.0.0.1", hostname="rc5-hv")
    after = console_tls.ensure_console_cert(tmp_path, "10.68.0.84", hostname="rc5-hv")
    assert not before.regenerated and not after.regenerated
    assert first.fingerprint_sha256 == before.fingerprint_sha256 == after.fingerprint_sha256
    prev = tmp_path / console_tls.CONSOLE_TLS_DIRNAME / (console_tls.CERT_NAME + console_tls.PREV_SUFFIX)
    assert not prev.exists()


def test_the_loopback_certificate_was_the_one_replaced(tmp_path):
    """What rc5 did: loopback first, then the address -- a second certificate."""
    from server.console import tls as console_tls
    console_tls.ensure_console_cert(tmp_path, "127.0.0.1", hostname="rc5-hv")
    after = console_tls.ensure_console_cert(tmp_path, "10.68.0.84", hostname="rc5-hv")
    assert after.regenerated
