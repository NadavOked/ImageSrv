"""What the server learns about a disk from round to round (#1859).

Nadav, 20/10: "yes to all", "if the server is smart and learns". The agent sends
every SMART attribute (agent/lib/smartattrs.sh): with the hello (ids and numbers,
from its cache) and with the disk_event of each round (with the vendor's names).
Here:

* ``record`` -- the round's snapshot, one row per (serial, session): the history.
* ``record_temp`` -- the temperature the agent read right after the write (#1859:
  "194 over a threshold while writing"), on that same row.
* ``learned_for`` -- the hello's ``smart_learned``: for each disk of the hello, what
  rose since this serial's last snapshot. **The server compares; the agent decides**
  (it turns the word into the orange question, as with ``disk_failures``).
* ``describe`` -- the console's view of one disk: every attribute in Hebrew
  (smart_names.py), critical ones marked, what rose since the previous round, and
  "no comparison yet" when there is no previous round -- never "stable".

A cumulative counter that did not rise is information only (#872 measured it on
CRC: 137..55,242 on six disks that clone perfectly).
"""

from __future__ import annotations

import json
import sqlite3

from . import crcwarn, smart_names
from .db import _write_lock, now_iso, writing

#: a rise in one of these since the previous round: "replace the disk" (orange). #1921: by
#: (id, smartctl's name) -- smart_names.CRITICAL_WORDS -- never by the id alone: 198 on an
#: OCZ Intrepid is the bytes it read, and it rises every round (#1922).
#: 199 is the cable, not the disk (#872) -- its rise is the console's "CRC +N".
#: Nadav: 183 (SATA downshift) / 188 (command timeout) that rose -- likely the cable (#1844).
#: Only where the name is that meaning: 183 on a Samsung SSD is Runtime_Bad_Block (ס3), critical.
ROSE_LINK = {183: "downshift_rose", 188: "timeout_rose"}
#: Celsius read right after the write; over it the next round asks (orange). An
#: assumption until Nadav sets it: most SATA SSDs are rated to 70.
TEMP_WARN_C = 60

NO_HISTORY_HE = "אין עדיין השוואה"

#: the learned words in Hebrew -- the same words as smart.sh smart_reason_he, the console and the GUI
#: (tests/test_reasons_he_1861.py keeps them in step).
LEARNED_HE = {
    "critical_rose": "מאפיין קריטי עלה מאז הסבב הקודם — להחליף את הדיסק",
    "downshift_rose": "ירידות מהירות SATA (183) עלו מאז הסבב הקודם",
    "timeout_rose": "פקודות שנתקעו (188) עלו מאז הסבב הקודם",
    "hot": "הדיסק התחמם בכתיבה בסבב הקודם",
}


def _rows(attrs: object) -> dict[int, dict]:
    """{"ata": [[id, value, worst, thresh, raw(, name)], ...]} -> {id: {...}}; junk dropped."""
    out: dict[int, dict] = {}
    if not isinstance(attrs, dict) or not isinstance(attrs.get("ata"), list):
        return out
    for r in attrs["ata"]:
        if not isinstance(r, list) or len(r) < 5 or not isinstance(r[0], int) or isinstance(r[0], bool):
            continue
        num = [v if isinstance(v, int) and not isinstance(v, bool) and v >= 0 else None for v in r[1:5]]
        out[r[0]] = {"value": num[0], "worst": num[1], "thresh": num[2], "raw": num[3],
                     "vendor": r[5] if len(r) > 5 and isinstance(r[5], str) else None}
    return out


def _nvme(attrs: object) -> dict[str, int]:
    nv = attrs.get("nvme") if isinstance(attrs, dict) else None
    if not isinstance(nv, dict):
        return {}
    return {k: v for k, v in nv.items() if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool)}


