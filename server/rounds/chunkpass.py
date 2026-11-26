"""#3125: מה שמנוע החתיכות ראה בכל מעבר של גל, כשדות בטבלה וכשורה ביומן -- ולא רק כטקסט שנעלם בסוף הגל.

הקטלוג של המוח (`server/brain/catalog.toml`, שכבת `log`) מנה שישה דברים שהגל ידע ולא רשם: מי פיגר אחרי הזרם (U1),
`--write-kbps` ולפי איזה דיסק (U2, R7), מי לא התייצב ומתי ויתרו עליו (U3, R10), קצב ההורדה ב-HTTP של כל מחשב (U8),
ומוני המעבר של כל מחשב (R6). **הכותב היחיד** של `chunk_pass_rates` ו-`chunk_pass_counters` הוא המודול הזה.

**מה נרשם ואיפה:**

- ``rates`` -- בכל מעבר ששולח משדר: `kbps`, `write_kbps`, ועל מה ה-`write_kbps` נשען. ``basis``: ``probe`` (הדיסק האיטי
  ביותר שנמדד בבדיקת הכתיבה, כפול המרווח), ``floor`` (דיסק אחד לא נמדד, והקצב הוא הסף שכל דיסק בגל עבר) או
  ``unmeasured`` (אף דיסק בגל לא דיווח בדיקה). `slow_mac`/`slow_serial` = הדיסק שקבע את המספר, או הדיסק שלא נמדד.
  **מדידה שלא נעשתה נרשמת "לא נמדד", לא כמספר** (עיקרון 5). שורה ביומן (`chunk_write_rate`) רק במעבר הראשון של מחיצה.
- ``reported`` -- כש-`ended` של מחשב מגיע: `skipped` (כמה חתיכות הדיסקים שלו ויתרו עליהן כי פיגרו; סכום, ולפי
  serial), `stray`, `received`, `fetched`, ובמעבר הורדה -- `fetch_s` ו-`fetch_mbps`. **שדה שהסוכן לא אמר הוא NULL,
  לא אפס.** שורה ביומן רק כשיש מה לומר: דיסק שפיגר (`chunk_drive_behind`), או הורדה שנמדדה (`chunk_fetch_rate`).
- ``no_show`` -- מחשב חי שלא התייצב למחיצה עד שהשרת ויתר עליו (`chunk_no_show`).

`ended` חוזר עד שהתשובה אינה `wait`: השורה בטבלה מתעדכנת (המספר האחרון), אבל **שעון ההורדה נשאר של הדיווח הראשון**
-- מי שמחכה לאחרים אחרי שסיים אינו מאט את ההורדה שלו. שורה ביומן נכתבת פעם אחת לכל (גל, מחיצה, מעבר, מחשב).

**לעולם אינו זורק** אל תהליכון השידור או אל בקשת הדיווח: כשל כתיבה הוא שורת לוג, והגל ממשיך.
"""

from __future__ import annotations

import json
import logging
import threading
from typing import Callable, TypeGuard

from server.db import _settle, _write_lock, now_iso, SqlConnection, writing

log = logging.getLogger("imagesrv.chunkpass")

#: כמה מפתחות של "כבר נאמר" נשמרים בזיכרון: גל אחד, כמה מחיצות, כמה מעברים, כמה מחשבים -- הרבה מתחת לזה.
MAX_SAID = 20_000


def _count(value: object) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def write_basis(probes: dict[tuple[str, str], int | None]) -> dict:
    """על מה נשען ה-`--write-kbps` של מעבר (`ChunkEngine._write_kbps`): ``probes`` = {(מחשב, serial): MB/s שנמדד, או None
    = לא נמדד} לדיסקים שלא נכשלו. מחזיר ``{"basis", "probe_mbps", "slow_mac", "slow_serial"}``."""
    if not probes:
        return {"basis": "unmeasured", "probe_mbps": None, "slow_mac": None, "slow_serial": None}
    order = sorted(probes.items())
    unmeasured = [(key, v) for key, v in order if v is None]
    measured = [(key, v) for key, v in order if v is not None]
    if unmeasured:
        # דיסק אחד שלא נמדד: הקצב הוא הסף, ושמו של מי שלא נמדד הוא מה שמעניין -- לא המהיר או האיטי שבין שאר הדיסקים
        (mac, serial), _ = unmeasured[0]
        return {"basis": "floor", "probe_mbps": min((v for _k, v in measured), default=None),
                "slow_mac": mac, "slow_serial": serial}
    (mac, serial), slowest = min(measured, key=lambda item: (item[1], item[0]))
    return {"basis": "probe", "probe_mbps": slowest, "slow_mac": mac, "slow_serial": serial}


