"""#1846 — אבדן חבילות ברשת ההפצה מול הבסיס שלה.

הבסיס = חציון הגלים הקודמים **על אותו כרטיס, באותו מספר מגירות ובאותו קצב**. בלי בסיס — "לא נבדק", לא "תקין".

#3173 (נדב 21/11): udpcast נמחק, ואיתו המדד של udp-sender (`loss_blocks` / הבלוקים) והקריאה מסוף השולח
(`netloss.evaluate`). המדד שנשאר הוא של מנוע החתיכות (`abandoned` לכל GB), ונבדק ב-`test_chunk_netloss_3164`;
שורה ישנה של udpcast אינה נקראת (`test_learn_no_udpcast_3173`). נשאר כאן מה שאינו תלוי במנוע.
"""

from __future__ import annotations

from server.system import health
from server.learn import netloss as learn_netloss


def test_the_health_screen_carries_it():
    """`health.collect` מוסיף את השורות — נבדק על המקור, כי collect דורש שרת שלם."""
    import inspect
    assert "_learn_netloss.health_checks(ctx.conn)" in inspect.getsource(health.collect)


def test_a_broken_database_is_a_named_unchecked_row():
    class Broken:
        def execute(self, *a):
            raise RuntimeError("db gone")
    (row,) = learn_netloss.health_checks(Broken())
    assert row["state"] == "off" and "לא נבדק" in row["detail"]
