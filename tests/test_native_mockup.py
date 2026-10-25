"""The approved native mockup (docs/design/apple-mockups/native.html, 10/10).

Nadav approved the "מוצע" column of the five screens on 10/10; this file
pins what was built from it, screen by screen. The audit behind it, with
the findings and file:line, is docs/design/native-ui-audit.md.

Two kinds of test, as everywhere in native-gui:

* source contracts -- run everywhere, also on the Windows workstation that
  has no C compiler. They fail on the pre-mockup source.
* ``--png`` renders -- ``requires_native``: built and run on the Linux
  Testrunner (cc + pango/cairo), a skip here and a failure there when the
  tools are missing (``IMAGECTL_REQUIRE_NATIVE=1``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from native import requires_native
from test_cloner_gui import (_build_gui, _count_color, _pkgconfig, _png, _png_rgb,
                             _render_all, _token)

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "native-gui" / "src"

GUI_BUILD = requires_native(
    ("cc", shutil.which("cc") or shutil.which("gcc")),
    ("pango/cairo/libdrm", _pkgconfig("pangocairo", "cairo", "libdrm")),
    why="native-gui נבנה על המעבדה בלבד",
)


def _src(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def code(text: str) -> str:
    """<text> without its C comments -- a comment may quote the old code."""
    return re.sub(r"/\*.*?\*/", "", text, flags=re.S)


def body(text: str, name: str) -> str:
    """The braces of the definition of <name> (not a call site)."""
    m = re.search(r"\n(?:static )?[\w][\w \*]*\b" + re.escape(name) + r"\([^;{)]*\)\s*\{", text)
    assert m, f"{name}() is not defined here"
    start = m.end(); depth = 1; i = start
    while depth and i < len(text):
        depth += (text[i] == "{") - (text[i] == "}")
        i += 1
    return text[start:i]


# --- 1. the build machine's menu ------------------------------------------------


def test_menu_cards_are_as_tall_as_their_content_two_to_a_row():
    """The cards were `ch = bottom - gy`: four 640-800px boxes, 70% empty."""
    menu = body(_src("screens.c"), "screen_menu")
    assert "ch=bottom-gy" not in menu
    assert "cols=2" in menu and "row_h=dmax(row_h,cards[j].h)" in menu
    card = body(_src("screens.c"), "menu_card_make")
    assert "k.h=18+dmax(48,k.title.h+4+k.desc.h+8+k.flow.h)+18;" in card
    assert "if(k.h<132)k.h=132;" in card


def test_menu_draws_four_distinct_icons_from_the_mockup_svgs():
    """Capture and restore shared ICON_DISK, room and direct ICON_NETWORK."""
    s = _src("screens.c")
    menu = re.search(r"MENU\[MENU_N\]\s*=\s*\{(.*?)\n\};", s, re.S).group(1)
    icons = re.findall(r"HIT_(CAPTURE|ROOM|DIRECT|RESTORE),(MENU_ICON_\w+)", menu)
    assert dict(icons) == {"CAPTURE": "MENU_ICON_CAPTURE", "ROOM": "MENU_ICON_SERVER",
                           "DIRECT": "MENU_ICON_DIRECT", "RESTORE": "MENU_ICON_RESTORE"}
    assert "ICON_NETWORK" not in s and "ICON_NETWORK" not in _src("widgets.h")
    icon = body(_src("widgets.c"), "draw_menu_icon")
    # one path per SVG, by its distinctive coordinates
    assert "cairo_move_to(cr, 12, 11); cairo_line_to(cr, 12, 3);" in icon          # capture arrow
    assert "draw_rrect_path(cr, (Rect){ 8, 2, 8, 5 }, 1);" in icon                # the server
    assert "draw_rrect_path(cr, (Rect){ 15, 7, 7, 10 }, 1.5);" in icon            # direct: 2nd disk
    assert "cairo_arc_negative(cr, 12, 12, 9, M_PI, atan2(-6.7, -6.0));" in icon  # restore arc
    assert "cairo_set_line_width(cr, 1.6);" in icon


def test_menu_pills_come_from_the_state_not_from_a_string():
    """'השרת מחובר' and 'מחשבי שיכפול ערים' were literals (screens.c:227)."""
    s = _src("screens.c")
    assert '"השרת מחובר"' not in s and '"מחשבי שיכפול ערים"' not in s
    pill = body(s, "menu_server_pill")
    assert "s->hello_rc==200" in pill and "PILL_BAD" in pill and "טרם נבדק" in pill
    cloners = body(s, "menu_cloner_pill")
    assert "if(!s->nmachines)return;" in cloners          # absent is not "0 awake"
    assert "s->machines[i].awake" in cloners and "fresh_drawers" in cloners


def test_escape_on_the_menu_asks_before_it_signs_out():
    """Esc set signed_in=0 at once (main.c:691) -- a stray key dropped the session."""
    main = _src("main.c")
    handle = body(main, "handle")
    esc = handle[handle.index("if (e->key == KEYSYM_ESC) {"):]
    esc = esc[:esc.index("\n        }")]
    menu_line = next(x for x in esc.splitlines() if "a->screen == SCREEN_MENU" in x)
    assert "a->logout_confirming = 1" in menu_line and "signed_in = 0" not in menu_line
    modal = body(main, "logout_modal")
    assert "KEYSYM_ENTER) sign_out(a)" in modal and "KEYSYM_ESC) a->logout_confirming = 0" in modal
    assert handle.index("logout_modal(a, e)") < handle.index("switch (e->type)")
    assert "if(a->logout_confirming)draw_logout_confirm(a,cr,W,H);" in body(_src("screens.c"), "screen_menu")


def test_menu_keyboard_has_shift_tab_and_arrows():
    """Tab went forward only (main.c:698); KEYSYM_BACKTAB arrived and was dropped."""
    handle = body(_src("main.c"), "handle")
    menu = handle[handle.index("if (a->screen == SCREEN_MENU) {\n            int ids[5]"):]
    assert "KEYSYM_BACKTAB" in menu and "menu_arrow(a, e->key, n)" in menu
    assert "case KEY_LEFT:" in _src("input.c") and "KEYSYM_LEFT, KEYSYM_RIGHT" in _src("input.h")
    arrow = body(_src("main.c"), "menu_arrow")
    assert "KEYSYM_LEFT && f % 2 == 0" in arrow        # RTL: left is the next card


def test_menu_status_bar_counts_the_cards_it_shows():
    """'1–4' was fixed -- a deploy user has three cards, classes make five."""
    keys = body(_src("screens.c"), "status_keys")
    assert '"1–4  בחירה' not in keys
    assert "(a->admin?4:3)+(a->st.menu_class?1:0)" in keys


@GUI_BUILD
def test_menu_leaves_the_bottom_of_the_body_empty(tmp_path):
    """1280x800, sample data (admin, five cards in three rows): the band just
    above the "התנתק" row is page background. The old full-height cards
    filled it with their surface colour."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-menu-light.png")
    bg = _token("--clr-bg")
    band = [px[(y * w + x) * ch:(y * w + x) * ch + 3] for y in range(650, 680) for x in range(200, w - 200, 7)]
    off = sum(1 for p in band if any(abs(p[k] - bg[k]) > 3 for k in range(3)))
    assert off == 0, f"{off} pixels of the band above the logout row are not background"


