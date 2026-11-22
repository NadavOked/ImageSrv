"""#3064 (קטלוג N4, N6, N8): מה שאפשר לראות על רשת ההפצה **מהשרת ומהמשכפל**, בלי גישה לסוויץ'.

**אין כאן שום קוד שמתחבר למתג** (אין לנו SNMP ולא סיסמה, נדב 14/11). כל מה שכאן הוא האזנה פסיבית של השרת
(`server/net/netwatch.py`, `server/net/igmp_querier.py`) ושל המשכפל (`agent/netwatch.c`, בתוך ה-hello כ-`netwatch`).

**מצב צל, `reported`:** כל חוש נרשם ב-``brain_decisions`` (``mode=shadow``) **רק כשהמצב שלו השתנה**, ואף החלטה לא
נשענת עליו. הכתיבה כבויה כשמתג המוח כבוי (`veto.OFF`).

**עיקרון 5 -- שלושה מצבים שונים, לא שניים.** בכל חוש התשובה היא אחת מאלה:

- ``found`` / ``flooded`` / ``foreign_seen`` -- **ראיה חיובית** (שאילתה נשמעה, מסגרת נקלטה).
- ``none_found`` / ``not_flooded`` / ``quiet`` -- **האזנו, ובדקנו שההאזנה יכלה לשמוע, ולא שמענו.** זה לא "לא בדקנו".
- ``not_checked`` -- לא הצלחנו להאזין (אין סוכן, אין הרשאה, חלון קצר מדי, חומת אש שחוסמת). הסיבה בעברית ב-``why``.
- ``unmeasured`` (N6 בלבד) -- ההאזנה הצליחה, אבל לא היה מה לשפוט: אף גל לא רץ בכל חלון הדגימה, או שהקבוצה של
  הגל אינה ידועה. "לא היה זרם" אינו "אין הצפה".

**אין ספים.** "תעבורה אחרת" נרשמת כמספרים; המוח יתאים אותם לבסיס של הרשת, לא כאן (הכרעת נדב 14/11: אין אחוזים קבועים).
"""

from __future__ import annotations

import ipaddress
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Mapping

from server.brain import l2frames, memory, veto

log = logging.getLogger("imagesrv.brain")

FOUND, NONE_FOUND, NOT_CHECKED = "found", "none_found", "not_checked"
FLOODED, NOT_FLOODED, UNMEASURED = "flooded", "not_flooded", "unmeasured"
FOREIGN_SEEN, QUIET = "foreign_seen", "quiet"
SHADOW_HE = "צל: חוש שנרשם בלבד, שום החלטה לא נשענת עליו"

#: שאילתת IGMP נשלחת כל 125 שניות (RFC 2236). חלון קצר מזה יכול לפספס אותה **מעצם הגדרתו** ("לא נמצא" אינו ראיה).
QUERIER_WINDOW_S = 130
#: אצל השרת: `igmp_querier.OTHER_PRESENT` (2 x 125 + 5), הזמן ש-querier אחר "קיים" אחרי השאילתה האחרונה שלו.
SERVER_LISTEN_S = 260
MAX_GROUPS_KEPT = 8
#: מסגרות שעברו את המסנן ולא נקראו, ועדיין "קראנו הכול": הקרנל סופר עד רגע הקריאה, וה-poll נגמר מעט לפניו.
READ_SLACK = 16


@dataclass(frozen=True)
class Sense:
    rule: str                       # N4 / N6 / N8
    state: str
    why: str                        # בעברית, למפעיל ולמוח
    evidence: dict = field(default_factory=dict)


def _n(value: object) -> int | None:
    """מספר שלם אי-שלילי, או ``None``. ``bool`` אינו מספר, ו-``True`` אינו 1 (עיקרון 5: דיווח פגום אינו אפס)."""
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _ips(value: object) -> list[str]:
    out = []
    for item in value if isinstance(value, list) else []:
        try:
            out.append(str(ipaddress.ip_address(item)))
        except (ValueError, TypeError):
            continue
    return out[:MAX_GROUPS_KEPT]


