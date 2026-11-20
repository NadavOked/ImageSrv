"""#2160 (CodeQL #652/#653, py/insecure-protocol): הקשר ה-TLS הבין-שרתי נעול על 1.3
בקריאות ``set_options`` נפרדות, דגל אחד בכל קריאה.

``set_options(A | B | C)`` לא מזוהה ב-CodeQL כהגבלה, וההתראות על
``storage_client.py:222,583`` נשארו פתוחות גם אחרי e9c4b2ba5. הטסט נועל את צורת
הקריאות (שהיא מה ש-CodeQL קורא) ואת התוצאה (כל הפרוטוקולים הישנים כבויים).
"""

from __future__ import annotations

import pytest

pytest.importorskip("OpenSSL", reason="pyOpenSSL נדרש להקשר הבין-שרתי")

from OpenSSL import SSL

from server.interserver import auth as interserver_auth

OLD = ("SSLv2", "SSLv3", "TLSv1", "TLSv1_1", "TLSv1_2")


def _build_and_record(monkeypatch):
    calls: list[int] = []

    class Spy(SSL.Context):
        def set_options(self, options):
            calls.append(options)
            return super().set_options(options)

    monkeypatch.setattr(SSL, "Context", Spy)
    ctx = interserver_auth.tls13_only_context()
    return ctx, calls


def test_every_old_protocol_is_switched_off_by_its_own_call(monkeypatch):
    _ctx, calls = _build_and_record(monkeypatch)
    flags = {name: getattr(SSL, "OP_NO_" + name) for name in OLD}
    for flag in calls:
        # ב-OpenSSL 1.1+ הערך של OP_NO_SSLv2 הוא 0: קריאה כזו מכסה אותו בלבד.
        covered = [n for n, v in flags.items() if (v and flag & v == v) or flag == v]
        assert len(covered) == 1, f"set_options({flag:#x}) מכבה יותר מפרוטוקול אחד בקריאה אחת"
    for name, value in flags.items():
        assert value in calls, f"אין קריאה ל-OP_NO_{name}"


def test_the_context_still_refuses_every_old_protocol(monkeypatch):
    ctx, _calls = _build_and_record(monkeypatch)
    now = ctx.set_options(0)
    for name in OLD:
        flag = getattr(SSL, "OP_NO_" + name)
        assert now & flag == flag, f"OP_NO_{name} כבוי בהקשר"
