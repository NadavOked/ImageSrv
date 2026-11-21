"""#3064 (K12): כוח העיבוד של השרת -- כמה גלים הוא מחזיק במקביל לפני רוויה, והחלטה בצל לפני פתיחת סבב או גל.

**בקשת נדב, 15/11:** "המוח צריך לדעת כמה כוח עיבוד יש לשרת, שיוכל לפתוח סבבים."

**מה נמדד** (קריאה בלבד, מהקרנל של השרת; ``null`` = לא נמדד, **לעולם לא 0**):

* מספר הליבות שהשרת רשאי להשתמש בהן (`cores`): הזיקה של התהליך (`sched_getaffinity`), ואם ה-cgroup של היחידה מגביל
  (`cpu.max`) -- הנמוך מבין השניים.
* צריכת המעבד של היחידה בזמן גל (V16, `server_senses`): `usage_usec` של ה-cgroup שלה. **היא כוללת תהליכי-בן שעוד חיים**
  (`zstd`, `chunkcast`, מי שהשרת הפעיל), שמונה התהליך (`/proc/<pid>/stat`, V8) אינו רואה עד שהם נקצרים. בלי cgroup
  (פיתוח, ווינדוס) -- סכום V8: השרת, הילדים שנקצרו ו-`chunkcast`, ורק כשכולם נקראו.
* לחץ המעבד **עכשיו**: PSI `some avg10` -- הקרנל כבר מחשב אותו, בלי דגימה ובלי המתנה בבקשה.

**מה נלמד** (`learn`): מכל גל שנמדד ב-`server_io` -- ליבות לגל (חציון), ליבות ל-Mb/s (חציון, לפי `nic_tx_mbps` של
הכרטיס), ומשך הגל (חציון). **סטטיסטיקה פשוטה, בלי מודל** (כלל הלמידה במוח). גל בלי מספר מעבד או בלי קצב אינו נספר, ונאמר
כמה דולגו. מפחות מ-`MIN_WAVES` גלים -- "עוד לא נלמד".

**התקרה** (`ceiling`): `floor(cores x HEADROOM / ליבות-לגל)` -- כמה גלים במקביל לפני רוויה.

**ההכרעה** (`judge`, טהורה) לפני פתיחת סבב או גל: ``OPEN``, ``WAIT`` (עם חיזוי כשיש), או ``UNDECIDED`` כשאין מדידה
(ליבות, למידה או PSI לא נקראו). גל ראשון תמיד ``OPEN`` מהתקרה (אין ממה להמתין), אבל לחץ מעבד קיים מחזיק גם אותו.

**⚠️ שני הספים הם ניחוש מנומק, לא מדידה** (עיקרון 5ב): `HEADROOM` ו-`PSI_BUSY`. אין עדיין גל שנמדד בו המעבד על ברזל.

**המצב:** ההכרעה **נרשמת בצל** ב-``brain_decisions`` (כלל K12), ואינה חוסמת. ``brain_cpu_gate`` (כבוי כברירת מחדל)
הוא המתג היחיד שמחזיק בפועל: ``WAIT`` מסרב לפתוח סבב, ומשאיר את `_attach_wave` לדופק הבא. ``UNDECIDED`` לעולם אינו
מחזיק. המתג של המוח (``brain_veto_mode`` = ``off``) מכבה גם את זה. **לעולם אינו זורק** אל הסבב.
"""

from __future__ import annotations

import logging
import math
import os
import statistics
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from server.brain import memory
from server.db import get_setting

log = logging.getLogger("imagesrv.brain")

