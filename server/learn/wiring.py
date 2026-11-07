"""‏#2028 — המלצת חיווט SATA לדגם, לחלון "מיפוי חיבורים".

נדב 23/10: במשכפלים יש יותר חיבורי SATA ממגירות. מהמדידות אפשר לומר לאילו חיבורים
לחבר את המגירות: המהירים, בלי היסטוריית CRC. **קריאה בלבד:** שום דבר כאן אינו משנה
מיפוי ואינו חוסם שמירה. מיפוי משתנה רק כשמנהל שומר אותו.

**המקור** — `port_history` (‏#1844), `ports.WINDOW` התצפיות האחרונות לכל (מכונה, חיבור),
ו-hello האחרון למה שמחובר עכשיו. תצפית = שורה שנמדד בה קישור או CRC, או שנכשלה בה כתיבה
בסיבת כבל. ‏"בעייתית" — ‏`ports._problem`, אותו כלל של "כנראה כבל". "לא נמדד" אינו ראיה
לשום כיוון: חיבור ריק שלא נמדד בשום מחשב מהדגם אינו מומלץ ואינו חשוד.

**הסף** (נדב 06/11, מוקאפ 2028, שאלה 4): המלצה לדגם רק מ-`MIN_MACHINES` מחשבים **וגם**
‏`MIN_WAVES` שיכפולים, ומתחתיו "אין מספיק נתונים". כתום "חשוד" על חיבור בודד — גם בלי
סף: די בתצפית בעייתית אחת בחיבור הזה של המחשב הזה, והיא יוצאת מהחלון אחרי `WINDOW`
תצפיות, כמו "כנראה כבל".

**מומלץ לדגם** (`recommended`): חיבור שנמדד נקי במחשב אחד לפחות, ובעייתי בפחות מ-
‏`MIN_MACHINES` מחשבים, מהמהיר לאיטי, עד מספר המגירות של המחשב. **להימנע** (`avoid`): בעייתי
ב-`MIN_MACHINES` מחשבים לפחות, או איטי מהמומלצים ולא נכנס למספר המגירות.
"""

from __future__ import annotations

import json
import sqlite3

from server.disks import model_slots
from server.learn import ports

#: הסף. המקור: נדב 06/11, ההמלצה בתחתית המוקאפ שאושר.
MIN_MACHINES = 2
MIN_WAVES = 5

ORDINALS_HE = ("הראשונה", "השנייה", "השלישית", "הרביעית", "החמישית", "השישית", "השביעית", "השמינית")


def _sata(port: int) -> str:
    return str(port - 1)        # ‏sata:k = ‏ataK = "SATA k-1" (המוסכמה של #1375)


def _list_he(port_numbers: list[int]) -> str:
    names = [_sata(p) for p in port_numbers]
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} ו-{names[-1]}"


def _gbps(value: float) -> str:
    return f"{value:g} Gb/s"


def _windows(conn: sqlite3.Connection, macs: list[str]) -> dict[tuple[str, int], list[sqlite3.Row]]:
    """‏`{(mac, port): התצפיות האחרונות, מהחדשה}`, בלי שורות שלא נמדד בהן דבר."""
    out: dict[tuple[str, int], list[sqlite3.Row]] = {}
    marks = ",".join("?" * len(macs))
    for row in conn.execute(f"SELECT * FROM port_history WHERE mac IN ({marks})"
                            " ORDER BY at DESC, rowid DESC", macs):
        if row["link_gbps"] is None and row["crc_delta"] is None and not row["cable_fail"]:
            continue
        rows = out.setdefault((row["mac"], row["port"]), [])
        if len(rows) < ports.WINDOW:
            rows.append(row)
    return out


def _bad(rows: list[sqlite3.Row]) -> list[sqlite3.Row]:
    return [r for r in rows
            if ports._problem(r["crc_delta"], r["link_gbps"], r["link_max_gbps"], r["cable_fail"])]


def _port_summary(rows: list[sqlite3.Row]) -> dict:
    deltas = [r["crc_delta"] for r in rows if r["crc_delta"] is not None]
    return {"observations": len(rows), "bad": len(_bad(rows)),
            "crc": sum(deltas) if deltas else None,
            "crc_waves": sum(1 for d in deltas if d > 0),
            "cable_fails": sum(1 for r in rows if r["cable_fail"])}


def _model_ports(windows: dict) -> dict[int, dict]:
    """לכל חיבור בדגם: באילו מחשבים נמדד נקי, באילו בעייתי, ומה הקישור הטוב שנמדד בו."""
    out: dict[int, dict] = {}
    for (mac, port), rows in windows.items():
        got = out.setdefault(port, {"clean": set(), "bad": set(), "link": None, "crc": 0, "fails": 0})
        (got["bad"] if _bad(rows) else got["clean"]).add(mac)
        got["crc"] += sum(r["crc_delta"] or 0 for r in rows)
        got["fails"] += sum(1 for r in rows if r["cable_fail"])
        links = [r["link_gbps"] for r in rows if r["link_gbps"] is not None]
        if links:
            got["link"] = max(got["link"] or 0, *links)
    return out


