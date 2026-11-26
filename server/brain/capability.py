"""#3041 (LR24): המוח לומד לבד, מכל גל, מה היכולת של כל משכפל לקבל -- לפי MAC. **צל: הלמידה נשמרת, ושום קצב לא זז.**

**נדב 15/11, #3041:** "זה צריך להיות חלק מהלמידה של המוח." אין כפתור "בדוק יכולת". אחרי כל גל חתיכות, לכל משכפל שהיה
בו, המוח שואל **מי עיכב** -- המכונה או הדיסק -- ולומד רק מהראשון:

- **המכונה עיכבה** = אובדן (``RcvbufErrors`` של הקרנל, חתיכות שננטשו באמצע, ``rx_missed``/``rx_no_buffer`` של הכרטיס),
  או שכל הליבות עברו את רף המעבד **שנלמד** (`cpu_threshold`), **בזמן שהדיסקים לא היו עמוסים**. נרשם אות יכולת (``LIMITED``).
- **הדיסק עיכב** = המקלט אומר שהדיסקים פיגרו (``busy`` ב-rxstats) או שמגירה הייתה עמוסה כמעט כל הגל. אובדן
  באותו גל אינו ראיה על המשכפל: ``DISK_BOUND``, **ולא נלמד דבר**.
- **לא נקרא** = ``UNDECIDED`` ולא נלמד דבר (עיקרון 5): אין קצב זרם, אין מונה אובדן, או לא ידוע אם הדיסקים פיגרו. גל ישן של
  המנוע הקודם מ-DB משודרג (בלי חושי הגל ובלי ``stream.chunk``, #3173) תמיד כאן.
- **גל שהמשכפל עמד בו** (קרא את מונה האובדן, והוא 0, והמעבד מתחת לרף) -- ``CLEAN``: הוכחה שהוא מקבל **לפחות** את הקצב הזה.
  זו הקצב הגבוה ביותר בלי אובדן. **#3554: "קיבל לפחות X" דורש שהגל באמת הגיע ל-X.** שלושה תנאים נוספים, כולם ראיה חיובית: הקצב שהושג
  (`Evidence.achieved_bps`: ``chunk.achieved_bps`` של גל החתיכות) לפחות ``drive_rates.KEEP_UP`` x הקצב שהתבקש;
  הדיסקים **נמדדו** ולא פיגרו. ברזל 16/11 (RS-1): אובדן 0, מעבד 46%, אבל הושגו 419.6Mb/s מתוך 669, ו-``disks_behind``; והוא נרשם CLEAN ו"קיבל
  לפחות 669Mb/s". היום: דיסקים שפיגרו = ``DISK_BOUND``; לא הגיע לקצב = ``NOT_PUSHED``; קצב שהושג או דיסקים שלא נקראו = ``UNDECIDED``. אף אחד מהם לא נלמד.

**מה נשמר, לתמיד** (`memory.remember`, ``brain_memory`` kind ``machine``, מקור ``capability``; בלי שינוי סכמה):
``clean_bps`` (המקסימום = הקצב הגבוה ביותר בלי אובדן), ``limited_bps`` (המינימום = הקצב הנמוך ביותר שבו המכונה עיכבה),
``cpu_at_best`` (עומס המעבד כשהשיא נקבע), ``cpu_at_loss`` (עומס המעבד בגל שבו אבדו חבילות; ממנו נלמד הרף), ``link_mbps`` ו-
``iface`` (הכרטיס והקישור), ו-``best_at`` (התאריך).

**היכולת** (`learned`): יש רק כשיש אות ``LIMITED`` -- גל שעמד בו מוכיח רק "לפחות", לא תקרה (כמו `chunk_rate.sustained`).
זה הקצב הנקי הגבוה ביותר שנמצא **מתחת** לקצב שבו המכונה עיכבה, ואם אין כזה -- הקצב שבו עיכבה (חסם עליון). נקי **מעל** קצב
שעיכב = ראיה סותרת (רעש): לא נלמד, והערך הקיים נשאר.

**איפוס:** כשהכרטיס (``iface``) או מהירות הקישור (כשנקראה, לא הונחה) משתנים, מה שנלמד נזרק. נרשמת שורה ביומן
(``capability_reset``) ושורה ב-``brain_decisions`` עם מה שהיה, והמוח לומד מחדש בגל הבא. אין כפתור "בדוק שוב".
**#4090: מהירות הקישור נבדקת בכל hello** (`reset_on_hello`), לפני ה-precheck ולפני חיזוי ה-ETA של הגל הבא; הבדיקה בסוף הגל
נשארה רשת ביטחון. hello בלי מהירות (לא נקראה) אינו מאפס.

**הרף של המעבד** (`cpu_threshold`): נקודת התחלה ``CPU_START`` (85%, נדב). אם המכונה הזו איבדה חבילות כשהליבה הפנויה ביותר
שלה הייתה עסוקה פחות -- זה הרף שלה (עד ``CPU_FLOOR``, כדי שגל אחד חריג לא יהפוך כל גל ל"מעבד"). **רף העומס של הדיסק**
(``DISK_UTIL_START``) הוא נקודת התחלה בלבד, ועוד אינו נלמד.

**ההשפעה על הקצב** (`wave_rate`): כשהגל יוצא, נרשם כמה היה יוצא לפי היכולת הנלמדת של המשכפלים שבו, מול הקצב שבו הוא יוצא.
``WOULD_LOWER``/``NO_CHANGE``/``NOT_LEARNED``. **שום קצב אינו משתנה**, גם ב-``enforce``. המתג הוא ``brain_veto_mode``:
``off`` = לא נלמד ולא נרשם. **לעולם אינו זורק** אל דיווח או אל גל.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from server.brain import agentsenses, memory, veto
from server.core.logtext import one_line
from server.db import journal, now_iso
from server.rounds import drive_rates, nic_speed

log = logging.getLogger("imagesrv.brain")

RULE, ACTION, RATE_ACTION = "LR24", "MACHINE_CAPABILITY", "WAVE_RATE_LEARNED"
SOURCE = "capability"
LIMITED, CLEAN, DISK_BOUND, UNDECIDED, RESET = "LIMITED", "CLEAN", "DISK_BOUND", "UNDECIDED", "RESET"
#: #3554: נקי, אבל הגל לא הגיע לקצב שהתבקש (השולח, הדיסק או הרשת עצרו אותו): לא הוכח שהמשכפל קיבל אותו
NOT_PUSHED = "NOT_PUSHED"
WOULD_LOWER, NO_CHANGE, NOT_LEARNED = "WOULD_LOWER", "NO_CHANGE", "NOT_LEARNED"
#: נקודות התחלה (נדב, #3041: "CPU מעל 85% בכל הליבות"). המוח מתאים את הרף של כל מכונה בתוך [CPU_FLOOR, CPU_START].
CPU_START, CPU_FLOOR = 85.0, 50.0
#: מגירה שעסוקה כמעט כל הגל. נקודת התחלה בלבד: אינה נלמדת עדיין.
DISK_UTIL_START = 90.0
OUTCOME_LEARNED = "נלמד ונשמר בזיכרון (צל: שום קצב לא השתנה)"
OUTCOME_NOTHING = "לא נלמד דבר"


@dataclass(frozen=True)
class Evidence:
    """מה שנמדד על משכפל אחד בגל. ``None`` = לא נקרא (לעולם לא 0)."""
    stream_bps: int | None = None
    rcvbuf: int | None = None            # RcvbufErrors של הקרנל בגל (`udp_drops.rcvbuf`)
    abandoned: int | None = None         # חתיכות שננטשו באמצע (`udp_drops.abandoned`, rxstats evicted)
    rx_missed: int | None = None         # S50
    rx_no_buffer: int | None = None      # S50, null בלי ethtool
    cpu_floor: float | None = None       # הליבה הפנויה ביותר: השיא של כל ליבה בגל, והנמוך מהם (S48)
    disks_behind: bool | None = None     # המקלט אמר שהדיסקים פיגרו, או שמגירה עמוסה; None = לא ידוע
    achieved_bps: int | None = None      # #3554: הקצב שהגל באמת הגיע אליו, בביטים לשנייה (`_achieved`); None = לא נקרא


@dataclass(frozen=True)
class Verdict:
    verdict: str
    reason: str
    lost: bool = False                   # היה אובדן (ולא רק מעבד): ממנו נלמד הרף של המעבד


def _loss(e: Evidence) -> list[str]:
    return [f"{name}={v}" for name, v in (("rcvbuf", e.rcvbuf), ("abandoned", e.abandoned),
                                          ("rx_missed", e.rx_missed), ("rx_no_buffer", e.rx_no_buffer)) if v]


def judge(e: Evidence, cpu_threshold: float = CPU_START) -> Verdict:
    """(מה נמדד, רף המעבד של המכונה) -> מי עיכב. טהורה."""
    if e.stream_bps is None:
        return Verdict(UNDECIDED, "קצב הזרם של הגל לא נקרא")
    lost = _loss(e)
    hot = e.cpu_floor is not None and e.cpu_floor >= cpu_threshold
    if lost or hot:
        what = ("אובדן: " + ", ".join(lost)) if lost else ""
        if hot:
            what += ("; " if what else "") + f"כל הליבות מעל {cpu_threshold:.0f}% (הפנויה ביותר הגיעה ל-{e.cpu_floor:.0f}%)"
        if e.disks_behind is None:
            return Verdict(UNDECIDED, f"{what} -- אבל לא ידוע אם הדיסקים היו עמוסים, ולכן לא נלמד דבר")
        if e.disks_behind:
            return Verdict(DISK_BOUND, f"{what} -- בזמן שהדיסקים פיגרו: העיכוב של הדיסק, לא של המשכפל. לא נלמד דבר")
        return Verdict(LIMITED, f"{what} והדיסקים לא היו עמוסים: המכונה עיכבה ב-{e.stream_bps / 10 ** 6:.0f}Mb/s",
                       lost=bool(lost))
    if e.rcvbuf is None:
        return Verdict(UNDECIDED, "מונה האובדן של הקרנל לא נקרא: אי אפשר לומר שהמשכפל עמד בקצב, ולא נלמד דבר")
    # #3554: "קיבל לפחות X" -- הגל הגיע ל-X (לא רק שלא אבד בו דבר), והדיסקים נמדדו ולא פיגרו
    asked = f"{e.stream_bps / 10 ** 6:.0f}Mb/s"
    if e.disks_behind:
        return Verdict(DISK_BOUND, f"לא אבדו חבילות, אבל הדיסקים פיגרו: לא הוכח שהמשכפל קיבל {asked}. לא נלמד דבר")
    if e.achieved_bps is None:
        return Verdict(UNDECIDED, f"לא אבדו חבילות, אבל הקצב שהגל הגיע אליו לא נקרא: לא הוכח שהמשכפל קיבל {asked}. לא נלמד דבר")
    if e.achieved_bps < drive_rates.KEEP_UP * e.stream_bps:
        return Verdict(NOT_PUSHED, f"לא אבדו חבילות, אבל הגל הגיע ל-{e.achieved_bps / 10 ** 6:.1f}Mb/s בלבד מתוך {asked} "
                                   f"(מתחת ל-{round(100 * drive_rates.KEEP_UP)}%): לא הוכח שהמשכפל קיבל {asked}. לא נלמד דבר")
    if e.disks_behind is None:
        return Verdict(UNDECIDED, f"לא אבדו חבילות, אבל לא ידוע אם הדיסקים פיגרו: לא הוכח שהמשכפל קיבל {asked}. לא נלמד דבר")
    return Verdict(CLEAN, f"לא אבדו חבילות, הגל הגיע ל-{e.achieved_bps / 10 ** 6:.0f}Mb/s והדיסקים לא פיגרו, והמעבד "
                          f"{'לא נקרא' if e.cpu_floor is None else f'מתחת ל-{cpu_threshold:.0f}%'}: המשכפל קיבל לפחות {asked}")


# --- מה שנשמר ----------------------------------------------------------------------------------------------------


def _row(conn, mac: str) -> dict:
    return memory.summary(conn, "machine", mac).get(SOURCE) or {}


def _stat(row: dict, field: str, which: str) -> float | None:
    got = ((row.get("num") or {}).get(field) or {}).get(which)
    return got if isinstance(got, (int, float)) and not isinstance(got, bool) else None


def _text(row: dict, field: str) -> str | None:
    got = ((row.get("text") or {}).get(field) or {}).get("value")
    return got if isinstance(got, str) else None


def cpu_threshold(row: dict) -> float:
    """הרף של המעבד למכונה: ``CPU_START``, או הנמוך ממנו שבו המכונה הזו כבר איבדה חבילות (לא מתחת ל-``CPU_FLOOR``)."""
    seen = _stat(row, "cpu_at_loss", "min")
    return CPU_START if seen is None else max(CPU_FLOOR, min(CPU_START, seen))


def _ceiling(row: dict) -> int | None:
    limited, clean = _stat(row, "limited_bps", "min"), _stat(row, "clean_bps", "max")
    if limited is None:
        return None                       # עמד בכל מה שניתן לו: הוכחה ל"לפחות", לא תקרה
    if clean is None:
        return int(limited)               # חסם עליון: עיכב בקצב הזה
    return int(clean) if clean < limited else None   # נקי מעל קצב שעיכב: ראיה סותרת, לא נלמד


def learned(conn, mac: str) -> int | None:
    """היכולת הנלמדת של המשכפל בביטים לשנייה, או ``None`` = לא נלמדה (הערך הקיים -- כרטיס × 90% -- נשאר). אינו זורק."""
    try:
        return _ceiling(_row(conn, mac))
    except Exception:                       # noqa: BLE001 -- הצרכנים נופלים בחזרה לערך הקיים
        log.exception("brain: the learned capability of %s was not read", mac)
        return None


# --- הראיות של גל ------------------------------------------------------------------------------------------------


def _json(raw: object) -> object:
    try:
        return json.loads(raw) if isinstance(raw, str) and raw else None
    except ValueError:
        return None


def _count(v: object) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None


def _achieved(stream_raw: object) -> int | None:
    """#3554: הקצב שהגל הגיע אליו בביטים לשנייה, מ-`wave_rates.stream`: `chunk.achieved_bps` (`chunk_rate.sample_of`: מה ששודר כפול
    החלק שהגיע). None = לא נקרא (הזרם עוד לא נרשם, או שהאבדן לא נמדד). #3173: `stream_bps` של השולח הקודם (גל ישן) אינו נקרא."""
    stream = _json(stream_raw)
    chunk = stream.get("chunk") if isinstance(stream, dict) else None
    return _count(chunk.get("achieved_bps")) if isinstance(chunk, dict) else None


