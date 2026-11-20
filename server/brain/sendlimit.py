"""#3471 (B58): מה הגביל את הקצב בצד השרת והשולח -- שורה אחת לגל, **בצל בלבד**. ועוד שורה אחת לגל מ-LR16: האבדן ברשת
ההפצה מול הבסיס שלה.

**למה:** נדב שואל "למה החדש איטי מהישן". השרת מודד בזמן גל את השולח (V4/V5), את מה שנעשה עמיד אצל כל משכפל (V7), את
מעבד השרת וה-chunkcast, ה-PSI, `tx_dropped` של הכרטיס ודיסק הספרייה (V8-V10) -- ואף כלל לא קרא את זה. LR16 (`netloss`)
הגיע ליומן ולמסך הבריאות, לא למוח.

**החיבור:** `server/learn/server_senses.py`, בסוף `Senses.end` (אחרי שהסיכום נכתב ל-`server_io`) קורא ל-`note_wave`;
`server/learn/netloss.py`, ב-`_evaluate` (אחרי `assess`) קורא ל-`note_netloss`. שתיהן עטופות: **לעולם אינן זורקות** אל סוף הגל.

**הסיכום ש-`diagnose` מקבל** (`Senses.end`): `peaks` (השיא של כל שדה בכל חלון), `nic_tx_dropped` (כל הגל), `passes`
(V4/V5: לכל `p<חלק>/r<מעבר>` חתיכות, מתוך כמה, בתים, שניות, `done`), `windows`, ו-`ledger` (V7, לכל משכפל: `chunks`,
`seconds`, `mbps` -- שמות השדות כמו ב-V7, והערך הוא MB/s -- ו-`idle`). ⚠️ `ledger` נוסף לסיכום ב-#3471: עד אז `WINDOW_ONLY`
הוציא אותו, והוא היה רק בשורות החלון. הוא נשמר עכשיו גם ב-`result_json.server`. הקצב הוא **בזמן שהמשכפל כתב**: מהחלון
הראשון עם חתיכות עמידות ועד האחרון (חלון אפס בתוכו נספר, ההתחלה והזנב לא), כדי שמשכפל שסיים ראשון לא ייקרא "איטי".

**הפסקים**, לפי סדר העדיפות (הראשון שמתקיים; **כל** האותות שפעלו כתובים בסיבה, גם כשהם לא הפסק):

1. ``SERVER_PRESSURE`` -- אחד מ: שיא מעבד השרת או ה-chunkcast (בליבות), שיא PSI של מעבד או זיכרון, `nic_tx_dropped` > 0,
   שיא `disk_util` של דיסק הספרייה או `disk_psi_full`. שרת לחוץ מאט את כולם, ולכן הוא קודם ל"משכפל איטי".
2. ``SLOW_CLONER`` -- משכפל שה-MB/s שלו נמוך מ-``SLOW_RATIO`` (חצי) מחציון **שאר** משכפלי הגל. נדרשים לפחות
   ``MIN_CLONERS`` (3) משכפלים **נמדדים**; משכפל שה-ledger שלו `null` ("לא נמדד") או שלא כתב חתיכה ("idle": אולי כבר
   הושלם) אינו נכנס לחציון, אינו איטי ואינו מהיר, ונכתב ברשימה.
3. ``NO_LIMIT_SEEN`` -- **רק** כשכל חמש הקבוצות נמדדו (מעבד, PSI, הכרטיס, דיסק הספרייה, משכפלים -- לפחות אחד מהשדות
   של כל קבוצה אינו `null`, ולפחות ``MIN_CLONERS`` משכפלים נמדדו) ושום אות לא עבר סף. גם אז זה "לא נראה גורם מגביל בין מה
   שנמדד": מי האט, `--kbps` או `--write-kbps`, **לא נמדד** (הלוג של `chunkcast send` אינו אומר; זה מונה ב-`agent/chunkcast.c`).
4. ``NOT_MEASURED`` -- אין סיכום, או שקבוצה חסרה ואין אות בשאר. הסיבה אומרת מה לא נמדד.

**עיקרון 5:** `null` / שדה חסר / ערך פגום = "לא נמדד", לעולם לא 0 ולעולם לא "תקין".

**הספים הם נקודות התחלה (הערכה), לא הגדרות, ולא נמדדו על ברזל** (נדב 14/11: ספים בהגדרות רק אחרי מדידה). שמות השדות
וסדרי הגודל מ-`PEAKS` ו-`window` ב-`server_senses.py`: ליבות = 1.0 ליבה אחת כל החלון; PSI ו-`disk_util` הם שבר 0..1 של
החלון. `chunkcast_cpu` הוא כל השולחים יחד, ולכן סף של ליבה אחת עשוי להחמיץ או להגזים כשיש כמה חלקים במקביל.
`disk_psi_full` > 0 בכל קריאה היה רעש: הסף הוא ``DISK_PSI_FULL``.

**LR16 (`note_netloss`):** שורה אחת לגל, ``B58``/``NET_LOSS``: `JUMP` / `OK` / `UNCHECKED` ("אין בסיס עדיין", **לא** "תקין")
/ `SERVER_DISK` / `NET_GAP`, לפי `state` של `netloss.assess`.

**הרישום:** ``brain_decisions``, ישות ``("round", <הגל>)``, `mode="shadow"`, התוצאה "צל: רק נרשם, שום פעולה לא בוצעה".
המתג ``brain_veto_mode=off`` = לא מחושב ולא נרשם. שורה אחת לכל פעולה בגל: גל שנסגר פעמיים אינו כופל.
"""

