"""שלב 1 של מוקאפ הקונסולה: כל זוג טקסט/רקע עיקרי עומד ב-4.5:1 (WCAG AA), בשני המצבים.

היחס מחושב מקובץ הטוקנים עצמו (design-tokens.css), לא ממספרים שנכתבו כאן: מי שמשנה צבע
וקורס את הניגודיות נופל כאן. כשל בקריאת הקובץ או טוקן חסר הוא כשל, לא דילוג (עיקרון 5).
"""

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
TOKENS = (STATIC / "design-tokens.css").read_text(encoding="utf-8")


def _block(selector: str) -> dict[str, str]:
    m = re.search(re.escape(selector) + r"\s*\{(.*?)\n\}", TOKENS, re.S)
    assert m, f"block {selector!r} not found in design-tokens.css"
    body = re.sub(r"/\*.*?\*/", "", m.group(1), flags=re.S)
    return dict(re.findall(r"(--[\w-]+)\s*:\s*([^;]+);", body))


LIGHT = _block(":root")
DARK = {**LIGHT, **_block(':root[data-theme="dark"]')}


def _resolve(theme: dict[str, str], name: str) -> str:
    val = theme[name].strip()
    for _ in range(8):
        m = re.fullmatch(r"var\((--[\w-]+)\)", val)
        if not m:
            break
        val = theme[m.group(1)].strip()
    return val


def _rgb(theme: dict[str, str], name: str) -> tuple[float, float, float]:
    val = _resolve(theme, name)
    m = re.fullmatch(r"#([0-9a-fA-F]{6})", val)
    assert m, f"{name} resolves to {val!r}, not a #rrggbb colour"
    h = m.group(1)
    return tuple(int(h[i:i + 2], 16) / 255 for i in (0, 2, 4))


def _lum(c: tuple[float, float, float]) -> float:
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in c]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def ratio(theme: dict[str, str], fg: str, bg: str) -> float:
    a, b = _lum(_rgb(theme, fg)), _lum(_rgb(theme, bg))
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


# (טקסט, רקע) — הזוגות שהקונסולה באמת מציירת
PAIRS = [
    ("--clr-text", "--clr-surface"), ("--clr-text", "--clr-bg"), ("--clr-text", "--clr-hover"),
    ("--clr-muted", "--clr-surface"), ("--clr-muted", "--clr-bg"), ("--clr-muted", "--clr-hover"),
    ("--clr-muted", "--clr-action-soft"),
    ("--clr-header-text", "--clr-header"),
    ("--on-ink", "--clr-action"), ("--on-ink", "--clr-action-hover"), ("--on-ink", "--clr-action-active"),
    ("--on-ink", "--clr-danger-fill"),
    ("--clr-action-fg", "--clr-surface"), ("--clr-action-fg", "--clr-bg"),
    ("--clr-action-fg", "--clr-action-soft"), ("--clr-action-fg", "--clr-info-soft"),
    ("--clr-action-fg", "--clr-hover"),
    ("--clr-success-fg", "--clr-surface"), ("--clr-success-fg", "--clr-success-soft"),
    ("--clr-danger-fg", "--clr-surface"), ("--clr-danger-fg", "--clr-danger-soft"),
    ("--clr-danger-fg", "--clr-bg"),
    ("--clr-warning-fg", "--clr-surface"), ("--clr-warning-fg", "--clr-warning-soft"),
    ("--clr-warning-fg", "--clr-bg"),
    # #2848: yellow -- its words on its own fill, and dark words on the yellow chip
    ("--clr-yellow-fg", "--clr-surface"), ("--clr-yellow-fg", "--clr-yellow-soft"), ("--clr-yellow-fg", "--clr-bg"),
    ("--clr-yellow-on", "--clr-yellow"),
]


def test_every_main_pair_meets_aa_in_both_themes():
    bad = []
    for theme_name, theme in (("light", LIGHT), ("dark", DARK)):
        for fg, bg in PAIRS:
            r = ratio(theme, fg, bg)
            if r < 4.5:
                bad.append(f"{theme_name}: {fg} on {bg} = {r:.2f}")
    assert not bad, "below 4.5:1 -> " + "; ".join(bad)


def test_dark_fill_is_the_darker_blue_not_the_old_one():
    # הכחול הישן #4c8fbd נתן 3.5:1 מול לבן; המוקאפ מאשר #2f78a8 (4.8:1)
    assert _resolve(DARK, "--clr-action").lower() == "#2f78a8"
    assert ratio(DARK, "--on-ink", "--clr-action") >= 4.7


def test_ratio_function_is_not_a_constant():
    # בקרה על הבודק עצמו: שחור על לבן = 21, צבע על עצמו = 1
    t = {"--a": "#000000", "--b": "#ffffff"}
    assert round(ratio(t, "--a", "--b"), 1) == 21.0
    assert round(ratio(t, "--a", "--a"), 1) == 1.0
