"""#2778 (הכרעת נדב, 09/11): מקלדת על המסך במתקין, כדי שאפשר יהיה להתקין בעכבר בלבד.

המתקין עובד בשלושה מצבים: עכבר בלבד, מקלדת בלבד (#2776), או שניהם. החוזה שנבדק כאן:

* לחיצה בעכבר על שדה טקסט פותחת מקלדת על המסך. נחיתה על שדה מהמקלדת (Tab) **לא**
  פותחת אותה: מי שיש לו מקלדת לא צריך אותה.
* המקשים מקלידים לשדה בדיוק כמו מקלדת פיזית: אותו מסלול אירועים, ולכן "נבחר
  בהגעה" של #2777, מחיקת הבדיקה הירוקה של #1310 והסתרת הסיסמה חלים גם עליהם.
* פריסה לטינית (US), ספרות וסימנים, Shift לתו הבא, מחיקה, רווח ו"סיום".
* המקלדת הפיזית ממשיכה לעבוד כשמקלדת המסך פתוחה. "סיום" סוגר; גם יציאה מהשדה
  או מהמסך סוגרת.
* המקלדת לעולם לא מכסה את השדה שמקלידים בו, ולא את "הבא"/"חזור" — בשלושה גדלי מסך.
* המקשים LTR (משמאל לימין) גם בעמוד RTL.

הדרייבר כולל את `main.c` האמיתי (כמו `test_hit_ids_2265.py`) ומקשר את כל קובצי
ה-GUI: מצייר את המסך, מוצא את מלבן הפקד בטבלת ה-hit, ולוחץ במרכזו דרך
`install_handle`. כלומר הלחיצה עוברת דרך `app_hit` והציור האמיתיים.
cc + pango/cairo = לינוקס בלבד; בווינדוס זה דילוג, לא מעבר.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native
from test_cloner_gui import BASH, GUI, _pkgconfig, posix

NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    ("bash", BASH),
    posix=True, why="native-gui נבנה ומורץ על לינוקס בלבד",
)

ROLE, NETWORK, HOSTNAME, TIME, BACKUP, ADMIN, SUMMARY = 2, 3, 4, 5, 6, 7, 8

DRIVER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#define main station_gui_main
#include "main.c"
#undef main
/* Without the keyboard (the negative control: the production files of before
 * #2778) the driver still builds -- no "osk:" names, osk=0 -- so the tests fail
 * on what they check, not on a missing header. */
#if __has_include("install_osk.h")
#include "install_osk.h"
#endif

static App app;
static int W = 1280, H = 800;

static const struct { const char *name; int id; } NAMES[] = {
    { "primary_url", HIT_INSTALL_PRIMARY_URL }, { "address", HIT_INSTALL_ADDRESS }, { "netmask", HIT_INSTALL_NETMASK },
    { "gateway", HIT_INSTALL_GATEWAY }, { "dns", HIT_INSTALL_DNS }, { "hostname", HIT_INSTALL_HOSTNAME },
    { "timezone", HIT_INSTALL_TIMEZONE }, { "ntp_servers", HIT_INSTALL_NTP_SERVERS },
#ifdef HIT_INSTALL_MT_BASE
    { "manual_time", HIT_INSTALL_MT_BASE },   /* #2866: the year's box, the first of the five */
#else
    { "manual_time", HIT_INSTALL_MANUAL_TIME },
#endif
    { "backup_other", HIT_INSTALL_BACKUP_OTHER_GB }, { "backup_time", HIT_INSTALL_BACKUP_TIME },
    { "admin_user", HIT_INSTALL_ADMIN_USER }, { "current_password", HIT_INSTALL_CURRENT_PASSWORD },
    { "password", HIT_INSTALL_PASSWORD }, { "confirm", HIT_INSTALL_CONFIRM },
    { "root_password", HIT_INSTALL_ROOT_PASSWORD }, { "root_confirm", HIT_INSTALL_ROOT_CONFIRM },
    { "show_password", HIT_INSTALL_SHOW_PASSWORD }, { "show_confirm", HIT_INSTALL_SHOW_CONFIRM },
    { "show_root", HIT_INSTALL_SHOW_ROOT }, { "show_root_confirm", HIT_INSTALL_SHOW_ROOT_CONFIRM }, { "next", HIT_INSTALL_NEXT }, { "back", HIT_INSTALL_BACK },
    { "dhcp", HIT_INSTALL_DHCP }, { "static", HIT_INSTALL_STATIC }, { "check", HIT_INSTALL_CHECK_PRIMARY },
};

static const char *name_of(int id) {
    static char buf[32];
    for (size_t i = 0; i < sizeof NAMES / sizeof NAMES[0]; i++) if (NAMES[i].id == id) return NAMES[i].name;
#ifdef HIT_INSTALL_OSK_BASE
    int k = id - HIT_INSTALL_OSK_BASE;
#ifdef HIT_INSTALL_OSK_GRIP
    if (k == OSK_GRIP) return "osk:grip";
    if (k == OSK_PREVIEW) return "osk:preview";
#endif
    if (k == OSK_KEYS) return "osk:panel";
    if (k == OSK_BKSP) return "osk:bksp";
    if (k == OSK_SHIFT) return "osk:shift";
    if (k == OSK_SPACE) return "osk:space";
    if (k == OSK_DONE) return "osk:done";
    if (k >= 0 && k < OSK_CHARS) { snprintf(buf, sizeof buf, "osk:%c", osk_char(k, 0)); return buf; }
#endif
    snprintf(buf, sizeof buf, "id%d", id);
    return buf;
}

/* #2778: the footer's key line as drawn; weak, so the driver still builds without it (the control) */
void install_keys_line(App *a, char *out, size_t n) __attribute__((weak));

static void draw(App *a) {
    cairo_surface_t *sf = cairo_image_surface_create(CAIRO_FORMAT_RGB24, W, H);
    cairo_t *cr = cairo_create(sf); app_draw(a, cr, W, H); cairo_destroy(cr); cairo_surface_destroy(sf);
}

static void click_at(App *a, InstallBridge *b, double lx, double ly) {
    double s = a->scale > 0 ? a->scale : 1;   /* hits are logical, a click is in screen pixels */
    Event e; memset(&e, 0, sizeof e); e.type = UIEV_CLICK; e.x = lx * s; e.y = ly * s;
    a->pressed_id = app_hit(a, e.x, e.y);     /* as main.c does before install_handle */
    install_handle(a, b, &e); draw(a);
}

static void show(const App *a, const char *tok) {
    const InstallState *s = &a->install;
#ifdef HIT_INSTALL_OSK_BASE
    int osk = s->osk != 0 && s->osk == s->view, shift = s->osk_shift;
#else
    int osk = 0, shift = 0;
#endif
#ifdef HIT_INSTALL_OSK_GRIP   /* round 3: what the keyboard's preview row shows */
    const char *preview = s->osk_field ? s->osk_shown : "", *label = s->osk_field ? s->osk_label : "";
#else
    const char *preview = "", *label = "";
#endif
    printf("state\ttok=%s\tview=%d\tfocus=%s\tosk=%d\tshift=%d\taddress=%s\tnetmask=%s\tgateway=%s\tdns=%s"
           "\thostname=%s\tpassword=%s\troot_password=%s\tntp=%s\tbackup_time=%s\tprimary_url=%s"
           "\tok=%d\tchecked=%d\tshown=%d\tpreview=%s\tlabel=%s\n",
           tok, s->view, name_of(a->focus), osk, shift, s->address, s->netmask,
           s->gateway, s->dns, s->hostname, s->password, s->root_password, s->ntp_servers, s->backup_time,
           s->primary_url, s->primary_ok, s->primary_checked, s->show_password, preview, label);
}

/* argv: BRIDGE-CMD TOKEN... -- one state line after every token but "hits". */
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    App *a = &app; InstallBridge b = { argv[1], NULL, 0 };
    a->theme = &THEME_LIGHT; a->default_theme = a->theme;
    for (int i = 2; i < argc; i++) {
        const char *t = argv[i];
        if (!strncmp(t, "size=", 5)) { sscanf(t + 5, "%dx%d", &W, &H); continue; }
        if (!strcmp(t, "dark")) { a->theme = &THEME_DARK; continue; }
        if (!strncmp(t, "view=", 5)) { install_demo(a, atoi(t + 5)); a->focus = 0; draw(a); show(a, "view"); continue; }
        if (!strcmp(t, "static")) a->install.static_mode = 1;
        else if (!strcmp(t, "ntp-org")) a->install.ntp_org = 1;
        else if (!strcmp(t, "ntp-manual")) { a->install.ntp_manual = 1; snprintf(a->install.manual_time, sizeof a->install.manual_time, "2026-11-09 14:32"); }   /* #2765 */
        else if (!strcmp(t, "backup-other")) a->install.backup_pick = INSTALL_BACKUP_OTHER;
        else if (!strcmp(t, "rerun")) a->install.rerun = 1;
        else if (!strcmp(t, "lab")) a->install.lab = INSTALL_LAB_YES;
        else if (!strcmp(t, "keyline")) {
            char line[512] = "";
            if (install_keys_line) install_keys_line(a, line, sizeof line);
            printf("keyline\t%s\n", line);
            continue;
        } else if (!strcmp(t, "hits")) {
            double s = a->scale > 0 ? a->scale : 1;
            printf("screen\t%g\t%g\n", W / s, H / s);
            for (int h = 0; h < a->nhits; h++)
                printf("hit\t%s\t%g\t%g\t%g\t%g\n", name_of(a->hits[h].id), a->hits[h].r.x, a->hits[h].r.y, a->hits[h].r.w, a->hits[h].r.h);
            continue;
        } else if (!strncmp(t, "click=", 6)) {
            int found = -1;
            for (int h = a->nhits - 1; h >= 0 && found < 0; h--) if (!strcmp(name_of(a->hits[h].id), t + 6)) found = h;
            if (found < 0) { printf("nohit\t%s\n", t + 6); continue; }
            Rect r = a->hits[found].r; click_at(a, &b, r.x + r.w / 2, r.y + r.h / 2);
        } else if (!strncmp(t, "drag=", 5)) {
            /* drag=NAME,DX,DY: press in the middle of NAME, move by half and by all of
             * (DX,DY) with the button held, let go, and move on 40px -- which must not drag */
            char name[32]; double dx, dy; int found = -1;
            if (sscanf(t + 5, "%31[^,],%lf,%lf", name, &dx, &dy) != 3) { fprintf(stderr, "bad %s\n", t); return 2; }
            for (int h = a->nhits - 1; h >= 0 && found < 0; h--) if (!strcmp(name_of(a->hits[h].id), name)) found = h;
            if (found < 0) { printf("nohit\t%s\n", name); continue; }
            Rect r = a->hits[found].r; double x = r.x + r.w / 2, y = r.y + r.h / 2, k = a->scale > 0 ? a->scale : 1;
            click_at(a, &b, x, y);
            const double step[][3] = { { .5, .5, 1 }, { 1, 1, 1 }, { 1, 1, 0 }, { 1, 1, 0 } };
            for (int j = 0; j < 4; j++) {
                Event m; memset(&m, 0, sizeof m); m.type = UIEV_MOVE;
                m.x = (x + dx * step[j][0] + (j == 3 ? 40 : 0)) * k; m.y = (y + dy * step[j][1] + (j == 3 ? 40 : 0)) * k;
#ifdef UIEV_MOVE_DOWN
                m.down = (int)step[j][2];
#endif
                install_handle(a, &b, &m); draw(a);
            }
        } else if (!strncmp(t, "clickat=", 8)) {
            double x, y; sscanf(t + 8, "%lf,%lf", &x, &y); click_at(a, &b, x, y);
        } else if (!strncmp(t, "T:", 2)) {
            for (const char *c = t + 2; *c; c++) { Event k; memset(&k, 0, sizeof k); k.type = UIEV_CHAR; k.ch = *c; install_handle(a, &b, &k); }
        } else if (!strncmp(t, "key=", 4)) {
            Event k; memset(&k, 0, sizeof k); k.type = UIEV_KEY; const char *n = t + 4;
            k.key = !strcmp(n, "TAB") ? KEYSYM_TAB : !strcmp(n, "BTAB") ? KEYSYM_BACKTAB : !strcmp(n, "ENTER") ? KEYSYM_ENTER
                  : !strcmp(n, "BKSP") ? KEYSYM_BACKSPACE : !strcmp(n, "ESC") ? KEYSYM_ESC : !strcmp(n, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(n, "F5") ? KEYSYM_F5 : 0;
            if (!k.key) { fprintf(stderr, "unknown key %s\n", n); return 2; }
            install_handle(a, &b, &k);
        } else { fprintf(stderr, "unknown token %s\n", t); return 2; }
        draw(a); show(a, t);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """נבנה פעם אחת למודול: הדרייבר + כל קובצי ה-GUI חוץ מ-main.c (שנכלל בדרייבר)."""
    d = tmp_path_factory.mktemp("osk2778")
    (d / "driver.c").write_text(DRIVER, encoding="utf-8", newline="\n")
    srcs = subprocess.run(["make", "-s", "-f", "Makefile", "print-gui-src"], cwd=str(GUI), capture_output=True,
                          text=True, stdin=subprocess.DEVNULL).stdout.split()
    srcs = [posix(GUI / s) for s in srcs if not s.endswith("main.c")]
    assert srcs, "make print-gui-src returned nothing"
    build = subprocess.run([BASH, "-c", 'cc -O1 -Wall -Wextra -std=c11 -D_GNU_SOURCE -I"$1/src" $(pkg-config --cflags pangocairo cairo libdrm) '
                            '-o "$2" "$3" ${@:4} $(pkg-config --libs pangocairo cairo libdrm) -lm',
                            "_", posix(GUI), posix(d / "drv"), posix(d / "driver.c"), *srcs],
                           capture_output=True, text=True, timeout=300, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return d / "drv"


def _run(driver: Path, tmp_path: Path, *tokens: str) -> tuple[list[dict], dict, list[str]]:
    """מריץ את הרצף. מחזיר (מצב אחרי כל אסימון, ה-hits האחרונים, פעולות הגשר).
    ב-hits גם: "snaps" -- כל צילומי ה-hits לפי הסדר, ו-"keylines" -- שורות המקשים."""
    log = tmp_path / "bridge.log"
    log.write_text("")
    run = subprocess.run([str(driver), f'cat >/dev/null; echo >>"{log}"', *tokens], capture_output=True, text=True,
                         encoding="utf-8", timeout=120, stdin=subprocess.DEVNULL,
                         env=dict(os.environ, FONTCONFIG_FILE=str(GUI / "fonts.conf")))
    assert run.returncode == 0, run.stderr
    states: list[dict] = []
    hits: dict = {"keys": {}, "nohit": []}
    snaps: list[dict] = []
    keylines: list[str] = []
    for line in run.stdout.splitlines():
        kind, *rest = line.split("\t")
        if kind == "state":
            states.append(dict(p.split("=", 1) for p in rest))
        elif kind == "screen":
            hits = {"screen": tuple(map(float, rest)), "keys": {}, "nohit": hits["nohit"]}
            snaps.append(hits)
        elif kind == "hit":
            name, *box = rest
            hits["keys"].setdefault(name, tuple(map(float, box)))
        elif kind == "nohit":
            hits["nohit"].append(rest[0])
        elif kind == "keyline":
            keylines.append(rest[0] if rest else "")
    hits["snaps"], hits["keylines"] = snaps, keylines
    return states, hits, log.read_text().split()


def _col(states: list[dict], name: str) -> list[str]:
    return [s[name] for s in states]


def _overlap(a: tuple, b: tuple) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# --- הלחיצה פותחת; המקלדת הפיזית לא --------------------------------------------------

#: כל שדות הטקסט במתקין, והמצב של ה-demo שבו הם על המסך
FIELDS = [
    (ROLE, (), "primary_url"),
    (NETWORK, ("static",), "address"), (NETWORK, ("static",), "netmask"),
    (NETWORK, ("static",), "gateway"), (NETWORK, ("static",), "dns"),
    (HOSTNAME, (), "hostname"),
    # round/1108j: the zone is on the clock step since #2764, and the manual time (#2765) is a field there too
    (TIME, (), "timezone"), (TIME, ("ntp-org",), "ntp_servers"), (TIME, ("ntp-manual",), "manual_time"),
    (BACKUP, ("backup-other",), "backup_other"), (BACKUP, (), "backup_time"),
    (ADMIN, (), "admin_user"), (ADMIN, ("rerun",), "current_password"),
    (ADMIN, (), "password"), (ADMIN, (), "confirm"), (ADMIN, (), "root_password"), (ADMIN, (), "root_confirm"),
]


@NATIVE
@pytest.mark.parametrize(("view", "setup", "field"), FIELDS, ids=[f[2] for f in FIELDS])
def test_a_click_on_a_field_opens_the_keyboard_on_the_screen(driver, tmp_path, view, setup, field):
    states, hits, _ = _run(driver, tmp_path, f"view={view}", *setup, f"click={field}", "hits")
    assert states[-1]["focus"] == field
    assert states[-1]["osk"] == "1", f"{field}: a click did not open the keyboard"
    assert "osk:done" in hits["keys"] and "osk:1" in hits["keys"], "the keyboard is not on the screen"


@NATIVE
def test_arriving_at_a_field_from_the_keyboard_does_not_open_it(driver, tmp_path):
    """השומר: מי שמקליד במקלדת פיזית לא מקבל מקלדת על המסך שמכסה חצי מסך."""
    states, hits, _ = _run(driver, tmp_path, f"view={ADMIN}", "key=TAB", "key=TAB", "hits")
    assert _col(states, "focus")[1:] == ["admin_user", "password"]
    assert set(_col(states, "osk")) == {"0"}
    assert not [k for k in hits["keys"] if k.startswith("osk:")]


# --- המקשים מקלידים כמו מקלדת פיזית ---------------------------------------------------

def _keys(text: str) -> list[str]:
    return [f"click=osk:{c}" for c in text]


@NATIVE
def test_the_keys_type_an_address_over_the_seed(driver, tmp_path):
    """#2777 גם מהעכבר: השדה נבחר בהגעה, והמקש הראשון מחליף את ערך ה-DHCP."""
    states, _, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=address", *_keys("10.0.0.2"),
                        "key=TAB", *_keys("255.255.255.0"))   # in the middle of the screen the keyboard is over the mask
    assert states[2]["address"] == "10.44.10.37", "the demo NIC's lease seeds the field (#2096)"
    assert states[-1]["address"] == "10.0.0.2"
    assert states[-1]["netmask"] == "255.255.255.0"
    assert states[-1]["osk"] == "1", "the next field keeps the keyboard open"


