"""Native live-installer rendering and interaction contracts (#1188/#1190)."""

from __future__ import annotations

import os
import re
import shutil
import struct
import subprocess
import zlib
from pathlib import Path

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
GUI = REPO / "native-gui"


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


def _build(tmp_path: Path) -> Path:
    binary = tmp_path / "gui"
    command = (
        'set -e; cd "$1"; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE '
        '$(pkg-config --cflags pangocairo cairo libdrm) '
        '-o "$2" $(make -s -f Makefile print-gui-src) '
        '$(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary)],
                          capture_output=True, text=True, timeout=300,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    return binary


def _size_and_ink(path: Path, box: tuple[int, int, int, int] | None = None) -> tuple[int, int, int]:
    width, height, colors = _colors(path, box)
    return width, height, len(colors)


def _colors(path: Path, box: tuple[int, int, int, int] | None = None) -> tuple[int, int, set]:
    raw = path.read_bytes()
    assert raw[:8] == b"\x89PNG\r\n\x1a\n"
    pos = 8; width = height = channels = 0; packed = b""
    while pos < len(raw):
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        kind, data = raw[pos + 4:pos + 8], raw[pos + 8:pos + 8 + length]
        pos += 12 + length
        if kind == b"IHDR":
            width, height, depth, color, *_ = struct.unpack(">IIBBBBB", data)
            assert depth == 8 and color in (2, 6)
            channels = 3 if color == 2 else 4
        elif kind == b"IDAT":
            packed += data
        elif kind == b"IEND":
            break
    scan = zlib.decompress(packed); stride = width * channels
    rows = []; prior = bytearray(stride); at = 0
    for _ in range(height):
        mode, cur = scan[at], bytearray(scan[at + 1:at + 1 + stride]); at += stride + 1
        for x in range(stride):
            left = cur[x - channels] if x >= channels else 0
            up = prior[x]
            ul = prior[x - channels] if x >= channels else 0
            if mode == 1: cur[x] = (cur[x] + left) & 255
            elif mode == 2: cur[x] = (cur[x] + up) & 255
            elif mode == 3: cur[x] = (cur[x] + ((left + up) >> 1)) & 255
            elif mode == 4:
                p = left + up - ul; pa, pb, pc = abs(p-left), abs(p-up), abs(p-ul)
                cur[x] = (cur[x] + (left if pa <= pb and pa <= pc else up if pb <= pc else ul)) & 255
        rows.append(cur); prior = cur
    x0, y0, x1, y1 = box or (0, 0, width, height)
    colors = {bytes(rows[y][x:x + 3]) for y in range(y0, y1)
              for x in range(x0 * channels, x1 * channels, channels)}
    return width, height, colors


@NATIVE
def test_every_installer_screen_renders_with_visible_ink_at_both_target_sizes(tmp_path) -> None:
    binary = _build(tmp_path)
    names = ("install-0-disk", "install-0b-disk-unchosen", "install-1-role", "install-1b-role-unchecked", "install-1c-role-failed",
             "install-2-servers-net", "install-3-hostname",
             "install-4-admin", "install-5-summary", "install-5c-progress",
             "install-5b-apply-failed", "install-6-done")
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    for size in ("1920x1080", "1280x1024"):
        prefix = tmp_path / size
        proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                               str(prefix), "--size", size], capture_output=True, text=True,
                              timeout=120, env=env, stdin=subprocess.DEVNULL)
        assert proc.returncode == 0, proc.stderr
        expected = tuple(map(int, size.split("x")))
        for name in names:
            for theme in ("light", "dark"):
                path = tmp_path / f"{size}-{name}-{theme}.png"
                width, height, colors = _size_and_ink(path)
                assert (width, height) == expected
                assert colors >= 20, f"{path.name} is visually blank ({colors} colors)"


