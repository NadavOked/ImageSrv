"""SMART in Hebrew, and its verdict -- from the two knowledge tables of #1921.

Nadav, 20/10 (final): the agent and the server judge a disk the same way, from the same
two data files, which live in the agent's tree and are packed into its initramfs:

* ``agent/lib/data/smart-knowledge.tsv`` -- problems of the disk itself: its colour
  (orange = an early sign, the disk works; red = the disk itself reports FAILED).
* ``agent/lib/data/hw-issues.tsv`` -- the cable, the drawer, the controller (CRC 199/218/210,
  a link that resets or runs slow, IDE mode, a drawer that drops, USB 2.0): a NOTE on the
  drawer or the machine, never the disk's colour.

An attribute is matched by **(id, the name smartctl printed)** -- smartctl already applied
drivedb.h, so the name carries the vendor's meaning (#1923: 202 on a Crucial is
``Percent_Lifetime_Remain``, not "data address mark errors"; 190 on a Seagate SSD is not a
temperature). An id or a name the tables do not know is "לא מוכר" -- shown by the
vendor's name, never "fine", never a colour (principle 5). The agent's twin is
``agent/lib/smartjudge.sh`` (awk on the same files); tests/test_smart_tables_1921.py
checks that both read these files and agree on the fixtures.

CRITICAL is Grok's list as Nadav took it (#1859, 20/10 00:15): a rise in one of them since
this serial's previous round adds "להחליף את הדיסק" (smart_history.py) -- except 199, the
cable (#872). It is matched by the table's word now, not by the id alone.
"""

from __future__ import annotations

from pathlib import Path

DATA = Path(__file__).resolve().parents[1] / "agent" / "lib" / "data"
SMART_TABLE = DATA / "smart-knowledge.tsv"
HW_TABLE = DATA / "hw-issues.tsv"
COLUMNS = ("kind", "id", "name", "field", "op", "value", "verdict", "word", "media", "name_he", "explain_he")

UNKNOWN_HE = "לא מוכר"
NO_NAME_HE = "לא מוכר — שם היצרן לא התקבל"


def _load(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        cells = line.split("\t")
        if len(cells) != len(COLUMNS):
            raise ValueError(f"{path.name}: {len(cells)} columns, not {len(COLUMNS)}: {line[:60]!r}")
        rows.append({k: (None if v == "-" else v) for k, v in zip(COLUMNS, cells)})
    return rows


def _index(rows: list[dict], table: str) -> tuple[dict, dict, dict]:
    ata, nvme, hw = {}, {}, {}
    for r in rows:
        r["table"] = table
        if r["kind"] == "ata":
            ata[(int(r["id"]), r["name"])] = r
        elif r["kind"] == "nvme":
            nvme[r["id"]] = r
        elif r["kind"] == "hw":
            hw[r["word"]] = r
    return ata, nvme, hw


_S_ATA, _S_NVME, _ = _index(_load(SMART_TABLE), "smart")
_H_ATA, _, HW = _index(_load(HW_TABLE), "hw")
#: (id, smartctl's name) -> the row, from both tables (a name is in one of them, never both).
ATA = {**_S_ATA, **_H_ATA}
NVME = _S_NVME

#: the NVMe health lines (agent: smartattrs.sh), by key -- (Hebrew name, explanation).
NVME_NAMES: dict[str, tuple[str, str]] = {k: (r["name_he"], r["explain_he"]) for k, r in NVME.items()}

#: Grok's critical list as Nadav took it (#1859), as the table's words: a rise in one of
#: these, by (id, name), is "replace the disk". 199 (the cable) is the hardware table's.
CRITICAL_WORDS = frozenset({"reallocated", "spin_retry", "reported_uncorrectable", "realloc_event",
                            "pending_sectors", "uncorrectable", "bad_blocks"})
#: the ids of that list, for the console's "קריטי" mark on a row the table knows.
CRITICAL = frozenset({5, 10, 187, 196, 197, 198, 199})
CRITICAL_NVME = frozenset({"critical_warning", "media_errors"})


def row(attr_id: int, vendor: str | None) -> dict | None:
    """The table row of (id, name), or None -- "לא מוכר"."""
    return ATA.get((attr_id, vendor)) if vendor else None


def name(attr_id: int, vendor: str | None = None) -> tuple[str, str, bool]:
    """(Hebrew name, explanation, known), by (id, smartctl's name). Unknown: the vendor's
    name and "לא מוכר"; no name at all: "מאפיין N" -- an id alone is not matched (#1923)."""
    r = row(attr_id, vendor)
    if r is not None:
        return r["name_he"], r["explain_he"], True
    return (vendor or f"מאפיין {attr_id}"), (UNKNOWN_HE if vendor else NO_NAME_HE), False


def word(attr_id: int, vendor: str | None) -> str | None:
    """The reason word of (id, name) -- its orange or hardware word -- or None."""
    r = row(attr_id, vendor)
    return r["word"] if r is not None else None


def _holds(r: dict, x: int, crc: int, wctemp: int | None) -> bool:
    if r["value"] == "crc":
        if not crc:
            return False   # crc_warn_threshold 0 = the note is off (crcwarn.py)
        t = crc
    elif r["value"] == "wctemp":
        if wctemp is None:
            return False
        t = wctemp
    else:
        t = int(r["value"])
    return {">": x > t, ">=": x >= t, "<": x < t, "<=": x <= t}.get(r["op"], False)


def judge_ata(attr_id: int, vendor: str | None, value: int | None, raw: int | None, *,
              media: str | None = None, crc: int = 0) -> str:
    """One ATA attribute, as smartjudge.sh judges it: "unknown", "info", "ok", or the
    verdict of the rule that held ("orange" / "red" / "hw"). `media` ssd|hdd|None."""
    r = row(attr_id, vendor)
    if r is None:
        return "unknown"
    if r["op"] is None:
        return "info"
    if r["media"] not in (None, "any") and r["media"] != media:
        return "info"   # e.g. 183 Runtime_Bad_Block: an SSD only (ס3); a medium not known is not judged
    if r["field"] == "value":
        x = value
    elif r["field"] == "temp":
        x = raw % 256 if isinstance(raw, int) and raw > 150 else raw
        x = x if isinstance(x, int) and 1 <= x <= 150 else None
    else:
        x = raw
    if x is None:
        return "info"
    return r["verdict"] if _holds(r, x, crc, None) else "ok"


def hw_sentence(word_: str) -> str | None:
    """The hardware table's Hebrew sentence for a word (the note on the drawer)."""
    r = HW.get(word_)
    return r["explain_he"] if r is not None else None
