"""‏#1310: "אתה שם?" — מה שהראשי עונה על 8443, ורק זה.

הכרעת נדב 12/10 (אפשרות א'): חריג מצומצם לכלל "הראשי תמיד יוזם" (18/09).
מתקין המשני שואל את הראשי אם הוא חי, כדי ש"בדוק חיבור" יוכל להיות
**ירוק** — ו"הבא" חסום עד אז. כל השאר (push/pull, ניהול) נשאר ביוזמת
הראשי בלבד, על המאזין ה-mTLS של המשני.

החוזה הצר:

* נתיב אחד, ``GET`` בלבד, בלי הזדהות ובלי פעולות. כל נתיב אחר = 404, כל
  שיטה אחרת = 405 — אין כאן ``/docs``/``/openapi.json`` של FastAPI, ואין
  אף ראוטר של הקונסולה או של הסוכן.
* התשובה היא **רק** ``{name, version, fingerprint}``.
  ``fingerprint`` = ‏SHA-256 של ה-SPKI של זהות הראשי הבין-שרתית
  (``storage_identity``) — אותו מפתח שה-TLS של המאזין הזה מציג, ושהמשני
  יצמיד ב-pairing. המתקין משווה את השניים: תשובה שהטביעה בה אינה של
  התעודה שהוצגה אינה "ירוק" (עיקרון 5).
* מאזין משלו (uvicorn + TLS מקובצי הזהות), על כרטיס השרתים של הראשי
  בפורט הבין-שרתי — **לא** נתיב על 8081: הקונסולה אינה נחשפת לרשת שבין
  האתרים, ו-FW האתר מעביר רק את 8443.
"""

from __future__ import annotations

from typing import Callable

from fastapi import FastAPI

#: הנתיב היחיד במאזין. ‏`installer/gui-bridge.sh` שואל אותו.
ALIVE_PATH = "/api/interserver/v1/alive"


def create_alive_app(*, name: Callable[[], str], version: Callable[[], str | None],
                     fingerprint: str) -> FastAPI:
    """האפליקציה של המאזין: נתיב אחד, שלושה שדות.

    ``name``/``version`` נקראים בכל בקשה (שם השרת יכול להשתנות בקונסולה,
    והגרסה אחרי עדכון); ``version`` ‏``None`` = לא נקראה — נאמר כ-``null``,
    לא כמחרוזת ריקה."""
    app = FastAPI(title="ImageSrv primary alive", docs_url=None, redoc_url=None,
                  openapi_url=None)

    @app.get(ALIVE_PATH)
    def alive() -> dict:
        return {"name": name(), "version": version(), "fingerprint": fingerprint}

    return app


def server_config(app: FastAPI, ident: dict, host: str, port: int):
    """‏``uvicorn.Config`` של המאזין: TLS מקובצי הזהות הבין-שרתית (אותו מפתח
    שה-``fingerprint`` מתאר). ‏HTTP פשוט אינו נענה כלל."""
    import uvicorn
    return uvicorn.Config(app, host=host, port=port, log_level="info",
                          ssl_certfile=ident["server_cert_ref"],
                          ssl_keyfile=ident["server_key_ref"])
