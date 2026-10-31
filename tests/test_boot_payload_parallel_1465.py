"""#1465 -- boot-payload-build.sh builds the initrd lines in parallel, all or nothing.

The two usual lines (plain and --with-gui) took 498 s one after the other on the
lab server (6 vCPU, 16/10). Here the real helper runs with a stub
build_initramfs.sh that proves *when* each build ran:

* parallel: each build waits (bounded) for the other to have started -- run one
  after the other, the first one gives up and the payload fails;
* two --with-gui lines never overlap (both would `make -B` the same binary);
* one failed build = the whole payload fails, named, and the old payload is
  byte-for-byte where it was (principle 5);
* the staging tree goes to the build temp dir (on disk), not to /tmp.
"""

from __future__ import annotations

from pathlib import Path

from native import requires_native
from test_boot_payload_1230 import OLD_KVER, _host, _host_env
from test_installer_1131 import BASH, REPO, posix, run_bash

pytestmark = requires_native(("bash", BASH), why="boot-payload-build.sh is bash")

#: Rendezvous: a build marks itself started, then waits up to 15 s for every build
#: named in $STUB_WAIT_FOR to have started too. Sequential builds never meet.
#: GUI builds also hold a mkdir lock for a moment: a second GUI build that finds
#: it held ran at the same time -- the collision the serial queue prevents.
STUB = r"""#!/usr/bin/env bash
out=""; gui=0
for a in "$@"; do case "$prev" in --output) out="$a" ;; esac; prev="$a"; [ "$a" = --with-gui ] && gui=1; done
name=$(basename "$out" .new)
echo "build $name TMPDIR=$TMPDIR"
: > "$STUB_DIR/started-$name"
for want in $STUB_WAIT_FOR; do
  [ "$want" = "$name" ] && continue
  i=0
  while [ ! -e "$STUB_DIR/started-$want" ]; do
    i=$((i + 1)); [ "$i" -gt 150 ] && { echo "$name: $want never started alongside" >&2; exit 4; }
    sleep 0.1
  done
done
if [ "$gui" = 1 ]; then
  mkdir "$STUB_DIR/gui-lock" 2>/dev/null || { echo "$name: another GUI build is running" >&2; exit 5; }
  sleep 1
  rmdir "$STUB_DIR/gui-lock"
fi
case " ${STUB_FAIL:-} " in *" $name "*) echo "$name broke" >&2; exit 3 ;; esac
printf 'new %s\n' "$name" > "$out"
"""


def _setup(tmp_path: Path, lines: list[str]):
    host = _host(tmp_path)
    tree = tmp_path / "app"
    (tree / "tools").mkdir(parents=True)
    (tree / "tools" / "boot-payload-build.sh").write_bytes(
        (REPO / "tools" / "boot-payload-build.sh").read_bytes())
    (tree / "tools" / "build_initramfs.sh").write_text(STUB, encoding="utf-8", newline="\n")
    boot = host / "boot"
    for name in ("initrd.img", "initrd.img.gui", "initrd.img.gui2", "vmlinuz"):
        (boot / name).write_text(f"old {name}\n", encoding="utf-8", newline="\n")
    flags = tmp_path / "initrd.flags"
    flags.write_text("".join(l.format(boot=posix(boot)) + "\n" for l in lines),
                     encoding="utf-8", newline="\n")
    stub_dir = tmp_path / "stub"
    stub_dir.mkdir()
    return host, tree, flags, stub_dir


def _build(tmp_path: Path, lines: list[str], *, wait_for: str = "", fail: str = "",
           tmpdir: str | None = None):
    host, tree, flags, stub_dir = _setup(tmp_path, lines)
    env = {**_host_env(host), "STUB_DIR": posix(stub_dir), "STUB_WAIT_FOR": wait_for,
           "STUB_FAIL": fail}
    if tmpdir is not None:
        env["IMAGESRV_BUILD_TMPDIR"] = tmpdir
    log = tmp_path / "build.log"
    proc = run_bash(
        f'bash "{posix(tree / "tools" / "boot-payload-build.sh")}" --http-root "{posix(host / "boot")}"'
        f' --flags-file "{posix(flags)}" --manifest "{posix(host / "etc" / "iso-release.json")}"'
        f' --build-log "{posix(log)}"', tmp_path, tmp_path, env=env)
    text = log.read_text(encoding="utf-8") if log.exists() else ""
    return proc, host / "boot", text


