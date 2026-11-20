"""גל של מנוע החתיכות אינו יוצא לפני שהאינדקס של האימג' שלו מוכן (#3137).

המנוע (`chunksend`) משדר רק מחיצה שיש לה אינדקס מאומת ליד הקובץ, והמקלט מוריד את
האינדקס הזה (`GET …/index/<n>`) לפני שהוא מאזין. אימג' שנקלט זה עתה (או הועתק, יובא,
סונכרן או שוחזר מגיבוי בלי ה-`.idx`) עוד אין לו אינדקס: השרת גוזר אותו ברקע
(`readbackhash`). עד #3137 גל שנפתח מיד אחרי קליטה יצא לדרך, והמנוע הכשיל אותו אחרי
13 שניות ("עדיין נבנה אחרי הקליטה"), כשהגזירה עצמה הייתה מסתיימת 30 שניות אחר כך.

**מה קורה עכשיו.** הגל נשאר פתוח (המשכפלים ממתינים לו כמו שהם ממתינים למגירות), והשרת
גוזר את האינדקס **מיד**, בלי לחכות למעבר ברקע ובלי לפנות את הדרך לעבודה אחרת. מעבר ברקע
שכבר גוזר את אותו אימג' ממשיך, והגל ממתין לו (`readbackhash.claim`): אותו אימג' אינו נפרס
פעמיים במקביל. מסך החדר מציג "מכין את האימג' לשידור, X%".

**שלושה סופים, כל אחד בשמו (עיקרון 5):**

- האינדקס נגזר ונרשם, והמנוע יקבל אותו (אותו תנאי שהמנוע בודק) — הגל יוצא.
- הגזירה נכשלה — הסבב נכשל עם הסיבה (קובץ שאינו מה שנקלט, קובץ חסר, zstd שנכשל).
  אף פעם לא "ממתין לנצח".
- הגזירה לא הסתיימה בתקרה — הסבב נכשל בשמו. התקרה לפי גודל הקבצים: `CEILING_BASE_S`
  ועוד שנייה לכל `CEILING_FLOOR_BPS` בייטים דחוסים. נמדד: img_4f8a23 (1.7GB) נגזר ב-43
  שניות על מעבדת ה-VM, 14/11 (#3137) — כ-40MB/s. התקרה מניחה קצב איטי פי 4, ועוד 5 דקות.

הגל מחכה כאן רק כשהמנוע הוא מנוע החתיכות (`server.app` מחבר את השער ל-`store.start_gate`).
גל של udpcast אינו צריך אינדקס ואינו ממתין.

**משיכה (#3231).** משיכה במנוע החתיכות (שחזור בודד מהגואי, משיכה לתחנה) מורידה את אותו אינדקס
(`GET …/index/<n>`) **אחרי** שהסוכן כבר מחק את טבלת המחיצות. אימג' שנקלט זה עתה הפיל אותה שם, על דיסק
ריק. `pull_gate` הוא אותו שער, לפני שהשרת עונה למשיכה: מתחיל את אותה גזירה, ממתין לה עד `PULL_WAIT_S`
(פחות מ-`--max-time` של הסוכן), ו**אינו עונה "נפתח"** עד שהאינדקס מוכן. שלושה סופים, כל אחד בשמו:
מוכן (המשיכה נפתחת), בהכנה (503 עם אחוזים; `curl --retry` של הסוכן שואל שוב, ואם נגמרו הניסיונות
המשיכה לא נפתחת והדיסק לא נגע), נכשלה (409 עם הסיבה). הגזירה שנכשלה נשכחת, כדי שניסיון חוזר יגזור מחדש.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from server.agent import readbackhash
from server.library import chunkformat
from server.library.images import format_problem, streamed_partitions

log = logging.getLogger("imagesrv.indexprep")

#: התקרה של הכנת האימג': בסיס, ועוד שנייה לכל כך וכך בייטים דחוסים (ראו למעלה, המקור והמדידה).
CEILING_BASE_S = 300.0
CEILING_FLOOR_BPS = 10_000_000
#: כמה ממתינים בין שתי בדיקות כשמעבר ברקע מחזיק את האימג'.
POLL_S = 0.5
#: משיכה (#3231): כמה השרת ממתין לאינדקס לפני שהוא עונה "בהכנה". קצר מ-`HTTP_TIMEOUT` של הסוכן (10),
#: אחרת `curl` נוטש את הבקשה והסוכן לא שומע את האחוזים.
PULL_WAIT_S = 8.0
#: המפתח של הכנה שמשיכה פתחה (במקום מזהה גל): אחת לכל אימג', משותפת לכל המושכים אותו.
PULL_KEY = "pull:"

PREPARING = "מכין את האימג' לשידור, {percent}%"
FAILED = "הכנת האימג' לשידור נכשלה — {why}. {tail}"
TIMED_OUT = ("הכנת האימג' לשידור לא הסתיימה תוך {ceiling:.0f} שניות (התקרה לאימג' של {gb:.1f}GB)."
             " {tail}")
WAVE_TAIL = "הגל לא יצא, ודבר לא שודר"
#: #3332: the server has no `chunkcast` with `b3list` -- the one it sends with (tools/server-tools-build.sh)
NO_B3_TOOL = ("אין בשרת chunkcast שמייצר רשימות BLAKE3 (b3list) — בלי רשימה המשכפלים אינם כותבים."
              " יש לבנות אותו מחדש (tools/server-tools-build.sh)")
PULL_TAIL = "המשיכה לא נפתחה והדיסק לא נגע"
PULL_PREPARING = PREPARING + " — המשיכה תיפתח כשהאימג' יהיה מוכן, נסו שוב בעוד רגע"


def ceiling_s(compressed_bytes: int) -> float:
    """התקרה להכנת אימג' בגודל הזה (בייטים דחוסים של הקבצים שצריך לגזור)."""
    return CEILING_BASE_S + max(0, compressed_bytes) / CEILING_FLOOR_BPS


