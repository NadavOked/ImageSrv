"""שלב 10 — היומן: כל מה שקרה הושאר אחריו עקבות, בעברית ובשמות."""

from __future__ import annotations

import re

from .harness import check

_HEBREW = re.compile("[א-ת]")


def run(ctx) -> None:
    print("\n10. יומן")
    status, rows = ctx.console.json("GET", "/api/console/journal?limit=1000")
    events = {r["event"] for r in rows}
    # #3173: בלי סבבי כיתה (שלב 6 — נדחים) אין כאן `client_failed` של תחנה ואין `agent_login_failed` של
    # סיסמה שגויה בפתיחת סבב (הסירוב קודם לסיסמה, #1282). במקומם — מה שמנוע החתיכות והפצה ישירה משאירים.
    for needed in ("capture_start", "capture_done", "image_upload", "unknown_mac",
                   "room_open", "room_wave", "room_done",
                   "send_start", "send_done", "send_delegated",
                   "room_open_direct", "direct_manifest", "direct_done", "direct_failed", "room_failed"):
        check(f"היומן מכיל {needed}", needed in events)
    failure = next(r for r in rows if r["event"] == "direct_failed")
    # הכישלון של שלב 9ב (מניפסט שאינו חותם על רשימת ה-BLAKE3 שהועלתה): כותרת בעברית, והסיבה בשמה
    check("היומן בעברית ובשם הסיבה", bool(_HEBREW.search(failure["label"])) and "chunk_b3" in failure["text"],
          f'{failure["label"]} | {failure["text"]}')