from __future__ import annotations

import logging
import math
import statistics

from server.brain import memory

log = logging.getLogger("imagesrv.brain")

RULE = "B58"
ACTION, NET_ACTION = "SEND_LIMIT", "NET_LOSS"
OFF = "off"
SERVER_PRESSURE, SLOW_CLONER, NO_LIMIT_SEEN, NOT_MEASURED = "SERVER_PRESSURE", "SLOW_CLONER", "NO_LIMIT_SEEN", "NOT_MEASURED"
OUTCOME = "צל: רק נרשם, שום פעולה לא בוצעה"

#: הערכה, לא מדידה (ראה את ראש המודול): ליבות של מעבד השרת / ה-chunkcast שמעליהן השרת "לחוץ"
CPU_CORES = 0.9
#: הערכה: שבר החלון שבו משימה כלשהי חיכתה למעבד או לזיכרון (PSI some / full)
PSI_SHARE = 0.10
#: הערכה: שבר החלון שבו דיסק הספרייה עסוק, ושבר החלון שבו כל המשימות חיכו לו
DISK_UTIL = 0.90
DISK_PSI_FULL = 0.05
#: הערכה: משכפל שמתחת לחלק הזה מחציון שאר המשכפלים הוא איטי
SLOW_RATIO = 0.5
#: פחות מזה אין חציון של "שאר המשכפלים" שמלמד משהו
MIN_CLONERS = 3

#: (קבוצה, שדה ב-`peaks`, הסף, שם בעברית, איך מציגים ערך). `nic_tx_dropped` בסיכום עצמו ולא ב-`peaks`.
_CHECKS = (
    ("cpu", "server_cpu", CPU_CORES, "מעבד השרת", "cores"),
    ("cpu", "chunkcast_cpu", CPU_CORES, "מעבד ה-chunkcast (כל השולחים יחד)", "cores"),
    ("psi", "psi_cpu_some", PSI_SHARE, "לחץ מעבד (PSI)", "share"),
    ("psi", "psi_mem_some", PSI_SHARE, "לחץ זיכרון (PSI some)", "share"),
    ("psi", "psi_mem_full", PSI_SHARE, "לחץ זיכרון (PSI full)", "share"),
    ("disk", "disk_util", DISK_UTIL, "דיסק הספרייה עסוק", "share"),
    ("disk", "disk_psi_full", DISK_PSI_FULL, "דיסק הספרייה: כל המשימות ממתינות לו (PSI full)", "share"),
)
_GROUPS_HE = {"cpu": "מעבד השרת וה-chunkcast", "psi": "לחץ PSI", "nic": "כרטיס ההפצה (nic_tx_dropped)",
              "disk": "דיסק הספרייה", "cloners": "קצב המשכפלים (V7)"}
