"""#3662 (S41, B71): מה דורש עין ליד המכונה -- ההכרעה היא של המוח, לפי הדגימה האחרונה של ה-probe (#1049).

**הכרעת נדב 18/11 (#3653):** המוח מחליט, והשרת מבצע ומציג. עד #3662 הכרעה זו ישבה ב-`server/agent/probe.py` (`verdicts`), עם שני ספים
שהוגדרו שם. עכשיו הכללים והספים כאן, ו-`probe.verdicts` הוא מעטפת: אותו שם, אותו פלט, שהקונסולה (כרטיס המכונה) והשערים B54 ו-B57
(`gate_machines.nic`, `gate_round.held`) כבר קוראים. **ההתנהגות זהה בדיוק:** הגוף הועבר ולא נכתב מחדש; אין סף שהשתנה.

**הפסקים (`level`):** ``err`` = סוללה, NVMe עם critical_warning או שגיאות מדיה, כפילות IP, כבל פגום (open/short); ``warn`` = סטיית שעון
מעל `RTC_SKEW_SECONDS`, בלאי NVMe מעל `NVME_USED_WARN`, קריסה קודמת (pstore), שגיאות CRC. **שדה שלא נמדד** (``None`` או ``{"error"}``) אינו
מייצר פריט, ולכן רשימה ריקה אומרת "לא נמצאה בעיה במה שנמדד", לא "הכול תקין" (עיקרון 5): מי שצריך ראיה חיובית (B54, B57) בודק בנפרד שנמדד משהו.

**הרישום:** הפסקים נרשמים ב-``brain_decisions`` בנקודה שבה הם משפיעים על סבב -- שערי B54 ו-B57 בפתיחת סבב (``probe:<key>=<level>`` בראיות),
ולא בכל קריאה של כרטיס המכונה (שורה לכל רענון מסך היא רעש). **אין כאן חסימה:** ``err`` ו-``warn`` מוצגים, ו-`opengate` (B70) הוא שמכריע אם
הם מזהירים או חוסמים (היום: מזהירים). מודול עלה: לא מייבא דבר מהשרת.
"""

from __future__ import annotations

import math

#: סטיית שעון שמעליה "סוללת BIOS חשודה" (הכרעת נדב, #1049: > 5 דק').
RTC_SKEW_SECONDS = 300
#: בלאי NVMe שמעליו אזהרה (percentage_used הוא אחוז מהחיים המובטחים).
NVME_USED_WARN = 90


def _measured(value: object) -> dict | None:
    """אובייקט **שנמדד**: לא ``None`` (לא נבדק) ולא ``{"error"}`` (נכשל).
    שני אלה אינם שער — "לא נבדק" ≠ "תקין", אבל גם ≠ "רע"."""
    if isinstance(value, dict) and "error" not in value:
        return value
    return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _skew_text(seconds: int) -> str:
    s = abs(seconds)
    if s >= 86400:
        return f"{s // 86400} ימים"
    if s >= 3600:
        return f"{s // 3600} שעות"
    return f"{s // 60} דק'"


def verdicts(probe: dict | None) -> list[dict]:
    """מה שדורש עין ליד המכונה, לפי הדגימה האחרונה.

    כל פריט: ``{"key", "level": "warn"|"err", "text_he"}``. שדה שלא נמדד
    (``None``/``error``) אינו מייצר פריט — ולכן רשימה ריקה אומרת "לא
    נמצאה בעיה במה שנמדד", לא "הכול תקין".
    """
    out: list[dict] = []
    if not isinstance(probe, dict):
        return out

    power = _measured(probe.get("power"))
    if power is not None and power.get("on_battery") is True:
        out.append({"key": "battery", "level": "err",
                    "text_he": "המחשב על סוללה — שיכפול/קליטה עלולים להיקטע"})

    rtc = _measured(probe.get("rtc"))
    skew = _int(rtc.get("skew_seconds")) if rtc is not None else None
    if skew is not None and abs(skew) > RTC_SKEW_SECONDS:
        out.append({"key": "rtc", "level": "warn",
                    "text_he": f"שעון החומרה סוטה ב-{_skew_text(skew)} — סוללת BIOS חשודה"})

    for disk in probe.get("disks") or []:
        if not isinstance(disk, dict):
            continue
        smart = _measured(disk.get("nvme_smart"))
        if smart is None:
            continue
        name = str(disk.get("name") or "?")
        crit, media, used = (_int(smart.get("critical_warning")),
                             _int(smart.get("media_errors")), _int(smart.get("percentage_used")))
        if (crit is not None and crit != 0) or (media is not None and media > 0):
            out.append({"key": f"nvme:{name}", "level": "err",
                        "text_he": f"NVMe {name}: " + " · ".join(
                            p for p in (f"critical_warning={crit}" if crit else "",
                                        f"{media} שגיאות מדיה" if media else "") if p)})
        elif used is not None and used >= NVME_USED_WARN:
            out.append({"key": f"nvme:{name}", "level": "warn",
                        "text_he": f"NVMe {name}: {used}% מהבלאי המובטח נוצל"})

    pstore = _measured(probe.get("pstore"))
    if pstore is not None and pstore.get("crashed") is True:
        out.append({"key": "pstore", "level": "warn",
                    "text_he": "המחשב קרס לפני האתחול הזה"})

    for nic in probe.get("nic") or []:
        if not isinstance(nic, dict):
            continue
        name = str(nic.get("name") or "?")
        conflict = _measured(nic.get("ip_conflict"))
        if conflict is not None and conflict.get("duplicate") is True:
            out.append({"key": f"ip_conflict:{name}", "level": "err",
                        "text_he": f"כפילות IP על {name} — מחשב אחר ברשת עונה על הכתובת"})
        stats = _measured(nic.get("stats"))
        crc = _int(stats.get("rx_crc_errors")) if stats is not None else None
        if crc is not None and crc > 0:
            out.append({"key": f"crc:{name}", "level": "warn",
                        "text_he": f"{crc} שגיאות CRC על {name} — כבל או פורט חשודים"})

    # #1048: כבל פגום הוא שער. מתג שלא משדר (lldp.unheard) אינו תקלה.
    netprobe = _measured(probe.get("netprobe"))
    cable = _measured(netprobe.get("cable")) if netprobe is not None else None
    if cable is not None and cable.get("status") in ("open", "short"):
        pair, he, length = "?", ("פתוח" if cable["status"] == "open" else "קצר"), None
        for item in cable.get("pairs") or []:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").lower()
            if code in ("open", "short"):
                pair = str(item.get("pair") or "?")
                he = "פתוח" if code == "open" else "קצר"
                length = item.get("length_m")
                break
        text = f"כבל פגום: זוג {pair} {he}"
        if isinstance(length, int) and not isinstance(length, bool):
            text += f" ב-{length} מטר"
        elif isinstance(length, float) and math.isfinite(length):
            text += f" ב-{int(length)} מטר"
        out.append({"key": "cable", "level": "err", "text_he": text})
    return out
