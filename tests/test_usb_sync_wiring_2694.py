"""#2694 -- usbclone_main calls the server-sync check, in the right place, and says a failure on the screen.

``test_usb_server_sync_2694.py`` proves what ``usb_sync_load`` decides. This proves it is CALLED: before the
boot diag (which reads the SMART tables, so the tables in force must already be the verified ones) and that the
verdict reaches ``usb.notice``, the line the clone screen shows.
"""

from __future__ import annotations

from native import requires_native
from test_cloner_gui import BASH
from test_usb_clone import GUI_UNTIL_EXIT, blkid_stub, needs_jq, posix, run, sysroot
from test_usb_server_sync_2694 import SMART, bundle, knowledge

pytestmark = [requires_native(("bash", BASH)), needs_jq]


def main(tmp_path, *, corrupt: bool):
    dest = bundle(tmp_path)
    if corrupt:
        (dest / knowledge.SMART_NAME).write_bytes(b"!" + SMART[1:])      # same size, another hash
    root = sysroot(tmp_path, {"sda": ["sda1"], "sdb": ["sdb1", "sdb2"]})
    (root / "sys" / "class" / "net" / "eth0").mkdir(parents=True)
    (root / "sys" / "class" / "net" / "eth0" / "address").write_text("aa:bb:cc:dd:ee:01\n")
    (root / "sys" / "class" / "graphics" / "fb0").mkdir(parents=True)
    script = (blkid_stub({"sdb1": "IMGSRV_USB", "sdb2": "IMGSRV_DATA", "sda1": "Windows"})
              + "mount() { return 0; }; "            # the data partition 'mounts': USB_DATA_DIR is the directory the test filled
              + GUI_UNTIL_EXIT
              + "AGENT_VERSION=v0.90.0; "
              # what the boot diag would read: the tables in force at the moment it runs
              + 'usb_diag() { echo "$1 kb=$SMART_KB" >> "$RUN_DIR/diag.calls"; log "usbclone: diag ran"; }; '
              + 'lc_session() { LC_END=back; }; usbclone_main')
    out = run(tmp_path, script, SYSROOT=posix(root), IMAGESRV_MODE="usbclone", IMAGESRV_TEST_ONCE="1",
              USB_FIND_S="0", USB_DATA_DIR=posix(tmp_path / "usbdata"))
    assert out.returncode == 0, out.stdout + out.stderr
    run_dir = tmp_path / "run"
    return (run_dir / "usb.notice").read_text(encoding="utf-8"), (run_dir / "agent.log").read_text(encoding="utf-8"), \
        (run_dir / "diag.calls").read_text(), posix(run_dir / "usb.sync.d")


def test_a_failed_check_is_on_the_screen_and_the_diag_still_reads_the_sticks_own_tables(tmp_path):
    notice, log, diag, adopted = main(tmp_path, corrupt=True)
    assert "server-sync" in notice and "smart-knowledge.tsv" in notice, notice
    assert adopted not in diag and diag.startswith("boot kb="), diag
    assert log.index("usbclone: server-sync:") < log.index("usbclone: diag ran")


def test_the_check_runs_before_the_diag_so_the_diag_reads_the_verified_tables(tmp_path):
    notice, log, diag, adopted = main(tmp_path, corrupt=False)
    assert notice.strip() == "", notice
    assert f"boot kb={adopted}" in diag, diag
    assert log.index("usbclone: server-sync:") < log.index("usbclone: diag ran")
