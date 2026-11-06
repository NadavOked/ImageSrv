"""#2520 -- the ISO's merged apt index is version-consistent, or the build stops.

From v0.82.0 to v0.84.1 every ISO stopped at 45% (`apt-install`): the pool
brought apt 3.0.4 (trixie-security, 04/11) next to the netinst's apt-utils
3.0.3, which depends on `apt (= 3.0.3)`. debootstrap installed both, and
`apt-get install` refused the broken set. Three guards here:

* `tools/iso/check-pool.py` finds a versioned relation that the newest
  versions in the index cannot meet (the real stanzas of the v0.84.0 ISO).
* `build-iso.sh` runs it on the merged index and stops on a skew -- the
  block between the index check and the code section is executed.
* `make-pool.sh` downloads debootstrap's base (Priority required +
  important) too, so apt-utils comes at the same snapshot as apt -- the
  whole script runs against fake apt tools that record what was requested.

Negative control (bakara-shlilit): build-iso.sh and make-pool.sh reverted
to origin/main, and check-pool.py mutated (`satisfies` always true).
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
ISO_DIR = REPO / "tools" / "iso"
CHECK = ISO_DIR / "check-pool.py"
BUILD_ISO = ISO_DIR / "build-iso.sh"
MAKE_POOL = ISO_DIR / "make-pool.sh"


def _find_bash() -> str | None:
    if os.name == "nt":
        for candidate in (r"C:\Program Files\Git\usr\bin\bash.exe",
                          r"C:\Program Files\Git\bin\bash.exe"):
            if Path(candidate).exists():
                return candidate
    return shutil.which("bash")


BASH = _find_bash()


def _sh_dir(p: Path) -> str:
    """A directory for bash's PATH: on Git Bash `C:/x` would split at the colon -> `/c/x`."""
    posix = p.as_posix()
    return f"/{posix[0].lower()}{posix[2:]}" if os.name == "nt" and posix[1:2] == ":" else posix
needs_bash = requires_native(("bash", BASH), why="build-iso.sh / make-pool.sh are bash")

# The stanzas of the official v0.84.0 ISO (sha256 da9f95c7...22d6b),
# dists/trixie/main/binary-amd64/Packages.gz, trimmed to the fields that matter.
APT_0840 = """\
Package: apt
Version: 3.0.3
Depends: base-passwd (>= 3.6.1) | adduser, libapt-pkg7.0 (>= 3.0.3), libc6 (>= 2.38)
Filename: pool/main/a/apt/apt_3.0.3_amd64.deb

Package: apt
Version: 3.0.4
Depends: base-passwd (>= 3.6.1) | adduser, libapt-pkg7.0 (>= 3.0.4), libc6 (>= 2.38)
Filename: pool/main/imagesrv/apt_3.0.4_amd64.deb

Package: apt-utils
Version: 3.0.3
Depends: apt (= 3.0.3), libapt-pkg7.0 (>= 2.9.27+exp1), libc6 (>= 2.34)
Filename: pool/main/a/apt/apt-utils_3.0.3_amd64.deb

Package: libapt-pkg7.0
Version: 3.0.3
Depends: libc6 (>= 2.38)
Filename: pool/main/a/apt/libapt-pkg7.0_3.0.3_amd64.deb

Package: libapt-pkg7.0
Version: 3.0.4
Depends: libc6 (>= 2.38)
Filename: pool/main/imagesrv/libapt-pkg7.0_3.0.4_amd64.deb

Package: libc6
Version: 2.41-12
Filename: pool/main/g/glibc/libc6_2.41-12_amd64.deb

Package: base-passwd
Version: 3.6.7
Filename: pool/main/b/base-passwd/base-passwd_3.6.7_amd64.deb
"""

APT_UTILS_304 = """
Package: apt-utils
Version: 3.0.4
Depends: apt (= 3.0.4), libapt-pkg7.0 (>= 2.9.27+exp1), libc6 (>= 2.34)
Filename: pool/main/imagesrv/apt-utils_3.0.4_amd64.deb
"""


def _index(path: Path, text: str, gz: bool = True) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode("utf-8")
    path.write_bytes(gzip.compress(data, mtime=0) if gz else data)
    return path


def _check(*paths: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-I", str(CHECK), *map(str, paths)], capture_output=True,
                          text=True, encoding="utf-8", stdin=subprocess.DEVNULL, timeout=60)


# --- check-pool.py ---------------------------------------------------------------

