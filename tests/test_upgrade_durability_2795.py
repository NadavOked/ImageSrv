"""#2795: what the upgrade marker describes is on the disk before the marker says so.

ESXi, 10/11: power cut ~2 s into `packages`. The marker had survived (fsync,
#2623), but 13 unit files were empty (an empty unit is masked -- the recovery
unit too), 41 objects in .git were empty and 65 files of the tree. The marker
described a state that was still in the page cache. Three things, each closing
another window, all through the real scripts:

* every move of the marker (a stage, `payload_saved`, its removal) comes after
  a `sync -f` of every filesystem the run writes to -- the tree, the units, the
  boot payload -- and git writes objects/index/refs with core.fsync;
* units and the recovery script's copy outside the tree are written beside the
  target, fsynced and renamed over it: a cut *inside* a stage leaves the old
  file or the new one, never an empty one; the unit runs that copy;
* a checkout cut before its files reached the disk is put back by the recovery
  with `checkout -f` -- a real git repo, its files truncated the way the cut
  left them; without -f git calls them local changes and refuses.

**Negative control**: tools/server-upgrade.sh, tools/server-upgrade-recover.sh,
deploy/imagesrv-upgrade-recover.service and deploy/setup-boot-server.sh of
origin/main (75b5ebf5) -- the PR lists which test fails on what.
"""
from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    INSTALLER, REPO, UPGRADE, _fake_repo, _run_upgrade, _seed_db, _status, bindir, calls, fake_tool,
    posix, pytestmark,
)
from test_upgrade_recovery_1893 import RECOVER, _recover

UNIT = REPO / "deploy" / "imagesrv-upgrade-recover.service"
COPY = "/usr/local/sbin/imagesrv-upgrade-recover"

# A `sync` that says what the marker said when it ran (<stage>/<payload_saved>,
# or none); for the fsync of a `.X.tmp`, the checksum of the temp and of X.
SYNC = r'''
m="$FAKE_MARKER"
if [ -e "$m" ]; then
    at="$(sed -n 's/.*"stage": "\([a-z]*\)".*/\1/p' "$m")"
    case "$(cat "$m")" in *'"payload_saved": true'*) at="$at/1" ;; *) at="$at/0" ;; esac
else
    at=none
fi
if [ "$1" = -f ]; then
    echo "syncfs $2 @$at" >> "$FAKE_LOG"
    exit 0
fi
name="${1##*/}"
case "$name" in
    .*.tmp)
        dest="${1%/*}/${name#.}"; dest="${dest%.tmp}"
        if [ -e "$dest" ]; then now="$(cksum < "$dest" | tr ' ' :)"; else now=missing; fi
        echo "fsync-tmp $dest tmp=$(cksum < "$1" | tr ' ' :) dest=$now" >> "$FAKE_LOG" ;;
    *) echo "fsync $1" >> "$FAKE_LOG" ;;
esac
'''

GIT = r'''
sub=$1; [ "$1" = -C ] && sub=$3
echo "git-env $sub ${GIT_CONFIG_KEY_0:-}=${GIT_CONFIG_VALUE_0:-}" >> "$FAKE_LOG"
case "$1" in describe) echo "$FAKE_TAG" ;; checkout) exit 0 ;;
    rev-parse) echo "commit-of-$FAKE_TAG" ;; rev-list) echo "commit-of-$4" ;; esac
'''


def _ck(data: bytes) -> str:
    out = subprocess.run(["cksum"], input=data, capture_output=True, check=True)
    return out.stdout.decode().strip().replace(" ", ":")


def _fakes(bindir: Path, monkeypatch, data: Path) -> None:
    fake_tool(bindir, "sync", SYNC)
    fake_tool(bindir, "systemctl", 'case "$1" in show) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;; esac')
    monkeypatch.setenv("FAKE_MARKER", posix(data / "server-upgrade.state"))