def read_cloner(nw: object) -> tuple[dict | None, str | None]:
    """`netwatch` מה-hello → ``(נתונים, None)``, או ``(None, למה אין)``. לעולם אינו זורק: ה-hello אינו מאומת."""
    if nw is None:
        return None, "המשכפל לא דיווח האזנה (סוכן ישן, או בלי imagesrv-netwatch)"
    if not isinstance(nw, dict):
        return None, "דיווח ההאזנה פגום"
    if nw.get("pending"):
        return None, "הדגימה הראשונה של המשכפל עוד רצה"
    if "error" in nw:
        return None, f"ההאזנה של המשכפל נכשלה: {str(nw['error'])[:80]}"
    if nw.get("v") != 1 or not isinstance(nw.get("igmp"), dict) or _n(nw.get("window_s")) is None:
        return None, "דיווח ההאזנה פגום או מגרסה שאינה מוכרת"
    return nw, None


def _evidence(nw: Mapping, *keys: str) -> dict:
    out = {"window_s": _n(nw.get("window_s")), "age_s": _n(nw.get("age_s"))}
    out.update({k: nw.get(k) for k in keys})
    return out


# --- N4: האם יש querier ----------------------------------------------------------------------------------------


def n4_cloner(nw: object, *, server_sending: bool | None = None) -> Sense:
    """האם שאילתת IGMP מגיעה אל **הפורט של המשכפל**. ``server_sending`` = השרת עצמו שולח שאילתות (`Querier`):
    כששניהם נכונים וה-``none_found`` של המשכפל הוא ראיה לכך שהשאילתות נעצרות בדרך."""
    data, missing = read_cloner(nw)
    if data is None:
        return Sense("N4", NOT_CHECKED, missing or "", {})
    igmp = data["igmp"]
    queries, window = _n(igmp.get("queries")), _n(data.get("window_s"))
    evidence = _evidence(data) | {"queries": queries, "sources": _ips(igmp.get("sources")),
                                  "versions": igmp.get("versions") if isinstance(igmp.get("versions"), list) else None,
                                  "server_sending": server_sending}
    if queries is None or window is None:
        return Sense("N4", NOT_CHECKED, "מוני ה-IGMP של המשכפל פגומים", evidence)
    if queries:
        return Sense("N4", FOUND, f"המשכפל שמע {queries} שאילתות IGMP מ-{', '.join(evidence['sources']) or 'מקור לא ידוע'}",
                     evidence)
    if window < QUERIER_WINDOW_S:
        return Sense("N4", NOT_CHECKED, f"חלון ההאזנה ({window} ש') קצר מ-{QUERIER_WINDOW_S}: querier ששואל כל 125 ש' "
                                        "יכול היה לא להישמע", evidence)
    tail = " — והשרת שולח שאילתות: הן נעצרות בדרך" if server_sending else ""
    return Sense("N4", NONE_FOUND, f"לא נמצא querier: {window} שניות של האזנה, אפס שאילתות{tail}", evidence)


def n4_server(*, error: str | None, sending: bool, other: str | None, other_age_s: float | None,
              listening_s: float | None, hears: bool | None) -> Sense:
    """האם יש querier ברשת ההפצה מנקודת המבט של השרת: הוא עצמו (`sending`), או אחר שנשמע (`other`).

    ``hears``: חומת האש הטעונה מקבלת IGMP בכרטיס (`igmp_querier.firewall_hears`). בלעדיה "לא שמענו" אינו ראיה."""
    evidence = {"sending": sending, "other": other, "other_age_s": None if other_age_s is None else int(other_age_s),
                "listening_s": None if listening_s is None else int(listening_s), "hears": hears}
    if other is not None:
        return Sense("N4", FOUND, f"querier אחר ברשת ההפצה: {other}", evidence)
    if sending:
        return Sense("N4", FOUND, "השרת עצמו שולח שאילתות IGMP; querier אחר לא נשמע", evidence)
    if error is not None:
        return Sense("N4", NOT_CHECKED, f"ההאזנה של השרת ל-IGMP נכשלה: {error}", evidence)
    if hears is not True:
        return Sense("N4", NOT_CHECKED, "חומת האש אינה מקבלת IGMP בכרטיס ההפצה, או שלא נבדקה — לא שמענו ≠ אין querier",
                     evidence)
    if listening_s is None or listening_s < SERVER_LISTEN_S:
        return Sense("N4", NOT_CHECKED, "השרת מאזין פחות מ-260 שניות — עוד מוקדם לקבוע", evidence)
    return Sense("N4", NONE_FOUND, f"לא נמצא querier: השרת מאזין {int(listening_s)} שניות ואינו שולח שאילתות", evidence)


