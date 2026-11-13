"""#1245: עברית בשדות הטקסט של הגואי הנייטיב.

נדב, 10/10, על ה-HP: אי אפשר להקליד עברית בשם ובתיאור של קליטה. ההכרעה
שלו באותו יום: **עברית בכל שדה שאפשר**, ומגבלה רק בשדה שבאמת לא יכול
לקבל עברית — ושם עם הסבר בממשק.

מה נבנה, ומה נבדק כאן:

* `input.c` מוסר לכל מקש גם את התו שלו בפריסה הישראלית התקנית (SI-1452,
  המצב הבסיסי): `Event.he`. `Event.ch` נשאר ה-US — קיצורי המקשים
  (W/N/S/R, 1–9) עובדים לפי המקש הפיזי בשתי השפות, והמתקין (שכל שדותיו
  אנגלית: כתובות, שם מחשב, סיסמה) לא נוגע ב-`he` כלל.
* Shift משאיר את האותיות הלטיניות הגדולות ואת סימני ה-US, כמו ב-SI-1452.
* Alt+Shift מחליף שפה — פעם אחת, על הלחיצה שסוגרת את הצירוף; חזרה
  אוטומטית (value 2) של מקש מוחזק אינה החלפה נוספת.
* `main.c` שומר את התו כ-UTF-8 (`utf8.h`), שלם או בכלל לא.
* שדות אנגלית בלבד: משתמש וסיסמה (חשבון קונסולה), מספר היעדים (ספרות),
  והארגומנט של כלי (מילה לכלי shell). שם האימג', התיאור והתיקייה החדשה
  — עברית (`images.DISPLAY_NAME_RE` מקבל א-ת).
* שורת הסטטוס מראה את השפה כשבפוקוס שדה טקסט: "עב" מלא, "EN" במסגרת —
  ולחיצה עליו מחליפה; בשדה אנגלית-בלבד — ההסבר במקום מתג.

שלושה סוגי טסט, כמו בכל native-gui: חוזי מקור (רצים גם בווינדוס, בלי
מהדר), דרייבר C שכולל את `input.c` ו-`utf8.h` ישירות (cc בלבד, בלי root
ובלי uinput), ורינדור `--png` (cc + pango/cairo, ה-Testrunner).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from native import requires_native
from test_cloner_gui import _build_gui, _count_color, _pkgconfig, _png_rgb, _render_all, _token

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

#: SI-1452, המצב הבסיסי (בלי Shift): כל מקש ש**שונה** מ-US. 27 האותיות
#: (22 + 5 סופיות), ועוד חמשת סימני הפיסוק שזזו. כל מקש אחר — כמו US.
SI1452 = {
    "KEY_GRAVE": ";", "KEY_Q": "/", "KEY_W": "'",
    "KEY_E": "ק", "KEY_R": "ר", "KEY_T": "א", "KEY_Y": "ט", "KEY_U": "ו",
    "KEY_I": "ן", "KEY_O": "ם", "KEY_P": "פ",
    "KEY_A": "ש", "KEY_S": "ד", "KEY_D": "ג", "KEY_F": "כ", "KEY_G": "ע",
    "KEY_H": "י", "KEY_J": "ח", "KEY_K": "ל", "KEY_L": "ך",
    "KEY_SEMICOLON": "ף", "KEY_APOSTROPHE": ",",
    "KEY_Z": "ז", "KEY_X": "ס", "KEY_C": "ב", "KEY_V": "ה", "KEY_B": "נ",
    "KEY_N": "מ", "KEY_M": "צ", "KEY_COMMA": "ת", "KEY_DOT": "ץ", "KEY_SLASH": ".",
}


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def _code(text: str) -> str:
    """<text> בלי הערות C — הערה יכולה לצטט את הקוד הישן."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def _body(text: str, name: str) -> str:
    """הסוגריים של ההגדרה של <name> (לא של קריאה)."""
    m = re.search(r"\n(?:static )?[\w][\w \*]*\b" + re.escape(name) + r"\([^;{)]*\)\s*\{", text)
    assert m, f"{name}() אינה מוגדרת כאן"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


def _c_char(literal: str) -> str:
    inner = literal.strip()[1:-1]
    return {"\\'": "'", "\\\\": "\\"}.get(inner, inner)


