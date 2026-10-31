"""‏#720 — המלאי החומרתי ב-hello (schema 2): הסוכן קורא אותו מ-sysfs,
השרת שומר אותו מגורסת ומציג אותו בכרטיס המכונה.

צד הסוכן רץ על ה-sh האמיתי מול sysfs מזויף (`SYSROOT`), וה-JSON נטען
בפייתון — לא "נראה תקין". צד השרת: ‏`well_formed` שורה-שורה, ואז
ההיסטוריה — שורה חדשה רק כשהמלאי השתנה.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from native import requires_native
from test_agent import AGENT, BASH, fake_machine, posix, sh  # noqa: F401 — fixture

pytestmark = requires_native(("bash", BASH))


# --- הסוכן -----------------------------------------------------------------


def _pci_device(root: Path, addr: str, vendor: str, device: str, klass: str) -> None:
    # כתובת אפיק אמיתית היא 0000:00:1f.6; נקודתיים אסורות בשם קובץ בווינדוס,
    # והסוכן ממילא אינו קורא את השם — רק את סדר ה-glob.
    d = root / "sys/bus/pci/devices" / addr.replace(":", "-")
    d.mkdir(parents=True)
    (d / "vendor").write_text(vendor + "\n")
    (d / "device").write_text(device + "\n")
    (d / "class").write_text(klass + "\n")


def _lenovo(sysroot: Path) -> None:
    dmi = sysroot / "sys/class/dmi/id"
    (dmi / "sys_vendor").write_text("LENOVO\n")
    (dmi / "product_name").write_text("10SQS0AK00\n")
    (dmi / "product_version").write_text("ThinkCentre M720q   \n")
    (dmi / "board_name").write_text("3132\n")
    _pci_device(sysroot, "0000:00:1f.6", "0x8086", "0x15BC", "0x020000")   # NIC (uppercase device)
    _pci_device(sysroot, "0000:00:17.0", "0x8086", "0xa352", "0x010601")   # AHCI
    _pci_device(sysroot, "0000:00:02.0", "0x8086", "0x3e92", "0x030000")   # GPU — לא נכלל
    _pci_device(sysroot, "0000:01:00.0", "0x144d", "0xa808", "0x010802")   # NVMe


def inventory_of(sysroot: Path) -> dict:
    out = sh(
        f'export SYSROOT={posix(sysroot)!r}; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/inventory.sh; '
        f'printf "{{\\"x\\":0%s}}" "$(inventory_json)"'
    )
    return json.loads(out)["inventory"]


def test_the_inventory_is_read_from_sysfs(fake_machine):
    _lenovo(fake_machine["sysroot"])
    inv = inventory_of(fake_machine["sysroot"])
    assert inv["dmi"] == {"sys_vendor": "LENOVO", "product_name": "10SQS0AK00",
                          "product_version": "ThinkCentre M720q", "board_name": "3132"}
    # רק רשת (02) ואחסון (01); hex קטן בלי 0x; בסדר sysfs (כתובת אפיק).
    assert inv["pci"] == ["8086:a352:010601", "8086:15bc:020000", "144d:a808:010802"]
    assert inv["tpm"] is None                 # אין /sys/class/tpm — לא נבדק


def test_an_empty_dmi_field_is_null_not_an_empty_string(fake_machine):
    sysroot = fake_machine["sysroot"]
    (sysroot / "sys/class/dmi/id/sys_vendor").write_text("   \n")
    inv = inventory_of(sysroot)
    assert inv["dmi"]["sys_vendor"] is None
    assert inv["dmi"]["product_name"] is None   # הקובץ חסר
    assert inv["pci"] == []


def test_a_device_whose_id_cannot_be_read_is_left_out(fake_machine):
    sysroot = fake_machine["sysroot"]
    _pci_device(sysroot, "0000:00:1f.6", "0x8086", "0x15bc", "0x020000")
    _pci_device(sysroot, "0000:02:00.0", "garbage", "0x1234", "0x020000")
    _pci_device(sysroot, "0000:03:00.0", "0x10ec", "0x8168", "notaclass")
    assert inventory_of(sysroot)["pci"] == ["8086:15bc:020000"]


@pytest.mark.parametrize("major, expected", [
    ("2", {"present": True, "version": "2.0"}),
    ("1", {"present": True, "version": "1.2"}),
    ("", {"present": True, "version": None}),
])
def test_the_tpm_has_three_states_not_two(fake_machine, major, expected):
    sysroot = fake_machine["sysroot"]
    tpm = sysroot / "sys/class/tpm"
    tpm.mkdir(parents=True)
    assert inventory_of(sysroot)["tpm"] == {"present": False}   # הסתכלנו, אין
    (tpm / "tpm0").mkdir()
    (tpm / "tpm0/tpm_version_major").write_text(major + "\n")
    assert inventory_of(sysroot)["tpm"] == expected


def _agent_hello(fake_machine, libs: str) -> dict:
    return json.loads(sh(
        f'export SYSROOT={posix(fake_machine["sysroot"])!r} '
        f'DEVROOT={posix(fake_machine["dev"])!r} '
        f'RUN_DIR={posix(fake_machine["run"])!r} IFACE=eth0 IP=10.44.12.187; '
        f'. {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/hw/sysinfo.sh; '
        f'{libs} build_hello'
    ))


def test_hello_schema_2_carries_the_inventory(fake_machine):
    _lenovo(fake_machine["sysroot"])
    hello = _agent_hello(fake_machine, f'. {posix(AGENT)}/lib/hw/inventory.sh;')
    assert hello["schema"] == 2
    assert hello["inventory"]["dmi"]["sys_vendor"] == "LENOVO"
    assert "8086:15bc:020000" in hello["inventory"]["pci"]
    assert hello["disks"], "שאר ה-hello נשאר כפי שהיה"


def test_hello_without_the_inventory_lib_has_no_inventory_field(fake_machine):
    """בלי inventory.sh השדה נעדר — לא ריק ולא מומצא; השרת שומר את הגרסה
    הקודמת (או כלום), כמו monitor_secret חסר."""
    assert "inventory" not in _agent_hello(fake_machine, "")


def test_the_agent_loads_the_inventory_lib():
    text = (AGENT / "imagesrv-agent").read_text(encoding="utf-8")
    assert '"$LIB_DIR/inventory.sh"' in text


# --- השרת: הצורה ------------------------------------------------------------

pytest.importorskip("fastapi")

from server.agent import inventory  # noqa: E402

GOOD = {
    "dmi": {"sys_vendor": "LENOVO", "product_name": "10SQS0AK00",
            "product_version": "ThinkCentre M720q", "board_name": "3132"},
    "pci": ["8086:15bc:020000", "8086:a352:010601"],
    "tpm": {"present": True, "version": "2.0"},
}


def test_a_well_formed_inventory_is_canonicalised():
    messy = {**GOOD, "pci": ["8086:a352:010601", "8086:15bc:020000", "8086:15bc:020000"],
             "dmi": {**GOOD["dmi"], "board_name": "  3132 "}}
    assert inventory.well_formed(messy) == GOOD


@pytest.mark.parametrize("bad", [
    None, [], "x",
    {"pci": [], "tpm": None},                                  # dmi חסר
    {"dmi": {}, "tpm": None},                                  # pci חסר
    {"dmi": {"sys_vendor": 5}, "pci": [], "tpm": None},        # dmi לא מחרוזת
    {"dmi": {}, "pci": ["8086:15bc"], "tpm": None},            # pci לא בצורה
    {"dmi": {}, "pci": ["8086:15BC:020000"], "tpm": None},     # hex גדול
    {"dmi": {}, "pci": [1], "tpm": None},
    {"dmi": {}, "pci": [], "tpm": "2.0"},                      # tpm לא אובייקט
    {"dmi": {}, "pci": [], "tpm": {"present": "yes"}},
    {"dmi": {}, "pci": ["0000:0000:000000"] * 65, "tpm": None},  # מעל התקרה
], ids=lambda b: str(b)[:40])
def test_a_malformed_inventory_is_none(bad):
    assert inventory.well_formed(bad) is None


def test_empty_dmi_and_a_missing_tpm_are_allowed():
    inv = inventory.well_formed({"dmi": {}, "pci": [], "tpm": None})
    assert inv == {"dmi": {f: None for f in inventory.DMI_FIELDS}, "pci": [], "tpm": None}
    assert inventory.well_formed({"dmi": {}, "pci": [], "tpm": {"present": False}})["tpm"] \
        == {"present": False, "version": None}


# --- השרת: היסטוריה מגורסת --------------------------------------------------

MAC = "b4:2e:99:07:1a:c4"


def test_a_version_is_written_once_and_a_change_opens_a_new_one(server):
    conn = server["ctx"].conn
    assert inventory.latest(conn, MAC) is None
    assert inventory.record(conn, MAC, inventory.well_formed(GOOD)) is True
    assert inventory.record(conn, MAC, inventory.well_formed(GOOD)) is False
    changed = {**GOOD, "pci": GOOD["pci"] + ["10ec:8168:020000"]}
    assert inventory.record(conn, MAC, inventory.well_formed(changed)) is True
    assert inventory.latest(conn, MAC)["inventory"]["pci"] == sorted(changed["pci"])
    assert [h["inventory"]["pci"] for h in inventory.history(conn, MAC)] == [
        sorted(changed["pci"]), GOOD["pci"]]
    assert set(inventory.latest_all(conn)) == {MAC}


def _hello(server, **fields):
    return server["anon"].post("/api/v1/agent/hello", json={
        "schema": 2, "mac": MAC, "all_macs": [MAC], "ip": "10.44.12.187",
        "disks": [], **fields})


def _machine(server) -> dict:
    from conftest import setup_classroom   # noqa: PLC0415
    setup_classroom(server)
    return next(m for m in server["admin"].get("/api/console/machines").json()
                if m["mac"] == MAC)


def test_hello_stores_the_inventory_and_the_machine_card_shows_it(server):
    assert _hello(server, inventory=GOOD).status_code == 200
    m = _machine(server)
    assert m["inventory"] == GOOD
    assert m["inventory_seen_at"]


def test_a_malformed_inventory_does_not_erase_the_previous_version(server):
    assert _hello(server, inventory=GOOD).status_code == 200
    assert _hello(server, inventory={"dmi": "no"}).status_code == 200
    assert _hello(server).status_code == 200                     # schema 1 — בלי השדה
    assert _machine(server)["inventory"] == GOOD


def test_a_machine_that_never_reported_shows_null_not_empty(server):
    m = _machine(server)
    assert m["inventory"] is None and m["inventory_seen_at"] is None