def record(conn: sqlite3.Connection, serial: str | None, session_id: str, mac: str, attrs: object,
           dev: object = None) -> bool:
    """The round's snapshot of a disk (disk_event). True when stored; no serial or no
    attributes is no snapshot -- a disk we cannot name has no history.

    **Only the machine that has the disk writes its history** (security review, #1859):
    the serial must be what this MAC reported for this `dev` in its last hello
    (`serial_of`). Otherwise any machine on the deploy network -- or a buggy agent --
    could write a snapshot under another disk's serial and hide its "rose since the
    previous round", or forge a "replace the disk". The row keeps the reporting MAC,
    and the key includes it: one machine cannot overwrite another's row either."""
    if not serial or not isinstance(attrs, dict) or (not _rows(attrs) and not _nvme(attrs)):
        return False
    if not isinstance(dev, str) or serial_of(conn, mac, dev) != serial:
        return False
    with _write_lock, writing(conn):
        conn.execute(
            "INSERT INTO smart_history (serial, session_id, mac, at, attrs_json) VALUES (?,?,?,?,?)"
            " ON CONFLICT(serial, session_id, mac) DO UPDATE SET at=excluded.at,"
            " attrs_json=excluded.attrs_json",
            (serial, session_id, mac, now_iso(), json.dumps(attrs)))
    return True


def record_temp(conn: sqlite3.Connection, serial: str | None, session_id: str, temp_c: object,
                mac: str) -> bool:
    """The temperature after the write (final report `temp_c`), on this machine's row of the round."""
    if not serial or not isinstance(temp_c, int) or isinstance(temp_c, bool) or not 0 < temp_c < 150:
        return False
    with _write_lock, writing(conn):
        return conn.execute("UPDATE smart_history SET temp_after = ? WHERE serial = ? AND session_id = ? AND mac = ?",
                            (temp_c, serial, session_id, mac)).rowcount == 1


def serial_of(conn: sqlite3.Connection, mac: str, dev: str) -> str | None:
    """The serial of `dev` on `mac`, from the last hello's disks; None = not reported."""
    row = conn.execute("SELECT disks_json FROM net_devices WHERE mac = ?", (mac,)).fetchone()
    try:
        disks = json.loads(row["disks_json"]) if row is not None and row["disks_json"] else []
    except (TypeError, ValueError):
        return None
    for d in disks if isinstance(disks, list) else []:
        if isinstance(d, dict) and d.get("dev") == dev and isinstance(d.get("serial"), str) and d["serial"]:
            return d["serial"]
    return None


def previous(conn: sqlite3.Connection, serial: str | None) -> sqlite3.Row | None:
    """This serial's last snapshot, or None -- no previous round. Only rows a machine wrote for
    a disk it reported (`record` checks it; a row without a MAC is not one) count."""
    if not serial:
        return None
    return conn.execute("SELECT session_id, at, attrs_json, temp_after FROM smart_history"
                        " WHERE serial = ? AND mac IS NOT NULL ORDER BY at DESC LIMIT 1", (serial,)).fetchone()


def _rose(now: dict[int, dict], before: dict[int, dict]) -> dict[int, int]:
    out = {}
    for i, r in now.items():
        b = before.get(i)
        if b and r["raw"] is not None and b["raw"] is not None and r["raw"] > b["raw"]:
            out[i] = r["raw"] - b["raw"]
    return out


def learned(now_attrs: object, prev: sqlite3.Row | None) -> str | None:
    """The orange the server learned for a disk, worst first (agent: SMART_WARN_REASONS)."""
    if prev is None:
        return None
    now, before = _rows(now_attrs), _rows(json.loads(prev["attrs_json"] or "{}"))
    rose = _rose(now, before)

    def vendor(i: int) -> str | None:   # the hello carries no names; the round's snapshot does
        return now[i]["vendor"] or before[i]["vendor"]
    if any(smart_names.word(i, vendor(i)) in smart_names.CRITICAL_WORDS for i in rose):
        return "critical_rose"
    for i, word in ROSE_LINK.items():
        if i in rose and smart_names.row(i, vendor(i)) is not None and smart_names.word(i, vendor(i)) is None:
            return word   # a name the table knows, with no word of its own: the downshift / timeout counter
    if isinstance(prev["temp_after"], int) and prev["temp_after"] > TEMP_WARN_C:
        return "hot"
    return None


