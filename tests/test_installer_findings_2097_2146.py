"""ממצאים קטנים במתקין וב-DCUI מבדיקות ה-rc של 24/10 (#2097, ‏#2146).

כל טסט כאן בודק ממצא אחד, דרך הקוד האמיתי ולא דרך מחרוזת בקובץ:

* ‏#2146 ס' 2 — ‏`Documentation=` ביחידות systemd מפנה לריפו הציבורי; בשרת
  במכללה קישור לריפו הפרטי אינו נפתח.
* ‏#2146 ס' 3 — כותרת המשנה של כונן שנוסף לא מציגה `/dev/sdX`: השם נקבע
  בזמן ההוספה ומתחלף בין אתחולים (#2137). העיגון לפי UUID.
* ‏#2097 ס' 5 — פרט עברי בחלון "הפעולה לא הצליחה" נשלח כ-RTL; כערך mono
  LTR הנקודה שבסופו קפצה לתחילת השורה (צילום esxi-17, ‏rc1).
* ‏#2097 ס' 3 — כותרת עברית ל-`unmount-failed`, ושורה ארוכה בפלט הטכני
  אינה נכתבת על השורה שאחריה (צילום hv-11b, ‏rc1).
* ‏#2097 ס' 4 — הסיכום ב-DHCP מציג את כתובת ה-IP של הכרטיס, לא את השם.

שלושת האחרונים בונים את הגואי (C, ‏pango/cairo) — לינוקס בלבד.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

from native import requires_native
from server.dcui import actions, view
from server.storage import locations as sl

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui"


# ---- #2146 ס' 2: Documentation= ---------------------------------------------------

def test_systemd_units_document_the_public_repo_not_the_private_one() -> None:
    units = [p for p in (REPO / "deploy").iterdir()
             if p.suffix in (".service", ".timer", ".socket", ".path")]
    docs = [(p.name, line.split("=", 1)[1].strip())
            for p in units for line in p.read_text(encoding="utf-8").splitlines()
            if line.startswith("Documentation=")]
    assert docs, "no Documentation= line was read -- the scan itself is broken"
    private = [(name, url) for name, url in docs if "ImageSrv-dev" in url]
    assert private == [], f"links to the private repo, which a college server cannot open: {private}"
    for name, url in docs:
        match = re.fullmatch(r"https://github\.com/NadavOked/ImageSrv(?:/blob/main/(\S+))?", url)
        assert match, f"{name}: {url} is not the public repo"
        if match.group(1):
            assert (REPO / match.group(1)).is_file(), f"{name}: {match.group(1)} does not exist"


# ---- #2146 ס' 3: the disk's subtitle -------------------------------------------------

def _disk_row(**params) -> dict:
    full = {"uuid": "0b3c9a51-6f3e-4c1d-9a4e-2d7f1e0c5b11", "fs_type": "ext4",
            "device": "/dev/sdb", "partition": "/dev/sdb1", "model": "", "serial": "",
            "size_bytes": 42_949_672_960, **params}
    return {"id": "loc_x", "type": "local", "params_json": json.dumps(full)}


def test_disk_subtitle_never_shows_the_kernel_name_it_had_when_added() -> None:
    # A virtual disk often has no serial: the subtitle fell back to /dev/sdb.
    virtual = sl.subtitle_of(_disk_row(model="Virtual Disk"))
    assert "/dev/" not in virtual, virtual
    assert virtual == "Virtual Disk"
    bare = sl.subtitle_of(_disk_row())
    assert "/dev/" not in bare and bare == "UUID=0b3c9a51-6f3e-4c1d-9a4e-2d7f1e0c5b11", bare
    # with a serial nothing changed
    assert sl.subtitle_of(_disk_row(model="WDC WD40", serial="WD-123")) == "WDC WD40 · WD-123"


# ---- #2097 ס' 5: the result dialog ---------------------------------------------------

def _result_details(*details: str) -> list[list[str]]:
    frame = view.Frame("settings")
    result = actions.ActionResult(False, "Management network restart was not verified.",
                                  details=details)
    view._result_dialog(frame, SimpleNamespace(result=result, message=""))
    return [list(line) for line in frame.lines if line[0] == "d.kv"]


def test_a_hebrew_detail_in_the_result_dialog_is_laid_out_right_to_left() -> None:
    kv = _result_details("Live read-back did not match the saved configuration.",
                         "ens33: 10.0.0.5/24")
    # d.kv <label> <value> <mono>: mono=1 is an LTR value, where "." jumped to the start
    assert kv == [
        ["d.kv", "", "המצב החי שנקרא בחזרה אינו ההגדרה השמורה.", "0"],
        ["d.kv", "", "ens33: 10.0.0.5/24", "1"],
    ]


# ---- the native installer: a driver around screens_install_final.c --------------------

def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("bash", shutil.which("bash")),
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("make", shutil.which("make")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    posix=True, why="native installer is built and rendered on Linux",
)

# The driver includes the screen's file (its helpers are static) and links the
# rest of the GUI; text_draw is wrapped at link time, so every string the box
# draws is reported with where it went and how tall it was.
DRIVER = r'''
#include "%(final_c)s"
#include <stdlib.h>
void __real_text_draw(cairo_t *cr, Text *t, double x, double y, Rgb c);
static int log_draws;
void __wrap_text_draw(cairo_t *cr, Text *t, double x, double y, Rgb c) {
    if (log_draws) printf("draw\t%%.1f\t%%d\t%%s\n", y, t->h, pango_layout_get_text(t->layout));
    __real_text_draw(cr, t, x, y, c);
}
static App app;
int main(int argc, char **argv) {
    InstallState *s = &app.install; char out[320];
    if (argc == 4 && !strcmp(argv[1], "failure")) {
        snprintf(s->error, sizeof s->error, "%%s", argv[2]); snprintf(s->phase, sizeof s->phase, "%%s", argv[3]);
        failure_he(s, out, sizeof out); puts(out); return 0;
    }
    if (argc == 6 && !strcmp(argv[1], "guess")) {   /* static_mode address hostname nic-address */
        s->static_mode = atoi(argv[2]); snprintf(s->address, sizeof s->address, "%%s", argv[3]);
        snprintf(s->hostname, sizeof s->hostname, "%%s", argv[4]);
        s->nnics = 1; s->nic = 0; snprintf(s->nics[0].address, sizeof s->nics[0].address, "%%s", argv[5]);
        snprintf(s->nics[0].current, sizeof s->nics[0].current, "%%s", argv[5][0] ? argv[5] : "\xe2\x80\x94");
        console_guess(s, out, sizeof out); puts(out); return 0;
    }
    if (argc == 3 && !strcmp(argv[1], "box")) {
        app.theme = &THEME_LIGHT;
        snprintf(s->output, sizeof s->output, "%%s", argv[2]);
        cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_ARGB32, 800, 300);
        cairo_t *cr = cairo_create(sf);
        log_draws = 1; output_box(&app, cr, (Rect){ 0, 0, 760, 250 }); log_draws = 0;
        cairo_destroy(cr); cairo_surface_destroy(sf); return 0;
    }
    return 2;
}
'''


def _driver(tmp_path: Path) -> Path:
    source = tmp_path / "final_driver.c"
    source.write_text(DRIVER % {"final_c": (GUI / "src" / "screens_install_final.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "final-driver"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_final.c); '
        'cc $flags -Dmain=imagesrv_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -Wl,--wrap=text_draw -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(source)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _run(binary: Path, *args: str) -> list[str]:
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    run = subprocess.run([str(binary), *args], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, (run.returncode, run.stderr)
    return run.stdout.splitlines()


@NATIVE
def test_installer_failure_headline_says_unmount_failed_in_hebrew(tmp_path) -> None:
    binary = _driver(tmp_path)
    # rc1 (ESXi + Hyper-V, 24/10): state=failed, so the phase gave only "ההתקנה נכשלה."
    assert _run(binary, "failure", "unmount-failed: one or more target filesystems remain mounted",
                "failed") == ["שחרור דיסק היעד בסוף ההתקנה נכשל."]
    assert _run(binary, "failure", "unmount-unchecked: cannot read /proc/mounts", "failed") == [
        "שחרור דיסק היעד בסוף ההתקנה נכשל."]
    # the codes that already had a sentence keep it
    assert _run(binary, "failure", "packages-failed: apt returned exit 100", "failed") == [
        "התקנת חבילות ImageSrv נכשלה."]


@NATIVE
def test_installer_summary_shows_the_dhcp_address_not_the_name(tmp_path) -> None:
    binary = _driver(tmp_path)
    assert _run(binary, "guess", "0", "", "imagesrv-ta", "10.68.0.61") == ["https://10.68.0.61:8081"]
    # no address yet: the name is all there is
    assert _run(binary, "guess", "0", "", "imagesrv-ta", "") == ["https://imagesrv-ta:8081"]
    # static: the address typed on the screen, as before
    assert _run(binary, "guess", "1", "10.44.10.5", "imagesrv-ta", "10.68.0.61") == ["https://10.44.10.5:8081"]


@NATIVE
def test_a_long_output_line_is_not_drawn_over_by_the_next(tmp_path) -> None:
    binary = _driver(tmp_path)
    long = "Synchronizing state of chrony.service with SysV service script with " + "/usr/lib/systemd/systemd-sysv-install " * 6
    draws = [line.split("\t", 3) for line in _run(binary, "box", f"first\n{long}\nNEXT-LINE\nlast")
             if line.startswith("draw\t")]
    rows = {text: (float(y), int(h)) for _, y, h, text in draws}
    assert {"first", long, "NEXT-LINE", "last"} <= set(rows), sorted(rows)
    long_y, long_h = rows[long]
    assert long_h > 2 * rows["first"][1], "the long line did not wrap -- the test proves nothing"
    assert rows["NEXT-LINE"][0] >= long_y + long_h, (
        f"NEXT-LINE at y={rows['NEXT-LINE'][0]} is drawn over the long line ({long_y}..{long_y + long_h})")
    assert rows["last"][0] > rows["NEXT-LINE"][0]
