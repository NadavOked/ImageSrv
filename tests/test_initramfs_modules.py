"""סדר טעינת המודולים ב-initramfs — דרייבר PHY לפני דרייבר MAC.

‏`r8169` שעושה probe לפני ש-`realtek.ko` נרשם נכשל ב-EADDRNOTAVAIL,
והקרנל אינו מנסה שוב: המודול נשאר טעון וההתקן נשאר בלי דרייבר. מחשב
Lenovo עם RTL8168 לא קיבל ממשק רשת בכלל בגלל זה, ורק במעבדת חומרה
(‏2026-08-29, ‏#76) זה נראה — ב-VM הכרטיס אינו זקוק ל-PHY נפרד.

הבדיקה מריצה את הקטע האמיתי מתוך `tools/build_initramfs.sh` על עץ
מודולים מזויף, ולא בודקת את הטקסט שלו: ניסוח אחר שישבור את הסדר
צריך להיכשל כאן.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

BUILDER = Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh"
INIT = Path(__file__).resolve().parent.parent / "agent" / "init"
BASH = shutil.which("bash")
KVER = "9.9.9-test"

#: הקטע מתוך הבנאי רץ ב-sh אמיתי; בלעדיו אין כאן בדיקה, ובמקום שבו
#: הוא אמור להיות זו תקלה ולא סיבה לדלג (#52).
pytestmark = requires_native("sh", why="הקטע מהבנאי רץ ב-sh")


def module_list_snippet() -> str:
    """הקטע שמייצר את /etc/imagectl/modules, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("_phy_mods=$(find"))
    end = next(i for i, l in enumerate(lines) if l.startswith("} | awk "))
    return "\n".join(lines[start:end + 1])