def learned_for(conn: sqlite3.Connection, disks: object) -> list[dict]:
    """The hello's `smart_learned`: [{"serial", "reason"}] for the disks of this hello
    whose attributes rose since their last snapshot. [] = nothing learned."""
    out = []
    for d in disks if isinstance(disks, list) else []:
        if not isinstance(d, dict) or not isinstance(d.get("serial"), str) or not d["serial"] \
                or not isinstance(d.get("smart_attrs"), dict):   # nothing read now, nothing to compare
            continue
        word = learned(d.get("smart_attrs"), previous(conn, d["serial"]))
        if word:
            out.append({"serial": d["serial"], "reason": word})
    return out


def describe(conn: sqlite3.Connection, disk: dict) -> dict:
    """One disk for the console: every attribute in Hebrew, and what rose."""
    now, nvme = _rows(disk.get("smart_attrs")), _nvme(disk.get("smart_attrs"))
    prev = previous(conn, disk.get("serial"))
    before = _rows(json.loads(prev["attrs_json"] or "{}")) if prev is not None else {}
    before_nvme = _nvme(json.loads(prev["attrs_json"] or "{}")) if prev is not None else {}
    rose = _rose(now, before)
    rows, hw = [], []
    crc = crcwarn.threshold(conn)
    for i in sorted(now):
        r = now[i]
        he, note, known = smart_names.name(i, r["vendor"] or (before.get(i) or {}).get("vendor"))
        failing = r["value"] is not None and r["thresh"] not in (None, 0) and r["value"] <= r["thresh"]
        vend = r["vendor"] or (before.get(i) or {}).get("vendor")
        verdict = smart_names.judge_ata(i, vend, r["value"], r["raw"], crc=crc)   # #1921: the agent's judge, same tables
        tr = smart_names.row(i, vend)
        if verdict == "hw" and tr is not None and tr["word"] not in [h["word"] for h in hw]:
            hw.append({"word": tr["word"], "he": smart_names.hw_sentence(tr["word"])})
        rows.append({"id": i, "name_he": he, "note_he": note, "known": known,
                     "vendor": r["vendor"] or (before.get(i) or {}).get("vendor"),
                     "value": r["value"], "worst": r["worst"], "thresh": r["thresh"], "raw": r["raw"],
                     "critical": (tr or {}).get("word") in smart_names.CRITICAL_WORDS | {"crc"}, "rose_by": rose.get(i),   # #1921: by (id, name)
                     # #1859 item 5: normalized at or under the vendor's threshold -- what the BIOS sees.
                     "failing_now": failing,
                     # #1921: smart = the disk's colour; hw = a note on the drawer, never the disk's colour
                     "table": tr["table"] if tr is not None else None, "verdict": verdict})
    for k in sorted(nvme):
        he, note = smart_names.NVME_NAMES.get(k, (k, smart_names.UNKNOWN_HE))
        b = before_nvme.get(k)
        rows.append({"id": k, "name_he": he, "note_he": note, "known": k in smart_names.NVME_NAMES,
                     "vendor": None, "value": None, "worst": None, "thresh": None, "raw": nvme[k],
                     "critical": k in smart_names.CRITICAL_NVME,
                     "rose_by": nvme[k] - b if b is not None and nvme[k] > b else None, "failing_now": False,
                     "table": "smart" if k in smart_names.NVME else None, "verdict": None})
    return {
        "serial": disk.get("serial"), "dev": disk.get("dev"), "port": disk.get("port"),
        "read": bool(rows),
        "compared": prev is not None,
        "previous_at": prev["at"] if prev is not None else None,
        "previous_temp_c": prev["temp_after"] if prev is not None else None,
        "learned": learned(disk.get("smart_attrs"), prev),
        "attributes": rows,
        "hw": hw,   # #1921: the hardware table's notes for this drawer -- the cable, not the disk
    }
