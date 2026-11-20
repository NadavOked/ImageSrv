"""#3185: מה הקונסולה מציגה מהזיכרון ארוך-הטווח של המוח -- סיכום לכל ישות, בדף של הישות.

הכרעת נדב 14/11: כל ידע נמצא במקום אחד, בדף הישות שלו (דיסקים, מגירת מחשב, היסטוריית סבבים), והיומן הוא אירועים בלבד.
המודול הזה לא קורא את ``brain_memory`` ו-``brain_decisions`` בעצמו: הוא עומד מעל `memory.knowledge` (נקודת הקריאה
היחידה), ומחבר את שני חצאי הידע לספירה אחת -- **מה שסוכם ומה שעוד בטבלה**, כאילו שום שורה לא נמחקה.

**ערך שלא נמדד אינו אפס:** שדה שלא נקרא לא נספר, ו"לא נקרא" יוצא מכאן כ-``None``, לא כ-0 (עיקרון 5).
"""

from __future__ import annotations

import json
import sqlite3

from server.agent.registry import ROOM_GROUPS
from server.brain import memory

#: כל השורות החיות של ישות (לא רק ה-`RECENT` האחרונות): הספירה חייבת לכלול את כולן.
ALL = 1_000_000
#: כמה החלטות אחרונות של ישות מוצגות.
DECISIONS = 20


def _json(raw):
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


class Facts:
    """ישות אחת: הסיכום של מה שיצא מהחלון + השורות שעוד בטבלאות. ``stats(rule)`` = שניהם יחד."""

    def __init__(self, conn: sqlite3.Connection, kind: str, key: str):
        got = memory.knowledge(conn, kind, key, limit=ALL)
        self.kind, self.key = kind, key
        self.recent, self.summary = got["recent"], got["summary"]
        self.decisions = got["decisions"][:DECISIONS]

    def live(self, table: str) -> list[dict]:
        return self.recent.get(table, [])

    def stats(self, rule: str) -> dict:
        """``{"rows", "first_at", "last_at", "num": {שדה: {n,sum,min,max,first,last}}, "text"}`` של מקור אחד.
        ``rows`` = 0 -- אין עליו שום ידע (לא "אפס שגיאות")."""
        source = memory.SOURCES[rule]
        rows = self.live(source.table)
        live: dict = {}
        for row in rows:
            memory._merge(live, row.get(source.at), source.nums(row), source.texts(row))
        stamps = [row[source.at] for row in rows if row.get(source.at)]
        old = self.summary.get(source.table)
        out = {"rows": len(rows), "first_at": min(stamps, default=None), "last_at": max(stamps, default=None), **live}
        if old is None or old.get("unreadable"):
            return {**out, "num": out.get("num", {}), "text": out.get("text", {})}
        merged = memory._combine({"num": old.get("num", {}), "text": old.get("text", {})}, live)
        return {"rows": old["rows"] + len(rows), "first_at": memory._span(old["first_at"], out["first_at"], min),
                "last_at": memory._span(old["last_at"], out["last_at"], max), **merged}


def stat(stats: dict, field: str) -> dict | None:
    """הסיכום של שדה מספרי אחד, או ``None`` אם אף שורה לא מדדה אותו."""
    return stats["num"].get(field)


def _avg(s: dict | None) -> float | None:
    return round(s["sum"] / s["n"], 1) if s and s["n"] else None


def _int(v):
    return None if v is None else int(v)


# --- מחשב --------------------------------------------------------------------------------------------------------


def _wol(f: Facts) -> dict | None:
    s = f.stats("wol_wakes")
    if not s["rows"]:
        return None
    count = lambda field: _int((stat(s, field) or {}).get("sum"))        # noqa: E731
    return {"sent": s["rows"], "answered": count("answered"), "avg_s": _avg(stat(s, "seconds")),
            "resent": count("resent"), "early": count("early"), "alerted": count("alerted"), "since": s["first_at"]}


def _boot(f: Facts) -> dict | None:
    """השלב שנמדד איטי ביותר. ``boot_step_times`` הוא שורה לכל מעבר, ולא סך אתחול -- ולכן זה "השלב", לא "האתחול"."""
    s = f.stats("boot_step_times")
    steps = {k[5:-2]: v for k, v in s["num"].items() if k.startswith("boot_") and k.endswith("_s")}
    if not steps:
        return None
    name = max(steps, key=lambda k: steps[k]["sum"] / steps[k]["n"])
    return {"transitions": sum(v["n"] for v in steps.values()), "steps": len(steps), "since": s["first_at"],
            "slowest": {"step": name, "avg_s": _avg(steps[name]), "max_s": round(steps[name]["max"], 1),
                        "n": steps[name]["n"]}}


def _hardware(f: Facts) -> dict | None:
    s = f.stats("machine_inventory")
    return {"versions": s["rows"], "last_at": s["last_at"]} if s["rows"] else None


