"""Socket-level checks for the two production listener configurations."""
import http.client
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import socket
import ssl
import subprocess
import threading
import time

import pytest
import uvicorn

from server.agent import tls
from server.core import ports
from server.main import agent_uvicorn_config
from test_boot_listener_3486 import REG, REG_IP, UNREG, _client, create_agent_app, create_boot_app, rt


@pytest.fixture
def listeners(rt, tmp_path):
    identity, _ = tls.ensure_identity(tmp_path)
    configs = [agent_uvicorn_config(create_boot_app(rt), "127.0.0.1", ports.BOOT),
               agent_uvicorn_config(create_agent_app(rt), "127.0.0.1", ports.AGENT,
                                    **identity.uvicorn_kwargs())]
    servers = [uvicorn.Server(config) for config in configs]
    threads = [threading.Thread(target=s.run, daemon=True) for s in servers]
    try:
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 5
        while not all(s.started for s in servers):
            assert time.monotonic() < deadline and all(t.is_alive() for t in threads)
            time.sleep(0.02)
        yield identity
    finally:
        for server in servers:
            server.should_exit = True
        for thread in threads:
            thread.join(5)


def test_boot_answers_plain_http_and_agent_rejects_plain_http(listeners):
    boot = http.client.HTTPConnection("127.0.0.1", ports.BOOT, timeout=3)
    boot.request("GET", "/boot/vmlinuz")
    assert boot.getresponse().read() == b"K" * 100
    boot.close()
    agent = http.client.HTTPConnection("127.0.0.1", ports.AGENT, timeout=3)
    with pytest.raises((http.client.RemoteDisconnected, ConnectionResetError)):
        agent.request("POST", "/api/v1/agent/hello", b"{}")
        agent.getresponse()
    agent.close()


def test_served_leaf_matches_only_the_baked_name(listeners):
    context = ssl.create_default_context(cafile=str(listeners.ca_path))
    with socket.create_connection(("127.0.0.1", ports.AGENT), timeout=3) as sock:
        with context.wrap_socket(sock, server_hostname=listeners.name) as channel:
            assert channel.getpeercert()["subjectAltName"] == (("DNS", listeners.name),)
    with socket.create_connection(("127.0.0.1", ports.AGENT), timeout=3) as sock:
        with pytest.raises(ssl.SSLCertVerificationError, match="Hostname mismatch"):
            context.wrap_socket(sock, server_hostname="imagesrv-agent-0000000000000000")


def test_agent_rejects_certificate_for_wrong_name(listeners, tmp_path):
    # Keep the correct CA and address. Only the expected SAN changes.
    root = Path(__file__).resolve().parents[1]
    name_file = tmp_path / "name"
    env = dict(os.environ, IMAGESRV_SERVER=f"https://127.0.0.1:{ports.AGENT}",
               SERVER_CA_FILE=listeners.ca_path.as_posix(), AGENT_NAME_FILE=name_file.as_posix(),
               RUN_DIR=tmp_path.as_posix(), NO_PROXY="*", no_proxy="*")
    # Schannel needs this for a private CA without a revocation service.
    script = """
. agent/lib/core/tls.sh && tls_config || exit 2
. agent/lib/core/hello.sh
curl() { command curl --ssl-no-revoke "$@"; }
die_local() { echo "$1" >&2; exit 60; }
HTTP_RETRIES=0 HTTP_TIMEOUT=3
hello_post "$SERVER/api/v1/agent/hello" "$AGENT_NAME_FILE" "$RUN_DIR/resp"
"""
    for expected, correct in [(listeners.name, True), ("imagesrv-agent-0000000000000000", False)]:
        name_file.write_text(expected + "\n", encoding="ascii", newline="\n")
        result = subprocess.run([shutil.which("bash"), "-c", script], env=env, cwd=root,
                                capture_output=True, encoding="utf-8", errors="replace", timeout=10,
                                stdin=subprocess.DEVNULL)
        if correct:
            # The test body is invalid JSON. An HTTP error proves the authenticated exchange.
            assert result.returncode == 22, result.stderr
        else:
            assert result.returncode == 60, result.stderr
            assert expected in result.stderr and "אימות תעודת השרת נכשל" in result.stderr


