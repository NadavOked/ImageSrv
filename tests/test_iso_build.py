"""‏ISO ההתקנה (#1139): הרשימה האחת, ה-preseed, ומה ש-firstboot אסור לו.

הלקח של R25: ההתקנה מה-ISO רצה **בלי אינטרנט**, ולכן חבילה שנוספה
ל-`PKGS` של המתקין או לרשימות `build_initramfs.sh` ולא ל-`tools/iso/
packages.txt` מתגלה רק מול שרת אמיתי, כשה-pool כבר נצרב. הטסט כאן קורא
את **המקורות עצמם** (לא עותק) ונופל בשני הכיוונים — חבילה שחסרה
ב-packages.txt, וחבילה ב-packages.txt שאין לה מקור.

ועוד שלושה חוזים שאסור שיזוזו בשקט:
  מפעיל את late-command.sh, root בלבד, הדיסק נבחר ב-early_command.
* ‏firstboot.sh לעולם אינו מעביר `--deploy-if` (R25 §2.5: זה המוקש —
  ‏dnsmasq שנדלק על כרטיס שנוחש), ובונה עם `--skip-apt`.
* ‏#1190: אין מתקין דביאן — ה-ISO עולה ל-/live (הקרנל + initramfs המתקין החי) עם
  imagesrv.mode=installer בלבד; firstboot משלים מהתשובות בלי אשף.
  ו-firstboot.sh קורא אותן — לא heuristic (R62).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_installer_packages import installer_pkgs

REPO = Path(__file__).resolve().parent.parent
ISO_DIR = REPO / "tools" / "iso"
INITRAMFS = REPO / "tools" / "build_initramfs.sh"
PACKAGES_TXT = ISO_DIR / "packages.txt"
FIRSTBOOT = ISO_DIR / "firstboot.sh"
FIRSTBOOT_ANSWERS = ISO_DIR / "firstboot-answers.sh"
PAYLOAD_BUILD = REPO / "tools" / "boot-payload-build.sh"
INSTALLER_BOOT = REPO / "agent" / "lib" / "core" / "installer_boot.sh"
LIVE_CONSOLE = REPO / "installer" / "imagesrv-installer"
WIZARD_SERVICE = REPO / "deploy" / "imagesrv-wizard.service"
WIZARD_RERUN_SERVICE = REPO / "deploy" / "imagesrv-wizard-rerun.service"
INSTALLER_GUI_SERVICE = REPO / "deploy" / "imagesrv-installer-gui.service"
FIRSTBOOT_SERVICE = ISO_DIR / "imagesrv-firstboot.service"
BUILD_ISO = ISO_DIR / "build-iso.sh"
LAB_GATE = ISO_DIR / "initrd-lab-gate.sh"
GRUB_TEMPLATE = ISO_DIR / "grub.cfg.in"
ISOLINUX_MENU_TEMPLATE = ISO_DIR / "isolinux-menu.cfg.in"
ISOLINUX_ENTRY_TEMPLATE = ISO_DIR / "imagesrv.cfg.in"
BASH_SCRIPTS = ("make-pool.sh", "build-iso.sh", "firstboot.sh", "firstboot-answers.sh", "test-iso.sh")

#: מה שרק ה-ISO מוסיף — אין לו מקור בקבצים האחרים, ולכן מוצהר כאן.
#: ‏ca-certificates/systemd-timesyncd: מה ש-d-i היה מביא ב-standard ו-debootstrap לא (ESXi 21/09).
#: ‏dbus ו-linux-image-amd64 עברו ל-PKGS של המתקין (‏#1987) — יש להם מקור שם.
ISO_ONLY = {"nftables", "sqlite3", "ca-certificates", "chrony"}
#: תיקיית קושחה ש-build_initramfs.sh דורש (#1125) → החבילה שמביאה אותה בדביאן 13.
FIRMWARE_PACKAGE = {"rtl_nic": "firmware-realtek", "i915": "firmware-intel-graphics"}
#: ‏#1820 (הכרעת נדב 31/10: "צריך לכלול הכול"): חבילת קושחה של דביאן 13 → מודול
#: קווי מ-drivers/net/ethernet שמבקש ממנה קובץ (`modinfo -F firmware`, ‏6.12.113, ‏31/10).
#: שרת עם כרטיס כזה עלה מה-ISO בלי רשת, כי ה-ISO נשא רק את rtl_nic.
WIRED_NIC_FIRMWARE = {
    "firmware-bnx2": "bnx2", "firmware-bnx2x": "bnx2x", "firmware-misc-nonfree": "tg3",
    "firmware-intel-misc": "ice", "firmware-qlogic": "qed", "firmware-netxen": "netxen_nic",
    "firmware-myricom": "myri10ge", "firmware-netronome": "nfp", "firmware-cavium": "liquidio",
    "firmware-linux-free": "pcnet_cs", "firmware-realtek": "r8169",
}


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.split("\n"))


def packages_txt() -> set[str]:
    return set(_strip_comments(PACKAGES_TXT.read_text(encoding="utf-8")).split())


def _paren_list(text: str, opener: str) -> list[str]:
    """התוכן של `NAME=( ... )` בלי הערות; הערות עלולות להכיל סוגריים."""
    body = text.split(opener, 1)[1]
    names: list[str] = []
    for line in body.split("\n"):
        code = line.split("#", 1)[0]
        done = ")" in code
        names += code.split(")", 1)[0].split()
        if done:
            break
    return names


def _continued_command(text: str, first_line: str) -> list[str]:
    """הטוקנים של פקודה שממשיכה ב-`\\` מהשורה שמכילה `first_line`."""
    lines = text.split("\n")
    start = next(i for i, l in enumerate(lines) if first_line in l)
    tokens: list[str] = []
    for line in lines[start:]:
        code = line.split("#", 1)[0].rstrip()
        cont = code.endswith("\\")
        tokens += code.rstrip("\\").split()
        if not cont:
            break
    return tokens


def initramfs_lists() -> dict[str, set[str]]:
    """ארבע הרשימות של build_initramfs.sh, מהקובץ עצמו."""
    text = INITRAMFS.read_text(encoding="utf-8")
    base = _continued_command(text, "apt-get install -y --no-install-recommends \\")
    gui_build = _continued_command(text, "apt-get install -y --no-install-recommends make pkg-config")
    tool_pkgs = {
        m.group(1) for m in re.finditer(r"\[[\w.-]+\]=([\w.+-]+)", text.split("declare -A TOOL_PKG=(", 1)[1].split(")", 1)[0])
    }
    installer_pkgs = {
        m.group(1) for m in re.finditer(r"\[[\w.-]+\]=([\w.+-]+)", text.split("declare -A INSTALLER_PKG=(", 1)[1].split(")", 1)[0])
    }
    return {
        "build_initramfs.sh INSTALLER_PKG": installer_pkgs,
        "build_initramfs.sh base apt list": set(base[3:]) - {"apt-get", "install", "-y", "--no-install-recommends"},
        "build_initramfs.sh GUI_PACKAGES": set(_paren_list(text, "\nGUI_PACKAGES=(")),
        "build_initramfs.sh GUI build deps": set(gui_build) - {"apt-get", "install", "-y", "--no-install-recommends"},
        "build_initramfs.sh TOOL_PKG": tool_pkgs,
    }


def test_the_sources_were_actually_parsed() -> None:
    """פרסור שהחזיר רשימה ריקה היה הופך את הטסט הבא לירוק על כלום."""
    lists = initramfs_lists()
    for name, pkgs in lists.items():
        assert len(pkgs) >= 5, f"{name}: נקראו {sorted(pkgs)} — הפרסור נשבר"
    assert "busybox-static" in lists["build_initramfs.sh base apt list"]
    assert "fonts-ibm-plex" in lists["build_initramfs.sh GUI_PACKAGES"]
    assert "libpango1.0-dev" in lists["build_initramfs.sh GUI build deps"]
    assert "testdisk" in lists["build_initramfs.sh TOOL_PKG"]
    assert "python3-fastapi" in installer_pkgs()


def vmupdate_pkgs() -> set[str]:
    """‏#1334: deploy/vmupdate-packages.txt — המקור היחיד של חבילות עדכון ה-VM
    (ההתקנה והעדכון מהכפתור מתקינים ממנו דרך deploy/vmupdate-host.sh)."""
    text = (REPO / "deploy" / "vmupdate-packages.txt").read_text(encoding="utf-8")
    return set(_strip_comments(text).split())


def test_packages_txt_is_the_union_of_every_apt_list() -> None:
    have = packages_txt()
    sources = {"setup-boot-server.sh PKGS": set(installer_pkgs()), **initramfs_lists(),
               "deploy/vmupdate-packages.txt": vmupdate_pkgs()}
    missing = {name: sorted(pkgs - have) for name, pkgs in sources.items() if pkgs - have}
    assert not missing, f"חסר ב-tools/iso/packages.txt (ההתקנה offline תיכשל עליהן): {missing}"


def test_every_package_in_packages_txt_has_a_source() -> None:
    """הכיוון ההפוך: שורה ב-packages.txt שאין לה מקור היא רשימה שנסחפה."""
    known = set().union(*initramfs_lists().values(), installer_pkgs(), vmupdate_pkgs(), ISO_ONLY,
                        FIRMWARE_PACKAGE.values(), WIRED_NIC_FIRMWARE)
    orphans = sorted(packages_txt() - known)
    assert not orphans, f"ב-packages.txt בלי מקור ובלי הצהרה ב-ISO_ONLY: {orphans}"


def test_the_iso_only_packages_are_declared_in_packages_txt() -> None:
    missing = sorted(ISO_ONLY - packages_txt())
    assert not missing, f"חסר ב-packages.txt: {missing}"
    assert "linux-image-cloud-amd64" not in packages_txt(), "קרנל cloud — build_initramfs.sh מסרב לו (#904)"


def firmware_gate_dirs() -> set[str]:
    """התיקיות ש-build_initramfs.sh עוצר בלעדיהן: FIRMWARE_DIRS=(...) והצטרפות i915."""
    text = INITRAMFS.read_text(encoding="utf-8")
    dirs = {name.strip("\"'") for name in _paren_list(text, "\nFIRMWARE_DIRS=(")}
    dirs.update(re.findall(r"FIRMWARE_DIRS\+=\((\w+)\)", text))
    return dirs


def test_firmware_the_initramfs_gate_demands_is_on_the_iso() -> None:
    """‏#1125 הפך תיקיית קושחה חסרה לכישלון בנייה; שרת שהותקן מה-ISO בלי
    ‏firmware-realtek נפל payload-failed בשלב א' של האתחול הראשון (QEMU, 19/09)."""
    dirs = firmware_gate_dirs()
    assert {"rtl_nic", "i915"} <= dirs, dirs
    unknown = sorted(dirs - FIRMWARE_PACKAGE.keys())
    assert not unknown, f"תיקיית קושחה בלי חבילה ידועה ב-FIRMWARE_PACKAGE: {unknown}"
    missing = sorted({FIRMWARE_PACKAGE[d] for d in dirs} - packages_txt())
    assert not missing, f"build_initramfs.sh ידרוש קושחה שה-ISO לא מתקין: {missing}"