def _keymap() -> dict[str, tuple[str, str, str | None]]:
    """KEYMAP של input.c: code → (רגיל, Shift, עברית או None)."""
    table = re.search(r"KEYMAP\[\]\s*=\s*\{(.*?)\n\};", _code(_src("input.c")), re.S)
    assert table, "KEYMAP[] לא נמצא ב-input.c"
    char = r"'(?:\\.|[^'])'"
    rows = re.findall(r"\{\s*(KEY_\w+)\s*,\s*(" + char + r")\s*,\s*(" + char + r")\s*(?:,\s*([^{}]+?))?\s*\}",
                      table.group(1))
    out = {}
    for code, plain, shifted, he in rows:
        hebrew = None                       # 0 (או בלי עמודה) = כמו US
        he = (he or "").strip()
        if he and he != "0":
            hebrew = chr(int(he, 16)) if he.lower().startswith("0x") else _c_char(he)
        out[code] = (_c_char(plain), _c_char(shifted), hebrew)
    return out


# --- 1. הפריסה ----------------------------------------------------------------


def test_si1452_base_level_maps_every_hebrew_letter_and_the_moved_punctuation():
    keymap = _keymap()
    assert len(keymap) > 60, f"KEYMAP נקרא חלקית: {len(keymap)} שורות"
    hebrew = {code: he for code, (_n, _s, he) in keymap.items() if he is not None}
    assert hebrew == SI1452
    letters = sorted(he for he in hebrew.values() if "א" <= he <= "ת")
    assert letters == [chr(cp) for cp in range(0x05D0, 0x05EB)], "חסרה אות, או אות כפולה"


def test_shift_keeps_the_us_level_on_every_key():
    """SI-1452: Shift על מקש אות נותן את האות הלטינית הגדולה. העמודה השנייה
    של KEYMAP לא זזה — העברית היא עמודה רביעית, לא החלפה של הקיימות."""
    keymap = _keymap()
    for letter in "QWERTYUIOPASDFGHJKLZXCVBNM":
        assert keymap[f"KEY_{letter}"][:2] == (letter.lower(), letter), letter


# --- 2. input.c / input.h -------------------------------------------------------


def test_event_carries_the_hebrew_character_and_alt_shift_is_a_key():
    header = _code(_src("input.h"))
    event = re.search(r"typedef struct \{(.*?)\} Event;", header, re.S)
    assert event and re.search(r"\bint he;", event.group(1)), "Event.he חסר"
    assert "KEYSYM_LAYOUT" in header
    key_event = _code(_body(_src("input.c"), "key_event"))
    assert "KEY_LEFTALT" in key_event and "KEY_RIGHTALT" in key_event
    assert "KEYSYM_LAYOUT" in key_event
    # Shift מחזיר את ה-US; רק המצב הבסיסי מקבל את העמודה העברית
    assert re.search(r"e\.he\s*=\s*!d->shift\s*&&\s*KEYMAP\[i\]\.he\s*\?\s*KEYMAP\[i\]\.he\s*:\s*e\.ch", key_event)


# --- 3. main.c -----------------------------------------------------------------


def test_english_only_fields_are_the_account_the_count_and_the_tool_argument():
    only = _code(_body(_src("main.c"), "field_english_only"))
    assert set(re.findall(r"HIT_\w+", only)) == {"HIT_USER", "HIT_PASS", "HIT_ROOM_TARGET", "HIT_TOOL_ARG"}


def test_typing_goes_through_the_language_and_is_stored_as_utf8():
    main = _src("main.c")
    handle = _code(_body(main, "handle"))
    assert re.search(r"KEYSYM_LAYOUT\)\s*\{\s*a->lang_he\s*=\s*!a->lang_he", handle), "Alt+Shift אינו מחליף"
    assert re.search(r"case HIT_LANG:\s*a->lang_he\s*=\s*!a->lang_he", handle), "לחיצה על החיווי אינה מחליפה"
    assert re.search(r"field_append\(buf,\s*n,\s*a->lang_he\s*&&\s*!field_english_only\(a->focus\)\s*\?\s*e->he\s*:\s*e->ch\)",
                     handle), "התו הנשמר אינו תלוי בשפה ובשדה"
    append = _code(_body(main, "field_append"))
    assert "utf8_append(" in append, "field_append שומר בייט אחד לתו"
    assert '#include "utf8.h"' in main
    # המתקין: כל שדותיו אנגלית, והוא לא קורא את העמודה העברית
    assert "->he" not in _code(_src("install.c")) and ".he" not in _code(_src("install.c"))


