"""#3816 (K17): מה הסוכן בחר למגירות -- פענוח משותף או נפרד, ``nice``, ``O_DIRECT``, ועל כמה מגירות ומעבדים -- **בצל בלבד**.

היום ``drawer_tune`` (``agent/lib/restore/drawerfeed.sh``) קובע את כל אלה בעצמו, מהסביבה או מ-``$RUN_DIR/tune``; לשרת ולמוח
אין בזה חלק. זה הצעד הראשון בהעברת ההחלטה למוח (#3653): **הסוכן מדווח מה שבחר**, והמוח רושם. בלי סף חדש ובלי שינוי התנהגות:
הסוכן ממשיך לבחור כמו קודם, והשרת לא כותב ל-``tune``.

הסוכן מוסיף ל-``senses`` של המכונה שדה ``tune`` (``sensecore.sh``): ``{decode, nice, direct, drawers, cpus, partition}`` --
הבחירה האפקטיבית של המחיצה האחרונה, ``null`` כשהסוכן לא הריץ ``drawer_tune`` (מנוע חתיכות, שיכפול מקומי, סוכן ישן).
במעבר הטרמינלי של מכונה בגל נרשמת שורה אחת: ``K17``/``DRAWER_TUNE``, ``mode=shadow``, ``outcome='n/a: shadow'``.
``OBSERVED`` = דווח; ``NOT_READ`` = לא דווח (**לא** "ברירת המחדל"; עיקרון 5). ``cpus`` שלא נקרא הוא ``null``, לעולם לא 0.

**מקור הערך "2 מגירות"** (שבו ``auto`` עובר ל-``shared``): ``measured``, Testrunner i7-10700 על 2 CPU לוגיים, tar של 1.5GB
ב-zstd -3, 18/10 -- עם מגירה אחת fanout שני עולה +13% זמן ו-+22% CPU, עם שתיים -10% / -9%, עם שלוש -26% / -24%. מדידה אחת על
מעבד אחד, לא על חומרת המכללה; מדידת הברזל ל-1/2/3 מגירות ברשימת הערב. כאן הוא נרשם בראיה (``auto_shared_from``), לא נקבע.

**לא כאן:** תקרת הפינוי של הזרם (פי 2 מ-``FANOUT_STALL_EVICT_MS``). היא מחושבת בסוכן מחוץ ל-``drawer_tune`` ועוד לא מדווחת.
המפה (``A29``) כותבת 150000 לתקרה, ובקוד היא 120000 (``chunkcast.c``, ``fanout.c``, ``drawerfeed.sh``): הקוד נשאר, המפה לא תואמת.
"""

from __future__ import annotations

import json
import logging

from server.brain import memory, veto

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K17", "DRAWER_TUNE"
OBSERVED, NOT_READ = "OBSERVED", "NOT_READ"
OUTCOME = "n/a: shadow"
DECODES = ("shared", "each")
AUTO_SHARED_FROM = 2       # measured: Testrunner i7-10700, 2 logical CPUs, 1.5GB tar zstd -3, 18/10 -- one bench, not the college's


def _int(v: object, lo: int, hi: int) -> int | None:
    return v if type(v) is int and lo <= v <= hi else None


def well_formed(raw: object) -> dict | None:
    """``senses.tune`` של הסוכן: כל שדה ``None`` כשחסר או אינו תקין. לא אובייקט, או בלי ``decode``/``drawers`` תקינים = ``None``."""
    if not isinstance(raw, dict):
        return None
    out = {"decode": raw.get("decode") if raw.get("decode") in DECODES else None,
           "nice": _int(raw.get("nice"), 0, 19), "direct": _int(raw.get("direct"), 0, 1),
           "drawers": _int(raw.get("drawers"), 0, 64), "cpus": _int(raw.get("cpus"), 1, 4096),
           "partition": _int(raw.get("partition"), 0, 255)}
    return out if out["decode"] is not None and out["drawers"] is not None else None


def verdict(tune: dict | None) -> tuple[str, str, tuple[str, ...]]:
    """(פסק, סיבה, ראיות). טהורה: רושמת מה דווח, לא שופטת אותו."""
    if tune is None:
        return NOT_READ, "הסוכן לא דיווח מה בחר למגירות (מנוע חתיכות, שיכפול מקומי, או סוכן ישן)", ("tune=null",)
    show = lambda v: "null" if v is None else str(v)           # noqa: E731 -- "not read" is printed as such, never as 0
    ev = tuple(f"{k}={show(tune[k])}" for k in ("decode", "nice", "direct", "drawers", "cpus", "partition"))
    ev += (f"auto_shared_from={AUTO_SHARED_FROM}(measured,Testrunner,2cpu,18/10)",)
    return OBSERVED, (f"פענוח {tune['decode']} על {tune['drawers']} מגירות חיות, nice {show(tune['nice'])}, "
                      f"O_DIRECT {show(tune['direct'])}, מעבדים {show(tune['cpus'])}"), ev


def note_final(conn, mac: str, session_id: str, machine_raw: object) -> int | None:
    """המעבר הטרמינלי של מכונה בגל: שורה אחת, בצל. ``machine_raw`` הוא ``senses`` של הדיווח. מחזיר את מספר השורה
    (``None``: כבוי, או לא נרשם). **אינו זורק.**"""
    try:
        if veto.mode(conn) == veto.OFF:
            return None
        tune = well_formed(machine_raw.get("tune")) if isinstance(machine_raw, dict) else None
        v, reason, evidence = verdict(tune)
        row = memory.record(conn, mode="shadow", rule=RULE, action=ACTION, point=f"{mac}/drawer_tune", round_id=session_id,
                            entity=("machine", mac), verdict=v, reason=reason, evidence=evidence, outcome=OUTCOME)
        log.info("brain %s", json.dumps({"mode": "shadow", "rule": RULE, "action": ACTION, "round": session_id, "target": mac,
                                         "verdict": v, "reason": reason, "enforced": False, "memory": row}, ensure_ascii=False))
        return row
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל דיווח
        log.exception("brain: the drawer tune of %s in %s was not recorded", mac, session_id)
        return None
