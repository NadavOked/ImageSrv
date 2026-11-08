"""#2694: a clone report's link speed covers every source and target."""

import json

import pytest

from native import requires_native
from test_cloner_gui import BASH
from test_disklink_432 import _sata
from test_usb_clone import curl_recorder, needs_jq, posix, run, sysroot

pytestmark = requires_native(("bash", BASH))


def links(tmp_path, target="3.0 Gbps"):
    # Reuse the libata sysfs helper (its fixed disk name is sda).
    root = sysroot(tmp_path, {"sda": []})
    block = root / "sys/block"
    (block / "sda/device").mkdir()
    _sata(root, ata=2, host=1, spd=target)
    (block / "sda").rename(block / "sdc")
    (block / "sda/device").mkdir(parents=True)
    _sata(root, ata=1, host=0, spd="6.0 Gbps")
    (block / "nvme0n1/device").mkdir(parents=True)
    return root


@pytest.mark.parametrize("args,target,expected", [
    ("sda", "3.0 Gbps", "6.0"),
    ("sda sdc", "3.0 Gbps", "3.0"),
    ("sdc sda", "1.5 Gbps", "1.5"),
    ("sda sdc", None, "null"),
    ("", "3.0 Gbps", "null"),
    ("nvme0n1", "3.0 Gbps", "null"),
    ("../sda", "3.0 Gbps", "null"),
    ("sda SDA", "3.0 Gbps", "null"),
    ("''", "3.0 Gbps", "null"),
])
def test_clone_link_gbps(tmp_path, args, target, expected):
    root = links(tmp_path, target)
    out = run(tmp_path, f"clone_link_gbps {args}", SYSROOT=posix(root))
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == expected, out.stdout + out.stderr


@needs_jq
@pytest.mark.parametrize("target,expected", [("3.0 Gbps", 3.0), (None, None)])
def test_saved_report_keeps_complete_or_unknown_link_speed(tmp_path, target, expected):
    rec, calls = curl_recorder(tmp_path)
    root = links(tmp_path, target)
    for disk, serial in (("sda", "SOURCE-SERIAL"), ("sdc", "TARGET-SERIAL")):
        (root / "sys/block" / disk / "device/serial").write_text(serial + "\n")
    session = tmp_path / "run/lc/run"
    session.mkdir(parents=True)
    for name, value in {"src": "sda", "targets": "sdc", "started": "100", "ended": "160"}.items():
        (session / name).write_text(value + "\n")
    result = tmp_path / "run/targets/sdc"
    result.mkdir(parents=True)
    (result / "state").write_text("done\n")
    data = tmp_path / "usbdata"
    data.mkdir()
    out = run(tmp_path, rec + 'LC="$RUN_DIR/lc"; _lr_src=sda; MAC=aa:bb; '
              'disk_port() { echo 1; }; usb_serials_snap sda sdc; '
              f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; lc_report',
              SYSROOT=posix(root), IMAGESRV_MODE="usbclone")
    kept = list((data / "reports").glob("clone-*.json"))
    assert len(kept) == 1, out.stdout + out.stderr
    report = json.loads(kept[0].read_text(encoding="utf-8"))
    assert "link_gbps" in report
    assert report["link_gbps"] == expected
    assert not calls.exists(), calls.read_text() if calls.exists() else ""
