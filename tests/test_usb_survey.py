"""#1914/#1921 -- the survey stick (Nadav 20/10, night): build_usb_clone.sh --survey.

* the stick is told by a marker on its boot partition (/imagesrv/survey), not by a
  kernel word: the kernel line stays imagesrv.mode=usbclone alone (principle 2);
* on it, every boot keeps survey/<stamp>-<drawer>-<hash>.txt for each disk that is
  not the stick: smartctl -x (and -d sat when -x gave nothing), -H and its exit
  code, nvme smart-log, the link, the controller, the machine, the screen's verdict;
* never the serial: hash = sha256(serial)[:12]; a serial from VPD, smartctl or the
  wwid is scrubbed, and when it cannot be shown gone the raw output is not kept;
* the regular stick keeps nothing; a full data partition is said, never a crash.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

from test_cloner_gui import BASH, posix
from test_usb_clone import REPO, blkid_stub, needs_jq, run, sysroot

import pytest
from native import requires_native

pytestmark = requires_native(("bash", BASH))

SERIAL = "Z3TABCDE"           # sda: sysfs, VPD 0x80, the t10 wwid and smartctl all carry it
NVME_SERIAL = "S4EWNX0R123456"
GONE = "הוסר — לא ניתן לוודא שאין בו serial"

SMARTCTL = r'''smartctl() {
    eval "_l=\${$#}"
    case "$*" in *-H*) _h=1 ;; *) _h=0 ;; esac
    case "$_l" in
        */sda) echo "=== START OF INFORMATION SECTION ==="; echo "Device Model:     ST500DM002-1BD142"
               echo "Serial Number:    Z3TABCDE"; echo "LU WWN Device Id: 5 000c50 0a1b2c3d4"
               echo "=== START OF READ SMART DATA SECTION ==="
               echo "SMART overall-health self-assessment test result: PASSED"
               [ "$_h" = 1 ] || echo "  5 Reallocated_Sector_Ct   0x0033   100   100   036    Pre-fail  Always       -       0"
               [ "$_h" = 1 ] || echo "Device Statistics (GP Log 0x04) Z3TABCDE"   # the serial where no rule expects it
               return 0 ;;
        */nvme0n1) echo "=== START OF INFORMATION SECTION ==="; echo "Model Number:   Samsung SSD 970 EVO 500GB"
                   echo "Serial Number:  S4EWNX0R123456"; echo "Namespace 1 IEEE EUI-64:  002538 5a91b2c3d4"
                   echo "=== START OF SMART DATA SECTION ==="; echo "SMART overall-health self-assessment test result: PASSED"
                   return 0 ;;
        */sdc) echo "Device serial XYZSECRET1 (a form no rule knows)"; return 2 ;;   # opens nothing, names itself
    esac
    return 2
}; '''


def machine(tmp_path: Path, stick_serial: bool = True) -> Path:
    root = sysroot(tmp_path, {"sda": ["sda1"], "nvme0n1": ["nvme0n1p1"], "sdb": ["sdb1", "sdb2"]})
    w = lambda rel, data: ((root / rel).parent.mkdir(parents=True, exist_ok=True),
                           (root / rel).write_bytes(data) if isinstance(data, bytes) else (root / rel).write_text(data, newline="\n"))
    w("sys/block/sda/device/serial", f"{SERIAL}\n")
    w("sys/block/sda/device/vpd_pg80", b"\x00\x80\x00\x08" + SERIAL.encode())
    w("sys/block/sda/device/wwid", f"t10.ATA     ST500DM002-1BD142                       {SERIAL}\n")
    w("sys/block/sda/device/model", "ST500DM002-1BD142\n"); w("sys/block/sda/size", "976773168\n")
    w("sys/block/sda/ro", "0\n")
    w("sys/block/nvme0n1/device/serial", f"{NVME_SERIAL}\n"); w("sys/block/nvme0n1/ro", "0\n")
    w("sys/block/nvme0n1/device/model", "Samsung SSD 970 EVO 500GB\n")
    if stick_serial:
        w("sys/block/sdb/device/serial", "STICK0001\n")
    w("sys/class/dmi/id/sys_vendor", "Hewlett-Packard\n"); w("sys/class/dmi/id/product_name", "HP Compaq 8100 Elite SFF PC\n")
    (tmp_path / "run").mkdir(exist_ok=True)
    (tmp_path / "run" / "usb-media.disks").write_text("sdb\n", newline="\n")
    return root


def survey(tmp_path: Path, root: Path, on: str = "1", extra: str = "") -> tuple[subprocess.CompletedProcess, Path]:
    data = tmp_path / "usbdata"; data.mkdir(exist_ok=True)
    (tmp_path / "run" / "usb.survey").write_text(on + "\n", newline="\n")
    out = run(tmp_path, SMARTCTL + extra + f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; '
              'usb_survey; echo "rc=$?"; echo "notice=$USB_SURVEY_NOTICE"',
              SYSROOT=posix(root), IMAGESRV_MODE="usbclone", SMARTCTL_MAX_S="10")
    return out, data


def files(data: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8", errors="replace") for p in (data / "survey").glob("*")} \
        if (data / "survey").exists() else {}


def h12(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def test_the_survey_keeps_a_file_per_disk_and_no_serial(tmp_path):
    out, data = survey(tmp_path, machine(tmp_path))
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    kept = files(data)
    names = sorted(kept)
    assert len(names) == 2, (names, out.stdout + out.stderr)   # sda and nvme0n1; the stick (sdb) is not one
    assert any(n.endswith(f"-x-{h12(SERIAL)}.txt") for n in names), names
    assert any(n.endswith(f"-x-{h12(NVME_SERIAL)}.txt") for n in names), names
    text = "".join(kept.values())
    for leak in (SERIAL, NVME_SERIAL, "STICK0001", "000c50 0a1b2c3d4", "5a91b2c3d4"):
        assert leak not in text, (leak, text)
    sda = next(v for k, v in kept.items() if h12(SERIAL) in k)
    for want in ("== smartctl -x (exit 0)", "== smartctl -H (exit 0)", "Serial Number: <serial>",
                 "LU WWN Device Id: <wwn>", "Device Statistics (GP Log 0x04) <serial>",
                 "SMART overall-health self-assessment test result: PASSED",
                 "machine: Hewlett-Packard HP Compaq 8100 Elite SFF PC", "model: ST500DM002-1BD142 · 465 GB",
                 "assessment (the screen): ירוק · SMART תקין", "SATA link: "):
        assert want in sda, (want, sda)
    assert "-d sat" not in sda   # -x gave data: no second try
    assert not any("sdb" in v.splitlines()[2] for v in kept.values())   # the stick is never a surveyed disk
    assert GONE not in text


def test_the_regular_stick_keeps_nothing(tmp_path):
    out, data = survey(tmp_path, machine(tmp_path), on="0")
    assert "rc=0" in out.stdout, out.stdout + out.stderr
    assert not (data / "survey").exists(), list(data.rglob("*"))


def test_no_marker_read_is_the_regular_stick(tmp_path):
    root = machine(tmp_path)
    (tmp_path / "run" / "usb.survey").unlink(missing_ok=True)
    data = tmp_path / "usbdata"; data.mkdir()
    out = run(tmp_path, SMARTCTL + f'USB_DATA_STATE=mounted; USB_DATA_DIR={posix(data)!r}; usb_survey; echo "rc=$?"',
              SYSROOT=posix(root), IMAGESRV_MODE="usbclone")
    assert "rc=0" in out.stdout and not (data / "survey").exists(), out.stdout + out.stderr


@pytest.mark.parametrize("marker,mount_rc,want", [(True, 0, "1"), (False, 0, "0"), (True, 32, "0")])
def test_the_marker_on_the_boot_partition_decides(tmp_path, marker, mount_rc, want):
    root = machine(tmp_path)
    mk = 'mkdir -p "$_m/imagesrv"; : > "$_m/imagesrv/survey"; ' if marker else ""
    stub = (blkid_stub({"sdb1": "IMGSRV_USB", "sdb2": "IMGSRV_DATA"})
            + f'mount() {{ eval "_m=\\${{$#}}"; [ {mount_rc} = 0 ] || return {mount_rc}; {mk}return 0; }}; '
            'umount() { rm -rf "$1/imagesrv"; }; ')
    out = run(tmp_path, stub + 'usb_survey_detect; echo "rc=$?"; cat "$RUN_DIR/usb.survey"', SYSROOT=posix(root))
    assert out.stdout.strip().splitlines()[-1] == want, out.stdout + out.stderr


def test_a_serial_that_cannot_be_shown_gone_keeps_no_raw_output(tmp_path):
    root = machine(tmp_path)
    root.joinpath("sys/block/sdc").mkdir(parents=True)   # no serial anywhere this side can read
    out, data = survey(tmp_path, root)
    kept = files(data)
    sdc = [v for k, v in kept.items() if k.endswith("-noserial.txt")]
    assert len(sdc) == 1, (sorted(kept), out.stdout + out.stderr)
    assert "XYZSECRET1" not in "".join(kept.values())
    assert GONE in sdc[0] and "smartctl -x (exit 2), -d sat (exit 2), -H (exit 2)" in sdc[0], sdc[0]
    assert "assessment (the screen): " in sdc[0]   # what is not raw stays


def test_a_full_data_partition_is_said_and_nothing_crashes(tmp_path):
    out, data = survey(tmp_path, machine(tmp_path),
                       extra='df() { printf "Filesystem 1024-blocks Used Available Capacity Mounted\\n/dev/sdb2 32768 32700 68 99%% /x\\n"; }; ')
    assert "rc=1" in out.stdout, out.stdout + out.stderr
    assert "notice=מחיצת הנתונים בדיסק-און-קי מלאה" in out.stdout, out.stdout
    assert files(data) == {}, files(data)


def test_without_a_data_partition_the_survey_says_so(tmp_path):
    root = machine(tmp_path)
    (tmp_path / "run" / "usb.survey").write_text("1\n")
    out = run(tmp_path, SMARTCTL + 'USB_DATA_STATE=failed; usb_survey; echo "rc=$?"; echo "notice=$USB_SURVEY_NOTICE"',
              SYSROOT=posix(root), IMAGESRV_MODE="usbclone")
    assert "rc=1" in out.stdout and "notice=אין מחיצת נתונים זמינה" in out.stdout, out.stdout + out.stderr


def test_the_screen_is_told_it_is_the_survey_stick(tmp_path):
    (tmp_path / "run").mkdir()
    (tmp_path / "run" / "usb.survey").write_text("1\n")
    out = run(tmp_path, 'LC="$RUN_DIR/lc"; GUI_DIR="$RUN_DIR/gui"; mkdir -p "$LC" "$GUI_DIR"; echo local > "$LC/screen"; '
              'lc_gui_state; cat "$GUI_DIR/state"', IMAGESRV_MODE="usbclone")
    assert "lc_survey=1" in out.stdout.splitlines(), out.stdout + out.stderr
    gui = (REPO / "native-gui" / "src")
    assert '"lc_survey"' in (gui / "lc_state.c").read_text(encoding="utf-8")
    assert "דיסק-און-קי · איסוף נתונים" in (gui / "screens.c").read_text(encoding="utf-8")
    assert "הדיסק-און-קי הזה שומר נתוני SMART של כל דיסק (בלי מספר סידורי)" in \
        (gui / "screens_local.c").read_text(encoding="utf-8")


@needs_jq   # usb_answer
def test_usbclone_main_surveys_on_the_survey_stick_and_reaches_the_screen(tmp_path):
    root = machine(tmp_path)
    (root / "sys" / "class" / "graphics" / "fb0").mkdir(parents=True)   # #1937: a screen to draw on
    data = tmp_path / "usbdata"
    stub = (SMARTCTL + blkid_stub({"sdb1": "IMGSRV_USB", "sdb2": "IMGSRV_DATA", "sda1": "Windows"})
            + 'mount() { eval "_m=\\${$#}"; mkdir -p "$_m"; case "$_m" in *usbboot) mkdir -p "$_m/imagesrv"; '
              ': > "$_m/imagesrv/survey" ;; esac; return 0; }; umount() { rm -rf "$1/imagesrv"; }; '
            'gui_cloner_parent() { sleep 30 & _gui_pid=$!; }; '
            'lc_session() { echo "SESSION survey=$(cat "$RUN_DIR/usb.survey")"; LC_END=back; }; usbclone_main')
    out = run(tmp_path, stub, SYSROOT=posix(root), IMAGESRV_MODE="usbclone", IMAGESRV_TEST_ONCE="1",
              USB_FIND_S="0", USB_DATA_DIR=posix(data), SMARTCTL_MAX_S="10")
    assert "SESSION survey=1" in out.stdout, out.stdout + out.stderr   # the clone screen comes up as always
    assert len(files(data)) == 2, (files(data), out.stdout + out.stderr)


# --- the build --------------------------------------------------------------------------------

BUILD = REPO / "tools" / "build_usb_clone.sh"


def test_the_survey_stick_is_never_published(tmp_path):
    out = subprocess.run([BASH, posix(BUILD), "--out", posix(tmp_path / "o"), "--survey", "--publish", posix(tmp_path / "d")],
                         capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
                         stdin=subprocess.DEVNULL)
    assert out.returncode == 1 and "--survey is never published" in out.stderr, out.stdout + out.stderr


def test_the_survey_build_marks_names_and_sizes_its_stick():
    text = BUILD.read_text(encoding="utf-8")
    assert 'mcopy -i "$ESP" "$STAGE/survey" ::/imagesrv/survey' in text
    assert 'NAME="imagesrv-usb-survey-$VERSION.img"' in text
    assert '[[ "$SURVEY" == 0 ]] || DATA_MB=128' in text
    assert '[[ "$got_mark" == "$SURVEY" ]]' in text   # read back from the image: there on survey, absent on regular
    assert "survey" not in (REPO / "tools" / "usb" / "grub.cfg.in").read_text(encoding="utf-8")   # principle 2