def test_status_bar_shows_the_language_only_on_a_text_field():
    lang = _code(_body(_src("main.c"), "app_typing_lang"))
    assert "field_visible(a, a->focus)" in lang and "LANG_NONE" in lang
    assert "field_english_only(a->focus)" in lang and "LANG_EN_ONLY" in lang
    assert "int app_typing_lang(const App *a);" in _src("ui.h")
    status = _code(_body(_src("screens.c"), "draw_status"))
    assert "draw_lang_chip(" in status
    chip = _src("screens.c")
    chip_body = _body(chip, "draw_lang_chip")
    assert "HIT_LANG" in chip_body and "אנגלית בלבד" in chip_body and '"עב"' in chip_body
    assert "Alt+Shift" in _body(chip, "status_keys")


def test_makefile_lists_the_utf8_header():
    make = (REPO / "native-gui" / "Makefile").read_text(encoding="utf-8")
    hdr = re.search(r"^GUI_HDR\s*=(.*)$", make, re.M)
    assert hdr and "src/utf8.h" in hdr.group(1)


# --- 4. דרייבר C: key_event ו-utf8 כפי שהם מתקמפלים --------------------------

CC = shutil.which("cc") or shutil.which("gcc")
NATIVE_CC = requires_native(("cc", CC), posix=True, why="input.c/utf8.h נבנים על לינוקס בלבד")

DRIVER = r'''
#include "%(input_c)s"
#include "%(utf8_h)s"
#include <stdio.h>

static void drain(Input *in) {
    Event e;
    while (input_next(in, &e)) {
        if (e.type == UIEV_KEY && e.key == KEYSYM_LAYOUT) printf("LAYOUT\n");
        else if (e.type == UIEV_CHAR) printf("CHAR %%d %%d\n", e.ch, e.he);
        else if (e.type == UIEV_KEY) printf("KEY %%d\n", e.key);
    }
}

int main(void) {
    Input *in = calloc(1, sizeof *in);
    Dev d = { 0 };
    /* Alt then Shift; Shift held repeats; both released; Shift then Alt */
    key_event(in, &d, KEY_LEFTALT, 1); key_event(in, &d, KEY_LEFTSHIFT, 1);
    key_event(in, &d, KEY_LEFTSHIFT, 2); key_event(in, &d, KEY_LEFTALT, 2);
    key_event(in, &d, KEY_LEFTSHIFT, 0); key_event(in, &d, KEY_LEFTALT, 0);
    key_event(in, &d, KEY_RIGHTSHIFT, 1); key_event(in, &d, KEY_RIGHTALT, 1);
    key_event(in, &d, KEY_RIGHTALT, 0); key_event(in, &d, KEY_RIGHTSHIFT, 0);
    printf("== chords\n"); drain(in);
    /* every KEYMAP key, plain and shifted */
    printf("== keys\n");
    for (size_t i = 0; i < sizeof KEYMAP / sizeof KEYMAP[0]; i++) {
        key_event(in, &d, KEYMAP[i].code, 1); key_event(in, &d, KEYMAP[i].code, 0);
        key_event(in, &d, KEY_LEFTSHIFT, 1);
        key_event(in, &d, KEYMAP[i].code, 1); key_event(in, &d, KEYMAP[i].code, 0);
        key_event(in, &d, KEY_LEFTSHIFT, 0);
        drain(in);
    }
    /* Shift alone, then Tab: still Shift-Tab, not a language switch */
    key_event(in, &d, KEY_LEFTSHIFT, 1); key_event(in, &d, KEY_TAB, 1); key_event(in, &d, KEY_LEFTSHIFT, 0);
    printf("== tab\n"); drain(in);
    /* utf8: boundaries, then whole-or-nothing appends */
    int cps[] = { 0x41, 0x7F, 0x80, 0x5D0, 0x7FF, 0x800, 0xD800, 0xFFFF, 0x10000, 0x1F, -1 };
    printf("== utf8\n");
    for (size_t i = 0; i < sizeof cps / sizeof cps[0]; i++) {
        char u[3]; size_t k = utf8_encode(cps[i], u);
        printf("U %%d", (int)k);
        for (size_t j = 0; j < k; j++) printf(" %%02x", (unsigned char)u[j]);
        printf("\n");
    }
    char four[4] = "ab", five[5] = "ab";
    utf8_append(four, sizeof four, 0x5D0); utf8_append(five, sizeof five, 0x5D0);
    printf("A %%zu %%zu\n", strlen(four), strlen(five));
    free(in);
    return 0;
}
'''


