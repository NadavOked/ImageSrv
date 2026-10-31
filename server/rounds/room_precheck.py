"""מה שמסך החדר אומר **לפני** שגל יוצא לדרך — ‏#432 ו-#65.

‏**#432 — הקישור וה-CRC של כל מגירה.** ב-05/09 כל מגירה שנכשלה אמרה זאת
לפני שהסבב התחיל (‏`SATA link up 1.5 Gbps`), והראיה ישבה ב-dmesg. הסוכן
מדווח עכשיו ב-hello את מהירות הקישור, את המקסימום של הפורט ואת מונה ה-CRC
(‏199); כאן הם הופכים למצב (`ok`/`slow`/`unknown`) ולאזהרה כתומה לפני
"התחל". **אזהרה ולא חסימה** — שיכפול תמיד עם אדם ליד המכונות, וכתום שואל
(כמו SMART, #872). ‏`unknown` אינו `ok` (עיקרון 5): קישור שלא נקרא מוצג
"לא ידוע", לעולם לא ירוק.

‏**#65 — המחשב החסר.** גל שמחכה ליותר מגירות ממה שהצטרפו אינו יוצא מקוצר
(אומת 29/08). המסך אומר **מי** חסר ולמה, ומציע: כבה אותו (להחליף בו
מגירות), התחל בכל זאת, בטל.
"""

from __future__ import annotations

import sqlite3

from server.disks import crcwarn, smart_history
from server.learn import learn_ports

#: ‏verdict של disk_event שבו מונה ה-CRC נקרא באמת (‏`smart.sh`). ‏`unchecked`
#: נקרא רק כשהסיבה היא `no_health` — ‏smartctl פתח את הדיסק וקרא מאפיינים.
#: ‏`failed_last` דורס את ה-reason בסיבת השרת, ולכן אינו בסיס.
_CRC_READ_VERDICTS = ("ok", "warn")
#: ‏#2083: ‏`fail` נקרא רק כשהאדום הוא ממצא שנקרא (‏`smart_was_read` של הסוכן,
#: ‏smart.sh). ‏`health_unread` (#2078) ו-`readonly` (#1742) שומרים crc=0 שלא
#: נקרא — בסיס כזה היה מייצר "CRC עלה" מזויף בסבב הבא (עיקרון 5).
_CRC_READ_FAIL_REASONS = ("health_failed", "media_readonly")


def _num(value) -> float | None:
    """מספר חיובי מה-hello, או None — bool/מחרוזת/0 אינם מהירות."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _count(value) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        return None
    return value


def link_state(gbps: float | None, max_gbps: float | None) -> str:
    """‏`slow` = נקרא מתחת למקסימום של הפורט; ‏`ok` = בו או מעליו;
    ‏`unknown` = אחד מהשניים לא נקרא — "לא הצלחנו לבדוק", לא "תקין"."""
    if gbps is None or max_gbps is None:
        return "unknown"
    return "slow" if gbps < max_gbps else "ok"


def crc_baseline(conn: sqlite3.Connection, serial: str | None) -> int | None:
    """מונה ה-CRC של הדיסק בשער ה-SMART של הסבב האחרון שלו (`disk_events`),
    או None כשאין סבב קודם שבו המונה נקרא באמת."""
    if not serial:
        return None
    row = conn.execute(
        "SELECT crc FROM disk_events WHERE serial = ?"
        " AND (verdict IN (?, ?) OR (verdict = 'fail' AND reason IN (?, ?))"
        " OR (verdict = 'unchecked' AND reason = 'no_health'))"
        " ORDER BY updated_at DESC LIMIT 1",
        (serial, *_CRC_READ_VERDICTS, *_CRC_READ_FAIL_REASONS),
    ).fetchone()
    return row["crc"] if row is not None else None


def drawer_fields(conn: sqlite3.Connection, disk: dict, mac: str | None = None) -> dict:
    """השדות של #432 למגירה אחת ב-`drawer_list`, מרשומת הדיסק של ה-hello.
    ‏`mac` — המכונה, ל-#1844 (מה שנלמד על החיבור); ‏None = לא נשאל על החיבור."""
    gbps, max_gbps = _num(disk.get("link_gbps")), _num(disk.get("link_max_gbps"))
    crc = _count(disk.get("crc"))
    base = crc_baseline(conn, disk.get("serial")) if crc is not None else None
    return {
        "link_gbps": gbps,
        "link_max_gbps": max_gbps,
        "link_state": link_state(gbps, max_gbps),
        "crc": crc,
        # ‏None = לא נמדד (אין מונה עכשיו, או אין סבב קודם שקרא אותו) — לא 0.
        # שלילי (דיסק אחר עם אותו סידורי, מונה שאופס) אינו "עלה".
        "crc_rise": max(0, crc - base) if base is not None else None,
        # ‏#1366: המונה המוחלט מעל `crc_warn_threshold` — כתום גם בגל ראשון,
        # שבו אין "עלייה". ‏None = המונה לא נקרא (לא "תקין").
        "crc_suspect": crcwarn.suspect(crc, crcwarn.threshold(conn)) if crc is not None else None,
        # ‏#1859: מה שהשרת למד מאז הסבב הקודם של הסידורי (‏None = כלום, או לא נקרא עכשיו), ו-
        # ‏`smart_compared` — יש בכלל סבב קודם להשוות אליו (‏False = "אין עדיין השוואה").
        **_learned(conn, disk),
        # ‏#1844: מה שהשרת למד על החיבור ועל הדיסק מהגלים הקודמים — "כנראה כבל/מחבר"
        # (‏`port_suspect`) או "הדיסק עצמו" (‏`disk_suspect`). המלצה, לא חסימה.
        **_ports(conn, disk, mac),
    }


