"""#3185: מה השרת יודע על דיסק -- עמודת "מגמה" בדף "דיסקים" וכרטיס הדיסק שנפתח משורה.

**המגמה** היא של מונה SMART מצטבר שעלה: סקטורים מוחלפים (ATA 5), סקטורים ממתינים (ATA 197), ושגיאות מדיה ב-NVMe.
מונה שלא עלה הוא מידע בלבד (`smart_history`); פחות משתי מדידות הן "אין עדיין מגמה", **לא** "יציב". הערך שלא נמדד
(המאפיין לא דווח) אינו נספר ואינו אפס.

הכרטיס קורא דרך `memory_view.Facts`, כלומר דרך `memory.knowledge` -- אותו קורא של המוח: מה שעוד בטבלאות ומה
שסוכם אחרי חלון השמירה, כאילו שום שורה לא נמחקה.
"""

from __future__ import annotations

import json
import sqlite3
from server.db import SqlConnection
from typing import Iterable

from server.brain import memory, memory_view as view
from server.disks import disk_sets, smart_history

#: המונים שמגמה נגזרת מהם, לפי סדר חשיבות; שם השדה בסיכום של `memory._smart`.
COUNTERS = (("reallocated", "ata_5"), ("pending", "ata_197"), ("media_errors", "nvme_media_errors"))
#: כמה שורות מפורטות של הכרטיס (שורות שעוד בטבלה; מה שיותר ישן נמצא בסיכום)
SHOWN = memory.RECENT


def _reading(row: sqlite3.Row) -> dict:
    """סבב אחד של SMART: המונים והטמפרטורה. מאפיין שלא דווח = ``None``."""
    try:
        attrs = json.loads(row["attrs_json"] or "{}")
    except (TypeError, ValueError):
        attrs = {}
    ata = smart_history._rows(attrs)
    nvme = smart_history._nvme(attrs)
    return {"at": row["at"], "session_id": row["session_id"], "mac": row["mac"], "temp": row["temp_after"],
            "reallocated": (ata.get(5) or {}).get("raw"), "pending": (ata.get(197) or {}).get("raw"),
            "media_errors": nvme.get("media_errors")}


def _readings(conn: SqlConnection, serials: Iterable[str] | None = None) -> dict[str, list[dict]]:
    """``{serial: [סבב, ...]}`` מהישן לחדש. ``serials=None`` = כל הדיסקים (דף הרשימה)."""
    out: dict[str, list[dict]] = {}
    sql = "SELECT serial, session_id, mac, at, attrs_json, temp_after FROM smart_history"
    params: tuple = ()
    if serials is not None:
        serials = list(serials)
        sql += f" WHERE serial IN ({','.join('?' * len(serials))})"
        params = tuple(serials)
    for row in conn.execute(sql + " ORDER BY at, rowid", params):
        out.setdefault(row["serial"], []).append(_reading(row))
    return out


def trend_of(rows: list[dict], older: dict | None) -> dict:
    """``{"state": none|stable|rose|fell, "counter", "from", "to", "rounds"}``. ``rows`` = מדידות חיות (ישן ראשון),
    ``older`` = הסיכום של מה שיצא מהחלון (`memory.summary(...)["smart_history"]`) או ``None``."""
    older_n = (older or {}).get("rows", 0)
    rounds = older_n + len(rows)
    best = None
    for name, field in COUNTERS:
        known = [r[name] for r in rows if r[name] is not None]
        old = ((older or {}).get("num") or {}).get(field)
        if (old["n"] if old else 0) + len(known) < 2:
            continue                                   # פחות משתי מדידות של המונה הזה: אין מגמה
        start = old["first"] if old else known[0]
        if known:
            end = known[-1]
        elif old:
            end = old["last"]
        else:
            continue
        state = "rose" if end > start else "fell" if end < start else "stable"
        mine = {"state": state, "counter": name, "from": int(start), "to": int(end), "rounds": rounds}
        if best is None or (state == "rose" and best["state"] != "rose"):
            best = mine
        elif state == "fell" and best["state"] == "stable":
            best = mine
    return best or {"state": "none", "counter": None, "from": None, "to": None, "rounds": rounds}


def trends(conn: SqlConnection, serials: Iterable[str]) -> dict[str, dict]:
    """המגמה של כל סידורי ברשימה (שאילתה אחת ל-SMART, וקריאת סיכום קטנה לכל דיסק)."""
    serials = list(serials)
    live = _readings(conn, serials if len(serials) < 500 else None)
    return {s: trend_of(live.get(s, []), memory.summary(conn, "disk", s).get("smart_history")) for s in serials}


