"""#3217: a package the netinst carries is lifted when trixie-security has replaced it.

The v0.94.3 SBOM (grype, 14/11) found `apt-transport-https 3.0.3` on the ISO while
`apt`, `apt-utils` and `libapt-pkg7.0` were at 3.0.4 (CVE-2026-109518, fixed in 3.0.4).
The pool holds the closure of packages.txt plus debootstrap's base; a transitional
package (Section oldlibs, Priority optional, `Depends: apt (>= 1.5~alpha4)`) is in neither,
and nothing pins it exactly, so neither the closure nor check-pool.py reached it.

`tools/iso/netinst-lift.py` finds every netinst package whose apt candidate comes from a
*-security suite and is newer than the netinst's; `make-pool.sh --netinst` downloads them.

The policy texts below are the real shape of `apt-cache policy` on Debian 13 (testrunner,
14/11/2026), trimmed. Negative control: see the PR body -- every production file reverted.
"""

from __future__ import annotations

import gzip
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from test_iso_pool_consistency_2520 import (
    DUMPAVAIL, ISO_DIR, _downloaded, _run_make_pool, needs_bash,
)

LIFT = ISO_DIR / "netinst-lift.py"

# What the netinst carries (the 13.7.0 ISO): the transitional package at 3.0.3, an untouched one.
NETINST = """\
Package: apt-transport-https
Version: 3.0.3

Package: hello
Version: 2.10-3

Package: libssl3t64
Version: 3.5.1-1
"""

POLICY_APT_TRANSPORT = """\
apt-transport-https:
  Installed: (none)
  Candidate: 3.0.4
  Version table:
     3.0.4 500
        500 mirror+file:/etc/apt/mirrors/debian-security.list trixie-security/main amd64 Packages
     3.0.3 500
        500 mirror+file:/etc/apt/mirrors/debian.list trixie/main amd64 Packages
"""
POLICY_HELLO = """\
hello:
  Installed: (none)
  Candidate: 2.10-3
  Version table:
     2.10-3 500
        500 mirror+file:/etc/apt/mirrors/debian.list trixie/main amd64 Packages
"""
POLICY_SSL_UPDATES = """\
libssl3t64:
  Installed: 3.5.1-1
  Candidate: 3.5.2-1
  Version table:
     3.5.2-1 500
        500 mirror+file:/etc/apt/mirrors/debian.list trixie-updates/main amd64 Packages
 *** 3.5.1-1 100
        100 /var/lib/dpkg/status
     3.5.1-1 500
        500 mirror+file:/etc/apt/mirrors/debian.list trixie/main amd64 Packages
"""
POLICY = POLICY_APT_TRANSPORT + POLICY_HELLO + POLICY_SSL_UPDATES