def _run_driver(tmp_path: Path) -> list[str]:
    source = tmp_path / "hebrew_drv.c"
    source.write_text(DRIVER % {"input_c": (SRC / "input.c").as_posix(),
                                "utf8_h": (SRC / "utf8.h").as_posix()}, encoding="utf-8")
    binary = tmp_path / "hebrew_drv"
    build = subprocess.run([CC, "-O2", "-Wall", "-Wextra", "-std=c11", "-D_GNU_SOURCE",
                            "-o", str(binary), str(source)],
                           capture_output=True, text=True, timeout=120, stdin=subprocess.DEVNULL)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stderr
    return run.stdout.splitlines()


@NATIVE_CC
def test_key_event_switches_once_per_chord_and_types_si1452(tmp_path):
    sections: dict[str, list[str]] = {}
    current = None
    for line in _run_driver(tmp_path):
        if line.startswith("== "):
            current = line[3:]; sections[current] = []
        else:
            assert current, line
            sections[current].append(line)
    # Alt→Shift ו-Shift→Alt: שתי החלפות בדיוק; חזרה אוטומטית (2) אינה החלפה
    assert sections["chords"] == ["LAYOUT", "LAYOUT"], sections["chords"]
    keymap = _keymap()
    typed = [line.split() for line in sections["keys"]]
    assert len(typed) == 2 * len(keymap) and all(t[0] == "CHAR" for t in typed), typed
    for k, (code, (plain, shifted, he)) in enumerate(keymap.items()):
        _c, n_ch, n_he = typed[2 * k]
        _c, s_ch, s_he = typed[2 * k + 1]
        assert (chr(int(n_ch)), chr(int(n_he))) == (plain, he or plain), code
        assert (chr(int(s_ch)), chr(int(s_he))) == (shifted, shifted), code
    # Shift לבד ואז Tab: עדיין Shift-Tab (KEYSYM_BACKTAB = 2), לא החלפת שפה
    assert sections["tab"] == ["KEY 2"], sections["tab"]
    assert sections["utf8"] == ["U 1 41", "U 0", "U 2 c2 80", "U 2 d7 90", "U 2 df bf", "U 3 e0 a0 80",
                                "U 0", "U 3 ef bf bf", "U 0", "U 0", "U 0",
                                # 4 בייטים: "ab"+א (2) לא נכנס שלם — ולכן בכלל לא; ב-5 נכנס
                                "A 2 4"], sections["utf8"]


# --- 5. רינדור: החיווי בשורת הסטטוס ---------------------------------------------

GUI_BUILD = requires_native(
    ("cc", CC),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def test_png_has_a_hebrew_typing_card():
    main = _src("main.c")
    assert '"pick-hebrew"' in main
    setup = _code(_body(main, "png_setup"))
    hebrew = setup[setup.index('"pick-hebrew"'):]
    hebrew = hebrew[:hebrew.index("else if")]
    assert "a->lang_he = 1" in hebrew and "a->focus = HIT_DESC" in hebrew
    assert re.search(r"a->focus = HIT_USER;\s*a->lang_he = 0;", setup), "כל כרטיס מתחיל ב-EN"


@GUI_BUILD
def test_status_bar_draws_the_hebrew_chip_only_on_the_hebrew_card(tmp_path):
    """pick (בלי שדה בפוקוס) מול pick-hebrew (התיאור בפוקוס, עב): הצ'יפ המלא
    בצבע הפעולה נמצא בשורת הסטטוס של השני בלבד."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    for theme in ("light", "dark"):
        action = _token("--clr-action", theme)
        counts = {}
        for card in ("pick", "pick-hebrew"):
            w, h, ch, px = _png_rgb(tmp_path / f"card-{card}-{theme}.png")
            bar = round(36 * 1.0)                 # ui_scale(800) == 1
            rows = px[(h - bar) * w * ch:]
            counts[card] = _count_color(rows, ch, action, tol=6)
        assert counts["pick-hebrew"] - counts["pick"] > 200, (theme, counts)