_ORDER = ("cpu", "psi", "nic", "disk", "cloners")


def _num(value) -> float | None:
    """מספר שנקרא, או ``None`` ("לא נמדד"). דגל, טקסט, שלילי, NaN -- פגום, ולכן לא נמדד."""
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0
    return float(value) if ok else None


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _say(value) -> str:
    return "unmeasured" if value is None else f"{value:.12g}" if isinstance(value, float) else str(value)


def _show(value: float, how: str) -> str:
    return f"{value:g} ליבות" if how == "cores" else f"{100 * value:.0f}% מהחלון"


def _avg_mbps(p: dict) -> float | None:
    """Mbps הממוצע של מעבר אחד בזרם הדחוס (V4): מ-`done` כשהוא נראה (זמן השולח עצמו), אחרת מהבתים ומהזמן מאז שהתחיל."""
    done = _dict(p.get("done"))
    nbytes, secs = (_num(done.get("bytes")), _num(done.get("ms"))) if done else (None, None)
    secs = None if secs is None else secs / 1000
    if nbytes is None or not secs:
        nbytes, secs = _num(p.get("bytes")), _num(p.get("elapsed_s"))
    return round(nbytes * 8 / secs / 1e6, 1) if nbytes is not None and secs else None


def _cloners(summary: dict) -> tuple[dict[str, float], list[str], list[str], bool]:
    """V7 של הגל -> (mac: MB/s של מי שנמדד, מי שלא כתב חתיכה, מי שלא נמדד, האם ה-ledger נקרא בכלל)."""
    ledger = summary.get("ledger")
    if not isinstance(ledger, dict):
        return {}, [], [], False
    rated, idle, unmeasured = {}, [], []
    for mac, entry in sorted(ledger.items(), key=lambda kv: str(kv[0])):
        entry = _dict(entry)
        rate = _num(entry.get("mbps"))
        if rate is not None:
            rated[str(mac)] = rate
        elif entry.get("idle") is True:
            idle.append(str(mac))
        else:
            unmeasured.append(str(mac))
    return rated, idle, unmeasured, True


def _slow(rated: dict[str, float]) -> list[tuple[str, float, float]]:
    """משכפל איטי = נמוך מ-`SLOW_RATIO` מחציון **השאר**. פחות מ-`MIN_CLONERS` נמדדים: אין פסק. חציון אפס: אין יחס."""
    if len(rated) < MIN_CLONERS:
        return []
    out = []
    for mac, value in rated.items():
        median = statistics.median(v for m, v in rated.items() if m != mac)
        if median > 0 and value < SLOW_RATIO * median:
            out.append((mac, value, median))
    return out


def _evidence(summary: dict, peaks: dict, rated, idle, unmeasured) -> tuple[str, ...]:
    out = [f"windows={_say(_num(summary.get('windows')))}", f"windows_dropped={_say(_num(summary.get('windows_dropped')))}"]
    out += [f"{key}={_say(_num(peaks.get(key)))}" for _, key, *_ in _CHECKS]
    out.append(f"nic_tx_dropped={_say(_num(summary.get('nic_tx_dropped')))}")
    out.append(f"sent_mbps_peak={_say(_num(peaks.get('sent_mbps')))}")
    for key, p in sorted(_dict(summary.get("passes")).items()):
        p = _dict(p)
        k, n = _num(p.get("chunks")), _num(p.get("of"))
        pct = round(100 * k / n, 1) if k is not None and n and k <= n else None
        out.append(f"pass={key} chunks={_say(k)}/{_say(n)} pct={_say(pct)} bytes={_say(_num(p.get('bytes')))}"
                   f" elapsed_s={_say(_num(p.get('elapsed_s')))} avg_mbps={_say(_avg_mbps(p))}"
                   f" done={'yes' if isinstance(p.get('done'), dict) else 'no'}")
    ledger = _dict(summary.get("ledger"))
    out += [f"cloner={mac} mbps={_say(v)} chunks={_say(_num(_dict(ledger.get(mac)).get('chunks')))}"
            f" seconds={_say(_num(_dict(ledger.get(mac)).get('seconds')))}" for mac, v in rated.items()]
    out += [f"cloner={mac} idle (no chunk became durable)" for mac in idle]
    out += [f"cloner={mac} unmeasured" for mac in unmeasured]
    out.append(f"limits=cpu>={CPU_CORES:g} psi>={PSI_SHARE:g} disk_util>={DISK_UTIL:g} disk_psi_full>={DISK_PSI_FULL:g}"
               f" slow<{SLOW_RATIO:g}xmedian_of_others min_cloners={MIN_CLONERS}")
    return tuple(out)