@NATIVE
def test_shift_is_for_the_next_key_only(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", "click=osk:shift", "click=osk:a",
                        "click=osk:b", "click=osk:shift", "click=osk:1", "click=osk:shift", "click=osk:shift", "click=osk:c")
    assert _col(states, "shift")[2:] == ["1", "0", "0", "1", "0", "1", "0", "0"]
    assert states[-1]["password"] == "Ab!c"


@NATIVE
def test_every_ascii_character_a_password_may_hold_is_on_the_keyboard(driver, tmp_path):
    """הסיסמה דורשת תו מיוחד, וכל תו שאינו אות או ספרה נחשב (gui-bridge.sh) — אז כולם על המקלדת."""
    _, hits, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", "hits")
    lower = {k[4:] for k in hits["keys"] if k.startswith("osk:") and len(k) == 5}
    upper = "~!@#$%^&*()_+QWERTYUIOP{}|ASDFGHJKL:\"ZXCVBNM<>?"
    assert lower == set("`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./")
    assert {"osk:shift", "osk:bksp", "osk:space", "osk:done"} <= set(hits["keys"])
    # with Shift, every key of the 47 gives the other half of printable ASCII
    states, _, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password",
                        *[t for c in "`1234567890-=qwertyuiop[]\\asdfghjkl;'zxcvbnm,./" for t in ("click=osk:shift", f"click=osk:{c}")])
    assert states[-1]["password"] == upper, "in the order the keys were pressed"
    assert len(set(states[-1]["password"])) == 47


