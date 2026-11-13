"""#1859 (Nadav 07/11) -- the table of every SMART attribute on the cloner's wait screen.

Mockup docs/design/mockups-1107b/1859-smart-table.html, "מוצע ב": a drawer from the side (600 px,
16 rows at 1024x768), opened by a click on a drawer or by S. Nadav's decisions:

* "קריטי" is a neutral tag; orange is a row background and a tag, only where the agent judged orange;
* "בסף היצרן" stays RED -- the mockup drew it grey, and Nadav said: "if I have to press F1, it is red";
* a name the tables do not know is the vendor's, with "לא מוכר" -- never fine, never a colour;
* "עלה" is a column only when the server compared with a previous round; otherwise "אין עדיין השוואה",
  and the word "יציב" is never written.

Two kinds of test, as everywhere in native-gui: source contracts (everywhere) and --png renders and a
driver on the real sources (`requires_native`: built and run on the Testrunner).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import GUI, _build_gui, _pkgconfig, _png_rgb, _token, posix

SRC = GUI / "src"
GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


# --- the rows: the mockup's disk, as the agent writes it ------------------------------------------------

#: id, flags, raw, rose, name -- MZ7LN256 of cloner 2 with the mockup's three changes (5 up from 9 to 12, row 213
#: unknown, the "עלה" column filled)
ROWS = [
    (5, "co", 12, 3, "סקטורים שהועברו"), (9, "", 9487, 31, "שעות עבודה"), (12, "", 1077, 2, "מחזורי הפעלה"),
    (170, "", 0, "", "מקום רזרבי פנוי"), (171, "", 0, "", "כשלונות כתיבה (SSD)"), (172, "", 0, "", "כשלונות מחיקה (SSD)"),
    (173, "", 75, "", "שחיקה"), (174, "", 369, 1, "כיבויים פתאומיים"), (178, "", 0, "", "רזרבה בשבב הגרוע"),
    (180, "", 614, "", "רזרבה פנויה (סה״כ)"), (184, "", 0, "", "שגיאות בנתיב הנתונים"),
    (187, "c", 0, "", "שגיאות שלא תוקנו"), (194, "", 28, "", "טמפרטורה"), (199, "ckh", 163, "", "שגיאות CRC"),
    (213, "u", 0, "", "Unknown_Attribute"), (233, "", 15728639, "", "מדד שחיקה"), (241, "", 18961, 412, "סה״כ נכתב"),
    (242, "", 13588, 260, "סה״כ נקרא"), (249, "", 28930, 330, "כתיבות לזיכרון"),
    (250, "", 39765357082, "", "ניסיונות קריאה חוזרים"), (251, "", 60672221888, "", "מידע יצרן (251)")]


def table(dev="sdb", cmp="1", rows=ROWS, patch=None) -> str:
    out = [f"csmart={dev}|{len(rows)}|{cmp}"]
    for i, fl, raw, rose, name in rows:
        if patch and i in patch:
            fl = patch[i]
        out.append(f"csmartrow={dev}|{i}|{fl}|{raw}|{rose if cmp == '1' else ''}|{name}")
    return "\n".join(out) + "\n"


HEAD = ("machine_name=CLONER-02\nserver_name=imagesrv-ta\nserver_addr=10.44.0.1\nserver_version=v0.88.0\n"
        "agent_version=v0.88.0\nmessage=x|y\ndisk_probe=drives\ndrawer_count=2\n"
        "cdrawer_slot=1|SATA 0|\ncdrawer_slot=2|SATA 1|\nhello_age=4\nhello_rc=200\n")
DISKS = ("cdisk=1|sda|WDC WD5000AAKX|500107862016|passed||6.0|6.0||1|SATA 0\n"
         "cdisk=2|sdb|SAMSUNG MZ7LN256HAJQ|256060514304|warn|reallocated|6.0|6.0||2|SATA 1\n")


def state(cmp="1", patch=None, first=True) -> str:
    """Disk 2's table first -- the --png card opens the first head. Disk 1 (sda) has none: the agent wrote none."""
    return HEAD + DISKS + table(cmp=cmp, patch=patch)


