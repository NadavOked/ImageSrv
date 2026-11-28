"""#1270 (הכרעת נדב 10/10): אין בקונסולה אישור בהקלדת מילה/שם.

פעולה הרסנית = חלון sheet() עם שאלה, השלכות, [ביטול] וכפתור אדום. ה-confirm_name
ש-API מסוים עדיין דורש נשלח מהקוד מתוך הערך הידוע — לא מהמפעיל. הטסט סורק את
הסטטי ונופל אם חזר שדה הקלדה או ניסוח שמבקש הקלדה.
"""
import re
from pathlib import Path

from console_src import console_files, console_js

STATIC = Path(__file__).resolve().parent.parent / "server" / "static"
FILES = sorted(p for p in STATIC.rglob("*") if p.suffix in {".js", ".html"} and "vendor" not in p.parts)

CODE_PATTERNS = {
    "verify option": r"\bmustEqual\b|\bverify\s*:\s*\{",
    "verify field": r"sf-verify|confirmName|pmodal-input",
    "confirm field in sheet": r"""\bid\s*:\s*["']confirm["']|\bid\s*:\s*["']n["']\s*\+""",
}
TEXT_PATTERNS = [
    "הקלדת שם", "הקלד את שם", "הקלידו את שם", "להקליד את שם", "להקליד בדיוק",
    "הקלד את ה-IQN", "הקלדת ה-IQN", "הקלדה זהה", "הקלד את שם הכרטיס",
    "הקלדת ${", 'הקלד "', "הקלידו את שמו", "הקלדת IQN",
]


def test_scan_finds_the_files():
    assert any(p.name == "console.js" for p in FILES)


def test_no_typed_word_confirmation_in_console():
    found = []
    linked = {STATIC / name for name in console_files()}
    sources = [("console*.js", console_js())]
    sources.extend((path.name, path.read_text(encoding="utf-8")) for path in FILES if path not in linked)
    for name, text in sources:
        for label, pattern in CODE_PATTERNS.items():
            for m in re.finditer(pattern, text):
                found.append(f"{name}:{text.count(chr(10), 0, m.start()) + 1} {label}")
        for phrase in TEXT_PATTERNS:
            i = text.find(phrase)
            while i != -1:
                found.append(f"{name}:{text.count(chr(10), 0, i) + 1} text {phrase!r}")
                i = text.find(phrase, i + 1)
    assert not found, "הקלדת מילה לאישור חזרה:\n" + "\n".join(found)
