"""שלב 9ג — גל במנוע החתיכות עם מגירה מאחרת והשלמה ב-HTTP (#3546).

שלבים 9 ו-9ב מריצים את מנוע החתיכות עם מקלט ש"קיבל הכול": `chunk-pass` של כל מגירה מדווח את כל
החתיכות, והתשובה חייבת להיות `done` (`machines.SimMachine._chunk_wave`). נתיב ההשלמה — מגירה שחסרה
לה חתיכה, `fetch` של בדיוק הטווח הזה, הורדה ב-HTTP Range, ויומן ההשלמה של השרת — לא נבדק בסימולציה.
כאן הוא נבדק.

**מה אמיתי כאן:** השרת (אותו תהליך של שאר השלבים) והמנוע שלו: האינדקס שהשרת גזר מהאימג' שנקלט,
שער הזהות, בחירת ההשלמה (HTTP בלבד או מולטיקאסט), יומן ההשלמה (`chunk_ledger`) וסגירת הסבב.
**מה מדומה:** השולח (`simsend`, כמו בשאר השלבים: המנוע מריץ את `chunkcast send` שלו, ושום חבילה אינה
יוצאת) והמקלט — כל מכונה מדווחת ב-`chunk-pass` מה שעמיד אצלה, כמו הסוכן. מגירה מאחרת היא מגירה
שהדיווח שלה חסר חתיכה 0 של מחיצה 3; ההשלמה שלה ב-HTTP בלבד, והבייטים שירדו נבדקים מול הקובץ
שהסימולציה עצמה בנתה. השלמה במולטיקאסט (חוסר משותף לשתי מכונות) אינה כאן: אין שם בייטים שאפשר לבדוק.
הסוכן האמיתי מול `chunkcast` אמיתי נבדק ב-tests/test_chunk_e2e_2533.py.

מה שנבדק, כראיה חיובית: השרת עונה לה `fetch` של בדיוק הטווח שחסר ← הבייטים שהורדו שווים לטווח
בקובץ המקור ← היומן של השרת מחזיק את כל החתיכות של כל מגירה (`written=N/N`) ← הסבב נסגר 3/3.
"""

from __future__ import annotations

import random
import sqlite3
import time

from .harness import (BUILD_MAC, CHUNK_MACS, GB256, Client, check, chunk_file, disk, hello, make_manifest,
                      sent_commands, wait_until)
from .machines import SimMachine

#: שלוש מגירות על שתי מכונות. ה-MAC-ים כתובים ב-`harness.SIM_MACS`, כדי שיהיו להם חכירות.
EARLY_MAC, LATE_MAC = CHUNK_MACS
#: מחיצה 3 היא ~9MiB = שלוש חתיכות של 4MiB; מחיצה 1 חתיכה אחת. תוכן שונה בכל חתיכה, כדי
#: שטווח שירד מהמקום הלא נכון לא יהיה שווה לטווח שהתבקש.
_PAYLOAD = random.Random(3546).randbytes(9 << 20)
FILES_C = {"p1.esp.pcl.zst": chunk_file(random.Random(3547).randbytes(1 << 20)),
           "p3.windows.pcl.zst": chunk_file(_PAYLOAD)}
#: החתיכות שהמגירה המאחרת פספסה במחיצה 3 (היא התחילה להאזין אחרי שהן יצאו).
LATE_MISSED = 1


# --- מכונה מדומה שמדברת במנוע החתיכות -----------------------------------------------------------


