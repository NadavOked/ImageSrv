"""שלב 6 — סבב כיתה: גרסה 2, והסירוב שלו גלוי (#3173).

עד #3173 השלב הזה הריץ שלושה סבבי כיתה (שני תנאי ההתחלה, תחנה שנכשלת, פותחת שנופלת,
מאחר). כולם יצאו במנוע השידור הקודם — למחשבי התלמיד אין נתיב במנוע החתיכות, שהוא עכשיו המנוע היחיד —
וכיתות הן v2. ב-v1 פתיחת סבב כיתה **נכשלת בגלוי**, בשם ובעברית, ואינה פותחת סבב שאין מי
שישדר אותו (עיקרון 5). השרת של הסימולציה עולה עם `CLASSROOMS` דלוק (`harness.start_server`),
ולכן הסירוב שנבדק כאן הוא של הסבב עצמו, ולא רק של מתג המהדורה.

מה שנשאר מהשלב הישן ואינו תלוי במנוע: התחנה ממשיכה לדיסק המקומי, ורואה את האימג' שנקלט.
"""

from __future__ import annotations

import re

from .harness import CLASS_MACS, DEPLOY, FILES_A, GB256, Client, check, disk, hello

_HEBREW = re.compile("[א-ת]")


def _refused(status: int, body) -> bool:
    """סירוב בגלוי: 4xx שאינו "לא מזוהה" / "אין הרשאה" / "אין נתיב" (אלה כשל אחר), עם הודעה בעברית."""
    return 400 <= status < 500 and status not in (401, 403, 404) and bool(_HEBREW.search(str(body)))


def _no_session(ctx) -> bool:
    status, overview = ctx.console.json("GET", "/api/console/overview")
    return status == 200 and overview.get("session") is None


def run(ctx) -> None:
    opener, second, _third, late = CLASS_MACS

    print("\n6. סבב כיתה — v2: הפתיחה נדחית בגלוי (#3173)")
    status, body = ctx.deploy.json("POST", "/api/v1/agent/sessions", {
        "username": DEPLOY["username"], "password": DEPLOY["password"], "mac": opener,
        "group_id": "grp_LAB1", "image_id": ctx.image_a, "macs": [opener, second]})
    check("פתיחת סבב כיתה מהתחנה נדחית בגלוי, בעברית", _refused(status, body), f"{status} {body}")
    check("לא נפתח סבב אחרי הסירוב מהתחנה", _no_session(ctx))

    status, body = ctx.console.json("POST", "/api/console/sessions", {
        "group_id": "grp_LAB1", "image_id": ctx.image_a})
    check("פתיחת סבב כיתה מהקונסולה נדחית בגלוי, בעברית", _refused(status, body), f"{status} {body}")
    check("לא נפתח סבב אחרי הסירוב מהקונסולה", _no_session(ctx))

    status, answer = hello(Client(), second, [disk("sda", GB256, "STN-1")])
    check("תחנה בכיתה לא מקבלת סבב — ממשיכה לדיסק המקומי",
          status == 200 and answer["known"] and answer["session"] is None, str(answer))

    # #997: קובצי אימג' מוגשים רק למי שיש לו סיבה — חבר בסבב, מחשב בנייה במשימה. תחנה בלי סבב
    # (וב-v1 אין לה סבב) מקבלת 403 בשם, לא את האימג'. (הבייטים עצמם מול הקליטה — בשלב 9, ממשכפל בגל.)
    status, payload = Client().request(
        "GET", f"/api/v1/images/{ctx.image_a}/files/p3.windows.pcl.zst")
    check("תחנה בלי סבב אינה מקבלת קובץ אימג' (403, #997)",
          status == 403 and payload != FILES_A["p3.windows.pcl.zst"], f"{status} {payload[:200]!r}")
    check("המאחרת של הכיתה רואה את האימג' שנקלט ממחשב הבנייה",
          ctx.image_a in hello(Client(), late, [disk("sda", GB256, "STN-3")])[1]["allowed_images"])
