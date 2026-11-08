"""#2080 (mockup 06, Nadav 24/10 and 25/10) -- the agent's side of the firmware image.

* the hello carries `sata_mode` (by the PCI class of the storage controllers) and `bios` (DMI);
* what the agent cannot read is left out -- the server keeps the last value, and "not read" is not "changed";
* the server's moved-connection warning reaches the cloner's state file (`fw_warn=`);
* "זה מכוון" (`fw-accept`) posts to the server, and is done only on a 200.
"""

from __future__ import annotations

import json

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 -- fixture
from test_inventory import _pci_device

pytestmark = requires_native(("bash", BASH))

PRE = f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/fwimage.sh; "


def _env(m, extra=""):
    return (f'export SYSROOT={posix(m["sysroot"])!r} DEVROOT={posix(m["dev"])!r} RUN_DIR={posix(m["run"])!r} '
            f'IFACE=eth0 IP=10.44.12.187 {extra}; ')


def _mode(m, *classes):
    for i, c in enumerate(classes):
        _pci_device(m["sysroot"], f"0000:00:1f.{i}", "0x8086", "0x1234", c)
    return sh(_env(m) + PRE + "fw_sata_mode")


def test_the_controller_mode_is_the_class_of_the_storage_controller(fake_machine):
    assert _mode(fake_machine, "0x010601", "0x020000", "0x010802") == "ahci"     # AHCI; the NIC and the NVMe say nothing


def test_ide_has_its_own_class(fake_machine):
    assert _mode(fake_machine, "0x010185") == "ide"


def test_raid(fake_machine):
    assert _mode(fake_machine, "0x010400") == "raid"


def test_two_different_modes_are_not_said_as_one(fake_machine):
    assert _mode(fake_machine, "0x010601", "0x010400") == "", "AHCI next to a VMD: no single mode to report"


def test_no_storage_controller_or_an_unreadable_class_says_nothing(fake_machine):
    assert _mode(fake_machine, "0x020000") == ""
    assert _mode(fake_machine) == ""
    (fake_machine["sysroot"] / "sys/bus/pci/devices/0000-00-1f.0/class").write_text("banana\n")
    assert sh(_env(fake_machine) + PRE + "fw_sata_mode") == ""


def _bios(m, version="786H1 v01.13", date="03/01/2012", vendor="Hewlett-Packard"):
    dmi = m["sysroot"] / "sys/class/dmi/id"
    for k, v in (("bios_version", version), ("bios_date", date), ("bios_vendor", vendor)):
        (dmi / k).write_text(v + "\n")


def _fragment(m, extra=""):
    return json.loads(sh(_env(m, extra) + PRE + 'printf "{\\"x\\":0%s}" "$(firmware_json)"'))


def test_the_fragment_carries_both_and_is_json(fake_machine):
    _pci_device(fake_machine["sysroot"], "0000:00:1f.2", "0x8086", "0x1c02", "0x010601")
    _bios(fake_machine)
    assert _fragment(fake_machine) == {"x": 0, "sata_mode": "ahci",
                                       "bios": {"version": "786H1 v01.13", "date": "03/01/2012", "vendor": "Hewlett-Packard"}}


def test_a_part_that_cannot_be_read_is_absent_not_empty(fake_machine):
    _bios(fake_machine, version="   ")                      # no version: no bios at all
    assert _fragment(fake_machine) == {"x": 0}


def test_an_empty_date_is_null_and_a_quote_is_escaped(fake_machine):
    _bios(fake_machine, date="", vendor='a"b')
    assert _fragment(fake_machine)["bios"] == {"version": "786H1 v01.13", "date": None, "vendor": 'a"b'}


def test_it_rides_the_real_hello(fake_machine):
    _pci_device(fake_machine["sysroot"], "0000:00:1f.2", "0x8086", "0x1c02", "0x010185")
    _bios(fake_machine)
    base = _env(fake_machine) + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; . {posix(AGENT)}/lib/hw/diskinfo.sh; '
    hello = json.loads(sh(base + f'. {posix(AGENT)}/lib/hw/fwimage.sh; build_hello'))
    assert hello["sata_mode"] == "ide" and hello["bios"]["version"] == "786H1 v01.13"
    old = json.loads(sh(base + "build_hello"))
    assert "sata_mode" not in old and "bios" not in old, "without the lib the hello is the old one"


def test_the_value_is_read_once_per_boot(fake_machine):
    _pci_device(fake_machine["sysroot"], "0000:00:1f.2", "0x8086", "0x1c02", "0x010601")
    first = _fragment(fake_machine)
    (fake_machine["sysroot"] / "sys/bus/pci/devices/0000-00-1f.2/class").write_text("0x010185\n")
    assert _fragment(fake_machine) == first, "cached in RUN_DIR for the boot"


# --- the way back: the warning on the cloner's wait screen, and "זה מכוון" ---


def _answer(m, body):
    (m["run"] / "response.json").write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")


