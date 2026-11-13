"""מכונה מדומה — תהליכון שמתנהג כמו הסוכן האמיתי.

כל מכונה דוגמת hello בלולאה (כמו הסוכן אחרי אתחול וב-wait_poll),
וכשהיא רואה סבב במצב running היא מושכת את המניפסט ומדווחת התקדמות —
בדיוק בממשקים 2, 3 ו-4. אין לה גישה לקוד השרת או ל-DB.

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
        report("writing", midway)
        names = session.get("hostnames")
        if names is not None:
            port_of = {d["dev"]: d.get("port") for d in self.disks}
            self.named.append({t["dev"]: names.get(str(port_of.get(t["dev"])))
                               for t in final if t["state"] == "done"})
        outcomes = {t["state"] for t in final}
        report("failed" if outcomes == {"failed"} else "done", final)

    def _total_bytes(self, image_id: str) -> int:
        """המכונה מושכת את המניפסט ביוניקאסט, כמו הסוכן לפני שידור."""
        if image_id not in self._manifests:
            status, manifest = self.client.json(
                "GET", f"/api/v1/images/{image_id}/manifest")
            if status != 200:
                raise RuntimeError(f"משיכת מניפסט {image_id} החזירה {status}")
            self._manifests[image_id] = manifest
        return self._manifests[image_id]["total_compressed_bytes"]
