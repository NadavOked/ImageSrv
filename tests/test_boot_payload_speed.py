"""Prebuilt boot payload decisions (#1788): every refusal is named and rebuilds."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from native import requires_native

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "deploy" / "boot-payload.py"
FILES = ("initrd.img", "initrd.img.gui", "vmlinuz")
TOOLS = ("build_initramfs.sh", "boot-payload-build.sh", "agent-lib-flatten.sh", "initramfs-version.sh")


def run(*args: object, version: str = "v9.9.9", tmp: Path) -> subprocess.CompletedProcess[str]:
    """The tree has no .git, so the version comes from the ISO manifest -- as on a
    server installed from a --source ISO."""
    manifest = tmp / "iso-release.json"
    manifest.write_text(json.dumps({"imagesrv_tag": version}), encoding="utf-8")
    env = {**os.environ, "IMAGESRV_ISO_MANIFEST": str(manifest)}
    return subprocess.run([sys.executable, str(HELPER), *(str(a) for a in args)], env=env,
                          text=True, capture_output=True, stdin=subprocess.DEVNULL)


def fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    src = tmp_path / "src"
    (src / "agent").mkdir(parents=True)
    (src / "native-gui" / "src").mkdir(parents=True)
    (src / "tools").mkdir()
    (src / "deploy").mkdir()
    (src / "agent" / "init").write_text("agent\n", encoding="utf-8")
    (src / "native-gui" / "src" / "screens.c").write_text("gui\n", encoding="utf-8")
    for name in TOOLS:
        (src / "tools" / name).write_text(name + "\n", encoding="utf-8")
    (src / "deploy" / "boot-payload.py").write_bytes(HELPER.read_bytes())
    prebuilt, target = tmp_path / "prebuilt", tmp_path / "target"
    prebuilt.mkdir()
    for name in FILES:
        (prebuilt / name).write_bytes((name + " verified\n").encode())
    flags = tmp_path / "initrd.flags"
    flags.write_text("--skip-apt --output /srv/imagesrv/boot/initrd.img\n"
                     "--skip-apt --with-gui --output /srv/imagesrv/boot/initrd.img.gui\n",
                     encoding="utf-8", newline="\n")
    manifest = prebuilt / "boot-payload.json"
    made = run("create", "--source", src, "--kernel", "6.12-test", "--flags", flags,
               "--payload-dir", prebuilt, "--source-commit", "abc123", "--output", manifest,
               tmp=tmp_path)
    assert made.returncode == 0, made.stderr + made.stdout
    return src, prebuilt, target, flags


def decide(src: Path, prebuilt: Path, target: Path, flags: Path, **kw: str) -> subprocess.CompletedProcess[str]:
    manifest = kw.get("manifest", str(prebuilt / "boot-payload.json"))
    args: list[object] = ["decide", "--source", src, "--kernel", kw.get("kernel", "6.12-test"),
                          "--flags", flags, "--payload-dir", target]
    if manifest:
        args += ["--manifest", manifest, "--prebuilt-dir", prebuilt]
    if kw.get("current_only"):
        args += ["--current-only"]
    return run(*args, version=kw.get("version", "v9.9.9"), tmp=src.parent)


def test_verified_prebuilt_is_installed_then_unchanged_inputs_keep_current(tmp_path: Path) -> None:
    src, prebuilt, target, flags = fixture(tmp_path)
    first = decide(src, prebuilt, target, flags)
    assert first.returncode == 0 and first.stdout.startswith("prebuilt\t"), first.stdout + first.stderr
    assert all((target / name).read_bytes() == (prebuilt / name).read_bytes() for name in FILES)
    assert json.loads((target / "boot-payload.json").read_text(encoding="utf-8"))["agent_version"] == "v9.9.9"
    second = decide(src, prebuilt, target, flags, manifest="")
    assert second.returncode == 0 and second.stdout.startswith("current\t"), second.stdout


@pytest.mark.parametrize(("mutation", "reason"), [
    ("missing", "not available"), ("kernel", "does not match installed kernel"),
    ("flags", "flags do not match"), ("hash", "sha256 mismatch"),
    ("inputs", "inputs do not match"), ("gui", "inputs do not match"),
    ("version", "is stamped v9.9.9, this tree is v9.9.10"), ("unknown", "source version is unknown"),
    ("manifest", "prebuilt manifest is invalid"),
])
def test_unusable_prebuilt_requests_rebuild_with_reason(tmp_path: Path, mutation: str, reason: str) -> None:
    src, prebuilt, target, flags = fixture(tmp_path)
    kwargs: dict[str, str] = {}
    if mutation == "missing": kwargs["manifest"] = ""
    elif mutation == "kernel": kwargs["kernel"] = "6.13-other"
    elif mutation == "flags": flags.write_text("--skip-apt --output /different/initrd.img\n", encoding="utf-8")
    elif mutation == "hash": (prebuilt / "initrd.img").write_bytes(b"corrupt")
    elif mutation == "inputs": (src / "agent" / "init").write_text("changed\n", encoding="utf-8")
    # native-gui is compiled into initrd.img.gui: a GUI change is a changed input
    elif mutation == "gui": (src / "native-gui" / "src" / "screens.c").write_text("new\n", encoding="utf-8")
    # the stations compare the stamped version with the server's: a new tag is a new payload
    elif mutation == "version": kwargs["version"] = "v9.9.10"
    elif mutation == "unknown": kwargs["version"] = ""
    else: (prebuilt / "boot-payload.json").write_text("{}", encoding="utf-8")
    proc = decide(src, prebuilt, target, flags, **kwargs)
    assert proc.returncode == 10, proc.stdout + proc.stderr
    assert proc.stdout.startswith("rebuild\t") and reason in proc.stdout, proc.stdout
    assert not any((target / name).exists() for name in FILES)


def test_corrupt_current_payload_is_not_treated_as_unchanged(tmp_path: Path) -> None:
    src, prebuilt, target, flags = fixture(tmp_path)
    assert decide(src, prebuilt, target, flags).returncode == 0
    (target / "vmlinuz").write_bytes(b"corrupt")
    proc = decide(src, prebuilt, target, flags, manifest="", current_only="1")
    assert proc.returncode == 10 and proc.stdout.startswith("rebuild\t"), proc.stdout
    assert "could not be verified" in proc.stdout


def test_a_new_version_is_not_current_even_when_no_file_changed(tmp_path: Path) -> None:
    """Without the version in the inputs, every station showed 'the initrd is older
    than the server' after an update that did not touch agent/ (#1788 review)."""
    src, prebuilt, target, flags = fixture(tmp_path)
    assert decide(src, prebuilt, target, flags).returncode == 0
    proc = decide(src, prebuilt, target, flags, manifest="", current_only="1", version="v9.9.10")
    assert proc.returncode == 10 and proc.stdout.startswith("rebuild\t"), proc.stdout


