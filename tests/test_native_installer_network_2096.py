"""#2096 (ISO rc1, Hyper-V 24/10): the installer's network screen, fixed static.

Measured on the screen (C:\\ImageCtl-Lab\\evening-1015\\iso-rc1-1024\\hv-04*, hv-05*):

* a server with one NIC arrived with no NIC selected;
* "the selected NIC is invalid" was on the screen before anything was done
  (the step-1 dry run validates the whole form, the interface was empty), and
  stayed after a NIC was chosen;
* walking up from the static fields to the NIC row went through "DHCP" and
  switched the mode (landing on an option selects it, #1206).

The driver includes ``install.c`` and runs the real ``install_start`` against a
fake bridge (a shell script), then feeds keys to ``install_handle``.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"
NETWORK = 3


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

static const char *focus_name(const App *a) {
    static char buf[32];
    switch (a->focus) {
    case HIT_INSTALL_DHCP: return "dhcp";
    case HIT_INSTALL_STATIC: return "static";
    case HIT_INSTALL_ADDRESS: return "address";
    case HIT_INSTALL_DNS: return "dns";
    }
    if (a->focus >= HIT_INSTALL_NIC_BASE && a->focus < HIT_INSTALL_NIC_BASE + INSTALL_MAX_NICS) {
        snprintf(buf, sizeof buf, "nic%%d", a->focus - HIT_INSTALL_NIC_BASE);
        return buf;
    }
    snprintf(buf, sizeof buf, "id%%d", a->focus);
    return buf;
}

static void show(const App *a, const char *tok) {
    const InstallState *s = &a->install;
    printf("%%s view=%%d focus=%%s nic=%%d static=%%d nic_error=%%d dns_error=%%d address_len=%%zu\n", tok,
           s->view, focus_name(a), s->nic, s->static_mode, s->field_error[3][0] != 0,
           s->field_error[8][0] != 0, strlen(s->address));
}

static App app;

/* argv: BRIDGE-CMD TOKEN... -- install_start through the bridge, then one
 * line of state after every token */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { fprintf(stderr, "install_start failed\n"); return 3; }
    refocus(a);
    show(a, "start");
    for (int i = 2; i < argc; i++) {
        const char *t = argv[i];
        Event e = { 0 };
        if (!strcmp(t, "nic-error")) { snprintf(a->install.field_error[3], 256, "x"); show(a, t); continue; }
        if (!strcmp(t, "dns-error")) { snprintf(a->install.field_error[8], 256, "x"); show(a, t); continue; }
        if (!strcmp(t, "focus-dns")) { a->focus = HIT_INSTALL_DNS; show(a, t); continue; }
        if (!strcmp(t, "CHAR1")) { e.type = UIEV_CHAR; e.ch = '1'; install_handle(a, &b, &e); show(a, t); continue; }
        if (!strcmp(t, "SPACE")) { e.type = UIEV_CHAR; e.ch = ' '; }
        else {
            e.type = UIEV_KEY;
            e.key = !strcmp(t, "UP") ? KEYSYM_UP : !strcmp(t, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(t, "ENTER") ? KEYSYM_ENTER : !strcmp(t, "BKSP") ? KEYSYM_BACKSPACE : 0;
            if (!e.key) { fprintf(stderr, "unknown token %%s\n", t); return 2; }
        }
        install_handle(a, &b, &e);
        show(a, t);
    }
    return 0;
}
'''

# The bridge the GUI talks to: one Hyper-V NIC with no lease (no interface=
# chosen by DHCP), and a step-1/step-2 dry run that -- like the real engine,
# which validates the whole form -- names the still-empty interface.
BRIDGE = r'''#!/bin/sh
input=$(cat)
case "$1" in
inventory)
    echo 'disk=/dev/sda|model=Virtual Disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0'
    echo 'nic=eth0|Hyper-V|00:15:5d:44:3c:09|מחובר|—|אין כתובת (אין DHCP ברשת הזו)||||'
    echo 'interface='; echo 'mode=dhcp'; echo 'hostname=imagesrv-server'; echo 'admin_user=admin' ;;
progress) echo 'state=ready' ;;
validate)
    case "$input" in *'"interface":""'*) echo 'errors.interface=כרטיס הרשת שנבחר אינו תקין' ;; esac ;;
esac
exit 0
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-net")
    source = tmp / "install_net.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_net"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _run(driver: Path, tmp_path: Path, *tokens: str) -> list[dict]:
    bridge = tmp_path / "bridge.sh"
    bridge.write_text(BRIDGE, encoding="utf-8", newline="\n")
    bridge.chmod(0o755)
    proc = subprocess.run([str(driver), str(bridge), *tokens], capture_output=True, text=True,
                          timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        tok, *pairs = line.split()
        states.append({"key": tok, **dict(pair.split("=", 1) for pair in pairs)})
    assert [s["key"] for s in states] == ["start", *tokens], proc.stdout
    return states


# Disk screen -> Space ("I understand"), Enter; role screen -> Enter; network.
TO_NETWORK = ("SPACE", "ENTER", "ENTER")


@NATIVE
def test_the_only_nic_is_selected_when_the_installer_starts(driver, tmp_path):
    states = _run(driver, tmp_path)
    assert states[0]["nic"] == "0"


@NATIVE
def test_the_network_screen_opens_without_an_earlier_steps_nic_error(driver, tmp_path):
    """The step-1 and step-2 dry runs both said "invalid NIC" (the form had
    no interface then); the network screen showed it before any key."""
    states = _run(driver, tmp_path, *TO_NETWORK)
    assert states[-1]["view"] == str(NETWORK)
    assert states[-1]["nic_error"] == "0"


@NATIVE
def test_choosing_the_nic_clears_its_error(driver, tmp_path):
    states = _run(driver, tmp_path, *TO_NETWORK, "DOWN", "nic-error", "DOWN")   # nic0 -> mode -> nic0
    assert states[-2]["nic_error"] == "1"
    assert states[-1]["focus"] == "nic0" and states[-1]["nic_error"] == "0"


@NATIVE
def test_editing_a_field_clears_that_fields_error(driver, tmp_path):
    states = _run(driver, tmp_path, *TO_NETWORK, "SPACE", "dns-error", "focus-dns", "CHAR1")
    assert states[-2]["dns_error"] == "1"
    assert states[-1]["dns_error"] == "0"


@NATIVE
def test_walking_up_from_the_static_fields_to_the_nic_keeps_static(driver, tmp_path):
    """Space -> static, focus on the IP; Up, Up reaches the NIC row. It used
    to land on "DHCP" on the way and switch the mode."""
    states = _run(driver, tmp_path, *TO_NETWORK, "SPACE", "UP", "UP")
    assert [s["focus"] for s in states[-3:]] == ["address", "static", "nic0"]
    assert [s["static"] for s in states[-3:]] == ["1", "1", "1"]


@NATIVE
def test_static_on_a_nic_without_an_address_starts_with_an_empty_ip_field(driver, tmp_path):
    """The auto-picked NIC seeds the static fields; with no lease its "current
    address" is the "—" placeholder, which landed in the IP field (Hyper-V
    run of the fix, 24/10). Only a real address is a seed."""
    states = _run(driver, tmp_path, *TO_NETWORK, "SPACE")
    assert states[-1]["static"] == "1" and states[-1]["address_len"] == "0"