def _wave_link(decision: object, mac: str) -> int | None:
    """#3940: המהירות ש**נקראה** (`reported`/`manual`) כשהגל יצא (`drive_rates.choose`: `cloner_links`). None = לא נקראה."""
    links = decision.get("cloner_links") if isinstance(decision, dict) else None
    mbps = links.get(mac) if isinstance(links, dict) else None
    return mbps if isinstance(mbps, int) and not isinstance(mbps, bool) and mbps > 0 else None


def _evidence(conn, session_id: str, mac: str, machine_raw: object) -> tuple[Evidence, dict]:
    """מה שהמשכפל וגם השרת יודעים על המשכפל בגל. מחזיר גם את הכרטיס: ``{"iface", "mbps"}``."""
    wave = conn.execute("SELECT decision, stream FROM wave_rates WHERE session_id = ?", (session_id,)).fetchone()
    decision = _json(wave["decision"]) if wave is not None else None
    stream = drive_rates.parse_bitrate(decision.get("max_bitrate")) if isinstance(decision, dict) else None
    member = conn.execute("SELECT udp_drops_json FROM session_members WHERE session_id = ? AND mac = ?",
                          (session_id, mac)).fetchone()
    drops = _json(member["udp_drops_json"]) if member is not None else None
    drops = drops if isinstance(drops, dict) else {}
    sensed = conn.execute("SELECT senses_json FROM wave_senses WHERE session_id = ? AND mac = ?",
                          (session_id, mac)).fetchone()
    senses = _json(sensed["senses_json"]) if sensed is not None else None
    senses = senses if isinstance(senses, dict) else {}
    raw_nic, raw_cpu = senses.get("nic"), senses.get("cpu")
    nic = raw_nic if isinstance(raw_nic, dict) else {}
    cores = raw_cpu if isinstance(raw_cpu, list) else []
    peaks = [c.get("busy_max") if isinstance(c, dict) else None for c in cores]
    numeric = [p for p in peaks if isinstance(p, (int, float)) and not isinstance(p, bool)]
    floor = float(min(numeric)) if peaks and len(numeric) == len(peaks) else None
    disks = list(senses["disks"].values()) if isinstance(senses.get("disks"), dict) else []
    utils = [d["util_max"] for d in disks if isinstance(d, dict) and isinstance(d.get("util_max"), (int, float))
             and not isinstance(d["util_max"], bool)]
    machine = agentsenses.well_formed_machine(machine_raw)
    rx = machine["rx"] if machine else None
    behind = None
    if rx is not None and rx.get("busy") is not None:
        behind = rx["busy"] > 0                           # תור האימות של המקלט התמלא: הדיסקים פיגרו (`busy`)
    if utils and max(utils) >= DISK_UTIL_START:
        behind = True                                     # מגירה עמוסה: גם בלי עדות של המקלט
    elif behind is None and utils and len(utils) == len(disks):
        behind = False                                    # כל המגירות נקראו, ואף אחת לא עמוסה
    sensed_nic = nic_speed.view(conn, mac)
    mbps = sensed_nic["speed_mbps"] if sensed_nic["source"] in ("reported", "manual") else None
    if mbps is None and sensed_nic["why"] in ("stale", "older_boot"):
        mbps = _wave_link(decision, mac)      # #3940: הדיווח פג בגל ארוך; מה שהגל עצמו קרא כשיצא הוא ראיה, ולא הנחה
    iface = nic.get("iface") if isinstance(nic.get("iface"), str) and nic.get("iface") else None
    return (Evidence(stream, _count(drops.get("rcvbuf")), _count(drops.get("abandoned")),
                     _count(nic.get("rx_missed")), _count(nic.get("rx_no_buffer")), floor, behind,
                     _achieved(wave["stream"] if wave is not None else None)),
            {"iface": iface, "mbps": mbps})


