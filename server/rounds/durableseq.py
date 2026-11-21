"""דיווח `durable` בדלתא (#2533, הכרעת המתאם 13/11): המספר הרץ שהשרת עונה, והשאלה "האם אני יכול למקם את הדלתא הזו".

עד כאן הסוכן שלח את היומן המצטבר **כולו** בכל דיווח — כל שתי שניות בדיווח ההתקדמות, ובכל שאלה של
`chunk-pass`. היומן גדל עם הנזק: אימג' של 200GB שחסרה בו כל חתיכה שנייה, על שני דיסקים, הוא 47,500
טווחים — מעבר לתקרת הגוף (1MiB, `server/agent/body.py`), ואז הדיווח כולו נדחה ב-413.

**החוזה** (interfaces.md, סעיפים 4 ו-7), לכל (סבב, מכונה, ערוץ) — הערוצים הם `progress` ו-`pass`:

- הסוכן שולח רק טווחים שנעשו עמידים מאז הדיווח האחרון **שהשרת אישר**, ועם הדיווח את `durable_seq`:
  המספר האחרון שנענה בו (0 בהתחלה).
- המספר שבדיווח הוא המספר שבידי השרת ← הדלתא ממשיכה את מה שכאן: נרשמת (איחוד בלבד, `chunkledger`),
  והתשובה נושאת את המספר הבא. הסוכן מסמן את מה ששלח כמאושר רק כשקיבל אותה.
- כל מספר אחר ← **פער**: תשובה שאבדה בדרך, או שרת שאותחל (המספרים בזיכרון בלבד, במכוון). התשובה
  נושאת `durable_full: true` ואת המספר שבידי השרת; הסוכן שוכח מה שחשב שאושר ושולח הכול מחדש, בחתיכות.
- דיווח בלי `durable_seq` (סוכן שלפני הדלתא, או דיווח שאין בו טווחים) אינו נספר ואינו נבדק.

מה שהשרת רשם אינו נמחק בפער: היומן הוא איחוד, ושליחה חוזרת של אותם טווחים אינה משנה בו דבר. לכן אין
כאן מצב שבו דלתא "נרשמה פעמיים" — רק מצב שבו השרת **אינו יודע מה חסר לו**, ואז הוא מבקש הכול.
"""

from __future__ import annotations

import threading


class DurableSeq:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at: dict[tuple[str, str, str], int] = {}

    def admit(self, session_id: object, mac: str, channel: str, body: dict) -> bool | None:
        """``None`` = הדיווח אינו ממוספר; ``True`` = הדלתא ממשיכה את מה שבידי השרת; ``False`` = פער."""
        claimed = body.get("durable_seq")
        if type(claimed) is not int or not isinstance(session_id, str):
            return None
        with self._lock:
            return self._at.get((session_id, mac, channel), 0) == claimed

    def answer(self, session_id: object, mac: str, channel: str, admitted: bool | None) -> dict:
        """השדות שהתשובה נושאת: המספר הבא (הדלתא התקבלה), או המספר שכאן ו-`durable_full` (פער)."""
        if admitted is None or not isinstance(session_id, str):
            return {}
        key = (session_id, mac, channel)
        with self._lock:
            if not admitted:
                return {"durable_seq": self._at.get(key, 0), "durable_full": True}
            self._at[key] = self._at.get(key, 0) + 1
            return {"durable_seq": self._at[key]}