# --- #3580 review: what the first cut left unchecked -----------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")


def _hello(identity, tmp_path, *, ca, name, prelude=""):
    """The real hello_post against the real listener; the agent trusts `ca` and expects `name`."""
    name_file = tmp_path / "name"
    name_file.write_text(name + "\n", encoding="ascii", newline="\n")
    env = dict(os.environ, IMAGESRV_SERVER=f"https://127.0.0.1:{ports.AGENT}", SERVER_CA_FILE=Path(ca).as_posix(),
               AGENT_NAME_FILE=name_file.as_posix(), RUN_DIR=tmp_path.as_posix(), NO_PROXY="*", no_proxy="*")
    script = """
. agent/lib/core/tls.sh && tls_config || exit 2
. agent/lib/core/hello.sh
curl() { command curl --ssl-no-revoke "$@"; }
die_local() { echo "$1" >&2; exit 60; }
HTTP_RETRIES=0 HTTP_TIMEOUT=3
hello_post "$SERVER/api/v1/agent/hello" "$AGENT_NAME_FILE" "$RUN_DIR/resp"
""".replace("HTTP_RETRIES=0", prelude + "\nHTTP_RETRIES=0")
    return subprocess.run([BASH, "-c", script], env=env, cwd=ROOT, capture_output=True, encoding="utf-8",
                          errors="replace", timeout=10, stdin=subprocess.DEVNULL)


def test_a_server_signed_by_another_site_ca_is_refused_by_name(listeners, tmp_path):
    # The name is right; the CA baked into this initrd is another server's. No hello, no fallback, a named failure.
    other, _ = tls.ensure_identity(tmp_path / "other-site")
    result = _hello(listeners, tmp_path, ca=other.ca_path, name=listeners.name)
    assert result.returncode == 60, result.stderr
    assert listeners.name in result.stderr and "אימות תעודת השרת נכשל" in result.stderr


BLOB = bytes((n * 7) % 251 for n in range(600))


def _range_server(identity):
    class Handler(http.server.BaseHTTPRequestHandler):
        seen: list = []

        def log_message(self, *args):
            pass

        def do_GET(self):
            self.seen.append(self.headers.get("Range"))
            first, last = (int(n) for n in re.fullmatch(r"bytes=(\d+)-(\d+)", self.headers["Range"]).groups())
            body = BLOB[first:last + 1]
            self.send_response(206)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(identity.cert_path, identity.key_path)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, Handler.seen


@pytest.mark.parametrize("script,ranges,call,setup", [
    ("chunkfetch.sh", "fetch.ranges", "blk", '_cf_url="$SERVER/api/v1/images/i/files/f"'),
    ("chunkfeed.sh", "feed.chunks.new", 'blk "$SERVER" i f', ":"),
])
def test_every_range_of_the_chunk_curl_config_verifies_the_server(tmp_path, script, ranges, call, setup):
    """`next` in a curl config resets the options of the transfer: the CA, the connect-to and the https-only
    rule must be written again for every range after the first, or those ranges meet the server unverified (or
    not at all). The awk program that ships in chunkfetch.sh and chunkfeed.sh runs here against a real TLS
    server: three ranges, one curl, every byte arrives."""
    identity, _ = tls.ensure_identity(tmp_path / "data")
    server, seen = _range_server(identity)
    try:
        (tmp_path / "idx").write_text("hdr\n" + "".join(f"{n * 100} 100\n" for n in range(6)))
        (tmp_path / "name").write_text(identity.name + "\n")
        (tmp_path / ranges).write_text("0-1,3-3,5-5\n")
        lines = (ROOT / "agent/lib/restore" / script).read_text(encoding="utf-8").splitlines()
        first = next(i for i, line in enumerate(lines) if line.lstrip().startswith("tr ") and ranges in line)
        last = next(i for i in range(first, len(lines)) if "END { exit made == n" in lines[i])
        block = "\n".join(lines[first:last + 1]).rstrip("\\ ")
        out = tmp_path / "all.curl"
        block = re.sub(r'> "\$CHUNK_DIR/\w+\.curl"$', f'> "{out.as_posix()}"', block)
        assert out.as_posix() in block
        env = dict(os.environ, IMAGESRV_SERVER=f"https://127.0.0.1:{server.server_address[1]}",
                   SERVER_CA_FILE=identity.ca_path.as_posix(), AGENT_NAME_FILE=(tmp_path / "name").as_posix(),
                   RUN_DIR=tmp_path.as_posix(), NO_PROXY="*", no_proxy="*")
        shell = f"""
. agent/lib/core/tls.sh && tls_config || exit 2
CHUNK_DIR={tmp_path.as_posix()}; CHUNK_IDX={(tmp_path / "idx").as_posix()}; WAIT_STREAM_STALL_S=20; HTTP_TIMEOUT=5
{setup}
blk() {{
{block}
}}
{call} || exit 3
curl -sS --fail-early --ssl-no-revoke -K "{out.as_posix()}" > "{(tmp_path / "all.bin").as_posix()}"
"""
        run = subprocess.run([BASH, "-c", shell], env=env, cwd=ROOT, capture_output=True, encoding="utf-8",
                             errors="replace", timeout=30, stdin=subprocess.DEVNULL)
        assert run.returncode == 0, run.stderr
        assert (tmp_path / "all.bin").read_bytes() == BLOB[0:200] + BLOB[300:400] + BLOB[500:600]
        assert seen == ["bytes=0-199", "bytes=300-399", "bytes=500-599"]
    finally:
        server.shutdown()