# --- source contracts --------------------------------------------------------------------------------------


def test_the_state_file_documents_both_records_and_the_parser_reads_them():
    readme = (GUI.parent / "native-gui" / "README.md").read_text(encoding="utf-8")
    assert "csmart=<dev>|<total>|<cmp>" in readme and "csmartrow=<dev>|<id>|<flags>|<raw>|<rose>|<name>" in readme
    parser = _src("state.c")
    assert 'KEY("csmart")' in parser and 'KEY("csmartrow")' in parser
    assert "smarttbl.c" in (GUI / "Makefile").read_text(encoding="utf-8")


def test_the_threshold_tag_is_red_never_grey():
    """Nadav 07/11: "אם אני צריך ללחוץ F1, זה אדום" -- the mockup's grey tag is not what is built."""
    c = _src("smarttbl.c")
    assert re.search(r'"בסף היצרן",\s*CH_RED\)', c), "בסף היצרן must be the red chip"
    assert not re.search(r'"בסף היצרן",\s*CH_PLAIN\)', c)


def test_the_word_stable_is_never_written_and_no_comparison_is_said_in_words():
    c = _src("smarttbl.c")
    assert "יציב" not in re.sub(r"/\*.*?\*/", "", c, flags=re.S), "no comparison is not 'stable'"
    assert "אין עדיין השוואה" in c
    assert "ההשוואה לסבב הקודם לא התקבלה מהשרת" in c, "an answer that was not read is not 'no previous round'"


def test_the_table_has_no_writing_control():
    """Read only: no button, no hit but the drawers' own, and nothing that emits a command."""
    c = _src("smarttbl.c")
    assert not re.search(r"(?<![a-z])printf\(", c) and "emit" not in c and "power_drop" not in c
    assert set(re.findall(r"HIT_[A-Z_]+", c)) == {"HIT_SMT_BASE", "HIT_SMT_SCRIM", "HIT_SMT_PANEL"}


def test_the_new_hit_ids_stay_out_of_the_ranges_of_the_bases():
    ui = _src("ui.h")
    assert "HIT_SMT_BASE = 3800" in ui
    assert ui.index("HIT_PLAIN_HIGH = 4000") < ui.index("HIT_SMT_SCRIM")


# --- renders -----------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def binary(tmp_path_factory) -> Path:
    return _build_gui(tmp_path_factory.mktemp("gui1859"))


