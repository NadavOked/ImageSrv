"""#595: טקסט של ``OSError`` ושל ``Exception`` כללי אינו יוצא ללקוח.

ההכרעה (SECURITY.md, "מה הלקוח רואה כשפעולה נכשלת"): הודעה עוברת רק
מחריגה שאנחנו כותבים; כל טקסט אחר נכתב ליומן השרת, והלקוח מקבל משפט
קבוע בעברית. הכישלון עצמו נשאר גלוי (עיקרון 5).
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from server.console import storage
from server.interserver import auth as interserver_auth, storage_client, storage_nodes

RAW = re.compile(r"WinError|refused|No such file|Errno|/var/lib|secret-path", re.I)


def test_an_os_error_becomes_the_hebrew_unreachable_sentence(caplog):
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    exc = ConnectionRefusedError(111, "Connection refused")
    text = storage.contact_failure(exc, 9443)
    assert "שרת המשנה לא עונה" in text and "9443" in text
    assert not RAW.search(text), text
    assert any("Connection refused" in r.getMessage() for r in caplog.records)


def test_a_generic_exception_is_a_fixed_sentence_and_the_details_are_logged(caplog):
    caplog.set_level("WARNING", logger="imagesrv.console_storage")
    exc = KeyError("/var/lib/imagesrv/secret-path")
    text = storage.contact_failure(exc, None)
    assert text == "הפנייה לשרת המשנה נכשלה — הפרטים ביומן השרת"
    assert any("secret-path" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("exc", [
    storage_client.InterserverClientError("ping נכשל (503): עסוק"),
    interserver_auth.CredentialFileError("קובץ האישורים אינו קובץ בטוח לפתיחה"),
    storage_nodes.IdentityError("אין זהות שרת"),
])
def test_a_message_we_wrote_still_reaches_the_operator(exc):
    assert storage.contact_failure(exc, None) == str(exc)


def test_a_message_we_wrote_is_still_redacted():
    exc = storage_client.InterserverClientError("Authorization: Bearer abcdef0123456789")
    assert "abcdef0123456789" not in storage.contact_failure(exc, None)