class ChunkMachine(SimMachine):
    """משכפל שמדווח `chunk-pass` כמו הסוכן, ומשלים ב-HTTP מה שפספס.

    `missed` = {מחיצה: כמה חתיכות מההתחלה היא פספסה בשידור הראשון} (0 = קלטה הכול). את מה שחסר
    לה השרת משלים, והיא מורידה ב-HTTP ובודקת מול `source`. ההתנהגות כולה בתוך `_chunk_wave`,
    שהוא מה ש-`SimMachine._write` קורא לו; ה-hello והדיווח הסופי הם של `SimMachine`."""

    def __init__(self, mac, disks, *, missed: dict[int, int], image_id: str, source: dict[int, bytes],
                 file_names: dict[int, str]):
        super().__init__(mac, disks)
        self.missed, self.image_id = missed, image_id
        self.held: dict[int, list[list[int]]] = {}
        self.index: dict[int, list[tuple[int, int]]] = {}
        self.source, self.file_names = source, file_names
        self.fetched: dict[int, list[tuple[str, bool]]] = {}     # מחיצה → [(טווח, הבייטים שווים למקור)]
        self.actions: list[tuple[int, str]] = []                 # (מחיצה, פעולה) שהשרת ענה, לפי הסדר

    def _post(self, session: dict, part: int, rnd: int, state: str, **more) -> dict:
        status, answer = self.client.json("POST", "/api/v1/agent/chunk-pass", {
            "mac": self.mac, "session_id": session["id"], "part": part, "pass": rnd, "state": state,
            "targets": [{"serial": d["serial"], "write_mbps": 150, "failed": False,
                         "durable": [{"part": part, "ranges": [] if state == "listening" else self.held[part]}]}
                        for d in self.disks], **more})
        if status != 200:
            raise RuntimeError(f"chunk-pass של {self.mac} (מחיצה {part}, {state}) החזיר {status}: {answer}")
        return answer

    def _chunk_wave(self, session: dict, plan: list[tuple]) -> None:
        for part in sorted(self.missed):
            self.index[part] = _index(self.client, self.image_id, part)
            n = len(self.index[part])
            self.held[part] = [[self.missed[part], n - 1]] if self.missed[part] < n else []
        for part in sorted(self.missed):
            answer = self._post(session, part, 1, "listening")
            if answer.get("action") != "receive":
                raise RuntimeError(f"{self.mac}: האזנה למחיצה {part} נענתה {answer}")
            self._wait_pass_sent(session, part)
            rnd, deadline = 1, time.time() + 120
            while True:
                answer = self._post(session, part, rnd, "ended")
                action = answer.get("action")
                self.actions.append((part, action))
                if action == "done":
                    break
                if action == "fetch":
                    rnd = answer["pass"]
                    self._fetch(session, part, rnd, answer["ranges"])
                elif action != "wait":
                    raise RuntimeError(f"{self.mac}: מחיצה {part} נענתה {answer} (המעבר בהשלמה במולטיקאסט אינו כאן)")
                if time.time() > deadline:
                    raise RuntimeError(f"{self.mac}: מחיצה {part} לא נסגרה תוך 120 שניות; האחרון: {answer}")
                time.sleep(max(0.3, float(answer.get("poll", 0) or 0) / 4))

    def _wait_pass_sent(self, session: dict, part: int) -> None:
        """המקלט האמיתי מדווח `ended` כשהמעבר הגיע אליו. כאן: כשהמנוע של השרת הריץ את `chunkcast send`
        של המחיצה (`simsend` רשם אותו) -- כמו `pass_sent` של שלבים 9 ו-9ב."""
        deadline = time.time() + 90
        while time.time() < deadline:
            if self.pass_sent(session, part):
                return
            time.sleep(0.3)
        raise RuntimeError(f"{self.mac}: השרת לא שידר את מחיצה {part} תוך 90 שניות")

    def _fetch(self, session: dict, part: int, rnd: int, ranges: list[list[int]]) -> None:
        """מורידה כל טווח חתיכות ב-HTTP Range, בודקת מול המקור, ואומרת שעמיד אצלה."""
        for first, last in ranges:
            start = self.index[part][first][0]
            end = self.index[part][last][0] + self.index[part][last][1] - 1
            http = Client(self.client.base, self.client.console_base)
            http.headers["Range"] = f"bytes={start}-{end}"
            status, body = http.request("GET", f"/api/v1/images/{self.image_id}/files/{self.file_names[part]}")
            equal = status == 206 and body == self.source[part][start:end + 1]
            self.fetched.setdefault(part, []).append((f"{first}-{last}", equal))
            if not equal:
                raise RuntimeError(f"{self.mac}: טווח {first}-{last} של מחיצה {part}: {status}, הבייטים אינם של המקור")
            self.held[part] = _union(self.held[part], [[first, last]])
        self._post(session, part, rnd, "fetching")


def _union(a: list[list[int]], b: list[list[int]]) -> list[list[int]]:
    out: list[list[int]] = []
    for lo, hi in sorted(a + b):
        if out and lo <= out[-1][1] + 1:
            out[-1][1] = max(out[-1][1], hi)
        else:
            out.append([lo, hi])
    return out


# --- הקליטה והשלב -----------------------------------------------------------------------------------


