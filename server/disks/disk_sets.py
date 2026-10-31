"""סטים של דיסקים (#1857, מוקאפ 04, נדב 24/10).

סט הוא קבוצת דיסקים עם שם וקידומת: "קוריאים" / ``xx`` -- מדבקות ``xx-INS`` (מרצה),
``xx-1`` … ``xx-29``. **השם הולך עם הדיסק לפי המספר הסידורי, לא עם המגירה**, והוא תצוגה
בלבד (לא נכתב ל-Windows). המטרה (נדב): "שאדע דיסק מסט xx לא תקין ואותו להחליף".

* סטים ושיוך: ``disk_sets`` / ``disk_set_members`` (``serial`` מפתח -- דיסק בסט אחד; ‏``set_id+suffix``
  ייחודי -- שני דיסקים לא יקבלו אותו שם). דיסק שיצא (הוחלף/הוסר) נשמר ב-``disk_set_history``.
* ``disk_inventory`` -- כל דיסק שנראה ב-hello של משכפל/מחשב בנייה (דגם, גודל, מקום אחרון): בלעדיו אי
  אפשר להראות דיסק שכבר יצא מהמשכפל.
* בריאות לכל דיסק -- **ממה שכבר נאסף** (``disk_events``, ‏``disk_failures``, ‏``room_rounds``), בלי איסוף
  חדש. ‏"לא נבדק" אינו "תקין" (עיקרון 5): דיסק בלי שום אירוע SMART הוא ``unchecked``.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from datetime import datetime, timezone

from server.db import NET_SEEN_MIN_INTERVAL_SECONDS, _settle, _write_lock, journal, now_iso, writing

log = logging.getLogger("imagesrv.disk_sets")

PREFIX_RE = re.compile(r"^[A-Za-z0-9]{1,8}$")
NAME_MAX = 40
STUDENTS_MAX = 200
INSTRUCTOR = "INS"
#: סדר החומרה: אדום ← כתום ← לא נבדק ← תקין (הרשימה מראה קודם מה שדורש טיפול).
ORDER = {"red": 0, "orange": 1, "unchecked": 2, "ok": 3}


class SetError(ValueError):
    """שגיאת קלט/התנגשות -- ההודעה בעברית, והקונסולה מציגה אותה כמות שהיא."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def suffix_ok(suffix: object) -> bool:
    return isinstance(suffix, str) and (
        suffix == INSTRUCTOR
        or (suffix.isascii() and suffix.isdigit() and not suffix.startswith("0") and len(suffix) <= 4))


def label(prefix: str, suffix: str) -> str:
    return f"{prefix}-{suffix}"


def _norm_suffix(raw: object) -> str:
    text = str(raw or "").strip()
    text = text.upper() if text.upper() == INSTRUCTOR else text
    if not suffix_ok(text):
        raise SetError(f"מספר לא תקין: {text!r} (‏INS או מספר שלם בלי אפס מוביל)")
    return text


