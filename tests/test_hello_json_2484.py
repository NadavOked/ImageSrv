"""#2484 -- the hello body must stay valid JSON whatever the hardware says.

Two sources broke it, and the server answers an invalid body with 400
(`server/agent/body.py`): the machine is never registered and boots its
local disk.

* `probe_memory` wrote the DIMM manufacturer between quotes unescaped, and
  `Size: Unknown` straight into a numeric field.
* `json_escape` passed bytes >= 0x80 through untouched, so a model or serial
  that is not valid UTF-8 (NVMe identify, SMBIOS) broke the document.

Every case is parsed by a real JSON parser (`json.loads`). The controls pin
what must NOT change: ASCII and valid UTF-8 (Hebrew included) stay exactly
as they were.
"""

from __future__ import annotations

import json

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 -- fixture
from test_probe import _agent_hello, _stub, probe_of

pytestmark = requires_native(("bash", BASH))

FFFD = "\ufffd"


def octal(data: bytes) -> str:
    """The bytes as a printf format, so no invalid byte ever crosses argv."""
    return "".join(f"\\{b:03o}" for b in data)


def escaped(data: bytes) -> str:
    return sh(f'. {posix(AGENT)}/lib/core/common.sh; json_escape "$(printf \'{octal(data)}\')"')


def dmidecode(tmp_path, body: str):
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    _stub(stubs, "dmidecode", body)
    return stubs


# --- the DIMM block of probe_memory -----------------------------------------


def test_a_manufacturer_with_a_quote_and_backslash_stays_valid_json(fake_machine, tmp_path):
    stubs = dmidecode(tmp_path, r"""printf 'Memory Device\n\tSize: 8 GB\n\tSpeed: 2666 MT/s\n\tManufacturer: Acme "Pro" \\ Mem\n'""")
    dimms = probe_of(fake_machine["sysroot"], stubs=stubs)["memory"]["dimms"]
    assert dimms == [{"size_bytes": 8 * 1073741824, "speed_mts": 2666,
                      "manufacturer": 'Acme "Pro" \\ Mem'}]


def test_size_unknown_does_not_enter_the_json_raw(fake_machine, tmp_path):
    """A DIMM that is present but whose size is unreadable is reported with
    `size_bytes: null` -- not dropped (principle 5: unknown is not absent)
    and not written as the bare word Unknown."""
    stubs = dmidecode(tmp_path, r"""printf 'Memory Device\n\tSize: Unknown\n\tManufacturer: Samsung\nMemory Device\n\tSize: No Module Installed\n'""")
    dimms = probe_of(fake_machine["sysroot"], stubs=stubs)["memory"]["dimms"]
    assert dimms == [{"size_bytes": None, "speed_mts": None, "manufacturer": "Samsung"}]


def test_a_manufacturer_with_an_invalid_byte_stays_valid_json(fake_machine, tmp_path):
    stubs = dmidecode(tmp_path, r"""printf 'Memory Device\n\tSize: 4 GB\n\tManufacturer: Bad\377Vendor\n'""")
    (dimm,) = probe_of(fake_machine["sysroot"], stubs=stubs)["memory"]["dimms"]
    assert dimm["manufacturer"] == f"Bad{FFFD}Vendor"


def test_a_hebrew_manufacturer_is_kept_exactly(fake_machine, tmp_path):
    stubs = dmidecode(tmp_path, "printf 'Memory Device\\n\\tSize: 16 GB\\n\\tManufacturer: יצרן שלום\\n'")
    (dimm,) = probe_of(fake_machine["sysroot"], stubs=stubs)["memory"]["dimms"]
    assert dimm["manufacturer"] == "יצרן שלום"
    assert dimm["size_bytes"] == 16 * 1073741824


# --- the disk model in the hello ---------------------------------------------


def test_a_disk_model_with_0xff_keeps_the_hello_valid(fake_machine):
    model = fake_machine["sysroot"] / "sys/block/sda/device/model"
    model.write_bytes(b"Bad\xffModel 500GB   \n")
    hello = _agent_hello(fake_machine, "")
    assert hello["disks"][0]["model"] == f"Bad{FFFD}Model 500GB"
    assert hello["mac"] == "b4:2e:99:07:1a:c4"      # the rest of the hello survived


def test_hebrew_and_ascii_disk_models_are_unchanged(fake_machine):
    model = fake_machine["sysroot"] / "sys/block/sda/device/model"
    model.write_bytes("דיסק שלום Samsung 870".encode() + b"  \n")
    assert _agent_hello(fake_machine, "")["disks"][0]["model"] == "דיסק שלום Samsung 870"
    model.write_bytes(b"Samsung SSD 870 EVO 250GB   \n")
    assert _agent_hello(fake_machine, "")["disks"][0]["model"] == "Samsung SSD 870 EVO 250GB"


# --- json_escape itself -------------------------------------------------------


VALID = [
    "plain ascii 123", 'a"b\\c', "שלום עולם", 'שלום "עולם" \\ נדב', "café",
    "\u0080", "߿", "ࠀ", "퟿", "", "￿", "\U00010000",
    "\U0010ffff", "😀 emoji", "mixed éא€\U0001f600 end",
]


@pytest.mark.parametrize("text", VALID)
def test_valid_utf8_is_returned_exactly(text):
    assert json.loads('"' + escaped(text.encode()) + '"') == text


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"a\xffb", f"a{FFFD}b"),
        (b"\xff", FFFD),
        (b"\x80", FFFD),                              # stray continuation
        (b"a\xd7", f"a{FFFD}"),                       # truncated at the end
        (b"\xc0\x80", FFFD * 2),                      # overlong NUL
        (b"\xc1\xbf", FFFD * 2),                      # overlong ASCII
        (b"\xe0\x80\x80", FFFD * 3),                  # overlong 3-byte
        (b"\xed\xa0\x80", FFFD * 3),                  # UTF-16 surrogate
        (b"\xf0\x80\x80\x80", FFFD * 4),              # overlong 4-byte
        (b"\xf4\x90\x80\x80", FFFD * 4),              # beyond U+10FFFF
        (b"\xf5\x80\x80\x80", FFFD * 4),
        (b"\xe2\x82x", f"{FFFD}{FFFD}x"),             # cut sequence, ASCII follows
        ("ש".encode() + b"\xff", f"ש{FFFD}"),         # valid, then invalid
        (b"\xff" + "ש".encode(), f"{FFFD}ש"),         # invalid, then valid
        (b"x\xffy\xfez", f"x{FFFD}y{FFFD}z"),
    ],
)
def test_invalid_bytes_become_u_fffd_and_the_rest_survives(data, expected):
    assert json.loads('"' + escaped(data) + '"') == expected