def missing(library, image_id: str, manifest: dict) -> list[dict]:
    """המחיצות שהמנוע היה מסרב להן: אותו תנאי בדיוק כמו `ChunkEngine._send` — אינדקס שלם
    ועקבי ליד הקובץ (`chunkformat.read_index`), `index_xxh64` במניפסט, ו-`readback_chunks` שמסכים.
    #3332: ורשימת BLAKE3 ליד הקובץ (`readbackhash.has_b3`) — המקלט אינו כותב בלעדיה, ולכן הגל ממתין לה
    כמו לאינדקס, ולא יוצא בלעדיה."""
    out = []
    for part in streamed_partitions(manifest):
        path = library.file_path(image_id, part["file"])
        index = chunkformat.read_index(Path(path)) if path is not None else None
        if (index is None or not isinstance(part.get("index_xxh64"), str)
                or part.get("readback_chunks") != index.chunks
                or not readbackhash.has_b3(Path(path).parent, part)):
            out.append(part)
    return out


def _bytes(library, image_id: str, parts: list[dict]) -> int:
    total = 0
    for part in parts:
        path = library.file_path(image_id, part["file"])
        try:
            total += path.stat().st_size if path is not None else 0
        except OSError:
            pass
    return total


@dataclass
class _Job:
    wave_id: str
    image_id: str
    started: float
    ceiling: float
    gb: float
    error: str | None = None
    timed_out: bool = False
    finished: bool = False