def _upgrade(tmp_path, bindir, monkeypatch, *, old_units: bool = False):
    """A full upgrade, with a `packages` stage, through stand-ins that log."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(RECOVER, repo / "tools" / "server-upgrade-recover.sh")
    (repo / "deploy" / "security-upgrade.sh").write_text("exit 0\n", encoding="utf-8", newline="\n")
    (tmp_path / "boot").mkdir()
    units, copy = tmp_path / "units", tmp_path / "sbin" / "imagesrv-upgrade-recover"
    if old_units:
        units.mkdir()
        copy.parent.mkdir()
        for dest in [copy, *(units / p.name for p in (repo / "deploy").glob("*.service"))]:
            dest.write_bytes(b"old\n")
    fake_tool(bindir, "git", GIT)
    _fakes(bindir, monkeypatch, data)
    proc = _run_upgrade(tmp_path, bindir, repo, data)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    return repo, units, copy


def test_every_move_of_the_marker_waits_for_a_sync_of_what_the_run_wrote(tmp_path, bindir, monkeypatch):
    repo, units, _copy = _upgrade(tmp_path, bindir, monkeypatch)
    seen = calls(tmp_path)
    # Each state the marker was in is left only after the tree, the units and the
    # payload were synced -- `packages` is where the ESXi cut found empty units.
    for state in ("fetch/0", "verify/0", "install/0", "packages/0", "payload/0", "payload/1", "restart/1"):
        for d in (repo, units, tmp_path / "boot"):
            assert f"syncfs {posix(d)} @{state}" in seen, (state, d.name, [c for c in seen if "syncfs" in c])
    # ...and the sync that closes `install` comes after the checkout it describes.
    checkout = seen.index(f"git -C {posix(repo)} checkout --detach v0.49.0")
    assert checkout < seen.index(f"syncfs {posix(repo)} @install/0")


def test_git_writes_its_objects_index_and_refs_with_fsync(tmp_path, bindir, monkeypatch):
    _upgrade(tmp_path, bindir, monkeypatch)
    for sub in ("fetch", "checkout"):
        env = [c for c in calls(tmp_path) if c.startswith(f"git-env {sub} ")]
        assert env, (sub, calls(tmp_path))
        key, _, value = env[0].split(" ", 2)[2].partition("=")
        assert key == "core.fsync" and {"objects", "index", "reference"} <= set(value.split(",")), env


def test_units_and_the_recovery_copy_are_fsynced_beside_the_target_and_renamed_over_it(
        tmp_path, bindir, monkeypatch):
    repo, units, copy = _upgrade(tmp_path, bindir, monkeypatch, old_units=True)
    seen = calls(tmp_path)
    targets = {copy: repo / "tools" / "server-upgrade-recover.sh",
               **{units / p.name: p for p in (repo / "deploy").glob("*.service")}}
    first, old = {}, _ck(b"old\n")
    for dest, src in targets.items():
        line = next((c for c in seen if c.startswith(f"fsync-tmp {posix(dest)} ")), None)
        assert line, (dest.name, seen)
        first[dest] = seen.index(line)
        # fsynced while the temp held the new file and the target still the old one
        assert line.endswith(f" tmp={_ck(src.read_bytes())} dest={old}"), line
        assert f"fsync {posix(dest.parent)}" in seen[first[dest]:], dest.name     # the rename, on disk
        assert dest.read_bytes() == src.read_bytes(), dest.name
    assert copy.stat().st_mode & stat.S_IXUSR
    assert not list(units.glob(".*")) and not list(copy.parent.glob(".*")), "a temp was left behind"
    assert first[copy] < first[units / "imagesrv-upgrade-recover.service"]   # the copy before its unit


# --- the recovery: a real git repo, cut in the middle of `install` ---------------------------

def _git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env=env, stdin=subprocess.DEVNULL).stdout.strip()


def _two_tags(repo: Path) -> dict[str, dict[str, bytes]]:
    """v0.48.0 and v0.49.0 -- a unit, a module and the recovery script differ, and
    v0.49.0 adds a file. Returns each tag's files."""
    trees = {"v0.48.0": {"deploy/imagesrv-server.service": b"[Unit]\nDescription=v0.48.0\n",
                         "deploy/imagesrv-upgrade-recover.service": UNIT.read_bytes(),
                         "tools/server-upgrade-recover.sh": RECOVER.read_bytes(),
                         "app.py": b"VERSION = 'v0.48.0'\n"}}
    trees["v0.49.0"] = {**trees["v0.48.0"], "deploy/imagesrv-server.service": b"[Unit]\nDescription=v0.49.0\n",
                        "tools/server-upgrade-recover.sh": RECOVER.read_bytes() + b"# v0.49.0\n",
                        "app.py": b"VERSION = 'v0.49.0'\n", "added.py": b"NEW = 1\n"}
    repo.mkdir()
    _git(repo, "init", "-q")
    for tag, files in trees.items():
        for rel, body in files.items():
            (repo / rel).parent.mkdir(parents=True, exist_ok=True)
            (repo / rel).write_bytes(body)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", tag)
        _git(repo, "tag", tag)
    return trees