def _icon_boxes(path: Path) -> list[bytes]:
    """The 48x48 action-soft icon boxes of the menu cards, right to left and
    top to bottom, as raw pixel blocks."""
    w, h, ch, px = _png_rgb(path)
    soft = _token("--clr-action-soft")
    hit = bytearray(w * h)
    for i in range(w * h):
        p = px[i * ch:i * ch + 3]
        hit[i] = all(abs(p[k] - soft[k]) <= 2 for k in range(3))
    seen = bytearray(w * h); boxes = []
    for start in range(w * h):
        if not hit[start] or seen[start]:
            continue
        stack = [start]; seen[start] = 1; x0 = y0 = 10 ** 9; x1 = y1 = -1
        while stack:
            i = stack.pop(); x, y = i % w, i // w
            x0, x1, y0, y1 = min(x0, x), max(x1, x), min(y0, y), max(y1, y)
            for j in (i - 1, i + 1, i - w, i + w):
                if 0 <= j < w * h and hit[j] and not seen[j] and abs(j % w - x) <= 1:
                    seen[j] = 1; stack.append(j)
        if 40 <= x1 - x0 <= 56 and 40 <= y1 - y0 <= 56:      # an icon box, not a pill
            boxes.append((y0, -x0, x0, y0, x1, y1))
    boxes.sort()
    out = []
    for _, _, x0, y0, x1, y1 in boxes:
        out.append(b"".join(bytes(px[(y * w + x0) * ch:(y * w + x1 + 1) * ch]) for y in range(y0, y1 + 1)))
    return out


@GUI_BUILD
def test_the_four_menu_icons_are_four_different_drawings(tmp_path):
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    boxes = _icon_boxes(tmp_path / "card-menu-light.png")
    assert len(boxes) >= 4, f"found {len(boxes)} icon boxes"
    assert len(set(boxes[:4])) == 4, "two of capture/room/direct/restore draw the same icon"


@GUI_BUILD
def test_the_server_pill_turns_red_when_the_last_hello_failed(tmp_path):
    """Same state twice, hello_rc 200 vs 503: the 503 menu has a red pill the
    200 one does not (the restore card's 'הדיסק נמחק.' is red in both)."""
    binary = _build_gui(tmp_path)
    red = _token("--clr-danger-fg")
    counts = {}
    for rc in ("200", "503"):
        state = tmp_path / f"{rc}.txt"
        state.write_text(f"server_name=TA\nserver_addr=10.44.0.1\nhello_age=4\nhello_rc={rc}\n"
                         "message=x|y\n", newline="\n")
        _png(binary, tmp_path / rc, state, "1280x800")
        _, _, ch, px = _png_rgb(tmp_path / f"{rc}-menu-light.png")
        counts[rc] = _count_color(px, ch, red)
    assert counts["503"] - counts["200"] > 25, counts