def test_init_never_reboots_for_a_tls_config_it_cannot_make(tmp_path):
    """A rebooting init loops PXE -> GRUB -> init for a machine whose trust files are wrong; the agent refuses
    the server by name (hello.sh) and boots the local disk. Here the config fails: no `fail`, no trace."""
    lines = (ROOT / "agent/init").read_text(encoding="utf-8").splitlines()
    crumb = next(i for i, line in enumerate(lines) if "trace_step agent-net" in line)
    first = max(i for i in range(crumb) if lines[i].startswith('if [ -f "$LIB_DIR/common.sh" ]'))
    last = next(i for i in range(crumb, len(lines)) if lines[i] == "fi")
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "common.sh").write_text("# sourced earlier in the real init; trace_step is defined below\n", newline="\n")
    results = {}
    for outcome in ("0", "1"):
        (lib / "tls.sh").write_text(f'tls_config() {{ TLS_WHY="TLS: no trust"; return {outcome}; }}\n', newline="\n")
        shell = (f'LIB_DIR={lib.as_posix()}; IFACE=eth0; _up=x; fail() {{ echo "FAIL $*"; exit 9; }}\n'
                 'trace_step() { echo "TRACE $*"; }\n' + "\n".join(lines[first:last + 1]))
        results[outcome] = subprocess.run([BASH, "-c", shell], capture_output=True, encoding="utf-8", timeout=10,
                                          stdin=subprocess.DEVNULL)
    assert results["0"].returncode == 0 and "TRACE agent-net up=x" in results["0"].stdout
    assert results["1"].returncode == 0, results["1"].stdout
    assert "FAIL" not in results["1"].stdout and "TRACE" not in results["1"].stdout
    assert "TLS: no trust" in results["1"].stdout


def test_the_network_report_looks_up_the_address_not_the_certificate_name(tmp_path):
    """$SERVER is the certificate name now; the netprobe must still report the address the machine reaches,
    and a name that DNS does not know must not become `dns: resolves false`."""
    run, root, sys_net = tmp_path / "run", tmp_path / "root", tmp_path / "root/sys/class/net/eth0"
    sys_net.mkdir(parents=True)
    run.mkdir()
    (sys_net / "carrier").write_text("1\n")
    (root / "etc").mkdir()
    (root / "etc/resolv.conf").write_text("nameserver 10.44.0.2\n")
    lookups = tmp_path / "lookups"
    stub = tmp_path / "nslookup"
    stub.write_text(f'#!/bin/sh\necho "$1" >> {lookups.as_posix()}\nexit 1\n', newline="\n")
    stub.chmod(0o755)
    curl = tmp_path / "curl"
    curl.write_text("#!/bin/sh\nprintf 404\nexit 0\n", newline="\n")
    curl.chmod(0o755)
    env = dict(os.environ, SYSROOT=root.as_posix(), RUN_DIR=run.as_posix(), IFACE="eth0",
               IMAGESRV_SERVER="https://10.44.0.1:7712", SERVER="https://imagesrv-agent-0123456789abcdef:7712",
               LLDPSNIFF="none-here", ETHTOOL="none-here", IPCMD="none-here", PING="none-here",
               NSLOOKUP=stub.as_posix(), CURL=curl.as_posix())
    script = (". agent/lib/core/common.sh; . agent/lib/hw/netprobe.sh; . agent/lib/hw/netreport.sh; "
              'printf "{\\"x\\":0%s}" "$(_netprobe_fresh)"')
    result = subprocess.run([BASH, "-c", script], env=env, cwd=ROOT, capture_output=True, encoding="utf-8",
                            timeout=10, stdin=subprocess.DEVNULL)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)["netprobe"]["report"]
    assert report["dns"]["resolves"] is None and not lookups.exists()
    assert report["tcp"][0]["host"] == "10.44.0.1" and report["tcp"][0]["port"] == 7712


