"""‏#1193 — git writes happen inside the detached upgrade script.

The real bash script runs against PATH shims.  The assertions cover the
observable command order and the persisted status.  The fetch regression also
uses real git repositories confined to tmp_path; no installed unit is touched.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH,
    UPGRADE,
    _fake_repo,
    _seed_db,
    _status,
    _upgrade_env,
    calls,
    fake_tool,
    posix,
    run_bash,
)

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _git_shim(fetch: str = "exit 0", checkout: str = "exit 0",
              head: str = "fake-commit-for-$FAKE_TAG",
              tag_commit: str = "fake-commit-for-$4") -> str:
    """``rev-parse HEAD`` answers ``head``; ``rev-list -n 1 <tag>`` maps the
    tag it was asked about to its commit -- by default the two agree."""
    return f'''\
if [ "$1" = -C ]; then shift 2; fi
case "$1" in
  fetch) {fetch} ;;
  checkout) {checkout} ;;
  rev-parse) echo "{head}" ;;
  rev-list) echo "{tag_commit}" ;;
  *) echo "unexpected git $*" >&2; exit 64 ;;
esac
'''


def _run(tmp_path: Path, bindir: Path, repo: Path, data: Path, tag: str = "v0.53.0"):
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag}
    return run_bash(
        f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
        tmp_path,
        bindir,
        env=env,
    )


def _success_tools(bindir: Path) -> None:
    fake_tool(bindir, "git", _git_shim())
    # #1361: the server screen's unit answers for itself; FAKE_DCUI_* pick the
    # case (default: an ISO server whose screen is running).
    fake_tool(bindir, "systemctl", r'''
case "$1" in
  show) case "$*" in
    *imagesrv-dcui*) printf "LoadState=%s\nActiveState=%s\n" "${FAKE_DCUI_LOAD-loaded}" "${FAKE_DCUI_ACTIVE-active}" ;;
    *) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;;
  esac ;;
  is-active) case "$*" in *imagesrv-dcui*) [ -z "${FAKE_DCUI_DEAD:-}" ] || exit 3 ;; esac ;;
esac
exit 0''')
    fake_tool(bindir, "curl", "exit 0")


def test_fetch_and_checkout_precede_units_restart_and_verification(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _success_tools(bindir)

    proc = _run(tmp_path, bindir, repo, data)
    log = calls(tmp_path)

    assert proc.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    fetch = next(i for i, row in enumerate(log) if row.startswith("git -C ") and row.endswith(
        " fetch --no-tags origin refs/tags/v0.53.0:refs/tags/v0.53.0"))
    checkout = next(i for i, row in enumerate(log) if row.startswith("git -C ") and " checkout --detach v0.53.0" in row)
    units = log.index("systemctl daemon-reload")
    restart = log.index("systemctl restart imagesrv-server")
    active = log.index("systemctl is-active --quiet imagesrv-server")
    assert fetch < checkout < units < restart < active


def test_fetch_failure_records_git_error_and_touches_no_units_or_service(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(
        bindir,
        "git",
        _git_shim(fetch='echo "cannot open .git/FETCH_HEAD: Read-only file system" >&2; exit 23'),
    )
    fake_tool(bindir, "systemctl", 'echo "must not run" >&2; exit 70')
    fake_tool(bindir, "curl", 'echo "must not run" >&2; exit 71')

    proc = _run(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    status = _status(data)

    assert proc.returncode != 0
    assert status and status["state"] == "failed" and status["tag"] == "v0.53.0"
    assert status["reason"] == "fetch"
    assert "git fetch" in status["error"] and "v0.53.0" in status["error"]
    assert "Read-only file system" in status["error"]
    assert not any(" checkout " in row for row in log)
    assert not any(row.startswith("systemctl ") for row in log)
    assert not (tmp_path / "units" / "imagesrv-server.service").exists()


@requires_native(("git", shutil.which("git")), why="tag conflicts require real git")
def test_fetch_requested_tag_ignores_unrelated_local_tag_conflict(tmp_path, bindir):
    """#2237: execute the actual fetch block, not a copy of its command.

    Restoring the old production script must fail at the success assertion
    with 'would clobber existing tag'.  Status/stage reporting and die are
    lightweight stubs; the fetch and its failure branch come from production.
    """
    source = UPGRADE.read_text(encoding="utf-8")
    start = source.index("\nstage fetch\n")
    end = source.index("\n# \u200f#1277:", start)
    fetch_block = source[start:end]
    proc = run_bash('''\
set -euo pipefail
export GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null
export LC_ALL=C
git init origin
git -C origin -c user.name=Test -c user.email=test@example.invalid \\
    -c commit.gpgsign=false commit --allow-empty -m initial
git clone --no-tags origin local
git -C local tag v0.76.4
local_tag=$(git -C local rev-parse refs/tags/v0.76.4)
local_head=$(git -C local rev-parse HEAD)
git -C origin -c user.name=Test -c user.email=test@example.invalid \\
    -c commit.gpgsign=false commit --allow-empty -m release
git -C origin tag v0.76.4
git -C origin tag v0.77.0
git -C origin tag v0.77.1
remote_tag=$(git -C origin rev-parse refs/tags/v0.76.4)
target=$(git -C origin rev-parse refs/tags/v0.77.0)
[ "$local_tag" != "$remote_tag" ]
printf 'conflict established\\n'
TAG=v0.77.0
REPO_DIR="$PWD/local"
UPGRADE_STATE="$PWD/upgrade.state"
stage() { printf 'stage: %s\\n' "$1"; }
set_status() { printf 'status: %s\\n' "$*"; }
die() { printf '%s\\n' "$*" >&2; exit 1; }
''' + fetch_block + '''
[ "$(git -C local rev-parse refs/tags/v0.77.0)" = "$target" ]
[ "$(git -C local rev-parse refs/tags/v0.76.4)" = "$local_tag" ]
[ "$(git -C local rev-parse HEAD)" = "$local_head" ]
[ "$(git -C local tag --list v0.77.1)" = "" ]
printf 'requested tag fetched; unrelated tags and HEAD preserved\\n'
''', tmp_path, bindir)

    assert "conflict established" in proc.stdout, proc.stdout + proc.stderr
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "requested tag fetched; unrelated tags and HEAD preserved" in proc.stdout
    assert "status: failed" not in proc.stdout


def test_checkout_failure_records_git_error_and_does_not_restart(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(
        bindir,
        "git",
        _git_shim(checkout='echo "fatal: reference is not a tree" >&2; exit 128'),
    )
    fake_tool(bindir, "systemctl", 'echo "must not run" >&2; exit 70')
    fake_tool(bindir, "curl", 'echo "must not run" >&2; exit 71')

    proc = _run(tmp_path, bindir, repo, data)
    status = _status(data)

    assert proc.returncode != 0
    assert status and status["state"] == "failed"
    assert "git checkout --detach v0.53.0" in status["error"]
    assert "reference is not a tree" in status["error"]
    assert not any(row.startswith("systemctl ") for row in calls(tmp_path))



# --- ‏#1185: אחרי שה-checkout הצליח, כל כשל מחזיר את העץ לתג הקודם -----------------
#
# העץ הוא מה שהשרת יריץ באתחול הבא. כשל שעוצר את הסקריפט אחרי ה-checkout ולפני
# החזרה משאיר את /opt/imagesrv על התג החדש בזמן שהשירות הרץ (והיחידות וה-initrd)
# הם של הישן — והסטטוס נשאר `running` לתמיד, כלומר "לא ידוע" שנראה כמו "בתהליך".


def _rolled_back_to_previous(tmp_path: Path, data: Path, previous: str = "v0.48.0") -> dict:
    status = _status(data)
    assert status and status["state"] == "failed", status
    assert f"הוחזר ל-{previous}" in status["error"], status["error"]
    log = calls(tmp_path)
    new = next(i for i, row in enumerate(log) if " checkout --detach v0.53.0" in row)
    back = next(i for i, row in enumerate(log) if row.endswith(f"checkout --detach {previous}"))
    assert new < back
    return status


def test_tree_not_on_the_tag_after_checkout_returns_to_the_previous_tag(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(bindir, "git", _git_shim(head="fake-commit-for-v0.47.9"))
    fake_tool(bindir, "systemctl", "exit 0")
    fake_tool(bindir, "curl", "exit 0")

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    status = _rolled_back_to_previous(tmp_path, data)
    assert "v0.53.0" in status["error"] and "fake-commit-for-v0.47.9" in status["error"]


def test_empty_commit_answers_are_not_the_same_commit(tmp_path, bindir):
    """Principle 5: ``""`` == ``""`` is the absence of evidence, not a match."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    # ‏#1546: לפני ה-checkout ה-HEAD נקרא (יעד החזרה); הריקים — רק אחריו.
    fake_tool(bindir, "git", _git_shim(
        checkout='touch "$FAKE_LOG.co"',
        head='$([ -e "$FAKE_LOG.co" ] || echo fake-commit-before)', tag_commit=""))
    fake_tool(bindir, "systemctl", "exit 0")
    fake_tool(bindir, "curl", "exit 0")

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    status = _rolled_back_to_previous(tmp_path, data)
    assert "v0.53.0" in status["error"]