def _record(m):
    return sh(_env(m) + PRE + "fw_warn_record")


def test_the_warning_becomes_one_state_record(fake_machine):
    _answer(fake_machine, {"fw_warning": {"changes": ["דיסק T עבר מ-SATA 1 ל-SATA 2", "דיסק B נעלם מ-SATA 3"], "count": 3}})
    assert _record(fake_machine) == "fw_warn=3|דיסק T עבר מ-SATA 1 ל-SATA 2 · דיסק B נעלם מ-SATA 3 · ועוד 1\n"


def test_no_warning_or_an_unreadable_answer_writes_nothing(fake_machine):
    assert _record(fake_machine) == ""                                  # no answer yet
    _answer(fake_machine, {"known": True})
    assert _record(fake_machine) == ""
    _answer(fake_machine, {"fw_warning": {"changes": [], "count": 0}})
    assert _record(fake_machine) == ""
    (fake_machine["run"] / "response.json").write_text("{not json")
    assert _record(fake_machine) == ""


def test_a_separator_or_newline_in_the_text_cannot_split_the_record(fake_machine):
    _answer(fake_machine, {"fw_warning": {"changes": ["a|b\nc"], "count": 1}})
    assert _record(fake_machine) == "fw_warn=1|a b c\n"


def _stub_curl(m, code: str) -> str:
    stubs = m["run"].parent / "stubs"
    stubs.mkdir(exist_ok=True)
    f = stubs / "curl"
    f.write_text('#!/bin/sh\nfor a; do case "$prev" in --data-binary) cat "${a#@}" > "' + posix(m["run"]) + '/posted";; esac; prev=$a; done\n'
                 f'printf "{code}"\n', newline="\n")
    f.chmod(0o755)
    return posix(stubs)


def _accept(m, code):
    stubs = _stub_curl(m, code)
    out = sh(_env(m, f'MAC=b4:2e:99:07:1a:c4 SERVER=http://srv PATH={stubs}:"$PATH"') + PRE + "fw_accept_post; echo rc=$?")
    return out.strip()


def test_accept_posts_the_mac_and_clears_the_warning_on_200(fake_machine):
    _answer(fake_machine, {"known": True, "fw_warning": {"changes": ["x"], "count": 1}})
    assert _accept(fake_machine, "200") == "rc=0"
    assert json.loads((fake_machine["run"] / "posted").read_text()) == {"mac": "b4:2e:99:07:1a:c4"}
    assert json.loads((fake_machine["run"] / "response.json").read_text()) == {"known": True}, "only the warning goes"


def test_accept_is_a_failure_on_anything_but_200(fake_machine):
    for code in ("403", "409", "500", "000", ""):
        _answer(fake_machine, {"fw_warning": {"changes": ["x"], "count": 1}})
        assert _accept(fake_machine, code) == "rc=1", code
        assert "fw_warning" in json.loads((fake_machine["run"] / "response.json").read_text()), "the warning stays"


def test_a_failed_accept_is_said_for_a_few_seconds_and_a_good_one_clears_it(fake_machine):
    _answer(fake_machine, {"fw_warning": {"changes": ["x"], "count": 1}})
    assert "toast=" not in _record(fake_machine)
    assert _accept(fake_machine, "409") == "rc=1"
    said = _record(fake_machine)
    assert "toast=השמירה נכשלה" in said and "fw_warn=1|x" in said, "the toast and the warning together"
    (fake_machine["run"] / "fw.failed").write_text("1\n")                        # a failure from long ago
    assert "toast=" not in _record(fake_machine)
    (fake_machine["run"] / "fw.failed").write_text("banana\n")                   # a marker that cannot be read says nothing
    assert "toast=" not in _record(fake_machine)
    assert _accept(fake_machine, "200") == "rc=0" and not (fake_machine["run"] / "fw.failed").exists()


def _dispatch(m, screen, code):
    stubs = _stub_curl(m, code)
    return sh(_env(m, f'MAC=b4:2e:99:07:1a:c4 SERVER=http://srv GUI_SCREEN={screen} GUI_DIR={posix(m["run"])!r} PATH={stubs}:"$PATH"')
              + f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/fwimage.sh; . {posix(AGENT)}/lib/gui/guibridge.sh; '
              + 'token=fw-accept; gui_dispatch 2>/dev/null; echo "rc=$?"').strip()


def test_the_bridge_takes_fw_accept_on_the_cloner_only(fake_machine):
    _answer(fake_machine, {"fw_warning": {"changes": ["x"], "count": 1}})
    assert _dispatch(fake_machine, "build", "200") == "rc=1" and not (fake_machine["run"] / "posted").exists(), "nothing posted off the cloner"
    assert _dispatch(fake_machine, "cloner", "409") == "rc=1" and (fake_machine["run"] / "fw.failed").exists()
    assert _dispatch(fake_machine, "cloner", "200") == "rc=0"