# --- N6: הצפת מולטיקאסט לפורט שלא ביקש ---------------------------------------------------------------------------


def n6_cloner(nw: object, *, engine_group: str | None, member: bool | None,
              wave_age_s: float | None) -> Sense:
    """האם משכפל **שלא הצטרף** לקבוצת הגל מקבל בכל זאת את התעבורה שלה.

    ``engine_group`` -- הקבוצה של מנוע החתיכות, או ``None`` כשהיא לא נקראה ("לא ידוע", עיקרון 5). ``member`` -- המשכפל חבר בגל שרץ עכשיו.
    ``wave_age_s`` -- כמה זמן גל שידור רץ ברצף, או ``None`` כשאין."""
    data, missing = read_cloner(nw)
    if data is None:
        return Sense("N6", NOT_CHECKED, missing or "", {})
    joined = data.get("joined")
    groups = data.get("groups") if isinstance(data.get("groups"), list) else []
    evidence = _evidence(data, "promisc", "joined", "passed", "kernel_drops") | {
        "engine_group": engine_group, "member": member, "wave_age_s": None if wave_age_s is None else int(wave_age_s)}
    if not isinstance(joined, list):
        return Sense("N6", NOT_CHECKED, "המשכפל לא קרא באילו קבוצות הצטרף (/proc/net/igmp)", evidence)
    if engine_group is None:
        return Sense("N6", UNMEASURED, "קבוצת המולטיקאסט של המנוע לא נקראה, "
                                       "ולכן אי אפשר לדעת איזו תעבורה היא שלנו", evidence)
    if engine_group in _ips(joined):
        return Sense("N6", UNMEASURED, "המשכפל עצמו הצטרף לקבוצה — קבלת התעבורה שלה היא בקשה, לא הצפה", evidence)
    if member:
        return Sense("N6", UNMEASURED, "המשכפל חבר בגל שרץ — הוא אינו 'פורט שלא ביקש'", evidence)
    seen = sum(_n(g.get("n")) or 0 for g in groups if isinstance(g, dict) and g.get("group") == engine_group)
    evidence["frames_to_group"] = seen
    if seen:
        return Sense("N6", FLOODED, f"משכפל שלא הצטרף לקבוצה {engine_group} קיבל {seen} מסגרות שלה — הסוויץ' מציף "
                                    "אותה לפורט שלא ביקש", evidence)
    window, age = _n(data.get("window_s")), _n(data.get("age_s")) or 0
    if wave_age_s is None or window is None or wave_age_s < age + window:
        return Sense("N6", UNMEASURED, "אף גל שידור לא רץ בכל חלון הדגימה: אפס מסגרות אינו אומר שהסוויץ' לא מציף",
                     evidence)
    if data.get("promisc") is not True:
        return Sense("N6", UNMEASURED, "ההאזנה לא הייתה במצב promisc: סינון החומרה של הכרטיס מסתיר הצפה", evidence)
    return Sense("N6", NOT_FLOODED, f"גל רץ בכל חלון הדגימה, והמשכפל (בלי להצטרף) לא קיבל אף מסגרת של {engine_group}",
                 evidence)