def who_he(macs: list[str]) -> str:
    """" (aa:bb, cc:dd)" או "" כשאין מי לנקוב בו. נוקב ב-MAC: לשרת אין כאן שם -- הקונסולה מתרגמת אותו."""
    return f" ({', '.join(sorted(macs))})" if macs else ""


class PassLog:
    """הכותב של `chunk_pass_rates` ו-`chunk_pass_counters`, והשורות ביומן שלצדם."""

    def __init__(self, emit: Callable[[str, str, str], None], chunk_mb: float):
        self.emit = emit                         # (event, detail, session_id) -- `ChunkEngine.on_event`
        self.chunk_mb = chunk_mb                 # חתיכה אחת, ב-MB: ההורדה נמדדת באותה יחידה כמו בדיקת הכתיבה
        #: חיבור ה-DB. None = לא מחובר (בדיקות של המנוע): רק היומן נכתב. מחובר ב-`server.app`.
        self.conn: SqlConnection | None = None
        self._lock = threading.Lock()
        self._said: set[tuple] = set()

    # --- פנים --------------------------------------------------------------------------------------------------

    def _once(self, key: tuple) -> bool:
        """True בפעם הראשונה של המפתח."""
        with self._lock:
            if key in self._said:
                return False
            if len(self._said) >= MAX_SAID:
                self._said.clear()               # גל ענק מאוד: שורה חוזרת ביומן עדיפה על זיכרון שגדל בלי גבול
            self._said.add(key)
            return True

    def _write(self, sql: str, params: tuple) -> None:
        if self.conn is None:
            return
        _settle(self.conn)
        with _write_lock, writing(self.conn):
            self.conn.execute(sql, params)

    # --- מעבר ששולח משדר ---------------------------------------------------------------------------------------

    def rates(self, session_id: str, part: int, rnd: int, kbps: int, write_kbps: int,
              probes: dict[tuple[str, str], int | None], floor_mbps: int, file: str, ruled: str | None = None) -> None:
        try:
            got = write_basis(probes)
            self._write(
                "INSERT OR REPLACE INTO chunk_pass_rates (session_id, part, pass, kbps, write_kbps, basis, probe_mbps,"
                " floor_mbps, slow_mac, slow_serial, at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (session_id, part, rnd, kbps, write_kbps, got["basis"], got["probe_mbps"], floor_mbps,
                 got["slow_mac"], got["slow_serial"], now_iso()))
            if rnd != 1 or not self._once(("rates", session_id, part)):
                return
            asked = f"{write_kbps / 8000:g}MB/s (--write-kbps {write_kbps}), הזרם {kbps}kbps"
            if got["basis"] == "probe" and ruled:
                # #3506: the brain's loop over the waves moved it -- the write check is the start, not the answer
                why = (f"הלולאה של K8 ({ruled}); בדיקת הכתיבה האיטית ביותר: {got['slow_serial']} של {got['slow_mac']},"
                       f" {got['probe_mbps']}MB/s")
            elif got["basis"] == "probe":
                why = (f"נקבע לפי {got['slow_serial']} של {got['slow_mac']}: הדיסק האיטי ביותר שנמדד בבדיקת הכתיבה,"
                       f" {got['probe_mbps']}MB/s")
            elif got["basis"] == "floor":
                why = (f"לא נמדד: {got['slow_serial']} של {got['slow_mac']} לא דיווח בדיקת כתיבה, ולכן הסף שכל דיסק"
                       f" בגל עבר, {floor_mbps}MB/s")
            else:
                why = f"לא נמדד: אף דיסק בגל לא דיווח בדיקת כתיבה, ולכן הסף {floor_mbps}MB/s"
            self.emit("chunk_write_rate", f"{file}: הדיסקים נדרשים לכתוב {asked}; {why}", session_id)
        except Exception:                        # noqa: BLE001 -- הרישום אינו עוצר גל
            log.exception("chunkpass: the rates of %s part %s pass %s were not recorded", session_id, part, rnd)

    # --- מחשב שדיווח `ended` -------------------------------------------------------------------------------

    def reported(self, session_id: str, part: int, rnd: int, mac: str, kind: str | None, skipped: dict[str, int | None],
                 stray: object, received: object, fetched: object, now: float, held: int | None,
                 began: tuple[float, int] | None) -> None:
        """`ended` של מחשב על (מחיצה, מעבר). ``kind`` = ``receive``/``fetch``/None (לא ידוע). ``held`` = כמה חתיכות
        עמידות על הדיסקים שלו עכשיו (None = לא נקרא). ``began`` = (שעון המנוע, וכמה חתיכות עמידות היו לו) כשההורדה
        התחילה -- None = לא ידוע מתי. ``skipped`` = serial ← מה שהסוכן אמר, או None."""
        try:
            said = [n for n in skipped.values() if _count(n)]
            total = sum(said) if said else None
            seconds = rate = None
            if kind == "fetch":
                if began is not None:
                    seconds = max(now - began[0], 0.0)
                    got = max((held - began[1]) if held is not None else 0, received if _count(received) else 0)
                    # הקצב נמדד רק על מה שהתקדם בפועל, ועל זמן חיובי: בלי ראיה אין מספר (לא אפס)
                    rate = got * self.chunk_mb / seconds if got > 0 and seconds > 0 else None
            key = (session_id, part, rnd, mac)
            first = self._once(("counters", *key))
            self._write(
                "INSERT INTO chunk_pass_counters (session_id, part, pass, mac, kind, skipped, skipped_json, stray,"
                " received, fetched, fetch_s, fetch_mbps, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (session_id, part, pass, mac) DO UPDATE SET kind = COALESCE(excluded.kind, chunk_pass_counters.kind),"
                " skipped = excluded.skipped, skipped_json = excluded.skipped_json, stray = excluded.stray,"
                " received = excluded.received, fetched = excluded.fetched, at = excluded.at,"
                " fetch_s = COALESCE(chunk_pass_counters.fetch_s, excluded.fetch_s),"
                " fetch_mbps = COALESCE(chunk_pass_counters.fetch_mbps, excluded.fetch_mbps)",
                (*key, kind, total, json.dumps(skipped, sort_keys=True) if said else None,
                 stray if _count(stray) else None, received if _count(received) else None,
                 fetched if _count(fetched) else None, seconds, rate, now_iso()))
            if not first:
                return
            for serial, n in sorted(skipped.items()):
                if _count(n) and n > 0:
                    self.emit("chunk_drive_behind",
                              f"{mac} {serial}: {n} חתיכות דולגו במחיצה {part} (מעבר {rnd}) -- הדיסק פיגר אחרי הזרם;"
                              " הן מושלמות אחר כך", session_id)
            if rate is not None:
                self.emit("chunk_fetch_rate",
                          f"{mac}: ההורדה ב-HTTP של מחיצה {part} (מעבר {rnd}) הסתיימה בקצב {rate:.1f}MB/s ({seconds:.0f}s)",
                          session_id)
            elif kind == "fetch":
                self.emit("chunk_fetch_rate", f"{mac}: קצב ההורדה ב-HTTP של מחיצה {part} (מעבר {rnd}) לא נמדד", session_id)
        except Exception:                        # noqa: BLE001
            log.exception("chunkpass: the counters of %s from %s were not recorded", session_id, mac)

    # --- מי לא התייצב ----------------------------------------------------------------------------------------

    def no_show(self, session_id: str, file: str, macs: list[str], waited: float) -> None:
        if not macs:
            return
        try:
            if self._once(("no_show", session_id, file)):
                self.emit("chunk_no_show",
                          f"{file}: לא התייצבו{who_he(macs)} -- השרת ויתר עליהם אחרי {waited:.0f} שניות והמחיצה יצאה"
                          " בלעדיהם", session_id)
        except Exception:                        # noqa: BLE001
            log.exception("chunkpass: the absence of %s from %s was not recorded", macs, session_id)
