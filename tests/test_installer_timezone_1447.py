"""#1447 (Nadav 17/10): the server installer asks the timezone; default
Asia/Jerusalem.

The chain, each link tested here:

* the native GUI -- a field under the server name (the same text field),
  ``Asia/Jerusalem`` until changed, sent as ``timezone`` in every form JSON;
  a field error on it keeps the person on that step;
* ``gui-bridge.sh`` -- ``timezone=`` in the answers file, ``Asia/Jerusalem``
  from the inventory, and the engine's ``invalid-timezone`` on that field;
* the engine (``answers.sh``) -- a required key, checked against
  ``installer/lib/zones.txt`` before any disk command (a typo is a field
  error, not a failed install after partitioning); ``finish.sh`` puts the
  chosen zone into the target and its answers file;
* firstboot -> ``setup-boot-server.sh --timezone`` -> the server's setting,
  which the server's start would otherwise overwrite with the default.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_installer_engine import LINUX_ONLY, _answers, _command_shims, _machine, _run
from test_installer_gui_bridge import FORM, bridge
from test_installer_gui_bridge import pytestmark as BRIDGE_ONLY   # sh + jq, not Windows
from test_installer_gui_bridge import _machine as _bridge_machine

REPO = Path(__file__).resolve().parent.parent
ZONES = REPO / "installer" / "lib" / "zones.txt"
SRC = REPO / "native-gui" / "src"
SYSTEM_ZONEINFO = Path("/usr/share/zoneinfo")


def _zone_names() -> list[str]:
    return [line for line in ZONES.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#")]


# --- the zone list --------------------------------------------------------------


def test_the_list_holds_the_default_and_utc_once_each_and_sorted():
    names = _zone_names()
    assert "Asia/Jerusalem" in names and "UTC" in names
    assert names == sorted(set(names)), "sorted, no duplicates"
    assert len(names) > 300, "tzdata's zone.tab, not a hand-picked few"


@pytest.mark.skipif(not (SYSTEM_ZONEINFO / "Asia" / "Jerusalem").is_file(),
                    reason="a real zoneinfo is required (Linux/CI)")
def test_every_listed_zone_is_a_real_tzif_file():
    bad = [n for n in _zone_names()
           if not (SYSTEM_ZONEINFO / n).is_file() or not (SYSTEM_ZONEINFO / n).read_bytes().startswith(b"TZif")]
    assert not bad, bad


def test_the_initramfs_carries_the_list():
    text = (REPO / "tools" / "build_initramfs.sh").read_text(encoding="utf-8")
    assert '"$INSTALLER_SRC/lib/zones.txt"' in text and "installer/lib/zones.txt" in text


# --- the engine -----------------------------------------------------------------


@pytest.mark.parametrize("zone", ["Asia/Jerusalm", "", "../../etc/passwd", "Asia/Jerusalem ", "asia/jerusalem"])
@LINUX_ONLY
def test_a_zone_outside_the_list_is_named_before_any_disk_command(tmp_path, zone):
    box = _machine(tmp_path)
    calls = _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", timezone=zone), "--dry-run")
    assert got.returncode == 2 and "[invalid-timezone]" in got.stderr, got.stderr
    assert not calls.exists()


@LINUX_ONLY
def test_an_unreadable_list_is_its_own_failure_not_an_unknown_zone(tmp_path):
    box = _machine(tmp_path)
    box["env"]["ZONES_FILE"] = str(tmp_path / "no-such-list")
    got = _run(box, _answers(tmp_path / "answers"), "--dry-run")
    assert got.returncode == 2 and "[zones-list-missing]" in got.stderr, got.stderr


@LINUX_ONLY
def test_the_chosen_zone_is_what_finish_puts_into_the_target(tmp_path):
    box = _machine(tmp_path)
    _command_shims(tmp_path, box["env"])
    got = _run(box, _answers(tmp_path / "answers", timezone="Europe/London"), "--dry-run")
    assert got.returncode == 0, got.stderr
    assert "test -f /usr/share/zoneinfo/Europe/London" in got.stdout
    assert "ln -sf /usr/share/zoneinfo/Europe/London /etc/localtime" in got.stdout
    assert "Asia/Jerusalem" not in got.stdout, "no fixed zone left in the engine"


def test_finish_writes_the_zone_into_the_target_answers_and_etc_timezone():
    text = (REPO / "installer" / "lib" / "finish.sh").read_text(encoding="utf-8")
    assert 'write_target 0644 /etc/timezone "$ANSWER_TIMEZONE' in text
    assert "\ntimezone=$ANSWER_TIMEZONE\n" in text


# --- the bridge -----------------------------------------------------------------


@BRIDGE_ONLY
def test_inventory_offers_jerusalem_and_apply_writes_the_chosen_zone(tmp_path):
    m = _bridge_machine(tmp_path)
    rc, out, err = bridge(m, "inventory")
    assert rc == 0, err
    assert out["timezone"] == ["Asia/Jerusalem"]
    rc, out, err = bridge(m, "apply", {**FORM, "timezone": "Europe/London"})
    assert rc == 0, err
    assert "timezone=Europe/London\n" in (m["run"] / "answers").read_text()


@BRIDGE_ONLY
def test_the_engines_timezone_refusal_lands_on_the_timezone_field(tmp_path):
    m = _bridge_machine(tmp_path)
    engine = tmp_path / "tz-engine"
    engine.write_text("#!/bin/sh\n"
                      "grep -q '^timezone=Mars/Base$' \"$1\" && "
                      "{ echo 'installer: [invalid-timezone] unknown timezone: Mars/Base' >&2; exit 2; }\n"
                      "exit 0\n")
    engine.chmod(0o755)
    rc, out, err = bridge(m, "validate", {**FORM, "timezone": "Mars/Base", "step": "3"}, ENGINE=str(engine))
    assert rc == 0, err
    assert out["errors.timezone"] == ["אזור הזמן אינו מוכר — שם אזור של tzdata באנגלית, למשל Asia/Jerusalem"]
    assert "errors.disk" not in out


# --- firstboot -> the server's setting ------------------------------------------


def test_firstboot_hands_the_zone_to_setup_and_setup_writes_the_setting():
    firstboot = (REPO / "tools" / "iso" / "firstboot-answers.sh").read_text(encoding="utf-8")
    assert 'argv+=(--timezone "$(answers_get timezone)")' in firstboot
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert '--timezone)    TIMEZONE="${2:?}"; shift 2 ;;' in setup
    assert "set_setting(connect(\"$DATA_DIR/imagesrv.db\"), timezone.SETTING, zone)" in setup
    assert "if zone not in timezone.zones():" in setup


# --- the native GUI -------------------------------------------------------------


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
static void key(App *a, InstallBridge *b, int k) { Event e = {0}; e.type = UIEV_KEY; e.key = k; install_handle(a, b, &e); }

/* argv: BRIDGE ZONE|- -- start, go to the server-name step, Tab, and (unless
 * "-") replace what the field holds with ZONE, then Enter. Only symbols the
 * installer had before #1447, so the same driver runs against the old code. */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    if (install_start(a, &b, 0)) { printf("start-failed\n"); return 3; }
    a->install.view = INSTALL_HOSTNAME; refocus(a);
    key(a, &b, KEYSYM_TAB);
    if (strcmp(argv[2], "-")) {
        for (int i = 0; i < 64; i++) key(a, &b, KEYSYM_BACKSPACE);
        for (const char *p = argv[2]; *p; p++) { Event c = {0}; c.type = UIEV_CHAR; c.ch = *p; install_handle(a, &b, &c); }
    }
    key(a, &b, KEYSYM_ENTER);
    printf("moved_on=%%d\nhostname=%%s\n", a->install.view != INSTALL_HOSTNAME, a->install.hostname);
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    tmp = tmp_path_factory.mktemp("install-tz")
    source = tmp / "install_tz.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_tz"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _drive(driver: Path, tmp_path: Path, zone: str, error: str = "") -> tuple[dict, dict]:
    """The bridge records each action's JSON; ``error`` = its errors.timezone on validate."""
    bridge_cmd = (f'f(){{ cat > "{tmp_path}/$1.json"; case "$1" in '
                  "inventory) printf 'disk=/dev/sda|model=X|size=1|bus=sata|removable=0|has=empty|iso=0\\n' ;; "
                  "progress) printf 'state=ready\\n' ;; "
                  'validate) [ -z "$TZ_ERR" ] || printf "errors.timezone=%s\\n" "$TZ_ERR" ;; esac; }; f')
    proc = subprocess.run([str(driver), bridge_cmd, zone], capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL, env={**os.environ, "TZ_ERR": error})
    assert proc.returncode == 0, proc.stdout + proc.stderr
    state = dict(line.split("=", 1) for line in proc.stdout.splitlines())
    sent = json.loads((tmp_path / "validate.json").read_text())
    return state, sent


@NATIVE
def test_the_gui_starts_at_jerusalem(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "-")
    assert sent.get("timezone") == "Asia/Jerusalem", "Nadav's default, before the person types"
    assert state["moved_on"] == "1"


@NATIVE
def test_tab_from_the_name_reaches_the_zone_field_and_the_typed_zone_is_sent(driver, tmp_path):
    state, sent = _drive(driver, tmp_path, "Europe/London")
    assert sent.get("timezone") == "Europe/London" and sent["step"] == 3
    # the installer's default name: "imagesrv-server" since #1716 (the product is ImageSrv)
    assert state["hostname"] == "imagesrv-server", "the typing went to the zone, not the name"
    assert state["moved_on"] == "1"


@NATIVE
def test_a_zone_error_keeps_the_person_on_the_step(driver, tmp_path):
    state, _ = _drive(driver, tmp_path, "Mars/Base", error="אזור הזמן אינו מוכר")
    assert state["moved_on"] == "0"
