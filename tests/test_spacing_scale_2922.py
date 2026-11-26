"""#2922: the shared .btn and .card keep every padding/margin/gap on the 4px scale.

Spacing rules in console.css are read the way a browser reads them (comments gone,
one declaration at a time) and every length in them must be a multiple of 4px.
Ties between two scale values round up (6 -> 8, 10 -> 12), the owner's rule of 20/11.
The other screens are separate PRs, so only the shared selectors below are checked.
"""
import re
from pathlib import Path

CSS = Path(__file__).resolve().parents[1] / "server" / "static" / "console.css"

SHARED = {
    ".btn", ".btn,.icon-btn,.tool-btn", ".btn .dot", ".btn.icon", "td .btn", ".btn.sm",
    ".page .btn.sm", ".btn.busy::before", ".page dl.mkv dd .btn.sm.flat",
    ".card-h", ".card-b", ".page .card-h", ".page .card-h .acts", ".page .card-b",
}
SPACING = re.compile(r"^(padding|margin|gap|row-gap|column-gap)(-[a-z-]+)?$")


def _rules():
    text = re.sub(r"/\*.*?\*/", "", CSS.read_text(encoding="utf-8"), flags=re.S)
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", text):
        yield re.sub(r"\s+", " ", m.group(1)).strip().replace(", ", ","), m.group(2)


def _off_scale():
    seen, bad = set(), []
    for sel, body in _rules():
        if sel not in SHARED:
            continue
        seen.add(sel)
        for decl in body.split(";"):
            prop, _, val = decl.partition(":")
            if not SPACING.match(prop.strip()):
                continue
            for n in re.findall(r"(?<![\w.-])(-?\d+(?:\.\d+)?)px", val):
                if float(n) % 4:
                    bad.append(f"{sel} {{ {prop.strip()}: {val.strip()} }}")
    return seen, bad


def test_the_shared_selectors_are_all_found():
    seen, _ = _off_scale()
    assert seen == SHARED, sorted(SHARED - seen)


def test_no_shared_btn_or_card_spacing_is_off_the_4px_scale():
    _, bad = _off_scale()
    assert bad == [], "\n".join(bad)
