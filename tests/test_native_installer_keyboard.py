"""‏#1206: כל מסך במתקין נגמר בחצים, רווח, Enter ו-Esc בלבד.

נמדד ב-23/09 על VM של ESXi: הקונסולה של ESXi בדפדפן מעבירה ל-VM את
החצים, Enter ו-Esc — **אבל לא Tab**, שהדפדפן בולע. ‏Tab שנשלח ישירות
(`govc vm.keystrokes`) כן הזיז פוקוס, כלומר המתקין טיפל בו; מה שחסר הוא
טיפול בחצים מחוץ למסך הדיסק. בלי עכבר (#1204) לא הייתה דרך לבחור תפקיד,
כרטיס רשת או DHCP/סטטי, ולא לעבור בין שדות הסיסמה.

החוזה שנבדק כאן:

* ‏↑/↓ (וגם Tab/Shift+Tab) עוברים על **כל** הפקדים במסך, בסדר הקריאה.
* נחיתה על אפשרות (תפקיד, כרטיס רשת, DHCP/סטטי) **בוחרת** אותה — כמו
  ברשימת הדיסקים: מה שמסומן הוא מה שנבחר.
* רווח מחוץ לשדה טקסט לוחץ על הכפתור שבפוקוס ("בדוק חיבור"), ובמסך הרשת
  מחליף DHCP↔סטטי בלי לעבור על שורות הכרטיסים האחרים.
* חץ שלא שינה דיסק אינו מבטל את "אני מבין" (VM עם דיסק אחד + ISO).

הדרייבר כולל את `install.c` ישירות (‏`#include`), כמו
‏`test_native_gui_input.py` עם `input.c`, ומזין אירועים ל-`install_handle`
על מצב ה-demo. הגשר הוא `echo >>LOG` — כל פעולה שהמתקין שלח נרשמת.
‏cc + כותרות cairo = לינוקס בלבד; בווינדוס זה דילוג, לא מעבר.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from native import requires_native

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

ROLE, NETWORK, ADMIN, DISK, SUMMARY = 2, 3, 5, 1, 6


def _pkgconfig(*pkgs: str) -> bool:
    if not shutil.which("pkg-config"):
        return False
    return subprocess.run(["pkg-config", "--exists", *pkgs],
                          stdin=subprocess.DEVNULL).returncode == 0


NATIVE = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("cairo", _pkgconfig("cairo")),
    posix=True, why="install.c נבנה ומורץ על לינוקס בלבד",
)

DRIVER = r'''
#include "%(install_c)s"
#include <stdio.h>

int app_hit(const App *a, double x, double y) { (void)a; (void)x; (void)y; return 0; }

static const char *focus_name(const App *a) {
    static char buf[32];
    switch (a->focus) {
    case 0: return "none";
    case HIT_INSTALL_PRIMARY: return "primary";
    case HIT_INSTALL_SECONDARY: return "secondary";
    case HIT_INSTALL_PRIMARY_URL: return "primary_url";
    case HIT_INSTALL_CHECK_PRIMARY: return "check";
    case HIT_INSTALL_DHCP: return "dhcp";
    case HIT_INSTALL_STATIC: return "static";
    case HIT_INSTALL_ADDRESS: return "address";
    case HIT_INSTALL_NETMASK: return "netmask";
    case HIT_INSTALL_GATEWAY: return "gateway";
    case HIT_INSTALL_DNS: return "dns";
    case HIT_INSTALL_HOSTNAME: return "hostname";
    case HIT_INSTALL_ADMIN_USER: return "admin_user";
    case HIT_INSTALL_PASSWORD: return "password";
    case HIT_INSTALL_CONFIRM: return "confirm";
    case HIT_INSTALL_CURRENT_PASSWORD: return "current_password";
#ifdef HIT_INSTALL_ROOT_PASSWORD
    case HIT_INSTALL_ROOT_PASSWORD: return "root_password";
    case HIT_INSTALL_ROOT_CONFIRM: return "root_confirm";
#endif
    }
    if (a->focus >= HIT_INSTALL_NIC_BASE && a->focus < HIT_INSTALL_NIC_BASE + INSTALL_MAX_NICS) {
        snprintf(buf, sizeof buf, "nic%%d", a->focus - HIT_INSTALL_NIC_BASE);
        return buf;
    }
    snprintf(buf, sizeof buf, "id%%d", a->focus);
    return buf;
}

static void show(const App *a, const char *tok) {
    const InstallState *s = &a->install;
    printf("%%s view=%%d focus=%%s secondary=%%d nic=%%d static=%%d disk=%%d ack=%%d ok=%%d checked=%%d\n", tok,
           s->view, focus_name(a), s->secondary, s->nic, s->static_mode, s->disk, s->disk_ack,
           s->primary_ok, s->primary_checked);
}

static App app;

/* argv: BRIDGE-CMD VIEW TOKEN... -- the demo state on VIEW, focus as on
 * arrival (refocus), then one line of state after every token. */
int main(int argc, char **argv) {
    if (argc < 3) return 2;
    App *a = &app;
    InstallBridge b = { argv[1], NULL, 0 };
    install_demo(a, atoi(argv[2]));
    refocus(a);
    show(a, "start");
    for (int i = 3; i < argc; i++) {
        const char *t = argv[i];
        Event e = { 0 };
        if (!strcmp(t, "only-disk-0")) { a->install.disks[1].iso = 1; show(a, t); continue; }
        if (!strcmp(t, "technical-open")) { a->install.technical_open = 1; show(a, t); continue; }
        if (!strcmp(t, "unchecked")) { a->install.primary_ok = a->install.primary_checked = 0; show(a, t); continue; }
        /* #2147: the inventory through the bridge and parse_lines, as on a server */
        if (!strcmp(t, "inventory")) { call(a, &b, "inventory", "{}"); show(a, t); continue; }
        if (!strcmp(t, "CHAR1")) { e.type = UIEV_CHAR; e.ch = '1'; install_handle(a, &b, &e); show(a, t); continue; }
        /* #1632: root=VALUE / rootconfirm=VALUE set the fields (no-ops on a
         * GUI without them); JSON prints what "next" would send, to stderr. */
        if (!strncmp(t, "root=", 5) || !strncmp(t, "rootconfirm=", 12)) {
#ifdef HIT_INSTALL_ROOT_PASSWORD
            if (t[4] == '=') snprintf(a->install.root_password, sizeof a->install.root_password, "%%s", t + 5);
            else snprintf(a->install.root_confirm, sizeof a->install.root_confirm, "%%s", t + 12);
#endif
            show(a, t[4] == '=' ? "root" : "rootconfirm"); continue;
        }
        if (!strncmp(t, "pw=", 3)) { snprintf(a->install.password, sizeof a->install.password, "%%s", t + 3); show(a, "pw"); continue; }
        if (!strncmp(t, "pwconfirm=", 10)) { snprintf(a->install.confirm, sizeof a->install.confirm, "%%s", t + 10); show(a, "pwconfirm"); continue; }
        if (!strcmp(t, "JSON")) { char json[4096]; config_json(a, json, sizeof json, 0); fprintf(stderr, "%%s\n", json); show(a, t); continue; }
        if (!strcmp(t, "SPACE")) { e.type = UIEV_CHAR; e.ch = ' '; }
        else {
            e.type = UIEV_KEY;
            e.key = !strcmp(t, "UP") ? KEYSYM_UP : !strcmp(t, "DOWN") ? KEYSYM_DOWN
                  : !strcmp(t, "TAB") ? KEYSYM_TAB : !strcmp(t, "BTAB") ? KEYSYM_BACKTAB
                  : !strcmp(t, "ENTER") ? KEYSYM_ENTER : !strcmp(t, "ESC") ? KEYSYM_ESC
                  : !strcmp(t, "BKSP") ? KEYSYM_BACKSPACE : 0;
            if (!e.key) { fprintf(stderr, "unknown token %%s\n", t); return 2; }
        }
        install_handle(a, &b, &e);
        show(a, t);
    }
    return 0;
}
'''


@pytest.fixture(scope="module")
def driver(tmp_path_factory) -> Path:
    """נבנה פעם אחת למודול; הסימון `NATIVE` מדלג/נופל לפני שמגיעים לכאן."""
    tmp = tmp_path_factory.mktemp("install-keys")
    source = tmp / "install_keys.c"
    source.write_text(DRIVER % {"install_c": (SRC / "install.c").as_posix()}, encoding="utf-8")
    binary = tmp / "install_keys"
    command = ('set -e; cc -O2 -Wall -Wextra -std=c11 -D_GNU_SOURCE $(pkg-config --cflags cairo) '
               '-I"$1" -o "$2" "$3" $(pkg-config --libs cairo) -lm')
    build = subprocess.run(["sh", "-c", command, "_", str(SRC), str(binary), str(source)],
                           capture_output=True, text=True, timeout=120,
                           stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    return binary


def _press(driver: Path, tmp_path: Path, view: int, *tokens: str) -> tuple[list[dict], list[str]]:
    """מריץ את הרצף ומחזיר (מצב אחרי כל מקש, פעולות הגשר שנשלחו)."""
    log = tmp_path / "bridge.log"
    log.write_text("")
    proc = subprocess.run([str(driver), f'echo >>"{log}"', str(view), *tokens],
                          capture_output=True, text=True, timeout=30,
                          stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        tok, *pairs = line.split()
        state = dict(pair.split("=", 1) for pair in pairs)
        state["key"] = tok
        states.append(state)
    names = [x.split("=", 1)[0] for x in tokens]
    assert [s["key"] for s in states] == ["start", *names], proc.stdout
    return states, log.read_text().split()


def _col(states: list[dict], name: str) -> list[str]:
    return [s[name] for s in states]


@NATIVE
def test_role_arrows_switch_the_role_and_reach_the_url_and_the_check_button(driver, tmp_path):
    """מסך 2: בלי Tab לא הייתה דרך לחזור ל"שרת ראשי" או להגיע ל"בדוק חיבור"."""
    states, bridge = _press(driver, tmp_path, ROLE, "UP", "UP", "DOWN", "DOWN", "DOWN", "SPACE")
    assert _col(states, "focus") == [
        "primary_url", "secondary", "primary", "secondary", "primary_url", "check", "check"]
    # נחיתה על אפשרות בוחרת אותה: ‏UP השני עבר ל"ראשי", ‏DOWN החזיר ל"משני"
    assert _col(states, "secondary") == ["1", "1", "0", "1", "1", "1", "1"]
    assert bridge == ["check-primary"], "רווח על 'בדוק חיבור' לוחץ עליו — ורק עליו"


@NATIVE
def test_network_arrows_pick_the_nic_and_pass_the_mode_without_changing_it(driver, tmp_path):
    """‏#2096: DHCP/סטטי הוא עצירה **אחת** — האפשרות שנבחרה. ‏↑ מהשדות אל שורת
    הכרטיס עבר דרך "DHCP" והחליף מצב (Hyper-V, 24/10); המצב מתחלף ברווח."""
    states, _ = _press(driver, tmp_path, NETWORK, "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "UP", "UP")
    assert _col(states, "focus") == [
        "nic0", "nic1", "nic2", "dhcp", "nic0", "nic1", "nic0", "dhcp"]
    assert _col(states, "nic") == ["0", "1", "2", "2", "0", "1", "0", "0"]
    assert _col(states, "static") == ["0"] * 8


@NATIVE
def test_network_space_flips_to_static_without_walking_past_the_other_nics(driver, tmp_path):
    """‏ens19 + כתובת סטטית: הליכה בחצים עד "סטטית" הייתה בוחרת את ens20
    בדרך. רווח מחליף מצב במקום, והפוקוס עובר לשדה ה-IP; ↑ מגיע לעצירת
    המצב בלי להחליף אותו, ורווח שם מחזיר ל-DHCP."""
    states, _ = _press(driver, tmp_path, NETWORK, "DOWN", "SPACE", "UP", "SPACE")
    assert _col(states, "focus") == ["nic0", "nic1", "address", "static", "dhcp"]
    assert _col(states, "nic") == ["0", "1", "1", "1", "1"]
    assert _col(states, "static") == ["0", "0", "1", "1", "0"]


@NATIVE
def test_admin_arrows_move_between_the_fields_like_tab(driver, tmp_path):
    """מסך 5: נמדד ש-Tab עובר מהמשתמש לסיסמה — אבל Tab לא מגיע מהדפדפן.
    ‏#1632: שני שדות ה-root באותו מסך, אחרי אימות סיסמת ה-admin."""
    states, _ = _press(driver, tmp_path, ADMIN, "DOWN", "DOWN", "DOWN", "DOWN", "DOWN", "UP", "TAB", "BTAB")
    assert _col(states, "focus") == [
        "admin_user", "password", "confirm", "root_password", "root_confirm", "admin_user",
        "root_confirm", "admin_user", "root_confirm"]