SETTING = "brain_cpu_gate"
LABEL_HE = "המוח: שער מעבד לפני פתיחת גל"
RULE, ACTION = "K12", "CPU_GATE"
OPEN, WAIT, UNDECIDED, NOT_RUN = "OPEN", "WAIT", "UNDECIDED", "OFF"
#: ⚠️ ניחוש: כמה מהליבות מותר לגלים (השאר: קונסולה, DB, ה-HTTP של האימג', הקליטה). לא נמדד.
HEADROOM = 0.75
#: ⚠️ ניחוש, כמו `budget.PSI_FULL_MAX` (0.2): מעליו המעבד כבר במחסור, וגל נוסף יחמיר.
PSI_BUSY = 0.20
#: כמה גלים שנמדדו לפחות לפני שסומכים על הלמידה, וכמה אחרונים נקראים. הערכה, כמו `slotfill.MIN_WAVES`.
MIN_WAVES = 3
LEARN_WAVES = 20
#: הקריאה החוזרת של אותה החלטה נרשמת פעם אחת בחלון הזה (הדופק מנסה לפתוח גל בכל hello).
DEDUPE_S = 30.0
PROC = Path("/proc")
CGROUP = Path("/sys/fs/cgroup")
SHADOW_HE = "צל: ההחלטה נרשמה בלבד, הקוד הקיים פתח את הסבב"
HELD_HE = f"הוחזק בפועל: {SETTING} דלוק"


# --- החושים ----------------------------------------------------------------------------------------------------


def _text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="ascii", errors="replace")
    except OSError:
        return None


def _own_cgroup(proc: Path, cgroup: Path) -> Path | None:
    """התיקייה של ה-cgroup של התהליך (v2: שורה ``0::/path`` ב-`/proc/self/cgroup`). None = לא נקרא, או v1."""
    for line in (_text(proc / "self" / "cgroup") or "").splitlines():
        head, _, path = line.partition("::")
        if head == "0" and path.startswith("/"):
            return cgroup / path.lstrip("/")
    return None


def unit_usage_usec(proc: Path = PROC, cgroup: Path = CGROUP) -> int | None:
    """V16: ``usage_usec`` של ה-cgroup של היחידה (כל התהליכים שבה, כולל ילדים חיים). None = לא נקרא."""
    here = _own_cgroup(proc, cgroup)
    for line in ((_text(here / "cpu.stat") or "") if here is not None else "").splitlines():
        key, _, value = line.partition(" ")
        if key == "usage_usec" and value.strip().isdigit():
            return int(value)
    return None


def _affinity() -> int | None:
    try:
        return len(os.sched_getaffinity(0))
    except (AttributeError, OSError):
        return os.cpu_count()


def cores(proc: Path = PROC, cgroup: Path = CGROUP, affinity: Callable[[], int | None] = _affinity
          ) -> tuple[float | None, str]:
    """(ליבות, מאיפה). הזיקה, ואם `cpu.max` של היחידה נמוך ממנה -- הוא. None = אף אחד לא נקרא."""
    n = affinity()
    got, source = (float(n), "affinity") if isinstance(n, int) and not isinstance(n, bool) and n > 0 else (None, "")
    here = _own_cgroup(proc, cgroup)
    fields = ((_text(here / "cpu.max") or "") if here is not None else "").split()
    if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit() and int(fields[1]) > 0 and int(fields[0]) > 0:
        quota = int(fields[0]) / int(fields[1])
        if got is None or quota < got:
            return quota, "cpu.max"
    return got, source or "לא נקרא"


def psi_avg10(proc: Path = PROC) -> float | None:
    """לחץ המעבד ב-10 השניות האחרונות, שבר בין 0 ל-1 (`some avg10`). None = לא נקרא."""
    for line in (_text(proc / "pressure" / "cpu") or "").splitlines():
        kind, *fields = line.split() or [""]
        raw = next((f.split("=", 1)[1] for f in fields if f.startswith("avg10=")), None)
        if kind == "some" and raw is not None:
            try:
                return min(max(float(raw) / 100.0, 0.0), 1.0)
            except ValueError:
                return None
    return None


# --- הלמידה ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Learned:
    waves: int                     # גלים שנמדדו (מעבד וקצב שניהם)
    skipped: int                   # גלים שנקראו ולא נמדדו: לא נספרים, ונאמר כמה
    per_wave: float | None         # ליבות לגל (חציון)
    per_mbps: float | None         # ליבות ל-Mb/s (חציון)
    seconds: float | None          # משך גל (חציון)


