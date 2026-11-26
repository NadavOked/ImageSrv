"""#3064 (K5, ההשלמה): HTTP מול מעבר multicast -- מה המוח היה בוחר בכל סבב השלמה. **צל בלבד.**

**ההשלמה של היום לא זזה** (`server/rounds/repair.py`, #3170, #3213): חתיכה שחסרה ל-2 משכפלים ומעלה משודרת שוב
לכולם (D8), ומה שחסר רק למשכפל אחד יורד ב-HTTP (D9); משכפל שחסר לו גם מהמשותף מאזין, ואת שלו מוריד בסבב הבא (D10).
המודול הזה **רק רושם** מה המוח היה עושה במקום, ליד מה שהקוד עשה, עם הזמן החזוי לכל דרך ועם הזמן בפועל -- כדי שאפשר
יהיה למדוד התאמה (#3321). `enforce` אינו מפעיל כאן דבר: אין עדיין מנגנון שמבצע את הבחירה.

**הקלט -- מה שנמדד, לא מה שהונח.** `chunk_ledger` של הסבב (מה עמיד על כל דיסק), חברי הגל שעוד חיים, והדיסקים שהמנוע
הכשיל (`chunk_disk_failed`). מזה המוח מחשב לכל משכפל כמה חתיכות וכמה טווחים חסרים לו, ואת התוכנית של היום
(`repair.plan`), ובודק שהיא מסכימה עם מה שהמנוע עצמו אמר (`chunk_repair`). אי-הסכמה נרשמת בראיות.

**שתי הדרכים** (`choose`, פונקציה טהורה):

- **(א) HTTP לכל מה שחסר** (B8 כחלופה, B3): `T_A` = כל משכפל מוריד את שלו (X3: `4 + 0.025·טווחים + חתיכות ÷ r_http`
  שניות), לכל היותר `HTTP_CONC` במקביל (KT7).
- **(ב) מעבר multicast** (התוכנית של היום): `T_B` = החתיכות המשותפות ÷ `r_mc`, ואז ה-HTTP של מה שחסר רק למאזינים;
  מי שאינו מאזין מוריד במקביל למעבר.
- ``F`` = סכום החוסר של כל המשכפלים ÷ האיחוד שלו: כמה פעמים שידור אחד חוסך.

**הסדר** (הספים הם נקודת התחלה בקוד, מהמחקר -- לא הגדרות, נדב 14/11):

1. ``NO_NEW_HTTP`` (B15, B19) -- המקלטים זרקו חבילות (`RcvbufErrors`), נטשו חתיכות, או שדיסק הספרייה של השרת לחוץ
   (`psi_full` מעל `budget.PSI_FULL_MAX`). אין HTTP חדש; עדיפות ל-(ב). המדידה מגיעה מהמחזור האחרון של תקציב הרשת (K7).
2. ``HTTP_TAIL`` (B3, X2) -- זנב קטן: `T_A ≤ TAIL_S`. הכול ל-HTTP עכשיו.
3. ``MULTICAST`` (B6, X7) -- `F ≥ 1.5` וגם `T_B ≤ 1.2·T_A`.
4. ``HTTP`` (B6, X7) -- `T_A ≤ 0.8·T_B`.
5. ``UNDECIDED`` -- בין שני הספים: "לא הוכרע", והקוד של היום עומד (עיקרון 5: לא ברירת מחדל שקטה).

**היסטרזיס ו-epoch** (B13, B14, KT8, X8): ההחלטה נבחנת רק באירוע (סבב השלמה חדש), ונשארת עד האירוע הבא. מעבר לדרך
אחרת מזו שנבחרה במחיצה קודם -- רק אם עברו לפחות `DWELL_S` והדרך החדשה מהירה ב-`MARGIN` לפחות. אחרת נשארים.

**הקצבים נלמדים חיים** (B28): `r_http` -- חציון ההורדות האחרונות שנמדדו (R9, למטה), ואם אין -- `R_HTTP_START` (KT4).
`r_mc` -- המעבר הראשון של הגל הזה (`ChunkEngine.rate_outcome`), ואם עוד אין -- הגל האחרון שלימד, ואם אין -- `R_MC_START`
(X4). המקור של כל קצב נרשם בראיות.

**מה עוד נרשם:**

- **R9 -- כל הורדה ב-HTTP:** מי, כמה חתיכות ובתים, כמה טווחים, כמה זמן, ובאיזה קצב. מתחילה בסבב ההשלמה שהטיל אותה,
  ונגמרת בדיווח ``ended`` של אותו סבב (`on_pass`). בלי ``ended`` -- "לא נמדד", לא אפס.
- **KT3/KT5/KT6 -- חוזה ההשלמה (lease):** `max(300, 3 × חסר ÷ r_http)` שניות, ועוד מה שנשאר מ-`GRACE_S` אחרי הפעלה מחדש
  של השרת, ולעולם לא מעל `LEASE_CEILING_S`. ההחלטה נסגרת בסוף ההורדה: בתוך החוזה או חרגה ממנו. **הקוד של היום
  (`FETCH_SLOW_S`, `_stall_window`) הוא שמכשיל הורדה** -- החוזה נרשם בלבד.
- **U4 -- למה משכפל קיבל ``receive`` ולא ``fetch``** (D10): כמה חתיכות שרק לו חסרות ממתינות לסבב הבא.
- **B11/X14:** משכפל שמה שחסר רק לו גדול מ-`PRIVATE_CAP_BYTES` -- בראיות.
- **R12 -- בסוף הגל:** כמה חתיכות שודרו שוב ב-multicast (`multicast_chunks`), כמה ירדו ב-HTTP, וכמה סבבים. ``0`` הוא
  הראיה שלא נעשה שידור ממוקד.
- **תחזית מול מה שקרה:** החלטת הדרך נסגרת כשכל המשכפלים של הסבב דיווחו ``ended`` עליו (או כשהסבב הבא תוכנן, או בסוף
  הגל): כמה זמן הסבב לקח בפועל, מול הזמן שהמוח חזה לדרך שהקוד בחר. גל שנכשל או נעצר באמצע -- "לא נמדד".

**המצב** -- אותו מתג של הווטו (``brain_veto_mode``): ``off`` = דבר לא מחושב ולא נרשם. אחרת נרשם כ-``shadow``.
**כל מה שכאן לעולם אינו זורק** אל המנוע או אל הסוכן: כשל הוא שורת לוג.
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Callable

from server.brain import budget as brain_budget, memory
from server.rounds import repair

log = logging.getLogger("imagesrv.brain")

RULE = "K5"
ROUTE, FETCH, LEASE, WHY, TOTALS = "COMPLETE_ROUTE", "HTTP_FETCH", "FETCH_LEASE", "RECEIVE_NOT_FETCH", "REPAIR_TOTALS"
HTTP, MULTICAST, TAIL, GUARD, BAND = "HTTP", "MULTICAST", "HTTP_TAIL", "NO_NEW_HTTP", "UNDECIDED"
OFF, SHADOW = "off", "shadow"

#: X3 (מחקר, לא נמדד): שניות להורדה אחת = קבוע + לכל טווח + לכל חתיכה (`1 ÷ r_http`).
HTTP_FIXED_S, HTTP_PER_RANGE_S = 4.0, 0.025
#: X2: "זנב קטן" -- כל ההשלמה ב-HTTP בתוך דקה.
TAIL_S = 60.0
#: X7: `F ≥ 1.5`, `T_A ≤ 0.8·T_B`, `T_B ≤ 1.2·T_A`.
FANOUT_MIN, HTTP_WINS, MC_TOLERANCE = 1.5, 0.8, 1.2
#: X8 / KT8: מעבר רק אחרי 30 ש', ורק אם מהיר ב-15% לפחות.
DWELL_S, MARGIN = 30.0, 0.15
#: X14: תקרה להשלמה פרטית, כמו Apple ASR (32MB). מחקר.
PRIVATE_CAP_BYTES = 32 * 10 ** 6
#: KT4: `r_http` התחלתי -- 42 חתיכות/ש', הנמוך שנמדד על ברזל (13/11, ses_108caf1d).
R_HTTP_START = 42.0
#: X4: `r_mc` התחלתי -- 36 חתיכות/ש' (מחקר; M13 תסביר למה 36).
R_MC_START = 36.0
#: KT7: הורדות HTTP במקביל בכל השרת (ולכל משכפל אחת). מעבר ל-2 רק אחרי M14.
HTTP_CONC = 2
#: KT3: רצפה ומכפיל של החוזה. KT6: התקרה המוחלטת. KT5: החסד אחרי הפעלה מחדש של השרת. הערכות מטיוטת התכנון.
LEASE_FLOOR_S, LEASE_FACTOR, LEASE_CEILING_S, GRACE_S = 300.0, 3.0, 1800.0, 300.0
#: B28: כמה הורדות אחרונות נכנסות לחציון של `r_http`.
WINDOW = 5
#: חתיכה במנוע החתיכות (`chunksend.CHUNK_BYTES`): מה שהדיסק כותב.
CHUNK_BYTES = 4 * 1024 * 1024


# --- ההחלטה (טהורה) ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Need:
    """מה שחסר למשכפל אחד: חתיכות וטווחים (איחוד על הדיסקים שלו, כמו `repair.plan`)."""
    mac: str
    chunks: int
    ranges: int


@dataclass(frozen=True)
class Guard:
    """מהמחזור האחרון של K7 (`budget.Sample`). ``None`` = לא נמדד (לעולם לא "נקי")."""
    rx_dropped: int | None = None
    abandoned: int | None = None
    io_full: float | None = None


@dataclass(frozen=True)
class Route:
    verdict: str                     # NO_NEW_HTTP / HTTP_TAIL / MULTICAST / HTTP / UNDECIDED
    route: str                       # HTTP / MULTICAST / WAIT / TODAY -- מה היה קורה
    reason: str
    t_a: float
    t_b: float
    fanout: float | None
    evidence: tuple[str, ...] = ()


def t_http(n: Need, r_http: float) -> float:
    return HTTP_FIXED_S + HTTP_PER_RANGE_S * n.ranges + n.chunks / r_http


def t_all_http(needs: list[Need], r_http: float, conc: int = HTTP_CONC) -> float:
    """כמה זמן לוקח שכולם יורידו, `conc` במקביל: לא פחות מהארוכה, ולא פחות מהסכום חלקי המקביליות."""
    times = [t_http(n, r_http) for n in needs if n.chunks > 0]
    return max(max(times), sum(times) / conc) if times else 0.0


def guard_reasons(g: Guard, psi_max: float = brain_budget.PSI_FULL_MAX) -> list[str]:
    out = []
    if g.rx_dropped:
        out.append(f"המקלטים זרקו {g.rx_dropped} חבילות (RcvbufErrors)")
    if g.abandoned:
        out.append(f"המקלטים נטשו {g.abandoned} חתיכות באמצע")
    if g.io_full is not None and g.io_full > psi_max:
        out.append(f"דיסק הספרייה של השרת לחוץ ({g.io_full:.2f} מהזמן, מעל {psi_max:g})")
    return out


def choose(whole: list[Need], shared: int, alone_receivers: list[Need], alone_fetchers: list[Need],
           r_http: float, r_mc: float, guard: Guard, prev: tuple[str, float] | None = None,
           now: float = 0.0) -> Route:
    """``whole`` = כל מה שחסר לכל משכפל (דרך א). ``shared`` = החתיכות שהקוד משדר שוב; ``alone_*`` = מה שחסר רק להם,
    למאזינים (ממתין לסבב הבא) ולמורידים (במקביל למעבר). ``prev`` = (הדרך שנבחרה קודם במחיצה, מתי)."""
    t_a = t_all_http(whole, r_http)
    t_b = max(shared / r_mc + t_all_http(alone_receivers, r_http), t_all_http(alone_fetchers, r_http)) if shared \
        else t_all_http(alone_fetchers + alone_receivers, r_http)
    union = shared + sum(n.chunks for n in alone_receivers + alone_fetchers)
    fanout = sum(n.chunks for n in whole) / union if union else None
    evidence = [f"T_A={t_a:.0f}s", f"T_B={t_b:.0f}s", f"F={'unmeasured' if fanout is None else f'{fanout:.2f}'}",
                f"shared={shared}", f"machines={len(whole)}", f"r_http={r_http:.1f}", f"r_mc={r_mc:.1f}"]
    # #3618: `Guard.rx_dropped` הוא `RcvbufErrors` של הקרנל (S44), לא מונה `rx_dropped` של הכרטיס: בראיות הוא נקרא `rcvbuf`, כמו ב-K7 (#3597)
    for label, got in (("rcvbuf", guard.rx_dropped), ("abandoned", guard.abandoned), ("io_full", guard.io_full)):
        evidence.append(f"{label}={'unmeasured' if got is None else got}")
    over = [f"{n.mac}:{n.chunks}" for n in alone_receivers + alone_fetchers if n.chunks * CHUNK_BYTES > PRIVATE_CAP_BYTES]
    if over:
        evidence.append(f"private_over_cap={','.join(over)}")      # B11/X14: עם מצטרפים, היו ממתינים למעבר

    def out(verdict: str, route: str, why: str) -> Route:
        return Route(verdict, route, why, t_a, t_b, fanout, tuple(evidence))

    stop = guard_reasons(guard)
    if stop:
        return out(GUARD, MULTICAST if shared else "WAIT", f"{'; '.join(stop)}: אין HTTP חדש, עדיפות למעבר multicast")
    if not union:
        return out(BAND, "TODAY", "לא חסר כלום לפי היומן: אין מה להחליט")
    if t_a <= TAIL_S:
        return out(TAIL, HTTP, f"זנב קטן: כל ההשלמה ב-HTTP בתוך {t_a:.0f}s (≤ {TAIL_S:g}s)")
    if not shared:
        return out(HTTP, HTTP, "אף חתיכה אינה חסרה ליותר ממשכפל אחד: (א) ו-(ב) הן אותן הורדות, ואין מעבר לבחור")
    if fanout is not None and fanout >= FANOUT_MIN and t_b <= MC_TOLERANCE * t_a:
        pick, why = MULTICAST, f"F={fanout:.2f} ≥ {FANOUT_MIN:g} ו-T_B {t_b:.0f}s ≤ {MC_TOLERANCE:g}·T_A ({t_a:.0f}s)"
    elif t_a <= HTTP_WINS * t_b:
        pick, why = HTTP, f"T_A {t_a:.0f}s ≤ {HTTP_WINS:g}·T_B ({t_b:.0f}s)"
    else:
        return out(BAND, prev[0] if prev else "TODAY",
                   f"בין הספים (T_A {t_a:.0f}s, T_B {t_b:.0f}s, F {'?' if fanout is None else f'{fanout:.2f}'}): לא הוכרע"
                   + (f", נשארים ב-{prev[0]}" if prev else ", הקוד של היום עומד"))
    if prev is not None and prev[0] in (HTTP, MULTICAST) and prev[0] != pick:
        old, new = (t_a, t_b) if prev[0] == HTTP else (t_b, t_a)
        gain = (old - new) / old if old > 0 else 0.0
        if now - prev[1] < DWELL_S or gain < MARGIN:
            return out(prev[0], prev[0], f"היסטרזיס: {why}, אבל עברו {now - prev[1]:.0f}s (נדרשים {DWELL_S:g}) ו-"
                                         f"השיפור {gain:.0%} (נדרש {MARGIN:.0%}) -- נשארים ב-{prev[0]}")
    return out(pick, pick, why)


def lease_s(chunks: int, r_http: float, uptime_s: float | None) -> float:
    """KT3: `max(300, 3 × חסר ÷ r_http)`, ועוד מה שנשאר מהחסד אחרי הפעלה מחדש (KT5); לעולם לא מעל התקרה (KT6)."""
    base = max(LEASE_FLOOR_S, LEASE_FACTOR * chunks / r_http)
    grace = max(GRACE_S - uptime_s, 0.0) if uptime_s is not None else 0.0
    return min(base + grace, LEASE_CEILING_S)


def r_http_of(samples: list[float]) -> tuple[float, str]:
    good = [s for s in samples[-WINDOW:] if s > 0]
    if not good:
        return R_HTTP_START, "KT4 (התחלה, ברזל 13/11)"
    return statistics.median(good), f"חציון {len(good)} הורדות אחרונות"


# --- המעקב בשרת ----------------------------------------------------------------------------------------------


def _lacks(chunks: int, durable: dict[str, dict[str, repair.Ranges]]) -> dict[str, repair.Ranges]:
    """מחשב ← מה שחסר לו (איחוד על הדיסקים שלו, כמו `repair.plan`). מחשב שלא חסר לו כלום אינו כאן."""
    out = {}
    for mac, disks in durable.items():
        lacks: repair.Ranges = []
        for have in disks.values():
            lacks = repair.union(lacks, repair.missing(chunks, have))
        if lacks:
            out[mac] = lacks
    return out


def _without(chunks: int, ranges: repair.Ranges, shared: repair.Ranges) -> repair.Ranges:
    """``ranges`` פחות ``shared``: מה שחסר רק למשכפל, אחרי שהמעבר המשותף ישלים את שלו."""
    return repair.missing(chunks, repair.union(repair.missing(chunks, ranges), shared))


def _mismatch(detail: str, shared: int, plan: repair.Plan) -> tuple[str, ...]:
    """מה שהמנוע אמר ב-`chunk_repair` מול מה שחושב מהיומן. שונה = הצילום אינו התוכנית שלו (דיווח שהגיע בין
    התוכנית לאירוע): בראיות, ולא "תקין". לא נקרא = "לא נקרא"."""
    told = re.search(r"multicast=(\d+) to (\d+)", detail or "")
    if told is None:
        return ("engine_plan=unread",)
    mc, to = int(told.group(1)), int(told.group(2))
    if mc != shared or to != len(plan.receive):
        return (f"engine_plan=differs (engine multicast={mc} to {to}, ledger {shared} to {len(plan.receive)})",)
    return ("engine_plan=matches",)


class Tracker:
    """המצב של הכלל בשרת אחד. `chunk` (המנוע) ו-`budget` (K7) מחוברים ב-`server.app` אחרי שנבנו."""

    def __init__(self, conn, *, chunk=None, budget=None, mode: Callable[[], str] | None = None,
                 clock: Callable[[], float] = time.monotonic, started: float | None = None):
        self.conn, self.chunk, self.budget = conn, chunk, budget
        self._mode_fn = mode
        self._clock = clock
        self._started = clock() if started is None else started
        self._lock = threading.Lock()
        self._failed: dict[str, set[tuple[str, str]]] = {}
        self._prev: dict[tuple[str, int], tuple[str, float]] = {}
        #: (wave, part) -> (row, מתי, החזוי לדרך של היום, round, המשכפלים שעוד לא דיווחו ended)
        self._open: dict[tuple[str, int], list] = {}
        #: (wave, part, mac) -> [מתי, חתיכות, טווחים, חוזה, row של החוזה, round]
        self._fetch: dict[tuple[str, int, str], list] = {}
        self._samples: list[float] | None = None

    # -- הכניסות: לעולם אינן זורקות --

    def on_event(self, event: str, detail: str, session_id: str) -> None:
        try:
            if self._mode() == OFF:
                return
            if event == "chunk_disk_failed":
                mac, _, rest = detail.partition(" ")
                serial = rest.split(":", 1)[0].strip()
                with self._lock:
                    self._failed.setdefault(session_id, set()).add((mac, serial))
            elif event == "chunk_repair":
                self._repair(session_id, detail)
            elif event in ("send_done", "send_failed", "send_stopped"):
                self._end(session_id, event)
        except Exception:                       # noqa: BLE001 -- מוח שנפל הוא שורת לוג, לא גל שנפל
            log.exception("brain: the completion route of %s failed on %s", session_id, event)

    def on_pass(self, session_id: object, mac: str, body: dict) -> None:
        """דיווח `chunk-pass` של משכפל, אחרי שהמנוע קיבל אותו: ``ended`` סוגר את ההורדה שלו ואת חלקו בסבב."""
        try:
            if not isinstance(body, dict) or body.get("state") != "ended" or self._mode() == OFF:
                return
            part, rnd = body.get("part"), body.get("pass")
            if not isinstance(part, int) or isinstance(part, bool) or not isinstance(rnd, int) or isinstance(rnd, bool):
                return
            self._ended(str(session_id), part, rnd, mac)
        except Exception:                       # noqa: BLE001
            log.exception("brain: the completion route of %s failed on a pass report", session_id)

    # -- פנים --

    def _mode(self) -> str:
        if self._mode_fn is not None:
            return self._mode_fn()
        from server.brain import veto            # כמו ב-`budget.production`: המתג מגיע מהווטו
        return veto.mode(self.conn)

    def _guard(self) -> Guard:
        s = getattr(self.budget, "sample", None)
        return Guard(s.rx_dropped, s.abandoned, s.io_full) if s is not None else Guard()

    def _r_http(self) -> tuple[float, str]:
        with self._lock:
            if self._samples is None:
                self._samples = self._remembered()
            return r_http_of(self._samples)

    def _remembered(self) -> list[float]:
        """B28: הקצבים של ההורדות האחרונות שנמדדו, מהזיכרון (החדשה אחרונה)."""
        out = []
        for d in memory.decisions(self.conn, rule=RULE, limit=200):
            if d.get("action") != FETCH:
                continue
            got = next((e.split("=", 1)[1] for e in d.get("evidence") or [] if str(e).startswith("rate_cps=")), None)
            if got is None:
                continue
            try:
                out.append(float(got))
            except (TypeError, ValueError):
                continue
            if len(out) >= WINDOW:
                break
        return out[::-1]

    def _r_mc(self, session_id: str) -> tuple[float, str]:
        got = self.chunk.rate_outcome(session_id) if self.chunk is not None else {}
        chunks, seconds = got.get("chunks"), got.get("first_pass_s")
        if isinstance(chunks, int) and chunks > 0 and isinstance(seconds, (int, float)) and seconds >= 1:
            return chunks / seconds, "המעבר הראשון של הגל הזה"
        from server.rounds import chunk_rate     # noqa: PLC0415 -- chunk_rate מייבא את budget
        last = chunk_rate.last_sample(self.conn) or {}
        chunks, seconds = last.get("chunks"), last.get("first_pass_s")
        if isinstance(chunks, int) and chunks > 0 and isinstance(seconds, (int, float)) and seconds >= 1:
            return chunks / seconds, f"הגל האחרון שלימד ({last.get('session_id')})"
        return R_MC_START, "X4 (התחלה, מחקר)"

    def _durable(self, session_id: str, part: int) -> tuple[int | None, dict[str, dict[str, repair.Ranges]]]:
        """(חתיכות המחיצה, מחשב ← serial ← עמיד), רק משכפלים חיים ודיסקים שלא נכשלו -- מ-`chunk_ledger`."""
        alive = {r["mac"] for r in self.conn.execute(
            "SELECT mac, state FROM session_members WHERE session_id = ?", (session_id,))
            if r["state"] not in ("done", "failed", "partial", "lost")}
        with self._lock:
            failed = set(self._failed.get(session_id, ()))
        chunks: int | None = None
        out: dict[str, dict[str, repair.Ranges]] = {}
        for row in self.conn.execute("SELECT mac, serial, chunks, ranges_json FROM chunk_ledger"
                                     " WHERE round_id = ? AND part = ?", (session_id, part)):
            if row["mac"] not in alive or (row["mac"], row["serial"]) in failed:
                continue
            chunks = row["chunks"]
            out.setdefault(row["mac"], {})[row["serial"]] = [tuple(r) for r in json.loads(row["ranges_json"])]
        return chunks, out

    def _repair(self, session_id: str, detail: str) -> None:
        st = self.chunk.status() if self.chunk is not None else None
        no_file = getattr(self.chunk, "no_file", None)
        if not st or st.get("session_id") != session_id or (no_file is not None and no_file(session_id)):
            return                               # הפצה ישירה: אין קובץ להוריד ממנו, ואין מה לבחור
        part, rnd, now = st["partition"], st["round"], self._clock()
        # המנוע מתכנן סבב חדש רק כשכל מי שהיה בקודם דיווח או נכשל: עד כאן הסבב הקודם, ומה שהורד בו
        self._close_round(session_id, part, now, "עד שהסבב הבא תוכנן")
        for key in [k for k in self._fetch if k[:2] == (session_id, part)]:
            with self._lock:
                fetch = self._fetch.pop(key, None)
            if fetch is not None:
                self._measured(session_id, part, key[2], fetch, now)
        chunks, durable = self._durable(session_id, part)
        if not chunks:
            return
        plan = repair.plan(chunks, durable, repairs_done=1)
        lacks = _lacks(chunks, durable)
        whole = [Need(m, repair.count(r), len(r)) for m, r in sorted(lacks.items())]
        receivers = [Need(m, repair.count(own), len(own)) for m in sorted(plan.receive)
                     if (own := _without(chunks, lacks.get(m, []), plan.multicast))]
        fetchers = [Need(m, repair.count(r), len(r)) for m, r in sorted(plan.fetch.items())]
        shared = repair.count(plan.multicast)
        r_http, http_from = self._r_http()
        r_mc, mc_from = self._r_mc(session_id)
        with self._lock:
            prev = self._prev.get((session_id, part))
        got = choose(whole, shared, receivers, fetchers, r_http, r_mc, self._guard(), prev, now)
        if got.verdict in (HTTP, MULTICAST, TAIL):
            with self._lock:
                self._prev[(session_id, part)] = (got.route, now) if prev is None or prev[0] != got.route else prev
        today = MULTICAST if shared else HTTP
        t_today = got.t_b if today == MULTICAST else got.t_a
        mismatch = _mismatch(detail, shared, plan)
        evidence = (f"part={part}", f"round={rnd}", f"today={today}", f"agree={got.route in (today, 'TODAY')}",
                    f"r_http_from={http_from}", f"r_mc_from={mc_from}", *got.evidence, *mismatch)
        row = memory.record(self.conn, mode=SHADOW, rule=RULE, action=ROUTE, verdict=got.verdict, round_id=session_id,
                            reason=f"{got.reason}. צל: הקוד של היום הריץ {today} -- שום דבר לא זז",
                            evidence=evidence, entity=("round", session_id),
                            predicted=round(t_today, 1) if t_today > 0 else None, unit="s")   # #3446: מה שנמדד אחר כך
        self._log(ROUTE, got.verdict, session_id, {"route": got.route, "today": today, "reason": got.reason,
                                                    "evidence": list(evidence)})
        with self._lock:
            self._open[(session_id, part)] = [row, now, t_today, rnd, set(plan.receive) | set(plan.fetch)]
        for n in receivers:                      # U4: למה receive ולא fetch (D10)
            memory.record(self.conn, mode=SHADOW, rule=RULE, action=WHY, verdict="D10", round_id=session_id,
                          reason=f"חסרות לו גם חתיכות שמשודרות שוב לכולם ({shared}): מאזין למעבר, ואת {n.chunks}"
                                 f" החתיכות שרק לו חסרות ({n.ranges} טווחים) מוריד בסבב הבא",
                          evidence=(f"part={part}", f"round={rnd}", f"own_chunks={n.chunks}", f"shared={shared}"),
                          entity=("machine", n.mac), outcome="הקוד של היום: receive")
        uptime = now - self._started
        for n in fetchers:                       # R9 מתחיל כאן, והחוזה (KT3/KT5/KT6) נרשם
            lease = lease_s(n.chunks, r_http, uptime)
            lease_row = memory.record(
                self.conn, mode=SHADOW, rule=RULE, action=LEASE, verdict="LEASE", round_id=session_id,
                reason=f"חוזה השלמה של {lease:.0f}s ל-{n.chunks} חתיכות (r_http {r_http:.1f}/s, {http_from})"
                       + (f", כולל חסד אחרי הפעלה מחדש ({max(GRACE_S - uptime, 0):.0f}s)" if uptime < GRACE_S else "")
                       + ". צל: מי שמכשיל הורדה הוא הקוד של היום",
                evidence=(f"part={part}", f"round={rnd}", f"chunks={n.chunks}", f"ranges={n.ranges}",
                          f"lease_s={lease:.0f}", f"uptime_s={uptime:.0f}"),
                entity=("machine", n.mac), predicted=round(lease, 1), unit="s")   # #3446
            with self._lock:
                self._fetch[(session_id, part, n.mac)] = [now, n.chunks, n.ranges, lease, lease_row, rnd]

    def _ended(self, session_id: str, part: int, rnd: int, mac: str) -> None:
        now = self._clock()
        with self._lock:
            fetch = self._fetch.get((session_id, part, mac))
            if fetch is not None and rnd >= fetch[5]:
                del self._fetch[(session_id, part, mac)]
            else:
                fetch = None
            open_ = self._open.get((session_id, part))
            finished = None
            if open_ is not None and rnd >= open_[3]:
                open_[4].discard(mac)
                if not open_[4]:
                    finished = self._open.pop((session_id, part))
        if fetch is not None:
            self._measured(session_id, part, mac, fetch, now)
        if finished is not None:
            with self._lock:
                self._open[(session_id, part)] = finished
            self._close_round(session_id, part, now, "כל המשכפלים דיווחו ended")

    def _measured(self, session_id: str, part: int, mac: str, fetch: list, now: float) -> None:
        began, planned, ranges, lease, lease_row, rnd = fetch
        chunks, durable = self._durable(session_id, part)
        left = repair.count(_lacks(chunks, {mac: durable[mac]}).get(mac, [])) if chunks and mac in durable else None
        done = planned - left if left is not None else None
        took = now - began
        rate = done / took if done and done > 0 and took > 0 else None
        if rate is not None:
            with self._lock:
                self._samples = (self._samples or []) + [rate]
        evidence = (f"part={part}", f"round={rnd}", f"mac={mac}", f"planned_chunks={planned}", f"ranges={ranges}",
                    f"chunks={'unmeasured' if done is None else done}",
                    f"bytes={'unmeasured' if done is None else done * CHUNK_BYTES}", f"seconds={took:.1f}",
                    f"rate_cps={'unmeasured' if rate is None else f'{rate:.2f}'}",
                    f"rate_mbps={'unmeasured' if rate is None else f'{rate * CHUNK_BYTES / 1e6:.1f}'}")
        memory.record(self.conn, mode=SHADOW, rule=RULE, action=FETCH, verdict="MEASURED" if rate else "UNMEASURED",
                      reason=(f"הורדה של {done} חתיכות ב-{took:.0f}s ({rate:.1f} חתיכות/ש')" if rate
                              else f"ההורדה הסתיימה אחרי {took:.0f}s, והקצב לא נמדד (היומן לא נקרא או לא גדל)"),
                      evidence=evidence, round_id=session_id, entity=("machine", mac), outcome="נמדד")
        memory.resolve(self.conn, [lease_row], f"ההורדה הסתיימה אחרי {took:.0f}s, "
                                               f"{'בתוך החוזה' if took <= lease else 'מעבר לחוזה'} של {lease:.0f}s",
                       actual=round(took, 1))   # #3446: `matched` נשאר NULL -- אין הגדרת התאמה ל-K5 (#3321)

    def _close_round(self, session_id: str, part: int | None, now: float, until: str | None) -> None:
        """הסבבים הפתוחים של הגל (או של מחיצה אחת): ``until`` = עד מתי נמדד; ``None`` = לא נמדד (הגל נכשל או נעצר)."""
        with self._lock:
            keys = [k for k in self._open if k[0] == session_id and (part is None or k[1] == part)]
            rounds = [self._open.pop(k) for k in keys]
        for row, began, t_today, *_rest in rounds:
            if until is None:
                memory.resolve(self.conn, [row], "הגל נכשל או נעצר באמצע הסבב: משך הסבב לא נמדד")
                continue
            took = now - began
            ratio = f" (בפועל/חזוי {took / t_today:.2f})" if t_today > 0 else ""
            memory.resolve(self.conn, [row], f"הסבב לקח בפועל {took:.0f}s ({until}); "
                                             f"החזוי לדרך של הקוד {t_today:.0f}s{ratio}",
                           actual=round(took, 1) if t_today > 0 else None)   # #3446: בלי `predicted` אין מה להשוות

    def _end(self, session_id: str, event: str) -> None:
        now, done = self._clock(), event == "send_done"
        self._close_round(session_id, None, now, "עד סוף הגל" if done else None)
        with self._lock:
            fetches = [(k, self._fetch.pop(k)) for k in [k for k in self._fetch if k[0] == session_id]]
            self._failed.pop(session_id, None)
            self._prev = {k: v for k, v in self._prev.items() if k[0] != session_id}
        for (_sid, part, mac), fetch in fetches:
            if done:
                self._measured(session_id, part, mac, fetch, now)
            else:
                memory.resolve(self.conn, [fetch[4]], f"הגל הסתיים ({event}) לפני שההורדה דיווחה על סיומה: לא נמדד")
        st = self.chunk.status() if self.chunk is not None else None
        if not st or st.get("session_id") != session_id:
            return
        mc, fetched, rounds = st.get("multicast_chunks"), st.get("fetched_chunks"), st.get("repair_rounds")
        verdict = "UNMEASURED" if not isinstance(mc, int) else "NO_TARGETED_MULTICAST" if mc == 0 else "TARGETED"
        evidence = (f"multicast_chunks={mc}", f"fetched_chunks={fetched}", f"repair_rounds={rounds}", f"ended={event}")
        memory.record(self.conn, mode=SHADOW, rule=RULE, action=TOTALS, verdict=verdict, evidence=evidence,
                      reason=f"בגל: {mc} חתיכות שודרו שוב ב-multicast, {fetched} ירדו ב-HTTP, {rounds} סבבי השלמה",
                      round_id=session_id, entity=("round", session_id), outcome="נמדד")
        self._log(TOTALS, verdict, session_id, {"evidence": list(evidence)})

    @staticmethod
    def _log(action: str, verdict: str, session_id: str, fields: dict) -> None:
        log.info("brain %s", json.dumps({"mode": SHADOW, "rule": RULE, "action": action, "verdict": verdict,
                                          "round": session_id, **fields,
                                          "say": "מחושב ונרשם בלבד (צל)"}, ensure_ascii=False))
