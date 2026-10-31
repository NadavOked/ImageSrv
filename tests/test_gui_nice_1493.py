"""#1493 -- when the CPU is full, the data path wins over the screen.

M1 (busybox top, cloner 2, i5-650 2C/4T, local clone 1->1 at 196MB/s,
Nadav 16/10): 92% busy, 8% idle, and no priority anywhere in the agent. The
native GUI is a redraw loop; partclone/zstd/fanout/udp-* are the copy. So the
GUI starts under `nice -n 10` and the data path keeps the default.

Debian 13's busybox-static (1:1.37.0-6+b9, lab server 16/10) has no `nice`
applet, so coreutils' nice is packed through BINARIES and called by absolute
path (busybox sh would prefer an applet over PATH). The build stops when
`$ROOT/usr/bin/nice` is not an executable (the --with-gui block), tested in
test_initramfs_gui.py (`fault="nice"`).
"""

from __future__ import annotations

import re
from pathlib import Path

AGENT = Path(__file__).resolve().parent.parent / "agent"
GUIBRIDGE = AGENT / "lib" / "gui" / "guibridge.sh"

#: The data path. None of these may ever be started under nice.
DATA_PATH = ("partclone", "fanout", "zstd", "udp-sender", "udp-receiver",
             "direct_udp_send", "stream_source", "curl")


def _code_lines(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


def test_the_native_gui_starts_under_nice_10():
    starts = [line for line in _code_lines(GUIBRIDGE)
              if "/usr/bin/imagesrv-station-gui" in line and "--help" not in line]
    assert len(starts) == 1, starts
    assert starts[0].strip().startswith("/usr/bin/nice -n 10 /usr/bin/imagesrv-station-gui "), starts[0]


def test_the_nice_the_gui_starts_under_is_packed_from_coreutils():
    """The absolute path in guibridge.sh must exist in the image: BINARIES
    copies /usr/bin/nice (and its libraries) and stops the build without it."""
    builder = (AGENT.parent / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    binaries = re.search(r"BINARIES=\((.*?)\)", builder, flags=re.DOTALL).group(1).split()
    assert "nice" in binaries, binaries


def test_no_stage_of_the_data_path_is_niced():
    """The other half: a `nice` that slipped onto a copy stage would slow
    exactly what this change is meant to protect."""
    offenders = []
    for path in sorted(AGENT.rglob("*")):
        if not path.is_file() or path.suffix not in ("", ".sh") or path.name.endswith(".c"):
            continue
        try:
            lines = _code_lines(path)
        except UnicodeDecodeError:
            continue
        for line in lines:
            if re.search(r"(^|[\s;|&({/])(nice|ionice|renice)\s", line) and \
                    any(stage in line for stage in DATA_PATH):
                offenders.append(f"{path.name}: {line.strip()}")
    assert offenders == [], offenders
