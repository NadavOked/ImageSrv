"""#3571: שלושה מישורי תקרה -- רשת, אחסון לכל דיסק, הפצה. המוח מחליט, והשולח עדיין לא מבצע.

אבדן ברשת ו-backlog של דיסק הם שתי בעיות. #3141 רשם ניתוק רשת כ"הדיסק לא עמד". כאן כל מישור מחשב תקרה
משלו, והתקרה המחמירה היא המינימום **רק כששלושתם נמדדו**. מישור חסר, ישן או קצר מדי הוא ``NOT_MEASURED`` /
``STALE`` / ``UNDECIDED``, לא "אינו מגביל".

**הכלי של הרשת** (TFMCC, RFC 5348 משוואה 3): אבדן, RTT ושידורים חוזרים. דיווח ישן מ-``REPORT_STALE_MS`` פג,
ודיווח זהה לקודם (``same=True``) מדוכא ואינו צעד חדש.

**הכלי של האחסון**, לכל דיסק יעד ולא ממוצע של המשכפל: קצב כתיבה עמיד, backlog ושיפוע ה-backlog, וגם קצב
הקריאה של דיסק הספרייה. חלון קצר מ-``STORE_WINDOW_S`` אינו קצב. היסטרזיס (``STORE_LOW_S`` / ``STORE_HIGH_S``),
deadband וצעד מרבי לכל דיווח חדש. דיסק שמתחת ל-``SLOW_PCT`` מהחציון של האחרים, עם backlog שגדל, ``STORE_BEHIND_MAX``
דיווחים ברצף -- נשאר מאחור ואינו קובע את הקצב. דיסק יחיד, או שכולם כאלה, כן קובע.

**ההפצה** (#3568 עוד לא מחוברת): מלאי חתיכות, עמיתים בריאים, תקרת העלאה, ונפילה לשרת. בלי דיווח -- לא נמדד.

**צל.** ``brain_veto_mode`` של ``off`` מחליט ואינו רושם. ``shadow`` ו-``enforce`` רושמים. ``obey`` מחזיר קצב רק
כש-``enforce`` **וגם** ``brain_planes=on`` **ויש תקרה שנמדדה**. ברירת המחדל של המתג כבויה, ואין קריאה אליו מנתיב
הנתונים: פקודת ``chunkcast send`` אינה משתנה. ``note_send`` רושם פעם לגל שהשולח עדיין לא הביא את החיישנים.

הספים הערכה, לא נמדדו על ברזל. נוסחת הרשת לא כוילה על chunkcast.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from server.brain import memory, snapshot

log = logging.getLogger("imagesrv.brain")

RULE = "K-PLANES"
RULE_VERSION = 1
ACTION_NET, ACTION_STORE, ACTION_DIST, ACTION_BIND = "PLANE_NET", "PLANE_STORE", "PLANE_DIST", "PLANE_BIND"
POINT = "3571"
SNAPSHOT_EVENT = "planes"   # what caused the frozen snapshot (snapshot.freeze), not a journal event: the operator never reads it
SETTING, ON, OFF = "brain_planes", "on", "off"
NOT_MEASURED, UNDECIDED, STALE, SUPPRESSED = "NOT_MEASURED", "UNDECIDED", "STALE", "SUPPRESSED"
CLEAR, CAPPED, NO_PEER, PACE = "CLEAR", "CAPPED", "NO_PEER", "PACE"
NET, STORE, DIST = "NET", "STORE", "DIST"
#: דיווח משוב שלא נשמע מעבר לזה פג. אותו חלון שהשולח כבר זורק בו עמית (``seen >= 2000`` ב-chunkcast). הערכה, לא מדידה חדשה.
REPORT_STALE_MS = 2000
#: חלון מינימלי לפני שקצב עמיד הוא קצב. דופק המשוב הוא ~100ms; 2 ש' הן עשרים דופקים. הערכה.
STORE_WINDOW_S = 2.0
#: לא זזים אם השינוי קטן מזה. הערכה.
STORE_DEADBAND_PCT = 5
#: השינוי המרבי לדיווח חדש אחד. הערכה. השולח היום יורד 20% (``rate * 8/10``); כאן התקרה של הצעד, בצל בלבד.
STORE_STEP_PCT = 10
#: backlog מתחת לזה (שניות של כתיבה עמידה) -- יש מקום לעלות. בין זה ל-``STORE_HIGH_S`` -- היסטרזיס, נשארים. הערכה.
STORE_LOW_S = 0.2
STORE_HIGH_S = 0.6
#: כמה דיווחים ברצף דיסק איטי נשאר מאחור. כמו ``high >= 10`` של השולח היום. הערכה, לא כיול מחדש.
STORE_BEHIND_MAX = 10
#: מתחת לאחוז זה מחציון שאר הדיסקים, עם backlog שגדל, הוא איטי עקבי. הערכה.
SLOW_PCT = 50
#: גודל חבילת chunkcast על החוט: CC_HDR + CC_DATA_HDR + CC_PAYLOAD. לא סף מכויל.
NET_PACKET_B = 16 + 16 + 1440
_ORDER = {"net": 0, "store": 1, "dist": 2}
_SHADOW = "צל: השולח לא שונה. אין מוציא לפועל בנתיב הנתונים"


@dataclass(frozen=True)
class Disk:
    serial: str
    durable_bps: int | None
    backlog_bytes: int | None
    slope_bps: int | None
    window_s: float | None
    behind_ticks: int | None = None


@dataclass(frozen=True)
class Net:
    loss_permille: int | None
    rtt_ms: float | None
    retransmits: int | None
    age_ms: int | None
    same: bool | None = None


@dataclass(frozen=True)
class Store:
    disks: tuple[Disk, ...]
    library_bps: int | None
    age_ms: int | None
    same: bool | None = None
    current_kbps: int | None = None


@dataclass(frozen=True)
class Dist:
    pieces_short: int | None
    peers_healthy: int | None
    peers_seen: int | None
    upload_kbps: int | None
    server_fallback: bool | None
    age_ms: int | None
    same: bool | None = None


@dataclass(frozen=True)
class Observe:
    net: Net | None = None
    store: Store | None = None
    dist: Dist | None = None
    now: datetime | None = None


@dataclass(frozen=True)
class Plane:
    name: str
    verdict: str
    ceiling_kbps: int | None
    counts: bool
    reason: str
    evidence: tuple[str, ...]
    step: str | None = None
    proposed_kbps: int | None = None


@dataclass(frozen=True)
class Finding:
    net: Plane
    store: Plane
    dist: Plane
    verdict: str
    ceiling_kbps: int | None
    reason: str
    evidence: tuple[str, ...]


def _plane(name, verdict, ceiling, counts, reason, evidence, step=None, proposed=None) -> Plane:
    return Plane(name, verdict, ceiling, counts, reason, tuple(evidence), step, proposed)


def _nn(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _pos_time(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
        return None
    return float(value)


def _fresh(age_ms) -> str | None:
    if type(age_ms) is not int or age_ms < 0:
        return NOT_MEASURED
    return STALE if age_ms > REPORT_STALE_MS else None


def _flag(same) -> bool | None | str:
    if same is None or same is True or same is False:
        return same
    return "bad"


def _kbps(bps: int) -> int:
    return int(bps * 8 / 1000)


def _tfrc_kbps(loss_permille: int, rtt_ms: float) -> int | None:
    """RFC 5348 משוואה 3, ביחידות kbit/s. ``None`` כשהנוסחה אינה מספר. אבדן 0 אינו נכנס לכאן."""
    p, rtt_s = loss_permille / 1000, rtt_ms / 1000
    denom = math.sqrt(2 * p / 3) + 12 * math.sqrt(3 * p / 8) * p * (1 + 32 * p * p)
    if not math.isfinite(denom) or denom <= 0 or rtt_s <= 0:
        return None
    bps = NET_PACKET_B / (rtt_s * denom)
    return int(bps * 8 / 1000) if math.isfinite(bps) and bps >= 0 else None


def _median(values: list[int]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2


# --- רשת --------------------------------------------------------------------------------------------------------------


def _net(net: Net | None) -> Plane:
    if net is None:
        return _plane("net", NOT_MEASURED, None, False, "אין דיווח רשת: אבדן, RTT ושידורים חוזרים לא נמדדו", ())
    fresh = _fresh(net.age_ms)
    if fresh == STALE:
        return _plane("net", STALE, None, False, f"דיווח הרשת ישן מ-{REPORT_STALE_MS}ms ואינו תקרה",
                      (f"age_ms={net.age_ms}",))
    if fresh == NOT_MEASURED or _flag(net.same) == "bad":
        return _plane("net", NOT_MEASURED, None, False, "גיל דיווח הרשת לא נמדד, ולכן אי אפשר לדעת אם פג",
                      (f"age_ms={net.age_ms!s}",))
    loss = net.loss_permille if type(net.loss_permille) is int and 0 <= net.loss_permille <= 1000 else None
    rtt, retx = _pos_time(net.rtt_ms), _nn(net.retransmits)
    evidence = (f"loss_permille={loss if loss is not None else 'unmeasured'}",
                f"rtt_ms={rtt if rtt is not None else 'unmeasured'}",
                f"retransmits={retx if retx is not None else 'unmeasured'}", f"age_ms={net.age_ms}")
    if loss is None or rtt is None or retx is None:
        return _plane("net", NOT_MEASURED, None, False, "אבדן, RTT או שידורים חוזרים לא נמדדו: אין תקרת רשת", evidence)
    if loss == 0 and retx > 0:
        return _plane("net", UNDECIDED, None, False, "אבדן 0 אבל יש שידורים חוזרים: הסתירה אינה 'הרשת נקיה'", evidence)
    if loss == 0:
        verdict, ceiling, reason = CLEAR, None, "אבדן 0 ואין שידורים חוזרים: הרשת נמדדה ואינה מגבילה"
    else:
        ceiling = _tfrc_kbps(loss, rtt)
        if ceiling is None:
            return _plane("net", NOT_MEASURED, None, False, "נוסחת TFMCC לא החזירה מספר: אין תקרה", evidence)
        verdict, reason = CAPPED, f"אבדן {loss}‰ ו-RTT {rtt:g}ms: תקרת TFMCC {ceiling} kbps (לא כויל)"
    if net.same is True:
        verdict, reason = SUPPRESSED, "דיווח רשת זהה לקודם: אין דגימה חדשה. " + reason
    return _plane("net", verdict, ceiling, True, reason, evidence + ("formula=rfc5348", f"packet_b={NET_PACKET_B}"))


# --- אחסון ------------------------------------------------------------------------------------------------------------


def _disk_ok(disk: Disk) -> bool:
    if not isinstance(disk.serial, str) or not disk.serial:
        return False
    if _nn(disk.durable_bps) is None or _nn(disk.backlog_bytes) is None or type(disk.slope_bps) is not int:
        return False
    if isinstance(disk.window_s, bool) or not isinstance(disk.window_s, (int, float)):
        return False
    if not math.isfinite(float(disk.window_s)) or disk.window_s < 0:
        return False
    return disk.behind_ticks is None or _nn(disk.behind_ticks) is not None


def _left_behind(disks: tuple[Disk, ...]) -> list[str]:
    marked = []
    for disk in disks:
        others = [other.durable_bps for other in disks if other.serial != disk.serial]
        if not others or disk.durable_bps is None or disk.slope_bps is None:
            continue
        if (disk.durable_bps < _median(others) * SLOW_PCT / 100 and disk.slope_bps > 0
                and type(disk.behind_ticks) is int and disk.behind_ticks >= STORE_BEHIND_MAX):
            marked.append(disk.serial)
    return [] if len(marked) == len(disks) else marked


def _pace_step(paced: list[Disk], current, ceiling: int | None, repeated: bool) -> tuple[str, int | None]:
    if repeated:
        return "hold", current if type(current) is int and current > 0 else None
    if type(current) is not int or current <= 0:
        return "undecided", None
    times = []
    for disk in paced:
        if disk.durable_bps is None or disk.durable_bps <= 0 or disk.backlog_bytes is None:
            return "undecided", None
        times.append(disk.backlog_bytes / disk.durable_bps)
    if any(t > STORE_HIGH_S for t in times):
        direction = "down"
    elif times and all(t < STORE_LOW_S for t in times):
        direction = "up"
    else:
        direction = "hold"
    if ceiling is not None and current > ceiling:
        direction = "down"
    jump = max(1, current * STORE_STEP_PCT // 100)
    if direction == "down":
        proposed = max(1, current - jump)
    elif direction == "up":
        proposed = current + jump
        if ceiling is not None and proposed > ceiling:
            proposed = ceiling
    else:
        proposed = current
    if proposed != current and abs(proposed - current) * 100 < STORE_DEADBAND_PCT * current:
        return "hold", current
    return direction, proposed


def _store(store: Store | None) -> Plane:
    if store is None or (not store.disks and store.library_bps is None):
        return _plane("store", NOT_MEASURED, None, False, "אין דיווח אחסון: לא דיסק יעד ולא קריאת הספרייה", ())
    fresh = _fresh(store.age_ms)
    if fresh == STALE:
        return _plane("store", STALE, None, False, f"דיווח האחסון ישן מ-{REPORT_STALE_MS}ms ואינו תקרה",
                      (f"age_ms={store.age_ms}",))
    if fresh == NOT_MEASURED or _flag(store.same) == "bad":
        return _plane("store", NOT_MEASURED, None, False, "גיל דיווח האחסון לא נמדד", (f"age_ms={store.age_ms!s}",))
    if not store.disks:
        return _plane("store", NOT_MEASURED, None, False, "אין דיסק יעד בדיווח: קריאת הספרייה לבדה אינה קצב כתיבה",
                      (f"library_bps={store.library_bps!s}",))
    serials = [d.serial for d in store.disks]
    if any(not _disk_ok(d) for d in store.disks) or len(set(serials)) != len(serials):
        return _plane("store", UNDECIDED, None, False, "דיסק אחד לפחות לא נמדד במלואו, ולכן האחרים לא קובעים את הקצב",
                      tuple(f"disk={d.serial or '?'}" for d in store.disks))
    if any(d.window_s is not None and d.window_s < STORE_WINDOW_S for d in store.disks):
        return _plane("store", UNDECIDED, None, False,
                      f"חלון המדידה קצר מ-{STORE_WINDOW_S:g}s: זה עדיין לא קצב עמיד",
                      tuple(f"window[{d.serial}]={d.window_s:g}s" for d in store.disks))
    behind = _left_behind(store.disks)
    paced = [d for d in store.disks if d.serial not in behind]
    pace_bps = min(d.durable_bps for d in paced if d.durable_bps is not None)
    evidence = [f"durable[{d.serial}]={d.durable_bps}B/s" for d in store.disks]
    evidence += [f"behind={serial}" for serial in behind]
    evidence.append(f"pace_bps={pace_bps}")
    if store.library_bps is None or _nn(store.library_bps) is None:
        evidence.append(f"partial_kbps={_kbps(pace_bps)}")
        evidence.append("library=unmeasured")
        return _plane("store", UNDECIDED, None, False, "קריאת דיסק הספרייה לא נמדדה: קצב הדיסקים אינו התקרה",
                      tuple(evidence))
    ceiling = min(_kbps(pace_bps), _kbps(store.library_bps))
    who = f"{', '.join(behind)} נשאר מאחור" if behind else "אף דיסק לא הושאר מאחור"
    reason = f"כתיבה עמידה { _kbps(pace_bps) } kbps, ספרייה { _kbps(store.library_bps) } kbps: תקרה {ceiling}. {who}"
    step, proposed = _pace_step(paced, store.current_kbps, ceiling, store.same is True)
    evidence += [f"library_bps={store.library_bps}", f"step={step}", f"current_kbps={store.current_kbps!s}"]
    verdict = SUPPRESSED if store.same is True else PACE
    if store.same is True:
        reason = "דיווח אחסון זהה לקודם: אין צעד חדש. " + reason
    return _plane("store", verdict, ceiling, True, reason, tuple(evidence), step, proposed)


# --- הפצה -------------------------------------------------------------------------------------------------------------


def _dist(dist: Dist | None) -> Plane:
    if dist is None:
        return _plane("dist", NOT_MEASURED, None, False, "אין דיווח הפצה: מלאי, עמיתים ותקרת העלאה לא נמדדו", ())
    fresh = _fresh(dist.age_ms)
    if fresh == STALE:
        return _plane("dist", STALE, None, False, f"דיווח ההפצה ישן מ-{REPORT_STALE_MS}ms ואינו תקרה",
                      (f"age_ms={dist.age_ms}",))
    if fresh == NOT_MEASURED or _flag(dist.same) == "bad":
        return _plane("dist", NOT_MEASURED, None, False, "גיל דיווח ההפצה לא נמדד", (f"age_ms={dist.age_ms!s}",))
    short, healthy, seen = _nn(dist.pieces_short), _nn(dist.peers_healthy), _nn(dist.peers_seen)
    upload = _nn(dist.upload_kbps)
    fallback = dist.server_fallback if isinstance(dist.server_fallback, bool) else None
    evidence = (f"pieces_short={short if short is not None else 'unmeasured'}",
                f"peers_healthy={healthy if healthy is not None else 'unmeasured'}",
                f"peers_seen={seen if seen is not None else 'unmeasured'}",
                f"upload_kbps={upload if upload is not None else 'unmeasured'}",
                f"server_fallback={fallback if fallback is not None else 'unmeasured'}")
    if None in (short, healthy, seen, upload, fallback) or healthy > seen or seen == 0:
        why = "אף עמית לא נראה: זה לא 'אין מי שיגיש'" if seen == 0 else "מלאי, עמיתים או תקרת ההעלאה לא נמדדו"
        return _plane("dist", NOT_MEASURED, None, False, why, evidence)
    if short == 0:
        verdict, ceiling, reason = CLEAR, None, "אין חוסר חתיכות: ההפצה נמדדה ואינה מגבילה"
    elif healthy == 0 and fallback is False:
        verdict, ceiling, reason = NO_PEER, 0, "יש חוסר, אין עמית בריא, והשרת אינו הגיבוי: התקרה 0"
    elif healthy == 0 and fallback is True:
        verdict, ceiling = CAPPED, upload
        reason = f"אין עמית בריא: השרת מגיש, עד {upload} kbps"
    else:
        verdict, ceiling, reason = CAPPED, upload, f"עמיתים מגישים את החסר, עד {upload} kbps"
    if dist.same is True:
        verdict, reason = SUPPRESSED, "דיווח הפצה זהה לקודם: אין דגימה חדשה. " + reason
    return _plane("dist", verdict, ceiling, True, reason, evidence)


# --- המחמיר -----------------------------------------------------------------------------------------------------------


def judge(obs: Observe) -> Finding:
    """טהור. תקרת כל מישור, והמינימום רק כששלושתם נספרים. חסר אינו 0 ואינו 'פתוח'."""
    net, store, dist = _net(obs.net), _store(obs.store), _dist(obs.dist)
    evidence = [f"net={net.verdict}", f"store={store.verdict}", f"dist={dist.verdict}"]
    for plane in (net, store, dist):
        if plane.ceiling_kbps is not None:
            evidence.append(f"{plane.name}_kbps={plane.ceiling_kbps}")
    if not (net.counts and store.counts and dist.counts):
        return Finding(net, store, dist, UNDECIDED, None,
                       "לא הוכרע: מישור אחד לפחות לא נמדד, ולכן אין תקרה מחמירה", tuple(evidence))
    numbered = [(p.name, p.ceiling_kbps) for p in (net, store, dist) if p.ceiling_kbps is not None]
    if not numbered:
        return Finding(net, store, dist, CLEAR, None, "שלושת המישורים נמדדו ואף אחד אינו מגביל", tuple(evidence))
    name, ceiling = min(numbered, key=lambda item: (item[1], _ORDER[item[0]]))
    ties = [who for who, kbps in numbered if kbps == ceiling]
    if len(ties) > 1:
        evidence.append("tie=" + ",".join(ties))
    verdict = {"net": NET, "store": STORE, "dist": DIST}[name]
    return Finding(net, store, dist, verdict, ceiling,
                   f"התקרה המחמירה היא {verdict}: {ceiling} kbps", tuple(evidence))


def obey(mode: str, setting: str | None, found: Finding) -> int | None:
    """הקצב שהשולח היה מקבל, או ``None`` כשאסור לגעת בו. צל, מתג כבוי, או פסק בלי מספר -- לא נוגעים."""
    if mode != "enforce" or setting != ON or type(found.ceiling_kbps) is not int:
        return None
    return found.ceiling_kbps


# --- רישום ------------------------------------------------------------------------------------------------------------


def _mode(conn) -> str:
    from server.brain import veto
    return veto.mode(conn)


def _received(now: datetime, age_ms) -> datetime | None:
    return None if type(age_ms) is not int or age_ms < 0 else now - timedelta(milliseconds=age_ms)


def _shot(obs: Observe, round_id: str) -> dict | None:
    if obs.now is None:
        return None
    age = REPORT_STALE_MS / 1000.0
    rows = []

    def add(kind, key, name, value, unit, age_ms):
        rows.append(snapshot.metric(snapshot.resource(kind, key), name, value, unit, "planes",
                                    _received(obs.now, age_ms), obs.now, max_age_s=age))

    net, store, dist = obs.net, obs.store, obs.dist
    add("machine", "net", "loss", None if net is None or type(net.loss_permille) is not int else net.loss_permille / 1000,
        "ratio", None if net is None else net.age_ms)
    add("machine", "net", "rtt", None if net is None or _pos_time(net.rtt_ms) is None else net.rtt_ms / 1000,
        "s", None if net is None else net.age_ms)
    if store is not None:
        for disk in store.disks:
            add("disk", disk.serial or "?", "durable", disk.durable_bps, "B/s", store.age_ms)
        add("disk", "library", "read", store.library_bps, "B/s", store.age_ms)
    else:
        add("disk", "library", "read", None, "B/s", None)
    add("machine", "dist", "pieces_short", None if dist is None else dist.pieces_short, "chunks",
        None if dist is None else dist.age_ms)
    return snapshot.freeze(rule=RULE, rule_version=RULE_VERSION, round_id=None, wave_id=round_id, phase="send",
                           event=SNAPSHOT_EVENT, taken_at=obs.now, metrics=rows)


def _record(conn, found: Finding, round_id: str, obs: Observe, mode: str) -> None:
    shot = None
    try:
        frozen = _shot(obs, round_id)
        shot = None if frozen is None else memory.save_snapshot(conn, frozen)
    except Exception:  # noqa: BLE001 -- בלי צילום ההחלטה עדיין נרשמת, והראיה אומרת שהצילום חסר
        log.exception("brain: the planes snapshot of %s was not saved", round_id)
    common = dict(mode=mode, rule=RULE, point=POINT, round_id=round_id, entity=("round", round_id),
                  snapshot=shot, rule_version=RULE_VERSION)
    memory.record(conn, action="PLANE_NET", verdict=found.net.verdict, reason=found.net.reason,
                  evidence=found.net.evidence, outcome=_SHADOW, **common)
    memory.record(conn, action="PLANE_STORE", verdict=found.store.verdict, reason=found.store.reason,
                  evidence=found.store.evidence, outcome=_SHADOW, **common)
    memory.record(conn, action="PLANE_DIST", verdict=found.dist.verdict, reason=found.dist.reason,
                  evidence=found.dist.evidence, outcome=_SHADOW, **common)
    memory.record(conn, action="PLANE_BIND", verdict=found.verdict, reason=found.reason,
                  evidence=found.evidence, outcome=_SHADOW, **common)


def decide(conn, obs: Observe, *, round_id: str) -> Finding | None:
    """הפסק, ונרשם כשהמתג אינו ``off``. ``None`` רק כשהחישוב עצמו נכשל -- לא 'הכול תקין'."""
    try:
        found = judge(obs)
    except Exception:  # noqa: BLE001
        log.exception("brain: planes of %s were not judged", round_id)
        return None
    if conn is None:
        return found
    try:
        mode = _mode(conn)
        if mode != OFF:
            _record(conn, found, round_id, obs, mode)
    except Exception:  # noqa: BLE001
        log.exception("brain: planes of %s were not recorded", round_id)
    return found


def note_send(conn, *, round_id: str) -> Finding | None:
    """פעם לגל, בנתיב השליחה: החיישנים (דיסק-דיסק, RTT, מלאי) עוד לא מגיעים לכאן, וזה נרשם בשם. אינו זורק."""
    try:
        if conn is None or not round_id or _mode(conn) == OFF:
            return None
        if any(row.get("action") == "PLANE_BIND" for row in memory.decisions(conn, kind="round", key=round_id,
                                                                              rule=RULE, limit=8)):
            return None
        obs = Observe(now=datetime.now(timezone.utc))
        found = judge(obs)
        _record(conn, found, round_id, obs, _mode(conn))
        return found
    except Exception:  # noqa: BLE001 -- השליחה ממשיכה גם כשהרישום נכשל
        log.exception("brain: planes of %s were not recorded", round_id)
        return None