def diagnose(summary) -> tuple[str, str, tuple[str, ...]]:
    """סיכום הגל של `Senses.end` -> (פסק, סיבה בעברית, ראיות). טהורה. סדר העדיפות בראש המודול."""
    if not isinstance(summary, dict):
        return NOT_MEASURED, "אין סיכום של מה שהשרת ראה בגל: לא הוכרע מה הגביל את הקצב", ("summary=unmeasured",)
    peaks = _dict(summary.get("peaks"))
    seen = {g: False for g in _ORDER}
    hits: list[str] = []
    for group, key, limit, name, how in _CHECKS:
        value = _num(peaks.get(key))
        seen[group] = seen[group] or value is not None
        if value is not None and value >= limit:
            hits.append(f"{name}: {_show(value, how)} (סף {_show(limit, how)})")
    dropped = _num(summary.get("nic_tx_dropped"))
    seen["nic"] = dropped is not None
    if dropped:
        hits.append(f"הכרטיס זרק {dropped:g} חבילות ביציאה (nic_tx_dropped)")
    rated, idle, unmeasured, read = _cloners(summary)
    seen["cloners"] = len(rated) >= MIN_CLONERS
    slow = _slow(rated)
    evidence = _evidence(summary, peaks, rated, idle, unmeasured)
    slow_he = [f"משכפל {mac}: {v:g} MB/s, {100 * v / med:.0f}% מחציון שאר המשכפלים ({med:g} MB/s; סף {SLOW_RATIO * 100:.0f}%)"
               for mac, v, med in slow]
    missing = [_GROUPS_HE[g] + (f" ({len(rated)} נמדדו מתוך {MIN_CLONERS} לפחות)" if g == "cloners" and read else
                                " (לא נקרא)" if g == "cloners" else "") for g in _ORDER if not seen[g]]
    tail = ("" if not missing else "; לא נמדד: " + ", ".join(missing)) \
        + ("" if not (idle or unmeasured) else "; בלי דירוג: " + ", ".join(
            [f"{m} (לא כתב חתיכה)" for m in idle] + [f"{m} (לא נמדד)" for m in unmeasured]))
    if hits:
        return SERVER_PRESSURE, "השרת לחוץ: " + "; ".join(hits + slow_he) + tail, evidence
    if slow:
        return SLOW_CLONER, "משכפל איטי מהשאר: " + "; ".join(slow_he) + tail, evidence
    if missing:
        return NOT_MEASURED, "לא הוכרע מה הגביל את הקצב: אין אות בחושים שנמדדו" + tail, evidence
    return NO_LIMIT_SEEN, ("לא נראה גורם מגביל בין מה שנמדד: המעבד, ה-PSI, הכרטיס, דיסק הספרייה וקצב המשכפלים לא עברו סף"
                           " (מי האט, --kbps או --write-kbps, לא נמדד)" + tail), evidence


def _mode(conn) -> str:
    from server.brain import veto                # noqa: PLC0415 -- המתג הכללי של המוח
    return veto.mode(conn)


def _noted(conn, session_id: str, action: str) -> bool:
    # #3185: the brain's tables are read only through `memory` (test_only_the_memory_module_reads_the_brains_tables)
    return any(d["action"] == action for d in memory.decisions(conn, round_id=session_id, rule=RULE, limit=64))


