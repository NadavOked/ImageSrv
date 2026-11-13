"""#2682 — "באתחול הבא" מראה את מסך הכישלון של #2666, באותה החלטה ובאותו תקציב.

אחרי #2666 מכונה שתשובתה `bad-schema` ובקשתה הגיעה לכרטיס מפיץ מקבלת, בתוך התקציב של #75,
את הערך של הסוכן (`answer-failure-screen`); אחריו -- הדיסק. התצוגה (#2550) חישבה לה את
`decide()` של התשובה הגולמית, ולכן אמרה "דיסק מקומי" בדיוק במקום שבו המכונה תקבל מסך.

כל טענה נבדקת מול האתחול האמיתי (`/boot/menu` על כרטיס ההפצה), לא מול עצמה.
"""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from boot.grub_menu import AGENT, LOCAL                                  # noqa: E402
from server.agent import answer_failure, boot_preview                    # noqa: E402
from server.agent.bootguard import ATTEMPT_LIMIT                         # noqa: E402

from test_answer_failure_screen_2666 import VLAN, age_mark, menu         # noqa: E402,F401
from test_boot_preview_2550 import (                                     # noqa: E402,F401
    BUILD, decision_code, preview, register, snapshot,
)

CODE = "answer-failure-screen"


@pytest.fixture()
def bad(server, monkeypatch):
    """השרת בונה תשובה שהמחולל אינו מבין -- בבקשת התפריט האמיתית ובתצוגה כאחד."""
    from server.agent import hello as hello_module
    real = hello_module.build_answer
    broken = lambda *a, **k: {**real(*a, **k), "schema": 2}              # noqa: E731
    monkeypatch.setattr(hello_module, "build_answer", broken)
    monkeypatch.setattr(boot_preview, "build_answer", broken)
    register(server, BUILD, "grp_BUILD", "BUILD-PC")
    return server


def shown(server) -> dict:
    return preview(server)["machines"][BUILD]


# --- התצוגה אומרת מה שהאתחול האמיתי עושה, בכל מצב של התקציב ---------------------


@pytest.mark.parametrize("boots_so_far", range(ATTEMPT_LIMIT + 3))
def test_the_preview_agrees_with_the_real_boot_inside_the_budget_and_after_it(bad, boots_so_far):
    """הבקשה ה-(ATTEMPT_LIMIT+1) היא הראשונה שחורגת: עד אז מסך הכישלון, מאז הדיסק --
    והתצוגה אומרת את אותו דבר *לפני* שהאתחול קורה."""
    for _ in range(boots_so_far):
        menu(bad, BUILD, VLAN)
    before = shown(bad)
    served = menu(bad, BUILD, VLAN)

    in_budget = boots_so_far < ATTEMPT_LIMIT
    assert decision_code(served) == (CODE if in_budget else "bad-schema")
    assert before["decided"] is True
    assert before["code"] == decision_code(served), before
    assert before["kind"] == ("failure" if in_budget else "local")
    assert before["action"] == (AGENT if in_budget else LOCAL)
    assert before["guard"] is None            # זו לא הגנת לולאה של #75 על הסבב


def test_a_quiet_window_gives_the_screen_back_in_the_preview_too(bad):
    for _ in range(ATTEMPT_LIMIT + 1):
        menu(bad, BUILD, VLAN)
    assert shown(bad)["kind"] == "local"                      # התקציב נגמר
    age_mark(bad, BUILD, answer_failure.WINDOW_SECONDS // 60 + 1)
    assert shown(bad)["code"] == CODE                         # שקט ארוך -- תקציב חדש
    assert decision_code(menu(bad, BUILD, VLAN)) == CODE      # והאתחול האמיתי מסכים


def test_the_failure_comes_before_known_exactly_as_in_the_real_resolver(bad, monkeypatch):
    """`make_resolver` בודק `failed()` לפני `known`; תצוגה שהייתה בודקת קודם `known` הייתה
    אומרת "לא רשומה" על מכונה שהאתחול האמיתי שלה הוא מסך הכישלון."""
    real = boot_preview.build_answer
    monkeypatch.setattr(boot_preview, "build_answer",
                        lambda *a, **k: {**real(*a, **k), "known": False})
    assert shown(bad)["code"] == CODE


# --- התצוגה אינה אתחול -------------------------------------------------------------


def test_previews_never_spend_the_failure_budget(bad):
    """הבקרה של "הצגה לא משנה מונה": הרבה תצוגות לפני האתחול הראשון, ומסך הכישלון עדיין
    מוגש ATTEMPT_LIMIT פעמים בדיוק."""
    before = snapshot(bad["ctx"].conn)
    for _ in range(ATTEMPT_LIMIT + 2):
        assert shown(bad)["kind"] == "failure"
    assert snapshot(bad["ctx"].conn) == before                # לא נכתבה שורה
    codes = [decision_code(menu(bad, BUILD, VLAN)) for _ in range(ATTEMPT_LIMIT + 1)]
    assert codes == [CODE] * ATTEMPT_LIMIT + ["bad-schema"]


def test_an_unreadable_counter_is_unknown_not_the_disk(bad):
    """"לא הצלחנו לקרוא את המונה" אינו "יעלה מהדיסק" (עיקרון 5)."""
    conn = bad["ctx"].conn
    conn.execute("ALTER TABLE boot_attempts RENAME TO boot_attempts_away")
    conn.commit()
    try:
        item = shown(bad)
    finally:
        conn.execute("ALTER TABLE boot_attempts_away RENAME TO boot_attempts")
        conn.commit()
    assert item == {"decided": False, "why": boot_preview.WHY_GUARD_UNREADABLE}


def test_the_kind_table_knows_the_failure_screen():
    assert boot_preview._kind(AGENT, CODE, False) == "failure"


# --- peek ו-offer: חשבון אחד --------------------------------------------------------


@pytest.mark.parametrize("stored_context", [answer_failure.CONTEXT, "session:ses_other", None])
@pytest.mark.parametrize("age_s", [0, answer_failure.WINDOW_SECONDS - 5, answer_failure.WINDOW_SECONDS + 5])
def test_peek_returns_what_offer_returns_in_every_counter_state(server, stored_context, age_s):
    """הקריאה-בלבד והאמיתית, זו מול זו, בכל מצב של המונה, ההקשר והחלון. כאן נחשף חשבון שני
    של התקציב אם `peek` ו-`offer` נפרדים."""
    from datetime import datetime, timedelta, timezone
    conn = server["ctx"].conn
    now = datetime.now(timezone.utc).replace(microsecond=0)
    stamp = (now - timedelta(seconds=age_s)).isoformat(timespec="seconds")
    answer = {"schema": 2, "known": True}
    at = now.isoformat(timespec="seconds")
    for stored in range(0, ATTEMPT_LIMIT + 3):
        conn.execute("DELETE FROM boot_attempts")
        if stored_context:
            conn.execute("INSERT INTO boot_attempts (mac, context, attempts, first_at, last_at)"
                         " VALUES ('m1', ?, ?, ?, ?)", (stored_context, stored, stamp, stamp))
        conn.commit()
        rows = lambda: [tuple(r) for r in conn.execute("SELECT * FROM boot_attempts")]   # noqa: E731
        before = rows()
        peeked = answer_failure.peek(conn, "m1", answer, now=at)
        assert rows() == before                              # `peek` אינו כותב
        offered = answer_failure.offer(conn, "m1", answer, now=at)
        assert peeked == offered, (stored_context, age_s, stored)
