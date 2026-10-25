"""‏#1246 — `NadavOked/ImageSrv` הוא הריפו הציבורי שלנו, לא "פרויקט אחר".

המשפט השגוי הופיע ב-CLAUDE.md, ב-README, ב-docs/server.md ובהערות של
כלים, ושכפל את עצמו מהעתקה להעתקה. הטסט סורק את הטקסט שנשלח בפועל
(‏md/sh/ps1/py) ונופל על הביטוי. היסטוריה (docs/handoff-*, logs/) מוחרגת
במכוון: היא מתעדת מה היה נכון ביומה.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# הביטוי מורכב מחלקים כדי שהקובץ הזה לא יתפוס את עצמו.
PHRASE = "פרויקט" + " " + "אחר"
SUFFIXES = {".md", ".sh", ".ps1", ".py"}
SKIP_DIRS = {".git", "logs", "node_modules", ".claude", ".agents", "__pycache__"}


def offenders(root: Path) -> list[str]:
    out = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if not p.is_file() or p.suffix not in SUFFIXES:
            continue
        if set(rel.parts) & SKIP_DIRS or rel.name.startswith("handoff-"):
            continue
        if p.resolve() == Path(__file__).resolve():
            continue
        if PHRASE in p.read_text(encoding="utf-8", errors="replace"):
            out.append(rel.as_posix())
    return out


def test_docs_never_call_the_public_repo_another_project():
    assert offenders(ROOT) == []


def test_the_scan_catches_the_phrase(tmp_path):
    """בקרה שלילית: בלי זה, סריקה שמוצאת 0 קבצים נראית כמו הצלחה."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "x.md").write_text(f"השם תפוס על ידי {PHRASE}", encoding="utf-8")
    (tmp_path / "docs" / "handoff-1.md").write_text(PHRASE, encoding="utf-8")
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "a.md").write_text(PHRASE, encoding="utf-8")
    assert offenders(tmp_path) == ["docs/x.md"]
