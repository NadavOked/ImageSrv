"""‏#1411 — עדכוני האבטחה של Debian מגיעים לשרת רק בתוך הגרסה החתומה.

הכרעת נדב (15/10): אין מקור apt חי (PR #1611). מה שנבדק כאן הוא החלק השני:

* ``tools/iso/make-pool.sh`` נכשל בלי trixie-security במקורות של המארח הבונה,
  וב-``--security`` אורז רק את חבילות trixie-security של הסגירה, עם
  ``security-pool.json`` (גיבוב, גודל, תאריך).
* ``install/security-upgrade.sh`` (מכפתור העדכון) מוריד את ה-tar, דוחה tar שאינו
  תואם לגיבוב שבעץ התג החתום, ומריץ ``apt-get upgrade`` שרואה **רק** את המאגר
  הזה — בתקרת זמן, עם ראיה חיובית שהגרסאות עלו.
* ``tools/server-upgrade.sh``: שלב ``packages`` אחרי היחידות ולפני ה-initrd;
  כשל = חזרה לתג הקודם.
* מסך השרת (DCUI) מציג "עדכוני מערכת מתאריך YYYY-MM-DD".

הכלים החיצוניים (apt, ‏dpkg, ‏curl, ‏git) מדומים על PATH; ‏tar, ‏sha256sum, ‏zcat
ו-timeout אמיתיים. שום מאגר ברשת ושום חבילה במכונה אינם נוגעים.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import subprocess
import sys
import tarfile
from dataclasses import replace
from datetime import datetime
from pathlib import Path

import pytest

from native import requires_native
from server.dcui import app as dcui_app
from server.dcui import data as dcui_data
from server.dcui import samples, view
from test_installer_1131 import (BASH, _fake_repo, _seed_db, _status, calls, fake_tool,
                                 posix, run_bash)
from test_server_upgrade_1193 import _run as _run_upgrade
from test_server_upgrade_1193 import _success_tools

ROOT = Path(__file__).resolve().parent.parent
LOCK_PY = ROOT / "install" / "security-pool-lock.py"
SEC_UP = ROOT / "install" / "security-upgrade.sh"
MAKE_POOL = ROOT / "tools" / "iso" / "make-pool.sh"
DAY = "2026-10-15"
ASSET = f"imagectl-security-pool-{DAY}.tar"

POSIX = requires_native(("bash", BASH), "tar", "sha256sum", "timeout", "zcat", "gzip",
                        posix=True, why="the upgrade path is bash with GNU tools")


# ---- security-pool.json: one reader for the publisher and the server ----------------

def _lock_cli(tmp_path: Path, doc) -> subprocess.CompletedProcess:
    path = tmp_path / "security-pool.json"
    path.write_text(doc if isinstance(doc, str) else json.dumps(doc), encoding="utf-8")
    return subprocess.run([sys.executable, "-I", str(LOCK_PY), str(path)], capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=30)


GOOD_LOCK = {"asset": ASSET, "date": DAY, "sha256": "a" * 64, "size": 1234,
             "packages": 3, "suite": "trixie-security"}


def test_a_valid_lock_prints_asset_hash_size_and_date(tmp_path):
    done = _lock_cli(tmp_path, GOOD_LOCK)
    assert done.returncode == 0, done.stderr
    assert done.stdout.split() == [ASSET, "a" * 64, "1234", DAY]


@pytest.mark.parametrize("change", [
    {"sha256": "a" * 63}, {"sha256": "A" * 64}, {"size": 0}, {"size": True}, {"size": "1234"},
    {"asset": "../../etc/passwd"}, {"asset": "imagectl-security-pool-2026-10-15.tar.gz"},
    {"date": "2026-10-16"}, {"date": "15/10/2026"}, {"asset": None},
])
def test_an_invalid_lock_is_refused_by_name(tmp_path, change):
    done = _lock_cli(tmp_path, GOOD_LOCK | change)
    assert done.returncode == 2 and "not valid" in done.stderr, (done.stdout, done.stderr)
    assert done.stdout == ""


@pytest.mark.parametrize("text", ["", "{", "[]", '"x"'])
def test_an_unreadable_lock_is_a_failure_not_no_pool(tmp_path, text):
    assert _lock_cli(tmp_path, text).returncode == 2


# ---- install/security-upgrade.sh -------------------------------------------------------

def _packages_index(packages: dict[str, str]) -> bytes:
    text = "".join(f"Package: {name}\nVersion: {ver}\nArchitecture: amd64\n"
                   f"Filename: pool/main/imagectl/{name}_{ver}_amd64.deb\n\n"
                   for name, ver in packages.items())
    return gzip.compress(text.encode("utf-8"), mtime=0)


def _tar(packages: dict[str, str]) -> bytes:
    members = {f"pool/main/imagectl/{n}_{v}_amd64.deb": b"deb" for n, v in packages.items()}
    members["dists/trixie/main/binary-amd64/Packages.gz"] = _packages_index(packages)
    members["dists/trixie/Release"] = b"Codename: trixie\n"
    members["pool-date"] = f"{DAY}\n".encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
    return buf.getvalue()


class World:
    """A repo checked out at the tag (install/ + the lock), a release dir with the
    tar, and fake apt/dpkg/curl whose "installed" versions live in a file."""

    def __init__(self, tmp_path: Path, bindir: Path, *, pool: dict[str, str] | None,
                 installed: dict[str, str], tamper: bool = False, lock: dict | None = None,
                 wanted: list[str] | None = None):
        self.tmp, self.bindir = tmp_path, bindir
        self.repo = tmp_path / "repo"
        (self.repo / "install").mkdir(parents=True, exist_ok=True)
        shutil.copy(SEC_UP, self.repo / "install" / "security-upgrade.sh")
        shutil.copy(LOCK_PY, self.repo / "install" / "security-pool-lock.py")
        # #1803: the tag's tools/iso/packages.txt -- by default exactly what is installed.
        self.packages_txt = self.repo / "tools" / "iso" / "packages.txt"
        self.packages_txt.parent.mkdir(parents=True, exist_ok=True)
        names = installed if wanted is None else wanted
        self.packages_txt.write_text("# packages\n" + "".join(f"{n}   # why\n" for n in names),
                                     encoding="utf-8", newline="\n")
        self.data = tmp_path / "data"
        self.data.mkdir(exist_ok=True)
        self.release = tmp_path / "release"
        self.release.mkdir()
        self.lock = lock
        if pool is not None:
            raw = _tar(pool)
            self.lock = lock or {"asset": ASSET, "date": DAY, "sha256": hashlib.sha256(raw).hexdigest(),
                                 "size": len(raw), "packages": len(pool), "suite": "trixie-security"}
            (self.release / ASSET).write_bytes(raw + (b"x" if tamper else b""))
            (self.repo / "install" / "security-pool.json").write_text(json.dumps(self.lock),
                                                                       encoding="utf-8")
            self.after = {**installed, **{n: v for n, v in pool.items() if n in installed}}
        else:
            self.after = dict(installed)
        self._write("installed", installed)
        self._write("upgraded", self.after)
        self._write("pool", pool or {})
        self._tools()

    def _write(self, name: str, versions: dict[str, str]) -> None:
        (self.tmp / name).write_text("".join(f"{n} {v}\n" for n, v in versions.items()),
                                     encoding="utf-8", newline="\n")

    def _tools(self) -> None:
        installed, upgraded = posix(self.tmp / "installed"), posix(self.tmp / "upgraded")
        pool = posix(self.tmp / "pool")
        fake_tool(self.bindir, "dpkg-query", f'''
[ -z "${{FAKE_DPKG_QUERY_BROKEN:-}}" ] || {{ echo "dpkg-query: database locked" >&2; exit 2; }}
for last; do :; done
v=$(awk -v p="$last" '$1 == p {{print $2}}' "{installed}")
[ -n "$v" ] || exit 1
printf 'install ok installed %s' "$v"''')
        fake_tool(self.bindir, "dpkg", '''
if [ "$1" = --compare-versions ]; then
  [ "$(printf '%s\\n%s\\n' "$2" "$4" | sort -V | tail -n 1)" = "$2" ]; exit $?
fi
exit 0''')
        fake_tool(self.bindir, "apt-get", f'''
for a; do case "$a" in Dir::Etc::SourceList=*) cat "${{a#Dir::Etc::SourceList=}}" >> "$FAKE_LOG.sources" ;; esac; done
case " $* " in
  *" update "*) exit "${{FAKE_APT_UPDATE_RC:-0}}" ;;
  *" upgrade "*|*" install "*)
    [ -z "${{FAKE_APT_SLEEP:-}}" ] || sleep "$FAKE_APT_SLEEP"
    [ "${{FAKE_APT_UPGRADE_RC:-0}}" = 0 ] || {{ echo "E: Sub-process /usr/bin/dpkg returned an error code (1)"; exit "$FAKE_APT_UPGRADE_RC"; }}
    # FAKE_APT_WANTS_REMOVE: the resolution removes that package (refused under --no-remove).
    if [ -n "${{FAKE_APT_WANTS_REMOVE:-}}" ]; then
      case " $* " in *" --no-remove "*) echo "E: Packages need to be removed but remove is disabled."; exit 100 ;; esac
    fi
    [ -z "${{FAKE_APT_NOOP:-}}" ] || {{ echo "Setting up whatever"; exit 0; }}
    # TA, 17/10: `upgrade` (even --with-new-pkgs) keeps back FAKE_APT_KEPT_BACK -- a meta
    # package that needs a NEW package -- and exits 0. `install` changes only what it names,
    # and brings FAKE_APT_NEW_DEPS ("pkg=ver ...") when a named package needs them.
    mode=upgrade; case " $* " in *" install "*) mode=install ;; esac
    awk -v mode="$mode" -v kept=" ${{FAKE_APT_KEPT_BACK:-}} " -v args=" $* " '
      NR == FNR {{ old[$1] = $2; next }}
      {{ v = $2
        if (mode == "upgrade" && index(kept, " " $1 " ")) v = old[$1]
        if (mode == "install" && !index(args, " " $1 " ")) v = old[$1]
        print $1, v }}' "{installed}" "{upgraded}" > "{installed}.new"
    if [ "$mode" = install ]; then
      for d in ${{FAKE_APT_NEW_DEPS:-}}; do echo "${{d%%=*}} ${{d#*=}}" >> "{installed}.new"; done
      # #1803: a named package that is not installed yet comes from the pool.
      awk -v args=" $* " 'NR == FNR {{ have[$1] = 1; next }}
        !($1 in have) && index(args, " " $1 " ") {{ print $1, $2 }}' "{installed}.new" "{pool}" > "{installed}.add"
      cat "{installed}.add" >> "{installed}.new"
    fi
    if [ -n "${{FAKE_APT_WANTS_REMOVE:-}}" ]; then
      awk -v r="$FAKE_APT_WANTS_REMOVE" '$1 != r' "{installed}.new" > "{installed}.rm" && mv "{installed}.rm" "{installed}.new"
    fi
    mv "{installed}.new" "{installed}"
    echo "Setting up whatever"; exit 0 ;;
esac
exit 64''')
        fake_tool(self.bindir, "curl", '''
out=""; url=""
while [ $# -gt 0 ]; do case "$1" in -o) out=$2; shift 2 ;; -*) [ "$1" = --max-time ] || [ "$1" = --connect-timeout ] || [ "$1" = --speed-limit ] || [ "$1" = --speed-time ] || [ "$1" = --proto ] || [ "$1" = --proto-redir ] && shift; shift ;; *) url=$1; shift ;; esac; done
src=${FAKE_CURL_SRC:-${url#file://}}
cp "$src" "$out" 2>/dev/null || { echo "curl: (22) The requested URL returned error: 404" >&2; exit 22; }''')
        fake_tool(self.bindir, "git", 'echo "${FAKE_ORIGIN:-https://github.com/NadavOked/ImageSrv.git}"')

    def run(self, **env: str) -> subprocess.CompletedProcess:
        full = {"IMAGECTL_DATA_DIR": posix(self.data), "IMAGECTL_PYTHON": sys.executable,
                "IMAGECTL_SECURITY_POOL_URL": "file://" + posix(self.release), **env}
        return run_bash(f'bash "{posix(self.repo / "install" / "security-upgrade.sh")}" v0.80.0 '
                        f'"{posix(self.repo)}"', self.tmp, self.bindir, env=full)

    def installed(self) -> dict[str, str]:
        rows = (self.tmp / "installed").read_text(encoding="utf-8").split("\n")
        return dict(r.split(" ", 1) for r in rows if r.strip())

    def calls(self, tool: str) -> list[str]:
        return [c for c in calls(self.tmp) if c.startswith(tool + " ")]

    def state(self) -> dict | None:
        path = self.data / "security-pool.json"
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None

    def sources(self) -> str:
        path = self.tmp / "calls.log.sources"
        return path.read_text(encoding="utf-8") if path.exists() else ""


POOL = {"libssl3t64": "3.5.1-1+deb13u1", "dnsmasq-base": "2.90-4+deb13u1",
        "openssh-client": "1:10.0p1-7+deb13u1"}
OLD = {"libssl3t64": "3.5.1-1", "dnsmasq-base": "2.90-4", "python3": "3.13.5-1"}


@pytest.fixture()
def bindir(tmp_path: Path) -> Path:
    path = tmp_path / "bin"
    path.mkdir()
    return path


@POSIX
def test_a_release_without_a_pool_installs_nothing(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=None, installed=OLD)
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "אינה נושאת מאגר עדכוני אבטחה" in done.stdout
    assert not world.calls("curl") and not world.calls("apt-get") and world.state() is None


@POSIX
def test_the_pool_is_installed_only_from_itself_and_recorded(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    apt = world.calls("apt-get")
    # #1411, TA 17/10: the packages that are behind, by name -- not `upgrade` -- and no removals
    assert len(apt) == 2 and " update " in apt[0] + " " and " install " in apt[1], apt
    assert apt[1].endswith(" install libssl3t64 dnsmasq-base"), apt[1]
    assert " upgrade" not in apt[1] and "dist-upgrade" not in apt[1]
    assert "--no-remove" in apt[1] and "Dpkg::Options::=--force-confold" in apt[1]
    for row in apt:     # the system's sources, lists and cache are never used
        for opt in ("Dir::Etc::SourceList=", "Dir::Etc::SourceParts=", "Dir::State::Lists=", "Dir::Cache="):
            assert opt + posix(world.data / "security-pool") in row, (opt, row)
    repo = posix(world.data / "security-pool" / "repo")
    assert world.sources().splitlines() == [f"deb [trusted=yes] file:{repo} trixie main"] * 2
    assert world.state() == {"date": DAY, "sha256": world.lock["sha256"], "asset": ASSET,
                             "tag": "v0.80.0", "upgraded": "2", "installed": "0"}
    assert done.stdout.strip().splitlines()[-1] == f"עדכוני מערכת מתאריך {DAY} מותקנים ({ASSET})"
    assert not (world.data / "security-pool").exists(), "the work dir is removed"
    assert "אזהרה" not in done.stdout


@POSIX
def test_a_pool_that_does_not_match_the_signed_hash_is_refused(tmp_path, bindir):
    """"חבילה בלי חתימה נדחית": ה-tar אינו מה שהתג החתום מתאר — לא מותקן ממנו כלום."""
    world = World(tmp_path, bindir, pool=POOL, installed=OLD, tamper=True)
    done = world.run()
    assert done.returncode == 3, done.stdout + done.stderr
    assert "אינו תואם לגיבוב שבגרסה החתומה" in done.stdout.splitlines()[-1]
    assert not world.calls("apt-get") and not world.calls("dpkg-query") and world.state() is None


@POSIX
def test_a_missing_release_asset_is_a_named_failure(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    (world.release / ASSET).unlink()
    done = world.run()
    assert done.returncode == 3 and "הורדת " + ASSET + " נכשלה" in done.stdout, done.stdout
    assert "404" in done.stdout and not world.calls("apt-get")


@POSIX
def test_an_invalid_lock_in_the_tag_is_a_failure_not_no_pool(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD, lock=GOOD_LOCK | {"sha256": "zz"})
    done = world.run()
    assert done.returncode == 2 and "לא תקין" in done.stdout, done.stdout
    assert not world.calls("curl") and not world.calls("apt-get")


@POSIX
def test_a_pool_already_installed_downloads_nothing(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    (world.data / "security-pool.json").write_text(json.dumps({"sha256": world.lock["sha256"],
                                                               "date": DAY}), encoding="utf-8")
    done = world.run()
    assert done.returncode == 0 and "כבר מותקנים" in done.stdout, done.stdout
    assert not world.calls("curl") and not world.calls("apt-get")


@POSIX
def test_packages_already_at_the_pool_versions_do_not_touch_apt(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed={**OLD, **{
        "libssl3t64": "3.5.1-1+deb13u2", "dnsmasq-base": "2.90-4+deb13u1"}})
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert not world.calls("apt-get")
    assert (world.state() or {}).get("upgraded") == "0"


@POSIX
def test_a_failed_upgrade_repairs_dpkg_and_fails_in_name(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(FAKE_APT_UPGRADE_RC="100")
    assert done.returncode == 5, done.stdout + done.stderr
    last = done.stdout.splitlines()[-1]
    assert "apt-get install נכשל (קוד 100)" in last and "dpkg returned an error" in last
    assert "dpkg --configure -a" in calls(tmp_path)
    assert world.state() is None


@POSIX
def test_an_upgrade_past_its_ceiling_is_stopped_and_named(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(IMAGECTL_SECURITY_TIMEOUT="1", FAKE_APT_SLEEP="20")
    assert done.returncode == 5, done.stdout + done.stderr
    assert "לא הסתיים תוך 1 ש'" in done.stdout.splitlines()[-1]
    assert "dpkg --configure -a" in calls(tmp_path) and world.state() is None


@POSIX
def test_apt_exit_zero_without_the_new_versions_is_not_success(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(FAKE_APT_NOOP="1")
    assert done.returncode == 6, done.stdout + done.stderr
    assert "לא עודכנו: " in done.stdout and "libssl3t64" in done.stdout.splitlines()[-1]
    assert world.state() is None


@POSIX
def test_a_version_check_that_could_not_run_installs_nothing(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(FAKE_DPKG_QUERY_BROKEN="1")
    assert done.returncode == 4 and "לא רצה" in done.stdout, done.stdout
    assert not world.calls("apt-get") and world.state() is None


@POSIX
def test_the_pool_is_downloaded_from_the_release_of_the_tag_over_https(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(IMAGECTL_SECURITY_POOL_URL="", FAKE_CURL_SRC=posix(world.release / ASSET))
    assert done.returncode == 0, done.stdout + done.stderr
    curl = world.calls("curl")
    assert curl and curl[0].endswith(
        f"https://github.com/NadavOked/ImageSrv/releases/download/v0.80.0/{ASSET}"), curl
    assert "--proto-redir =https" in curl[0] and "--max-time 1800" in curl[0]


@POSIX
def test_an_origin_that_is_not_github_is_not_guessed(tmp_path, bindir):
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(IMAGECTL_SECURITY_POOL_URL="", FAKE_ORIGIN="/srv/mirror/ImageCtl.git")
    assert done.returncode == 3 and "אינו ריפו ב-github" in done.stdout, done.stdout
    assert not world.calls("curl")


@POSIX
def test_a_new_kernel_says_the_server_itself_needs_a_reboot(tmp_path, bindir):
    world = World(tmp_path, bindir, pool={**POOL, "linux-image-amd64": "6.12.48-1"},
                  installed={**OLD, "linux-image-amd64": "6.12.43-1"})
    done = world.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert "אזהרה: קרנל חדש הותקן" in done.stdout


KERNEL = {"linux-image-amd64": "6.12.113-1", "linux-image-6.12.113+deb13-amd64": "6.12.113-1"}


@POSIX
def test_a_meta_package_that_needs_a_new_package_is_installed_not_kept_back(tmp_path, bindir):
    """TA, 17/10: `apt-get upgrade --with-new-pkgs` left linux-image-amd64 behind -- it needs
    the NEW linux-image-6.12.113+deb13-amd64 -- and exited 0; 20 of 21 were updated."""
    world = World(tmp_path, bindir, pool={**POOL, **KERNEL},
                  installed={**OLD, "linux-image-amd64": "6.12.111-1"})
    done = world.run(FAKE_APT_KEPT_BACK="linux-image-amd64",
                     FAKE_APT_NEW_DEPS="linux-image-6.12.113+deb13-amd64=6.12.113-1")
    assert done.returncode == 0, done.stdout + done.stderr
    now = world.installed()
    assert now["linux-image-amd64"] == "6.12.113-1", now
    assert now["linux-image-6.12.113+deb13-amd64"] == "6.12.113-1", now
    assert now["libssl3t64"] == POOL["libssl3t64"] and world.state()["upgraded"] == "3"
    assert "אזהרה: קרנל חדש הותקן" in done.stdout


@POSIX
def test_an_upgrade_that_would_remove_a_package_installs_nothing_and_fails_in_name(tmp_path, bindir):
    """No removal, ever: if apt's answer removes a package, the server keeps it and says so."""
    world = World(tmp_path, bindir, pool=POOL, installed=OLD)
    done = world.run(FAKE_APT_WANTS_REMOVE="python3")
    assert done.returncode == 5, done.stdout + done.stderr
    assert "remove is disabled" in done.stdout.splitlines()[-1]
    assert world.installed() == OLD and world.state() is None