def test_the_v0840_index_is_a_skew_named_by_package(tmp_path):
    proc = _check(_index(tmp_path / "main" / "Packages.gz", APT_0840))
    assert proc.returncode == 1, proc.stdout + proc.stderr
    skews = [line for line in proc.stdout.splitlines() if line.startswith("check-pool: skew:")]
    assert skews == ["check-pool: skew: apt-utils 3.0.3: Depends: apt (= 3.0.3) -- the index has apt 3.0.4"], proc.stdout


def test_apt_utils_at_the_same_snapshot_is_consistent(tmp_path):
    proc = _check(_index(tmp_path / "main" / "Packages.gz", APT_0840 + APT_UTILS_304))
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("check-pool: consistent: 5 packages,"), proc.stdout


def test_the_components_are_one_index(tmp_path):
    """The skew crosses components: apt-utils in contrib against apt in main is still judged."""
    main = _index(tmp_path / "main" / "Packages.gz", APT_0840.split("\nPackage: apt-utils")[0] + "\n")
    contrib = _index(tmp_path / "contrib" / "Packages", "Package: apt-utils\nVersion: 3.0.3\n"
                     "Depends: apt (= 3.0.3)\n", gz=False)
    proc = _check(main, contrib)
    assert proc.returncode == 1 and "apt-utils 3.0.3" in proc.stdout, proc.stdout + proc.stderr


@pytest.mark.parametrize("text,expect", [
    # an upper bound that the pool's newer target breaks is the same failure
    ("Package: a\nVersion: 1\nDepends: b (<< 2)\n\nPackage: b\nVersion: 2\n", 1),
    # one satisfied alternative is enough
    ("Package: a\nVersion: 1\nDepends: b (= 1) | c\n\nPackage: b\nVersion: 2\n\nPackage: c\nVersion: 1\n", 0),
    # a versioned Provides satisfies a versioned dependency
    ("Package: a\nVersion: 1\nDepends: v (>= 2)\n\nPackage: v\nVersion: 1\n\n"
     "Package: p\nVersion: 9\nProvides: v (= 3)\n", 0),
    # a target that is not in the index is not this check's judgement
    ("Package: a\nVersion: 1\nDepends: elsewhere (= 7)\n", 0),
    # epochs and tildes order the Debian way: 1:1.0 > 9.9, 2.0~rc1 < 2.0
    ("Package: a\nVersion: 1\nDepends: b (>> 9.9), c (<< 2.0)\n\nPackage: b\nVersion: 1:1.0\n\n"
     "Package: c\nVersion: 2.0~rc1\n", 0),
    # binNMU/revision: 2.41-12+b1 is newer than 2.41-12
    ("Package: a\nVersion: 1\nDepends: b (= 2.41-12)\n\nPackage: b\nVersion: 2.41-12\n\n"
     "Package: b\nVersion: 2.41-12+b1\n", 1),
])
def test_relations(tmp_path, text, expect):
    proc = _check(_index(tmp_path / "Packages", text, gz=False))
    assert proc.returncode == expect, proc.stdout + proc.stderr


def test_an_empty_or_unreadable_index_is_not_checked(tmp_path):
    """Principle 5: no packages and no file are 'not checked' (2), never 'consistent' (0)."""
    empty = _check(_index(tmp_path / "Packages.gz", ""))
    missing = _check(tmp_path / "nope" / "Packages.gz")
    assert empty.returncode == 2 and "not checked" in empty.stderr, empty.stdout + empty.stderr
    assert missing.returncode == 2 and "not checked" in missing.stderr, missing.stdout + missing.stderr


# --- build-iso.sh: the merged index is checked, and a skew stops the build ---------

def _build_iso_gate() -> str:
    """From right after the Packages.gz loop to the code section: two anchors that predate #2520."""
    text = BUILD_ISO.read_text(encoding="utf-8")
    start = text.index('die "אין Packages.gz ל-$comp אחרי האינדוקס"\ndone\n')
    start = text.index("done\n", start) + len("done\n")
    end = text.index("# --- הקוד ---")
    return text[start:end]


