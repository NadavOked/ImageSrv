"""תמונת הקושחה של כל מכונה, ו"השתנה מאז ההדלקה הקודמת" (#2080, מוקאפ 06, נדב 24/10).

נדב: "השרת צריך לקחת מהברזל מידע לפני, ולפי זה לדעת. ככה הוא לא יפספס כלום." בכל hello הסוכן שולח
את מצב האתחול (UEFI/BIOS), את מצב בקר הדיסקים (``sata_mode``: ahci/ide/raid), את גרסת ה-BIOS (``bios``)
ואת הדיסק שבכל חיבור SATA (``disks[].port``). השרת שומר **תמונה** כשהיא משתנה, ומשווה להדלקה הקודמת
(או לתמונה ש"זו ההגדרה הנכונה" שמרה).

* שינוי = אזהרה כתומה "היה → עכשיו", לא חסימה.
* **שדה שלא דווח אינו שינוי** (עיקרון 5): ‏hello בלי ``sata_mode`` (סוכן ישן) משאיר את הערך הקודם ואינו
  נספר "השתנה"; השוואה רק כששני הצדדים נקראו.
* ההשוואה ברמת החיבורים (``slots``) רק כששתי התמונות נושאות רשימת דיסקים.
* ההתאמה לאימג' (בקר IDE מול אימג' שנקלט ב-AHCI) **אינה כאן**: היא דורשת שהקליטה תרשום את מצב הבקר של
  מכונת המקור -- ראו interfaces.md סעיף 41.
"""

from __future__ import annotations

import json
import sqlite3

from .db import _settle, _write_lock, journal, now_iso, writing

SATA_MODES = ("ahci", "ide", "raid")
KEEP = 20
TEXT_MAX = 80
#: שמות בעברית לשדות, לשורת היומן ולקונסולה.
FIELD_HE = {"sata_mode": "מצב בקר הדיסקים", "boot": "מצב אתחול", "secure_boot": "Secure Boot", "bios_version": "גרסת BIOS"}
BOOT_HE = {"uefi": "UEFI", "bios": "BIOS"}


def _mode_he(v) -> str:
    return {"ahci": "AHCI", "ide": "IDE", "raid": "RAID"}.get(v, str(v))


def _val_he(field: str, v) -> str:
    if field == "sata_mode":
        return _mode_he(v)
    if field == "boot":
        return BOOT_HE.get(v, str(v))
    if field == "secure_boot":
        return "דלוק" if v else "כבוי"
    return str(v)


def _text(raw: object) -> str | None:
    return raw.strip()[:TEXT_MAX] or None if isinstance(raw, str) else None


def well_formed_bios(raw: object) -> dict | None:
    """``{"version", "date", "vendor"}`` מקוצצים, או ``None`` (חסר/פגום = לא דווח)."""
    if not isinstance(raw, dict):
        return None
    out = {k: _text(raw.get(k)) for k in ("version", "date", "vendor")}
    return out if out["version"] else None


def build(prev: dict | None, hello: dict) -> dict:
    """התמונה הנוכחית: מה שה-hello אמר, ומה שלא נאמר -- מהתמונה הקודמת (``None`` = אף פעם לא דווח)."""
    prev = prev or {}
    boot = hello.get("firmware") if hello.get("firmware") in BOOT_HE else prev.get("boot")
    sb = hello.get("secure_boot")
    sb = sb if isinstance(sb, bool) else prev.get("secure_boot")
    mode = hello.get("sata_mode")
    mode = mode if mode in SATA_MODES else prev.get("sata_mode")
    bios = well_formed_bios(hello.get("bios"))
    inv = hello.get("inventory") if isinstance(hello.get("inventory"), dict) else {}
    dmi = inv.get("dmi") if isinstance(inv.get("dmi"), dict) else {}
    model = _text(dmi.get("product_name")) or prev.get("model")
    slots = prev.get("slots")
    disks = hello.get("disks")
    if isinstance(disks, list):
        slots = {}
        for d in disks:
            if isinstance(d, dict) and isinstance(d.get("port"), int) and not isinstance(d["port"], bool):
                slots[str(d["port"])] = {
                    "serial": _text(d.get("serial")), "model": _text(d.get("model")),
                    "size_bytes": d.get("size_bytes") if isinstance(d.get("size_bytes"), int) else None,
                    "link_gbps": d.get("link_gbps") if isinstance(d.get("link_gbps"), (int, float)) else None}
    return {"boot": boot, "secure_boot": sb, "sata_mode": mode,
            "bios_version": bios["version"] if bios else prev.get("bios_version"),
            "bios_date": bios["date"] if bios else prev.get("bios_date"),
            "model": model, "slots": slots}


