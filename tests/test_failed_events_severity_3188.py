"""#3188 — אירוע כישלון אינו "מידע".

ברירת המחדל של היומן היא "דורש תשומת לב" (warn/err, #3157). 15 אירועי כישלון סווגו
info, ולכן נעלמו ממנה: מפעיל שפתח את היומן לא ראה ששעון החדר נפל או שהעדכון לא הוחל.
הכלל: כל אירוע ששמו נגמר ב-``_failed`` או שהתווית שלו אומרת "נכשל" — warn או err.
"""

from __future__ import annotations

import pytest

from server.console import journal_he


def _failure_events() -> list[str]:
    return sorted(e for e, label in journal_he.EVENTS_HE.items()
                  if e.endswith("_failed") or "נכשל" in label)


def test_the_rule_sees_the_failure_events():
    """בקרה לכלל עצמו: הוא תופס את מה שנכתב בשבילו, ולא רשימה ריקה."""
    found = _failure_events()
    assert len(found) > 40, found
    assert {"room_tick_failed", "update_apply_failed", "timesync_apply_failed"} <= set(found)


def test_every_failure_event_needs_attention():
    quiet = {e: journal_he.severity(e) for e in _failure_events()
             if journal_he.severity(e) not in ("warn", "err")}
    assert quiet == {}, "אירועי כישלון שלא יופיעו ב'דורש תשומת לב': " + ", ".join(sorted(quiet))


#: ההכרעה לכל אחד (הנימוק בהערה ב-journal_he.py): אדום = לא עובד עכשיו או ניסיון שנדחה;
#: כתום = השינוי לא נכנס והשרת ממשיך במצב הקודם.
DECIDED = {
    "room_tick_failed": "err", "update_apply_failed": "err", "deploy_net_failed": "err",
    "grub_password_failed": "err", "grub_cfg_lock_failed": "err", "grub_server_lock_failed": "err",
    "mfa_disable_failed": "err",
    "net_deploy_bond_failed": "warn", "hostname_change_failed": "warn",
    "hostname_restart_failed": "warn", "console_tls_restart_failed": "warn",
    "timesync_change_failed": "warn", "timezone_change_failed": "warn",
    "timesync_apply_failed": "warn", "timezone_apply_failed": "warn",
}


@pytest.mark.parametrize("event,level", sorted(DECIDED.items()))
def test_each_of_the_fifteen_has_its_decided_level(event, level):
    assert journal_he.severity(event) == level


def test_an_error_one_is_kept_forever_and_a_warning_one_is_not():
    """err = לא נמחק לעולם (#3157); warn נמחק אחרי שנה כמו השאר."""
    kept = journal_he.events_of("err")
    assert "room_tick_failed" in kept and "update_apply_failed" in kept
    assert "hostname_change_failed" not in kept
