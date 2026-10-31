"""שלב 9ב — הפצה ישירה ממחשב הבנייה (‏#715): השרת מתזמר, המקור הוא הדיסק.

אותו שרת אמיתי, אותם תהליכוני מכונות. מחשב הבנייה מגלה משימת
`direct_send` ב-hello (נשלח מהשלב — התהליכון שלו נעצר אחרי הקליטה), מוסר את המניפסט (ולא בייט אחד),
ומדווח שידור; מחשב השיכפול רואה סבב `running` עם `image_id` חי, מושך את
המניפסט **באותו נתיב** כמו אימג' מהספרייה, ומדווח כרגיל. מה שנבדק:
ה-WoL רק לנבחר, הגל ממתין למניפסט, המנוע של השרת לא הופעל
(‏`send_delegated`), הסבב יחיד ונסגר עם הגל, המשימה נסגרת מדיווח המקור,
והספרייה בדיסק לא השתנתה.
"""

from __future__ import annotations

from .harness import (FILES_A, GB256, SENDER_PORTBASE, Client, check, hello,
                      make_manifest, wait_until)

#: מחשב הבנייה המדומה נעצר בסוף שלב הקליטה (פחות רעש); כאן ה-hello שלו
#: נשלח מהשלב עצמו, באותם ממשקים (2/3), עם הדיסקים שהוא דיווח.
_builder = Client()


def _room(ctx) -> dict:
    if ctx.cloner.error is not None:
        raise SystemExit(f"FAILED: room heartbeat stopped: {ctx.cloner.error}")
    status, view = ctx.deploy.json("GET", "/api/console/room")
    if status != 200:
        raise SystemExit(f"FAILED: מסך החדר החזיר {status}: {view}")
    return view


def _task(ctx) -> dict | None:
    status, answer = hello(_builder, ctx.builder.mac, ctx.builder.disks)
    if status != 200:
        raise SystemExit(f"FAILED: hello של מחשב הבנייה החזיר {status}: {answer}")
    ctx.builder.answer = answer
    return answer.get("task")


