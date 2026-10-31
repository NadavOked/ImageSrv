"""‏#1287 — חזרה לתג הקודם מחזירה גם את מטען האתחול שלו.

‏server-upgrade.sh בונה initrd חדש (#1230). כשהראיה אחרי ה-restart נכשלה
הוא חזר לקוד הקודם — אבל ה-initrd וה-vmlinuz שנבנו מהתג החדש נשארו, כלומר
סוכן חדש מול שרת ישן (עם #1076: initrd מוצמד ל-TLS מול שרת שאינו מאזין
ל-8083 — והמכונות עולות מהדיסק). כאן הסקריפט האמיתי רץ מול shims, ובנייה
מזויפת **דורסת** את המטען בתוכן חדש; הבדיקה היא מה יש בתיקיית ה-boot בסוף.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from native import requires_native
from test_installer_1131 import (
    BASH,
    _fake_repo,
    _seed_db,
    _status,
    _upgrade_env,
    calls,
    fake_tool,
    posix,
    run_bash,
)
from test_server_upgrade_1193 import _git_shim

pytestmark = requires_native(("bash", BASH), why="server-upgrade.sh is bash")

PAYLOAD = ("initrd.img", "initrd.img.gui", "vmlinuz")


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


def _setup(tmp_path: Path, bindir: Path, *, live_ok: bool) -> tuple[Path, Path, Path]:
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=live_ok)
    boot = tmp_path / "boot"
    boot.mkdir()
    for name in PAYLOAD:
        (boot / name).write_text(f"old {name}\n", encoding="utf-8", newline="\n")
    (tmp_path / "initrd.flags").write_text(
        "# two builds, as firstboot records them\n"
        f"--skip-apt --output {posix(boot)}/initrd.img\n"
        f"--skip-apt --with-gui --output {posix(boot)}/initrd.img.gui\n",
        encoding="utf-8", newline="\n")
    # בנייה שמחליפה את המטען בפועל, כמו boot-payload-build.sh אחרי הצלחה.
    (repo / "tools" / "boot-payload-build.sh").write_text(
        '#!/usr/bin/env bash\nprintf "boot-payload-build %s\\n" "$*" >> "$FAKE_LOG"\n'
        'while [ $# -gt 0 ]; do case "$1" in --http-root) root=$2; shift 2 ;; *) shift ;; esac; done\n'
        'for f in initrd.img initrd.img.gui vmlinuz; do printf "new %s\\n" "$f" > "$root/$f"; done\n',
        encoding="utf-8", newline="\n")
    fake_tool(bindir, "git", _git_shim())
    fake_tool(bindir, "systemctl",
              'case "$1" in show) echo "IMAGESRV_URL=http://10.10.10.8:8080 X=y" ;; esac; exit 0')
    fake_tool(bindir, "curl", "exit 0")
    return data, repo, boot


def _run(tmp_path: Path, bindir: Path, repo: Path, data: Path, tag: str = "v0.53.0"):
    (tmp_path / "units").mkdir(exist_ok=True)
    env = {**_upgrade_env(tmp_path, repo, data), "FAKE_TAG": tag}
    return run_bash(
        f'bash "{posix(repo / "tools" / "server-upgrade.sh")}" {tag} "{posix(repo)}"',
        tmp_path, bindir, env=env)


def _payload(boot: Path) -> dict[str, str]:
    return {name: (boot / name).read_text(encoding="utf-8") for name in PAYLOAD}


def test_failure_after_the_build_restores_the_previous_payload_with_the_code(tmp_path, bindir):
    data, repo, boot = _setup(tmp_path, bindir, live_ok=False)

    proc = _run(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")

    assert proc.returncode != 0
    assert any(row.startswith("boot-payload-build ") for row in calls(tmp_path)), log
    assert any(row.endswith("checkout --detach v0.48.0") for row in calls(tmp_path)), log
    assert _payload(boot) == {name: f"old {name}\n" for name in PAYLOAD}, log
    status = _status(data)
    assert status and status["state"] == "failed"
    assert "הוחזר ל-v0.48.0" in status["error"]
    assert "ה-initrd הקודם הוחזר" in status["error"], status["error"]
    assert "נשאר" not in status["error"], status["error"]


def test_verified_upgrade_keeps_the_new_payload_and_drops_the_copy(tmp_path, bindir):
    data, repo, boot = _setup(tmp_path, bindir, live_ok=True)

    proc = _run(tmp_path, bindir, repo, data)
    log = (tmp_path / "upgrade.log").read_text(encoding="utf-8")

    assert proc.returncode == 0, log
    assert _payload(boot) == {name: f"new {name}\n" for name in PAYLOAD}
    assert sorted(p.name for p in boot.iterdir()) == sorted(PAYLOAD), \
        "העותק של המטען הקודם נשאר בתיקיית ה-boot אחרי שדרוג שאומת"


def test_failure_before_the_copy_never_restores_a_stale_copy(tmp_path, bindir):
    """עותק שנשאר מריצה קודמת אינו "המטען הקודם" של הריצה הזו."""
    data, repo, boot = _setup(tmp_path, bindir, live_ok=True)
    stale = boot / ".pre-upgrade"
    stale.mkdir()
    (stale / "0").write_text("stale\n", encoding="utf-8", newline="\n")
    (stale / "list").write_text(f"0 {posix(boot)}/initrd.img\n", encoding="utf-8", newline="\n")
    fake_tool(bindir, "systemctl",
              'case "$1" in daemon-reload) [ -e "$FAKE_LOG.dr" ] && exit 0; '
              'touch "$FAKE_LOG.dr"; exit 1 ;; esac; exit 0')

    proc = _run(tmp_path, bindir, repo, data)

    assert proc.returncode != 0
    assert _payload(boot) == {name: f"old {name}\n" for name in PAYLOAD}