def test_a_checkout_cut_before_its_files_reached_the_disk_is_put_back(tmp_path, bindir, monkeypatch):
    data, repo, units, boot = tmp_path / "data", tmp_path / "repo", tmp_path / "units", tmp_path / "boot"
    _seed_db(data)
    trees = _two_tags(repo)
    _git(repo, "checkout", "-q", "--detach", "v0.49.0")
    # The cut: every file this checkout wrote is empty; the units it installed
    # (atomically) are the new ones; the marker says `install`.
    for rel in ("deploy/imagesrv-server.service", "tools/server-upgrade-recover.sh", "app.py", "added.py"):
        (repo / rel).write_bytes(b"")
    units.mkdir()
    boot.mkdir()
    for rel in ("deploy/imagesrv-server.service", "deploy/imagesrv-upgrade-recover.service"):
        (units / Path(rel).name).write_bytes(trees["v0.49.0"][rel])
    (data / "server-upgrade.state").write_text(
        '{"repo": "%s", "previous": "v0.48.0", "tag": "v0.49.0", "stage": "install", "http_root": "%s",'
        ' "db_snap_ver": "14", "payload_saved": false}' % (posix(repo), posix(boot)), encoding="utf-8")
    _fakes(bindir, monkeypatch, data)
    fake_tool(bindir, "logger", "exit 0")
    fake_tool(bindir, "dpkg", "exit 0")
    proc = _recover(tmp_path, bindir, data)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert _git(repo, "rev-parse", "HEAD") == _git(repo, "rev-parse", "v0.48.0^{commit}")
    assert _git(repo, "status", "--porcelain") == ""
    for rel, body in trees["v0.48.0"].items():
        assert (repo / rel).read_bytes() == body, rel
    assert not (repo / "added.py").exists()
    assert (units / "imagesrv-server.service").read_bytes() == trees["v0.48.0"]["deploy/imagesrv-server.service"]
    copy = tmp_path / "sbin" / "imagesrv-upgrade-recover"
    assert copy.read_bytes() == trees["v0.48.0"]["tools/server-upgrade-recover.sh"]   # the copy went back too
    status = _status(data)
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert not (data / "server-upgrade.state").exists()
    seen = calls(tmp_path)
    # what was put back reached the disk while the marker still said `install`
    for d in (repo, units, boot):
        assert f"syncfs {posix(d)} @install/0" in seen, (d.name, seen)
    assert seen.index("systemctl daemon-reload") < seen.index(f"syncfs {posix(units)} @install/0")
    assert [c for c in seen if c.startswith(f"fsync-tmp {posix(units)}/imagesrv-server.service ")], seen


# --- the unit and the installer ---------------------------------------------------------------

def test_the_recovery_unit_runs_the_copy_outside_the_tree_and_the_tree_only_without_one(tmp_path):
    match = re.search(r"^ExecStart=/bin/sh -c '(.*)'$", UNIT.read_text(encoding="utf-8"), re.M)
    assert match, "ExecStart is not /bin/sh -c '...'"
    copy, tree = tmp_path / "copy", tmp_path / "tree.sh"
    copy.write_text("echo ran-the-copy\n", encoding="utf-8", newline="\n")
    tree.write_text("echo ran-the-tree\n", encoding="utf-8", newline="\n")
    cmd = (match.group(1).replace("$$", "$").replace(COPY, posix(copy))
           .replace("/opt/imagesrv/tools/server-upgrade-recover.sh", posix(tree)))

    def run() -> str:
        return subprocess.run(["sh", "-c", cmd], capture_output=True, text=True, check=True,
                              stdin=subprocess.DEVNULL).stdout
    assert run() == "ran-the-copy\n"
    copy.unlink()            # a server no #2795 upgrade has run on yet
    out = run()
    assert out.endswith("ran-the-tree\n") and "no " in out, out


def test_the_installer_and_both_scripts_agree_on_where_the_copy_is():
    for script in (UPGRADE, RECOVER):
        assert f'IMAGESRV_RECOVER_COPY:-{COPY}}}' in script.read_text(encoding="utf-8"), script.name
    text = INSTALLER.read_text(encoding="utf-8")
    copy_at = text.index(f'"$APP_DIR/tools/server-upgrade-recover.sh" {COPY}\n')
    assert copy_at < text.index('"$APP_DIR/deploy/imagesrv-upgrade-recover.service"')