@GUI_BUILD
def test_the_logout_card_draws_the_question(tmp_path):
    """--png card "logout": the menu under a scrim with a two-button dialog."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    a = (tmp_path / "card-menu-light.png").read_bytes()
    b = (tmp_path / "card-logout-light.png").read_bytes()
    assert a != b


# --- 2. the capture progress screen ---------------------------------------------


def test_capture_progress_says_what_is_known_not_dashes():
    """With no total the screen drew "--" in 88px over "הושלם" while the
    capture was running (screens_capture.c:200-204, audit finding 7)."""
    prog = code(body(_src("screens_capture.c"), "screen_progress"))
    assert '"--"' not in prog
    # "הושלם" is the caption of a known percentage only
    assert 'if (s->pct >= 0) { snprintf(big, sizeof big, "%d%%", s->pct > 100 ? 100 : s->pct); cap_s = "הושלם"; }' in prog
    assert prog.count('"הושלם"') == 1
    # the unknown total: the bytes read, big, and the unknown said in words
    assert "fmt_bytes(rd, sizeof rd, (double)s->bytes);" in prog and '"נקראו"' in prog
    assert "הסך לא ידוע — מוצג מה שכבר נקרא" in prog
    assert '"טרם נקראו בייטים"' in prog


def status_line(screen: str) -> str:
    """What status_keys returns for <screen>: its own case, else default."""
    keys = code(body(_src("screens.c"), "status_keys"))
    at = keys.find(f"case {screen}:")
    if at < 0:
        at = keys.index("default:")
    return keys[at:].split("return", 1)[1].split(";")[0]


def test_capture_progress_status_bar_offers_no_dead_escape():
    said = status_line("SCREEN_PROGRESS")
    assert "Esc" not in said, said
    assert "אין פעולה במסך הזה" in said


# --- 3. the cloner, the room and the live round ---------------------------------


def test_cloner_draws_a_card_per_disk_it_found_not_three():
    """`for (int port = 1; port <= 3; port++)` and "שלושה דיסקים" -- cloner 1
    has two SATA ports (audit finding 5, S19)."""
    src = code(_src("screens_cloner.c"))
    assert "port <= 3" not in src and "שלושה דיסקים" not in src
    # #1495: one card per drawer record (or idle disk), ordered by the drawer of
    # the mapping, plus the disk a SMART question is about -- not ports 1..3.
    entries = body(src, "cloner_entries")
    assert "for (int i = 0; i < s->ndrawers; i++)" in entries and "for (int i = 0; i < s->nidisks; i++)" in entries
    assert "s->smart_pending" in entries and "out[i].port == s->smart_port" in entries
    cloner = body(src, "screen_cloner")
    assert "int n = cloner_entries(s, entries);" in cloner   # 17/10: a row per entry (lcbay.c)


def test_cloner_smart_answers_are_ordered_by_risk():
    """'דלג' was BTN_DANGER (red) and 'כתוב בכל זאת' BTN_PRIMARY -- the
    inviting colour on the risky choice. Now: replace, skip (plain), and
    write-anyway as a warning outline, laid from the inline end."""
    cloner = code(body(_src("screens_cloner.c"), "screen_cloner"))
    # no red and no inviting colour on a SMART answer. #2081 (mockup 03) adds the held
    # end's own "כבה את המחשב" as the screen's primary button -- that one only.
    styled = re.findall(r"(BTN_DANGER|BTN_PRIMARY),\s*(HIT_\w+)", cloner)
    assert styled == [("BTN_PRIMARY", "HIT_END_NOW")], styled
    draws = re.findall(r'key_button\(a, cr, x, by, ([^,]+(?:, [^,]+)?), "?[^,]*"?, (BTN_\w+), (HIT_SMART_\w+), 1\)', cloner)
    assert [d[2] for d in draws] == ["HIT_SMART_REPLACE", "HIT_SMART_SKIP", "HIT_SMART_RESCUE"], draws
    assert [d[1] for d in draws] == ["BTN_PLAIN", "BTN_PLAIN", "BTN_WARN"], draws
    assert "BTN_WARN" in _src("widgets.h") and "kind == BTN_WARN" in body(_src("widgets.c"), "draw_btn")


def test_cloner_and_standby_status_bars_list_only_working_keys():
    keys = code(body(_src("screens.c"), "status_keys"))
    cloner = keys[keys.index("case SCREEN_CLONER:"):keys.index("case SCREEN_RESTORE:")]
    assert "if(!a->st.smart_pending)" in cloner
    # no SMART question, no keys -- but Enter on a held end (#2081: its own "כבה את המחשב")
    assert 'a->st.end_kind!=END_NONE&&a->st.end_held?"Enter  כיבוי":"אין פעולה במסך הזה"' in cloner
    assert "דיסקים: 1 · 2 · 3" not in keys


def test_cloner_explains_a_wave_lost_to_server_restart():
    screens = _src("screens.c")
    assert "השרת אותחל באמצע הגל — המחשב יצטרף לבד לגל הבא" in screens


def test_live_round_draws_a_bar_per_reported_disk():
    """The live grid drew max(drawer_count, nroom_drawers) bars -- a slot with
    no disk got a bar with the machine's percentage on it."""
    # 17/10 ("גרסה ב׳"): the live round is a cage of rows per cloner (lcbay.c) --
    # a row per present drawer the round writes, sorted by its drawer number
    live = code(body(_src("screens_room.c"), "live_bays"))
    assert "m->drawer_count" not in live
    assert "live_target(&m->room_drawers[j])" in live and "room_drawer_after(out[k - 1], v)" in live
    assert "d->present" in code(body(_src("screens_room.c"), "live_target"))


def _white_runs_mode(path: Path, top: int, bottom: int) -> int:
    """The most common number of long (>=120px) surface-white runs per row in
    [top, bottom): the number of cards side by side."""
    w, h, ch, px = _png_rgb(path)
    white = _token("--clr-surface")
    counts = {}
    for y in range(top, bottom):
        runs = run = 0
        for x in range(w):
            i = (y * w + x) * ch
            if all(abs(px[i + k] - white[k]) <= 3 for k in range(3)):
                run += 1
            else:
                runs += run >= 120; run = 0
        runs += run >= 120
        if runs:
            counts[runs] = counts.get(runs, 0) + 1
    return max(counts, key=counts.get) if counts else 0


def _track_runs(path: Path) -> int:
    """The 8px progress tracks down one column, top to bottom -- one per
    target row (lcbay.c): the rows stack, they no longer sit side by side."""
    w, h, ch, px = _png_rgb(path)
    track = _theme_hex("track")
    # a column near the start of the bars (they fill from the right, RTL)
    x, runs, inside = int(w * 0.15), 0, False
    for y in range(48, h - 36):
        i = (y * w + x) * ch
        on = all(abs(px[i + k] - track[k]) <= 3 for k in range(3))
        runs += on and not inside
        inside = on
    return runs