def test_an_unreadable_head_before_the_upgrade_refuses_without_touching_the_tree(tmp_path, bindir):
    """‏#1546: יעד החזרה הוא ה-HEAD של לפני הריצה. בלי ראיה מהו — לא משדרגים."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(bindir, "git", _git_shim(head=""))
    fake_tool(bindir, "systemctl", "exit 0")
    fake_tool(bindir, "curl", "exit 0")

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    assert not [row for row in calls(tmp_path) if " checkout " in f" {row} "]
    assert not [row for row in calls(tmp_path) if row.startswith("systemctl restart")]
    status = _status(data)
    assert status and status["state"] == "failed" and "אין יעד חזרה" in status["error"]


def test_unit_install_failure_returns_to_the_previous_tag_in_name(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(bindir, "git", _git_shim())
    # daemon-reload נכשל רק בפעם הראשונה — החזרה עצמה מצליחה
    fake_tool(bindir, "systemctl",
              'case "$1" in daemon-reload) [ -e "$FAKE_LOG.dr" ] && exit 0; '
              'touch "$FAKE_LOG.dr"; echo "Failed to reload" >&2; exit 1 ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    status = _rolled_back_to_previous(tmp_path, data)
    assert "daemon-reload" in status["error"]
    assert not any(row.startswith("boot-payload-build ") for row in calls(tmp_path))


def test_unexpected_failure_after_checkout_is_never_left_running(tmp_path, bindir):
    """כשל שאיש לא כתב לו `|| rollback` — כאן `systemctl show` תחת pipefail
    בגזירת כתובת הסוכן, לפני verify-boot-payload. אין ראיה = חזרה, בשם."""
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl", 'case "$1" in show) echo "no bus" >&2; exit 1 ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    status = _rolled_back_to_previous(tmp_path, data)
    assert "נכשל בשורה" in status["error"]
    assert not any(row.startswith("verify-boot-payload ") for row in calls(tmp_path))
