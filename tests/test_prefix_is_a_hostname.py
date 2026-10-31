"""הקידומת חייבת להיות משהו ששם מחשב יכול להיבנות ממנו (#98).

הקידומת נוסעת מהשרת לסוכן ומשם לשם המחשב, ואף שלב בדרך לא בדק שהיא
יכולה **להיות** שם מחשב. הבדיקה היחידה יושבת ב-`agent/lib/postdeploy/hostname.sh`
— בקצה, אחרי שהאימג' כבר נכתב על הדיסק.

**השורש הוא בשם שהמפעיל מקליד.** ‏`console_api.py:113` הופך רווח לקו
תחתון, וקו תחתון שורד את הסינון ומותר במפורש במזהה הקבוצה — אבל הוא
**אינו** תו חוקי בשם מחשב:

    "Lab A"  →  grp_Lab_A  →  LAB_A  →  LAB_A-05  →  bad_hostname

וגם האורך: ‏NetBIOS חוסם ב-15 תווים, ו-`grp_Computer_Lab_204` נותן
קידומת בת 16 לפני שהוסיפו מקף וסיומת.

**והכשל שקט לחלוטין.** ‏`name_this_machine` מתועדת "Never fatal" וכותבת
שורת יומן ל-ramdisk שנמחק באתחול הבא; דיווח ההתקדמות אינו נושא את
תוצאת השם; והקונסולה מציגה `done`. כל שלושים המחשבים עולים עם השם
שהיה באימג' — כלומר כולם עם אותו שם, ואיש לא יודע עד שמישהו ניגש
למחשב. זו בדיוק צורת הכשל של #62 ושל #89.

הבדיקה כאן היא במקור — `sessions.open`, המקום היחיד שכל הפותחים
(קונסולה, תחנה, `room.py`, `pulls.py`) עוברים דרכו.
"""

from __future__ import annotations

import pytest
from conftest import setup_classroom

pytest.importorskip("fastapi")


def open_round(server, prefix: str):
    ids = setup_classroom(server)
    return server["admin"].post(
        "/api/console/sessions",
        json={"group_id": ids["group"], "image_id": "img_7f3a91",
              "prefix": prefix, "expected_clients": 2},
    )


@pytest.mark.parametrize("prefix", ["LAB_A", "LAB A", "LAB.1", "כיתה", "LAB#1"])
def test_a_prefix_that_cannot_be_a_hostname_is_refused(server, prefix):
    """‏`LAB_A` הוא המקרה הריאלי: הוא נולד מ-"Lab A" שהמפעיל הקליד."""
    r = open_round(server, prefix)
    # ‏409 ולא 400: זו המוסכמה הקיימת — `SessionError` ממופה ל-409 בכל
    # נקודות הקצה, וגם "קידומת ריקה" מוחזרת כך היום. יישור לשם, ולא
    # שינוי מיפוי רחב שה-Issue לא ביקש.
    assert r.status_code == 409, f"{prefix!r} התקבל"
    assert "קידומת" in r.json()["detail"]


def test_a_prefix_too_long_for_netbios_is_refused(server):
    """‏15 תווים הם התקרה, והסיומת והמקף נספרים בתוכם.

    ‏`COMPUTER_LAB_204` (16) נדחה על התווים; ‏`COMPUTERLAB2040` (15)
    חוקי בתווים ועדיין בלתי אפשרי — ‏`+ "-05"` נותן 18.
    """
    r = open_round(server, "COMPUTERLAB2040")
    assert r.status_code == 409, "קידומת של 15 תווים התקבלה למרות שאין מקום לסיומת"
    assert "15" in r.json()["detail"]


@pytest.mark.parametrize("prefix", ["LAB1", "LAB-1", "A", "LAB1234567"])
def test_a_prefix_that_works_is_still_accepted(server, prefix):
    """הבדיקה אינה חוסמת את מה שעובד היום. ‏`LAB1234567` + `-05` = 13."""
    assert open_round(server, prefix).status_code == 200, prefix
