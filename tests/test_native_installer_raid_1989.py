"""#1989: the RAID choice on the installer's disk step (native GUI).

The driver #includes install.c (as test_native_installer_keyboard.py does)
and feeds it bridge output and keys; after every token it prints the RAID
state and the JSON the GUI would send to the bridge. cc + cairo = Linux only.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs], stdin=subprocess.DEVNULL).returncode == 0


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

static void show(const char *tok) {
    char json[4096]; config_json(&app, json, sizeof json, 1);
    printf("%%s raid=%%d virt=%%d disk=%%d ack=%%d json=%%s\n", tok, app.install.raid, app.install.virt,
           app.install.disk, app.install.disk_ack, json);
}

/* argv: INVENTORY-FILE TOKEN... -- the inventory text goes through
 * parse_lines exactly as the bridge's stdout would. */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    App *a = &app; InstallState *s = &a->install;
    memset(s, 0, sizeof *s); s->active = 1; s->view = INSTALL_DISK; s->disk = s->nic = s->existing_disk = -1;
    static char text[8192]; FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    size_t n = fread(text, 1, sizeof text - 1, f); text[n] = 0; fclose(f);
    parse_lines(s, text, "inventory");
    InstallBridge b = { "true", NULL, 0 };
    show("start");
    for (int i = 2; i < argc; i++) {
        const char *t = argv[i]; Event e = { 0 }; e.type = UIEV_KEY;
        if (!strncmp(t, "click", 5)) { choose(a, &b, HIT_INSTALL_RAID_BASE + atoi(t + 5)); show(t); continue; }
        if (!strncmp(t, "disk", 4)) { choose(a, &b, HIT_INSTALL_DISK_BASE + atoi(t + 4)); show(t); continue; }
        if (!strcmp(t, "SPACE")) { e.type = UIEV_CHAR; e.ch = ' '; }
        else e.key = !strcmp(t, "LEFT") ? KEYSYM_LEFT : !strcmp(t, "RIGHT") ? KEYSYM_RIGHT
                   : !strcmp(t, "DOWN") ? KEYSYM_DOWN : !strcmp(t, "UP") ? KEYSYM_UP : 0;
        install_handle(a, &b, &e);
        show(t);
    }
    return 0;
}
'''

NONE, R1, R1S, R5, R6, R10 = range(6)


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-raid")
    source = tmp / "install_raid.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_raid"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _disks(*specs: str) -> str:
    """spec = name[:usb|:rm|:iso]"""
    lines = []
    for spec in specs:
        name, _, flag = spec.partition(":")
        bus = "usb" if flag == "usb" else "sata"
        lines.append(f"disk=/dev/{name}|model=M {name}|size=500107862016|bus={bus}|"
                     f"removable={1 if flag == 'rm' else 0}|has=empty|iso={1 if flag == 'iso' else 0}")
    return "\n".join(lines) + "\n"


def _run(driver: Path, tmp_path: Path, inventory: str, *tokens: str) -> list[dict]:
    inv = tmp_path / "inventory"
    inv.write_text(inventory, encoding="utf-8", newline="\n")
    proc = subprocess.run([str(driver), str(inv), *tokens], capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        head, _, js = line.partition(" json=")
        tok, *pairs = head.split()
        state = {k: int(v) for k, v in (p.split("=", 1) for p in pairs)}
        state["key"], state["json"] = tok, json.loads(js)
        states.append(state)
    assert [s["key"] for s in states] == ["start", *tokens], proc.stdout
    return states


@NATIVE
@pytest.mark.parametrize(("disks", "default", "level", "members"), [
    (("sda", "sdb"), "raid1", R1, "/dev/sda,/dev/sdb"),
    (("sda", "sdb", "sdc"), "raid5", R5, "/dev/sda,/dev/sdb,/dev/sdc"),
    (("sda", "sdb", "sdc", "sdd"), "raid10", R10, "/dev/sda,/dev/sdb,/dev/sdc,/dev/sdd"),
])
def test_the_bridge_default_reaches_the_answers_with_every_suitable_disk(driver, tmp_path, disks, default, level, members):
    inv = _disks(*disks, "sr0:iso", "sdx:usb") + f"virt=metal\nraid_default={default}\n"
    start, chosen = _run(driver, tmp_path, inv, "disk0")
    assert start["raid"] == level
    # #2147: the default level is offered; the disks are not chosen for the person
    assert start["disk"] == -1 and start["json"]["disk"] == "" and start["json"]["raid_disks"] == ""
    assert chosen["json"]["raid_level"] == default and chosen["json"]["raid_disks"] == members
    assert chosen["json"]["disk"] == "/dev/sda" and chosen["json"]["raid_spares"] == "0"


@NATIVE
def test_a_virtual_server_gets_no_raid_and_the_arrows_change_nothing(driver, tmp_path):
    inv = _disks("sda", "sdb", "sdc") + "virt=virtual\nraid_default=raid5\n"
    states = _run(driver, tmp_path, inv, "LEFT", "RIGHT", "click3")
    assert [s["raid"] for s in states] == [NONE] * 4
    assert states[-1]["json"]["raid_level"] == "none" and states[-1]["json"]["raid_disks"] == ""


@NATIVE
def test_the_arrows_walk_only_the_levels_four_disks_allow_and_reset_the_ack(driver, tmp_path):
    inv = _disks("sda", "sdb", "sdc", "sdd") + "virt=metal\nraid_default=raid10\n"
    states = _run(driver, tmp_path, inv, "disk0", "SPACE", "LEFT", "LEFT", "LEFT", "LEFT", "LEFT", "LEFT")[1:]
    assert [s["raid"] for s in states] == [R10, R10, NONE, R1, R1S, R5, R6, R10]
    assert states[1]["ack"] == 1 and states[2]["ack"] == 0   # a new choice is a new "I understand"
    spare = states[4]["json"]
    assert spare["raid_level"] == "raid1" and spare["raid_spares"] == "1"


@NATIVE
def test_two_disks_offer_only_none_and_raid1(driver, tmp_path):
    inv = _disks("sda", "sdb") + "virt=metal\nraid_default=raid1\n"
    states = _run(driver, tmp_path, inv, "RIGHT", "RIGHT", "click5", "click3")
    assert [s["raid"] for s in states] == [R1, NONE, R1, R1, R1]


@NATIVE
def test_an_unknown_server_starts_without_raid_but_may_choose_it(driver, tmp_path):
    inv = _disks("sda", "sdb", "sdc") + "virt=unknown\nraid_default=none\n"
    states = _run(driver, tmp_path, inv, "click3", "disk0")
    assert states[0]["raid"] == NONE and states[1]["raid"] == R5
    assert states[1]["disk"] == -1, "#2147: choosing a level is not choosing the disk"
    assert states[2]["json"]["raid_disks"] == "/dev/sda,/dev/sdb,/dev/sdc"


@NATIVE
def test_the_chosen_disk_is_first_and_a_removable_one_leaves_raid(driver, tmp_path):
    inv = _disks("sda", "sdb", "sdc:rm") + "virt=metal\nraid_default=raid1\n"
    states = _run(driver, tmp_path, inv, "disk1", "disk2")
    assert states[1]["json"]["raid_disks"] == "/dev/sdb,/dev/sda"
    assert states[2]["raid"] == NONE and states[2]["json"]["disk"] == "/dev/sdc"
    assert states[2]["json"]["raid_disks"] == ""