def _check_card(conn, mac: str, session_id: str | None, mode: str, row: dict, card: dict, seen_at: str = "wave_end") -> dict:
    """הכרטיס או מהירות הקישור השתנו: מה שנלמד נזרק, ושורה ביומן. מחזיר את הסיכום שנשאר (ריק אחרי איפוס).
    ``seen_at`` = איפה זה נתפס (``hello`` -- #4090, או ``wave_end`` -- רשת הביטחון): נרשם בראיות."""
    old_iface, old_mbps = _text(row, "iface"), _stat(row, "link_mbps", "last")
    why = []
    if card["iface"] and old_iface and card["iface"] != old_iface:
        why.append(f"הכרטיס {old_iface} -> {card['iface']}")
    if card["mbps"] and old_mbps and int(old_mbps) != card["mbps"]:
        why.append(f"מהירות הקישור {int(old_mbps)} -> {card['mbps']}Mb/s")
    if not why:
        return row
    was = _ceiling(row)
    reason = "; ".join(why) + ": היכולת שנלמדה נזרקה, והמוח לומד אותה מחדש בגל הבא"
    # #4160 (עיקרון 5): הכתיבה קודם. `remember` מחזיר False כשנכשלה, ו"נזרקה" ביומן בלי שנזרקה היא ראיה שקרית; השורה הישנה
    # נשארת עם המהירות הישנה, ולכן ה-hello הבא (או סוף הגל) רואה שוב שינוי ומנסה שוב
    if not memory.remember(conn, "machine", mac, SOURCE, {"link_mbps": card["mbps"]},
                           {"iface": card["iface"], "reset": reason}, replace=True):
        log.error("brain: the capability of %s was NOT reset (%s): the write failed, the old one stays and is retried", mac, one_line(reason))
        return row
    memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=RESET, reason=reason, round_id=session_id,
                  entity=("machine", mac), outcome=OUTCOME_LEARNED,
                  evidence=(f"was_bps={'unlearned' if was is None else was}", f"was_iface={old_iface}",
                            f"was_link_mbps={'unmeasured' if old_mbps is None else int(old_mbps)}",
                            f"iface={card['iface']}", f"link_mbps={card['mbps'] or 'unmeasured'}", f"seen_at={seen_at}"))
    journal(conn, "capability_reset", f"{mac} {reason}", machine=mac, round_id=session_id)
    return _row(conn, mac)