@GUI_BUILD
def test_a_two_disk_cloner_shows_two_cards(tmp_path):
    binary = _build_gui(tmp_path)
    state = tmp_path / "two.txt"
    state.write_text("cloner=Office 2024\n"
                     "drawer=1|sda|waiting|0|0|\n"
                     "drawer=2|sdb|waiting|0|0|\n"
                     "message=x|y\n", newline="\n")
    _png(binary, tmp_path / "two", state, "1000x760")
    assert _track_runs(tmp_path / "two-cloner-light.png") == 2      # two rows, not three


@GUI_BUILD
def test_the_smart_bar_has_no_red_and_puts_write_anyway_at_the_end(tmp_path):
    """Sample data: disk 2 has an open SMART question (verdict fail). In the
    bar above the status line: no danger red (skip is no longer red), and
    the warning-outlined "write anyway" is in the left half."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-cloner-light.png")
    warn, red = _token("--clr-warning"), _token("--clr-danger")
    y0, y1 = h - 36 - 74 + 5, h - 36 - 4
    warn_x, reds = [], 0
    for y in range(y0, y1):
        for x in range(w):
            i = (y * w + x) * ch
            p = px[i:i + 3]
            if all(abs(p[k] - warn[k]) <= 12 for k in range(3)):
                warn_x.append(x)
            if all(abs(p[k] - red[k]) <= 12 for k in range(3)):
                reds += 1
    assert reds == 0, f"{reds} red pixels in the SMART bar"
    assert len(warn_x) > 100, len(warn_x)
    assert max(warn_x) < w / 2, "the warning button is not at the inline end"


def _theme_hex(field: str) -> tuple[int, int, int]:
    light = re.search(r"const Theme THEME_LIGHT\s*=\s*\{(.*?)\};", _src("theme.c"), re.S).group(1)
    v = int(re.search(r"\." + field + r"\s*=\s*HEX\(0x([0-9A-Fa-f]{6})\)", light).group(1), 16)
    return (v >> 16) & 255, (v >> 8) & 255, v & 255


@GUI_BUILD
def test_the_live_round_draws_two_bars_for_two_reported_disks(tmp_path):
    """One machine, three configured slots, two disks reported. Down the
    middle column the track colour appears once per disk row: 2. The old grid
    drew a third bar for the empty slot (17/10: no big bar -- the hero says the %)."""
    binary = _build_gui(tmp_path)
    state = tmp_path / "live.txt"
    state.write_text("round=img|2|0|7|24|5|17|-1|-1|-1\n"
                     "machine=CLONE-01|3C:52:82:A1:00:21|1|1|2|writing|42|1|\n"
                     "machine_drawers=0|3\n"
                     "room_drawer=0|1|S1|Model A|256060514304|passed|1|1\n"
                     "room_drawer=0|2|S2|Model B|500107862016|passed|1|0\n"
                     "message=x|y\n", newline="\n")
    _png(binary, tmp_path / "live", state, "1000x760")
    runs = _track_runs(tmp_path / "live-room-live-light.png")
    assert runs == 2, f"{runs} track runs down the middle (want 2 disks)"


# --- 4. the installer -------------------------------------------------------------


def test_installer_is_one_centred_column_of_one_width():
    """1556 / 760 / 720 / 650 wide, step by step (audit finding 9)."""
    frame = _src("screens_install.c")
    assert "#define INSTALL_COL_W 760.0" in frame
    assert "col=fmin(INSTALL_COL_W,cw-64),cx=(cw-col)/2" in frame
    assert "{cx,105,col,H-185}" in frame
    setup, forms = _src("screens_install_setup.c"), _src("screens_install_forms.c")
    assert "fmin(720,l.body.w)" not in setup and "fmin(650,l.body.w)" not in forms


def test_installer_progress_bar_fills_from_the_right():
    """fill.x = bar.x filled left to right on an RTL page (audit finding 8)."""
    final = _src("screens_install_final.c")
    assert "fill.x=bar.x+bar.w-fill.w;" in final


def test_installer_failure_is_said_in_hebrew_with_the_code_below():
    final = _src("screens_install_final.c")
    he = body(final, "failure_he")
    assert '{"packages","התקנת חבילות ImageSrv נכשלה."}' in he   # #1302
    assert "(unsigned char)e[0]>=0x80" in he                # a Hebrew message stays as it is
    failed = body(final, "failed")
    assert "install_text_r(cr,t,s->error[0]?s->error" not in failed   # the raw code was the headline
    assert "if(raw)install_text_ltr(cr,s->error,11,400," in failed


def test_installer_footer_lists_the_working_keys_on_every_step():
    frame = _src("screens_install.c")
    keys = body(frame, "install_keys")
    for view in ("INSTALL_DISK", "INSTALL_ROLE", "INSTALL_NETWORK", "INSTALL_HOSTNAME",
                 "INSTALL_SUMMARY", "INSTALL_PROGRESS", "INSTALL_FAILED"):
        assert f"case {view}:" in keys, view
    assert "רווח  אני מבין" in keys                  # install.c: Space ticks it on the disk step
    assert "install_keys_draw(a,cr,l);}" in frame     # after every step's own drawing


def test_installer_future_steps_are_outlines_not_faint_discs():
    frame = _src("screens_install.c")
    assert "else{cairo_arc(cr,l.rail.x+l.rail.w-45,y,14,0,2*M_PI);draw_set(cr,dim);cairo_set_line_width(cr,2);cairo_stroke(cr);}" in frame


def _installer_pngs(binary: Path, tmp_path: Path, size: str = "1280x800") -> None:
    import os
    import subprocess
    env = dict(os.environ)
    fonts = REPO / "native-gui" / "fonts.conf"
    if fonts.exists():
        env["FONTCONFIG_FILE"] = str(fonts)
    proc = subprocess.run([str(binary), "--screen", "install", "--demo", "--png",
                           str(tmp_path / "inst"), "--size", size], capture_output=True,
                          text=True, timeout=120, env=env, stdin=subprocess.DEVNULL)
    assert proc.returncode == 0, proc.stderr


@GUI_BUILD
def test_installer_progress_fill_is_at_the_right_of_the_bar(tmp_path):
    """Demo: 52%. 1280x800: the column is x 135..895, the bar y 105..113.
    The left 40% of the bar is track; the right 40% is the action blue."""
    binary = _build_gui(tmp_path)
    _installer_pngs(binary, tmp_path)
    w, h, ch, px = _png_rgb(tmp_path / "inst-install-5c-progress-light.png")
    blue = _token("--clr-action")
    def blues(x0, x1):
        return sum(1 for x in range(x0, x1)
                   if all(abs(px[(109 * w + x) * ch + k] - blue[k]) <= 6 for k in range(3)))
    assert blues(140, 135 + 304) == 0, "the fill starts at the left"
    assert blues(135 + 456, 890) > 200, "no fill at the right"


@GUI_BUILD
def test_installer_footer_draws_the_key_row(tmp_path):
    """1280x800, disk step: the key row ends at x 768 (clear of "הבא" at
    880); the old "שלב 1 מתוך 7" was at x 30..170 and left 400..768 blank."""
    binary = _build_gui(tmp_path)
    _installer_pngs(binary, tmp_path)
    w, h, ch, px = _png_rgb(tmp_path / "inst-install-0-disk-light.png")
    colors = {bytes(px[(y * w + x) * ch:(y * w + x) * ch + 3])
              for y in range(h - 60, h - 4) for x in range(400, 768)}
    assert len(colors) > 4, "no key row in the footer"


# --- 5. across every screen ---------------------------------------------------------


def test_a_click_draws_the_button_pressed_for_a_moment():
    """draw_btn knew hover only (widgets.c:36-57): no feedback on the frame
    the click lands (audit finding 2)."""
    btn = code(body(_src("widgets.c"), "draw_btn"))
    assert "int pressed = kind != BTN_OFF && id && a->pressed_id == id;" in btn
    assert "if (pressed) {" in btn and "+ (pressed ? 1 : 0)" in btn
    main = code(_src("main.c"))
    loop = main[main.index("while (!stop_now && !done) {"):]
    assert "if (a.pressed_id && now_s() >= a.pressed_until) { a.pressed_id = 0; scene_dirty = 1; }" in loop
    assert "a.pressed_id = app_hit(&a, e.x, e.y);" in loop
    assert "int pressed_id;" in _src("ui.h") and "double pressed_until;" in _src("ui.h")


def test_shift_tab_walks_the_fields_backwards():
    handle = code(body(_src("main.c"), "handle"))
    assert "(e->key == KEYSYM_TAB || e->key == KEYSYM_BACKTAB) && n" in handle
    assert "ids[k <= 0 ? n - 1 : k - 1]" in handle


def test_one_destructive_pattern_red_and_only_after_the_confirmation():
    """The live round's "עצור סבב" was a filled red button, clickable before
    anything was asked. Since #1270 (owner, 10/10: no typed word anywhere)
    the red action lives in a confirm dialog; the screen offers red words."""
    widgets = code(body(_src("widgets.c"), "draw_btn"))
    assert "if (kind != BTN_OFF) hit_add(a, b, id);" in widgets          # disabled = not clickable
    footer = code(body(_src("screens_room.c"), "live_footer"))
    assert "BTN_GHOST_DANGER, HIT_ROOM_CLOSE);" in footer
    room = code(body(_src("screens_room.c"), "screen_room"))
    assert "BTN_DANGER" not in room
    tools = code(body(_src("screens_tools.c"), "tools_confirm_view"))
    assert "kinds[nb] = !ready ? BTN_OFF : level == 2 ? BTN_DANGER : BTN_PRIMARY;" in tools


def test_no_typed_word_confirmation_anywhere_1270():
    """#1270 (owner, 10/10): restore, the room stop and rw/destroy tools ask in
    a dialog -- [ביטול] and the red action; Enter confirms, Esc cancels. The
    token the bridge/server still checks is filled from the known value."""
    ui = code(_src("ui.h"))
    for gone in ("restore_confirm", "room_confirm", "class_confirm", "tool_confirm",
                 "HIT_RESTORE_CONFIRM", "HIT_ROOM_CONFIRM", "HIT_CLASS_CONFIRM", "HIT_TOOL_CONFIRM"):
        assert gone not in ui, gone
    for f in sorted(SRC.glob("*.c")):
        text = code(f.read_text(encoding="utf-8"))
        assert "typed_matches" not in text and "confirm_label" not in text, f.name
        assert "הקלידו את שם" not in text and "מקלידים את שם" not in text, f.name
    main = code(_src("main.c"))
    modal = body(main, "confirm_modal")
    assert "if (e->key == KEYSYM_ENTER) confirm_do(a);" in modal
    assert "else if (e->key == KEYSYM_ESC) a->confirm = CONFIRM_NONE;" in modal
    handle = body(main, "handle")
    assert handle.index("confirm_modal(a, e)") < handle.index("switch (e->type)")
    assert "case HIT_RESTORE_START: if (restore_ready(a)) a->confirm = CONFIRM_RESTORE;" in handle
    assert "case HIT_ROOM_CLOSE: if (a->st.has_round) a->confirm = CONFIRM_ROOM_STOP;" in handle
    do = body(main, "confirm_do")
    assert 'stop_round("room-close", a->st.round_image)' in do          # what the server compares
    assert 'emit_kv("confirm", a->st.machine_name);' in body(main, "start_restore")
    dialog = code(body(_src("screens.c"), "draw_confirm_dialog"))
    assert "BTN_DANGER,HIT_CONFIRM_OK" in dialog and "BTN_PLAIN,HIT_CONFIRM_CANCEL" in dialog
    assert "a->nhits=0;" in dialog                                     # modal
    restore = code(body(_src("screens_restore.c"), "screen_restore"))
    assert "draw_field(" not in restore                                 # the name field is gone


def test_every_status_bar_lists_only_keys_that_work():
    assert "Esc" not in status_line("SCREEN_MESSAGE")                    # default: a message has no way out
    assert "Enter" not in status_line("SCREEN_TOOLS")                    # handle() ignores Enter there
    keys = code(body(_src("screens.c"), "status_keys"))
    restore = keys[keys.index("case SCREEN_RESTORE:"):]
    # the build machine's restore has one field: Tab goes nowhere. The cloner's
    # pull screen (16/10) has the drawer row, and there Tab does move (lc_pull.c).
    build = restore[restore.index("}") + 1:].split(";")[0]
    assert "if(lc_pull_active(a)){" in restore and "Tab" not in build, build
    room = keys[keys.index("case SCREEN_ROOM:"):keys.index("case SCREEN_CLONER:")]
    setup = next(x for x in room.splitlines() if "if(!a->st.has_round)return" in x)
    assert "N  " not in setup                                            # N starts a wave; setup has none
    assert "Esc  עצור / חזרה" not in keys
    # and the class screen's Esc, which its status bar promised, now works
    handle = code(body(_src("main.c"), "handle"))
    assert 'a->screen == SCREEN_CLASS && !a->st.has_session) { go_menu(a); emit1("back");' in handle


def test_the_screen_scales_with_its_height():
    """Every size was fixed px (audit finding 4): a 1080p screen was a
    1024x768 layout with more empty space and 10px labels."""
    screens = code(_src("screens.c"))
    assert "double ui_scale(int H){double s=floor(H/800.0*4)/4;return s<1?1:s>1.4?1.4:s;}" in screens
    draw = body(screens, "app_draw")
    assert "cairo_scale(cr,s,s);" in draw and "app_draw_logical(a,cr,W/s,H/s);" in draw
    assert "x/=s;y/=s;" in body(screens, "app_hit")
    assert "a->ptr_x / s, a->ptr_y / s" in body(_src("widgets.c"), "hovered")
    assert "double s=ui_scale(h);a->scale=s;cairo_scale(cr,s,s);screen_install(a,cr,w/s,h/s);" in _src("screens_install.c")


def test_no_text_is_drawn_below_11px():
    text = code(_src("text.c"))
    assert "#define TEXT_MIN_PX 11.0" in text
    assert "if (px < TEXT_MIN_PX) px = TEXT_MIN_PX;" in body(text, "font_desc")
    small = re.compile(r"(?:text_make(?:_markup)?\(cr, ?FONT_(?:SANS|MONO), ?|rtl_block_make\(cr, ?|"
                       r"install_text_(?:r|ltr)\(cr,(?:t,)?[^,]*,)(?:[0-9]|10)(?:\.\d+)?\s*,")
    for f in sorted(SRC.glob("*.c")):
        hits = small.findall(code(f.read_text(encoding="utf-8")))
        assert not hits, (f.name, hits)


@GUI_BUILD
def test_the_live_rounds_stop_is_red_words_until_confirmed(tmp_path):
    """Sample data, 1280x800: the live round's footer (y 700..742) has red
    text, not a filled red button."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    w, h, ch, px = _png_rgb(tmp_path / "card-room-live-light.png")
    red = _token("--clr-danger-fg")
    n = sum(1 for y in range(700, 742) for x in range(w)
            if all(abs(px[(y * w + x) * ch + k] - red[k]) <= 12 for k in range(3)))
    assert 0 < n < 900, f"{n} red pixels in the footer (a filled button is ~3000)"


