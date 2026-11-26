"""B59 (#3041 / #3064): מה הגביל את הקצב בצד המקלט (המשכפל) -- **ההחלטה בלבד, בצל.** אין פעולה ואין חסימה.

**למה:** נדב שואל "למה החדש איטי מהישן". הסוכן והשרת כבר אוספים הרבה על המקלט, אבל חלק רק נשמר כראיה וחלק לא נקרא
בשרת בכלל. כאן, במעבר הטרמינלי של מכונה בגל, נקרא הכול יחד ונרשמת שורה אחת: מה הגביל, ולפי אילו אותות.

**מה נקרא (כולו כבר ב-DB, ואיש לא חישב עליו פסק):**

- ``session_members.udp_drops_json``: ``rcvbuf``, ``inerrors``, ``abandoned`` (S44, ש-``budget`` קורא דרך ``netsense``;
  כאן נקראים **לפסק על המכונה**, לא מחושבים מחדש), ו-``rcvbuf_asked``, ``rcvbuf_bytes`` (S42), ``pass_word`` (S43).
- ``wave_senses`` דרך ``wavesense.numbers`` (S46-S50): ``cpu_busy_min_core``, ``rx_missed``, ``rx_no_buffer``,
  ``disk_util_max`` (``softirq_max`` ו-``net_rx_ps_max`` רק כראיה).
- ``senses`` של המכונה בדיווח הסופי (S41 ``rx``, S59 ``http``, S60 ``psi``; S51 ``carrier`` כבר נשפט ב-``agentsenses``).
- ``session_members.cpu_json`` (S21/S28): ``throttle_delta`` (אות), ושאר השדות כראיה.

**הפסקים** (``diagnose``, טהורה). כמה אותות יכולים לפעול יחד: הפסק הוא הראשון **לפי סדר העדיפות הקבוע** -- מהקרוב
לחוט אל הרחוק ממנו (מה שנמצא קרוב לחוט הוא גם מה שהאחרים מסבירים) -- וכל האותות שפעלו כתובים בסיבה:

1. ``NIC_DROPS`` -- ``rx_missed`` או ``rx_no_buffer`` מעל 0: הכרטיס זרק לפני שהחבילה הגיעה לקרנל.
2. ``RX_DROPS`` -- הקרנל או המקלט איבדו: ``rcvbuf`` / ``abandoned`` (או ``evicted`` של S41 כשאין ``abandoned``) מעל 0,
   ``inerrors`` מעל ``rcvbuf`` (שגיאות UDP אחרות), או ``rcvbuf_bytes`` קטן ממה שביקשנו (הסיבה אומרת בכמה).
   ``foreign`` (חבילות של סבב אחר באותו ערוץ) **אינו אובדן** -- ``chunkcast`` מדלג עליהן; הוא ראיה בלבד.
3. ``CPU_BOUND`` -- **כל** הליבות מעל 85% בחלון האחרון (הכרעת נדב 15/11 ב-#3041), או ``throttle_delta`` מעל 0.
4. ``DISK_BOUND`` -- ``util`` מרבי של מגירה מעל 90%, או ``busy`` של S41 מעל 0 (``chunkcast``: "הדיסקים מאחורי הזרם").
5. ``STALLED_PASS`` -- ``pass_word`` = ``stalled``.
6. ``NO_LIMIT_SEEN`` -- נמדדו **ארבעת** החושים (חוצץ הקבלה, כרטיס, מעבד, דיסק) ואין אות.
7. ``NO_LIMIT_PARTIAL`` -- בחושים שנמדדו אין אות, וחוש אחד לפחות לא נמדד: **לא** "אין הגבלה" (עיקרון 5). ההכרעה הזאת
   נוספה כי בלעדיה המקרה החלקי נאלץ להיות ``NO_LIMIT_SEEN`` (שקר) או ``NOT_MEASURED`` (שקר בכיוון ההפוך).
8. ``NOT_MEASURED`` -- אף חוש לא נמדד.

**עיקרון 5:** ``null`` או חסר = "לא נמדד", לעולם לא 0 ולעולם לא "תקין". הראיות אומרות אילו חושים לא נמדדו. ב-``wave_senses``
כבר ``numbers`` משמיט מפתח שלא נקרא, ושם חסר = לא נמדד.

**``rcvbuf_asked`` חסר** (גל חתיכות מדווח רק ``rcvbuf_bytes``): נלקחת הבקשה של הקוד, 16MB (``CC_RCVBUF`` ב-``chunkcast.c``
ו-``RECV_RCVBUF`` ב-``rcvbuf.sh``), והראיה אומרת ``default``. אם הסוכן שונה מהקוד -- הפסק לא נכון, וזה כתוב.

**ספי ההתחלה הם הערכה, לא הגדרות, ולא נמדדו על ברזל** (נדב 14/11): 85% הוא הכרעה של נדב ב-#3041, ו-90% לדיסק הוא הערכה.
``disk_util_max`` הוא **שיא** על פני חלונות הגל, לא ממוצע -- חלון אחד עמוס מספיק. ``cpu_busy_min_core`` הוא החלון
**האחרון**, ולכן מעבד שהיה עמוס באמצע הגל ורגוע בסופו לא יופיע.

**הרישום:** ``brain_decisions`` (כלל ``B59``, פעולה ``RX_LIMIT``), שורה אחת למכונה בגל, במעבר הטרמינלי, ``mode=shadow``.
המתג ``brain_veto_mode=off`` = לא נקרא ולא נרשם. **לעולם אינו זורק** אל הדיווח, וקריאה שנכשלה היא "לא נמדד".

**לא נבנה: לכל מעבר.** מוני המקלט (S41) מגיעים מצטברים לגל (``rxsense.sh``), לא לכל מעבר; פסק מעבר-מעבר דורש שינוי בסוכן.
"""

