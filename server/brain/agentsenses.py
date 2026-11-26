"""#3064: חושי הסוכן במשכפל, ושער ה-AHCI. **בצל בלבד**: נרשם, אינו פועל.

הסוכן (``agent/lib/hw/sensecore.sh``) מוסיף לדיווח ההתקדמות שדה ``senses`` בשני מקומות שכבר קיימים, בלי שדה חדש
ברמה העליונה: ברמת המכונה (``machine_extra_json``) ולכל יעד (``target_extra_json``). הדיווח נשמר כמו שהוא ב-
``session_members.targets_json``; **כאן** הוא מנותח, ובמעבר הטרמינלי של המכונה נרשם ב-``brain_decisions``
(``mode=shadow``, וזיכרון לתמיד לפי #3185) -- בלי טבלה חדשה ובלי שינוי סכמה.

**עיקרון 5, בכל שדה:** ``None`` = לא נמדד. חוש שהסוכן לא יכול היה לקרוא מגיע כ-``null`` והופך כאן ל-``NOT_READ``,
לעולם לא ל-0 ולעולם לא ל"תקין".

המכונה: S41 מוני המקלט (``rx``), S51 carrier_changes, S59 הורדות ה-HTTP (``http``), S60 PSI, S65 הדרייבר של הבקר.
היעד: S52 אירועי phy של SATA, S53 איפוסי קישור, S54 טמפרטורה, S56 מהכנסה עד מוכן, S57 היה במחשב באתחול,
S58 שם החיבור הפיזי.
זהות האימג' (S64, B31): ``image_id`` של הסבב נרשם בראיות לכל יעד, ושורות ``chunk_ledger`` של אותו דיסק מאימג' אחר
נקראות ``MIXED``. הכתיבה עצמה כבר חוסמת -- ``chunkledger.record`` מוחק יומן של אימג' אחר, והמקלט פוסל חתיכה שאינה
באינדקס (``bad_chunk``) -- וזו הבדיקה שהמצב הזה אינו קיים.

**שער AHCI (B23) ו-R16:** בלי AHCI אין פס ייצור. כאן נרשמת רק ההחלטה "היה חוסם" (``WOULD_BLOCK_BAND``) ומוחזרת אזהרה
כתומה (``controller_warning``). **אין חסימה**: האם חוסמים או רק מזהירים היא הכרעה של נדב.
"""

from __future__ import annotations

import json
import logging

from server.brain import memory, veto
from server.db import now_iso

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "S-AGENT", "AGENT_SENSES"
GATE_RULE, GATE_ACTION = "B23", "AHCI_GATE"
IDENT_RULE, IDENT_ACTION = "B31", "IMAGE_IDENTITY"
OBSERVED, NOT_READ, LINK_TROUBLE, LINK_DROPPED = "OBSERVED", "NOT_READ", "LINK_TROUBLE", "LINK_DROPPED"
AHCI_OK, WOULD_BLOCK_BAND = "AHCI_OK", "WOULD_BLOCK_BAND"
SAME, MIXED, NO_LEDGER = "SAME", "MIXED", "NO_LEDGER"
SHADOW_HE = "צל: רק נרשם, שום פעולה לא בוצעה"
SATA_MODES = ("ahci", "ide", "raid")
MODE_HE = {"ahci": "AHCI", "ide": "IDE", "raid": "RAID"}
RX_KEYS = ("pkts", "foreign", "stray", "bad_pkt", "dup", "evicted", "busy", "bad_chunk", "good", "skipped", "conflict", "version")
HTTP_KEYS = ("ranges", "req_bytes", "taken", "secs", "curl_rc")


def _count(v: object, limit: int = 1 << 53) -> int | None:
    return v if type(v) is int and 0 <= v <= limit else None


def _pct(v: object) -> float | None:
    return float(v) if (type(v) is int or type(v) is float) and 0 <= v <= 100 else None


def _word(v: object, n: int = 40) -> str | None:
    return v if isinstance(v, str) and v and len(v) <= n and all(c.isalnum() or c in "_.-" for c in v) else None


def _obj(raw: object, keys: dict) -> dict | None:
    """אובייקט עם המפתחות ובדיקה לכל אחד; לא אובייקט = ``None`` (לא נמדד)."""
    return {k: check(raw.get(k)) for k, check in keys.items()} if isinstance(raw, dict) else None


