"""#1257: provenance survives initrd -> hello -> machine -> native header."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import hello_body
from native import requires_native
from test_agent import AGENT, BASH, posix
from test_capture import setup_build_machine
from test_cloner_gui import _build_gui, _pkgconfig, _png, _png_rgb

REPO = AGENT.parent


@pytest.mark.parametrize("value", ["v0.59.0", "v0.59.0-2-gabcd123", "unknown"])
def test_hello_version_is_stored_and_exposed_per_machine(server, value):
    mac = setup_build_machine(server)
    body = hello_body(mac)
    body["agent_version"] = value
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert state.get("agent_version") == value
    row = server["ctx"].conn.execute("SELECT * FROM net_devices WHERE mac=?", (mac,)).fetchone()
    assert dict(row).get("agent_version") == value
    other = server["anon"].get("/api/v1/agent/state?mac=aa:bb:cc:00:00:22").json()
    assert other.get("agent_version") is None


def test_console_lists_reported_version_per_machine(server):
    mac = setup_build_machine(server)
    machines = {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}
    assert machines[mac].get("agent_version", "absent") is None
    body = {**hello_body(mac), "agent_version": "v0.58.0"}
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    machines = {m["mac"]: m for m in server["admin"].get("/api/console/machines").json()}
    assert machines[mac].get("agent_version") == "v0.58.0"
    devices = {d["mac"]: d for d in server["admin"].get("/api/console/net").json()}
    assert devices[mac].get("agent_version") == "v0.58.0"


def test_version_change_is_not_lost_to_hello_throttling(server):
    mac = setup_build_machine(server)
    for version in ("v0.58.0", "v0.59.0", None):
        body = hello_body(mac)
        body["agent_version"] = version
        assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
        state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
        assert state.get("agent_version") == (version or "unknown")


@pytest.mark.parametrize("value", [True, "bad\nversion", "x" * 129, "bad|version"])
def test_invalid_version_is_explicitly_unknown(server, value):
    mac = setup_build_machine(server)
    body = {**hello_body(mac), "agent_version": value}
    assert server["anon"].post("/api/v1/agent/hello", json=body).status_code == 200
    state = server["anon"].get(f"/api/v1/agent/state?mac={mac}").json()
    assert state.get("agent_version") == "unknown"


def _no_verified_tag(monkeypatch, update) -> None:
    """#1297: the shown version prefers a *signed* tag at HEAD over describe.
    The public CI runs on exactly such a commit, so a test that injects
    ``describe`` must also say "no verified tag here" -- or the repo's own
    release tag answers instead of the injected value. A fresh cache too:
    it is per (repo, HEAD) for the process, and an earlier test filled it."""
    monkeypatch.setattr(update, "_tags_at_head", lambda _repo: [])
    monkeypatch.setattr(update, "_verify_installed_tag", lambda _repo, _tag: False)
    monkeypatch.setattr(update, "_DISPLAY_VERSION_CACHE", {}, raising=False)


@pytest.mark.parametrize("version", ["v0.59.0", None])
def test_state_reads_server_version_once_even_if_unavailable(request, monkeypatch, version):
    from server import update
    # No import of the new module: reverting all production files must reach
    # the endpoint and fail on its response, not during test collection.
    cached = sys.modules.get("server.agent_version")
    if cached:
        cached.server_version.cache_clear()
    calls = []
    def describe(path):
        calls.append(path)
        if version is None:
            raise OSError("git unavailable")
        return version
    monkeypatch.setattr(update, "_describe", describe)
    # #1868: a workstation checkout has .git, so HEAD keys a cache and a read
    # per poll would go unnoticed here, while a git-archive tree (the
    # Testrunner) has no HEAD to key on and spawns git every time. Force the
    # unreadable-HEAD path on every machine so this test sees the real count.
    def unreadable_head(_repo):
        raise update.VersionError("not-a-git-tree", "no .git")
    monkeypatch.setattr(update, "_head", unreadable_head)
    _no_verified_tag(monkeypatch, update)
    try:
        server = request.getfixturevalue("server")
        mac = setup_build_machine(server)
        reads = []                      # describe calls seen after each poll
        for _ in range(4):
            reply = server["anon"].get(f"/api/v1/agent/state?mac={mac}")
            assert reply.status_code == 200
            assert "server_version" in reply.json()
            assert reply.json()["server_version"] == version
            reads.append(len(calls))
        # Polling must not read again: the station router takes the version
        # once, when the app is built (station.py `running_version`). Without
        # a HEAD the app-startup warmup (#1297) is not shared with it, so two
        # reads in total is startup + router -- the 1 this test used to assert
        # held only where .git made the two share a cache entry (#1868).
        assert len(set(reads)) == 1, reads
        assert reads[-1] <= 2, reads
    finally:
        cached = sys.modules.get("server.agent_version")
        if cached:
            cached.server_version.cache_clear()


GIT = shutil.which("git")


@requires_native(("git", GIT))
def test_isolation_holds_on_a_head_with_a_verified_tag(tmp_path, monkeypatch):
    """The CI case, staged: HEAD carries a tag the verifier accepts. Without
    the isolation that tag is the answer; with it, the injected describe is."""
    from server import update
    repo = tmp_path / "repo"
    repo.mkdir()
    for argv in (["init", "-q"],
                 ["-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "commit", "-q",
                  "--allow-empty", "-m", "release"],
                 ["tag", "v9.9.9"]):
        subprocess.run([GIT, "-C", str(repo), *argv], check=True,
                       stdin=subprocess.DEVNULL, capture_output=True)
    monkeypatch.setattr(update, "_describe", lambda _repo: "v0.59.0")

    monkeypatch.setattr(update, "_verify_installed_tag", lambda _repo, tag: tag == "v9.9.9")
    monkeypatch.setattr(update, "_DISPLAY_VERSION_CACHE", {}, raising=False)
    assert update.current_version(update.default_hooks(), repo) == "v9.9.9"

    _no_verified_tag(monkeypatch, update)
    assert update.current_version(update.default_hooks(), repo) == "v0.59.0"


def shell(tmp_path, script):
    path = tmp_path / "check.sh"
    path.write_text(script, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, posix(path)], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", timeout=30)


@requires_native(("bash", BASH))
@pytest.mark.parametrize("stamp,expected", [("v0.59.0", "v0.59.0"), (None, "unknown")])
def test_running_hello_uses_stamp_or_unknown(tmp_path, stamp, expected):
    version_file = tmp_path / "agent-version"
    interface = tmp_path / "sys/class/net/eth0"
    interface.mkdir(parents=True)
    (interface / "address").write_text("aa:bb:cc:00:00:10\n")
    if stamp:
        version_file.write_text(stamp + "\n")
    result = shell(tmp_path, f'''
AGENT_VERSION_FILE={posix(version_file)!r}
RUN_DIR={posix(tmp_path)!r}
SYSROOT=$RUN_DIR; IFACE=eth0
. {posix(AGENT / 'lib/common.sh')!r}
. {posix(AGENT / 'lib/sysinfo.sh')!r}
list_macs() {{ echo aa:bb:cc:00:00:10; }}
detect_uuid() {{ :; }}
detect_firmware() {{ echo bios; }}
detect_secure_boot() {{ echo null; }}
detect_memory_bytes() {{ echo 1024; }}
disk_probe() {{ echo ok; }}
list_disks() {{ :; }}
IP=10.0.0.1
build_hello
''')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["agent_version"] == expected


@requires_native(("bash", BASH))
@pytest.mark.parametrize("explicit,git_version,expected", [
    ("v8.2.1", None, "v8.2.1"), ("", "v8.2.0-3-gabc", "v8.2.0-3-gabc"),
    ("", None, "unknown"), ("", "", "unknown"),
])
def test_builder_stamps_source_version(tmp_path, explicit, git_version, expected):
    tools_dir = tmp_path / "source/tools"
    tools_dir.mkdir(parents=True)
    helper = REPO / "tools/initramfs-version.sh"
    if helper.exists():
        shutil.copyfile(helper, tools_dir / helper.name)
    if git_version is not None:
        (tools_dir.parent / ".git").mkdir()
    # Run the real packaging stanza. On the baseline it creates the initrd
    # directories but never emits a version; the assertion concerns its output.
    builder = (REPO / "tools/build_initramfs.sh").read_text(encoding="utf-8")
    stanza = builder.split('mkdir -p "$ROOT"/{bin,', 1)[1].split("# --- busybox", 1)[0]
    stanza = 'mkdir -p "$ROOT"/{bin,' + stanza
    root = tmp_path / "initrd"
    result = shell(tmp_path, f'''
set -e
SCRIPT_DIR={posix(tools_dir)!r}
ROOT={posix(root)!r}
SOURCE_VERSION={explicit!r}
IMAGECTL_ISO_MANIFEST={posix(tmp_path / 'missing.json')!r}
git() {{ printf '%s\\n' {git_version or ''!r}; }}
{stanza}
''')
    assert result.returncode == 0, result.stderr
    stamp = root / "etc/imagectl/agent-version"
    assert stamp.exists(), "builder produced an initrd without source provenance"
    assert stamp.read_text().strip() == expected


@requires_native(("bash", BASH), "jq")
def test_iso_manifest_supplies_version_without_git(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    manifest = tmp_path / "iso-release.json"
    manifest.write_text(json.dumps({"imagectl_tag": "v0.59.0"}))
    # Use the same real builder stanza, with its helper resolved in the source tree.
    tools_dir = source / "tools"
    tools_dir.mkdir()
    helper = REPO / "tools/initramfs-version.sh"
    if helper.exists():
        shutil.copyfile(helper, tools_dir / helper.name)
    builder = (REPO / "tools/build_initramfs.sh").read_text(encoding="utf-8")
    stanza = 'mkdir -p "$ROOT"/{bin,' + builder.split('mkdir -p "$ROOT"/{bin,', 1)[1].split("# --- busybox", 1)[0]
    root = tmp_path / "initrd"
    result = shell(tmp_path, f'SCRIPT_DIR={posix(tools_dir)!r}\nROOT={posix(root)!r}\nSOURCE_VERSION=""\nIMAGECTL_ISO_MANIFEST={posix(manifest)!r}\n' + stanza)
    assert result.returncode == 0, result.stderr
    stamp = root / "etc/imagectl/agent-version"
    assert stamp.exists(), "ISO build omitted the agent version"
    assert stamp.read_text().strip() == "v0.59.0"


@requires_native(("bash", BASH), "jq")
def test_gui_snapshot_carries_local_and_server_versions(tmp_path):
    state = tmp_path / "station-input.json"
    state.write_text(json.dumps({"known": True, "name": "build", "disks": [], "task": None, "server_version": "v0.59.0"}))
    (tmp_path / "mode").write_text("build\n")
    result = shell(tmp_path, f'''
RUN_DIR={posix(tmp_path)!r}; GUI_DIR=$RUN_DIR
. {posix(AGENT / 'lib/guistate.sh')!r}
. {posix(AGENT / 'lib/askgui.sh')!r}
AGENT_VERSION=v0.58.0
http_get() {{ cat {posix(state)!r}; }}
class_deploy_on() {{ return 1; }}
tools_on() {{ return 1; }}
gui_shrink_state() {{ :; }}
disk_slot_records() {{ :; }}
gui_state
''')
    assert result.returncode == 0, result.stderr
    output = (tmp_path / "state").read_text()
    assert "agent_version=v0.58.0\n" in output
    assert "server_version=v0.59.0\n" in output


@requires_native(("bash", BASH), ("cc", shutil.which("cc") or shutil.which("gcc")),
                 ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")), posix=True)
def test_native_header_changes_with_versions_and_mismatch(tmp_path):
    binary = _build_gui(tmp_path)
    headers = []
    for i, versions in enumerate([("v0.59.0", "v0.59.0"), ("v0.58.0", "v0.59.0"), ("unknown", "v0.59.0")]):
        state = tmp_path / f"state-{i}"
        state.write_text(f"agent_version={versions[0]}\nserver_version={versions[1]}\nmessage=x|y\n")  # --png: "message" needs msg_title
        prefix = tmp_path / f"view-{i}"
        _png(binary, prefix, state)
        png = next(tmp_path.glob(f"view-{i}-menu-light.png"))   # the login card has no header bar
        width, height, channels, pixels = _png_rgb(png)
        headers.append(pixels[:width * channels * min(48, height)])
    assert headers[0] != headers[1], "mismatched initrd produced the same header"
    assert headers[1] != headers[2], "unknown provenance must not claim an old initrd"
