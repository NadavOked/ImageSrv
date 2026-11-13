"""שלב 3 — הקצוות שקובעים את הביטחון במערכת.

עיקרון 1 של הפרויקט: כל מצב לא ברור מסתיים באתחול רגיל מהדיסק.
כאן מוודאים שכל שלושת המסלולים לשם — בלי משימה, MAC לא מוכר, שרת
שקט — אכן מסתיימים שם, ושסינון הגודל וההרשאות עומדים במקומם.
"""

from __future__ import annotations

import urllib.error

from .harness import (CLASS_MACS, CONSOLE_BASE, DEAD_BASE, GB256, GB500,
                      NO_LEASE_MAC, UNKNOWN_MAC, Client, check, disk, hello)


def local_boot(answer: dict) -> bool:
    """החלטת הסוכן לפי ממשק 3: אין הוראה מפורשת → דיסק מקומי."""
    return answer.get("task") is None and answer.get("session") is None


def run(ctx) -> None:
    print("\n3. ברירת המחדל — דיסק מקומי")
    probe = Client()
    status, answer = hello(probe, CLASS_MACS[1], [disk("sda", GB256, "STN-1")])
    check("מכונה רשומה בלי משימה → דיסק מקומי",
          status == 200 and answer["known"] and local_boot(answer), str(answer))

    status, answer = hello(probe, UNKNOWN_MAC, [disk("sda", GB256, "XXX-1")])
    check("MAC לא מוכר: לא מציעים דבר → דיסק מקומי",
          answer["known"] is False and answer["allowed_images"] == []
          and local_boot(answer), str(answer))

    # #855: שומר הזהות פעיל בשרת הזה — MAC בלי חכירה מסורב **בשם**, לא
    # מתקבל ולא נבלע כ"לא מוכר". וזו הראיה שהחכירות של המכונות האחרות
    # הן מה שמכניס אותן, לא היעדר בדיקה.
    status, answer = hello(probe, NO_LEASE_MAC, [disk("sda", GB256, "XXX-2")])
    check("MAC בלי חכירת DHCP: הזהות לא ניתנת לבדיקה → 403 בשם",
          status == 403 and answer.get("code") == "identity_unverifiable",
          f"{status} {answer}")

    try:
        hello(Client(DEAD_BASE), CLASS_MACS[1], [disk("sda", GB256, "STN-1")])
        check("שרת שקט: החיבור אמור להיכשל", False)
    except urllib.error.URLError:
        # אצל הסוכן האמיתי (agent/lib, מכוסה ב-tests/test_agent.py)
        # כשל חיבור הוא בדיוק אותו מסלול: chain to local disk.
        check("שרת שקט: אין חיבור → דיסק מקומי", True)

    print("\n4. סינון לפי גודל")
    status, answer = hello(probe, CLASS_MACS[1], [disk("sda", GB256, "STN-1")])
    check("כונן 256 לא רואה אימג' 500",
          ctx.image_a in answer["allowed_images"]
          and ctx.image_b not in answer["allowed_images"], str(answer["allowed_images"]))
    status, answer = hello(probe, ctx.builder.mac, [disk("sda", GB500, "BLD-5")])
    check("כונן 500 רואה את שניהם",
          {ctx.image_a, ctx.image_b} <= set(answer["allowed_images"]),
          str(answer["allowed_images"]))

    print("\n5. הרשאות — משתמש הפצה")
    # #1073: בקיוסק — רק ה-allowlist (אימג'ים, כיתות); בקונסולה — 403 על
    # **כל** נתיב ניהול, גם עם העוגייה שהקיוסק הנפיק (אותו סוד).
    allowed = [("GET", "/api/console/images"), ("GET", "/api/console/groups")]
    for method, path in allowed:
        status, _ = ctx.deploy.json(method, path)
        check(f"deploy מורשה בקיוסק: {path}", status == 200, str(status))
    web = Client(console_base=CONSOLE_BASE)
    web.cookie = ctx.deploy.cookie
    denied = [("GET", "/api/console/images"), ("GET", "/api/console/overview"),
              ("GET", "/api/console/users"), ("GET", "/api/console/journal"),
              ("GET", "/api/console/settings"), ("POST", "/api/console/groups")]
    for method, path in denied:
        status, _ = web.json(method, path, {} if method == "POST" else None)
        check(f"deploy נדחה בקונסולה: {path}", status == 403, str(status))

    status, _ = probe.json("POST", "/api/v1/agent/sessions", {
        "username": "madrich", "password": "wrong-pass",
        "mac": CLASS_MACS[0], "group_id": "grp_LAB1", "image_id": ctx.image_a})
    check("פתיחת סבב עם סיסמה שגויה נדחית", status == 401, str(status))