def _write(conn, session_id: str, action: str, verdict: str, reason: str, evidence) -> int | None:
    return memory.record(conn, mode="shadow", rule=RULE, action=action, verdict=verdict, reason=reason,
                         evidence=evidence, round_id=session_id, entity=("round", session_id), outcome=OUTCOME)


def note_wave(conn, session_id: str, summary) -> int | None:
    """שורה אחת לגל: מה הגביל את הקצב. מחזיר את מספר השורה, או ``None`` (המתג כבוי, כבר נרשם, או הכתיבה נכשלה).
    לעולם אינו זורק."""
    try:
        if _mode(conn) == OFF or _noted(conn, session_id, ACTION):
            return None
        verdict, reason, evidence = diagnose(summary)
        return _write(conn, session_id, ACTION, verdict, reason, evidence)
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל את סוף הגל
        log.exception("brain: the B58 send-limit note of %s failed", session_id)
        return None


_NET_VERDICTS = {"jump": "JUMP", "ok": "OK", "unchecked": "UNCHECKED", "server_disk": "SERVER_DISK", "net_gap": "NET_GAP"}


def _loss(kind: str, value) -> str:
    v = _num(value)
    return "לא נמדד" if v is None else f"{100 * v:.2f}%" if kind == "udpcast" else f"{v:.2f}/GB"


def note_netloss(conn, session_id: str, assessed) -> int | None:
    """שורה אחת לגל מ-LR16 (`netloss.assess`): האבדן ברשת ההפצה מול הבסיס. ``unchecked`` = "אין בסיס עדיין", לא "תקין".
    לעולם אינו זורק."""
    try:
        if not isinstance(assessed, dict) or _mode(conn) == OFF or _noted(conn, session_id, NET_ACTION):
            return None
        from server.learn import netloss             # noqa: PLC0415 -- הספים של LR16, לא עותק שלהם
        state, kind = assessed.get("state"), assessed.get("kind")
        now, base = _loss(kind, assessed.get("loss")), _loss(kind, assessed.get("baseline"))
        where = (f"כרטיס {assessed.get('interface') or 'לא נקרא'}, {_say(assessed.get('drawers'))} מגירות,"
                 f" קצב {_say(assessed.get('rate'))}")
        if state == "jump":
            reason = (f"האבדן ברשת ההפצה קפץ: {now} מול בסיס {base} ({_say(assessed.get('waves'))} גלים; {where});"
                      f" סף הקפיצה פי {netloss.JUMP}")
        elif state == "ok":
            reason = f"האבדן ברשת ההפצה בתוך הבסיס: {now} מול בסיס {base} ({_say(assessed.get('waves'))} גלים; {where})"
        elif state == "unchecked":
            reason = (f"אין בסיס עדיין, לא נבדק: {_say(assessed.get('waves'))} מתוך {netloss.MIN_WAVES} גלים באותם תנאים"
                      f" ({where}); האבדן בגל הזה {now}")
        elif state == "server_disk":
            reason = f"לא נבדק מול הרשת: הגל היה מוגבל בדיסק הספרייה בשרת ({where}); האבדן בגל הזה {now}"
        elif state == "net_gap":
            reason = f"לא נבדק מול הרשת: חלקים לא הגיעו למחשב ברשת (ניתוק) ({where}); האבדן בגל הזה {now}"
        else:
            reason = f"מצב לא מוכר של LR16 ({_say(state)}): לא הוכרע"
        evidence = tuple(f"{key}={_say(assessed.get(key))}" for key in (
            "state", "kind", "interface", "drawers", "rate", "loss", "baseline", "waves", "local", "local_baseline",
            "rcvbuf", "rcvbuf_baseline", "timeouts_per_gb", "timeouts_baseline"))
        return _write(conn, session_id, NET_ACTION, _NET_VERDICTS.get(state, NOT_MEASURED), reason, evidence)
    except Exception:                           # noqa: BLE001 -- המוח אינו מפיל את סוף הגל
        log.exception("brain: the B58 net-loss note of %s failed", session_id)
        return None
