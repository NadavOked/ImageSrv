"""גואי שלא עלה: האתחול הבא הולך לדיסק המקומי, והשרת שומע (נדב, 11/10).

‏`die_local` הוא אתחול, לא ירידה מובטחת לדיסק: מחשב build/classroom בלי
משימה עולה אוטומטית ל-ImageSrv (#641), ושומר הלולאה של השרת אינו סופר
אתחולים בלי הקשר — כך גואי שנכשל בכל אתחול היה לולאה. לפני ה-`die_local`:

* ‏`boot_next_local` (‏localboot.sh) מכוון BootNext לרשומת Boot#### של דיסק
  מקומי (‏HD(...), לא רשת), ‏**ומאמת בקריאה חוזרת** (עיקרון 5). נכשל = נאמר.
* ‏`gui_failed_report` שולח hello אחד עם ``gui_failed`` (screen, boot_next,
  detail).
* המסך אומר אם האתחול הבא כוון, ואם לא — למה.

‏efibootmgr מזויף ב-PATH, ו-SYSROOT מצביע על עץ efivars מזויף — הטסט
**לעולם** אינו נוגע ב-NVRAM של המכונה המריצה.

בקרה שלילית: על 8eda4eff ‏localboot.sh אינו קיים, ו-gui_unavailable_local
מאתחל בלי BootNext ובלי דיווח.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from test_agent import AGENT, BASH, REPO, posix

pytestmark = pytest.mark.skipif(BASH is None, reason="needs bash")

TABLE = """BootCurrent: 0003
Timeout: 0 seconds
BootOrder: 0003,0001,0002
Boot0001* Windows Boot Manager\tHD(1,GPT,6c1d...,0x800,0x32000)/File(\\EFI\\Microsoft\\Boot\\bootmgfw.efi)
Boot0002* UEFI: USB stick\tPciRoot(0x0)/USB(0x14,0x0)
Boot0003* UEFI PXEv4 (MAC:3C5282A1001F)\tPciRoot(0x0)/Pci(0x1f,0x6)/MAC(3c5282a1001f,0)/IPv4(0.0.0.0)
"""

NETWORK_ONLY = """BootOrder: 0003
Boot0003* UEFI PXEv4\tPciRoot(0x0)/MAC(3c5282a1001f,0)/IPv4(0.0.0.0)
"""

EFIBOOTMGR = """#!/bin/sh
d="$(dirname "$0")"
echo "efibootmgr $*" >> "$d/calls"
case "$1" in
  -v) cat "$d/table" ;;
  -n) [ -f "$d/ignore_n" ] || echo "$2" > "$d/next" ;;
  "") cat "$d/table"; [ -f "$d/next" ] && echo "BootNext: $(cat "$d/next")" ;;
