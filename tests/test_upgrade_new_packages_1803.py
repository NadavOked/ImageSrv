"""‏#1803 — עדכון גרסה מתקין חבילות שנוספו ל-packages.txt של התג החדש.

מעבדה 19/10: שרת שהותקן מ-ISO v0.59.2 ועודכן בכפתור עד rc7 — ‏ifenslave (‏#1264,
‏packages.txt:39) לא הותקן, כי ``install/security-upgrade.sh`` שידרג רק חבילות
**מותקנות**. מה שנבדק כאן:

* חבילה מ-``tools/iso/packages.txt`` של התג שחסרה בשרת — מותקנת מהמאגר החתום של
  הגרסה, וראיה חיובית בקריאה חוזרת (‏dpkg-query) שהיא מותקנת.
* חבילה חדשה שאינה במאגר — כשל גלוי **לפני** apt (עיקרון 5), ובכפתור העדכון: חזרה לתג
  הקודם בשלב ``packages``, עם הסיבה בסטטוס.
* אין חבילות חדשות — אין קריאה ל-apt.
* ``tools/iso/make-pool.sh --security`` אורז את **כל** הסגירה של packages.txt.

אותו harness כמו ‏#1411 (‏``World`` ב-test_security_pool_1411): ‏apt, ‏dpkg ו-curl
מדומים על PATH; ‏tar, ‏sha256sum ו-zcat אמיתיים.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tarfile
from pathlib import Path

import pytest

from test_installer_1131 import _fake_repo, _seed_db, _status, calls, fake_tool, posix
from test_security_pool_1411 import OLD, POOL, POSIX, World, _make_pool, _pool_tools
from test_server_upgrade_1193 import _run as _run_upgrade
from test_server_upgrade_1193 import _success_tools

#: המאגר של הגרסה החדשה: עדכוני האבטחה של #1411 + החבילה ש-#1264 הוסיף לרשימה.
POOL_NEW = {**POOL, "ifenslave": "2.14"}
#: השרת כבר על גרסאות המאגר — כל מה שנשאר הוא החבילה החסרה.
CURRENT = {**OLD, "libssl3t64": POOL["libssl3t64"], "dnsmasq-base": POOL["dnsmasq-base"]}


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


@POSIX
def test_a_package_new_in_the_tag_is_installed_from_the_signed_pool(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_NEW, installed=OLD, wanted=[*OLD, "ifenslave"])
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    apt = world.calls("apt-get")
    assert len(apt) == 2 and " install " in apt[1], apt
    # one install: the packages that are behind, then the new ones -- by name, only from the pool
    assert apt[1].endswith(" install libssl3t64 dnsmasq-base ifenslave"), apt[1]
    assert "--no-install-recommends" in apt[1] and "--no-remove" in apt[1]
    assert world.installed()["ifenslave"] == "2.14"
    assert "חבילות חדשות בגרסה: ifenslave" in done.stdout
    assert world.state()["installed"] == "1" and world.state()["upgraded"] == "2"


@POSIX
def test_a_new_package_that_is_not_in_the_pool_fails_in_name_before_apt(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD, wanted=[*OLD, "chrony", "ifenslave"])
    done = world.run()
    assert done.returncode == 7, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "אינן במאגר החתום של v0.80.0" in last and "chrony ifenslave" in last, last
    assert not world.calls("apt-get") and world.state() is None
    assert world.installed() == OLD


@POSIX
def test_no_new_packages_means_no_apt_install(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_NEW, installed={**CURRENT, "ifenslave": "2.14"},
                  wanted=[*CURRENT, "ifenslave"])
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not world.calls("apt-get")
    assert world.state()["installed"] == "0" and world.state()["upgraded"] == "0"


@POSIX
def test_an_install_that_reading_back_does_not_prove_is_a_failure(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_NEW, installed=CURRENT, wanted=[*CURRENT, "ifenslave"])
    done = world.run(FAKE_APT_NOOP="1")
    assert done.returncode == 6, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "apt-get יצא 0, אבל לא הותקנו: ifenslave" in last, last
    assert world.state() is None


@POSIX
def test_a_pool_already_applied_is_fetched_again_for_a_package_the_tag_added(tmp_path, bindir):
    """The sha256 short-cut is "nothing to do" only when nothing from the list is missing."""
    world = World(tmp_path, bindir, pool=POOL_NEW, installed=CURRENT, wanted=[*CURRENT, "ifenslave"])
    (world.data / "security-pool.json").write_text(json.dumps({"sha256": world.lock["sha256"],
                                                               "date": "2026-10-15"}), encoding="utf-8")
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert world.calls("curl") and world.installed()["ifenslave"] == "2.14"


@POSIX
def test_a_tag_without_packages_txt_is_a_failure_not_nothing_missing(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    world.packages_txt.unlink()
    done = world.run()
    assert done.returncode == 2, done.stdout + done.stderr
    assert "tools/iso/packages.txt" in done.stdout.splitlines()[-1]
    assert not world.calls("curl") and not world.calls("apt-get")


@POSIX
def test_a_tag_without_a_pool_cannot_install_what_its_list_adds(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=None, installed=OLD, wanted=[*OLD, "ifenslave"])
    done = world.run()
    assert done.returncode == 7, done.stdout + done.stderr
    assert "ifenslave" in done.stdout.splitlines()[-1]
    assert not world.calls("curl") and not world.calls("apt-get")


@POSIX
def test_a_broken_installed_check_installs_nothing(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL_NEW, installed=OLD, wanted=[*OLD, "ifenslave"])
    done = world.run(FAKE_DPKG_QUERY_BROKEN="1")
    assert done.returncode == 4 and "לא רצה" in done.stdout.splitlines()[-1], done.stdout
    assert not world.calls("apt-get") and world.state() is None


# ---- tools/server-upgrade.sh: a package missing from the pool rolls the update back ----

@POSIX
def test_the_update_rolls_back_and_names_the_package_missing_from_the_pool(tmp_path, bindir,
                                                                             monkeypatch):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    world = World(tmp_path, bindir, pool=POOL, installed=OLD, wanted=[*OLD, "chrony"])
    _success_tools(bindir)
    # curl for the pool (file://) and for the server's own health checks (exit 0)
    fake_tool(bindir, "curl", '''
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2 ;; -*) case "$1" in --max-time|--connect-timeout|--speed-limit|--speed-time|--proto|--proto-redir|-m) shift ;; esac; shift ;; *) url=$1; shift ;; esac; done
case "$url" in file://*) cp "${url#file://}" "$out" ;; *) exit 0 ;; esac''')
    monkeypatch.setenv("IMAGECTL_SECURITY_POOL_URL", "file://" + posix(world.release))
    monkeypatch.setenv("IMAGECTL_PYTHON", sys.executable)
    done = _run_upgrade(tmp_path, bindir, repo, data)
    status = _status(data)
    log = calls(tmp_path)
    assert done.returncode != 0
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert status["stage"] == "packages"
    assert "chrony" in status["detail"] and "אינן במאגר החתום" in status["detail"], status
    assert "הוחזר ל-v0.48.0" in status["error"], status
    assert any(" checkout --detach v0.48.0" in row for row in log)
    assert not any(row.startswith("apt-get ") for row in log)


# ---- tools/iso/make-pool.sh --security: the whole closure of packages.txt ----------------

@POSIX
def test_make_pool_security_carries_the_whole_closure_of_packages_txt(tmp_path, bindir):
    _pool_tools(tmp_path, bindir, sim="""\
