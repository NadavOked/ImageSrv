"""#2921: text on a status fill / soft badge keeps 4.5:1 in both themes.

Hallmark sloplint measured these pairs under 4.5:1. The test reads the real CSS, resolves
`var()` against the light tokens (and the dark overlay for the dark theme) and computes
the WCAG ratio, so it fails when a rule goes back to a token that is too light.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "server" / "static"
MIN = 4.5


def _strip(css: str) -> str:
    return re.sub(r"/\*.*?\*/", "", css, flags=re.S)


def _rules(css: str):
    for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", _strip(css)):
        yield [s.strip() for s in sel.split(",")], body


def _decls(body: str) -> dict[str, str]:
    out = {}
    for part in body.split(";"):
        if ":" in part:
            key, val = part.split(":", 1)
            out[key.strip()] = val.strip()
    return out


def _tokens(theme: str) -> dict[str, str]:
    tokens: dict[str, str] = {}
    for sels, body in _rules((STATIC / "design-tokens.css").read_text(encoding="utf-8")):
        if ":root" in sels or (theme == "dark" and ':root[data-theme="dark"]' in sels):
            tokens.update({k: v for k, v in _decls(body).items() if k.startswith("--")})
    return tokens


def _resolve(value: str, tokens: dict[str, str]) -> str:
    value = value.replace("!important", "").strip()
    match = re.fullmatch(r"var\((--[\w-]+)\)", value)
    if match:
        return _resolve(tokens[match.group(1)], tokens)
    return value


def _lum(hex_: str) -> float:
    assert re.fullmatch(r"#[0-9a-fA-F]{6}", hex_), f"not a plain hex colour: {hex_!r}"
    chans = [int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in chans]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _pair(file: str, selector: str, theme: str) -> tuple[str, str, float]:
    tokens = _tokens(theme)
    css = (STATIC / file).read_text(encoding="utf-8")
    found = [_decls(body) for sels, body in _rules(css) if selector in sels]
    assert found, f"{selector} not found in {file}"
    decls = {k: v for d in found for k, v in d.items()}
    bg = _resolve(decls.get("background") or decls["background-color"], tokens)
    fg = _resolve(decls["color"], tokens)
    return bg, fg, _ratio(bg, fg)


PAIRS = [
    ("console.css", ".topbar #alertBadge"),
    ("console.css", "#login .rules li.ok i"),
    ("console.css", ".pw-pol li.ok i"),
    ("console.css", ".tag.has-alerts"),
    ("vmupdate.css", ".vmu-uc .ubadge.bad"),
    ("vmupdate.css", ".vmu-us li.bad .si"),
]


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("file, selector", PAIRS)
def test_text_on_status_fills_keeps_the_aa_ratio(file, selector, theme):
    bg, fg, ratio = _pair(file, selector, theme)
    assert ratio >= MIN, f"{selector} ({theme}): {fg} on {bg} is {ratio:.2f}:1, under {MIN}"