# #1566: the disk-contents column, through the C function itself. A driver
# includes screens_install_setup.c (display_disk_contents is static) and links
# the rest of the GUI; main.c is renamed so the driver's main() is the one.
DISK_CONTENTS_DRIVER = r'''
#include "%(setup_c)s"
int main(int argc, char **argv) {
    char out[256];
    for (int i = 1; i < argc; i++) { display_disk_contents(argv[i], out, sizeof out); puts(out); }
    return 0;
}
'''
FSI, PDI = "⁨", "⁩"


def _disk_contents(tmp_path: Path, *raw: str) -> list[str]:
    driver = tmp_path / "disk_contents.c"
    driver.write_text(DISK_CONTENTS_DRIVER % {"setup_c": (GUI / "src" / "screens_install_setup.c").as_posix()},
                      encoding="utf-8", newline="\n")
    binary = tmp_path / "disk-contents"
    command = (
        'set -e; cd "$1"; flags="-O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags pangocairo cairo libdrm)"; '
        'srcs=$(make -s -f Makefile print-gui-src | tr " " "\\n" | grep -v -x -e src/main.c -e src/screens_install_setup.c); '
        'cc $flags -Dmain=imagectl_gui_main -c src/main.c -o "$3/gui-main.o"; '
        'cc $flags -o "$2" "$4" "$3/gui-main.o" $srcs $(pkg-config --libs pangocairo cairo libdrm) -lm'
    )
    proc = subprocess.run(["bash", "-c", command, "_", str(GUI), str(binary), str(tmp_path), str(driver)],
                          capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    run = subprocess.run([str(binary), *raw], capture_output=True, text=True, encoding="utf-8",
                         timeout=60, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@NATIVE
def test_disk_contents_reach_the_screen_in_hebrew(tmp_path) -> None:
    """disks.sh keeps its English machine values (they are data, #1566 forbids
    translating them there); the screen translates them at the boundary."""
    got = _disk_contents(tmp_path, "empty", "unknown", "gpt:2 partitions (ext4, vfat)",
                         "mbr:3 partitions (ntfs, unknown, unknown)", "gpt:0 partitions",
                         "filesystem ext4")
    assert got == [
        "ריק",
        "לא ידוע",
        f"{FSI}gpt{PDI}:2 מחיצות ({FSI}ext4, vfat{PDI})",
        # every unknown partition, not only the first
        f"{FSI}mbr{PDI}:3 מחיצות ({FSI}ntfs, לא ידוע, לא ידוע{PDI})",
        f"{FSI}gpt{PDI}:0 מחיצות",
        f"מערכת קבצים {FSI}ext4{PDI}",
    ]


@NATIVE
def test_network_rows_reserve_an_untruncated_line_for_the_mac_at_1024(tmp_path) -> None:
    draw = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    # #2096: the second line is "name · MAC" at its natural width (never wrapped onto)
    assert 'snprintf(ids,sizeof ids,"%s · %s",n->name,n->mac)' in draw
    assert 'Text q=text_make(cr,FONT_MONO,11,400,ids,0,DIR_LTR)' in draw
    assert 'snprintf(model,sizeof model,"%s · %s"' not in draw

    binary = _build(tmp_path)
    prefix = tmp_path / "mac"
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(prefix), "--size", "1024x768"], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    path = tmp_path / "mac-install-2-servers-net-light.png"
    width, height, colors = _size_and_ink(path)
    assert (width, height) == (1024, 768)
    assert colors >= 20, "network screen, including its dedicated MAC lines, did not render"


@NATIVE
def test_progress_has_no_primary_button_ink_in_the_footer_action_area(tmp_path) -> None:
    binary = _build(tmp_path)
    prefix = tmp_path / "footer"
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    # 1280x800: scale 1 (mockup 10/10 scales 1280x1024 by 1.25); footer y 736..800
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(prefix), "--size", "1280x800"], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    path = tmp_path / "footer-install-5c-progress-light.png"
    _, _, colors = _size_and_ink(path, (820, 749, 1010, 787))
    assert colors == 1, "running progress must leave the footer action area blank"


