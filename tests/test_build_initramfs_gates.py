"""‏#1125 — קובץ חסר עוצר את בניית ה-initramfs; שתי בניות = אותם בייטים.

הדפוס של #33/#78/#84 חזר בארבעה מקומות ב-`tools/build_initramfs.sh`:
הלודר ו-libnss (`cp … 2>/dev/null || true`), ‏`modules.order`/`modules.builtin`
(‏`|| true`), תת-עצי מודולים (`if [ -d ]`) ו-firmware (`if [ -d ]`). בכולם
קובץ חסר הוא בנייה שיצאה 0 והכשל מתגלה מול מכונה — curl בלי DNS,
‏"N modules did not load", ‏NIC בלי קושחה — בלי רמז למה.

והחמישי: ‏`find . | cpio | gzip -9` ארז לפי סדר inode, עם mtime של הבנייה
ועם חותמת זמן של gzip, ולכן שתי בניות מאותו מקור נתנו hash שונה — ו-#1078
(‏hash של initrd ב-grub.cfg) לא יכול לעמוד על זה.

הבדיקות מריצות את הקטעים האמיתיים מתוך הבנאי על עצים מזויפים ולא
בודקות את הטקסט: ניסוח אחר שיבלע קובץ חסר צריך להיכשל כאן. נתיבים
מוחלטים (`/lib64/…`, ‏`/lib/firmware`) מוחלפים בנתיב זמני — כמו
‏`run_native_pack` ב-`test_initramfs_gui.py`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_initramfs_modules import KVER, LAYOUT, declared

REPO = Path(__file__).resolve().parent.parent
BUILDER = REPO / "tools" / "build_initramfs.sh"
BASH = shutil.which("bash")

pytestmark = requires_native("bash", why="הקטעים מהבנאי הם bash")


def bash_path(path: Path) -> str:
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def _lines() -> list[str]:
    return BUILDER.read_text(encoding="utf-8").split("\n")


def run(tmp_path: Path, script: str, timeout: int = 90) -> subprocess.CompletedProcess:
    """דרך קובץ ולא `bash -c`: קטע של 150 שורות חוצה את ~8K התווים ששורת
    הפקודה בווינדוס מעבירה, ונחתך ל-`unexpected EOF` באמצע מחרוזת."""
    f = tmp_path / "snippet.sh"
    f.write_text("set -euo pipefail\n" + script, encoding="utf-8", newline="\n")
    return subprocess.run([BASH, bash_path(f)],
                          stdin=subprocess.DEVNULL, capture_output=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


# --- 1. the loader and the NSS libraries ------------------------------------------


def loader_snippet() -> str:
    lines = _lines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith("# The dynamic loader and the NSS libraries"))
    end = next(i for i, l in enumerate(lines[start:], start) if l == "done")
    return "\n".join(lines[start:end + 1])


def run_loader(tmp_path: Path, *, hide: str = "", prepacked: bool = False):
    """הלולאה האמיתית, עם ארבעת הנתיבים המוחלטים מופנים לעץ מזויף."""
    snippet = loader_snippet()
    absolute = sorted(set(re.findall(r"/lib(?:64)?/[^ \\\n\"']+\.so\.\d", snippet)))
    assert len(absolute) == 4, absolute
    sysroot, root = tmp_path / "sysroot", tmp_path / "root"
    root.mkdir()
    fake = {}
    for a in absolute:
        f = sysroot / a.lstrip("/")
        f.parent.mkdir(parents=True, exist_ok=True)
        if Path(a).name != hide:
            f.write_bytes(b"lib:" + a.encode())
        fake[a] = bash_path(f)
        snippet = snippet.replace(a, fake[a])
    if prepacked:
        # copy_libs already put the loader under ROOT (it is in every ldd
        # closure): the `-n` copy skips it and must not be a failure.
        loader = next(a for a in absolute if "ld-linux" in a)
        packed = root / fake[loader].lstrip("/")
        packed.parent.mkdir(parents=True)
        packed.write_bytes(b"lib:" + loader.encode())
    done = run(tmp_path, f"ROOT={bash_path(root)!r}\n{snippet}\n")
    return done, root, fake


def test_a_missing_nss_library_stops_the_build(tmp_path: Path):
    """הבאג: libnss_dns חסר → הבנייה יצאה 0, ו-curl בתחנה לא פתר שמות."""
    done, _, _ = run_loader(tmp_path, hide="libnss_dns.so.2")
    assert done.returncode != 0, (
        "libnss_dns.so.2 חסר עבר את הבנייה — ה-DNS ייכשל בתחנה בלי רמז: "
        f"rc={done.returncode} stderr={done.stderr!r}")
    assert "libnss_dns.so.2" in done.stderr, done.stderr


def test_a_missing_loader_stops_the_build(tmp_path: Path):
    done, _, _ = run_loader(tmp_path, hide="ld-linux-x86-64.so.2")
    assert done.returncode != 0, done.stderr
    assert "ld-linux-x86-64.so.2" in done.stderr, done.stderr


@pytest.mark.parametrize("prepacked", [False, True])
def test_all_four_present_are_packed_byte_for_byte(tmp_path: Path, prepacked: bool):
    """הצד החיובי, גם כשהלודר כבר נארז דרך copy_libs (‏`cp -n` מדלג ב-0)."""
    done, root, fake = run_loader(tmp_path, prepacked=prepacked)
    assert done.returncode == 0, done.stderr
    for a, path in fake.items():
        packed = root / path.lstrip("/")
        assert packed.read_bytes() == b"lib:" + a.encode(), f"לא נארז: {a}"


# --- 2. modules.order / modules.builtin --------------------------------------------


def modules_meta_snippet() -> str:
    lines = _lines()
    start = next(i for i, l in enumerate(lines)
                 if l.startswith('echo "module dependency closure:')) + 1
    end = next(i for i, l in enumerate(lines[start:], start)
               if "modules.{order,builtin}" in l)
    return "\n".join(lines[start:end + 1])


def run_modules_meta(tmp_path: Path, present: list[str]):
    src, root = tmp_path / "src", tmp_path / "root" / "lib" / "modules" / KVER
    src.mkdir()
    root.mkdir(parents=True)
    for name in present:
        (src / name).write_text(name, encoding="utf-8")
    done = run(tmp_path, f"ROOT={bash_path(tmp_path / 'root')!r}\nKVER={KVER!r}\n"
               f"MODSRC={bash_path(src)!r}\n{modules_meta_snippet()}\n")
    return done, root


def test_a_missing_modules_order_stops_the_build(tmp_path: Path):
    """הבאג: `cp … || true` — depmod רץ על עץ בלי modules.order ויצא 0."""
    done, root = run_modules_meta(tmp_path, ["modules.builtin", "modules.builtin.modinfo"])
    assert done.returncode != 0, (
        "modules.order חסר עבר — depmod יבנה אינדקס חלקי ו-init ידווח "
        f"'N modules did not load' בלי רמז: rc={done.returncode} stderr={done.stderr!r}")
    assert "modules.order" in done.stderr, done.stderr


def test_a_missing_modules_builtin_stops_the_build(tmp_path: Path):
    done, _ = run_modules_meta(tmp_path, ["modules.order"])
    assert done.returncode != 0, done.stderr
    assert "modules.builtin" in done.stderr, done.stderr


def test_the_depmod_inputs_are_all_copied(tmp_path: Path):
    files = ["modules.order", "modules.builtin", "modules.builtin.modinfo",
             "modules.builtin.alias.bin"]
    done, root = run_modules_meta(tmp_path, files)
    assert done.returncode == 0, done.stderr
    assert sorted(p.name for p in root.iterdir()) == sorted(files)


# --- 3. the declared module trees -------------------------------------------------


def declared_subdirs() -> list[str]:
    """‏`MODULE_SUBDIRS` — ההערות בתוכו מכילות `)` ("(issue #12)"), ולכן
    ‏`declared()` של test_initramfs_modules נחתך אחרי שבעה; כאן קודם
    מסירים הערות, ורק אז מחפשים את הסוגר."""
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.startswith("MODULE_SUBDIRS=("))
    names: list[str] = []
    for line in lines[start:]:
        code = line.split("#", 1)[0].replace("MODULE_SUBDIRS=(", "")
        closed = ")" in code
        names += code.split(")", 1)[0].split()
        if closed:
            break
    assert len(names) == 23, names   # #1441: + kernel/drivers/cpufreq; #1914: + kernel/drivers/pci/controller (vmd)
    return names


def subdirs_snippet() -> str:
    """מהצהרת העצים ועד ה-`fi` של דוח החוסרים — הקטע שבו כולם נאספים."""
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.startswith("MODULE_SUBDIRS=("))
    report = next(i for i, l in enumerate(lines[start:], start)
                  if 'echo "missing required modules:' in l)
    end = next(i for i, l in enumerate(lines[report:], report) if l == "fi")
    return "\n".join(lines[start:end + 1])


def run_subdirs(tmp_path: Path, *, hide: tuple[str, ...] = ()):
    """עץ שבו כל מודול מוצהר קיים, וכל תת-עץ מוצהר קיים — חוץ מ-`hide`."""
    src, root = tmp_path / "src", tmp_path / "root"
    for mod in declared("REQUIRED_MODULES") + declared("REQUIRED_FS_MODULES"):
        d = src / LAYOUT[mod]
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{mod}.ko.xz").write_bytes(b"")
    for sub in declared_subdirs():
        if sub not in hide:
            (src / sub).mkdir(parents=True, exist_ok=True)
    # ‏FUSE built-in, כמו בקרנל דביאן 13 — אחרת שער ה-FUSE (#1368) עוצר.
    (src / "modules.builtin").write_text("kernel/fs/fuse/fuse.ko\n", encoding="utf-8")
    (root / "lib" / "modules" / KVER).mkdir(parents=True)
    done = run(tmp_path, f"ROOT={bash_path(root)!r}\nKVER={KVER!r}\n"
               f"MODSRC={bash_path(src)!r}\nWITH_GUI=0\nWITH_INSTALLER=0\n{subdirs_snippet()}\n")
    return done, root / "lib" / "modules" / KVER


def test_a_declared_tree_that_is_missing_stops_the_build(tmp_path: Path):
    """הבאג: `if [ -d ]` דילג על kernel/drivers/usb/host בשקט — מקלדת USB
    בלי בקר, ‏#77 מחדש, ובנייה שיצאה 0."""
    done, _ = run_subdirs(tmp_path, hide=("kernel/drivers/usb/host",))
    assert done.returncode != 0, (
        "תת-עץ מוצהר חסר עבר את הבנייה: "
        f"rc={done.returncode} stderr={done.stderr!r}")
    assert "kernel/drivers/usb/host" in done.stderr, done.stderr