def _rows(conn: sqlite3.Connection, mac: str, limit: int = 2) -> list[tuple[str, dict]]:
    out = []
    for r in conn.execute("SELECT at, snap_json FROM fw_snapshots WHERE mac = ? ORDER BY id DESC LIMIT ?", (mac, limit)):
        try:
            out.append((r["at"], json.loads(r["snap_json"])))
        except ValueError:
            continue
    return out


def diff(ref: dict, cur: dict) -> list[dict]:
    """מה השתנה בין ``ref`` ל-``cur``. רק כששני הצדדים נקראו."""
    out = []
    for field in ("sata_mode", "boot", "secure_boot", "bios_version"):
        a, b = ref.get(field), cur.get(field)
        if a is not None and b is not None and a != b:
            out.append({"kind": "field", "field": field, "was": a, "now": b,
                        "text": f"{FIELD_HE[field]} השתנה: {_val_he(field, a)} ← {_val_he(field, b)}"})
    rs, cs = ref.get("slots"), cur.get("slots")
    if isinstance(rs, dict) and isinstance(cs, dict):
        where_now = {v["serial"]: p for p, v in cs.items() if v.get("serial")}
        for port, v in sorted(rs.items(), key=lambda kv: int(kv[0])):
            serial = v.get("serial")
            if not serial or cs.get(port, {}).get("serial") == serial:
                continue
            if serial in where_now:
                out.append({"kind": "moved", "serial": serial, "model": v.get("model"), "from": int(port), "to": int(where_now[serial]),
                            "text": f"דיסק {serial} עבר מ-SATA {int(port) - 1} ל-SATA {int(where_now[serial]) - 1}"})
            else:
                out.append({"kind": "gone", "serial": serial, "model": v.get("model"), "port": int(port),
                            "text": f"דיסק {serial} נעלם מ-SATA {int(port) - 1}"})
    return out


def baseline(conn: sqlite3.Connection, mac: str) -> tuple[str, dict] | None:
    r = conn.execute("SELECT at, snap_json FROM fw_baseline WHERE mac = ?", (mac,)).fetchone()
    if r is None:
        return None
    try:
        return r["at"], json.loads(r["snap_json"])
    except ValueError:
        return None


def reference(conn: sqlite3.Connection, mac: str) -> tuple[str, dict, str] | None:
    """התמונה שמשווים אליה: ה"הגדרה הנכונה" ששמרו, ואם אין -- ההדלקה הקודמת. ``(at, snap, "baseline"|"previous")``."""
    base = baseline(conn, mac)
    if base is not None:
        return base[0], base[1], "baseline"
    rows = _rows(conn, mac, 2)
    return (rows[1][0], rows[1][1], "previous") if len(rows) == 2 else None


def record(conn: sqlite3.Connection, mac: str, hello: dict) -> None:
    """נקרא מ-hello של מכונה רשומה. תמונה חדשה נכתבת רק כשהשתנתה; שינוי משמעותי -- שורת יומן."""
    last = _rows(conn, mac, 1)
    prev = last[0][1] if last else None
    cur = build(prev, hello)
    if prev == cur or all(v is None for v in cur.values()):
        return
    ts = now_iso()
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO fw_snapshots (mac, at, snap_json) VALUES (?, ?, ?)",
                     (mac, ts, json.dumps(cur, sort_keys=True, ensure_ascii=False)))
        conn.execute("DELETE FROM fw_snapshots WHERE mac = ? AND id NOT IN"
                     " (SELECT id FROM fw_snapshots WHERE mac = ? ORDER BY id DESC LIMIT ?)", (mac, mac, KEEP))
    if prev is not None:
        changes = diff(prev, cur)
        if changes:
            journal(conn, "fw_changed", f"{mac}: " + " · ".join(c["text"] for c in changes))


def change_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """לכל מכונה: כמה שינויים מול ההפניה. מכונה בלי הפניה או בלי שינוי חסרה."""
    out = {}
    for r in conn.execute("SELECT DISTINCT mac FROM fw_snapshots"):
        n = len(changes_for(conn, r["mac"])["changes"])
        if n:
            out[r["mac"]] = n
    return out