def _ports(conn: sqlite3.Connection, disk: dict, mac: str | None) -> dict:
    got = learn_ports.verdict(conn, mac, disk.get("port"), disk.get("serial") or None)
    return {"port_suspect": got["port_suspect"], "port_disks": got["port_disks"],
            "disk_suspect": got["disk_suspect"], "disk_ports": got["disk_ports"],
            "learned_he": got["text_he"]}


def _learned(conn: sqlite3.Connection, disk: dict) -> dict:
    attrs = disk.get("smart_attrs")
    if not isinstance(attrs, dict) or not disk.get("serial"):
        return {"smart_learned": None, "smart_compared": None}
    prev = smart_history.previous(conn, disk["serial"])
    return {"smart_learned": smart_history.learned(attrs, prev), "smart_compared": prev is not None}


def _slot(m: dict, port: int | None, d: dict | None = None) -> str:
    names = m.get("slot_names") or {}
    name = (d or {}).get("slot_name") or (names.get(f"sata:{port}") if port else None)
    if name:
        return name
    if port is not None:
        return f"דיסק {port}"
    return (d or {}).get("serial") or (d or {}).get("dev") or "דיסק"


def _gbps(x: float) -> str:
    return f"{x:.1f}"


def warnings(machines: list[dict], selection: bool) -> list[dict]:
    """האזהרות הכתומות לפני "התחל": מגירה שהגל הזה יכתוב (טרייה, במכונה
    שהצטרפה, בחריץ שנבחר — אותן מגירות ש-`ready_drives` סופר) שהקישור שלה
    איטי מהפורט, או שמונה ה-CRC שלה עלה מאז הסבב הקודם שלה."""
    out = []
    for m in machines:
        if not m.get("joined"):
            continue
        for d in m.get("drawer_list") or []:
            if d.get("fresh") is not True or (selection and not d.get("selected")):
                continue
            slow = d.get("link_state") == "slow"
            rise = d.get("crc_rise") or 0
            suspect = d.get("crc_suspect") is True    # #1366
            learned = smart_history.LEARNED_HE.get(d.get("smart_learned") or "")   # #1859
            ported = d.get("learned_he")                                            # #1844
            speed = d.get("model_speed") if isinstance(d.get("model_speed"), dict) else {}
            model_slow = speed.get("slow") is True                                  # #1845
            if not slow and rise <= 0 and not suspect and not learned and not ported                     and not model_slow:
                continue
            parts = []
            if slow:
                parts.append(f"קישור {_gbps(d['link_gbps'])} מתוך {_gbps(d['link_max_gbps'])} Gb/s")
            if rise > 0:
                # עברית לפני "CRC": אחרת "6.0 Gb/s · CRC +12" הוא ריצת LTR אחת והסדר מתהפך
                parts.append(f"מונה CRC עלה ב-{rise} מאז הסבב הקודם")
            text = " · ".join(parts) + " — לבדוק כבל או מגירה" if parts else ""
            if suspect:
                text = " · ".join(x for x in (crcwarn.text_he(d["crc"]), text) if x)
            if learned:
                text = " · ".join(x for x in (learned, text) if x)
            if ported:
                text = " · ".join(x for x in (ported, text) if x)
            if model_slow:
                text = " · ".join(x for x in (speed.get("text_he"), text) if x)
            out.append({
                "mac": m["mac"], "machine": m.get("name"), "port": d.get("port"),
                "serial": d.get("serial"), "slot_name": d.get("slot_name"),
                "link_gbps": d.get("link_gbps"), "link_max_gbps": d.get("link_max_gbps"),
                "crc_rise": d.get("crc_rise"), "crc": d.get("crc"), "crc_suspect": suspect,
                "smart_learned": d.get("smart_learned"),
                "port_suspect": d.get("port_suspect"), "disk_suspect": d.get("disk_suspect"),
                "model_slow": model_slow,
                "text_he": f"{m.get('name') or m['mac']} · {_slot(m, d.get('port'), d)}: {text}",
            })
    return out