def test_every_missing_tree_is_named_at_once(tmp_path: Path):
    hide = ("kernel/drivers/net/phy", "kernel/drivers/hid", "kernel/drivers/input/serio")
    done, _ = run_subdirs(tmp_path, hide=hide)
    assert done.returncode != 0, done.stderr
    for sub in hide:
        assert sub in done.stderr, (sub, done.stderr)


def test_all_declared_trees_present_is_a_clean_build(tmp_path: Path):
    """הצד החיובי — כל 21 העצים קיימים בקרנל דביאן 13 הרגיל (20 נמדדו
    19/09, ‏kernel/drivers/input/mouse נוסף ב-#1204), ולכן אין רשימת
    אופציונליים: הרשימה המוצהרת היא כולה חובה."""
    done, packed = run_subdirs(tmp_path)
    assert done.returncode == 0, done.stderr
    for sub in declared_subdirs():
        assert (packed / sub).is_dir(), f"{sub} לא נארז"
    assert "OPTIONAL_MODULE_SUBDIRS" not in BUILDER.read_text(encoding="utf-8")


# --- 4. firmware ---------------------------------------------------------------------


def firmware_snippet() -> str:
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.startswith('for fw in "${FIRMWARE_DIRS'))
    while lines[start - 1].strip() and not lines[start - 1].startswith("#"):
        start -= 1
    end = next(i for i, l in enumerate(lines[start:], start) if l.startswith("# --- pack"))
    return "\n".join(lines[start:end]).rstrip()


