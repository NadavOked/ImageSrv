"""מכונה מדומה — תהליכון שמתנהג כמו הסוכן האמיתי.

כל מכונה דוגמת hello בלולאה (כמו הסוכן אחרי אתחול וב-wait_poll),
וכשהיא רואה סבב במצב running היא מושכת את המניפסט, עוברת את מנוע החתיכות
מחיצה אחרי מחיצה (`chunk-pass`, #3173) ומדווחת התקדמות — בדיוק בממשקים
2, 3 ו-4. אין לה גישה לקוד השרת או ל-DB.

ההתנהגות נקבעת בתוכניות כתיבה (write_plans): רשימת תוכניות, אחת לכל
סבב חדש שהמכונה פוגשת. תוכנית היא רשימת יעדים — (dev, "done") או
(dev, "failed", "הודעת שגיאה"). התוכנית האחרונה משמשת שוב ושוב.
"""

from __future__ import annotations

import threading
import time

from .harness import Client, hello


class SimMachine(threading.Thread):
    def __init__(self, mac: str, disks: list[dict], *,
                 base: str | None = None, interval: float = 0.3):
        super().__init__(daemon=True, name=f"sim-{mac[-5:]}")
        self.mac = mac
        self.disks = disks
        self.client = Client(base) if base else Client()
        self.interval = interval
        self.answer: dict | None = None      # התשובה האחרונה, ממשק 3
        self.silent = False                  # "המכונה נפלה" — מפסיקה לדבר
        self.silent_after_join = False       # נופלת מיד אחרי שהצטרפה
        self.write_plans: list[list[tuple]] = []
        self.handled_sessions: set[str] = set()
        #: #2786: לכל גל — השם שהסוכן היה כותב לכל מגירה שנכתבה עד הסוף (dev → שם),
        #: מתוך `session.hostnames` לפי החריץ שלה, כמו `name_drawers` (drawernames.sh).
        self.named: list[dict[str, str]] = []
        #: #3173: (סבב, מחיצה) שמנוע החתיכות סגר מול המכונה הזו (`done` ב-`chunk-pass`), לפי הסדר.
        self.chunk_parts: list[tuple[str, int]] = []
        #: #3173: ה-`session` של ה-hello שבו המכונה ראתה כל גל `running` — כולל `engine` ו-`chunk`.
        self.waves: list[dict] = []
        #: #3173: למשוך את קובץ המחיצה ביוניקאסט בתוך הגל (ממשק 7), ומה שנמשך: (אימג', קובץ) → (status, בייטים).
        self.pull_files = False
        #: #3173: (session מה-hello, מחיצה) → האם השולח כבר סיים את מעבר 1 שלה. השלב שמריץ גל קובע אותו.
        self.pass_sent = None
        self.pulled: dict[tuple[str, str], tuple[int, bytes]] = {}
        self.error: Exception | None = None
        self._manifests: dict[str, dict] = {}
        self._stop = threading.Event()

    # --- מחזור החיים ---------------------------------------------------------

    def run(self) -> None:
        try:
            while not self._stop.is_set():
                if not self.silent:
                    self._poll()
                time.sleep(self.interval)
        except Exception as exc:            # nose: הכשל מדווח ב-main
            self.error = exc

    def stop(self) -> None:
        self._stop.set()

    # --- התנהגות הסוכן -------------------------------------------------------

    def _poll(self) -> None:
        status, answer = hello(self.client, self.mac, self.disks)
        if status != 200:
            raise RuntimeError(f"hello של {self.mac} החזיר {status}: {answer}")
        self.answer = answer
        session = answer.get("session")
        if not session:
            return
        if session["state"] == "open" and self.silent_after_join:
            # ההצטרפות כבר קרתה ב-hello הזה עצמו — עכשיו המכונה "נופלת".
            self.silent = True
        elif session["state"] == "running" \
                and session["id"] not in self.handled_sessions and self.write_plans:
            self.handled_sessions.add(session["id"])
            self.waves.append(dict(session))
            self._write(session)

    def _write(self, session: dict) -> None:
        """מדמה סבב כתיבה: דיווח באמצע ודיווח סופי, יעד-יעד (ממשק 4)."""
        plan = (self.write_plans[0] if len(self.write_plans) == 1
                else self.write_plans.pop(0))
        total = self._total_bytes(session["image_id"])

        def report(state: str, targets: list[dict]) -> None:
            status, answer = self.client.json("POST", "/api/v1/agent/progress", {
                "session_id": session["id"], "mac": self.mac,
                "state": state, "targets": targets,
            })
            if status != 200:
                raise RuntimeError(f"progress של {self.mac} החזיר {status}: {answer}")

        midway, final = [], []
        for item in plan:
            dev, outcome = item[0], item[1]
            error = item[2] if len(item) > 2 else None
            midway.append({"dev": dev, "bytes_written": total // 2,
                           "bytes_total": total, "state": "writing"})
            final.append({"dev": dev,
                          "bytes_written": total if outcome == "done" else 4096,
                          "bytes_total": total, "state": outcome,
                          "error": error})
        if session.get("engine") != "chunk":
            # #3173: מנוע החתיכות הוא היחיד. גל שה-hello לא אמר עליו `engine: chunk` אין לסוכן
            # איך לקבל — הסוכן האמיתי נכשל כאן, ולכן גם המכונה המדומה.
            raise RuntimeError(f"{self.mac}: הגל {session['id']} אינו במנוע החתיכות: {session}")
        self._chunk_wave(session, plan)
        report("writing", midway)
        names = session.get("hostnames")
        if names is not None:
            port_of = {d["dev"]: d.get("port") for d in self.disks}
            self.named.append({t["dev"]: names.get(str(port_of.get(t["dev"])))
                               for t in final if t["state"] == "done"})
        outcomes = {t["state"] for t in final}
        report("failed" if outcomes == {"failed"} else "done", final)

    def _chunk_wave(self, session: dict, plan: list[tuple]) -> None:
        """#3173: מה ש-`chunkrecv.sh` עושה, מחיצה אחרי מחיצה, באותו סדר שהמנוע משדר (המניפסט):
        `listening` על מעבר 1, ואז `ended` עם מה שעמיד על כל מגירה — שוב ושוב עד שהתשובה
        אינה `wait`. השולח המדומה (`simsend`) אינו משדר דבר, ולכן המגירה "קיבלה" הכול:
        מגירה שהתוכנית מכשילה מדווחת `failed`, וכל השאר את כל החתיכות. התשובה חייבת
        להיות `done` — `fetch` או מעבר השלמה הם כשל של הסימולציה, בגלוי."""
        manifest = self._manifest(session["image_id"])
        serial = {d["dev"]: d["serial"] for d in self.disks}
        for part in (p for p in manifest["partitions"] if p.get("file")):
            index, chunks = part["index"], part.get("readback_chunks")
            if not isinstance(chunks, int) or chunks < 1:
                raise RuntimeError(f"{self.mac}: למחיצה {index} אין readback_chunks במניפסט: {part}")

            def targets(ended: bool) -> list[dict]:
                return [{"serial": serial[item[0]], "write_mbps": 150,
                         "failed": ended and item[1] == "failed",
                         "durable": [{"part": index, "ranges": [[0, chunks - 1]]
                                      if ended and item[1] == "done" else []}]}
                        for item in plan]

            answer = self._chunk_pass(session, index, "listening", targets(False))
            if answer.get("action") != "receive":
                raise RuntimeError(f"{self.mac}: listening על מחיצה {index} נענה {answer}")
            # המקלט האמיתי מדווח `ended` רק כשהמעבר נגמר. כאן אין חבילות לחכות להן, ולכן השלב אומר
            # מתי השולח שלו (`simsend`, או המקור בהפצה ישירה) סיים את המעבר של המחיצה הזו.
            if self.pass_sent is None:
                raise RuntimeError(f"{self.mac}: אין לסימולציה איך לדעת שהמעבר של מחיצה {index} שודר")
            deadline = time.time() + 60
            while not self.pass_sent(session, index):
                if time.time() > deadline:
                    raise RuntimeError(f"{self.mac}: המעבר של מחיצה {index} לא שודר תוך 60 שניות")
                time.sleep(0.2)
            deadline = time.time() + 60
            while (answer := self._chunk_pass(session, index, "ended", targets(True))).get("action") == "wait":
                if time.time() > deadline:
                    raise RuntimeError(f"{self.mac}: מחיצה {index} לא נסגרה תוך 60 שניות: {answer}")
                time.sleep(0.2)
            if answer.get("action") != "done":
                raise RuntimeError(f"{self.mac}: ended על מחיצה {index} נענה {answer}")
            self.chunk_parts.append((session["id"], index))
            if self.pull_files:
                # ממשק 7: מה שסוכן מוריד ב-HTTP כשחסר לו (`fetch`) — כאן הקובץ כולו, כדי שהשלב ישווה בייטים
                status, payload = self.client.request(
                    "GET", f"/api/v1/images/{session['image_id']}/files/{part['file']}")
                self.pulled[(session["image_id"], part["file"])] = (status, payload)

    def _chunk_pass(self, session: dict, part: int, state: str, targets: list[dict]) -> dict:
        status, answer = self.client.json("POST", "/api/v1/agent/chunk-pass", {
            "mac": self.mac, "session_id": session["id"], "part": part, "pass": 1,
            "state": state, "targets": targets})
        if status != 200:
            raise RuntimeError(f"chunk-pass של {self.mac} ({state}, מחיצה {part}) החזיר {status}: {answer}")
        return answer

    def _manifest(self, image_id: str) -> dict:
        """המכונה מושכת את המניפסט ביוניקאסט, כמו הסוכן לפני שידור."""
        if image_id not in self._manifests:
            status, manifest = self.client.json(
                "GET", f"/api/v1/images/{image_id}/manifest")
            if status != 200:
                raise RuntimeError(f"משיכת מניפסט {image_id} החזירה {status}")
            self._manifests[image_id] = manifest
        return self._manifests[image_id]

    def _total_bytes(self, image_id: str) -> int:
        return self._manifest(image_id)["total_compressed_bytes"]