def _run_gate(tmp_path: Path, main_index: str) -> subprocess.CompletedProcess:
    tree = tmp_path / "tree"
    _index(tree / "dists" / "trixie" / "main" / "binary-amd64" / "Packages.gz", main_index)
    _index(tree / "dists" / "trixie" / "contrib" / "binary-amd64" / "Packages.gz", "")
    bindir = tmp_path / "bin"   # python3 = the interpreter running the tests (Windows has no python3)
    bindir.mkdir()
    (bindir / "python3").write_text(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
                                    encoding="utf-8", newline="\n")
    (bindir / "python3").chmod(0o755)
    script = ("set -euo pipefail\n"
              "die() { printf 'build-iso: %s\\n' \"$*\" >&2; exit 1; }\n"
              "say() { printf 'build-iso: %s\\n' \"$*\"; }\n"
              f"SCRIPT_DIR='{ISO_DIR.as_posix()}'; ISO_TREE='{tree.as_posix()}'\n"
              f"PATH='{_sh_dir(bindir)}':\"$PATH\"\n"
              + _build_iso_gate() + "echo GATE_PASSED\n")
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          stdin=subprocess.DEVNULL, timeout=120)


@needs_bash
def test_build_iso_stops_on_the_v0840_skew(tmp_path):
    proc = _run_gate(tmp_path, APT_0840)
    assert proc.returncode != 0, f"build-iso went on with apt-utils 3.0.3 against apt 3.0.4:\n{proc.stdout}"
    assert "GATE_PASSED" not in proc.stdout
    assert "apt-utils 3.0.3" in proc.stderr and "check-pool.py" in proc.stderr, proc.stderr


@needs_bash
def test_build_iso_goes_on_with_a_consistent_index(tmp_path):
    proc = _run_gate(tmp_path, APT_0840 + APT_UTILS_304)
    assert proc.returncode == 0 and "GATE_PASSED" in proc.stdout, proc.stdout + proc.stderr
    assert "consistent:" in proc.stdout, proc.stdout


# --- make-pool.sh: debootstrap's base is downloaded with the rest -------------------

FAKES = {
    "id": '#!/bin/sh\n[ "$1" = -u ] && echo 0 && exit 0\nexit 1\n',
    "apt-cache": r'''#!/bin/sh
case "$1" in
  policy) if [ $# -eq 1 ]; then echo ' release o=Debian,n=trixie-security,c=main'; else echo '  Candidate: 1.0'; fi ;;
  dumpavail) [ -n "${FAKE_DUMPAVAIL_FAIL:-}" ] && exit 100; cat "$FAKE_DUMPAVAIL" ;;
  *) exit 2 ;;
esac
''',
    "apt-get": r'''#!/bin/sh
[ "$1" = update ] && exit 0
dl=; seen=
for a in "$@"; do
  case "$a" in Dir::Cache::archives=*) dl=${a#Dir::Cache::archives=} ;; esac
  if [ -n "$seen" ]; then echo "$a" >> "$FAKE_LOG.download"; : > "$dl/${a}_1.0_amd64.deb"; fi
  [ "$a" = install ] && seen=1 && echo "--- call" >> "$FAKE_LOG.download"
done
exit 0
''',
    "dpkg-deb": '#!/bin/sh\necho main\n',
    "dpkg-scanpackages": r'''#!/bin/sh
for f in "$4"/*.deb "$4"/*/*.deb; do [ -e "$f" ] || continue; b=${f##*/}; printf 'Package: %s\nVersion: 1.0\nFilename: %s\n\n' "${b%%_*}" "$f"; done
''',
    # -osirrox on -indev ISO -extract /dists DEST: the netinst's dists/ is a prepared tree.
    "xorriso": r'''#!/bin/sh
[ -n "${FAKE_XORRISO_FAIL:-}" ] && exit 5
cp -r "$FAKE_NETINST_DISTS" "$7"
''',
    "python3": f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "$@"\n',
}

# The 06/11 lab build: vim on the netinst pins vim-common, which the base lifts (+deb13u1).
NETINST_VIM = """\
Package: vim
Version: 0.9
Depends: vim-common (= 0.9), libc6

Package: vim-common
Version: 0.9

Package: hello
Version: 0.9
"""

DUMPAVAIL = """\
Package: apt
Priority: required
Version: 3.0.4

Package: apt-utils
Version: 3.0.4
Priority: important

Package: nano
Priority: important

Package: hello
Priority: optional
"""


