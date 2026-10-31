"""#860: קוד pairing בצורת XXX-XXX-XXX מוסתר ביומן גם בלי `code=` לפניו.

‏`test_interserver_enrollment.py::test_redact_hides_a_bare_pairing_code` מגריל
קוד ב-`generate_pairing_code()`, ומאז 16/10 הצורה שלו היא XXXX-XXXX — שמוסתרת
בתבנית אחרת. לכן בבקרה הרטרואקטיבית (24/10) הוא עבר גם כשתבנית #860 הוסרה:
אף טסט לא בדק את הצורה ש-#860 תיקן. כאן הצורה כתובה במפורש.
"""

from __future__ import annotations

from server.interserver import interserver_auth


def test_a_bare_three_group_code_is_redacted():
    out = interserver_auth.redact_secrets("pairing failed for ABC-DEF-GHJ at 10.0.0.5")
    assert "ABC-DEF-GHJ" not in out
    assert "[redacted]" in out
    assert "10.0.0.5" in out                     # מה שאינו סוד נשאר


def test_a_lowercase_or_short_lookalike_is_not_a_code():
    """הצד החיובי: התבנית אינה מוחקת כל דבר עם מקפים."""
    text = "disk sda-sdb-sdc on host lab-01"
    assert interserver_auth.redact_secrets(text) == text