def _capture_c(ctx) -> str:
    """האימג' נקלט כמו שמחשב בנייה שולח אותו: משימה, אסימון, קבצים, מניפסט — ללא עקיפה. מחשב הבנייה
    המדומה של שלב 3 כבר נעצר (`phase_capture`), ולכן ה-hello שלו כאן הוא של הלקוח הזה, על אותו דיסק."""
    client = Client()
    status, task = ctx.console.json("POST", "/api/console/tasks/capture", {
        "mac": BUILD_MAC, "disk": "sda", "name": "Chunk Wave", "description": "chunk wave", "folder": "Chunk"})
    check("קליטה לגל החתיכות: משימה נוצרה", status == 200, str(task))

    def hello_task():
        _s, answer = hello(client, BUILD_MAC, ctx.builder.disks)
        return answer.get("task")
    got = wait_until(lambda: (t := hello_task()) and t.get("id") == task["id"] and t,
                     "מחשב הבנייה קיבל את משימת הקליטה של גל החתיכות")
    uploader = Client()
    uploader.headers["X-Imagesrv-Task-Token"] = got["token"]
    for name, payload in FILES_C.items():
        status, _ = uploader.request("PUT", f"/api/v1/capture/{task['id']}/files/{name}",
                                     body=payload, ctype="application/octet-stream", raw=True)
        check(f"הועלה {name} ({len(payload) // 1024}KiB)", status == 200, str(status))
    status, result = uploader.json("PUT", f"/api/v1/capture/{task['id']}/manifest", make_manifest(GB256, FILES_C))
    check("המניפסט של גל החתיכות התקבל", status == 202, str(result))

    def state():
        return next(t for t in ctx.console.json("GET", "/api/console/tasks")[1] if t["id"] == task["id"])["state"]
    wait_until(lambda: state() != "verifying", "האימות של האימג' ברקע הסתיים")
    check("האימות עבר", state() == "done", state())
    image_id = task["image_id"]

    def derived():
        _status, detail = ctx.console.json("GET", f"/api/console/images/{image_id}")
        parts = {p["index"]: p for p in detail.get("partitions", [])}
        check("השרת לא דיווח שגיאת אינדקס", not any(p.get("index_error") for p in parts.values()), str(parts))
        return all(isinstance(parts[n].get("readback_chunks"), int) for n in (1, 3))
    wait_until(derived, "השרת גזר את האינדקס של שתי המחיצות")
    return image_id


def _index(client: Client, image_id: str, part: int) -> list[tuple[int, int]]:
    """(היסט, אורך) של כל חתיכה — מהאינדקס שהשרת מגיש לחברי סבב של האימג'. נקרא מתוך תהליכון
    של מכונה, ולכן זורק (ולא `check`)."""
    status, body = client.request("GET", f"/api/v1/images/{image_id}/index/{part}")
    if status != 200:
        raise RuntimeError(f"האינדקס של מחיצה {part} לא הוגש לחבר בסבב: {status}")
    rows = body.decode("ascii").splitlines()[1:]
    return [(int(r.split()[0]), int(r.split()[1])) for r in rows if r.strip()]