PLAIN = "--skip-apt --output {boot}/initrd.img"
GUI = "--skip-apt --with-gui --output {boot}/initrd.img.gui"
GUI2 = "--skip-apt --with-gui --output {boot}/initrd.img.gui2"


def test_the_plain_and_the_gui_build_run_at_the_same_time(tmp_path: Path):
    proc, boot, log = _build(tmp_path, [PLAIN, GUI], wait_for="initrd.img initrd.img.gui")
    assert proc.returncode == 0, proc.stderr + log
    assert (boot / "initrd.img").read_text(encoding="utf-8") == "new initrd.img\n"
    assert (boot / "initrd.img.gui").read_text(encoding="utf-8") == "new initrd.img.gui\n"
    assert (boot / "vmlinuz").read_text(encoding="utf-8") == f"kernel {OLD_KVER}\n"
    # the logs come out whole and in line order, not interleaved
    assert log.index("=== build_initramfs.sh --skip-apt --output") < log.index("build initrd.img TMPDIR") \
        < log.index("=== build_initramfs.sh --skip-apt --with-gui") < log.index("build initrd.img.gui TMPDIR")
    assert "1 parallel, 1 in the serial queue" in proc.stderr


def test_the_staging_tree_goes_to_the_build_temp_dir_not_tmp(tmp_path: Path):
    proc, _, log = _build(tmp_path, [PLAIN])
    assert proc.returncode == 0, proc.stderr + log
    assert f"build initrd.img TMPDIR={posix(tmp_path / 'host' / 'var-tmp')}\n" in log
    assert not list((tmp_path / "host" / "var-tmp").iterdir()), "the private log dir is left behind"


def test_a_missing_build_temp_dir_fails_by_name_before_building(tmp_path: Path):
    proc, boot, _ = _build(tmp_path, [PLAIN], tmpdir=posix(tmp_path / "no-such-dir"))
    assert proc.returncode != 0
    assert "build temp dir" in proc.stderr and "previous payload is untouched" in proc.stderr
    assert not (tmp_path / "stub" / "started-initrd.img").exists()
    assert (boot / "initrd.img").read_text(encoding="utf-8") == "old initrd.img\n"


def test_two_gui_lines_never_build_at_the_same_time(tmp_path: Path):
    proc, boot, log = _build(tmp_path, [GUI, GUI2, PLAIN],
                             wait_for="initrd.img initrd.img.gui")   # plain runs beside the GUI queue
    assert proc.returncode == 0, proc.stderr + log
    assert "another GUI build is running" not in log
    for name in ("initrd.img", "initrd.img.gui", "initrd.img.gui2"):
        assert (boot / name).read_text(encoding="utf-8") == f"new {name}\n"


def test_one_failed_build_fails_the_payload_by_name_and_keeps_the_old_one_whole(tmp_path: Path):
    proc, boot, log = _build(tmp_path, [PLAIN, GUI], wait_for="initrd.img initrd.img.gui",
                             fail="initrd.img.gui")
    assert proc.returncode != 0
    last = proc.stderr.strip().splitlines()[-1]
    assert "Build failed: --skip-apt --with-gui" in last and "(exit 3)" in last, last
    assert last.endswith("the previous payload is untouched"), last
    assert "initrd.img.gui broke" in log
    # the plain build succeeded, and still nothing was swapped: one payload, one kernel
    for name in ("initrd.img", "initrd.img.gui", "vmlinuz"):
        assert (boot / name).read_text(encoding="utf-8") == f"old {name}\n", name


def test_every_failure_is_named_not_only_the_first(tmp_path: Path):
    proc, boot, _ = _build(tmp_path, [PLAIN, GUI], fail="initrd.img initrd.img.gui")
    assert proc.returncode != 0
    assert proc.stderr.count("Build failed:") >= 2
    assert "(+1 more); the previous payload is untouched" in proc.stderr
    assert (boot / "initrd.img").read_text(encoding="utf-8") == "old initrd.img\n"
