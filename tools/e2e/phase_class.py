"""שלבים 6-8 — סבבי הכיתה: הצטרפות, שני תנאי ההתחלה, נפילות ומאחרים.

שלושה סבבים על אותה כיתה:
- סבב א': נפתח מהתחנה, כולם מצטרפים בלי סיסמה, מתחיל כשהמספר הושג.
  תחנה אחת נכשלת — השאר מסיימות, ומי שסיים לא מקבל את הסבב שוב.
- סבב ב': המכונה שפתחה נופלת אחרי ההצטרפות; השידור מתחיל כשהטיימר
  מהמצטרף האחרון פוקע; מאחר שמגיע באמצע נשלח לדיסק המקומי.
- סבב ג': המאחר נכנס לסבב הבא — סבב יחיד משלו, בוחר אימג' ומקבל שם.
"""

from __future__ import annotations

from .harness import (CLASS_MACS, DEPLOY, FILES_A, GB256, Client, check,
                      disk, hello, sha, wait_until)

FAIL_PLAN = [("sda", "failed", "I/O error at sector 8419328")]
DONE_PLAN = [("sda", "done")]


def _view(ctx) -> dict | None:
    status, overview = ctx.console.json("GET", "/api/console/overview")
    return overview.get("session")


def _members(ctx) -> dict[str, dict]:
    view = _view(ctx)
    return {m["hostname"]: m for m in view["members"]} if view else {}


def _open_from_station(ctx, opener_mac: str, macs=None) -> dict:
    body = {"username": DEPLOY["username"], "password": DEPLOY["password"],
            "mac": opener_mac, "group_id": "grp_LAB1", "image_id": ctx.image_a}
    if macs is not None:
        body["macs"] = macs
    status, session = ctx.deploy.json("POST", "/api/v1/agent/sessions", body)
    check("סבב נפתח מהתחנה (משתמש הפצה)", status == 200, str(session))
    return session


def _close_active(ctx) -> None:
    # שם האימג' המשודר הוא האישור בהקלדה (#581) — בדיוק מה שהמסך מציג
    # בכותרת, ולכן זה גם מה שהסימולציה מקלידה.
    view = _view(ctx)
    status, _ = ctx.deploy.json(
        "POST", f"/api/console/sessions/{view['id']}/close",
        {"confirm_name": view["image_name"]},
    )
    check("משתמש ההפצה סגר את הסבב", status == 200, str(status))