def _rx(raw: object) -> dict | None:
    out = _obj(raw, {"passes": _count, "read": _count, **{k: _count for k in RX_KEYS}})
    return out if out and out["passes"] is not None and out["read"] is not None else None


def _http(raw: object) -> dict | None:
    out = _obj(raw, {"fetches": _count, "curl_failed": _count, **{k: _count for k in HTTP_KEYS}})
    return out if out and out["fetches"] is not None else None


def _psi(raw: object) -> dict | None:
    peak = _obj(raw.get("peak") if isinstance(raw, dict) else None, {k: _pct for k in ("cpu_some", "io_some", "io_full", "mem_some", "mem_full")})
    return {"samples": _count(raw.get("samples")), "peak": peak} if peak and isinstance(raw, dict) else None


def _ctrl(raw: object) -> list | None:
    if not isinstance(raw, list):
        return None
    return [{"mode": c.get("mode") if c.get("mode") in SATA_MODES else None, "driver": _word(c.get("driver"))}
            for c in raw[:8] if isinstance(c, dict)]


def well_formed_machine(raw: object) -> dict | None:
    """``senses`` ברמת המכונה: חמשת החושים, כל אחד ``None`` כשלא נמדד. לא אובייקט = ``None``."""
    if not isinstance(raw, dict):
        return None
    return {"carrier": _obj(raw.get("carrier"), {"iface": _word, "changes": _count, "delta": _count}),
            "psi": _psi(raw.get("psi")), "ctrl": _ctrl(raw.get("ctrl")), "rx": _rx(raw.get("rx")), "http": _http(raw.get("http"))}


def _phy(raw: object) -> dict | None:
    if not isinstance(raw, dict) or _count(raw.get("total")) is None or not isinstance(raw.get("ids"), dict):
        return None
    ids = {k: v for k, v in raw["ids"].items() if isinstance(k, str) and k.startswith("0x") and len(k) <= 8 and _count(v) is not None}
    return {"total": raw["total"], "ids": ids}


def _conn(raw: object) -> dict | None:
    out = _obj(raw, {"ata": _count, "sata": _count, "drawer": lambda v: v if v == "unmapped" else _count(v)})
    return out if out and out["ata"] is not None else None


def well_formed_target(raw: object) -> dict | None:
    """``senses`` של יעד אחד. ``at_boot`` ``True``/``False``/``None``; כל השאר מספר או ``None``."""
    if not isinstance(raw, dict):
        return None
    return {"phy": _phy(raw.get("phy")), "resets": _obj(raw.get("resets"), {"boot": _count, "delta": _count}),
            "conn": _conn(raw.get("conn")),
            "temp": _obj(raw.get("temp"), {"before_c": _count, "peak_during_c": _count, "after_c": _count, "samples": _count}),
            "ready_s": _count(raw.get("ready_s")), "at_boot": raw["at_boot"] if isinstance(raw.get("at_boot"), bool) else None}


def _show(v: object) -> str:
    return "לא נמדד" if v is None else str(v)


def machine_verdict(s: dict | None) -> tuple[str, str, tuple[str, ...]]:
    """(פסק, סיבה, ראיות) של חושי המכונה בגל. ``LINK_DROPPED`` רק כש-carrier_changes עלה בגל."""
    if s is None or all(v is None for v in s.values()):
        return NOT_READ, "הסוכן לא דיווח חושי מכונה (סוכן ישן, או שאף חוש לא נקרא)", ()
    ev, why = [], []
    c = s["carrier"]
    ev.append("carrier=" + ("null" if c is None else f"{c['iface']}:{c['changes']}:delta={c['delta']}"))
    rx = s["rx"]
    ev.append("rx=" + ("null" if rx is None else f"passes={rx['passes']},read={rx['read']}," + ",".join(f"{k}={rx[k]}" for k in RX_KEYS)))
    h = s["http"]
    ev.append("http=" + ("null" if h is None else ",".join(f"{k}={h[k]}" for k in ("fetches", *HTTP_KEYS, "curl_failed"))))
    p = s["psi"]
    ev.append("psi=" + ("null" if p is None else ",".join(f"{k}={v}" for k, v in p["peak"].items())))
    ev.append("ctrl=" + ("null" if s["ctrl"] is None else ";".join(f"{d['mode']}/{d['driver']}" for d in s["ctrl"])))
    if c and c["delta"]:
        return LINK_DROPPED, f"קישור הרשת נפל {c['delta']} פעמים תוך הגל ({c['iface']})", tuple(ev)
    why.append(f"קישור: {_show(c and c['delta'])}")
    why.append("מקלט: " + ("לא נמדד" if rx is None else f"{rx['read']} מתוך {rx['passes']} מעברים אמרו את המונים"))
    return OBSERVED, " · ".join(why), tuple(ev)


