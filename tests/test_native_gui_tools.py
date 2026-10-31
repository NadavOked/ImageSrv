"""‏#649 תחום 1 — מסך "כלים" ב-GUI הנייטיבי, נבדק על המקור (אין מהדר בווינדוס).

מה שנאכף כאן בלי לקמפל: הכפתור "כלים" חי **רק** בתפריט מחשב הבנייה
(‏`screen_menu` ב-`screens.c`) ולא במסכי הקלונר/הכיתה; שלושת המסכים
(רשימה / אישור / פלט) קיימים עם המחרוזות שהמפעיל רואה; ‏rc 2 מוצג
כ"לא הצלחנו לבדוק" ו-rc 3 כ"האישור לא תואם"; המצב הריק אומר "אין כלים
ארוזים"; והמסך צבוע **רק** בטוקנים הקיימים — אין `HEX(` בקובץ (כדי
ש-`test_native_gui_theme` יישאר מקור האמת היחיד לצבעים). ‏`--png`
מקבל שלושה כרטיסים חדשים, ו-`want[]` גדל איתם (‏#715 לימד).

הקומפילציה ו-`make png` — אצל המתאם במעבדה.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "native-gui" / "src"
TOOLS_C = (SRC / "screens_tools.c").read_text(encoding="utf-8")
SCREENS_C = (SRC / "screens.c").read_text(encoding="utf-8")
MAIN_C = (SRC / "main.c").read_text(encoding="utf-8")
UI_H = (SRC / "ui.h").read_text(encoding="utf-8")
MAKEFILE = (SRC.parent / "Makefile").read_text(encoding="utf-8")
BRIDGE = (SRC.parents[1] / "agent" / "lib" / "gui" / "guibridge.sh").read_text(encoding="utf-8")


def function_body(text: str, name: str) -> str:
    m = re.search(r"\n(?:static )?\w[\w \*]*\b" + re.escape(name) + r"\([^)]*\)\s*\{", text)
    assert m, f"{name} לא נמצאה"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


# --- הכפתור: בתפריט הבנייה בלבד -------------------------------------------------


def test_the_tools_button_lives_in_the_build_menu_only():
    assert "HIT_TOOLS" in function_body(SCREENS_C, "screen_menu")
    for other in ("screens_cloner.c", "screens_class.c", "screens_rounds.c", "screens_room.c",
                  "screens_capture.c", "screens_restore.c"):
        assert "HIT_TOOLS" not in (SRC / other).read_text(encoding="utf-8"), other
    # ואינו אחת מבחירות התפריט: לא בטבלת MENU, כפתור משני בראש הכרטיס.
    menu_table = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};", SCREENS_C, flags=re.S).group(1)
    assert "HIT_TOOLS" not in menu_table
    assert "BTN_PLAIN, HIT_TOOLS" in SCREENS_C


def test_the_button_is_registered_before_the_body_clip():
    body = function_body(SCREENS_C, "screen_menu")
    assert body.index("HIT_TOOLS") < body.index("body_clip_begin(a, cr, body)")


def test_the_toolbox_routes_to_its_own_screen():
    assert "case MODE_TOOLS:   a->screen = SCREEN_TOOLS" in SCREENS_C
    assert "case SCREEN_TOOLS:    screen_tools(a, cr, W, H, head_h)" in SCREENS_C
    assert "SCREEN_TOOLS" in UI_H and "MODE_TOOLS" in UI_H
    assert "src/screens_tools.c" in MAKEFILE


# --- שלושת המסכים והמחרוזות שהמפעיל רואה ------------------------------------------


def test_the_list_groups_by_domain_with_hebrew_headings():
    for domain, heading in (("disk", "דיסקים"), ("net", "רשת"), ("windows", "Windows"),
                            ("boot", "אתחול"), ("hw", "חומרה")):
        assert f'"{domain}"' in TOOLS_C and f'"{heading}"' in TOOLS_C, (domain, heading)


def test_the_risk_tags_carry_the_three_words():
    for word in ("קריאה בלבד", "משנה דיסק", "הרסני"):
        assert word in TOOLS_C, word
    # ומילה לא מוכרת נקראת כהרסנית, כמו ב-tools.sh -- לא כקריאה בלבד.
    body = function_body(TOOLS_C, "tools_risk_level")
    assert "return 2;" in body.split("rw")[-1]


def test_rc_2_and_rc_3_have_their_own_words_and_2_is_not_green():
    body = function_body(TOOLS_C, "rc_word")
    line2 = next(l for l in body.splitlines() if "case 2:" in l)
    assert "לא הצלחנו לבדוק" in line2 and "warning" in line2 and "success" not in line2
    line3 = next(l for l in body.splitlines() if "case 3:" in l)
    assert "האישור לא תואם" in line3 and "danger" in line3


def test_the_empty_state_and_the_loading_state_are_two_texts():
    assert "לא נבחרו כלים בשרת" in TOOLS_C   # #1050: the cause is the selection, not the initrd
    assert "טוען את רשימת הכלים" in TOOLS_C


def test_the_confirm_view_asks_no_typed_name_rw_and_destroy_confirm_in_a_dialog():
    """#1270 (owner, 10/10): no typed-word confirmation -- the view keeps the
    warning, the disk and the argument; "הרץ…" opens the confirm dialog."""
    body = function_body(TOOLS_C, "tools_confirm_view")
    assert "הקלידו את שם המחשב" not in body and "HIT_TOOL_CONFIRM" not in body
    assert "HIT_TOOL_DISK_BASE" in body and "HIT_TOOL_ARG" in body
    go = function_body(MAIN_C, "tool_go")
    assert "if (tools_risk_level(a->tools[a->tool_sel].risk)) a->confirm = CONFIRM_TOOL;" in go


def test_the_output_view_spins_while_running_and_offers_run_again():
    body = function_body(TOOLS_C, "tools_output_view")
    assert "spinner_draw" in body and "רץ…" in body
    assert "הרץ שוב" in body and "HIT_TOOL_AGAIN" in body
    assert "למטה" in body and "למעלה" in body


def test_run_again_never_replays_a_risky_run():
    """‏rw/destroy (או כלי עם ארגומנט) חוזר למסך האישור — ומשם לחלון האישור
    (#1270); רק ro בלי ארגומנט רץ שוב מיד."""
    body = function_body(MAIN_C, "handle")
    again = body[body.index("case HIT_TOOL_AGAIN:"):body.index("case HIT_TOOL_DOWN:")]
    assert "a->tools_view = TOOLS_CONFIRM;" in again and "else tool_run(a);" in again


def test_the_bridge_still_gets_the_machine_name_it_checks():
    """#1270: nobody types the name; tools.sh still checks it, so tool_run
    sends the name tools= gave, and refuses when it is not known."""
    body = function_body(MAIN_C, "tool_run")
    assert 'snprintf(confirm, sizeof confirm, "%s", tools_risk_level(t->risk) ? a->tools_machine : "");' in body
    assert 'tool-run|%s|%s|%s' in body
    ready = function_body(MAIN_C, "tool_ready")
    assert "!a->tools_machine[0]" in ready


# --- טוקנים בלבד, וטבלאות --png ---------------------------------------------------


def test_the_tools_screen_defines_no_colour_of_its_own():
    assert "HEX(" not in TOOLS_C
    assert not re.search(r"cairo_set_source_rgb\s*\(", TOOLS_C)
    assert not re.search(r"\(Rgb\)\s*\{", TOOLS_C)


def test_png_cards_include_the_three_tools_views():
    cards = re.search(r"PNG_CARDS\[\]\s*=\s*\{(.*?)\};", MAIN_C, flags=re.S).group(1)
    for card in ('"tools"', '"tools-confirm"', '"tools-output"'):
        assert card in cards, card
    want = re.search(r"static const Screen want\[\]\s*=\s*\{(.*?)\};", MAIN_C, flags=re.S).group(1)
    assert want.count("SCREEN_TOOLS") == 3


# --- הגשר: שתי שורות, אחרי gui_role -------------------------------------------------


def test_the_bridge_dispatches_both_tokens_behind_the_session_gate():
    start = BRIDGE.index("gui_dispatch() {")
    body = BRIDGE[start:BRIDGE.index("\n}\n", start)]
    gate = body.index("gui_role ||")
    assert body.index("tool-list) tools_gui_list") > gate
    assert body.index('"tool-run|"*) tools_gui_run "$token"') > gate
    assert '. "$LIB_DIR/tools.sh"' in BRIDGE


# --- v1 בלי ארגז הכלים (הכרעת נדב 19/09; v1.1 = הכלים) ----------------------------
#
# הגואי יודע על יכולות השרת דרך קובץ המצב: hello/‏`/state` נושאים `tools`
# (‏`server/core/capabilities.py`), ‏`tools_on` ב-`buildmenuitems.sh` קורא אותו
# (חסר = כבוי), ‏`guistate.sh` כותב `menu_tools=0|1` **תמיד**, ו-`screens.c`
# מצייר את כפתור "כלים" רק על 1 — אותו ערוץ בדיוק כמו `menu_class` (#1081).

import json
import os
import shutil
import subprocess

import pytest

from native import requires_native

STATE_C = (SRC / "state.c").read_text(encoding="utf-8")
AGENT = SRC.parents[1] / "agent"
ITEMS = (AGENT / "lib" / "gui" / "buildmenuitems.sh").read_text(encoding="utf-8")
GUISTATE = (AGENT / "lib" / "gui" / "guistate.sh").read_text(encoding="utf-8")


def test_the_tools_button_is_drawn_only_when_menu_tools_is_on():
    """הציור של HIT_TOOLS ב-`screen_menu` יושב בתוך `if (a->st.menu_tools)`,
    ‏`state.c` מפרסר את הרשומה (היעדר = 0 מ-memset), והדגימה של `--png`
    מדליקה אותה כדי ששלושת כרטיסי הכלים יישארו ברי-רינדור."""
    body = function_body(SCREENS_C, "screen_menu")
    gate = body.index("if (a->st.menu_tools) {")
    assert gate < body.index("BTN_PLAIN, HIT_TOOLS") < body.index("body_clip_begin(a, cr, body)")
    assert 'KEY("menu_tools")' in STATE_C and "s->menu_tools = num(val, 0) != 0" in STATE_C
    assert "int menu_tools;" in UI_H
    assert "s->menu_tools = 1;" in function_body(MAIN_C, "sample_state")


def test_the_agent_gate_reads_tools_from_hello_then_state_and_guistate_writes_it():
    assert "tools_on() {" in ITEMS
    body = ITEMS[ITEMS.index("tools_on() {"):]
    body = body[:body.index("\n}\n")]
    assert '".tools"' in body and 'station.json' in body
    assert "menu_tools=%s" in GUISTATE and "if tools_on; then _mt=1; else _mt=0; fi" in GUISTATE


STATION = {"known": True, "role": "build", "disks": [], "task": None, "allowed_images": []}


def _state_records(tmp_path: Path, hello: dict | None, station: dict = STATION) -> list[str]:
    from test_cloner_gui import posix, sh
    run = tmp_path / "run"; run.mkdir(parents=True)
    gui = tmp_path / "gui"; gui.mkdir(parents=True)
    (run / "station.json").write_text(json.dumps(station), newline="\n")
    if hello is not None:
        (run / "response.json").write_text(json.dumps(hello), newline="\n")
    (gui / "mode").write_text("menu\n", newline="\n")
    a = posix(AGENT)
    script = (
        f'export RUN_DIR={posix(run)!r} GUI_DIR={posix(gui)!r} '
        f'SERVER=http://127.0.0.1:9 MAC=aa:bb:cc:dd:ee:ff IMAGESRV_TEST=1; '
        f'. {a}/lib/core/common.sh; . {a}/lib/core/jsonq.sh; . {a}/lib/gui/buildmenuitems.sh; . {a}/lib/gui/buildmenu.sh; '
        f'. {a}/lib/gui/guistate.sh; . {a}/lib/gui/askgui.sh; '
        'disk_slot_records() { :; }; '   # #1495: disk numbering is test_drawer_map_gui_1495's
        + f'http_get() {{ cat {posix(run / "station.json")!r}; }}; gui_state'
    )
    out = sh(script)
    assert out.returncode == 0, out.stderr
    return (gui / "state").read_text().splitlines()


def _bash():
    from test_cloner_gui import BASH
    return ("bash", BASH)


@requires_native(_bash(), "jq", why="gui_state בונה את המצב ב-jq")
def test_gui_state_writes_menu_tools_off_by_default_and_on_only_when_the_server_says_so(tmp_path):
    """‏v1: hello בלי `tools` (ברירת המחדל) → `menu_tools=0` **נכתב**; גם
    בלי קובץ hello. ‏v1.1: `tools: true` ב-hello, או ב-`/state` כשה-hello
    שותק → `menu_tools=1`. **בקרה שלילית:** בלי `tools_on`/הרשומה — היא
    חסרה מהמצב, והטסט נופל."""
    absent = _state_records(tmp_path / "absent", {"role": "build"})
    assert "menu_tools=0" in absent, absent
    no_file = _state_records(tmp_path / "nofile", None)
    assert "menu_tools=0" in no_file, no_file
    off = _state_records(tmp_path / "off", {"role": "build", "tools": False})
    assert "menu_tools=0" in off and "menu_tools=1" not in off, off
    on = _state_records(tmp_path / "on", {"role": "build", "tools": True})
    assert "menu_tools=1" in on, on
    via_state = _state_records(tmp_path / "state", {"role": "build"}, station={**STATION, "tools": True})
    assert "menu_tools=1" in via_state, via_state


def _menu_png(tmp_path: Path, state_text: str) -> tuple[int, int, int, bytes]:
    """מרנדר את כל הכרטיסים ממצב נתון ומחזיר את פיקסלי כרטיס התפריט."""
    from test_cloner_gui import GUI, _build_gui, _png_rgb, posix
    tmp_path.mkdir(parents=True, exist_ok=True)
    binary = _build_gui(tmp_path)
    state = tmp_path / "state.txt"
    state.write_text(state_text + "message=x|y\n", newline="\n")
    env2 = dict(os.environ)
    fonts_conf = GUI / "fonts.conf"
    if fonts_conf.exists():
        env2["FONTCONFIG_FILE"] = str(fonts_conf)
    run = subprocess.run(
        [str(binary), "--png", posix(tmp_path / "card"), "--size", "1000x760",
         "--mac", "3C:52:82:A1:00:21", "--ip", "10.10.10.31", "--state", posix(state)],
        capture_output=True, text=True, timeout=120, env=env2, stdin=subprocess.DEVNULL,
    )
    assert run.returncode == 0, run.stderr
    return _png_rgb(tmp_path / "card-menu-light.png")


def _differing_pixels(a, b) -> int:
    assert a[:3] == b[:3], (a[:3], b[:3])
    ch, pa, pb = a[2], a[3], b[3]
    return sum(1 for i in range(0, len(pa), ch) if pa[i:i + 3] != pb[i:i + 3])


def _cc():
    from test_cloner_gui import _pkgconfig
    return (("cc", shutil.which("cc") or shutil.which("gcc")),
            ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")))


@requires_native(*_cc(), why="native-gui נבנה על המעבדה בלבד")
def test_the_tools_button_is_not_drawn_without_menu_tools(tmp_path):
    """‏`menu_tools=1` מצייר את כפתור "כלים" בראש כרטיס התפריט; ‏0 לא, ורשומה
    חסרה שווה ל-0 **פיקסל בפיקסל**. נמדד כהפרש בין שני רינדורים — לא בדיו
    הכהה לבדו: הכיתוב קטן ורוב פיקסליו גווני-ביניים של אנטי-אליאסינג
    (נמדד במעבדה 19/09: 2,166 פיקסלים שונים בתיבה של 57×38, ורק 13 כהים)."""
    shown = _menu_png(tmp_path / "on", "menu_tools=1\n")
    hidden = _menu_png(tmp_path / "off", "menu_tools=0\n")
    absent = _menu_png(tmp_path / "absent", "")
    assert _differing_pixels(shown, hidden) > 500
    assert _differing_pixels(absent, hidden) == 0
