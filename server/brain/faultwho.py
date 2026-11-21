"""#3064 (B50): מי אשם בכשל של מגירה -- החריץ או הכבל, או הדיסק. **בצל בלבד.**

**למה.** השרת כבר מודד את כל מה שמבדיל בין "כבל" ל"דיסק", והמוח לא קרא אף אחד מהם:
- אירועי phy (S52) ואיפוסי קישור (S53) של הגל, ושם החיבור הפיזי (S58) -- ``targets_json[].senses``;
- CRC וקישור לכל חיבור (S29, LR8): ``learn.ports.verdict`` -- אותו חיבור בעייתי עם דיסקים שונים = החיבור, דיסק בעייתי
  בכל חיבור = הדיסק. היום רק ``room_precheck`` קורא אותו (המלצה כתומה), לא המוח;
- חריץ שנכשל שוב (R15): ``port_history.failed`` -- נשמר ב-#3125, ואף אחד לא קורא;
- הטמפרטורה בזמן הכתיבה (S54).

**הכלל.** בסוף כל גל (`room._brain_after_wave`, ליד B33), לכל מגירה שהייתה בה **ראיה בגל הזה** -- כשל, CRC או קישור איטי
(`ports._problem`), אירועי קו, או חום מעל ``smart_history.TEMP_WARN_C`` -- שורה אחת ב-``brain_decisions``
(``B50 / FAULT_WHO``, ישות ``slot``, ``mac/port``). מגירה שלא הייתה בה ראיה בגל הזה אינה נרשמת: לא שורה לכל מגירה בכל גל.

**הפסקים** (`judge`, טהורה), לפי הסדר:
- ``SLOT`` -- החריץ או הכבל: `learn.ports` אמר שהחיבור חשוד (≥ ``MIN_DISKS`` דיסקים שונים עם CRC/קישור/כשל-כבל), **או** שהכתיבה
  בו נכשלה עם ≥ ``MIN_DISKS`` דיסקים שונים, בגלים שבהם מגירה אחרת באותו משכפל לא נכשלה (אחרת זה הזרם או המשכפל, כמו ב-B33).
- ``DISK`` -- הדיסק: אותו סידורי בעייתי ב-≥ ``MIN_DISKS`` חיבורים שונים.
- ``UNDECIDED`` כששניהם נכונים יחד: שום ראיה לא מפרידה, והכלל אינו בוחר.
- ``LINK_NOW`` -- יש ראיה לקו בגל הזה (phy, איפוס, CRC, קישור איטי, כשל-כבל, או ש-K9 קבע ``LINK`` על אותו דיסק באותו גל, #3595)
  ואין היסטוריה שמבדילה: כבל או מגע, עוד לא ידוע איזה.
- ``HOT`` -- הדיסק היה חם מעל הסף בזמן הכתיבה. **לא מוכח כסיבה:** הכשל לא הוסבר, והחום נאמר.
- ``UNDECIDED`` -- המגירה נכשלה ואין ראיה שמבדילה.

**K9 הוא ראיה (#3595).** דיסק שנותק בקרנל (``removed``) אינו מדווח חושים (``conn=null``, ``link_events=null``), ולכן B50 הסיק מ"אין
ראיה" שאין, בזמן ש-K9 (`cause`) כבר אמר ``LINK`` על אותו סידורי באותו גל. B50 קורא את שורות K9 של הדיסק בגל (``entity=("disk", serial)``,
``round_id`` = הגל) ומציג את הפסק בראיות: ``k9=LINK`` (גם כשהוא אחר: ``DRIVE`` וכו'), ``k9=none`` (נקרא ואין שורה), ``k9=unread``
(השאילתה נכשלה). **רק ``LINK`` משנה את הפסק:** ראיה לקו, כמו phy. הוא אינו מכריע "כבל" -- המוח מציע ולא פוסל -- ואינו גובר על היסטוריה
(``SLOT``/``DISK``). K9 עצמו לא השתנה. K9 אחר (``DRIVE``, ``NETWORK``, ``SOFTWARE``, ``UNCLASSIFIED``) אינו משנה את הפסק (``UNDECIDED``), אבל
הנימוק מצטט אותו ואינו אומר "אין ראיה" (#3619). ``DISK`` מ-``DRIVE`` לבדו: שאלה פתוחה לנדב, לא הוכרעה.

**עיקרון 5.** ``None`` = לא נמדד, ואינו "תקין": חוש שהסוכן לא דיווח (סוכן ישן, `smartctl` שלא ענה) מופיע בראיות כ-``null``, וההיסטוריה
שלא נקראה (`verdict` נפל) נאמרת בשם בסיבה של ``UNDECIDED``. כשל בשאילתה אחת אינו עוצר את שאר המגירות, ולעולם אינו זורק אל סיום הגל.

**הספים אינם חדשים:** ``MIN_DISKS`` ו-``WINDOW`` של `learn.ports`, ו-``TEMP_WARN_C`` של `smart_history` (שהוא הערכה שלהם,
"עד שנדב יקבע"). אין הגדרה חדשה (נדב 14/11: ספים של המוח אינם בהגדרות).

**בצל:** הכלל רושם ואינו משנה צבע, כשל או סימון. המתג: ``brain_veto_mode`` (``off`` = לא מחושב).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from server.brain import agentsenses, memory, veto
from server.brain.cause import LINK as K9_LINK, RULE as K9_RULE
from server.disks import smart_history
from server.learn import ports as learn_ports

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "B50", "FAULT_WHO"
SLOT, DISK, LINK_NOW, HOT, UNDECIDED = "SLOT", "DISK", "LINK_NOW", "HOT", "UNDECIDED"
#: ``Seen.k9``: K9 נקרא ואין לו שורה על הדיסק בגל הזה (ולא ``None`` = השאילתה נכשלה, "לא נקרא").
K9_NONE = "none"
OUTCOME = "צל: רק נרשם, צבע המגירה והדיסק לא משתנים"
#: כמה גלים אחרונים של החריץ נקראים לספירת הדיסקים שנכשלו בו (אותו חלון של `learn.ports`).
WINDOW = learn_ports.WINDOW


@dataclass(frozen=True)
class Seen:
    """מה שידוע על חריץ אחד אחרי הגל. ``None`` = לא נמדד או לא נקרא -- לעולם לא "תקין"."""
    failed: bool | None = None          # הכתיבה בחריץ נכשלה בגל הזה (`port_history.failed`)
    problem: bool = False               # CRC עלה, קישור מתחת למקסימום, או כשל בסיבת כבל (`ports._problem`)
    link_events: int | None = None      # אירועי phy + איפוסי קישור בגל (S52, S53); None = אף אחד מהם לא נמדד
    peak_c: int | None = None           # השיא בזמן הכתיבה (S54)
    port_suspect: bool | None = None    # LR8: החיבור חשוד (≥ MIN_DISKS דיסקים שונים)
    port_disks: int | None = None
    disk_suspect: bool | None = None    # LR8: הדיסק חשוד (≥ MIN_DISKS חיבורים שונים)
    disk_ports: int | None = None
    failed_disks: int | None = None     # דיסקים שונים שנכשלו בחריץ כשמגירה אחרת במשכפל לא נכשלה; None = לא נקרא
    k9: str | None = None               # #3595: הפסק של K9 על הדיסק בגל הזה (`LINK`...), `K9_NONE` = אין שורה, None = לא נקרא
    k9_reason: str | None = None        # הסיבה ש-K9 כתב, לציטוט

    @property
    def hot(self) -> bool:
        return self.peak_c is not None and self.peak_c > smart_history.TEMP_WARN_C

    @property
    def evidence_now(self) -> bool:
        """יש משהו לומר על הגל הזה. בלי זה אין שורה, ולא משנה מה ההיסטוריה אומרת."""
        return bool(self.failed or self.problem or (self.link_events or 0) > 0 or self.hot)


def judge(s: Seen) -> tuple[str, str] | None:
    """(פסק, סיבה), או ``None`` כשאין בגל הזה ראיה לאמר עליה דבר."""
    if not s.evidence_now:
        return None
    slot = s.port_suspect is True or (s.failed_disks or 0) >= learn_ports.MIN_DISKS
    disk = s.disk_suspect is True
    if slot and disk:
        return UNDECIDED, "גם החריץ וגם הדיסק חשודים (דיסקים שונים נכשלו בחריץ, והדיסק נכשל בחריצים שונים): שום ראיה לא מפרידה"
    if slot:
        if s.port_suspect:
            return SLOT, (f"{s.port_disks} דיסקים שונים היו בעייתיים בחריץ הזה (CRC, קישור או כבל): החריץ או הכבל, לא הדיסק")
        return SLOT, (f"{s.failed_disks} דיסקים שונים נכשלו בחריץ הזה, כשמגירה אחרת באותו משכפל לא נכשלה: החריץ או הכבל, לא הדיסק")
    if disk:
        return DISK, f"אותו דיסק היה בעייתי ב-{s.disk_ports} חריצים שונים: הדיסק עצמו"
    k9_link = s.k9 == K9_LINK
    if s.problem or (s.link_events or 0) > 0 or k9_link:
        said = f'K9 קבע באותו גל על אותו דיסק {K9_LINK}: "{s.k9_reason}"' if k9_link else ""
        if s.problem or (s.link_events or 0) > 0:
            return LINK_NOW, ("יש ראיה לקו בגל הזה (אירועי phy, איפוס קישור, CRC, קישור איטי או כשל-כבל), ואין עדיין היסטוריה "
                              "שמבדילה בין החריץ לדיסק" + (f". {said}" if said else ""))
        return LINK_NOW, f"{said}. זו ראיה לקו (כבל או מגירה, לא הדיסק), ואין עדיין היסטוריה שמבדילה בין החריץ לכבל"
    if s.hot:
        return HOT, (f"הדיסק היה ב-{s.peak_c}C בזמן הכתיבה (סף {smart_history.TEMP_WARN_C}C), והכשל לא הוסבר בראיה אחרת: "
                     "החום נאמר, לא מוכח כסיבה")
    lost = " (ההיסטוריה של החריץ לא נקראה)" if s.port_suspect is None else ""
    lost += " (K9 לא נקרא)" if s.k9 is None else ""
    if s.k9 not in (None, "none"):
        # #3619: K9 סיווג את הכשל בגל הזה (DRIVE, NETWORK, SOFTWARE, UNCLASSIFIED): "אין ראיה" אינו נכון. הפסק נשאר UNDECIDED --
        # כשל אחד אינו היסטוריה של חריצים (DISK/SLOT), והמוח מציע ולא פוסל -- אבל הנימוק מצטט את K9
        return UNDECIDED, ("הכתיבה בחריץ נכשלה ואין היסטוריה או ראיית קו שמבדילות בין החריץ, הכבל והדיסק. "
                           f'K9 סיווג את הכשל באותו גל על אותו דיסק {s.k9}: "{s.k9_reason}". B50 מציג את זה ואינו מכריע ממנו לבד' + lost)
    return UNDECIDED, "הכתיבה בחריץ נכשלה ואין ראיה שמבדילה בין החריץ, הכבל והדיסק" + lost


def _failed_disks(conn, mac: str, port: int) -> int | None:
    """דיסקים שונים שנכשלו בחריץ בחלון, בגלים שבהם מגירה אחרת באותו משכפל **לא** נכשלה. ``None`` = השאילתה נכשלה."""
    try:
        rows = conn.execute(
            "SELECT DISTINCT p.serial FROM (SELECT * FROM port_history WHERE mac = ? AND port = ?"
            " ORDER BY at DESC, rowid DESC LIMIT ?) p WHERE p.failed = 1 AND EXISTS ("
            "SELECT 1 FROM port_history o WHERE o.session_id = p.session_id AND o.mac = p.mac"
            " AND o.port != p.port AND o.failed = 0)", (mac, port, WINDOW)).fetchall()
        return len(rows)
    except Exception:                           # noqa: BLE001 -- לא נקרא, לא "אפס"
        log.exception("brain: B50 could not count the failed disks of %s/%s", mac, port)
        return None


def _k9(conn, wave_id: str, serial: str | None) -> tuple[str | None, str | None]:
    """(הפסק, הסיבה) של K9 על הדיסק בגל: ``LINK`` אם אחת משורותיו אמרה כך (נקודות שונות של אותו גל), אחרת האחרונה. ``(K9_NONE, None)`` =
    נקרא ואין שורה. ``(None, None)`` = **לא נקרא** (בלי סידורי, או שהשאילתה נכשלה): לא "K9 לא אמר דבר"."""
    if not serial:
        return None, None
    try:
        got = memory.decisions(conn, kind="disk", key=serial, rule=K9_RULE, round_id=wave_id, limit=20)
    except Exception:                           # noqa: BLE001 -- לא נקרא, לא "אין שורה"
        log.exception("brain: B50 could not read K9 of %s in %s", serial, wave_id)
        return None, None
    link = next((d for d in got if d["verdict"] == K9_LINK), None)
    pick = link or (got[0] if got else None)
    return (pick["verdict"], pick["reason"]) if pick is not None else (K9_NONE, None)


def _senses(conn, members) -> dict[tuple[str, str], dict]:
    """``{(mac, serial): senses}`` של היעדים של הגל, מה שהסוכן דיווח (`agentsenses.well_formed_target`)."""
    out: dict[tuple[str, str], dict] = {}
    for m in members:
        try:
            mac = str(m["mac"])
            targets = json.loads(m["targets_json"] or "[]")
        except (KeyError, IndexError, TypeError, ValueError):
            continue                            # דיווח פגום: אין חושים למכונה הזו, וזה "לא נמדד"
        for t in targets if isinstance(targets, list) else ():
            if not isinstance(t, dict) or not isinstance(t.get("dev"), str):
                continue
            serial = t.get("serial") if isinstance(t.get("serial"), str) and t.get("serial") else smart_history.serial_of(conn, mac, t["dev"])
            senses = agentsenses.well_formed_target(t.get("senses"))
            if serial and senses is not None:
                out[(mac, serial)] = senses
    return out


def _link_events(senses: dict | None) -> int | None:
    """phy + איפוסים בגל. ``None`` = אף אחד משני החושים לא נמדד (לא 0)."""
    if senses is None:
        return None
    parts = [senses["phy"]["total"] if senses["phy"] else None, senses["resets"]["delta"] if senses["resets"] else None]
    got = [p for p in parts if p is not None]
    return sum(got) if got else None


def _evidence(row, s: Seen, senses: dict | None) -> list[str]:
    def show(v: object) -> str:
        return "null" if v is None else str(v)

    conn_ = senses["conn"] if senses else None
    return [f"mac={row['mac']}", f"port={row['port']}", f"serial={row['serial']}", f"failed={show(None if s.failed is None else int(s.failed))}",
            f"crc_delta={show(row['crc_delta'])}", f"link={show(row['link_gbps'])}/{show(row['link_max_gbps'])}Gbps",
            f"cable_fail={row['cable_fail']}", f"link_events={show(s.link_events)}", f"peak_c={show(s.peak_c)}",
            "conn=" + ("null" if conn_ is None else f"ata{conn_['ata']}/SATA {conn_['sata']}/drawer={conn_['drawer']}"),
            f"slot_bad_disks={show(s.port_disks)}", f"disk_bad_slots={show(s.disk_ports)}", f"slot_failed_disks={show(s.failed_disks)}",
            f"k9={'unread' if s.k9 is None else s.k9}"]


def after_wave(conn, wave_id: str, round_id: str | None, members=()) -> int:
    """הגל הסתיים (`room._brain_after_wave`, אחרי `learn_ports.record_wave`): כל חריץ עם ראיה בגל נשפט. מחזיר כמה שורות נרשמו."""
    try:
        mode = veto.mode(conn)
        if mode == veto.OFF:
            return 0
        senses = _senses(conn, members)
        rows = conn.execute("SELECT mac, port, serial, crc_delta, link_gbps, link_max_gbps, cable_fail, failed FROM port_history"
                            " WHERE session_id = ?", (wave_id,)).fetchall()
    except Exception:                           # noqa: BLE001 -- המוח אינו עוצר סיום גל
        log.exception("brain: B50 could not read wave %s", wave_id)
        return 0
    found = 0
    for row in rows:
        try:
            key = f"{row['mac']}/{row['port']}"
            got = senses.get((row["mac"], row["serial"]))
            verdict = learn_ports.verdict(conn, row["mac"], row["port"], row["serial"])
            temp = got["temp"] if got else None
            k9, k9_reason = _k9(conn, wave_id, row["serial"])
            seen = Seen(failed=None if row["failed"] is None else bool(row["failed"]),
                        problem=learn_ports._problem(row["crc_delta"], row["link_gbps"], row["link_max_gbps"], row["cable_fail"]),
                        link_events=_link_events(got), peak_c=temp["peak_during_c"] if temp else None,
                        port_suspect=verdict["port_suspect"], port_disks=verdict["port_disks"],
                        disk_suspect=verdict["disk_suspect"], disk_ports=verdict["disk_ports"],
                        failed_disks=_failed_disks(conn, row["mac"], row["port"]), k9=k9, k9_reason=k9_reason)
            judged = judge(seen)
            if judged is None:
                continue
            if any(d["point"] == wave_id for d in memory.decisions(conn, kind="slot", key=key, rule=RULE, round_id=round_id, limit=20)):
                continue                         # הגל הזה כבר נשפט במגירה הזו
            why = judged[1]
            memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=judged[0], reason=why, point=wave_id,
                          evidence=_evidence(row, seen, got), round_id=round_id, entity=("slot", key), outcome=OUTCOME)
            log.info("brain %s", json.dumps({"mode": mode, "rule": RULE, "action": ACTION, "verdict": judged[0], "slot": key,
                                              "reason": why}, ensure_ascii=False))
            found += 1
        except Exception:                       # noqa: BLE001 -- מגירה אחת שנכשלה אינה עוצרת את השאר
            log.exception("brain: B50 failed for %s/%s in %s", row["mac"], row["port"], wave_id)
    return found