def run_firmware(tmp_path: Path, dirs: list[str], present: list[str]):
    fw, root = tmp_path / "firmware", tmp_path / "root"
    root.mkdir()
    for name in present:
        (fw / name).mkdir(parents=True)
        (fw / name / f"{name}.fw").write_bytes(b"blob")
    # only the source side (`"/lib/firmware/$fw"`), not `$ROOT/lib/firmware`
    snippet = firmware_snippet().replace('"/lib/firmware/', '"' + bash_path(fw) + "/")
    # RADEON_LEGACY_FW: declared 0 at the script's top (#1937); the region reads it under set -u
    done = run(tmp_path, f"ROOT={bash_path(root)!r}\nFIRMWARE_DIRS=({' '.join(dirs)})\nRADEON_LEGACY_FW=0\n{snippet}\n")
    return done, root


def test_missing_rtl_nic_firmware_stops_the_build(tmp_path: Path):
    """הבאג: `if [ -d ]` — NIC של Realtek בלי קושחה, ובנייה שיצאה 0."""
    done, _ = run_firmware(tmp_path, ["rtl_nic"], present=[])
    assert done.returncode != 0, (
        f"rtl_nic חסר עבר את הבנייה: rc={done.returncode} stderr={done.stderr!r}")
    assert "rtl_nic" in done.stderr, done.stderr


