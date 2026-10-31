"""‏`--with-gui`: החבילות והנתיבים של הקיוסק מוצהרים, והבנייה נעצרת בלעדיהם.

מסלול ה-GUI לא היה בר-בנייה על דביאן 13 ואיש לא ידע, כי איש לא בנה
אותו: ה-initramfs שהמעבדה הגישה בפועל לא הכיל `cage`, לא `chromium`
ולא גופן (#120). כשניסו — ‏`apt-get install fonts-ibm-plex` ענה
‏`E: Unable to locate package` ויצא 100.

ההודעה ההיא היא הבאג האמיתי: היא אינה מבדילה בין "אין חבילה כזאת
בדביאן" לבין "היא קיימת, אבל ה-sources.list כאן לא מכיל את הקומפוננטה
שלה". ‏`fonts-ibm-plex` **קיימת** בדביאן 13 — ‏6.1.1-1, ב-contrib —
וה-sources.list של השרת מכיל `main non-free-firmware` בלבד.

ומאחוריה הסתתר באג שני, שקט לגמרי: הסקריפט העתיק
‏`/usr/share/fonts/opentype/ibm-plex`, והחבילה מתקינה ל-`truetype`.
‏`if [ -d "$dir" ]` דילג על הנתיב שאינו קיים בלי מילה — כך שגם אחרי
שהחבילה הייתה מותקנת, הקיוסק היה נבנה נקי ובלי גופן.

הבדיקות מריצות את הקטעים האמיתיים מתוך `tools/build_initramfs.sh`
ולא בודקות את הטקסט שלהם, למעט מקום אחד שבו הטקסט הוא הנקודה: ששתי
הרשימות שנבדקות הן אותן רשימות שמותקנות ומועתקות.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

BUILDER = Path(__file__).resolve().parent.parent / "tools" / "build_initramfs.sh"
BASH = shutil.which("bash")

#: מערכי bash ו-`case` — הקטעים רצים ב-bash אמיתי; בלעדיו אין כאן
#: בדיקה, ובמקום שבו הוא אמור להיות זו תקלה ולא סיבה לדלג (#52).
pytestmark = requires_native("bash", why="GUI_PACKAGES ו-GUI_PATHS הם מערכי bash")

#: כל החבילות המוצהרות זמינות ב-apt חוץ מזו — המצב האמיתי על דביאן 13.
CONTRIB_ONLY = "fonts-ibm-plex"


def _lines() -> list[str]:
    return BUILDER.read_text(encoding="utf-8").split("\n")


def bash_path(path: Path) -> str:
    """נתיב שאפשר לשרשר: ‏`$ROOT$_p` מדביק שני נתיבים מוחלטים זה לזה.

    ‏`C:/x` + `C:/y` הוא נתיב עם נקודתיים באמצע, שווינדוס דוחה; בצורת
    ‏msys (‏`/c/x`) השרשור תקין, וזו גם הצורה שה-bash של Git מבין.
    """
    text = path.as_posix()
    if os.name == "nt" and len(text) > 1 and text[1] == ":":
        return "/" + text[0].lower() + text[2:]
    return text


def preflight_snippet() -> str:
    """ההצהרה ובדיקת הזמינות שלפני apt, כפי שהן בסקריפט."""
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.startswith("GUI_PACKAGES=("))
    end = next(i for i, l in enumerate(lines[start:], start) if l == "fi")
    return "\n".join(lines[start:end + 1])


def path_check_snippet() -> str:
    """בדיקת הנתיבים וההעתקה שאחרי ההתקנה, כפי שהן בסקריפט."""
    lines = _lines()
    start = next(i for i, l in enumerate(lines) if l.strip() == '_no_path=""')
    copy = next(i for i, l in enumerate(lines[start:], start)
                if 'cp -a "$_p/." "$ROOT$_p/"' in l)
    end = next(i for i, l in enumerate(lines[copy:], copy) if l.strip() == "done")
    return "\n".join(lines[start:end + 1])


def declared_array(name: str) -> list[str]:
    """התוכן של מערך מוצהר בסקריפט, בלי הערות."""
    body = BUILDER.read_text(encoding="utf-8").split(f"{name}=(", 1)[1].split(")", 1)[0]
    words: list[str] = []
    for line in body.split("\n"):
        words += line.split("#", 1)[0].split()
    return words


def run_preflight(with_gui: int = 1, unavailable: tuple[str, ...] = (),
                  no_version: tuple[str, ...] = ()):
    """מריץ את בדיקת הזמינות האמיתית מול apt-cache מזויף.

    ‏`apt-cache` כאן היא פונקציית bash ולא סקריפט על ה-PATH: אין תלות
    בהרשאת הרצה, בקצה שורה או בפרשנות shebang של הסביבה, והבדיקה
    מתנהגת אותו דבר במעבדה, ב-CI ועל עמדת הפיתוח בווינדוס.
    """
    stub = f"""