def _module():
    spec = importlib.util.spec_from_file_location("netinst_lift", LIFT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _index(tmp_path: Path, text: str = NETINST) -> Path:
    path = tmp_path / "Packages.gz"
    path.write_bytes(gzip.compress(text.encode()))
    return path


def _cli(tmp_path: Path, mode: str, stdin: str = "", index: str = NETINST):
    return subprocess.run([sys.executable, "-I", str(LIFT), mode, str(_index(tmp_path, index))],
                          input=stdin, capture_output=True, text=True, encoding="utf-8", timeout=60)


# -- netinst-lift.py -----------------------------------------------------------------------

def test_the_transitional_package_with_a_security_candidate_is_lifted_and_nothing_else(tmp_path):
    proc = _cli(tmp_path, "lift", POLICY)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == ["apt-transport-https=3.0.4"], proc.stdout


def test_a_candidate_from_updates_or_the_same_suite_is_not_a_security_lift(tmp_path):
    """Only *-security: libssl3t64's newer candidate is trixie-updates, hello has nothing newer."""
    proc = _cli(tmp_path, "lift", POLICY_HELLO + POLICY_SSL_UPDATES + POLICY_APT_TRANSPORT)
    assert "libssl3t64" not in proc.stdout and "hello" not in proc.stdout


def test_the_netinst_already_at_the_security_version_is_not_lifted(tmp_path):
    proc = _cli(tmp_path, "lift", POLICY, index=NETINST.replace("3.0.3", "3.0.4"))
    assert proc.returncode == 0 and proc.stdout.split() == []


def test_a_security_candidate_older_than_the_netinst_is_not_lifted(tmp_path):
    """Version order, not 'different': the netinst's 3.0.5 is newer than the archive's 3.0.4."""
    proc = _cli(tmp_path, "lift", POLICY, index=NETINST.replace("3.0.3", "3.0.5"))
    assert proc.returncode == 0 and "apt-transport-https" not in proc.stdout


def test_epoch_and_revision_are_compared_the_debian_way():
    mod = _module()
    index = {"vim": {"Version": "2:9.1.1230-2"}}
    policy = ("vim:\n  Candidate: 2:9.1.1230-2+deb13u1\n  Version table:\n"
              "     2:9.1.1230-2+deb13u1 500\n        500 http://s/ trixie-security/main amd64 Packages\n")
    assert mod.lift(index, policy) == (["vim=2:9.1.1230-2+deb13u1"], [])


def test_names_lists_every_package_of_the_index(tmp_path):
    proc = _cli(tmp_path, "names")
    assert proc.returncode == 0 and proc.stdout.split() == ["apt-transport-https", "hello", "libssl3t64"]


@pytest.mark.parametrize("policy", ["", "N: Unable to locate package apt-transport-https\n",
                                    "garbage\nwithout blocks\n"],
                         ids=["empty", "apt-said-no", "no-blocks"])
def test_a_policy_that_answered_nothing_is_not_checked_not_nothing_to_lift(tmp_path, policy):
    """Principle 5: an empty list from a failed read looks exactly like 'no security updates'."""
    proc = _cli(tmp_path, "lift", policy)
    assert proc.returncode == 2 and "not checked" in proc.stderr, (proc.returncode, proc.stderr)
    assert proc.stdout == ""


def test_a_package_apt_does_not_know_is_said_and_not_lifted_but_all_unknown_is_not_checked(tmp_path):
    some = _cli(tmp_path, "lift", POLICY_APT_TRANSPORT)          # hello and libssl3t64 have no block
    assert some.returncode == 0 and "2 netinst packages have no candidate" in some.stderr
    assert some.stdout.split() == ["apt-transport-https=3.0.4"]
    none = _cli(tmp_path, "lift", "other-package:\n  Candidate: 1\n  Version table:\n     1 500\n"
                                  "        500 http://s/ trixie/main amd64 Packages\n")
    assert none.returncode == 2 and "knows none" in none.stderr


def test_a_candidate_that_is_not_in_its_own_table_is_not_trusted(tmp_path):
    bad = "apt-transport-https:\n  Candidate: 3.0.4\n  Version table:\n     3.0.3 500\n        500 http://s/ trixie/main amd64 Packages\n"
    proc = _cli(tmp_path, "lift", bad + POLICY_HELLO)
    assert proc.returncode == 2 and "not in its own version table" in proc.stderr


def test_an_index_that_cannot_be_read_is_not_checked(tmp_path):
    (tmp_path / "Packages.gz").write_bytes(b"not gzip")
    proc = subprocess.run([sys.executable, "-I", str(LIFT), "names", str(tmp_path / "Packages.gz")],
                          capture_output=True, text=True, timeout=60, stdin=subprocess.DEVNULL)
    assert proc.returncode == 2 and "not checked" in proc.stderr


# -- make-pool.sh --netinst ----------------------------------------------------------------

def _policy_file(tmp_path: Path, text: str) -> dict:
    path = tmp_path / "policy.txt"
    path.write_text(text, encoding="utf-8", newline="\n")
    return {"FAKE_POLICY": path.as_posix()}


@needs_bash
def test_make_pool_downloads_the_security_update_of_a_netinst_only_package(tmp_path):
    proc = _run_make_pool(tmp_path, DUMPAVAIL, netinst=NETINST, env_extra=_policy_file(tmp_path, POLICY))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = _downloaded(tmp_path)
    assert "apt-transport-https=3.0.4" in got, got
    assert not {"hello", "libssl3t64"} & {g.split("=")[0] for g in got}, "no security update, not lifted"
    assert any(p.name.startswith("apt-transport-https_3.0.4_") for p in (tmp_path / "out" / "pool").rglob("*.deb"))
    assert "apt-transport-https=3.0.4" in proc.stdout
    assert not (tmp_path / "out" / ".netinst-lift.err").exists()


@needs_bash
def test_make_pool_says_so_when_nothing_of_the_netinst_has_a_security_update(tmp_path):
    proc = _run_make_pool(tmp_path, DUMPAVAIL, netinst=NETINST,
                          env_extra=_policy_file(tmp_path, POLICY_HELLO + POLICY_SSL_UPDATES + POLICY_APT_TRANSPORT.replace("trixie-security", "trixie")))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "אין חבילה של ה-netinst עם עדכון" in proc.stdout
    assert all("=" not in g for g in _downloaded(tmp_path))


@needs_bash
def test_make_pool_stops_when_apt_cache_policy_fails(tmp_path):
    proc = _run_make_pool(tmp_path, DUMPAVAIL, netinst=NETINST, env_extra={"FAKE_POLICY_FAIL": "1"})
    assert proc.returncode != 0 and "apt-cache policy" in proc.stderr, proc.stdout + proc.stderr
    assert not (tmp_path / "out" / "pool" / "main").exists()


@needs_bash
def test_make_pool_stops_when_policy_answers_nothing_usable(tmp_path):
    proc = _run_make_pool(tmp_path, DUMPAVAIL, netinst=NETINST,
                          env_extra=_policy_file(tmp_path, "N: Unable to locate package apt-transport-https\n"))
    assert proc.returncode != 0 and "netinst-lift.py" in proc.stderr and "not checked" in proc.stderr, proc.stderr
    assert not (tmp_path / "out" / "pool" / "main").exists()


@needs_bash
def test_make_pool_stops_when_the_update_was_not_downloaded(tmp_path):
    """Positive evidence per package: a download that ended without the .deb is a failure."""
    proc = _run_make_pool(tmp_path, DUMPAVAIL, netinst=NETINST,
                          env_extra={**_policy_file(tmp_path, POLICY), "FAKE_APT_NO_DEB": "apt-transport-https"})
    assert proc.returncode != 0 and "apt-transport-https 3.0.4" in proc.stderr, proc.stdout + proc.stderr