def reset_on_hello(conn, mac: str, mbps: int | None) -> None:
    """#4090: ה-hello מדווח מהירות קישור שונה מזו שהיכולת הנלמדת נשמרה מולה -- היא נזרקת **עכשיו**, לפני ה-precheck של הסבב
    ולפני חיזוי ה-ETA של הגל הבא, ולא רק בסוף הגל (ברזל 24/11: קישור שחזר מ-100 ל-1000 המשיך להזהיר ב-100Mb בשני גלים).
    אותו איפוס בדיוק (`_check_card`: שורת `capability_reset` ו-LR24/RESET עם הסיבה), והאיפוס בסוף הגל נשאר רשת ביטחון.

    ``mbps`` = מה ש-hello **קרא** (`nic_live.live`); ``None`` = לא נקרא (סוכן ישן, קישור למטה, sysfs שבור): לא מאפסים, כי
    "לא נקרא" אינו "השתנה" (עיקרון 5). מהירות ידנית (`nic_speed.manual`) היא של המפעיל ולא של ה-hello: היכולת נשמרת מולה, ולכן
    ה-hello אינו שופט אותה. הכרטיס (`iface`) אינו נבדק כאן: רק המהירות. אינו זורק."""
    try:
        if not isinstance(mbps, int) or isinstance(mbps, bool) or mbps <= 0:
            return
        mode = veto.mode(conn)
        if mode == veto.OFF or nic_speed.manual(conn, mac) is not None:
            return
        row = _row(conn, mac)
        if row:
            _check_card(conn, mac, None, mode, row, {"iface": _text(row, "iface"), "mbps": mbps}, "hello")
    except Exception:                       # noqa: BLE001 -- ה-hello אינו נעצר בגלל המוח
        log.exception("brain: the capability of %s was not checked against the hello", mac)


