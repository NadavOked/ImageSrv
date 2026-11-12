"""The console's JavaScript files, in the order index.html loads them (#2594).

Tests that slice console code by function name read it through here instead of
opening `console.js` directly, so moving code between the console's own files
(`console.js`, later `console-core.js`) does not move it out from under them.
The list comes from index.html, never from a hard-coded one.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
_SCRIPT = re.compile(r'<script\s+src="(console[^"/?]*\.js)(?:\?[^"]*)?"')


def console_files() -> list[str]:
    """File names (relative to server/static) of every console*.js, load order."""
    names = _SCRIPT.findall((STATIC / "index.html").read_text(encoding="utf-8"))
    if "console.js" not in names:
        raise AssertionError("index.html no longer loads console.js")
    return names


def console_js() -> str:
    """The text of all console files, concatenated in load order."""
    return "\n".join((STATIC / n).read_text(encoding="utf-8") for n in console_files())