@NATIVE
def test_delete_empties_a_selected_field_then_takes_one(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=gateway", "click=osk:bksp",
                        *_keys("10.1"), "click=osk:bksp")
    assert _col(states, "gateway")[3:] == ["", "1", "10", "10.", "10.1", "10."]


@NATIVE
def test_space_types_a_space_between_two_time_servers(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={TIME}", "ntp-org", "click=ntp_servers", *_keys("a"),
                        "click=osk:space", *_keys("b"))
    assert states[-1]["ntp"] == "a b"


@NATIVE
def test_a_key_on_the_primary_address_forgets_the_green_check(driver, tmp_path):
    """#1310 דרך המקלדת על המסך: עריכה מבטלת את הבדיקה, כמו מקש פיזי."""
    states, _, bridge = _run(driver, tmp_path, f"view={ROLE}", "click=primary_url", *_keys("1"), "key=ENTER")
    assert (states[1]["ok"], states[1]["checked"]) == ("1", "1")
    assert (states[2]["ok"], states[2]["checked"]) == ("0", "0")
    assert bridge == [] and states[-1]["view"] == str(ROLE)


@NATIVE
def test_a_password_typed_on_the_screen_stays_hidden(driver, tmp_path):
    """המקלדת לא נוגעת ב"הצג": השדה נשאר ממוסך, כמו בהקלדה פיזית."""
    states, _, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", *_keys("abc"))
    assert set(_col(states, "shown")) == {"0"}
    assert states[-1]["password"] == "abc"