def missing(machines: list[dict], remaining: int, slots: list[dict] | None,
            ready: int) -> list[dict]:
    """‏#65: מי חסר בגל הפתוח, כשהמגירות המוכנות פחות ממה שהגל מחכה לו.

    עם בחירת יעדים (‏#695) — כל מכונה שנבחרה ואין לה דיסק טרי בחריץ שנבחר,
    או שלא הצטרפה. בלי בחירה — כל משכפל שלא הצטרף (אין דרך לדעת איזו מגירה
    "צריכה" להיות). מכונה שהצטרפה וכבתה (‏`awake` false) היא `off`: הדיסקים
    שדיווחה לפני שכבתה אינם ראיה שהיא תכתוב."""
    expected = remaining
    if slots is not None:
        expected = min(remaining, sum(len(item["ports"]) for item in slots))
    if expected <= 0 or ready >= expected:
        return []
    by_mac = {m["mac"]: m for m in machines}
    wanted = ([(item["mac"], item["ports"]) for item in slots] if slots is not None
              else [(m["mac"], []) for m in machines])
    out = []
    for mac, ports in wanted:
        m = by_mac.get(mac)
        if m is None:
            continue
        fresh = {d.get("port") for d in m.get("drawer_list") or [] if d.get("fresh") is True}
        short = sorted(set(ports) - fresh)
        if m.get("held") and not m.get("joined"):
            reason = "held"     # #2031: דלוק ומחכה ל"כבה" (#2024) — לא "כבוי"
        elif not m.get("awake"):
            reason = "off"
        elif not m.get("joined"):
            reason = "not_joined"
        elif short:
            reason = "drawers"
        else:
            continue
        name = m.get("name") or mac
        where = ", ".join(_slot(m, p) for p in (short if reason == "drawers" else ports))
        text = {"held": f"{name} דלוק ומחכה ללחיצה על 'כבה' — יש בו דיסק עם בעיה מהסבב הקודם",
                "off": f"{name} כבוי או לא מחובר — לא הצטרף לגל",
                "not_joined": f"{name} דולק אך לא הצטרף לגל",
                "drawers": f"{name}: אין דיסק חדש ב{where}"}[reason]
        if reason != "drawers" and where:
            text += f" ({where})"
        out.append({"mac": mac, "name": m.get("name"), "reason": reason,
                    "ports": short if reason == "drawers" else list(ports),
                    "awake": bool(m.get("awake")), "joined": bool(m.get("joined")),
                    # הכיבוי נמסר ב-hello (`power_action`): מכונה שאינה עונה לא תקבל אותו.
                    # ‏#2031: למוחזק — בתשובת הדיווח held שלו
                    "can_poweroff": bool(m.get("awake") or m.get("held")),
                    # ‏#1367: באמצע שיכפול מקומי — הכיבוי ישבור את היעד; הקונסולה מזהירה
                    "local_copy": bool(m.get("local_copy")),
                    "text_he": text})
    return out