# --- N8: תעבורה אחרת ברשת ההפצה ---------------------------------------------------------------------------------


def n8(data: object, *, engine_group: str | None, who: str) -> Sense:
    """כמה מהמסגרות שהגיעו לפורט אינן של ImageSrv. ``data`` בצורת ה-JSON של `netwatch.c` (גם של המאזין בשרת).

    לא נספרים: ``arp``, ``dhcp``, ``wol``, ``igmp``, ``lldp``, ``l2ctl`` (צפויים), ו-``ipv6`` (רעש רקע, מדווח בנפרד).
    מולטיקאסט IPv4: מופחת הזרם של מנוע החתיכות; כשהקבוצה לא ידועה **כולו** מחוץ לספירה."""
    if not isinstance(data, dict) or not isinstance(data.get("classes"), dict):
        return Sense("N8", NOT_CHECKED, f"{who}: אין ספירת תעבורה", {})
    classes = {k: _n(data["classes"].get(k)) for k in l2frames.CLASSES}
    if any(v is None for v in classes.values()):
        return Sense("N8", NOT_CHECKED, f"{who}: ספירת התעבורה פגומה", {})
    groups = data.get("groups") if isinstance(data.get("groups"), list) else []
    ours = sum(_n(g.get("n")) or 0 for g in groups
               if isinstance(g, dict) and engine_group is not None and g.get("group") == engine_group)
    mcast_other = max(classes["mcast_ip"] - ours, 0) if engine_group is not None else 0
    foreign = sum(classes[k] for k in l2frames.FOREIGN) + mcast_other
    evidence = {"who": who, "window_s": _n(data.get("window_s")), "passed": _n(data.get("passed")),
                "sampled": _n(data.get("sampled")), "kernel_drops": _n(data.get("kernel_drops")),
                "foreign_in_sample": foreign, "classes": classes, "ours_group_frames": ours,
                "mcast_unattributed": classes["mcast_ip"] if engine_group is None else 0,
                "top_sources": data.get("macs") if isinstance(data.get("macs"), list) else None}
    if foreign:
        return Sense("N8", FOREIGN_SEEN, f"{who}: {foreign} מסגרות שאינן של ImageSrv בחלון הדגימה "
                                         f"({classes['bcast_ip']} שידור, {mcast_other} מולטיקאסט, "
                                         f"{classes['unicast_other']} יוניקאסט למי שאינו אנחנו, {classes['other']} אחר)", evidence)
    passed, sampled = evidence["passed"], evidence["sampled"]
    if passed is None or sampled is None:
        return Sense("N8", NOT_CHECKED, f"{who}: מונה הקרנל לא נקרא — אין לדעת אם כל מה שעבר נקרא", evidence)
    if passed > sampled + READ_SLACK:
        return Sense("N8", NOT_CHECKED, f"{who}: נקראו {sampled} מתוך {passed} מסגרות שעברו (השאר רק נספרו) — "
                                        "בדגימה לא נראתה תעבורה זרה, אבל לא ראינו הכול", evidence)
    return Sense("N8", QUIET, f"{who}: כל {passed} המסגרות שעברו נקראו, ואף אחת אינה תעבורה שאינה של ImageSrv", evidence)


# --- הרישום -----------------------------------------------------------------------------------------------------

_last: dict[tuple[str, str, str], str] = {}
_seen_sample: dict[str, str] = {}
_server_view: Callable[[], bool | None] = lambda: None


def set_server_view(sending: Callable[[], bool | None]) -> None:
    """main: האם השרת שולח שאילתות IGMP עכשיו (`Querier.sending`). בלי זה ``server_sending`` הוא ``None``: לא ידוע."""
    global _server_view
    _server_view = sending