def _num(value: object, *, positive: bool = False) -> float | None:
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0 and (value > 0 or not positive)
    return float(value) if ok else None


def wave_cores(result: dict) -> float | None:
    """כמה ליבות הגל עלה: ה-cgroup של היחידה כשנקרא, ואחרת סכום V8 -- ורק כשכל שלושת המרכיבים נקראו."""
    server = result.get("server") if isinstance(result.get("server"), dict) else {}
    unit = _num(server.get("unit_cpu"))
    if unit is not None:
        return unit
    parts = [_num(server.get(k)) for k in ("server_cpu", "children_cpu", "chunkcast_cpu")]
    return None if None in parts else sum(parts)


def learn(conn, limit: int = LEARN_WAVES) -> Learned:
    """ה-`limit` הגלים האחרונים ב-`server_io` שנמדד בהם מעבד וקצב. כשל בקריאה = לא נלמד כלום."""
    import json                                    # noqa: PLC0415 -- רק כאן
    cores_, per_mbps, seconds, skipped = [], [], [], 0
    try:
        rows = conn.execute("SELECT result_json FROM server_io WHERE kind = 'wave' AND result_json IS NOT NULL"
                            " ORDER BY started_at DESC, rowid DESC LIMIT ?", (limit * 3,)).fetchall()
    except Exception:                              # noqa: BLE001 -- הלמידה אינה מפילה החלטה
        log.exception("brain: K12 could not read the waves' CPU")
        return Learned(0, 0, None, None, None)
    for row in rows:
        if len(cores_) >= limit:
            break
        try:
            result = json.loads(row["result_json"])
        except (TypeError, ValueError):
            result = None
        server = result.get("server") if isinstance(result, dict) and isinstance(result.get("server"), dict) else {}
        used, mbps = wave_cores(result) if isinstance(result, dict) else None, _num(server.get("nic_tx_mbps"), positive=True)
        if used is None or mbps is None:
            skipped += 1
            continue
        cores_.append(used)
        per_mbps.append(used / mbps)
        secs = _num(result.get("seconds"), positive=True)
        if secs is not None:
            seconds.append(secs)
    med = statistics.median
    return Learned(len(cores_), skipped, med(cores_) if cores_ else None, med(per_mbps) if per_mbps else None,
                   med(seconds) if seconds else None)


def ceiling(n_cores: float | None, learned: Learned) -> int | None:
    """כמה גלים במקביל לפני רוויה. None = אין מספר (ליבות לא נקראו, או עוד לא נלמד). 0 = אפילו גל אחד חורג."""
    if n_cores is None or learned.per_wave is None or learned.waves < MIN_WAVES or learned.per_wave <= 0:
        return None
    return math.floor(n_cores * HEADROOM / learned.per_wave)


# --- ההכרעה ----------------------------------------------------------------------------------------------------


def _learned_he(learned: Learned) -> str:
    return (f"{learned.waves} גלים נמדדו" + (f", {learned.skipped} דולגו (מעבד או קצב לא נקראו)" if learned.skipped else ""))