@NATIVE
def test_disk_arrow_that_changes_nothing_keeps_the_acknowledgement(driver, tmp_path):
    """VM עם דיסק אחד ו-ISO: החץ לא החליף דיסק, ובכל זאת מחק את הסימון
    "אני מבין" — ונראה כאילו החצים לא עושים כלום."""
    states, _ = _press(driver, tmp_path, DISK, "only-disk-0", "DOWN", "UP")
    assert _col(states, "disk") == ["0", "0", "0", "0"]
    assert _col(states, "ack") == ["1", "1", "1", "1"]


@NATIVE
def test_disk_arrow_that_changes_the_disk_still_clears_the_acknowledgement(driver, tmp_path):
    """השומר מהצד השני: דיסק אחר = אישור חדש. רווח מאשר שוב."""
    states, _ = _press(driver, tmp_path, DISK, "DOWN", "SPACE", "DOWN")
    assert _col(states, "disk") == ["0", "1", "1", "0"]  # ‏sr0 היא המדיה ומדולגת
    assert _col(states, "ack") == ["1", "0", "1", "0"]


@NATIVE
def test_a_leftover_technical_open_does_not_steal_the_arrows(driver, tmp_path):
    """‏technical_open נשאר דלוק אחרי ריצה שנכשלה ו"נסה שוב"; החצים גללו
    פלט שאינו על המסך במקום לזוז בין הפקדים."""
    states, _ = _press(driver, tmp_path, ROLE, "technical-open", "UP")
    assert _col(states, "focus")[-1] == "secondary"