esac
exit 0
"""


def run(tmp_path: Path, body: str, table: str = TABLE, efi: bool = True,
        ignore_n: bool = False) -> tuple[str, int, Path]:
    box = tmp_path / "box"
    stub = box / "bin"; stub.mkdir(parents=True)
    (stub / "efibootmgr").write_text(EFIBOOTMGR, newline="\n")
    (stub / "table").write_text(table, newline="\n")
    if ignore_n:
        (stub / "ignore_n").write_text("", newline="\n")
    sysroot = box / "root"
    if efi:
        (sysroot / "sys/firmware/efi/efivars").mkdir(parents=True)
        (sysroot / "sys/firmware/efi/efivars/BootOrder-8be4df61").write_bytes(b"\x07")
    else:
        sysroot.mkdir()
    rd = box / "run"; rd.mkdir()
    script = (
        f'chmod +x {shlex.quote(posix(stub))}/efibootmgr; '
        f'export PATH="$(cd {shlex.quote(posix(stub))} && pwd):$PATH"; '
        f"export SYSROOT={shlex.quote(posix(sysroot))} RUN_DIR={shlex.quote(posix(rd))} "
        f"LOG_FILE={shlex.quote(posix(rd / 'agent.log'))} SERVER=http://x MAC=aa:bb IMAGESRV_TEST=1; "
        f". {posix(AGENT)}/lib/core/common.sh; . {posix(AGENT)}/lib/core/waits.sh; "
        f". {posix(AGENT)}/lib/gui/ui.sh; . {posix(AGENT)}/lib/gui/localboot.sh; "
        f". {posix(AGENT)}/lib/gui/guiparent.sh; "
        'sleep() { :; }; '
        'build_hello() { printf %s "{\\"mac\\":\\"$MAC\\",\\"joining\\":$1}"; }; '
        'http_post_json() { cat "$2" >> "$RUN_DIR/hellos"; echo >> "$RUN_DIR/hellos"; return 0; }; '
        + body
    )
    proc = subprocess.run([BASH, "-c", 'export PATH="/usr/bin:$PATH"; ' + script],
                          capture_output=True, text=True, encoding="utf-8",
                          cwd=str(REPO), stdin=subprocess.DEVNULL, timeout=60)
    return proc.stdout + proc.stderr, proc.returncode, box


def hellos(box: Path) -> list[dict]:
    f = box / "run" / "hellos"
    return [json.loads(line) for line in f.read_text(encoding="utf-8").splitlines() if line] if f.exists() else []


def test_boot_next_goes_to_the_local_disk_and_is_read_back(tmp_path):
    """BootOrder מתחיל ב-PXE (0003); הרשומה המקומית הראשונה היא 0001."""
    out, _, box = run(tmp_path, 'boot_next_local; echo "rc=$?"')
    assert "set:Boot0001" in out and "rc=0" in out, out
    assert "efibootmgr -n 0001" in (box / "bin" / "calls").read_text()


def test_a_write_that_did_not_read_back_is_a_failure(tmp_path):
    out, _, _ = run(tmp_path, 'boot_next_local; echo "rc=$?"', ignore_n=True)
    assert "failed:BootNext נקרא בחזרה ''" in out and "rc=1" in out, out


def test_no_local_disk_entry_is_said(tmp_path):
    out, _, box = run(tmp_path, 'boot_next_local; echo "rc=$?"', table=NETWORK_ONLY)
    assert "failed:אין רשומת Boot#### של דיסק מקומי" in out and "rc=1" in out, out
    assert "-n" not in (box / "bin" / "calls").read_text()


def test_legacy_bios_is_skipped_by_name(tmp_path):
    out, _, box = run(tmp_path, 'boot_next_local; echo "rc=$?"', efi=False)
    assert "skipped:אין efivars" in out and "rc=1" in out, out
    assert not (box / "bin" / "calls").exists()


def test_the_gui_failure_points_the_next_boot_and_tells_the_server(tmp_path):
    out, rc, box = run(tmp_path, "gui_unavailable_local build")
    assert rc == 86, out
    assert "Booting from the local disk." in out
    assert "TEST-REBOOT: native GUI unavailable (build screen)" in out
    (h,) = hellos(box)
    assert h["joining"] is False
    assert h["gui_failed"] == {"screen": "build", "boot_next": "set", "detail": "Boot0001"}


def test_a_boot_next_that_failed_is_on_the_screen_and_in_the_report(tmp_path):
    out, rc, box = run(tmp_path, "gui_unavailable_local recovery", table=NETWORK_ONLY)
    assert rc == 86
    assert "could NOT be pointed at the local disk" in out
    assert "server console journal" in out        # the cause is Hebrew: it goes to the journal, not to the ASCII tty
    assert out.isascii(), out
    (h,) = hellos(box)
    assert h["gui_failed"]["detail"] == "אין רשומת Boot#### של דיסק מקומי"
    assert h["gui_failed"]["screen"] == "recovery"
    assert h["gui_failed"]["boot_next"] == "failed"


def test_an_undelivered_report_is_said_and_the_reboot_goes_ahead(tmp_path):
    out, rc, box = run(tmp_path, 'http_post_json() { return 22; }; gui_unavailable_local build')
    assert rc == 86
    assert "GUI failure NOT reported" in out
