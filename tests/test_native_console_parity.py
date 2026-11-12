"""The native GUI (station / build / cloner) and the installer look like the console.

11/10, Nadav: "שלא יקרה מצב שהגואי הקטן או ההתקנה נראה שונה מהשרת". The
reference is the console itself -- server/static/design-tokens.css (every
colour, radius, shadow) and console.css (the .win dialog frame: button and
field geometry) -- not a mockup copied from it once and left to drift.

This file is the table. Every colour field of THEME_LIGHT / THEME_DARK
(native-gui/src/theme.c) names the console token it IS, and the test reads
both sides in both themes. A token changed in the console without theme.c
(or the reverse) turns it red; a new theme.c colour with no row here does too.
tests/test_native_gui_theme.py binds the geometry constants the same way
(selector "console ...").
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKENS = (ROOT / "server/static/design-tokens.css").read_text(encoding="utf-8")
THEME_C = (ROOT / "native-gui/src/theme.c").read_text(encoding="utf-8")

# native field -> console token
MAP = {
    "porcelain": "--clr-bg", "surface": "--clr-surface", "ink": "--clr-text",
    "muted": "--clr-muted", "hair": "--clr-border", "indigo": "--clr-action",
    "indigo_soft": "--clr-action-soft", "led_write": "--clr-action",
    "led_ok": "--clr-success", "led_idle": "--led-idle", "danger": "--clr-danger",
    "danger_fill": "--clr-danger-fill", "danger_fg": "--clr-danger-fg",
    "hover": "--clr-hover", "field": "--clr-field", "field_line": "--clr-border",
    "track": "--clr-row-border", "sunken": "--clr-bg", "btn_hover": "--clr-hover",
    "btn_hover_line": "--clr-action", "ink_hover": "--clr-action-hover",
    "on_ink": "--on-ink", "danger_line": "--danger-line", "mark_line": "--art-c",
    "login_a": "--art-bg", "login_b": "--art-a", "login_glow": "--art-b",
    "choice": "--clr-surface", "choice_line": "--clr-border",
    "selected_line": "--clr-action", "button": "--clr-surface",
    "button_line": "--clr-border", "danger_bg": "--clr-danger-soft",
    "warning_bg": "--clr-warning-soft", "warning_line": "--clr-warning",
    "warning_ink": "--clr-warning-fg", "success_bg": "--clr-success-soft",
    "success_line": "--clr-success", "success_ink": "--clr-success-fg",
    "metric": "--clr-surface", "metric_line": "--clr-border", "status_bg": "--clr-nav",
    "header": "--clr-header", "header_text": "--clr-header-text",
    "info": "--clr-info", "info_soft": "--clr-info-soft",
    "success_soft": "--clr-success-soft", "danger_soft": "--clr-danger-soft",
    "warning_soft": "--clr-warning-soft", "bar_done": "--clr-success",
    "bar_idle": "--clr-border", "art_bg": "--art-bg", "art_a": "--art-a",
    "art_b": "--art-b", "art_c": "--art-c", "art_line": "--art-line",
    "art_server": "--art-server", "disk_bg": "--clr-surface", "disk_line": "--clr-border",
    "disk_selected": "--clr-action-soft", "disk_selected_line": "--clr-action",
    "clone_line": "--clr-border", "round_line": "--clr-border", "image_bg": "--clr-field",
    "image_line": "--clr-border", "image_selected": "--clr-action-soft",
    "image_selected_line": "--clr-action", "alert_bg": "--clr-warning-soft",
    "alert_line": "--clr-warning", "alert_ink": "--clr-text",
    "success_btn": "--clr-success", "success_btn_line": "--clr-success",
    "node_active_line": "--clr-action", "node_warn_line": "--clr-warning",
    "warn": "--clr-warning", "brand_accent": "--clr-header-text",
    "action_fg": "--clr-action-fg",
    # #2848 (Nadav 11/11): yellow -- failed the last clone / a cut write; not SMART's orange
    "yellow": "--clr-yellow", "yellow_soft": "--clr-yellow-soft", "yellow_fg": "--clr-yellow-fg", "yellow_on": "--clr-yellow-on",
}


def blocks():
    light = TOKENS[TOKENS.index(":root{"):TOKENS.index("\n}", TOKENS.index(":root{"))]
    start = TOKENS.index(':root[data-theme="dark"]{')
    return light, TOKENS[start:TOKENS.index("\n}", start)]


LIGHT_BLOCK, DARK_BLOCK = blocks()


def token(prop, theme):
    """A token's final value; dark inherits what only :root defines; a var()
    alias is followed (the console keeps --porcelain: var(--clr-bg) etc.)."""
    for block in ([DARK_BLOCK] if theme == "dark" else []) + [LIGHT_BLOCK]:
        found = re.search(r"(?<![\w-])" + re.escape(prop) + r":([^;]+);", block)
        if found:
            value = found[1].strip()
            alias = re.fullmatch(r"var\((--[\w-]+)\)", value)
            return token(alias[1], theme) if alias else value
    raise KeyError(prop)


def hex6(value):
    value = value.lstrip("#").lower()
    return value if len(value) == 6 else "".join(c * 2 for c in value)


def theme_body(name):
    return re.search(r"const Theme " + name + r"\s*=\s*\{(.*?)\n\};", THEME_C, re.S)[1]


def fields(body):
    return dict(re.findall(r"\.(\w+)\s*=\s*HEX\(0x([\dA-Fa-f]{6})\)", body))


NATIVE = {"light": fields(theme_body("THEME_LIGHT")), "dark": fields(theme_body("THEME_DARK"))}
BODY = {"light": theme_body("THEME_LIGHT"), "dark": theme_body("THEME_DARK")}


def test_every_native_colour_names_the_console_token_it_is():
    for theme in ("light", "dark"):
        have = set(NATIVE[theme]) - {"shadow_strong"}      # black: see the shadow test
        assert have == set(MAP), (theme, have ^ set(MAP))


def test_every_native_colour_equals_its_console_token_in_both_themes():
    wrong = []
    for theme in ("light", "dark"):
        for field, prop in sorted(MAP.items()):
            want = hex6(token(prop, theme))
            if NATIVE[theme][field].lower() != want:
                wrong.append(f"{theme} {field}: native {NATIVE[theme][field]} != {prop} {want}")
    assert not wrong, "\n".join(wrong)


def test_radii_are_the_consoles_r_and_r_sm_per_theme():
    """Light 9/6px, dark 3/3px -- the native one was 4/3 (the old mockup's)."""
    for theme in ("light", "dark"):
        assert re.search(r"\.radius\s*=\s*([\d.]+)", BODY[theme])[1] == token("--r", theme).removesuffix("px"), theme
        assert re.search(r"\.radius_sm\s*=\s*([\d.]+)", BODY[theme])[1] == token("--r-sm", theme).removesuffix("px"), theme


def test_shadows_are_black_with_the_consoles_overlay_and_card_alpha():
    """The native shadow was the header's blue-grey; the console's are black:
    --shadow-overlay (menus, toast) and --shadow-card (cards)."""
    for theme in ("light", "dark"):
        overlay = re.search(r"/\s*(\d+)%\)|,\s*\.(\d+)\)", token("--shadow-overlay", theme).split("px")[-1])
        a_overlay = float(overlay[1]) / 100 if overlay[1] else float("0." + overlay[2])
        card = re.findall(r"/\s*(\d+)%\)|,\s*\.(\d+)\)", token("--shadow-card", theme))[-1]
        a_card = float(card[0]) / 100 if card[0] else float("0." + card[1])
        assert NATIVE[theme]["shadow_strong"] == "000000", theme
        assert float(re.search(r"\.shadow_strong_a\s*=\s*([\d.]+)", BODY[theme])[1]) == a_overlay, theme
        assert float(re.search(r"\.shadow_card_a\s*=\s*([\d.]+)", BODY[theme])[1]) == a_card, theme