# ---- tools/server-upgrade.sh: the `packages` stage ---------------------------------------

def _stub(repo: Path, body: str) -> None:
    (repo / "install" / "security-upgrade.sh").write_text(
        '#!/usr/bin/env bash\nprintf "security-upgrade %s\\n" "$*" >> "$FAKE_LOG"\n' + body + "\n",
        encoding="utf-8", newline="\n")


@POSIX
def test_upgrade_installs_the_security_pool_after_the_units_and_before_the_initrd(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _stub(repo, 'echo "עדכוני מערכת מתאריך 2026-10-15 מותקנים"')
    _success_tools(bindir)
    done = _run_upgrade(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    assert done.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    sec = next(i for i, row in enumerate(log) if row.startswith("security-upgrade "))
    assert log[sec] == f"security-upgrade v0.53.0 {posix(repo)}"
    assert log.index("systemctl daemon-reload") < sec
    assert sec < next(i for i, row in enumerate(log) if row.startswith("boot-payload-build "))
    status = _status(data)
    assert status["state"] == "done" and "packages" in status["stages"], status


@POSIX
def test_a_refused_security_pool_rolls_the_server_back(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _stub(repo, 'echo "מוריד..."; echo "עדכוני מערכת: X אינו תואם לגיבוב שבגרסה החתומה — נדחה"; exit 3')
    _success_tools(bindir)
    done = _run_upgrade(tmp_path, bindir, repo, data)
    log = calls(tmp_path)
    status = _status(data)
    assert done.returncode != 0
    assert status["state"] == "failed" and status["reason"] == "rollback", status
    assert status["stage"] == "packages"
    assert "נדחה" in status["detail"] and "הוחזר ל-v0.48.0" in status["error"], status
    assert any(" checkout --detach v0.48.0" in row for row in log)
    assert not any(row.startswith("boot-payload-build ") for row in log)


@POSIX
def test_a_kernel_warning_reaches_the_update_status(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    _stub(repo, 'echo "אזהרה: קרנל חדש הותקן מעדכוני המערכת — בשרת עצמו הוא ייכנס לתוקף באתחול הבא"\n'
                'echo "עדכוני מערכת מתאריך 2026-10-15 מותקנים"')
    _success_tools(bindir)
    done = _run_upgrade(tmp_path, bindir, repo, data)
    status = _status(data)
    assert done.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert status["state"] == "done" and "קרנל חדש הותקן" in status.get("warning", ""), status


@POSIX
def test_the_real_script_in_a_release_without_a_pool_lets_the_upgrade_finish(tmp_path, bindir):
    data = tmp_path / "data"
    _seed_db(data)
    repo = _fake_repo(tmp_path, live_ok=True)
    shutil.copy(SEC_UP, repo / "install" / "security-upgrade.sh")
    # #1803: the tag's package list, everything on it installed
    (repo / "tools" / "iso").mkdir(parents=True)
    (repo / "tools" / "iso" / "packages.txt").write_text("dpkg\n", encoding="utf-8", newline="\n")
    _success_tools(bindir)
    fake_tool(bindir, "dpkg-query", "printf 'install ok installed 1.22.21'")
    done = _run_upgrade(tmp_path, bindir, repo, data)
    assert done.returncode == 0, (tmp_path / "upgrade.log").read_text(encoding="utf-8")
    assert "אינה נושאת מאגר עדכוני אבטחה" in (tmp_path / "upgrade.log").read_text(encoding="utf-8")


# ---- tools/iso/make-pool.sh -----------------------------------------------------------

SIM = """\
NOTE: This is only a simulation!
Inst libssl3t64 (3.5.1-1+deb13u1 Debian-Security:13/stable-security [amd64])
Inst libc6 (2.41-12 Debian:13.1/stable [amd64])
Inst openssh-client (1:10.0p1-7+deb13u1 Debian:13.1/stable, Debian-Security:13/stable-security [amd64])
Inst dnsmasq-base [2.90-4] (2.90-4+deb13u1 Debian-Security:13/stable-security [amd64])
Inst python3-security (1.0-1 Debian:13.1/stable [amd64])
Inst dnsmasq (2.90-4+deb13u1 Debian:13.1/stable [all])
Conf libssl3t64 (3.5.1-1+deb13u1 Debian-Security:13/stable-security [amd64])
"""
POLICY = " 500 http://security.debian.org/debian-security trixie-security/main amd64 Packages\n" \
         "     release v=13,o=Debian,a=stable-security,n=trixie-security,l=Debian-Security,c=main,b=amd64\n"


def _pool_tools(tmp_path: Path, bindir: Path, *, policy: str = POLICY, sim: str = SIM) -> dict:
    (tmp_path / "policy").write_text(policy, encoding="utf-8", newline="\n")
    (tmp_path / "sim").write_text(sim, encoding="utf-8", newline="\n")
    fake_tool(bindir, "id", "echo 0")
    fake_tool(bindir, "apt-cache", f'''
if [ "$1" = policy ] && [ $# -eq 1 ]; then cat "{posix(tmp_path / "policy")}"; exit 0; fi
echo "  Candidate: 1.0"''')
    fake_tool(bindir, "apt-get", f'''
case " $* " in
  *" -s "*) cat "{posix(tmp_path / "sim")}" ;;
  *" download "*) for s; do case "$s" in *=*) p=${{s%%=*}}; v=${{s#*=}}; v=$(printf '%s' "$v" | sed 's/:/%3a/g'); : > "${{p}}_${{v}}_amd64.deb" ;; esac; done ;;
  *" update "*|*" -qq") ;;
  *) echo "unexpected apt-get $*" >&2; exit 64 ;;
esac''')
    fake_tool(bindir, "dpkg-deb", 'echo net')
    fake_tool(bindir, "dpkg-scanpackages", '''
for f in "$4"/*/*.deb; do b=${f##*/}; printf 'Package: %s\\nVersion: 1\\nFilename: %s\\n\\n' "${b%%_*}" "$f"; done''')
    pk = tmp_path / "packages.txt"
    pk.write_text("dnsmasq\nopenssh-client\n", encoding="utf-8", newline="\n")
    return {"PKS": posix(pk)}


def _make_pool(tmp_path: Path, bindir: Path, *args: str) -> subprocess.CompletedProcess:
    pk = posix(tmp_path / "packages.txt")
    return run_bash(f'bash "{posix(MAKE_POOL)}" --packages "{pk}" ' + " ".join(args), tmp_path, bindir)


@POSIX
@pytest.mark.parametrize("mode", [[], ["--security"]])
def test_make_pool_refuses_a_build_host_without_trixie_security(tmp_path, bindir, mode):
    _pool_tools(tmp_path, bindir, policy=" 500 http://deb.debian.org/debian trixie/main amd64 Packages\n"
                                         "     release v=13.1,o=Debian,a=stable,n=trixie,l=Debian,c=main\n")
    done = _make_pool(tmp_path, bindir, "--out", posix(tmp_path / "out"), *mode)
    assert done.returncode == 1, done.stdout + done.stderr
    assert "trixie-security אינו במקורות של המארח הבונה" in done.stderr
    assert not any(" -s " in row or "download" in row for row in calls(tmp_path) if row.startswith("apt-get"))


@POSIX
def test_make_pool_security_packs_the_closure_with_a_valid_lock(tmp_path, bindir):
    """#1803: the whole closure of packages.txt (at the candidate versions -- the security
    ones where there are), so the update button can install a package new in the list."""
    _pool_tools(tmp_path, bindir)
    out = tmp_path / "out"
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(out))
    assert done.returncode == 0, done.stdout + done.stderr
    dl = next(row for row in calls(tmp_path) if row.startswith("apt-get download"))
    assert dl.split()[2:] == ["libssl3t64=3.5.1-1+deb13u1", "libc6=2.41-12",
                              "openssh-client=1:10.0p1-7+deb13u1", "dnsmasq-base=2.90-4+deb13u1",
                              "python3-security=1.0-1", "dnsmasq=2.90-4+deb13u1"], dl
    lock = json.loads((out / "security-pool.json").read_text(encoding="utf-8"))
    tar_path = out / lock["asset"]
    raw = tar_path.read_bytes()
    assert lock["sha256"] == hashlib.sha256(raw).hexdigest() and lock["size"] == len(raw)
    assert lock["packages"] == 6 and lock["security"] == 3, lock
    assert lock["asset"] == f"imagectl-security-pool-{lock['date']}.tar"
    with tarfile.open(tar_path) as tar:
        names = sorted(tar.getnames())
    debs = [n for n in names if n.endswith(".deb")]
    assert debs == ["pool/main/imagectl/dnsmasq-base_2.90-4+deb13u1_amd64.deb",
                    "pool/main/imagectl/dnsmasq_2.90-4+deb13u1_amd64.deb",
                    "pool/main/imagectl/libc6_2.41-12_amd64.deb",
                    "pool/main/imagectl/libssl3t64_3.5.1-1+deb13u1_amd64.deb",
                    "pool/main/imagectl/openssh-client_1%3a10.0p1-7+deb13u1_amd64.deb",
                    "pool/main/imagectl/python3-security_1.0-1_amd64.deb"], names
    assert "dists/trixie/main/binary-amd64/Packages.gz" in names and "pool-date" in names
    assert _lock_cli(tmp_path, (out / "security-pool.json").read_text(encoding="utf-8")).returncode == 0


@POSIX
def test_make_pool_security_with_no_security_package_is_a_failure(tmp_path, bindir):
    _pool_tools(tmp_path, bindir, sim="Inst libc6 (2.41-12 Debian:13.1/stable [amd64])\n")
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(tmp_path / "out"))
    assert done.returncode == 1 and "אף חבילה בסגירה אינה מ-trixie-security" in done.stderr
    assert not (tmp_path / "out" / "security-pool.json").exists()


@POSIX
def test_make_pool_security_refuses_an_out_dir_with_an_old_pool(tmp_path, bindir):
    _pool_tools(tmp_path, bindir)
    (tmp_path / "out" / "pool" / "main").mkdir(parents=True)
    done = _make_pool(tmp_path, bindir, "--security", "--out", posix(tmp_path / "out"))
    assert done.returncode == 1 and "כבר קיים" in done.stderr
    assert not any(row.startswith("apt-get") for row in calls(tmp_path))


# ---- the server screen ----------------------------------------------------------------

def _row(frame: str, label: str) -> list[str]:
    records = [line.split("\t") for line in frame.rstrip("\n").split("\n")]
    return next(rec for rec in records if rec[0] == "row" and rec[1] == label)


def _frame(snapshot) -> str:
    runtime = samples._runtime(snapshot, samples.PRIMARY_EXTRAS)
    return view.render(runtime, samples.NOW, samples.NOW, samples.CLOCK, samples.TODAY, 47)


def test_the_server_screen_shows_the_date_of_the_system_updates():
    row = _row(_frame(samples.PRIMARY), "עדכוני מערכת")
    assert row[3] == f"מתאריך {view.FSI}{DAY}{view.PDI}", row
    assert row[5] == "" and row[6:8] == ["", ""]
    records = [line.split("\t") for line in _frame(samples.PRIMARY).split("\n")]
    tiles = [rec[1] for rec in records[:records.index(row)] if rec[0] == "tile"]
    assert tiles[-1] == "שרתים והפצה", "the lower tile, which is not clipped by problems"


def test_an_unknown_date_is_said_not_hidden():
    row = _row(_frame(replace(samples.PRIMARY, system_updates="")), "עדכוני מערכת")
    assert row[3] == "תאריך לא ידוע" and row[6:8] == ["warn", "לא נבדק"], row


def test_the_text_screen_shows_it_too():
    lines = dcui_app.render_main(samples.PRIMARY)
    assert any("System updates:" in line and f"from {DAY}" in line for line in lines)
    lines = dcui_app.render_main(replace(samples.PRIMARY, system_updates=""))
    assert any("System updates:" in line and "date unknown" in line for line in lines)


def test_the_date_is_the_last_installed_pool_else_the_isos(tmp_path):
    manifest = tmp_path / "iso-release.json"
    manifest.write_text(json.dumps({"imagectl_tag": "v0.70.0", "pool_date": "2026-09-30"}),
                        encoding="utf-8")
    assert dcui_data.system_updates_date(tmp_path, manifest) == "2026-09-30"
    (tmp_path / "security-pool.json").write_text(json.dumps({"date": DAY}), encoding="utf-8")
    assert dcui_data.system_updates_date(tmp_path, manifest) == DAY


def test_an_unreadable_installed_pool_is_unknown_not_the_older_iso_date(tmp_path):
    manifest = tmp_path / "iso-release.json"
    manifest.write_text(json.dumps({"pool_date": "2026-09-30"}), encoding="utf-8")
    (tmp_path / "security-pool.json").write_text("{", encoding="utf-8")
    assert dcui_data.system_updates_date(tmp_path, manifest) == ""
    (tmp_path / "security-pool.json").unlink()
    manifest.write_text(json.dumps({"imagectl_tag": "v0.60.0"}), encoding="utf-8")
    assert dcui_data.system_updates_date(tmp_path, manifest) == "", "an old ISO has no date"
    assert dcui_data.system_updates_date(tmp_path, tmp_path / "missing.json") == ""


def test_the_snapshot_carries_the_date_to_both_screens():
    assert "system_updates" in {f.name for f in dcui_data.Snapshot.__dataclass_fields__.values()}
    assert datetime.strptime(samples.PRIMARY.system_updates, "%Y-%m-%d")
