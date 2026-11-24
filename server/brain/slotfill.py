"""#3064, B33: מגירה שצריכה השלמה בכל סבב -- חשד לחומרה (המגירה, הכבל או החיבור), לא לדיסק. **בצל.**

**למה.** מ-#3295 (אפשרות ב', נדב 15/11) הגל רץ בקצב של המהירים, ודיסק שלא עמד בקצב משלים אחר כך. דיסק איטי
אחד הוא הדיסק. אבל **אותה מגירה** שבכל גל, עם דיסקים שונים, לא קיבלה את השידור כולו -- בזמן שמגירה אחרת
באותו משכפל קיבלה -- היא חשד לחומרה: המגירה, הכבל או חיבור ה-SATA. זה המשלים של `learn/ports.py` (CRC וקישור),
מאות אחר: מה שהדיסק לקח מהשידור.

**האות** -- מה שכבר נשמר, בלי שדה חדש:
- המגירה (משכפל + חיבור) ומי ישב בה בכל גל -- ``port_history`` (`learn_ports.record_wave`).
- כמה כל דיסק לקח מהמעבר הראשון, מול מה ששודר -- ``wave_rates.stream.chunk.drives`` ו-``stream_zbytes``
  (#3296, `ChunkEngine.rate_outcome`, שנשמר ב-`chunk_rate.record`).

תצפית = (גל, דיסק): **צריך השלמה** כשהדיסק לקח פחות מ-`drive_rates.KEEP_UP` ממה ששודר; **עמד** כשלקח לפחות
כך. גל בלי מעבר ראשון שנמדד, או דיסק שאינו בו, אינו תצפית ("לא נמדד" אינו "עמד" ואינו "צריך", עיקרון 5).

**ההכרעה** (`judge`, טהורה): ``SUSPECT`` כש-`MIN_WAVES` התצפיות האחרונות של המגירה **כולן** צריכות השלמה,
בהן לפחות `MIN_DISKS` דיסקים שונים, ובכל אחת מהן מגירה אחרת באותו משכפל עמדה (אחרת זה הזרם או המשכפל, לא
המגירה). הספים הם נקודת התחלה בקוד, לא הגדרות (נדב 14/11): 3 גלים ו-2 דיסקים -- כמו "≥2 דיסקים שונים" של
`learn_ports.MIN_DISKS`, ולא נמדדו.

**נרשם בלבד:** ``SLOT_SUSPECT`` על המגירה (ישות ``slot``, ``mac/port``), פעם אחת לגל. הצבע במסך עוד לא (R15).
המתג: ``brain_veto_mode`` (``off`` = לא מחושב). לעולם אינו זורק אל סיום הגל.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from server.brain import memory
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, SUSPECT = "K9", "SLOT_SUSPECT", "SUSPECT"
MIN_WAVES = 3
MIN_DISKS = 2
#: כמה גלים אחרונים של המגירה נקראים כדי למצוא `MIN_WAVES` תצפיות שנמדדו.
SCAN = 12


@dataclass(frozen=True)
class Obs:
    session_id: str
    serial: str
    needed: bool                    # לקח פחות מ-KEEP_UP ממה ששודר
    others_kept: bool               # מגירה אחרת באותו משכפל, באותו גל, עמדה


def judge(obs: list[Obs]) -> tuple[bool, str]:
    """``obs`` -- התצפיות שנמדדו, החדשה ראשונה. (חשד?, למה)."""
    last = obs[:MIN_WAVES]
    if len(last) < MIN_WAVES:
        return False, f"רק {len(last)} גלים נמדדו במגירה (צריך {MIN_WAVES})"
    if not all(o.needed for o in last):
        return False, "בגל אחד לפחות הדיסק במגירה עמד בשידור"
    disks = {o.serial for o in last}
    if len(disks) < MIN_DISKS:
        return False, f"אותו דיסק בכל {MIN_WAVES} הגלים: זה הדיסק, לא המגירה"
    if not all(o.others_kept for o in last):
        return False, "גם המגירות האחרות במשכפל לא עמדו: זה הזרם או המשכפל, לא המגירה"
    return True, (f"המגירה צריכה השלמה ב-{MIN_WAVES} הגלים האחרונים, עם {len(disks)} דיסקים שונים, בזמן"
                  " שמגירה אחרת באותו משכפל עמדה בשידור: חשד למגירה, לכבל או לחיבור")


def _chunk(conn, session_id: str) -> dict | None:
    row = conn.execute("SELECT stream FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
    try:
        got = (json.loads(row["stream"]) if row is not None and row["stream"] else {}).get("chunk")
    except (TypeError, ValueError, AttributeError):
        return None
    return got if isinstance(got, dict) else None


def _kept(took: object, offered: int) -> bool | None:
    if not isinstance(took, int) or isinstance(took, bool) or took < 0:
        return None
    return took >= drive_rates.KEEP_UP * offered


def observations(conn, mac: str, port: int) -> list[Obs]:
    """התצפיות שנמדדו במגירה, החדשה ראשונה."""
    out = []
    for row in conn.execute("SELECT session_id, serial FROM port_history WHERE mac = ? AND port = ?"
                            " ORDER BY at DESC, rowid DESC LIMIT ?", (mac, port, SCAN)).fetchall():
        chunk = _chunk(conn, row["session_id"]) or {}
        offered, drives = chunk.get("stream_zbytes"), chunk.get("drives")
        if not isinstance(offered, int) or offered <= 0 or not isinstance(drives, dict):
            continue
        mine_raw = drives.get(mac)
        mine = mine_raw if isinstance(mine_raw, dict) else {}
        kept = _kept(mine.get(row["serial"]), offered)
        if kept is None:
            continue
        others = [_kept(took, offered) for serial, took in mine.items() if serial != row["serial"]]
        out.append(Obs(row["session_id"], row["serial"], not kept, any(o is True for o in others)))
    return out


def after_wave(conn, session_id: str, round_id: str | None) -> int:
    """הגל הסתיים (`room._finish_wave`, אחרי `learn_ports`): כל מגירה של הגל נשפטת. מחזיר כמה חשודות נרשמו."""
    try:
        from server.brain import veto            # כמו ב-`roundend._mode`
        mode = veto.mode(conn)
        if mode == "off" or _chunk(conn, session_id) is None:
            return 0
        found = 0
        for row in conn.execute("SELECT mac, port FROM port_history WHERE session_id = ?", (session_id,)).fetchall():
            obs = observations(conn, row["mac"], row["port"])
            if not obs or obs[0].session_id != session_id:
                continue                         # הגל הזה לא נמדד במגירה: אין מה לחדש
            suspect, why = judge(obs)
            if not suspect:
                continue
            key = f"{row['mac']}/{row['port']}"
            memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=SUSPECT, reason=why, point=session_id,
                          evidence=[{"wave": o.session_id, "serial": o.serial, "needed": o.needed,
                                     "others_kept": o.others_kept} for o in obs[:MIN_WAVES]],
                          round_id=round_id, entity=("slot", key),
                          outcome="נרשם בלבד: הצבע במסך עוד לא (R15)")
            log.info("brain %s", json.dumps({"mode": mode, "rule": RULE, "action": ACTION, "verdict": SUSPECT,
                                              "slot": key, "reason": why}, ensure_ascii=False))
            found += 1
        return found
    except Exception:                           # noqa: BLE001 -- הזיכרון אינו עוצר סיום גל
        log.exception("brain: B33 after %s failed", session_id)
        return 0