def _run_make_pool(tmp_path: Path, dumpavail: str, fail: bool = False,
                   netinst: str | None = None, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for name, body in FAKES.items():
        (bindir / name).write_text(body, encoding="utf-8", newline="\n")
        (bindir / name).chmod(0o755)
    (tmp_path / "dumpavail").write_text(dumpavail, encoding="utf-8", newline="\n")
    (tmp_path / "packages.txt").write_text("# list\nnftables\npartclone\n", encoding="utf-8", newline="\n")
    env = {**os.environ, "FAKE_DUMPAVAIL": (tmp_path / "dumpavail").as_posix(),
           "FAKE_LOG": (tmp_path / "log").as_posix()}
    if fail:
        env["FAKE_DUMPAVAIL_FAIL"] = "1"
    extra = ""
    if netinst is not None:
        main = tmp_path / "netinst-dists" / "trixie" / "main" / "binary-amd64"
        main.mkdir(parents=True)
        (main / "Packages.gz").write_bytes(gzip.compress(netinst.encode()))
        (tmp_path / "netinst.iso").write_bytes(b"iso")
        env["FAKE_NETINST_DISTS"] = (tmp_path / "netinst-dists").as_posix()
        extra = f" --netinst '{(tmp_path / 'netinst.iso').as_posix()}'"
    env.update(env_extra or {})
    script = (f"PATH='{_sh_dir(bindir)}':\"$PATH\"\n"
              f"exec bash '{MAKE_POOL.as_posix()}' --out '{(tmp_path / 'out').as_posix()}' "
              f"--packages '{(tmp_path / 'packages.txt').as_posix()}'{extra}\n")
    return subprocess.run([BASH, "-c", script], capture_output=True, text=True, encoding="utf-8",
                          env=env, stdin=subprocess.DEVNULL, timeout=120)


def _downloaded(tmp_path: Path) -> set[str]:
    log = tmp_path / "log.download"
    return set(log.read_text(encoding="utf-8").split()) - {"---", "call"} if log.exists() else set()


@needs_bash
def test_make_pool_downloads_debootstraps_base_with_the_list(tmp_path):
    proc = _run_make_pool(tmp_path, DUMPAVAIL)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = _downloaded(tmp_path)
    assert {"nftables", "partclone"} <= got, got
    assert {"apt", "apt-utils", "nano"} <= got, f"the base (required + important) was not downloaded: {got}"
    assert "hello" not in got, "an optional package is not debootstrap's base"
    assert any(p.name.startswith("apt-utils_") for p in (tmp_path / "out" / "pool").rglob("*.deb"))


@needs_bash
def test_make_pool_stops_when_the_base_cannot_be_read(tmp_path):
    """Principle 5: dumpavail failing, or a base without apt/apt-utils, is a broken read -- not 'no base'."""
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    failed = _run_make_pool(tmp_path / "a", DUMPAVAIL, fail=True)
    no_utils = _run_make_pool(tmp_path / "b", DUMPAVAIL.replace("apt-utils", "apt-doc"))
    assert failed.returncode != 0 and "dumpavail" in failed.stderr, failed.stdout + failed.stderr
    assert no_utils.returncode != 0 and "apt-utils" in no_utils.stderr, no_utils.stdout + no_utils.stderr
    assert not (tmp_path / "b" / "out" / "pool" / "main").exists(), "nothing is downloaded on a broken base"


DUMPAVAIL_VIM = DUMPAVAIL + "\nPackage: vim-common\nPriority: important\n"


@needs_bash
def test_make_pool_lifts_the_netinst_packages_that_pin_a_lifted_sibling(tmp_path):
    """The 06/11 lab build stopped on `vim 2:9.1.1230-2: Depends: vim-common (= 2:9.1.1230-2)`:
    the base lifted vim-common, and vim is on the netinst only. --netinst downloads the newer vim."""
    proc = _run_make_pool(tmp_path, DUMPAVAIL_VIM, netinst=NETINST_VIM)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got = _downloaded(tmp_path)
    assert "vim" in got, f"vim on the netinst pins vim-common (= 0.9), and it was not lifted: {got}"
    assert "hello" not in got, "a netinst package that pins nothing we lifted stays the netinst's"
    assert any(p.name.startswith("vim_") for p in (tmp_path / "out" / "pool").rglob("*.deb"))
    assert "consistent" in proc.stdout, proc.stdout
    assert not (tmp_path / "out" / ".netinst-dists").exists()


@needs_bash
def test_make_pool_stops_when_the_netinst_cannot_be_read(tmp_path):
    """Principle 5: a netinst whose dists/ was not read is 'not checked', not 'nothing to lift'."""
    proc = _run_make_pool(tmp_path, DUMPAVAIL_VIM, netinst=NETINST_VIM, env_extra={"FAKE_XORRISO_FAIL": "1"})
    assert proc.returncode != 0 and "xorriso" in proc.stderr, proc.stdout + proc.stderr
    assert not (tmp_path / "out" / "pool" / "main").exists()


def test_the_security_pool_carries_the_base_too():
    """--security builds the update button's pool: an installed server has apt-utils, so apt alone breaks it."""
    text = MAKE_POOL.read_text(encoding="utf-8")
    block = text[text.index('if [[ -n "$SECURITY" ]]; then\n    # אותה סגירה'):text.index("sort_into_pool\n    write_index")]
    assert 'install "${BASE[@]}"' in block and 'sim+=' in block, block


# --- install-test.py: the answers it writes are the ones the engine demands ---------

def _install_test_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("install_test", ISO_DIR / "install-test.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_install_test_answers_carry_every_key_the_engine_requires():
    """parse_answers (installer/lib/answers.sh) fails on a missing key: the full-install
    check would then die on missing-key and read as 'the ISO is broken'."""
    import re
    text = (REPO / "installer" / "lib" / "answers.sh").read_text(encoding="utf-8")
    loop = re.search(r"for _pa_key in (.*?); do", text, re.S).group(1)
    required = set(loop.replace("\\\n", " ").split())
    answers = _install_test_module().answers_text("eth0")
    keys = {line.split("=", 1)[0] for line in answers.splitlines()}
    assert len(required) >= 10 and required <= keys, sorted(required - keys)
    assert "disk=/dev/vda" in answers and "role=standalone" in answers and "servers_mode=dhcp" in answers


def test_install_test_reads_only_the_state_files_own_lines():
    """The terminal echoes the command; only state=/pct=/title=/log=/error= lines count."""
    out = "# cat /tmp/install.state; echo RC$?abcdEND\r\nstate=failed\r\npct=45\r\nerror=apt-install: x\r\n"
    assert _install_test_module().read_state(out) == {"state": "failed", "pct": "45", "error": "apt-install: x"}


class _FakeGuest:
    """Serial stand-in around the real Serial.run: records the bytes it types, answers like the live image."""

    def __init__(self, engine_up=True):
        self.lines, self.engine_up = [], engine_up
        self.real_run = _install_test_module().Serial.run

    def __call__(self, path, log):  # constructed as Serial(sock, log)
        return self

    @property
    def sock(self):
        return self

    def wait_shell(self, seconds):
        return True

    def sendall(self, data):
        self.lines.append(data.decode().rstrip("\r"))

    def _read(self, seconds, until=None):
        import re
        sent = self.lines[-1]
        rc, reply = self.reply(sent)
        tag = re.search(r"RC\$\?(\w+)END$", sent).group(1)
        return f"{sent}\r\n{reply}RC{rc}{tag}END".encode()

    def reply(self, sent):
        if "echo NIC=" in sent:
            return 0, "NIC=eth0\r\n"
        if "echo PID=$!" in sent:
            return 0, "PID=123\r\n"
        if sent.startswith("cat /tmp/install.state"):
            if not self.engine_up:
                return 1, "state=install\r\npct=45\r\n"
            return 0, "state=done\r\npct=100\r\nENGINE=up\r\n"
        return 0, ""

    def run(self, cmd, seconds=60):
        return self.real_run(self, cmd, seconds)


def _drive(tmp_path, guest, monkeypatch):
    import types
    mod = _install_test_module()
    monkeypatch.setattr(mod, "Serial", guest)
    monkeypatch.setattr(mod.time, "sleep", lambda s: None)
    (tmp_path / "serial.sock").touch()
    args = types.SimpleNamespace(out=str(tmp_path), iso="x.iso", boot_timeout=1, timeout=60)
    qemu = types.SimpleNamespace(poll=lambda: None, returncode=None)
    return mod.drive(args, qemu, str(tmp_path / "serial.sock"))


@needs_bash
def test_install_test_types_only_lines_the_shell_can_parse(tmp_path, monkeypatch):
    """The first QEMU run (06/11): `engine ... &` + `; echo RC$?` = `&;`, a syntax error in
    the guest's ash -- 'could not start the engine' on every ISO, good or broken."""
    guest = _FakeGuest()
    assert _drive(tmp_path, guest, monkeypatch) == 0
    assert any("--state /tmp/install.state" in line for line in guest.lines), guest.lines
    for line in guest.lines:
        proc = subprocess.run([BASH, "-n", "-c", line], capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30)
        assert proc.returncode == 0, f"{line}\n{proc.stderr}"


def test_install_test_fails_when_the_engine_is_gone_without_a_final_state(tmp_path, monkeypatch, capsys):
    """An engine that died at 45% without writing failed is a fail now -- not a 3h wait, never a pass."""
    assert _drive(tmp_path, _FakeGuest(engine_up=False), monkeypatch) == 1
    assert "the engine exited without state=done/failed" in capsys.readouterr().out