def generate(tmp_path: Path, phy: list[str], ethernet: list[str], pre: str = "") -> list[str]:
    root = tmp_path / "root"
    for sub, names in (("phy", phy), ("ethernet", ethernet)):
        d = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "net" / sub
        d.mkdir(parents=True, exist_ok=True)
        for name in names:
            (d / f"{name}.ko.xz").write_bytes(b"")
    (root / "etc" / "imagectl").mkdir(parents=True, exist_ok=True)

    script = f'ROOT={root.as_posix()!r}\nKVER={KVER!r}\nWITH_GUI=0\n' + pre + module_list_snippet()
    subprocess.run(["sh", "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)
    out = root / "etc" / "imagectl" / "modules"
    return out.read_text(encoding="utf-8").split()


def test_phy_drivers_load_before_the_mac_drivers_that_need_them(tmp_path: Path):
    """‏realtek לפני r8169 — למרות ש-r8169 קודם לו אלפביתית."""
    mods = generate(tmp_path, phy=["realtek", "marvell"],
                    ethernet=["r8169", "e1000e", "igc"])
    assert mods.index("realtek") < mods.index("r8169")
    assert mods.index("marvell") < mods.index("r8169")


def test_every_phy_driver_precedes_every_ethernet_driver(tmp_path: Path):
    """הכלל הוא קבוצתי, לא רשימת מקרים פרטיים."""
    phy = ["realtek", "broadcom", "micrel", "aquantia"]
    ethernet = ["r8169", "atlantic", "bnx2x", "e1000", "tg3"]
    mods = generate(tmp_path, phy=phy, ethernet=ethernet)
    last_phy = max(mods.index(m) for m in phy)
    first_mac = min(mods.index(m) for m in ethernet)
    assert last_phy < first_mac


def test_storage_still_comes_first_and_there_are_no_duplicates(tmp_path: Path):
    """‏sort -u ביטל בשקט גם את הכוונה של "Storage first"; היא חזרה."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169", "e1000e"])
    assert mods.index("ahci") < mods.index("realtek")
    assert mods.index("nvme") < mods.index("r8169")
    assert len(mods) == len(set(mods))


def test_a_phy_module_listed_in_both_trees_is_not_duplicated(tmp_path: Path):
    """הסינון שומר על הסדר — ולכן חייב גם להסיר כפילויות."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["realtek", "r8169"])
    assert mods.count("realtek") == 1
    assert mods.index("realtek") < mods.index("r8169")

def test_the_usb_controller_loads_before_anything_that_hangs_off_it(tmp_path: Path):
    """‏usbhid מצהיר depends: usbcore,hid — ובלי הבקר אין USB בכלל (#77).

    ‏#43 הוסיף את `hid` ונעצר שם, ולכן מקלדת USB לא עבדה על חומרה:
    האשף הוצג ואף הקשה לא הגיעה. ‏PS/2 הסתירה את זה — `i8042` built-in.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for controller in ("usbcore", "xhci_hcd", "ehci_hcd", "uhci_hcd"):
        assert controller in mods, f"{controller} is not loaded at all"
        assert mods.index(controller) < mods.index("usbhid")
    # ‏usbhid תלוי גם ב-hid עצמו.
    assert mods.index("hid") < mods.index("usbhid")
    # ומה שמתחבר ל-USB בא אחרי הבקר.
    assert mods.index("usbcore") < mods.index("usb-storage")


def test_the_crc32c_provider_loads_before_the_filesystems_that_need_it(tmp_path: Path):
    """#1207: libcrc32c (ext4/btrfs/xfs) needs a crc32c provider already
    registered. busybox modprobe ignores the softdep, so btrfs and xfs,
    listed before crc32c_generic, failed with "unknown symbol" on every boot
    and init reported a broken restore. Measured in QEMU 24/09: xfs alone
    rc=1, crc32c_generic then xfs rc=0 -- one variable."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for fs in ("ext4", "btrfs", "xfs"):
        assert mods.index("crc32c_generic") < mods.index(fs), fs
        assert mods.index("crc32c-intel") < mods.index(fs), fs


def test_the_builder_packs_the_usb_core_and_host_trees(tmp_path: Path):
    """רשימת הטעינה חסרת ערך אם המודולים עצמם אינם נארזים."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "kernel/drivers/usb/core" in text
    assert "kernel/drivers/usb/host" in text


def test_the_builder_packs_the_cpufreq_tree():
    """Older Intel CPUs need Debian's modular acpi-cpufreq fallback (#1441)."""
    text = BUILDER.read_text(encoding="utf-8")
    assert "kernel/drivers/cpufreq" in text


def test_psmouse_is_in_the_modules_load_list(tmp_path: Path):
    """‏#1204: ‏VM של ESXi (govc) מקבל בקר PS/2 בלבד, בלי בקר USB. בלי
    ‏psmouse (שכולל את פרוטוקול vmmouse) אין עכבר במתקין הגרפי בכלל,
    אף שהמקלדת עבדה — ‏`i8042` הוא built-in, בדיוק כמו ש-PS/2 הסתיר את
    היעדר `usbhid` ב-#77. מודול חסר אינו פטאלי, כמו atkbd/hyperv_keyboard
    לצידו."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    assert "psmouse" in mods


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_the_builder_packs_the_input_mouse_tree(tmp_path: Path):
    """רשימת הטעינה (לעיל) חסרת ערך אם psmouse עצמו אינו נארז (#1204)."""
    packed = run_copy(tmp_path, {
        "kernel/drivers/input/mouse": ["psmouse"],
        "kernel/drivers/net/ethernet": ["e1000e"],
    })
    assert (packed / "kernel/drivers/input/mouse/psmouse.ko.xz").is_file()


def closure_snippet() -> str:
    """הקטע שסוגר את גרף התלויות, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines)
                 if l.startswith('[ -f "$MODSRC/modules.softdep" ]'))
    end = next(i for i, l in enumerate(lines)
               if l.startswith('echo "module dependency closure:'))
    return "\n".join(lines[start:end + 1])


def run_closure(tmp_path: Path, src: Path, root: Path, *, softdep: bool = True,
                check: bool = True):
    if softdep and not (src / "modules.softdep").exists():
        (src / "modules.softdep").write_text("", encoding="utf-8")
    if not (src / "modules.builtin").exists():
        (src / "modules.builtin").write_text("", encoding="utf-8")
    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\n" + closure_snippet())
    return subprocess.run(["sh", "-c", script], check=check,
                          stdin=subprocess.DEVNULL, capture_output=True, timeout=90)


def test_a_module_pulls_in_a_dependency_from_a_directory_nobody_listed(tmp_path: Path):
    """‏usbcore תלוי ב-usb-common, שיושב בתיקייה שלישית שאיש לא רשם.

    זה מה שהשאיר את המקלדת בלי חשמל אחרי שכבר נוספו usb/core ו-usb/host
    בידיים (‏#77): הנורה לא נדלקה, כי בלי usb-common גם usbcore לא נטען.
    רשימת תיקיות ידנית תמיד תפספס תלות אחת עמוק יותר — ולכן הגרף נסגר
    מ-modules.dep ולא מניחוש.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for sub in ("core", "common", "host"):
        (src / "kernel" / "drivers" / "usb" / sub).mkdir(parents=True)
    (src / "kernel/drivers/usb/core/usbcore.ko.xz").write_bytes(b"")
    (src / "kernel/drivers/usb/common/usb-common.ko.xz").write_bytes(b"")
    (src / "kernel/drivers/usb/host/xhci-hcd.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text(
        "kernel/drivers/usb/core/usbcore.ko.xz:"
        " kernel/drivers/usb/common/usb-common.ko.xz\n"
        "kernel/drivers/usb/host/xhci-hcd.ko.xz:"
        " kernel/drivers/usb/core/usbcore.ko.xz"
        " kernel/drivers/usb/common/usb-common.ko.xz\n",
        encoding="utf-8")

    # רק ה-host הועתק, בדיוק כמו רשימת תיקיות שפספסה את השאר.
    dst = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "usb" / "host"
    dst.mkdir(parents=True)
    (dst / "xhci-hcd.ko.xz").write_bytes(b"")

    run_closure(tmp_path, src, root)

    packed = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "usb"
    assert (packed / "core" / "usbcore.ko.xz").is_file(), "usbcore was not pulled in"
    assert (packed / "common" / "usb-common.ko.xz").is_file(), (
        "usb-common was not pulled in -- the dependency that left the keyboard dark")


def test_the_closure_terminates_when_nothing_is_missing(tmp_path: Path):
    """סגירה שלא עוצרת היא בנייה תקועה — גרוע מאימג' חסר."""
    src, root = tmp_path / "src", tmp_path / "root"
    (src / "kernel" / "drivers" / "net").mkdir(parents=True)
    (src / "kernel/drivers/net/lonely.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text("kernel/drivers/net/lonely.ko.xz:\n",
                                     encoding="utf-8")
    dst = root / "lib" / "modules" / KVER / "kernel" / "drivers" / "net"
    dst.mkdir(parents=True)
    (dst / "lonely.ko.xz").write_bytes(b"")

    done = run_closure(tmp_path, src, root)
    assert b"after 1 rounds" in done.stdout


def test_libcrc32c_softdep_pulls_the_crc32c_implementation_into_the_image(tmp_path: Path):
    """#1169 negative control: modules.dep alone left crc32c_generic out."""
    src, root = tmp_path / "src", tmp_path / "root"
    (src / "kernel/lib").mkdir(parents=True)
    (src / "kernel/crypto").mkdir(parents=True)
    (src / "kernel/lib/libcrc32c.ko.xz").write_bytes(b"")
    (src / "kernel/crypto/crc32c_generic.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text(
        "kernel/lib/libcrc32c.ko.xz:\n"
        "kernel/crypto/crc32c_generic.ko.xz:\n", encoding="utf-8")
    (src / "modules.softdep").write_text(
        "softdep libcrc32c pre: crc32c\n", encoding="utf-8")
    dst = root / "lib" / "modules" / KVER / "kernel/lib"
    dst.mkdir(parents=True)
    (dst / "libcrc32c.ko.xz").write_bytes(b"")

    run_closure(tmp_path, src, root)

    packed = root / "lib" / "modules" / KVER
    assert (packed / "kernel/crypto/crc32c_generic.ko.xz").is_file()


def test_missing_modules_softdep_is_a_named_build_failure(tmp_path: Path):
    src, root = tmp_path / "src", tmp_path / "root"
    (src / "kernel/lib").mkdir(parents=True)
    (src / "kernel/lib/libcrc32c.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text("kernel/lib/libcrc32c.ko.xz:\n",
                                      encoding="utf-8")
    dst = root / "lib" / "modules" / KVER / "kernel/lib"
    dst.mkdir(parents=True)
    (dst / "libcrc32c.ko.xz").write_bytes(b"")

    done = run_closure(tmp_path, src, root, softdep=False, check=False)

    assert done.returncode != 0
    assert b"modules.softdep" in done.stderr


def test_softdep_metadata_is_packed_for_busybox_modprobe():
    """The closure alone is insufficient: modprobe needs the metadata too."""
    source = BUILDER.read_text(encoding="utf-8")
    assert 'cp "$MODSRC/modules.softdep" "$ROOT/lib/modules/$KVER/"' in source


# --- מערכות קבצים: העץ שלא נארז, וארבעה תסמינים שנראו לא קשורים (#84) --------


def copy_snippet() -> str:
    """הקטע שמעתיק את עצי המודולים, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("MODULE_SUBDIRS=("))
    copy = next(i for i, l in enumerate(lines) if 'cp -a "$MODSRC/$sub/."' in l)
    end = next(i for i, l in enumerate(lines[copy:], copy) if l == "done")
    return "\n".join(lines[start:end + 1])


def run_copy(tmp_path: Path, trees: dict[str, list[str]]) -> Path:
    """מריץ את לולאת ההעתקה האמיתית על עץ מודולים מזויף.

    ‏bash ולא sh: ‏MODULE_SUBDIRS הוא מערך, ו-dash אינו מכיר מערכים.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for rel, names in trees.items():
        directory = src / rel
        directory.mkdir(parents=True, exist_ok=True)
        for name in names:
            (directory / f"{name}.ko.xz").write_bytes(b"")
    (root / "lib" / "modules" / KVER).mkdir(parents=True)

    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\n"
              f"MODSRC={src.as_posix()!r}\nWITH_GUI=0\n" + copy_snippet())
    subprocess.run([BASH, "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=90)
    return root / "lib" / "modules" / KVER


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_the_builder_packs_the_filesystem_trees(tmp_path: Path):
    """בלעדיהם ה-initramfs יכול לעגן NTFS דרך FUSE ותו לא.

    זה נראה כמו שלושה באגים נפרדים: שם המחשב בלינוקס לא נכתב כי
    ‏`mount -t ext4` נכשל (#62), ‏`used_bytes` היה 0 בכל מניפסט כי
    המדידה מודדת אחרי מאונט, והרחבת btrfs הייתה נכשלת ברגע שיהיה
    אימג' כזה. עץ אחד שלא נארז.
    """
    packed = run_copy(tmp_path, {
        "kernel/fs/fat": ["fat", "vfat", "msdos"],
        "kernel/fs/nls": ["nls_cp437", "nls_ascii"],
        "kernel/fs/ext4": ["ext4"],
        "kernel/fs/btrfs": ["btrfs"],
        "kernel/fs/xfs": ["xfs"],
        "kernel/fs/efivarfs": ["efivarfs"],
        "kernel/fs/ceph": ["ceph"],          # לא נדרש — ולא אמור להיארז
        "kernel/drivers/net/ethernet": ["e1000e"],
    })
    for rel in ("fat/vfat", "ext4/ext4", "btrfs/btrfs", "xfs/xfs", "efivarfs/efivarfs"):
        assert (packed / "kernel/fs" / f"{rel}.ko.xz").is_file(), f"{rel} לא נארז"


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_the_whole_nls_tree_is_packed_and_not_a_guessed_subset(tmp_path: Path):
    """‏vfat דורש את קידוד ברירת המחדל של הקרנל — כאן `cp437` **וגם**
    `ascii` (‏CONFIG_FAT_DEFAULT_CODEPAGE=437, ‏IOCHARSET="ascii").
    בחירת תת-קבוצה היא בדיוק הניחוש שנכשל ב-#33, ‏#76 ו-#77."""
    packed = run_copy(tmp_path, {
        "kernel/fs/nls": ["nls_cp437", "nls_ascii", "nls_cp1255", "nls_utf8"],
    })
    for name in ("nls_cp437", "nls_ascii", "nls_cp1255", "nls_utf8"):
        assert (packed / "kernel/fs/nls" / f"{name}.ko.xz").is_file()


@requires_native("bash", why="MODULE_SUBDIRS הוא מערך bash")
def test_a_tree_nobody_asked_for_is_left_out(tmp_path: Path):
    """הרשימה היא בחירה, לא `kernel/fs` כולו — אחרת ה-initramfs תופח."""
    packed = run_copy(tmp_path, {
        "kernel/fs/ceph": ["ceph"],
        "kernel/fs/bcachefs": ["bcachefs"],
        "kernel/fs/ext4": ["ext4"],
    })
    assert (packed / "kernel/fs/ext4/ext4.ko.xz").is_file()
    assert not (packed / "kernel/fs/ceph").exists()
    assert not (packed / "kernel/fs/bcachefs").exists()


def test_ext4_pulls_in_the_dependencies_that_sit_outside_its_own_tree(tmp_path: Path):
    """‏mbcache יושב ישירות ב-kernel/fs ולא בתת-תיקייה, ו-crc16 ב-kernel/lib.

    בדיוק הצורה של #77: התיקייה שנרשמה בידיים היא לא התיקייה שבה יושבת
    התלות. הגרף נסגר מ-modules.dep, ולכן אלה לא צריכים להיות ברשימה.
    """
    src, root = tmp_path / "src", tmp_path / "root"
    for rel in ("kernel/fs/ext4", "kernel/fs/jbd2", "kernel/lib", "kernel/fs"):
        (src / rel).mkdir(parents=True, exist_ok=True)
    (src / "kernel/fs/ext4/ext4.ko.xz").write_bytes(b"")
    (src / "kernel/fs/jbd2/jbd2.ko.xz").write_bytes(b"")
    (src / "kernel/fs/mbcache.ko.xz").write_bytes(b"")
    (src / "kernel/lib/crc16.ko.xz").write_bytes(b"")
    (src / "modules.dep").write_text(
        "kernel/fs/ext4/ext4.ko.xz: kernel/lib/crc16.ko.xz"
        " kernel/fs/mbcache.ko.xz kernel/fs/jbd2/jbd2.ko.xz\n"
        "kernel/fs/jbd2/jbd2.ko.xz:\n"
        "kernel/fs/mbcache.ko.xz:\n"
        "kernel/lib/crc16.ko.xz:\n",
        encoding="utf-8")

    dst = root / "lib" / "modules" / KVER / "kernel" / "fs" / "ext4"
    dst.mkdir(parents=True)
    (dst / "ext4.ko.xz").write_bytes(b"")

    run_closure(tmp_path, src, root)

    packed = root / "lib" / "modules" / KVER
    assert (packed / "kernel/fs/mbcache.ko.xz").is_file(), "mbcache לא נגרר"
    assert (packed / "kernel/fs/jbd2/jbd2.ko.xz").is_file(), "jbd2 לא נגרר"
    assert (packed / "kernel/lib/crc16.ko.xz").is_file(), "crc16 לא נגרר"


def test_the_filesystem_modules_are_loaded_and_not_left_to_autoload(tmp_path: Path):
    """טעינה מפורשת ולא הסתמכות על ‏`fs-ext4` דרך modules.alias.

    השרשרת ההיא (‏depmod → modules.alias → /proc/sys/kernel/modprobe →
    busybox modprobe) נכשלת בשקט בכל חוליה. ברשימה, כישלון נספר
    ומדווח כ-`N modules did not load`.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in ("efivarfs", "fat", "vfat", "nls_cp437", "nls_ascii", "ext4", "btrfs", "xfs"):
        assert name in mods, f"{name} אינו ברשימת הטעינה"
    # ‏efivarfs נטען לפני שה-init מנסה לעגן אותו — ראו agent/init.
    assert len(mods) == len(set(mods))


# --- כיסוי לפלטפורמות היעד המוצהרות (#78) ------------------------------------

#: איפה כל דרייבר יושב באמת בעץ של דביאן. שניים מהם הם קבצים בודדים
#: ישירות תחת `kernel/drivers/net` — בלי תיקייה משלהם — וזו כל הנקודה:
#: לולאת התיקיות לעולם לא תיגע בהם, בדיוק כמו `mbcache` ב-#84.
PLATFORM_LAYOUT = {
    "hv_netvsc": "kernel/drivers/net/hyperv",
    "hv_storvsc": "kernel/drivers/scsi",
    "vmxnet3": "kernel/drivers/net/vmxnet3",
    "vmw_pvscsi": "kernel/drivers/scsi",
    "virtio_net": "kernel/drivers/net",
    "virtio_blk": "kernel/drivers/block",
    "xen-netfront": "kernel/drivers/net",
    "xen-blkfront": "kernel/drivers/block",
}

#: ומערכות הקבצים, שעד #121 לא היו מוצהרות כלל אלא הגיעו כתוצר לוואי
#: של `MODULE_SUBDIRS` — ולכן נשירה שלהן הסתיימה ב-exit 0.
FS_LAYOUT = {
    "ext4": "kernel/fs/ext4",
    "btrfs": "kernel/fs/btrfs",
    "xfs": "kernel/fs/xfs",
    "vfat": "kernel/fs/fat",
    "fat": "kernel/fs/fat",
    "nls_cp437": "kernel/fs/nls",
    "nls_ascii": "kernel/fs/nls",
    "efivarfs": "kernel/fs/efivarfs",
    "crc32c_generic": "kernel/crypto",
    "crc32c-intel": "kernel/arch/x86/crypto",
}

#: #1466: the local clone's read-only source -- a dm-snapshot view of the
#: source partition with its copy-on-write store on a brd RAM disk.
LC_LAYOUT = {
    "dm-mod": "kernel/drivers/md",
    "dm-snapshot": "kernel/drivers/md",
    "dm-bufio": "kernel/drivers/md",
    "brd": "kernel/drivers/block",
}

#: #1596: fanout's SHA-256 through AF_ALG, on the kernel's SSSE3/AVX code.
SHA_LAYOUT = {
    "af_alg": "kernel/crypto",
    "algif_hash": "kernel/crypto",
    "sha256-ssse3": "kernel/arch/x86/crypto",
}

#: #1918: the ACPI power button, as a module (BUILTIN_FUSE does not list it as
#: built in). Not a measurement of Debian's kernel: the builder reads
#: modules.builtin for that, and both answers are tested below.
BUTTON_LAYOUT = {"button": "kernel/drivers/acpi"}

#: #1967: the cloner's audible round result -- the PC speaker, declared required.
BEEP_LAYOUT = {"pcspkr": "kernel/drivers/input/misc"}

LAYOUT = {**PLATFORM_LAYOUT, **FS_LAYOUT, **LC_LAYOUT, **SHA_LAYOUT, **BUTTON_LAYOUT, **BEEP_LAYOUT}


def declared(array: str) -> list[str]:
    """שמות המודולים במערך מוצהר בסקריפט — מקור האמת הוא הסקריפט."""
    text = BUILDER.read_text(encoding="utf-8")
    body = text.split(f"{array}=(", 1)[1].split(")", 1)[0]
    names = []
    for line in body.split("\n"):
        names += line.split("#", 1)[0].split()
    return names


def required_snippet() -> str:
    """הצהרת הפלטפורמות וההעתקה לפי שם, כפי שהן בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("REQUIRED_MODULES=("))
    exit_at = next(i for i, l in enumerate(lines[start:], start) if l.strip() == "exit 1")
    end = next(i for i, l in enumerate(lines[exit_at:], exit_at) if l == "fi")
    return "\n".join(lines[start:end + 1])


#: ‏modules.builtin של קרנל דביאן 13 — ‏FUSE built-in (נמדד 13/10, #1368).
BUILTIN_FUSE = "kernel/fs/fuse/fuse.ko\nkernel/fs/fuse/virtiofs.ko\n"


def run_required(tmp_path: Path, present, kver: str = KVER,
                 builtin: str | None = BUILTIN_FUSE, pre: str = ""):
    """מריץ את ההעתקה-לפי-שם האמיתית על עץ שבו קיימים רק `present`.

    ‏`builtin` = תוכן modules.builtin; ‏None = הקובץ אינו קיים. ‏`pre` = מה
    שנקבע לפני האזור (‏#1918: ‏BUTTON_MOD / _btn_miss של בדיקת כפתור ההפעלה)."""
    src, root = tmp_path / "src", tmp_path / "root"
    for mod in present:
        directory = src / LAYOUT[mod]
        directory.mkdir(parents=True, exist_ok=True)
        (directory / f"{mod}.ko.xz").write_bytes(b"")
    src.mkdir(exist_ok=True)
    if builtin is not None:
        (src / "modules.builtin").write_text(builtin, encoding="utf-8")
    (root / "lib" / "modules" / kver).mkdir(parents=True)

    script = (f"ROOT={root.as_posix()!r}\nKVER={kver!r}\n"
              f"MODSRC={src.as_posix()!r}\nWITH_GUI=0\nMODULE_SUBDIRS=()\n"
              + pre + required_snippet())
    done = subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=90)
    return done, root / "lib" / "modules" / kver


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_every_declared_platform_gets_its_nic_and_its_disk_controller(tmp_path: Path):
    """‏ESXi, ‏KVM ו-Xen — לא רק ה-Hyper-V שעליו נבנה האימג'.

    מכונה על ESXi עלתה בלי כרטיס רשת, ועל KVM ו-Xen גם בלי דיסק.
    ‏`vmw_pvscsi` ו-`virtio_scsi` שרדו רק במקרה, כי הם יושבים תחת
    `scsi` שכן נארזת.
    """
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in PLATFORM_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} לא נארז"


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_a_driver_that_sits_loose_under_drivers_net_is_still_packed(tmp_path: Path):
    """‏`virtio_net` ו-`xen-netfront` אינם בתיקייה משלהם.

    לולאת התיקיות מעתיקה `kernel/drivers/net/<תת-תיקייה>`, והם קבצים
    ישירות תחת `net`. רשימה לפי תיקיות לא יכולה להגיע אליהם בכלל —
    ולכן ההצהרה היא לפי שם.
    """
    done, packed = run_required(tmp_path, ["virtio_net", "xen-netfront"] +
                                [m for m in LAYOUT
                                 if m not in ("virtio_net", "xen-netfront")])
    assert done.returncode == 0, done.stderr
    assert (packed / "kernel/drivers/net/virtio_net.ko.xz").is_file()
    assert (packed / "kernel/drivers/net/xen-netfront.ko.xz").is_file()


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
@pytest.mark.parametrize("absent", sorted(LC_LAYOUT))
def test_the_build_stops_when_a_local_clone_view_module_is_missing(tmp_path: Path, absent: str):
    """#1466: without dm-snapshot or brd the local clone falls back to shrinking the
    source in place -- legal, but exactly what the owner ruled out. A kernel without
    one of them is caught by the build, not by a cloner in a classroom."""
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != absent])
    assert done.returncode != 0, f"{absent} missing and the build passed"
    assert absent in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_local_clone_view_modules_are_packed_by_name(tmp_path: Path):
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in LC_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} not packed"