from __future__ import annotations

import json
import logging
import re

from server.brain import agentsenses, memory, veto, wavesense

log = logging.getLogger("imagesrv.brain")

#: אותה צורה ש-`reports._UDP_DROPS_EXTRA` מקבל: מילה של `chunkcast`, לא טקסט חופשי
_WORD = re.compile(r"[a-z][a-z0-9-]{0,31}")

RULE, ACTION = "B59", "RX_LIMIT"
NIC_DROPS, RX_DROPS, CPU_BOUND, DISK_BOUND, STALLED_PASS = "NIC_DROPS", "RX_DROPS", "CPU_BOUND", "DISK_BOUND", "STALLED_PASS"
NO_LIMIT_SEEN, NO_LIMIT_PARTIAL, NOT_MEASURED = "NO_LIMIT_SEEN", "NO_LIMIT_PARTIAL", "NOT_MEASURED"
#: סדר העדיפות של הפסק: מהקרוב לחוט אל הרחוק
ORDER = (NIC_DROPS, RX_DROPS, CPU_BOUND, DISK_BOUND, STALLED_PASS)
#: הכרעת נדב 15/11 (#3041): כל הליבות מעל. התחלה, לא הגדרה
CPU_BUSY_ALL = 85.0
#: הערכה, לא נמדד על ברזל: שיא ה-util של מגירה בגל
DISK_UTIL = 90.0
#: ארבעת החושים ש-NO_LIMIT_SEEN דורש, בשמם בעברית
GROUPS_HE = {"socket": "חוצץ הקבלה", "nic": "הכרטיס", "cpu": "המעבד", "disk": "הדיסק"}
#: מה ש-`chunkcast` ו-`rcvbuf.sh` מבקשים כשהדיווח לא אומר
DEFAULT_ASK = 16 << 20


def _int(v) -> int | None:
    return v if type(v) is int and v >= 0 else None


def _num(v) -> float | None:
    return float(v) if type(v) in (int, float) and v >= 0 else None