def target_verdict(s: dict | None, image_id: str | None = None) -> tuple[str, str, tuple[str, ...]]:
    """(פסק, סיבה, ראיות) של חושי יעד אחד. ``LINK_TROUBLE`` = אירועי phy או איפוס קישור בגל: כבל או מגע, לא הדיסק."""
    ev = [f"image_id={image_id or '?'}"]
    if s is None or all(v is None for v in s.values()):
        return NOT_READ, "הסוכן לא דיווח חושי יעד", tuple(ev)
    phy, rs, conn, t = s["phy"], s["resets"], s["conn"], s["temp"]
    ev += ["phy=" + ("null" if phy is None else f"total={phy['total']}," + ",".join(f"{k}={v}" for k, v in sorted(phy["ids"].items()))),
           "resets=" + ("null" if rs is None else f"boot={rs['boot']},delta={rs['delta']}"),
           "conn=" + ("null" if conn is None else f"ata{conn['ata']}/SATA {conn['sata']}/drawer={conn['drawer']}"),
           "temp=" + ("null" if t is None else f"before={t['before_c']},peak={t['peak_during_c']},after={t['after_c']},n={t['samples']}"),
           f"ready_s={s['ready_s']}", f"at_boot={s['at_boot']}"]
    bad = []
    if phy and phy["total"]:
        bad.append(f"אירועי phy +{phy['total']}")
    if rs and rs["delta"]:
        bad.append(f"איפוס קישור +{rs['delta']}")
    if bad:
        return LINK_TROUBLE, " · ".join(bad) + " -- כבל או מגע, לא פסק דין על הדיסק", tuple(ev)
    return OBSERVED, f"phy: {_show(phy and phy['total'])} · איפוסים: {_show(rs and rs['delta'])} · מוכן אחרי: {_show(s['ready_s'])}", tuple(ev)


def _mixed_image(conn, serial: str, image_id: str) -> list[str]:
    """אימג'ים אחרים שליומן הכתיבה של הדיסק נושא (S64/B31). ריק = אין ערבוב."""
    return [r[0] for r in conn.execute("SELECT DISTINCT image_id FROM chunk_ledger WHERE serial = ? AND part >= 0 AND image_id != ?",
                                       (serial, image_id))]


def _ledger_rows(conn, serial: str) -> int:
    return conn.execute("SELECT COUNT(*) FROM chunk_ledger WHERE serial = ? AND part >= 0", (serial,)).fetchone()[0]