Inst ifenslave (2.14 Debian:13.1/stable [all])
Inst libssl3t64 (3.5.1-1+deb13u1 Debian-Security:13/stable-security [amd64])
Inst libc6 (2.41-12 Debian:13.1/stable [amd64])
""")
    (tmp_path / "packages.txt").write_text("ifenslave   # #1264\n", encoding="utf-8", newline="\n")
    out = tmp_path / "out"
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(out))
    assert done.returncode == 0, done.stdout + done.stderr
    dl = next(row for row in calls(tmp_path) if row.startswith("apt-get download"))
    assert dl.split()[2:] == ["ifenslave=2.14", "libssl3t64=3.5.1-1+deb13u1", "libc6=2.41-12"], dl
    lock = json.loads((out / "security-pool.json").read_text(encoding="utf-8"))
    assert lock["packages"] == 3 and lock["security"] == 1, lock
    raw = (out / lock["asset"]).read_bytes()
    assert lock["sha256"] == hashlib.sha256(raw).hexdigest()
    with tarfile.open(out / lock["asset"]) as tar:
        assert "pool/main/imagectl/ifenslave_2.14_amd64.deb" in tar.getnames()


@POSIX
def test_make_pool_security_refuses_a_listed_package_outside_the_closure(tmp_path, bindir):
    """Positive evidence: every name in packages.txt has a .deb in the pool, or no pool."""
    _pool_tools(tmp_path, bindir, sim="Inst libssl3t64 (3.5.1-1+deb13u1 Debian-Security:13/stable-security [amd64])\n")
    (tmp_path / "packages.txt").write_text("ifenslave\n", encoding="utf-8", newline="\n")
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(tmp_path / "out"))
    assert done.returncode == 1, done.stdout + done.stderr
    assert "ifenslave" in done.stderr and not (tmp_path / "out" / "security-pool.json").exists()

