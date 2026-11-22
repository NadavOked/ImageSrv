"""#3064 (B16, B34, B30): מגירה שמשלימה, ומגירה חשודה שמאומתת ולא נמחקת. **במצב צל כברירת מחדל.**

**B16 -- לא לסגור סבב ולא להכשיל מחיצה כשמשכפל עוד משלים מגירה שחזרה.** ההתנהגות **קיימת** ושייכת לסוכן:
``drawerback.sh`` (``drawer_back``) משלים ברקע, מגירה שמשלימה לעולם אינה מעכבת את האחרות, וה-hello של המשכפל נשאר
``writing`` ולכן הגל והסבב אינם נסגרים; ``drawerhold.sh`` (``drawer_hold``, #3140) מחזיק את המשכפל בסבב כשהמגירה
האחרונה שלו נשלפה. כאן **לא נכתבת התנהגות שנייה**. מה שהיה חסר הוא שהשרת **לא ידע** (S45, G1): הסוכן שולח היום
`targets[].rejoin` (`docs/architecture/interfaces.md` סעיף 4), והמוח רושם מזה **החלטה אחת לכל מגירה ומחיצה**:
``HOLD_COMPLETING`` (``rule="K6"``) ב-``brain_decisions``, עם הסיבה, הספירה והמדיניות. התוצאה נסגרת **מהדיווח הבא**
שבו השדה ירד, בשם מה שהדיווח אומר (נכשל / השדה ירד במצב X / היעד יצא), לעולם לא "הושלם" שלא נראה. ב-``enforce``
אין כאן עוד פעולה: ה-HOLD כבר נאכף בסוכן, ואם משכפל שותק, K2 (`veto.py`) הוא זה ששופט.

**B34 -- שם למדיניות ההצטרפות.** מ-NORM (RFC 5740): ``SYNC_CURRENT`` מצטרף מהנקודה הנוכחית ולא מבקש עבר, ו-``SYNC_ALL``
מבקש את כל מה שעבר. אצלנו כל מגירה שחזרה מצטרפת לשידור החי (`drawerfill.sh`, ``_fl_live``), ולכן ההבדל הוא רק
אם חסר לה משהו מהעבר: ``CURRENT`` = לא חסר דבר, ``ALL`` = חסר, והעבר מושלם משכנה ואז ב-HTTP. השם נרשם בראיות של
כל החלטה כאן וב-``FILL_SOURCE`` (`fill.py`). ``None`` = הסוכן לא ספר, ואין שם (לא ``CURRENT``).

**B30 -- מגירה חשודה מאומתת ולא נמחקת.** מגירה שנכשלה בקריאה החוזרת **עם רשימת החתיכות שלא תאמו**
(``readback_failed_chunks``: ``[{"partition": P, "chunk_index": K}]``, #3323/S63, interfaces.md סעיף 4) אינה צריכה
כתיבה מחדש של כל הדיסק: הדיסק הוא ה-ledger השני, והאימות מול האינדקס כבר נקב בהבדלים. ההחלטה (``VERIFY_REPAIR``,
``rule="K5"``) היא להוריד **רק את אלה**, דרך הזרימה של מגירה שחזרה (`drawerfill.sh`: חסר = האינדקס פחות ה-ledger).
**הביצוע מאחורי מתג כבוי** (``brain_verify_repair``, ברירת מחדל ``off``): ב-``enforce`` **וגם** כשהמתג ``on`` התשובה
נושאת ``verify``; בכל מצב אחר ההחלטה רק נרשמת. שדה חסר, ריק או פגום = אין החלטה (עיקרון 5: "לא נמדד" אינו "תקין").

הרישום כמו K5/K7: שורת ``brain {...}`` בלוגר `imagesrv.brain` ושורה ב-``brain_decisions`` (`memory.record`).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone

from server.brain import fill, memory
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")

OFF, SHADOW, ENFORCE = fill.OFF, fill.SHADOW, fill.ENFORCE
HOLD = "HOLD"
REPAIR = "REPAIR"
RULE_HOLD, RULE_VERIFY = "K6", "K5"
ACTION_HOLD, ACTION_VERIFY = "HOLD_COMPLETING", "VERIFY_REPAIR"
#: B34: שם המדיניות. ראה את ה-docstring.
CURRENT, ALL = "CURRENT", "ALL"
#: B30: המתג של הביצוע. ``on`` בדיוק; כל ערך אחר, חסר או שלא נקרא = כבוי.
SETTING = "brain_verify_repair"
#: כמה חתיכות נוקבות בשם בראיות (השאר נספרות). הרשימה השלמה נשלחת לסוכן ב-``verify`` כשהביצוע דלוק.
SHOWN_CHUNKS = 8
#: רשומות פתוחות בזיכרון (תהליך אחד). מעל זה נשכחות הישנות: ההחלטה כבר נרשמה, והתוצאה שלה תיסגר כ"יתומה" בהפעלה הבאה.
MAX_OPEN = 256


def join_policy(missing: int | None) -> str | None:
    """B34: ``CURRENT`` כשלא חסר דבר, ``ALL`` כשחסר, ``None`` כשהסוכן לא ספר (לעולם לא ``CURRENT``)."""
    return None if missing is None else fill.join_policy(missing)


def verify_enabled(conn) -> bool:
    """המתג של B30. כל כשל בקריאה = כבוי: הביצוע אינו נדלק מקריאה שנכשלה."""
    try:
        return get_setting(conn, SETTING) == "on"
    except Exception:                           # noqa: BLE001
        log.exception("brain: %s unreadable, verify-repair stays off", SETTING)
        return False


def parse_bad_chunks(raw) -> list[tuple[int, int]] | None:
    """``readback_failed_chunks`` -> ``[(partition, chunk_index)]`` מסודר וללא כפילויות. ``None`` = לא נאמר, ריק או פגום
    (רשימה ריקה היא "נבדק ותקין", ואין מה לתקן; רשימה עם ערך אחד פגום אינה נקראת בכלל)."""
    if not isinstance(raw, list) or not raw:
        return None
    out: set[tuple[int, int]] = set()
    for item in raw:
        if not isinstance(item, dict):
            return None
        part, chunk = item.get("partition"), item.get("chunk_index")
        for value in (part, chunk):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                return None
        out.add((part, chunk))
    return sorted(out)


def _pct(need: fill.Need) -> str | None:
    """כמה אחוז מהמחיצה חסר, אחרי בדיקה ש-``missing <= chunks``. ``None`` = לא נמדד."""
    if need.chunks is None or need.chunks <= 0 or need.missing > need.chunks:
        return None
    return f"{100 * need.missing / need.chunks:.0f}%"


class Completing:
    """המצב של B16/B30 בשרת אחד: אילו החלטות פתוחות. המפתחות כוללים את הגל."""

    def __init__(self):
        self._lock = threading.Lock()
        self._hold: dict[tuple[str, str, str], tuple[int | None, int | None]] = {}    # (גל, mac, מגירה) -> (שורה, מחיצה)
        self._verify: dict[tuple[str, str, str], tuple] = {}                            # (גל, mac, מגירה) -> הרשימה שנרשמה

    def observe(self, conn, round_row, mac: str, body: dict, *, mode: str) -> dict:
        """דיווח התקדמות -> ``{"verify": {serial: plan}}`` רק ב-``enforce`` עם המתג דלוק, אחרת ``{}``. לעולם אינו זורק."""
        try:
            return self._observe(conn, round_row, mac, body, mode)
        except Exception:                       # noqa: BLE001 -- מוח שנפל הוא שורת יומן, לא דיווח שנכשל
            log.exception("brain: the completing decision for %s failed", mac)
            return {}

    def _observe(self, conn, round_row, mac, body, mode) -> dict:
        if mode == OFF:
            return {}
        mode = mode if mode in (SHADOW, ENFORCE) else SHADOW
        session = body.get("session_id")
        if not isinstance(session, str):
            return {}
        targets = [t for t in body.get("targets") or [] if isinstance(t, dict)]
        self._holds(conn, round_row, mac, session, targets, mode)
        return self._verifies(conn, round_row, mac, session, targets, mode)

    # --- B16 + B34 -----------------------------------------------------------------------------------------

    def _holds(self, conn, round_row, mac, session, targets, mode) -> None:
        seen: set[tuple[str, str, str]] = set()
        for t in targets:
            need = fill.parse_need(t.get("rejoin"))
            if need is None:
                continue
            who = str(t.get("serial") or t.get("dev") or "?")
            key = (session, mac, who)
            seen.add(key)
            with self._lock:
                before = self._hold.get(key)
            if before is not None and before[1] == need.part:
                continue
            if before is not None:
                self._close(conn, key, f"הסוכן עבר למחיצה {need.part if need.part is not None else '?'}")
            self._open_hold(conn, round_row, mode, mac, who, key, need)
        with self._lock:
            ended = [k for k in self._hold if k[0] == session and k[1] == mac and k not in seen]
        by_who = {str(t.get("serial") or t.get("dev") or "?"): t for t in targets}
        for key in ended:
            self._close(conn, key, self._why_ended(by_who.get(key[2])))

    def _open_hold(self, conn, round_row, mode, mac, who, key, need: fill.Need) -> None:
        policy = join_policy(need.missing)
        pct = _pct(need)
        part = "לא נאמרה" if need.part is None else str(need.part)
        reason = (f"משכפל {mac} משלים את מגירה {who}: חסרות {need.missing} חתיכות"
                  f"{'' if pct is None else f' ({pct} מהמחיצה)'}, מחיצה {part}. הסבב לא נסגר והמחיצה לא נכשלת בגללה "
                  f"-- ההחזקה נאכפת בסוכן (drawerback.sh), והמוח רושם אותה. מדיניות ההצטרפות: {policy}")
        evidence = (f"mac={mac}", f"disk={who}", f"join={policy}", f"missing={need.missing}",
                    f"chunks={'unmeasured' if need.chunks is None else need.chunks}",
                    f"part={'unmeasured' if need.part is None else need.part}",
                    f"missing_share={'unmeasured' if pct is None else pct}",
                    f"away_s={'unmeasured' if need.away_s is None else f'{need.away_s:.0f}'}",
                    "held_by=agent/lib/restore/drawerback.sh:drawer_back")
        self._log({"mode": mode, "rule": RULE_HOLD, "action": ACTION_HOLD, "verdict": HOLD, "mac": mac, "disk": who,
                   "join": policy, "missing": need.missing, "enforced": False, "reason": reason})
        rid = memory.record(conn, mode=mode, rule=RULE_HOLD, action=ACTION_HOLD, verdict=HOLD, reason=reason,
                            evidence=evidence, point="D25", round_id=round_row["id"], entity=("disk", who))
        with self._lock:
            self._hold[key] = (rid, need.part)
            self._trim(self._hold)

    def _close(self, conn, key, outcome: str) -> None:
        with self._lock:
            rid, _part = self._hold.pop(key, (None, None))
        memory.resolve(conn, [rid], outcome)

    @staticmethod
    def _why_ended(t: dict | None) -> str:
        """מה הדיווח אומר על מגירה ששדה ה-``rejoin`` שלה ירד. לא מנחשים "הושלם": זה מה שהסוכן אומר, לא מה שהוכח."""
        if t is None:
            return "היעד יצא מהדיווח; ההשלמה לא הוכחה מכאן"
        state = str(t.get("state") or "unknown")
        if state == "failed":
            return f"ההשלמה נכשלה: {str(t.get('error') or 'בלי סיבה בדיווח')[:200]}"
        return f"השדה rejoin ירד מהדיווח, והמגירה במצב {state}"

    # --- B30 ---------------------------------------------------------------------------------------------------

    def _verifies(self, conn, round_row, mac, session, targets, mode) -> dict:
        plans: dict = {}
        on = None
        for t in targets:
            bad = parse_bad_chunks(t.get("readback_failed_chunks"))
            if bad is None:
                continue
            who = str(t.get("serial") or t.get("dev") or "?")
            key = (session, mac, who)
            if on is None:
                on = mode == ENFORCE and verify_enabled(conn)
            with self._lock:
                told = self._verify.get(key) == tuple(bad)
            if not told:
                self._open_verify(conn, round_row, mode, mac, who, bad, on)
                with self._lock:
                    self._verify[key] = tuple(bad)
                    self._trim(self._verify)
            if on:
                parts: dict[str, list[int]] = {}
                for part, chunk in bad:
                    parts.setdefault(str(part), []).append(chunk)
                plans[who] = {"verdict": REPAIR, "partitions": parts}
        return {"verify": plans} if plans else {}

    def _open_verify(self, conn, round_row, mode, mac, who, bad, on: bool) -> None:
        parts = sorted({p for p, _c in bad})
        shown = ",".join(f"p{p}#{c}" for p, c in bad[:SHOWN_CHUNKS]) + ("..." if len(bad) > SHOWN_CHUNKS else "")
        reason = (f"מגירה {who} ב-{mac} נכשלה באימות ב-{len(bad)} חתיכות (מחיצות {', '.join(map(str, parts))}). "
                  f"לא מוחקים ולא כותבים את כולה מחדש: מורידים רק אותן, דרך הזרימה של מגירה שחזרה. "
                  f"הביצוע {'דלוק' if on else 'כבוי (מתג ' + SETTING + ', ב-enforce בלבד)'}")
        evidence = (f"mac={mac}", f"disk={who}", f"bad_chunks={len(bad)}", f"partitions={len(parts)}", f"first={shown}",
                    "checked_against=index (xxh64; BLAKE3 where a .b3 exists)", "source_of_list=readback_failed_chunks",
                    f"execution={'on' if on else 'off'}")
        self._log({"mode": mode, "rule": RULE_VERIFY, "action": ACTION_VERIFY, "verdict": REPAIR, "mac": mac,
                   "disk": who, "bad_chunks": len(bad), "enforced": bool(on), "reason": reason})
        memory.record(conn, mode=mode, rule=RULE_VERIFY, action=ACTION_VERIFY, verdict=REPAIR, reason=reason,
                      evidence=evidence, round_id=round_row["id"], entity=("disk", who),
                      # #3750: ביצוע כבוי = אין פעולה לסגור. ביצוע דלוק נשאר פתוח: מי שסוגר אותו עוד לא נמצא (שאלה פתוחה ב-#3750)
                      outcome=None if on else memory.na(f"הביצוע כבוי (מתג {SETTING}): נרשם בלבד"))

    # --- משותף -------------------------------------------------------------------------------------------------

    @staticmethod
    def _trim(table: dict) -> None:
        while len(table) > MAX_OPEN:
            table.pop(next(iter(table)))

    def _log(self, fields: dict) -> None:
        stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        say = "נאכף" if fields.get("enforced") else "מחושב ונרשם בלבד"
        log.info("brain %s", json.dumps({"at": stamp, **fields, "say": say}, ensure_ascii=False))