@NATIVE
def test_back_button_is_drawn_on_every_screen_where_escape_goes_back(tmp_path) -> None:
    """‏#1205: Esc חוזר ממסכים 2–6, אבל כפתור "חזור" צויר רק בחלקם — במסך
    התפקיד (2) היה רק "הבא". מי שלא יודע על Esc לא מוצא את החזרה.

    ב-1280x800 (קנה מידה 1, מוקאפ 10/10): ‏"הבא" ברוחב 118 מתחיל ב-x=880,
    ו"חזור" נגמר 12 פיקסלים לפניו. האזור 850–866 נמצא בתוך "חזור" (מסגרת +
    רקע) ובמסך בלי חזרה הוא רקע הפס התחתון בלבד — צבע אחד. שורת המקשים
    נגמרת ב-768, לפני האזור."""
    binary = _build(tmp_path)
    prefix = tmp_path / "back"
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(prefix), "--size", "1280x800"], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    box = (850, 744, 866, 791)
    with_back = ("install-1-role", "install-2-servers-net", "install-3-hostname",
                 "install-4-admin", "install-5-summary")
    without = ("install-0-disk", "install-5c-progress", "install-6-done")
    for name in with_back + without:
        _, _, colors = _size_and_ink(tmp_path / f"back-{name}-light.png", box)
        if name in with_back:
            assert colors > 1, f"{name}: no Back button where Escape goes back"
        else:
            assert colors == 1, f"{name}: Back drawn where there is no way back"


@NATIVE
def test_role_next_is_disabled_until_the_check_is_green(tmp_path) -> None:
    """‏#1310: שלושת המצבים של מסך התפקיד. ב-1280x800 (קנה מידה 1) "הבא"
    מתחיל ב-x=880; הנקודה 884,767 היא מילוי הכפתור (שמאל לטקסט). ירוק =
    מילוי של כפתור ראשי; לא-נבדק ונכשל = מילוי של כפתור מושבת, זהה בשניהם."""
    binary = _build(tmp_path)
    prefix = tmp_path / "role"
    env = dict(os.environ)
    if (GUI / "fonts.conf").exists():
        env["FONTCONFIG_FILE"] = str(GUI / "fonts.conf")
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(prefix), "--size", "1280x800"], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    for theme in ("light", "dark"):
        def fill(name: str) -> set:
            return _colors(tmp_path / f"role-install-{name}-{theme}.png", (884, 767, 885, 768))[2]
        green, unchecked, failed = fill("1-role"), fill("1b-role-unchecked"), fill("1c-role-failed")
        assert green != unchecked, f"{theme}: Next looks the same before any check"
        assert unchecked == failed, f"{theme}: a failed check must not enable Next"


def test_role_radios_register_hit_boxes_and_dispatch_to_distinct_values() -> None:
    draw = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    control = (GUI / "src" / "install.c").read_text(encoding="utf-8")
    assert "HIT_INSTALL_PRIMARY" in draw and "HIT_INSTALL_SECONDARY" in draw
    assert "if(id==HIT_INSTALL_PRIMARY)s->secondary=0" in control
    assert "else if(id==HIT_INSTALL_SECONDARY){s->secondary=1;refocus(a);}" in control


def test_installer_keyboard_contract_has_reverse_tab_and_scrollable_failure_output() -> None:
    input_c = (GUI / "src" / "input.c").read_text(encoding="utf-8")
    input_h = (GUI / "src" / "input.h").read_text(encoding="utf-8")
    control = (GUI / "src" / "install.c").read_text(encoding="utf-8")
    assert "KEYSYM_BACKTAB" in input_h
    assert "d->shift ? KEYSYM_BACKTAB : KEYSYM_TAB" in input_c
    assert "REL_WHEEL" in input_c and "UIEV_SCROLL" in input_c
    assert "e->key==KEYSYM_TAB||e->key==KEYSYM_BACKTAB" in control
    assert "s->view==INSTALL_FAILED" in control and "s->output_scroll" in control