def _rank(model: dict[int, dict], drawers: int) -> tuple[list[int], list[int]]:
    """‏`(recommended, avoid)` — ראו למעלה."""
    fit = sorted((p for p, m in model.items()
                  if m["clean"] and len(m["bad"]) < MIN_MACHINES and m["link"] is not None),
                 key=lambda p: (-model[p]["link"], p))
    recommended = fit[:drawers]
    slowest = min((model[p]["link"] for p in recommended), default=None)
    avoid = [p for p, m in model.items() if len(m["bad"]) >= MIN_MACHINES
             or (p in fit[drawers:] and m["link"] < slowest)]
    return sorted(recommended), sorted(avoid)


def _why_he(port: int, m: dict, slow: bool) -> str:
    causes = []
    if slow or not (m["crc"] or m["fails"]):
        causes.append(f"איטי ({_gbps(m['link'])})" if m["link"] is not None else "איטי")
    if m["crc"]:
        causes.append("יש עליו היסטוריית CRC")
    if m["fails"]:
        causes.append("נכשלה בו כתיבה בסיבת כבל")
    return f"SATA {_sata(port)} {' ו'.join(causes)}"


def _text_he(model: dict, recommended: list[int], avoid: list[int], drawers: int,
             machines: int, waves: int, best: float | None) -> str:
    if len(recommended) >= drawers:
        text = f"חברו את {drawers} המגירות ל-SATA {_list_he(recommended)}."
    elif recommended:
        text = f"רק {len(recommended)} חיבורים נמדדו נקיים: SATA {_list_he(recommended)}. אין ראיה על השאר."
    else:
        text = "אף חיבור לא נמדד נקי."
    for port in avoid:
        slow = best is not None and model[port]["link"] is not None and model[port]["link"] < best
        text += f" {_why_he(port, model[port], slow)}, ולכן מומלץ להשאיר אותו פנוי."
    return f"{text} מבוסס על {machines} מחשבים מהדגם, {waves} שיכפולים."


def _hello(conn: sqlite3.Connection, mac: str) -> dict[int, dict]:
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    disks = json.loads(row["disks_json"] or "[]") if row else []
    return {d["port"]: d for d in disks if isinstance(d, dict) and isinstance(d.get("port"), int)}


def advice(conn: sqlite3.Connection, mac: str) -> dict:
    """ההמלצה למחשב `mac`. קריאה בלבד; כשל בקריאה עולה למעלה — לא נבדק אינו "הכול תקין"."""
    model_name = model_slots.model_of(conn, mac)
    macs = [m for m, name in model_slots.models_by_mac(conn).items() if name == model_name] \
        if model_name else []
    macs = sorted({mac, *macs})
    windows = _windows(conn, macs)
    machines = len({m for m, _ in windows})
    waves = len({r["session_id"] for rows in windows.values() for r in rows})
    enough = bool(model_name) and machines >= MIN_MACHINES and waves >= MIN_WAVES
    drawers = conn.execute("SELECT drawer_count FROM machines WHERE mac = ?",
                           (mac,)).fetchone()["drawer_count"]
    model = _model_ports(windows)
    recommended, avoid = _rank(model, drawers) if enough else ([], [])
    best = max((model[p]["link"] for p in recommended), default=None)
    connected = _hello(conn, mac)

    local = {port: _port_summary(rows) for (m, port), rows in windows.items() if m == mac}
    suspect = sorted(p for p, got in local.items() if got["bad"])
    leave = sorted({*avoid, *suspect})
    free = [p for p in recommended if p not in connected and p not in leave]
    moves = [{"from": p, "to": to, "drawer": sorted(connected).index(p) + 1}
             for p, to in zip([p for p in leave if p in connected], free)]
    instead = {m["to"]: m["from"] for m in moves}

    out_ports = {}
    for port in sorted({*connected, *model}):
        disk, got = connected.get(port, {}), local.get(port)
        link = disk.get("link_gbps")
        m = model.get(port)
        out_ports[str(port)] = {
            **(got or {"observations": 0, "bad": 0, "crc": None, "crc_waves": 0, "cable_fails": 0}),
            "link_gbps": link if isinstance(link, (int, float)) else None,
            "model_link_gbps": m["link"] if m else None,
            "slow_for_model": bool(best and isinstance(link, (int, float)) and link < best),
            "advice": "avoid" if port in leave else "use" if port in recommended else None,
            "instead_of": instead.get(port)}
    notes = [f"המגירה {ORDINALS_HE[m['drawer'] - 1] if m['drawer'] <= len(ORDINALS_HE) else m['drawer']}"
             f" מחוברת היום ל-SATA {_sata(m['from'])}. מומלץ להעביר אותה ל-SATA {_sata(m['to'])}."
             for m in moves]
    return {"model": model_name, "machines": machines, "waves": waves, "enough": enough,
            "required_machines": MIN_MACHINES, "required_waves": MIN_WAVES, "drawers": drawers,
            "recommended": recommended, "avoid": leave, "moves": moves, "move_notes_he": notes,
            "head_he": f"המלצת חיווט לדגם {model_name}:" if enough else None,
            "text_he": _text_he(model, recommended, avoid, drawers, machines, waves, best)
            if enough else None,
            "ports": out_ports}