# --- המקלדת הפיזית ממשיכה לעבוד; סגירה -----------------------------------------------

@NATIVE
def test_the_physical_keyboard_types_while_the_keyboard_is_open(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=address", "T:10.1",
                        "click=osk:5", "key=BKSP", "T:7")
    assert _col(states, "address")[3:] == ["10.1", "10.15", "10.1", "10.17"]
    assert set(_col(states, "osk")[2:]) == {"1"}


@NATIVE
def test_done_closes_it_and_its_keys_are_gone(driver, tmp_path):
    states, hits, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", *_keys("srv"), "click=osk:done",
                           "click=osk:x", "hits")
    assert _col(states, "osk")[1:] == ["1", "1", "1", "1", "0"]
    assert states[-1]["hostname"] == "srv" and states[-1]["focus"] == "hostname"
    assert hits["nohit"] == ["osk:x"]
    assert not [k for k in hits["keys"] if k.startswith("osk:")]


@NATIVE
def test_leaving_the_screen_closes_it(driver, tmp_path):
    states, _, bridge = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", "key=ENTER")
    assert bridge == ["validate"] and states[-1]["view"] == str(TIME)
    assert states[-1]["osk"] == "0"
    states, _, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", "click=back")
    assert states[-1]["view"] == str(NETWORK) and states[-1]["osk"] == "0"