def note_final(conn, mac: str, session_id: str, machine_raw: object, targets: list) -> None:
    """המעבר הטרמינלי של מכונה בגל: שורה למכונה, ושורה לכל יעד שנושא ``senses`` או סידורי -- בצל. אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        from server.disks import smart_history                       # noqa: PLC0415 -- לא נטען עד שצריך
        row = conn.execute("SELECT image_id FROM sessions WHERE id = ?", (session_id,)).fetchone()
        image_id = row["image_id"] if row else None
        verdict, reason, ev = machine_verdict(well_formed_machine(machine_raw))
        _log(conn, memory.record(conn, mode="shadow", rule=RULE, action=ACTION, point=f"{mac}/machine", round_id=session_id,
                                 entity=("machine", mac), verdict=verdict, reason=reason, evidence=ev, outcome=SHADOW_HE),
             verdict, reason, f"{mac}/machine", RULE, ACTION, session_id)
        for t in targets:
            if not isinstance(t, dict) or not isinstance(t.get("dev"), str):
                continue
            serial = smart_history.serial_of(conn, mac, t["dev"])
            name = f"{mac}/{t['dev']}/{serial or '?'}"
            entity = ("disk", serial) if serial else ("machine", mac)
            verdict, reason, ev = target_verdict(well_formed_target(t.get("senses")), image_id)
            _log(conn, memory.record(conn, mode="shadow", rule=RULE, action=ACTION, point=name, round_id=session_id, entity=entity,
                                     verdict=verdict, reason=reason, evidence=ev, outcome=SHADOW_HE), verdict, reason, name, RULE, ACTION, session_id)
            if serial and image_id:
                other = _mixed_image(conn, serial, image_id)
                v = MIXED if other else SAME if _ledger_rows(conn, serial) else NO_LEDGER
                why = {MIXED: f"ביומן הכתיבה של הדיסק יש גם אימג'ים אחרים: {', '.join(other)}",
                       SAME: "כל היומן של הדיסק הוא של האימג' של הסבב", NO_LEDGER: "אין יומן כתיבה לדיסק (מנוע אחר, או שלא דווח)"}[v]
                _log(conn, memory.record(conn, mode="shadow", rule=IDENT_RULE, action=IDENT_ACTION, point=name, round_id=session_id,
                                         entity=entity, verdict=v, reason=why, evidence=(f"image_id={image_id}", *(f"other={o}" for o in other)),
                                         outcome=SHADOW_HE), v, why, name, IDENT_RULE, IDENT_ACTION, session_id)
    except Exception:                                                 # noqa: BLE001 -- חוש שנכשל אינו מפיל דיווח
        log.exception("brain: the agent's senses of %s in %s were not recorded", mac, session_id)


def _log(conn, row_id, verdict, reason, target, rule, action, session_id) -> None:
    (log.warning if verdict in (LINK_TROUBLE, LINK_DROPPED, MIXED) else log.info)(
        "brain %s", json.dumps({"at": now_iso(), "mode": "shadow", "rule": rule, "action": action, "round": session_id,
                                "target": target, "verdict": verdict, "reason": reason, "enforced": False, "memory": row_id}, ensure_ascii=False))


# --- B23 / R16: בלי AHCI אין פס -----------------------------------------------------------------------------------------


def ahci_gate(mode: object, driver: object = None) -> tuple[str, str]:
    """(פסק, סיבה). ``None`` = לא נקרא: ``NOT_READ``, לא "תקין" ולא "חוסם"."""
    if mode not in SATA_MODES:
        return NOT_READ, "מצב בקר הדיסקים לא נקרא -- אי אפשר לומר אם המשכפל עומד בשער"
    d = f" (הדרייבר: {driver})" if _word(driver) else ""
    if mode == "ahci":
        return AHCI_OK, f"הבקר במצב AHCI{d}"
    return WOULD_BLOCK_BAND, f"הבקר במצב {MODE_HE[mode]}{d}, לא AHCI: בלי AHCI אין פס ייצור (צל: לא נחסם)"


def controller_warning(mode: object, driver: object = None) -> dict | None:
    """R16: האזהרה הכתומה לקונסולה. ``None`` כשהבקר ב-AHCI או שלא נקרא (אין מה להראות, ולא נמציא)."""
    verdict, why = ahci_gate(mode, driver)
    if verdict != WOULD_BLOCK_BAND:
        return None
    return {"severity": "warn", "mode": mode, "driver": driver if _word(driver) else None,
            "text": why.removesuffix(" (צל: לא נחסם)") + ". להעביר ל-AHCI ב-BIOS."}


def note_hello(conn, mac: str, body: dict) -> None:
    """hello של מכונה רשומה: ההחלטה נרשמת רק כשהמצב (או הדרייבר) השתנה מההחלטה האחרונה. אינו זורק."""
    try:
        if veto.mode(conn) == veto.OFF:
            return
        mode = body.get("sata_mode") if body.get("sata_mode") in SATA_MODES else None
        driver = _word(body.get("sata_driver"))
        verdict, reason = ahci_gate(mode, driver)
        if mode is None:                            # hello בלי השדה: סוכן ישן -- ההחלטה הקודמת נשארת, לא "לא נקרא" חדשה
            return
        key = f"mode={mode}", f"driver={driver}"
        last = memory.decisions(conn, kind="machine", key=mac, rule=GATE_RULE, limit=1)
        if last and last[0]["verdict"] == verdict and tuple(last[0]["evidence"] or ()) == key:
            return
        memory.record(conn, mode="shadow", rule=GATE_RULE, action=GATE_ACTION, point=mac, entity=("machine", mac),
                      verdict=verdict, reason=reason, evidence=key, outcome=SHADOW_HE)
        (log.warning if verdict == WOULD_BLOCK_BAND else log.info)(
            "brain %s", json.dumps({"at": now_iso(), "mode": "shadow", "rule": GATE_RULE, "action": GATE_ACTION, "target": mac,
                                    "verdict": verdict, "reason": reason, "enforced": False}, ensure_ascii=False))
    except Exception:                                                 # noqa: BLE001
        log.exception("brain: the AHCI gate of %s was not recorded", mac)