@NATIVE
def test_escape_goes_back_and_enter_validates_forward_unchanged(driver, tmp_path):
    """השומר מפני תיקון-יתר: Enter ו-Esc עבדו לפני #1206 וממשיכים לעבוד."""
    states, bridge = _press(driver, tmp_path, ROLE, "ESC", "ENTER", "ENTER")
    assert _col(states, "view") == [str(ROLE), str(DISK), str(ROLE), str(NETWORK)]
    assert bridge == ["validate", "validate"]


@NATIVE
def test_next_on_the_role_screen_waits_for_a_green_check(driver, tmp_path):
    """‏#1310 (נדב 12/10: "זה לא תקין אם זה לא ירוק"): משני עובר את מסך
    התפקיד רק אחרי בדיקה ירוקה. Enter הוא הדרך היחידה ב-ESXi — הכפתור
    המושבת לבדו לא היה עוצר אותו."""
    states, bridge = _press(driver, tmp_path, ROLE, "ENTER")      # the demo is green
    assert bridge == ["validate"] and states[-1]["view"] == str(NETWORK)

    states, bridge = _press(driver, tmp_path, ROLE, "unchecked", "ENTER")
    assert bridge == [] and states[-1]["view"] == str(ROLE), "not checked is not ok"

    states, bridge = _press(driver, tmp_path, ROLE, "UP", "UP", "ENTER")   # primary: no check
    assert _col(states, "secondary")[2] == "0" and bridge == ["validate"]