@NATIVE
def test_off_the_fields_it_hides_and_on_a_field_again_it_is_back(driver, tmp_path):
    """פתוחה נשארת פתוחה באותו מסך; היא מוסתרת כשאין שדה בפוקוס (DHCP לקח את השדות)."""
    states, hits, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=address", "hits", "key=BTAB", "hits",
                           "T: ", "hits", "T: ", "hits")
    assert _col(states, "focus")[2:] == ["address", "static", "dhcp", "address"]
    assert set(_col(states, "osk")[2:]) == {"1"}, "on the same screen it stays open"
    shown = ["osk:panel" in snap["keys"] for snap in hits["snaps"]]
    assert shown == [True, False, False, True], "off the fields it is hidden; Space back to static brings the IP and it"


@NATIVE
def test_a_click_between_the_keys_reaches_nothing_under_the_keyboard(driver, tmp_path):
    _, hits, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", "hits")
    assert "osk:panel" in hits["keys"], "no keyboard on the screen"
    px, py, _, ph = hits["keys"]["osk:panel"]   # the bottom corner: below the keys, not the grip
    states, _, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", f"clickat={px + 3},{py + ph - 3}")
    assert states[-1]["focus"] == "password" and states[-1]["osk"] == "1"
    assert states[-1]["password"] == states[1]["password"]


# --- איפה המקלדת על המסך (הכרעת נדב אחרי הצילומים, 09/11: "במרכז, עם אפשרות להזיז") ---

SIZES = ["1024x768", "1280x800", "1920x1080"]


def _osk_keys(keys: dict) -> dict:
    return {k: v for k, v in keys.items() if k.startswith("osk:") and k not in ("osk:panel", "osk:grip", "osk:preview")}


@NATIVE
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(("view", "setup", "field"), FIELDS + [(ADMIN, ("lab",), "root_confirm")],
                         ids=[f[2] for f in FIELDS] + ["root_confirm-lab"])