def _nic(n: Facts) -> dict | None:
    s = n.stats("nic_history")
    if not s["rows"]:
        return None
    speed, crc, dropped = stat(s, "speed_mbps"), stat(s, "rx_crc_errors"), stat(s, "rx_dropped")
    low_at = None
    if speed and speed["min"] < speed["max"]:                      # מתי -- רק אם השורה עוד בטבלה
        low_at = next((r["at"] for r in n.live("nic_history") if r.get("speed_mbps") == speed["min"]), None)
    return {"samples": s["rows"], "since": s["first_at"], "speed": speed and {
                "last": _int(speed["last"]), "min": _int(speed["min"]), "max": _int(speed["max"]), "low_at": low_at,
                "n": speed["n"]},
            "crc": crc and {"first": _int(crc["first"]), "last": _int(crc["last"]), "max": _int(crc["max"])},
            "dropped": dropped and {"last": _int(dropped["last"]), "max": _int(dropped["max"])}}


def _drops(conn: sqlite3.Connection, n: Facts, mac: str) -> dict | None:
    """מה שמקלט זרק, גל אחר גל: מ-`wave_rates.drops` (השורות החיות) ומהסיכום של מה שכבר יצא מהחלון."""
    waves, total = 0, {f: {"n": 0, "sum": 0.0} for f in ("rcvbuf", "inerrors", "abandoned")}
    for row in conn.execute("SELECT drops FROM wave_rates WHERE drops IS NOT NULL"):
        drops = _json(row["drops"])
        entry = drops.get(mac) if isinstance(drops, dict) else None
        if not isinstance(entry, dict):
            continue
        waves += 1
        for field, acc in total.items():
            v = memory._num(entry.get(field))
            if v is not None:                                      # null = לא נקרא: אינו נספר
                acc["n"] += 1
                acc["sum"] += v
    old = n.summary.get("wave_rates")
    if old and not old.get("unreadable"):
        waves += old["rows"]
        for field, acc in total.items():
            s = old.get("num", {}).get(field)
            if s:
                acc["n"] += s["n"]
                acc["sum"] += s["sum"]
    if not waves:
        return None
    return {"waves": waves, **{f: {"read": a["n"], "sum": _int(a["sum"]) if a["n"] else None} for f, a in total.items()}}


def _slots(conn: sqlite3.Connection, mac: str) -> list[dict]:
    from server.disks import disk_card                              # noqa: PLC0415 -- disk_card מייבא את Facts
    ports = {r["port"] for r in conn.execute("SELECT DISTINCT port FROM port_history WHERE mac = ?", (mac,))}
    ports |= {int(e["key"].rpartition("/")[2]) for e in memory.entities(conn, "slot")
              if e["key"].startswith(mac + "/") and e["key"].rpartition("/")[2].isdigit()}
    inv = {r["port"]: r for r in conn.execute("SELECT serial, port, first_seen FROM disk_inventory WHERE mac = ?",
                                              (mac,)) if r["port"] is not None}
    ports |= set(inv)                                              # חריץ שיש בו דיסק עכשיו, גם אם עוד אין לו היסטוריה
    trends = disk_card.trends(conn, [r["serial"] for r in inv.values()])
    out = []
    for port in sorted(ports):
        f = Facts(conn, "slot", f"{mac}/{port}")
        ph, fail = f.stats("port_history"), f.stats("disk_failures")
        link, top = stat(ph, "link_gbps"), stat(ph, "link_max_gbps")
        crc, cable = stat(ph, "crc_delta"), stat(ph, "cable_fail")
        disk = inv.get(port)
        out.append({"port": port, "rounds": ph["rows"], "since": ph["first_at"] or fail["first_at"],
                    "crc": _int(crc["sum"]) if crc else None, "cable_fails": _int(cable["sum"]) if cable else None,
                    "link": link and {"last": link["last"], "min": link["min"]}, "link_max": top and top["last"],
                    "failures": fail["rows"],
                    "disk": disk and {"serial": disk["serial"], "first_seen": disk["first_seen"],
                                      "trend": trends[disk["serial"]]}})
    return out


def machine(conn: sqlite3.Connection, mac: str) -> dict:
    """הקבוצה "נתונים שנאספו", "מה היה בחריצים" וההחלטות של המוח -- לכרטיס מחשב אחד."""
    m, n = Facts(conn, "machine", mac), Facts(conn, "network", mac)
    nic, wol, boot = _nic(n), _wol(m), _boot(m)
    return {"mac": mac, "nic": nic, "drops": _drops(conn, n, mac), "wol": wol, "boot": boot,
            "hardware": _hardware(m), "slots": _slots(conn, mac), "decisions": m.decisions,
            "since": min((x["since"] for x in (nic, wol, boot) if x and x["since"]), default=None)}


# --- סבב ---------------------------------------------------------------------------------------------------------


def _wave_row(row: sqlite3.Row) -> dict:
    from server.rounds import drive_rates                           # noqa: PLC0415 -- כבד; רק לסבב
    decision, stream, drops = _json(row["decision"]), _json(row["stream"]), _json(row["drops"])
    decision = decision if isinstance(decision, dict) else {}
    stream = stream if isinstance(stream, dict) else {}
    return {"at": row["at"], "rate_bps": drive_rates.parse_bitrate(decision.get("max_bitrate")),
            "sent_bytes": _int(memory._num(stream.get("sent_bytes"))),
            "loss_blocks": _int(memory._num(stream.get("loss_blocks"))),
            "drops": drops if isinstance(drops, dict) else None}


