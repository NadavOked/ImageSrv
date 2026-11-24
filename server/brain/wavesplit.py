"""#1445, #3064 (B48): גל אחד, או גלים נפרדים לפי היכולת של המשכפלים -- המוח מחליט לבד. **צל בלבד.**

**נדב 15/11, #1445:** "למה המוח לא מבין לבד מה מחובר ולפי זה?" -- אין מתג ואין שאלה למפעיל. המוח מחליט אם לפצל
סבב לכמה גלים, ומפצל רק כשהחיזוי מראה שזה מקצר את זמן הסבב הכולל. **אין סף קבוע:** הפיצול נבחר כשהזמן החזוי שלו
קצר מזה של גל אחד, ושני הזמנים נרשמים.

**הקלט, ברגע שהגל יוצא** (`ChunkEngine.wave_start`, אחרי `chunk_rate.start` ו-`waveeta`):

- **היכולת של כל משכפל:** מה שנלמד מגלים קודמים (#3041) כשנמדד, ואחרת כרטיס הרשת שלו מה-hello (`nic_speed.view`)
  כפול `LINK_CEILING_PER_MBPS` -- אותו גג שהגל של היום מקבל מהמשכפל האיטי. מהירות שהונחה (`assumed`) נרשמת ככזו.
- **השרת:** מה שהגל רשאי לבקש מהרשת (`chunk_rate.cap_of`: כרטיס ההפצה ותקציב המוח). אף קבוצה אינה מהירה ממנו.
- **ה-ETA:** `waveeta.predict` לכל גל מועמד, כולל ההשלמה של כונן שפיגר בגל קודם (`chunk_rate.write_history`).
- **מה שנשאר בסבב:** `room_rounds` -- כמה דיסקים עוד צריך לכתוב אחרי הגל הזה (פס ייצור, G13).

**שתי האפשרויות** (`plan`, פונקציה טהורה). המנוע משדר זרם אחד בכל פעם, ולכן גלים רצים בזה אחר זה:

- **גל אחד (היום):** כל המשכפלים בקצב של האיטי. מה שנשאר בסבב -- עוד גלים של כולם, ``ceil(נשאר ÷ כל המגירות)``.
- **פיצול:** קודם גל של המהירים בקצב שלהם, ואחריו גל של האיטיים. מה שנשאר בסבב נכתב במהירים בלבד. נבדקת כל נקודת
  חיתוך בין יכולות שונות, והקצרה נבחרת.

כשכל המשכפלים באותה יכולת -- כמו במכללה, כולם 1Gb/s, או שהשרת הוא התקרה של כולם -- אין מה לפצל: ``SAME``, גל אחד.
בסבב של גל אחד בלבד, פיצול לעולם אינו מקצר (שני גלים בזה אחר זה), והחיזוי מראה זאת בעצמו.

**מה שלא נמדד:** אימג' שמספר החתיכות או הגודל שלו לא ידועים -- ``UNDECIDED`` בלי מספרים. יכולת שהונחה אצל משכפל
כשיש יותר מיכולת אחת -- ``UNDECIDED``: ההחלטה הייתה נשענת על הנחה (עיקרון 5). שני החיזויים נרשמים גם אז.

**הרישום:** `brain_decisions`, ``rule=B48``, ``action=WAVE_SPLIT``, פעם אחת לגל. **שום דבר לא זז:** הקוד של היום
מריץ גל אחד, גם ב-``enforce``, וזו התוצאה שנרשמת. המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם.
**לעולם אינו זורק** אל הגל.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from server.brain import memory, waveeta
from server.rounds import chunk_rate, drive_rates, nic_speed, room_state

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "B48", "WAVE_SPLIT"
ONE, SPLIT, SAME, UNDECIDED = "ONE", "SPLIT", "SAME", "UNDECIDED"
OFF = "off"
#: כמה גלים אחרונים נזכרים כדי לא להחליט פעמיים על אותו גל (`wave_start` נקרא לכל מחיצה)
SEEN = 64


@dataclass(frozen=True)
class Cloner:
    mac: str
    cap_bps: int                     # מה שהוא מקבל: הנלמד כשנמדד, ואחרת כרטיס הרשת × 90%
    slots: int                       # כמה דיסקים הוא כותב בגל הזה
    source: str = "reported"         # learned / reported / manual / assumed
    slow: tuple[waveeta.Slow, ...] = ()


@dataclass(frozen=True)
class Plan:
    verdict: str                     # ONE / SPLIT / SAME / UNDECIDED
    one_s: float
    waves_one: int
    split_s: float | None = None
    waves_split: int | None = None
    fast: tuple[str, ...] = ()       # המשכפלים של הגל המהיר בפיצול הטוב ביותר


def plan(chunks: int, zbytes: int, server_bps: int, cloners: list[Cloner], remaining: int) -> Plan:
    """(חתיכות, בתים דחוסים, מה שהשרת נותן, המשכפלים בגל, כמה דיסקים נשארים בסבב אחריו) -> ההחלטה. טהורה."""
    def rate(group):
        return min(server_bps, *(c.cap_bps for c in group))

    def eta(group, slow=True):
        return waveeta.predict(chunks, zbytes, rate(group), [s for c in group for s in c.slow] if slow else []).eta_s

    def total(now, later):
        # הדיסקים של מה שנשאר עוד לא ידועים: הגלים הבאים בלי כונן איטי
        n = -(-remaining // sum(c.slots for c in later)) if remaining > 0 else 0
        return len(now) + n, sum(eta(g) for g in now) + n * eta(later, slow=False)

    waves_one, one = total([cloners], cloners)
    caps = sorted({min(server_bps, c.cap_bps) for c in cloners})
    if len(caps) < 2:
        return Plan(SAME, one, waves_one)
    splits = []
    for cut in caps[1:]:
        group = [c for c in cloners if min(server_bps, c.cap_bps) >= cut]
        waves, t = total([group, [c for c in cloners if c not in group]], group)
        splits.append((waves, t, tuple(c.mac for c in group)))
    waves, t, fast = min(splits, key=lambda s: s[1])
    if any(c.source == "assumed" for c in cloners):
        verdict = UNDECIDED
    else:
        verdict = SPLIT if t < one else ONE
    return Plan(verdict, one, waves_one, t, waves, fast)


class WaveSplit:
    """המצב בשרת אחד. ``image(session_id)`` = (חתיכות, בתים דחוסים, למה לא) כמו ב-`waveeta`; ``learned(mac)`` = היכולת
    הנלמדת בביטים לשנייה (#3041), או None כשלא נמדדה."""

    def __init__(self, conn, *, image: Callable[[str], tuple], learned: Callable[[str], int | None] | None = None,
                 mode: Callable[[], str] | None = None):
        self.conn, self._image, self._learned, self._mode_fn = conn, image, learned, mode
        self._seen: dict[str, None] = {}

    def _mode(self) -> str:
        if self._mode_fn is not None:
            return self._mode_fn()
        from server.brain import veto            # כמו ב-`waveeta`: המתג מגיע מהווטו
        return veto.mode(self.conn)

    def started(self, session_id: str, bits: object, probes: dict) -> object:
        """`ChunkEngine.wave_start`: רושם את ההחלטה פעם אחת לגל, ומחזיר את ``bits`` כמו שהוא."""
        del probes
        try:
            if session_id not in self._seen and self._mode() != OFF:
                self._seen[session_id] = None
                while len(self._seen) > SEEN:
                    self._seen.pop(next(iter(self._seen)))
                self._decide(session_id, bits)
        except Exception:                       # noqa: BLE001 -- המוח אינו מפיל גל
            log.exception("brain: the wave split of %s was not decided", session_id)
        return bits

    def _cloners(self, drives: list[dict]) -> list[Cloner]:
        by_mac: dict[str, list[str]] = {}
        for d in drives:
            if d.get("mac") and d.get("serial"):
                by_mac.setdefault(d["mac"], []).append(d["serial"])
        out = []
        for mac, serials in sorted(by_mac.items()):
            nic = nic_speed.view(self.conn, mac)
            cap, source = nic["speed_mbps"] * drive_rates.LINK_CEILING_PER_MBPS, nic["source"]
            learned = self._learned(mac) if self._learned is not None else None
            if isinstance(learned, int) and not isinstance(learned, bool) and learned > 0:
                cap, source = min(cap, learned), "learned"
            behind = {s: got[0] for s in serials if (got := chunk_rate.write_history(self.conn, s)) is not None}
            slow = tuple(waveeta.Slow(s, wrote, waveeta.NEIGHBOR if any(o not in behind for o in serials)
                                      else waveeta.HTTP, mac) for s, wrote in sorted(behind.items()))
            out.append(Cloner(mac, cap, len(serials), source, slow))
        return out

    def _decide(self, session_id: str, bits: object) -> None:
        mode = self._mode()
        round_row = self.conn.execute("SELECT * FROM room_rounds WHERE wave_session_id = ? AND state = 'active'",
                                      (session_id,)).fetchone()
        drives = room_state.wave_drives(self.conn, session_id)
        cloners = self._cloners(drives)
        if round_row is None or not cloners:
            return                               # לא גל של סבב חדר: אין מה לפצל
        remaining = max(room_state._remaining(round_row) - len(drives), 0)
        decision = drive_rates.decision(self.conn, session_id) or {}
        server, server_from = chunk_rate.cap_of(self.conn, {"link": decision.get("link")})
        chunks, zbytes, why = self._image(session_id)
        common = (f"server_mbps={server // 10 ** 6}", f"server_from={server_from}", f"remaining={remaining}",
                  f"stream_mbps={bits // 10 ** 6 if isinstance(bits, int) else 'unmeasured'}",
                  *(f"cloner={c.mac}:{c.cap_bps // 10 ** 6}Mb/s:{c.source}:{c.slots}:behind={len(c.slow)}"
                    for c in cloners))
        outcome = "הקוד הקיים ביצע: גל אחד"
        entity = ("round", session_id)
        if why is not None:
            memory.record(self.conn, mode=mode, rule=RULE, action=ACTION, verdict=UNDECIDED, evidence=common,
                          reason=f"לא הוכרע: {why}", round_id=session_id, entity=entity, outcome=outcome)
            return
        p = plan(chunks, zbytes, server, cloners, remaining)
        evidence = (f"one_s={p.one_s:.0f}", f"waves_one={p.waves_one}",
                    f"split_s={'none' if p.split_s is None else f'{p.split_s:.0f}'}",
                    f"waves_split={'none' if p.waves_split is None else p.waves_split}",
                    *(f"fast={mac}" for mac in p.fast), *common)
        if p.verdict == SAME or p.split_s is None:
            reason = (f"גל אחד: כל המשכפלים באותה יכולת ({min(server, cloners[0].cap_bps) // 10 ** 6}Mb/s), "
                      f"ואין מה לפצל. חזוי {p.one_s / 60:.0f} דק'")
        else:
            reason = (f"{'פיצול' if p.verdict == SPLIT else 'גל אחד'}: גל אחד חזוי {p.one_s / 60:.0f} דק' "
                      f"({p.waves_one} גלים), פיצול ({len(p.fast)} מהירים קודם) חזוי {p.split_s / 60:.0f} דק' "
                      f"({p.waves_split} גלים)")
            if p.verdict == UNDECIDED:
                reason = "לא הוכרע: המהירות של חלק מהמשכפלים הונחה ולא נמדדה. " + reason
        memory.record(self.conn, mode=mode, rule=RULE, action=ACTION, verdict=p.verdict,
                      reason=reason + ". החלטה בצל: הגל יוצא כמו היום", evidence=evidence, round_id=session_id,
                      entity=entity, outcome=outcome)