def test_disk_admin_and_seven_step_controls_are_wired_into_the_live_form() -> None:
    control = (GUI / "src" / "install.c").read_text(encoding="utf-8")
    disk = (GUI / "src" / "screens_install_setup.c").read_text(encoding="utf-8")
    forms = (GUI / "src" / "screens_install_forms.c").read_text(encoding="utf-8")
    frame = (GUI / "src" / "screens_install.c").read_text(encoding="utf-8")
    assert '"disk"' in control and '"admin_user"' in control
    assert "HIT_INSTALL_DISK_BASE" in disk and "מדיית ההתקנה" in disk and "יימחק" in disk
    assert "HIT_INSTALL_ADMIN_USER" in forms and "s->admin_user" in forms
    assert "HIT_INSTALL_SHOW_PASSWORD" in forms and "HIT_INSTALL_SHOW_CONFIRM" in forms
    assert 'shown?"הסתר":"הצג"' in forms
    # mockup 10/10: the footer carries the working keys; "שלב N מתוך 7" repeated the rail
    assert "for(int i=0;i<7;i++" in frame and "install_keys_draw(a,cr,l);" in frame
    assert "שלב %d מתוך 7" not in frame


def test_engine_state_mapping_is_exact_and_restart_ejects_before_reboot() -> None:
    control = (GUI / "src" / "install.c").read_text(encoding="utf-8")
    table = re.search(r"ENGINE_STATES\[\]\s*=\s*\{(.*?)\n\};", control, re.S)
    assert table
    assert re.findall(r'\{"([a-z]+)",', table.group(1)) == [
        "ready", "partitioning", "bootstrap", "packages",
        "bootloader", "finishing", "done", "failed",
    ]
    restart = next(line for line in control.splitlines() if "static void reboot_or_report" in line)
    assert restart.index('call(a,b,"eject"') < restart.index('call(a,b,"reboot"')


def test_progress_uses_collapsed_technical_details_and_no_footer_button() -> None:
    final = (GUI / "src" / "screens_install_final.c").read_text(encoding="utf-8")
    progress = re.search(r"static void progress\(.*?\n\}", final, re.S)
    assert progress
    # the collapsed toggle is drawn by details_toggle(), which owns the hit box
    assert "details_toggle(" in progress.group(0)
    assert "HIT_INSTALL_TECHNICAL" in final and "s->technical_open" in final
    assert "install_footer" not in progress.group(0) and "draw_btn" not in progress.group(0)


def test_new_install_options_are_primary_and_wizard_names_remain_aliases() -> None:
    main = (GUI / "src" / "main.c").read_text(encoding="utf-8")
    assert "[--install-cmd CMD] [--install-cwd DIR]" in main
    assert '!strcmp(o, "--install-cmd") || !strcmp(o, "--wizard-cmd")' in main
    assert '!strcmp(o, "--install-cwd") || !strcmp(o, "--wizard-cwd")' in main


def test_disk_screen_is_usable_from_the_keyboard_alone() -> None:
    """שרת בארון: מקלדת, לא עכבר. חצים בוחרים דיסק, רווח מאשר "אני מבין" —
    בלעדיהם ההתקנה נתקעת במסך הראשון (נמדד ב-ESXi דרך govc keystrokes, 21/09)."""
    src = (REPO / "native-gui" / "src" / "install.c").read_text(encoding="utf-8")
    assert "s->view==INSTALL_DISK&&e->type==UIEV_CHAR&&e->ch==' '" in src, "רווח מאשר"
    assert "s->view==INSTALL_DISK&&e->type==UIEV_KEY&&(e->key==KEYSYM_UP||e->key==KEYSYM_DOWN)" in src, "חצים בוחרים דיסק"
    assert "if(!s->disks[d].iso){s->disk=d;" in src, "המדיה עצמה אינה נבחרת גם מהמקלדת"
