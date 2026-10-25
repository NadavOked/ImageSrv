"""הפנים של הריפו הציבורי: ה-README וארבעת התרשימים אינם נושאים גרסה מיושנת.

נדב 24/10: "ה-README שם צריך להיות מעודכן תמיד, זה הפנים שלנו". הטסט נכשל
כש"ImageCtl v1" או "v1 היא מסירת" חוזרים (הם היו שם אחרי שהגרסאות נקבעו
מחדש: v0.76 = ISO, v1.0 אחרי בדיקות שטח, v2.0 = כיתות), ומוודא שהתרשים הבהיר
והכהה מציגים אותם טקסטים.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACE = ["README.md", "docs/architecture-v1.svg", "docs/architecture-v1-dark.svg",
        "docs/flow-v1.svg", "docs/flow-v1-dark.svg",
        "docs/usb-clone.svg", "docs/usb-clone-dark.svg"]
# מורכבים מחלקים כדי שהקובץ הזה לא יתפוס את עצמו.
BANNED = ["ImageCtl" + " v1", "v1 היא" + " מסירת", "מה ב-v1" + " ומה לא", "ארגז כלים** — **v3"]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _texts(rel: str) -> list[str]:
    root = ET.fromstring(_read(rel))
    return ["".join(t.itertext()).strip() for t in root.iter("{http://www.w3.org/2000/svg}text")]


def test_face_files_exist_and_have_no_stale_version() -> None:
    for rel in FACE:
        text = _read(rel)
        for phrase in BANNED:
            assert phrase not in text, f"{rel}: '{phrase}'"
        assert not re.search(r"\bv1\s*·", text), f"{rel}: כותרת תחתונה של v1"


def test_light_and_dark_diagrams_say_the_same() -> None:
    for a, b in (("docs/architecture-v1.svg", "docs/architecture-v1-dark.svg"),
                 ("docs/flow-v1.svg", "docs/flow-v1-dark.svg"),
                 ("docs/usb-clone.svg", "docs/usb-clone-dark.svg")):
        assert _texts(a) == _texts(b), f"{a} ו-{b} נסחפו"