def record(conn, sense: Sense, entity: tuple[str, str]) -> int | None:
    """שורה ב-``brain_decisions`` **רק כשהמצב של החוש לישות הזו השתנה**. מחזיר את מספר השורה, או ``None``
    (לא השתנה / המתג כבוי / הכתיבה נכשלה)."""
    key = (sense.rule, *entity)
    if _last.get(key) == sense.state or veto.mode(conn) == veto.OFF:
        return None
    row = memory.record(conn, mode="shadow", rule=sense.rule, action="SENSE", verdict=sense.state, reason=sense.why,
                        evidence=[sense.evidence], entity=entity, outcome=SHADOW_HE)
    if row is not None:                 # כתיבה שנכשלה נרשמת שוב בדגימה הבאה, לא נבלעת
        _last[key] = sense.state
    return row


def _epoch(text: object) -> float | None:
    from server.brain import observe
    return observe.epoch(text)


def cloner_context(conn, mac: str, now: float) -> dict:
    """מה שהשרת יודע **עכשיו** ושנחוץ לשפוט הצפה: הקבוצה של המנוע, כמה זמן גל שידור רץ ברצף, והאם המשכפל חבר בו.
    כל שאילתה שנכשלה היא ``None`` ("לא ידוע"), לא "אין גל" ולא "לא חבר"."""
    from server.rounds import chunksend
    ctx: dict = {"engine_group": None, "wave_age_s": None, "member": None}
    try:
        ctx["engine_group"] = chunksend.GROUP
    except Exception:                                   # noqa: BLE001
        log.exception("brain: the stream engine could not be read")
    try:
        row = conn.execute("SELECT MIN(started_at) AS at FROM sessions WHERE kind = 'multicast'"
                           " AND state = 'running'").fetchone()
        started = _epoch(row["at"]) if row is not None else None
        ctx["wave_age_s"] = max(now - started, 0.0) if started is not None else None
        ctx["wave_read"] = True
    except Exception:                                   # noqa: BLE001
        log.exception("brain: the running wave could not be read")
        ctx["wave_read"] = False
    try:
        ctx["member"] = conn.execute(
            "SELECT 1 FROM session_members m JOIN sessions s ON s.id = m.session_id WHERE m.mac = ?"
            " AND s.state IN ('open', 'running') AND m.state NOT IN ('done', 'failed', 'partial', 'lost')",
            (mac,)).fetchone() is not None
    except Exception:                                   # noqa: BLE001
        log.exception("brain: the membership of %s could not be read", mac)
    return ctx


def n8_cloner(nw: object, *, engine_group: str | None) -> Sense:
    data, missing = read_cloner(nw)
    if data is None:
        return Sense("N8", NOT_CHECKED, f"המשכפל: {missing}", {})
    return n8(data, engine_group=engine_group, who="המשכפל")


def note_hello(conn, mac: str, nw: object, now: float | None = None) -> list[Sense]:
    """נקרא מה-hello (`server.agent.hello`): שלושת החושים של המשכפל, ורישום מה שהשתנה. **לעולם אינו זורק** -- חוש
    בצל אינו מפיל hello. דגימה שכבר נשפטה (אותו תוכן, ללא ``age_s``) אינה נשפטת שוב: ה-hello חוזר כל כמה שניות
    עם אותה דגימה מהמטמון של הסוכן."""
    try:
        clock = time.time() if now is None else now
        sample = json.dumps({k: v for k, v in nw.items() if k != "age_s"} if isinstance(nw, dict) else nw,
                            sort_keys=True, default=str)
        if _seen_sample.get(mac) == sample:
            return []
        ctx = cloner_context(conn, mac, clock)
        senses = [n4_cloner(nw, server_sending=_server_view()),
                  n6_cloner(nw, engine_group=ctx["engine_group"], member=ctx["member"], wave_age_s=ctx["wave_age_s"]
                            if ctx["wave_read"] else None),
                  n8_cloner(nw, engine_group=ctx["engine_group"])]
        for sense in senses:
            record(conn, sense, ("machine", mac))
        _seen_sample[mac] = sample
        return senses
    except Exception:                                   # noqa: BLE001
        log.exception("brain: the network senses of %s were not recorded", mac)
        return []
