"""#3064, K3, קבוצה ד': שערי המשכפלים בסבב לפני פתיחתו -- AHCI (B23), אתחולים (B52), PXE (B53), כרטיס רשת (B54), קושחה וחיווט (B56).

הכללים (פסקים, צל בלבד, סיבה מהקוד הקיים) ב-`server/brain/gates.py`. כל פונקציה: ``(conn, macs, round_id) -> list[Reading]``,
וקריאה **לכל** משכפל ב-``macs`` (``Reading.mac``): משכפל שלא נקרא אינו "תקין", ולכן אין דילוג. שער שנכשל על משכפל אחד אינו
מפיל את השאר: אותו משכפל מקבל ``UNDECIDED`` בשמו (`_each`).

הסיבה של כל קריאה היא שם המשכפל והטקסט של הקוד הקיים, ללא ניסוח מחדש: `agentsenses.ahci_gate`, שורות `pxe_log.STATES_HE`,
`probe.verdicts`, טקסט השינוי של `firmware_image`, `wiring.advice`, `loops.loop_checks`, וטקסט הקונסולה של האתחולים החוזרים
(``stuckNote`` ב-`server/static/console.js`). ``OPEN`` נכתב רק על ראיה חיובית, והראיה בשדה ``evidence``.

**מה אינו נקרא, במכוון:** duplex, rx_dropped ו-tx_errors נאספים בסוכן (`probe_nic`), אבל אין להם ב-`probe.verdicts` ולא בשום מקום
אחר בשרת סף או כלל; השער אינו ממציא סף. `linkwarn` כבר מזהיר על קישור איטי, ולכן הוא גם אינו כאן.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import Callable

from server.brain.gate_core import BLOCK, OPEN, UNDECIDED, WARN, Reading, name_of, undecided

log = logging.getLogger("imagesrv.brain")

#: הטקסט שהמפעיל רואה היום על משכפל שאתחל שוב ושוב (`stuckNote` ב-`server/static/console.js`); הטסט מוודא שהוא עדיין שם.
BLOCKED_HE = "נכשל באתחול {n} פעמים — נשלח לדיסק המקומי"
REPEATED_HE = "אתחל {n} פעמים ולא הצטרף"
_RANK = {OPEN: 0, UNDECIDED: 1, WARN: 2, BLOCK: 3}


def _name(conn: sqlite3.Connection, mac: str) -> str:
    try:
        return name_of(conn, mac)
    except Exception:                                                       # noqa: BLE001 -- שם שלא נקרא אינו מפיל קריאה
        return mac


def _each(conn: sqlite3.Connection, macs: tuple[str, ...], gate: str, one: Callable[[str], Reading]) -> list[Reading]:
    """קריאה לכל משכפל. משכפל שהבדיקה שלו זרקה = ``UNDECIDED`` בשמו, והשאר נקראים."""
    if not macs:
        return [undecided("לא נבחרו משכפלים לסבב, ואין מה לבדוק")]
    out = []
    for mac in macs:
        try:
            out.append(one(mac))
        except Exception as exc:                                            # noqa: BLE001 -- משכפל אחד שנפל אינו מפיל את השער
            log.exception("brain: the %s round gate failed for %s", gate, mac)
            out.append(undecided(f"{_name(conn, mac)}: לא הצלחנו לבדוק: {type(exc).__name__}", (f"error={type(exc).__name__}",), mac))
    return out


def _round(conn: sqlite3.Connection, round_id: str) -> tuple[str | None, dict[str, set[int]] | None]:
    """``(הקשר האתחול של גל הסבב, {mac: חיבורי ה-SATA שנבחרו})``. ``None`` = לא נקרא: אין לנחש מהו."""
    try:
        row = conn.execute("SELECT wave_session_id, target_slots_json FROM room_rounds WHERE id = ?", (round_id,)).fetchone()
        if row is None:
            return None, None
        slots = {item["mac"]: {p for p in item["ports"] if isinstance(p, int) and not isinstance(p, bool)}
                 for item in json.loads(row["target_slots_json"])}
        return (f"session:{row['wave_session_id']}" if row["wave_session_id"] else None), slots
    except Exception:                                                       # noqa: BLE001 -- "לא נקרא" אינו "אין"
        log.exception("brain: the round %s could not be read by the machine gates", round_id)
        return None, None


# --- B23: מצב בקר ה-SATA והדרייבר ---------------------------------------------------------------------------------------


def ahci(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B23. ההחלטה היא של `agentsenses.ahci_gate` (אותו כלל כמו ב-hello), על התמונה האחרונה של הקושחה. ``ide``/``raid`` = ``WARN``;
    ``BLOCK`` רק בסבב שהוא פס ייצור (נדב 13/11: "בלי AHCI אין פס"), והסיבה אומרת שזה צל. לא נקרא = ``UNDECIDED``."""
    from server.agent import firmware_image                                 # noqa: PLC0415
    from server.brain import agentsenses                                    # noqa: PLC0415
    from server.rounds import prodline                                      # noqa: PLC0415

    try:
        line: bool | None = prodline.line_of(conn, round_id) is not None
    except Exception:                                                       # noqa: BLE001 -- פס שלא נקרא: לא מחמירים ל-BLOCK על ניחוש
        log.exception("brain: the production line of %s was not read by the AHCI gate", round_id)
        line = None

    def one(mac: str) -> Reading:
        cur = firmware_image.changes_for(conn, mac)["current"]
        mode, driver = (cur.get("sata_mode"), cur.get("sata_driver")) if cur else (None, None)
        verdict, why = agentsenses.ahci_gate(mode, driver)
        ev = (f"mode={mode}", f"driver={driver}", f"snapshot_at={cur['at'] if cur else None}")
        reason = f"{_name(conn, mac)}: {why}"
        if verdict == agentsenses.AHCI_OK:
            return Reading(OPEN, reason, ev, mac)
        if verdict == agentsenses.NOT_READ:
            return undecided(reason, ev, mac)
        return Reading(BLOCK if line else WARN, reason, (*ev, f"production_line={'unknown' if line is None else str(line).lower()}"), mac)

    return _each(conn, macs, "ahci", one)


