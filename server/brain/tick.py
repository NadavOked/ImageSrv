"""#3320 (KT1, KT2, K1): ה-tick של המוח -- חיות של כל משכפל בגל רץ, גם כשאף נקודה הרסנית לא שואלת.

עד כאן K1 חושב רק בתוך הווטו, כלומר רק ברגע שהקוד הקיים עומד להרוס משהו. ה-tick מריץ את אותו K1 (`decide.liveness`)
על כל גל רץ, **לכל היותר פעם ב-``TICK_S``** (KT1, 10 ש'), מתוך הדופק של החדר (`room.tick`: כל hello וכל דופק רקע --
"כל אירוע"). ``SUSPECT`` (KT2: ``decide.Config.suspect_s``, 30 ש' בלי שום ראיה, ערך התחלה בקוד ולא הגדרה, ומכויל לכל משכפל ב-
`calibrate`) **אינו מפעיל דבר**, רק שורה. משכפל חשוד שחזר לחיים בלי LOST נרשם כתצפית כיול (``RECOVERED``).

**מה נרשם.** רק **מעבר** מצב של משכפל (ALIVE → SUSPECT, SUSPECT → LOST, LOST → ALIVE...), לא כל tick: שורת
``brain {...}`` בלוג, ושורה ב-``brain_decisions`` (כלל ``K1``, פעולה ``LIVENESS``, הישות היא המשכפל) עם התוצאה "צל".
המצב הראשון שנראה למשכפל בגל נרשם רק כשאינו ALIVE: משכפל חי הוא המצב הרגיל ואינו החלטה. בכל מעבר נכתבת גם
ההקלטה של הגל (R14).

**הראיות** הן של הצילום מה-DB (progress, ledger) ו-V6 (בייטים שהשרת הגיש), כמו D21 ב-`room._mark_lost`. ראיות
המנוע (``listening``/``fetched``) אינן כאן: הן במנוע החתיכות, שאינו בתחום של #3320. משכפל שמדבר רק דרכן ייראה כאן
SUSPECT, ושורה כזו היא "המוח לא ראה", לא "המשכפל שתק" -- ולכן ה-tick רושם בלבד ואינו מזין אף פעולה.

המתג הוא ``brain_veto_mode``: ``off`` = לא מחושב ולא נרשם. **לעולם אינו זורק** אל `room.tick`.
"""

from __future__ import annotations

import logging
import threading
import time

from server.brain import calibrate, decide as brain, memory, observe, recording, veto

log = logging.getLogger("imagesrv.brain")

#: KT1: מרווח מינימלי בין שני ticks של אותו גל (ערך התחלה מטיוטת התכנון, research-3064 4ב).
TICK_S = 10.0
#: כמה גלים הרשם זוכר (גל אחד רץ בכל רגע; השאר הם גלים שנגמרו).
WAVES = 16
SHADOW_HE = "צל: K1 רק נרשם, שום פעולה לא נגזרה ממנו"

_last: dict[str, float] = {}                 # גל -> מתי רץ (monotonic)
_state: dict[tuple[str, str], str] = {}      # (גל, mac) -> המצב האחרון שנרשם
#: KT2 (כיול): (גל, mac) -> (מתי נהיה חשוד, הגיל של הראיה האחרונה אז, הסף שהיה). נמחק כשחזר או כשנקבע LOST.
_suspect: dict[tuple[str, str], tuple[float, float, float]] = {}
_lock = threading.Lock()


def due(wave_id: str, mono: float) -> bool:
    """האם הגל צריך tick עכשיו (KT1). מסמן אותו כאילו רץ."""
    with _lock:
        last = _last.get(wave_id)
        if last is not None and mono - last < TICK_S:
            return False
        _last[wave_id] = mono
        for old in sorted(_last, key=lambda w: _last[w])[:max(len(_last) - WAVES, 0)]:
            del _last[old]                  # גל שנגמר: הרשם אינו גדל לנצח
            for k in [k for k in _state if k[0] == old]:
                del _state[k]
            for k in [k for k in _suspect if k[0] == old]:
                del _suspect[k]
        return True