def test_the_local_clone_view_modules_are_not_loaded_at_boot():
    """brd loaded at boot would come up at its default size, and the agent could not size it."""
    text = BUILDER.read_text(encoding="utf-8")
    boot = text[text.index("printf '%s\\n' usbcore"):text.index('> "$ROOT/etc/imagectl/modules"')]
    for mod in ("brd", "dm-snapshot", "dm_snapshot", "dm-mod", "dm_mod"):
        assert mod not in boot, mod


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_build_stops_when_a_declared_platform_driver_is_missing(tmp_path: Path):
    """שכחה נתפסת בבנייה ולא מול מכונה — זו כל תוחלת ההצהרה.

    ‏initramfs שנבנה בלי דרייבר של פלטפורמה מוצהרת נראה תקין לחלוטין,
    והכשל מגיע רק כשמחשב אמיתי עולה ומודיע `no DHCP lease on any
    interface` — בלי רמז לאיזה מודול חסר. ככה נראו #76, ‏#77 ו-#84.
    """
    present = [m for m in LAYOUT if m != "vmxnet3"]
    done, _ = run_required(tmp_path, present)
    assert done.returncode != 0, "בנייה חסרה הצליחה — ההצהרה חסרת ערך"
    assert "vmxnet3" in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_missing_report_names_all_of_them_at_once(tmp_path: Path):
    """שלושה חסרים = הודעה אחת, לא שלוש בנייות."""
    absent = {"vmxnet3", "virtio_blk", "xen-netfront"}
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m not in absent])
    assert done.returncode != 0
    for mod in absent:
        assert mod in done.stderr, f"{mod} לא הוזכר: {done.stderr}"