# --- 6. polish after the first real renders (gate-1010f, 1920x1080 cloner) ---------


def test_the_snapshot_age_is_said_in_whole_words():
    """The cloner subtitle showed "עודכן לפני 4 שנ" -- the abbreviation read
    as cut off, and its geresh jumped to the other end of the line."""
    age = code(body(_src("screens_rounds.c"), "pace_age"))
    assert "שנ'" not in age
    for words in ('"עודכן לפני %lld שניות"', '"עודכן לפני %lld דקות"', '"עודכן לפני %lld שעות"',
                  '"עודכן לפני שנייה"', '"עודכן לפני דקה"', '"עודכן לפני שעה"'):
        assert words in age, words


def test_an_image_name_is_one_isolated_run_in_an_rtl_line():
    """"Office 2024 — סטנדרט" first in the subtitle made auto-dir lay the whole
    line LTR: "'סטנדרט · 3 דיסקים · עודכן לפני 4 שנ — Office 2024"."""
    iso = code(body(_src("screens_rounds.c"), "bidi_isolate"))
    assert r'"\xE2\x81\xA8%s\xE2\x81\xA9"' in iso                      # FSI ... PDI
    assert r'#define RLM "\xE2\x80\x8F"' in _src("screens_rounds.h")
    title = code(body(_src("screens_cloner.c"), "screen_cloner"))
    assert 'snprintf(label, sizeof label, RLM "שיכפול במקביל · %s%s%s%s", image,' in title   # #1331: + the drivers phrase
    assert "bidi_isolate(s->cloner_image[0] ? s->cloner_image" in title
    live = code(body(_src("screens_room.c"), "screen_room"))
    assert "bidi_isolate(s->round_image, image, sizeof image);" in live
    # 17/10: "נכשל" for a failed wave, else "משדר" -- the image is still one isolated run;
    # #1331 (mockup v2): a wave that ended says neither "משדר" nor anything -- a failed one still says "נכשל"
    assert 'RLM "הפצה למחשבי שיכפול · %s%s · גל %d%s%s%s", wave_failed ? "נכשל: " : wave_over ? "" : "משדר: ",' in live