def round_waves(conn: sqlite3.Connection, created_at: str, closed_at: str | None) -> list[dict]:
    """גלי הסבב (אותו חלון זמן כמו `learn.waits.round_waits`), עם מה ש-`wave_rates` עוד זוכר עליהם.
    גל שהשורה שלו נמחקה: ``kept: False`` -- "לא נשמר", לא 0."""
    rows = conn.execute(
        f"SELECT s.id, s.created_at, w.session_id AS kept, w.decision, w.at, w.stream, w.drops FROM sessions s"
        f" LEFT JOIN wave_rates w ON w.session_id = s.id WHERE s.group_id IN {ROOM_GROUPS} AND s.kind = 'multicast'"
        " AND julianday(s.created_at) >= julianday(?) AND julianday(s.created_at) <= julianday(?)"
        " ORDER BY s.created_at, s.rowid", (created_at, closed_at or "9999-12-31")).fetchall()
    return [{"session_id": r["id"], "created_at": r["created_at"], "kept": r["kept"] is not None,
             **(_wave_row(r) if r["kept"] is not None else {})} for r in rows]


def flow(waves: list[dict]) -> dict:
    """"קצב ואיבוד" של סבב: הקצב שבו הגל האחרון ששרד שודר, וסך הבלוקים שאבדו בגליו.
    ``None`` = לא נשמר / לא נמדד -- לעולם לא 0 (עיקרון 5)."""
    kept = [w for w in waves if w["kept"]]
    rates = [w["rate_bps"] for w in kept if w.get("rate_bps") is not None]
    losses = [w["loss_blocks"] for w in kept if w.get("loss_blocks") is not None]
    return {"waves": len(waves), "kept": len(kept), "rate_bps": rates[-1] if rates else None,
            "loss_blocks": sum(losses) if losses else None}


def _drives(conn: sqlite3.Connection, ids: list[str], names: dict[str, str]) -> list[dict]:
    """הדיסקים שנכתבו בגלי הסבב: מגירה, דיסק, דגם, MB/s, ומה הוא ביחס לממוצע של הדגם."""
    out, averages = [], {}
    for e in conn.execute(
            f"SELECT e.session_id, e.mac, e.disk, e.port, e.serial, i.model, m.mbps FROM disk_events e"
            f" LEFT JOIN disk_inventory i ON i.serial = e.serial"
            f" LEFT JOIN model_speeds m ON m.session_id = e.session_id AND m.serial = e.serial"
            f" WHERE e.session_id IN ({','.join('?' * len(ids))}) AND e.serial IS NOT NULL AND e.serial != ''"
            f" ORDER BY e.mac, e.port", ids):
        member = conn.execute("SELECT targets_json FROM session_members WHERE session_id = ? AND mac = ?",
                              (e["session_id"], e["mac"])).fetchone()
        targets = _json(member["targets_json"]) if member else None
        state = next((t.get("state") for t in targets if isinstance(t, dict) and t.get("dev") == e["disk"]), None)             if isinstance(targets, list) else None
        if e["model"] not in averages:
            averages[e["model"]] = _avg(stat(Facts(conn, "model", e["model"]).stats("model_speeds"), "mbps"))                 if e["model"] else None
        avg = averages[e["model"]]
        out.append({"mac": e["mac"], "machine": names.get(e["mac"]), "port": e["port"], "serial": e["serial"], "model": e["model"],
                    "mbps": e["mbps"], "state": state,
                    "pct_of_model": round(100 * e["mbps"] / avg) if e["mbps"] is not None and avg else None})
    return out


def round_summary(conn: sqlite3.Connection, created_at: str, closed_at: str | None) -> dict:
    """השורה שנפתחת בהיסטוריית הסבבים: הגלים, המקלטים (מה שזרקו), הדיסקים שנכתבו והחלטות המוח."""
    from server.disks import disk_sets                                  # noqa: PLC0415
    names = disk_sets._machine_names(conn)
    waves = round_waves(conn, created_at, closed_at)
    ids = [w["session_id"] for w in waves]
    receivers: dict[str, dict] = {}
    for w in waves:
        for mac, d in (w.get("drops") or {}).items():
            if not isinstance(d, dict):
                continue
            r = receivers.setdefault(mac, {"mac": mac, "machine": names.get(mac), "rcvbuf": None, "inerrors": None,
                                           "abandoned": None})
            for field in ("rcvbuf", "inerrors", "abandoned"):
                v = memory._num(d.get(field))
                if v is not None:                                   # לא נקרא אינו אפס
                    r[field] = (r[field] or 0) + int(v)
    return {"waves": [{k: v for k, v in w.items() if k != "drops"} for w in waves], "flow": flow(waves),
            "receivers": list(receivers.values()), "drives": _drives(conn, ids, names) if ids else [],
            "decisions": memory.decisions(conn, round_ids=ids, limit=DECISIONS)}
