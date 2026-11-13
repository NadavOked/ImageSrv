"""#2826: on the installer's "כתובת השרת" screen, Tab in the NIC list moved
the *choice* (not only the focus), and Space typed a space into the IP field.

Seen in both ESXi installs of v0.91.10 (10/11, lab/release-tests/rel0910):
eth1 got chosen on the way to the fields instead of the management NIC. The
NIC chosen here is the management NIC -- the console comes up on it.

Now Tab walks the stops with the NIC list as one stop (the chosen row) and
chooses nothing by landing; the arrows still walk every row and choose by
landing (#1206), and Space keeps flipping DHCP/static. A space is refused in
the address, mask, gateway and DNS fields.

The driver includes ``install.c`` and runs the real ``install_start`` against
a fake bridge (a shell script), then feeds keys to ``install_handle``.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from test_native_installer_network_2096 import NATIVE, SRC

NETWORK = 3

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
    case HIT_INSTALL_NETMASK: return "netmask";
    case HIT_INSTALL_GATEWAY: return "gateway";
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
    printf("%%s view=%%d focus=%%s nic=%%d static=%%d address=[%%s] dns=[%%s]\n", tok, s->view,
           focus_name(a), s->nic, s->static_mode, s->address, s->dns);
}

static App app;

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
        if (!strncmp(t, "CHAR", 4)) { e.type = UIEV_CHAR; e.ch = (unsigned char)t[4]; install_handle(a, &b, &e); show(a, t); continue; }
        if (!strcmp(t, "SPACE")) { e.type = UIEV_CHAR; e.ch = ' '; }
        else {
            e.type = UIEV_KEY;
            e.key = !strcmp(t, "UP") ? KEYSYM_UP : !strcmp(t, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(t, "TAB") ? KEYSYM_TAB : !strcmp(t, "BACKTAB") ? KEYSYM_BACKTAB
                  : !strcmp(t, "ENTER") ? KEYSYM_ENTER : !strcmp(t, "BKSP") ? KEYSYM_BACKSPACE : 0;
            if (!e.key) { fprintf(stderr, "unknown token %%s\n", t); return 2; }
        }
        install_handle(a, &b, &e);
        show(a, t);
    }
    return 0;
}
'''

# Three NICs; the management NIC (eth0) has the lease and is chosen by the bridge.
BRIDGE = r'''#!/bin/sh
input=$(cat)
case "$1" in
inventory)
    echo 'disk=/dev/sda|model=Virtual Disk|size=64424509440|bus=scsi|removable=0|has=empty|iso=0'
    echo 'nic=eth0|VMXNET3|00:50:56:01:00:01|מחובר|10.44.10.37/24|DHCP · שער 10.44.10.254|10.44.10.37|255.255.255.0|10.44.10.254|10.44.10.2'
    echo 'nic=eth1|VMXNET3|00:50:56:01:00:02|מחובר|—|אין כתובת||||'
    echo 'nic=eth2|VMXNET3|00:50:56:01:00:03|לא מחובר|—|—||||'
    echo 'interface=eth0'; echo 'mode=dhcp'; echo 'hostname=imagesrv-server'; echo 'admin_user=admin' ;;
progress) echo 'state=ready' ;;
esac
exit 0
'''

TO_NETWORK = ("SPACE", "ENTER", "ENTER")   # disk: "I understand", next; role: next


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-nic-tab")
    source = tmp / "install_nic_tab.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_nic_tab"
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
        tok = line.split(" ", 1)[0]
        pairs = {k: v.strip("[]") for k, v in re.findall(r"(\w+)=(\[[^\]]*\]|\S+)", line)}
        states.append({"key": tok, **pairs})
    assert [s["key"] for s in states] == ["start", *tokens], proc.stdout
    return states


@NATIVE
def test_tab_through_the_network_screen_never_changes_the_chosen_nic(driver, tmp_path):
    states = _run(driver, tmp_path, *TO_NETWORK, "TAB", "TAB", "BACKTAB", "BACKTAB", "BACKTAB")
    net = states[len(TO_NETWORK):]
    assert net[0]["view"] == str(NETWORK) and net[0]["focus"] == "nic0" and net[0]["nic"] == "0"
    assert [s["nic"] for s in net] == ["0"] * len(net), [(s["key"], s["focus"], s["nic"]) for s in net]
    # one stop for the whole list: Tab from the list goes to the mode, not to eth1
    assert net[1]["focus"] == "dhcp"


@NATIVE
def test_tab_to_the_static_fields_keeps_the_nic_and_the_typed_address(driver, tmp_path):
    states = _run(driver, tmp_path, *TO_NETWORK, "SPACE", "BKSP", "CHAR9", "BACKTAB", "TAB")
    assert states[-5]["static"] == "1" and states[-5]["focus"] == "address"
    assert [s["nic"] for s in states[-5:]] == ["0"] * 5
    assert states[-1]["focus"] == "address" and states[-1]["address"] == states[-3]["address"]


@NATIVE
def test_the_arrows_still_choose_a_nic_by_landing(driver, tmp_path):
    """#1206's walk is kept: Down from eth0 lands on eth1 and chooses it."""
    states = _run(driver, tmp_path, *TO_NETWORK, "DOWN")
    assert states[-1]["focus"] == "nic1" and states[-1]["nic"] == "1"


@NATIVE
def test_space_is_not_typed_into_the_ip_fields(driver, tmp_path):
    states = _run(driver, tmp_path, *TO_NETWORK, "SPACE", "BKSP", "SPACE", "CHAR1", "SPACE")
    assert states[-5]["focus"] == "address" and states[-5]["static"] == "1"
    assert " " not in states[-1]["address"], states[-1]
    assert states[-1]["address"].endswith("1")