_fake_unknown={" ".join(unavailable)!r}
_fake_no_version={" ".join(no_version)!r}
apt-cache() {{
    # לא מוכרת בכלל: פלט ריק, יציאה 0 — בדיוק מה ש-apt עושה.
    for _m in $_fake_unknown; do [ "$2" = "$_m" ] && return 0; done
    for _n in $_fake_no_version; do
        [ "$2" = "$_n" ] && {{ printf '%s:\\n  Candidate: (none)\\n' "$2"; return 0; }}
    done
    printf '%s:\\n  Installed: (none)\\n  Candidate: 1.2.3\\n' "$2"
}}
"""
    script = f"WITH_GUI={with_gui}\nSKIP_APT=0\n{stub}\n" + preflight_snippet()
    return subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=90)


def run_path_check(tmp_path: Path, present: list[str]):
    """מריץ את בדיקת הנתיבים האמיתית על עץ מזויף.

    הנתיבים המוצהרים מוחלפים בנתיבים תחת tmp_path — הבדיקה היא של
    הלוגיקה, לא של המכונה שעליה היא רצה.
    """
    fake = tmp_path / "sysroot"
    root = tmp_path / "root"
    root.mkdir()
    paths = []
    for rel in ("usr/share/fontconfig", "usr/share/fonts/truetype/ibm-plex", "etc/fonts"):
        target = fake / rel
        if rel in present:
            target.mkdir(parents=True)
            (target / "a-file").write_text("x", encoding="utf-8")
        paths.append(bash_path(target))

    declaration = "GUI_PATHS=(" + " ".join(f"'{p}'" for p in paths) + ")"
    script = (f"ROOT={bash_path(root)!r}\n{declaration}\n" + path_check_snippet())
    done = subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", errors="replace",
                          timeout=90)
    return done, root


# --- זמינות החבילות, לפני apt ------------------------------------------------


def test_the_build_stops_before_apt_when_a_declared_package_is_unavailable():
    """הבקרה השלילית של #120.

    בלי זה `apt-get` הוא שנכשל, ב-exit 100, עם הודעה שאינה מבדילה בין
    חבילה שאינה קיימת לחבילה שהקומפוננטה שלה כבויה.
    """
    done = run_preflight(unavailable=(CONTRIB_ONLY,))
    assert done.returncode != 0, "חבילה חסרה עברה את הבדיקה — ההצהרה חסרת ערך"
    assert CONTRIB_ONLY in done.stderr, done.stderr


def test_the_message_says_which_component_the_package_needs():
    """"לא נמצאה" אינה תשובה — היא בדיוק ההודעה שהשאירה את זה פתוח."""
    done = run_preflight(unavailable=(CONTRIB_ONLY,))
    assert "contrib" in done.stderr, done.stderr


def test_a_package_apt_knows_but_cannot_install_counts_as_missing():
    """‏`Candidate: (none)` הוא "אין", לא "יש".

    ‏`apt-cache policy` יוצא 0 גם על חבילה שאין ממנה גרסה בת-התקנה,
    וגם על חבילה שאינה מוכרת בכלל. קוד היציאה אינו הראיה — שורת
    ה-Candidate היא (עיקרון 5).
    """
    done = run_preflight(no_version=(CONTRIB_ONLY,))
    assert done.returncode != 0, "‏Candidate: (none) נספר כזמין"
    assert CONTRIB_ONLY in done.stderr, done.stderr


def test_the_preflight_names_every_unavailable_package_at_once():
    """שלוש חסרות = הודעה אחת, לא שלוש בנייות של דקות כל אחת."""
    absent = ("libpango-1.0-0", CONTRIB_ONLY, "libdrm2")
    done = run_preflight(unavailable=absent)
    assert done.returncode != 0
    for pkg in absent:
        assert pkg in done.stderr, f"{pkg} לא הוזכר: {done.stderr}"


def test_the_preflight_passes_when_every_declared_package_has_a_candidate():
    """הצהרה שנכשלת תמיד היא הצהרה שיכבו — הצד החיובי נבדק גם הוא."""
    done = run_preflight()
    assert done.returncode == 0, done.stderr
    assert done.stderr.strip() == "", done.stderr


def test_a_build_without_the_kiosk_never_looks_at_the_kiosk_packages():
    """תחנת כיתה אינה צריכה קיוסק, ואסור שהבדיקה הזאת תחסום אותה.

    הבנייה הרגילה עוברת נקי על אותה מכונה שבה `fonts-ibm-plex` אינה
    זמינה — זו הייתה המציאות במעבדה, וזה מה שחייב להישאר.
    """
    done = run_preflight(with_gui=0, unavailable=tuple(declared_array("GUI_PACKAGES")))
    assert done.returncode == 0, done.stderr


# --- הנתיבים, אחרי ההתקנה ----------------------------------------------------


def test_the_build_stops_when_a_declared_path_is_missing_after_install(tmp_path: Path):
    """הבאג השקט של #120: ‏`if [ -d "$dir" ]` דילג בלי מילה.

    ככה נתיב גופן שגוי — ‏`opentype` במקום `truetype` — ייצר קיוסק
    בלי גופן עברי, מבנייה שנראתה נקייה לחלוטין.
    """
    done, _ = run_path_check(tmp_path, present=["usr/share/fontconfig", "etc/fonts"])
    assert done.returncode != 0, "נתיב חסר דולג — בדיוק הבאג"
    assert "ibm-plex" in done.stderr, done.stderr


def test_the_missing_path_report_names_all_of_them_at_once(tmp_path: Path):
    done, _ = run_path_check(tmp_path, present=["etc/fonts"])
    assert done.returncode != 0
    assert "fontconfig" in done.stderr, done.stderr
    assert "ibm-plex" in done.stderr, done.stderr


def test_every_declared_path_is_copied_when_they_are_all_there(tmp_path: Path):
    """הצד החיובי: מה שהוצהר גם נארז, ולא רק נספר."""
    everything = ["usr/share/fontconfig", "usr/share/fonts/truetype/ibm-plex", "etc/fonts"]
    done, root = run_path_check(tmp_path, present=everything)
    assert done.returncode == 0, done.stderr
    copied = [p.name for p in root.rglob("a-file")]
    assert len(copied) == len(everything), f"נארזו {len(copied)} מתוך {len(everything)}"


# --- ההצהרה היא זו שמותקנת, וזו שמועתקת --------------------------------------


def test_the_font_path_is_the_one_debian_actually_installs():
    """‏fonts-ibm-plex מתקינה ל-truetype. ‏opentype/ibm-plex לא היה קיים מעולם."""
    assert "/usr/share/fonts/truetype/ibm-plex" in declared_array("GUI_PATHS")
    assert "opentype/ibm-plex" not in BUILDER.read_text(encoding="utf-8")


def test_the_fontconfig_configuration_travels_with_the_fonts():
    """גופן בלי תצורת fontconfig הוא גופן שכרומיום לא ימצא.

    ושני הנתיבים ולא אחד: קובצי `/etc/fonts/conf.d` הם קישורים
    סימבוליים אל `/usr/share/fontconfig/conf.avail`, וקישור יתום בתוך
    ה-initramfs שקול לקובץ חסר.
    """
    paths = declared_array("GUI_PATHS")
    assert "/etc/fonts" in paths
    assert "/usr/share/fontconfig" in paths


def test_the_hebrew_font_package_is_declared():
    """הקיוסק הוא עברית RTL — גופן שאינו מכסה עברית אינו גופן כאן.

    ‏`fonts-ibm-plex` כוללת את `IBMPlexSansHebrew`, שהוא בדיוק מה
    ש-`console.css` מבקש. החלפתה בגופן אחר היא שינוי עיצוב, לא פרט
    בנייה — ולכן אם השם הזה משתנה, זה קורה בדיון ולא בשקט.
    """
    assert CONTRIB_ONLY in declared_array("GUI_PACKAGES")


def test_the_packages_apt_installs_are_the_packages_that_were_checked():
    """שתי רשימות שמתפצלות הן איך הצהרה נשחקת בשקט.

    כאן הטקסט הוא הנקודה: הבדיקה שלפני apt חסרת ערך אם `apt-get
    install` מקבל רשימה אחרת.
    """
    text = BUILDER.read_text(encoding="utf-8")
    assert 'apt-get install -y --no-install-recommends "${GUI_PACKAGES[@]}"' in text


@pytest.mark.parametrize("package", [
    "fonts-ibm-plex", "fontconfig-config", "libpango-1.0-0",
    "libpangocairo-1.0-0", "libpangoft2-1.0-0", "libcairo2", "libpixman-1-0",
    "libharfbuzz0b", "libfribidi0", "libfreetype6", "libfontconfig1",
    "libglib2.0-0t64", "libdrm2",
])
def test_native_runtime_packages_are_declared(package: str):
    assert package in declared_array("GUI_PACKAGES")


def test_browser_packages_and_data_directories_are_not_packed():
    assert not {"cage", "chromium", "seatd", "libgl1-mesa-dri", "libinput-bin",
                "xkb-data"} & set(declared_array("GUI_PACKAGES"))
    assert set(declared_array("GUI_PATHS")) == {
        "/usr/share/fonts/truetype/ibm-plex", "/etc/fonts", "/usr/share/fontconfig",
    }


def run_native_pack(tmp_path: Path, fault: str = ""):
    """Run the real optional GUI block with fake make/ldd, no compiler or apt."""
    checkout = tmp_path / "checkout"
    gui = checkout / "native-gui"
    gui.mkdir(parents=True)
    root = tmp_path / "root"
    (root / "usr/bin").mkdir(parents=True)
    # #1493: coreutils' nice, packed by copy_bin (BINARIES) before this block.
    # Debian 13's busybox-static has no nice applet, so a $ROOT/bin/nice link
    # must not satisfy the gate -- it is planted on the "nice" fault too.
    (root / "bin").mkdir()
    (root / "bin/nice").write_text("busybox applet link", encoding="utf-8")
    if fault != "nice":
        nice = root / "usr/bin/nice"
        nice.write_text("#!/bin/sh\nshift 2\nexec \"$@\"\n", encoding="utf-8")
        nice.chmod(0o755)
    fonts = tmp_path / "fonts"
    fonts.mkdir()
    faces = ["IBMPlexSansHebrew-Regular", "IBMPlexSansHebrew-Medium",
             "IBMPlexSansHebrew-SemiBold", "IBMPlexSansHebrew-Bold",
             "IBMPlexMono-Regular", "IBMPlexMono-Medium"]
    for face in faces:
        if fault != "font" or face != faces[-1]:
            (fonts / f"{face}.ttf").write_text("font", encoding="utf-8")
    (fonts / "unneeded.ttf").write_text("not packed", encoding="utf-8")
    config = tmp_path / "config"
    config.mkdir()
    (config / "fonts.conf").write_text("config", encoding="utf-8")
    lib = tmp_path / "libnative.so.1"
    lib.write_text("library", encoding="utf-8")
    loader = tmp_path / "ld-linux.so.2"
    loader.write_text("loader", encoding="utf-8")
    # #690: the GUI block now also compiles imagesrv-monitor from AGENT_DIR.
    agent = checkout / "agent"
    agent.mkdir()
    (agent / "monitor.c").write_text("int main(void){return 0;}", encoding="utf-8")
    (agent / "hmac_sha256.c").write_text("void hmac_sha256(void){}\n", encoding="utf-8")
    text = BUILDER.read_text(encoding="utf-8")
    block = text.split("# --- the kiosk (optional):", 1)[1]
    block = block.split("# --- kernel modules and firmware", 1)[0]
    block = block[block.index('if [ "$WITH_GUI" -eq 1 ]; then'):]
    block = block.replace("/usr/share/fonts/truetype/ibm-plex", bash_path(fonts))
    ldd_output = ("libmissing.so => not found" if fault == "library" else
                  f"libnative.so.1 => {bash_path(lib)} (0x123)\n\t{bash_path(loader)} (0x456)")
    # 17/10: the builder renders the last-resort screen with the binary
    # (--splash-raw PATH --size 640x240); the fake writes that many bytes,
    # or too few on the "splash" fault.
    splash_bytes = 10 if fault == "splash" else 640 * 240 * 4
    make_body = ("return 9" if fault == "make" else "return 0" if fault == "binary" else
                 "printf '%s\\n' '#!/bin/sh' "
                 f"'[ \"$1\" = --splash-raw ] && head -c {splash_bytes} /dev/zero > \"$2\"' 'exit 0' "
                 '> "$GUI_DIR/imagesrv-station-gui"; '
                 'chmod +x "$GUI_DIR/imagesrv-station-gui"')
    script = f"""set -euo pipefail