def changes(wave_id: str, decisions) -> list:
    """ההחלטות שהן מעבר מצב (טהור מול הרשם). ALIVE ראשון אינו מעבר."""
    out = []
    with _lock:
        for d in decisions:
            key = (wave_id, d.target)
            before = _state.get(key)
            if before != d.verdict and not (before is None and d.verdict == brain.ALIVE):
                out.append(d)
            _state[key] = d.verdict
    return out


def recovered(wave_id: str, decisions, snap, cfg) -> list[tuple[str, float, float]]:
    """KT2: משכפל שהיה חשוד וחזר לחיים (ALIVE/STALLED) בלי ש-LOST נקבע: ``(mac, שתיקה בשניות, הסף שהיה)``. השתיקה
    היא חסם עליון: הגיל של הראיה כשנהיה חשוד ועוד הזמן עד ה-tick שראה אותו חי. LOST או לא ידוע אינם חזרה."""
    out = []
    with _lock:
        for d in decisions:
            key = (wave_id, d.target)
            if d.verdict == brain.SUSPECT:
                if key not in _suspect:
                    c = snap.cloner(d.target)
                    life = max((e.seen_at for e in c.evidence), default=None) if c is not None else None
                    if life is not None:
                        _suspect[key] = (snap.now, max(snap.now - life, 0.0), cfg.suspect_for(d.target))
            elif key in _suspect:
                at, age, threshold = _suspect.pop(key)
                if d.verdict in (brain.ALIVE, brain.STALLED):
                    out.append((d.target, age + max(snap.now - at, 0.0), threshold))
    return out


def run(conn, wave_id: str, *, now: float | None = None, mono=time.monotonic) -> list:
    """tick אחד של גל רץ. מחזיר את המעברים שנרשמו (ריק כשלא הגיע הזמן, כשהמתג כבוי, או בכשל)."""
    try:
        mode_ = veto.mode(conn)
        if mode_ == veto.OFF or not due(wave_id, mono()):
            return []
        now = time.time() if now is None else now
        lost_s = veto.config(conn).lost_s
        sv, sv_unknown = veto._served_evidence(conn, None, now, lost_s)
        snap = observe.from_db(conn, wave_id, now, served=sv, unknown=sv_unknown)
        cfg = veto.config(conn, [c.mac for c in snap.cloners], now)      # KT2: הסף מכויל לכל משכפל בגל
        decisions = [d for d in brain.decide(snap, cfg) if d.rule == "K1"]
        for mac, silence, threshold in recovered(wave_id, decisions, snap, cfg):
            calibrate.record(conn, mac, wave_id, silence, threshold, mode=veto.SHADOW)
        moved = changes(wave_id, decisions)
        if not moved:
            return []
        digest, ids = snap.digest(), []
        for d in moved:
            log.info("%s", veto.line(d, now=now, mode_=veto.SHADOW, snap=digest, round_id=wave_id, part=snap.part,
                                     point="KT1"))
            ids.append(memory.record(conn, mode=veto.SHADOW, rule=d.rule, point="KT1", action=d.action,
                                     verdict=d.verdict, reason=d.reason, evidence=d.evidence, round_id=wave_id,
                                     entity=("machine", d.target), outcome=SHADOW_HE))
        recording.write(conn, wave_id, kind="K1", snap=snap, cfg=cfg, decisions=decisions, ids=ids,
                        mode=veto.SHADOW, point="KT1")
        return moved
    except Exception:                        # noqa: BLE001 — ה-tick אינו מפיל את דופק החדר
        log.exception("brain: the K1 tick of %s failed", wave_id)
        return []


def forget(wave_id: str | None = None) -> None:
    """מנקה את הרשם (סוף גל, ובדיקות)."""
    with _lock:
        for w in [w for w in _last if wave_id is None or w == wave_id]:
            del _last[w]
        for k in [k for k in _state if wave_id is None or k[0] == wave_id]:
            del _state[k]
        for k in [k for k in _suspect if wave_id is None or k[0] == wave_id]:
            del _suspect[k]