def test_tool_fields_are_cut_on_a_character_boundary():
    cp = code(body(_src("screens_tools.c"), "cp"))
    assert "snprintf" not in cp
    assert "((unsigned char)src[len] & 0xC0) == 0x80" in cp


def _guard_flagged(s: str) -> bool:
    """gcc -Wmisleading-indentation, for a line that starts with a guard: its
    one-statement body is followed by another statement on the same line."""
    def end(s, i, stop):
        depth = 0; q = None
        while i < len(s):
            c = s[i]
            if q:
                if c == "\\":
                    i += 2; continue
                if c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
                if depth == 0 and stop == ")" and c == ")":
                    return i + 1
            elif c == ";" and depth == 0 and stop == ";":
                return i + 1
            i += 1
        return -1
    m = re.match(r"(else\s+if|if|for|while)\s*\(", s)
    if m:
        j = end(s, s.index("(", m.start()), ")")
    elif re.match(r"else\b", s):
        j = 4
    else:
        return False
    rest = s[j:].lstrip()
    if not rest or rest[0] == "{":
        return False
    e = _body_end(rest, end)
    after = rest[e:].strip() if e > 0 else ""
    return bool(after) and not after.startswith(("else", "}", "//"))


def _body_end(rest: str, end) -> int:
    """Where the one statement of an unbraced guard body ends: a braced
    block, a nested guard with its own body (`for(..)for(..){..} return 0;`
    -- the shape gcc flagged in screens_install.c:78), or up to its ';'."""
    if rest[0] == "{":
        depth = 0; q = None; i = 0
        while i < len(rest):                     # braces inside "..." do not count
            c = rest[i]
            if q:
                if c == "\\":
                    i += 2; continue
                if c == q:
                    q = None
            elif c in "\"'":
                q = c
            elif c in "{}":
                depth += 1 if c == "{" else -1
                if depth == 0:
                    return i + 1
            i += 1
        return -1
    m = re.match(r"(if|for|while)\s*\(", rest)
    if m:
        j = end(rest, rest.index("("), ")")
        inner = rest[j:].lstrip()
        k = _body_end(inner, end) if inner else -1
        return -1 if k < 0 else len(rest) - len(inner) + k
    return end(rest, 0, ";")