#: #3815: the pause setting of `ethtool -a` as `wavesense.numbers` keeps it (on = 1, off = 0); not said = absent = "unmeasured"
_PAUSE_SETTING = {1.0: "on", 0.0: "off"}


def _pause(value: float | None) -> str:
    return "unmeasured" if value is None else _PAUSE_SETTING.get(value, "unmeasured")


def _say(v) -> str:
    return "unmeasured" if v is None else f"{v:g}" if isinstance(v, float) else str(v)


def _whole(v) -> str:
    """#3815: a byte or frame count in full (`7000000`, not `7e+06`); not read = "unmeasured"."""
    n = _num(v)
    return "unmeasured" if n is None else str(int(n))


def _size(n: int) -> str:
    return f"{n / (1 << 20):g}MB" if n >= 1 << 20 else f"{n / 1024:.0f}KB" if n >= 1024 else f"{n} בייטים"


def diagnose(drops: dict | None, wave: dict | None, senses: dict | None, cpu: dict | None) -> tuple[str, str, tuple[str, ...]]:
    """(פסק, סיבה בעברית, ראיות). ``drops`` = ``udp_drops`` של המכונה; ``wave`` = הפלט של ``wavesense.numbers`` (מפתח חסר =
    לא נמדד); ``senses`` = ``agentsenses.well_formed_machine``; ``cpu`` = ``cloner_cpu.well_formed``. כל אחד יכול להיות
    ``None`` או פגום: זה "לא נמדד", לא חריגה."""
    d = drops if isinstance(drops, dict) else {}
    w = wave if isinstance(wave, dict) else {}
    c = cpu if isinstance(cpu, dict) else {}
    s = senses if isinstance(senses, dict) else {}
    rx = s.get("rx") if isinstance(s.get("rx"), dict) else None
    http = s.get("http") if isinstance(s.get("http"), dict) else None
    psi = s.get("psi") if isinstance(s.get("psi"), dict) and isinstance(s["psi"].get("peak"), dict) else None

    rcvbuf, inerrors, abandoned = _int(d.get("rcvbuf")), _int(d.get("inerrors")), _int(d.get("abandoned"))
    asked, got = _int(d.get("rcvbuf_asked")), _int(d.get("rcvbuf_bytes"))
    word = d["pass_word"] if isinstance(d.get("pass_word"), str) and _WORD.fullmatch(d["pass_word"]) else None
    asked_how = "reported"
    if asked is None and got is not None:
        asked, asked_how = DEFAULT_ASK, "default"
    evicted = _int(rx.get("evicted")) if rx else None
    busy, foreign = (_int(rx.get(k)) if rx else None for k in ("busy", "foreign"))
    missed, no_buf = _num(w.get("rx_missed")), _num(w.get("rx_no_buffer"))
    min_core, util = _num(w.get("cpu_busy_min_core")), _num(w.get("disk_util_max"))
    throttle = _int(c.get("throttle_delta"))

    why: dict[str, list[str]] = {}

    if missed or no_buf:
        why.setdefault(NIC_DROPS, []).append(f"הכרטיס זרק: rx_missed={_say(missed)}, rx_no_buffer={_say(no_buf)} (לפני שהגיעו לקרנל)")
    if rcvbuf:
        why.setdefault(RX_DROPS, []).append(f"הקרנל זרק {rcvbuf} בלוקים בחוצץ הקבלה (RcvbufErrors)")
    if abandoned:
        why.setdefault(RX_DROPS, []).append(f"המקלט נטש {abandoned} חתיכות באמצע")
    elif abandoned is None and evicted:
        why.setdefault(RX_DROPS, []).append(f"המקלט פינה {evicted} חתיכות שלא הושלמו (evicted)")
    if inerrors and inerrors > (rcvbuf or 0):
        why.setdefault(RX_DROPS, []).append(f"שגיאות UDP נוספות: InErrors={inerrors} מול rcvbuf={_say(rcvbuf)}")
    if got is not None and asked is not None and got < asked:
        times = f"פי {asked / got:.1f} פחות" if got else "כלום"
        note = "" if asked_how == "reported" else " (הבקשה לא דווחה: נלקחה ברירת המחדל של הקוד)"
        why.setdefault(RX_DROPS, []).append(f"הקרנל קיצץ את חוצץ הקבלה: ביקשנו {_size(asked)}, ניתנו {_size(got)} ({times}){note}")
    if min_core is not None and min_core > CPU_BUSY_ALL:
        why.setdefault(CPU_BOUND, []).append(f"כל הליבות מעל {CPU_BUSY_ALL:g}% בחלון האחרון (הפחות עמוסה: {min_core:g}%)")
    if throttle:
        why.setdefault(CPU_BOUND, []).append(f"המעבד הואט תרמית (throttle +{throttle})")
    if util is not None and util > DISK_UTIL:
        why.setdefault(DISK_BOUND, []).append(f"הדיסק עסוק: util מרבי {util:g}% (מעל {DISK_UTIL:g}%)")
    if busy:
        why.setdefault(DISK_BOUND, []).append(f"הדיסקים היו מאחורי הזרם: {busy} חתיכות נדלגו (busy)")
    if word == "stalled":
        why.setdefault(STALLED_PASS, []).append("המעבר האחרון נגמר ב-stalled: נתקע ולא הסתיים")

    groups = {"socket": rcvbuf, "nic": missed, "cpu": min_core if min_core is not None else throttle, "disk": util}
    unmeasured = [name for name, v in groups.items() if v is None]
    unmeasured_he = ", ".join(GROUPS_HE[n] for n in unmeasured)
    any_measured = any(v is not None for v in (rcvbuf, inerrors, abandoned, got, word, evicted, busy, missed, no_buf,
                                                min_core, util, throttle))

    ev = [f"rcvbuf={_say(rcvbuf)}", f"inerrors={_say(inerrors)}", f"abandoned={_say(abandoned)}",
          f"rcvbuf_asked={_say(asked)}({asked_how})" if asked is not None else "rcvbuf_asked=unmeasured",
          f"rcvbuf_bytes={_say(got)}", f"pass_word={_say(word)}",
          *((f"rx.{k}={_say(_int(rx.get(k)))}" for k in ("foreign", "evicted", "busy", "bad_pkt", "bad_chunk")) if rx else ("rx=unmeasured",)),
          f"rx_missed={_say(missed)}", f"rx_no_buffer={_say(no_buf)}",
          # #3815: evidence only, no threshold -- a switch that sends the card pause frames looks like loss
          f"rx_bytes={_whole(w.get('rx_bytes'))}", f"ring_rx={_whole(w.get('ring_rx'))}",
          f"ring_rx_max={_whole(w.get('ring_rx_max'))}",
          *(f"{k}={_whole(w.get(k))}" for k in ("tx_xon", "tx_xoff", "rx_xon", "rx_xoff")),
          *(f"{k}={_pause(_num(w.get(k)))}" for k in ("pause_rx", "pause_tx")),
          f"cpu_busy_min_core={_say(min_core)}",
          f"softirq_max={_say(_num(w.get('softirq_max')))}", f"net_rx_ps_max={_say(_num(w.get('net_rx_ps_max')))}",
          f"disk_util_max={_say(util)}", f"throttle_delta={_say(throttle)}", f"cpu_busy_pct={_say(_int(c.get('busy_pct')))}",
          f"cpu_iowait_pct={_say(_int(c.get('iowait_pct')))}", f"temp_max_c={_say(_int(c.get('temp_max_c')))}",
          "http=" + ("unmeasured" if http is None else f"fetches={_say(http.get('fetches'))},curl_failed={_say(http.get('curl_failed'))}"),
          "psi=" + ("unmeasured" if psi is None else ",".join(f"{k}={_say(v)}" for k, v in psi["peak"].items())),
          f"cpu_limit={CPU_BUSY_ALL:g}", f"disk_limit={DISK_UTIL:g}", "unmeasured=" + (",".join(unmeasured) or "none")]

    tail = []
    if http and (_int(http.get("curl_failed")) or 0) > 0:
        tail.append(f"הורדות HTTP נכשלו: {http['curl_failed']} מתוך {_say(http.get('fetches'))}")
    if foreign:
        tail.append(f"{foreign} חבילות זרות (של סבב אחר, לא נספרו כאובדן)")

    fired = [v for v in ORDER if v in why]
    if fired:
        parts = [f"{v}: " + "; ".join(why[v]) for v in fired]
        verdict, reason = fired[0], " · ".join(parts)
    elif not any_measured:
        verdict, reason = NOT_MEASURED, "אף חוש בצד המקלט לא נמדד (סוכן ישן, או דיווח בלי חושים): לא הוכרע"
    elif unmeasured:
        verdict = NO_LIMIT_PARTIAL
        reason = f"בחושים שנמדדו אין אות, אבל לא נמדדו: {unmeasured_he} -- לא אומר שאין הגבלה"
    else:
        verdict, reason = NO_LIMIT_SEEN, "נמדדו חוצץ הקבלה, הכרטיס, המעבד והדיסק, ובאף אחד אין אות הגבלה"
    if tail:
        reason += " · " + " · ".join(tail)
    if fired and unmeasured:
        reason += f" · לא נמדדו: {unmeasured_he}"
    return verdict, reason, tuple(ev)