def _serial(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip() or len(raw.strip()) > 64:
        raise SetError("מספר סידורי חסר או ארוך מדי")
    return raw.strip()


# ---------------------------------------------------------------- inventory

def note_hello(conn: sqlite3.Connection, mac: str, disks: object) -> None:
    """רושם את הדיסקים של hello (משכפל/בנייה). ‏SELECT קודם -- hello שלא שינה דבר ובן פחות מ-15ש'
    אינו כותב (כמו ``net_seen``, #136)."""
    if not isinstance(disks, list):
        return
    now = datetime.now(timezone.utc)
    for d in disks:
        if not isinstance(d, dict) or not isinstance(d.get("serial"), str) or not d["serial"].strip():
            continue
        serial = d["serial"].strip()[:64]
        model = d.get("model") if isinstance(d.get("model"), str) else None
        size = d.get("size_bytes") if isinstance(d.get("size_bytes"), int) and not isinstance(d.get("size_bytes"), bool) else None
        port = d.get("port") if isinstance(d.get("port"), int) and not isinstance(d.get("port"), bool) else None
        row = conn.execute("SELECT model, size_bytes, mac, port, last_seen FROM disk_inventory WHERE serial = ?",
                           (serial,)).fetchone()
        if row is not None and (row["model"], row["size_bytes"], row["mac"], row["port"]) == (model, size, mac, port):
            try:
                age = (now - datetime.fromisoformat(row["last_seen"])).total_seconds()
            except ValueError:
                age = NET_SEEN_MIN_INTERVAL_SECONDS
            if 0 <= age < NET_SEEN_MIN_INTERVAL_SECONDS * 4:
                continue
        ts = now_iso()
        _settle(conn)
        with _write_lock, writing(conn):
            conn.execute(
                "INSERT INTO disk_inventory (serial, model, size_bytes, mac, port, first_seen, last_seen)"
                " VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (serial) DO UPDATE SET"
                " model = COALESCE(excluded.model, model), size_bytes = COALESCE(excluded.size_bytes, size_bytes),"
                " mac = excluded.mac, port = excluded.port, last_seen = excluded.last_seen",
                (serial, model, size, mac, port, ts, ts))


# ---------------------------------------------------------------- sets

def _set_row(conn: sqlite3.Connection, set_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM disk_sets WHERE id = ?", (set_id,)).fetchone()
    if row is None:
        raise SetError("הסט לא נמצא", 404)
    return row


def planned(row: sqlite3.Row) -> list[str]:
    """המדבקות המתוכננות של הסט, בסדר: INS, 1 … N."""
    return ([INSTRUCTOR] if row["instructor"] else []) + [str(i) for i in range(1, row["students"] + 1)]


def create(conn: sqlite3.Connection, name: object, prefix: object, students: object, instructor: object,
           user: str) -> int:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > NAME_MAX:
        raise SetError(f"שם הסט: טקסט של 1–{NAME_MAX} תווים")
    if not isinstance(prefix, str) or not PREFIX_RE.match(prefix.strip()):
        raise SetError("קידומת: 1–8 אותיות לטיניות או ספרות")
    if isinstance(students, bool) or not isinstance(students, int) or not 0 <= students <= STUDENTS_MAX:
        raise SetError(f"כמה דיסקים לתלמידים: מספר שלם 0–{STUDENTS_MAX}")
    if not isinstance(instructor, bool):
        raise SetError("דיסק מרצה: true או false")
    if students == 0 and not instructor:
        raise SetError("סט בלי דיסקים אינו סט")
    name, prefix = name.strip(), prefix.strip()
    clash = conn.execute("SELECT name, prefix FROM disk_sets WHERE name = ? OR prefix = ? COLLATE NOCASE",
                         (name, prefix)).fetchone()
    if clash is not None:
        raise SetError(f"כבר יש סט בשם {clash['name']!r} / קידומת {clash['prefix']!r}", 409)
    _settle(conn)
    with _write_lock, writing(conn):
        cur = conn.execute(
            "INSERT INTO disk_sets (name, prefix, students, instructor, created_at, created_by)"
            " VALUES (?, ?, ?, ?, ?, ?)", (name, prefix, students, int(instructor), now_iso(), user))
        set_id = cur.lastrowid
    journal(conn, "disk_set_created", f"{name} · {prefix} · {students}{'+INS' if instructor else ''}", user)
    return set_id


# ---------------------------------------------------------------- health

def _written_at(conn: sqlite3.Connection) -> dict[str, str]:
    """הכתיבה התקינה האחרונה לכל סידורי: סוף הסבב שבו נכתב (‏`written_serials`)."""
    out: dict[str, str] = {}
    for r in conn.execute("SELECT written_serials, COALESCE(closed_at, created_at) AS at FROM room_rounds"):
        try:
            serials = json.loads(r["written_serials"] or "[]")
        except ValueError:
            continue
        for s in serials if isinstance(serials, list) else []:
            if isinstance(s, str) and r["at"] > out.get(s, ""):
                out[s] = r["at"]
    return out


def _current_places(conn: sqlite3.Connection) -> dict[str, tuple[str, int | None]]:
    """איפה כל דיסק **עכשיו** -- מה-hello האחרון של כל מכונה: ``{serial: (mac, port)}``."""
    out: dict[str, tuple[str, int | None]] = {}
    for r in conn.execute("SELECT mac, disks_json FROM net_devices WHERE disks_json IS NOT NULL"):
        try:
            disks = json.loads(r["disks_json"])
        except ValueError:
            continue
        for d in disks if isinstance(disks, list) else []:
            if isinstance(d, dict) and isinstance(d.get("serial"), str):
                out[d["serial"]] = (r["mac"], d.get("port") if isinstance(d.get("port"), int) else None)
    return out


def _machine_names(conn: sqlite3.Connection) -> dict[str, str]:
    role = {"cloner": "משכפל", "build": "מחשב בנייה", "classroom": "תחנה"}
    return {r["mac"]: f"{role.get(r['role'], '')} {r['suffix']}".strip()
            for r in conn.execute("SELECT m.mac, m.suffix, g.role FROM machines m JOIN groups g ON g.id = m.group_id")}


def health(conn: sqlite3.Connection, serials: list[str]) -> dict[str, dict]:
    """מצב כל סידורי: ``state`` (red/orange/ok/unchecked), ``reason`` (קוד, כמו ב-SMART), מתי נכשל, מתי נכתב.

    אדום = אירוע SMART ``fail`` אחרון, או כשל פתוח של דיסק (``disk_failures`` בלי ``cleared_at``,
    ‏cause=disk). כתום = ``warn``, או כשל פתוח אחר (כבל…). תקין = ``ok`` ואין כשל פתוח. אין אירוע = לא נבדק."""
    good = _written_at(conn)
    out: dict[str, dict] = {}
    for serial in serials:
        ev = conn.execute(
            "SELECT verdict, reason, updated_at FROM disk_events WHERE serial = ? ORDER BY updated_at DESC LIMIT 1",
            (serial,)).fetchone()
        fails = conn.execute(
            "SELECT cause, at, cleared_at FROM disk_failures WHERE serial = ? ORDER BY at DESC", (serial,)).fetchall()
        open_fail = [f for f in fails if f["cleared_at"] is None]
        verdict = ev["verdict"] if ev else None
        if verdict == "fail" or any(f["cause"] == "disk" for f in open_fail):
            state = "red"
        elif verdict == "warn" or open_fail:
            state = "orange"
        elif verdict == "ok":
            state = "ok"
        else:
            state = "unchecked"
        reason = ev["reason"] if ev and verdict in ("fail", "warn") else None
        if reason is None and open_fail:
            reason = open_fail[0]["cause"]
        out[serial] = {"state": state, "reason": reason,
                       "last_good_write": good.get(serial), "last_fail": fails[0]["at"] if fails else None,
                       "checked_at": ev["updated_at"] if ev else None}
    return out


# ---------------------------------------------------------------- views

def _disk_view(conn, serial, inv, member, set_by_id, hl, places, names) -> dict:
    mac, port = places.get(serial, (None, None))
    here = mac is not None
    if not here and inv is not None:
        mac, port = inv["mac"], inv["port"]
    s = set_by_id.get(member["set_id"]) if member else None
    return {
        "serial": serial,
        "model": inv["model"] if inv else None,
        "size_bytes": inv["size_bytes"] if inv else None,
        "set_id": s["id"] if s else None,
        "set_name": s["name"] if s else None,
        "label": label(s["prefix"], member["suffix"]) if s else None,
        "suffix": member["suffix"] if s else None,
        "state": hl["state"], "reason": hl["reason"],
        "last_good_write": hl["last_good_write"], "last_fail": hl["last_fail"],
        "in_machine": here,
        "mac": mac, "machine": names.get(mac) if mac else None, "port": port,
        "last_seen": inv["last_seen"] if inv else None,
    }


def disks(conn: sqlite3.Connection) -> list[dict]:
    """כל דיסק שהשרת מכיר: מהמלאי, מאירועי SMART, ומהסטים."""
    serials = {r[0] for r in conn.execute("SELECT serial FROM disk_inventory")}
    serials |= {r[0] for r in conn.execute("SELECT DISTINCT serial FROM disk_events WHERE serial IS NOT NULL AND serial != ''")}
    serials |= {r[0] for r in conn.execute("SELECT serial FROM disk_set_members")}
    order = sorted(serials)
    inv = {r["serial"]: r for r in conn.execute("SELECT * FROM disk_inventory")}
    members = {r["serial"]: r for r in conn.execute("SELECT * FROM disk_set_members")}
    sets = {r["id"]: r for r in conn.execute("SELECT * FROM disk_sets")}
    hl = health(conn, order)
    places, names = _current_places(conn), _machine_names(conn)
    return [_disk_view(conn, s, inv.get(s), members.get(s), sets, hl[s], places, names) for s in order]


def sort_key(d: dict):
    suffix = d["suffix"] or ""
    num = 0 if suffix == INSTRUCTOR else (int(suffix) if suffix.isdigit() else 10**6)
    return (ORDER[d["state"]], num, d["serial"])


def list_sets(conn: sqlite3.Connection) -> list[dict]:
    all_disks = {d["serial"]: d for d in disks(conn)}
    out = []
    for row in conn.execute("SELECT * FROM disk_sets ORDER BY name").fetchall():
        mine = {m["suffix"]: all_disks[m["serial"]] for m in
                conn.execute("SELECT serial, suffix FROM disk_set_members WHERE set_id = ?", (row["id"],))
                if m["serial"] in all_disks}
        plan = planned(row)
        counts = {"red": 0, "orange": 0, "unchecked": 0, "ok": 0}
        for d in mine.values():
            counts[d["state"]] += 1
        free = next((s for s in plan if s != INSTRUCTOR and s not in mine), None)
        out.append({"id": row["id"], "name": row["name"], "prefix": row["prefix"],
                    "students": row["students"], "instructor": bool(row["instructor"]),
                    "capacity": len(plan), "assigned": len(mine), "counts": counts,
                    "grid": [mine[s]["state"] if s in mine else "empty" for s in plan],
                    "plan": plan, "next_free": label(row["prefix"], free) if free else None})
    return out


def members(conn: sqlite3.Connection, set_id: int) -> dict:
    row = _set_row(conn, set_id)
    by_serial = {d["serial"]: d for d in disks(conn)}
    rows = [by_serial[m["serial"]] for m in
            conn.execute("SELECT serial FROM disk_set_members WHERE set_id = ?", (set_id,)) if m["serial"] in by_serial]
    rows.sort(key=sort_key)
    return {"id": set_id, "name": row["name"], "prefix": row["prefix"], "disks": rows}


def rounds(conn: sqlite3.Connection, limit: int = 40) -> list[dict]:
    """ההרצות שאפשר לשייך מהן: (סבב-גל, מכונה) עם הדיסקים שבו לפי סדר המגירות ותוצאת הכתיבה."""
    out = []
    names = _machine_names(conn)
    in_set = {r["serial"]: label(r["prefix"], r["suffix"]) for r in conn.execute(
        "SELECT m.serial, m.suffix, s.prefix FROM disk_set_members m JOIN disk_sets s ON s.id = m.set_id")}
    inv = {r["serial"]: r for r in conn.execute("SELECT * FROM disk_inventory")}
    groups = conn.execute(
        "SELECT e.session_id, e.mac, MAX(e.updated_at) AS at, s.image_id FROM disk_events e"
        " LEFT JOIN sessions s ON s.id = e.session_id WHERE e.serial IS NOT NULL AND e.serial != ''"
        " GROUP BY e.session_id, e.mac ORDER BY at DESC LIMIT ?", (limit,)).fetchall()
    for g in groups:
        targets: dict[str, str] = {}
        m = conn.execute("SELECT targets_json FROM session_members WHERE session_id = ? AND mac = ?",
                         (g["session_id"], g["mac"])).fetchone()
        try:
            for t in json.loads(m["targets_json"] or "[]") if m else []:
                if isinstance(t, dict) and t.get("dev"):
                    targets[t["dev"]] = t.get("state") or ""
        except ValueError:
            pass
        evs = conn.execute(
            "SELECT disk, port, serial FROM disk_events WHERE session_id = ? AND mac = ? AND serial IS NOT NULL"
            " AND serial != '' ORDER BY port, disk", (g["session_id"], g["mac"])).fetchall()
        out.append({"session_id": g["session_id"], "mac": g["mac"], "machine": names.get(g["mac"], g["mac"]),
                    "at": g["at"], "image_id": g["image_id"],
                    "drawers": [{"port": e["port"], "serial": e["serial"],
                                 "model": inv[e["serial"]]["model"] if e["serial"] in inv else None,
                                 "written": targets.get(e["disk"]) == "done",
                                 "result": targets.get(e["disk"]) or None,
                                 "in_set": in_set.get(e["serial"])} for e in evs]})
    return out


# ---------------------------------------------------------------- changes

def assign(conn: sqlite3.Connection, set_id: int, items: object, user: str) -> int:
    """משייך דיסקים לסט (אחרי אימות מלא, או כלום). ‏``items``: ``[{"serial", "suffix"}]``."""
    row = _set_row(conn, set_id)
    if not isinstance(items, list) or not items:
        raise SetError("אין דיסקים לשייך")
    seen_serial, seen_suffix, clean = set(), set(), []
    for it in items:
        if not isinstance(it, dict):
            raise SetError("פריט לא תקין")
        serial, suffix = _serial(it.get("serial")), _norm_suffix(it.get("suffix"))
        if serial in seen_serial:
            raise SetError(f"הדיסק {serial} מופיע פעמיים")
        if suffix in seen_suffix:
            raise SetError(f"השם {label(row['prefix'], suffix)} מופיע פעמיים", 409)
        seen_serial.add(serial)
        seen_suffix.add(suffix)
        clean.append((serial, suffix))
    for serial, suffix in clean:
        other = conn.execute("SELECT serial FROM disk_set_members WHERE set_id = ? AND suffix = ?",
                             (set_id, suffix)).fetchone()
        if other is not None and other["serial"] != serial:
            raise SetError(f"{label(row['prefix'], suffix)} כבר שייך לדיסק אחר", 409)
        cur = conn.execute("SELECT m.set_id, s.name, s.prefix, m.suffix FROM disk_set_members m"
                           " JOIN disk_sets s ON s.id = m.set_id WHERE m.serial = ?", (serial,)).fetchone()
        if cur is not None and cur["set_id"] != set_id:
            raise SetError(f"הדיסק {serial} כבר בסט {cur['name']} ({label(cur['prefix'], cur['suffix'])}) — להוציא אותו משם קודם", 409)
    ts = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        for serial, suffix in clean:
            conn.execute("DELETE FROM disk_set_members WHERE serial = ?", (serial,))
            conn.execute("INSERT INTO disk_set_members (serial, set_id, suffix, assigned_at, assigned_by)"
                         " VALUES (?, ?, ?, ?, ?)", (serial, set_id, suffix, ts, user))
    journal(conn, "disk_set_assigned",
            f"{row['name']}: " + ", ".join(f"{label(row['prefix'], sfx)}={ser}" for ser, sfx in clean), user)
    return len(clean)


def replace(conn: sqlite3.Connection, set_id: int, suffix: object, new_serial: object, user: str) -> None:
    """הדיסק הישן יוצא מהסט (נשמר בהיסטוריה) והשם עובר לחדש -- הכרעת ברירת המחדל של המוקאפ."""
    row = _set_row(conn, set_id)
    suffix, new = _norm_suffix(suffix), _serial(new_serial)
    old = conn.execute("SELECT serial FROM disk_set_members WHERE set_id = ? AND suffix = ?", (set_id, suffix)).fetchone()
    if old is None:
        raise SetError(f"{label(row['prefix'], suffix)} אינו משויך לאף דיסק", 404)
    if old["serial"] == new:
        raise SetError("הדיסק החדש הוא הדיסק הנוכחי")
    taken = conn.execute("SELECT m.suffix, s.prefix, s.name FROM disk_set_members m JOIN disk_sets s ON s.id = m.set_id"
                         " WHERE m.serial = ?", (new,)).fetchone()
    if taken is not None:
        raise SetError(f"הדיסק החדש כבר בסט {taken['name']} ({label(taken['prefix'], taken['suffix'])}) — להוציא אותו משם קודם", 409)
    ts = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO disk_set_history (serial, set_id, suffix, left_at, reason, replaced_by, left_by)"
                     " VALUES (?, ?, ?, ?, 'replaced', ?, ?)", (old["serial"], set_id, suffix, ts, new, user))
        conn.execute("DELETE FROM disk_set_members WHERE serial = ?", (old["serial"],))
        conn.execute("INSERT INTO disk_set_members (serial, set_id, suffix, assigned_at, assigned_by)"
                     " VALUES (?, ?, ?, ?, ?)", (new, set_id, suffix, ts, user))
    journal(conn, "disk_set_replaced", f"{label(row['prefix'], suffix)}: {old['serial']} -> {new}", user)


def remove(conn: sqlite3.Connection, serial: object, user: str) -> None:
    serial = _serial(serial)
    m = conn.execute("SELECT m.set_id, m.suffix, s.prefix FROM disk_set_members m JOIN disk_sets s ON s.id = m.set_id"
                     " WHERE m.serial = ?", (serial,)).fetchone()
    if m is None:
        raise SetError("הדיסק אינו בסט", 404)
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO disk_set_history (serial, set_id, suffix, left_at, reason, left_by)"
                     " VALUES (?, ?, ?, ?, 'removed', ?)", (serial, m["set_id"], m["suffix"], now_iso(), user))
        conn.execute("DELETE FROM disk_set_members WHERE serial = ?", (serial,))
    journal(conn, "disk_set_removed", f"{label(m['prefix'], m['suffix'])}: {serial}", user)


def labels_for(conn: sqlite3.Connection, disks_: object) -> dict[str, dict]:
    """לתשובת ה-hello: ``{serial: {"set", "label", "text"}}`` לדיסקים שהמכונה שלחה ושיש להם סט.
    דיסק בלי סט אינו מופיע ("בלי סט" מוצג כמו היום)."""
    serials = [d["serial"] for d in disks_ if isinstance(d, dict) and isinstance(d.get("serial"), str)] \
        if isinstance(disks_, list) else []
    if not serials:
        return {}
    marks = ",".join("?" * len(serials))
    out = {}
    for r in conn.execute(
            f"SELECT m.serial, m.suffix, s.name, s.prefix FROM disk_set_members m JOIN disk_sets s ON s.id = m.set_id"
            f" WHERE m.serial IN ({marks})", serials):
        lab = label(r["prefix"], r["suffix"])
        out[r["serial"]] = {"set": r["name"], "label": lab, "text": f"{r['name']} · {lab}"}
    return out