def run(ctx) -> None:
    print("\n9ב. הפצה ישירה — מחשב הבנייה משדר מהדיסק שלו")
    library_before = sorted(p.name for p in ctx.images_dir.iterdir())
    # רק שתי מגירות מהשלוש נבחרות (חריצים 1 ו-2 = sdb, sdc); המכונה המדומה
    # כותבת בדיוק אותן, כמו הסוכן שמכבד את target_ports (#695/#701).
    ctx.cloner.write_plans = [[("sdb", "done"), ("sdc", "done")]]
    wait_until(lambda: _room(ctx)["round"] is None, "אין סבב חדר פעיל לפני השלב")

    status, opened = ctx.deploy.json("POST", "/api/console/room", {
        "source": {"kind": "build_disk", "mac": ctx.builder.mac, "disk": "sda"},
        "target_slots": [{"mac": ctx.cloner.mac, "ports": [1, 2]}],
    })
    check("סבב ישיר נפתח ממחשב הבנייה (2 מגירות נבחרו)", status == 200, str(opened))
    check("המזהה חי — לא אימג' בספרייה", str(opened.get("image_id", "")).startswith("live_"),
          str(opened))
    view = _room(ctx)
    check("המסך אומר שהמקור הוא מחשב הבנייה",
          view["round"]["source"]["kind"] == "build_disk"
          and view["round"]["source"]["disk"] == "sda"
          and view["round"]["source"]["manifest_ready"] is False, str(view["round"]))
    check("היעד הוא בדיוק הסט הנבחר — סבב יחיד",
          view["round"]["target_drives"] == 2, str(view["round"]))

    # מחשב הבנייה מגלה את המשימה בלולאת ה-hello שלו.
    wait_until(lambda: (_task(ctx) or {}).get("id") == opened["task_id"],
               "מחשב הבנייה קיבל משימת direct_send ב-hello")
    task = _task(ctx)
    check("המשימה היא direct_send על הדיסק שנבחר",
          task["type"] == "direct_send" and task["disk"] == "sda"
          and task["image_id"] == opened["image_id"], str(task))
    check("פרמטרי השידור הם של השרת (portbase של הסימולציה)",
          task["direct"]["multicast"]["portbase"] == SENDER_PORTBASE
          and task["direct"]["session_id"] == opened["wave_session_id"], str(task["direct"]))

    # מחשב השיכפול מצטרף — והגל **לא** יוצא, כי המניפסט טרם הגיע.
    wait_until(lambda: _room(ctx)["round"]["ready_drives"] == 2,
               "מחשב השיכפול הצטרף ושתי המגירות הנבחרות מוכנות")
    status, refused = ctx.deploy.json("POST", "/api/console/room/start")
    check("'שלח עכשיו' נדחה לפני המניפסט (409)", status == 409, str(refused))
    check("הגל נשאר פתוח בלי מניפסט", _room(ctx)["round"]["wave_state"] == "open",
          str(_room(ctx)["round"]))
    status, _ = ctx.deploy.json("GET", f"/api/v1/images/{opened['image_id']}/manifest")
    check("המניפסט החי עדיין אינו מוגש (404)", status == 404, str(status))

    # המקור מוסר את המניפסט בלבד — עם האסימון שהגיע ב-hello.
    source = Client()
    source.headers["X-Imagesrv-Task-Token"] = task["token"]
    manifest = make_manifest(GB256, FILES_A)
    status, result = source.json(
        "PUT", f"/api/v1/direct/{opened['task_id']}/manifest", manifest)
    check("המניפסט החי התקבל", status == 200, str(result))
    status, served = ctx.deploy.json("GET", f"/api/v1/images/{opened['image_id']}/manifest")
    check("המקבל מושך את המניפסט החי באותו נתיב כמו אימג' מהספרייה",
          status == 200 and served["partitions"][0]["sha256"]
          == manifest["partitions"][0]["sha256"], str(status))

    # הדופק הבא מוציא את הגל — ומחשב הבנייה רואה running.
    wait_until(lambda: _room(ctx)["round"] is None
               or _room(ctx)["round"]["wave_state"] == "running",
               "הגל יצא מעצמו אחרי המניפסט")
    wait_until(lambda: ((_task(ctx) or {}).get("direct") or {}).get("session_state")
               in ("running", "closed") or _task(ctx) is None,
               "מחשב הבנייה רואה session_state=running")
    status, reply = source.json("POST", "/api/v1/agent/progress", {
        "task_id": opened["task_id"], "mac": ctx.builder.mac, "state": "sending",
        "targets": [{"dev": "sda", "bytes_written": 1024,
                     "bytes_total": manifest["total_compressed_bytes"],
                     "state": "waiting"}]})
    check("דיווח 'sending' של המקור התקבל", status == 200, str(reply))
    status, reply = source.json("POST", "/api/v1/agent/progress", {
        "task_id": opened["task_id"], "mac": ctx.builder.mac, "state": "done",
        "targets": [{"dev": "sda", "bytes_written": manifest["total_compressed_bytes"],
                     "bytes_total": manifest["total_compressed_bytes"], "state": "done"}]})
    check("הדיווח הסופי של המקור התקבל", status == 200, str(reply))

    # הסבב יחיד: הגל שנגמר סוגר אותו — אין גל שני.
    wait_until(lambda: _room(ctx)["round"] is None,
               "הסבב הישיר נסגר עם הגל (2/2, בלי גל שני)")
    status, tasks = ctx.console.json("GET", "/api/console/tasks")
    row = next(t for t in tasks if t["id"] == opened["task_id"])
    check("משימת המקור הסתיימה done — מדיווח מחשב הבנייה", row["state"] == "done", str(row))
    wait_until(lambda: _task(ctx) is None,
               "מחשב הבנייה חופשי — המשימה אינה מוצעת שוב")

    status, rows = ctx.console.json("GET", "/api/console/journal")
    events = [r["event"] for r in rows]
    check("היומן: הגל הואצל למחשב הבנייה ולא למנוע של השרת",
          "send_delegated" in events, str(sorted(set(events))))
    check("היומן: room_open_direct + direct_manifest + direct_done + room_done",
          all(e in events for e in ("room_open_direct", "direct_manifest",
                                    "direct_done", "room_done")), str(sorted(set(events))))
    started = [r for r in rows if r["event"] == "send_start"]
    delegated = [r for r in rows if r["event"] == "send_delegated"]
    check("send_start לא נרשם על הגל הישיר",
          not any(opened["wave_session_id"] in (r.get("text") or r.get("detail") or "")
                  for r in started) and len(delegated) >= 1,
          f"started={[r.get('text') for r in started]}")
    library_after = sorted(p.name for p in ctx.images_dir.iterdir())
    check("שום דבר לא נכתב לספרייה בשרת", library_after == library_before,
          f"{library_before} -> {library_after}")
