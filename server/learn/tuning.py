"""‏#2028 — למידה אוטומטית: סבב על 2 השיכפולים הבאים, ומה הותאם בעקבותיו.

הכרעות נדב 23/10 ו-06/11 (מוקאפ `docs/design/mockups-1107/2028-learning.html`):

* המתג `learning_enabled`, כבוי כברירת מחדל. כבוי = הכול ידני, כמו היום.
* **הלמידה נשענת על שיכפולים אמיתיים.** אין כאן שום כתיבה לדיסק לצורך מדידה: כל מה
  שנקרא הוא מה שהגל כבר השאיר — ‏`wave_rates`, ‏`model_speeds`, ‏`port_history`.
* סבב = `REQUIRED_WAVES` הגלים הבאים. הגיעו כולם עם ראיה — מתאימים; לא הגיעו —
  **לא משנים כלום** ומציגים "לא נלמד: 1 מתוך 2" (עיקרון 5: בלי ראיה אין שינוי).
* כל שינוי נרשם עם הערך הקודם והחדש ועם המספר שהניע אותו (עיקרון 5ב), ואפשר לחזור
  ממנו. כיבוי המתג אינו מחזיר דבר: "ההגדרות נשארות כפי שהן". שינוי ידני גובר.
* שלוש רמות: דיסק לפי סידורי, מחשב וחיבור לפי MAC ופורט, רשת לפי השרת (`_levels`).

**ראיה לגל** (`_evidence`): השולח סיים (‏`send_done`), המונים שלו נקראו, הזרם אינו קצר
מדגימה (‏`MIN_SAMPLE_*`), אף מקלט לא נותק, ומגירה אחת לפחות סיימה. גל ישיר ממחשב בנייה
(‏#715) אינו נספר כלל: המונה שלו אינו בשרת.

**מה הלמידה רשאית לשנות** — שתי הגדרות, ושתיהן רק כלפי מעלה:

    ‏`writecheck_min_mbps` = הנמוך מבין `FLOOR_OF_MEDIAN` × החציון, `drive_rates.MARGIN` × הדיסק
                            האיטי שנמדד, והזרם שהשרת עצמו מדד. לא פוסל דיסק שנכתב בסבב.
    ‏`learned_start_mbps`  = הקצב שבו **כל** גלי הסבב עמדו, מעוגל מטה ל-`REFINE_STEP`,
                            בלי אבדן מעל `LOSS_DOWN_RATIO`. הגג של `drive_rates.choose` נשאר.

שתיהן עד `MAX_STEP` מהערך הנוכחי בסבב אחד. ‏`crc_warn_threshold` מוצג בסיכום ואינו משתנה:
סף CRC הוא הכרעה של מנהל.

**לא סומכים על מספר שמכונה מדווחת** (סקירת אבטחה 06/11: מכונה ברשת ההפצה יכולה לשקר, והרף
חל על כולן). ‏`write_preflight.mbps` הוא דיווח של הסוכן, ולכן:

* נספרת רק מדידה של דיסק שהשרת שם ברשימת הגל (‏`decision.roster`, לפני שהגל יצא), של מכונה
  רשומה, שמגירה שלה סיימה בגל (`_measured`).
* מדידה מעל מה שהקישור שלה מסוגל לו, או מעל `MODEL_OUTLIER` מחציון הדגם מחוץ לסבב — נדחית,
  נרשמת ביומן ולא נספרת. מדידה **נמוכה** אינה נדחית: היא יכולה רק למנוע העלאה, ודיסק גוסס
  אמיתי הוא בדיוק מי שהסף חייב להתחשב בו.
* **התקרה נמדדת בשרת:** הזרם (‏`stream_bps`, המונה של udp-sender בשרת) מתקדם בקצב המקבל האיטי,
  וכל דיסק שסיים עמד בו. מעליו אין ראיה משרתת, וגם אין צורך: דיסק מהיר מהזרם אינו מעכב גל.
  ‏`learned_start_mbps` כולו ממדידה בשרת, ודיווח של סוכן אינו יכול להעלות אותו.
* כיוון מטה חסום לגמרי: למידה אינה מורידה הגדרה.

‏`record_wave` לעולם אינו זורק — למידה אינה עוצרת סיום גל. הצעת הלמידה למחשב חדש
(`machine_states`): ‏"נלמד" = נמדדו החיבורים שלו ב-`REQUIRED_WAVES` גלים (נדב 23/10:
"נמדדו החיבורים שלו, לא דיסקים מסוימים"), ונגזר מ-`port_history`.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics

from server.db import _settle, _write_lock, get_setting, journal, now_iso, writing
from server.disks import crcwarn, writefloor
from server.learn import models as learn_models
from server.rounds import drive_rates

log = logging.getLogger("imagesrv.learn_tuning")

SETTING = "learning_enabled"
#: כמה שיכפולים לסבב. המקור: נדב 23/10 ("2 הרצות"), אושר במוקאפ 06/11, שאלה 2.
REQUIRED_WAVES = 2
#: הסף מחצי החציון — אותו יחס שהסוכן כבר פוסל בו באדום (‏#1366, ‏`writecheck.sh`).
FLOOR_OF_MEDIAN = 0.5
#: כמה הגדרה יכולה לעלות בסבב אחד. ⚠️ ניחוש מנומק, לא מדידה: הגנה מפני דיווח שקרי (סקירת
#: אבטחה 06/11). ‏50→75 בסבב, ו-600→900 הוא גג הקו כולו.
MAX_STEP = 1.5
#: פי כמה מחציון הדגם מדידה נדחית. ⚠️ ניחוש מנומק: מרווח רחב, כי באותו דגם יש קושחות ומצבי
#: מטמון שונים, והמטרה היא מספר שקרי ולא דיסק מהיר.
MODEL_OUTLIER = 3
#: ‏MB/s לכל Gb/s של קישור SATA: קידוד 8b/10b, כלומר 6Gb/s הם 600MB/s לכל היותר.
SATA_MBPS_PER_GBPS = 100
#: בלי קישור ידוע (NVMe): PCIe 4.0 x4 הוא כ-7.9GB/s, מעל זה אין דיסק בחדר.
ANY_DISK_MAX_MBPS = 8000

CHANGE_LABELS = {writefloor.SETTING: "סף מהירות כתיבה לדיסק",
                 drive_rates.START_SETTING: "קצב פתיחה של המולטיקאסט"}
CRC_LABEL = "אזהרת CRC למגירה"


class Refused(Exception):
    """פעולה שהמצב לא מאפשר. ‏`status` — קוד ה-HTTP, הטקסט — למסך."""

    def __init__(self, status: int, text: str) -> None:
        super().__init__(text)
        self.status = status


def enabled(conn: sqlite3.Connection) -> bool:
    return get_setting(conn, SETTING) == "true"


def _number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _json(raw: object) -> dict:
    try:
        got = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return got if isinstance(got, dict) else {}


def _collecting(conn: sqlite3.Connection) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM learning_runs WHERE state = 'collecting'"
                        " ORDER BY id DESC LIMIT 1").fetchone()


def _evidence(row: sqlite3.Row) -> str | None:
    """למה הגל **אינו** ראיה, או ‏None כשהוא כן."""
    stream, delivered = _json(row["stream"]), _json(row["delivered"])
    if not stream:
        return "המונים של השולח לא נקראו"
    if stream.get("ended") != "send_done":
        return "השולח לא סיים את הזרם"
    sent, loss, wall = stream.get("sent_bytes"), stream.get("loss_blocks"), stream.get("wall_s")
    if not _number(sent) or not _number(loss) or not _number(wall):
        return "המונים של השולח לא נקראו"
    if sent < drive_rates.MIN_SAMPLE_BYTES or wall < drive_rates.MIN_SAMPLE_S:
        return "הזרם קצר מדי למדידה"
    dropped = stream.get("dropped_clients")
    if not _number(dropped):
        return "יומן השולח לא נקרא"
    if dropped:
        return "השולח ניתק מקלט"
    if not any(isinstance(got, dict) and _number(got.get("done")) and got["done"] > 0
               for got in delivered.values()):
        return "אף מגירה לא סיימה"
    return None


def _loss(stream: dict) -> float:
    return stream["loss_blocks"] / -(-stream["sent_bytes"] // drive_rates.UDPCAST_BLOCK)


def _mbit(bits: int) -> str:
    return f"{bits // 10 ** 6} Mb/s"


def _in(sessions: list[str]) -> str:
    return ",".join("?" * len(sessions))


def _waves(conn: sqlite3.Connection, sessions: list[str]) -> list[tuple[dict, dict]]:
    """‏`(decision, stream)` לכל גל של הסבב שנרשם."""
    if not sessions:
        return []
    return [(_json(r["decision"]), _json(r["stream"])) for r in conn.execute(
        f"SELECT decision, stream FROM wave_rates WHERE session_id IN ({_in(sessions)})", sessions)]


def _proven(past: dict, stream: dict) -> int | None:
    """הקצב שהגל באמת עמד בו, בביטים: זרם שרץ מתחת ל-`KEEP_UP` מהתקרה לא הוכיח את התקרה."""
    rate = drive_rates.parse_bitrate(past.get("max_bitrate"))
    moved = stream.get("stream_bps")          # בייטים לשנייה (`SenderEngine.wave_outcome`)
    if rate is None or not _number(moved):
        return None
    return rate if moved * 8 >= drive_rates.KEEP_UP * rate else int(moved * 8)


def _model_base(conn: sqlite3.Connection, row: sqlite3.Row, sessions: list[str]) -> float | None:
    """חציון הדגם מדיסקים אחרים **מחוץ לסבב**: סבב אינו הבסיס של עצמו."""
    rows = conn.execute(
        f"SELECT serial, mbps FROM model_speeds WHERE model = ? AND serial != ?"
        f" AND session_id NOT IN ({_in(sessions)}) ORDER BY at DESC, rowid DESC LIMIT ?",
        (row["model"], row["serial"], *sessions, learn_models.WINDOW)).fetchall()
    if len(rows) < learn_models.MIN_SAMPLES or len({r["serial"] for r in rows}) < learn_models.MIN_SERIALS:
        return None
    return statistics.median(r["mbps"] for r in rows)


def _measured(conn: sqlite3.Connection, sessions: list[str]) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
    """‏`(נספרות, נדחו)` מבין מדידות הכתיבה של הסבב — ראו למעלה."""
    good: list[sqlite3.Row] = []
    bad: list[sqlite3.Row] = []
    registered = {r["mac"] for r in conn.execute("SELECT mac FROM machines")}
    for session in sessions:
        wave = conn.execute("SELECT decision, delivered FROM wave_rates WHERE session_id = ?",
                            (session,)).fetchone()
        past, delivered = (_json(wave["decision"]), _json(wave["delivered"])) if wave else ({}, {})
        roster = {(d.get("mac"), d.get("serial")) for d in past.get("roster") or [] if isinstance(d, dict)}
        links = {(r["mac"], r["serial"]): r["link_gbps"] for r in conn.execute(
            "SELECT mac, serial, link_gbps FROM port_history WHERE session_id = ?", (session,))}
        for row in conn.execute("SELECT * FROM model_speeds WHERE session_id = ?", (session,)).fetchall():
            key, got = (row["mac"], row["serial"]), delivered.get(row["mac"])
            link = links.get(key)
            limit = link * SATA_MBPS_PER_GBPS if _number(link) else ANY_DISK_MAX_MBPS
            base = _model_base(conn, row, sessions)
            real = (row["mac"] in registered and key in roster and isinstance(got, dict)
                    and _number(got.get("done")) and got["done"] > 0)
            sane = row["mbps"] <= limit and (base is None or row["mbps"] <= base * MODEL_OUTLIER)
            (good if real and sane else bad).append(row)
    return good, bad


def _bounded(current: int, target: int) -> int:
    return min(target, int(current * MAX_STEP))


def _floor_change(conn: sqlite3.Connection, sessions: list[str]) -> dict:
    current = writefloor.mbps(conn)
    rows, rejected = _measured(conn, sessions)
    serials = {r["serial"] for r in rows}
    base = {"key": writefloor.SETTING, "value_he": f"{current} MB/s"}
    if len(rows) < learn_models.MIN_SAMPLES or len(serials) < learn_models.MIN_SERIALS:
        return {**base, "why_he": f"אין מספיק מדידות: {len(rows)} מ-{len(serials)} דיסקים"}
    streams = [stream.get("stream_bps") for _, stream in _waves(conn, sessions)]
    if not streams or not all(_number(v) for v in streams):
        return {**base, "why_he": "קצב הזרם לא נמדד בכל השיכפולים"}
    speeds = [r["mbps"] for r in rows]
    median, slowest, stream = statistics.median(speeds), min(speeds), int(min(streams) / 10 ** 6)
    why = (f"חציון הדיסקים בחדר: {int(median)} MB/s, האיטי: {slowest} MB/s ({len(serials)} דיסקים),"
           f" הזרם בשרת: {stream} MB/s" + (f", {len(rejected)} מדידות נדחו" if rejected else ""))
    target = _bounded(current, min(int(median * FLOOR_OF_MEDIAN), int(slowest * drive_rates.MARGIN),
                                   stream, writefloor.MAXIMUM))
    if target <= current:
        return {**base, "why_he": why}
    return {**base, "value": str(target), "previous_he": f"{current} MB/s",
            "value_he": f"{target} MB/s", "why_he": why}


def _start_change(conn: sqlite3.Connection, sessions: list[str]) -> dict:
    current = drive_rates.learned_start(conn) or drive_rates.START_BITRATE
    base = {"key": drive_rates.START_SETTING, "value_he": _mbit(current)}
    waves = _waves(conn, sessions)
    proven = [_proven(past, stream) for past, stream in waves]
    if not waves or None in proven:
        return {**base, "why_he": "קצב הזרם לא נמדד בכל השיכפולים"}
    worst = max(_loss(stream) for _, stream in waves)
    if worst > drive_rates.LOSS_DOWN_RATIO:
        return {**base, "why_he": f"אבדן {worst:.2%} בשיכפול אחד לפחות"}
    step = drive_rates.REFINE_STEP
    target = _bounded(current, min(proven)) // step * step
    why = f"הזרם עמד ב-{_mbit(target)} ב-{len(waves)} שיכפולים, אבדן עד {worst:.2%}"
    if target <= current:
        return {**base, "why_he": why}
    return {**base, "value": str(target // 10 ** 6), "previous_he": _mbit(current),
            "value_he": _mbit(target), "why_he": why}


def _crc_row(conn: sqlite3.Connection, sessions: list[str]) -> dict:
    limit = crcwarn.threshold(conn)
    rises = conn.execute(f"SELECT COUNT(*) AS n FROM port_history WHERE crc_delta > 0"
                         f" AND session_id IN ({_in(sessions)})", sessions).fetchone()["n"]
    return {"key": crcwarn.SETTING, "label_he": CRC_LABEL,
            "value_he": f"{limit} שגיאות" if limit else "כבויה",
            "why_he": (f"עליית CRC ב-{rises} מדידות: מוצג בחלון מיפוי חיבורים, והסף נשאר למנהל"
                       if rises else "אין היסטוריית CRC שמצדיקה סף")}


def _levels(conn: sqlite3.Connection, sessions: list[str]) -> dict:
    """מה שנמדד בסבב, בשלוש הרמות. ‏`null` בשדה = לא נמדד, לא אפס."""
    disks = {"count": 0, "min_mbps": None, "max_mbps": None}
    links: dict[str, int] = {}
    machines, ports = set(), set()
    network = {"top_mbit": None, "loss": None, "interface": None}
    if not sessions:
        return {"disks": disks, "machines": {"count": 0, "ports": 0, "links": links}, "network": network}
    speeds = _measured(conn, sessions)[0]
    if speeds:
        got = [r["mbps"] for r in speeds]
        disks = {"count": len({r["serial"] for r in speeds}), "min_mbps": min(got), "max_mbps": max(got)}
    seen = set()
    for r in conn.execute(f"SELECT mac, port, link_gbps FROM port_history WHERE session_id IN ({_in(sessions)})"
                          " AND (link_gbps IS NOT NULL OR crc_delta IS NOT NULL)", sessions):
        machines.add(r["mac"])
        ports.add((r["mac"], r["port"]))
        if r["link_gbps"] is not None and (r["mac"], r["port"]) not in seen:
            seen.add((r["mac"], r["port"]))
            key = f"{r['link_gbps']:g}"
            links[key] = links.get(key, 0) + 1
    waves = [(p, s) for p, s in _waves(conn, sessions) if _number(s.get("loss_blocks"))
             and _number(s.get("sent_bytes")) and s["sent_bytes"] > 0]
    if waves:
        proven = [got for got in (_proven(p, s) for p, s in waves) if got is not None]
        network = {"top_mbit": max(proven) // 10 ** 6 if proven else None,
                   "loss": round(max(_loss(s) for _, s in waves), 5),
                   "interface": next(((p.get("link") or {}).get("interface") for p, _ in waves
                                      if (p.get("link") or {}).get("interface")), None)}
    return {"disks": disks, "machines": {"count": len(machines), "ports": len(ports), "links": links},
            "network": network}


def _finish(conn: sqlite3.Connection, run: sqlite3.Row, waves: list[sqlite3.Row]) -> None:
    sessions = [w["session_id"] for w in waves]
    good = [w["session_id"] for w in waves if w["ok"]]
    learned = len(good) >= run["required"]
    rows = [_floor_change(conn, good), _start_change(conn, good)] if learned else []
    changes = [r for r in rows if "value" in r]
    rejected = _measured(conn, good)[1] if learned else []
    summary = {"levels": _levels(conn, sessions),
               "unchanged": ([{**r, "label_he": CHANGE_LABELS[r["key"]]} for r in rows if "value" not in r]
                             + [_crc_row(conn, good)]) if learned else []}
    at = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        done = conn.execute(
            "UPDATE learning_runs SET state = ?, ended_at = ?, summary_json = ?"
            " WHERE id = ? AND state = 'collecting'",
            ("done" if learned else "not_learned", at, json.dumps(summary, ensure_ascii=False), run["id"]))
        if done.rowcount != 1:
            return                # תהליכון אחר סגר את הסבב, או שהמתג כובה בינתיים
        for change in changes:
            key = change["key"]
            conn.execute("UPDATE learning_changes SET state = 'replaced', ended_at = ?"
                         " WHERE key = ? AND state = 'applied'", (at, key))
            conn.execute(
                "INSERT INTO learning_changes (run_id, key, previous, value, previous_he, value_he,"
                " why_he, at) VALUES (?,?,?,?,?,?,?,?)",
                (run["id"], key, get_setting(conn, key), change["value"], change["previous_he"],
                 change["value_he"], change["why_he"], at))
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?)"
                         " ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                         (key, change["value"]))
    if not learned:
        journal(conn, "learning_not_learned", f"{len(good)}/{run['required']}")
        return
    journal(conn, "learning_done", f"{len(good)}/{run['required']} changes={len(changes)}")
    if rejected:
        journal(conn, "learning_rejected", f"{len(rejected)} "
                + " ".join(sorted({f"{r['mac']}:{r['mbps']}MB/s" for r in rejected})))
    for change in changes:
        journal(conn, "learning_changed", f"{change['key']}: {change['previous_he']} -> "
                                          f"{change['value_he']} ({change['why_he']})")


def record_wave(conn: sqlite3.Connection, session_id: str) -> None:
    """נקרא משני הקצוות של גל: כשהשולח סיים (`app.on_sender_event`) וכשהחדר סגר אותו
    (`room`). הסדר ביניהם אינו מובטח, ולכן מי שמגיע שני הוא שסופר: בלי המונים של השולח ובלי
    הספירה של החדר אין עדיין מה לשפוט, וזה אינו כישלון של הגל. גל שאינו של החדר (אין לו
    ‏`delivered`) אינו נספר. חוזר על עצמו בבטחה."""
    try:
        run = _collecting(conn)
        if run is None:
            return
        row = conn.execute("SELECT decision, stream, delivered FROM wave_rates WHERE session_id = ?",
                           (session_id,)).fetchone()
        if row is None or row["stream"] is None or row["delivered"] is None \
                or _json(row["decision"]).get("direct"):
            return
        why = _evidence(row)
        _settle(conn)
        with _write_lock, writing(conn):
            added = conn.execute(
                "INSERT OR IGNORE INTO learning_waves (run_id, session_id, ok, why_he, at)"
                " VALUES (?,?,?,?,?)", (run["id"], session_id, int(why is None), why, now_iso())).rowcount
        if not added:
            return
        waves = conn.execute("SELECT session_id, ok FROM learning_waves WHERE run_id = ? ORDER BY at, rowid",
                             (run["id"],)).fetchall()
        journal(conn, "learning_wave", f"{len(waves)}/{run['required']} {why or 'ok'}", round_id=session_id)
        if len(waves) >= run["required"]:
            _finish(conn, run, waves)
    except Exception:             # noqa: BLE001 — הלמידה אינה עוצרת סיום גל
        log.exception("learning from %s failed", session_id)


def settings_written(conn: sqlite3.Connection, written: dict[str, str], was_on: bool, user: str) -> None:
    """נקרא אחרי `POST /settings`. הדלקה פותחת סבב, כיבוי סוגר אותו (ההגדרות נשארות),
    ושינוי ידני של הגדרה שהותאמה גובר ומסיר ממנה את התג."""
    at = now_iso()
    overridden = [key for key, value in written.items() if conn.execute(
        "SELECT 1 FROM learning_changes WHERE key = ? AND state = 'applied' AND value != ?",
        (key, value)).fetchone()]
    want = written.get(SETTING)
    _settle(conn)
    with _write_lock, writing(conn):
        for key in overridden:
            conn.execute("UPDATE learning_changes SET state = 'overridden', ended_at = ?, ended_by = ?"
                         " WHERE key = ? AND state = 'applied'", (at, user, key))
        if want == "true" and not was_on:
            conn.execute("UPDATE learning_runs SET state = 'cancelled', ended_at = ?"
                         " WHERE state = 'collecting'", (at,))
            conn.execute("INSERT INTO learning_runs (required, started_at, started_by) VALUES (?,?,?)",
                         (REQUIRED_WAVES, at, user))
        elif want == "false":
            conn.execute("UPDATE learning_runs SET state = 'cancelled', ended_at = ?"
                         " WHERE state = 'collecting'", (at,))
    for key in overridden:
        journal(conn, "learning_overridden", f"{key}={written[key]}", user)


def _restore(conn: sqlite3.Connection, change: sqlite3.Row, at: str, user: str) -> bool:
    """מחזיר בתוך טרנזאקציה פתוחה. ‏False — הערך כבר אינו מה שהלמידה כתבה."""
    if get_setting(conn, change["key"]) != change["value"]:
        return False
    if change["previous"] is None:
        conn.execute("DELETE FROM settings WHERE key = ?", (change["key"],))
    else:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (change["previous"], change["key"]))
    conn.execute("UPDATE learning_changes SET state = 'reverted', ended_at = ?, ended_by = ? WHERE id = ?",
                 (at, user, change["id"]))
    return True


def revert(conn: sqlite3.Connection, change_id: int | None, user: str) -> int:
    """מחזיר שינוי אחד, או את כולם (‏`None`). מחזיר כמה הוחזרו; נכשל בקול (`Refused`)."""
    where, args = ("id = ? AND state = 'applied'", (change_id,)) if change_id is not None \
        else ("state = 'applied'", ())
    at, done, stale = now_iso(), [], []
    _settle(conn)
    with _write_lock, writing(conn):
        for change in conn.execute(f"SELECT * FROM learning_changes WHERE {where}", args).fetchall():
            (done if _restore(conn, change, at, user) else stale).append(change)
    if stale and change_id is not None:
        raise Refused(409, "הערך השתנה מאז שהלמידה כתבה אותו. לא הוחזר דבר")
    if not done and change_id is not None:
        raise Refused(404, "אין שינוי פעיל כזה")
    for change in done:
        journal(conn, "learning_reverted", f"{change['key']}: {change['value_he']} -> "
                                           f"{change['previous_he']}", user)
    return len(done)


def _wave_rows(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute("SELECT session_id, ok, why_he, at FROM learning_waves WHERE run_id = ?"
                        " ORDER BY at, rowid", (run_id,)).fetchall()


def _running(conn: sqlite3.Connection, seen: set[str]) -> bool:
    return any(r["wave_session_id"] not in seen for r in conn.execute(
        "SELECT wave_session_id FROM room_rounds WHERE state = 'active'"
        " AND wave_session_id IS NOT NULL"))


def status(conn: sqlite3.Connection) -> dict:
    """מה שחלון הלמידה מציג. קריאה בלבד."""
    run = conn.execute("SELECT * FROM learning_runs WHERE state != 'cancelled'"
                       " ORDER BY id DESC LIMIT 1").fetchone()
    out: dict = {"enabled": enabled(conn), "required": REQUIRED_WAVES, "run": None, "changes": [],
                 "adjusted_keys": []}
    run_id = run["id"] if run else None
    if run is not None:
        waves = _wave_rows(conn, run_id)
        ids = [w["session_id"] for w in waves]
        summary = _json(run["summary_json"])
        out["run"] = {
            "id": run_id, "state": run["state"], "required": run["required"],
            "started_at": run["started_at"], "ended_at": run["ended_at"],
            "attempts": len(waves), "learned": sum(w["ok"] for w in waves),
            "waves": [{"at": w["at"], "ok": bool(w["ok"]), "why_he": w["why_he"]} for w in waves],
            "running": run["state"] == "collecting" and _running(conn, set(ids)),
            "levels": summary.get("levels") or _levels(conn, ids),
            "unchanged": summary.get("unchanged") or []}
    rows = conn.execute("SELECT * FROM learning_changes WHERE state = 'applied' OR run_id = ?"
                        " ORDER BY id", (run_id,)).fetchall()
    out["changes"] = [{"id": r["id"], "key": r["key"], "label_he": CHANGE_LABELS.get(r["key"], r["key"]),
                       "previous_he": r["previous_he"], "value_he": r["value_he"], "why_he": r["why_he"],
                       "state": r["state"], "at": r["at"]} for r in rows]
    out["adjusted_keys"] = sorted({r["key"] for r in rows if r["state"] == "applied"})
    return out


def machine_states(conn: sqlite3.Connection) -> dict[str, dict] | None:
    """‏`{mac: {"state", "waves", "ports", "required"}}` לכל מכונה שיש עליה מה לומר.
    ‏`state`: ‏`learned` / ‏`started` / ‏`dismissed`. מכונה שאינה ברשימה = הצעה (`offer`).
    ‏None — לא נקרא, ואז אין הצעה: רשימת המכונות אינה נופלת בגלל הלמידה."""
    try:
        measured = {r["mac"]: r for r in conn.execute(
            "SELECT mac, COUNT(DISTINCT session_id) AS waves, COUNT(DISTINCT port) AS ports"
            " FROM port_history WHERE link_gbps IS NOT NULL OR crc_delta IS NOT NULL GROUP BY mac")}
        marks = {r["mac"]: r["state"] for r in conn.execute("SELECT mac, state FROM learning_machines")}
    except sqlite3.Error:
        log.exception("reading the machines' learning state failed")
        return None
    out = {}
    for mac in measured.keys() | marks.keys():
        row = measured.get(mac)
        waves, ports = (row["waves"], row["ports"]) if row else (0, 0)
        state = "learned" if waves >= REQUIRED_WAVES else marks.get(mac, "offer")
        out[mac] = {"state": state, "waves": waves, "ports": ports, "required": REQUIRED_WAVES}
    return out


def answer_offer(conn: sqlite3.Connection, mac: str, action: str, user: str) -> None:
    """‏`start` / ‏`dismiss` על הצעת הלמידה למחשב. אינו נוגע במתג ואינו כותב לאף דיסק."""
    state = {"start": "started", "dismiss": "dismissed"}.get(action)
    if state is None:
        raise Refused(400, "פעולה לא מוכרת: start או dismiss")
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO learning_machines (mac, state, at, by) VALUES (?,?,?,?)"
                     " ON CONFLICT (mac) DO UPDATE SET state = excluded.state, at = excluded.at,"
                     " by = excluded.by", (mac, state, now_iso(), user))
    journal(conn, "learning_machine", f"{mac} {state}", user, machine=mac)