def _read(conn, sql: str, args: tuple):
    """עמודה אחת של שורה אחת, או ``None`` -- שורה חסרה או קריאה שנכשלה (נרשם בלוג): "לא נמדד", לא חריגה."""
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else None
    except Exception:                           # noqa: BLE001
        log.exception("brain: B59 could not read %s", sql.split(" FROM ")[-1].split()[0])
        return None


def _json(raw):
    try:
        return json.loads(raw) if isinstance(raw, str) else None
    except ValueError:
        return None


def note_final(conn, mac: str, session_id: str, machine_raw: object) -> int | None:
    """המעבר הטרמינלי של מכונה בגל: שורה אחת, בצל. קורא מה-DB את ``udp_drops_json``, ``cpu_json`` ו-``wave_senses``;
    ``machine_raw`` הוא ``senses`` של הדיווח. מחזיר את מספר השורה (``None``: כבוי, או לא נרשם). **אינו זורק.**"""
    try:
        if veto.mode(conn) == veto.OFF:
            return None
        member = "SELECT {} FROM session_members WHERE session_id = ? AND mac = ?"
        key = (session_id, mac)
        drops = _json(_read(conn, member.format("udp_drops_json"), key))
        cpu = _json(_read(conn, member.format("cpu_json"), key))
        wave = wavesense.numbers(_read(conn, "SELECT senses_json FROM wave_senses WHERE session_id = ? AND mac = ?", key))
        try:
            senses = agentsenses.well_formed_machine(machine_raw)
        except Exception:                       # noqa: BLE001 -- דיווח פגום הוא "לא נמדד"
            log.exception("brain: B59 could not parse the senses of %s", mac)
            senses = None
        verdict, reason, evidence = diagnose(drops, wave, senses, cpu)
        row = memory.record(conn, mode="shadow", rule=RULE, action=ACTION, point=f"{mac}/rx", round_id=session_id,
                            entity=("machine", mac), verdict=verdict, reason=reason, evidence=evidence,
                            outcome=agentsenses.SHADOW_HE)
        (log.warning if verdict in ORDER else log.info)(
            "brain %s", json.dumps({"mode": "shadow", "rule": RULE, "action": ACTION, "round": session_id, "target": mac,
                                    "verdict": verdict, "reason": reason, "enforced": False, "memory": row}, ensure_ascii=False))
        return row
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל דיווח
        log.exception("brain: the receive-side limit of %s in %s was not recorded", mac, session_id)
        return None