def run(ctx) -> None:
    opener, second, third, late = CLASS_MACS

    print("\n6. סבב כיתה — פתיחה מהתחנה, התחלה לפי מספר")
    ctx.stations[opener].write_plans = [DONE_PLAN]
    ctx.stations[second].write_plans = [DONE_PLAN]
    ctx.stations[third].write_plans = [FAIL_PLAN, DONE_PLAN]
    _open_from_station(ctx, opener, macs=[opener, second, third])

    # תחנה מצטרפת ב-hello בלבד — בלי סיסמה ובלי בחירת אימג'.
    status, answer = hello(Client(), second, [disk("sda", GB256, "STN-1")])
    check("הצטרפות בלי סיסמה: הסבב פתוח ו-require_login כבוי",
          answer["session"]["state"] == "open"
          and answer["ui"]["require_login"] is False, str(answer))

    for mac in (opener, second, third):
        ctx.stations[mac].start()
    wait_until(lambda: (v := _view(ctx)) and v["state"] == "running"
               and v["joined"] == 3,
               "השידור התחיל כשהגיע המספר שהוצהר (3/3)")
    status, overview = ctx.console.json("GET", "/api/console/overview")
    check("מנוע השידור יצא לדרך", overview["sender"] is not None
          and overview["sender"]["session_id"] == _view(ctx)["id"],
          str(overview["sender"]))

    wait_until(lambda: {h: m["state"] for h, m in _members(ctx).items()} ==
               {"LAB1-05": "done", "LAB1-06": "done", "LAB1-07": "failed"},
               "שתי תחנות סיימו, השלישית נכשלה — והשאר לא נעצרו",
               detail=lambda: f"members={ {h: m['state'] for h, m in _members(ctx).items()} } view={_view(ctx)}")
    check("השמות נגזרו מטבלת ה-MAC ולא מהמכונה",
          set(_members(ctx)) == {"LAB1-05", "LAB1-06", "LAB1-07"})
    check("הקונסולה מציגה את שם האימג'",
          _view(ctx)["image_name"] == "Windows 11 Base", _view(ctx)["image_name"])
    wait_until(lambda: ctx.stations[opener].answer["session"] is None,
               "מי שדיווח done לא מקבל את הסבב שוב")
    _close_active(ctx)

    print("\n7. סבב כיתה — הפותחת נופלת, הטיימר מתחיל, מאחר נדחה")
    # #1794: ה-API דוחה פחות מ-30 שניות (כמו הקונסולה), ולכן הסימולציה
    # מקצרת את הטיימר ישירות במסד -- הקוד שנבדק כאן הוא הטיימר, לא ה-API.
    status, _ = ctx.console.json("POST", "/api/console/settings",
                                 {"session_wait_seconds": "2"})
    check("ה-API דוחה טיימר של שתי שניות (#1794)", status == 400, str(status))
    from server.db import connect, set_setting      # noqa: PLC0415
    conn = connect(ctx.data_dir / "imagesrv.db")
    try:
        set_setting(conn, "session_wait_seconds", "2")
    finally:
        conn.close()
    ctx.stations[opener].silent_after_join = True
    _open_from_station(ctx, opener)                 # בלי בחירה — כל הכיתה (4)
    wait_until(lambda: ctx.stations[opener].silent,
               "המכונה שפתחה הצטרפה — ונפלה",
               detail=lambda: f"answer={ctx.stations[opener].answer} "
                              f"view={_view(ctx)}")
    wait_until(lambda: (v := _view(ctx)) and v["state"] == "running"
               and v["joined"] == 3,
               "השידור התחיל מהטיימר: 3 מתוך 4 — המספר לא הושג")

    status, answer = hello(Client(), late, [disk("sda", GB256, "STN-3")])
    check("מצטרף מאוחר בזמן שידור → דיסק מקומי",
          answer["known"] and answer["session"] is None, str(answer))

    wait_until(lambda: (m := _members(ctx))
               and m["LAB1-06"]["state"] == "done"
               and m["LAB1-07"]["state"] == "done"
               and not m["LAB1-05"]["done"],
               "השאר סיימו למרות שהפותחת נעלמה")
    _close_active(ctx)
    ctx.stations[opener].silent_after_join = False
    ctx.stations[opener].silent = False

    print("\n8. תחנת תלמיד — המאחר נכנס לסבב הבא, לבדו")
    ctx.stations[late].write_plans = [DONE_PLAN]
    ctx.stations[late].start()
    wait_until(lambda: ctx.stations[late].answer
               and ctx.image_a in ctx.stations[late].answer["allowed_images"],
               "התחנה רואה את האימג' שנקלט ממחשב הבנייה")
    _open_from_station(ctx, late, macs=[late])
    wait_until(lambda: (v := _view(ctx)) and v["state"] == "running" and v["single"],
               "סבב יחיד יוצא לדרך מיד (1/1)")
    wait_until(lambda: (m := _members(ctx)) and
               m.get("LAB1-08", {}).get("state") == "done",
               "התחנה שוחזרה וקיבלה את השם LAB1-08")

    # ההוכחה שהאימג' עצמו מגיע: משיכת קובץ מחיצה ביוניקאסט (ממשק 7)
    # והשוואת ה-sha256 למניפסט — אותם בייטים שמחשב הבנייה העלה.
    status, payload = ctx.stations[late].client.request(
        "GET", f"/api/v1/images/{ctx.image_a}/files/p3.windows.pcl.zst")
    part = next(p for p in ctx.manifest_a["partitions"] if p["index"] == 3)
    check("הבייטים שהתחנה מושכת הם שהועלו בקליטה, sha256 תואם",
          status == 200 and payload == FILES_A["p3.windows.pcl.zst"]
          and sha(payload) == part["sha256"])
    wait_until(lambda: ctx.stations[late].answer["session"] is None,
               "גם התחנה הבודדת לא מקבלת את הסבב שוב אחרי done")
    _close_active(ctx)
    for station in ctx.stations.values():
        station.stop()      # הכיתה "כבתה" — עולה מהדיסק המקומי