CLOUD_KVER = "9.9.9-cloud-amd64"
#: שלושת הקבצים שמעתיקים מהראשי במקום לבנות על קרנל cloud (#904).
BOOT_FILES = ("vmlinuz", "initrd.img", "initrd.img.gui")


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_a_cloud_kernel_refusal_says_to_copy_the_boot_files_from_the_primary(
    tmp_path: Path
):
    """‏#904: השרת המשני במעבדה (16/09) עלה על linux-image-cloud-amd64,
    והבנאי סירב — נכון (#78). אבל ההודעה אמרה רק אילו מודולים חסרים,
    והמסלול שעבד — העתקת vmlinuz + initrd.img + initrd.img.gui מהראשי —
    לא נאמר בשום מקום. עכשיו הסירוב על קרנל cloud נוקב בו בשמו. הלוגיקה
    לא השתנתה: עדיין יציאה שאינה 0, ועדיין שמות החסרים."""
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != "vmxnet3"],
                           kver=CLOUD_KVER)
    assert done.returncode != 0
    assert "vmxnet3" in done.stderr, done.stderr
    assert "cloud kernel" in done.stderr, done.stderr
    for name in BOOT_FILES:
        assert name in done.stderr, f"{name} לא הוזכר: {done.stderr}"
    assert "primary" in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_a_regular_kernel_refusal_does_not_blame_a_cloud_kernel(tmp_path: Path):
    """על קרנל רגיל מודול חסר הוא תקלה אמיתית (#76/#77/#84), לא "קרנל
    cloud" — עצה להעתיק מהראשי הייתה מסתירה אותה."""
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != "vmxnet3"])
    assert done.returncode != 0
    assert "cloud kernel" not in done.stderr, done.stderr


