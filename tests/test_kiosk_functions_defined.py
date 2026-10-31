"""Every command the kiosk can reach must exist in the kiosk's own process.

``imagesrv-kiosk`` is a separate process (tools/build_initramfs.sh writes it:
``. guibridge.sh; gui_main``). Nothing ``imagesrv-agent`` sourced is there
unless ``gui_libs`` (agent/lib/gui/guibridge.sh) loads it. A function the kiosk
reaches that lives in a file ``gui_libs`` does not load fails with
``<fn>: not found`` -- at run time, on the metal, and (a function call that
does not exist is just a failed command) quietly: the caller sees an empty
answer or a non-zero code and carries on.

Two bugs of that family were found on metal on the same day (17/10):

* #1484 -- ``diskfit.sh`` was missing from ``gui_libs``.
* #1588 / PR #1589 -- ``manifest_plan`` (restore.sh), called by
  ``_expand_candidate`` (expand.sh), logged ``manifest_plan: not found`` once
  a second for the whole local clone.

This test closes the family instead of the instance. It does not run the kiosk;
it reads it:

1. the load chain is the real ``gui_libs`` list (plus what ``tools_load`` pulls
   in lazily), parsed from the source, not copied here;
2. ``tests/shcalls.py`` scans those files for every word in command position
   and every function definition;
3. a call graph from the kiosk's entry points (``gui_main``, the top level of
   every loaded file, the toolbox providers) says which calls the kiosk can
   actually make. The load chain is shared with the agent, which has the rest
   of the libraries -- a call that only an agent-side function makes is not a
   kiosk bug, and listing those as exceptions would bury the real ones;
4. each reachable call must resolve to a function in the chain, a shell
   builtin, a busybox applet, or a binary the initramfs builder packs.

What the static view cannot see is listed, not ignored: a command word that is
itself an expansion (``DYNAMIC_SITES``), and a call guarded by an explicit
``command -v NAME`` in the same function (absence is handled there, by design).
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path

from agentlib import flat_lib, lib_files

from native import requires_native
from shcalls import Call, scan_sh
from test_cloner_gui import AGENT, BASH, REPO, posix, sh

LIB = flat_lib()
#: The bugs this test knows about and has not seen fixed yet: name -> where it
#: is tracked. ``test_the_known_broken_set_is_exactly_what_is_tracked`` pins the
#: reachable-missing set to exactly this, so a NEW missing function fails even
#: while the old ones are still open -- an ``xfail`` alone would hide it.
KNOWN_BROKEN: dict[str, str] = {
    # manifest_plan (#1588) and _partition_role (#1591) were here; PR #1589 closed both.
}

# ---------------------------------------------------------------------------
# What counts as "exists in the initramfs"
# ---------------------------------------------------------------------------

#: POSIX special + regular builtins, plus the ones busybox ash (the initramfs
#: /bin/sh) and dash implement beyond POSIX. Deliberately NOT bash's whole
#: ``compgen -b``: this runs under ash, and a bash-only builtin must fail here.
BUILTINS = frozenset(
    ". : [ alias bg break cd command continue echo eval exec exit export false fg getopts hash "
    "jobs kill local printf pwd read readonly return set shift test times trap true type ulimit "
    "umask unalias unset wait".split())

#: ``busybox --list`` of busybox-static 1:1.37.0-6+b9 -- the package the builder
#: installs (``apt-get install busybox-static``) -- measured on the testrunner
#: VM, 17/10/2026, 272 applets. build_initramfs.sh links every applet of the
#: build host's busybox, so this is the set unless the host's package moves.
#: Note what is NOT here: ``nice`` (packed from coreutils, #1493), ``lsblk``.
APPLETS = frozenset("""
    [ [[ acpid adjtimex ar arch arp arping ascii ash awk base64 basename bc blkdiscard blockdev
    brctl bunzip2 busybox bzcat bzip2 cal cat chgrp chmod chown chroot chvt clear cmp cp cpio crc32
    crond crontab cttyhack cut date dc dd deallocvt depmod devmem df diff dirname dmesg
    dnsdomainname dos2unix dpkg dpkg-deb du dumpkmap dumpleases echo ed egrep env expand expr
    factor fallocate false fatattr fdisk fgrep find findfs fold free freeramdisk fsfreeze fstrim
    ftpget ftpput getfattr getopt getty grep groups gunzip gzip halt head hexdump hostid hostname
    httpd hwclock i2cdetect i2cdump i2cget i2cset i2ctransfer id ifconfig ifdown ifup init insmod
    install ionice ip ipcalc kill killall klogd last less link linux32 linux64 linuxrc ln loadfont
    loadkmap logger login logname logread losetup ls lsmod lsscsi lzcat lzma lzop md5sum mdev
    microcom mim mkdir mkdosfs mke2fs mkfifo mknod mkpasswd mkswap mktemp modinfo modprobe more
    mount mt mv nameif nbd-client nc netstat nl nologin nproc nsenter nslookup nuke od openvt
    partprobe passwd paste patch pidof ping ping6 pivot_root poweroff printf ps pwd rdate readlink
    realpath reboot renice reset resume rev rm rmdir rmmod route rpm rpm2cpio run-init run-parts
    sed seq setkeycodes setpriv setsid sh sha1sum sha256sum sha3sum sha512sum shred shuf sleep sort
    ssl_client start-stop-daemon stat strings stty su sulogin svc svok swapoff swapon switch_root
    sync sysctl syslogd tac tail tar taskset tc tee telnet telnetd test tftp time timeout top touch
    tr traceroute traceroute6 true truncate ts tty tunctl ubirename udhcpc udhcpc6 udhcpd uevent
    umount uname uncompress unexpand uniq unix2dos unlink unlzma unshare unxz unzip uptime usleep
    uudecode uuencode vconfig vi w watch watchdog wc wget which who whoami xargs xxd xz xzcat yes
    zcat
""".split())

#: Packed by the builder outside BINARIES: compiled from agent/*.c, or the
#: native GUI image's own binaries (``--with-gui``).
COMPILED = frozenset({"fanout", "hivewrite", "lldpsniff", "imagesrv-monitor",
                      "imagesrv-station-gui", "imagesrv-kiosk"})


def packed_binaries() -> frozenset[str]:
    text = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    match = re.search(r"BINARIES=\((.*?)\)", text, flags=re.DOTALL)
    assert match, "BINARIES list not found in tools/build_initramfs.sh"
    return frozenset(match.group(1).split())


def toolbox_binaries() -> frozenset[str]:
    """What the toolbox catalog (toolbins.sh) may pull in per selected tool.

    Not always in the image: the builder packs ``tools_bins`` of only the ids
    the operator selected (#1050), and every ``tools_*.sh`` provider gates the
    tool on its own binary (``_tb_need`` / ``_tools_disk_need``). So a call to
    one of these from a toolbox provider is gated at run time by design."""
    text = (LIB / "toolbins.sh").read_text(encoding="utf-8")
    return frozenset(w for row in re.findall(r"printf '([^'\\]*)\\n'", text) for w in row.split())


#: A command word that is itself an expansion. Each reachable one is listed
#: here with why it is not a hole; a new one fails ``test_dynamic_sites``.
#: Key: (file, function).
DYNAMIC_SITES: dict[tuple[str, str], str] = {
    ("waits.sh", "capped_run"): "it IS the runner: it runs \"$@\"; its callers are resolved through RUNNERS",
    ("smart.sh", "smart_probe"): "capped_run's command is \"$SMARTCTL\", default `smartctl` (smart.sh:20; BINARIES); "
                                 "the variable exists so a test can put a stub there",
    ("hwissues.sh", "hw_refresh"): "\"$DMESG\", default `dmesg` (hwissues.sh; a busybox applet) -- "
                                   "the variable exists so a test can put a stub there (#1921)",
    ("hwissues.sh", "_hw_dmesg_t"): "the same \"$DMESG\" (dmesg), moved here by #1937: the time a drawer was identified at",
    ("tools.sh", "tools_list"): "provider dispatch tools_${domain}_list; reachable() roots every defined provider",
    ("tools.sh", "_tools_find"): "same dispatch, tools_${domain}_list",
    ("tools.sh", "tools_run"): "provider dispatch tools_${domain}_run; reachable() roots every defined provider",
}

#: Functions that run one of their own arguments as a command: name -> index of
#: that argument. ``capped_run SECONDS OUTFILE CMD ARGS..`` (waits.sh).
RUNNERS = {"capped_run": 2}


# ---------------------------------------------------------------------------
# The chain
# ---------------------------------------------------------------------------

@dataclass
class Chain:
    files: list[str]
    defs: dict[str, str]                       # function -> file
    calls: list[tuple[str, Call]]              # (file, call)
    dynamic: list[tuple[str, int, str, str]]   # (file, line, owner, word)
    guards: set[tuple[str, str]]


def kiosk_files(lib: Path = LIB) -> list[str]:
    """The files the kiosk process has: guibridge.sh itself, what ``gui_libs``
    sources, and what ``tools_load`` sources the first time it runs."""
    bridge = (lib / "guibridge.sh").read_text(encoding="utf-8")
    body = re.search(r"^gui_libs\(\) \{\n(.*?)^\}", bridge, flags=re.DOTALL | re.MULTILINE)
    assert body, "gui_libs() not found in guibridge.sh"
    loaded = re.findall(r'\. "\$LIB_DIR/([\w.]+)"', body.group(1))
    assert loaded, "gui_libs() sources nothing?"
    files = ["guibridge.sh", *loaded]
    tools = lib / "tools.sh"
    if "tools.sh" in loaded:
        text = tools.read_text(encoding="utf-8")
        assert "toolbins.sh" in text and "/tools_*.sh" in text, \
            "tools_load no longer pulls toolbins.sh + tools_*.sh -- update kiosk_files()"
        files += ["toolbins.sh", *sorted(p.name for p in lib_files(lib) if p.match("tools_*.sh"))]
    return list(dict.fromkeys(files))


def build_chain(lib: Path = LIB) -> Chain:
    files = kiosk_files(lib)
    chain = Chain(files, {}, [], [], set())
    for name in files:
        path = lib / name
        assert path.is_file(), f"gui_libs names {name}, which is not in {lib}"
        scan = scan_sh(path.read_text(encoding="utf-8"), runners=RUNNERS)
        for fn in scan.defs:
            chain.defs.setdefault(fn, name)
        chain.calls += [(name, c) for c in scan.calls]
        chain.dynamic += [(name, *d) for d in scan.dynamic]
        chain.guards |= scan.guards
    return chain


def reachable(chain: Chain) -> set[str]:
    """Functions the kiosk can run. ``""`` is file top level, which runs at
    source time. Toolbox providers are reached by name from tools_list /
    tools_run (``tools_${provider}_list|run``), which no static scan can follow."""
    edges: dict[str, set[str]] = defaultdict(set)
    for _, call in chain.calls:
        edges[call.owner].add(call.name)
    todo = deque(["", "gui_main", *(f for f in chain.defs if re.fullmatch(r"tools_[a-z0-9]+_(list|run)", f))])
    seen = set(todo)
    while todo:
        for callee in edges.get(todo.popleft(), ()):
            if callee in chain.defs and callee not in seen:
                seen.add(callee)
                todo.append(callee)
    return seen


def resolves(name: str, file: str, chain: Chain, packed: frozenset[str]) -> bool:
    if name in chain.defs or name in BUILTINS:
        return True
    base = name.rsplit("/", 1)[-1]
    if base in APPLETS or base in packed or base in COMPILED:
        return True
    return file.startswith("tools_") and base in toolbox_binaries()


def missing_for_kiosk(chain: Chain, packed: frozenset[str] | None = None) -> dict[str, list[str]]:
    """name -> where the kiosk calls it ("file:line in function")."""
    packed = packed_binaries() if packed is None else packed
    live = reachable(chain)
    out: dict[str, list[str]] = defaultdict(list)
    for file, call in chain.calls:
        if call.owner not in live or (call.owner, call.name) in chain.guards:
            continue
        if not resolves(call.name, file, chain, packed):
            out[call.name].append(f"{file}:{call.line} in {call.owner or '(top level)'}")
    return dict(out)


# ---------------------------------------------------------------------------
# The real kiosk
# ---------------------------------------------------------------------------

def _report(missing: dict[str, list[str]]) -> str:
    return "\n".join(f"  {name}: {', '.join(where[:3])}" for name, where in sorted(missing.items()))


def test_the_chain_is_the_real_gui_libs_list():
    chain = build_chain()
    assert {"guibridge.sh", "common.sh", "expand.sh", "diskfit.sh", "tools.sh"} <= set(chain.files)
    assert "gui_main" in chain.defs and "min_free_check" in chain.defs
    assert len(chain.defs) > 150, "the scan found suspiciously few functions"


@requires_native(("bash", BASH))
def test_the_scanner_finds_exactly_the_functions_bash_defines(tmp_path):
    """The scanner's view of "defined" against the shell's own: source the
    real chain (``gui_libs`` + ``tools_load``) and list what bash knows."""
    run = tmp_path / "run"
    run.mkdir()
    out = sh(f'export RUN_DIR={posix(run)!r} LIB_DIR={posix(LIB)!r} IMAGESRV_TEST=1; '
             f'. {posix(LIB)}/guibridge.sh; gui_libs; tools_load 2>/dev/null; '
             # usb_offline (usbclone.sh, #1914) only defines this boot's no-server stubs: the
             # scanner counts them, and bash knows them once it has run
             f'usb_offline; declare -F | while read -r _ _ n; do echo "$n"; done')
    assert out.returncode == 0, out.stderr
    in_bash = set(out.stdout.split())
    in_scan = set(build_chain().defs)
    assert in_scan == in_bash, (f"only scanned: {sorted(in_scan - in_bash)}; "
                                f"only in bash: {sorted(in_bash - in_scan)}")


def test_the_known_broken_set_is_exactly_what_is_tracked():
    """A ratchet that holds in both directions: a NEW unresolved call fails
    here even while an old one is still open, and a fixed one must leave
    ``KNOWN_BROKEN`` (so the list never goes stale)."""
    missing = missing_for_kiosk(build_chain())
    assert set(missing) == set(KNOWN_BROKEN), (
        f"reachable-but-undefined now: {sorted(missing)}; tracked: {sorted(KNOWN_BROKEN)}\n" + _report(missing))


def test_dynamic_sites():
    """What the scan cannot resolve, said out loud: a reachable command word
    that is an expansion must be one we have looked at."""
    chain = build_chain()
    live = reachable(chain)
    unlisted = sorted({(f, o) for f, _, o, _ in chain.dynamic if o in live} - set(DYNAMIC_SITES))
    assert not unlisted, f"unreviewed dynamic command words (file, function): {unlisted}"
    stale = sorted(set(DYNAMIC_SITES) - {(f, o) for f, _, o, _ in chain.dynamic})
    assert not stale, f"DYNAMIC_SITES entries that no longer exist: {stale}"


def test_every_command_the_kiosk_can_reach_exists_in_the_kiosk_process():
    missing = missing_for_kiosk(build_chain())
    assert not missing, "the kiosk reaches commands its process does not have:\n" + _report(missing)


# ---------------------------------------------------------------------------
# The detector itself, on a chain we control
# ---------------------------------------------------------------------------

def _fake_lib(tmp_path: Path, files: dict[str, str]) -> Path:
    lib = tmp_path / "lib"
    lib.mkdir(parents=True)
    for name, text in files.items():
        (lib / name).write_text(text, encoding="utf-8", newline="\n")
    return lib


KIOSK = ('gui_libs() {\n    . "$LIB_DIR/a.sh"\n}\n'
         'gui_main() {\n    gui_libs\n    work\n}\n')


def test_a_function_in_an_unloaded_file_is_reported_with_its_caller(tmp_path):
    lib = _fake_lib(tmp_path, {
        "guibridge.sh": KIOSK,
        "a.sh": "work() {\n    helper_in_b \"$1\" | cat\n}\n",
        "b.sh": "helper_in_b() { :; }\n",       # exists in the repo, not in the chain
    })
    missing = missing_for_kiosk(build_chain(lib), frozenset())
    assert missing == {"helper_in_b": ["a.sh:2 in work"]}


def test_the_same_call_loaded_is_not_reported(tmp_path):
    # both states: the detector does not cry wolf when the file IS loaded
    lib = _fake_lib(tmp_path, {
        "guibridge.sh": KIOSK.replace('    . "$LIB_DIR/a.sh"\n', '    . "$LIB_DIR/a.sh"; . "$LIB_DIR/b.sh"\n'),
        "a.sh": "work() {\n    helper_in_b \"$1\" | cat\n}\n",
        "b.sh": "helper_in_b() { :; }\n",
    })
    assert missing_for_kiosk(build_chain(lib), frozenset()) == {}


def test_a_call_only_an_agent_side_function_makes_is_not_the_kiosks_bug(tmp_path):
    lib = _fake_lib(tmp_path, {
        "guibridge.sh": KIOSK,
        "a.sh": "work() { :; }\nagent_only() {\n    helper_in_b\n}\n",
    })
    assert missing_for_kiosk(build_chain(lib), frozenset()) == {}


def test_a_call_behind_trap_command_substitution_and_a_runner_is_still_a_call(tmp_path):
    lib = _fake_lib(tmp_path, {
        "guibridge.sh": KIOSK,
        "a.sh": ("work() {\n    trap lost_cleanup EXIT\n    x=$(lost_sub)\n"
                 "    capped_run 5 /dev/null lost_run -a\n}\n"
                 "capped_run() { :; }\n"),
    })
    assert set(missing_for_kiosk(build_chain(lib), frozenset())) == {"lost_cleanup", "lost_sub", "lost_run"}


def test_a_command_v_guard_in_the_same_function_is_by_design(tmp_path):
    lib = _fake_lib(tmp_path, {
        "guibridge.sh": KIOSK,
        "a.sh": ("work() {\n    if command -v optional_beat >/dev/null 2>&1; then optional_beat; fi\n}\n"
                 "other() {\n    optional_beat\n}\n"),
    })
    assert missing_for_kiosk(build_chain(lib), frozenset()) == {}
    lib2 = _fake_lib(tmp_path / "x", {
        "guibridge.sh": KIOSK,
        "a.sh": "work() {\n    command -v something_else >/dev/null\n    optional_beat\n}\n",
    })
    assert set(missing_for_kiosk(build_chain(lib2), frozenset())) == {"optional_beat"}


def test_a_binary_the_builder_does_not_pack_is_reported(tmp_path):
    lib = _fake_lib(tmp_path, {"guibridge.sh": KIOSK, "a.sh": "work() {\n    curl -s x | zstd -d | lsblk\n}\n"})
    assert set(missing_for_kiosk(build_chain(lib), frozenset({"curl"}))) == {"zstd", "lsblk"}