def test_the_agent_boot_step_is_recorded_over_the_agent_listener_for_the_machine_itself(rt):
    """init's breadcrumb moved from the plain /boot/step to the TLS API: same identity rule (#2411)."""
    path = f"/api/v1/agent/boot-step?mac={REG}&s=agent-net"
    mine = _client(create_agent_app(rt), REG_IP, ports.AGENT).get(path)
    assert mine.status_code == 204, mine.text
    row = rt.conn.execute("SELECT step FROM boot_steps WHERE mac = ?", (REG,)).fetchone()
    assert row is not None and row["step"] == "agent-net"
    # a registered machine writing the trail of another machine's MAC: the lease of its address says it is not that MAC
    spoof = f"/api/v1/agent/boot-step?mac={UNREG}&s=agent-net"
    sent = _client(create_agent_app(rt), REG_IP, ports.AGENT).get(spoof)
    assert sent.status_code == 204, sent.text
    assert rt.conn.execute("SELECT step FROM boot_steps WHERE mac = ?", (UNREG,)).fetchone() is None


@pytest.mark.parametrize("server", ["http://10.44.0.1:7712", "10.44.0.1:7712", "https://imagesrv.local:7712",
                                    "https://10.44.0.1", ""])
def test_the_agent_takes_no_server_it_cannot_verify(tmp_path, server):
    """No http, no bare address, no name the PXE address was not given for: tls_config fails, says why, and leaves
    $SERVER unset -- nothing downstream can fall back to talking plain."""
    identity, _ = tls.ensure_identity(tmp_path / "data")
    (tmp_path / "name").write_text(identity.name + "\n")
    env = dict(os.environ, IMAGESRV_SERVER=server, SERVER_CA_FILE=identity.ca_path.as_posix(),
               AGENT_NAME_FILE=(tmp_path / "name").as_posix(), RUN_DIR=tmp_path.as_posix())
    env.pop("SERVER", None)
    result = subprocess.run([BASH, "-c", '. agent/lib/core/tls.sh; tls_config; echo "rc=$? server=[${SERVER:-}] why=$TLS_WHY"'],
                            env=env, cwd=ROOT, capture_output=True, encoding="utf-8", timeout=10,
                            stdin=subprocess.DEVNULL)
    assert re.search(r"rc=1 server=\[\] why=TLS: ", result.stdout), result.stdout + result.stderr


def test_a_refusal_on_a_machine_whose_clock_is_before_2000_says_so(listeners, tmp_path):
    """Every certificate starts 2000-01-01 (a dead BIOS battery still verifies the right server). Before that the
    right server is refused too, and the machine says it is its own clock, not the server, that is wrong (5a)."""
    other, _ = tls.ensure_identity(tmp_path / "other-site")
    stale = 'date() { case "$1" in +%s) echo 100 ;; *) command date "$@" ;; esac; }'
    said = _hello(listeners, tmp_path, ca=other.ca_path, name=listeners.name, prelude=stale).stderr
    assert "לפני 2000-01-01" in said and "לא חושדים בשרת" in said, said
    fine = _hello(listeners, tmp_path, ca=other.ca_path, name=listeners.name).stderr
    assert "אימות תעודת השרת נכשל" in fine and "2000-01-01" not in fine, fine