# --- מערכות הקבצים מוצהרות, ולא נוכחות במקרה (#121) ---------------------------


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
@pytest.mark.parametrize("absent", sorted(FS_LAYOUT))
def test_the_build_stops_when_a_declared_filesystem_module_is_missing(
    tmp_path: Path, absent: str
):
    """הבקרה השלילית של #121, מודול-מודול.

    ‏`exfat` ו-`isofs` נשרו מהעץ בין שתי גרסאות והבנייה יצאה 0, כי
    מערכות הקבצים לא היו מוצהרות בשום מקום — הן הגיעו כתוצר לוואי של
    ‏`MODULE_SUBDIRS` וסגירת התלויות. ‏`ext4` שנושר כך הוא #62 ו-#84
    מחדש: שם המחשב לא נכתב, ‏`used_bytes` אפס בכל מניפסט — ושוב, רק
    מול מכונה אמיתית.
    """
    done, _ = run_required(tmp_path, [m for m in LAYOUT if m != absent])
    assert done.returncode != 0, f"{absent} נשר והבנייה הצליחה — ההצהרה חסרת ערך"
    assert absent in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
def test_a_declared_filesystem_module_is_packed_even_from_a_tree_nobody_listed(
    tmp_path: Path,
):
    """ההצהרה גם מרפאת: המודול מועתק לפי שם, לא לפי תיקייה.

    ‏`MODULE_SUBDIRS=()` כאן — אף עץ לא הועתק — ובכל זאת כל מודול
    מוצהר חייב להימצא בתוצר. אותה תכונה בדיוק כמו ‏`virtio_net`,
    שיושב ישירות תחת `kernel/drivers/net` ולולאת התיקיות לא נוגעת בו.
    """
    done, packed = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr
    for mod, where in FS_LAYOUT.items():
        assert (packed / where / f"{mod}.ko.xz").is_file(), f"{mod} לא נארז"