class IndexPrep:
    """ההכנה של כל גל, בתהליכון משלה. השעון וההמתנה מוזרקים לבדיקות."""

    def __init__(self, library, *, on_event: Callable[[str, str, str], None] | None = None,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.library = library
        self.on_event = on_event or (lambda event, detail, wave_id: None)
        self.clock, self.sleep = clock, sleep
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._ready: set[str] = set()         # גלים שכבר נמצא שאינם ממתינים לדבר

    def gate(self, wave_id: str, image_id: str, start: bool = True, *, tail: str = WAVE_TAIL) -> dict | None:
        """None = הגל אינו ממתין להכנה (האינדקס שהמנוע בודק כבר שם, או שהאימג' אינו בספרייה —
        והמנוע יסרב לו בשמו). אחרת `{"state": "preparing", "percent", "he"}` או
        `{"state": "failed", "error"}`. `start=False` (תצוגה) אינו מתחיל הכנה."""
        if wave_id in self._ready:
            return None
        manifest = self.library.get(image_id)
        if manifest is None:
            return None
        with self._lock:
            job = self._jobs.get(wave_id)
        # read before the files: a job that finishes after this line is judged on the next call
        finished = job is not None and job.finished
        parts = missing(self.library, image_id, manifest)
        with self._lock:
            if not parts and (job is None or finished):
                self._ready.add(wave_id)
                self._jobs.pop(wave_id, None)
                return None
            job = self._jobs.get(wave_id)
            if job is None and start:
                size = _bytes(self.library, image_id, parts)
                job = self._jobs[wave_id] = _Job(wave_id, image_id, self.clock(), ceiling_s(size), size / 1e9)
                for old in [w for w, j in self._jobs.items() if j.finished and w != wave_id]:
                    del self._jobs[old]
                threading.Thread(target=self._run, args=(job,), daemon=True,
                                 name=f"indexprep-{wave_id}").start()
                self.on_event("room_index_wait",
                              f"{wave_id} {image_id} parts={','.join(p['file'] for p in parts)}"
                              f" bytes={size} ceiling={job.ceiling:.0f}s", wave_id)
        if job is not None:
            if job.timed_out or self.clock() - job.started > job.ceiling:
                return {"state": "failed", "error": TIMED_OUT.format(ceiling=job.ceiling, gb=job.gb, tail=tail)}
            if job.error:
                return {"state": "failed", "error": FAILED.format(why=job.error, tail=tail)}
            if finished and parts:
                files = ", ".join(p["file"] for p in parts)
                return {"state": "failed", "error": FAILED.format(
                    why=f"האינדקס נגזר ונרשם, אבל ל-{files} עדיין אין אינדקס שהמנוע מקבל", tail=tail)}
        got = readbackhash.percent(image_id)
        percent = 0 if got is None else got
        return {"state": "preparing", "percent": percent, "he": PREPARING.format(percent=percent)}

    def pull_gate(self, image_id: str, wait_s: float | None = None) -> dict | None:
        """#3231: השער של משיכה. None = האינדקס מוכן והמשיכה נפתחת. אחרת `{"state": "preparing", "percent",
        "he"}` (עוד נגזר, אחרי `wait_s`) או `{"state": "failed", "error"}`. הכנה שהסתיימה, בהצלחה או בכישלון,
        נשכחת: מסירוב שנקרא פעם אחת אין "נכשל לנצח", וניסיון חוזר גוזר מחדש."""
        key = PULL_KEY + image_id
        deadline = self.clock() + (PULL_WAIT_S if wait_s is None else wait_s)
        while True:
            state = self.gate(key, image_id, tail=PULL_TAIL)
            if state is None or state["state"] == "failed" or self.clock() >= deadline:
                break
            self.sleep(POLL_S)
        if state is None or state["state"] == "failed":
            with self._lock:
                self._jobs.pop(key, None)
                self._ready.discard(key)
        elif state["state"] == "preparing":
            state["he"] = PULL_PREPARING.format(percent=state["percent"])
        return state

    # --- התהליכון ---------------------------------------------------------------------------

    def _over(self, job: _Job) -> bool:
        return self.clock() - job.started > job.ceiling

    def _run(self, job: _Job) -> None:
        began = self.clock()
        try:
            while not readbackhash.claim(job.image_id, self.library.get(job.image_id)):
                if self._over(job):                  # the background pass holds it past the ceiling
                    job.timed_out = True
                    return
                self.sleep(POLL_S)
            try:
                self._derive(job)
            finally:
                readbackhash.release(job.image_id)
        except Exception as exc:                     # noqa: BLE001 -- the thread ends here, by name
            log.exception("index preparation of %s failed", job.image_id)
            job.error = f"{type(exc).__name__}: {exc}"
        finally:
            job.finished = True
        if not job.error and not job.timed_out:
            self.on_event("room_index_ready",
                          f"{job.wave_id} {job.image_id} in {self.clock() - began:.0f}s", job.wave_id)

    def _derive(self, job: _Job) -> None:
        manifest = self.library.get(job.image_id)
        if manifest is None:
            job.error = f"האימג' {job.image_id} אינו בספרייה"
            return
        if not missing(self.library, job.image_id, manifest):
            return                                   # the background pass finished it while we waited
        if readbackhash.b3_tool() is None:           # #3332: no list can be made here -- and none is a wave without one
            job.error = NO_B3_TOOL
            return
        if not readbackhash.needs_list(manifest):
            # nothing the server can derive: an index already judged wrong, the old format, no signed index
            problem = format_problem(manifest)
            job.error = (problem[1] if problem is not None else
                         "למחיצות " + ", ".join(p["file"] for p in missing(self.library, job.image_id, manifest))
                         + " אין אינדקס חתום מהקליטה (index_xxh64), ואין מה לגזור")
            return

        def stop() -> str | None:
            if self._over(job):
                job.timed_out = True
                return "the ceiling passed"
            return None
        try:
            values, problems = readbackhash.derive(manifest, stop=stop, progress=readbackhash.tracker(job.image_id))
        except readbackhash.Paused as exc:
            values, problems = exc.values, ([] if job.timed_out else [exc.reason])
        if values and not self.library.record_index(job.image_id, values):
            problems.append("the manifest was not updated")
        if job.timed_out:
            return
        problems += [f"p{i}: רשימת ה-BLAKE3 לא נוצרה — {v['b3_note']}"      # #3332: the index stands, the wave does not go
                     for i, v in sorted(values.items()) if isinstance(v, dict) and v.get("b3_note")]
        if problems:
            job.error = "; ".join(problems)
            return
        manifest = self.library.get(job.image_id)
        left = missing(self.library, job.image_id, manifest) if manifest is not None else []
        if manifest is None or left:
            job.error = ("האינדקס נגזר, אבל " + (", ".join(p["file"] for p in left) or job.image_id)
                         + " עדיין בלי אינדקס שהמנוע מקבל")
