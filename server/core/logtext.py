"""ערך לשורת לוג — שורה אחת (CodeQL ‏py/log-injection, ‏#662/#666-669/#807).

‏`logging` אינו מנקה שבירות שורה: ערך עם ``\\n`` מייצר ביומן ה-systemd שורה
שנראית כמו רשומה עצמאית. הערכים שעוברים כאן (MAC קנוני, מזהה קבוצה או משימה
שנמצא ב-DB) נקיים כבר; הניקוי יושב בנקודת הכתיבה כדי שלא יהיה תלוי בזה.
"""

from __future__ import annotations


def one_line(value: object) -> str:
    """``str(value)`` כשורה אחת: ‏CR ו-LF הופכים לרווח."""
    return str(value).replace("\r", " ").replace("\n", " ")
