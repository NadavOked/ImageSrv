"""#2623: the upgrade's recovery marker (and its DB snapshot) survive a power cut.

`os.replace` is a change of the directory; without an fsync of the directory a
cut right after it leaves only the `.tmp` (VM, 07/11: the marker vanished, the
recovery never ran, the console stayed `running`). The real server-upgrade.sh
runs with a `sitecustomize` that logs every `os.replace` and every fsync of a
directory made by its python helpers; the assertion is on the order: each
replace of the marker / the snapshot is followed, before anything else, by an
fsync of the data directory.
"""
from __future__ import annotations

import os

from test_installer_1131 import (  # noqa: F401 -- bindir is a fixture
    REPO, _fake_repo, _seed_db, _status, _upgrade_env, _upgrade_fakes, bindir, posix, pytestmark, run_bash,
)

SHIM = r'''import os, stat
_log = os.environ.get("FSYNC_LOG")
if _log:
    _fsync, _replace = os.fsync, os.replace
    def _w(line):
        with open(_log, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    def fsync(fd):
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            _w(f"fsync-dir {st.st_ino}")
        return _fsync(fd)
    def replace(src, dst, *a, **k):
        result = _replace(src, dst, *a, **k)
        _w(f"replace {os.path.basename(dst)}")
        return result
    os.fsync, os.replace = fsync, replace
'''


def test_the_marker_and_the_db_snapshot_are_followed_by_an_fsync_of_their_directory(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data, previous="v0.48.0")
    repo = _fake_repo(tmp_path, live_ok=True)
    _upgrade_fakes(bindir)
    shim = tmp_path / "shim"
    shim.mkdir()
    (shim / "sitecustomize.py").write_text(SHIM, encoding="utf-8", newline="\n")
    log = tmp_path / "fsync.log"
    (tmp_path / "units").mkdir()
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": "v0.49.0", "FSYNC_LOG": posix(log),
           "PYTHONPATH": f"{posix(shim)}{os.pathsep}{posix(REPO)}"}
    proc = run_bash(f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" v0.49.0 "{posix(repo)}"',
                    tmp_path, bindir, env=env)
    assert proc.returncode == 0, proc.stderr + (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    events = log.read_text(encoding="utf-8").splitlines()
    data_dir = f"fsync-dir {data.stat().st_ino}"
    for name in ("server-upgrade.state", "imagesrv.db.pre-upgrade"):
        positions = [i for i, e in enumerate(events) if e == f"replace {name}"]
        assert positions, (name, events)            # the shim saw it: the check below is not vacuous
        for i in positions:
            assert events[i + 1:i + 2] == [data_dir], (name, events)
    assert (_status(data) or {}).get("state") != "failed"
