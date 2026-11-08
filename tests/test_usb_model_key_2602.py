"""#2602 -- the clone stick keys its connections map by the SAME model key as the server (#2001).

The stick (agent/lib/usb/usbmap.sh usb_model) took the DMI product_name as it was. On a
board that left the field unfilled ("To Be Filled By O.E.M.") every such machine shared
one key, so a drawer map saved on one was put on another -- a wrong map writes to the
wrong disk (principles 1 and 5). And a machine with no product_name at all (the HP
i5-4590 of 22/10) got no per-model map. The server's rule is
server/disks/model_slots.py model_key; this file runs the stick's shell, under busybox
ash (the shell the agent runs under), on the same inputs and compares the two:

* the inputs go through the agent's own inventory_dmi (what the hello carries), so the
  server sees exactly what the stick's files said;
* the generic list is one list on both sides.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


import hygiene
from agentlib import agent_sources
from native import requires_native
from server.disks import model_slots
from test_cloner_gui import posix
from test_usb_clone import needs_jq
from test_usb_map_1962 import MODEL, SATA1_OFF, TO_MAP, machine, saved, stick

pytestmark = [needs_jq, requires_native(("busybox", shutil.which("busybox")), posix=True,
                                        why="the agent runs under busybox ash; bash would hide a bashism")]

OEM = "To Be Filled By O.E.M."

#: (sys_vendor, product_name, board_name, the key). None = the file is not there.
CASES = [
    ("HP", "HP Compaq 8100 Elite SFF PC", "304Bh", "HP Compaq 8100 Elite SFF PC"),
    ("Hewlett-Packard", None, "18E7", "Hewlett-Packard (board 18E7)"),      # the HP i5-4590
    ("Hewlett-Packard", "", "18E7", "Hewlett-Packard (board 18E7)"),
    ("ASUS", OEM, "H81M-K", "ASUS (board H81M-K)"),
    ("ASUS", "  to be filled by oem ", "H81M-K", "ASUS (board H81M-K)"),
    ("ASUS", "System Product Name", "PRIME B250", "ASUS (board PRIME B250)"),
    ("Dell Inc.", "Default string", "0X1234", "Dell Inc. (board 0X1234)"),
    ("ASUS", "O.E.M.", "H81M-K", "ASUS (board H81M-K)"),
    ("ASUS", "Not Applicable", "H81M-K", "ASUS (board H81M-K)"),
    ("ASUS", "N/A", "H81M-K", "ASUS (board H81M-K)"),
    ("ASUS", "A", "H81M-K", "ASUS (board H81M-K)"),                           # one character
    ("ASUS", "0000", "H81M-K", "ASUS (board H81M-K)"),                        # one character, repeated
    ("ASUS", "ababab", None, "ababab"),                                       # real: two characters
    ("Lenovo", "10SQS0AK00", None, "10SQS0AK00"),
    ("LENOVO", "  ThinkCentre  M720q ", "3132", "ThinkCentre  M720q"),        # stripped, not squeezed
    ("יצרן", "מחשב כיתה", None, "מחשב כיתה"),                                  # Hebrew is letters
    ("יצרן", None, "לוח-7", "יצרן (board לוח-7)"),
    ("Éé", "Éé", "Éé", None),                                                  # one letter in two cases
    # no usable source -> no key
    ("Hewlett-Packard", None, None, None),
    (None, None, "18E7", None),                                               # a board number alone is no model
    (OEM, None, "18E7", None),                                                # a generic maker is no maker
    ("ASUS", OEM, OEM, None),
    ("Default string", "Default string", "Default string", None),
    ("ASUS", "0", "0000", None),
    (None, None, None, None),
    ("ASUS", "Type1ProductConfigId", "System Version", None),
]


def _sysroot(base: Path, i: int, vendor, product, board) -> Path:
    dmi = base / f"m{i}" / "sys" / "class" / "dmi" / "id"
    dmi.mkdir(parents=True)
    for name, value in (("sys_vendor", vendor), ("product_name", product), ("board_name", board)):
        if value is not None:
            (dmi / name).write_text(value + "\n", encoding="utf-8", newline="\n")
    return base / f"m{i}"


def _sh(body: str) -> str:
    script = ('export IMAGESRV_TEST=1; ' + agent_sources("common", "inventory", "usbmap") + body)
    r = hygiene.run_group([shutil.which("busybox"), "ash", "-c", script], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    return r.stdout


def _models(tmp_path: Path, cases) -> list[tuple[str, dict]]:
    """Per case: what the stick's usb_model prints, and the dmi the hello would carry."""
    roots = [_sysroot(tmp_path, i, *c[:3]) for i, c in enumerate(cases)]
    out = _sh("for r in " + " ".join(f"'{posix(r)}'" for r in roots)
              + '; do SYSROOT="$r"; printf \'%s\\t%s\\n\' "$(usb_model)" "$(inventory_dmi)"; done')
    rows = out.rstrip("\n").split("\n")
    assert len(rows) == len(cases), out
    return [(m, json.loads(d)) for m, d in (row.split("\t") for row in rows)]


