"""‏#1303 (הכרעת נדב 12/10): ‏`server/agentctl/` — תשתית צי סוכני ה-AI — נמחק
מהמוצר. אין לו קורא, והוא נארז לשרת ולריפו הציבורי; מקומו ב-otogit.

שלוש ההכרעות האחרות של #1303 נשמרות בטסטים משלהן:
מעטפת הניפוי — ‏`test_agent.py::test_debug_flag_is_an_unknown_parameter` ו-
‏`test_agent_offers_no_shell_on_any_terminal`; החסימה לפי כתובת מקור —
‏`test_console_bind.py::test_console_source_allowlist_flag_is_gone_and_refused_by_name`;
מתג הקצב הנלמד — ‏`test_room_learned_rate.py` ו-`small_pages_wol.test.cjs`.
להכרעה הזו לא היה שומר: סנכרון מ-otogit או מיזוג ישן היה מחזיר את התיקייה בשקט.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_agentctl_is_not_part_of_the_server():
    # קבצי מקור ולא התיקייה: ‏`__pycache__` שנשאר מהרצה ישנה אינו קוד במוצר.
    assert sorted((ROOT / "server" / "agentctl").rglob("*.py")) == []
    mentions = [p.relative_to(ROOT).as_posix() for p in (ROOT / "server").rglob("*.py")
                if re.search(r"\bagentctl\b", p.read_text(encoding="utf-8"))]
    assert mentions == []
