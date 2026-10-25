"""שלב 0 — הקונסולה: משתמשים, קבוצות, וטבלת ה-MAC.

משתמש ההתחלה נוצר ישירות ב-DB (כמו בהתקנה); כל השאר דרך ה-API
של הקונסולה, בדיוק כפי שנדב היה עושה בדפדפן. הוא admin שאינו המקומי.
נדב 18/10: חיוב MFA למנהלים הוא מתג (`mfa_required_for_admins`, ברירת מחדל
כבוי), ולכן הכניסה הראשונה **אינה** דורשת רישום; הסימולציה מפעילה MFA
מרצון ב-HTTP, כמו הדפדפן, ולא עוקפת אותו ב-DB.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

from .harness import (ADMIN, BUILD_MAC, CLASS_MACS, CLONER_MAC, DEPLOY,
                      GB256, BASE, REPO, Client, check, disk)
from .machines import SimMachine


def run(data_dir, images_dir) -> SimpleNamespace:
    print("0. קונסולה ורישום")
    sys.path.insert(0, str(REPO))
    from server import users                   # noqa: PLC0415
    from server.db import connect              # noqa: PLC0415
    conn = connect(data_dir / "imagectl.db")
    users.create(conn, ADMIN["username"], ADMIN["password"], "admin", by="sim")
    conn.close()

    console = Client()
    status, body = console.json("POST", "/api/console/login", ADMIN)
    check("כניסה לקונסולה", status == 200, str(status))
    check("בלי המתג, admin אינו נדרש לרישום MFA בכניסה הראשונה (נדב 18/10)",
          not body.get("mfa_enrollment_required"), str(body))
    from server import totp as totp_mod       # noqa: PLC0415
    status, setup = console.json("POST", "/api/console/me/mfa/setup")
    check("הגדרת MFA מחזירה סוד", status == 200 and bool(setup.get("secret")), str(setup))
    status, _ = console.json("POST", "/api/console/me/mfa/enable",
                             {"code": totp_mod.totp_code(setup["secret"])})
    check("MFA הופעל בקוד TOTP", status == 200, str(status))
    status, me = console.json("GET", "/api/console/me")
    check("ה-cookie נשמר בין בקשות", status == 200 and me["role"] == "admin", str(me))

    status, _ = console.json("POST", "/api/console/users", dict(DEPLOY, role="deploy"))
    check("משתמש הפצה נוצר", status == 200, str(status))
    # ‏#1073: למשתמש הפצה אין קונסולה וובית — הכניסה בקונסולה נדחית בשם,
    # והוא נכנס דרך פורט הסוכן (פני מחשב הבנייה), שנושא את allowlist הקיוסק.
    web = Client()
    status, body = web.json("POST", "/api/console/login", DEPLOY)
    check("למשתמש ההפצה אין קונסולה וובית (#1073)",
          status == 403 and body.get("error") == "deploy_no_console", f"{status} {body}")
    deploy = Client(console_base=BASE)
    status, _ = deploy.json("POST", "/api/console/login", DEPLOY)
    check("כניסת משתמש ההפצה דרך פורט הסוכן", status == 200, str(status))

    status, _ = console.json("POST", "/api/console/groups",
                             {"id": "grp_LAB1", "label": "כיתה LAB1", "role": "classroom"})
    check("קבוצת כיתה נוצרה", status == 200)
    # ‏#880: v1 מדליק "הפצה לכיתות" רק מהקונסולה — הסימולציה מדליקה
    # אותה כמו המפעיל, אחרת שלב 6 (סבב מהתחנה) היה נדחה ב-409.
    status, _ = console.json("POST", "/api/console/settings",
                             {"class_deploy_enabled": "true"})
    check("הפצה לכיתות הודלקה (#880)", status == 200, str(status))
    status, imported = console.json("POST", "/api/console/machines/import", {
        "group_id": "grp_LAB1",
        "text": "\n".join(f"{m} {i:02d}" for i, m in enumerate(CLASS_MACS, start=5)),
    })
    check("ארבע תחנות יובאו", imported.get("saved") == 4, str(imported))
    status, _ = console.json("POST", "/api/console/machines", {
        "mac": BUILD_MAC, "name": "BUILD-PC", "group_id": "grp_BUILD"})   # #1997: שם מחשב באנגלית
    check("מחשב הבנייה נרשם (קבוצה קבועה)", status == 200, str(status))
    status, _ = console.json("POST", "/api/console/machines", {
        "mac": CLONER_MAC, "name": "01", "group_id": "grp_CLONERS"})
    check("מחשב השיכפול נרשם", status == 200, str(status))

    # המכונות המדומות — כל אחת תהליכון עם לולאת hello משלה.
    builder = SimMachine(BUILD_MAC, [disk("sda", GB256, "BLD-1")])
    stations = {mac: SimMachine(mac, [disk("sda", GB256, f"STN-{i}")])
                for i, mac in enumerate(CLASS_MACS)}
    # שלוש מגירות זו מעל זו: החריץ (port) הוא סדר המגירות, ושם ההתקן
    # הוא סדר הגילוי — כאן הם הפוכים בכוונה, בדיוק כמו בחדר (#27).
    cloner = SimMachine(CLONER_MAC, [disk("sda", GB256, "DRW-A", port=3),
                                     disk("sdb", GB256, "DRW-B", port=1),
                                     disk("sdc", GB256, "DRW-C", port=2)])
    return SimpleNamespace(
        console=console, deploy=deploy, images_dir=images_dir, data_dir=data_dir,
        builder=builder, stations=stations, cloner=cloner,
        image_a=None, manifest_a=None, image_b=None, manifest_b=None,
    )
