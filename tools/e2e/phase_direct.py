"""שלב 9ב — הפצה ישירה ממחשב הבנייה (#715) במנוע החתיכות (#3161, #3173): השרת מתזמר, המקור הוא הדיסק.

אותו שרת אמיתי, אותם תהליכוני מכונות. מחשב הבנייה מגלה משימת `direct_send` ב-hello (נשלח
מהשלב — התהליכון שלו נעצר אחרי הקליטה), ו-`task.direct.engine` אומר לו "העלה אינדקסים":
לפני המניפסט הוא מעלה לכל מחיצה את האינדקס, את רשימת ה-BLAKE3 ואת פריים הראש — לא בייט
אחד של האימג'. השרת חותם על המניפסט רק כשה-`index_xxh64` וה-`chunk_b3` שבו הם של מה שהועלה
(`directchunk.seal`, ה-BLAKE3 נבדק ב-`chunkcast b3sum` של השרת). אחר כך המנוע של השרת מתזמר
את הגל, אבל **אינו משדר בעצמו**: לכל מעבר הוא נותן למקור הזמנה (`task.direct.chunk.order`),
והמקור מדווח עליה (`chunk-sent`). מחשב השיכפול רואה גל `running` עם `image_id` חי, ועובר
אותו באותו `chunk-pass` כמו גל מהספרייה. מה שנבדק עוד: ה-WoL רק לנבחר, הגל ממתין למניפסט,
הסבב יחיד ונסגר עם הגל, המשימה נסגרת מדיווח המקור, והספרייה בדיסק לא השתנתה.
"""

from __future__ import annotations

from server.library import chunkformat

from .harness import (CHUNKED, FILES_A, GB256, Client, b3_list, b3_sum, check, hello,
                      make_manifest, sent_commands, wait_until)

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


def _source_files() -> dict[int, dict]:
    """מה שמחשב הבנייה מחזיק אחרי הקריאה הראשונה של הדיסק, לכל מחיצה בזרם: האינדקס
    (`chunkcut`), רשימת ה-BLAKE3 של זרם הנתונים (`chunkcast b3list`), ופריים הראש."""
    out = {}
    for number, name in ((1, "p1.esp.pcl.zst"), (3, "p3.windows.pcl.zst")):
        raw = FILES_A[name]
        index = CHUNKED[raw]["index"]
        parsed = chunkformat.parse_index(index)
        listed = b3_list(CHUNKED[raw]["data"])
        out[number] = {"index": index.encode("ascii"), "b3": listed, "chunk_b3": b3_sum(listed),
                       "head": raw[:parsed.head_zbytes], "chunks": parsed.chunks}
    return out


class _Source:
    """המקור: מבצע כל הזמנה פעם אחת ומדווח עליה (`chunk-sent`), עד שהשרת אומר `chunk.done`."""

    def __init__(self, ctx, client: Client, task_id: str):
        self.ctx, self.client, self.task_id = ctx, client, task_id
        self.orders: list[dict] = []
        self.answers: list[tuple[int, dict]] = []

    def step(self) -> bool:
        direct = (_task(self.ctx) or {}).get("direct") or {}
        chunk = direct.get("chunk") or {}
        order = chunk.get("order")
        if order and order["seq"] not in {o["seq"] for o in self.orders}:
            for body in ({"seq": order["seq"], "state": "sending"},
                         {"seq": order["seq"], "state": "done", "rc": 0}):
                status, answer = self.client.json("POST", f"/api/v1/direct/{self.task_id}/chunk-sent", body)
                self.answers.append((status, answer))
            self.orders.append(order)           # אחרי הדיווח: מכאן המקלט יודע שהמעבר שודר
        return chunk.get("done") is True

    def sent(self, session: dict, part: int) -> bool:
        return any(o["part"] == part and o["key"] == session["chunk"]["key"] for o in list(self.orders))


