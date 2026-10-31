"""צורת מזהה האימג' — כלל אחד, נאכף בשני המסלולים שמכניסים אימג' (#110).

המזהה אינו מחרוזת תצוגה: הוא שם התיקייה שהאימג' נכנס אליה, ולכן הוא
הגבול בין "מזהה" ל"נתיב". שתי בדיקות כאן הן על הכלל עצמו ולא על קלט
זדוני, והן החשובות שבקובץ:

* הכלל נגזר מהמחולל, ולכן המזהה שהשרת מקצה חייב לעבור אותו;
* הכלל צר יותר מהמציאות = אימג'ים קיימים נעלמים מהמסך, וזה כשל בפני
  עצמו — ולכן ארבעת האימג'ים שמונחים בספריית המעבדה נבדקים בשמם.
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")

from server.library import images
from server.library.images import inside, valid_image_id

from test_capture import make_task, setup_build_machine

#: ארבעת האימג'ים שמונחים בספרייה של שרת המעבדה (‏docs/lab-test-plan.md,
#: ‏docs/handoff-2026-08-29.md) נכון ל-2026-08-29.
LAB_LIBRARY = ("img_6f28b0", "img_9c0b87", "img_b6bfd4", "img_f9879d")


def test_every_image_in_the_lab_library_still_passes():
    """‏regex צר מדי אינו "מחמיר יותר": הוא מעלים אימג'ים קיימים."""
    assert [name for name in LAB_LIBRARY if not valid_image_id(name)] == []


def test_the_id_the_server_hands_out_is_an_id_the_server_accepts(server):
    """הכלל נגזר מהמחולל (`"img_" + secrets.token_hex(3)`), לא להפך.

    מי שיחליף את אורך האסימון בלי לגעת בכלל יגלה זאת רק כשקליטה של
    אימג' אמיתי תיפול **אחרי** שכל הבייטים כבר הועלו. כאן זה נופל מיד.
    """
    created = make_task(server, setup_build_machine(server)).json()
    assert valid_image_id(created["image_id"])


@pytest.mark.parametrize("bad", [
    "../evil",                    # יציאה מהשורש
    "../../srv/tftp",
    "img_7f3a91/../../evil",      # מתחיל כמו מזהה ומסתיים כנתיב
    "/etc/imagesrv",
    "images/img_7f3a91",
    "img_7f3a91 ",                # רווח בקצה — נדחה, לא נחתך
    " img_7f3a91",
    "img_7F3A91",                 # השרת מייצר אותיות קטנות
    "img_7f3a9", "img_7f3a912",   # לא שש ספרות
    "img_zzzzzz", "img_", "7f3a91", "img7f3a91", "",
    "img_7f3a91\n",               # ‏fullmatch ולא `$`: שורה שנייה אינה סוף
    "img_7f3a91\x00",
    None, 7, True, ["img_7f3a91"], {"id": "img_7f3a91"},
])
def test_anything_that_is_not_the_format_is_refused_as_it_came(bad):
    """אין `strip`, אין `basename`, אין החלפת תווים — רק כן או לא.

    ניקוי שקט הופך קלט זדוני לקלט תקין: `../evil` היה נכנס לספרייה
    בשם `evil` והכל היה "עובד" (עיקרון 5 בכיוון ההפוך).
    """
    assert not valid_image_id(bad)


def test_the_format_the_capture_generator_writes_is_accepted():
    """מה שהמחולל מייצר, בכל צורותיו: שש ספרות הקסה קטנות."""
    for value in ("img_000000", "img_ffffff", "img_7f3a91", "img_a8df93"):
        assert valid_image_id(value)


# --- החגורה השנייה: הנתיב שיוצא, לא המחרוזת שנכנסת --------------------------


def test_a_path_that_lands_in_the_root_comes_back_resolved(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    assert inside(root / "img_7f3a91", root) == (root / "img_7f3a91").resolve()


def test_a_path_that_climbs_out_of_the_root_is_refused(tmp_path):
    root = tmp_path / "images"
    root.mkdir()
    (tmp_path / "evil").mkdir()
    assert inside(root / ".." / "evil", root) is None
    assert inside(tmp_path / "evil", root) is None
    assert inside(root / "img_7f3a91" / ".." / ".." / "evil", root) is None


def test_the_boundary_does_not_depend_on_the_id_rule(tmp_path, monkeypatch):
    """‏regex שמישהו ירחיב בעתיד לא ישבור את הגבול — הן בדיקות נפרדות."""
    monkeypatch.setattr(images, "IMAGE_ID", re.compile(r"[^\x00]*"))
    root = tmp_path / "images"
    root.mkdir()
    assert valid_image_id("../evil")               # הכלל הורחב...
    assert inside(root / "../evil", root) is None  # ...והגבול נשאר