WITH_GUI=1
SKIP_APT=1
SCRIPT_DIR={bash_path(checkout / 'tools')!r}
AGENT_DIR={bash_path(agent)!r}
ROOT={bash_path(root)!r}
GUI_PATHS=({bash_path(fonts)!r} {bash_path(config)!r})
make() {{ {make_body}; }}
ldd() {{ printf '%s\\n' {shlex.quote(ldd_output)}; }}
gcc() {{ while [ "$1" != "-o" ]; do shift; done; \
  printf '#!/bin/sh\\nexit 0\\n' > "$2"; chmod +x "$2"; }}
{block}
"""
    # SCRIPT_DIR/../native-gui must resolve through an existing directory.
    (checkout / "tools").mkdir()
    done = subprocess.run([BASH, "-c", script], stdin=subprocess.DEVNULL,
                          capture_output=True, encoding="utf-8", timeout=90)
    return done, root, fonts, lib, loader


def test_native_pack_includes_binary_closure_fonts_and_placeholder(tmp_path):
    done, root, fonts, lib, loader = run_native_pack(tmp_path)
    assert done.returncode == 0, done.stderr
    assert (root / "usr/bin/imagesrv-station-gui").is_file()
    # #690: the remote monitor rides in the same GUI image.
    assert (root / "usr/bin/imagesrv-monitor").is_file()
    for source in (lib, loader):
        assert (root / bash_path(source).lstrip("/")).read_bytes() == source.read_bytes()
    packed_fonts = root / bash_path(fonts).lstrip("/")
    assert len(list(packed_fonts.glob("*.ttf"))) == 6
    assert not (packed_fonts / "unneeded.ttf").exists()
    wrapper = (root / "usr/bin/imagesrv-kiosk").read_text()
    # The kiosk is now the native-GUI bridge (feat/native-gui): it sources
    # guibridge.sh and enters gui_main, which owns --auth-cmd/--state/stdout.
    assert '. "$LIB_DIR/guibridge.sh"' in wrapper
    assert 'gui_main "$@"' in wrapper
    assert "exec /usr/bin/imagesrv-station-gui\n" not in wrapper
    assert "--demo" not in wrapper
    # 17/10: the last-resort screen, rendered by the binary at build time.
    assert (root / "usr/share/imagesrv/splash-640x240.raw").stat().st_size == 640 * 240 * 4


@pytest.mark.parametrize("fault, message", [
    ("make", ""), ("binary", "native GUI binary missing"),
    ("library", "unresolved native GUI libraries"), ("font", "font face missing"),
    ("nice", "no nice binary packed"),
    ("splash", "splash-640x240.raw is not 640x240 XRGB8888"),
])
def test_native_pack_stops_on_build_or_missing_runtime_files(tmp_path, fault, message):
    done, root, *_ = run_native_pack(tmp_path, fault)
    assert done.returncode != 0
    assert message in done.stderr
    assert not (root / "usr/bin/imagesrv-kiosk").exists()