@NATIVE
@pytest.mark.parametrize("edit", ["CHAR1", "BKSP"])
def test_editing_the_address_forgets_the_green_check(driver, tmp_path, edit):
    states, bridge = _press(driver, tmp_path, ROLE, edit, "ENTER")
    assert states[0]["focus"] == "primary_url"
    assert (states[1]["ok"], states[1]["checked"]) == ("0", "0")
    assert bridge == [] and states[-1]["view"] == str(ROLE)


@NATIVE
def test_a_check_turns_green_only_on_ok_1_from_the_bridge(driver, tmp_path):
    """הגשר כאן עונה בלי `ok=` כלל. לפני #1310 ה-ok הקודם (ירוק) נשאר —
    "לא נבדק" נקרא כ"תקין"."""
    states, bridge = _press(driver, tmp_path, ROLE, "DOWN", "SPACE", "ENTER")
    assert bridge == ["check-primary"]
    assert (states[2]["ok"], states[2]["checked"]) == ("0", "1")
    assert states[-1]["view"] == str(ROLE)


# --- #1632 (Nadav 24/10): root's password on the admin screen --------------------------

@NATIVE
@pytest.mark.parametrize(("root", "confirm"), [
    ("Ab1!xyz", "Ab1!xyz"),             # 7 characters
    ("abcdefgh!", "abcdefgh!"),         # no digit
    ("abcdefg12", "abcdefg12"),         # no special character
    ("R00t!haifa", "R00t!haifb"),       # the confirmation differs
    ("", ""),                           # not filled at all
])
def test_next_on_the_admin_screen_waits_for_a_valid_root_password(driver, tmp_path, root, confirm):
    """Enter is the only way forward on ESXi; a disabled button alone would not stop it."""
    states, bridge = _press(driver, tmp_path, ADMIN, f"root={root}", f"rootconfirm={confirm}", "ENTER")
    assert bridge == [], "the bridge was asked to validate a root password the screen should refuse"
    assert states[-1]["view"] == str(ADMIN)