def changes_for(conn: sqlite3.Connection, mac: str) -> dict:
    rows = _rows(conn, mac, 1)
    if not rows:
        return {"current": None, "changes": [], "ref_at": None, "ref_kind": None}
    ref = reference(conn, mac)
    cur_at, cur = rows[0]
    changes = diff(ref[1], cur) if ref else []
    return {"current": {**cur, "at": cur_at}, "changes": changes,
            "ref_at": ref[0] if ref else None, "ref_kind": ref[2] if ref else None, "ref": ref[1] if ref else None}


def view(conn: sqlite3.Connection, mac: str) -> dict:
    """מה שהמגירה מציגה: התמונה, השינויים, טבלת החיבורים (כולל "ריק, היה: …"), והשוואה לדגם."""
    v = changes_for(conn, mac)
    cur, ref = v["current"], v.get("ref")
    table = []
    if cur and isinstance(cur.get("slots"), dict):
        ports = set(cur["slots"]) | (set(ref["slots"]) if ref and isinstance(ref.get("slots"), dict) else set())
        moved_to = {c["to"]: c for c in v["changes"] if c["kind"] == "moved"}
        for port in sorted(ports, key=int):
            now = cur["slots"].get(port)
            was = (ref or {}).get("slots", {}).get(port) if ref and isinstance(ref.get("slots"), dict) else None
            row = {"port": int(port) - 1, "connection": f"SATA {int(port) - 1}", "disk": now, "was": None, "note": None, "changed": False}
            if int(port) in moved_to:
                row["note"] = f"עבר מ-SATA {moved_to[int(port)]['from'] - 1}"; row["changed"] = True
            if now is None and was and was.get("serial"):
                row["was"] = was; row["changed"] = True        # "ריק, היה: …" -- moved away or gone
            elif now is None:
                continue
            table.append(row)
        ps = sorted(int(p) for p, d in cur["slots"].items() if d.get("serial"))
        view_gap = bool(ps) and any(p not in ps for p in range(1, ps[-1]))
    else:
        view_gap = False
    peers = []
    if cur and cur.get("model"):
        for r in conn.execute("SELECT mac, MAX(id) AS id FROM fw_snapshots GROUP BY mac"):
            if r["mac"] == mac:
                continue
            snap = json.loads(conn.execute("SELECT snap_json FROM fw_snapshots WHERE id = ?", (r["id"],)).fetchone()[0])
            if snap.get("model") == cur["model"]:
                name = conn.execute("SELECT m.suffix, g.role FROM machines m JOIN groups g ON g.id = m.group_id WHERE m.mac = ?", (r["mac"],)).fetchone()
                peers.append({"mac": r["mac"], "name": f"{ {'cloner': 'משכפל', 'build': 'מחשב בנייה'}.get(name['role'], '')} {name['suffix']}".strip() if name else r["mac"],
                              "boot": snap.get("boot"), "sata_mode": snap.get("sata_mode"), "bios_version": snap.get("bios_version"),
                              "differs": [f for f in ("boot", "sata_mode", "bios_version")
                                          if snap.get(f) is not None and cur.get(f) is not None and snap.get(f) != cur.get(f)]})
    return {"mac": mac, "current": cur, "changes": v["changes"], "ref_at": v["ref_at"], "ref_kind": v["ref_kind"],
            "slots": table, "gap_below_populated": view_gap, "peers": peers}


def accept(conn: sqlite3.Connection, mac: str, user: str) -> bool:
    """"זו ההגדרה הנכונה": התמונה הנוכחית נשמרת כתמונה התקינה, ומכאן משווים אליה."""
    rows = _rows(conn, mac, 1)
    if not rows:
        return False
    _settle(conn)
    with _write_lock, writing(conn):
        conn.execute("INSERT INTO fw_baseline (mac, at, snap_json, by) VALUES (?, ?, ?, ?)"
                     " ON CONFLICT (mac) DO UPDATE SET at = excluded.at, snap_json = excluded.snap_json, by = excluded.by",
                     (mac, now_iso(), json.dumps(rows[0][1], sort_keys=True, ensure_ascii=False), user))
    journal(conn, "fw_accepted", mac, user)
    return True