def test_it_opens_in_the_middle_of_the_whole_screen(driver, tmp_path, size, view, setup, field):
    _, hits, _ = _run(driver, tmp_path, f"size={size}", f"view={view}", *setup, f"click={field}", "hits")
    keys = hits["keys"]
    sw, sh = hits["screen"]
    assert "osk:panel" in keys, f"{size} {field}: no keyboard on the screen"
    px, py, pw, ph = keys["osk:panel"]
    assert (px + pw / 2, py + ph / 2) == pytest.approx((sw / 2, sh / 2), abs=1), \
        f"{size} {field}: not in the middle of the screen ({sw}x{sh}): {keys['osk:panel']}"
    assert px >= 0 and py >= 0 and px + pw <= sw and py + ph <= sh, f"{size}: off the screen {keys['osk:panel']}"
    osk = _osk_keys(keys)
    assert len(osk) == 51
    gx, gy, gw, gh = keys["osk:grip"]
    vx, vy, vw, vh = keys["osk:preview"]
    assert (gx, gy, gw) == (px, py, pw) and gh >= 24, "the grip is the panel's top strip"
    assert gy + gh <= vy and vy + vh <= min(y for _x, y, _w, _h in osk.values()), "grip, then the preview row, then the keys"
    for name, (x, y, w, h) in osk.items():
        assert px <= x and x + w <= px + pw and py <= y and y + h <= py + ph, f"{size}: {name} outside the panel"
        assert w >= 30 and h >= 30, f"{size}: {name} is {w}x{h}, too small to click"


@NATIVE
def test_the_keys_run_left_to_right_on_the_rtl_page(driver, tmp_path):
    _, hits, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=address", "hits")
    keys = hits["keys"]
    for row in ("1234567890", "qwertyuiop", "asdfghjkl", "zxcvbnm"):
        xs = [keys[f"osk:{c}"][0] for c in row]
        assert xs == sorted(xs), f"{row}: {xs}"
        assert len({keys[f"osk:{c}"][1] for c in row}) == 1, f"{row}: not one row"
    assert keys["osk:1"][1] < keys["osk:q"][1] < keys["osk:a"][1] < keys["osk:z"][1] < keys["osk:space"][1]
    assert keys["osk:shift"][0] < keys["osk:z"][0] and keys["osk:bksp"][0] > keys["osk:="][0]


@NATIVE
def test_the_dark_theme_draws_it_too(driver, tmp_path):
    states, hits, _ = _run(driver, tmp_path, "dark", f"view={ADMIN}", "click=root_password", *_keys("x"), "hits")
    assert states[-1]["root_password"] == "x" and "osk:done" in hits["keys"]


# --- F5: פותח וסוגר מהמקלדת (הכרעת נדב, 09/11) -----------------------------------------
# F5 פנוי במתקין: KEYSYM_F5 מגיע ל-install_handle ואיש לא טיפל בו (Alt+F5 הוא החלפת VT, input.c).

#: מסך עם שדות, המצב שבו הם על המסך, והשדה הראשון בו
FIRST_FIELD = [
    (ROLE, (), "primary_url"), (NETWORK, ("static",), "address"), (HOSTNAME, (), "hostname"),
    (TIME, ("ntp-org",), "timezone"), (BACKUP, (), "backup_time"), (ADMIN, (), "admin_user"),   # #2764: the zone is first
]


@NATIVE
@pytest.mark.parametrize(("view", "setup", "first"), FIRST_FIELD, ids=[f[2] for f in FIRST_FIELD])
def test_f5_with_no_field_in_focus_opens_it_on_the_first_field(driver, tmp_path, view, setup, first):
    states, hits, _ = _run(driver, tmp_path, f"view={view}", *setup, "key=F5", "hits")
    assert states[-1]["focus"] == first
    assert states[-1]["osk"] == "1" and "osk:panel" in hits["keys"]


@NATIVE
def test_f5_on_a_field_opens_it_there_and_f5_again_closes_it(driver, tmp_path):
    states, hits, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "key=TAB", "key=TAB", "key=TAB",
                           "key=F5", "hits", "key=F5", "hits")
    assert _col(states, "focus")[-2:] == ["gateway", "gateway"], "F5 does not move a field in focus"
    assert _col(states, "osk")[-2:] == ["1", "0"]
    assert ["osk:panel" in snap["keys"] for snap in hits["snaps"]] == [True, False]


@NATIVE
def test_f5_types_nothing_into_the_field(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "key=F5", "click=osk:a", "key=F5", "key=F5", "click=osk:b")
    assert states[-1]["hostname"] == "ab"


@NATIVE
@pytest.mark.parametrize("view", [1, SUMMARY])
def test_f5_on_a_screen_with_no_text_field_does_nothing(driver, tmp_path, view):
    states, hits, bridge = _run(driver, tmp_path, f"view={view}", "key=F5", "hits")
    assert states[-1]["osk"] == "0" and states[-1]["focus"] == states[0]["focus"]
    assert not [k for k in hits["keys"] if k.startswith("osk:")] and bridge == []


@NATIVE
@pytest.mark.parametrize(("view", "setup", "first"), FIRST_FIELD, ids=[f[2] for f in FIRST_FIELD])
def test_the_key_line_says_f5_where_there_is_a_text_field(driver, tmp_path, view, setup, first):
    _, hits, _ = _run(driver, tmp_path, f"view={view}", *setup, "keyline")
    assert "F5  מקלדת מסך" in hits["keylines"][0], hits["keylines"]