def judge(*, n_cores: float | None, source: str, learned: Learned, psi: float | None, live: int,
          eta_s: float | None = None) -> tuple[str, str, list[str]]:
    """(פסק, סיבה, ראיות). טהורה. ``live`` = גלים שרצים עכשיו; ``eta_s`` = מתי הראשון מהם צפוי להסתיים (או None)."""
    evidence = [f"cores={'לא נקרא' if n_cores is None else f'{n_cores:g}'} ({source})", f"live={live}",
                f"psi_cpu_avg10={'לא נקרא' if psi is None else f'{psi:.2f}'}",
                f"learned={learned.waves}/{MIN_WAVES} skipped={learned.skipped}"]
    if n_cores is None:
        return UNDECIDED, "מספר הליבות של השרת לא נקרא", evidence
    if learned.waves < MIN_WAVES or learned.per_wave is None:
        return UNDECIDED, f"עוד לא נלמד כמה מעבד גל עולה: {_learned_he(learned)}, ונדרשים {MIN_WAVES}", evidence
    if psi is None:
        return UNDECIDED, "לחץ המעבד (PSI) לא נקרא", evidence
    top = ceiling(n_cores, learned)
    evidence += [f"per_wave_cores={learned.per_wave:.2f}",
                 "per_mbps_cores=" + ("לא נמדד" if learned.per_mbps is None else f"{learned.per_mbps:.4f}"),
                 f"ceiling={top}", f"headroom={HEADROOM:g} (ניחוש)", f"psi_busy={PSI_BUSY:g} (ניחוש)"]
    wait = "" if eta_s is None else f"; חיזוי: ~{eta_s:.0f} ש' עד שגל מסתיים"
    if psi >= PSI_BUSY:
        return WAIT, f"המעבד כבר במחסור: PSI {psi:.0%} מעל {PSI_BUSY:.0%}{wait}", evidence
    if live >= 1 and live + 1 > top:
        return WAIT, (f"השרת מחזיק עד ~{top} גלים במקביל ({n_cores:g} ליבות, גל עולה ~{learned.per_wave:.2f}), "
                      f"ורצים {live}{wait}"), evidence
    if live == 0 and top < 1:
        return OPEN, (f"גל אחד עולה ~{learned.per_wave:.2f} ליבות, יותר ממה שהתקרה מרשה ({n_cores:g} x {HEADROOM:g}), "
                      "אבל אין גל שרץ, ולכן אין ממה להמתין"), evidence
    return OPEN, f"השרת מחזיק עד ~{top} גלים במקביל, ורצים {live}", evidence


@dataclass(frozen=True)
class Gate:
    verdict: str
    reason: str
    hold: bool = False             # WAIT, ו-`brain_cpu_gate` דלוק: מי שפותח לא פותח
    eta_s: float | None = None

    @property
    def he(self) -> str:
        return f"השרת עמוס, הסבב לא נפתח: {self.reason}"


def gate_on(conn) -> bool:
    """המתג. רק ``"true"`` מדליק; חסר, פגום או שלא נקרא = כבוי (לעולם לא מחזיק בגלל "לא יודע")."""
    try:
        return get_setting(conn, SETTING) == "true"
    except Exception:                              # noqa: BLE001
        log.exception("brain: %s unreadable, off", SETTING)
        return False


_last: dict[tuple, float] = {}
_lock = threading.Lock()


def forget() -> None:
    with _lock:
        _last.clear()


def _fresh(key: tuple, mono: float) -> bool:
    """האם ההחלטה הזו כבר נרשמה בחלון (ואם לא -- מסמן שנרשמה)."""
    with _lock:
        before = _last.get(key)
        if before is not None and mono - before < DEDUPE_S:
            return False
        _last[key] = mono
        for old in [k for k, t in _last.items() if mono - t >= DEDUPE_S]:
            del _last[old]
        return True


def eta_from_history(conn, learned: Learned) -> float | None:
    """חיזוי מתי גל חי מסתיים: משך גל חציוני פחות מה שחלף מאז שהגל האחרון התחיל. None = אין גל חי או אין היסטוריה."""
    if learned.seconds is None:
        return None
    try:
        row = conn.execute("SELECT started_at FROM server_io WHERE kind = 'wave' AND ended_at IS NULL"
                           " ORDER BY started_at DESC LIMIT 1").fetchone()
        from datetime import datetime                   # noqa: PLC0415
        began = datetime.fromisoformat(row["started_at"]).timestamp() if row is not None else None
    except Exception:                              # noqa: BLE001
        return None
    elapsed = None if began is None else time.time() - began
    # שורה שלא נסגרה (שרת שקרס באמצע גל) אינה גל חי: מעל פי 3 מהחציון אין חיזוי, ולא "0 שניות"
    return None if elapsed is None or elapsed > 3 * learned.seconds else max(learned.seconds - elapsed, 0.0)


