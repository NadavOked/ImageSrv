"""#3064, כלל 9 (חלק מ-K3, שער הקבלה), #3277: אזהרת קישור איטי ו-ETA חזוי לפני שהגל יוצא.

**המקרה (מכללה, 11/11, #2841):** משכפל על קישור 100Mb/s. הגל יצא בלי אזהרה ובלי ETA, רץ 3:10 שעות (82.6GB,
~7.27MB/s) ונגמר ``written=0/3``. התקרה לפי הכרטיס האיטי (D6, `nic_speed`) כבר קיימת -- מה שחסר הוא שהמפעיל
יראה **לפני** "התחל" שכל הגל ירוץ בקצב של הכרטיס הזה, וכמה זמן זה ייקח.

**מה הכלל עושה:**

- **אזהרה כתומה** (אזהרה, לא חסימה -- כמו כל אזהרות "לפני ההתחלה", #432) על כל מגירה שהגל יכתוב במשכפל שכרטיס
  ההפצה שלו **נמדד** ב-`SLOW_MBPS` ומטה, עם ETA חזוי. מהירות שהונחה (``source: assumed``) אינה מדידה: היא לא
  מזהירה, וגם לא נחשבת "תקינה" (עיקרון 5).
- **החרגה מהשידור -- בצל בלבד** (הכרעה, #3277): ``EXCLUDE`` = "היה מוציא מהשידור", כשיש בגל משכפל שהקישור שלו
  נמדד מהיר יותר. שום דבר לא זז, גם ב-``enforce``.
- **רישום** ב-``brain_decisions`` (כלל ``K3``, פעולה ``LINK_SLOW``) כשהגל יוצא, עם ה-ETA החזוי. בסוף הגל ההחלטה
  מקבלת את ה-ETA בפועל (R3: תחזית מול מה שקרה).

**אין סף בהגדרות** (נדב 14/11: הספים שייכים למוח). המתג הוא ``brain_veto_mode``, כמו K2/K7: ``off`` = לא מחושב,
לא מוצג ולא נרשם.

**כל מה שכאן לעולם אינו זורק** אל הגל או אל המסך: כשל הוא שורת לוג, והגל יוצא כמו בלי המוח.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from server.brain import capability, forecast, memory, waveeta
from server.rounds import drive_rates, nic_speed

log = logging.getLogger("imagesrv.brain")

RULE, ACTION = "K3", "LINK_SLOW"
WARN, EXCLUDE = "WARN", "EXCLUDE"
OFF = "off"
#: הסף: קישור של 100Mb/s ומטה. **מכללה 11/11 (#2841): 100Mb/s.** מהירויות Ethernet הן 10/100/1000/2500...,
#: ולכן "100 ומטה" זהה ל"מתחת ל-1000" (ח7, מקור חיצוני) -- המקרה שנמדד חייב להזהיר, ולכן הסף כולל אותו.
SLOW_MBPS = 100
#: בייטים לשנייה לכל Mb/s של הקישור: **7.27MB/s ב-100Mb/s, מכללה 11/11 (#2841, v0.91.10), מדידה אחת.**
#: על ציר `total_compressed_bytes` (כמו `learn_eta.wave`). מדידה יחידה -- ולכן ה-ETA החזוי נרשם מול ה-ETA בפועל.
BYTES_PER_S_PER_MBPS = 72_700
#: #3041: משכפל שהיכולת הנלמדת שלו נמוכה משל האחרים בגל מקבל אזהרת "יאט את הסבב" רק אם הוא מוסיף לסבב לפחות כך (הכרעת
#: זאוס 15/11). אין אחוז: הזמן נגזר מ-`waveeta.predict`.
MIN_ADDED_S = 60
#: המקורות של `nic_speed.view` שהם מדידה. `assumed` (1Gb/s שהונח) אינו.
MEASURED = ("reported", "manual")


@dataclass(frozen=True)
class Finding:
    mac: str
    name: str
    mbps: int
    eta_s: int | None               # None = גודל האימג' לא ידוע: "לא ידוע", לא אפס
    verdict: str                    # WARN / EXCLUDE (צל)
    faster: tuple[str, ...] = ()    # שמות המשכפלים בגל שהקישור שלהם נמדד מהיר יותר
    learned: bool = False           # #3041: ה-mbps הוא היכולת הנלמדת של המשכפל, לא כרטיס הרשת
    adds_s: int | None = None       # #3041: כמה שניות המשכפל מוסיף לסבב מול האחרים (`waveeta.predict`); רק באזהרת "יאט"
    rate_bps: int | None = None     # #3041: הקצב שהמשכפל הזה מקבל, בביטים לשנייה


def eta_s(image_bytes: object, mbps: int) -> int | None:
    """ה-ETA החזוי של גל שרץ בקצב של קישור של ``mbps``. ``None`` כשגודל האימג' לא ידוע."""
    if isinstance(image_bytes, bool) or not isinstance(image_bytes, int) or image_bytes <= 0 or mbps <= 0:
        return None
    return round(image_bytes / (mbps * BYTES_PER_S_PER_MBPS))


