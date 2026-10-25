"""#2108: a bridge that leaves before reading its input must not kill the installer.

``run_bridge`` (``native-gui/src/install.c``) writes the JSON to the bridge's
stdin. When the bridge has already closed it (jq missing, a syntax error, an
``exit`` on the first line), the write raised SIGPIPE and the whole installer
screen died: ``returncode=-13`` in the full gate, 24/10. ``main.c`` did not
ignore SIGPIPE as ``dcui.c`` does.

Deterministic, not a race: the input is four times a Linux pipe buffer
(64 KiB), so the write *must* block on a full pipe and then meet the closed
end, whatever the scheduling. The bridge exits 0 -- the input that never
reached it is what says it failed (principle 5).

The driver includes ``install.c`` (as ``test_native_installer_keyboard.py``
does) and calls ``call()``, the path every bridge action takes to the screen.
It does not touch SIGPIPE, except in the "gui" mode of the last test, which
copies main.c to check that the bridge does not inherit the GUI's choice.
"""

from __future__ import annotations

import shutil
import signal
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
BIG = 256 * 1024
SIGPIPE_BIT = 1 << (signal.SIGPIPE - 1)


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo", _pkgconfig("cairo")),
    posix=True, why="install.c is built and run on Linux only",
)

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }

static App app;

/* argv: BRIDGE-CMD BYTES [gui] -- one "validate" call carrying BYTES of JSON
 * padding; "gui" ignores SIGPIPE first, as main.c does */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    if (argc > 3 && !strcmp(argv[3], "gui")) signal(SIGPIPE, SIG_IGN);
    InstallBridge b = { argv[1], NULL, 0 };
    size_t n = strtoul(argv[2], NULL, 10);
    char *json = malloc(n + 16);
    if (!json) return 2;
    memcpy(json, "{\"pad\":\"", 8); memset(json + 8, 'x', n); memcpy(json + 8 + n, "\"}", 3);
    int rc = call(&app, &b, "validate", json);
    free(json);
    printf("rc=%%d\nerror=%%s\n", rc, app.install.error);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("bridge-sigpipe")
    source = tmp / "bridge_sigpipe.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "bridge_sigpipe"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _call(driver: Path, bridge: str, size: int, *mode: str) -> dict:
    proc = subprocess.run([str(driver), bridge, str(size), *mode], capture_output=True,
                          encoding="utf-8", timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode != -signal.SIGPIPE, "SIGPIPE killed the installer: the bridge left before reading"
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines())


@NATIVE
def test_a_bridge_that_closed_its_input_is_a_failed_bridge_not_a_dead_installer(driver, tmp_path):
    log = tmp_path / "bridge.log"
    out = _call(driver, f'exec <&-; sleep 1; echo exited-0 >"{log}"; :', BIG)
    assert log.read_text().split() == ["exited-0"], "the bridge closed its input, stayed, and exited 0"
    assert out == {"rc": "-1", "error": "פעולת הגשר נכשלה: validate"}, \
        "input that never reached the bridge was read as a bridge that succeeded"


@NATIVE
def test_a_bridge_that_reads_the_same_input_succeeds(driver):
    """The guard: the size alone is not the failure."""
    out = _call(driver, "cat >/dev/null; :", BIG)
    assert out == {"rc": "0", "error": ""}


@NATIVE
def test_the_bridge_does_not_inherit_the_gui_ignoring_sigpipe(driver, tmp_path):
    """main.c ignores SIGPIPE; an ignored signal survives exec, and a bridge
    pipeline (`... | head -1`) would then see write errors it never saw."""
    log = tmp_path / "sigign"
    out = _call(driver, f'cat >/dev/null; grep SigIgn /proc/$$/status >"{log}"; :', 10, "gui")
    assert out["rc"] == "0", out
    mask = int(log.read_text().split()[1], 16)
    assert not mask & SIGPIPE_BIT, f"the bridge started with SIGPIPE ignored (SigIgn {mask:#x})"


def test_main_ignores_sigpipe_before_the_first_bridge_call():
    """install_start makes the first bridge call ("inventory"); the old
    signal() line sat after it, next to SIGINT/SIGTERM."""
    main = (SRC / "main.c").read_text(encoding="utf-8")
    assert "signal(SIGPIPE, SIG_IGN)" in main, "main.c does not ignore SIGPIPE (dcui.c does)"
    assert main.index("signal(SIGPIPE, SIG_IGN)") < main.index("install_start(&a, &wizard, demo)")