def test_a_payload_built_on_the_server_is_current_on_the_next_run(tmp_path: Path) -> None:
    """boot-payload-build.sh writes the manifest after a rebuild; the next run of the
    same version keeps it without a separate input file."""
    src, prebuilt, target, flags = fixture(tmp_path)
    target.mkdir()
    for name in FILES:
        (target / name).write_bytes((name + " built here\n").encode())
    made = run("create", "--source", src, "--kernel", "6.12-test", "--flags", flags,
               "--payload-dir", target, "--source-commit", "abc123",
               "--output", target / "boot-payload.json", tmp=tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr
    proc = decide(src, prebuilt, target, flags, manifest="", current_only="1")
    assert proc.returncode == 0 and proc.stdout.startswith("current\t"), proc.stdout


@requires_native("git", why="the input list of a git tree is `git ls-files`")
def test_in_a_git_tree_build_artifacts_are_not_inputs(tmp_path: Path) -> None:
    """The ISO builds the payload in a clone where `make -B` leaves the GUI binary in
    native-gui/; the installed tree is a fresh copy. Only tracked files are inputs,
    and the version is `git describe`, as build_initramfs.sh stamps it."""
    src, prebuilt, target, flags = fixture(tmp_path)

    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(src), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                       check=True, capture_output=True, stdin=subprocess.DEVNULL)
    git("init", "-q")
    git("add", "-A")
    git("commit", "-qm", "tree")
    git("tag", "v1.2.3")
    made = run("create", "--source", src, "--kernel", "6.12-test", "--flags", flags,
               "--payload-dir", prebuilt, "--source-commit", "abc123",
               "--output", prebuilt / "boot-payload.json", tmp=tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr
    doc = json.loads((prebuilt / "boot-payload.json").read_text(encoding="utf-8"))
    assert doc["agent_version"] == "v1.2.3"
    (src / "native-gui" / "imagesrv-station-gui").write_bytes(b"\x7fELF built")   # untracked artifact
    first = decide(src, prebuilt, target, flags)
    assert first.returncode == 0 and first.stdout.startswith("prebuilt\t"), first.stdout
    (src / "native-gui" / "src" / "screens.c").write_text("edited\n", encoding="utf-8")   # tracked change
    again = decide(src, prebuilt, target, flags, manifest="", current_only="1")
    assert again.returncode == 10, again.stdout


def _release(src: Path, flags: Path, target: Path, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """The release script of the tree, with the real server.agent.tls (the fake tree has no server/)."""
    shutil.copy(ROOT / "deploy" / "boot-payload-release.sh", src / "deploy" / "boot-payload-release.sh")
    manifest = tmp_path / "iso-release.json"
    manifest.write_text(json.dumps({"imagesrv_tag": "v9.9.9"}), encoding="utf-8")
    env = {**os.environ, "IMAGESRV_ISO_MANIFEST": str(manifest), "IMAGESRV_DATA_DIR": str(tmp_path / "data"),
           "PYTHONPATH": str(ROOT)}
    return subprocess.run(["bash", str(src / "deploy" / "boot-payload-release.sh"), "v9.9.9", str(src),
                           str(flags), str(target), "6.12-test"], env=env, text=True,
                          capture_output=True, stdin=subprocess.DEVNULL)


def _site_ca(tmp_path: Path, where: str = "data") -> Path:
    from server.agent import tls
    return tls.ensure_identity(tmp_path / where)[0].ca_path


def _built_here(src: Path, flags: Path, target: Path, ca: Path, tmp_path: Path) -> None:
    """A payload as boot-payload-build.sh leaves it: the images, each with the CA it packed, and the manifest."""
    target.mkdir()
    for name in FILES:
        (target / name).write_bytes((name + " built here\n").encode())
    for name in ("initrd.img", "initrd.img.gui"):
        shutil.copyfile(ca, target / (name + ".ca.pem"))
        (target / (name + ".agent-name")).write_text("imagesrv-agent-0123456789abcdef\n", encoding="utf-8")
    made = run("create", "--source", src, "--kernel", "6.12-test", "--flags", flags, "--payload-dir", target,
               "--source-commit", "abc123", "--output", target / "boot-payload.json", tmp=tmp_path)
    assert made.returncode == 0, made.stdout + made.stderr


@requires_native("bash", posix=True, why="deploy/boot-payload-release.sh is bash")
def test_the_release_payload_is_never_installed_on_a_server_that_bakes_its_own_ca(tmp_path: Path) -> None:
    """#3580: the initrds of this server carry its own CA, so the generic payload of a release is not one it can
    use. The release script asks for a rebuild (10) and nothing reaches the payload directory -- a release asset
    that happens to be available changes nothing."""
    src, prebuilt, target, flags = fixture(tmp_path)
    proc = _release(src, flags, target, tmp_path)
    assert proc.returncode == 10 and proc.stdout.startswith("rebuild\t"), proc.stdout + proc.stderr
    assert not any((target / name).exists() for name in FILES)


@requires_native("bash", posix=True, why="deploy/boot-payload-release.sh is bash")
def test_a_payload_built_with_this_servers_ca_is_current_and_one_with_another_ca_is_not(tmp_path: Path) -> None:
    src, prebuilt, target, flags = fixture(tmp_path)
    _built_here(src, flags, target, _site_ca(tmp_path), tmp_path)
    same = _release(src, flags, target, tmp_path)
    assert same.returncode == 0 and same.stdout.startswith("current\t"), same.stdout + same.stderr
    # the same inputs, the same bytes -- baked for another server: this one's machines would refuse it
    other_target = tmp_path / "target-of-another-site"
    _built_here(src, flags, other_target, _site_ca(tmp_path, "other-site"), tmp_path)
    other = _release(src, flags, other_target, tmp_path)
    assert other.returncode == 10 and other.stdout.startswith("rebuild\t"), other.stdout + other.stderr


@requires_native("bash", posix=True, why="deploy/boot-payload-release.sh is bash")
def test_one_initrd_without_the_ca_makes_the_payload_not_current(tmp_path: Path) -> None:
    src, prebuilt, target, flags = fixture(tmp_path)
    _built_here(src, flags, target, _site_ca(tmp_path), tmp_path)
    (target / "initrd.img.gui.ca.pem").unlink()           # the GUI initrd lost its sidecar: not one CA for both
    proc = _release(src, flags, target, tmp_path)
    assert proc.returncode == 10 and proc.stdout.startswith("rebuild\t"), proc.stdout + proc.stderr


# The publishing side (manifest in the signed tag, files on the Release) is tested
# in tests/test_publish_to_public.py: tools/git/ is private, and this file is public.


def test_server_upgrade_rebuilds_with_a_reason_and_never_installs_unverified() -> None:
    upgrade = (ROOT / "tools" / "server-upgrade.sh").read_text(encoding="utf-8")
    release = (ROOT / "deploy" / "boot-payload-release.sh").read_text(encoding="utf-8")
    assert "[[ -f deploy/boot-payload-release.sh ]]" in upgrade   # an older tag: rebuild, not rollback
    assert "rebuilding boot payload" in upgrade
    assert 'rollback "החלטת מטען האתחול נכשלה' in upgrade
    # #3580: only the installed payload can be current; a release asset is never fetched or installed here
    assert "--current-only" in release and "--agent-ca" in release
    assert "--prebuilt-dir" not in release and "curl" not in release.lower()
