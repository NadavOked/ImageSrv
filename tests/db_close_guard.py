"""סוגר אחרי כל טסט את חיבורי ה-DB שהטסט פתח (#3180).

``sqlite3.Connection`` שלא נסגר נשאר פתוח, עם שלושה fd (הקובץ, ``-wal``,
``-shm``), עד שאיסוף הזבל המחזורי מגיע אליו: מטמון ההצהרות שלו מחזיק אותו
במעגל. טסט שבונה ``create_app`` משאיר ``db.Database`` שמחזיק חיבור של
התהליכון הראשי, וה-``Database`` עצמו במעגל של האפליקציה. כש-worker של
pytest רץ תחת כיסוי, איסוף הזבל המלא נדיר, והחיבורים מצטברים: ב-CI הציבורי
של v0.95.0 ה-fd עברו את 1024, ו-``select.select`` נפל (#3180).

השרת סוגר בעצמו את החיבור של תהליכון שמת (``db._ThreadConnections``). כאן
נסגר מה שנשאר: החיבור של התהליכון הראשי בכל ``Database`` שנוצר בזמן הטסט.
``Database`` שממשיך לשמש אחרי הטסט (פיקסטורה ברמת מודול) פותח חיבור חדש
בקריאה הבאה, ולכן הסגירה אינה משנה את מה שהטסט הבא רואה.
"""

import weakref

import pytest


@pytest.fixture(autouse=True)
def db_close_guard(monkeypatch):
    from server import db                                    # noqa: PLC0415

    opened: list = []
    real_init = db.Database.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        opened.append(weakref.ref(self))

    monkeypatch.setattr(db.Database, "__init__", init)
    yield
    for ref in opened:
        database = ref()
        if database is not None:
            database.close()