def test_missing_i915_firmware_stops_the_gui_build(tmp_path: Path):
    """‏`--with-gui` מוסיף i915 ל-FIRMWARE_DIRS; חסר → מסך שחור, לא בנייה נקייה."""
    done, _ = run_firmware(tmp_path, ["rtl_nic", "i915"], present=["rtl_nic"])
    assert done.returncode != 0, done.stderr
    assert "i915" in done.stderr, done.stderr


def test_every_declared_firmware_dir_is_packed(tmp_path: Path):
    done, root = run_firmware(tmp_path, ["rtl_nic", "i915"], present=["rtl_nic", "i915"])
    assert done.returncode == 0, done.stderr
    for name in ("rtl_nic", "i915"):
        assert (root / "lib" / "firmware" / name / f"{name}.fw").read_bytes() == b"blob"


def test_the_default_firmware_is_rtl_nic_and_gui_adds_i915():
    text = BUILDER.read_text(encoding="utf-8")
    assert 'FIRMWARE_DIRS=("rtl_nic")' in text
    assert "FIRMWARE_DIRS+=(i915)" in text


# --- 5. reproducible pack (#1078) ---------------------------------------------------


def pack_snippet() -> str:
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.startswith("# --- pack"))
    end = next(i for i, l in enumerate(lines[start:], start) if l.startswith("SIZE=$(du"))
    return "\n".join(lines[start:end])


def test_the_pack_command_is_reproducible_by_construction():
    """מה שאפשר לבדוק בלי cpio: סדר קבוע, mtime קבוע, בלי inode ובלי חותמת gzip."""
    pack = pack_snippet()
    assert "LC_ALL=C sort -z" in pack
    assert "-print0" in pack and "--null" in pack
    assert "--reproducible" in pack
    assert re.search(r"gzip -9n\b|gzip -n", pack), pack
    assert 'touch -h -d "@$SOURCE_DATE_EPOCH"' in pack
    assert "git -C" in pack and "--format=%ct" in pack
    assert "--source-date-epoch" in BUILDER.read_text(encoding="utf-8")


