"""#2592 — הטבלה `trusted_browsers` (דפדפן זכור, #1085) הוצאה משימוש ונשארה בסכמה.

"דפדפן זכור" הוסר (63135798, #1243): כל כניסה עם MFA דורשת קוד. הטבלה **נשארת**
בסכמה, כי הורדתה היא שינוי סכמה ו-"חזור לגרסה" תלוי בה — כמו `smart_asks`
(`test_smart_ask_removed_2044.py`). מה שנעל כאן: אין לה קורא או כותב מחוץ להגדרה,
והתיעוד אינו אומר שמשהו נמחק ממנה.

**בקרה שלילית:** הטסט השלישי נופל על main שלפני התיקון (`interfaces.md` כתב "מחיקת
`trusted_browsers`"). הראשון נשמר ירוק בשני המצבים ונופל רק כשמוסיפים קורא או כותב
לטבלה בקוד; שניהם נמדדו, והפקודות בגוף ה-PR.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TABLE = "trusted_browsers"
CREATE = "CREATE TABLE IF NOT EXISTS trusted_browsers ("
#: טקסט בלבד: קובצי bytecode ותמונות אינם "קוד שקורא".
TEXT = (".py", ".sh", ".js", ".sql", ".c", ".html", ".css", ".json", ".service", ".conf")


def _mentions(roots: tuple[str, ...]) -> list[str]:
    hits = []
    for root in roots:
        for path in sorted((REPO / root).rglob("*")):
            if not path.is_file() or path.suffix not in TEXT:
                continue
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if TABLE in line:
                    hits.append(f"{path.relative_to(REPO).as_posix()}:{n}: {line.strip()}")
    return hits


def test_nothing_reads_or_writes_trusted_browsers_outside_its_definition():
    hits = _mentions(("server", "agent", "boot", "tools", "installer", "deploy", "native-gui"))
    assert [h.split(": ", 1)[1] for h in hits] == [CREATE], hits
    assert hits[0].startswith("server/db.py:"), hits


def test_the_table_stays_in_the_schema():
    """"חזור לגרסה": הגרסה הקודמת מצפה לטבלה. הורדתה היא שינוי סכמה, לא ניקוי."""
    from server import db
    assert CREATE in db.SCHEMA
    assert f"DROP TABLE IF EXISTS {TABLE}" not in (REPO / "server" / "db.py").read_text(encoding="utf-8")


def test_the_interface_doc_does_not_claim_anything_is_deleted_from_it():
    """RED על main: `interfaces.md` כתב שאיפוס סיסמה מוחק את `trusted_browsers` ושיש
    עוגיית `imagesrv_trusted` שמדלגת על MFA; שניהם כבר לא קורים."""
    doc = (REPO / "docs" / "architecture" / "interfaces.md").read_text(encoding="utf-8")
    assert not re.search(r"מחיקת\s+`trusted_browsers`", doc)
    for n, line in enumerate(doc.splitlines(), 1):
        if "דפדפן זכור" in line or "הדפדפנים הזכורים" in line:
            assert "אין" in line or "מוסר" in line, f"interfaces.md:{n}: {line}"
    assert "שמדלגת על שלב 2" not in doc