def link_low(mbps: int, top: int | None) -> bool:
    """#3663 (G2): הקישור נמוך -- `SLOW_MBPS` ומטה (צוואר בקבוק בכל רשת), או מתחת ליכולת הכרטיס עצמו (``top``, ``None`` = לא ידוע:
    רק הרצפה). הסף היחיד של הבאנר: היה גם ב-`screens_standby.c` (הגואי) וב-`nic_live.is_low`."""
    return mbps <= SLOW_MBPS or (top is not None and mbps < top)


def duration_he(seconds: int | None) -> str:
    if seconds is None:
        return "לא ידוע"
    minutes = max(1, round(seconds / 60))
    return f"{minutes // 60}:{minutes % 60:02d} שעות" if minutes >= 60 else f"{minutes} דק'"


def judge(links: dict[str, dict], names: dict[str, str], image_bytes: object,
          learned: dict[str, int] | None = None) -> list[Finding]:
    """טהור. ``links`` = ``nic_speed.view`` לכל MAC בגל. ממצא לכל משכפל שהקישור שלו **נמדד** ב-`SLOW_MBPS` ומטה.

    #3041: ``learned`` = היכולת הנלמדת של משכפל בביטים לשנייה (`capability.learned`). היא באה **במקום** הכרטיס כשהיא
    נמוכה ממנו, כקישור שקול (`LINK_CEILING_PER_MBPS`), ומשכפל שנלמדה לו יכולת נחשב נמדד גם כשהכרטיס שלו הונח. בלי יכולת
    נלמדת -- הכרטיס, בדיוק כמו קודם."""
    effective: dict[str, tuple[int, bool]] = {}
    for mac, v in links.items():
        card = v["speed_mbps"] if v.get("source") in MEASURED and isinstance(v.get("speed_mbps"), int) else None
        got = (learned or {}).get(mac)
        equal = max(1, round(got / drive_rates.LINK_CEILING_PER_MBPS)) if isinstance(got, int) and got > 0 else None
        if equal is not None and (card is None or equal < card):
            effective[mac] = (equal, True)
        elif card is not None:
            effective[mac] = (card, False)
    measured = {mac: mbps for mac, (mbps, _) in effective.items()}
    out = []
    for mac in sorted(measured):
        mbps = measured[mac]
        if mbps > SLOW_MBPS:
            continue
        faster = tuple(sorted(names.get(m, m) for m, s in measured.items() if s > mbps))
        out.append(Finding(mac, names.get(mac, mac), mbps, eta_s(image_bytes, mbps),
                           EXCLUDE if faster else WARN, faster, effective[mac][1]))
    return out + _slows(links, names, image_bytes, learned or {}, {f.mac for f in out})