def test_no_unbraced_guard_shares_its_line_with_the_next_statement():
    bad = [f"{f.name}:{n}" for f in sorted(SRC.glob("*.c"))
           for n, line in enumerate(code(f.read_text(encoding="utf-8")).splitlines(), 1)
           if _guard_flagged(line.strip())]
    assert not bad, bad


def test_the_login_fields_are_bordered_with_the_label_above():
    """The owner, 10/10: the native login as the console draws it -- label
    above, a bordered LTR input (draw_field), only "הצג" inside the password
    field at its right end in the action colour, the error only after a
    failed attempt. The underline fields (underlined_field) are gone."""
    screens = code(_src("screens.c"))
    assert "underlined_field" not in screens
    login = body(screens, "screen_login")
    assert 'y+=login_label(a,cr,x,y,fw,"שם משתמש");' in login
    assert "draw_field(a,cr,(Rect){x,y,fw,N_FIELD_H},a->user,0,NULL,HIT_USER);" in login
    assert "draw_field(a,cr,pf,a->pass,!a->show_pw,NULL,HIT_PASS);" in login
    assert login.index('"שם משתמש"') < login.index("HIT_USER") < login.index('"סיסמה"') < login.index("HIT_PASS")
    assert "Rect er={pf.x+pf.w-eye.w-24," in login and "t->action_fg);hit_add(a,er,HIT_EYE);" in login
    assert "if(a->error[0]){" in login                          # drawn only when set
    widgets = code(body(_src("widgets.c"), "draw_field"))
    assert "int ltr = (id == HIT_USER || id == HIT_PASS);" in widgets  # LTR, from the left


# --- 7. folder-first image pickers (owner, 10/10) --------------------------------------


def test_the_pickers_group_images_by_the_folder_the_agent_sends():
    """image= records already carry the folder (guistate.sh: id|name|folder,
    for restore from allowed_images and for the room from /images)."""
    rounds = code(_src("screens_rounds.c"))
    folders = body(rounds, "image_folders")
    assert "strcmp(s->images[first[k]].folder, s->images[i].folder)" in folders
    assert "count[k]++;" in folders
    assert "if (!strcmp(s->images[i].folder, folder)) idx[n++] = i;" in body(rounds, "folder_images")
    assert '"ללא תיקייה"' in body(rounds, "folder_label")