# --- ‏FUSE: ‏ntfs-3g רץ עליו (#1368) -------------------------------------------


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
@pytest.mark.parametrize(("builtin", "named"), [
    ("kernel/fs/ext4/ext4.ko\n", "fuse(not-built-in)"),
    (None, "fuse(modules.builtin-unreadable)"),
])
def test_the_build_stops_when_fuse_is_not_built_in(tmp_path: Path, builtin, named):
    """בלי FUSE אין ntfs-3g: ‏used_bytes של NTFS, בדיקת מחיצת המערכת וכתיבת
    השם נופלים כולם — ומתגלים מול מחשב בנייה כ-null. כאן הבנייה עוצרת.
    ‏modules.builtin שלא נקרא אינו "built-in" (עיקרון 5)."""
    done, _ = run_required(tmp_path, list(LAYOUT), builtin=builtin)
    assert done.returncode != 0, "FUSE חסר והבנייה הצליחה"
    assert named in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
def test_a_kernel_with_fuse_built_in_builds(tmp_path: Path):
    done, _ = run_required(tmp_path, list(LAYOUT))
    assert done.returncode == 0, done.stderr


# --- #1918: the ACPI power button --------------------------------------------------
# button.ko turns a short press into the "Power Button" input device the GUI
# reads. Built in or a module is the kernel's config: read from
# modules.builtin, never assumed -- both answers, and the third (neither).

NO_BUTTON = [m for m in LAYOUT if m != "button"]