def gate(conn, *, point: str, live: int = 0, round_id: str | None = None,
         eta: Callable[[], float | None] | None = None, proc: Path = PROC, cgroup: Path = CGROUP,
         mono: Callable[[], float] = time.monotonic) -> Gate:
    """ההכרעה לפני פתיחת סבב (`point` = ``open_round``) או גל (``attach_wave``). נרשמת בצל; ``hold`` רק עם המתג.
    ``eta`` -- חיזוי חיצוני לסיום גל חי (`waveeta`); בלעדיו, חציון המשך מההיסטוריה. לעולם אינו זורק."""
    try:
        from server.brain import veto                   # noqa: PLC0415 -- כמו `slotfill`
        if veto.mode(conn) == veto.OFF:
            return Gate(NOT_RUN, "המוח כבוי")
        n_cores, source = cores(proc, cgroup)
        learned = learn(conn)
        wait_for = eta() if eta is not None else (eta_from_history(conn, learned) if live else None)
        verdict, reason, evidence = judge(n_cores=n_cores, source=source, learned=learned, psi=psi_avg10(proc),
                                          live=live, eta_s=wait_for if isinstance(wait_for, (int, float)) else None)
        hold = verdict == WAIT and gate_on(conn)
        if _fresh((point, verdict, reason), mono()):
            memory.record(conn, mode=veto.SHADOW, rule=RULE, action=ACTION, verdict=verdict, reason=reason,
                          evidence=evidence, point=point, round_id=round_id,
                          outcome=HELD_HE if hold else SHADOW_HE,
                          predicted=float(wait_for) if verdict == WAIT and isinstance(wait_for, (int, float)) else None,
                          unit="s" if verdict == WAIT and isinstance(wait_for, (int, float)) else None)
            log.info("brain K12 %s %s: %s", point, verdict, reason)
        return Gate(verdict, reason, hold, wait_for if isinstance(wait_for, (int, float)) else None)
    except Exception:                                  # noqa: BLE001 -- הסבב אינו נפתח או נחסם בגלל כשל של המוח
        log.exception("brain: K12 at %s failed", point)
        return Gate(UNDECIDED, "ההכרעה נכשלה")


def summary(conn, proc: Path = PROC, cgroup: Path = CGROUP) -> tuple[str, str]:
    """לשורת הבריאות וה-brainreport: ``(state, detail)``. "עדיין לא נלמד" הוא מידע, לא תקלה ולא ירוק."""
    try:
        n_cores, source = cores(proc, cgroup)
        learned = learn(conn)
        top = ceiling(n_cores, learned)
        if n_cores is None:
            return "unknown", "מספר הליבות של השרת לא נקרא"
        if top is None:
            return "info", (f"עדיין לא נלמד כמה גלים השרת מחזיק במקביל ({_learned_he(learned)}, נדרשים {MIN_WAVES}); "
                            f"{n_cores:g} ליבות ({source})")
        if top < 1:
            return "warn", (f"גל אחד עולה ~{learned.per_wave:.2f} ליבות, ולשרת {n_cores:g} ליבות: אפילו גל אחד חורג "
                            f"מהתקרה ({HEADROOM:g}) — נלמד מ-{learned.waves} גלים")
        return "ok", (f"השרת מחזיק עד ~{top} גלים במקביל (נלמד מ-{learned.waves} גלים): {n_cores:g} ליבות, "
                      f"גל עולה ~{learned.per_wave:.2f}"
                      + ("" if learned.per_mbps is None else f", ~{learned.per_mbps * 100:.1f} ליבות לכל 100 Mb/s"))
    except Exception:                                  # noqa: BLE001
        log.exception("brain: K12 summary failed")
        return "unknown", "כוח העיבוד לא נקרא"
