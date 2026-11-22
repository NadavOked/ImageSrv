"""השולח המדומה של הסימולציה (#3173): `chunkcast send` שאינו משדר דבר.

השרת שהסימולציה מרימה אמיתי, וכך גם מנוע החתיכות שלו (`chunksend.ChunkEngine`): הוא בוחר
קצב, מחכה שהמגירות יתייצבו (`chunk-pass listening`), מריץ פקודה, מחכה ל-`ended`, מתכנן השלמה
מהיומן וסוגר מחיצה אחרי מחיצה. רק הפקודה עצמה מוחלפת כאן, וזה במכוון: במעבדה מחוברות לשרת
הזה מכונות אמיתיות, ו-`chunkcast send` אמיתי היה משדר מולטיקאסט לרשת ההפצה (#201).

במקום השידור רץ תהליך שיוצא 0, והפקודה נרשמת — שורת JSON לכל פקודה — לקובץ ש-`ENV` נותן.
הקובץ הוא הראיה שהמנוע שידר, ומה (מחיצה, מעבר, פורט); בלעדיו אין שולח, והפקודה נכשלת
בשמה (`OSError`, שהמנוע הופך לכישלון של הגל), לא "משדרת" בשקט (עיקרון 5).

נטען בתהליך של השרת לפני `server.main.main()` (`harness.start_server`), כמו `CLASSROOMS`.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from server.rounds import chunklog

#: אותו שם כמו `harness.SIMSEND_ENV`.
ENV = "IMAGESRV_SIM_SENDS"
#: מה שנכתב ללוג של המחיצה (`chunklog.log_path`), שהמנוע קורא אחרי המעבר.
LOG_LINE = b"simsend: simulated chunkcast send -- no packet was sent\n"


def run(cmd: list[str]) -> subprocess.Popen:
    """במקום `chunklog.run_process`: רושם את הפקודה, ומחזיר תהליך שיוצא 0 בלי לשדר."""
    path = os.environ.get(ENV)
    if not path:
        raise OSError(f"simsend: {ENV} is not set -- the simulated sender has nowhere to record what it sends")
    if cmd[:2] != ["chunkcast", "send"]:
        raise OSError(f"simsend: not a chunkcast send: {cmd[:2]}")
    with open(path, "a", encoding="utf-8") as record:
        record.write(json.dumps(cmd) + "\n")
    with chunklog.log_path(cmd[cmd.index("--file") + 1]).open("wb") as handle:
        handle.write(LOG_LINE)
        handle.flush()
        return subprocess.Popen([sys.executable, "-c", ""], stdin=subprocess.DEVNULL,
                                stdout=handle, stderr=subprocess.STDOUT)


def install() -> None:
    """לפני `create_runtime`: `DirectChunks.runner` קורא את `chunklog.run_process` כשהשרת נבנה."""
    chunklog.run_process = run
