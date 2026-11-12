"""apple-design quick wins: משוב ותנועה בקונסולה, בקיוסק ובאשף.

בדיקות סטטיות על קובצי ה-CSS/JS (אין דפדפן ב-CI): :active, reduced-motion,
טוקני משך, כניסה/יציאה ל-toast ול-backdrop, ו-pointerType ב-reorder.js.
"""
import re
from pathlib import Path

from console_src import console_js

ROOT = Path(__file__).resolve().parent.parent / "server"
STATIC = ROOT / "static"
CSS = {
    "console": STATIC / "console.css",
    "wizard": ROOT / "wizard" / "static" / "wizard.css",
}


def _read(path):
    return path.read_text(encoding="utf-8")


def test_every_css_file_has_active_feedback():
    for name, path in CSS.items():
        assert ":active" in _read(path), f"{name}: אין :active"


def test_every_css_file_respects_reduced_motion():
    for name, path in CSS.items():
        css = _read(path)
        assert re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)", css), name
        block = css[css.index("prefers-reduced-motion"):]
        assert "transition-duration" in block and "animation-duration" in block, name


def test_motion_tokens_exist_and_are_used_instead_of_bare_durations():
    tokens = _read(STATIC / "design-tokens.css")
    for token in ("--dur-fast:", "--dur:", "--ease:"):
        assert token in tokens, token
    for path in (CSS["console"],):
        css = _read(path)
        assert "var(--dur-fast)" in css, path.name
        assert not re.search(r"transition:\s*\.1\d?s\s*[;}]", css), \
            f"{path.name}: transition גורף על הכול"


def test_toast_and_backdrop_animate_instead_of_toggling_display():
    css = _read(CSS["console"])
    assert re.search(r"#toast\.show\{[^}]*opacity:1", css)
    assert re.search(r"\.detail-backdrop\.show\{[^}]*opacity:1", css)
    js = console_js()
    assert 'detailBackdrop").classList.add("show")' in js
    assert 'el.style.display = "none"' not in js.split("function toast(")[1][:400]


def test_reorder_long_press_distinguishes_mouse_from_touch():
    js = _read(STATIC / "reorder.js")
    assert "event.pointerType" in js
    assert re.search(r"LONG_PRESS_MOUSE_MS\s*=\s*150", js)
    assert re.search(r"LONG_PRESS_MS\s*=\s*400", js)


def test_f06_modal_and_drawer_use_state_classes_with_origin_and_sheet_duration():
    css = _read(CSS["console"])
    tokens = _read(STATIC / "design-tokens.css")
    for token in ("--dur-press:", "--dur-base:", "--dur-sheet:"):
        assert token in tokens, token
    assert re.search(r"#modal\.show\{[^}]*opacity:1", css)
    assert re.search(r"#modal #modalBox[^{]*\{[^}]*scale\(\.94\)[^}]*transform-origin:var\(--origin", css)
    assert re.search(r"\.drawer\{[^}]*transform:translateX\(-105%\);transition:transform var\(--dur-sheet\)", css)
    js = console_js()
    assert 'modal.style.display' not in js
    assert "function modalOrigin(" in js and "clearTimeout(modalCleanupTimer)" in js


def test_f06_reduced_motion_is_a_short_fade_only():
    css = _read(CSS["console"])
    # every reduced-motion block, not just the last (later features add their own)
    block = "".join(css[i:i + 4000] for i in [m.start() for m in re.finditer(r"@media \(prefers-reduced-motion", css)])
    assert re.search(r"#modal,\.drawer\{transition:opacity var\(--dur-fast\) linear", block)
    assert "transform:none!important" in block


def test_f09_type_scale_tokens_in_rem_and_every_font_size_uses_them():
    tokens = _read(STATIC / "design-tokens.css")
    for name, value in (("xs", ".6875"), ("sm", ".75"), ("base", ".8125"),
                        ("md", ".9375"), ("lg", "1.125"), ("xl", "1.375")):
        assert f"--t-{name}:{value}rem" in tokens, name
    for name, path in CSS.items():
        css = _read(path)
        assert not re.findall(r"font-size:\s*(?:\d|\.)\d*(?:\.\d+)?px", css), f"{name}: font-size בפיקסלים"
        assert "var(--t-" in css, name


def test_f09_no_uppercase_labels_and_letter_spacing_in_em():
    for name, path in CSS.items():
        css = _read(path)
        assert "text-transform:uppercase" not in css.replace(" ", ""), name
        assert not re.search(r"letter-spacing:\s*-?\.?\d+(?:\.\d+)?px", css), f"{name}: letter-spacing בפיקסלים"
    console = _read(CSS["console"])
    assert re.search(r"h1,h2,h3\{letter-spacing:-\.01em;line-height:1\.2\}", console)
    assert re.search(r"th,[^{]*th\{letter-spacing:\.02em\}", console)