def test_restore_shows_folders_first_then_the_images_of_one():
    grid = code(body(_src("screens_restore.c"), "screen_restore"))
    assert "hit_add(a, box, in ? HIT_OPTION_BASE + items[i] : HIT_PICK_FOLDER_BASE + i);" in grid
    assert "HIT_PICK_FOLDERS_BACK" in grid and '"כל התיקיות"' in grid
    assert '"תיקיות › %s"' in grid                                    # the breadcrumb
    assert "for (int i = 0; i < shown; i++)" in grid and "shown = s->nimages" not in grid


def test_the_room_image_select_is_folder_first_too():
    popup = code(body(_src("widgets.c"), "draw_popup"))
    assert "id = HIT_PICK_FOLDER_BASE + i;" in popup
    assert "id = HIT_PICK_FOLDERS_BACK;" in popup
    assert "id = HIT_OPTION_BASE + items[i - 1] + 1;" in popup        # option = image + 1, as before


def test_digits_pick_a_folder_then_an_image_and_esc_or_backspace_go_back():
    main = code(_src("main.c"))
    handle = body(main, "handle")
    assert "int image = pick_digit(a, e->ch - '1');" in handle
    assert handle.count("pick_digit(a, e->ch - '1')") == 2               # restore and the room's list
    assert "if (image >= 0) restore_pick(a, image);" in handle   # #1896: an unchecked image opens the C: picker first
    assert "if (image >= 0) { a->image_sel = image + 1; a->dd_open = 0; }" in handle
    esc = handle[handle.index("if (e->key == KEYSYM_ESC) {"):]
    assert esc.index("a->pick_in_folder = 0; break;") < esc.index("if (a->dd_open) { a->dd_open = 0; break; }")
    assert "if (e->key == KEYSYM_BACKSPACE && a->dd_open == HIT_ROOM_IMAGE) { a->pick_in_folder = 0; break; }" in handle
    assert "if (!has_text && a->pick_in_folder &&" in handle            # restore: a typed name is edited first
    digit = body(main, "pick_digit")
    assert "if (!a->pick_in_folder) { pick_open_folder(a, k); return -1; }" in digit
    assert "a->pick_in_folder = 0;" in body(main, "go_menu")


def test_the_pick_disk_subtitle_says_image_in_hebrew():
    pick = body(_src("screens_capture.c"), "screen_pick")
    assert "ייווצר image" not in pick and "ייווצר האימג'" in pick


@GUI_BUILD
def test_the_folder_cards_render(tmp_path):
    """Sample data has two folders. "restore" shows the folders, "restore-
    folder" the images of the first; "room-images" is the room with the image
    select open on the folders."""
    binary = _build_gui(tmp_path)
    _render_all(binary, tmp_path, "1280x800")
    for a, b in (("restore", "restore-folder"), ("room", "room-images"), ("restore", "restore-confirm")):
        assert (tmp_path / f"card-{a}-light.png").read_bytes() != (tmp_path / f"card-{b}-light.png").read_bytes(), (a, b)


# --- 8. a picked disk slot reads at a glance (owner, 10/10) -----------------------------


def test_a_selected_slot_has_a_ring_and_a_check_and_the_rest_are_lighter():
    """The selected state was a thin blue border on a green/orange bar."""
    room = code(body(_src("screens_room.c"), "room_machine_draw"))
    assert "Rect ring = { slot.x - SLOT_RING, slot.y - SLOT_RING," in room
    assert "draw_border_rrect(cr, ring, RADIUS_SM + SLOT_RING, t->indigo, 3);" in room
    assert "smart.r * .55 + t->surface.r * .45" in room                # 45% toward the surface
    assert "draw_border_rrect(cr, slot, RADIUS_SM, smart, 2);" in room # the SMART colour stays
    assert "cairo_line_to(cr, cx + h * .90, cy + h * .20);" in room    # the check
    assert '"ער · %d מגירות מוכנות · %d נבחרו"' in room
    assert "if (nsel || picked) draw_border_rrect(cr, item, RADIUS_SM, t->indigo, 2);" in room   # 17/10: a whole picked machine too
    assert "#define SLOT_GAP  (2 * SLOT_RING)" in _src("screens_room.c")


def _room_png(binary, tmp_path: Path, name: str, selected: tuple[int, int]) -> tuple:
    state = tmp_path / f"{name}.txt"
    state.write_text("machine=CLONE-01|3C:52:82:A1:00:21|1|1|2|writing|-1|0|\n"
                     "machine_drawers=0|2\n"
                     f"room_drawer=0|1|SER-A|Model A|256060514304|passed|1|{selected[0]}\n"
                     f"room_drawer=0|2|SER-B|Model B|500107862016|passed|1|{selected[1]}\n"
                     "message=x|y\n", newline="\n")
    _png(binary, tmp_path / name, state, "1000x760")
    return _png_rgb(tmp_path / f"{name}-room-light.png")


@GUI_BUILD
def test_selecting_a_slot_changes_it_unmistakably(tmp_path):
    """Two healthy slots. Selecting the second one fills it with the solid
    SMART green (unselected it is a lighter tint) and rings it in the action
    blue -- thousands of pixels, not a 1px border."""
    binary = _build_gui(tmp_path)
    one = _room_png(binary, tmp_path, "one", (1, 0))
    both = _room_png(binary, tmp_path, "both", (1, 1))
    green, blue = _token("--clr-success"), _token("--clr-action")
    g1, g2 = _count_color(one[3], one[2], green, tol=6), _count_color(both[3], both[2], green, tol=6)
    b1, b2 = _count_color(one[3], one[2], blue, tol=6), _count_color(both[3], both[2], blue, tol=6)
    assert g2 - g1 > 3000, (g1, g2)
    assert b2 - b1 > 1500, (b1, b2)