@NATIVE
def test_a_valid_root_password_goes_on_to_the_summary(driver, tmp_path):
    """The guard: the demo's root password passes, so the rule is not 'refuse all'."""
    states, bridge = _press(driver, tmp_path, ADMIN, "root=Other!pw9", "rootconfirm=Other!pw9", "ENTER")
    assert bridge == ["validate"] and states[-1]["view"] == str(SUMMARY)


@NATIVE
def test_the_root_password_reaches_the_bridge_on_stdin_never_in_argv(driver, tmp_path):
    """"Apply" on the summary: the bridge is a script that records its argv and
    its stdin. The root password is in the JSON on stdin, and nowhere in argv."""
    argv, stdin = tmp_path / "argv", tmp_path / "stdin"
    script = tmp_path / "bridge.sh"
    script.write_text(f'printf "%s\\n" "$*" >> "{argv}"\ncat >> "{stdin}"\n', encoding="utf-8")
    proc = subprocess.run([str(driver), f"sh {script}", str(SUMMARY), "root=Picked!on5creen",
                           "rootconfirm=Picked!on5creen", "JSON", "ENTER"],
                          capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    assert '"root_password":"Picked!on5creen"' in proc.stderr and '"root_password_confirm":"Picked!on5creen"' in proc.stderr
    assert argv.read_text().split() == ["apply"]
    assert '"root_password":"Picked!on5creen"' in stdin.read_text()



# --- one "special character" rule for the screen and the bridge (rc1, 24/10) ----------
# The GUI counted only !@#$%^&*(),.?":{}|<> as special; gui-bridge.sh counts any
# character that is not a letter or a digit. "-" and "_" (named under the user
# name field) were refused on the screen and accepted by the server.

def _bridge_accepts(field: str, pw: str) -> bool:
    form = {"step": "4", "password": "Imag3ctl!haifa", "password_confirm": "Imag3ctl!haifa",
            "root_password": "R00t!haifa", "root_password_confirm": "R00t!haifa"}
    form[field] = form[field + "_confirm"] = pw
    got = subprocess.run(["sh", str(REPO / "installer" / "gui-bridge.sh"), "validate"], input=json.dumps(form),
                         capture_output=True, text=True, timeout=30, env={**os.environ, "LC_ALL": "C"})
    assert got.returncode == 0, got.stderr
    return not [line for line in got.stdout.splitlines() if line.startswith("errors.")]


@NATIVE
@pytest.mark.skipif(shutil.which("jq") is None or shutil.which("sh") is None, reason="the bridge needs sh + jq")
@pytest.mark.parametrize("which", ["password", "root_password"])
@pytest.mark.parametrize("pw", ["Abcdefg1-", "Abcdefg1_", "Abcdefg1 ", "Abcdefg1~", "Abcdefg1=",
                                "Abcdefg1!", "Abcdefg1\u05d0", "Abcdefgh1"])
def test_the_screen_and_the_bridge_agree_on_what_a_special_character_is(driver, tmp_path, which, pw):
    tok = "pw" if which == "password" else "root"
    _, bridge = _press(driver, tmp_path, ADMIN, f"{tok}={pw}", f"{tok}confirm={pw}", "ENTER")
    screen_accepts = bridge == ["validate"]
    assert screen_accepts == _bridge_accepts(which, pw), f"screen {screen_accepts}, bridge the opposite, for {pw!r}"
    assert screen_accepts == (pw != "Abcdefgh1"), "any character that is not a letter or a digit is special"


# --- #2147 (rc4, ESXi 24/10): two disks, and the installer chose one by itself ---------
# The empty 20 GB sda was marked "will be erased" before anyone looked; ticking
# "I understand" + Enter would have installed on it (the system disk was sdb).

RC4_TWO_DISKS = ("disk=/dev/sda|model=Virtual disk|size=21474836480|bus=unknown|removable=0|has=empty|iso=0",
                 "disk=/dev/sdb|model=Virtual disk|size=42949672960|bus=unknown|removable=0|"
                 "has=gpt:3 partitions (ext4, vfat)|iso=0",
                 "disk=/dev/sr0|model=VMware SATA CD|size=1073741824|bus=sata|removable=1|has=iso9660|iso=1",
                 "virt=virtual")


def _press_inventory(driver: Path, tmp_path: Path, inventory: tuple[str, ...], *tokens: str):
    """Like _press, with a bridge that answers `inventory` with these lines; the
    bridge log is every action the installer sent, and stderr carries JSON."""
    log, inv = tmp_path / "bridge.log", tmp_path / "inventory"
    log.write_text("")
    inv.write_text("\n".join(inventory) + "\n", encoding="utf-8", newline="\n")
    script = tmp_path / "bridge.sh"
    script.write_text(f'echo "$1" >> "{log}"\n[ "$1" = inventory ] && cat "{inv}"\nexit 0\n',
                      encoding="utf-8", newline="\n")
    proc = subprocess.run([str(driver), f"sh {script}", str(DISK), "inventory", *tokens],
                          capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr
    states = []
    for line in proc.stdout.splitlines():
        tok, *pairs = line.split()
        states.append({"key": tok, **dict(pair.split("=", 1) for pair in pairs)})
    assert [s["key"] for s in states] == ["start", "inventory", *tokens], proc.stdout
    return states[1:], log.read_text().split(), proc.stderr


@NATIVE
def test_two_disks_start_with_no_choice_and_enter_goes_nowhere(driver, tmp_path):
    """The rule: more than one disk the installer may use = no disk chosen.
    Space ("I understand") and Enter do nothing until the person picks one."""
    states, bridge, _ = _press_inventory(driver, tmp_path, RC4_TWO_DISKS, "SPACE", "ENTER", "SPACE", "ENTER")
    assert _col(states, "disk") == ["-1"] * 5, "a disk was chosen without the person choosing it"
    assert _col(states, "ack") == ["0"] * 5
    assert _col(states, "view") == [str(DISK)] * 5
    assert bridge == ["inventory"], "Enter with no disk chosen reached the bridge"


@NATIVE
def test_an_arrow_is_the_choice_and_then_ack_and_enter_go_on_with_that_disk(driver, tmp_path):
    """The guard from the other side: the keyboard alone still installs -- on the
    disk the person walked to, not on the first one listed."""
    states, bridge, err = _press_inventory(driver, tmp_path, RC4_TWO_DISKS,
                                           "DOWN", "DOWN", "SPACE", "JSON", "ENTER")
    assert _col(states, "disk") == ["-1", "0", "1", "1", "1", "1"]
    assert _col(states, "ack") == ["0", "0", "0", "1", "1", "1"]
    assert '"disk":"/dev/sdb"' in err
    assert bridge == ["inventory", "validate"] and states[-1]["view"] == str(ROLE)


@NATIVE
def test_one_disk_and_the_install_media_still_start_with_that_disk(driver, tmp_path):
    """One disk the installer may use is that disk (allowed, as before) -- but the
    "I understand" is still the person's own tick."""
    states, bridge, _ = _press_inventory(driver, tmp_path, (RC4_TWO_DISKS[1], RC4_TWO_DISKS[2]), "ENTER", "SPACE")
    assert _col(states, "disk") == ["0", "0", "0"]
    assert _col(states, "ack") == ["0", "0", "1"]
    assert bridge == ["inventory"]