def learn(conn, mac: str, session_id: str, machine_raw: object, state: object) -> None:
    """המעבר הטרמינלי של משכפל בגל: שואל מי עיכב, ולומד רק אם זו המכונה. ``state`` = מצב המכונה: רק ``done`` נלמד
    ממנו (גל שנכשל או נעצר מעורבב מדי). אינו זורק."""
    try:
        mode = veto.mode(conn)
        if mode == veto.OFF or state != "done":
            return
        e, card = _evidence(conn, session_id, mac, machine_raw)
        row = _check_card(conn, mac, session_id, mode, _row(conn, mac), card)
        v = judge(e, cpu_threshold(row))
        evidence = (f"stream_bps={e.stream_bps if e.stream_bps is not None else 'unmeasured'}",
                    *(f"{k}={'unmeasured' if x is None else x}" for k, x in (
                        ("rcvbuf", e.rcvbuf), ("abandoned", e.abandoned), ("rx_missed", e.rx_missed),
                        ("rx_no_buffer", e.rx_no_buffer), ("cpu_floor", e.cpu_floor),
                        ("disks_behind", e.disks_behind), ("achieved_bps", e.achieved_bps))),
                    f"cpu_threshold={cpu_threshold(row):.0f}", f"iface={card['iface']}",
                    f"link_mbps={card['mbps'] or 'unmeasured'}")
        learnt = v.verdict in (LIMITED, CLEAN)
        memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=v.verdict, reason=v.reason + ". צל: רק נרשם",
                      evidence=evidence, round_id=session_id, entity=("machine", mac),
                      outcome=OUTCOME_LEARNED if learnt else OUTCOME_NOTHING)
        if learnt:
            _keep(conn, mac, row, e, card, v)
    except Exception:                       # noqa: BLE001 -- הלמידה אינה מפילה דיווח
        log.exception("brain: the capability of %s in %s was not learned", mac, session_id)