def run(ctx) -> None:
    print("\n9ג. מנוע החתיכות — גל עם מגירה מאחרת והשלמה ב-HTTP (#3546)")
    image_id = _capture_c(ctx)
    for mac, name in ((EARLY_MAC, "03"), (LATE_MAC, "04")):
        status, _ = ctx.console.json("POST", "/api/console/machines", {"mac": mac, "name": name, "group_id": "grp_CLONERS"})
        check(f"משכפל {name} נרשם", status == 200, str(status))

    early_disks = [disk("sda", GB256, "CH-A1", port=1), disk("sdb", GB256, "CH-A2", port=2)]
    late_disks = [disk("sda", GB256, "CH-B1", port=1)]
    # מי שמגיע ראשון רואה את הסבב נפתח; כאן שתיהן ערות לפניו (כמו בחדר).
    probe = Client()
    for mac, disks_ in ((EARLY_MAC, early_disks), (LATE_MAC, late_disks)):
        hello(probe, mac, disks_)
    status, opened = ctx.deploy.json("POST", "/api/console/room", {
        "image_id": image_id, "target_drives": 3,
        "target_slots": [{"mac": EARLY_MAC, "ports": [1, 2]}, {"mac": LATE_MAC, "ports": [1]}]})
    check("סבב חדר נפתח על מנוע החתיכות (3 מגירות, שתי מכונות)", status == 200, str(opened))

    names = {1: "p1.esp.pcl.zst", 3: "p3.windows.pcl.zst"}
    source = {part: FILES_C[names[part]] for part in (1, 3)}
    early = ChunkMachine(EARLY_MAC, early_disks, missed={1: 0, 3: 0}, image_id=image_id,
                         source=source, file_names=names)
    late = ChunkMachine(LATE_MAC, late_disks, missed={1: 0, 3: LATE_MISSED}, image_id=image_id,
                        source=source, file_names=names)
    machines = (early, late)
    for m in machines:
        m.write_plans = [[(d["dev"], "done") for d in m.disks]]
        # המעבר שודר = המנוע של השרת הריץ את `chunkcast send` שלו (`simsend` רשם אותו), כמו בשלב 9
        m.pass_sent = lambda session, part: any(
            c[c.index("--session") + 1] == session["chunk"]["key"] and c[c.index("--part") + 1] == str(part)
            for c in sent_commands(ctx.data_dir))
        m.start()
    ctx.stations.update({m.mac: m for m in machines})      # ש-main יעצור אותם ויראה אם תהליכון נפל

    def done():
        for m in machines:
            if m.error is not None:
                raise SystemExit(f"FAILED: תהליכון {m.mac} נפל: {m.error}")
        status, view = ctx.deploy.json("GET", "/api/console/room")
        return status == 200 and view["round"] is None
    wait_until(done, "הסבב של מנוע החתיכות נסגר (3/3)", timeout=120)

    # ראיה חיובית, לא היעדר שגיאה: מי הושלם, במה, ובאיזה בייטים.
    sizes = early.index
    check("האינדקס של שתי המחיצות הוגש לחברי הסבב (200)", sorted(sizes) == [1, 3], str(sizes))
    check("מחיצה 3 היא שלוש חתיכות, מחיצה 1 חתיכה אחת", [len(sizes[1]), len(sizes[3])] == [1, 3], str(sizes))
    check("המגירה המאחרת קיבלה fetch של בדיוק החתיכה שפספסה",
          late.fetched.get(3) == [(f"0-{LATE_MISSED - 1}", True)], str(late.fetched))
    check("המכונה שקלטה הכול לא הורידה דבר", early.fetched == {}, str(early.fetched))
    check("המאחרת הורידה רק במחיצה 3", sorted(late.fetched) == [3], str(late.fetched))
    check("שתיהן קיבלו done בכל מחיצה",
          all((p, "done") in m.actions for m in machines for p in (1, 3)), str([m.actions for m in machines]))
    # היומן נקרא מה-DB (כמו tests/test_chunk_e2e_2533.py): `detail` הוא מה שהמנוע כתב, לא התרגום לקונסולה.
    conn = sqlite3.connect(f"file:{ctx.data_dir / 'imagesrv.db'}?mode=ro", uri=True)
    try:
        lines = [f"{event} {detail}" for event, detail in conn.execute(
            "SELECT event, detail FROM journal WHERE event IN ('chunk_repair', 'chunk_disk_failed', 'send_failed',"
            " 'send_done', 'send_start') ORDER BY rowid")]
    finally:
        conn.close()
    check("היומן: send_start של מנוע החתיכות", any(l.startswith("send_start") and "engine=chunk" in l for l in lines),
          str(lines[-12:]))
    repairs = [l for l in lines if l.startswith("chunk_repair")]
    check("היומן: השלמה אחת, ב-HTTP בלבד (multicast=0), למאחרת",
          len(repairs) == 1 and "multicast=0 " in repairs[0] and LATE_MAC in repairs[0], str(repairs))
    check("היומן: אין כישלון שידור או דיסק",
          not [l for l in lines if l.startswith(("send_failed", "chunk_disk_failed"))], str(lines[-12:]))
    check("היומן: הגל נסגר send_done", any(l.startswith("send_done") for l in lines), str(lines[-12:]))
    _ledger(ctx, sizes, ["CH-A1", "CH-A2", "CH-B1"])


def _ledger(ctx, sizes: dict, serials: list[str]) -> None:
    """`chunk_ledger` של השרת: לכל מגירה, בכל מחיצה, כל החתיכות עמידות — `written=N/N`, מהיומן ולא מהדיווח האחרון."""
    conn = sqlite3.connect(f"file:{ctx.data_dir / 'imagesrv.db'}?mode=ro", uri=True)
    try:
        for serial in serials:
            rows = {r[0]: r[1] for r in conn.execute("SELECT part, ranges_json FROM chunk_ledger WHERE serial = ?", (serial,))}
            want = {part: f"[[0, {len(sizes[part]) - 1}]]" for part in (1, 3)}
            got = {part: rows.get(part, "").replace(" ", "") for part in (1, 3)}
            check(f"יומן השרת: {serial} מחזיק את כל החתיכות של שתי המחיצות (written=N/N)",
                  got == {p: w.replace(" ", "") for p, w in want.items()}, f"{rows}")
    finally:
        conn.close()