@NATIVE
@pytest.mark.parametrize("view", [1, SUMMARY, NETWORK])
def test_the_key_line_says_no_f5_where_there_is_none(driver, tmp_path, view):
    """במסך הרשת ב-DHCP אין שדה פעיל, ולכן גם אין F5."""
    _, hits, _ = _run(driver, tmp_path, f"view={view}", "keyline")
    assert hits["keylines"][0] and "F5" not in hits["keylines"][0], hits["keylines"]


# --- מעבר בין שדות, ושורת התצוגה (הכרעת נדב, 09/11) --------------------------------------
# הלוח לא קופץ לבד. אם הוא מכסה את השדה שבפוקוס, השדה מוצג גם בתוכו, בשורת תצוגה מעל
# המקשים: תמיד רואים מה מקלידים, וסיסמה מוצגת מוסתרת. השורה מוצגת תמיד, כדי שהלוח לא
# ישנה גובה כשהפוקוס עובר.

#: כל מסך עם שדות, ומספר ה-Tab שמקיף את כל הטבעת שלו פעם אחת לפחות
SCREENS = [
    (ROLE, (), 4), (NETWORK, ("static",), 9), (HOSTNAME, (), 3), (TIME, ("ntp-org",), 4),
    (BACKUP, ("backup-other",), 7), (ADMIN, (), 11), (ADMIN, ("rerun",), 13), (ADMIN, ("lab",), 11),
]
#: השדה -> המפתח שלו בשורת המצב (מה שהשדה מכיל, גלוי)
SHOWN = {"address": "address", "netmask": "netmask", "gateway": "gateway", "dns": "dns", "hostname": "hostname",
         "ntp_servers": "ntp", "backup_time": "backup_time", "primary_url": "primary_url"}
MASKED = {"password", "confirm", "root_password", "root_confirm", "current_password"}


def _fields(view: int, setup: tuple) -> set[str]:
    return {f[2] for f in FIELDS if f[0] == view and set(f[1]) <= set(setup)}


def _assert_preview(snaps: list[dict], states: list[dict], size: str) -> int:
    """לכל צילום שהפוקוס בו בשדה: הלוח על המסך, שורת התצוגה מראה את השדה, והלוח לא זז."""
    checked, where = 0, None
    for snap, state in zip(snaps, states):
        field = state["focus"]
        if field not in {f[2] for f in FIELDS}:
            continue
        assert "osk:panel" in snap["keys"], f"{size}: focus on {field}, the open keyboard is not on the screen"
        where = where or snap["keys"]["osk:panel"]
        assert snap["keys"]["osk:panel"] == where, f"{size}: the keyboard jumped on {field}"
        assert state["label"], f"{size}: the preview row has no label on {field}"   # an empty field: an empty value
        if field in SHOWN:
            assert state["preview"] == state[SHOWN[field]], (field, state["preview"])
        if field in MASKED:
            assert set(state["preview"]) <= {"•"}, f"{size}: {field} shown in the clear: {state['preview']!r}"
        checked += 1
    return checked


@NATIVE
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(("view", "setup", "tabs"), SCREENS, ids=[f"{v}{'-' + s[0] if s else ''}" for v, s, _ in SCREENS])
def test_tab_between_fields_shows_the_field_in_focus_and_the_keyboard_stays_put(driver, tmp_path, size, view, setup, tabs):
    tokens = [f"size={size}", f"view={view}", *setup, "key=F5", "hits"]
    for _ in range(tabs):
        tokens += ["key=TAB", "hits"]
    for _ in range(tabs):
        tokens += ["key=BTAB", "hits"]
    states, hits, _ = _run(driver, tmp_path, *tokens)
    walked = states[-(2 * tabs + 1):]          # F5 and every Tab after it, one hits snapshot each
    assert set(_col(walked, "osk")) == {"1"}, "the keyboard closed on the way"
    assert {s["focus"] for s in walked} & {f[2] for f in FIELDS} == _fields(view, setup), "the walk missed a field"
    assert _assert_preview(hits["snaps"], walked, size) > 0


@NATIVE
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(("view", "setup", "tabs"), SCREENS, ids=[f"{v}{'-' + s[0] if s else ''}" for v, s, _ in SCREENS])
def test_a_click_from_field_to_field_shows_it_in_the_keyboard(driver, tmp_path, size, view, setup, tabs):
    """מכל שדה לכל שדה אחר, בלחיצה במרכזו. מרכז שמתחת ללוח: הלחיצה נופלת על הלוח, והפוקוס
    לא עובר דרכו. אחרת הפוקוס עובר, הלוח לא זז, ושורת התצוגה מראה את השדה החדש."""
    names = sorted(_fields(view, setup))
    for start in names:
        for target in names:
            if target == start:
                continue
            states, hits, _ = _run(driver, tmp_path, f"size={size}", f"view={view}", *setup,
                                   f"click={start}", "hits", f"click={target}", "hits")
            before, after = hits["snaps"]
            tx, ty, tw, th = before["keys"][target]
            if _overlap(before["keys"]["osk:panel"], (tx + tw / 2, ty + th / 2, 0.001, 0.001)):
                assert states[-1]["focus"] == start, f"{size}: a click through the keyboard reached {target}"
                continue
            assert states[-1]["focus"] == target
            _assert_preview([before, after], states[-2:], size)