def test_the_stick_and_the_server_key_the_same_inputs_the_same(tmp_path):
    rows = _models(tmp_path, CASES)
    wrong = []
    for (vendor, product, board, want), (stick_key, dmi) in zip(CASES, rows):
        server_key = model_slots.model_key(dmi)
        if not (stick_key == (server_key or "") == (want or "")):
            wrong.append(f"{vendor!r}/{product!r}/{board!r}: stick {stick_key!r}, server {server_key!r}, want {want!r}")
    assert not wrong, "\n".join(wrong)


def test_one_list_of_generic_values_on_both_sides(tmp_path):
    out = _sh('printf "%s\\n" $USBMAP_GENERIC')
    assert sorted(out.split()) == sorted(model_slots._GENERIC)


def test_a_key_saved_under_a_real_product_name_is_unchanged(tmp_path):
    """The 22/10 maps on sticks in the field: the key is the product_name, byte for byte."""
    (key,) = _models(tmp_path, [("Hewlett-Packard", MODEL, "18E7", MODEL)])
    assert key[0] == MODEL


# -- the stick itself, on a machine with no product_name -------------------------------------

BOARD_KEY = "Hewlett-Packard (board 18E7)"


def _no_product_name(tmp_path: Path, vendor: str | None, board: str | None, product: str | None = None) -> None:
    root = machine(tmp_path, product)   # product_name only when given; the rest is the HP 8000 of the other tests
    dmi = root / "sys" / "class" / "dmi" / "id"
    for name, value in (("sys_vendor", vendor), ("board_name", board)):
        if value is not None:
            (dmi / name).write_text(value + "\n", encoding="utf-8", newline="\n")


def test_a_stick_on_a_machine_with_no_product_name_keeps_its_map_by_the_board(tmp_path):
    _no_product_name(tmp_path, "Hewlett-Packard", "18E7")
    out, usb = stick(tmp_path, TO_MAP + SATA1_OFF + ["KEY F2"], model=None, then='cat "$RUN_DIR/response.json"')
    want = ["sata:1", "sata:3", "nvme:0000:03:00.0"]   # SATA 1 off (#1874: on by default): a compact list is a saved one
    assert saved(usb) == {"drawer_slots": {BOARD_KEY: want}}, out.stdout + out.stderr
    assert json.loads(out.stdout.split("@@")[1])["drawer_slots"] == want
    # the same board finds it again; another board of the same maker gets the default
    again, _ = stick(tmp_path, [], model=None, then="usb_map_json")
    assert json.loads(again.stdout.split("@@")[1]) == want, again.stdout + again.stderr
    other = tmp_path / "other"; other.mkdir()
    _no_product_name(other, "Hewlett-Packard", "2AB1")
    miss, _ = stick(other, [], model=None, settings={"drawer_slots": {BOARD_KEY: want}}, then='usb_map_json; echo "rc=$?"')
    assert miss.stdout.split("@@")[1].strip() == "rc=1", miss.stdout + miss.stderr


def test_a_generic_product_name_saves_nothing_on_the_stick(tmp_path):
    """Before: the key was "To Be Filled By O.E.M." -- one map for every such machine."""
    _no_product_name(tmp_path, "ASUS", None, OEM)
    out, usb = stick(tmp_path, TO_MAP + SATA1_OFF + ["KEY F2"], model=OEM)
    assert "לא זוהה דגם המחשב" in out.stdout, out.stdout + out.stderr
    assert not (usb / "settings.json").exists()


def test_a_map_saved_under_a_generic_name_no_longer_applies(tmp_path):
    out, _ = stick(tmp_path, [], model=OEM, settings={"drawer_slots": {OEM: ["sata:2"]}},
                   then='usb_map_json; echo "rc=$?"; usb_settings_load; usb_answer; jq -c ".drawer_slots" "$RUN_DIR/response.json"')
    rc, slots = out.stdout.split("@@")[1].split("\n")[1:3]
    assert rc == "rc=1" and slots == '["sata:1","sata:2","sata:3","nvme:0000:03:00.0"]', out.stdout + out.stderr