def fake_root(tmp_path: Path, name: str, order: list[str], mtime: int) -> Path:
    root = tmp_path / name
    (root / "usr" / "bin").mkdir(parents=True)
    (root / "etc").mkdir()
    for rel in order:
        p = root / rel
        p.write_bytes(rel.encode())
        os.utime(p, (mtime, mtime))
    try:
        os.symlink("busybox", root / "usr" / "bin" / "sh")   # `touch -h` must not follow it
    except OSError:
        pass                                                # ווינדוס בלי הרשאת symlink
    return root


def pack(tmp_path: Path, root: Path, name: str, epoch: int) -> subprocess.CompletedProcess:
    out = tmp_path / name
    script = (f"export SOURCE_DATE_EPOCH={epoch}\nSCRIPT_DIR=/nonexistent\n"
              f"ROOT={bash_path(root)!r}\nOUTPUT={bash_path(out)!r}\n{pack_snippet()}\n")
    done = run(tmp_path, script)
    assert done.returncode == 0, done.stderr
    return out


@requires_native("cpio", "gzip", why="האריזה עצמה רצה במעבדה")
def test_two_packs_of_the_same_tree_are_the_same_bytes(tmp_path: Path):
    """שני עצים זהים בתוכן, שנוצרו בסדר אחר ועם mtime אחר → אותו sha256."""
    files = ["init", "etc/passwd", "usr/bin/busybox", "usr/bin/curl"]
    a = fake_root(tmp_path, "a", files, mtime=1_700_000_000)
    b = fake_root(tmp_path, "b", list(reversed(files)), mtime=1_700_009_999)
    out_a = pack(tmp_path, a, "a.cpio.gz", epoch=1_758_000_000)
    out_b = pack(tmp_path, b, "b.cpio.gz", epoch=1_758_000_000)
    ha = hashlib.sha256(out_a.read_bytes()).hexdigest()
    hb = hashlib.sha256(out_b.read_bytes()).hexdigest()
    assert ha == hb, f"שתי בניות מאותו מקור נבדלות: {ha} != {hb}"


@requires_native("cpio", "gzip", why="האריזה עצמה רצה במעבדה")
def test_a_different_epoch_is_a_different_archive(tmp_path: Path):
    """הצד השני: ה-epoch באמת נכנס לארכיון — אחרת "זהה" היה ריק מתוכן."""
    files = ["init", "usr/bin/busybox"]
    a = fake_root(tmp_path, "a", files, mtime=1_700_000_000)
    b = fake_root(tmp_path, "b", files, mtime=1_700_000_000)
    out_a = pack(tmp_path, a, "a.cpio.gz", epoch=1_758_000_000)
    out_b = pack(tmp_path, b, "b.cpio.gz", epoch=1_758_000_001)
    assert out_a.read_bytes() != out_b.read_bytes()


def test_a_non_numeric_epoch_is_refused(tmp_path: Path):
    root = fake_root(tmp_path, "a", ["init"], mtime=1_700_000_000)
    done = run(tmp_path, f"export SOURCE_DATE_EPOCH=yesterday\nSCRIPT_DIR=/nonexistent\n"
               f"ROOT={bash_path(root)!r}\nOUTPUT={bash_path(tmp_path / 'x')!r}\n"
               f"{pack_snippet()}\n")
    assert done.returncode != 0
    assert "SOURCE_DATE_EPOCH" in done.stderr


def test_no_epoch_and_no_git_is_a_stopped_build_not_a_one_off_hash(tmp_path: Path):
    """עיקרון 5: בלי epoch ובלי git אין hash יציב — עוצרים, לא נופלים ל-`date`."""
    root = fake_root(tmp_path, "a", ["init"], mtime=1_700_000_000)
    done = run(tmp_path, f"unset SOURCE_DATE_EPOCH\nSCRIPT_DIR={bash_path(tmp_path / 'nogit')!r}\n"
               f"ROOT={bash_path(root)!r}\nOUTPUT={bash_path(tmp_path / 'x')!r}\n"
               f"{pack_snippet()}\n")
    assert done.returncode != 0
    assert "SOURCE_DATE_EPOCH" in done.stderr
    assert not (tmp_path / "x").exists()