@NATIVE
def test_nadavs_case_the_root_password_under_the_keyboard_is_shown_in_it(driver, tmp_path):
    """הצילום של נדב: פוקוס בסיסמה, והלוח מעל "סיסמת root". Tab עד אליו: הלוח לא קופץ,
    והשדה מוצג בשורת התצוגה, ממוסך, עם התווית שלו."""
    states, hits, _ = _run(driver, tmp_path, "size=1280x800", f"view={ADMIN}", "click=password", "hits",
                           "key=TAB", "key=TAB", "key=TAB", "key=TAB", "hits")
    assert states[-1]["focus"] == "root_password"
    before, after = hits["snaps"]
    assert _overlap(before["keys"]["osk:panel"], before["keys"]["root_password"]), \
        "the precondition: the keyboard does sit over the root password"
    assert "osk:panel" in after["keys"], "Tab through the eye closed the keyboard"
    assert after["keys"]["osk:panel"] == before["keys"]["osk:panel"], "the keyboard jumped"
    assert states[-1]["label"] == "סיסמת root (כניסה מקומית למסך השרת)"
    assert states[-1]["preview"] == "•" * len("R00t!haifa")


@NATIVE
def test_the_preview_row_follows_typing_and_hides_a_password(driver, tmp_path):
    states, _, _ = _run(driver, tmp_path, f"view={NETWORK}", "static", "click=address", *_keys("10.0"))
    assert states[2]["label"] == "כתובת IP" and states[2]["preview"] == "10.44.10.37"
    assert states[-1]["preview"] == "10.0"
    states, _, _ = _run(driver, tmp_path, f"view={ADMIN}", "click=password", *_keys("ab"), "click=show_password")
    assert states[-2]["preview"] == "••" and states[-2]["password"] == "ab"
    assert states[-1]["preview"] == "ab", "the eye shows it in the field and in the row alike"


# --- גרירה ------------------------------------------------------------------------------

@NATIVE
@pytest.mark.parametrize("size", SIZES)
def test_the_grip_drags_the_keyboard_and_letting_go_stops_it(driver, tmp_path, size):
    _, hits, _ = _run(driver, tmp_path, f"size={size}", f"view={ADMIN}", "click=password", "hits",
                      "drag=osk:grip,-90,-70", "hits")
    before, after = (snap["keys"]["osk:panel"] for snap in hits["snaps"])
    assert after[0] == pytest.approx(before[0] - 90) and after[1] == pytest.approx(before[1] - 70), \
        f"{size}: the drag moved it from {before} to {after}; the move after letting go must not count"
    assert after[2:] == before[2:]


@NATIVE
def test_a_drag_from_a_key_does_not_move_it(driver, tmp_path):
    """רק פס האחיזה גורר. לחיצה על מקש מקלידה, ותזוזה אחריה לא מזיזה את הלוח."""
    states, hits, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", "hits", "drag=osk:q,-90,-70", "hits")
    before, after = (snap["keys"]["osk:panel"] for snap in hits["snaps"])
    assert after == before and states[-1]["hostname"] == "q"


@NATIVE
@pytest.mark.parametrize("size", SIZES)
@pytest.mark.parametrize(("dx", "dy"), [(-5000, -5000), (5000, 5000), (-5000, 5000), (5000, -5000)])
def test_the_keyboard_never_leaves_the_screen(driver, tmp_path, size, dx, dy):
    states, hits, _ = _run(driver, tmp_path, f"size={size}", f"view={NETWORK}", "static", "click=address",
                           f"drag=osk:grip,{dx},{dy}", "hits")
    sw, sh = hits["screen"]
    assert "osk:panel" in hits["keys"]
    px, py, pw, ph = hits["keys"]["osk:panel"]
    assert px >= 0 and py >= 0 and px + pw <= sw and py + ph <= sh, (size, hits["keys"]["osk:panel"])
    assert (px == 0 or px + pw == pytest.approx(sw)) and (py == 0 or py + ph == pytest.approx(sh)), "pushed to the edge"
    assert states[-1]["osk"] == "1"


@NATIVE
def test_where_it_was_put_it_stays_across_done_f5_and_screens(driver, tmp_path):
    """זיכרון מיקום, כל עוד המתקין רץ: "סיום", F5 ומעבר מסך לא מחזירים אותו למרכז."""
    _, hits, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", "drag=osk:grip,-120,-150", "hits",
                      "click=osk:done", "key=F5", "hits")
    moved, reopened = (snap["keys"].get("osk:panel") for snap in hits["snaps"])
    assert moved and reopened == moved, "'סיום' + F5 brought it back to the middle"
    # hostname -> time -> backup -> admin; F5 on the admin screen's first field
    _, hits2, _ = _run(driver, tmp_path, f"view={HOSTNAME}", "click=hostname", "drag=osk:grip,-120,-150",
                       "key=ENTER", "key=ENTER", "key=ENTER", "key=F5", "hits")
    assert hits2["keys"].get("osk:panel") == moved, "the next screens opened it elsewhere"