def run(ctx) -> None:
    print("\n9ב. הפצה ישירה — מחשב הבנייה משדר מהדיסק שלו, במנוע החתיכות")
    library_before = sorted(p.name for p in ctx.images_dir.iterdir())
    sends_before = len(sent_commands(ctx.data_dir))
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
    check("לפני המניפסט: השרת אומר למקור 'מנוע החתיכות — העלה אינדקסים', ואין עדיין הזמנה",
          task["direct"].get("engine") == "chunk" and "chunk" not in task["direct"]
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

    # המקור מעלה, לפני המניפסט ועם האסימון שהגיע ב-hello: אינדקס, רשימת BLAKE3 וראש לכל מחיצה.
    source = Client()
    source.headers["X-Imagesrv-Task-Token"] = task["token"]
    files = _source_files()
    uploads = []
    for number, got in files.items():
        for what, body in (("index", got["index"]), ("b3", got["b3"]), ("head", got["head"])):
            status, answer = source.request("PUT", f"/api/v1/direct/{opened['task_id']}/{what}/{number}",
                                            body, ctype="application/octet-stream", raw=True)
            uploads.append((number, what, status, answer[:200]))
    check("המקור העלה אינדקס, רשימת BLAKE3 ופריים ראש לכל מחיצה (200)",
          [(n, w, s) for n, w, s, _a in uploads] == [(n, w, 200) for n in files for w in ("index", "b3", "head")],
          str(uploads))

    manifest = make_manifest(GB256, FILES_A)
    wrong = {**manifest, "partitions": [dict(p) for p in manifest["partitions"]]}
    for part in wrong["partitions"]:
        part["chunk_b3"] = "ab" * 32
    status, result = source.json("PUT", f"/api/v1/direct/{opened['task_id']}/manifest", wrong)
    check("מניפסט שה-chunk_b3 שלו אינו של הרשימה שהועלתה נדחה בשם (400)",
          status == 400 and "chunk_b3" in str(result), f"{status} {result}")
    # הסירוב הכשיל את המשימה ואת הסבב (`test_direct_chunks_3161`) — זה הסבב הבא, נכון מתחילתו.
    wait_until(lambda: (r := _room(ctx)["round"]) is not None and r["id"] == opened["id"]
               and r["state"] == "failed" and "chunk_b3" in (r.get("failed_reason") or ""),
               "הסבב עם המניפסט השגוי נכשל בגלוי, והסיבה במסך (chunk_b3)",
               detail=lambda: str(_room(ctx)["round"])[:1500])
    status, tasks = ctx.console.json("GET", "/api/console/tasks")
    row = next(t for t in tasks if t["id"] == opened["task_id"])
    check("המשימה של המניפסט השגוי נכשלה — לא נשארה תלויה", row["state"] == "failed", str(row))
    wait_until(lambda: _task(ctx) is None, "מחשב הבנייה חופשי אחרי הסירוב")
    _wave(ctx, files, library_before, sends_before)


def _wave(ctx, files: dict[int, dict], library_before: list[str], sends_before: int) -> None:
    status, opened = ctx.deploy.json("POST", "/api/console/room", {
        "source": {"kind": "build_disk", "mac": ctx.builder.mac, "disk": "sda"},
        "target_slots": [{"mac": ctx.cloner.mac, "ports": [1, 2]}],
    })
    check("סבב ישיר שני נפתח", status == 200, str(opened))
    wait_until(lambda: (_task(ctx) or {}).get("id") == opened["task_id"],
               "מחשב הבנייה קיבל את המשימה השנייה")
    task = _task(ctx)
    source = Client()
    source.headers["X-Imagesrv-Task-Token"] = task["token"]
    for number, got in files.items():
        for what in ("index", "b3", "head"):
            source.request("PUT", f"/api/v1/direct/{opened['task_id']}/{what}/{number}",
                           got[what], ctype="application/octet-stream", raw=True)
    manifest = make_manifest(GB256, FILES_A)
    for part in manifest["partitions"]:
        part["chunk_b3"] = files[part["index"]]["chunk_b3"]
    status, result = source.json("PUT", f"/api/v1/direct/{opened['task_id']}/manifest", manifest)
    check("המניפסט החי התקבל: הוא חותם על האינדקסים ועל רשימות ה-BLAKE3 שהועלו", status == 200, str(result))
    status, served = ctx.deploy.json("GET", f"/api/v1/images/{opened['image_id']}/manifest")
    check("המקבל מושך את המניפסט החי באותו נתיב כמו אימג' מהספרייה, עם מספר החתיכות מהאינדקס",
          status == 200 and [(p["sha256"], p.get("readback_chunks")) for p in served["partitions"]]
          == [(p["sha256"], files[p["index"]]["chunks"]) for p in manifest["partitions"]], str(served))
    # הדופק הבא מוציא את הגל — והמקור מקבל הזמנה לכל מעבר, ומדווח עליה.
    sender = _Source(ctx, source, opened["task_id"])
    ctx.cloner.pass_sent = sender.sent
    wait_until(lambda: (r := _room(ctx)["round"]) is None or r["wave_state"] == "running",
               "הגל יצא מעצמו אחרי המניפסט")
    wait_until(sender.step, "השרת אומר למקור שכל מחיצה שלמה על המגירות (`chunk.done`)", timeout=30,
               detail=lambda: f"orders={sender.orders} answers={sender.answers} parts={ctx.cloner.chunk_parts}")
    # חבר בגל (#997: קבצים מוגשים רק למי שיש לו סיבה) — מה שמקלט מוריד לפני שהוא כותב
    status, index = ctx.cloner.client.request("GET", f"/api/v1/images/{opened['image_id']}/index/3")
    status_b3, listed = ctx.cloner.client.request("GET", f"/api/v1/images/{opened['image_id']}/b3/3")
    check("המקלטים מקבלים בדיוק את האינדקס ואת רשימת ה-BLAKE3 שהמקור העלה",
          (status, status_b3) == (200, 200) and index == files[3]["index"] and listed == files[3]["b3"],
          f"{status} {status_b3}")

    wave = next((w for w in ctx.cloner.waves if w["id"] == opened["wave_session_id"]), None)
    check("המשכפל ראה את הגל הישיר כגל של מנוע החתיכות",
          wave is not None and wave.get("engine") == "chunk", str(ctx.cloner.waves[-1:]))
    check("הזמנה אחת לכל מחיצה, מעבר 1, כל המחיצה — אל הקבוצה והפורט שהמשכפל קיבל",
          [(o["part"], o["pass"], o["chunks"]) for o in sender.orders] == [(1, 1, False), (3, 1, False)]
          and all((o["group"], o["port"], o["key"]) == (wave["chunk"]["group"], wave["chunk"]["port"],
                                                        wave["chunk"]["key"]) for o in sender.orders)
          and all(o["kbps"] > 0 and o["write_kbps"] > 0 for o in sender.orders), str(sender.orders))
    check("כל דיווח `chunk-sent` של המקור התקבל",
          len(sender.answers) == 4 and all(s == 200 and a.get("ok") and not a.get("stale")
                                           for s, a in sender.answers), str(sender.answers))
    # המנוע סגר את המחיצה לפני שהמשכפל שאל שוב ושמע `done` — "בסוף זה יקרה", לא "קרה עכשיו"
    wait_until(lambda: [p for s, p in ctx.cloner.chunk_parts if s == opened["wave_session_id"]] == [1, 3],
               "המשכפל סגר את שתי המחיצות של הגל הישיר מול המנוע",
               detail=lambda: str(ctx.cloner.chunk_parts))

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

    status, rows = ctx.console.json("GET", "/api/console/journal?limit=1000")
    events = [r["event"] for r in rows]
    status, rows = ctx.console.json("GET", "/api/console/journal?event=send_delegated&limit=1000")
    delegated = [r for r in rows if r["round_id"] == opened["wave_session_id"]]
    check("היומן: הגל הישיר הואצל למחשב הבנייה, פעם אחת", len(delegated) == 1, str(delegated))
    check("היומן: room_open_direct + direct_manifest + direct_done + room_done",
          all(e in events for e in ("room_open_direct", "direct_manifest",
                                    "direct_done", "room_done")), str(sorted(set(events))))
    check("השרת לא הריץ `chunkcast send` בעצמו על הגל הישיר — רק המקור שידר",
          len(sent_commands(ctx.data_dir)) == sends_before, str(sent_commands(ctx.data_dir)[sends_before:]))
    library_after = sorted(p.name for p in ctx.images_dir.iterdir())
    check("שום דבר לא נכתב לספרייה בשרת", library_after == library_before,
          f"{library_before} -> {library_after}")
