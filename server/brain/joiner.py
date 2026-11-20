"""#3064 / #3181: המוח של המצטרף -- דיסק שנכנס לסבב שכבר רץ, או מגירה שחזרה. **החלטות טהורות, במצב צל.**

כל פונקציה כאן מקבלת את מה שנמדד ומחזירה `Call`: פסק, סיבה בעברית וראיות. ``None`` בקלט = **לא נמדד**, לעולם
לא אפס, וכשההחלטה נשענת עליו הפסק הוא ``UNDECIDED`` ("לא הוכרע"), לא ברירת מחדל שקטה (עיקרון 5).
מי שקורא לפונקציות, אוסף את הקלט ורושם (`server.brain.joinwatch`) -- **המוח כאן לא פועל:** אף סוכן לא שומע
על הפסקים האלה, והקוד הקיים (`server.rounds.prodline`, `agent/lib/restore/drawerback.sh`) ממשיך כמו היום.

ההחלטות (הקטלוג, `catalog.toml`):

- **B1** `accept` -- דיסק חדש באמצע סבב פס ייצור: serial, SMART לא אדום, בדיקת כתיבה, ואינו בסבב אחר.
- **B25** `slot_at_post`, `safe_pull` -- חריץ שהיה תפוס באתחול (hot-plug מובטח רק שם, #3108), וניתוק לפני שליפה.
- **B44** `order` -- כמה דיסקים ממתינים לשם: מי ראשון (FIFO), ומי לא יקבל שם כשהשמות נגמרים.
- **B2** `join_now` -- להצטרף לשידור שבאוויר עכשיו, או לחכות למחיצה הבאה: לפי הזנב שנשאר מול `X1`.
- **B45** `short_part` -- המחיצה שבאוויר קצרה מ-`X1` כולה (ESP/MSR): ממתינים למחיצה הבאה.
- **B4** `big_gap` -- משכפל אחד עם חוסר גדול: מצטרף לזרם, ואת ההתחלה משלים ב-HTTP אחרי המעבר (לא מעבר נוסף).
- **B17/B18** `catchup` -- השלמה שתסתיים לפני המחיצה הבאה = ברקע (B17); שלא תסתיים = נדחית לסוף הסבב (B18).
- **B46** `sibling` -- מגירה אחרת באותו משכפל עוד משלימה: במקביל, או אחריה כשתקציב ההשלמה קוצץ.
- **B21** `http_after` -- מגירה שהחסירה הרבה ב-`HTTP_AFTER_PASSES` מעברים: השאר שלה ב-HTTP.
- **B41** `late_slow` -- D11 ("איטי מהמינימום = כשל") אינו חל על מצטרף מאוחר: הוא משלים אחר כך (#3295 ב').

**הספים הם נקודת התחלה, והמוח מכייל אותם לפי מדידה בתוך גבול** (הכרעת נדב 14/11 בערב). `calibrate` הוא הכלל
היחיד: חציון של המדידות האחרונות כפול מקדם, בין רצפה לתקרה; פחות מ-`MIN_SAMPLES` מדידות = ערך ההתחלה, וזה
נאמר בראיות ("start", לא "learned").
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Iterable

ACCEPT, REFUSE, UNDECIDED = "ACCEPT", "REFUSE", "UNDECIDED"
POST_OCCUPIED, POST_EMPTY = "POST_OCCUPIED", "POST_EMPTY"
SAFE, UNSAFE = "SAFE", "UNSAFE"
ORDER = "ORDER"
LIVE, NEXT_PART = "LIVE", "NEXT_PART"
STREAM_THEN_HTTP, REPASS_CANDIDATE = "STREAM_THEN_HTTP", "REPASS_CANDIDATE"
IN_TIME, DEFER_TO_END = "IN_TIME", "DEFER_TO_END"
PARALLEL, SERIAL = "PARALLEL", "SERIAL"
HTTP, STAY = "HTTP", "STAY"
EXEMPT = "EXEMPT"

MIB = 1024 * 1024
#: חתיכה במנוע החתיכות (`chunksend.CHUNK_BYTES`) -- לא דחוסה, ולכן זמן מעבר שמחושב ממנה הוא **חסם עליון**.
CHUNK_BYTES = 4 * MIB
#: כמה מדידות לפני שסומכים על הנלמד. הערכה, כמו `recall.THRESHOLDS["min_returns"]`.
MIN_SAMPLES = 3

#: **X1** -- הזנב המינימלי שעוד שווה להצטרף אליו. התחלה: כ-50 ש' (המחקר של #3064, 13/11; M6 לא נמדדה).
#: נלמד: פעמיים החציון של זמן ההצטרפות שנמדד (מהדיווח הראשון של `rejoin` עד שהחוסר ירד לראשונה), 10–300 ש'.
X1_START_S, X1_FACTOR, X1_FLOOR_S, X1_CEIL_S = 50.0, 2.0, 10.0, 300.0
#: **X17** -- קצב ההשלמה, חתיכות לשנייה, שממנו נגזר כמה זמן ההשלמה תיקח. התחלה: 2,071 חתיכות ב-42 ש' = כ-49
#: לשנייה (ברזל 13/11, ההערה בקטלוג על X17; M15 לא נמדדה במלואה). נלמד: החציון של הירידה ב-`missing` לשנייה.
#: **הגל אינו מחכה לה** (הכרעת נדב 15/11, #3295 ב'): הקצב קובע רק אם היא ברקע (B17) או בסוף הסבב (B18).
X17_START_CPS, X17_FLOOR_CPS, X17_CEIL_CPS = 2071 / 42, 1.0, 1000.0
#: **B21** -- כמה מעברים עם חוסר גדול לפני שמגירה עוברת ל-HTTP. הערכה (UFTP `-N`), לא מדידה.
HTTP_AFTER_PASSES = 2
#: "חוסר גדול" במעבר הוא יחסי לשכנות: פי `PEER_FACTOR` מהחציון שלהן, ולא פחות מ-`MISS_FLOOR` מהמחיצה. הערכה.
PEER_FACTOR, MISS_FLOOR = 2.0, 0.10


@dataclass(frozen=True)
class Call:
    rule: str
    action: str
    verdict: str
    reason: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class Threshold:
    value: float
    learned: bool
    samples: int

    def say(self, name: str) -> str:
        return f"{name}={self.value:g} ({'learned' if self.learned else 'start'}, n={self.samples})"


def calibrate(start: float, samples: Iterable[float], *, factor: float = 1.0, floor: float, ceil: float) -> Threshold:
    """ערך ההתחלה, או `factor` כפול החציון של המדידות -- בין `floor` ל-`ceil`. מדידה שאינה מספר חיובי נזרקת."""
    good = [float(s) for s in samples if isinstance(s, (int, float)) and not isinstance(s, bool) and s > 0]
    if len(good) < MIN_SAMPLES:
        return Threshold(start, False, len(good))
    return Threshold(min(ceil, max(floor, factor * statistics.median(good))), True, len(good))


def x1(latencies: Iterable[float]) -> Threshold:
    return calibrate(X1_START_S, latencies, factor=X1_FACTOR, floor=X1_FLOOR_S, ceil=X1_CEIL_S)


def x17(rates: Iterable[float]) -> Threshold:
    return calibrate(X17_START_CPS, rates, floor=X17_FLOOR_CPS, ceil=X17_CEIL_CPS)


def _known(**facts) -> tuple[str, ...]:
    return tuple(f"{k}={'unmeasured' if v is None else v}" for k, v in facts.items())


# --- B1, B25: קבלת דיסק חדש ---------------------------------------------------------------------------------


def accept(*, serial: str | None, smart: str | None, writecheck: str | None, other_round: str | None | bool,
           post: str | None, actual: str | None) -> Call:
    """B1. ``smart``: הפסק האחרון של השרת על ה-serial (`disk_events`), ``None`` = אין. ``writecheck``:
    ``write_preflight.state``. ``other_round``: מזהה סבב פתוח אחר שהדיסק בו, ``False`` = אין, ``None`` = לא נקרא.
    ``post``: פסק B25 על החריץ. ``actual``: מה שהקוד הקיים עשה (`prodline`) -- רק לראיות, לא קלט."""
    ev = _known(serial=serial, smart=smart, writecheck=writecheck, other_round=other_round, post=post, actual=actual)
    if not serial:
        return Call("B1", "ACCEPT_DISK", UNDECIDED, "אין serial: אין למה לקשור שם או ספירה", ev)
    no = []
    if smart == "fail":
        no.append("SMART אדום")
    if writecheck == "failed":
        no.append("נכשל בבדיקת הכתיבה")
    if isinstance(other_round, str):
        no.append(f"הדיסק כבר בסבב {other_round}")
    if no:
        return Call("B1", "ACCEPT_DISK", REFUSE, f"הדיסק {serial} לא יתקבל: {'; '.join(no)}", ev)
    unknown = [name for name, value in (("SMART", smart), ("סבב אחר", other_round)) if value is None]
    if writecheck not in ("ok", "slow"):
        unknown.append("בדיקת הכתיבה" if writecheck is None else f"בדיקת הכתיבה ({writecheck})")
    if unknown:
        return Call("B1", "ACCEPT_DISK", UNDECIDED, f"לא הוכרע: לא נמדד {', '.join(unknown)}", ev)
    note = " (איטי בבדיקת הכתיבה: מתקבל ומשלים אחר כך, #3295 ב')" if writecheck == "slow" else ""
    if post == POST_EMPTY:
        note += " (החריץ היה ריק באתחול: hot-plug לא מובטח, B25)"
    return Call("B1", "ACCEPT_DISK", ACCEPT, f"הדיסק {serial} מתקבל{note}", ev)


def slot_at_post(port: int | None, post_ports: frozenset[int] | None) -> Call:
    """B25, החלק של האתחול: ``post_ports`` = החריצים שהיו תפוסים ב-hello הראשון של האתחול הזה, ``None`` = לא
    נראה (השרת עלה אחרי האתחול של המשכפל)."""
    ev = _known(port=port, post_ports=None if post_ports is None else ",".join(map(str, sorted(post_ports))) or "-")
    if port is None or post_ports is None:
        return Call("B25", "SLOT_AT_POST", UNDECIDED, "לא ידוע אם החריץ היה תפוס באתחול", ev)
    if port in post_ports:
        return Call("B25", "SLOT_AT_POST", POST_OCCUPIED, f"חריץ {port} היה תפוס באתחול", ev)
    return Call("B25", "SLOT_AT_POST", POST_EMPTY,
                f"חריץ {port} היה ריק באתחול: hot-plug בו לא מובטח (#3108)", ev)


def safe_pull(port: int | None, detached_before: bool) -> Call:
    """B25, החלק של השליפה: דיסק שדווח ``removed`` -- האם קודם לכן דווח ``detached`` (ניתוק בטוח)."""
    ev = _known(port=port, detached_before=detached_before)
    if detached_before:
        return Call("B25", "PULL", SAFE, f"חריץ {port}: נותק בבטחה לפני שהוצא", ev)
    return Call("B25", "PULL", UNSAFE, f"חריץ {port}: הוצא בלי ניתוק בטוח לפניו", ev)


# --- V15, B44: תור הממתינים -----------------------------------------------------------------------------------


def order(waiting: list[tuple[str, float, str]], names_left: int | None) -> Call:
    """B44. ``waiting`` = ``(serial, מתי נראה לראשונה, mac)`` (V15). FIFO לפי הזמן, ובתיקו לפי המשכפל ואז ה-serial
    (דטרמיניסטי). ``names_left`` = שמות שאף דיסק לא מחזיק; ``None`` = לא נקרא."""
    queue = sorted(waiting, key=lambda w: (w[1], w[2], w[0]))
    ev = (f"queue={','.join(s for s, _at, _mac in queue)}", *_known(names_left=names_left))
    if names_left is None:
        return Call("B44", "ORDER", UNDECIDED, f"{len(queue)} ממתינים, ולא ידוע כמה שמות נשארו", ev)
    short = [s for s, _at, _mac in queue[names_left:]]
    if short:
        return Call("B44", "ORDER", ORDER, f"{len(queue)} ממתינים ו-{names_left} שמות: לפי סדר ההגעה; "
                    f"בלי שם: {', '.join(short)}", ev)
    return Call("B44", "ORDER", ORDER, f"{len(queue)} ממתינים, שם לכל אחד: לפי סדר ההגעה", ev)


# --- B2, B45, B4: איך מצטרפים ----------------------------------------------------------------------------------


def pass_s(chunks: int | None, stream_Bps: int | None) -> float | None:
    """זמן מעבר שלם של מחיצה, חסם עליון (חתיכות לא דחוסות מול קצב הזרם בבתים). ``None`` = לא נמדד."""
    if not chunks or not stream_Bps:
        return None
    return chunks * CHUNK_BYTES / stream_Bps


def short_part(part_seconds: float | None, x1_: Threshold) -> Call | None:
    """B45. ``None`` = המחיצה אינה קצרה (אין מה להחליט). זמן לא ידוע = לא הוכרע."""
    ev = (*_known(pass_s=None if part_seconds is None else round(part_seconds, 1)), x1_.say("x1_s"))
    if part_seconds is None:
        return Call("B45", "SHORT_PART", UNDECIDED, "לא ידוע כמה ארוכה המחיצה שבאוויר", ev)
    if part_seconds < x1_.value:
        return Call("B45", "SHORT_PART", NEXT_PART, f"המחיצה שבאוויר קצרה ({part_seconds:.0f} ש' כולה, פחות מ-"
                    f"{x1_.value:.0f}): המצטרף ממתין למחיצה הבאה", ev)
    return None


def join_now(on_air: bool | None, tail_s: float | None, x1_: Threshold) -> Call:
    """B2. ``on_air``: המחיצה של המצטרף היא זו שבמעבר הראשון עכשיו. ``tail_s``: כמה נשאר למעבר (חסם עליון)."""
    ev = (*_known(on_air=on_air, tail_s=None if tail_s is None else round(tail_s, 1)), x1_.say("x1_s"))
    if on_air is None:
        return Call("B2", "JOIN", UNDECIDED, "לא ידוע מה באוויר עכשיו", ev)
    if not on_air:
        return Call("B2", "JOIN", NEXT_PART, "המחיצה שלו אינה במעבר הראשון עכשיו: מצטרף בתחילת הבאה", ev)
    if tail_s is None:
        return Call("B2", "JOIN", UNDECIDED, "לא ידוע כמה נשאר למעבר שבאוויר", ev)
    if tail_s >= x1_.value:
        return Call("B2", "JOIN", LIVE, f"נשארו כ-{tail_s:.0f} ש' למעבר: מצטרף לשידור החי עכשיו", ev)
    return Call("B2", "JOIN", NEXT_PART, f"נשארו כ-{tail_s:.0f} ש' בלבד (פחות מ-{x1_.value:.0f}): לא שווה להצטרף, "
                "ממתין למחיצה הבאה", ev)


def big_gap(missing: int, few_max: int, cloners_big: int) -> Call | None:
    """B4. ``cloners_big`` = כמה משכפלים שונים חסרים הרבה עכשיו (כולל זה). ``None`` = החוסר קטן: אין החלטה."""
    if missing <= few_max:
        return None
    ev = (f"missing={missing}", f"few_max={few_max}", f"cloners_big={cloners_big}")
    if cloners_big <= 1:
        return Call("B4", "BIG_GAP", STREAM_THEN_HTTP, f"רק משכפל אחד חסר הרבה ({missing} חתיכות): מצטרף לזרם, "
                    "ואת ההתחלה משלים ב-HTTP אחרי המעבר", ev)
    return Call("B4", "BIG_GAP", REPASS_CANDIDATE, f"{cloners_big} משכפלים חסרים הרבה: מעבר נוסף אולי זול יותר "
                "(ההחלטה של K5)", ev)


# --- B17, B18, B46: ההשלמה -----------------------------------------------------------------------------------


def catchup(missing: int, rate: Threshold, next_part_in_s: float | None) -> Call:
    """B17/B18. ``rate`` = X17 (חתיכות לשנייה). ``next_part_in_s`` = עוד כמה זמן מתחילה המחיצה הבאה (חסם עליון)."""
    expected = missing / rate.value
    ev = (f"missing={missing}", rate.say("x17_cps"), f"expected_s={expected:.0f}",
          *_known(next_part_in_s=None if next_part_in_s is None else round(next_part_in_s, 1)))
    if next_part_in_s is None:
        return Call("B18", "CATCHUP", UNDECIDED, f"ההשלמה תיקח כ-{expected:.0f} ש', ולא ידוע מתי המחיצה הבאה", ev)
    if expected <= next_part_in_s:
        return Call("B17", "CATCHUP", IN_TIME, f"ההשלמה (כ-{expected:.0f} ש') מסתיימת לפני המחיצה הבאה: ברקע, "
                    "והמשכפל ממשיך עם המגירות הבריאות", ev)
    return Call("B18", "CATCHUP", DEFER_TO_END, f"ההשלמה (כ-{expected:.0f} ש') לא תסתיים לפני המחיצה הבאה (עוד "
                f"{next_part_in_s:.0f} ש'): נדחית לסוף הסבב, והגל לא מחכה לה", ev)


def sibling(siblings_missing: list[int], budget_bps: int | None, ceiling_bps: int) -> Call | None:
    """B46. ``siblings_missing`` = מה שחסר למגירות אחרות באותו משכפל שעוד משלימות. אין כאלה = אין החלטה.
    ``budget_bps`` = תקציב ההשלמה של המשכפל (K5, `fill.Brain.budget`); ``None`` = לא נקרא."""
    if not siblings_missing:
        return None
    ev = (f"siblings={len(siblings_missing)}", f"siblings_missing={sum(siblings_missing)}",
          *_known(budget_mibps=None if budget_bps is None else round(budget_bps / MIB, 1)),
          f"ceiling_mibps={ceiling_bps / MIB:.0f}")
    if budget_bps is None:
        return Call("B46", "SIBLING", UNDECIDED, "מגירה אחרת במשכפל משלימה, ותקציב ההשלמה לא נקרא", ev)
    if budget_bps >= ceiling_bps:
        return Call("B46", "SIBLING", PARALLEL, f"{len(siblings_missing)} מגירות אחרות במשכפל משלימות, והתקציב "
                    "שלם: משלימים במקביל", ev)
    return Call("B46", "SIBLING", SERIAL, f"{len(siblings_missing)} מגירות אחרות במשכפל משלימות, והתקציב קוצץ "
                f"ל-{budget_bps / MIB:.0f}MiB/s: משלים אחריהן", ev)


# --- B21, B41 ------------------------------------------------------------------------------------------------


def bad_pass(share: float, peers: list[float]) -> bool:
    """מעבר שבו המגירה החסירה "הרבה": פי `PEER_FACTOR` מהחציון של השכנות, ולפחות `MISS_FLOOR`."""
    bar = max(MISS_FLOOR, PEER_FACTOR * statistics.median(peers)) if peers else MISS_FLOOR
    return share >= bar


def http_after(bad_passes: int, passes: int) -> Call | None:
    """B21. ``None`` = לא החסירה הרבה אפילו פעם אחת: אין החלטה."""
    if bad_passes == 0:
        return None
    ev = (f"bad_passes={bad_passes}", f"passes={passes}", f"after={HTTP_AFTER_PASSES}")
    if bad_passes >= HTTP_AFTER_PASSES:
        return Call("B21", "MISS_RATE", HTTP, f"החסירה הרבה ב-{bad_passes} מעברים: את השאר שלה ב-HTTP, "
                    "ולא במעבר מולטיקאסט נוסף", ev)
    return Call("B21", "MISS_RATE", STAY, f"החסירה הרבה במעבר {bad_passes} מתוך {passes}: נשארת בשידור", ev)


def late_slow(slow: str | None) -> Call | None:
    """B41. ``slow`` = מה ש-`repair.slow_reason` היה אומר על המצטרף. ``None`` = אינו איטי: אין החלטה."""
    if not slow:
        return None
    return Call("B41", "LATE_SLOW", EXEMPT, "מצטרף מאוחר: D11 אינו חל עליו -- הוא משלים אחר כך, משכנה או ב-HTTP "
                "(#3295 ב')", (f"d11={slow}",))


def completing_text(drawer: int | None, missing: int, chunks: int | None) -> str:
    """R5: "משלים מגירה N: X חתיכות, Y%" -- Y = כמה מהמחיצה כבר עליה. בלי מספר מגירה / בלי סך: נאמר."""
    who = f"מגירה {drawer}" if drawer is not None else "מגירה (מספר לא ידוע)"
    if not chunks:
        return f"משלים {who}: חסרות {missing:,} חתיכות"
    done = max(0, min(100, round(100 * (chunks - missing) / chunks)))
    return f"משלים {who}: חסרות {missing:,} חתיכות, {done}% כבר עליה"