def render(binary, tmp, text, size="1024x768"):
    sf = tmp / "state.txt"; sf.write_text(text, encoding="utf-8", newline="\n")
    ahead = __import__("time").time() + 3600; os.utime(sf, (ahead, ahead))   # a fresh state stamp for the whole run (#2110)
    log = tmp / "text.log"; log.write_text("")
    env = dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf"), IMAGESRV_GUI_TEXTLOG=posix(log))
    run = subprocess.run([str(binary), "--png", posix(tmp / "p"), "--size", size, "--mac", "3C:52:82:A1:00:1F",
                          "--ip", "10.10.10.31", "--state", posix(sf)],
                         capture_output=True, text=True, timeout=300, env=env, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    cards: dict[str, list[str]] = {}
    cur = None
    for l in log.read_text(encoding="utf-8").splitlines():
        if l.startswith("@@ "):
            cur = cards.setdefault(l[3:], [])
        elif cur is not None:
            cur.append(re.sub("[\u200e\u200f\u2066-\u2069]", "", l))
    return cards


def count(img, box, rgb, tol=8) -> int:
    w, _h, ch, px = img
    x0, y0, x1, y1 = box
    n = 0
    for y in range(y0, y1):
        for x in range(x0, x1):
            i = (y * w + x) * ch
            if all(abs(px[i + k] - rgb[k]) <= tol for k in range(3)):
                n += 1
    return n


@GUI_BUILD
def test_the_drawer_says_every_attribute_in_hebrew_with_sixteen_rows_at_1024x768(binary, tmp_path):
    w = render(binary, tmp_path, state())["standby-smart-light"]
    assert "מאפייני SMART · מגירה 2" in w, w[:12]
    assert "SAMSUNG MZ7LN256HAJQ" in w
    for name in ("סקטורים שהועברו", "שעות עבודה", "מחזורי הפעלה", "שגיאות שלא תוקנו", "טמפרטורה", "שגיאות CRC"):
        assert name in w, name
    assert "מוצגות 16 מתוך 21" in w, [l for l in w if l.startswith("מוצגות")]
    assert "↑ ↓ גלילה · Esc סגירה" in w                              # 21 rows: the rest scrolls
    assert not [l for l in w if l in ("sda", "sdb")], "a device name on the screen"


@GUI_BUILD
def test_the_drawer_says_what_rose_in_a_column_and_a_banner_only_when_compared(binary, tmp_path):
    w = render(binary, tmp_path, state("1"))["standby-smart-light"]
    assert "עלה" in w and "+3" in w and "+31" in w                    # the column, and the rise of 5 and of 9
    assert "עלה מאז הסבב הקודם — להחליף את הדיסק" in w
    assert "סקטורים שהועברו: מ-9 ל-12" in w                           # raw 12, rose 3: it was 9
    assert "אין עדיין השוואה" not in w
    none = render(binary, tmp_path, state("0"))["standby-smart-light"]
    assert "אין עדיין השוואה" in none and "עלה" not in none and not [l for l in none if l.startswith("+")]
    unread = render(binary, tmp_path, state("-"))["standby-smart-light"]
    assert "ההשוואה לסבב הקודם לא התקבלה מהשרת" in unread and "אין עדיין השוואה" not in unread
    assert "עלה" not in unread
    for card in (w, none, unread):
        assert not [l for l in card if "יציב" in l], "no comparison is not 'stable'"


@GUI_BUILD
def test_a_rise_in_the_cable_is_not_replace_the_disk(binary, tmp_path):
    """199 is the cable (#872): a rise there is a number, not the orange banner."""
    rows = [(5, "c", 0, "", "סקטורים שהועברו"), (199, "ckh", 200, 37, "שגיאות CRC")]
    w = render(binary, tmp_path, HEAD + DISKS + table(rows=rows))["standby-smart-light"]
    assert "+37" in w and "עלה מאז הסבב הקודם — להחליף את הדיסק" not in w
    assert "הושווה לסבב הקודם" in w and "אף מאפיין קריטי לא עלה מאז." in w


@GUI_BUILD
def test_the_tags_critical_orange_vendor_threshold_unknown_and_the_cable(binary, tmp_path):
    w = render(binary, tmp_path, state(patch={184: "f"}))["standby-smart-light"]
    for tag in ("קריטי", "כתום", "בסף היצרן", "לא מוכר", "מגירה/כבל, לא הדיסק"):
        assert tag in w, tag
    assert "Unknown_Attribute" in w, "an unknown attribute is shown by the name the vendor reported"
    assert "תקין" not in [l for l in w if "Unknown" in l]


@GUI_BUILD
def test_an_unknown_attribute_without_a_name_says_so(binary, tmp_path):
    rows = ROWS[:3] + [(213, "u", 0, "", "")]
    w = render(binary, tmp_path, HEAD + DISKS + table(rows=rows))["standby-smart-light"]
    assert "מאפיין 213" in w and "לא מוכר — שם היצרן לא התקבל" in w


@GUI_BUILD
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_at_the_vendors_threshold_is_red_and_critical_at_zero_is_not_orange(binary, tmp_path, theme):
    on = render(binary, tmp_path, state(patch={184: "f"}))
    img_on = _png_rgb(tmp_path / f"p-standby-smart-{theme}.png")
    render(binary, tmp_path, state())
    img_off = _png_rgb(tmp_path / f"p-standby-smart-{theme}.png")
    drawer = (0, 48, 600, 732)
    red, soft_red = _token("--clr-danger-fill", theme), _token("--clr-danger-soft", theme)
    assert count(img_on, drawer, red) - count(img_off, drawer, red) >= 120, "the red tag"
    assert count(img_on, drawer, soft_red) - count(img_off, drawer, soft_red) >= 10000, "the red row"
    assert "בסף היצרן" in on[f"standby-smart-{theme}"]


@GUI_BUILD
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_orange_is_a_row_background_only_where_the_agent_judged_orange(binary, tmp_path, theme):
    render(binary, tmp_path, state())
    img_on = _png_rgb(tmp_path / f"p-standby-smart-{theme}.png")
    render(binary, tmp_path, state(patch={5: "c"}))                   # row 5 critical but not orange: a tag, no colour
    img_off = _png_rgb(tmp_path / f"p-standby-smart-{theme}.png")
    soft = _token("--clr-warning-soft", theme)
    assert count(img_on, (0, 200, 600, 732), soft) - count(img_off, (0, 200, 600, 732), soft) >= 10000


@GUI_BUILD
def test_the_wait_screen_offers_the_table_only_where_the_agent_wrote_one(binary, tmp_path):
    cards = render(binary, tmp_path, state())
    w = cards["standby-light"]
    assert w.count("כל מאפייני ה-SMART (21) ›") == 1, "the second drawer has no table: no line"
    assert "S  מאפייני SMART" in w and "S" in w
    bare = render(binary, tmp_path, HEAD + DISKS)["standby-light"]
    assert not [l for l in bare if "מאפייני ה-SMART" in l] and "S  מאפייני SMART" not in bare


# --- the keys and the clicks, on the real sources ------------------------------------------------------------


DRIVER = r'''
#include <stdio.h>
#include <string.h>
#include "smarttbl.h"
#include "lcui.h"
#include "screens_rounds.h"
#include "state.h"
int app_typing_lang(const App *a) { (void)a; return LANG_NONE; }
static App a;
static void draw(void) {
    cairo_surface_t *s = cairo_image_surface_create(CAIRO_FORMAT_RGB24, 1024, 768);
    cairo_t *cr = cairo_create(s); app_draw(&a, cr, 1024, 768); cairo_destroy(cr); cairo_surface_destroy(s);
}
static void say(const char *what) {
    printf("%s open=%d dev=%s top=%d vis=%d\n", what, a.smt.open, a.smt.dev, a.smt.top, a.smt.vis);
}
static int hit_of(int id, double *x, double *y) {
    for (int i = 0; i < a.nhits; i++) if (a.hits[i].id == id) { *x = a.hits[i].r.x + 5; *y = a.hits[i].r.y + a.hits[i].r.h / 2; return 1; }
    return 0;
}
int main(int argc, char **argv) {
    if (argc != 2) return 2;
    a.theme = &THEME_LIGHT; a.scale = 1; a.signed_in = 1;
    FILE *f = fopen(argv[1], "r"); if (!f) return 3;
    state_parse(&a.st, f); fclose(f);
    a.screen = SCREEN_STANDBY; a.force_standby = 1; a.force_cloner = 1; a.fixed_clock = 1;
    draw(); say("start");
    double hx, hy;
    printf("bay hits: sda=%d sdb=%d\n", hit_of(HIT_SMT_BASE + 0, &hx, &hy), hit_of(HIT_SMT_BASE + 1, &hx, &hy));
    Event e = { .type = UIEV_CHAR, .ch = 's' };
    printf("s -> %d\n", standby_handle(&a, &e)); draw(); say("after-s");
    e = (Event){ .type = UIEV_KEY, .key = KEYSYM_DOWN };
    for (int i = 0; i < 40; i++) standby_handle(&a, &e);
    draw(); say("down-40");
    e = (Event){ .type = UIEV_SCROLL, .delta = 1 };
    for (int i = 0; i < 40; i++) standby_handle(&a, &e);
    draw(); say("wheel-up-40");
    e = (Event){ .type = UIEV_KEY, .key = KEYSYM_UP };
    printf("up -> %d\n", standby_handle(&a, &e));
    e = (Event){ .type = UIEV_CHAR, .ch = 'x' };
    printf("x while open -> %d\n", standby_handle(&a, &e));
    e = (Event){ .type = UIEV_KEY, .key = KEYSYM_ESC };
    printf("esc -> %d\n", standby_handle(&a, &e)); draw(); say("after-esc");
    double x, y;
    hit_of(HIT_SMT_BASE + 0, &x, &y);
    e = (Event){ .type = UIEV_CLICK, .x = x, .y = y };
    printf("click -> %d\n", standby_handle(&a, &e)); draw(); say("after-click");
    e = (Event){ .type = UIEV_CLICK, .x = 900, .y = 400 };
    printf("click outside -> %d\n", standby_handle(&a, &e)); draw(); say("after-outside");
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory):
    d = tmp_path_factory.mktemp("drv1859")
    (d / "driver.c").write_text(DRIVER, encoding="utf-8", newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=GUI, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=30).stdout.split()
    build = subprocess.run(
        ["bash", "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
         '-o "$2" "$3" "${@:4}" $(pkg-config --libs pangocairo cairo libdrm) -lm',
         "_", posix(GUI), posix(d / "driver"), posix(d / "driver.c"), *[posix(GUI / s) for s in srcs if not s.endswith("main.c")]],
        capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "driver"


def run_driver(driver, tmp_path, text):
    sf = tmp_path / "state.txt"; sf.write_text(text, encoding="utf-8", newline="\n")
    ahead = __import__("time").time() + 3600; os.utime(sf, (ahead, ahead))
    run = subprocess.run([str(driver), posix(sf)], capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL,
                         env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    return run.stdout


@GUI_BUILD
def test_s_opens_it_keys_scroll_it_esc_closes_it_and_a_click_on_a_drawer_opens_that_one(driver, tmp_path):
    # disk 1 (sda) has a table too: S still goes to the orange one (sdb), and a click on drawer 1 opens sda
    out = run_driver(driver, tmp_path, HEAD + DISKS + table(dev="sda", rows=ROWS[:3]) + table()).splitlines()
    assert "start open=0 dev= top=0 vis=0" in out and "bay hits: sda=1 sdb=1" in out
    assert "s -> 1" in out
    assert any(l.startswith("after-s open=1 dev=sdb top=0 vis=16") for l in out), out      # 16 rows on show
    assert "down-40 open=1 dev=sdb top=5 vis=16" in out, out                              # 21 rows, 16 on show: the end is 5
    assert "wheel-up-40 open=1 dev=sdb top=0 vis=16" in out, out
    assert "up -> 1" in out and "x while open -> 1" in out                                # every key is the drawer's
    assert "esc -> 1" in out and any(l.startswith("after-esc open=0") for l in out)
    assert "choice-back" not in "\n".join(out), "Esc closed the drawer; it did not go back to the boot choice"
    assert any(l.startswith("after-click open=1 dev=sda") for l in out), out
    assert "click outside -> 1" in out and any(l.startswith("after-outside open=0") for l in out)


@GUI_BUILD
def test_a_drawer_with_no_table_is_not_clickable_and_s_goes_to_the_one_that_has_it(driver, tmp_path):
    out = run_driver(driver, tmp_path, HEAD + DISKS + table()).splitlines()
    assert "bay hits: sda=0 sdb=1" in out, out
    assert any(l.startswith("after-s open=1 dev=sdb") for l in out)


@GUI_BUILD
def test_without_any_table_s_does_nothing_and_esc_still_goes_back(driver, tmp_path):
    out = run_driver(driver, tmp_path, HEAD + DISKS).splitlines()
    assert "s -> 0" in out and any(l.startswith("after-s open=0") for l in out)
    assert "esc -> 1" in out and "choice-back" in "\n".join(out), "closed: Esc is the way back (#2079)"
