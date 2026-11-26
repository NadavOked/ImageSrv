"""#3320: הקטלוג של המוח צמוד לקוד **גם בכיוון ההפוך** -- כל כלל K שהקוד רושם כתוב בקטלוג, באותו קובץ.

השורש של #3320: `test_brain_catalog.py` בודק שמה שכתוב בקטלוג **קיים** (file+symbol), ולא שמה שקיים **נכתב**. שבעה
כללים היו בקוד ו-`planned` בקטלוג, ואיש לא נכשל. כאן:

1. כל ``RULE = "Kn"``, ``rule="Kn"`` ו-``Decision("Kn"`` ב-`server/` הוא פריט K בקטלוג, שה-`file` שלו (או אחד מ-`also`)
   הוא הקובץ שבו הכלל נמצא, והסטטוס שלו אינו ``planned``.
2. לכל כלל K יש ``mode`` (``shadow`` / ``enforce``) -- איך הוא רץ היום. ``K8`` הוא ``enforce``, והיחיד.
3. כלל שה-`mode` שלו ``enforce`` אכן כותב ``mode="enforce"`` בקובץ שלו, וכלל ``shadow`` אינו כותב.

**בקרה שלילית:** החזרת שורות K בקטלוג לבסיס (``planned``, בלי ``mode``) מפילה את 1 ו-2.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CATALOG = REPO / "server" / "brain" / "catalog.toml"
#: שורות K שאינן של #3320: K10 שייכת ל-#3276/#3280, ו-`mode` שלה נוסף שם.
NOT_MINE = {"K10"}
MODES = {"shadow", "enforce"}
PATTERNS = (re.compile(r'\bRULE\s*=\s*"(K\d+)"'), re.compile(r'\brule\s*=\s*"(K\d+)"'),
            re.compile(r'\bDecision\(\s*"(K\d+)"'), re.compile(r'\bK\d+_RULE\b[^=\n]*=\s*"(K\d+)"'),
            re.compile(r'\bRULE\b,[^=\n]*=\s*"(K\d+)"'))


def _items() -> dict[str, dict]:
    with CATALOG.open("rb") as f:
        return {it["id"]: it for it in tomllib.load(f)["item"] if re.fullmatch(r"K\d+", it["id"])}


def _rules_in_code() -> dict[str, set[str]]:
    found: dict[str, set[str]] = {}
    for path in (REPO / "server").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        for pattern in PATTERNS:
            for rule in pattern.findall(text):
                found.setdefault(rule, set()).add(path.relative_to(REPO).as_posix())
    return found


def test_the_scan_finds_the_rules_we_know_are_in_the_code():
    # סריקה שלא מצאה כלום הייתה מעבירה את הטסט הבא בלי לבדוק דבר (עיקרון 5)
    assert {"K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K9", "K10"} <= set(_rules_in_code())


def test_every_rule_in_the_code_is_in_the_catalog_in_its_file():
    items = _items()
    bad = []
    for rule, files in sorted(_rules_in_code().items()):
        it = items.get(rule)
        if it is None:
            bad.append(f"{rule}: אין פריט בקטלוג ({', '.join(sorted(files))})")
            continue
        if it["status"] == "planned":
            bad.append(f"{rule}: בקוד ({', '.join(sorted(files))}), ובקטלוג planned")
        known = {it.get("file"), *(a["file"] for a in it.get("also", []))}
        missing = sorted(files - known)
        if missing:
            bad.append(f"{rule}: הקטלוג אינו מצביע על {', '.join(missing)}")
    assert bad == [], "\n".join(bad)


def test_every_rule_says_how_it_runs_and_only_K8_decides():
    items = _items()
    without = sorted(i for i, it in items.items() if "mode" not in it and i not in NOT_MINE)
    assert without == [], f"כלל K בלי mode: {without}"
    assert all(it["mode"] in MODES for it in items.values() if "mode" in it)
    assert sorted(i for i, it in items.items() if it.get("mode") == "enforce") == ["K8"]


def test_the_mode_matches_what_the_rule_writes():
    for rid, it in _items().items():
        if "mode" not in it or "file" not in it:
            continue
        text = (REPO / it["file"]).read_text(encoding="utf-8")
        writes = 'mode="enforce"' in text
        assert writes == (it["mode"] == "enforce"), f"{rid}: mode={it['mode']}, ו-{it['file']} {'כותב' if writes else 'אינו כותב'} enforce"