def button_detect_snippet() -> str:
    """The modules.builtin read, as it is in the script (next to MODSRC)."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("# #1918: the power button."))
    end = next(i for i, l in enumerate(lines[start:], start) if l == "esac")
    return "\n".join(lines[start:end + 1])


@requires_native("bash", why="the builder's region is bash")
@pytest.mark.parametrize("builtin,want", [
    (BUILTIN_FUSE + "kernel/drivers/acpi/button.ko\n", "mod=[] miss=[]"),    # built in
    (BUILTIN_FUSE, "mod=[button] miss=[]"),                                    # a module
    (None, "mod=[] miss=[ button(modules.builtin-unreadable)]"),               # not read
], ids=["built-in", "module", "unreadable"])
def test_the_power_button_is_read_from_modules_builtin(tmp_path: Path, builtin, want):
    src = tmp_path / "src"; src.mkdir()
    if builtin is not None:
        (src / "modules.builtin").write_text(builtin, encoding="utf-8")
    done = subprocess.run([BASH, "-c", f"MODSRC={src.as_posix()!r}\nKVER={KVER!r}\n" + button_detect_snippet()
                           + '\necho "mod=[$BUTTON_MOD] miss=[$_btn_miss]"'],
                          stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", timeout=60)
    assert done.stdout.strip().splitlines()[-1] == want, done.stdout + done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_the_power_button_module_is_packed_when_it_is_not_built_in(tmp_path: Path):
    done, packed = run_required(tmp_path, list(LAYOUT), pre="BUTTON_MOD=button\n")
    assert done.returncode == 0, done.stderr
    assert (packed / "kernel/drivers/acpi/button.ko.xz").is_file()


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
@pytest.mark.parametrize("pre,named", [
    ("BUTTON_MOD=button\n", " button"),                                        # a module, and missing
    ("_btn_miss=' button(modules.builtin-unreadable)'\n", "button(modules.builtin-unreadable)"),
], ids=["module-missing", "builtin-unreadable"])
def test_the_build_stops_without_the_power_button(tmp_path: Path, pre, named):
    done, _ = run_required(tmp_path, NO_BUTTON, pre=pre)
    assert done.returncode != 0, "no power button driver and the build passed"
    assert "missing required modules:" in done.stderr and named in done.stderr, done.stderr


@requires_native("bash", why="REQUIRED_MODULES הוא מערך bash")
def test_a_kernel_with_the_power_button_built_in_packs_nothing_for_it(tmp_path: Path):
    done, packed = run_required(tmp_path, NO_BUTTON)     # BUTTON_MOD empty: built in
    assert done.returncode == 0, done.stderr
    assert not list(packed.rglob("button.ko*"))


def test_the_power_button_is_loaded_at_boot_only_when_it_is_a_module(tmp_path: Path):
    assert "button" in generate(tmp_path / "m", phy=["realtek"], ethernet=["r8169"], pre="BUTTON_MOD=button\n")
    assert "button" not in generate(tmp_path / "b", phy=["realtek"], ethernet=["r8169"], pre='BUTTON_MOD=""\n')


def test_the_declared_filesystems_are_exactly_the_ones_in_the_load_list(tmp_path: Path):
    """מודול שנארז ולא נטען הוא מודול שלא קיים, ולהפך.

    שתי רשימות באותו קובץ שמתפצלות הן איך ההצהרה נשחקת בשקט — ולכן
    שתיהן נקראות מהסקריפט, ולא נכתבות כאן בידיים.
    """
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in declared("REQUIRED_FS_MODULES"):
        assert name in mods, f"{name} מוצהר אבל אינו ברשימת הטעינה"


def test_exfat_and_isofs_stay_out_until_something_actually_asks_for_them(tmp_path: Path):
    """ההכרעה של #121, מוצמדת: אין להם קורא ברפו.

    הסוכן עולה מהרשת ולא ממדיה אופטית, ומחיצת exFAT נשלחת
    ל-`partclone.dd` — שקורא בלוקים ולא מערכת קבצים. אם מישהו יוסיף
    קורא, הטסט הזה הוא המקום שבו ההחלטה נפתחת מחדש ולא מוחמצת.
    """
    assert "exfat" not in declared("REQUIRED_FS_MODULES")
    assert "isofs" not in declared("REQUIRED_FS_MODULES")
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    assert "exfat" not in mods
    assert "isofs" not in mods


def test_the_paravirtual_disk_controllers_are_in_the_load_list(tmp_path: Path):
    """דרייבר שנארז ולא נטען הוא דרייבר שלא קיים."""
    mods = generate(tmp_path, phy=["realtek"], ethernet=["r8169"])
    for name in ("vmw_pvscsi", "virtio_scsi", "virtio_blk", "xen-blkfront"):
        assert name in mods, f"{name} אינו ברשימת הטעינה"
    assert len(mods) == len(set(mods))


# --- הבנייה מוסרת ל-agent/init איזה מודול הוא חובה (#407) ---------------------


def modules_region_snippet() -> str:
    """הקטע שמייצר את modules ואת modules.required, כפי שהוא בסקריפט."""
    lines = BUILDER.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith("_phy_mods=$(find"))
    end = next(i for i, l in enumerate(lines)
               if l.startswith('for fw in "${FIRMWARE_DIRS'))
    return "\n".join(lines[start:end])


@requires_native("bash", why="REQUIRED_FS_MODULES הוא מערך bash")
def test_the_build_emits_the_required_module_set_for_the_runtime(tmp_path: Path):
    """מודולי החובה נכתבים לקובץ נפרד ש-`agent/init` יקרא (#407).

    בלי הקובץ הזה, ‏`agent/init` אינו יכול להבחין בין מודול פלטפורמה
    שלא נטען (צפוי על ברזל) לבין מודול חובה שלא נטען (‏initramfs שבור),
    ושניהם מתקפלים ל-`N modules did not load` — בדיוק עיקרון 5.
    הקובץ חייב לשקף את ההצהרה, ולא רשימה שנכתבה כאן בידיים.
    """
    root = tmp_path / "root"
    (root / "etc" / "imagectl").mkdir(parents=True)
    (root / "lib" / "modules" / KVER).mkdir(parents=True)
    decl = declared("REQUIRED_FS_MODULES")
    script = (f"ROOT={root.as_posix()!r}\nKVER={KVER!r}\nWITH_GUI=0\n"
              f"REQUIRED_FS_MODULES=({' '.join(decl)})\n" + modules_region_snippet())
    subprocess.run([BASH, "-c", script], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True, timeout=90)
    out = root / "etc" / "imagectl" / "modules.required"
    assert out.is_file(), (
        "הבנייה לא הפיקה modules.required — agent/init לא יבחין חובה מרשות (#407)")
    assert out.read_text(encoding="utf-8").split() == decl


# --- agent/init: 'לא נדרש כאן' ו'חסר קריטי' הן שתי הודעות שונות (#407) --------


def init_module_block() -> str:
    """קטע טעינת המודולים מתוך agent/init, בין שני עוגנים יציבים.

    לא בודקים את הטקסט אלא מריצים אותו: הסיווג נמדד לפי הפלט, כך
    שניסוח אחר שיאבד את ההבחנה ייכשל כאן.
    """
    lines = INIT.read_text(encoding="utf-8").split("\n")
    start = next(i for i, l in enumerate(lines) if l.strip() == "mkdir -p /run/imagectl")
    end = next(i for i, l in enumerate(lines) if l.startswith("# efivars"))
    return "\n".join(lines[start + 1:end])


def run_init_block(tmp_path: Path, modules: list[str], required: list[str],
                   fail: list[str], dmi: str = "", hypervisor: bool = False) -> str:
    """מריץ את קטע הטעינה עם `modprobe` מזויף שנכשל על `fail`."""
    moddir = tmp_path / "etc" / "imagectl"
    moddir.mkdir(parents=True)
    # ‏LF בלבד — הבנייה כותבת את הקבצים על דביאן, ו-`read -r` בסוכן משאיר
    # ‏`\r` שווינדוס היה מוסיף, כך שכל שם היה נראה שונה מהמצופה.
    (moddir / "modules").write_text(
        "\n".join(modules) + "\n", encoding="utf-8", newline="\n")
    (moddir / "modules.required").write_text(
        "\n".join(required) + "\n", encoding="utf-8", newline="\n")
    block = init_module_block().replace("/etc/imagectl", moddir.as_posix())
    # #1702: the loop lives in lib/initmods.sh; no split files here, so it
    # runs the whole list in order, as before.
    block = block.replace("/usr/lib/imagectl", (INIT.parent / "lib").as_posix())
    block = block.replace(
        'cat /sys/class/dmi/id/sys_vendor /sys/class/dmi/id/product_name 2>/dev/null',
        f'printf %s {dmi!r}').replace(
        "grep -q '[[:space:]]hypervisor\\([[:space:]]\\|$\\)' /proc/cpuinfo 2>/dev/null",
        "true" if hypervisor else "false")
    stub = ('modprobe() { for _f in $FAIL; do '
            '[ "$_f" = "$1" ] && return 1; done; return 0; }\n')
    # ‏`:` בסוף — הקטע יושב באמצע `agent/init`, אחריו יש עוד קוד ואין
    # ‏`set -e`, ולכן קוד היציאה של השורה האחרונה בו אינו משמעותי שם.
    # בלעדיו, ריצה שבה רק מודול רשות נכשל הייתה יוצאת 1 על `[ -n "" ]`.
    run_dir = tmp_path / "run"; run_dir.mkdir()
    script = (f'FAIL="{" ".join(fail)}"\nRUN_DIR={run_dir.as_posix()!r}\n'
              + 'fail() { echo "FAIL: $1"; exit 3; }\n' + stub + block + "\n:\n")
    done = subprocess.run(["sh", "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=30)
    assert done.returncode == 0, done.stderr
    return done.stdout


FS_REQUIRED = ["ext4", "btrfs", "xfs", "vfat", "fat", "nls_cp437", "nls_ascii",
               "efivarfs", "crc32c_generic", "crc32c-intel"]


def test_init_names_required_and_platform_failures_on_separate_lines(tmp_path: Path):
    """מודול חובה שנכשל = ERROR נקוב בשם; מודול פלטפורמה = מצב צפוי, נקוב בשם."""
    out = run_init_block(
        tmp_path,
        modules=["hv_netvsc", "vmxnet3", "ext4", "vfat", "r8169"],
        required=FS_REQUIRED,
        fail=["hv_netvsc", "vmxnet3", "ext4"],
        dmi="Microsoft Hyper-V VMware",
    )
    err = [l for l in out.splitlines() if "ERROR required modules" in l]
    opt = [l for l in out.splitlines() if "expected on this hardware" in l]
    assert err, f"אין שורת ERROR למודול חובה שנכשל; הפלט: {out!r}"
    assert opt, f"אין שורת פלטפורמה מסווגת; הפלט: {out!r}"
    assert "ext4" in err[0] and "ext4" not in opt[0]
    assert "hv_netvsc" in opt[0] and "vmxnet3" in opt[0]
    assert "hv_netvsc" not in err[0] and "vmxnet3" not in err[0]
    # ההודעה השטוחה הישנה — שלא הבחינה בין השניים — נעלמה
    assert not re.search(r"\d+ modules did not load", out), (
        f"ההודעה השטוחה הישנה חזרה: {out!r}")
    assert (tmp_path / "run/modules.failed").read_text().splitlines() == ["ext4"]


def test_only_detected_virtual_platform_failures_are_warned(tmp_path: Path):
    out = run_init_block(tmp_path, ["hv_vmbus", "xen-blkfront", "vmxnet3"], [],
                         ["hv_vmbus", "xen-blkfront", "vmxnet3"],
                         dmi="Microsoft Hyper-V")
    assert "ImageSrv: platform modules absent" in out
    assert "hv_vmbus" in out
    assert "xen-blkfront" not in out and "vmxnet3" not in out


def test_init_has_no_old_user_visible_prefix():
    assert "imagectl init:" not in INIT.read_text(encoding="utf-8")


def test_a_required_module_failure_is_named_not_a_silent_count(tmp_path: Path):
    """הבקרה השלילית של הבאג: מודול נדרש חסר → נאמר בשם, לא ממשיך בשקט."""
    out = run_init_block(tmp_path, modules=["ext4", "hv_netvsc"],
                         required=FS_REQUIRED, fail=["ext4"])
    err = [l for l in out.splitlines() if "ERROR required modules" in l]
    assert err, f"מודול חובה נכשל ולא נאמר בשם; הפלט: {out!r}"
    assert "ext4" in err[0]
    assert "hv_netvsc" not in out  # לא נכשל — לא מדובר עליו כלל
    assert not re.search(r"\d+ modules did not load", out), (
        f"ההודעה השטוחה הישנה חזרה: {out!r}")


def test_platform_drivers_absent_on_bare_metal_are_not_an_error(tmp_path: Path):
    """התרחיש של #407 עצמו: 8 דרייברי וירטואליזציה על ברזל = מצב תקין.

    הם נקובים בשם כמידע, אף אחד אינו ERROR, ואין את המספר השטוח הישן.
    זו גם השמירה מפני 'תיקון' רחב מדי שהיה מכריז על הכול ככשל.
    """
    platform = ["hv_netvsc", "hv_storvsc", "vmxnet3", "vmw_pvscsi",
                "virtio_net", "virtio_blk", "xen-netfront", "xen-blkfront"]
    out = run_init_block(tmp_path, modules=platform + ["ext4"],
                         required=FS_REQUIRED, fail=platform)
    assert "ERROR" not in out, f"מודול פלטפורמה סווג בטעות ככשל: {out!r}"
    assert "expected on this hardware" not in out
    assert not re.search(r"\d+ modules did not load", out), (
        f"ההודעה השטוחה הישנה חזרה: {out!r}")