def _slows(links: dict[str, dict], names: dict[str, str], image_bytes: object, learned: dict[str, int],
           flagged: set[str]) -> list[Finding]:
    """#3041: "משכפל N יאט את הסבב ל-~X MB/s". משכפל שהיכולת הנלמדת שלו נמוכה מהקצב של כל האחרים בגל, ושלפי `waveeta` הוא
    מוסיף לסבב לפחות `MIN_ADDED_S`. הקצב של האחרים: הכרטיס שלהם (או היכולת הנלמדת, אם נמוכה ממנו) -- כמו שהגל עצמו קובע.
    גודל אימג' לא ידוע = אין חיזוי, ואין אזהרה."""
    if isinstance(image_bytes, bool) or not isinstance(image_bytes, int) or image_bytes <= 0:
        return []
    rate = {}
    for mac, v in links.items():
        if isinstance(v.get("speed_mbps"), int) and v["speed_mbps"] > 0:
            card = v["speed_mbps"] * drive_rates.LINK_CEILING_PER_MBPS
            got = learned.get(mac)
            rate[mac] = min(card, got) if isinstance(got, int) and got > 0 else card
    out = []
    for mac in sorted(rate):
        got = learned.get(mac)
        if mac in flagged or not isinstance(got, int) or got <= 0 or len(rate) < 2:
            continue
        others = [r for m, r in rate.items() if m != mac]
        if rate[mac] >= min(others):
            continue
        slower = waveeta.predict(0, image_bytes, rate[mac], []).eta_s
        added = slower - waveeta.predict(0, image_bytes, min(others), []).eta_s
        if added < MIN_ADDED_S:
            continue
        mbps = max(1, round(rate[mac] / drive_rates.LINK_CEILING_PER_MBPS))
        out.append(Finding(mac, names.get(mac, mac), mbps, eta_s(image_bytes, mbps), WARN,
                           tuple(sorted(names.get(m, m) for m, r in rate.items() if r > rate[mac])), True,
                           round(added), int(rate[mac])))
    return out


def text_he(f: Finding) -> str:
    if f.adds_s is not None and f.rate_bps is not None:
        return (f"היכולת שהמוח למד שהמשכפל מקבל בלי אובדן נמוכה משל האחרים בגל: הוא יאט את הסבב ל-~{f.rate_bps / 8e6:.0f}MB/s "
                f"ויוסיף לו כ-{duration_he(f.adds_s)}, ETA חזוי {duration_he(f.eta_s)} -- לבדוק כבל רשת, כרטיס ועומס מעבד")
    if f.learned:
        return (f"היכולת שהמוח למד שהמשכפל מקבל בלי אובדן שקולה לקישור של {f.mbps}Mb/s: הגל כולו ירוץ בקצב שלו, ETA חזוי "
                f"{duration_he(f.eta_s)} -- לבדוק כבל רשת, כרטיס ועומס מעבד")
    return (f"כרטיס הרשת של המשכפל מקושר ב-{f.mbps}Mb/s: הגל כולו ירוץ בקצב שלו, ETA חזוי {duration_he(f.eta_s)}"
            " -- לבדוק כבל רשת ופורט בסוויץ'")


def _mode(conn) -> str:
    from server.brain import veto                # כמו ב-`budget.production`: המתג מגיע מהווטו
    return veto.mode(conn)


def image_bytes(round_row: sqlite3.Row, lookup: Callable[[str], object] | None) -> int | None:
    """גודל האימג' (`total_compressed_bytes`): מחשב בנייה -- מהמניפסט החי שבשורת הסבב; ספרייה -- מ-``lookup``."""
    try:
        if round_row["source_kind"] == "build_disk":
            size = json.loads(round_row["live_manifest_json"] or "{}").get("total_compressed_bytes")
        else:
            size = lookup(round_row["image_id"]) if lookup is not None else None
    except Exception:                           # noqa: BLE001 -- גודל שלא נקרא הוא "לא ידוע"
        log.exception("brain: the image size of %s was not read", round_row["id"])
        return None
    return size if isinstance(size, int) and not isinstance(size, bool) and size > 0 else None


def _findings(conn, macs, names: dict[str, str], size: int | None) -> list[Finding]:
    macs = sorted(set(macs))
    return judge({mac: nic_speed.view(conn, mac) for mac in macs}, names, size,
                 {mac: got for mac in macs if (got := capability.learned(conn, mac)) is not None})


def attach(conn, machines: list[dict], size: int | None) -> None:
    """``nic_warn`` על כל מכונה ב-``machines`` שהקישור שלה איטי -- `room_precheck.warnings` מציג אותו."""
    try:
        if _mode(conn) == OFF:
            return
        names = {m["mac"]: m.get("display_name") or m.get("name") or m["mac"] for m in machines}
        found = {f.mac: f for f in _findings(conn, names, names, size)}
        for m in machines:
            f = found.get(m["mac"])
            if f is not None:
                m["nic_warn"] = {"mbps": f.mbps, "eta_s": f.eta_s, "text_he": text_he(f)}
    except Exception:                           # noqa: BLE001 -- המסך אינו נופל בגלל המוח
        log.exception("brain: the link warning was not computed")


