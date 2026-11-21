"""#2984: the build machine's direct send names its source disk the way the operator knows it, and says "מחיצה N" without
the internal "(בלוקים)". The routing of a direct send without drawers is pinned in test_build_direct_drawers_2849.py."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_agent import BASH, posix
from test_cloner_gui import GUI, _pkgconfig

DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "lcui.h"
#include "state.h"
#include "screens_rounds.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
int main(int argc, char **argv) {
    static App a; a.theme = &THEME_LIGHT;
    FILE *f = fopen(argv[1], "r"); if (!f) return 2;
    state_parse(&a.st, f); fclose(f);
    char name[160], label[96];
    direct_src_name(&a.st, name, sizeof name);
    progress_label(label, sizeof label, 5, 1, 1);
    printf("@name=%s\n@label=%s\n", name, label);
    return 0;
}
'''
DISK = "disk=sda|SAMSUNG MZ7TE256HMHP|256060514304|1|0\n"


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("builddirectprogress")
    (d / "driver.c").write_text(DRIVER, newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [str(GUI / s) for s in srcs if not s.endswith("main.c")]
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def run(driver: Path, tmp_path: Path, state: str) -> dict[str, str]:
    path = tmp_path / "state.txt"; path.write_text(state, encoding="utf-8", newline="\n")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"))
    out = subprocess.run([str(driver), posix(path)], capture_output=True, text=True, encoding="utf-8", timeout=60,
                         env=env, stdin=subprocess.DEVNULL)
    assert out.returncode == 0, out.stderr
    return dict(l[1:].split("=", 1) for l in out.stdout.splitlines() if l.startswith("@"))


NATIVE = requires_native(("cc", shutil.which("cc") or shutil.which("gcc")),
                         ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")), ("bash", BASH),
                         why="native-gui נבנה על המעבדה בלבד")


@NATIVE
def test_the_source_is_its_drawer_and_model_not_sda(driver, tmp_path):
    assert run(driver, tmp_path, DISK + "disk_slot=sda|1|sata:0\ntask_disk=sda\n")["name"] == "דיסק 1 · SAMSUNG MZ7TE256HMHP"
    # the agent may write the device with its path
    assert run(driver, tmp_path, DISK + "disk_slot=sda|1|sata:0\ntask_disk=/dev/sda\n")["name"] == "דיסק 1 · SAMSUNG MZ7TE256HMHP"


@NATIVE
def test_a_source_without_a_drawer_is_model_and_size_and_an_unlisted_one_its_device(driver, tmp_path):
    assert run(driver, tmp_path, DISK + "task_disk=sda\n")["name"] == "SAMSUNG MZ7TE256HMHP · 238 GB"
    assert run(driver, tmp_path, "task_disk=sdz\n")["name"] == "sdz"


@NATIVE
def test_the_partition_is_not_followed_by_the_internal_word_blocks(driver, tmp_path):
    label = run(driver, tmp_path, "task_disk=sda\n")["label"]
    assert label == "5% · מחיצה 1" and "בלוקים" not in label
