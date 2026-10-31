"""שלב 1 — מחשב הבנייה: קליטת שני אימג'ים והספרייה.

אימג' א' (משפחת 256) הוא זה שיופץ בהמשך לכיתה, לתחנה ולחדר השיכפולים —
סגירת הלולאה המלאה. אימג' ב' (משפחת 500) קיים בשביל סינון הגודל,
והוא גם עובר את מסלול ההורדה/העלאה בין שרתים.
"""

from __future__ import annotations

from .harness import (FILES_A, FILES_B, GB256, GB500, Client, check, disk,
                      make_manifest, wait_until)


def _capture(ctx, name: str, folder: str, files: dict, source_bytes: int):
    status, task = ctx.console.json("POST", "/api/console/tasks/capture", {
        "mac": ctx.builder.mac, "disk": "sda", "name": name,
        "description": "Captured in simulation", "folder": folder})
    check(f"משימת קליטה נוצרה: {name}", status == 200, str(task))

    # המכונה מגלה את המשימה בלולאת ה-hello שלה — לא בקריאה יזומה שלנו.
    wait_until(lambda: (ctx.builder.answer or {}).get("task", {})
               and ctx.builder.answer["task"].get("id") == task["id"],
               "מחשב הבנייה קיבל את המשימה ב-hello")
    check("המשימה גוברת על סבב", ctx.builder.answer["session"] is None)

    # ‏#737: בלי הכותרת הזו השרת מחזיר 401 על PUT /files (ועל המניפסט),
    # והשלב נעצר באימות בלי לבדוק את נתיב ההעלאה. הסוכן האמיתי שולח
    # אותה מ-`TASK_TOKEN` שחזר ב-hello — אותו מקור, אותה כותרת.
    token = ((ctx.builder.answer or {}).get("task") or {}).get("token") or ""
    check("המשימה נשאה אסימון ב-hello", bool(token),
          str((ctx.builder.answer or {}).get("task")))

    uploader = Client()          # ההעלאה במקביל ללולאת ה-hello של התהליכון
    uploader.headers["X-Imagesrv-Task-Token"] = token
    for filename, payload in files.items():
        status, _ = uploader.request(
            "PUT", f"/api/v1/capture/{task['id']}/files/{filename}",
            body=payload, ctype="application/octet-stream", raw=True)
        check(f"הועלה {filename}", status == 200, str(status))

    # אזור הביניים קיים *עכשיו*, באמצע הקליטה. בלי הביקורת החיובית הזו
    # הבדיקה "אזור הביניים נוקה" שלמטה היא glob ריק — והיא הייתה עוברת
    # גם אם התבנית ‏.capture-* לא קיימת בכלל, כלומר גם אם הניקוי נמחק.
    check("אזור ביניים נפתח לקליטה", bool(list(ctx.images_dir.glob(".capture-*"))),
          str(list(ctx.images_dir.iterdir())))

    first = next(iter(files.values()))
    # ‏#855: הדיווח נושא את אותה כותרת אסימון כמו ההעלאה (‏`uploader`),
    # והתשובה נבדקת — 403 כאן היה נבלע, והבדיקה הבאה נופלת רחוק מהסיבה.
    status, reply = uploader.json("POST", "/api/v1/agent/progress", {
        "task_id": task["id"], "mac": ctx.builder.mac, "state": "capturing",
        "targets": [{"dev": "sda", "bytes_written": len(first),
                     "bytes_total": sum(len(v) for v in files.values()),
                     "state": "capturing"}]})
    check("דיווח הקליטה התקבל עם האסימון", status == 200, str(reply))
    status, tasks = ctx.console.json("GET", "/api/console/tasks")
    row = next(t for t in tasks if t["id"] == task["id"])
    check("הקונסולה רואה התקדמות קליטה",
          row["state"] == "running" and row["bytes_written"] > 0, str(row))

    manifest = make_manifest(source_bytes, files)
    status, result = uploader.json(
        "PUT", f"/api/v1/capture/{task['id']}/manifest", manifest)
    # ‏#1470: ‏202 — המניפסט התקבל, מחשב הבנייה משתחרר, והאימות רץ ברקע.
    # האימג' בספרייה רק כשהמשימה `done`.
    check("המניפסט התקבל — האימות ברקע", status == 202, str(result))

    def task_state():
        _, rows = ctx.console.json("GET", "/api/console/tasks")
        return next(t for t in rows if t["id"] == task["id"])["state"]

    wait_until(lambda: task_state() != "verifying", "האימות ברקע הסתיים")
    check("האימות עבר", task_state() == "done", task_state())
    image_id = task["image_id"]
    check("האימג' על הדיסק", (ctx.images_dir / image_id / "manifest.json").is_file())
    check("אזור הביניים נוקה", not list(ctx.images_dir.glob(".capture-*")))
    wait_until(lambda: ctx.builder.answer and ctx.builder.answer.get("task") is None,
               "המשימה נמחקה בשרת — אין קליטה בלולאה")
    return image_id, manifest


def run(ctx) -> None:
    print("\n1. מחשב הבנייה — קליטה לספרייה")
    ctx.builder.start()
    ctx.image_a, ctx.manifest_a = _capture(
        ctx, "Windows 11 Base", "Office", FILES_A, source_bytes=GB256)
    status, listed = ctx.console.json("GET", "/api/console/images")
    check("האימג' בספרייה בשם שהוקלד",
          any(i["id"] == ctx.image_a and i["name"] == "Windows 11 Base"
              for i in listed), str(listed))

    # מגירה גדולה יותר הוכנסה למחשב הבנייה — האימג' השני ממשפחת 500.
    ctx.builder.disks = [disk("sda", GB500, "BLD-5")]
    ctx.image_b, ctx.manifest_b = _capture(
        ctx, "Windows 11 Lab 500", "Labs", FILES_B, source_bytes=GB500)

    print("\n2. הורדה והעלאה בין שרתים")
    status, tar = ctx.console.request(
        "GET", f"/api/console/images/{ctx.image_b}/download")
    check("האימג' הורד כ-tar", status == 200 and len(tar) > len(FILES_B["p3.windows.pcl.zst"]),
          str(status))
    # המחיקה חייבת להיבדק: בלעדיה "הועלה בחזרה" מוכיח רק שהאימג' נמצא
    # בספרייה — וזה היה נכון גם אילו המחיקה נכשלה וההעלאה לא עשתה דבר.
    status, deleted = ctx.console.json(
        "POST", f"/api/console/images/{ctx.image_b}/delete",
        {"confirm_name": "Windows 11 Lab 500"})
    check("האימג' נמחק מהספרייה", status == 200, str(deleted))
    status, listed = ctx.console.json("GET", "/api/console/images")
    check("והוא באמת איננו לפני ההעלאה",
          ctx.image_b not in {i["id"] for i in listed}, str(listed))

    status, restored = ctx.console.request(
        "POST", "/api/console/images/upload", body=tar,
        ctype="application/x-tar", raw=True)
    check("אותו tar הועלה בחזרה ואומת", status == 200,
          restored[:200].decode(errors="replace"))
    status, listed = ctx.console.json("GET", "/api/console/images")
    check("שני האימג'ים בספרייה",
          {ctx.image_a, ctx.image_b} <= {i["id"] for i in listed}, str(listed))
    ctx.builder.stop()      # תפקידו נגמר — פחות רעש רשת לשאר השלבים