def started(conn, wave_id: str, macs, names: dict[str, str], size: int | None) -> None:
    """הגל יצא: שורה לכל משכפל איטי ב-``brain_decisions``, פעם אחת לגל. ההחרגה -- נרשמת בלבד."""
    try:
        mode = _mode(conn)
        if mode == OFF:
            return
        done = {d["entity_key"] for d in memory.decisions(conn, round_id=wave_id, rule=RULE, limit=64)}
        for f in _findings(conn, macs, names, size):
            if f.mac in done:
                continue
            say = (f"צל: היה מוציא את {f.name} מהשידור (מהירים ממנו: {', '.join(f.faster)}) -- שום דבר לא זז"
                   if f.verdict == EXCLUDE else "אזהרה בלבד: הגל יוצא כמו שהוא")
            evidence = (f"{'learned' if f.learned else 'nic'}={f.mbps}Mb/s", f"eta_s={f.eta_s}", f"bytes={size}",
                        f"threshold={SLOW_MBPS}Mb/s", 
                        *(() if f.adds_s is None else (f"adds_s={f.adds_s}",)), *(f"faster={n}" for n in f.faster))
            memory.record(conn, mode=mode, rule=RULE, action=ACTION, verdict=f.verdict, round_id=wave_id,
                          reason=f"{f.name}: {text_he(f)}. {say}", evidence=evidence, entity=("network", f.mac),
                          predicted=f.eta_s, unit="s")      # #3125: the forecast as a number; None = size unknown
            log.info("brain %s", json.dumps({"mode": mode, "rule": RULE, "action": ACTION, "verdict": f.verdict,
                                              "round": wave_id, "mac": f.mac, "machine": f.name, "say": say,
                                              "evidence": list(evidence)}, ensure_ascii=False))
    except Exception:                           # noqa: BLE001 -- הגל יוצא גם כשהמוח נכשל
        log.exception("brain: the link decision of %s was not recorded", wave_id)


def _iso(text: str | None) -> datetime | None:
    try:
        at = datetime.fromisoformat(text) if text else None
    except ValueError:
        return None
    return at if at is not None and at.tzinfo is not None else None


def finished(conn, wave_id: str, started_at: str | None, now: datetime | None = None) -> None:
    """סוף הגל: לכל החלטה פתוחה של הגל -- ה-ETA בפועל מול החזוי (R3). זמן התחלה שלא נקרא = "לא נמדד"."""
    try:
        rows = [d for d in memory.decisions(conn, round_id=wave_id, rule=RULE, limit=64)
                if d["outcome_state"] == "open"]
        if not rows:
            return
        began = _iso(started_at)
        actual = None if began is None else round(((now or datetime.now(timezone.utc)) - began).total_seconds())
        # #3125: the time a wave ran before it FAILED is not the time it takes -- not measured, not "did not match"
        state = conn.execute("SELECT state FROM sessions WHERE id = ?", (wave_id,)).fetchone()
        failed = state is not None and state["state"] == "failed"
        actual = None if failed else actual
        for d in rows:
            # #3125: the number is the column; the evidence line is what rows from before the column carry
            predicted = d.get("predicted")
            if predicted is None:
                predicted = next((e.split("=", 1)[1] for e in d.get("evidence") or []
                                  if str(e).startswith("eta_s=")), None)
            guess = (int(predicted) if isinstance(predicted, (int, float)) and not isinstance(predicted, bool)
                     else int(predicted) if isinstance(predicted, str) and predicted.isdigit() else None)
            ratio = f" (בפועל/חזוי {actual / guess:.2f})" if actual is not None and guess else ""
            memory.resolve(conn, [d["id"]], f"ETA חזוי {duration_he(guess)}, בפועל "
                                            f"{duration_he(actual) if actual is not None else 'לא נמדד'}"
                                            f"{' (הגל נכשל)' if failed else ''}{ratio}",
                           actual=actual, matched=forecast.judge(RULE, guess, actual))
    except Exception:                           # noqa: BLE001
        log.exception("brain: the actual ETA of %s was not recorded", wave_id)