def _keep(conn, mac: str, row: dict, e: Evidence, card: dict, v: Verdict) -> None:
    if e.stream_bps is None:                  # `judge` מחזירה ``UNDECIDED`` בלי קצב, ולכן לא מגיעים לכאן; `learn` מתעדת וממשיכה
        raise ValueError("a learned verdict without a stream rate")
    rate = float(e.stream_bps)
    nums: dict = {"link_mbps": card["mbps"]}
    texts: dict = {"iface": card["iface"]}
    if v.verdict == CLEAN:
        nums["clean_bps"] = rate
        best = _stat(row, "clean_bps", "max")
        if best is None or rate > best:           # שיא חדש: העומס והתאריך שלו
            nums["cpu_at_best"] = e.cpu_floor
            texts["best_at"] = now_iso()
    else:
        nums["limited_bps"] = rate
        if v.lost:
            nums["cpu_at_loss"] = e.cpu_floor
    memory.remember(conn, "machine", mac, SOURCE, nums, texts)


# --- השפעה על הקצב של הגל: צל ------------------------------------------------------------------------------------


def wave_rate(conn, session_id: str, bits: int, macs: list[str]) -> int | None:
    """הגל יוצא בקצב ``bits``. נרשם בצל כמה היה יוצא לפי היכולת הנלמדת של ``macs``, ומוחזר הקצב הזה (או ``None`` = אף
    משכפל לא נלמד, או שהוא לא מוריד). **שום דבר לא זז.** אינו זורק."""
    try:
        mode = veto.mode(conn)
        if mode == veto.OFF:
            return None
        caps = {mac: c for mac in sorted(set(macs)) if (c := learned(conn, mac)) is not None}
        low = min(caps.items(), key=lambda kv: kv[1]) if caps else None
        if low is None:
            verdict, reason = NOT_LEARNED, "אף משכפל בגל לא נלמדה לו יכולת עדיין: הקצב נשאר לפי הכרטיס"
        elif low[1] < bits:
            verdict = WOULD_LOWER
            reason = (f"היכולת הנלמדת של {low[0]} היא {low[1] / 10 ** 6:.0f}Mb/s: היה מוריד את קצב הגל מ-"
                      f"{bits / 10 ** 6:.0f}Mb/s. צל: הגל יוצא כמו היום")
        else:
            verdict = NO_CHANGE
            reason = f"היכולת הנלמדת הנמוכה ({low[1] / 10 ** 6:.0f}Mb/s, {low[0]}) אינה מתחת לקצב הגל: אין שינוי"
        memory.record(conn, mode=mode, rule=RULE, action=RATE_ACTION, verdict=verdict, reason=reason,
                      evidence=(f"stream_bps={bits}", f"cloners={len(set(macs))}",
                                *(f"learned={mac}:{c}" for mac, c in caps.items())),
                      round_id=session_id, entity=("round", session_id), outcome="צל: הגל יצא בקצב הרגיל")
        return low[1] if low is not None and verdict == WOULD_LOWER else None
    except Exception:                       # noqa: BLE001 -- הגל יוצא גם כשהמוח נכשל
        log.exception("brain: the learned rate of wave %s was not recorded", session_id)
        return None