def _model_average(conn: SqlConnection, model: str | None) -> dict:
    s = view.Facts(conn, "model", model).stats("model_speeds") if model else None
    mbps = view.stat(s, "mbps") if s else None
    return {"mbps": view._avg(mbps), "n": mbps["n"] if mbps else 0}


def card(conn: SqlConnection, serial: str) -> dict | None:
    """כל מה שהשרת יודע על דיסק אחד. ``None`` = אין עליו שום ידע (הנתיב מחזיר 404)."""
    inv = conn.execute("SELECT model, size_bytes, first_seen, last_seen FROM disk_inventory WHERE serial = ?",
                       (serial,)).fetchone()
    f = view.Facts(conn, "disk", serial)
    live = _readings(conn, [serial]).get(serial, [])
    drive, speeds, ports = f.stats("drive_rates"), f.stats("model_speeds"), f.stats("port_history")
    fails, smart = f.stats("disk_failures"), f.summary.get("smart_history")
    if inv is None and not (live or drive["rows"] or smart or fails["rows"] or f.decisions):
        return None
    model = inv["model"] if inv else None
    avg = _model_average(conn, model)
    names = disk_sets._machine_names(conn)
    by: dict[str, dict] = {}                                              # סבב -> מה שנרשם בו על הדיסק

    def merge(session: str, at: str, mac: str, **fields) -> None:
        r = by.setdefault(session, {"session_id": session, "at": at, "mac": mac, "port": None, "temp": None,
                                    "reallocated": None, "pending": None, "media_errors": None, "mbps": None,
                                    "link_gbps": None, "crc_delta": None, "written": False})
        r.update({k: v for k, v in fields.items() if v is not None})
        r["at"] = max(r["at"], at)

    for r in live:
        merge(r["session_id"], r["at"], r["mac"], temp=r["temp"], reallocated=r["reallocated"], pending=r["pending"],
              media_errors=r["media_errors"])
    for sp in f.live("model_speeds"):
        merge(sp["session_id"], sp["at"], sp["mac"], mbps=sp["mbps"])
    for pl in f.live("port_history"):
        merge(pl["session_id"], pl["at"], pl["mac"], port=pl["port"], link_gbps=pl["link_gbps"], crc_delta=pl["crc_delta"])
    for dr in f.live("drive_rates"):                                      # נכתב בסבב: שורה ב-drive_rates
        merge(dr["session_id"], dr["at"], "", written=True)
    rounds = sorted(by.values(), key=lambda r: r["at"], reverse=True)[:SHOWN]
    for r in rounds:
        r["machine"] = names.get(r["mac"])
        r["pct_of_model"] = round(100 * r["mbps"] / avg["mbps"]) if r["mbps"] is not None and avg["mbps"] else None
    mbps = view.stat(speeds, "mbps")
    last = view.stat(ports, "link_gbps")
    top = view.stat(ports, "link_max_gbps")
    crc = view.stat(ports, "crc_delta")
    folded = (f.summary.get("model_speeds") or {})
    return {
        "serial": serial, "model": model, "first_seen": inv["first_seen"] if inv else None,
        "rounds": {"total": drive["rows"], "detailed": len(f.live("drive_rates")),
                   "summarized": (f.summary.get("drive_rates") or {}).get("rows", 0)},
        "trend": trend_of(live, smart),
        "smart": {"rounds": rounds, "older": smart and {
            "rounds": smart["rows"], **{n: ((smart.get("num") or {}).get(k) or {}).get("max") for n, k in COUNTERS},
            "temp": ((smart.get("num") or {}).get("temp_after") or {}).get("max")}},
        "write": {"failures": fails["rows"], "failures_last_at": fails["last_at"],
                  "last_mbps": next((r["mbps"] for r in rounds if r["mbps"] is not None), None), "model_avg_mbps": avg["mbps"],
                  "model_n": avg["n"],
                  "older": {"rounds": folded["rows"], "avg": view._avg((folded.get("num") or {}).get("mbps")),
                            "min": ((folded.get("num") or {}).get("mbps") or {}).get("min")} if folded else None,
                  "mbps": mbps and {"n": mbps["n"], "avg": view._avg(mbps)}},
        "link": {"rounds": ports["rows"], "last_gbps": last["last"] if last else None,
                 "max_gbps": top["last"] if top else None, "crc_total": view._int(crc["sum"]) if crc else None,
                 "cable_fails": view._int((view.stat(ports, "cable_fail") or {}).get("sum"))},
        "failures": [{"at": r["at"], "cause": r["cause"], "mac": r["mac"], "port": r["port"],
                      "machine": names.get(r["mac"])} for r in f.live("disk_failures")[:SHOWN]],
        "decisions": f.decisions,
    }
