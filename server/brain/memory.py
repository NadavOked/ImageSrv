"""#3064 שלב 4 / #3185: הזיכרון ארוך-הטווח של המוח -- ההחלטות שלו ומה יצא מהן, והידע על כל ישות, לתמיד.

**הכרעת נדב, 14/11:** "המוח על מחשבים ודיסקים צריך לשמור תמיד", ובהמשך "מאושר":

- ``WINDOW`` השורות האחרונות לכל ישות נשמרות מלאות. **מה שישן מהן מסוכם, ולא נמחק.**
- **החלטות כלליות** (שאינן נוגעות בישות מסוימת): 90 יום, ועוד ``WINDOW`` האחרונות.
- **הקונסולה והמוח קוראים דרך אותה פונקציה:** `knowledge` ו-`decisions` כאן. אף מודול אחר אינו קורא את
  ``brain_memory`` או ``brain_decisions`` ישירות (`tests/test_brain_memory_3185.py` בודק את זה).

**הישויות:** ``machine`` (MAC), ``disk`` (סידורי, כולל מגמת SMART), ``slot`` (``mac/port``), ``model``,
``network`` (MAC של משכפל, או ``server`` לרשת ההפצה), ``round``, ``image``, ``settings`` (שם ההגדרה).
חומרת המשכפל היא ``machine`` (`machine_inventory`).

**איך "מסוכם ולא נמחק" עובד.** כלל השמירה של כל טבלה נשאר אחד, ב-`housekeeping._rules`, ולא משוכפל לכאן.
בטבלה שרשומה ב-`SOURCES`, הניקוי היומי אינו מוחק: `fold` בוחר **בדיוק אותן שורות** שה-DELETE של הכלל היה
מוחק, מסכם אותן לשורה אחת לכל (ישות, מקור) ב-``brain_memory``, ומוחק אותן -- באותה טרנזאקציה. כך אף קורא
קיים (`drive_rates._limit_of`, `learn_models`...) אינו רואה שינוי, ומה שיצא מהחלון אינו אובד: הספירה, הסכום,
המינימום, המקסימום, הערך הראשון והאחרון של כל שדה מספרי, והערך האחרון של כל שדה טקסט.

**סבב (#3185, סעיף 8).** שורה לכל סבב חדר שנסגר -- מתי, אימג', כמה דיסקים, כמה הצליחו, משך, קצב, איבוד וסיבת
כשל -- ב-``brain_memory`` תחת ``kind="round"``, מפתח = מזהה הסבב. **בניגוד לשאר המקורות הטבלה עצמה אינה נמחקת:**
``room_rounds`` היא טבלת פעולות שההיסטוריה בקונסולה קוראת (``Source.keep``), ולכן הסיכום נכתב **לפני** שמה שהוא
מביא ממנה עם ``wave_rates`` ו-``drive_rates`` (הקצב והאיבוד) יוצא מהחלון שלהם, והסבב מסוכם פעם אחת בלבד.

**איפה הידע הטרי, ומתי ``brain_memory`` מתמלא (#3430).** ``brain_memory`` הוא **הסיכום של מה שיצא מהחלון**, לא הזיכרון
הטרי: הוא נכתב רק מ-`fold` בניקוי היומי (`housekeeping`, אחת ל-24 שעות, הריצה הראשונה 10 דקות אחרי העלייה), ורק על שורות
שיצאו מחלון השמירה (90 יום, או ``WINDOW`` לישות). סיכום סבב נכתב ``ROUND_DAYS`` אחרי הסגירה. לכן לפני הניקוי הראשון -- וגם
שעה אחרי שלושה כשלים של אותו דיסק -- ``brain_memory`` ריק, **וזה התקין**: ההחלטות הטריות (K9 על ישות ``disk``) יושבות ב-
``brain_decisions`` ונקראות דרך `decisions`/`knowledge`, וטבלאות המקור נקראות במקומן. אסור "לתקן" את זה בכתיבה לזיכרון בזמן
אמת: `knowledge` קורא את שני המקומות, והשורה הטרייה הייתה נספרת פעמיים (טסט: `tests/test_brain_memory_fresh_3430.py`).

**ערך שלא נמדד (NULL) אינו אפס:** הוא לא נכנס לממוצע ולא לספירה של השדה (עיקרון 5).

**כל הכתיבה כאן לעולם אינה זורקת** אל הנקודה ההרסנית או אל מחזור התקציב: זיכרון שנכשל הוא שורת לוג, לא
פעולה שלא בוצעה. `fold` כן זורק -- הניקוי היומי רושם את הכשל בשם הטבלה וממשיך (`retention_failed`).
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from typing import Callable, Iterable

from server.brain import say
from server.db import _settle, _write_lock, now_iso, writing
from server.brain import wavesense

log = logging.getLogger("imagesrv.brain")

#: השורות האחרונות של ``brain_decisions`` לכל ישות (ולכל כלל, בהחלטות הכלליות) שנשמרות מלאות. הערכה: כמו
#: חלון החיבורים (`learn_ports.WINDOW`) -- מספיק לראות דפוס של משכפל או דיסק, וקטן מספיק לקריאה במסך.
WINDOW = 20
#: כמה שורות אחרונות מכל מקור `knowledge` מחזיר.
RECENT = 20
KINDS = ("machine", "disk", "slot", "model", "network", "round", "image", "settings")
#: תוצאה של החלטה שהייתה פתוחה כשהשרת עלה מחדש: היא לא נסגרה, ואין לנו ראיה למה שקרה לה.
ORPHAN_HE = "השרת הופעל מחדש לפני שההחלטה נסגרה: התוצאה לא ידועה"

_STARTED = now_iso()
_orphans_closed = False


# --- החלטות ------------------------------------------------------------------------------------------------------


def _write(conn, sql: str, params: Iterable) -> sqlite3.Cursor:
    _settle(conn)
    with _write_lock, writing(conn):
        return conn.execute(sql, tuple(params))


def _close_orphans(conn) -> None:
    """פעם אחת בכל תהליך: החלטה שנשארה פתוחה מתהליך קודם לא תיסגר לעולם -- סוגרים אותה בשם, לא "בוצעה"."""
    global _orphans_closed
    if _orphans_closed:
        return
    _orphans_closed = True
    _write(conn, "UPDATE brain_decisions SET outcome = ?, outcome_at = ? WHERE outcome IS NULL AND at < ?",
           (ORPHAN_HE, now_iso(), _STARTED))


def record(conn, *, mode: str, rule: str, action: str, verdict: str, reason: str, evidence: Iterable = (),
           point: str | None = None, round_id: str | None = None, entity: tuple[str, str] | None = None,
           outcome: str | None = None, at: str | None = None, predicted: float | None = None,
           unit: str | None = None, orphans: bool = True) -> int | None:
    """שורה אחת ב-``brain_decisions``, ומספרה -- או ``None`` כשהכתיבה נכשלה (נרשם בלוג, לא נזרק).
    ``entity`` = (kind, key), או ``None`` להחלטה כללית. ``outcome`` כבר ידוע (צל: "הקוד הקיים ביצע") או ``None``.
    #3125: ``predicted`` + ``unit`` = מה שההחלטה חזתה, כמספר (לא רק בטקסט); `resolve` משלים את ``actual`` ו-``matched``.
    #3668: ``orphans=False`` = תהליך קצר שאינו השרת (`tools/server-upgrade.sh`) כותב שורה. סגירת היתומות היא של תהליך השרת:
    תהליך אחר שסוגר "החלטות פתוחות שקדמו לו" היה סוגר את ההחלטות החיות של השרת שרץ."""
    try:
        if orphans:
            _close_orphans(conn)
        kind, key = entity if entity else (None, None)
        if kind is not None and kind not in KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        stamp = at or now_iso()
        cur = _write(conn, "INSERT INTO brain_decisions (at, mode, rule, point, action, verdict, reason, evidence_json,"
                     " round_id, entity_kind, entity_key, outcome, outcome_at, predicted, unit)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (stamp, mode, rule, point, action, verdict, reason,
                      json.dumps(list(evidence), ensure_ascii=False), round_id, kind, key,
                      outcome, stamp if outcome is not None else None, predicted, unit))
        return cur.lastrowid
    except Exception:                           # noqa: BLE001 — הזיכרון אינו מפיל החלטה
        log.exception("brain: the decision %s %s %s was not remembered", rule, action, verdict)
        return None


def resolve(conn, ids: Iterable[int | None], outcome: str, at: str | None = None, *,
            actual: float | None = None, matched: bool | None = None) -> int:
    """התוצאה של החלטות פתוחות. החלטה שכבר נסגרה אינה נדרסת. מחזיר כמה נסגרו (0 גם בכשל, שנרשם בלוג).
    #3125: ``actual`` ו-``matched`` הם הצד המובנה של ``outcome`` (שנשאר טקסט חופשי): מה שקרה בפועל, באותה יחידה
    של ``predicted``, והאם התאים. ``None`` = לא נמדד -- לא אפס ולא "לא התאים" (עיקרון 5)."""
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return 0
    try:
        marks = ",".join("?" * len(ids))
        return _write(conn, f"UPDATE brain_decisions SET outcome = ?, outcome_at = ?, actual = ?, matched = ?"
                            f" WHERE outcome IS NULL AND id IN ({marks})",
                      (outcome, at or now_iso(), actual, None if matched is None else int(matched), *ids)).rowcount
    except Exception:                           # noqa: BLE001
        log.exception("brain: the outcome of %s was not remembered", ids)
        return 0


def outcomes(conn, ids: Iterable[int | None]) -> dict[int, str | None]:
    """``outcome`` של החלטות לפי מספר (#3654: למה שאלה בוטלה, בהיסטוריה של דף "שאלות המוח"). מספר שאינו קיים אינו במילון: "לא
    נקרא", לא "אין תוצאה". קריאת ``brain_decisions`` היא של המודול הזה בלבד (``test_only_the_memory_module_reads_the_brains_tables``)."""
    ids = [i for i in ids if isinstance(i, int)]
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {r["id"]: r["outcome"] for r in conn.execute(f"SELECT id, outcome FROM brain_decisions WHERE id IN ({marks})", ids)}


def _outcome_state(outcome: str | None) -> str:
    """מה אפשר לומר על התוצאה: ``open`` (עוד לא נסגרה), ``unknown`` (נסגרה בלי ראיה למה שקרה: השרת הופעל מחדש),
    או ``closed``. הקונסולה אינה מנחשת "בוצע" מהיעדר טקסט -- היא קוראת את זה (#3185)."""
    return "open" if outcome is None else "unknown" if outcome == ORPHAN_HE else "closed"


def _decision(row: sqlite3.Row) -> dict:
    out = dict(row)
    out["outcome_state"] = _outcome_state(out.get("outcome"))
    try:
        out["evidence"] = json.loads(out.pop("evidence_json") or "[]")
    except (TypeError, ValueError):
        out["evidence"] = None                  # נשמר פגום: "לא נקרא", לא "אין ראיות"
    out["line_he"] = say.line_he(out)           # #3320 (R1): השורה בעברית שהקונסולה מציגה
    return out


def decisions(conn, *, kind: str | None = None, key: str | None = None, rule: str | None = None,
              limit: int = RECENT, round_ids: Iterable[str] | None = None, round_id: str | None = None,
              since: str | None = None) -> list[dict]:
    """ההחלטות האחרונות, החדשה ראשונה. ``kind``/``key`` = של ישות; ``kind="general"`` = רק הכלליות;
    ``round_ids`` = מה שהוחלט בגלים האלה (לסיכום סבב, #3185); ``round_id`` = של סבב אחד;
    ``since`` = רק מזמן ISO זה והלאה (#3064 שלב 5: המוח קורא את הזיכרון בחלון)."""
    where, params = [], []
    if round_ids is not None:
        round_ids = list(round_ids)
        where.append(f"round_id IN ({','.join('?' * len(round_ids))})")
        params.extend(round_ids)
    if round_id is not None:
        where.append("round_id = ?")
        params.append(round_id)
    if since is not None:
        where.append("julianday(at) >= julianday(?)")
        params.append(since)
    if kind == "general":
        where.append("entity_kind IS NULL")
    elif kind is not None:
        where.append("entity_kind = ?")
        params.append(kind)
    if key is not None:
        where.append("entity_key = ?")
        params.append(key)
    if rule is not None:
        where.append("rule = ?")
        params.append(rule)
    sql = "SELECT * FROM brain_decisions" + (" WHERE " + " AND ".join(where) if where else "") \
        + " ORDER BY id DESC LIMIT ?"
    return [_decision(r) for r in conn.execute(sql, (*params, max(int(limit), 0)))]


def rule_tally(conn) -> list[dict]:
    """#3563: לכל כלל, מה ``brain_decisions`` יודעת עליו לצורך שער המעבר מצל לאכיפה -- ספירה בלבד, בלי שיפוט (השיפוט ב-
    `server/brain/shadowgate.py`). רק שורות ``shadow`` נספרות כראיה; ``mode`` = המצב של השורה **האחרונה** של הכלל.
    ``measured`` = שורות צל שיש להן ``matched`` (0/1). ``NULL`` = **לא נמדד**: אינו התאמה ואינו אי-התאמה, ואינו במונה
    ולא במכנה (#3750, עיקרון 5). ``samples`` = אירועים בלתי תלויים: שורות באותו ``round_id`` הן דגימה אחת (דיסקים באותו
    גל אינם דגימות נפרדות); שורה בלי ``round_id`` היא דגימה בפני עצמה."""
    sql = (
        "SELECT rule,"
        " SUM(mode = 'shadow') AS shadow_rows,"
        " SUM(mode = 'shadow' AND matched IS NOT NULL) AS measured,"
        " SUM(mode = 'shadow' AND matched = 1) AS agreed,"
        " SUM(mode = 'shadow' AND matched IS NULL) AS unmeasured,"
        " COUNT(DISTINCT CASE WHEN mode = 'shadow' AND matched IS NOT NULL"
        "                     THEN COALESCE(round_id, 'row:' || id) END) AS samples,"
        " MAX(CASE WHEN mode = 'enforce' THEN id END) AS last_enforce_id,"
        " MAX(CASE WHEN mode = 'shadow' THEN id END) AS last_shadow_id,"
        " MAX(at) AS last_at"
        " FROM brain_decisions GROUP BY rule ORDER BY rule")
    out = []
    for r in conn.execute(sql):
        d = {k: r[k] for k in r.keys()}
        enforce, shadow = d.pop("last_enforce_id"), d.pop("last_shadow_id")
        d["mode"] = "enforce" if enforce is not None and (shadow is None or enforce > shadow) else "shadow"
        out.append(d)
    return out


# --- המקורות: מה מסוכם, ולאיזו ישות -------------------------------------------------------------------------------


def _num(value) -> float | None:
    """מספר, או ``None`` (לא נמדד). ``bool`` הוא דגל, והוא נספר כ-0/1."""
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        head = value.strip().split(" ")[0]
        try:
            return float(head)
        except ValueError:
            return None
    return None


def _json(raw) -> object:
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def _bitrate(raw) -> float | None:
    from server.rounds import drive_rates       # noqa: PLC0415 — רק לסיכום של גל; drive_rates כבד
    got = drive_rates.parse_bitrate(raw)
    return float(got) if isinstance(got, int) else None


def _wave(row) -> dict:
    decision = _json(row["decision"]) or {}
    stream = _json(row["stream"]) or {}
    drops = _json(row["drops"]) or {}
    out = {"max_bitrate_bps": _bitrate(decision.get("max_bitrate")) if isinstance(decision, dict) else None,
           "loss_blocks": _num(stream.get("loss_blocks")) if isinstance(stream, dict) else None,
           "sent_bytes": _num(stream.get("sent_bytes")) if isinstance(stream, dict) else None}
    if isinstance(drops, dict) and drops:
        for field in ("rcvbuf", "abandoned"):
            values = [d.get(field) if isinstance(d, dict) else None for d in drops.values()]
            # מקלט אחד שלא נקרא = כל הגל "לא נקרא" (אותו כלל של `drive_rates._rcvbuf`)
            if values and all(isinstance(v, int) and not isinstance(v, bool) and v >= 0 for v in values):
                out[field] = float(sum(values))
    return out


def _wave_drops(row) -> dict:
    """לכל משכפל בגל: rcvbuf, inerrors ו-abandoned שדיווח. ``null`` (לא נקרא) אינו נכנס לסיכום."""
    drops = _json(row["drops"])
    return {("network", str(mac)): {f: _num(d.get(f)) for f in ("rcvbuf", "inerrors", "abandoned")}
            for mac, d in (drops.items() if isinstance(drops, dict) else ()) if isinstance(d, dict)}


def _smart(row) -> dict:
    attrs = _json(row["attrs_json"]) or {}
    out = {"temp_after": _num(row["temp_after"])}
    for attr in (attrs.get("ata") or []) if isinstance(attrs, dict) else []:
        if isinstance(attr, list) and len(attr) >= 5 and isinstance(attr[0], int):
            out[f"ata_{attr[0]}"] = _num(attr[4])
    nvme = attrs.get("nvme") if isinstance(attrs, dict) else None
    for name, value in (nvme.items() if isinstance(nvme, dict) else ()):
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            out[f"nvme_{name}"] = float(value)
    return out


def _server_io(row) -> dict:
    """#3125: גל ב-`server_io` -- דיסק הספרייה (#1884) ומה שהשרת ראה בגל (`result_json.server`: כל הגל, ושדה שכבר
    קיים בדיסק מקבל `server_`, כמו `seconds`; והשיא בחלון הגבוה, `peak_*`). ``null`` (לא נמדד) אינו נכנס לסיכום."""
    result = _json(row["result_json"]) or {}
    server = result.get("server") if isinstance(result, dict) else None
    server = server if isinstance(server, dict) else {}
    peaks = server.get("peaks") if isinstance(server.get("peaks"), dict) else {}
    out = {k: _num(v) for k, v in (result.items() if isinstance(result, dict) else ()) if k != "server"}
    out.update({f"server_{k}" if k in out else k: _num(v) for k, v in server.items() if k != "peaks"})
    out.update({f"peak_{k}": _num(v) for k, v in peaks.items()})
    return {k: v for k, v in out.items() if v is not None}


@dataclass(frozen=True)
class Source:
    """טבלה שהניקוי היומי מסכם לפני שהוא מוחק. ``rule`` = השם ב-`housekeeping._rules`."""
    rule: str
    table: str
    at: str
    keys: Callable[[sqlite3.Row], list[tuple[str, str]]]
    nums: Callable[[sqlite3.Row], dict]
    texts: Callable[[sqlite3.Row], dict] = lambda row: {}
    #: ישויות נוספות מאותה שורה, כל אחת עם המספרים שלה: גל אחד = מה שכל משכפל בו זרק (`wave_rates.drops`).
    extra: Callable[[sqlite3.Row], dict] = lambda row: {}
    #: #3185: טבלת פעולות -- השורות מסוכמות ואינן נמחקות (`room_rounds`). כלל השמירה בוחר אילו, וצריך לסנן שורה
    #: שכבר סוכמה (`fold` לא מוחק, ולכן לא יסכם אותה שוב מעצמו).
    keep: bool = False
    #: #3185: עמודות שהשאילתה מוסיפה לכל שורה (תת-שאילתות על טבלאות אחרות, "AS name"), אחרי `*`.
    select: str = ""


def _has(*pairs: tuple[str, object]) -> list[tuple[str, str]]:
    return [(kind, str(key)) for kind, key in pairs if key not in (None, "")]


def _slot(row) -> str | None:
    return f"{row['mac']}/{row['port']}" if row["port"] is not None else None


def _in_round(alias: str) -> str:
    """הדגימה נכנסת לסבב אם הזמן שלה בין פתיחתו לסגירתו. סבב החדר אחד בכל רגע (`visible_round`), ולכן החלון
    קובע; ל-`wave_rates` ול-`drive_rates` אין מזהה סבב, רק מזהה גל."""
    return (f"julianday({alias}.at) BETWEEN julianday(room_rounds.created_at)"
            " AND julianday(room_rounds.closed_at)")


def _wave_total(field: str) -> str:
    """סכום שדה מספרי של `wave_rates.stream` בגלי הסבב. NULL = אף גל לא דיווח אותו (לא 0); JSON פגום או שדה
    שאינו מספר אינו נספר -- ואינו מפיל את הסיכום של כל הסבבים."""
    return (f"(SELECT SUM(v) FROM (SELECT json_extract(w.stream, '$.{field}') AS v FROM wave_rates w"
            f" WHERE json_valid(w.stream) AND {_in_round('w')}) WHERE typeof(v) IN ('integer', 'real'))")


#: #3185: מה שסבב מביא מהטבלאות שיש להן חלון שמירה משלהן. הקצב = ממוצע הקצב שכל דיסק כתב בו (`drive_rates`).
_ROUND_SELECT = ", ".join((
    "ROUND((julianday(closed_at) - julianday(created_at)) * 86400) AS seconds",
    f"(SELECT AVG(d.rate_bps) FROM drive_rates d WHERE {_in_round('d')}) AS rate_bps",
    f"{_wave_total('loss_blocks')} AS loss_blocks",
    f"{_wave_total('sent_bytes')} AS sent_bytes",
    f"(SELECT COUNT(*) FROM wave_rates w WHERE {_in_round('w')}) AS waves"))


SOURCES: dict[str, Source] = {s.rule: s for s in (
    Source("room_rounds", "room_rounds", "closed_at", lambda r: _has(("round", r["id"])),
           lambda r: {"disks": _num(r["target_drives"]), "ok": _num(r["written_drives"]),
                      "seconds": _num(r["seconds"]), "rate_bps": _num(r["rate_bps"]),
                      "loss_blocks": _num(r["loss_blocks"]), "sent_bytes": _num(r["sent_bytes"]),
                      "waves": _num(r["waves"])},
           lambda r: {"when": r["created_at"], "image_id": r["image_id"], "state": r["state"],
                      "reason": r["failed_reason"]},
           keep=True, select=_ROUND_SELECT),
    Source("drive_rates", "drive_rates", "at", lambda r: _has(("disk", r["serial"]), ("model", r["model"])),
           lambda r: {k: _num(r[k]) for k in ("rate_bps", "cap_bps", "bytes", "seconds")}),
    Source("wave_rates", "wave_rates", "at", lambda r: [("network", "server")], _wave, extra=_wave_drops),
    # #3125: רק שורת הגל מסוכמת (`network/server`). חלונות הגל (`kind = "window"`) והקליטות יוצאים כמו קודם.
    Source("server_io", "server_io", "started_at",
           lambda r: [("network", "server")] if r["kind"] == "wave" else [], _server_io),
    Source("model_speeds", "model_speeds", "at", lambda r: _has(("model", r["model"]), ("disk", r["serial"])),
           lambda r: {"mbps": _num(r["mbps"])}),
    Source("smart_history", "smart_history", "at", lambda r: _has(("disk", r["serial"])), _smart),
    Source("port_history", "port_history", "at",
           lambda r: _has(("slot", f"{r['mac']}/{r['port']}"), ("disk", r["serial"])),
           lambda r: {k: _num(r[k]) for k in ("crc_delta", "link_gbps", "link_max_gbps", "cable_fail")}),
    Source("nic_history", "nic_history", "at", lambda r: _has(("network", r["mac"])),
           lambda r: {k: _num(r[k]) for k in ("speed_mbps", "rx_crc_errors", "rx_dropped")},
           lambda r: {"iface": r["iface"]}),
    Source("wol_wakes", "wol_wakes", "sent_at", lambda r: _has(("machine", r["mac"])),
           lambda r: {"seconds": _num(r["seconds"]), "answered": 1.0 if r["answered_at"] else 0.0,
                      "resent": _num(r["resent"]), "early": _num(r["early"]), "alerted": _num(r["alerted"])}),
    Source("boot_step_times", "boot_step_times", "at", lambda r: _has(("machine", r["mac"])),
           lambda r: {f"boot_{r['step']}_s": _num(r["seconds"])}, lambda r: {"version": r["version"]}),
    Source("disk_failures", "disk_failures", "at", lambda r: _has(("disk", r["serial"]), ("slot", _slot(r))),
           lambda r: {f"fail_{r['cause']}": 1.0}, lambda r: {"cause": r["cause"], "error": r["error"]}),
    Source("machine_inventory", "machine_inventory", "seen_at", lambda r: _has(("machine", r["mac"])),
           lambda r: {"inventory_versions": 1.0}),
    # #3041/#3125: what the cloner itself felt in a chunk wave (S46-S50) -- the wave's highest of each sense
    Source("wave_senses", "wave_senses", "at", lambda r: _has(("machine", r["mac"])),
           lambda r: wavesense.numbers(r["senses_json"]), lambda r: {"session_id": r["session_id"]}),
    Source("brain_decisions", "brain_decisions", "at",
           lambda r: _has((r["entity_kind"], r["entity_key"])) if r["entity_kind"] in KINDS else [],
           lambda r: {f"{r['rule']}_{r['verdict']}": 1.0},
           lambda r: {"reason": r["reason"], "outcome": r["outcome"]}),
    Source("settings_history", "journal", "ts",
           lambda r: _has(("settings", (r["detail"] or "").split("=", 1)[0].strip() or None)),
           lambda r: {"changes": 1.0},
           lambda r: {"value": (r["detail"] or "").split("=", 1)[-1].strip(), "user": r["user"]}),
)}
#: התקרה בשורות של `learn.net_history` (בכל הכנסה) מוציאה מאותה טבלה, ולכן גם היא מסכמת ולא מוחקת.
SOURCES["nic_history_cap"] = SOURCES["nic_history"]


# --- הסיכום ------------------------------------------------------------------------------------------------------


def _merge(stats: dict, at: str | None, nums: dict, texts: dict) -> None:
    """שורה אחת לתוך הסיכום. ``first``/``last`` לפי הזמן, לא לפי סדר ההגעה."""
    num = stats.setdefault("num", {})
    for field, value in nums.items():
        if value is None:
            continue                            # לא נמדד: לא נספר
        s = num.get(field)
        if s is None:
            num[field] = {"n": 1, "sum": value, "min": value, "max": value, "first": value, "last": value,
                          "first_at": at, "last_at": at}
            continue
        s["n"] += 1
        s["sum"] += value
        s["min"], s["max"] = min(s["min"], value), max(s["max"], value)
        if at is not None and (s.get("first_at") is None or at < s["first_at"]):
            s["first"], s["first_at"] = value, at
        if at is not None and (s.get("last_at") is None or at >= s["last_at"]):
            s["last"], s["last_at"] = value, at
    text = stats.setdefault("text", {})
    for field, value in texts.items():
        if value in (None, ""):
            continue
        old = text.get(field)
        if old is None or at is None or old.get("at") is None or at >= old["at"]:
            text[field] = {"value": value, "at": at}


def _combine(old: dict, new: dict) -> dict:
    """סיכום קיים + סיכום של שורות חדשות. שקול לסיכום של כל השורות יחד (נבדק בטסט)."""
    out = json.loads(json.dumps(old))
    for field, s in new.get("num", {}).items():
        o = out.setdefault("num", {}).get(field)
        if o is None:
            out["num"][field] = dict(s)
            continue
        o["n"] += s["n"]
        o["sum"] += s["sum"]
        o["min"], o["max"] = min(o["min"], s["min"]), max(o["max"], s["max"])
        if s.get("first_at") is not None and (o.get("first_at") is None or s["first_at"] < o["first_at"]):
            o["first"], o["first_at"] = s["first"], s["first_at"]
        if s.get("last_at") is not None and (o.get("last_at") is None or s["last_at"] >= o["last_at"]):
            o["last"], o["last_at"] = s["last"], s["last_at"]
    for field, t in new.get("text", {}).items():
        o = out.setdefault("text", {}).get(field)
        if o is None or t.get("at") is None or o.get("at") is None or t["at"] >= o["at"]:
            out["text"][field] = t
    return out


def _span(a: str | None, b: str | None, pick) -> str | None:
    return b if a is None else a if b is None else pick(a, b)


def fold(conn, rule: str, delete_sql: str, params: tuple) -> int:
    """במקום ה-DELETE של כלל השמירה: מסכם את השורות שהוא היה מוחק לתוך ``brain_memory``, ומוחק אותן. רץ בתוך
    הטרנזאקציה של הקורא (`housekeeping.purge_tables`). מחזיר כמה שורות יצאו מהטבלה -- אותו מספר שה-DELETE החזיר. בטבלה ``keep`` אף שורה לא יוצאת,
    והמספר הוא כמה שורות סוכמו."""
    source = SOURCES[rule]
    head = f"DELETE FROM {source.table} WHERE "
    if not delete_sql.startswith(head):
        raise ValueError(f"{rule}: the retention rule is not a DELETE FROM {source.table} WHERE ...")
    rows = conn.execute(f"SELECT rowid AS _rid, *{', ' + source.select if source.select else ''}"
                        f" FROM {source.table} WHERE " + delete_sql[len(head):], params).fetchall()
    if not rows:
        return 0
    folded: dict[tuple[str, str], dict] = {}
    for row in rows:
        at = row[source.at]
        nums, texts, extra = source.nums(row), source.texts(row), source.extra(row)
        targets = {entity: (nums, texts) for entity in source.keys(row)}
        for entity, more in extra.items():
            base, words = targets.get(entity, ({}, {}))
            targets[entity] = ({**base, **more}, words)
        for (kind, key), (got, words) in targets.items():
            entry = folded.setdefault((kind, key), {"rows": 0, "first_at": None, "last_at": None, "stats": {}})
            entry["rows"] += 1
            entry["first_at"] = _span(entry["first_at"], at, min)
            entry["last_at"] = _span(entry["last_at"], at, max)
            _merge(entry["stats"], at, got, words)
    stamp = now_iso()
    for (kind, key), entry in folded.items():
        old = conn.execute("SELECT rows, first_at, last_at, stats_json FROM brain_memory"
                           " WHERE kind = ? AND key = ? AND source = ?", (kind, key, source.table)).fetchone()
        if old is not None:
            prior = _json(old["stats_json"])
            if not isinstance(prior, dict):     # סיכום פגום אינו נדרס בשקט: הכשל נרשם והשורות נשארות
                raise ValueError(f"{rule}: the summary of {kind}/{key} is unreadable")
            entry = {"rows": old["rows"] + entry["rows"],
                     "first_at": _span(old["first_at"], entry["first_at"], min),
                     "last_at": _span(old["last_at"], entry["last_at"], max),
                     "stats": _combine(prior, entry["stats"])}
        conn.execute("INSERT INTO brain_memory (kind, key, source, rows, first_at, last_at, stats_json, updated_at)"
                     " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (kind, key, source) DO UPDATE SET rows = excluded.rows,"
                     " first_at = excluded.first_at, last_at = excluded.last_at, stats_json = excluded.stats_json,"
                     " updated_at = excluded.updated_at",
                     (kind, key, source.table, entry["rows"], entry["first_at"], entry["last_at"],
                      json.dumps(entry["stats"], ensure_ascii=False), stamp))
    if source.keep:
        return len(rows)                        # סוכמו, והטבלה נשארת כמו שהיא
    ids = [row["_rid"] for row in rows]
    deleted = 0
    for start in range(0, len(ids), 500):
        part = ids[start:start + 500]
        deleted += conn.execute(f"DELETE FROM {source.table} WHERE rowid IN ({','.join('?' * len(part))})",
                                part).rowcount
    return deleted


# --- ידע שהמוח לומד בעצמו (#3041) -----------------------------------------------------------------------------------


def remember(conn, kind: str, key: str, source: str, nums: dict, texts: dict | None = None, *,
             at: str | None = None, replace: bool = False) -> bool:
    """תצפית אחת לתוך הסיכום של ישות, במקור ``source`` ב-``brain_memory`` -- באותה צורה ש-`fold` כותב (ספירה, סכום,
    מינימום, מקסימום, ראשון ואחרון לכל שדה). **נשמר לתמיד**, כמו כל הסיכומים. ``replace`` זורק את מה שנצבר ומתחיל מהתצפית
    הזו (איפוס). ערך ``None`` ב-``nums`` אינו נספר (עיקרון 5). סיכום קיים שאינו נקרא אינו נדרס בשקט. מחזיר ``False``
    בכשל, שנרשם בלוג: הזיכרון אינו מפיל את מי שכותב אליו."""
    try:
        if kind not in KINDS:
            raise ValueError(f"unknown entity kind {kind!r}")
        stamp = at or now_iso()
        fresh: dict = {}
        _merge(fresh, stamp, nums, texts or {})
        _settle(conn)
        with _write_lock, writing(conn):
            old = None if replace else conn.execute(
                "SELECT rows, first_at, last_at, stats_json FROM brain_memory WHERE kind = ? AND key = ? AND source = ?",
                (kind, key, source)).fetchone()
            if old is None:
                rows, first_at, last_at, stats = 1, stamp, stamp, fresh
            else:
                prior = _json(old["stats_json"])
                if not isinstance(prior, dict):
                    raise ValueError(f"the summary of {kind}/{key}/{source} is unreadable")
                rows, first_at, last_at = old["rows"] + 1, _span(old["first_at"], stamp, min), _span(old["last_at"], stamp, max)
                stats = _combine(prior, fresh)
            conn.execute("INSERT INTO brain_memory (kind, key, source, rows, first_at, last_at, stats_json, updated_at)"
                         " VALUES (?,?,?,?,?,?,?,?) ON CONFLICT (kind, key, source) DO UPDATE SET rows = excluded.rows,"
                         " first_at = excluded.first_at, last_at = excluded.last_at, stats_json = excluded.stats_json,"
                         " updated_at = excluded.updated_at",
                         (kind, key, source, rows, first_at, last_at, json.dumps(stats, ensure_ascii=False), stamp))
        return True
    except Exception:                           # noqa: BLE001 -- הזיכרון אינו מפיל את הלמידה
        log.exception("brain: %s/%s/%s was not remembered", kind, key, source)
        return False


# --- הקריאה: אותה פונקציה לקונסולה ולמוח --------------------------------------------------------------------------


def _slot_key(key: str) -> tuple[str, int | None]:
    mac, _, port = key.rpartition("/")
    return mac, int(port) if port.isdigit() else None


#: השורות האחרונות של כל ישות, מהטבלאות עצמן (מה שעוד בחלון). (מקור, SQL, איך המפתח הופך לפרמטרים).
_RECENT: dict[str, list[tuple[str, str, Callable[[str], tuple]]]] = {
    "disk": [("drive_rates", "SELECT * FROM drive_rates WHERE serial = ? ORDER BY id DESC", lambda k: (k,)),
             ("model_speeds", "SELECT * FROM model_speeds WHERE serial = ? ORDER BY at DESC", lambda k: (k,)),
             ("smart_history", "SELECT serial, session_id, mac, at, temp_after FROM smart_history"
                               " WHERE serial = ? ORDER BY at DESC", lambda k: (k,)),
             ("port_history", "SELECT * FROM port_history WHERE serial = ? ORDER BY at DESC", lambda k: (k,)),
             ("disk_failures", "SELECT id, session_id, mac, port, at, cause, error, cleared_at FROM disk_failures"
                               " WHERE serial = ? ORDER BY id DESC", lambda k: (k,))],
    "model": [("model_speeds", "SELECT * FROM model_speeds WHERE model = ? ORDER BY at DESC", lambda k: (k,)),
              ("drive_rates", "SELECT * FROM drive_rates WHERE model = ? ORDER BY id DESC", lambda k: (k,))],
    "slot": [("port_history", "SELECT * FROM port_history WHERE mac = ? AND port = ? ORDER BY at DESC", _slot_key),
             ("disk_failures", "SELECT id, session_id, serial, at, cause, error, cleared_at FROM disk_failures"
                               " WHERE mac = ? AND port = ? ORDER BY id DESC", _slot_key)],
    "machine": [("wol_wakes", "SELECT * FROM wol_wakes WHERE mac = ? ORDER BY sent_epoch DESC", lambda k: (k,)),
                ("boot_step_times", "SELECT * FROM boot_step_times WHERE mac = ? ORDER BY rowid DESC",
                 lambda k: (k,)),
                ("machine_inventory", "SELECT id, mac, seen_at FROM machine_inventory WHERE mac = ? ORDER BY id DESC",
                 lambda k: (k,)),
                ("wave_senses", "SELECT session_id, at, senses_json FROM wave_senses WHERE mac = ? ORDER BY at DESC",
                 lambda k: (k,))],
    "network": [("nic_history", "SELECT * FROM nic_history WHERE mac = ? ORDER BY at DESC", lambda k: (k,)),
                ("wave_rates", "SELECT session_id, at, decision, stream, drops FROM wave_rates WHERE ? = 'server'"
                               " ORDER BY rowid DESC", lambda k: (k,)),
                ("server_io", "SELECT ref, started_at, ended_at, result_json, basis FROM server_io"
                              " WHERE ? = 'server' AND kind = 'wave' ORDER BY started_at DESC, rowid DESC",
                 lambda k: (k,))],
    "round": [("room_rounds", "SELECT id, image_id, target_drives, written_drives, state, created_at, closed_at,"
                              " failed_reason FROM room_rounds WHERE id = ?", lambda k: (k,)),
              ("sessions", "SELECT id, image_id, expected_clients, state, created_at, started_at, closed_at,"
                           " failed_reason FROM sessions WHERE id = ?", lambda k: (k,)),
              ("wave_rates", "SELECT session_id, at, decision, stream, drops FROM wave_rates WHERE session_id = ?",
               lambda k: (k,)),
              # #3125: the wave's row and its windows (`server_senses`), in order
              ("server_io", "SELECT kind, ref, started_at, ended_at, result_json, basis FROM server_io"
                            " WHERE (kind = 'wave' AND ref = ?) OR (kind = 'window' AND substr(ref, 1, length(?) + 1)"
                            " = ? || '/') ORDER BY started_at, rowid", lambda k: (k, k, k))],
    "image": [("room_rounds", "SELECT id, target_drives, written_drives, state, created_at, closed_at, failed_reason"
                              " FROM room_rounds WHERE image_id = ? ORDER BY created_at DESC", lambda k: (k,)),
              ("sessions", "SELECT id, expected_clients, state, created_at, closed_at, failed_reason FROM sessions"
                           " WHERE image_id = ? ORDER BY created_at DESC", lambda k: (k,))],
    "settings": [("journal", "SELECT ts, user, detail FROM journal WHERE event = 'setting_change'"
                             " AND (detail = ? OR detail LIKE ? ESCAPE '\\') ORDER BY id DESC",
                  lambda k: (k, k.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "=%"))],
}


def summary(conn, kind: str, key: str) -> dict[str, dict]:
    """מה שסוכם על הישות, לפי מקור. ריק = עוד לא יצא דבר מהחלון (לא "אין ידע")."""
    out = {}
    for row in conn.execute("SELECT source, rows, first_at, last_at, stats_json FROM brain_memory"
                            " WHERE kind = ? AND key = ? ORDER BY source", (kind, key)):
        stats = _json(row["stats_json"])
        out[row["source"]] = {"rows": row["rows"], "first_at": row["first_at"], "last_at": row["last_at"],
                              **(stats if isinstance(stats, dict) else {"unreadable": True})}
    return out


def knowledge(conn, kind: str, key: str, *, limit: int = RECENT) -> dict:
    """כל מה שהשרת יודע על ישות אחת: השורות האחרונות מכל מקור (מלאות), הסיכום של מה שיצא מהחלון, וההחלטות
    האחרונות של המוח עליה. **זו נקודת הקריאה היחידה** -- גם של הקונסולה וגם של המוח."""
    if kind not in KINDS:
        raise ValueError(f"unknown entity kind {kind!r}")
    recent = {}
    for source, sql, params in _RECENT.get(kind, []):
        recent[source] = [dict(r) for r in conn.execute(sql + " LIMIT ?", (*params(key), max(int(limit), 0)))]
    return {"kind": kind, "key": key, "recent": recent, "summary": summary(conn, kind, key),
            "decisions": decisions(conn, kind=kind, key=key, limit=limit)}


def entities(conn, kind: str) -> list[dict]:
    """כל הישויות מסוג אחד שיש עליהן סיכום, גם כאלה שלא נראו שנה ("ישות שלא נראתה עדיין קיימת")."""
    if kind not in KINDS:
        raise ValueError(f"unknown entity kind {kind!r}")
    return [dict(r) for r in conn.execute(
        "SELECT key, SUM(rows) AS rows, MIN(first_at) AS first_at, MAX(last_at) AS last_at FROM brain_memory"
        " WHERE kind = ? GROUP BY key ORDER BY key", (kind,))]