# --- B52: אתחולים שנכשלו ולולאות ----------------------------------------------------------------------------------------


def boot(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B52. ``boot_attempts`` (`bootguard`) ו-`agent_loops` (`loops`). ``BLOCK`` רק כשהשומר של #75 כבר מסרב למכונה בהקשר של
    **הסבב הזה** (נשלחה לדיסק המקומי); אתחול חוזר באותו הקשר, הגעה לסוכן בלי סיבה, ומכונה שהשומר שלח לדיסק המקומי בעבודה
    קודמת = ``WARN``. ``OPEN`` רק כששני המקורות נקראו ואין בהם דבר; מקור שלא נקרא = ``UNDECIDED``, לא "אין לולאות"."""
    from server.agent import bootguard, loops                               # noqa: PLC0415
    from server.console.journal_he import EVENTS_HE                         # noqa: PLC0415

    context, _slots = _round(conn, round_id)
    try:
        last_context = {r["mac"]: r["context"] for r in conn.execute("SELECT mac, context FROM boot_attempts").fetchall()}
        repeated = {ctx: bootguard.repeats(conn, ctx) for ctx in set(last_context.values())}
    except Exception:                                                       # noqa: BLE001 -- מונה שלא נקרא אינו "אין אתחולים חוזרים"
        log.exception("brain: boot_attempts were not read by the boot gate")
        last_context = repeated = None
    try:
        in_loop = {r["id"]: r["detail"] for r in loops.loop_checks(loops.current(conn))}
    except Exception:                                                       # noqa: BLE001 -- ראו `health.py`: שאילתה שנפלה אינה "אין לולאות"
        log.exception("brain: agent_loops were not read by the boot gate")
        in_loop = None

    def one(mac: str) -> Reading:
        name, found, ev = _name(conn, mac), [], []
        if last_context is None:
            ev.append("attempts=unread")
        elif mac in last_context:
            ctx = last_context[mac]
            got = repeated[ctx].get(mac)
            ev += [f"context={ctx}", f"attempts={got['attempts'] if got else '<2'}", f"round_context={ctx == context}"]
            if got is not None and got["blocked"]:
                found.append((BLOCK if ctx == context else WARN, BLOCKED_HE.format(n=got["attempts"])))
            elif got is not None and ctx == context:
                found.append((WARN, REPEATED_HE.format(n=got["attempts"])))
        else:
            ev.append("attempts=none")
        if in_loop is None:
            ev.append("loops=unread")
        elif f"agent_loop:{mac}" in in_loop:
            found.append((WARN, f"{EVENTS_HE['agent_loop']} — {in_loop[f'agent_loop:{mac}']}"))
            ev.append("loops=in_loop")
        else:
            ev.append("loops=none")
        if found:
            return Reading(max((v for v, _t in found), key=_RANK.get), f"{name}: " + " · ".join(t for _v, t in found), tuple(ev), mac)
        unread = [he for he, bad in (("מונה האתחולים לא נקרא", last_context is None), ("רשימת הלולאות לא נקראה", in_loop is None)) if bad]
        if unread:
            return undecided(f"{name}: לא הצלחנו לבדוק אתחולים חוזרים ולולאות: " + "; ".join(unread), tuple(ev), mac)
        return Reading(OPEN, f"{name}: אין אתחולים חוזרים, ואין הגעה לסוכן בלי משימה ובלי סבב ב-{loops.SILENCE_SECONDS // 60} הדקות האחרונות", tuple(ev), mac)

    return _each(conn, macs, "boot", one)


# --- B53: אירועי PXE ו-DHCP --------------------------------------------------------------------------------------------


def pxe(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B53. ``pxe_log.verdict`` מאז ההערה האחרונה של המכונה. ``hello`` (הגיעה לסוכן) = ``OPEN``. לוג שלא נקרא, או **אין אף אירוע**
    של המכונה בחלון = ``UNDECIDED`` ("לא נמדד", ואינו "לא עלתה": מכונה שיושבת בסוכן שעות אינה משאירה אירוע). בעיה שנמדדה
    (קיבלה DHCP ולא טענה, או טענה ולא הגיעה לסוכן) = ``WARN`` בטקסט של `STATES_HE`."""
    from server.net import pxe_log                                          # noqa: PLC0415

    def one(mac: str) -> Reading:
        name = _name(conn, mac)
        got = pxe_log.verdict(conn, mac, pxe_log.since_last_wake(conn, mac))
        state, he, events = got["state"], got["he"], got["events"]
        ev = (f"state={state}", f"since={got['since']}", f"events={len(events)}")
        if state == "hello":
            return Reading(OPEN, f"{name}: {he}", ev, mac)
        if state == "unchecked":
            return undecided(f"{name}: {he}", ev, mac)
        if state == "no_dhcp" and not events:
            return undecided(f"{name}: לא נמדד: אין אף אירוע DHCP או PXE של המכונה מאז {got['since']}, ולכן אי אפשר לדעת אם עלתה", ev, mac)
        return Reading(WARN, f"{name}: {he}", ev, mac)

    return _each(conn, macs, "pxe", one)


# --- B54: כרטיס הרשת, כבל ו-LLDP ---------------------------------------------------------------------------------------


def _nic_measured(sample: dict) -> list[str]:
    """מה במדגם נמדד בפועל (ראיה חיובית): ``crc`` = מונה rx_crc_errors מספרי; ``cable`` = בדיקת הכבל החזירה ``ok``."""
    out = []
    for n in sample.get("nic") or []:
        stats = n.get("stats") if isinstance(n, dict) else None
        crc = stats.get("rx_crc_errors") if isinstance(stats, dict) else None
        if isinstance(crc, int) and not isinstance(crc, bool):
            out.append("crc")
            break
    netprobe = sample.get("netprobe")
    cable = netprobe.get("cable") if isinstance(netprobe, dict) else None
    if isinstance(cable, dict) and cable.get("status") == "ok":
        out.append("cable")
    return out


def nic(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B54. הדגימה האחרונה של `probe` (S31, S32), ורק מה ש-`probe.verdicts` כבר מחליט עליו: שגיאות CRC, כבל פגום, כפילות IP.
    ממצא = ``WARN`` (שום כלל קיים אינו מסרב למכונה כזו, ולכן אין ``BLOCK``). ``OPEN`` רק כשמונה ה-CRC נמדד או שבדיקת הכבל החזירה
    ``ok``, ולא נמצא ממצא. אין דגימה, או שאף אחד משניהם לא נמדד = ``UNDECIDED``."""
    from server.agent import probe                                          # noqa: PLC0415

    def one(mac: str) -> Reading:
        name, latest = _name(conn, mac), probe.latest(conn, mac)
        if latest is None:
            return undecided(f"{name}: אין בדיקת מכונה (probe) של המשכפל: סוכן ישן, או שעוד לא דיווח", ("probe=none",), mac)
        sample = latest["probe"]
        measured = _nic_measured(sample)
        ev = (f"sampled_at={latest['sampled_at']}", f"measured={','.join(measured) or 'none'}")
        found = [v for v in probe.verdicts(sample) if v["key"] == "cable" or v["key"].startswith(("crc:", "ip_conflict:"))]
        if found:
            return Reading(WARN, f"{name}: " + " · ".join(v["text_he"] for v in found), (*ev, *(f"finding={v['key']}" for v in found)), mac)
        if not measured:
            return undecided(f"{name}: מוני כרטיס הרשת ובדיקת הכבל לא נמדדו בדגימה האחרונה", ev, mac)
        what = " ו".join({"crc": "מוני שגיאות הכרטיס", "cable": "בדיקת הכבל"}[m] for m in measured)
        return Reading(OPEN, f"{name}: נמדדו {what}, ואין שגיאות CRC, כבל פגום או כפילות IP", ev, mac)

    return _each(conn, macs, "nic", one)


# --- B56: סטיית קושחה והמלצת חיווט -------------------------------------------------------------------------------------


def _wiring(conn: sqlite3.Connection, mac: str, ports: set[int] | None) -> tuple[tuple[str, ...], str | None, bool]:
    """``(ראיות, טקסט אזהרה או None, האם לא הצלחנו לבדוק)``. בלי מספיק מדידה (`advice["enough"]`): ``wiring=no_basis``, ואין תרומה."""
    from server.learn import wiring                                         # noqa: PLC0415

    try:
        advice = wiring.advice(conn, mac)
    except Exception as exc:                                                # noqa: BLE001 -- "לא הצלחנו לבדוק" אינו "אין המלצה"
        log.exception("brain: the wiring advice of %s was not read by the firmware gate", mac)
        return (f"wiring=error:{type(exc).__name__}",), None, True
    if not advice["enough"]:
        return ("wiring=no_basis",), None, False
    if ports is None:
        return ("wiring=round_ports_unknown",), None, True
    bad = sorted(set(advice["avoid"]) & ports)
    if not bad:
        return ("wiring=ok",), None, False
    return ("wiring=avoid_in_round", *(f"avoid_port={p}" for p in bad)), " ".join([advice["text_he"] or "", *advice["move_notes_he"]]).strip(), False


def firmware(conn: sqlite3.Connection, macs: tuple[str, ...], round_id: str) -> list[Reading]:
    """B56. סטייה מול ההפניה של `firmware_image` (``baseline`` או ההדלקה הקודמת) = ``WARN``, בטקסט השינוי הקיים (אותו טקסט שביומן
    ``fw_changed``); וכן חיבור SATA של הסבב שהמלצת החיווט (LR9) אומרת להימנע ממנו. ``OPEN`` רק כשיש הפניה, אין סטייה, והחיווט
    נבדק או שאין לו בסיס. אין תמונה, אין הפניה, או חיווט שלא נקרא = ``UNDECIDED``."""
    from server.agent import firmware_image                                 # noqa: PLC0415

    _context, slots = _round(conn, round_id)

    def one(mac: str) -> Reading:
        name, got = _name(conn, mac), firmware_image.changes_for(conn, mac)
        cur = got["current"]
        wire_ev, wire_text, wire_failed = _wiring(conn, mac, None if slots is None else slots.get(mac, set()))
        ev = (f"snapshot_at={cur['at'] if cur else None}", f"ref={got['ref_kind']}", f"ref_at={got['ref_at']}", *wire_ev)
        texts = [c["text"] for c in got["changes"]] + ([wire_text] if wire_text else [])
        if texts:
            return Reading(WARN, f"{name}: " + " · ".join(texts), (*ev, *(f"changed={c['kind']}:{c.get('field', '')}" for c in got["changes"])), mac)
        if cur is None:
            return undecided(f"{name}: אין תמונת קושחה של המכונה: עוד לא דיווחה מצב אתחול, בקר ו-BIOS", ev, mac)
        if got["ref_kind"] is None:
            return undecided(f"{name}: יש תמונת קושחה אחת ואין הפניה להשוואה: לא נשמרה ״הגדרה נכונה״ וזו ההדלקה הראשונה", ev, mac)
        if wire_failed:
            return undecided(f"{name}: הקושחה זהה להפניה, אבל לא הצלחנו לבדוק את המלצת החיווט", ev, mac)
        which = "ההגדרה הנכונה ששמרו" if got["ref_kind"] == "baseline" else "ההדלקה הקודמת"
        return Reading(OPEN, f"{name}: הקושחה (מצב אתחול, מצב בקר, Secure Boot, גרסת BIOS) זהה ל{which}", ev, mac)

    return _each(conn, macs, "firmware", one)