def test_wired_nic_firmware_is_on_the_iso_and_in_the_notices_1820() -> None:
    """‏#1820: שרת עם Broadcom NetXtreme II (bnx2) עלה מה-ISO בלי רשת, כי ה-ISO נשא
    רק firmware-realtek. כל חבילת קושחה לכרטיס קווי חייבת להיות ב-packages.txt
    (ההתקנה offline מתקינה את כולה), ורשומה ב-THIRD_PARTY_NOTICES.md (#1707)."""
    missing = sorted(WIRED_NIC_FIRMWARE.keys() - packages_txt())
    assert not missing, f"קושחת רשת קווית חסרה ב-packages.txt (#1820): {missing}"
    notices = (REPO / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    unlisted = sorted(p for p in WIRED_NIC_FIRMWARE if f"`{p}`" not in notices)
    assert not unlisted, f"חבילת קושחה ב-ISO בלי שורה ב-THIRD_PARTY_NOTICES.md (#1707): {unlisted}"
    assert "firmware-linux-nonfree" not in packages_txt(), "#1820: לא את המטא-חבילה השלמה"


def test_no_ssh_server_on_the_product_server() -> None:
    """הכרעת נדב 19/09: אין SSH על השרת האמיתי — כלי מעבדה, מותקן ידנית.
    ‏ISO שמביא sshd הוא ISO שמפר את ההכרעה בשקט."""
    assert "openssh-server" not in packages_txt()
    for name in ("firstboot.sh", "firstboot-answers.sh", "imagesrv-firstboot.service"):
        code = _strip_comments((ISO_DIR / name).read_text(encoding="utf-8"))
        assert not re.search(r"\bsshd?\b|openssh", code), f"{name} נוגע ב-SSH מחוץ להערה"


def test_build_iso_ships_the_live_installer_and_no_debian_installer() -> None:
    """‏#1190: המטען הוא /live/vmlinuz + /live/initrd.img (build_initramfs --installer
    --with-gui), הקרנל מה-pool ומודולי ה-initramfs מושווים אליו, אין גיבוב root
    (‏#1632 — נבחר במסך ההתקנה), ומתקין דביאן (install.amd, preseed) אינו על ה-ISO."""
    build = BUILD_ISO.read_text(encoding="utf-8")
    assert "--live-initrd" in build and 'die "חסר --live-initrd' in build
    assert 'dpkg-deb -x "$kdeb"' in build and '"$ikver" == "$KVER"' in build, "אין השוואת קרנל↔מודולים"
    assert 'install -m 0644 "$kvmlinuz" "$ISO_TREE/live/vmlinuz"' in build
    assert 'install -m 0644 "$LIVE_INITRD" "$ISO_TREE/live/initrd.img"' in build
    # #1632: root's password is chosen on the install screen -- no hash on the ISO
    assert "root-password.hash" not in build and "openssl passwd" not in build
    assert 'rm -rf "$ISO_TREE/install.amd"' in build
    assert 'DI_ARGS="imagesrv.mode=installer"' in build, "שורת הקרנל: רק המצב (עיקרון 2)"
    assert "preseed/file=" not in build and "late-command" not in build.replace("late-command.sh מתקין", "")
    assert '-volid "$VOLID"' in build and 'VOLID="IMAGESRV_INSTALL"' in build, "init מוצא את המדיה לפי התווית"
    assert "-boot_image any replay" in build, "בלי replay ה-shim/GRUB החתומים לא נשמרים (R57)"
    assert "-report_el_torito" in build
    assert not (ISO_DIR / "preseed.cfg").exists() and not (ISO_DIR / "late-command.sh").exists()


@requires_native("bash", "cpio", "gzip")
def test_lab_initrd_requires_flag_and_is_marked(tmp_path: Path) -> None:
    root = tmp_path / "root"
    hook = root / "etc" / "imagesrv"
    hook.mkdir(parents=True)
    (hook / "authorized_keys").write_text("lab-key\n", encoding="utf-8")
    initrd = tmp_path / "lab.img"
    command = 'find . -print | cpio -o -H newc 2>/dev/null | gzip -n > "$1"'
    subprocess.run(["bash", "-c", command, "build", str(initrd)], cwd=root, check=True,
                   stdin=subprocess.DEVNULL)
    refused = subprocess.run(["bash", str(LAB_GATE), str(initrd)],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert refused.returncode != 0 and "--lab" in refused.stderr
    allowed = subprocess.run(["bash", str(LAB_GATE), str(initrd), "--lab"],
                             capture_output=True, text=True, stdin=subprocess.DEVNULL)
    assert allowed.returncode == 0 and allowed.stdout.strip() == "true"
    build = BUILD_ISO.read_text(encoding="utf-8")
    assert 'TAG="$TAG-lab"' in build and '"lab_overlay":' in build


def test_build_iso_proves_every_requested_package_is_in_the_merged_pool() -> None:
    """#1162: the build must inspect Package fields and name every missing package.

    Negative control for the Linux Testrunner: delete one requested package's
    .deb from both the supplied pool and netinst tree, then build; build-iso
    must exit nonzero and print that package name before xorriso packs the ISO.
    """
    build = BUILD_ISO.read_text(encoding="utf-8")
    merge = build.index('cp -a "$POOL/pool/." "$ISO_TREE/pool/"')
    pack = build.index('xorriso -indev "$NETINST" -outdev "$ISO"')
    guard = build[merge:pack]
    assert 'dpkg-deb -f "$deb" Package' in guard
    assert 'find "$ISO_TREE/pool" -type f -name \'*.deb\' -print0' in guard
    assert 'grep -Fxq "$p" "$POOL_PACKAGES" || missing+=("$p")' in guard
    assert 'חבילות מ-packages.txt חסרות מה-pool הממוזג: ${missing[*]}' in guard


def test_grub_template_boots_the_live_installer_only() -> None:
    grub = GRUB_TEMPLATE.read_text(encoding="utf-8")
    entries = re.findall(r"^menuentry .*", grub, re.MULTILINE)
    assert entries == ["menuentry --hotkey=m 'ImageSrv server install' {"]   # #1302
    assert "set timeout=0" in grub, "‏#1190: ישר למתקין, בלי תפריט"
    assert "set default=0" in grub
    assert "ImageSrv @TAG@ installer" in grub
    shown = [line for line in grub.splitlines() if not line.lstrip().startswith("#")]
    assert not [line for line in shown if "ImageCtl" in line], "#1302: no old name on the boot screen"
    entry = ISOLINUX_ENTRY_TEMPLATE.read_text(encoding="utf-8")
    assert "menu label I^mageSrv server install" in entry.splitlines()[1]   # BIOS: the same name
    assert "set color_normal=white/black" in grub and "background_color '#1b2a41'" in grub
    assert all(word not in grub for word in ("Graphical", "Advanced", "Accessible", "install.amd", "secondary"))
    assert "linux    /live/vmlinuz @DI_ARGS@ --- quiet" in grub
    assert "initrd   /live/initrd.img" in grub


def test_isolinux_templates_boot_the_live_installer_only() -> None:
    menu = ISOLINUX_MENU_TEMPLATE.read_text(encoding="utf-8").splitlines()
    assert menu == ["include stdmenu.cfg", "include imagesrv.cfg", "timeout 1"], "‏#1190: ישר למתקין גם ב-BIOS"
    entries = ISOLINUX_ENTRY_TEMPLATE.read_text(encoding="utf-8")
    assert re.findall(r"^label (.+)$", entries, re.MULTILINE) == ["imagesrv"]
    assert entries.count("menu default") == 1
    assert "kernel /live/vmlinuz" in entries and "append initrd=/live/initrd.img @DI_ARGS@ --- quiet" in entries
    assert all(word not in entries for word in ("Graphical", "Advanced", "Accessible", "install.amd", "secondary"))


# --- firstboot -----------------------------------------------------------------

def test_firstboot_never_passes_deploy_if_builds_payload_and_starts_wizard() -> None:
    code = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    assert "--deploy-if" not in code, "כרטיס הפצה שנוחש = dnsmasq על הרשת הלא נכונה (R25 §2.5)"
    assert "setup-boot-server.sh" not in code, "שלב ב' שייך לאשף ואינו עוד התקנה לא-אינטראקטיבית"
    # ‏#1230: הבנייה עברה ל-tools/boot-payload-build.sh — אותו קוד שכפתור העדכון
    # מריץ. שני ה-initrd, ‏--skip-apt וה-epoch נבדקים בהרצה ב-test_boot_payload_1230.
    assert "tools/boot-payload-build.sh" in code and "--iso-default" in code
    assert '--write-flags "$ETC/initrd.flags"' in code, "העדכון בונה לפי מה ש-firstboot רשם"
    # ‏#1125: build_initramfs.sh נופל בלי SOURCE_DATE_EPOCH כשאין .git — ו-/opt/imagesrv-src
    # של בניית --source הוא git archive. הזמן מגיע ממניפסט ה-ISO (נמדד ב-QEMU 19/09).
    assert '--manifest "$ETC/iso-release.json"' in code
    helper = _strip_comments(PAYLOAD_BUILD.read_text(encoding="utf-8"))
    # ‏#1465: הסקריפט מזכיר את הדגל גם בתזמון המקבילי — סופרים רק בברירת המחדל של ה-ISO.
    iso_default = re.search(r'^    lines=\((.*?)\)$', helper, re.MULTILINE | re.DOTALL)
    assert iso_default, "boot-payload-build.sh: the ISO default lines=(...) not found"
    assert iso_default.group(1).count("--skip-apt") == 2, "שני initrd (טקסט + GUI), שניהם בלי apt — החבילות מה-ISO"
    assert '--source-date-epoch "$EPOCH"' in helper and "source_date_epoch" in helper
    assert "--with-gui" in helper
    assert 'systemctl start imagesrv-wizard' in code
    assert 'deploy/imagesrv-wizard.service' in code
    assert 'deploy/imagesrv-wizard-rerun.service' in code
    unit = WIZARD_SERVICE.read_text(encoding="utf-8")
    assert "After=network-online.target" in unit
    assert "--installer-nic /etc/imagesrv/installer-nic" in unit
    assert "--installer-role /etc/imagesrv/installer-role" in unit
    rerun_unit = WIZARD_RERUN_SERVICE.read_text(encoding="utf-8")
    assert "--rerun --host 0.0.0.0" in rerun_unit


def test_firstboot_reads_the_installer_facts_and_never_guesses_a_nic() -> None:
    code = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    assert "/etc/imagesrv" in code and "installer-nic" in code and "installer-role" in code
    for state in ("network-nic-undecidable", "check-error", "secondary-needs-primary"):
        assert state in code, f"המצב {state} אינו מדווח"
    assert "firstboot.status" in code
    # ה-heuristic של R25 — "הראשון עם IPv4" — אסור שיחזור.
    assert not re.search(r"addr show scope global.*\|\s*awk.*exit", code), "בחירת 'הראשון עם IPv4' חזרה"


def test_firstboot_owns_tty1_shows_progress_and_leaves_failure_visible() -> None:
    unit = FIRSTBOOT_SERVICE.read_text(encoding="utf-8")
    assert "Type=oneshot" in unit and "Restart=" not in unit
    assert "StandardOutput=journal+console" in unit and "StandardError=journal+console" in unit
    assert "Before=getty@tty1.service" in unit and "Conflicts=getty@tty1.service" in unit, \
        "firstboot must stop tty1 getty from racing its progress output"

    code = FIRSTBOOT.read_text(encoding="utf-8")
    assert "FIRSTBOOT_TTY=/dev/tty1" in code, "firstboot progress is not directed to tty1"
    assert 'printf \'ImageSrv: %s\\n\' "$*" > "$FIRSTBOOT_TTY"' in code
    assert "ImageSrv: first boot failed" in code, "#1302: the visible name on tty1"
    assert "Stage A: building boot files (several minutes)" in code
    assert code.count("preparing the server - Stage B:") == 2
    failure = code[code.index("fail() {"):code.index("[[ -f \"$STAMP\" ]]")]
    assert 'message="$*"' in failure
    assert 'message=%s\\n\' "$state" "$(date -u +%FT%TZ)" "$message"' in failure
    assert '"$state" "$message" > "$FIRSTBOOT_TTY"' in failure
    assert "Press Alt+F2 to log in on tty2." in failure
    assert "trap - ERR" in code and "trap 'unexpected_failure" in code
    assert "getty@tty2" not in code + unit


def test_installer_console_runtime_text_is_ascii_only() -> None:
    for script in (FIRSTBOOT, FIRSTBOOT_ANSWERS, PAYLOAD_BUILD, INSTALLER_BOOT, LIVE_CONSOLE):
        runtime_text = _strip_comments(script.read_text(encoding="utf-8"))
        assert runtime_text.isascii(), f"{script.name} has non-ASCII runtime text"


def test_firstboot_brings_up_dhcp_then_wizard_gui_and_dcui_fallback_on_the_headless_path() -> None:
    full = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    # ‏#1190: המסלול הראשון (תשובות) מדליק את ה-DCUI ויוצא; המסלול ללא תשובות
    # (שרת בלי מסך) מתחיל ב-NIC_FILE — בודקים את הסדר שם.
    code = full[full.index('NIC_FILE="$ETC/installer-nic"'):]
    dhcp = code.index('dhcpcd -4 -1 -t 20 "$nic"')
    wizard = code.index("systemctl start imagesrv-wizard")
    gui = code.index("systemctl start imagesrv-installer-gui.service")
    fallback = code.index("systemctl restart imagesrv-dcui.service")
    assert dhcp < wizard < gui < fallback
    assert 'for nic_path in /sys/class/net/*' in code
    assert 'temporary_dhcp "$nic" &' in code and 'wait "$dhcp_pid"' in code
    assert '/sys/class/net/$nic/carrier' in code and "sleep 1" in code
    assert "for _ in 1 2 3" in code  # ‏shellcheck SC2034: המשתנה אינו בשימוש
    for outcome in ("(dhcp)", "no carrier", "no dhcp offer"):
        assert outcome in code
    assert full.count('>>"$BUILD_LOG" 2>&1') >= 1 and '--build-log "$BUILD_LOG"' in full
    assert "WorkingDirectory=/opt/imagesrv-src" in code
    assert "imagesrv-dcui.service.d/firstboot.conf" in code
    assert 'systemctl mask getty@tty1.service' in code
    assert 'systemctl is-active --quiet imagesrv-dcui.service' in code
    assert "[[ -e /dev/fb0" in code
    assert "ID_INPUT_KEYBOARD=1" in code and "/dev/input/event*" in code
    assert 'installer gui: started on tty1' in code
    assert 'installer gui: no framebuffer/keyboard, DCUI only' in code


def test_native_installer_unit_owns_tty1_then_hands_it_to_dcui() -> None:
    unit = INSTALLER_GUI_SERVICE.read_text(encoding="utf-8")
    assert "Conflicts=getty@tty1.service imagesrv-dcui.service" in unit
    assert "Type=simple" in unit and "Restart=no" in unit
    assert "StandardInput=tty" in unit and "TTYPath=/dev/tty1" in unit
    assert "--screen install --wizard-cwd /opt/imagesrv-src" in unit
    assert "ExecStopPost=systemctl start imagesrv-dcui.service" in unit
    first = FIRSTBOOT.read_text(encoding="utf-8")
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert 'deploy/imagesrv-installer-gui.service' in first
    assert 'deploy/imagesrv-installer-gui.service' in setup
    assert "enable imagesrv-installer-gui" not in first + setup


def test_permanent_setup_stops_other_temporary_clients_and_removes_dcui_dropin() -> None:
    setup = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert 'dhcpcd -k "$temporary_nic"' in setup
    assert '"$temporary_nic" == "$SERVERS_IF"' in setup
    assert "rm -f /etc/systemd/system/imagesrv-dcui.service.d/firstboot.conf" in setup
    assert "systemctl restart imagesrv-dcui.service" in setup


# --- תחביר -------------------------------------------------------------------

@requires_native(("bash", shutil.which("bash") or shutil.which("bash.exe")))
@pytest.mark.parametrize("name", BASH_SCRIPTS)
def test_scripts_parse(name: str) -> None:
    bash = shutil.which("bash") or shutil.which("bash.exe")
    proc = subprocess.run([bash, "-n", str(ISO_DIR / name)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, f"{name}: {proc.stderr}"


def test_the_iso_source_is_the_public_repo_and_private_paths_stop_the_build() -> None:
    """‏20/09: ISO v0.51.0 הראשון נבנה מ-`git archive` של הריפו **הפרטי** —
    62 תשובות מחקר, `tools/agents/`, `.agents/`, `logs/` נסעו בתוך `imagesrv-src/`
    לכל שרת שיותקן. ברירת המחדל היא clone של הציבורי בתג, ושומר ב-ISO_TREE
    מסרב לארוז נתיבים פרטיים גם כשנותנים `--source` מקומי — אותה רשימה כמו
    HARD_DENY ב-publish-to-public.sh, ועוד מה שרק הפרטי מחזיק."""
    code = BUILD_ISO.read_text(encoding="utf-8")
    assert 'https://github.com/NadavOked/ImageSrv.git' in code, "ברירת המחדל אינה הריפו הציבורי"
    assert 'git clone -q --branch "$REF" --depth 1 "$PUBLIC_URL"' in code
    for deny in (".claude", ".agents", ".otogit", "tools/agents", "AGENTS.md", "logs", "docs/research"):
        assert f" {deny} " in code or f" {deny};" in code, f"השומר אינו מכסה {deny}"
    assert 'die "המקור אינו העץ הציבורי' in code


# --- #1185: כפתור העדכון משרת שהותקן מה-ISO --------------------------------
# השרת הראשון שהותקן מה-ISO (20/09) הציג "לא ידועה (אין תגית git על העץ)"
# ו-"[Errno 2] No such file or directory: 'git'": ה-ISO ארז את העץ ב-git
# archive (בלי .git) ולא נשא git. שלושת התנאים שהעדכון דורש נבדקים כאן
# בשמם, כי כל אחד מהם לבדו נכשל בשקט עד מול שרת אמיתי.


def test_the_installer_always_installs_git_so_the_update_button_works() -> None:
    assert "git" in installer_pkgs(), "git ב-PKGS של המתקין תמיד — לא רק כשמושכים קוד מהרשת"
    src = (REPO / "deploy" / "setup-boot-server.sh").read_text(encoding="utf-8")
    assert "PKGS+=(git)" not in src, "git מותנה בהיעדר server/main.py — מה-ISO הוא קיים ו-git לא הותקן"
    assert "git" in packages_txt(), "git חייב להיות ב-pool של ה-ISO (ההתקנה offline)"


def test_build_iso_packs_the_public_clone_with_its_git_dir_and_never_a_private_one() -> None:
    src = BUILD_ISO.read_text(encoding="utf-8")
    code = src.split("--- הקוד ---", 1)[1]
    assert 'cp -a "$REPO/.git" "$ISO_TREE/imagesrv-src/.git"' in code, "ה-.git של ה-clone מהציבורי לא נארז"
    guard = code.split('cp -a "$REPO/.git"', 1)[0]
    assert '"$origin" == "$PUBLIC_URL"' in guard, "אין בדיקה ש-origin של ה-.git הנארז הוא הציבורי"
    assert 'if [[ -z "$SOURCE" ]]' in guard, "‏--source (עץ מקומי, היסטוריה פרטית) חייב להישאר בלי .git"


# --- #1190: המתקין החי ---------------------------------------------------------

def test_firstboot_completes_from_the_installers_answers_without_the_wizard() -> None:
    """הגואי על מסך השרת כבר שאל הכול: firstboot מריץ את setup-boot-server מהתשובות,
    מאמת את המטען, חותם, מדליק את ה-DCUI — ולא את האשף. בלי תשובות: המסלול הישן."""
    fb = _strip_comments(FIRSTBOOT.read_text(encoding="utf-8"))
    answers = fb.index('if [[ -f "$ETC/answers" ]]')
    assert answers < fb.index('NIC_FILE="$ETC/installer-nic"'), "מסלול התשובות קודם למסלול האשף"
    branch = fb[answers:fb.index("NIC_FILE=")]
    assert "firstboot_from_answers" in branch and "systemctl restart imagesrv-dcui.service" in branch
    assert "imagesrv-wizard" not in branch and "exit 0" in branch
    fa = FIRSTBOOT_ANSWERS.read_text(encoding="utf-8")
    for flag in ("--servers-if", "--servers-mode", "--console-host", "--storage-role", '--admin-user "$(answers_get admin_user)"', "--admin-pass-fd 3"):
        assert flag in fa, flag
    # הערך בלבד מגיע ל-fd 3 — לא `admin_pass=...` כולו: כך הסיסמה הפכה
    # ל-"admin_pass=Aa..." על השרת הראשון שהותקן חי (ESXi 21/09).
    assert "sed -n 's/^admin_pass=//p' \"$ANSWERS_SECRET\"" in fa, "הערך נחלץ מהשורה"
    assert "3< <(printf '%s\\n' \"$secret\")" in fa and 'shred -u "$ANSWERS_SECRET"' in fa, "הסיסמה דרך FD ונמחקת"
    assert '3< "$ANSWERS_SECRET"' not in fa, "הקובץ כולו אינו הסיסמה"
    assert "answers-secret-missing \"$ANSWERS_SECRET has no admin_pass= line\"" in fa
    # ‏הכרטיס עולה אחרי setup-boot-server — networking.service רץ לפני שהקובץ נכתב;
    # דרכו ולא ב-ifup מתוך firstboot (#2094: ה-dhcpcd מת עם יחידת ה-oneshot)
    assert "systemctl restart networking.service" in fa and "[nic-up-failed]" in fa
    assert "verify-boot-payload.sh" in fa and 'touch "$STAMP"' in fa
    assert fa.index("verify-boot-payload.sh") < fa.index('touch "$STAMP"'), "החותמת רק אחרי האימות"
    assert "servers_mac" in fa, "הכרטיס נפתר לפי MAC — השם עלול להשתנות בין הקרנל החי למותקן"


def test_init_hands_over_to_the_live_installer_only_in_installer_mode() -> None:
    init = _strip_comments((REPO / "agent" / "init").read_text(encoding="utf-8"))
    common = (REPO / "agent" / "lib" / "core" / "common.sh").read_text(encoding="utf-8")
    # #1914: one case arm for installer|usbclone, the mode taken from the word itself
    assert re.search(r"imagesrv\.mode=installer(\|imagesrv\.mode=usbclone)?\)", common), "installer arm"
    assert 'IMAGESRV_MODE="installer"' in common or 'IMAGESRV_MODE="${_word#imagesrv.mode=}"' in common
    assert 'if [ "$IMAGESRV_MODE" = installer ]; then' in init
    assert init.index("installer_boot") < init.index("udhcpc -i"), "המסירה למתקין לפני לולאת ה-DHCP שנכשלת בלי חכירה"
    assert init.count("parse_cmdline") == 1, "שורת הקרנל נקראת פעם אחת (עיקרון 2)"
    boot = _strip_comments(INSTALLER_BOOT.read_text(encoding="utf-8"))
    assert 'blkid -L "$INSTALL_LABEL"' in boot and 'INSTALL_LABEL:-IMAGESRV_INSTALL' in boot
    assert "reboot" not in boot, "המסך החי לא מאתחל לעולם מעצמו"
    assert 'exec "$INSTALLER_BIN"' in boot
    console = _strip_comments(LIVE_CONSOLE.read_text(encoding="utf-8"))
    assert "--screen install" in console and "--install-cmd" in console
    assert "reboot" not in console, "הגואי/הגשר מאתחלים; המסך החי רק מדווח וממתין"
    assert 'hold "no framebuffer' in console and 'hold "no keyboard found"' in console


def test_build_initramfs_packs_the_installer_only_with_the_flag() -> None:
    text = INITRAMFS.read_text(encoding="utf-8")
    start = text.index("# --installer (#1190): the live installer")
    block = text[start:text.index("\nfi\n", start)]
    for f in ("imagesrv-install", "imagesrv-installer", "lib/*.sh", "gui-bridge.sh"):
        assert f in block, f
    tools = text[text.index('if [ "$WITH_INSTALLER" -eq 1 ]; then'):]
    assert "eject" in tools[:tools.index("\nfi\n")], "eject נארז רק עם --installer"
